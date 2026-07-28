#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
agent_tagger.py — 素材拆解分析脚本（多游戏 Excel + NocoDB 双写版）

工作流：
  ① 从 Excel「素材总览」读取状态=待分析 的素材
  ② 从 Excel「标签字典」加载该游戏的标签体系
  ③ 读取 OSS 文件内容（如有）
  ④ 按四维拆解框架调 Mimo AI 分析
  ⑤ 写入 Excel「逐帧拆解」+「总结分析」
  ⑥ 同步写入 NocoDB ai_analysis 表
  ⑦ 更新状态=AI已分析（Excel + NocoDB 双写）

用法：
    python agent_tagger.py                      # 分析所有待分析素材
    python agent_tagger.py --limit 5            # 最多分析5条
    python agent_tagger.py --game 心动防线       # 只分析指定游戏
    python agent_tagger.py --dry-run            # 只看不分析
    python agent_tagger.py --xlsx-only          # 只写Excel，不同步NocoDB
"""

import os
import sys
import json
import time
import argparse
import logging
from pathlib import Path
from datetime import datetime

import requests
import yaml
from openpyxl import load_workbook

# ── 加载 .env ──────────────────────────────────────────────
def load_env(path):
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

load_env(Path(__file__).parent / ".env")

# ── 配置 ───────────────────────────────────────────────────
NOCODB_BASE = os.environ.get("NOCODB_BASE", "")
NOCODB_TOKEN = os.environ.get("NOCODB_TOKEN", "")
OPENAI_BASE = os.environ.get("OPENAI_BASE", "")
OPENAI_KEY = os.environ.get("OPENAI_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "mimo-v2.5-pro")
OSS_AK_ID = os.environ.get("OSS_AK_ID", "")
OSS_AK_SECRET = os.environ.get("OSS_AK_SECRET", "")
OSS_ENDPOINT = os.environ.get("OSS_ENDPOINT", "")
OSS_BUCKET = os.environ.get("OSS_BUCKET", "")
TABLE_ASSETS = os.environ.get("NOCODB_TABLE_ASSETS", "")
TABLE_ANALYSIS = os.environ.get("NOCODB_TABLE_ANALYSIS", "")

NOCODB_HEADERS = {
    "xc-token": NOCODB_TOKEN,
    "Content-Type": "application/json",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("tagger")

XLSX_PATH = Path(__file__).parent / "素材拆解分析模板.xlsx"


# ═══════════════════════════════════════════════════════════
# Excel 读写层
# ═══════════════════════════════════════════════════════════

def load_xlsx():
    """加载 Excel 工作簿"""
    if not XLSX_PATH.exists():
        log.error(f"Excel 模板不存在: {XLSX_PATH}")
        sys.exit(1)
    return load_workbook(str(XLSX_PATH))

def save_xlsx(wb):
    """保存 Excel 工作簿"""
    wb.save(str(XLSX_PATH))


# ── 素材总览 ──────────────────────────────────────────────

def get_pending_assets(wb, game_filter=None):
    """从素材总览读取状态=待分析 的素材"""
    ws = wb["素材总览"]
    headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
    col_map = {h: i + 1 for i, h in enumerate(headers)}

    assets = []
    for row in range(2, ws.max_row + 1):
        name = ws.cell(row=row, column=col_map["素材名称"]).value
        if not name:
            continue
        status = str(ws.cell(row=row, column=col_map["状态"]).value or "").strip()
        if status != "待分析":
            continue

        game = str(ws.cell(row=row, column=col_map["游戏名称"]).value or "").strip()
        if game_filter and game != game_filter:
            continue

        asset = {"_row": row}
        for h, c in col_map.items():
            asset[h] = ws.cell(row=row, column=c).value
        assets.append(asset)

    return assets

def update_asset_status(wb, row, status):
    """更新素材总览的状态列"""
    ws = wb["素材总览"]
    headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
    status_col = headers.index("状态") + 1
    ws.cell(row=row, column=status_col).value = status


# ── 标签字典 ──────────────────────────────────────────────

def load_game_tags(wb, game_name):
    """从标签字典加载指定游戏的标签（通用 + 游戏特有）"""
    ws = wb["标签字典"]
    headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
    col_map = {h: i + 1 for i, h in enumerate(headers)}

    tags = {}  # {维度: [标签值, ...]}
    for row in range(2, ws.max_row + 1):
        g = str(ws.cell(row=row, column=col_map["游戏名称"]).value or "").strip()
        if g not in ("通用", game_name):
            continue
        dim = str(ws.cell(row=row, column=col_map["维度"]).value or "").strip()
        val = str(ws.cell(row=row, column=col_map["标签值"]).value or "").strip()
        if dim and val:
            tags.setdefault(dim, [])
            if val not in tags[dim]:
                tags[dim].append(val)

    return tags

def format_tags_for_prompt(tags):
    """将标签字典格式化为 prompt 字符串"""
    lines = []
    for dim, values in tags.items():
        lines.append(f"\n### {dim}")
        for v in values:
            lines.append(f"   - {v}")
    return "\n".join(lines)


# ── 逐帧拆解 ──────────────────────────────────────────────

def write_timeline(wb, asset_name, game_name, timeline):
    """写入逐帧拆解 sheet"""
    ws = wb["逐帧拆解"]
    for item in timeline:
        ws.append([
            asset_name,
            game_name,
            item.get("时间轴", ""),
            item.get("画面内容", ""),
            item.get("画面元素", ""),
            item.get("文案/字幕", ""),
            item.get("音效", ""),
            item.get("目的", ""),
            item.get("可借鉴点", ""),
        ])


# ── 总结分析 ──────────────────────────────────────────────

def write_summary(wb, asset_name, game_name, result):
    """写入总结分析 sheet"""
    ws = wb["总结分析"]
    tags = result.get("标签", {})
    breakdown = result.get("四维拆解", {})

    ws.append([
        asset_name,
        game_name,
        result.get("素材类型", "视频"),
        # 拆解维度
        result.get("Hook内容", ""),
        result.get("Hook类型", ""),
        result.get("人物", ""),
        result.get("UI元素", ""),
        result.get("特效", ""),
        result.get("色彩", ""),
        result.get("字幕", ""),
        result.get("节奏", ""),
        result.get("BGM", ""),
        result.get("核心卖点", ""),
        # 6 维标签
        tags.get("hook_策略", ""),
        tags.get("角色元素", ""),
        tags.get("场景元素", ""),
        tags.get("制作方式", ""),
        tags.get("叙事模式", ""),
        tags.get("画面风格", ""),
        # 扩展
        result.get("目标受众", ""),
        result.get("情绪触发", ""),
        result.get("营销策略", ""),
        result.get("可优化方向", ""),
        # AI
        "AI建议",
        result.get("AI置信度", 0),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "",
    ])


# ═══════════════════════════════════════════════════════════
# NocoDB 同步层
# ═══════════════════════════════════════════════════════════

def noco_get(table_id, where=None, limit=50):
    url = f"{NOCODB_BASE}/api/v2/tables/{table_id}/records"
    params = {"limit": limit}
    if where:
        params["where"] = where
    r = requests.get(url, headers=NOCODB_HEADERS, params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("list", [])

def noco_post(table_id, rows):
    url = f"{NOCODB_BASE}/api/v2/tables/{table_id}/records"
    r = requests.post(url, headers=NOCODB_HEADERS, json=rows, timeout=30)
    r.raise_for_status()
    return r.json()

def noco_patch(table_id, rows):
    url = f"{NOCODB_BASE}/api/v2/tables/{table_id}/records"
    r = requests.patch(url, headers=NOCODB_HEADERS, json=rows, timeout=30)
    r.raise_for_status()
    return r.json()

def sync_to_nocodb(result, tags):
    """同步分析结果到 NocoDB ai_analysis 表"""
    tags = result.get("标签", {})
    row = {
        "开头策略": tags.get("hook_策略", ""),
        "开头文案": result.get("Hook内容", ""),
        "角色元素": tags.get("角色元素", ""),
        "视觉风格": tags.get("画面风格", ""),
        "文案角度": tags.get("叙事模式", ""),
        "BGM类型": result.get("BGM", ""),
        "目标受众": result.get("目标受众", []),
        "情绪触发": result.get("情绪触发", []),
        "AI置信度": result.get("AI置信度", 0),
        "AI原始输出": json.dumps(result, ensure_ascii=False, indent=2),
        "分析时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "人工批注": "",
    }
    noco_post(TABLE_ANALYSIS, [row])


# ═══════════════════════════════════════════════════════════
# OSS 辅助
# ═══════════════════════════════════════════════════════════

def get_oss_bucket():
    import oss2
    auth = oss2.Auth(OSS_AK_ID, OSS_AK_SECRET)
    return oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)

def read_oss_text(bucket, key, max_bytes=500_000):
    try:
        result = bucket.get_object(key, byte_range=(0, max_bytes))
        return result.read().decode("utf-8", errors="replace")
    except Exception as e:
        log.warning(f"读取 OSS 失败 {key}: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# AI Prompt（四维拆解框架）
# ═══════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你是游戏海外市场营销专家，擅长分析 UA 素材。

## 四维拆解框架

1. **前3秒·钩子**：视觉冲击 / 听觉锚点 / 文案悬念 — 用什么方式抓住注意力？
2. **核心卖点·主体**：玩法展示 / 心理诉求 / 角色情感 — 主体展示了什么？
3. **视听语言·包装**：节奏设计 / 风格调性 / 文化适配 — 画面风格、BGM、节奏如何？
4. **转化路径·CTA**：类型 / 文案 / 位置 — 结尾如何引导转化？

## 逐帧拆解要求

将素材按时间段拆解，每个时间段输出：
- 时间轴（如 0:00-0:03）
- 画面内容（发生了什么）
- 画面元素（角色/UI/特效等具体元素）
- 文案/字幕
- 音效（BGM/音效/口播）
- 目的（这段的设计目的）
- 可借鉴点

## 输出要求

返回 JSON（不要多余文字）：

{{
  "逐帧拆解": [
    {{
      "时间轴": "0:00-0:03",
      "画面内容": "...",
      "画面元素": "...",
      "文案/字幕": "...",
      "音效": "...",
      "目的": "...",
      "可借鉴点": "..."
    }}
  ],
  "总结": {{
    "素材类型": "视频/图片/Banner",
    "Hook内容": "前3秒的钩子具体内容",
    "Hook类型": "原始hook分类（如：反常识标题/选择互动/福利展示等）",
    "人物": "出现的角色/人物描述",
    "UI元素": "游戏UI元素",
    "特效": "视觉特效",
    "色彩": "主色调/配色方案",
    "字幕": "字幕使用情况",
    "节奏": "整体节奏描述",
    "BGM": "背景音乐类型",
    "核心卖点": "素材主打的卖点",
    "目标受众": ["从以下选1-3个: 二次元/RPG玩家/泛游戏/FPS玩家/女性向/休闲玩家/核心军事"],
    "情绪触发": ["从以下选1-3个: 爽感/紧张/怀旧/好奇/认同/心动/搞笑/恐惧"],
    "营销策略": "整体营销策略描述",
    "可优化方向": "改进建议",
    "标签": {{
      "hook_策略": "从标签库选最匹配的值",
      "角色元素": "从标签库选最匹配的值",
      "场景元素": "从标签库选最匹配的值",
      "制作方式": "从标签库选最匹配的值",
      "叙事模式": "从标签库选最匹配的值",
      "画面风格": "从标签库选最匹配的值"
    }},
    "AI置信度": 0.0-1.0
  }}
}}

## 标签库

{tags_str}"""

USER_PROMPT = """请分析以下素材：

素材名称：{name}
游戏：{game}
平台：{platform}
类型：{type}
{content_section}"""


# ═══════════════════════════════════════════════════════════
# AI 调用
# ═══════════════════════════════════════════════════════════

def call_mimo_ai(system_prompt, user_prompt):
    url = f"{OPENAI_BASE}/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_KEY}",
        "Content-Type": "application/json",
    }
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 2000,
        "temperature": 0.1,
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=120)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        return json.loads(content.strip())
    except Exception as e:
        log.error(f"AI 调用失败: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════

def run(limit=10, dry_run=False, game_filter=None, xlsx_only=False):
    log.info(f"开始 | limit={limit} | game={game_filter or '全部'} | dry_run={dry_run}")

    wb = load_xlsx()
    assets = get_pending_assets(wb, game_filter)

    if not assets:
        log.info("没有待分析素材，退出")
        return

    log.info(f"找到 {len(assets)} 条待分析素材")

    # 加载标签字典
    all_games = set(a["游戏名称"] for a in assets)
    game_tags = {}
    for g in all_games:
        game_tags[g] = load_game_tags(wb, g)
        log.info(f"  [{g}] 加载 {sum(len(v) for v in game_tags[g].values())} 个标签")

    # OSS
    bucket = None
    if OSS_AK_ID and not dry_run:
        try:
            bucket = get_oss_bucket()
        except Exception:
            pass

    analyzed = 0
    failed = 0

    for asset in assets[:limit]:
        name = asset["素材名称"]
        game = asset["游戏名称"]
        platform = asset.get("平台", "未知")
        asset_type = asset.get("类型", "未知")
        link = asset.get("链接", "")
        row = asset["_row"]

        log.info(f"\n分析: [{row}] {name} ({game})")

        # 读 OSS 内容
        content_section = ""
        if bucket and link and "aliyuncs.com" in str(link):
            try:
                key = str(link).split(".com/", 1)[1]
                text = read_oss_text(bucket, key)
                if text:
                    content_section = f"素材内容（前3000字）：\n{text[:3000]}"
            except Exception:
                pass
        if not content_section:
            content_section = f"素材链接：{link}\n（无法读取文件内容，请根据名称和类型推断）"

        # 构建 prompt
        tags_str = format_tags_for_prompt(game_tags.get(game, {}))
        system = SYSTEM_PROMPT.format(tags_str=tags_str)
        user = USER_PROMPT.format(
            name=name, game=game, platform=platform,
            type=asset_type, content_section=content_section,
        )

        if dry_run:
            log.info(f"  [DRY-RUN] 游戏={game} | 标签维度={len(game_tags.get(game, {}))}")
            continue

        # 调 AI
        result = call_mimo_ai(system, user)
        if not result:
            failed += 1
            continue

        summary = result.get("总结", result)
        timeline = result.get("逐帧拆解", [])

        # 写 Excel
        write_timeline(wb, name, game, timeline)
        write_summary(wb, name, game, summary)
        update_asset_status(wb, row, "AI已分析")
        save_xlsx(wb)
        log.info(f"  Excel 写入完成: {len(timeline)} 帧时间线 + 总结")

        # 同步 NocoDB
        if not xlsx_only and NOCODB_BASE:
            try:
                sync_to_nocodb(summary, summary.get("标签", {}))
                log.info(f"  NocoDB 同步完成")
            except Exception as e:
                log.warning(f"  NocoDB 同步失败: {e}")

        analyzed += 1
        time.sleep(1)

    log.info(f"\n运行结束 | 成功: {analyzed} | 失败: {failed}")
    if analyzed > 0:
        log.info("下一步: 打开 Excel 检查「总结分析」sheet，确认标签后运行 aggregate.py")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="素材拆解分析脚本（多游戏 Excel+NocoDB）")
    parser.add_argument("--limit", type=int, default=10, help="最多分析几条")
    parser.add_argument("--game", type=str, help="只分析指定游戏")
    parser.add_argument("--dry-run", action="store_true", help="只看不分析")
    parser.add_argument("--xlsx-only", action="store_true", help="只写Excel，不同步NocoDB")
    args = parser.parse_args()
    run(limit=args.limit, dry_run=args.dry_run, game_filter=args.game, xlsx_only=args.xlsx_only)
