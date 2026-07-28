"""MOVA 报告生成引擎.

纯 SQL 查询 + Python 模板拼文本，不调 AI，秒级响应。
数据源: data/mova_materials.sqlite3

Usage:
    from feishu_bot.report_engine import ReportEngine
    engine = ReportEngine(db_path)
    text = engine.build_daily_report("2026-07-22")
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "mova_materials.sqlite3"

# 异常阈值
ANOMALY_THRESHOLD = 0.20  # ±20%


class ReportEngine:
    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DEFAULT_DB

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    # ── 日报 ────────────────────────────────────────────────────────

    def build_daily_report(self, target_date: str | None = None) -> str:
        """生成日报文本."""
        if target_date is None:
            target_date = (date.today() - timedelta(days=1)).isoformat()
        prev_date = _prev_date(target_date)
        display = _display_date(target_date)

        conn = self._connect()
        lines: list[str] = [f"📊 MOVA 日报 | {display}"]

        # 1. 投放总览
        today = _query_totals(conn, target_date)
        yesterday = _query_totals(conn, prev_date)
        campaign_count = conn.execute(
            "SELECT COUNT(DISTINCT campaign_id) FROM appsflyer_daily_facts WHERE fact_date = ? AND campaign_id != ''",
            (target_date,),
        ).fetchone()[0]

        lines.append("")
        lines.append("▸ 投放总览")
        lines.append(f"  安装  {today['installs']}（Android {today['android_installs']} / iOS {today['ios_installs']}）")
        lines.append(f"  事件  {today['events']:,}")
        lines.append(f"  收入  ${today['revenue']:,.2f}")
        lines.append(f"  活跃 Campaign  {campaign_count} 个")

        # 2. 新素材
        new_count = conn.execute(
            "SELECT COUNT(*) FROM material_registry WHERE updated_at >= ?",
            (target_date,),
        ).fetchone()[0]
        new_names = conn.execute(
            "SELECT material_name FROM material_registry WHERE updated_at >= ? LIMIT 3",
            (target_date,),
        ).fetchall()

        lines.append("")
        lines.append("▸ 新素材")
        if new_count > 0:
            lines.append(f"  发现 {new_count} 个新广告素材，已自动匹配 OSS 并注册")
            for r in new_names:
                lines.append(f"  · {r['material_name'][:60]}")
        else:
            lines.append("  无新素材")

        # 3. 变化趋势
        if yesterday["installs"] > 0:
            i_chg = _pct(today["installs"], yesterday["installs"])
            e_chg = _pct(today["events"], yesterday["events"])
            r_chg = _pct(today["revenue"], yesterday["revenue"])
            lines.append("")
            lines.append("▸ 变化趋势（较前日）")
            lines.append(f"  安装 {_arrow(i_chg)}  |  事件 {_arrow(e_chg)}  |  收入 {_arrow(r_chg)}")

        # 4. Top 5 Campaign
        top_campaigns = conn.execute(
            """SELECT campaign_name, SUM(installs) as inst, SUM(revenue_usd) as rev
               FROM appsflyer_daily_facts
               WHERE fact_date = ? AND campaign_name IS NOT NULL AND campaign_name != ''
               GROUP BY campaign_name ORDER BY inst DESC LIMIT 5""",
            (target_date,),
        ).fetchall()

        if top_campaigns:
            lines.append("")
            lines.append("▸ Top 5 Campaign（按安装）")
            for i, r in enumerate(top_campaigns):
                name = (r["campaign_name"] or "")[:50]
                lines.append(f"  {i+1}. {name}")
                lines.append(f"     {r['inst']} 安装  ${r['rev']:,.2f}")

        # 5. 异常提醒
        anomalies = _detect_anomalies(conn, target_date, prev_date, today, yesterday)
        if anomalies:
            lines.append("")
            lines.append("▸ 异常提醒")
            for a in anomalies:
                lines.append(f"  ⚠ {a}")

        conn.close()
        return "\n".join(lines)

    # ── 周报 ────────────────────────────────────────────────────────

    def build_weekly_report(self, week_end: str | None = None) -> str:
        """生成周报文本（前 7 天）."""
        if week_end is None:
            week_end = (date.today() - timedelta(days=1)).isoformat()
        week_start = _date_add(week_end, -6)
        display = f"{_display_date(week_start)} – {_display_date(week_end)}"

        conn = self._connect()
        lines: list[str] = [f"📈 MOVA 周报 | {display}"]

        # 本周总览
        w = _query_range(conn, week_start, week_end)
        days = _days_between(week_start, week_end)
        lines.append("")
        lines.append("▸ 本周总览")
        lines.append(f"  总安装  {w['installs']:,}    日均 {w['installs'] // max(days, 1)}")
        lines.append(f"  总事件  {w['events']:,}  日均 {w['events'] // max(days, 1)}")
        lines.append(f"  总收入  ${w['revenue']:,.0f}  日均 ${w['revenue'] / max(days, 1):,.0f}")

        # 日趋势
        daily = conn.execute(
            """SELECT fact_date, SUM(installs) as inst, SUM(revenue_usd) as rev
               FROM appsflyer_daily_facts
               WHERE fact_date BETWEEN ? AND ?
               GROUP BY fact_date ORDER BY fact_date""",
            (week_start, week_end),
        ).fetchall()
        if daily:
            bars = _sparkline([r["inst"] for r in daily])
            peak = max(daily, key=lambda r: r["inst"])
            valley = min(daily, key=lambda r: r["inst"])
            lines.append("")
            lines.append("▸ 日趋势（安装）")
            lines.append(f"  {bars}")
            lines.append(f"  峰值: {_display_date(peak['fact_date'])} {peak['inst']}  |  谷值: {_display_date(valley['fact_date'])} {valley['inst']}")

        # 平台对比
        android = _query_range(conn, week_start, week_end, "Android")
        ios = _query_range(conn, week_start, week_end, "iOS")
        lines.append("")
        lines.append("▸ 平台对比")
        lines.append(f"              {'Android':>10s}  {'iOS':>10s}")
        lines.append(f"  安装        {android['installs']:>10,}  {ios['installs']:>10,}")
        lines.append(f"  事件        {android['events']:>10,}  {ios['events']:>10,}")
        lines.append(f"  收入        ${android['revenue']:>9,.0f}  ${ios['revenue']:>9,.0f}")
        if android['installs'] > 0 and ios['installs'] > 0:
            a_cpi = android['revenue'] / android['installs'] if android['installs'] else 0
            i_cpi = ios['revenue'] / ios['installs'] if ios['installs'] else 0
            lines.append(f"  平均 CPI    ${a_cpi:>9.2f}  ${i_cpi:>9.2f}")

        # 新素材
        new_count = conn.execute(
            "SELECT COUNT(*) FROM material_registry WHERE updated_at BETWEEN ? AND ?",
            (week_start, week_end),
        ).fetchone()[0]
        new_names = conn.execute(
            "SELECT material_name FROM material_registry WHERE updated_at BETWEEN ? AND ? LIMIT 5",
            (week_start, week_end),
        ).fetchall()
        lines.append("")
        lines.append("▸ 本周新素材")
        lines.append(f"  新发现 {new_count} 个，已全部注册入本地库")
        for r in new_names:
            lines.append(f"  · {r['material_name'][:60]}")

        # 环比
        prev_start = _date_add(week_start, -7)
        prev_end = _date_add(week_end, -7)
        prev_w = _query_range(conn, prev_start, prev_end)
        if prev_w["installs"] > 0:
            lines.append("")
            lines.append("▸ 变化趋势（较上周）")
            lines.append(f"  安装 {_arrow(_pct(w['installs'], prev_w['installs']))}  |  收入 {_arrow(_pct(w['revenue'], prev_w['revenue']))}")

        conn.close()
        return "\n".join(lines)

    # ── 趋势 ────────────────────────────────────────────────────────

    def build_trend(self, days: int = 7) -> str:
        """最近 N 天安装/收入走势."""
        end = (date.today() - timedelta(days=1)).isoformat()
        start = _date_add(end, -(days - 1))

        conn = self._connect()
        daily = conn.execute(
            """SELECT fact_date, SUM(installs) as inst, SUM(revenue_usd) as rev
               FROM appsflyer_daily_facts
               WHERE fact_date BETWEEN ? AND ?
               GROUP BY fact_date ORDER BY fact_date""",
            (start, end),
        ).fetchall()
        conn.close()

        lines: list[str] = [f"📉 最近 {days} 天趋势"]
        lines.append("")
        lines.append(f"{'日期':12s} {'安装':>8s} {'收入':>10s}  走势")
        max_inst = max((r["inst"] for r in daily), default=1)
        for r in daily:
            bar_len = int(r["inst"] / max_inst * 20) if max_inst > 0 else 0
            bar = "█" * bar_len
            lines.append(f"  {_display_date(r['fact_date']):10s} {r['inst']:>8,} ${r['rev']:>9,.0f}  {bar}")
        return "\n".join(lines)

    # ── 概况 ────────────────────────────────────────────────────────

    def build_overview(self) -> str:
        """系统总览."""
        conn = self._connect()

        material_count = conn.execute("SELECT COUNT(*) FROM material_registry").fetchone()[0]
        mapping_count = conn.execute("SELECT COUNT(*) FROM material_appsflyer_mapping").fetchone()[0]
        date_range = conn.execute(
            "SELECT MIN(fact_date), MAX(fact_date) FROM appsflyer_daily_facts"
        ).fetchone()
        total = conn.execute(
            "SELECT SUM(installs), SUM(events), SUM(revenue_usd) FROM appsflyer_daily_facts"
        ).fetchone()
        latest = conn.execute(
            "SELECT fact_date, SUM(installs) as inst, SUM(revenue_usd) as rev FROM appsflyer_daily_facts GROUP BY fact_date ORDER BY fact_date DESC LIMIT 1"
        ).fetchone()

        conn.close()

        lines: list[str] = ["📋 MOVA 系统概况"]
        lines.append("")
        lines.append("▸ 素材库")
        lines.append(f"  已注册素材  {material_count}")
        lines.append(f"  已映射投放  {mapping_count}")
        lines.append(f"  映射覆盖率  {mapping_count / max(material_count, 1) * 100:.0f}%")
        lines.append("")
        lines.append("▸ 投放数据")
        lines.append(f"  数据范围  {date_range[0]} – {date_range[1]}")
        lines.append(f"  累计安装  {total[0] or 0:,}")
        lines.append(f"  累计事件  {total[1] or 0:,}")
        lines.append(f"  累计收入  ${total[2] or 0:,.0f}")
        if latest:
            lines.append("")
            lines.append(f"▸ 最新数据（{_display_date(latest['fact_date'])}）")
            lines.append(f"  安装  {latest['inst']:,}")
            lines.append(f"  收入  ${latest['rev']:,.0f}")

        return "\n".join(lines)


# ── helpers ──────────────────────────────────────────────────────────

def _connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _prev_date(date_str: str) -> str:
    return _date_add(date_str, -1)


def _date_add(date_str: str, delta_days: int) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return (d + timedelta(days=delta_days)).strftime("%Y-%m-%d")


def _display_date(date_str: str) -> str:
    d = datetime.strptime(date_str, "%Y-%m-%d")
    return f"{d.month}月{d.day}日"


def _days_between(start: str, end: str) -> int:
    s = datetime.strptime(start, "%Y-%m-%d")
    e = datetime.strptime(end, "%Y-%m-%d")
    return (e - s).days + 1


def _pct(current: float, previous: float) -> float:
    if previous == 0:
        return 1.0 if current > 0 else 0.0
    return (current - previous) / previous


def _arrow(change: float) -> str:
    if change > 0.05:
        return f"↑{change:+.0%}"
    elif change < -0.05:
        return f"↓{change:+.0%}"
    else:
        return f"→{change:+.0%}"


def _query_totals(conn: sqlite3.Connection, date_str: str) -> dict:
    row = conn.execute(
        "SELECT platform, SUM(installs) as inst, SUM(events) as evt, SUM(revenue_usd) as rev FROM appsflyer_daily_facts WHERE fact_date = ? GROUP BY platform",
        (date_str,),
    ).fetchall()
    result = {"installs": 0, "events": 0, "revenue": 0.0, "android_installs": 0, "ios_installs": 0}
    for r in row:
        result["installs"] += r["inst"] or 0
        result["events"] += r["evt"] or 0
        result["revenue"] += r["rev"] or 0.0
        if r["platform"] == "Android":
            result["android_installs"] = r["inst"] or 0
        elif r["platform"] == "iOS":
            result["ios_installs"] = r["inst"] or 0
    return result


def _query_range(conn: sqlite3.Connection, start: str, end: str, platform: str | None = None) -> dict:
    query = "SELECT SUM(installs) as inst, SUM(events) as evt, SUM(revenue_usd) as rev FROM appsflyer_daily_facts WHERE fact_date BETWEEN ? AND ?"
    params: tuple = (start, end)
    if platform:
        query += " AND platform = ?"
        params = (start, end, platform)
    row = conn.execute(query, params).fetchone()
    return {"installs": row["inst"] or 0, "events": row["evt"] or 0, "revenue": row["rev"] or 0.0}


def _detect_anomalies(conn: sqlite3.Connection, today: str, yesterday: str, t: dict, y: dict) -> list[str]:
    warnings: list[str] = []
    # 收入变化
    if y["revenue"] > 0:
        rev_chg = (t["revenue"] - y["revenue"]) / y["revenue"]
        if rev_chg < -ANOMALY_THRESHOLD:
            warnings.append(f"收入较前日下降 {abs(rev_chg):.0%}（${y['revenue']:,.0f} → ${t['revenue']:,.0f}）")
        elif rev_chg > ANOMALY_THRESHOLD:
            warnings.append(f"收入较前日上升 {rev_chg:.0%}（${y['revenue']:,.0f} → ${t['revenue']:,.0f}）")

    # 安装变化
    if y["installs"] > 0:
        inst_chg = (t["installs"] - y["installs"]) / y["installs"]
        if inst_chg < -ANOMALY_THRESHOLD:
            warnings.append(f"安装较前日下降 {abs(inst_chg):.0%}（{y['installs']} → {t['installs']}）")

    # 零安装 Campaign（今天有昨天也有，但今天突然 0 安装）
    zero_install = conn.execute(
        """SELECT DISTINCT campaign_id FROM appsflyer_daily_facts
           WHERE fact_date = ? AND campaign_id IN (
             SELECT DISTINCT campaign_id FROM appsflyer_daily_facts WHERE fact_date = ? AND installs > 0
           )
           GROUP BY campaign_id HAVING SUM(installs) = 0 LIMIT 3""",
        (today, yesterday),
    ).fetchall()
    for r in zero_install:
        warnings.append(f"Campaign {r['campaign_id'][:20]} 今日 0 安装（昨日有）")

    return warnings


def _sparkline(values: list[int]) -> str:
    if not values:
        return ""
    chars = "▁▂▃▄▅▆▇█"
    mx = max(values) if max(values) > 0 else 1
    return " ".join(chars[min(int(v / mx * 7), 7)] for v in values)
