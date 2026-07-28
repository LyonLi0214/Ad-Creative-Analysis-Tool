"""Run faster-whisper in an isolated process and exchange observations as JSON."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
from .asr_adapter import FasterWhisperRecognizer

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("asset")
    parser.add_argument("--output", required=True)
    parser.add_argument("--language", default="ja")
    parser.add_argument("--model", default="small")
    args = parser.parse_args()
    observations, language = FasterWhisperRecognizer(args.model).recognize(args.asset, args.language or None)
    payload = {"observations": observations, "language": language}
    Path(args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
