"""新素材自动发现管道.

从 AppsFlyer 日效果数据中检测新广告素材名 →
匹配 OSS 对应文件 → 下载 → 注册入库 → 同步 Base → 飞书群通知.

Usage:
    python pipeline_new_material.py                        # 扫描最近一次 pull
    python pipeline_new_material.py --date 2026-07-22      # 指定日期
    python pipeline_new_material.py --dry-run              # 只检测，不执行
    python pipeline_new_material.py --ad-name \"xxx.mp4\"   # 手动指定单个素材
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import oss2

ROOT = Path(__file__).resolve().parent
SECRET_PATH = ROOT / ".env"
DB_PATH = ROOT / "data" / "mova_materials.sqlite3"
DOWNLOAD_DIR = ROOT / "downloads" / "new_materials"

# OSS 配置
OSS_AK_ID = "[REDACTED]"
OSS_AK_SECRET = "[REDACTED]"
OSS_ENDPOINT = "https://oss-cn-shanghai.aliyuncs.com"
OSS_BUCKET = "mova-eu-game-uxd"
OSS_PREFIX = "心动防线-日/"


# ── 名称归一化 ──────────────────────────────────────────────────────

def normalize_name(name: str) -> str:
    """归一化素材名，用于模糊匹配."""
    s = str(name).strip()
    s = re.sub(r"\.[a-z0-9]{2,5}$", "", s, flags=re.IGNORECASE)  # 去扩展名
    s = re.sub(r"[_\-\s·]+", "", s)  # 去分隔符
    return s.lower()


def tokenize(name: str) -> list[str]:
    """提取名称中的关键 token：日期、字母数字段."""
    tokens: list[str] = []
    # 日期: 20260119, 2026-01-19
    for m in re.finditer(r"\d{8}|\d{4}[-/]\d{2}[-/]\d{2}", name):
        tokens.append(m.group().replace("-", "").replace("/", ""))
    # 字母数字段 (>=4个字符)
    for m in re.finditer(r"[a-z0-9]{4,}", name, re.IGNORECASE):
        tokens.append(m.group().lower())
    return tokens


# ── OSS 操作 ────────────────────────────────────────────────────────

class OssFinder:
    def __init__(self):
        auth = oss2.Auth(OSS_AK_ID, OSS_AK_SECRET)
        self.bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)
        self._file_list: list[dict] | None = None

    def list_files(self, refresh: bool = False) -> list[dict]:
        """列出 OSS 前缀下所有文件。缓存结果."""
        if self._file_list is not None and not refresh:
            return self._file_list
        files: list[dict] = []
        for obj in oss2.ObjectIterator(self.bucket, prefix=OSS_PREFIX):
            key = obj.key
            if key.endswith("/"):
                continue
            name = key.split("/")[-1]
            files.append({
                "key": key,
                "name": name,
                "size": obj.size,
                "name_norm": normalize_name(name),
                "tokens": tokenize(name),
            })
        self._file_list = files
        print(f"OSS: {len(files)} 个文件 ({OSS_PREFIX})")
        return files

    def search(self, ad_name: str) -> dict | None:
        """在 OSS 中搜索匹配的素材文件."""
        files = self.list_files()
        target_norm = normalize_name(ad_name)
        target_tokens = set(tokenize(ad_name))

        best: dict | None = None
        best_score = 0

        for f in files:
            score = 0
            # 归一化后子串匹配
            if target_norm in f["name_norm"] or f["name_norm"] in target_norm:
                score += 50
            # Token 交集
            common = target_tokens & set(f["tokens"])
            score += len(common) * 30
            # 同一日期 token
            date_tokens_target = {t for t in target_tokens if t.isdigit() and len(t) == 8}
            date_tokens_file = {t for t in f["tokens"] if t.isdigit() and len(t) == 8}
            if date_tokens_target & date_tokens_file:
                score += 40
            # 扩展名匹配（MP4/图片）
            ad_ext = Path(ad_name).suffix.lower()
            file_ext = Path(f["name"]).suffix.lower()
            if ad_ext == file_ext:
                score += 10

            if score > best_score:
                best_score = score
                best = f

        if best and best_score >= 40:
            return {"key": best["key"], "name": best["name"], "size": best["size"], "score": best_score}
        return None

    def download(self, key: str, local_dir: Path) -> Path:
        """从 OSS 下载文件到本地."""
        local_dir.mkdir(parents=True, exist_ok=True)
        name = key.split("/")[-1]
        target = local_dir / name
        if target.exists():
            print(f"  已存在: {target}")
            return target
        print(f"  下载中: {key} → {target}")
        self.bucket.get_object_to_file(key, str(target))
        print(f"  下载完成: {target} ({target.stat().st_size / 1024:.0f}KB)")
        return target


# ── SQLite 操作 ─────────────────────────────────────────────────────

def get_existing_materials(db_path: Path) -> dict[str, dict]:
    """返回 {normalized_name: {material_key, material_name, ...}}."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    existing: dict[str, dict] = {}
    for r in conn.execute("SELECT * FROM material_registry"):
        existing[r["normalized_name"]] = dict(r)
    conn.close()
    return existing


def register_material(db_path: Path, ad_name: str, oss_key: str, game_name: str = "心动防线") -> str:
    """注册新素材到 material_registry."""
    import hashlib
    material_key = "mat_" + hashlib.sha256(ad_name.encode()).hexdigest()[:16]
    now = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        INSERT OR IGNORE INTO material_registry (material_key, game_name, material_name, normalized_name, updated_at)
        VALUES (?, ?, ?, ?, ?)
    """, (material_key, game_name, ad_name, normalize_name(ad_name), now))
    conn.commit()
    conn.close()
    return material_key


def create_mapping(db_path: Path, material_key: str, app_id: str, platform: str,
                   campaign_id: str, adset_id: str, ad_id: str, ad_name: str,
                   match_type: str = "自动匹配", confidence: float = 0.8) -> str:
    """创建素材→AppsFlyer 映射."""
    import hashlib
    raw = "|".join([material_key, app_id, campaign_id or "", adset_id or "", ad_id or ""])
    mapping_key = "map_" + hashlib.sha256(raw.encode()).hexdigest()[:16]
    now = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        INSERT OR IGNORE INTO material_appsflyer_mapping
            (mapping_key, material_key, app_id, platform, campaign_id, adset_id, ad_id,
             ad_name, normalized_ad_name, match_type, confidence, confirmation_status, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '待人工确认', ?)
    """, (mapping_key, material_key, app_id, platform, campaign_id, adset_id, ad_id,
          ad_name, normalize_name(ad_name), match_type, confidence, now))
    conn.commit()
    conn.close()
    return mapping_key


# ── 飞书通知 ────────────────────────────────────────────────────────

def send_feishu_notification(text: str) -> bool:
    """通过飞书 Bot 发送消息到默认群."""
    import json, requests

    for path in (ROOT / ".env", SECRET_PATH):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()

    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")

    # 获取 token
    r = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret}, timeout=10,
    )
    token = r.json()["tenant_access_token"]

    # 读取 default_chat_id
    state_path = ROOT / "logs" / "feishu_bot_state.json"
    chat_id = ""
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        chat_id = state.get("default_chat_id", "")

    if not chat_id:
        print("  ⚠ 没有 default_chat_id，跳过通知")
        return False

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {
        "receive_id": chat_id,
        "msg_type": "text",
        "content": json.dumps({"text": text[:8000]}, ensure_ascii=False),
    }
    r = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        headers=headers, json=body, timeout=10,
    )
    if r.json().get("code") == 0:
        print(f"  已通知群聊: {chat_id}")
        return True
    else:
        print(f"  通知失败: {r.json().get('msg')}")
        return False


# ── 主流程 ──────────────────────────────────────────────────────────

def detect_new_materials(
    db_path: Path,
    date_filter: str | None = None,
    dry_run: bool = False,
) -> list[dict]:
    """扫描 daily facts 中的广告名，检测新素材.

    Returns:
        [{ad_name, app_id, platform, campaign_id, adset_id, ad_id, ...}]
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # 取最近日期
    if date_filter:
        dates = [date_filter]
    else:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT fact_date FROM appsflyer_daily_facts ORDER BY fact_date DESC LIMIT 1"
        )]
    if not dates:
        print("没有 daily facts 数据，先跑 appsflyer_pull.py")
        conn.close()
        return []

    print(f"扫描日期: {dates[0]}")

    # 获取所有不重复的 ad_name（排除空值）
    rows = conn.execute(
        """SELECT DISTINCT ad_name, app_id, platform, campaign_id, adset_id, ad_id
           FROM appsflyer_daily_facts
           WHERE fact_date = ? AND ad_name IS NOT NULL AND ad_name != ''""",
        (dates[0],),
    ).fetchall()

    # 已有素材
    existing = get_existing_materials(db_path)
    print(f"material_registry: {len(existing)} 个已注册素材")

    new_materials: list[dict] = []
    for r in rows:
        ad_name = r["ad_name"]
        norm = normalize_name(ad_name)
        # 模糊匹配已有素材
        matched = None
        for en, erec in existing.items():
            if norm in en or en in norm:
                matched = erec
                break
        if matched is None:
            new_materials.append({
                "ad_name": ad_name,
                "app_id": r["app_id"],
                "platform": r["platform"],
                "campaign_id": r["campaign_id"],
                "adset_id": r["adset_id"],
                "ad_id": r["ad_id"],
            })

    conn.close()
    print(f"扫描 {len(rows)} 个不重复广告名 → 发现 {len(new_materials)} 个新素材")
    return new_materials


def process_new_materials(new_materials: list[dict], dry_run: bool = False, progress_every: int = 20) -> dict:
    """处理新素材：OSS匹配 → 下载 → 注册 → 映射 → 通知.

    Args:
        progress_every: 每 N 个素材在群里同步一次进度（0=不通知）
    """
    if not new_materials:
        print("没有新素材需要处理")
        return {"found": 0, "matched_oss": 0, "registered": 0, "notified": False}

    oss = OssFinder()
    oss.list_files()  # 预加载缓存

    results = {"found": len(new_materials), "matched_oss": 0, "registered": 0, "notified": False}
    notifications: list[str] = []
    failed: list[str] = []

    for i, nm in enumerate(new_materials):
        ad_name = nm["ad_name"]
        print(f"\n[{i+1}/{len(new_materials)}] {ad_name[:60]}")

        # 1. OSS 搜索
        match = oss.search(ad_name)
        if not match:
            print(f"  ⚠ OSS 未匹配到文件 (score<40)")
            failed.append(f"{ad_name[:40]} (OSS未匹配)")
            continue
        print(f"  OSS 匹配: {match['name']} (score={match['score']})")
        results["matched_oss"] += 1

        if dry_run:
            print(f"  [DRY RUN] 将注册并下载")
            results["registered"] += 1
            notifications.append(f"🆕 新素材: {ad_name[:40]}")
            continue

        # 2. 下载
        try:
            local_file = oss.download(match["key"], DOWNLOAD_DIR)
        except Exception as e:
            print(f"  ✗ 下载失败: {e}")
            failed.append(f"{ad_name[:40]} (下载失败)")
            continue

        # 3. 注册到 material_registry
        material_key = register_material(DB_PATH, ad_name, match["key"])
        print(f"  已注册: {material_key}")

        # 4. 创建映射
        mapping_key = create_mapping(
            DB_PATH, material_key,
            nm["app_id"], nm["platform"],
            nm["campaign_id"], nm["adset_id"], nm["ad_id"], ad_name,
        )
        print(f"  已映射: {mapping_key}")
        results["registered"] += 1

        notifications.append(
            f"🆕 {ad_name[:50]}"
        )
        results["registered"] += 1

        # 进度通知（每 N 个同步一次）
        if progress_every > 0 and not dry_run and results["registered"] % progress_every == 0:
            pct = (i + 1) * 100 // len(new_materials)
            prog_text = (
                f"📦 MOVA 新素材同步: {i+1}/{len(new_materials)} ({pct}%)\n"
                f"  OSS匹配 {results['matched_oss']} | 已注册 {results['registered']} | 跳过/失败 {len(failed)}"
            )
            send_feishu_notification(prog_text)

    # 5. 飞书通知（最终汇总）
    if not dry_run:
        summary = (
            f"📦 MOVA 新素材检测完成 ({datetime.now().strftime('%m-%d %H:%M')})\n"
            f"  扫描: {results['found']} 个广告\n"
            f"  OSS 匹配: {results['matched_oss']}\n"
            f"  成功注册: {results['registered']}\n"
            f"  失败: {len(failed)}\n"
        )
        if failed[:5]:
            summary += f"  前5个失败: {'; '.join(failed[:5])}"
        if results['registered'] > 0:
            summary += f"\n  新增素材已入库 SQLite，可通过 sync_to_feishu_base.py 同步至飞书 Base"
        results["notified"] = send_feishu_notification(summary)

    return results


# ── CLI ─────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="MOVA 新素材自动发现管道")
    parser.add_argument("--date", help="扫描日期 YYYY-MM-DD，默认最近")
    parser.add_argument("--ad-name", help="手动指定单个素材名，跳过扫描")
    parser.add_argument("--dry-run", action="store_true", help="只检测不执行")
    args = parser.parse_args()

    if args.ad_name:
        # 手动单素材模式
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        # 从 daily facts 取该 ad_name 的信息
        row = conn.execute(
            "SELECT DISTINCT ad_name, app_id, platform, campaign_id, adset_id, ad_id FROM appsflyer_daily_facts WHERE ad_name = ? LIMIT 1",
            (args.ad_name,),
        ).fetchone()
        conn.close()
        if not row:
            print(f"ad_name '{args.ad_name}' 不在 daily facts 中", file=sys.stderr)
            return 1
        new_materials = [dict(row)]
    else:
        new_materials = detect_new_materials(DB_PATH, args.date, args.dry_run)

    result = process_new_materials(new_materials, dry_run=args.dry_run)
    print(f"\nDone: found={result['found']} oss_match={result['matched_oss']} registered={result['registered']} notified={result['notified']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
