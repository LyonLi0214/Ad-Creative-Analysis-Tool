from __future__ import annotations

import collections
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from migration.noco_to_feishu import DB_PATH, FeishuClient, load_configuration


ROOT = Path(__file__).resolve().parents[1]
GUIDE_PATH = ROOT / "docs" / "appsflyer_guide.md"


def ensure_local_schema(connection: sqlite3.Connection) -> None:
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS game_background (
      game_key TEXT PRIMARY KEY, game_name TEXT NOT NULL, aliases_json TEXT NOT NULL,
      product_background TEXT, core_gameplay TEXT, target_market TEXT,
      material_context TEXT, evidence_boundary TEXT, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS appsflyer_app_mapping (
      app_id TEXT PRIMARY KEY, game_key TEXT NOT NULL, platform TEXT NOT NULL,
      campaign_endpoint TEXT NOT NULL, raw_installs_endpoint TEXT NOT NULL,
      raw_events_endpoint TEXT NOT NULL, campaign_granularity TEXT NOT NULL,
      raw_granularity TEXT NOT NULL, material_match_rule TEXT NOT NULL,
      timezone_note TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS knowledge_documents (
      document_key TEXT PRIMARY KEY, title TEXT NOT NULL, source_path TEXT NOT NULL,
      content TEXT NOT NULL, content_hash TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    """)


def source_stats(connection: sqlite3.Connection) -> dict:
    rows = [json.loads(item[0]) for item in connection.execute("SELECT payload_json FROM source_records WHERE source_table_name='素材主表'")]
    games = collections.Counter(str(row.get("游戏名称") or "未填写") for row in rows)
    platforms = collections.Counter(str(row.get("平台") or "未填写") for row in rows)
    types = collections.Counter(str(row.get("类型") or "未填写") for row in rows)
    campaigns = {str(row.get("Campaign")) for row in rows if row.get("Campaign")}
    return {"materials": len(rows), "games": dict(games), "platforms": dict(platforms), "types": dict(types), "campaigns": len(campaigns)}


def list_records(feishu: FeishuClient, table_id: str) -> list[dict]:
    result, token = [], None
    while True:
        params = {"page_size": 500}
        if token:
            params["page_token"] = token
        data = feishu.call("GET", f"/open-apis/bitable/v1/apps/{feishu.base_token}/tables/{table_id}/records", params=params)
        result.extend(data.get("items") or [])
        if not data.get("has_more"):
            return result
        token = data.get("page_token")


def upsert_visible(feishu: FeishuClient, table_id: str, records: list[dict]) -> tuple[int, int]:
    existing = {}
    for item in list_records(feishu, table_id):
        name = (item.get("fields") or {}).get("名称")
        if name:
            existing[str(name)] = str(item.get("record_id"))
    created, updated = 0, 0
    for record in records:
        key = str(record["名称"])
        if key in existing:
            feishu.update_record(table_id, existing[key], record)
            updated += 1
        else:
            feishu.create_records(table_id, [record])
            created += 1
    return created, updated


def main() -> int:
    load_configuration()
    connection = sqlite3.connect(DB_PATH)
    ensure_local_schema(connection)
    now = datetime.now(timezone.utc).isoformat()
    stats = source_stats(connection)
    guide = GUIDE_PATH.read_text(encoding="utf-8-sig")
    connection.execute("INSERT OR REPLACE INTO knowledge_documents VALUES (?,?,?,?,?,?)", (
        "appsflyer_pull_guide", "AppsFlyer Pull API 查询指南", str(GUIDE_PATH), guide,
        hashlib.sha256(guide.encode("utf-8")).hexdigest(), now,
    ))
    material_context = f"当前本地素材库共 {stats['materials']} 条；游戏分布 {stats['games']}；投放平台分布 {stats['platforms']}；素材类型分布 {stats['types']}；Campaign 去重 {stats['campaigns']} 个。"
    connection.execute("INSERT OR REPLACE INTO game_background VALUES (?,?,?,?,?,?,?,?,?)", (
        "xindongfangxian", "心动防线", json.dumps(["心动防线", "心动JP"], ensure_ascii=False),
        "当前素材拆解、标签分析与 AppsFlyer 效果关联的主要游戏。世界观、角色设定和正式产品介绍尚未获得业务确认，不能自行补写。",
        "待业务补充并确认。Agent 可引用已识别的素材画面与标签，但不得把素材表现推断成产品完整玩法。",
        "当前数据与项目命名指向日本投放场景；更细国家、渠道和受众结论必须查询 AppsFlyer 或由业务补充。",
        material_context,
        "素材识别事实、产品背景、AppsFlyer 效果必须分开表达；Campaign 不等于素材，Ad 也只有建立唯一素材映射后才可称为单素材。",
        now,
    ))
    common = {
        "campaign_endpoint": "partners_by_date_report/v5",
        "raw_installs_endpoint": "installs_report/v5",
        "raw_events_endpoint": "in_app_events_report/v5",
        "campaign_granularity": "日期/渠道/Campaign 聚合；不可解释为广告或单素材效果",
        "raw_granularity": "原始记录含 Adset、Ad、Ad ID；可自行聚合至 Ad",
        "material_match_rule": "只有 Ad ID/Ad 与素材主数据形成唯一映射且无一对多冲突时，才标记为单素材已确认；否则标记为素材候选或广告粒度",
        "timezone_note": "未传 timezone 时为 UTC；日本口径使用 Asia/Tokyo，并在报告中明确时区",
    }
    mappings = [("com.singularity.xdfxrw", "Android"), ("id6766773102", "iOS")]
    for app_id, platform in mappings:
        connection.execute("INSERT OR REPLACE INTO appsflyer_app_mapping VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            app_id, "xindongfangxian", platform, common["campaign_endpoint"], common["raw_installs_endpoint"],
            common["raw_events_endpoint"], common["campaign_granularity"], common["raw_granularity"],
            common["material_match_rule"], common["timezone_note"], now,
        ))
    connection.commit()

    feishu = FeishuClient()
    game_table = feishu.ensure_table("游戏背景库")
    game_fields = ["游戏Key", "游戏名称", "别名", "产品背景", "核心玩法", "目标市场", "素材库现状", "证据边界", "更新时间"]
    feishu.ensure_fields(game_table, game_fields)
    game_record = {"名称": "心动防线", "游戏Key": "xindongfangxian", "游戏名称": "心动防线", "别名": "心动防线、心动JP", "产品背景": "当前素材拆解、标签分析与 AppsFlyer 效果关联的主要游戏。正式世界观和角色设定待业务确认。", "核心玩法": "待业务补充并确认；不可根据广告素材自行补写完整玩法。", "目标市场": "当前项目命名指向日本投放场景；具体结论以 AppsFlyer 数据为准。", "素材库现状": material_context, "证据边界": "Campaign 不等于素材；Ad 只有与素材建立唯一映射后才能解释为单素材。", "更新时间": now}
    game_result = upsert_visible(feishu, game_table, [game_record])

    af_table = feishu.ensure_table("AppsFlyer应用与查询口径")
    af_fields = ["游戏名称", "平台", "App ID", "Campaign接口", "明细安装接口", "明细事件接口", "Campaign粒度", "Raw粒度", "素材关联规则", "时区口径", "更新时间"]
    feishu.ensure_fields(af_table, af_fields)
    af_records = []
    for app_id, platform in mappings:
        af_records.append({"名称": f"心动防线-{platform}", "游戏名称": "心动防线", "平台": platform, "App ID": app_id, "Campaign接口": common["campaign_endpoint"], "明细安装接口": common["raw_installs_endpoint"], "明细事件接口": common["raw_events_endpoint"], "Campaign粒度": common["campaign_granularity"], "Raw粒度": common["raw_granularity"], "素材关联规则": common["material_match_rule"], "时区口径": common["timezone_note"], "更新时间": now})
    af_result = upsert_visible(feishu, af_table, af_records)
    connection.close()
    print(json.dumps({"sqlite": str(DB_PATH), "stats": stats, "game_table": {"id": game_table, "created_updated": game_result}, "appsflyer_table": {"id": af_table, "created_updated": af_result}, "base_url": f"https://dreametech.feishu.cn/base/{feishu.base_token}"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
