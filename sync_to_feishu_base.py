"""SQLite → 飞书 Base 同步脚本.

将本地 SQLite 数据库中的表结构对齐并增量同步到飞书 Base 多维表格。
SQLite 是数据源（计算引擎），Base 是镜像（在线展示）。

Usage:
    python sync_to_feishu_base.py --table appsflyer_daily_facts
    python sync_to_feishu_base.py --table material_appsflyer_mapping
    python sync_to_feishu_base.py --table all --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parent
SECRET_PATH = ROOT / ".env"
DB_PATH = ROOT / "data" / "mova_materials.sqlite3"
BASE_TOKEN = os.environ.get("FEISHU_BASE_TOKEN", "")

# ── 字段映射: SQLite 列名 → (Base field_id, Base 类型) ────────────────
# Base 类型: 1=text, 2=number, 5=datetime, 21=link

APPSFLYER_DAILY_FACTS_MAPPING: dict[str, tuple[str, int]] = {
    "fact_key":      ("fldkhV2BSK", 1),   # 事实键
    "fact_date":     ("fldXgKbwef", 5),   # 日期
    "platform":      ("fldJqvTfHf", 1),   # 平台
    "app_id":        ("fldrLNp0IV", 1),   # App ID
    "campaign_id":   ("fldZWAKqoo", 1),   # Campaign ID
    "campaign_name": ("fldwn6YqRn", 1),   # Campaign名称
    "adset_id":      ("fld1MU2Vvn", 1),   # Adset ID
    "adset_name":    ("fldUwXg9Us", 1),   # Adset名称
    "ad_id":         ("fldqAsioao", 1),   # Ad ID
    "ad_name":       ("fldvQZsWI4", 1),   # AppsFlyer广告名
    "installs":      ("fld5bEhOU7", 2),   # 安装
    "events":        ("fldYoNe4ru", 2),   # 事件数
    "paid_events":   ("fldmPtneeL", 2),   # 付费事件数
    "revenue_usd":   ("fldgWv4cRI", 2),   # 收入USD
    "source_run":    ("fldWfEqoX8", 1),   # 同步批次
    "updated_at":    ("fldr5tL0J5", 5),   # 更新时间
}

MATERIAL_MAPPING_MAPPING: dict[str, tuple[str, int]] = {
    "mapping_key":         ("fld61qt4lu", 1),   # 映射键
    "material_key":        ("fldJSZScGg", 1),   # 素材Key
    "material_name":       ("fldT4X75Ad", 1),   # 素材名称 (join)
    "game_name":           ("fldc4kHtLb", 1),   # 游戏名称 (join)
    "platform":            ("fldiPQ798K", 1),   # 平台
    "app_id":              ("fldcubcCpM", 1),   # App ID
    "campaign_id":         ("fldvr68e4m", 1),   # Campaign ID
    "adset_id":            ("fld74hpPM0", 1),   # Adset ID
    "ad_id":               ("fldAo0CSpl", 1),   # Ad ID
    "ad_name":             ("fldq8hqinT", 1),   # AppsFlyer广告名
    "match_type":          ("fldUe9A4xC", 1),   # 匹配方式
    "confidence":          ("fld472BABz", 2),   # 匹配置信度
    "confirmation_status": ("flddqybP75", 1),   # 人工确认状态
    "updated_at":          ("fldwhOepUo", 5),   # 更新时间
}


# ── config ──────────────────────────────────────────────────────────

def load_config() -> tuple[str, str]:
    for path in (ROOT / ".env", SECRET_PATH):
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            os.environ[key.strip()] = val.strip()
    return os.environ.get("FEISHU_APP_ID", ""), os.environ.get("FEISHU_APP_SECRET", "")


# ── Feishu Base API ─────────────────────────────────────────────────

class BaseSync:
    def __init__(self, app_id: str, app_secret: str, table_id: str, business_key_field_id: str):
        self.app_id = app_id
        self.app_secret = app_secret
        self.table_id = table_id
        self.business_key_field_id = business_key_field_id
        self._token = ""

    def _auth(self) -> str:
        if not self._token:
            r = requests.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={"app_id": self.app_id, "app_secret": self.app_secret},
                timeout=15,
            )
            data = r.json()
            if data.get("code") != 0:
                raise RuntimeError(f"飞书认证失败: {data.get('msg')}")
            self._token = data["tenant_access_token"]
        return self._token

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self._auth()}", "Content-Type": "application/json"}

    def _api(self, method: str, path: str, **kwargs) -> dict:
        r = requests.request(method, f"https://open.feishu.cn{path}", headers=self._headers(), timeout=30, **kwargs)
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"飞书 API 错误 code={data.get('code')} msg={data.get('msg')}")
        return data.get("data", {})

    def list_all_records(self) -> list[dict]:
        """读取全表记录，返回 [{record_id, fields}, ...]"""
        records: list[dict] = []
        page_token: str | None = None
        while True:
            params: dict = {"page_size": 200}
            if page_token:
                params["page_token"] = page_token
            data = self._api("GET", f"/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{self.table_id}/records", params=params)
            items = data.get("items", [])
            records.extend(items)
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
            time.sleep(0.3)
        return records

    def build_key_index(self) -> dict[str, str]:
        """构建 业务键 → record_id 索引."""
        index: dict[str, str] = {}
        for rec in self.list_all_records():
            key_val = rec.get("fields", {}).get(self.business_key_field_id, "")
            if key_val:
                index[str(key_val).strip()] = rec["record_id"]
        print(f"  Base 现有 {len(index)} 条记录（索引键: {self.business_key_field_id}）")
        return index

    def create_record(self, fields: dict[str, Any]) -> str:
        data = self._api("POST", f"/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{self.table_id}/records", json={"fields": fields})
        return data.get("record", {}).get("record_id", "")

    def update_record(self, record_id: str, fields: dict[str, Any]) -> None:
        self._api("PUT", f"/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{self.table_id}/records/{record_id}", json={"fields": fields})


# ── value formatting ────────────────────────────────────────────────

def _to_timestamp(value: Any) -> int | None:
    """将日期字符串转为飞书毫秒时间戳."""
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        # ISO format or date
        if "T" in s:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            dt = datetime.strptime(s[:10], "%Y-%m-%d")
        return int(dt.timestamp() * 1000)
    except (ValueError, OSError):
        return None


def format_field(value: Any, field_type: int) -> Any:
    """将 SQLite 值转为 Base 字段格式."""
    if value is None:
        return None if field_type != 2 else 0
    if field_type == 5:  # datetime → ms timestamp
        return _to_timestamp(value)
    if field_type == 2:  # number
        try:
            return float(value)
        except (ValueError, TypeError):
            return 0
    # type 1 = text
    s = str(value).strip()
    return s if s else None


# ── sync logic ──────────────────────────────────────────────────────

def sync_table(
    table_name: str,
    mapping: dict[str, tuple[str, int]],
    business_key_sqlite: str,
    business_key_base: str,
    base_table_id: str,
    extra_joins: dict[str, str] | None = None,
    dry_run: bool = False,
) -> dict:
    """同步一张 SQLite 表到 Base.

    Args:
        table_name: SQLite 表名
        mapping: SQLite列 → (Base field_id, Base type)
        business_key_sqlite: SQLite 中的业务键列名
        business_key_base: Base 中的业务键 field_id
        base_table_id: Base 表 ID
        extra_joins: 额外需要 join 的字段 {output_col: sql_expression}
        dry_run: 只看不写
    """
    app_id, app_secret = load_config()
    if not app_id or not app_secret:
        raise RuntimeError("飞书应用凭证未配置")

    syncer = BaseSync(app_id, app_secret, base_table_id, business_key_base)
    key_index = syncer.build_key_index()

    # 读 SQLite
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    select_cols = list(mapping.keys())
    if extra_joins:
        select_cols = [c for c in select_cols if c not in extra_joins] + list(extra_joins.keys())

    query = f"SELECT {', '.join(select_cols)} FROM {table_name}"
    rows = list(conn.execute(query))

    created, updated, skipped = 0, 0, 0

    for row in rows:
        row_dict = dict(row)

        # 应用 extra_joins（如 material_registry 的 material_name, game_name）
        if extra_joins:
            for col, sql_expr in extra_joins.items():
                # 简单 join：从 material_registry 获取
                if "material_registry" in sql_expr:
                    material_key = row_dict.get("material_key", "")
                    cur = conn.execute(
                        "SELECT game_name, material_name FROM material_registry WHERE material_key = ?",
                        (material_key,),
                    )
                    reg = cur.fetchone()
                    if reg:
                        if col == "game_name":
                            row_dict[col] = reg["game_name"]
                        elif col == "material_name":
                            row_dict[col] = reg["material_name"]

        # 构建 Base fields
        fields: dict[str, Any] = {}
        for sqlite_col, (base_fid, base_type) in mapping.items():
            val = row_dict.get(sqlite_col)
            formatted = format_field(val, base_type)
            if formatted is not None:
                fields[base_fid] = formatted

        if not fields:
            skipped += 1
            continue

        # 业务键
        biz_key = str(row_dict.get(business_key_sqlite, "")).strip()
        if not biz_key:
            skipped += 1
            continue

        existing_rid = key_index.get(biz_key)

        if dry_run:
            if existing_rid:
                print(f"  [DRY RUN] 更新 {biz_key[:30]}... (rid={existing_rid})")
            else:
                print(f"  [DRY RUN] 新建 {biz_key[:30]}...")
            created += 1 if not existing_rid else 0
            updated += 1 if existing_rid else 0
            continue

        try:
            if existing_rid:
                syncer.update_record(existing_rid, fields)
                updated += 1
            else:
                new_rid = syncer.create_record(fields)
                if new_rid:
                    conn.execute(
                        f"UPDATE {table_name} SET feishu_record_id = ? WHERE {business_key_sqlite} = ?",
                        (new_rid, biz_key),
                    )
                created += 1
        except RuntimeError as e:
            print(f"  ERROR: {biz_key[:30]}... → {e}", file=sys.stderr)

        if (created + updated) % 20 == 0 and not dry_run:
            print(f"  进度: {created + updated}/{len(rows)}")

        time.sleep(0.15)  # 避免触发频率限制

    if not dry_run:
        conn.commit()
    conn.close()

    result = {"table": table_name, "total_sqlite": len(rows), "created": created, "updated": updated, "skipped": skipped}
    print(f"  完成: {created} 新建, {updated} 更新, {skipped} 跳过 (共 {len(rows)} 条)")
    return result


# ── main ─────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite → 飞书 Base 同步")
    parser.add_argument("--table", choices=("appsflyer_daily_facts", "material_appsflyer_mapping", "all"), default="all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    results: list[dict] = []

    if args.table in ("appsflyer_daily_facts", "all"):
        print("=== 同步: appsflyer_daily_facts → AppsFlyer日效果 ===")
        r = sync_table(
            "appsflyer_daily_facts",
            APPSFLYER_DAILY_FACTS_MAPPING,
            business_key_sqlite="fact_key",
            business_key_base="fldkhV2BSK",
            base_table_id="tblWxSuO3zwfZZY8",
            dry_run=args.dry_run,
        )
        results.append(r)

    if args.table in ("material_appsflyer_mapping", "all"):
        print("=== 同步: material_appsflyer_mapping → 素材与AppsFlyer映射 ===")
        r = sync_table(
            "material_appsflyer_mapping",
            MATERIAL_MAPPING_MAPPING,
            business_key_sqlite="mapping_key",
            business_key_base="fld61qt4lu",
            base_table_id="tbla6UqLJBoZPIlq",
            extra_joins={
                "material_name": "join material_registry",
                "game_name": "join material_registry",
            },
            dry_run=args.dry_run,
        )
        results.append(r)

    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}同步完成: {json.dumps(results, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
