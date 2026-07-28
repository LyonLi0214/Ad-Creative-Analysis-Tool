import os
"""素材表现排行 — TOP/BOTTOM 推送到飞书 Base.

从本地 SQLite 聚合数据中找出表现最好/最差的素材，生成 AI 判断，推送到 Base 素材表现排行表。

Usage:
    python push_top_bottom.py                # 推送全量排行
    python push_top_bottom.py --dry-run       # 只打印不推送
"""

from __future__ import annotations

import argparse, hashlib, json, os, sqlite3, sys, time
from datetime import datetime, timezone
from pathlib import Path
from collections import defaultdict

import requests

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "mova_materials.sqlite3"
SECRET_PATH = ROOT / ".env"
BASE_TOKEN = os.environ.get("FEISHU_BASE_TOKEN", "")
TABLE_RANKING = "tblD8tjxvItKsElO"

def load_config():
    for path in (ROOT / ".env", SECRET_PATH):
        if not path.is_file(): continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line=line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k,v=line.split("=",1); os.environ[k.strip()]=v.strip()


def get_material_performance():
    """从 SQLite 获取每条素材当日投放表现."""
    conn = sqlite3.connect(str(DB_PATH)); conn.row_factory = sqlite3.Row

    # 取最新日期
    latest_date = conn.execute("SELECT MAX(fact_date) FROM appsflyer_daily_facts").fetchone()[0]
    prev_date = conn.execute("SELECT DISTINCT fact_date FROM appsflyer_daily_facts WHERE fact_date < ? ORDER BY fact_date DESC LIMIT 1", (latest_date,)).fetchone()
    prev_date = prev_date[0] if prev_date else None

    materials = defaultdict(lambda: {"installs": 0.0, "revenue": 0.0, "installs_prev": 0.0, "revenue_prev": 0.0})

    # 映射: material_key → campaign_id
    mat_campaigns = defaultdict(set)
    for r in conn.execute("SELECT material_key, LOWER(campaign_id) as cid FROM material_appsflyer_mapping WHERE campaign_id != ''"):
        mat_campaigns[r["material_key"]].add(r["cid"])

    # Campaign 当日安装数据
    camp_data = {}
    for r in conn.execute("SELECT LOWER(campaign_id) as cid, SUM(installs) as i, SUM(revenue_usd) as r FROM appsflyer_daily_facts WHERE fact_date=? GROUP BY LOWER(campaign_id)", (latest_date,)):
        camp_data[r["cid"]] = (r["i"] or 0, r["r"] or 0.0)
    # 前日数据（算趋势）
    camp_data_prev = {}
    if prev_date:
        for r in conn.execute("SELECT LOWER(campaign_id) as cid, SUM(installs) as i, SUM(revenue_usd) as r FROM appsflyer_daily_facts WHERE fact_date=? GROUP BY LOWER(campaign_id)", (prev_date,)):
            camp_data_prev[r["cid"]] = (r["i"] or 0, r["r"] or 0.0)

    # 每个 campaign 关联了多少素材
    camp_mat_count = defaultdict(int)
    for mk, cids in mat_campaigns.items():
        for cid in cids:
            camp_mat_count[cid] += 1

    # 计算每个素材的均分表现（当日）
    for mk, cids in mat_campaigns.items():
        for cid in cids:
            inst, rev = camp_data.get(cid, (0, 0.0))
            inst_p, rev_p = camp_data_prev.get(cid, (0, 0.0))
            share = 1.0 / max(camp_mat_count.get(cid, 1), 1)
            materials[mk]["installs"] += inst * share
            materials[mk]["revenue"] += rev * share
            materials[mk]["installs_prev"] += inst_p * share
            materials[mk]["revenue_prev"] += rev_p * share

    # 补充标签
    for r in conn.execute("SELECT material_key, material_name, game_name, hook_strategy, visual_style, narrative_mode FROM ai_analysis"):
        if r["material_key"] in materials:
            materials[r["material_key"]]["material_name"] = r["material_name"]
            materials[r["material_key"]]["game_name"] = r["game_name"] or "心动防线"
            tags = [r[t] for t in ["hook_strategy", "visual_style", "narrative_mode"] if r[t] and r[t] != "未识别到"]
            materials[r["material_key"]]["tags"] = " | ".join(tags[:3])

    conn.close()

    # 只保留有标签+当日有数据的
    result = []
    for mk, m in materials.items():
        if m.get("tags"):
            result.append(m)
    return result, latest_date


def generate_judgment(m: dict, rank_type: str) -> str:
    """生成简要判断."""
    tags = m.get("tags", "")
    inst = m["installs"]
    rev = m["revenue"]
    inst_p = m.get("installs_prev", 0)
    rev_p = m.get("revenue_prev", 0)

    trend = ""
    if inst_p > 0:
        chg = (inst - inst_p) / inst_p
        trend = f"较前日{'↑' if chg > 0 else '↓'}{abs(chg):.0%}。"
    elif inst > 0:
        trend = "新投放素材。"

    if "TOP" in rank_type:
        return f"标签组合：{tags}。{trend}建议分析其创意要素，考虑复用。"
    else:
        return f"标签组合：{tags}。{trend}建议复盘原因，考虑优化或停投。"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("Querying material performance...")
    materials, latest_date = get_material_performance()
    print(f"  {len(materials)} materials with performance data + tags, date={latest_date}")

    if not materials:
        print("No data to push")
        return

    # TOP: 有实际安装的素材按安装/收入排序
    with_installs = [m for m in materials if m["installs"] > 0.01]
    with_revenue = [m for m in materials if m["revenue"] > 0.01]

    by_installs = sorted(with_installs, key=lambda m: m["installs"], reverse=True)
    by_revenue = sorted(with_revenue, key=lambda m: m["revenue"], reverse=True)

    rankings = []

    # TOP 3 by installs (仅有实际安装>0的素材)
    if by_installs:
        for m in by_installs[:3]:
            inst_p = m.get("installs_prev", 0)
            trend = f"↑{(m['installs']-inst_p)/max(inst_p,0.01)*100:.0f}%" if inst_p > 0.01 else "NEW"
            rankings.append({**m, "rank_type": "TOP安装",
                             "metric": f"{m['installs']:.1f}安装 ({latest_date}) {trend}"})

    # TOP 3 by revenue
    for m in by_revenue[:3]:
        rev_p = m.get("revenue_prev", 0)
        trend = f"↑{(m['revenue']-rev_p)/max(rev_p,0.01)*100:.0f}%" if rev_p > 0.01 else "NEW"
        rankings.append({**m, "rank_type": "TOP收入",
                         "metric": f"${m['revenue']:.2f} ({latest_date}) {trend}"})

    # BOTTOM 安装
    top_keys = {id(m) for m in (by_installs[:3] if by_installs else []) + by_revenue[:3]}
    if by_installs and len(by_installs) > 3:
        for m in sorted(by_installs, key=lambda m: m["installs"])[:3]:
            if id(m) not in top_keys:
                rankings.append({**m, "rank_type": "BOTTOM安装",
                                 "metric": f"{m['installs']:.1f}安装 ({latest_date})"})

    # BOTTOM 收入
    if len(by_revenue) > 3:
        for m in sorted(by_revenue, key=lambda m: m["revenue"])[:3]:
            if id(m) not in top_keys:
                rankings.append({**m, "rank_type": "BOTTOM收入",
                                 "metric": f"${m['revenue']:.2f} ({latest_date})"})

    if args.dry_run:
        for i, r in enumerate(rankings):
            rt = r.get('rank_type', '?')
            mn = r.get('material_name', '?')
            mt = r.get('metric', '?')
            tg = r.get('tags', '')
            print(f'{i+1}. [{rt}] {mn[:50]}')
            print(f'   metric: {mt} | tags: {tg}')
            print(f'   judgment: {generate_judgment(r, rt)}')
        return

    # Push to Base — first clear old records
    load_config()
    r = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", json={"app_id": os.environ["FEISHU_APP_ID"], "app_secret": os.environ["FEISHU_APP_SECRET"]}, timeout=10)
    token = r.json()["tenant_access_token"]
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Clear old ranking records
    old_rids = []
    pt = None
    while True:
        p = {"page_size": 200}
        if pt: p["page_token"] = pt
        r = requests.get(f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_RANKING}/records", headers=headers, params=p, timeout=30)
        d = r.json().get("data", {})
        for it in d.get("items", []): old_rids.append(it["record_id"])
        if not d.get("has_more"): break
        pt = d.get("page_token")
    # Batch delete — use records batch delete if available, else skip (will overwrite by clearing first)
    for rid in old_rids:
        requests.delete(f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_RANKING}/records/{rid}", headers=headers, timeout=10)
    print(f"  Cleared {len(old_rids)} old ranking records")

    today_ts = int(datetime.now().timestamp() * 1000)
    pushed = 0
    for rank in rankings:
        fields = {
            "素材名称": rank.get("material_name", "")[:200],
            "游戏名称": rank.get("game_name", "心动防线"),
            "排行类型": rank["rank_type"],
            "指标值": rank["metric"],
            "标签摘要": rank.get("tags", ""),
            "AI判断": generate_judgment(rank, rank["rank_type"]),
            "统计日期": today_ts,
        }
        r = requests.post(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_RANKING}/records",
            headers=headers, json={"fields": fields}, timeout=10,
        )
        if r.json().get("code") == 0:
            pushed += 1
        else:
            print(f"  Push error: {r.json().get('msg')}")
        time.sleep(0.2)

    print(f"Pushed {pushed}/{len(rankings)} to 素材表现排行")


if __name__ == "__main__":
    main()
