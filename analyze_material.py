"""素材 AI 拆解 — 本地识别(OCR/ASR/CLIP) → evidence JSON → Claude → SQLite.

Usage:
    python analyze_material.py --key mat_xxx              # 分析单个素材
    python analyze_material.py --key mat_xxx --skip-local  # 跳过本地识别(用已有 evidence)
    python analyze_material.py --all --limit 5             # 批量分析
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

import platform
ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "mova_materials.sqlite3"
DOWNLOAD_DIR = ROOT / "downloads" / "new_materials"
LABEL_SCHEMA_PATH = ROOT / "label_schema.yaml"
CLAUDE_CLI = os.environ.get("CLAUDE_CLI_PATH", "claude")
# Cross-platform recognition venv path
if platform.system() == "Windows":
    RECOGNITION_VENV = ROOT / ".venv-recognition" / "Scripts" / "python.exe"
else:
    RECOGNITION_VENV = ROOT / ".venv-recognition" / "bin" / "python"


# ── 本地识别管道 ───────────────────────────────────────────────────

def run_local_recognition(video_path: Path) -> Path | None:
    """运行 OCR+ASR+CLIP 本地识别管道，返回 evidence JSON 路径."""
    work_dir = video_path.parent / f"{video_path.stem}_work"
    evidence_path = video_path.parent / f"{video_path.stem}_evidence.json"

    if evidence_path.exists():
        print(f"  evidence JSON 已存在: {evidence_path}")
        return evidence_path

    if not RECOGNITION_VENV.exists():
        print(f"  ⚠ 识别 venv 未找到，跳过本地识别")
        return None

    print(f"  运行本地识别管道...")
    t0 = time.time()
    try:
        result = subprocess.run(
            [str(RECOGNITION_VENV), "-m", "local_recognition.recognize_asset",
             str(video_path),
             "--work-dir", str(work_dir),
             "--output", str(evidence_path),
             "--language", "ja"],
            capture_output=True, text=True, timeout=600,
            encoding="utf-8", errors="replace",
        )
        elapsed = time.time() - t0
        if evidence_path.exists():
            print(f"  识别完成 ({elapsed:.0f}s): {evidence_path}")
            return evidence_path
        else:
            print(f"  识别失败 ({elapsed:.0f}s): {result.stderr[:200]}")
            return None
    except subprocess.TimeoutExpired:
        print(f"  识别超时 (600s)")
        return None
    except Exception as e:
        print(f"  识别异常: {e}")
        return None


# ── Evidence 摘要 ───────────────────────────────────────────────────

def summarize_evidence(evidence_path: Path) -> str:
    """将 evidence JSON 压缩为 Claude 可读的文本摘要."""
    with open(evidence_path, "r", encoding="utf-8") as f:
        ev = json.load(f)

    lines: list[str] = []
    media = ev.get("media", {})
    lines.append(f"素材类型: {ev.get('asset_type', '?')}")
    lines.append(f"时长: {media.get('duration_sec', '?')}s | 分辨率: {media.get('width', '?')}x{media.get('height', '?')}")
    lines.append(f"有音频: {media.get('has_audio', False)} | 片段数: {len(ev.get('segments', []))}")

    # OCR 全文
    ocr_texts = ev.get("global_evidence", {}).get("ocr_full_text", [])
    if ocr_texts:
        lines.append(f"\nOCR 识别文本 ({len(ocr_texts)} 条):")
        lines.append("; ".join(ocr_texts[:20]))

    # ASR 全文
    speech = ev.get("global_evidence", {}).get("speech_full_text", [])
    if speech:
        lines.append(f"\nASR 语音转写 ({len(speech)} 条):")
        lines.append("; ".join(speech[:15]))

    # CTA
    cta = ev.get("global_evidence", {}).get("cta_present", False)
    lines.append(f"\nCTA 检测: {'有转化引导' if cta else '未检测到'}")

    # 逐段摘要
    lines.append(f"\n片段拆解 ({len(ev.get('segments', []))} 段):")
    for seg in ev.get("segments", []):
        sid = seg.get("segment_id", "?")
        start = seg.get("start_sec", 0)
        end = seg.get("end_sec", 0)
        # OCR
        seg_ocr = [o["text_original"] for o in seg.get("ocr", [])[:3]]
        # ASR
        seg_speech = [s["text_original"] for s in seg.get("speech", [])[:3]]
        # Visual
        visual = seg.get("visual", {}).get("semantic_candidates", [])
        visual_labels = []
        for v in visual:
            if v.get("threshold_passed"):
                visual_labels.append(f"{v['group']}={v['top_label_zh']}({v['relative_score']:.0%})")
            else:
                visual_labels.append(f"{v['group']}≈{v['top_label_zh']}({v['relative_score']:.0%}*)")

        lines.append(f"  {sid} [{start:.0f}-{end:.0f}s]:")
        if seg_ocr:
            lines.append(f"    OCR: {' | '.join(seg_ocr)}")
        if seg_speech:
            lines.append(f"    ASR: {' | '.join(seg_speech)}")
        if visual_labels:
            lines.append(f"    CLIP: {', '.join(visual_labels)}")

    return "\n".join(lines)


# ── label schema ────────────────────────────────────────────────────

def load_label_context() -> str:
    with open(LABEL_SCHEMA_PATH, "r", encoding="utf-8") as f:
        schema = yaml.safe_load(f)
    dims = schema.get("dimensions", {})
    lines: list[str] = ["## 六维标签体系\n"]
    for dim_name, dim_info in dims.items():
        values = dim_info.get("values", [])
        vals_str = "、".join(v["name"] for v in values)
        lines.append(f"- {dim_name}（{dim_info.get('description', '')}）：{vals_str}")
    return "\n".join(lines)


# ── Claude 分析 ────────────────────────────────────────────────────

SYSTEM_PROMPT = """你是游戏广告素材分析专家。你收到的是经过本地 OCR/ASR/CLIP 视觉模型预处理后的素材证据摘要。你需要基于这些证据进行「四维拆解」并从六维标签体系中选择标签。

## 四维拆解框架
1. **前3秒·钩子**：开头用什么方式抓住注意力？
2. **核心卖点·主体**：主体展示了什么核心内容？
3. **视听语言·包装**：画面风格、BGM、节奏如何？
4. **转化路径·CTA**：结尾如何引导转化？

{label_context}

## 输出格式
只输出一个 JSON 对象：
{{
  "逐帧拆解": [
    {{"时间轴":"00-03s","画面内容":"...","画面元素":"...","文案/字幕":"...","音效":"...","目的":"...","可借鉴点":"..."}}
  ],
  "总结": {{
    "Hook内容":"...","Hook类型":"...","人物":"...","UI元素":"...","特效":"...","色彩":"...","字幕":"...","节奏":"...","BGM":"...","核心卖点":"...",
    "hook_策略":"...","角色元素":"...","场景元素":"...","制作方式":"...","叙事模式":"...","画面风格":"...",
    "目标受众":"...","情绪触发":"...","营销策略":"...","可优化方向":"...","AI置信度":0.8
  }}
}}

## 规则
- 标签值必须从六维标签体系中选取，不得自创
- CLIP 标签中带 * 号的表示低于置信度阈值，仅供参考
- 证据中未出现的元素填"未识别到"
- AI置信度按证据充分程度给 0.0-1.0（有完整 OCR+ASR+CLIP 证据的给 0.8+）"""


def call_claude_for_analysis(system_prompt: str, user_prompt: str) -> dict:
    """将 system+user prompt 合并为一次 Claude CLI 调用."""
    full_prompt = system_prompt + "\n\n---\n\n## 素材证据\n\n" + user_prompt

    env = os.environ.copy()
    env["CLAUDE_CODE_SIMPLE"] = "1"
    env["PYTHONUTF8"] = "1"

    cmd = [
        CLAUDE_CLI, "--bare", "--print", "--output-format", "text",
        "-p", full_prompt,
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300,
            env=env, cwd=str(ROOT),
            encoding="utf-8", errors="replace",
        )
        if result.returncode != 0:
            raise RuntimeError(f"Claude exit={result.returncode}: {result.stderr[:300]}")
        output = result.stdout
        if not output:
            raise RuntimeError("Claude 返回空输出")
        return _parse_json_response(output.strip())
    except subprocess.TimeoutExpired:
        raise RuntimeError("Claude 分析超时（300s）")


def _parse_json_response(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start:end + 1]
    text = re.sub(r",\s*}", "}", text)
    text = re.sub(r",\s*]", "]", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"无法解析 Claude 输出的 JSON，前 300 字符: {text[:300]}")


# ── SQLite ──────────────────────────────────────────────────────────

def save_analysis(db_path: Path, material_key: str, material_name: str,
                  analysis: dict, game_name: str = "心动防线") -> None:
    summary = analysis.get("总结", {})
    timeline = analysis.get("逐帧拆解", [])
    now = datetime.now(timezone.utc).isoformat()

    conn = sqlite3.connect(str(db_path))

    def _s(v):
        """Convert list to comma-joined string, or return as-is."""
        if isinstance(v, list): return ", ".join(str(x) for x in v)
        return str(v) if v else ""

    conn.execute("""
        INSERT OR REPLACE INTO ai_analysis (
            material_key, material_name, game_name, asset_type, name,
            hook_content, hook_type, characters, ui_elements, effects, colors,
            subtitles, rhythm, bgm, core_selling_point,
            hook_strategy, character_elements, scene_elements, production_method,
            narrative_mode, visual_style,
            target_audience, emotional_trigger, marketing_strategy, optimization_direction,
            label_status, ai_confidence, analysis_time, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        material_key, material_name, game_name, "video", summary.get("名称", material_name),
        _s(summary.get("Hook内容")), _s(summary.get("Hook类型")), _s(summary.get("人物")),
        _s(summary.get("UI元素")), _s(summary.get("特效")), _s(summary.get("色彩")),
        _s(summary.get("字幕")), _s(summary.get("节奏")), _s(summary.get("BGM")), _s(summary.get("核心卖点")),
        _s(summary.get("hook_策略")), _s(summary.get("角色元素")), _s(summary.get("场景元素")),
        _s(summary.get("制作方式")), _s(summary.get("叙事模式")), _s(summary.get("画面风格")),
        _s(summary.get("目标受众")), _s(summary.get("情绪触发")), _s(summary.get("营销策略")),
        _s(summary.get("可优化方向")),
        "AI已分析", summary.get("AI置信度", 0.8), now, now,
    ))

    conn.execute("DELETE FROM frame_timeline WHERE material_key = ?", (material_key,))
    for seg in timeline:
        conn.execute("""
            INSERT INTO frame_timeline (
                material_key, material_name, game_name, name,
                timeline, frame_content, frame_elements, subtitles, sound_effect,
                purpose, learnable_point, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            material_key, material_name, game_name, seg.get("名称", ""),
            seg.get("时间轴"), seg.get("画面内容"), seg.get("画面元素"),
            seg.get("文案/字幕"), seg.get("音效"),
            seg.get("目的"), seg.get("可借鉴点"), now,
        ))

    conn.commit()
    conn.close()


# ── 主流程 ──────────────────────────────────────────────────────────

def analyze_one(material_key: str, skip_local: bool = False, dry_run: bool = False) -> dict | None:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    reg = conn.execute("SELECT * FROM material_registry WHERE material_key = ?", (material_key,)).fetchone()
    if not reg:
        reg = conn.execute("SELECT * FROM material_registry WHERE material_name = ?", (material_key,)).fetchone()
    if not reg:
        print(f"素材未找到: {material_key}")
        conn.close()
        return None

    material_name = reg["material_name"]
    material_key = reg["material_key"]
    conn.close()
    print(f"分析: {material_name[:60]}")

    # 找下载文件
    local_file = None
    for f in DOWNLOAD_DIR.iterdir():
        norm_f = re.sub(r"[_\-\s]+", "", f.stem.lower())
        norm_m = re.sub(r"[_\-\s]+", "", Path(material_name).stem.lower())
        if norm_f in norm_m or norm_m in norm_f:
            local_file = f
            break
    if not local_file:
        print(f"  未找到下载文件")
        return None
    print(f"  文件: {local_file.name}")

    if dry_run:
        print("  [DRY RUN]")
        return {"material_key": material_key, "dry_run": True}

    # 1. 本地识别
    evidence_path = None
    if not skip_local:
        evidence_path = run_local_recognition(local_file)

    # 如果跳过本地识别，尝试找已有的 evidence JSON
    if skip_local and not evidence_path:
        # 多种命名模式尝试
        candidates = [
            local_file.parent / f"{local_file.stem}_evidence.json",
            local_file.parent / f"{Path(material_name).stem}_evidence.json",
        ]
        # 也搜目录下所有 evidence JSON，做文件名模糊匹配
        for f in local_file.parent.glob("*_evidence.json"):
            candidates.append(f)
        for c in candidates:
            if c.exists():
                evidence_path = c
                print(f"  使用已有 evidence: {c.name}")
                break
    if not evidence_path or not evidence_path.exists():
        print("  WARN: 无 evidence JSON，无法分析")
        return None

    # 2. 构建 evidence 摘要
    evidence_text = summarize_evidence(evidence_path)
    print(f"  evidence 摘要: {len(evidence_text)} 字符")

    # 3. Claude 分析
    label_ctx = load_label_context()
    system_prompt = SYSTEM_PROMPT.replace("{label_context}", label_ctx)
    user_prompt = f"""请基于以下本地识别证据，对游戏广告素材进行四维拆解分析。

素材名称: {material_name}
游戏: 心动防线（日本市场）

## 本地识别证据
{evidence_text}

请严格依据以上证据，输出 JSON 格式的分析结果。不确定的填"未识别到"。"""

    print("  调 Claude 分析...")
    t0 = time.time()
    try:
        analysis = call_claude_for_analysis(system_prompt, user_prompt)
    except RuntimeError as e:
        print(f"  Claude 分析失败: {e}")
        return None
    elapsed = time.time() - t0
    print(f"  分析完成 ({elapsed:.0f}s)")

    # 4. 保存
    save_analysis(DB_PATH, material_key, material_name, analysis)
    print(f"  已保存到 SQLite")
    return analysis


def main():
    parser = argparse.ArgumentParser(description="MOVA 素材 AI 拆解")
    parser.add_argument("--key", help="material_key")
    parser.add_argument("--all", action="store_true", help="批量分析所有未分析素材")
    parser.add_argument("--limit", type=int, default=5, help="批量上限")
    parser.add_argument("--skip-local", action="store_true", help="跳过本地识别(用已有 evidence)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.all:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        analyzed = {r[0] for r in conn.execute("SELECT material_key FROM ai_analysis")}
        registered = conn.execute("SELECT * FROM material_registry ORDER BY updated_at DESC").fetchall()
        conn.close()
        pending = [r for r in registered if r["material_key"] not in analyzed]
        print(f"已分析: {len(analyzed)}, 待分析: {len(pending)}, 本次最多: {args.limit}")
        for reg in pending[:args.limit]:
            analyze_one(reg["material_key"], skip_local=args.skip_local, dry_run=args.dry_run)
    elif args.key:
        analyze_one(args.key, skip_local=args.skip_local, dry_run=args.dry_run)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
