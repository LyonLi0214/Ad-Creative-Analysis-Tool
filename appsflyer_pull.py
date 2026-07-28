"""MOVA AppsFlyer 每日数据拉取 + SQLite 聚合入库.

Usage:
    python appsflyer_pull.py                          # 拉取昨天
    python appsflyer_pull.py --from 2026-07-21 --to 2026-07-22
    python appsflyer_pull.py --dry-run                 # 只拉不写
    python appsflyer_pull.py --today                   # 拉取今天(到现在)

流程:
    AppsFlyer API → raw CSV → normalize_attribution → 按维度聚合 → UPSERT SQLite
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from appsflyer_integration.client import AppsFlyerClient, AppsFlyerError
from appsflyer_integration.normalize import normalize_attribution

ROOT = Path(__file__).resolve().parent
SECRET_PATH = ROOT / ".env"
DB_PATH = ROOT / "data" / "mova_materials.sqlite3"
MANIFEST_PATH = ROOT / "appsflyer_data" / "latest_manifest.json"

PLATFORM_MAP = {
    "com.singularity.xdfxrw": "Android",
    "id6766773102": "iOS",
}


# ── config ──────────────────────────────────────────────────────────

def load_config() -> tuple[str, list[str]]:
    """从 .env 和 .mova-secrets.env 加载配置。"""
    for path in (ROOT / ".env", SECRET_PATH):
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key, val = key.strip(), val.strip()
            if key and key not in os.environ:
                os.environ[key] = val

    token = os.getenv("APPSFLYER_API_TOKEN", "").strip()
    app_ids = [
        os.getenv("APPSFLYER_ANDROID_APP_ID", "").strip(),
        os.getenv("APPSFLYER_IOS_APP_ID", "").strip(),
    ]
    return token, [a for a in app_ids if a]


# ── aggregation ─────────────────────────────────────────────────────

def make_fact_key(app_id: str, fact_date: str, campaign_id: str, adset_id: str, ad_id: str) -> str:
    raw = "|".join([app_id, fact_date, campaign_id or "", adset_id or "", ad_id or ""])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _platform(app_id: str, raw: dict) -> str:
    """确定平台：优先从 CSV Platform 列，其次按 app_id 推断。"""
    csv_platform = str(raw.get("Platform", "")).strip()
    if csv_platform:
        return csv_platform
    return PLATFORM_MAP.get(app_id, "Unknown")


def aggregate_records(
    records: list[dict], app_id: str, fact_date: str, source_run: str,
) -> list[dict]:
    """将归一化后的 installs + events 记录按维度聚合为 daily facts。

    records 中每条包含 'attribution' (normalize_attribution 输出) 和 'raw' (CSV 行)。
    通过 raw['Event Name'] 是否为空区分 install / event。
    """
    groups: dict[tuple, dict] = defaultdict(lambda: {
        "installs": 0, "events": 0, "paid_events": 0, "revenue_usd": 0.0,
        "campaign_name": "", "adset_name": "", "ad_name": "", "platform": "",
    })

    for rec in records:
        attr = rec.get("attribution", {})
        raw = rec.get("raw", {})

        cid = attr.get("campaign_id", "")
        asid = attr.get("adset_id", "")
        aid = attr.get("ad_id", "")

        key = (cid, asid, aid)
        g = groups[key]

        # 第一次出现时记录名称
        if not g["campaign_name"]:
            g["campaign_name"] = attr.get("campaign", "")
        if not g["adset_name"]:
            g["adset_name"] = attr.get("adset", "")
        if not g["ad_name"]:
            g["ad_name"] = attr.get("ad", "")
        if not g["platform"]:
            g["platform"] = _platform(app_id, raw)

        event_name = str(raw.get("Event Name", "")).strip().lower()
        revenue = float(raw.get("Event Revenue USD", "0") or "0")

        # install 报告的 Event Name 是 "install"，事件报告的是 "af_purchase" 等
        if event_name and event_name != "install":
            g["events"] += 1
            if revenue > 0:
                g["paid_events"] += 1
                g["revenue_usd"] += revenue
        else:
            g["installs"] += 1

    facts: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    for (cid, asid, aid), g in groups.items():
        facts.append({
            "fact_key": make_fact_key(app_id, fact_date, cid, asid, aid),
            "fact_date": fact_date,
            "app_id": app_id,
            "platform": g["platform"],
            "campaign_id": cid,
            "adset_id": asid,
            "adset_name": g["adset_name"],
            "campaign_name": g["campaign_name"],
            "ad_id": aid,
            "ad_name": g["ad_name"],
            "installs": g["installs"],
            "events": g["events"],
            "paid_events": g["paid_events"],
            "revenue_usd": round(g["revenue_usd"], 2),
            "source_run": source_run,
            "updated_at": now_iso,
        })
    return facts


# ── SQLite ───────────────────────────────────────────────────────────

def upsert_facts(db_path: Path, facts: list[dict], dry_run: bool = False) -> int:
    """按 fact_key UPSERT 到 SQLite appsflyer_daily_facts 表。"""
    if dry_run:
        print(f"[DRY RUN] 将写入 {len(facts)} 条 daily facts:")
        for f in sorted(facts, key=lambda x: (-x["installs"], -x["events"]))[:10]:
            print(f"  {f['fact_date']} | {f['app_id'][-20:]} | {f['platform']} | "
                  f"installs={f['installs']} events={f['events']} revenue=${f['revenue_usd']:.2f} | "
                  f"campaign={f['campaign_name'][:30]}")
        if len(facts) > 10:
            print(f"  ... 共 {len(facts)} 条")
        return len(facts)

    conn = sqlite3.connect(str(db_path))
    inserted, updated = 0, 0
    for f in facts:
        cur = conn.execute("""
            INSERT INTO appsflyer_daily_facts
                (fact_key, fact_date, app_id, platform, campaign_id, campaign_name,
                 adset_id, adset_name, ad_id, ad_name,
                 installs, events, paid_events, revenue_usd, source_run, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fact_key) DO UPDATE SET
                installs=excluded.installs, events=excluded.events,
                paid_events=excluded.paid_events, revenue_usd=excluded.revenue_usd,
                campaign_name=excluded.campaign_name,
                source_run=excluded.source_run, updated_at=excluded.updated_at
        """, (
            f["fact_key"], f["fact_date"], f["app_id"], f["platform"],
            f["campaign_id"], f["campaign_name"],
            f["adset_id"], f["adset_name"],
            f["ad_id"], f["ad_name"],
            f["installs"], f["events"], f["paid_events"], f["revenue_usd"],
            f["source_run"], f["updated_at"],
        ))
        if cur.rowcount == 1:
            inserted += 1
        else:
            updated += 1
    conn.commit()
    conn.close()
    print(f"SQLite: {inserted} 条新增, {updated} 条更新, 共 {len(facts)} 条")
    return len(facts)


# ── main ─────────────────────────────────────────────────────────────

def main() -> int:
    yesterday = date.today() - timedelta(days=1)

    parser = argparse.ArgumentParser(description="MOVA AppsFlyer 每日拉取 + SQLite 入库")
    parser.add_argument("--from", dest="from_date", default=yesterday.isoformat())
    parser.add_argument("--to", dest="to_date", default=None)
    parser.add_argument("--today", action="store_true", help="拉取今天（到现在）")
    parser.add_argument("--dry-run", action="store_true", help="只拉不写 SQLite")
    args = parser.parse_args()

    if args.today:
        args.from_date = date.today().isoformat()
    to_date = args.to_date or args.from_date
    source_run = f"{args.from_date}_{to_date}"

    # Config
    token, app_ids = load_config()
    if not token:
        print("ERROR: 未配置 APPSFLYER_API_TOKEN，请在 .mova-secrets.env 中设置", file=sys.stderr)
        return 2
    if not app_ids:
        print("ERROR: 未配置 App ID", file=sys.stderr)
        return 2

    client = AppsFlyerClient(token)
    manifest = {
        "run_id": source_run,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "reports": [],
        "errors": [],
    }

    # 逐 app、逐 report 拉取
    raw_records: dict[str, list[dict]] = defaultdict(list)  # app_id → records
    for app_id in app_ids:
        for report in ("installs", "in_app_events"):
            try:
                result = client.pull_raw(app_id, report, args.from_date, to_date)
                records = [
                    {"attribution": normalize_attribution(row, app_id), "raw": row}
                    for row in result.rows
                ]
                raw_records[app_id].extend(records)
                manifest["reports"].append({
                    "app_id": app_id, "report": report,
                    "rows": len(records), "date_range": f"{args.from_date}→{to_date}",
                })
                print(f"  {app_id} / {report}: {len(records)} 条")
            except AppsFlyerError as exc:
                msg = f"app={app_id} report={report} status={exc.status_code}: {exc}"
                print(f"  ERROR: {msg}", file=sys.stderr)
                manifest["errors"].append(msg)

    if not raw_records:
        print("没有拉取到任何数据", file=sys.stderr)
        return 1

    # 聚合 + 入库
    all_facts: list[dict] = []
    for app_id, records in raw_records.items():
        facts = aggregate_records(records, app_id, args.from_date, source_run)
        all_facts.extend(facts)
        print(f"  {app_id}: 聚合 {len(records)} 条 → {len(facts)} 个 daily facts")

    if not all_facts:
        print("聚合后无数据", file=sys.stderr)
        return 1

    total = upsert_facts(DB_PATH, all_facts, dry_run=args.dry_run)

    # Manifest
    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    manifest["total_facts"] = total
    if not args.dry_run:
        MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        MANIFEST_PATH.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Manifest 已更新: {MANIFEST_PATH}")

    # 统计摘要
    total_installs = sum(f["installs"] for f in all_facts)
    total_events = sum(f["events"] for f in all_facts)
    total_revenue = sum(f["revenue_usd"] for f in all_facts)
    print(f"摘要: {total_installs} 安装, {total_events} 事件, ${total_revenue:.2f} 收入, {total} 条 facts")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
