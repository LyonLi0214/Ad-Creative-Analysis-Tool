from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from appsflyer_integration.normalize import normalize_name
from migration.noco_to_feishu import DB_PATH, FeishuClient, load_configuration


ROOT = Path(__file__).resolve().parents[1]


def nonempty(value: Any) -> bool:
    return value is not None and value != "" and value != [] and value != {}


def all_records(client: FeishuClient, table_id: str) -> list[dict[str, Any]]:
    rows, token = [], None
    while True:
        params: dict[str, Any] = {"page_size": 500}
        if token:
            params["page_token"] = token
        data = client.call("GET", f"/open-apis/bitable/v1/apps/{client.base_token}/tables/{table_id}/records", params=params)
        rows.extend(data.get("items") or [])
        if not data.get("has_more"):
            return rows
        token = data.get("page_token")


def feishu_audit() -> list[dict[str, Any]]:
    client = FeishuClient()
    output = []
    for table in client.list_tables():
        table_id, name = table["table_id"], table["name"]
        fields = client.list_fields(table_id)
        rows = all_records(client, table_id)
        filled = Counter()
        for item in rows:
            for key, value in (item.get("fields") or {}).items():
                if nonempty(value):
                    filled[key] += 1
        output.append({
            "name": name,
            "table_id": table_id,
            "records": len(rows),
            "fields": [{"name": field.get("field_name"), "type": field.get("type"), "filled": filled.get(field.get("field_name"), 0), "fill_rate": round(filled.get(field.get("field_name"), 0) / len(rows), 4) if rows else 0} for field in fields],
        })
    return output


def sqlite_audit() -> dict[str, Any]:
    connection = sqlite3.connect(DB_PATH)
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    result = {}
    for table in tables:
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')]
        count = connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        result[table] = {"records": count, "columns": columns}
    connection.close()
    return result


def appsflyer_audit() -> dict[str, Any]:
    manifest_path = ROOT / "appsflyer_data" / "latest_manifest.json"
    if not manifest_path.is_file():
        return {"status": "no manifest"}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    field_names = {
        "media_source": ("Media Source (pid)", "media_source", "pid"),
        "campaign": ("Campaign (c)", "campaign", "c"),
        "campaign_id": ("Campaign ID", "campaign_id"),
        "adset": ("Adset", "af_adset", "adset"),
        "adset_id": ("Adset ID", "af_adset_id", "adset_id"),
        "ad": ("Ad", "af_ad", "ad"),
        "ad_id": ("Ad ID", "af_ad_id", "ad_id"),
        "creative": ("Creative", "af_creative", "creative"),
        "creative_id": ("Creative ID", "af_creative_id", "creative_id"),
        "cost": ("Cost Value", "cost_value"),
        "revenue_usd": ("Event Revenue USD", "event_revenue_usd"),
        "event_name": ("Event Name", "event_name"),
        "country": ("Country Code", "country_code"),
    }
    reports = []
    ad_values: set[str] = set()
    for report in manifest.get("reports", []):
        path = ROOT / report["path"]
        data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []
        counts = Counter()
        for item in data:
            raw = item.get("raw") or {}
            for logical, aliases in field_names.items():
                value = next((raw.get(alias) for alias in aliases if nonempty(raw.get(alias))), None)
                if nonempty(value):
                    counts[logical] += 1
                    if logical in {"ad", "creative"}:
                        ad_values.add(normalize_name(str(value)))
        reports.append({"app_id": report.get("app_id"), "report": report.get("report"), "rows": len(data), "coverage": {key: {"filled": counts[key], "rate": round(counts[key] / len(data), 4) if data else 0} for key in field_names}})

    connection = sqlite3.connect(DB_PATH)
    material_rows = [json.loads(row[0]) for row in connection.execute("SELECT payload_json FROM source_records WHERE source_table_name='素材主表'")]
    connection.close()
    names = {normalize_name(str(row.get("素材名称") or "")) for row in material_rows if row.get("素材名称")}
    exact = names & ad_values
    fuzzy = {name for name in names if len(name) >= 5 and any(name in ad or ad in name for ad in ad_values if len(ad) >= 5)}
    return {"manifest_run": manifest.get("run_id"), "reports": reports, "material_names": len(names), "distinct_ad_or_creative_names": len(ad_values), "exact_name_matches": len(exact), "substring_candidate_matches": len(fuzzy), "unmatched_materials": len(names - fuzzy)}


def main() -> int:
    load_configuration()
    print(json.dumps({"feishu": feishu_audit(), "sqlite": sqlite_audit(), "appsflyer": appsflyer_audit()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
