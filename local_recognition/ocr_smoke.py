"""One-image PaddleOCR smoke test used during component evaluation."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "BOS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    from paddleocr import PaddleOCR

    imported = time.perf_counter()
    pipeline = PaddleOCR(
        lang="japan",
        device="cpu",
        text_detection_model_name="PP-OCRv6_small_det",
        text_recognition_model_name="PP-OCRv6_small_rec",
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=False,
        cpu_threads=10,
        enable_mkldnn=False,
    )
    initialized = time.perf_counter()
    results = pipeline.predict(args.image)
    predicted = time.perf_counter()
    for result in results:
        result.save_to_json(save_path=str(output))
    summary = {
        "image": args.image,
        "import_sec": round(imported - started, 3),
        "initialize_sec": round(initialized - imported, 3),
        "predict_sec": round(predicted - initialized, 3),
        "result_count": len(results),
    }
    (output / "smoke_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
