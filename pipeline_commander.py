"""MOVA 总指挥官 v2 — Bot 触发 + 趋势分析 + 历史迭代.

数据源: 飞书 Wiki 表格（所有心动日服 sheet）
触发: Bot /日报 命令 → 自动找最新周期 → 对比上期 → 入库 → 推送

Usage:
    python pipeline_commander.py --chat-id oc_xxx   # 从 Bot 触发
    python pipeline_commander.py --dry-run           # 只看不写
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
SECRET_PATH = ROOT / ".env"
DB_PATH = ROOT / "data" / "mova_materials.sqlite3"
LARK_CLI = os.environ.get("LARK_CLI_PATH", "lark-cli")

SHEET_URL = "https://dreametech.feishu.cn/wiki/JjgNwLEFaitohZk0g73c0yyfnMI"
BASE_TOKEN = os.environ.get("FEISHU_BASE_TOKEN", "")
TABLE_RANKING = "tblD8tjxvItKsElO"
TABLE_AGGREGATION = "tblPHmTzGV5cBCbO"
TEST_GROUP = "oc_bb4354db8aaa610f03f0c1e095dc4bc8"

SIX_DIMS = ["hook_策略", "角色元素", "场景元素", "制作方式", "叙事模式", "画面风格"]

# 心动日服相关的 sheet 识别关键词
XD_SHEET_KEYWORDS = ["心动", "心動"]


# ── 通知 ─────────────────────────────────────────────────────────────

def _load_feishu_token() -> str:
    for p in (ROOT / ".env", SECRET_PATH):
        if not p.is_file(): continue
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()
    app_id = os.environ.get("FEISHU_APP_ID", "")
    app_secret = os.environ.get("FEISHU_APP_SECRET", "")
    r = __import__("requests").post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
    return r.json()["tenant_access_token"]


def notify(msg: str, chat_id: str = "", dry_run: bool = False) -> None:
    """发送通知到指定飞书群."""
    print(f"  >> {msg}")
    if dry_run or not chat_id:
        return
    try:
        import requests as req
        token = _load_feishu_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = {"receive_id": chat_id, "msg_type": "text",
                "content": json.dumps({"text": f">> MOVA: {msg}"[:8000]}, ensure_ascii=False)}
        req.post("https://open.feishu.cn/open-apis/im/v1/messages",
                 params={"receive_id_type": "chat_id"}, headers=headers, json=body, timeout=10)
    except Exception as e:
        print(f"  notify failed: {e}")


# ── Sheet 发现 ────────────────────────────────────────────────────────

def discover_xd_sheets() -> list[dict]:
    """发现所有心动日服相关 sheet，返回 [{sheet_id, sheet_name, period}]."""
    result = subprocess.run(
        [LARK_CLI, "sheets", "+workbook-info", "--url", SHEET_URL, "--format", "json"],
        capture_output=True, text=True, encoding='utf-8', timeout=20)
    data = json.loads(result.stdout)
    sheets = data.get("data", {}).get("sheets", [])

    xd_sheets = []
    for s in sheets:
        name = s.get("sheet_name", "")
        if not any(kw in name for kw in XD_SHEET_KEYWORDS):
            continue
        # 提取周期（如 "截止到0719"、"0525~0531"、"6月总数据"）
        period = ""
        m = re.search(r"截止到(\d{4})", name)
        if m:
            period = m.group(1)
        else:
            m = re.search(r"(\d{4}~\d{4})", name)
            if m:
                period = m.group(1)
            elif "6月" in name:
                period = "0630"
        xd_sheets.append({
            "sheet_id": s["sheet_id"],
            "sheet_name": name,
            "period": period,
            "row_count": s.get("row_count", 0),
        })
    # 按周期排序（最新的在前）
    xd_sheets.sort(key=lambda x: x["period"], reverse=True)
    return xd_sheets


# ── 读取素材数据 ──────────────────────────────────────────────────────

def read_material_data(sheet_id: str) -> list[dict]:
    """从指定 sheet 读取素材级数据."""
    result = subprocess.run(
        [LARK_CLI, "sheets", "+csv-get", "--url", SHEET_URL,
         "--sheet-id", sheet_id, "--range", "A3:O900"],
        capture_output=True, text=True, encoding='utf-8', timeout=45)
    data = json.loads(result.stdout)
    csv_text = data.get("data", {}).get("annotated_csv", "")

    headers_map = ["ad_name", "spend", "ctr", "installs", "conversion_rate",
                   "cpi", "first_pay_users", "first_pay_rate", "d1_recovery",
                   "pay_user_cost", "d1_roi", "cumulative_pay", "cumulative_roi", "retention"]
    rows = []
    for line in csv_text.splitlines():
        m = re.match(r"\[row=(\d+)\]\s*(.*)", line)
        if not m: continue
        row_num = int(m.group(1))
        if row_num < 3: continue
        values = m.group(2).split(",")
        if len(values) < 2 or not values[0].strip(): continue
        row = {}
        for i, h in enumerate(headers_map):
            val = values[i].strip() if i < len(values) else ""
            if h in ("spend", "ctr", "installs", "conversion_rate", "cpi",
                     "first_pay_users", "first_pay_rate", "d1_recovery",
                     "pay_user_cost", "d1_roi", "cumulative_pay", "cumulative_roi", "retention"):
                try:
                    clean = val.replace("$", "").replace("%", "").replace(",", "").strip()
                    if not clean:
                        row[h] = 0
                    elif "%" in val and h not in ("spend", "installs", "first_pay_users", "cumulative_pay", "pay_user_cost", "d1_recovery"):
                        row[h] = float(clean) / 100
                    else:
                        row[h] = float(clean)
                except ValueError:
                    row[h] = 0
            else:
                row[h] = val
        rows.append(row)
    return rows


# ── SQLite 存储 ───────────────────────────────────────────────────────

def ensure_performance_table():
    """确保 material_performance 表存在."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS material_performance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            period TEXT NOT NULL,
            ad_name TEXT NOT NULL,
            spend REAL DEFAULT 0,
            ctr REAL DEFAULT 0,
            installs REAL DEFAULT 0,
            conversion_rate REAL DEFAULT 0,
            cpi REAL DEFAULT 0,
            first_pay_users REAL DEFAULT 0,
            first_pay_rate REAL DEFAULT 0,
            d1_recovery REAL DEFAULT 0,
            pay_user_cost REAL DEFAULT 0,
            d1_roi REAL DEFAULT 0,
            cumulative_pay REAL DEFAULT 0,
            cumulative_roi REAL DEFAULT 0,
            retention REAL DEFAULT 0,
            material_key TEXT DEFAULT '',
            tags_json TEXT DEFAULT '{}',
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mp_period ON material_performance(period)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mp_adname ON material_performance(ad_name)")
    conn.commit()
    conn.close()


def save_period_data(period: str, materials: list[dict]):
    """保存一个周期的数据到 SQLite（先删旧数据再插入）."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM material_performance WHERE period = ?", (period,))
    now = datetime.now(timezone.utc).isoformat()
    for m in materials:
        tags = json.dumps(m.get("tags", {}), ensure_ascii=False)
        conn.execute("""
            INSERT INTO material_performance
                (period, ad_name, spend, ctr, installs, conversion_rate, cpi,
                 first_pay_users, first_pay_rate, d1_recovery, pay_user_cost,
                 d1_roi, cumulative_pay, cumulative_roi, retention,
                 material_key, tags_json, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (period, m.get("ad_name", ""), m.get("spend", 0), m.get("ctr", 0),
              m.get("installs", 0), m.get("conversion_rate", 0), m.get("cpi", 0),
              m.get("first_pay_users", 0), m.get("first_pay_rate", 0),
              m.get("d1_recovery", 0), m.get("pay_user_cost", 0),
              m.get("d1_roi", 0), m.get("cumulative_pay", 0),
              m.get("cumulative_roi", 0), m.get("retention", 0),
              m.get("material_key", ""), tags, now))
    conn.commit()
    conn.close()


def get_previous_period(current_period: str) -> str | None:
    """获取上一个周期."""
    conn = sqlite3.connect(str(DB_PATH))
    r = conn.execute(
        "SELECT DISTINCT period FROM material_performance WHERE period < ? ORDER BY period DESC LIMIT 1",
        (current_period,)).fetchone()
    conn.close()
    return r[0] if r else None


# ── JOIN & 分析 ───────────────────────────────────────────────────────

def join_tags(materials: list[dict]) -> list[dict]:
    """JOIN SQLite 获取 AI 标签."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    tags_map = {}
    for r in conn.execute(
        "SELECT material_key, hook_strategy, character_elements, scene_elements, "
        "production_method, narrative_mode, visual_style FROM ai_analysis"):
        tags_map[r["material_key"]] = {
            "hook_策略": r["hook_strategy"] or "",
            "角色元素": r["character_elements"] or "",
            "场景元素": r["scene_elements"] or "",
            "制作方式": r["production_method"] or "",
            "叙事模式": r["narrative_mode"] or "",
            "画面风格": r["visual_style"] or "",
        }
    reg = {}
    for r in conn.execute("SELECT material_key, material_name, normalized_name FROM material_registry"):
        reg[r["normalized_name"]] = r["material_key"]
    conn.close()

    def _norm(s): return re.sub(r"[_\-\s·\.]+", "", str(s).strip()).lower()
    def _strip(s):
        s = re.sub(r"\.[a-z0-9]{2,5}$", "", s, flags=re.IGNORECASE)
        s = re.sub(r"_[A-Z]{2}\d{2,4}$", "", s)
        s = re.sub(r"广告名称.*$", "", s)
        return s.strip()
    def _key(s):
        v = re.search(r"^[A-Z]\d{3,4}|^[A-Z]_", s.strip())
        d = re.search(r"\d{8}|\d{4}[-/]\d{2}[-/]\d{2}", s.strip())
        return (v.group(0) if v else "", d.group(0).replace("-","").replace("/","") if d else "")

    matched = 0
    for m in materials:
        ad = m.get("ad_name", "")
        clean = _strip(ad)
        cn, sk = _norm(clean), _key(clean)
        mk, best = "", 0
        for rn, rk in reg.items():
            rc = _strip(rn)
            rn_norm, rk_key = _norm(rc), _key(rc)
            score = 0
            if sk[0] and rk_key[0] and sk[0] == rk_key[0]: score += 3
            if sk[1] and rk_key[1] and sk[1] == rk_key[1]: score += 5
            if cn in rn_norm or rn_norm in cn: score += 2
            if score > best: best = score; mk = rk
        if mk and best >= 2:
            m["material_key"] = mk
            m["tags"] = tags_map.get(mk, {})
            matched += 1
        else:
            m["material_key"] = ""; m["tags"] = {}
    return materials


def compute_trends(current: list[dict], prev_period: str | None, chat_id: str, dry: bool) -> str:
    """对比本期 vs 上期趋势."""
    if not prev_period:
        return ""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    prev = list(conn.execute(
        "SELECT * FROM material_performance WHERE period = ?", (prev_period,)))
    conn.close()

    if not prev:
        return ""

    # 本期 vs 上期 汇总对比
    c_spend = sum(m.get("spend", 0) for m in current if m.get("installs", 0) > 0)
    c_inst = sum(m.get("installs", 0) for m in current if m.get("installs", 0) > 0)
    c_roi = sum(m.get("d1_roi", 0) for m in current if m.get("installs", 0) > 0) / max(len([m for m in current if m.get("installs", 0) > 0]), 1)
    p_spend = sum(r["spend"] or 0 for r in prev if (r["installs"] or 0) > 0)
    p_inst = sum(r["installs"] or 0 for r in prev if (r["installs"] or 0) > 0)
    p_roi = sum(r["d1_roi"] or 0 for r in prev if (r["installs"] or 0) > 0) / max(len([r for r in prev if (r["installs"] or 0) > 0]), 1)

    def _chg(cur, pre):
        if pre == 0: return "新增" if cur > 0 else "—"
        return f"{(cur-pre)/pre*100:+.0f}%"

    lines = [
        f">> 趋势对比: 本期 vs {prev_period}",
        f"  花费: ${c_spend:,.0f} ({_chg(c_spend, p_spend)})",
        f"  安装: {c_inst:.0f} ({_chg(c_inst, p_inst)})",
        f"  平均首日ROI: {c_roi*100:.1f}% ({_chg(c_roi, p_roi)})",
    ]
    return "\n".join(lines)


# ── 日报生成 ──────────────────────────────────────────────────────────

def generate_report(materials: list[dict], period: str, prev_period: str | None, chat_id: str, dry: bool) -> str:
    """生成完整日报."""
    valid = [m for m in materials if m.get("installs", 0) > 0]
    tagged = [m for m in valid if m.get("tags")]
    total_spend = sum(m.get("spend", 0) for m in valid)
    total_inst = sum(m.get("installs", 0) for m in valid)

    # Top/Bottom
    by_roi = sorted(valid, key=lambda x: x.get("d1_roi", 0), reverse=True)

    # 聚合
    agg = defaultdict(lambda: {"count": 0, "spend": 0, "inst": 0, "roi": 0, "cpi": 0})
    for m in tagged:
        for dim in SIX_DIMS:
            tag = m["tags"].get(dim, "")
            if not tag: continue
            a = agg[(dim, tag)]
            a["count"] += 1
            a["spend"] += m.get("spend", 0)
            a["inst"] += m.get("installs", 0)
            a["roi"] += m.get("d1_roi", 0)
            a["cpi"] += m.get("cpi", 0)

    lines = [
        f">> MOVA 心动日服日报 | {period} | {datetime.now().strftime('%m/%d %H:%M')}",
        "",
        f"[总览] {len(valid)}素材有数据 | {len(tagged)}已拆解 | ${total_spend:,.0f}花费 | {total_inst:.0f}安装",
        f"[拆解覆盖率] {len(tagged)}/{len(valid)} ({len(tagged)*100//max(len(valid),1)}%)",
        "",
    ]

    # 趋势
    trend_text = compute_trends(materials, prev_period, chat_id, dry)
    if trend_text:
        lines.append(trend_text)
        lines.append("")

    # TOP 5
    lines.append("[TOP 5 首日ROI]")
    for i, m in enumerate(by_roi[:5]):
        ad = (m.get("ad_name") or "")[:45]
        tags = m.get("tags", {})
        ts = ",".join(v for v in tags.values() if v)[:50]
        lines.append(f"  {i+1}. {ad}")
        lines.append(f"     安装{m.get('installs',0):.0f} ROI={m.get('d1_roi',0)*100:.0f}% ${m.get('spend',0):.0f} | {ts}")

    # 标签表现
    if agg:
        lines.append("")
        lines.append("[标签表现 TOP]")
        top_tags = sorted(agg.items(), key=lambda x: x[1]["roi"] / max(x[1]["count"], 1), reverse=True)[:5]
        for (dim, tag), a in top_tags:
            n = a["count"]
            lines.append(f"  {dim}={tag}: {n}素材 ROI={a['roi']/n*100:.0f}% CPI=${a['cpi']/n:.1f}")

    lines.append("")
    lines.append(f"Base: https://dreametech.feishu.cn/base/{BASE_TOKEN}")
    return "\n".join(lines)


# ── main ─────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(description="MOVA 总指挥官 v2")
    parser.add_argument("--chat-id", default=TEST_GROUP, help="通知目标群 chat_id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    chat_id = args.chat_id
    dry = args.dry_run
    t0 = time.time()

    notify(f"日报开始... (目标: {chat_id[:20]}...)", chat_id, dry)

    # Step 1: 发现 sheet
    xd_sheets = discover_xd_sheets()
    if not xd_sheets:
        notify("未找到心动日服 sheet", chat_id, dry)
        return 1
    latest = xd_sheets[0]
    notify(f"最新周期: {latest['sheet_name']} ({latest['row_count']}行)", chat_id, dry)

    # Step 2: 读取数据
    materials = read_material_data(latest["sheet_id"])
    notify(f"读取 {len(materials)} 条素材", chat_id, dry)

    # Step 3: JOIN AI标签
    materials = join_tags(materials)
    tagged = sum(1 for m in materials if m.get("tags"))
    notify(f"JOIN AI标签: {tagged}/{len(materials)} 匹配", chat_id, dry)

    # Step 4: 趋势对比
    prev_period = get_previous_period(latest["period"])
    if prev_period:
        notify(f"上期: {prev_period}", chat_id, dry)
    else:
        notify("首次运行，无上期数据对比", chat_id, dry)

    # Step 5: 保存到 SQLite
    ensure_performance_table()
    if not dry:
        save_period_data(latest["period"], materials)
        notify(f"已保存到本地库 (period={latest['period']})", chat_id, dry)

    # Step 6: 推送 Base
    if not dry:
        valid = [m for m in materials if m.get("installs", 0) > 0]
        by_roi = sorted(valid, key=lambda x: x.get("d1_roi", 0), reverse=True)
        for rank_type, items in [("TOP", by_roi[:10]), ("BOTTOM", by_roi[-10:])]:
            for i, m in enumerate(items):
                try:
                    subprocess.run([
                        LARK_CLI, "base", "+record-upsert", "--base-token", BASE_TOKEN,
                        "--table-id", TABLE_RANKING,
                        "--json", json.dumps({
                            "素材名称": m.get("ad_name", "")[:200],
                            "排名类型": rank_type, "排名": i + 1,
                            "花费": round(m.get("spend", 0), 2),
                            "安装": int(m.get("installs", 0)),
                            "首日ROI": round(m.get("d1_roi", 0) * 100, 1),
                        }, ensure_ascii=False), "--format", "json",
                    ], capture_output=True, text=True, encoding='utf-8', timeout=15)
                except: pass
        notify("Base 排行已更新", chat_id, dry)

    # Step 7: 生成并推送日报
    report = generate_report(materials, latest["period"], prev_period, chat_id, dry)
    print(report)
    if not dry:
        try:
            import requests as req
            token = _load_feishu_token()
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            body = {"receive_id": chat_id, "msg_type": "text",
                    "content": json.dumps({"text": report[:8000]}, ensure_ascii=False)}
            req.post("https://open.feishu.cn/open-apis/im/v1/messages",
                     params={"receive_id_type": "chat_id"}, headers=headers, json=body, timeout=10)
        except: pass

    elapsed = time.time() - t0
    notify(f"完成! {elapsed:.0f}s | {len(materials)}素材 | {tagged}已拆解", chat_id, dry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
