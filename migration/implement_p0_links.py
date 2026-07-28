from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from appsflyer_integration.normalize import normalize_name
from migration.noco_to_feishu import DB_PATH, ROOT, TOOLS_CONFIG


BASE_TOKEN = json.loads(TOOLS_CONFIG.read_text(encoding="utf-8"))["base_token"]
MATERIAL_TABLE_ID = "tblrjnWVj9yJwreB"
CLI = Path(os.environ.get("APPDATA", "")) / "npm" / "lark-cli.cmd"
MANIFEST_PATH = ROOT / "appsflyer_data" / "latest_manifest.json"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_key(game: str, name: str) -> str:
    digest = hashlib.sha256(f"{game}|{normalize_name(name)}".encode("utf-8")).hexdigest()[:16]
    return f"mat_{digest}"


def run_cli(*args: str) -> dict[str, Any]:
    env = os.environ.copy()
    env["LARKSUITE_CLI_NO_UPDATE_NOTIFIER"] = "1"
    env["LARKSUITE_CLI_NO_SKILLS_NOTIFIER"] = "1"
    command = [str(CLI), *args, "--format", "json"]
    proc = subprocess.run(command, cwd=ROOT, env=env, text=True, encoding="utf-8", capture_output=True)
    raw = proc.stdout.strip() or proc.stderr.strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"lark-cli 返回无法解析：{' '.join(args[:2])}: {raw[-1000:]}") from exc
    if proc.returncode or not payload.get("ok"):
        raise RuntimeError(f"lark-cli 调用失败：{' '.join(args[:2])}: {json.dumps(payload, ensure_ascii=False)}")
    return payload


def table_map() -> dict[str, str]:
    result = run_cli("base", "+table-list", "--as", "bot", "--base-token", BASE_TOKEN)
    return {row["name"]: row["id"] for row in result["data"]["tables"]}


def ensure_table(name: str, fields: list[dict[str, Any]]) -> tuple[str, bool]:
    tables = table_map()
    if name in tables:
        return tables[name], False
    result = run_cli(
        "base", "+table-create", "--as", "bot", "--base-token", BASE_TOKEN,
        "--name", name, "--fields", json.dumps(fields, ensure_ascii=False, separators=(",", ":")),
    )
    data = result.get("data") or {}
    table = data.get("table") or data
    table_id = table.get("id") or table.get("table_id")
    if not table_id:
        table_id = table_map().get(name)
    if not table_id:
        raise RuntimeError(f"创建表后未取得 table_id：{name}")
    return str(table_id), True


def batch_create(table_id: str, fields: list[str], rows: list[list[Any]]) -> list[str]:
    ids: list[str] = []
    # Windows CreateProcess has a short command-line limit. Keep JSON batches
    # deliberately small because AppsFlyer names can be long.
    for start in range(0, len(rows), 10):
        body = {"fields": fields, "rows": rows[start:start + 10]}
        payload_path = ROOT / ".p0_batch.json"
        payload_path.write_text(json.dumps(body, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        try:
            result = run_cli(
                "base", "+record-batch-create", "--as", "bot", "--base-token", BASE_TOKEN,
                "--table-id", table_id, "--json", "@.p0_batch.json",
            )
        finally:
            payload_path.unlink(missing_ok=True)
        data = result.get("data") or {}
        ids.extend(str(item) for item in (data.get("record_id_list") or []))
    return ids


def record_key_map(table_id: str, key_field: str) -> dict[str, str]:
    output: dict[str, str] = {}
    offset = 0
    while True:
        result = run_cli(
            "base", "+record-list", "--as", "bot", "--base-token", BASE_TOKEN,
            "--table-id", table_id, "--field-id", key_field, "--limit", "200", "--offset", str(offset),
        )
        data = result.get("data") or {}
        values = data.get("data") or []
        record_ids = data.get("record_id_list") or []
        for value_row, record_id in zip(values, record_ids):
            if value_row and value_row[0]:
                output[str(value_row[0])] = str(record_id)
        if not data.get("has_more"):
            return output
        offset += len(record_ids)


def load_materials(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT s.source_table_id,s.source_record_id,s.payload_json,m.feishu_record_id
           FROM source_records s JOIN feishu_record_map m
             ON m.source_table_id=s.source_table_id AND m.source_record_id=s.source_record_id
           WHERE s.source_table_name='素材主表'"""
    ).fetchall()
    output = []
    for source_table_id, source_record_id, payload_json, feishu_record_id in rows:
        payload = json.loads(payload_json)
        name = str(payload.get("素材名称") or "").strip()
        game = str(payload.get("游戏名称") or "心动防线").strip()
        if name:
            output.append({
                "source_table_id": source_table_id,
                "source_record_id": source_record_id,
                "feishu_record_id": feishu_record_id,
                "name": name,
                "normalized_name": normalize_name(name),
                "game": game,
                "material_key": stable_key(game, name),
            })
    return output


def raw_value(raw: dict[str, Any], *aliases: str) -> Any:
    for alias in aliases:
        value = raw.get(alias)
        if value not in (None, "", [], {}):
            return value
    return None


def as_number(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def load_appsflyer() -> tuple[dict[tuple[str, str], set[tuple[str, str, str, str, str]]], dict[tuple[str, ...], dict[str, Any]], str]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    ads: dict[tuple[str, str], set[tuple[str, str, str, str, str]]] = defaultdict(set)
    facts: dict[tuple[str, ...], dict[str, Any]] = {}
    for report in manifest.get("reports", []):
        app_id = str(report.get("app_id") or "")
        platform = "iOS" if app_id.startswith("id") else "Android"
        rows = json.loads((ROOT / report["path"]).read_text(encoding="utf-8"))
        kind = report.get("report")
        for item in rows:
            raw = item.get("raw") or {}
            ad_name = str(raw_value(raw, "Ad", "af_ad", "ad", "Creative", "af_creative", "creative") or "").strip()
            normalized_ad = normalize_name(ad_name) if ad_name else ""
            campaign_id = str(raw_value(raw, "Campaign ID", "campaign_id") or "")
            adset_id = str(raw_value(raw, "Adset ID", "af_adset_id", "adset_id") or "")
            adset_name = str(raw_value(raw, "Adset", "af_adset", "adset") or "")
            ad_id = str(raw_value(raw, "Ad ID", "af_ad_id", "ad_id") or "")
            if normalized_ad:
                ads[(app_id, platform)].add((normalized_ad, ad_name, campaign_id, adset_id, ad_id))
            date_value = str(raw_value(raw, "Event Time", "Install Time", "Attributed Touch Time", "event_time", "install_time") or manifest.get("run_id", "")[:10])[:10]
            key = (date_value, app_id, platform, campaign_id, adset_id, ad_id, ad_name)
            fact = facts.setdefault(key, {
                "date": date_value, "app_id": app_id, "platform": platform, "campaign_id": campaign_id,
                "adset_id": adset_id, "adset_name": adset_name, "ad_id": ad_id, "ad_name": ad_name,
                "installs": 0, "events": 0, "paid_events": 0, "revenue_usd": 0.0,
            })
            if kind == "installs":
                fact["installs"] += 1
            else:
                fact["events"] += 1
                revenue = as_number(raw_value(raw, "Event Revenue USD", "event_revenue_usd"))
                fact["revenue_usd"] += revenue
                if revenue > 0:
                    fact["paid_events"] += 1
    return ads, facts, str(manifest.get("run_id") or "")


def select_candidates(materials: list[dict[str, Any]], ads: dict[tuple[str, str], set[tuple[str, str, str, str, str]]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in materials:
        grouped[row["normalized_name"]].append(row)
    candidates = []
    for normalized_name, material_rows in sorted(grouped.items()):
        exact_options, fuzzy_options = [], []
        for (app_id, platform), values in ads.items():
            for normalized_ad, ad_name, campaign_id, adset_id, ad_id in values:
                option = (app_id, platform, normalized_ad, ad_name, campaign_id, adset_id, ad_id)
                if normalized_ad == normalized_name:
                    exact_options.append(option)
                elif min(len(normalized_name), len(normalized_ad)) >= 5 and (normalized_name in normalized_ad or normalized_ad in normalized_name):
                    fuzzy_options.append(option)
        options, match_type = (exact_options, "精确候选") if exact_options else (fuzzy_options, "模糊候选")
        if not options:
            continue
        options.sort(key=lambda x: (-SequenceMatcher(None, normalized_name, x[2]).ratio(), x[0], x[3]))
        app_id, platform, normalized_ad, ad_name, campaign_id, adset_id, ad_id = options[0]
        first = material_rows[0]
        candidates.append({
            "mapping_key": hashlib.sha256(f"{first['material_key']}|{app_id}|{normalized_ad}".encode()).hexdigest()[:24],
            "material_key": first["material_key"], "material_name": first["name"], "game": first["game"],
            "app_id": app_id, "platform": platform, "campaign_id": campaign_id, "adset_id": adset_id,
            "ad_id": ad_id, "ad_name": ad_name, "normalized_ad": normalized_ad,
            "match_type": match_type, "confidence": round(1.0 if match_type == "精确候选" else SequenceMatcher(None, normalized_name, normalized_ad).ratio(), 4),
            "status": "待人工确认", "material_record_ids": [r["feishu_record_id"] for r in material_rows],
        })
    return candidates


def init_local(connection: sqlite3.Connection) -> None:
    connection.executescript("""
    CREATE TABLE IF NOT EXISTS material_registry (
      material_key TEXT PRIMARY KEY, game_name TEXT NOT NULL, material_name TEXT NOT NULL,
      normalized_name TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS material_appsflyer_mapping (
      mapping_key TEXT PRIMARY KEY, material_key TEXT NOT NULL, app_id TEXT NOT NULL, platform TEXT NOT NULL,
      campaign_id TEXT, adset_id TEXT, ad_id TEXT, ad_name TEXT, normalized_ad_name TEXT,
      match_type TEXT NOT NULL, confidence REAL NOT NULL, confirmation_status TEXT NOT NULL,
      feishu_record_id TEXT, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS appsflyer_daily_facts (
      fact_key TEXT PRIMARY KEY, fact_date TEXT NOT NULL, app_id TEXT NOT NULL, platform TEXT NOT NULL,
      campaign_id TEXT, adset_id TEXT, adset_name TEXT, ad_id TEXT, ad_name TEXT,
      installs INTEGER NOT NULL, events INTEGER NOT NULL, paid_events INTEGER NOT NULL,
      revenue_usd REAL NOT NULL, source_run TEXT NOT NULL, feishu_record_id TEXT, updated_at TEXT NOT NULL
    );
    """)


def main() -> int:
    connection = sqlite3.connect(DB_PATH)
    init_local(connection)
    materials = load_materials(connection)
    ads, facts, source_run = load_appsflyer()
    candidates = select_candidates(materials, ads)
    now = utcnow()

    for row in materials:
        connection.execute(
            "INSERT INTO material_registry VALUES (?,?,?,?,?) ON CONFLICT(material_key) DO UPDATE SET game_name=excluded.game_name,material_name=excluded.material_name,normalized_name=excluded.normalized_name,updated_at=excluded.updated_at",
            (row["material_key"], row["game"], row["name"], row["normalized_name"], now),
        )
    for row in candidates:
        connection.execute(
            """INSERT INTO material_appsflyer_mapping VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(mapping_key) DO UPDATE SET campaign_id=excluded.campaign_id,adset_id=excluded.adset_id,ad_id=excluded.ad_id,ad_name=excluded.ad_name,normalized_ad_name=excluded.normalized_ad_name,match_type=excluded.match_type,confidence=excluded.confidence,updated_at=excluded.updated_at""",
            (row["mapping_key"], row["material_key"], row["app_id"], row["platform"], row["campaign_id"], row["adset_id"], row["ad_id"], row["ad_name"], row["normalized_ad"], row["match_type"], row["confidence"], row["status"], None, now),
        )
    fact_rows = []
    for key, row in sorted(facts.items()):
        fact_key = hashlib.sha256("|".join(key).encode()).hexdigest()[:24]
        row["fact_key"] = fact_key
        fact_rows.append(row)
        connection.execute(
            """INSERT INTO appsflyer_daily_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(fact_key) DO UPDATE SET installs=excluded.installs,events=excluded.events,paid_events=excluded.paid_events,revenue_usd=excluded.revenue_usd,source_run=excluded.source_run,updated_at=excluded.updated_at""",
            (fact_key, row["date"], row["app_id"], row["platform"], row["campaign_id"], row["adset_id"], row["adset_name"], row["ad_id"], row["ad_name"], row["installs"], row["events"], row["paid_events"], row["revenue_usd"], source_run, None, now),
        )
    connection.commit()

    mapping_fields = [
        {"name": "映射键", "type": "text"}, {"name": "素材Key", "type": "text"}, {"name": "素材名称", "type": "text"},
        {"name": "游戏名称", "type": "text"}, {"name": "平台", "type": "text"}, {"name": "App ID", "type": "text"},
        {"name": "Campaign ID", "type": "text"}, {"name": "Adset ID", "type": "text"}, {"name": "Ad ID", "type": "text"},
        {"name": "AppsFlyer广告名", "type": "text"}, {"name": "匹配方式", "type": "text"},
        {"name": "匹配置信度", "type": "number", "style": {"type": "plain", "precision": 4}},
        {"name": "人工确认状态", "type": "text"},
        {"name": "关联素材", "type": "link", "link_table": MATERIAL_TABLE_ID, "bidirectional": True, "bidirectional_link_field_name": "AppsFlyer映射"},
        {"name": "更新时间", "type": "datetime", "style": {"format": "yyyy-MM-dd HH:mm"}},
    ]
    daily_fields = [
        {"name": "事实键", "type": "text"}, {"name": "日期", "type": "datetime", "style": {"format": "yyyy-MM-dd"}},
        {"name": "平台", "type": "text"}, {"name": "App ID", "type": "text"}, {"name": "Campaign ID", "type": "text"},
        {"name": "Adset ID", "type": "text"}, {"name": "Adset名称", "type": "text"}, {"name": "Ad ID", "type": "text"},
        {"name": "AppsFlyer广告名", "type": "text"}, {"name": "安装", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "事件数", "type": "number", "style": {"type": "plain", "precision": 0}}, {"name": "付费事件数", "type": "number", "style": {"type": "plain", "precision": 0}},
        {"name": "收入USD", "type": "number", "style": {"type": "currency", "precision": 2, "currency_code": "USD"}},
        {"name": "同步批次", "type": "text"}, {"name": "更新时间", "type": "datetime", "style": {"format": "yyyy-MM-dd HH:mm"}},
    ]
    mapping_table_id, mapping_created = ensure_table("素材与AppsFlyer映射", mapping_fields)
    daily_table_id, daily_created = ensure_table("AppsFlyer日效果", daily_fields)

    # Recover cloud writes from a previous interrupted run before computing pending rows.
    for business_key, record_id in record_key_map(mapping_table_id, "映射键").items():
        connection.execute("UPDATE material_appsflyer_mapping SET feishu_record_id=? WHERE mapping_key=?", (record_id, business_key))
    for business_key, record_id in record_key_map(daily_table_id, "事实键").items():
        connection.execute("UPDATE appsflyer_daily_facts SET feishu_record_id=? WHERE fact_key=?", (record_id, business_key))
    connection.commit()

    # 新建映射表的首次运行才回填主表。若后续从批次失败恢复，避免重复更新 97 行。
    if mapping_created:
        for row in materials:
            run_cli(
                "base", "+record-upsert", "--as", "bot", "--base-token", BASE_TOKEN, "--table-id", MATERIAL_TABLE_ID,
                "--record-id", row["feishu_record_id"], "--json", json.dumps({"素材ID": row["material_key"]}, ensure_ascii=False),
            )

    mapping_ids: list[str] = []
    pending_candidates = [c for c in candidates if connection.execute("SELECT feishu_record_id FROM material_appsflyer_mapping WHERE mapping_key=?", (c["mapping_key"],)).fetchone()[0] is None]
    if pending_candidates:
        fields = ["映射键", "素材Key", "素材名称", "游戏名称", "平台", "App ID", "Campaign ID", "Adset ID", "Ad ID", "AppsFlyer广告名", "匹配方式", "匹配置信度", "人工确认状态", "关联素材", "更新时间"]
        rows = [[c["mapping_key"], c["material_key"], c["material_name"], c["game"], c["platform"], c["app_id"], c["campaign_id"], c["adset_id"], c["ad_id"], c["ad_name"], c["match_type"], c["confidence"], c["status"], [{"id": rid} for rid in c["material_record_ids"]], now[:19].replace("T", " ")] for c in pending_candidates]
        mapping_ids = batch_create(mapping_table_id, fields, rows)
        for candidate, record_id in zip(pending_candidates, mapping_ids):
            connection.execute("UPDATE material_appsflyer_mapping SET feishu_record_id=? WHERE mapping_key=?", (record_id, candidate["mapping_key"]))

    daily_ids: list[str] = []
    pending_facts = [r for r in fact_rows if connection.execute("SELECT feishu_record_id FROM appsflyer_daily_facts WHERE fact_key=?", (r["fact_key"],)).fetchone()[0] is None]
    if pending_facts:
        fields = ["事实键", "日期", "平台", "App ID", "Campaign ID", "Adset ID", "Adset名称", "Ad ID", "AppsFlyer广告名", "安装", "事件数", "付费事件数", "收入USD", "同步批次", "更新时间"]
        rows = [[r["fact_key"], f"{r['date']} 00:00:00", r["platform"], r["app_id"], r["campaign_id"], r["adset_id"], r["adset_name"], r["ad_id"], r["ad_name"], r["installs"], r["events"], r["paid_events"], round(r["revenue_usd"], 4), source_run, now[:19].replace("T", " ")] for r in pending_facts]
        daily_ids = batch_create(daily_table_id, fields, rows)
        for fact, record_id in zip(pending_facts, daily_ids):
            connection.execute("UPDATE appsflyer_daily_facts SET feishu_record_id=? WHERE fact_key=?", (record_id, fact["fact_key"]))
    connection.commit()
    connection.close()

    exact = sum(c["match_type"] == "精确候选" for c in candidates)
    fuzzy = sum(c["match_type"] == "模糊候选" for c in candidates)
    summary = {
        "ok": True, "material_rows_updated": len(materials), "distinct_material_keys": len({m['material_key'] for m in materials}),
        "mapping_candidates": len(candidates), "exact_candidates": exact, "fuzzy_candidates": fuzzy,
        "daily_fact_rows": len(fact_rows), "mapping_table_id": mapping_table_id, "daily_table_id": daily_table_id,
        "mapping_table_created": mapping_created, "daily_table_created": daily_created,
        "base_url": f"https://dreametech.feishu.cn/base/{BASE_TOKEN}", "source_run": source_run,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
