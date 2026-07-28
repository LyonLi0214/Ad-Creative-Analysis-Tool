#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
analyze_pipeline.py — 一条龙：OSS下载 → 提取元数据 → AI分析 → 写回Excel+NocoDB

用法：
    python analyze_pipeline.py                # 分析全部待分析素材
    python analyze_pipeline.py --limit 5      # 最多分析5条
    python analyze_pipeline.py --dry-run      # 只匹配不分析
    python analyze_pipeline.py --game 心动防线 # 只分析指定游戏
"""
import os, sys, json, re, time, argparse, logging, subprocess
from pathlib import Path
from datetime import datetime

import requests
import oss2
import yaml
import numpy as np
from openpyxl import load_workbook

# ── 加载 .env ──
env_path = Path(__file__).parent / ".env"
for line in env_path.read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

# ── 配置 ──
NOCODB_BASE = os.environ["NOCODB_BASE"]
NOCODB_TOKEN = os.environ["NOCODB_TOKEN"]
OPENAI_BASE = os.environ["OPENAI_BASE"]
OPENAI_KEY = os.environ["OPENAI_KEY"]
OPENAI_MODEL = os.environ["OPENAI_MODEL"]
OSS_AK_ID = os.environ["OSS_AK_ID"]
OSS_AK_SECRET = os.environ["OSS_AK_SECRET"]
OSS_ENDPOINT = os.environ["OSS_ENDPOINT"]
OSS_BUCKET = os.environ["OSS_BUCKET"]
TABLE_ASSETS = os.environ["NOCODB_TABLE_ASSETS"]
TABLE_ANALYSIS = os.environ["NOCODB_TABLE_ANALYSIS"]

NOCODB_HEADERS = {"xc-token": NOCODB_TOKEN, "Content-Type": "application/json"}
XLSX_PATH = Path(__file__).parent / "素材拆解分析模板.xlsx"
DOWNLOAD_DIR = Path(__file__).parent / "downloads"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("pipeline")


# ═══════════════════════════════════════════════════════════
# OSS 匹配 & 下载
# ═══════════════════════════════════════════════════════════

def get_oss_bucket():
    auth = oss2.Auth(OSS_AK_ID, OSS_AK_SECRET)
    return oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)

def build_oss_index(bucket, prefix='心动防线-日/'):
    """构建 OSS 文件名 → OSS key 的索引"""
    log.info(f"构建 OSS 索引 (prefix={prefix})...")
    index = {}
    count = 0
    for obj in oss2.ObjectIterator(bucket, prefix=prefix):
        if obj.key.endswith('/'):
            continue
        filename = obj.key.split('/')[-1]
        # 存多个 key（同名文件可能在不同目录）
        if filename not in index:
            index[filename] = []
        index[filename].append((obj.key, obj.size))
        count += 1
    log.info(f"  索引完成: {count} 个文件, {len(index)} 个唯一文件名")
    return index

def match_asset_to_oss(asset_name, oss_index):
    """把素材名匹配到 OSS 文件"""
    # 清理素材名
    clean = asset_name.strip()
    clean = re.sub(r'\.mp4$', '', clean)
    clean = re.sub(r'_广告名称.*$', '', clean)

    # 精确匹配
    if clean + '.mp4' in oss_index:
        return oss_index[clean + '.mp4'][0]
    if clean in oss_index:
        return oss_index[clean][0]

    # 模糊匹配：素材名是 OSS 文件名的子串
    for oss_name, entries in oss_index.items():
        oss_clean = oss_name.replace('.mp4', '')
        if clean in oss_clean or oss_clean in clean:
            return entries[0]

    return None

def download_from_oss(bucket, oss_key, local_path):
    """从 OSS 下载文件"""
    local_path.parent.mkdir(parents=True, exist_ok=True)
    bucket.get_object_to_file(oss_key, str(local_path))
    return local_path


# ═══════════════════════════════════════════════════════════
# 视频分析（ffmpeg + opencv）
# ═══════════════════════════════════════════════════════════

import imageio_ffmpeg
import cv2
import struct

FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()

def read_image_cv2(path):
    """Read image supporting Chinese paths"""
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)

def extract_video_metadata(filepath):
    """用 ffmpeg 提取视频详细元数据"""
    p = Path(filepath)
    info = {
        'filename': p.name,
        'size_mb': p.stat().st_size / 1024 / 1024,
        'ext': p.suffix.lower(),
    }
    # 从文件名解析分辨率
    size_match = re.search(r'(\d+)x(\d+)', p.stem)
    if size_match:
        info['resolution'] = f"{size_match.group(1)}x{size_match.group(2)}"

    # 用 ffmpeg -i 提取信息
    try:
        result = subprocess.run(
            [FFMPEG_PATH, '-i', str(filepath)],
            capture_output=True, timeout=15, encoding='utf-8', errors='replace'
        )
        output = result.stderr  # ffmpeg outputs info to stderr
        # Duration: 00:00:24.75
        dur_match = re.search(r'Duration:\s*(\d+):(\d+):(\d+)\.(\d+)', output)
        if dur_match:
            h, m, s, ms = int(dur_match.group(1)), int(dur_match.group(2)), int(dur_match.group(3)), int(dur_match.group(4))
            info['duration'] = h * 3600 + m * 60 + s + ms / 100
        # Video: h264, yuvj420p, 720x1280
        vid_match = re.search(r'Video:\s*(\w+).*?(\d{2,4})x(\d{2,4})', output)
        if vid_match:
            info['video_codec'] = vid_match.group(1)
            info['width'] = int(vid_match.group(2))
            info['height'] = int(vid_match.group(3))
        # fps
        fps_match = re.search(r'(\d+(?:\.\d+)?)\s*fps', output)
        if fps_match:
            info['fps'] = float(fps_match.group(1))
        # Audio
        aud_match = re.search(r'Audio:\s*(\w+).*?(\d+)\s*Hz', output)
        if aud_match:
            info['audio_codec'] = aud_match.group(1)
            info['audio_sample_rate'] = aud_match.group(2)
        # Bitrate
        br_match = re.search(r'bitrate:\s*(\d+)\s*kb/s', output)
        if br_match:
            info['bitrate'] = int(br_match.group(1))
    except Exception as e:
        log.warning(f"  ffmpeg metadata failed: {e}")

    # 纯 Python fallback
    if 'duration' not in info:
        try:
            with open(filepath, 'rb') as f:
                data = f.read(8192)
            pos = 0
            while pos < len(data) - 8:
                size = struct.unpack('>I', data[pos:pos+4])[0]
                box_type = data[pos+4:pos+8].decode('ascii', errors='ignore')
                if box_type == 'moov':
                    moov_data = data[pos+8:pos+min(size, 4096)]
                    mpos = 0
                    while mpos < len(moov_data) - 8:
                        msize = struct.unpack('>I', moov_data[mpos:mpos+4])[0]
                        mtype = moov_data[mpos+4:mpos+8].decode('ascii', errors='ignore')
                        if mtype == 'mvhd':
                            version = moov_data[mpos+8]
                            if version == 0:
                                ts = struct.unpack('>I', moov_data[mpos+20:mpos+24])[0]
                                dur = struct.unpack('>I', moov_data[mpos+24:mpos+28])[0]
                            else:
                                ts = struct.unpack('>I', moov_data[mpos+28:mpos+32])[0]
                                dur = struct.unpack('>Q', moov_data[mpos+32:mpos+40])[0]
                            info['duration'] = dur / ts if ts else 0
                            break
                        mpos += max(msize, 1)
                    break
                pos += max(size, 1)
        except Exception:
            pass

    return info

def extract_keyframes(filepath, max_frames=5):
    """用 ffmpeg 提取关键帧"""
    frames_dir = DOWNLOAD_DIR / 'keyframes' / Path(filepath).stem
    frames_dir.mkdir(parents=True, exist_ok=True)

    try:
        # 提取关键帧（I帧）
        cmd = [
            FFMPEG_PATH, '-i', str(filepath),
            '-vf', 'select=eq(pict_type\\,I)',
            '-vsync', 'vfr',
            '-frames:v', str(max_frames),
            '-q:v', '2',
            str(frames_dir / 'frame_%03d.jpg'),
            '-y', '-v', 'quiet'
        ]
        subprocess.run(cmd, timeout=30, capture_output=True)

        # 如果关键帧不够，用均匀采样
        frames = list(frames_dir.glob('frame_*.jpg'))
        if len(frames) < 3:
            cmd2 = [
                FFMPEG_PATH, '-i', str(filepath),
                '-vf', f'fps=1/{max(1, int(extract_video_metadata(filepath).get("duration", 10) / max_frames))}',
                '-frames:v', str(max_frames),
                '-q:v', '2',
                str(frames_dir / 'sample_%03d.jpg'),
                '-y', '-v', 'quiet'
            ]
            subprocess.run(cmd2, timeout=30, capture_output=True)

        return sorted(frames_dir.glob('*.jpg'))
    except Exception as e:
        log.warning(f"  Keyframe extraction failed: {e}")
        return []

def analyze_keyframes(frame_paths):
    """用 opencv 分析关键帧"""
    if not frame_paths:
        return {}

    results = {
        'frame_count': len(frame_paths),
        'colors': [],
        'has_text': False,
        'brightness': 0,
        'complexity': 0,
    }

    total_brightness = 0
    total_complexity = 0

    for fp in frame_paths[:5]:
        img = read_image_cv2(fp)
        if img is None:
            continue

        # 亮度
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        total_brightness += gray.mean()

        # 复杂度（Laplacian方差）
        laplacian = cv2.Laplacian(gray, cv2.CV_64F)
        total_complexity += laplacian.var()

        # 主色调（k-means k=3）
        pixels = img.reshape(-1, 3).astype(np.float32)
        if len(pixels) > 100:
            sample_idx = np.random.choice(len(pixels), 100, replace=False)
            sample = pixels[sample_idx]
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
            _, labels, centers = cv2.kmeans(sample, 3, None, criteria, 3, cv2.KMEANS_RANDOM_CENTERS)
            for center in centers:
                b, g, r = int(center[0]), int(center[1]), int(center[2])
                results['colors'].append(f'#{r:02x}{g:02x}{b:02x}')

    n = min(len(frame_paths), 5)
    if n > 0:
        results['brightness'] = round(total_brightness / n, 1)
        results['complexity'] = round(total_complexity / n, 1)

    return results

def try_ffprobe(filepath):
    """尝试用 ffprobe 提取视频详细信息"""
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'quiet', '-print_format', 'json',
             '-show_format', '-show_streams', str(filepath)],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return None


# ═══════════════════════════════════════════════════════════
# 标签体系
# ═══════════════════════════════════════════════════════════

def load_game_tags(wb, game_name):
    ws = wb["标签字典"]
    headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
    col_map = {h: i + 1 for i, h in enumerate(headers)}
    tags = {}
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
    lines = []
    for dim, values in tags.items():
        lines.append(f"\n### {dim}")
        for v in values:
            lines.append(f"   - {v}")
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# AI 分析
# ═══════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你是游戏海外市场营销专家，擅长分析 UA 素材。

## 四维拆解框架

1. **前3秒·钩子**：视觉冲击 / 听觉锚点 / 文案悬念 — 用什么方式抓住注意力？
2. **核心卖点·主体**：玩法展示 / 心理诉求 / 角色情感 — 主体展示了什么？
3. **视听语言·包装**：节奏设计 / 风格调性 / 文化适配 — 画面风格、BGM、节奏如何？
4. **转化路径·CTA**：类型 / 文案 / 位置 — 结尾如何引导转化？

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
    "Hook类型": "原始hook分类",
    "人物": "出现的角色/人物描述",
    "UI元素": "游戏UI元素",
    "特效": "视觉特效",
    "色彩": "主色调/配色方案",
    "字幕": "字幕使用情况",
    "节奏": "整体节奏描述",
    "BGM": "背景音乐类型",
    "核心卖点": "素材主打的卖点",
    "目标受众": ["从以下选1-3个: 二次元/RPG玩家/泛游戏/FPS玩家/女性向/休闲玩家"],
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

USER_PROMPT = """请根据以下素材信息进行分析：

素材名称：{name}
游戏：{game}
平台：{platform}
类型：{type}
分辨率：{resolution}
文件大小：{size_mb:.1f}MB
投放数据：花费={spend}, 安装={installs}, 首日ROI={d1_roi}, 累计ROI={cum_roi}, 次留={retention}

{content_section}"""

def call_mimo_ai(system_prompt, user_prompt):
    url = f"{OPENAI_BASE}/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"}
    body = {
        "model": OPENAI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 4000,
        "temperature": 0.1,
    }
    try:
        r = requests.post(url, headers=headers, json=body, timeout=120)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        # 提取 JSON
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        content = content.strip()
        # 清理常见问题
        content = re.sub(r',\s*}', '}', content)  # trailing comma
        content = re.sub(r',\s*]', ']', content)  # trailing comma in array
        return json.loads(content)
    except json.JSONDecodeError as e:
        log.warning(f"  JSON解析失败，尝试修复: {e}")
        try:
            # 尝试找到第一个 { 和最后一个 }
            start = content.find('{')
            end = content.rfind('}')
            if start >= 0 and end > start:
                snippet = content[start:end+1]
                snippet = re.sub(r',\s*}', '}', snippet)
                snippet = re.sub(r',\s*]', ']', snippet)
                return json.loads(snippet)
        except Exception:
            pass
        log.error(f"  AI 返回内容无法解析: {content[:200]}")
        return None
    except Exception as e:
        log.error(f"  AI 调用失败: {e}")
        return None


# ═══════════════════════════════════════════════════════════
# 写回
# ═══════════════════════════════════════════════════════════

def to_cell(val):
    """Convert value to Excel-safe format"""
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    return val if val is not None else ""

def write_to_excel(wb, name, game, result, meta, row_data):
    """写入逐帧拆解 + 总结分析"""
    summary = result.get("总结", result)
    timeline = result.get("逐帧拆解", [])
    tags = summary.get("标签", {})

    # 逐帧拆解
    ws_tl = wb["逐帧拆解"]
    for item in timeline:
        ws_tl.append([
            name, game,
            item.get("时间轴", ""), item.get("画面内容", ""),
            item.get("画面元素", ""), item.get("文案/字幕", ""),
            item.get("音效", ""), item.get("目的", ""), item.get("可借鉴点", ""),
        ])

    # 总结分析
    ws_sum = wb["总结分析"]
    ws_sum.append([
        name, game, summary.get("素材类型", "视频"),
        summary.get("Hook内容", ""), summary.get("Hook类型", ""),
        summary.get("人物", ""), summary.get("UI元素", ""),
        summary.get("特效", ""), summary.get("色彩", ""),
        summary.get("字幕", ""), summary.get("节奏", ""),
        summary.get("BGM", ""), summary.get("核心卖点", ""),
        tags.get("hook_策略", ""), tags.get("角色元素", ""),
        tags.get("场景元素", ""), tags.get("制作方式", ""),
        tags.get("叙事模式", ""), tags.get("画面风格", ""),
        to_cell(summary.get("目标受众", "")),
        to_cell(summary.get("情绪触发", "")),
        summary.get("营销策略", ""), summary.get("可优化方向", ""),
        "AI建议", summary.get("AI置信度", 0),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "",
    ])

    # 更新素材总览状态
    ws_overview = wb["素材总览"]
    headers = [ws_overview.cell(row=1, column=c).value for c in range(1, ws_overview.max_column + 1)]
    status_col = headers.index("状态") + 1
    for row in range(2, ws_overview.max_row + 1):
        if ws_overview.cell(row=row, column=1).value == name:
            ws_overview.cell(row=row, column=status_col).value = "AI已分析"
            break

def sync_to_nocodb(result, meta, row_data):
    """同步到 NocoDB ai_analysis 表"""
    summary = result.get("总结", result)
    tags = summary.get("标签", {})
    row = {
        "素材名称": row_data.get("素材名称", ""),
        "游戏名称": row_data.get("游戏名称", ""),
        "素材类型": summary.get("素材类型", "视频"),
        "开头策略": tags.get("hook_策略", ""),
        "开头文案": summary.get("Hook内容", ""),
        "角色元素": tags.get("角色元素", ""),
        "视觉风格": tags.get("画面风格", ""),
        "文案角度": tags.get("叙事模式", ""),
        "BGM类型": summary.get("BGM", ""),
        "目标受众": summary.get("目标受众", []),
        "情绪触发": summary.get("情绪触发", []),
        "Hook内容": summary.get("Hook内容", ""),
        "Hook类型": summary.get("Hook类型", ""),
        "人物": summary.get("人物", ""),
        "UI元素": summary.get("UI元素", ""),
        "特效": summary.get("特效", ""),
        "色彩": summary.get("色彩", ""),
        "字幕": summary.get("字幕", ""),
        "节奏": summary.get("节奏", ""),
        "核心卖点": summary.get("核心卖点", ""),
        "hook_策略": tags.get("hook_策略", ""),
        "场景元素": tags.get("场景元素", ""),
        "制作方式": tags.get("制作方式", ""),
        "叙事模式": tags.get("叙事模式", ""),
        "画面风格": tags.get("画面风格", ""),
        "营销策略": summary.get("营销策略", ""),
        "可优化方向": summary.get("可优化方向", ""),
        "AI置信度": summary.get("AI置信度", 0),
        "AI原始输出": json.dumps(result, ensure_ascii=False, indent=2),
        "分析时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "标签状态": "AI建议",
    }
    url = f"{NOCODB_BASE}/api/v2/tables/{TABLE_ANALYSIS}/records"
    requests.post(url, headers=NOCODB_HEADERS, json=[row], timeout=30)


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════

def run(limit=10, dry_run=False, game_filter=None):
    log.info(f"开始 | limit={limit} | game={game_filter or '全部'} | dry_run={dry_run}")

    wb = load_workbook(str(XLSX_PATH))
    ws = wb["素材总览"]

    # 读表头
    headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
    col_map = {h: i + 1 for i, h in enumerate(headers)}

    # 读待分析素材
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
        row_data = {}
        for h, c in col_map.items():
            row_data[h] = ws.cell(row=row, column=c).value
        row_data["_row"] = row
        assets.append(row_data)

    log.info(f"待分析: {len(assets)} 条")

    # 构建 OSS 索引
    bucket = get_oss_bucket()
    oss_index = build_oss_index(bucket)

    # 匹配
    matched = []
    for asset in assets:
        oss_result = match_asset_to_oss(asset["素材名称"], oss_index)
        if oss_result:
            matched.append((asset, oss_result))
        else:
            log.warning(f"  未匹配: {asset['素材名称']}")

    log.info(f"匹配成功: {len(matched)}/{len(assets)}")

    if dry_run:
        log.info("DRY-RUN 模式，跳过分析")
        for asset, (oss_key, size) in matched[:10]:
            log.info(f"  {asset['素材名称']} → {oss_key} ({size/1024/1024:.1f}MB)")
        return

    # 加载标签
    games = set(a["游戏名称"] for a, _ in matched)
    game_tags = {}
    for g in games:
        game_tags[g] = load_game_tags(wb, g)

    # 分析
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    analyzed = 0
    failed = 0

    for asset, (oss_key, oss_size) in matched[:limit]:
        name = asset["素材名称"]
        game = asset["游戏名称"]
        log.info(f"\n分析: {name}")

        # 下载
        local_file = DOWNLOAD_DIR / oss_key.split('/')[-1]
        if not local_file.exists():
            log.info(f"  下载: {oss_key} ({oss_size/1024/1024:.1f}MB)")
            try:
                download_from_oss(bucket, oss_key, local_file)
            except Exception as e:
                log.error(f"  下载失败: {e}")
                failed += 1
                continue

        # 元数据 + 关键帧分析
        meta = extract_video_metadata(local_file)
        log.info(f"  时长={meta.get('duration', 0):.1f}s 分辨率={meta.get('width', '?')}x{meta.get('height', '?')} 编码={meta.get('video_codec', '?')}")

        # 提取关键帧
        keyframes = []
        frame_analysis = {}
        if meta['ext'] in ('.mp4', '.mov', '.avi', '.mkv'):
            keyframes = extract_keyframes(local_file, max_frames=5)
            frame_analysis = analyze_keyframes(keyframes)
            log.info(f"  关键帧: {len(keyframes)}张 亮度={frame_analysis.get('brightness', '?')} 复杂度={frame_analysis.get('complexity', '?')}")

        # 构建 prompt
        tags_str = format_tags_for_prompt(game_tags.get(game, {}))
        resolution = meta.get("resolution", f"{meta.get('width', '?')}x{meta.get('height', '?')}")
        content_section = f"文件名: {meta['filename']}\n大小: {meta['size_mb']:.1f}MB"
        if meta.get('duration'):
            content_section += f"\n时长: {meta['duration']:.1f}秒"
        if meta.get('video_codec'):
            content_section += f"\n编码: {meta['video_codec']} FPS={meta.get('fps', '?')}"
        if meta.get('audio_codec'):
            content_section += f"\n音频: {meta['audio_codec']} {meta.get('audio_sample_rate', '')}Hz"
        if frame_analysis.get('colors'):
            content_section += f"\n主色调: {', '.join(frame_analysis['colors'][:3])}"
        if frame_analysis.get('brightness'):
            content_section += f"\n画面亮度: {frame_analysis['brightness']} (0-255)"
        if frame_analysis.get('complexity'):
            content_section += f"\n画面复杂度: {frame_analysis['complexity']} (越高越复杂)"
        content_section += f"\n关键帧数: {frame_analysis.get('frame_count', 0)}"

        system = SYSTEM_PROMPT.format(tags_str=tags_str)
        user = USER_PROMPT.format(
            name=name, game=game,
            platform=asset.get("平台", ""),
            type=asset.get("类型", "视频"),
            resolution=resolution,
            size_mb=meta['size_mb'],
            spend=asset.get("花费", ""),
            installs=asset.get("安装", ""),
            d1_roi=asset.get("首日ROI", ""),
            cum_roi=asset.get("累计ROI", ""),
            retention=asset.get("次留", ""),
            content_section=content_section,
        )

        # 调 AI
        result = call_mimo_ai(system, user)
        if not result:
            failed += 1
            continue

        # 写 Excel
        write_to_excel(wb, name, game, result, meta, asset)
        save_xlsx(wb)
        log.info(f"  Excel 写入完成")

        # 同步 NocoDB
        try:
            sync_to_nocodb(result, meta, asset)
            log.info(f"  NocoDB 同步完成")
        except Exception as e:
            log.warning(f"  NocoDB 同步失败: {e}")

        analyzed += 1
        time.sleep(1)

    log.info(f"\n完成 | 成功: {analyzed} | 失败: {failed}")

def save_xlsx(wb):
    wb.save(str(XLSX_PATH))

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="一条龙：OSS下载→AI分析→写回Excel+NocoDB")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--game", type=str)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(limit=args.limit, dry_run=args.dry_run, game_filter=args.game)
