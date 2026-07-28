"""PaddleOCR v6 small adapter returning schema-ready observations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")


class PaddleOcrRecognizer:
    def __init__(self) -> None:
        from paddleocr import PaddleOCR
        self.pipeline = PaddleOCR(
            device="cpu",
            text_detection_model_name="PP-OCRv6_small_det",
            text_recognition_model_name="PP-OCRv6_small_rec",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            cpu_threads=10,
            enable_mkldnn=False,
        )

    @staticmethod
    def _payload(result: Any) -> dict[str, Any]:
        value = getattr(result, "json", None)
        if callable(value): value = value()
        if isinstance(value, dict): return value.get("res", value)
        value = getattr(result, "res", None)
        if isinstance(value, dict): return value
        raise TypeError("unsupported PaddleOCR result object")

    def recognize(self, image_path: str | Path, width: int, height: int) -> list[dict[str, Any]]:
        from .enrich import normalize_bbox
        observations = []
        for result in self.pipeline.predict(str(image_path)):
            payload = self._payload(result)
            for text, score, box in zip(payload.get("rec_texts", []),
                                        payload.get("rec_scores", []),
                                        payload.get("rec_boxes", [])):
                text = str(text).strip()
                if not text: continue
                observations.append({
                    "text_original": text, "language": "unknown",
                    "text_normalized": text,
                    "bbox": normalize_bbox([float(v) for v in box], width, height),
                    "confidence": round(float(score), 6),
                })
        return observations
