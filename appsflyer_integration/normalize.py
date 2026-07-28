from __future__ import annotations

import hashlib
import re
from typing import Any


# 别名顺序：实际 CSV 列名排第一，旧版/API v4 列名作 fallback
ALIASES = {
    "media_source": ("Media Source", "media_source", "Media Source (pid)", "pid"),
    "campaign": ("Campaign", "campaign", "Campaign (c)", "c"),
    "campaign_id": ("Campaign ID", "campaign_id"),
    "adset": ("Adset", "af_adset", "adset"),
    "adset_id": ("Adset ID", "af_adset_id", "adset_id"),
    "ad": ("Ad", "af_ad", "ad"),
    "ad_id": ("Ad ID", "af_ad_id", "ad_id"),
    "creative": ("Creative", "af_creative", "creative"),
    "creative_id": ("Creative ID", "af_creative_id", "creative_id"),
    "site_id": ("Site ID", "af_siteid", "site_id"),
}


def _first(row: dict[str, Any], names: tuple[str, ...]) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def normalize_name(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"\.[a-z0-9]{2,5}$", "", value)
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", value).strip("_")


def normalize_attribution(row: dict[str, Any], app_id: str) -> dict[str, Any]:
    result = {key: _first(row, names) for key, names in ALIASES.items()}
    strongest = "campaign"
    if result["adset"] or result["adset_id"]:
        strongest = "adset"
    if result["ad"] or result["ad_id"]:
        strongest = "ad"
    if result["creative"] or result["creative_id"]:
        strongest = "creative_candidate"
    parts = [app_id, result["media_source"], result["campaign_id"] or result["campaign"], result["adset_id"] or result["adset"], result["ad_id"] or result["ad"], result["creative_id"] or result["creative"]]
    canonical = "|".join(normalize_name(part) for part in parts)
    result.update({
        "app_id": app_id,
        "available_granularity": strongest,
        "attribution_key": hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24],
        "material_effect_confirmed": False,
        "association_note": "仅在 creative/ad 标识与素材主数据建立唯一映射后，才可标记为单素材效果。",
    })
    return result
