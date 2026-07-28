"""MOVA 每日数据管道编排器.

按顺序串联所有步骤，每步完成后记录日志，失败则跳过后续依赖步骤。

Usage:
    python pipeline_daily.py                    # 完整流程
    python pipeline_daily.py --from pull        # 从某个步骤开始
    python pipeline_daily.py --dry-run           # 只看不跑

管道步骤:
    1. pull      — 从 AppsFlyer 拉取并聚合 daily facts
    2. detect    — 发现新素材 → OSS 匹配 → 下载 → 注册 → 映射
    3. analyze   — 对未分析素材跑 AI 拆解（batch_analyze）
    4. aggregate — 六维标签效果聚合
    5. sync      — SQLite → 飞书 Base 同步
    6. dashboard — 生成 HTML 看板
    7. report    — Bot 推送日报到飞书群
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)

STEPS = ["pull", "detect", "analyze", "aggregate", "sync", "dashboard", "report"]

STEP_SCRIPTS = {
    "pull":      ("appsflyer_pull.py", ["--from", "{date}"]),
    "detect":    ("pipeline_new_material.py", ["--date", "{date}"]),
    "analyze":   ("batch_analyze.py", []),
    "aggregate": ("aggregate.py", []),
    "sync":      ("sync_to_feishu_base.py", ["--table", "all"]),
    "dashboard": ("gen_dashboard.py", []),
    "report":    ("_push_report.py", ["--date", "{date}"]),
}


def run_step(name: str, step_date: str, dry_run: bool = False) -> dict:
    """运行单个管道步骤."""
    script, args_tmpl = STEP_SCRIPTS.get(name, ("", []))
    if not script:
        return {"step": name, "ok": False, "error": "unknown step"}

    script_path = ROOT / script
    if not script_path.exists():
        return {"step": name, "ok": False, "error": f"script not found: {script}"}

    args = [arg.format(date=step_date) for arg in args_tmpl]
    cmd = [str(PYTHON), str(script_path)] + args

    if dry_run:
        print(f"  [DRY RUN] {' '.join(str(x) for x in cmd[-3:])}")
        return {"step": name, "ok": True, "dry_run": True}

    print(f"  ⏳ {name}: {' '.join(str(x) for x in cmd[-3:])}")
    t0 = time.time()

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600, cwd=str(ROOT))
        elapsed = time.time() - t0
        ok = result.returncode == 0

        # 取最后一行作摘要
        lines = [l for l in result.stdout.splitlines() if l.strip()]
        summary = lines[-1] if lines else (result.stderr[-200:] if result.stderr else "")

        if ok:
            print(f"  ✅ {name}: 完成 ({elapsed:.0f}s) — {summary[:120]}")
        else:
            print(f"  ❌ {name}: 失败 (exit={result.returncode}) — {result.stderr[:200]}")
            print(f"     stdout tail: {lines[-1][:200] if lines else ''}")

        return {
            "step": name, "ok": ok, "elapsed": round(elapsed, 1),
            "summary": summary[:200], "exit_code": result.returncode,
        }
    except subprocess.TimeoutExpired:
        print(f"  ❌ {name}: 超时 (10min)")
        return {"step": name, "ok": False, "error": "timeout"}


def main() -> int:
    today = date.today()
    yesterday = (today - timedelta(days=1)).isoformat()

    parser = argparse.ArgumentParser(description="MOVA 每日管道编排器")
    parser.add_argument("--from", dest="start_from", choices=STEPS, default="pull")
    parser.add_argument("--date", default=yesterday, help="数据日期，默认昨天")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    step_date = args.date
    start_index = STEPS.index(args.start_from)
    steps_to_run = STEPS[start_index:]

    print(f"MOVA Pipeline | 日期: {step_date} | 从 '{args.start_from}' 开始 | {'[DRY RUN]' if args.dry_run else ''}")
    print(f"{'='*60}")

    results: list[dict] = []
    pipeline_ok = True

    for step in steps_to_run:
        result = run_step(step, step_date, dry_run=args.dry_run)
        results.append(result)

        if not result.get("ok"):
            # pull/detect 失败阻断后续；analyze/aggregate/dashboard/report 失败不阻断
            if step in ("pull", "detect", "sync"):
                pipeline_ok = False
                print(f"  ⛔ 关键步骤 '{step}' 失败，管道中断")
                break
            else:
                print(f"  ⚠ '{step}' 失败，继续后续步骤")

    # 写执行日志
    log_entry = {
        "pipeline": "daily",
        "date": step_date,
        "started_from": args.start_from,
        "dry_run": args.dry_run,
        "ok": pipeline_ok,
        "steps": results,
    }
    log_path = LOG_DIR / f"pipeline_{today.isoformat()}.json"
    log_path.write_text(json.dumps(log_entry, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n日志: {log_path}")

    # 汇总
    ok_count = sum(1 for r in results if r.get("ok"))
    fail_count = sum(1 for r in results if not r.get("ok"))
    print(f"结果: {ok_count}/{len(results)} 成功, {fail_count} 失败")

    return 0 if pipeline_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
