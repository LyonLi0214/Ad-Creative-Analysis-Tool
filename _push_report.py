"""Bot 推送报告 — 用 ReportEngine 生成日报，发送到飞书群.

供 pipeline_daily.py 调用，也可手动触发。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
SECRET_PATH = ROOT / ".env"
sys.path.insert(0, str(ROOT))
from feishu_bot.report_engine import ReportEngine


def send_to_feishu(text: str) -> bool:
    """发送消息到飞书默认群."""
    for path in (ROOT / ".env", SECRET_PATH):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")

    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret}, timeout=10,
    )
    token = r.json()["tenant_access_token"]

    state_path = ROOT / "logs" / "feishu_bot_state.json"
    chat_id = ""
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        chat_id = state.get("default_chat_id", "")

    if not chat_id:
        print("缺少 default_chat_id，跳过推送", file=sys.stderr)
        return False

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text[:8000]}, ensure_ascii=False),
    }
    r = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        headers=headers, json=body, timeout=10,
    )
    ok = r.json().get("code") == 0
    print(f"推送{'✓' if ok else '✗'} → {chat_id}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="推送 MOVA 报告")
    parser.add_argument("--type", choices=("daily", "weekly", "overview", "trend"), default="daily")
    parser.add_argument("--date", help="日期 YYYY-MM-DD")
    args = parser.parse_args()

    engine = ReportEngine()

    if args.type == "daily":
        text = engine.build_daily_report(args.date)
    elif args.type == "weekly":
        text = engine.build_weekly_report(args.date)
    elif args.type == "trend":
        text = engine.build_trend()
    elif args.type == "overview":
        text = engine.build_overview()

    print(text)
    print(f"\n{'='*40}")

    ok = send_to_feishu(text)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
