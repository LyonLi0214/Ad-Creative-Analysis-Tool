from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib import error, parse, request

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "mova_materials.sqlite3"
API = "https://graph.facebook.com"


def _print_safe(obj) -> None:
    """Print JSON line to stdout, survive Windows GBK console encoding errors."""
    try:
        print(json.dumps(obj, ensure_ascii=False), flush=True)
    except (OSError, UnicodeEncodeError):
        print(json.dumps(obj, ensure_ascii=True), flush=True)


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def _build_opener():
    """Build a urllib opener with optional proxy support."""
    proxy_url = os.getenv("META_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("https_proxy") or ""
    if proxy_url:
        handler = request.ProxyHandler({"https": proxy_url, "http": proxy_url})
        return request.build_opener(handler)
    return request.build_opener()


def api_get(path: str, params: dict | None = None, retries: int = 5) -> dict:
    token = os.environ["META_ACCESS_TOKEN"]
    configured_version = os.getenv("META_GRAPH_API_VERSION", "v25.0").strip().lower().removeprefix("v")
    try:
        version = f"v{max(25, int(configured_version.split('.')[0]))}.0"
    except ValueError:
        version = "v25.0"
    url = path if path.startswith("http") else f"{API}/{version}/{path.lstrip('/')}"
    if params:
        url += ("&" if "?" in url else "?") + parse.urlencode(params)
    opener = _build_opener()
    for attempt in range(retries):
        req = request.Request(url, headers={"Authorization": f"Bearer {token}", "User-Agent": "MOVA-Meta-Pipeline/1.0"})
        try:
            with opener.open(req, timeout=90) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            if exc.code in (429, 500, 502, 503, 504) and attempt + 1 < retries:
                time.sleep(min(2 ** attempt * 3, 30))
                continue
            raise RuntimeError(f"Meta API HTTP {exc.code}: {body[:600]}") from exc
        except error.URLError as exc:
            if attempt + 1 < retries:
                time.sleep(min(2 ** attempt * 3, 15))
                continue
            raise RuntimeError(f"Meta API unreachable after {retries} retries: {exc.reason}") from exc
    raise RuntimeError("Meta API retry exhausted")


def pages(path: str, params: dict) -> list[dict]:
    rows: list[dict] = []
    payload = api_get(path, params)
    while True:
        rows.extend(payload.get("data", []))
        nxt = payload.get("paging", {}).get("next")
        if not nxt:
            return rows
        payload = api_get(nxt)


def as_num(value, integer=False):
    if value in (None, ""):
        return 0 if integer else 0.0
    return int(float(value)) if integer else float(value)


def action_value(items, names, value_key="value") -> float:
    lookup = {x.get("action_type"): as_num(x.get(value_key)) for x in (items or [])}
    for name in names:
        if name in lookup:
            return lookup[name]
    return 0.0


def schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS meta_sync_runs (
      run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT,
      date_from TEXT NOT NULL, date_to TEXT NOT NULL, account_count INTEGER DEFAULT 0,
      row_count INTEGER DEFAULT 0, status TEXT NOT NULL, error TEXT
    );
    CREATE TABLE IF NOT EXISTS meta_creatives (
      creative_id TEXT PRIMARY KEY, creative_name TEXT, thumbnail_url TEXT,
      video_id TEXT, image_hash TEXT, object_type TEXT, raw_json TEXT, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS meta_ad_daily (
      report_date TEXT NOT NULL, account_id TEXT NOT NULL, account_name TEXT,
      account_currency TEXT, report_timezone TEXT, campaign_id TEXT, campaign_name TEXT,
      adset_id TEXT, adset_name TEXT, ad_id TEXT NOT NULL, ad_name TEXT,
      creative_id TEXT, creative_name TEXT, thumbnail_url TEXT,
      impressions INTEGER DEFAULT 0, reach INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0,
      spend REAL DEFAULT 0, ctr REAL DEFAULT 0, cpm REAL DEFAULT 0, cpc REAL DEFAULT 0,
      frequency REAL DEFAULT 0, link_clicks INTEGER DEFAULT 0, app_installs INTEGER DEFAULT 0,
      registrations INTEGER DEFAULT 0, purchases INTEGER DEFAULT 0, purchase_value REAL DEFAULT 0,
      video_plays INTEGER DEFAULT 0, thruplays INTEGER DEFAULT 0, video_p25 INTEGER DEFAULT 0,
      video_p50 INTEGER DEFAULT 0, video_p75 INTEGER DEFAULT 0, video_p100 INTEGER DEFAULT 0,
      actions_json TEXT, action_values_json TEXT, raw_json TEXT,
      is_partial_day INTEGER DEFAULT 0, source_updated_at TEXT NOT NULL,
      PRIMARY KEY (report_date, account_id, ad_id)
    );
    CREATE INDEX IF NOT EXISTS idx_meta_daily_date ON meta_ad_daily(report_date);
    CREATE INDEX IF NOT EXISTS idx_meta_daily_creative ON meta_ad_daily(creative_id, report_date);
    CREATE INDEX IF NOT EXISTS idx_meta_daily_ad ON meta_ad_daily(ad_id, report_date);
    """)


def account_list() -> list[dict]:
    configured = [x.strip().removeprefix("act_") for x in os.getenv("META_AD_ACCOUNT_IDS", "").split(",") if x.strip()]
    rows = pages("me/adaccounts", {"fields": "id,name,account_status,currency,timezone_name", "limit": 200})
    active = [x for x in rows if int(x.get("account_status", 0)) == 1]
    if configured:
        return [x for x in active if x["id"].removeprefix("act_") in configured]
    matched = [x for x in active if "心动" in x.get("name", "")]
    return matched or active


def earliest_campaign(accounts: list[dict]) -> date:
    earliest = date.today()
    for account in accounts:
        campaigns = pages(f"{account['id']}/campaigns", {"fields": "created_time", "limit": 200})
        for campaign in campaigns:
            value = campaign.get("created_time")
            if value:
                earliest = min(earliest, datetime.fromisoformat(value.replace("Z", "+00:00")).date())
    return earliest


def creative_map(ad_ids: list[str]) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for start in range(0, len(ad_ids), 50):
        ids = ad_ids[start:start + 50]
        payload = api_get("", {"ids": ",".join(ids), "fields": "id,name,creative{id,name,thumbnail_url,video_id,image_hash,object_type}"})
        for ad_id, ad in payload.items():
            result[ad_id] = ad.get("creative") or {}
    return result


FIELDS = ",".join([
    "account_id", "account_name", "campaign_id", "campaign_name", "adset_id", "adset_name", "ad_id", "ad_name",
    "date_start", "date_stop", "impressions", "reach", "clicks", "spend", "ctr", "cpm", "cpc", "frequency",
    "actions", "action_values", "video_play_actions", "video_thruplay_watched_actions", "video_p25_watched_actions",
    "video_p50_watched_actions", "video_p75_watched_actions", "video_p100_watched_actions"
])


def pull(args) -> dict:
    load_env(ROOT / ".env")
    if not os.getenv("META_ACCESS_TOKEN"):
        raise RuntimeError("META_ACCESS_TOKEN 未配置")
    accounts = account_list()
    if not accounts:
        raise RuntimeError("没有找到可用 Meta 广告账户")
    end = date.fromisoformat(args.to_date) if args.to_date else date.today()
    start = date.fromisoformat(args.from_date) if args.from_date else earliest_campaign(accounts)
    if start > end:
        raise ValueError("起始日期晚于结束日期")
    conn = sqlite3.connect(DB_PATH)
    schema(conn)
    run_id = datetime.now().strftime("meta_%Y%m%d_%H%M%S")
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    conn.execute("INSERT INTO meta_sync_runs(run_id,started_at,date_from,date_to,account_count,status) VALUES(?,?,?,?,?,?)",
                 (run_id, now, start.isoformat(), end.isoformat(), len(accounts), "running"))
    conn.commit()
    total = 0
    try:
        for account in accounts:
            cursor = start
            while cursor <= end:
                chunk_end = min(cursor + timedelta(days=args.chunk_days - 1), end)
                rows = pages(f"{account['id']}/insights", {
                    "level": "ad", "fields": FIELDS, "time_increment": 1, "limit": 500,
                    "time_range": json.dumps({"since": cursor.isoformat(), "until": chunk_end.isoformat()}),
                    "action_attribution_windows": json.dumps(["7d_click", "1d_view"]),
                })
                cmap = creative_map(sorted({r["ad_id"] for r in rows if r.get("ad_id")})) if rows else {}
                for row in rows:
                    creative = cmap.get(row.get("ad_id", ""), {})
                    cid = creative.get("id")
                    if cid:
                        conn.execute("""INSERT INTO meta_creatives VALUES(?,?,?,?,?,?,?,?)
                          ON CONFLICT(creative_id) DO UPDATE SET creative_name=excluded.creative_name,
                          thumbnail_url=excluded.thumbnail_url,video_id=excluded.video_id,image_hash=excluded.image_hash,
                          object_type=excluded.object_type,raw_json=excluded.raw_json,updated_at=excluded.updated_at""",
                          (cid, creative.get("name"), creative.get("thumbnail_url"), creative.get("video_id"),
                           creative.get("image_hash"), creative.get("object_type"), json.dumps(creative, ensure_ascii=False), now))
                    actions = row.get("actions") or []
                    values = row.get("action_values") or []
                    vals = (
                        row.get("date_start"), row.get("account_id"), row.get("account_name") or account.get("name"),
                        account.get("currency"), os.getenv("ADS_REPORT_TIMEZONE", "Asia/Shanghai"),
                        row.get("campaign_id"), row.get("campaign_name"), row.get("adset_id"), row.get("adset_name"),
                        row.get("ad_id"), row.get("ad_name"), cid, creative.get("name"), creative.get("thumbnail_url"),
                        as_num(row.get("impressions"), True), as_num(row.get("reach"), True), as_num(row.get("clicks"), True),
                        as_num(row.get("spend")), as_num(row.get("ctr")), as_num(row.get("cpm")), as_num(row.get("cpc")),
                        as_num(row.get("frequency")), int(action_value(actions, ["link_click"])),
                        int(action_value(actions, ["mobile_app_install", "omni_app_install"])),
                        int(action_value(actions, ["complete_registration", "app_custom_event.fb_mobile_complete_registration"])),
                        int(action_value(actions, ["purchase", "app_custom_event.fb_mobile_purchase"])),
                        action_value(values, ["purchase", "app_custom_event.fb_mobile_purchase"]),
                        int(action_value(row.get("video_play_actions"), ["video_view"])),
                        int(action_value(row.get("video_thruplay_watched_actions"), ["video_view"])),
                        int(action_value(row.get("video_p25_watched_actions"), ["video_view"])),
                        int(action_value(row.get("video_p50_watched_actions"), ["video_view"])),
                        int(action_value(row.get("video_p75_watched_actions"), ["video_view"])),
                        int(action_value(row.get("video_p100_watched_actions"), ["video_view"])),
                        json.dumps(actions, ensure_ascii=False), json.dumps(values, ensure_ascii=False),
                        json.dumps(row, ensure_ascii=False), int(row.get("date_start") == date.today().isoformat()), now,
                    )
                    conn.execute(f"""INSERT INTO meta_ad_daily VALUES({','.join(['?'] * len(vals))})
                      ON CONFLICT(report_date,account_id,ad_id) DO UPDATE SET
                      account_name=excluded.account_name,account_currency=excluded.account_currency,report_timezone=excluded.report_timezone,
                      campaign_id=excluded.campaign_id,campaign_name=excluded.campaign_name,adset_id=excluded.adset_id,adset_name=excluded.adset_name,
                      ad_name=excluded.ad_name,creative_id=excluded.creative_id,creative_name=excluded.creative_name,thumbnail_url=excluded.thumbnail_url,
                      impressions=excluded.impressions,reach=excluded.reach,clicks=excluded.clicks,spend=excluded.spend,ctr=excluded.ctr,cpm=excluded.cpm,
                      cpc=excluded.cpc,frequency=excluded.frequency,link_clicks=excluded.link_clicks,app_installs=excluded.app_installs,
                      registrations=excluded.registrations,purchases=excluded.purchases,purchase_value=excluded.purchase_value,
                      video_plays=excluded.video_plays,thruplays=excluded.thruplays,video_p25=excluded.video_p25,video_p50=excluded.video_p50,
                      video_p75=excluded.video_p75,video_p100=excluded.video_p100,actions_json=excluded.actions_json,
                      action_values_json=excluded.action_values_json,raw_json=excluded.raw_json,is_partial_day=excluded.is_partial_day,
                      source_updated_at=excluded.source_updated_at""", vals)
                    total += 1
                conn.commit()
                _print_safe({"account": account.get("name"), "from": cursor.isoformat(), "to": chunk_end.isoformat(), "rows": len(rows)})
                cursor = chunk_end + timedelta(days=1)
        conn.execute("UPDATE meta_sync_runs SET completed_at=?,row_count=?,status='success' WHERE run_id=?",
                     (datetime.now().astimezone().isoformat(timespec="seconds"), total, run_id))
        conn.commit()
        return {"run_id": run_id, "date_from": start.isoformat(), "date_to": end.isoformat(), "accounts": len(accounts), "api_rows": total}
    except Exception as exc:
        conn.execute("UPDATE meta_sync_runs SET completed_at=?,row_count=?,status='failed',error=? WHERE run_id=?",
                     (datetime.now().astimezone().isoformat(timespec="seconds"), total, str(exc)[:1000], run_id))
        conn.commit()
        raise
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Meta ad/creative daily performance pipeline")
    parser.add_argument("--from", dest="from_date", help="YYYY-MM-DD; omitted = earliest campaign")
    parser.add_argument("--to", dest="to_date", help="YYYY-MM-DD; omitted = today")
    parser.add_argument("--chunk-days", type=int, default=30)
    args = parser.parse_args()
    result = pull(args)
    _print_safe(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
