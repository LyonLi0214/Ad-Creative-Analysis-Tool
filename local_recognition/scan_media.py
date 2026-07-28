"""Read-only decodability scan for local media samples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def check(path: Path) -> dict:
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
        image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        return {"name": path.name, "type": "image", "decodable": image is not None}
    cap = cv2.VideoCapture(str(path))
    opened = cap.isOpened()
    ok, _ = cap.read() if opened else (False, None)
    result = {"name": path.name, "type": "video", "decodable": bool(opened and ok),
              "frames": int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0),
              "fps": round(float(cap.get(cv2.CAP_PROP_FPS) or 0), 3)}
    cap.release()
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("--output")
    args = parser.parse_args()
    root = Path(args.directory)
    supported = {".mp4", ".mov", ".m4v", ".avi", ".webm", ".png", ".jpg", ".jpeg", ".webp"}
    results = [check(p) for p in sorted(root.iterdir()) if p.is_file() and p.suffix.lower() in supported]
    for result in results:
        print(json.dumps(result, ensure_ascii=False))
    if args.output:
        Path(args.output).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if any(x["decodable"] for x in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
