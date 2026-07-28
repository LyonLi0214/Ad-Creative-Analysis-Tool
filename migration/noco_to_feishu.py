from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "mova_materials.sqlite3"
SECRET_PATH = ROOT / ".env"
TOOLS_CONFIG = ROOT / "feishu_bot" / "tools_config.json"

TABLES = {
    "素材主表": "NOCODB_TABLE_ASSETS",
    "AI分析结果": "NOCODB_TABLE_ANALYSIS",
    "逐帧拆解": "NOCODB_TABLE_TIMELINE",
    "标签字典": "NOCODB_TABLE_DICT",
    "效果聚合": "NOCODB_TABLE_AGGREGATION",
    "运行日志": "NOCODB_TABLE_LOGS",
}

LOCAL_ONLY_FIELDS = {"AI原始输出", "原始输出", "错误堆栈", "密钥", "Token", "App Secret"}
NOCO_SYSTEM_FIELDS = {"Id", "ID", "CreatedAt", "UpdatedAt", "nc_created_by", "nc_updated_by", "nc_order", "__nc_deleted"}
NUMBER_FIELDS = {
    "花费", "点击率", "安装", "转化率", "单价", "首充用户", "首充率", "首日回收",
    "单个付费用户成本", "首日ROI", "累计付费", "累计ROI", "次留", "时长", "AI置信度",
    "素材数", "平均花费", "平均单价", "平均首日ROI", "平均累计ROI", "平均次留",
}


def load_env(path: Path, overwrite: bool = False) -> None:
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if overwrite or key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip()


def load_configuration() -> None:
    load_env(ROOT / ".env")
    load_env(SECRET_PATH, overwrite=True)


class NocoClient:
    def __init__(self) -> None:
        self.base = os.environ["NOCODB_BASE"].rstrip("/")
        self.headers = {"xc-token": os.environ["NOCODB_TOKEN"]}

    def metadata(self, table_id: str) -> dict[str, Any]:
        response = requests.get(f"{self.base}/api/v2/meta/tables/{table_id}", headers=self.headers, timeout=30)
        response.raise_for_status()
        return response.json()

    def records(self, table_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            response = requests.get(
                f"{self.base}/api/v2/tables/{table_id}/records",
                headers=self.headers, params={"limit": 1000, "offset": offset}, timeout=60,
            )
            response.raise_for_status()
            payload = response.json()
            page = payload.get("list") or []
            rows.extend(page)
            info = payload.get("pageInfo") or {}
            if not info.get("isLastPage", len(page) < 1000):
                offset += len(page)
                continue
            break
        return rows


class FeishuClient:
    def __init__(self) -> None:
        self.app_id = os.environ["FEISHU_APP_ID"]
        self.app_secret = os.environ["FEISHU_APP_SECRET"]
        self.base_token = json.loads(TOOLS_CONFIG.read_text(encoding="utf-8"))["base_token"]
        self.token = ""

    def _auth(self) -> str:
        if self.token:
            return self.token
        response = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret}, timeout=30,
        )
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError("飞书应用认证失败")
        self.token = payload["tenant_access_token"]
        return self.token

    def call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._auth()}", "Content-Type": "application/json"}
        response = requests.request(method, "https://open.feishu.cn" + path, headers=headers, timeout=60, **kwargs)
        payload = response.json()
        if not response.ok or payload.get("code") != 0:
            raise RuntimeError(f"飞书调用失败 code={payload.get('code')} msg={payload.get('msg')}")
        return payload.get("data") or {}

    def list_tables(self) -> list[dict[str, Any]]:
        data = self.call("GET", f"/open-apis/bitable/v1/apps/{self.base_token}/tables", params={"page_size": 100})
        return data.get("items") or []

    def list_fields(self, table_id: str) -> list[dict[str, Any]]:
        data = self.call("GET", f"/open-apis/bitable/v1/apps/{self.base_token}/tables/{table_id}/fields", params={"page_size": 100})
        return data.get("items") or []

    def ensure_table(self, name: str) -> str:
        existing = {item.get("name"): item.get("table_id") for item in self.list_tables()}
        if name in existing:
            return str(existing[name])
        body = {"table": {"name": name, "default_view_name": "默认视图", "fields": [{"field_name": "名称", "type": 1}]}}
        data = self.call("POST", f"/open-apis/bitable/v1/apps/{self.base_token}/tables", json=body)
        table = data.get("table") or data
        created_id = table.get("table_id") or table.get("id")
        if not created_id:
            refreshed = {item.get("name"): item.get("table_id") for item in self.list_tables()}
            created_id = refreshed.get(name)
        if not created_id:
            raise RuntimeError(f"飞书已创建数据表但未返回可识别的 table_id：{name}")
        return str(created_id)

    def ensure_fields(self, table_id: str, names: list[str]) -> None:
        existing = {item.get("field_name") for item in self.list_fields(table_id)}
        for name in names:
            if name in existing or name == "名称":
                continue
            field_type = 2 if name in NUMBER_FIELDS else 1
            self.call("POST", f"/open-apis/bitable/v1/apps/{self.base_token}/tables/{table_id}/fields", json={"field_name": name, "type": field_type})
            existing.add(name)

    def create_records(self, table_id: str, rows: list[dict[str, Any]]) -> list[str]:
        ids: list[str] = []
        for start in range(0, len(rows), 200):
            batch = [{"fields": row} for row in rows[start:start + 200]]
            data = self.call("POST", f"/open-apis/bitable/v1/apps/{self.base_token}/tables/{table_id}/records/batch_create", json={"records": batch})
            ids.extend(str(item.get("record_id")) for item in data.get("records", []))
        return ids

    def update_record(self, table_id: str, record_id: str, fields: dict[str, Any]) -> None:
        self.call("PUT", f"/open-apis/bitable/v1/apps/{self.base_token}/tables/{table_id}/records/{record_id}", json={"fields": fields})


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript("""
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS source_records (
      source_table_id TEXT NOT NULL,
      source_table_name TEXT NOT NULL,
      source_record_id TEXT NOT NULL,
      business_key TEXT,
      payload_json TEXT NOT NULL,
      content_hash TEXT NOT NULL,
      imported_at TEXT NOT NULL,
      PRIMARY KEY (source_table_id, source_record_id)
    );
    CREATE TABLE IF NOT EXISTS feishu_record_map (
      source_table_id TEXT NOT NULL,
      source_record_id TEXT NOT NULL,
      feishu_table_id TEXT NOT NULL,
      feishu_record_id TEXT NOT NULL,
      content_hash TEXT NOT NULL,
      synced_at TEXT NOT NULL,
      PRIMARY KEY (source_table_id, source_record_id)
    );
    CREATE TABLE IF NOT EXISTS migration_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      started_at TEXT NOT NULL,
      completed_at TEXT,
      status TEXT NOT NULL,
      summary_json TEXT
    );
    """)


def record_id(row: dict[str, Any]) -> str:
    for key in ("Id", "ID", "id", "ncRecordId"):
        if row.get(key) is not None:
            return str(row[key])
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:24]


def content_hash(row: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def visible_fields(row: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in row.items():
        if key not in allowed or key in LOCAL_ONLY_FIELDS or value is None:
            continue
        if key in NUMBER_FIELDS:
            try:
                output[key] = float(value)
            except (TypeError, ValueError):
                continue
        elif isinstance(value, (dict, list)):
            output[key] = json.dumps(value, ensure_ascii=False)[:9000]
        else:
            output[key] = str(value)[:9000]
    return output


def audit() -> dict[str, Any]:
    noco = NocoClient()
    result: dict[str, Any] = {"tables": []}
    for name, env_key in TABLES.items():
        table_id = os.environ.get(env_key, "")
        if not table_id:
            continue
        meta = noco.metadata(table_id)
        rows = noco.records(table_id)
        columns = [col.get("title") for col in meta.get("columns", []) if col.get("title")]
        result["tables"].append({"name": name, "table_id": table_id, "records": len(rows), "columns": columns})
    return result


def migrate() -> dict[str, Any]:
    noco, feishu = NocoClient(), FeishuClient()
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB_PATH)
    init_db(connection)
    now = datetime.now(timezone.utc).isoformat()
    run_id = connection.execute("INSERT INTO migration_runs(started_at,status) VALUES (?,?)", (now, "running")).lastrowid
    summary: dict[str, Any] = {"sqlite": str(DB_PATH), "tables": [], "errors": []}
    try:
        for name, env_key in TABLES.items():
            source_table_id = os.environ.get(env_key, "")
            if not source_table_id:
                continue
            meta, rows = noco.metadata(source_table_id), noco.records(source_table_id)
            column_names = [str(col.get("title")) for col in meta.get("columns", []) if col.get("title")]
            allowed = {field for field in column_names if field not in LOCAL_ONLY_FIELDS and field not in NOCO_SYSTEM_FIELDS}
            feishu_table_id = feishu.ensure_table(name)
            feishu.ensure_fields(feishu_table_id, sorted(allowed))
            feishu_field_names = {item.get("field_name") for item in feishu.list_fields(feishu_table_id)}
            new_rows: list[dict[str, Any]] = []
            new_source_ids: list[str] = []
            updated = 0
            for row in rows:
                sid, digest = record_id(row), content_hash(row)
                key = str(row.get("素材名称") or row.get("标签值") or row.get("名称") or "")
                connection.execute("""INSERT INTO source_records VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(source_table_id,source_record_id) DO UPDATE SET business_key=excluded.business_key,payload_json=excluded.payload_json,content_hash=excluded.content_hash,imported_at=excluded.imported_at""",
                    (source_table_id, name, sid, key, json.dumps(row, ensure_ascii=False, default=str), digest, now))
                mapped = connection.execute("SELECT feishu_table_id,feishu_record_id,content_hash FROM feishu_record_map WHERE source_table_id=? AND source_record_id=?", (source_table_id, sid)).fetchone()
                visible = visible_fields(row, allowed)
                if "名称" in feishu_field_names:
                    visible["名称"] = key or sid
                if mapped:
                    if mapped[2] != digest:
                        feishu.update_record(mapped[0], mapped[1], visible)
                        connection.execute("UPDATE feishu_record_map SET content_hash=?,synced_at=? WHERE source_table_id=? AND source_record_id=?", (digest, now, source_table_id, sid))
                        updated += 1
                else:
                    new_rows.append(visible); new_source_ids.append(sid)
            created_ids = feishu.create_records(feishu_table_id, new_rows) if new_rows else []
            for sid, fid, row in zip(new_source_ids, created_ids, new_rows):
                source_row = next(item for item in rows if record_id(item) == sid)
                connection.execute("INSERT OR REPLACE INTO feishu_record_map VALUES (?,?,?,?,?,?)", (source_table_id, sid, feishu_table_id, fid, content_hash(source_row), now))
            connection.commit()
            summary["tables"].append({"name": name, "source": len(rows), "local": len(rows), "feishu_created": len(created_ids), "feishu_updated": updated, "feishu_table_id": feishu_table_id})
        completed = datetime.now(timezone.utc).isoformat()
        connection.execute("UPDATE migration_runs SET completed_at=?,status=?,summary_json=? WHERE id=?", (completed, "complete", json.dumps(summary, ensure_ascii=False), run_id))
        connection.commit()
    except Exception as exc:
        connection.execute("UPDATE migration_runs SET completed_at=?,status=?,summary_json=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), "failed", json.dumps({"error": str(exc)}, ensure_ascii=False), run_id))
        connection.commit()
        raise
    finally:
        connection.close()
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("audit", "migrate"))
    args = parser.parse_args()
    load_configuration()
    result = audit() if args.command == "audit" else migrate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
