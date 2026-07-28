from __future__ import annotations

import json
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = ROOT / "venv" / "Scripts" / "python.exe"
LOG_DIR = ROOT / "logs" / "meta_dashboard"


def step(name: str, args: list[str], timeout: int) -> dict:
    started = datetime.now()
    result = subprocess.run([str(PYTHON), *args], cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    return {"name": name, "ok": result.returncode == 0,
            "seconds": round((datetime.now() - started).total_seconds(), 1),
            "stdout_tail": result.stdout[-1500:], "stderr_tail": result.stderr[-1500:]}


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=13)
    jobs = [
        ("meta_pull", [str(ROOT / "meta_creative_pipeline.py"), "--from", start.isoformat(), "--to", end.isoformat(), "--chunk-days", "14"], 3600),
        ("build", [str(ROOT / "build_meta_deliverables.py")], 1200),
        ("deploy", [str(ROOT / "deploy_meta_dashboard.py")], 300),
    ]
    report = {"started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
              "date_from": start.isoformat(), "date_to": end.isoformat(), "steps": []}
    ok = True
    for name, args, timeout in jobs:
        try:
            result = step(name, args, timeout)
        except Exception as exc:
            result = {"name": name, "ok": False, "error": str(exc)}
        report["steps"].append(result)
        if not result["ok"]:
            ok = False
            break
    report["ok"] = ok
    report["completed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    path = LOG_DIR / f"daily_{date.today().isoformat()}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": ok, "log": str(path), "steps": [{"name": x["name"], "ok": x["ok"]} for x in report["steps"]]}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
