"""One-video faster-whisper CPU INT8 smoke test."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("media")
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="tiny")
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    from faster_whisper import WhisperModel

    imported = time.perf_counter()
    model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=10)
    initialized = time.perf_counter()
    segments_iter, info = model.transcribe(
        args.media, language="ja", beam_size=5, vad_filter=True,
        condition_on_previous_text=False,
    )
    segments = []
    for segment in segments_iter:
        segments.append({
            "start_sec": round(segment.start, 3),
            "end_sec": round(segment.end, 3),
            "text_original": segment.text.strip(),
            "confidence": round(max(0.0, min(1.0, math.exp(segment.avg_logprob))), 4),
            "no_speech_prob": round(segment.no_speech_prob, 4),
        })
    predicted = time.perf_counter()
    result = {
        "media": args.media, "model": args.model,
        "detected_language": info.language,
        "language_probability": round(info.language_probability, 4),
        "duration_sec": round(info.duration, 3),
        "segments": segments,
        "import_sec": round(imported - started, 3),
        "initialize_sec": round(initialized - imported, 3),
        "predict_sec": round(predicted - initialized, 3),
    }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
