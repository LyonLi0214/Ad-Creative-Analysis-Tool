"""Run CLIP in an isolated process and write traceable visual candidates."""
from __future__ import annotations
import argparse
import json
import time
from pathlib import Path
from .visual_adapter import ClipVisualRecognizer


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", required=True, help="UTF-8 JSON list containing key and path")
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="openai/clip-vit-base-patch32")
    args = parser.parse_args()
    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    started = time.perf_counter()
    recognizer = ClipVisualRecognizer(args.model)
    results = {}
    for task in tasks:
        item = recognizer.recognize(task["path"], task.get("bbox"))
        item["evidence_path"] = task["path"]
        results[task["key"]] = item
    payload = {
        "schema_version": "visual-candidates-v0.1",
        "elapsed_sec": round(time.perf_counter() - started, 3),
        "results": results,
    }
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved={args.output} images={len(tasks)} elapsed={payload['elapsed_sec']}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
