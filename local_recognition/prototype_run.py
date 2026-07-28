"""Run the evidence skeleton extractor over a small representative batch."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .extractor import extract_skeleton, save_evidence


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", help="video/image paths")
    parser.add_argument("--output", required=True, help="output directory")
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    results = []
    for raw_path in args.paths:
        source = Path(raw_path)
        started = time.perf_counter()
        try:
            evidence = extract_skeleton(source, output / "frames" / source.stem)
            save_evidence(evidence, output / "evidence" / f"{source.stem}.json")
            result = {
                "asset_name": source.name, "status": "ok",
                "asset_type": evidence["asset_type"],
                "segments": len(evidence["segments"]),
                "duration_sec": evidence["media"]["duration_sec"],
                "has_audio": evidence["media"]["has_audio"],
                "elapsed_sec": round(time.perf_counter() - started, 3),
            }
        except Exception as exc:
            result = {
                "asset_name": source.name, "status": "error",
                "error_type": type(exc).__name__, "error": str(exc),
                "elapsed_sec": round(time.perf_counter() - started, 3),
            }
        results.append(result)
        print(json.dumps(results[-1], ensure_ascii=False))
    (output / "summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
