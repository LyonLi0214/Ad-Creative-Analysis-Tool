"""faster-whisper small CPU INT8 adapter."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any


class FasterWhisperRecognizer:
    def __init__(self, model_name: str = "small") -> None:
        from faster_whisper import WhisperModel
        self.model_name = model_name
        self.model = WhisperModel(model_name, device="cpu", compute_type="int8", cpu_threads=10)

    def recognize(self, media_path: str | Path, language: str | None = "ja") -> tuple[list[dict[str, Any]], str]:
        segments, info = self.model.transcribe(
            str(media_path), language=language, beam_size=5, vad_filter=True,
            condition_on_previous_text=False,
        )
        items = []
        for segment in segments:
            text = segment.text.strip()
            if not text: continue
            items.append({
                "text_original": text, "language": info.language,
                "start_sec": round(segment.start, 3), "end_sec": round(segment.end, 3),
                "confidence": round(max(0.0, min(1.0, math.exp(segment.avg_logprob))), 6),
            })
        return items, info.language
