"""Generate a validated local recognition evidence JSON for one asset."""
from __future__ import annotations
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from .enrich import (attach_image_ocr, attach_speech, attach_video_ocr,
                     attach_visual_candidates, derive_cta_from_ocr)
from .extractor import extract_skeleton, save_evidence
from .schema import validate_evidence

def _run_module(module: str, arguments: list[str]) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", module, *arguments],
        check=False, text=True, capture_output=True, encoding="utf-8",
    )
    if completed.returncode:
        raise RuntimeError(
            f"{module} failed ({completed.returncode})\nstdout: {completed.stdout}\nstderr: {completed.stderr}"
        )

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("asset")
    parser.add_argument("--output", required=True)
    parser.add_argument("--work-dir", required=True)
    parser.add_argument("--language", default="ja")
    parser.add_argument("--asr-model", default="small")
    parser.add_argument("--skip-ocr", action="store_true")
    parser.add_argument("--skip-asr", action="store_true")
    parser.add_argument("--skip-visual", action="store_true")
    parser.add_argument("--visual-model", default="openai/clip-vit-base-patch32")
    args = parser.parse_args()
    started = time.perf_counter()
    asset = Path(args.asset)
    evidence = extract_skeleton(asset, Path(args.work_dir) / "frames" / asset.stem)
    exchange_dir = Path(args.work_dir) / "worker_exchange" / asset.stem
    exchange_dir.mkdir(parents=True, exist_ok=True)

    # OCR intentionally runs first: process isolation makes DLL load order irrelevant.
    if not args.skip_ocr:
        width, height = evidence["media"]["width"], evidence["media"]["height"]
        if evidence["asset_type"] == "image":
            tasks = [{"key": "asset", "path": str(asset), "width": width, "height": height}]
        else:
            tasks = [
                {"key": frame, "path": frame, "width": width, "height": height}
                for segment in evidence["segments"]
                for frame in segment.get("representative_frames", [])
            ]
        task_path, result_path = exchange_dir / "ocr_tasks.json", exchange_dir / "ocr_results.json"
        task_path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        _run_module("local_recognition.ocr_worker", ["--tasks", str(task_path), "--output", str(result_path)])
        results = json.loads(result_path.read_text(encoding="utf-8"))
        attach_image_ocr(evidence, results["asset"]) if evidence["asset_type"] == "image" else attach_video_ocr(evidence, results)
        evidence["runtime"]["components"]["ocr"] = "PP-OCRv6_small_det+rec_cpu_worker"
        derive_cta_from_ocr(evidence)

    if evidence["asset_type"] == "video" and evidence["media"]["has_audio"] and not args.skip_asr:
        result_path = exchange_dir / "asr_results.json"
        _run_module("local_recognition.asr_worker", [str(asset), "--output", str(result_path), "--language", args.language, "--model", args.asr_model])
        result = json.loads(result_path.read_text(encoding="utf-8"))
        attach_speech(evidence, result["observations"], result["language"])
        evidence["runtime"]["components"]["asr"] = f"faster-whisper-{args.asr_model}-cpu-int8_worker"
    elif evidence["asset_type"] == "video":
        evidence["runtime"]["components"]["asr"] = "skipped_or_no_audio"

    if not args.skip_visual:
        if evidence["asset_type"] == "image":
            tasks = [{"key": segment["segment_id"], "path": str(asset), "bbox": segment["bbox"]}
                     for segment in evidence["segments"]]
        else:
            tasks = [
                {"key": frame, "path": frame}
                for segment in evidence["segments"]
                for frame in segment.get("representative_frames", [])
            ]
        task_path, result_path = exchange_dir / "visual_tasks.json", exchange_dir / "visual_results.json"
        task_path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
        _run_module("local_recognition.visual_worker", ["--tasks", str(task_path), "--output", str(result_path), "--model", args.visual_model])
        result = json.loads(result_path.read_text(encoding="utf-8"))
        attach_visual_candidates(evidence, result["results"])
        evidence["runtime"]["components"]["visual"] = "clip_controlled_candidates_worker"

    evidence["runtime"]["elapsed_sec"] = round(time.perf_counter() - started, 3)
    validate_evidence(evidence)
    save_evidence(evidence, args.output)
    print(f"saved={args.output} segments={len(evidence['segments'])} elapsed={evidence['runtime']['elapsed_sec']}s")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
