"""Attach normalized OCR and ASR observations to evidence segments."""

from __future__ import annotations

from typing import Any, Iterable

from .schema import validate_evidence

CTA_STRONG_TERMS = ("GOOGLE PLAY", "APP STORE", "DOWNLOAD", "ダウンロード", "インストール")
CTA_SIGNAL_TERMS = CTA_STRONG_TERMS + ("今すぐ", "無料", "GET IT ON", "入手")


def normalize_bbox(box: list[float], width: int, height: int) -> list[float]:
    if width <= 0 or height <= 0 or len(box) != 4:
        raise ValueError("invalid image geometry")
    x1, y1, x2, y2 = box
    return [round(max(0, min(1, x1 / width)), 6),
            round(max(0, min(1, y1 / height)), 6),
            round(max(0, min(1, x2 / width)), 6),
            round(max(0, min(1, y2 / height)), 6)]


def _region_contains(region_bbox: list[float], item_bbox: list[float]) -> bool:
    cx = (item_bbox[0] + item_bbox[2]) / 2
    cy = (item_bbox[1] + item_bbox[3]) / 2
    return region_bbox[0] <= cx <= region_bbox[2] and region_bbox[1] <= cy <= region_bbox[3]


def attach_image_ocr(evidence: dict[str, Any], observations: Iterable[dict[str, Any]]) -> None:
    if evidence["asset_type"] != "image":
        raise ValueError("image evidence required")
    items = list(observations)
    for segment in evidence["segments"]:
        segment["ocr"] = [dict(item) for item in items if _region_contains(segment["bbox"], item["bbox"])]
    evidence["global_evidence"]["ocr_full_text"] = [item["text_original"] for item in items]
    evidence["quality"]["ocr_coverage"] = 1.0
    low = [f"ocr:{item['text_original']}" for item in items if item["confidence"] < 0.7]
    evidence["quality"].setdefault("low_confidence_fields", []).extend(low)
    validate_evidence(evidence)


def derive_cta_from_ocr(evidence: dict[str, Any]) -> None:
    """Derive CTA evidence from recognized text without inventing a button."""
    seen = set()
    signals = []
    for segment in evidence["segments"]:
        for item in segment.get("ocr", []):
            key = (item["text_original"], tuple(item["bbox"]), item.get("evidence_frame"))
            if key in seen:
                continue
            seen.add(key)
            normalized = item["text_original"].upper().strip()
            strong = [term for term in CTA_STRONG_TERMS if term in normalized]
            matched = [term for term in CTA_SIGNAL_TERMS if term in normalized]
            if not matched:
                continue
            bbox = item["bbox"]
            signals.append({
                "text_original": item["text_original"],
                "bbox": bbox,
                "confidence": item["confidence"],
                "evidence_frame": item.get("evidence_frame"),
                "bottom_area": (bbox[1] + bbox[3]) / 2 >= 0.65,
                "matched_terms": matched,
                "strength": "strong" if strong else "signal",
            })
    confirmed = [item for item in signals if item["strength"] == "strong" and item["confidence"] >= 0.7]
    evidence["global_evidence"]["cta_signal_present"] = bool(signals)
    evidence["global_evidence"]["cta_present"] = bool(confirmed)
    evidence["global_evidence"]["cta_evidence"] = signals
    if signals and not confirmed:
        evidence["quality"].setdefault("warnings", []).append("cta_signal_found_but_no_strong_cta_evidence")
    validate_evidence(evidence)


def attach_video_ocr(evidence: dict[str, Any], by_frame: dict[str, list[dict[str, Any]]]) -> None:
    if evidence["asset_type"] != "video":
        raise ValueError("video evidence required")
    all_text = []
    processed = 0
    for segment in evidence["segments"]:
        items = []
        for frame in segment.get("representative_frames", []):
            frame_items = by_frame.get(frame, [])
            processed += int(frame in by_frame)
            for raw in frame_items:
                item = dict(raw)
                item["evidence_frame"] = frame
                items.append(item)
                all_text.append(item["text_original"])
                if item["confidence"] < 0.7:
                    evidence["quality"].setdefault("low_confidence_fields", []).append(f"ocr:{item['text_original']}")
        segment["ocr"] = items
    total = sum(len(s.get("representative_frames", [])) for s in evidence["segments"])
    evidence["quality"]["ocr_coverage"] = round(processed / total, 4) if total else 0.0
    evidence["global_evidence"]["ocr_full_text"] = list(dict.fromkeys(all_text))
    validate_evidence(evidence)


def attach_speech(evidence: dict[str, Any], speech_items: Iterable[dict[str, Any]], language: str) -> None:
    if evidence["asset_type"] != "video":
        raise ValueError("video evidence required")
    items = list(speech_items)
    for segment in evidence["segments"]:
        segment["speech"] = [dict(item) for item in items
            if item["end_sec"] > segment["start_sec"] and item["start_sec"] < segment["end_sec"]]
        segment["audio"]["speech_present"] = bool(segment["speech"])
    evidence["global_evidence"]["speech_full_text"] = [item["text_original"] for item in items]
    evidence["global_evidence"]["detected_languages"] = [language] if language else []
    evidence["quality"]["asr_coverage"] = 1.0
    evidence["quality"].setdefault("low_confidence_fields", []).extend(
        f"asr:{item['text_original']}" for item in items if item["confidence"] < 0.7)
    validate_evidence(evidence)


def _semantic_candidates(result: dict[str, Any], evidence_path: str) -> list[dict[str, Any]]:
    model = result["model"]
    items = []
    for group, value in result["groups"].items():
        items.append({
            "group": group,
            "top_label": value["top_label"],
            "top_label_zh": value["top_label_zh"],
            "relative_score": value["relative_score"],
            "margin": value["margin"],
            "threshold_passed": value["accepted"],
            "calibration_status": "calibrating" if group in {"content_mode", "subject_style"} else "experimental",
            "model": model,
            "evidence_path": evidence_path,
            "evidence_bbox": result.get("evidence_bbox"),
            "candidates": value["candidates"],
        })
    return items


def attach_visual_candidates(evidence: dict[str, Any], results: dict[str, dict[str, Any]]) -> None:
    """Attach traceable candidates without promoting them to confirmed facts."""
    processed = 0
    expected = 0
    if evidence["asset_type"] == "image":
        expected = len(evidence["segments"])
        for segment in evidence["segments"]:
            result = results.get(segment["segment_id"])
            if result:
                processed += 1
                segment["visual"]["semantic_candidates"] = _semantic_candidates(
                    result, result.get("evidence_path", evidence["source_path"])
                )
    else:
        for segment in evidence["segments"]:
            for frame in segment.get("representative_frames", []):
                expected += 1
                result = results.get(frame)
                if not result:
                    continue
                processed += 1
                candidates = _semantic_candidates(result, frame)
                for item in candidates:
                    item["evidence_frame"] = frame
                segment["visual"].setdefault("semantic_candidates", []).extend(candidates)
    evidence["quality"]["visual_coverage"] = round(processed / expected, 4) if expected else 0.0
    evidence["quality"]["visual_confirmed_fact_coverage"] = 0.0
    warnings = evidence["quality"].setdefault("warnings", [])
    if processed:
        warnings.append("visual_semantics_are_calibrating_candidates_not_confirmed_facts")
    validate_evidence(evidence)
