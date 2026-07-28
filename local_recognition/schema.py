"""Validation rules for recognition evidence schema v0.1."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "0.1"
ASSET_TYPES = {"video", "image"}
IMAGE_REGIONS = {"left", "center", "right", "top", "bottom", "full", "custom"}


@dataclass(frozen=True)
class ValidationIssue:
    path: str
    message: str


class EvidenceValidationError(ValueError):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__("; ".join(f"{i.path}: {i.message}" for i in issues))


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _confidence(value: Any) -> bool:
    return _is_number(value) and 0 <= float(value) <= 1


def _bbox(value: Any) -> bool:
    return (
        isinstance(value, list) and len(value) == 4
        and all(_is_number(v) and 0 <= float(v) <= 1 for v in value)
        and value[0] <= value[2] and value[1] <= value[3]
    )


def validate_evidence(data: dict[str, Any], *, raise_on_error: bool = True) -> list[ValidationIssue]:
    """Validate a recognition evidence document and return all issues."""
    issues: list[ValidationIssue] = []

    def issue(path: str, message: str) -> None:
        issues.append(ValidationIssue(path, message))

    if not isinstance(data, dict):
        issue("$", "must be an object")
        if raise_on_error:
            raise EvidenceValidationError(issues)
        return issues

    required = {"schema_version", "asset_name", "asset_type", "source_path", "media", "segments", "global_evidence", "quality", "runtime"}
    for key in sorted(required - data.keys()):
        issue(f"$.{key}", "is required")
    if data.get("schema_version") != SCHEMA_VERSION:
        issue("$.schema_version", f"must equal {SCHEMA_VERSION!r}")
    asset_type = data.get("asset_type")
    if asset_type not in ASSET_TYPES:
        issue("$.asset_type", "must be 'video' or 'image'")
    for key in ("asset_name", "source_path"):
        if not isinstance(data.get(key), str) or not data.get(key, "").strip():
            issue(f"$.{key}", "must be a non-empty string")

    media = data.get("media")
    if not isinstance(media, dict):
        issue("$.media", "must be an object")
        media = {}
    for key in ("width", "height"):
        if not isinstance(media.get(key), int) or media.get(key, 0) <= 0:
            issue(f"$.media.{key}", "must be a positive integer")
    if asset_type == "video":
        if not _is_number(media.get("duration_sec")) or media.get("duration_sec", 0) <= 0:
            issue("$.media.duration_sec", "must be positive for video")
        if not _is_number(media.get("fps")) or media.get("fps", 0) <= 0:
            issue("$.media.fps", "must be positive for video")
        if not isinstance(media.get("has_audio"), bool):
            issue("$.media.has_audio", "must be boolean")
    elif asset_type == "image":
        if media.get("duration_sec") is not None:
            issue("$.media.duration_sec", "must be null for image")
        if media.get("fps") is not None:
            issue("$.media.fps", "must be null for image")
        if media.get("has_audio") is not False:
            issue("$.media.has_audio", "must be false for image")

    segments = data.get("segments")
    if not isinstance(segments, list):
        issue("$.segments", "must be an array")
        segments = []
    seen_ids: set[str] = set()
    for index, segment in enumerate(segments):
        path = f"$.segments[{index}]"
        if not isinstance(segment, dict):
            issue(path, "must be an object")
            continue
        segment_id = segment.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id:
            issue(f"{path}.segment_id", "must be a non-empty string")
        elif segment_id in seen_ids:
            issue(f"{path}.segment_id", "must be unique")
        else:
            seen_ids.add(segment_id)

        if asset_type == "video":
            if not _is_number(segment.get("start_sec")) or segment.get("start_sec", -1) < 0:
                issue(f"{path}.start_sec", "must be non-negative")
            if not _is_number(segment.get("end_sec")) or segment.get("end_sec", -1) <= segment.get("start_sec", -1):
                issue(f"{path}.end_sec", "must be greater than start_sec")
            if "region" in segment or "analysis_module" in segment:
                issue(path, "video segment cannot use image-only region fields")
        elif asset_type == "image":
            if "start_sec" in segment or "end_sec" in segment or "scene_change_reason" in segment:
                issue(path, "image region cannot contain timeline fields")
            if segment.get("region") not in IMAGE_REGIONS:
                issue(f"{path}.region", "has unsupported region value")
            if not _bbox(segment.get("bbox")):
                issue(f"{path}.bbox", "must be normalized [x1,y1,x2,y2]")
            if not isinstance(segment.get("analysis_module"), str) or not segment.get("analysis_module", "").strip():
                issue(f"{path}.analysis_module", "must be a non-empty string")

        for oi, item in enumerate(segment.get("ocr", [])):
            opath = f"{path}.ocr[{oi}]"
            if not isinstance(item, dict):
                issue(opath, "must be an object")
                continue
            if not isinstance(item.get("text_original"), str) or not item.get("text_original", "").strip():
                issue(f"{opath}.text_original", "must be non-empty")
            if not _confidence(item.get("confidence")):
                issue(f"{opath}.confidence", "must be between 0 and 1")
            if not _bbox(item.get("bbox")):
                issue(f"{opath}.bbox", "must be normalized [x1,y1,x2,y2]")
            if asset_type == "video" and not item.get("evidence_frame"):
                issue(f"{opath}.evidence_frame", "is required for video OCR")

        speech = segment.get("speech", [])
        if asset_type == "image" and speech:
            issue(f"{path}.speech", "image cannot contain speech")
        for si, item in enumerate(speech):
            spath = f"{path}.speech[{si}]"
            if not isinstance(item, dict):
                issue(spath, "must be an object")
                continue
            if not isinstance(item.get("text_original"), str) or not item.get("text_original", "").strip():
                issue(f"{spath}.text_original", "must be non-empty")
            if not _confidence(item.get("confidence")):
                issue(f"{spath}.confidence", "must be between 0 and 1")
            if not _is_number(item.get("start_sec")) or not _is_number(item.get("end_sec")) or item.get("end_sec", -1) <= item.get("start_sec", -1):
                issue(spath, "must have a valid time range")

        audio = segment.get("audio", {})
        if asset_type == "image" and audio:
            issue(f"{path}.audio", "image cannot contain audio evidence")
        if asset_type == "video" and media.get("has_audio") is False:
            if any(audio.get(k) for k in ("speech_present", "music_present", "sound_effects_present")) or speech:
                issue(path, "video without audio track cannot contain audio or speech evidence")

        visual = segment.get("visual", {})
        if not isinstance(visual, dict):
            issue(f"{path}.visual", "must be an object")
            visual = {}
        for vi, candidate in enumerate(visual.get("semantic_candidates", [])):
            vpath = f"{path}.visual.semantic_candidates[{vi}]"
            if not isinstance(candidate, dict):
                issue(vpath, "must be an object")
                continue
            for key in ("group", "top_label", "top_label_zh", "model", "calibration_status"):
                if not isinstance(candidate.get(key), str) or not candidate.get(key, "").strip():
                    issue(f"{vpath}.{key}", "must be a non-empty string")
            if candidate.get("calibration_status") not in {"calibrating", "experimental", "validated"}:
                issue(f"{vpath}.calibration_status", "has unsupported value")
            if not _confidence(candidate.get("relative_score")):
                issue(f"{vpath}.relative_score", "must be between 0 and 1")
            if not _confidence(candidate.get("margin")):
                issue(f"{vpath}.margin", "must be between 0 and 1")
            if not isinstance(candidate.get("threshold_passed"), bool):
                issue(f"{vpath}.threshold_passed", "must be boolean")
            if not isinstance(candidate.get("candidates"), list) or not candidate.get("candidates"):
                issue(f"{vpath}.candidates", "must be a non-empty array")
            if asset_type == "video" and not candidate.get("evidence_frame"):
                issue(f"{vpath}.evidence_frame", "is required for video visual candidate")

        forbidden = {"目的", "可借鉴点", "营销策略", "投放建议", "优化建议", "purpose", "recommendation"}
        for key in forbidden & segment.keys():
            issue(f"{path}.{key}", "is inference, not recognition evidence")

    for key in ("global_evidence", "quality", "runtime"):
        if not isinstance(data.get(key), dict):
            issue(f"$.{key}", "must be an object")
    if issues and raise_on_error:
        raise EvidenceValidationError(issues)
    return issues
