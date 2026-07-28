"""标签维度效果聚合引擎.

Join: ai_analysis (标签) → material_appsflyer_mapping → appsflyer_daily_facts (效果)
输出: 每个标签维度 × 每个标签值的加权平均表现

Usage:
    python aggregate.py                        # 聚合全量数据
    python aggregate.py --date 2026-07-22      # 单日聚合
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "mova_materials.sqlite3"

# 六维标签对应的 SQLite 列名
TAG_DIMENSIONS = {
    "hook_策略": "hook_strategy",
    "角色元素": "character_elements",
    "场景元素": "scene_elements",
    "制作方式": "production_method",
    "叙事模式": "narrative_mode",
    "画面风格": "visual_style",
}


def aggregate(date_filter: str | None = None) -> dict:
    """执行标签维度聚合，返回按维度的结果."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # 获取所有已分析的素材及其标签
    analyzed = {}
    for r in conn.execute("SELECT material_key, material_name, " + ", ".join(TAG_DIMENSIONS.values()) + " FROM ai_analysis"):
        analyzed[r["material_key"]] = {dim: r[col] for dim, col in TAG_DIMENSIONS.items()}

    # 获取映射关系: material_key → [campaign_id (lowercase)]
    mappings = {}
    for r in conn.execute("SELECT material_key, LOWER(campaign_id) as cid FROM material_appsflyer_mapping WHERE campaign_id != ''"):
        if r["material_key"] not in mappings:
            mappings[r["material_key"]] = []
        mappings[r["material_key"]].append(r["cid"])

    # 获取效果数据按 campaign_id 聚合（安装都在 campaign 层，ad_id/adset_id 通常为空）
    where = "WHERE 1=1"
    params: list = []
    if date_filter:
        where += " AND fact_date = ?"
        params.append(date_filter)

    facts = {}
    for r in conn.execute(f"SELECT LOWER(campaign_id) as cid, SUM(installs) as i, SUM(events) as e, SUM(paid_events) as p, SUM(revenue_usd) as r FROM appsflyer_daily_facts {where} GROUP BY LOWER(campaign_id)", params):
        facts[r["cid"]] = {
            "installs": r["i"] or 0, "events": r["e"] or 0,
            "paid_events": r["p"] or 0, "revenue_usd": round(r["r"] or 0, 2),
        }

    # 找到实际匹配的 campaign 数
    mapped_cids = set()
    for cids in mappings.values():
        mapped_cids.update(cids)
    matched_cids = mapped_cids & set(facts.keys())
    print(f"  campaign match: {len(matched_cids)}/{len(mapped_cids)} mapping campaigns have install data")

    # 计算每个 campaign 关联了多少个素材（用于均分安装）
    campaign_material_count = defaultdict(int)
    for mat_key, cids in mappings.items():
        for cid in cids:
            campaign_material_count[cid] += 1

    # 按标签维度聚合
    results: dict[str, list[dict]] = {}
    for dim_name, dim_col in TAG_DIMENSIONS.items():
        dim_stats: dict[str, dict] = defaultdict(lambda: {
            "installs": 0.0, "events": 0, "paid_events": 0, "revenue_usd": 0.0,
            "material_count": 0, "weight_sum": 0.0,
        })

        for mat_key, tags in analyzed.items():
            tag_value = tags.get(dim_name, "")
            if not tag_value or tag_value == "未识别到":
                continue

            # 拆分多值标签
            tag_values = [t.strip() for t in str(tag_value).replace("、", ",").split(",") if t.strip()]

            # 找该素材的投放效果（campaign 级安装按素材数均分）
            mat_cids = mappings.get(mat_key, [])
            mat_installs = 0.0
            mat_revenue = 0.0
            mat_events = 0.0
            mat_paid = 0.0
            for cid in mat_cids:
                f = facts.get(cid, {})
                share = 1.0 / max(campaign_material_count.get(cid, 1), 1)
                mat_installs += f.get("installs", 0) * share
                mat_revenue += f.get("revenue_usd", 0) * share
                mat_events += f.get("events", 0) * share
                mat_paid += f.get("paid_events", 0) * share

            weight = mat_installs if mat_installs > 0 else 1

            for tv in tag_values:
                s = dim_stats[tv]
                s["installs"] += mat_installs
                s["events"] += mat_events
                s["paid_events"] += mat_paid
                s["revenue_usd"] += mat_revenue
                s["material_count"] += 1
                s["weight_sum"] += weight

        # 计算加权平均
        dim_result = []
        for tag_value, stats in sorted(dim_stats.items(), key=lambda x: x[1]["installs"], reverse=True):
            n = stats["material_count"]
            w = stats["weight_sum"]
            dim_result.append({
                "维度": dim_name,
                "标签值": tag_value,
                "素材数": n,
                "总安装": stats["installs"],
                "总事件": stats["events"],
                "总收入": round(stats["revenue_usd"], 2),
                "平均安装/素材": round(stats["installs"] / n, 1) if n else 0,
                "平均收入/素材": round(stats["revenue_usd"] / n, 2) if n else 0,
            })
        results[dim_name] = dim_result

    conn.close()
    return results


def save_results(results: dict):
    """将聚合结果写入 SQLite 和 JSON."""
    conn = sqlite3.connect(str(DB_PATH))

    # 建表
    conn.execute("""CREATE TABLE IF NOT EXISTS aggregation_results (
        dimension TEXT, tag_value TEXT, material_count INTEGER,
        total_installs REAL, total_events REAL, total_revenue REAL,
        avg_installs REAL, avg_revenue REAL,
        updated_at TEXT, PRIMARY KEY (dimension, tag_value)
    )""")

    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for dim_name, dim_results in results.items():
        for r in dim_results:
            conn.execute("""INSERT OR REPLACE INTO aggregation_results
                (dimension, tag_value, material_count, total_installs, total_events, total_revenue, avg_installs, avg_revenue, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (dim_name, r["标签值"], r["素材数"], r["总安装"], r["总事件"], r["总收入"], r["平均安装/素材"], r["平均收入/素材"], now))
            count += 1
    conn.commit()
    conn.close()

    # JSON
    json_path = ROOT / "data" / "aggregation_results.json"
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved: {json_path} ({count} rows)")


def main():
    parser = argparse.ArgumentParser(description="MOVA 标签维度效果聚合")
    parser.add_argument("--date", help="日期过滤 YYYY-MM-DD")
    args = parser.parse_args()

    print("Querying tag × performance...")
    results = aggregate(args.date)
    for dim_name, dim_results in results.items():
        print(f"  {dim_name}: {len(dim_results)} values, top: {dim_results[0]['标签值'] if dim_results else 'N/A'}")

    save_results(results)
    print("Done")


if __name__ == "__main__":
    main()
