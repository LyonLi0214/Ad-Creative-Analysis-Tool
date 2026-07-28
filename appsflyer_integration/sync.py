from __future__ import annotations

import argparse
import json
import os
from datetime import date, timedelta
from pathlib import Path

from appsflyer_integration.client import AppsFlyerClient, AppsFlyerError, utc_timestamp, write_json_atomic
from appsflyer_integration.normalize import normalize_attribution


ROOT = Path(__file__).resolve().parents[1]
SECRET_PATH = ROOT / ".env"
OUTPUT_ROOT = ROOT / "appsflyer_data"


def load_env(path: Path, overwrite: bool = False) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if overwrite or key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip()


def configuration() -> tuple[str, list[str]]:
    load_env(ROOT / ".env")
    load_env(SECRET_PATH, overwrite=True)
    token = os.getenv("APPSFLYER_API_TOKEN", "").strip()
    app_ids = [os.getenv("APPSFLYER_ANDROID_APP_ID", "").strip(), os.getenv("APPSFLYER_IOS_APP_ID", "").strip()]
    return token, [app_id for app_id in app_ids if app_id]


def check(client: AppsFlyerClient, configured_apps: list[str]) -> dict:
    remote = client.list_apps()
    available = {str(item.get("id")) for item in remote}
    return {
        "checked_at": utc_timestamp(),
        "token_valid": True,
        "configured_apps": [{"app_id": app_id, "available": app_id in available} for app_id in configured_apps],
        "account_app_count": len(remote),
    }


def pull(client: AppsFlyerClient, app_ids: list[str], from_date: str, to_date: str) -> dict:
    run_id = f"{from_date}_{to_date}"
    manifest = {"run_id": run_id, "started_at": utc_timestamp(), "reports": [], "errors": []}
    for app_id in app_ids:
        for report in ("installs", "in_app_events"):
            try:
                result = client.pull_raw(app_id, report, from_date, to_date)
                records = [{"attribution": normalize_attribution(row, app_id), "raw": row} for row in result.rows]
                target = OUTPUT_ROOT / app_id / run_id / f"{report}.json"
                write_json_atomic(target, records)
                manifest["reports"].append({"app_id": app_id, "report": report, "rows": len(records), "path": str(target.relative_to(ROOT))})
            except AppsFlyerError as exc:
                manifest["errors"].append({"app_id": app_id, "report": report, "status_code": exc.status_code, "message": str(exc)})
    manifest["completed_at"] = utc_timestamp()
    write_json_atomic(OUTPUT_ROOT / "latest_manifest.json", manifest)
    return manifest


def main() -> int:
    yesterday = date.today() - timedelta(days=1)
    parser = argparse.ArgumentParser(description="MOVA AppsFlyer read-only integration")
    parser.add_argument("command", choices=("status", "check", "pull"))
    parser.add_argument("--from", dest="from_date", default=yesterday.isoformat())
    parser.add_argument("--to", dest="to_date", default=yesterday.isoformat())
    args = parser.parse_args()
    token, app_ids = configuration()
    status = {"token_configured": bool(token), "configured_app_count": len(app_ids), "output_root": str(OUTPUT_ROOT)}
    if args.command == "status":
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0 if token and app_ids else 2
    if not token:
        print(json.dumps({**status, "error": "请在 .mova-secrets.env 填写 APPSFLYER_API_TOKEN"}, ensure_ascii=False, indent=2))
        return 2
    client = AppsFlyerClient(token)
    try:
        result = check(client, app_ids) if args.command == "check" else pull(client, app_ids, args.from_date, args.to_date)
    except (AppsFlyerError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc), "status_code": getattr(exc, "status_code", None)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
