"""批量并行素材分析 — 对已下载素材跑 local_recognition + Claude 分析.

Usage:
    python batch_analyze.py --workers 2     # 2个并行（默认）
    python batch_analyze.py --limit 10       # 最多分析10个
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "mova_materials.sqlite3"
DOWNLOAD_DIR = ROOT / "downloads" / "new_materials"
RECOGNITION_VENV = ROOT / ".venv-recognition" / "Scripts" / "python.exe"


def find_pending():
    """找已下载但未分析的素材."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    analyzed = {r[0] for r in conn.execute("SELECT material_key FROM ai_analysis")}
    registered = conn.execute("SELECT * FROM material_registry ORDER BY updated_at DESC").fetchall()
    conn.close()

    pending = []
    for reg in registered:
        if reg["material_key"] in analyzed:
            continue
        # 找下载文件
        material_name = reg["material_name"]
        for f in DOWNLOAD_DIR.iterdir():
            if f.suffix.lower() not in {".mp4", ".mov", ".avi", ".webm", ".png", ".jpg", ".jpeg"}:
                continue
            norm_f = re.sub(r"[_\-\s]+", "", f.stem.lower())
            norm_m = re.sub(r"[_\-\s]+", "", Path(material_name).stem.lower())
            if norm_f in norm_m or norm_m in norm_f:
                pending.append({"material_key": reg["material_key"], "material_name": material_name, "file": str(f)})
                break
    return pending


def process_one(item: dict) -> dict | None:
    """处理一个素材：本地识别 → Claude 分析."""
    material_key = item["material_key"]
    material_name = item["material_name"]
    file_path = Path(item["file"])
    evidence_path = file_path.parent / f"{file_path.stem}_evidence.json"

    result = {"material_key": material_key, "material_name": material_name[:50]}

    # Step 1: 本地识别
    if not evidence_path.exists():
        work_dir = file_path.parent / f"{file_path.stem}_work"
        t0 = time.time()
        try:
            subprocess.run(
                [str(RECOGNITION_VENV), "-m", "local_recognition.recognize_asset",
                 str(file_path), "--work-dir", str(work_dir),
                 "--output", str(evidence_path), "--language", "ja"],
                capture_output=True, text=True, timeout=600,
                encoding="utf-8", errors="replace",
            )
            elapsed = time.time() - t0
            if evidence_path.exists():
                result["recognition"] = f"ok ({elapsed:.0f}s)"
            else:
                result["recognition"] = "failed"
                return result
        except Exception as e:
            result["recognition"] = f"error: {e}"
            return result
    else:
        result["recognition"] = "cached"

    # Step 2: Claude 分析
    t0 = time.time()
    try:
        r = subprocess.run(
            [sys.executable, str(ROOT / "analyze_material.py"), "--key", material_key, "--skip-local"],
            capture_output=True, text=True, timeout=300,
            encoding="utf-8", errors="replace",
        )
        elapsed = time.time() - t0
        if "已保存到 SQLite" in r.stdout:
            result["analysis"] = f"ok ({elapsed:.0f}s)"
        else:
            result["analysis"] = f"failed: {r.stdout[-200:]}"
    except Exception as e:
        result["analysis"] = f"error: {e}"

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2, help="并行数（默认2）")
    parser.add_argument("--limit", type=int, default=0, help="最多分析几个（0=全部）")
    args = parser.parse_args()

    pending = find_pending()
    if args.limit > 0:
        pending = pending[:args.limit]

    print(f"待分析: {len(pending)} 个素材, 并行数: {args.workers}")
    print(f"开始时间: {datetime.now().strftime('%H:%M:%S')}")

    completed = 0
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(process_one, item): item for item in pending}
        for future in as_completed(futures):
            result = future.result()
            completed += 1
            if result:
                status = f"recog:{result.get('recognition','?')} analysis:{result.get('analysis','?')}"
                safe_name = result.get('material_name','')[:40].encode('ascii','replace').decode('ascii')
                print(f"[{completed}/{len(pending)}] {safe_name} | {status}")
            else:
                print(f"[{completed}/{len(pending)}] FAILED")

    print(f"\n完成: {datetime.now().strftime('%H:%M:%S')}")


if __name__ == "__main__":
    main()
