"""Run PaddleOCR in an isolated process and exchange observations as JSON."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .ocr_adapter import PaddleOcrRecognizer

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    tasks = json.loads(Path(args.tasks).read_text(encoding="utf-8"))
    recognizer = PaddleOcrRecognizer()
    results = {
        task["key"]: recognizer.recognize(task["path"], int(task["width"]), int(task["height"]))
        for task in tasks
    }
    Path(args.output).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
