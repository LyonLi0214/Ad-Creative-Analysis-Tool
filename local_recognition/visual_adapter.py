"""CLIP adapter for controlled, group-relative visual semantic candidates."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .visual_labels import GROUP_THRESHOLDS, LABEL_GROUPS


class ClipVisualRecognizer:
    def __init__(self, model_name: str = "openai/clip-vit-base-patch32") -> None:
        import torch
        from transformers import CLIPModel, CLIPProcessor

        self.torch = torch
        self.model_name = model_name
        self.processor = CLIPProcessor.from_pretrained(model_name)
        self.model = CLIPModel.from_pretrained(model_name)
        self.model.eval()

    def recognize(self, image_path: str | Path, bbox: list[float] | None = None) -> dict[str, Any]:
        from PIL import Image

        image = Image.open(image_path).convert("RGB")
        if bbox is not None:
            if len(bbox) != 4 or not all(0 <= value <= 1 for value in bbox):
                raise ValueError("bbox must be normalized [x1,y1,x2,y2]")
            width, height = image.size
            crop_box = (int(bbox[0] * width), int(bbox[1] * height),
                        int(bbox[2] * width), int(bbox[3] * height))
            if crop_box[2] <= crop_box[0] or crop_box[3] <= crop_box[1]:
                raise ValueError("bbox has no area")
            image = image.crop(crop_box)
        groups = {}
        for group_name, definitions in LABEL_GROUPS.items():
            prompts = [item[2] for item in definitions]
            inputs = self.processor(text=prompts, images=image, return_tensors="pt", padding=True)
            with self.torch.inference_mode():
                logits = self.model(**inputs).logits_per_image[0]
                scores = logits.softmax(dim=-1).cpu().tolist()
            ranked = sorted(
                [
                    {"label": key, "label_zh": label_zh, "relative_score": round(float(score), 6)}
                    for (key, label_zh, _), score in zip(definitions, scores)
                ],
                key=lambda item: item["relative_score"], reverse=True,
            )
            margin = ranked[0]["relative_score"] - ranked[1]["relative_score"]
            threshold = GROUP_THRESHOLDS[group_name]
            groups[group_name] = {
                "top_label": ranked[0]["label"],
                "top_label_zh": ranked[0]["label_zh"],
                "relative_score": ranked[0]["relative_score"],
                "margin": round(margin, 6),
                "accepted": bool(threshold and ranked[0]["relative_score"] >= threshold[0]
                                 and margin >= threshold[1]),
                "min_score": threshold[0] if threshold else None,
                "min_margin": threshold[1] if threshold else None,
                "threshold_policy": "calibrating_v0.1" if threshold else "disabled_experimental",
                "candidates": ranked,
            }
        return {"model": self.model_name, "groups": groups, "evidence_bbox": bbox}
