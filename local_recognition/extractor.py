"""Dependency-light evidence skeleton extraction.

This module uses the project's existing OpenCV/Numpy stack. OCR, ASR and
semantic visual recognizers plug into the generated segments in later stages.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import imageio_ffmpeg

from .schema import validate_evidence


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _histogram(frame: np.ndarray) -> np.ndarray:
    small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
    return cv2.normalize(hist, hist).flatten()


def _frame_name(asset_path: Path, index: int) -> str:
    return f"{asset_path.stem}_evidence_{index:04d}.jpg"


def _has_audio_stream(path: Path) -> bool:
    """Ask ffmpeg to map the first audio stream; do not infer from extension."""
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(path),
               "-map", "0:a:0", "-frames:a", "1", "-f", "null", "-"]
    completed = subprocess.run(command, capture_output=True, timeout=30)
    return completed.returncode == 0


def extract_video_skeleton(
    path: Path,
    *,
    output_dir: Path,
    sample_interval_sec: float = 0.5,
    cut_threshold: float = 0.30,
    min_segment_sec: float = 2.0,
) -> dict[str, Any]:
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise ValueError(f"cannot open video: {path}")
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if fps <= 0 or frame_count <= 0 or width <= 0 or height <= 0:
        cap.release()
        raise ValueError(f"invalid video metadata: {path}")
    duration = frame_count / fps
    output_dir.mkdir(parents=True, exist_ok=True)

    step = max(1, int(round(fps * sample_interval_sec)))
    samples: list[tuple[float, np.ndarray, np.ndarray, np.ndarray]] = []
    frame_index = 0
    while frame_index < frame_count:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = cap.read()
        if not ok:
            break
        small = cv2.resize(frame, (160, 90), interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        samples.append((frame_index / fps, frame, _histogram(frame), gray))
        frame_index += step
    cap.release()
    if not samples:
        raise ValueError(f"no decodable frames: {path}")

    cuts = [0.0]
    for current, previous in zip(samples[1:], samples[:-1]):
        timestamp, _, hist, gray = current
        histogram_score = cv2.compareHist(previous[2], hist, cv2.HISTCMP_BHATTACHARYYA)
        pixel_score = float(np.mean(cv2.absdiff(previous[3], gray)) / 255.0)
        score = max(histogram_score, pixel_score * 2.0)
        if score >= cut_threshold and timestamp - cuts[-1] >= min_segment_sec:
            cuts.append(timestamp)
    if duration - cuts[-1] < min_segment_sec and len(cuts) > 1:
        cuts.pop()
    boundaries = cuts + [duration]

    segments = []
    for index, (start, end) in enumerate(zip(boundaries[:-1], boundaries[1:]), 1):
        middle = (start + end) / 2
        selected = min(samples, key=lambda item: abs(item[0] - middle))
        frame_filename = _frame_name(path, index)
        frame_path = output_dir / frame_filename
        cv2.imencode(".jpg", selected[1], [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tofile(str(frame_path))
        segments.append({
            "segment_id": f"seg_{index:03d}",
            "start_sec": round(start, 3),
            "end_sec": round(end, 3),
            "representative_frames": [str(frame_path)],
            "scene_change_reason": "histogram_cut" if index > 1 else "video_start",
            "ocr": [], "speech": [],
            "visual": {"people": [], "characters": [], "objects": [], "ui_elements": [],
                       "effects": [], "scene": [], "colors": [], "cta": [], "logo": []},
            "audio": {"speech_present": False, "music_present": False,
                      "sound_effects_present": False, "energy": 0.0},
        })

    has_audio = _has_audio_stream(path)
    evidence = {
        "schema_version": "0.1", "asset_name": path.name, "asset_type": "video",
        "source_path": str(path),
        "media": {"duration_sec": round(duration, 3), "width": width, "height": height,
                  "fps": round(fps, 3), "has_audio": has_audio,
                  "language_candidates": [], "file_hash": sha256_file(path)},
        "segments": segments,
        "global_evidence": {"ocr_full_text": [], "speech_full_text": [],
            "detected_languages": [], "dominant_colors": [], "cta_present": False,
            "logo_present": False, "character_count_estimate": None,
            "scene_count": len(segments)},
        "quality": {"ocr_coverage": 0.0, "asr_coverage": 0.0, "visual_coverage": 0.0,
                    "low_confidence_fields": [],
                    "warnings": []},
        "runtime": {"started_at": started_at, "elapsed_sec": round(time.perf_counter() - started, 3),
                    "components": {"scene_detector": "opencv_histogram_v0.1",
                                   "ocr": "pending", "asr": "pending", "visual": "pending"}},
    }
    validate_evidence(evidence)
    return evidence


def extract_image_skeleton(path: Path) -> dict[str, Any]:
    started = time.perf_counter()
    started_at = datetime.now(timezone.utc).isoformat()
    raw = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"cannot open image: {path}")
    height, width = image.shape[:2]
    regions = [
        ("full", [0.0, 0.0, 1.0, 1.0], "整体构图"),
        ("top", [0.0, 0.0, 1.0, 0.33], "上部区域"),
        ("center", [0.0, 0.33, 1.0, 0.67], "中部区域"),
        ("bottom", [0.0, 0.67, 1.0, 1.0], "底部区域"),
    ]
    segments = [{
        "segment_id": f"region_{index:03d}", "region": region, "bbox": bbox,
        "analysis_module": module, "ocr": [],
        "visual": {"people": [], "characters": [], "objects": [], "ui_elements": [],
                   "effects": [], "scene": [], "colors": [], "cta": [], "logo": []},
        "confidence": 1.0,
    } for index, (region, bbox, module) in enumerate(regions, 1)]
    evidence = {
        "schema_version": "0.1", "asset_name": path.name, "asset_type": "image",
        "source_path": str(path),
        "media": {"duration_sec": None, "width": width, "height": height, "fps": None,
                  "has_audio": False, "language_candidates": [], "file_hash": sha256_file(path)},
        "segments": segments,
        "global_evidence": {"ocr_full_text": [], "speech_full_text": [],
            "detected_languages": [], "dominant_colors": [], "cta_present": False,
            "logo_present": False, "character_count_estimate": None, "scene_count": 1},
        "quality": {"ocr_coverage": 0.0, "asr_coverage": 1.0, "visual_coverage": 0.0,
                    "low_confidence_fields": [], "warnings": []},
        "runtime": {"started_at": started_at, "elapsed_sec": round(time.perf_counter() - started, 3),
                    "components": {"scene_detector": "image_regions_v0.1",
                                   "ocr": "pending", "asr": "not_applicable", "visual": "pending"}},
    }
    validate_evidence(evidence)
    return evidence


def extract_skeleton(path: str | Path, output_dir: str | Path) -> dict[str, Any]:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix in {".mp4", ".mov", ".m4v", ".avi", ".webm"}:
        return extract_video_skeleton(source, output_dir=Path(output_dir))
    if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
        return extract_image_skeleton(source)
    raise ValueError(f"unsupported media type: {source.suffix}")


def save_evidence(evidence: dict[str, Any], output_path: str | Path) -> None:
    validate_evidence(evidence)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
