#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
analyze_parallel.py — 并行版：OSS下载 → 视频分析 → AI分析 → 写回Excel+NocoDB

用法：
    python analyze_parallel.py              # 6并发分析全部
    python analyze_parallel.py --workers 8  # 8并发
    python analyze_parallel.py --limit 20   # 最多分析20条
"""
import os, sys, json, re, time, argparse, logging, subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import requests
import oss2
import numpy as np
import cv2
import imageio_ffmpeg
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
TABLE_TIMELINE = os.environ["NOCODB_TABLE_TIMELINE"]

NOCODB_HEADERS = {"xc-token": NOCODB_TOKEN, "Content-Type": "application/json"}
XLSX_PATH = Path(__file__).parent / "素材拆解分析模板.xlsx"
DOWNLOAD_DIR = Path(__file__).parent / "downloads"
PROGRESS_FILE = Path(__file__).parent / "progress.json"

FFMPEG_PATH = imageio_ffmpeg.get_ffmpeg_exe()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("parallel")

# ── 线程锁 ──
xlsx_lock = Lock()
progress_lock = Lock()
oss_bucket_lock = Lock()


# ═══════════════════════════════════════════════════════════
# 进度追踪
# ═══════════════════════════════════════════════════════════

class ProgressTracker:
    def __init__(self, total):
        self.total = total
        self.done = 0
        self.success = 0
        self.failed = 0
        self.current = {}  # {worker_id: current_task}
        self.start_time = time.time()
        self.results = []

    def update(self, worker_id, task_name, status, detail=""):
        with progress_lock:
            if status == "done":
                self.done += 1
                self.success += 1
                self.current.pop(worker_id, None)
                self.results.append({"name": task_name, "status": "success", "detail": detail})
            elif status == "failed":
                self.done += 1
                self.failed += 1
                self.current.pop(worker_id, None)
                self.results.append({"name": task_name, "status": "failed", "detail": detail})
            else:
                self.current[worker_id] = {"task": task_name, "status": status}
            self._save()

    def _save(self):
        elapsed = time.time() - self.start_time
        eta = (elapsed / max(self.done, 1)) * (self.total - self.done) if self.done > 0 else 0
        data = {
            "total": self.total,
            "done": self.done,
            "success": self.success,
            "failed": self.failed,
            "running": len(self.current),
            "current": self.current,
            "elapsed_sec": round(elapsed),
            "eta_sec": round(eta),
            "results": self.results[-20:],  # 最近20条
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        PROGRESS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ═══════════════════════════════════════════════════════════
# 视频分析
# ═══════════════════════════════════════════════════════════

def read_image_cv2(path):
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)

def extract_video_metadata(filepath):
    p = Path(filepath)
    info = {'filename': p.name, 'size_mb': p.stat().st_size / 1024 / 1024, 'ext': p.suffix.lower()}
    size_match = re.search(r'(\d+)x(\d+)', p.stem)
    if size_match:
        info['resolution'] = f"{size_match.group(1)}x{size_match.group(2)}"

    try:
        result = subprocess.run([FFMPEG_PATH, '-i', str(filepath)],
                                capture_output=True, timeout=15, encoding='utf-8', errors='replace')
        output = result.stderr
        dur_match = re.search(r'Duration:\s*(\d+):(\d+):(\d+)\.(\d+)', output)
        if dur_match:
            h, m, s, ms = int(dur_match.group(1)), int(dur_match.group(2)), int(dur_match.group(3)), int(dur_match.group(4))
            info['duration'] = h * 3600 + m * 60 + s + ms / 100
        vid_match = re.search(r'Video:\s*(\w+).*?(\d{2,4})x(\d{2,4})', output)
        if vid_match:
            info['video_codec'] = vid_match.group(1)
            info['width'] = int(vid_match.group(2))
            info['height'] = int(vid_match.group(3))
        fps_match = re.search(r'(\d+(?:\.\d+)?)\s*fps', output)
        if fps_match:
            info['fps'] = float(fps_match.group(1))
        aud_match = re.search(r'Audio:\s*(\w+).*?(\d+)\s*Hz', output)
        if aud_match:
            info['audio_codec'] = aud_match.group(1)
            info['audio_sample_rate'] = aud_match.group(2)
    except Exception:
        pass

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
    frames_dir = DOWNLOAD_DIR / 'keyframes' / Path(filepath).stem
    frames_dir.mkdir(parents=True, exist_ok=True)
    try:
        cmd = [FFMPEG_PATH, '-i', str(filepath), '-vf', 'select=eq(pict_type\\,I)',
               '-vsync', 'vfr', '-frames:v', str(max_frames), '-q:v', '2',
               str(frames_dir / 'frame_%03d.jpg'), '-y', '-v', 'quiet']
        subprocess.run(cmd, timeout=30, capture_output=True)
        frames = sorted(frames_dir.glob('frame_*.jpg'))
        if len(frames) < 3:
            dur = extract_video_metadata(filepath).get('duration', 10)
            interval = max(1, int(dur / max_frames))
            cmd2 = [FFMPEG_PATH, '-i', str(filepath), '-vf', f'fps=1/{interval}',
                    '-frames:v', str(max_frames), '-q:v', '2',
                    str(frames_dir / 'sample_%03d.jpg'), '-y', '-v', 'quiet']
            subprocess.run(cmd2, timeout=30, capture_output=True)
            frames = sorted(frames_dir.glob('*.jpg'))
        return frames
    except Exception:
        return []

def analyze_keyframes(frame_paths):
    if not frame_paths:
        return {}
    results = {'frame_count': len(frame_paths), 'colors': [], 'brightness': 0, 'complexity': 0}
    total_b, total_c = 0, 0
    for fp in frame_paths[:5]:
        img = read_image_cv2(fp)
        if img is None:
            continue
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        total_b += gray.mean()
        total_c += cv2.Laplacian(gray, cv2.CV_64F).var()
        pixels = img.reshape(-1, 3).astype(np.float32)
        if len(pixels) > 100:
            sample = pixels[np.random.choice(len(pixels), 100, replace=False)]
            criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
            _, _, centers = cv2.kmeans(sample, 3, None, criteria, 3, cv2.KMEANS_RANDOM_CENTERS)
            for c in centers:
                results['colors'].append(f'#{int(c[2]):02x}{int(c[1]):02x}{int(c[0]):02x}')
    n = min(len(frame_paths), 5)
    if n > 0:
        results['brightness'] = round(total_b / n, 1)
        results['complexity'] = round(total_c / n, 1)
    return results


# ═══════════════════════════════════════════════════════════
# 标签 & AI
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

SYSTEM_PROMPT = """你是游戏海外市场营销专家，擅长分析 UA 素材。

## 四维拆解框架

1. **前3秒·钩子**：用什么方式抓住注意力？
2. **核心卖点·主体**：主体展示了什么？
3. **视听语言·包装**：画面风格、BGM、节奏如何？
4. **转化路径·CTA**：结尾如何引导转化？

## 输出要求

返回 JSON（不要多余文字，确保 JSON 完整）：

{{
  "逐帧拆解": [
    {{"时间轴": "0:00-0:03", "画面内容": "...", "画面元素": "...", "文案/字幕": "...", "音效": "...", "目的": "...", "可借鉴点": "..."}}
  ],
  "总结": {{
    "素材类型": "视频/图片",
    "Hook内容": "前3秒钩子",
    "Hook类型": "hook分类",
    "人物": "角色描述",
    "UI元素": "UI元素",
    "特效": "特效",
    "色彩": "配色",
    "字幕": "字幕情况",
    "节奏": "节奏描述",
    "BGM": "BGM类型",
    "核心卖点": "卖点",
    "目标受众": "受众1, 受众2",
    "情绪触发": "情绪1, 情绪2",
    "营销策略": "策略",
    "可优化方向": "优化建议",
    "标签": {{"hook_策略": "...", "角色元素": "...", "场景元素": "...", "制作方式": "...", "叙事模式": "...", "画面风格": "..."}},
    "AI置信度": 0.7
  }}
}}

## 标签库
{tags_str}"""

USER_PROMPT = """分析以下素材：

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
    body = {"model": OPENAI_MODEL, "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ], "max_tokens": 4000, "temperature": 0.1}
    try:
        r = requests.post(url, headers=headers, json=body, timeout=120)
        r.raise_for_status()
        content = r.json()["choices"][0]["message"]["content"]
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        elif "```" in content:
            content = content.split("```")[1].split("```")[0]
        content = re.sub(r',\s*}', '}', content.strip())
        content = re.sub(r',\s*]', ']', content)
        return json.loads(content)
    except json.JSONDecodeError:
        try:
            start = content.find('{')
            end = content.rfind('}')
            if start >= 0 and end > start:
                snippet = content[start:end+1]
                snippet = re.sub(r',\s*}', '}', snippet)
                snippet = re.sub(r',\s*]', ']', snippet)
                return json.loads(snippet)
        except Exception:
            pass
        return None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════
# 写回（线程安全）
# ═══════════════════════════════════════════════════════════

def to_cell(val):
    if isinstance(val, list):
        return ", ".join(str(v) for v in val)
    return val if val is not None else ""

def write_to_excel(wb, name, game, result, meta):
    summary = result.get("总结", result)
    timeline = result.get("逐帧拆解", [])
    tags = summary.get("标签", {})
    with xlsx_lock:
        ws_tl = wb["逐帧拆解"]
        for item in timeline:
            ws_tl.append([name, game, item.get("时间轴", ""), item.get("画面内容", ""),
                          item.get("画面元素", ""), item.get("文案/字幕", ""),
                          item.get("音效", ""), item.get("目的", ""), item.get("可借鉴点", "")])
        ws_sum = wb["总结分析"]
        ws_sum.append([name, game, summary.get("素材类型", "视频"),
                        summary.get("Hook内容", ""), summary.get("Hook类型", ""),
                        summary.get("人物", ""), summary.get("UI元素", ""),
                        summary.get("特效", ""), summary.get("色彩", ""),
                        summary.get("字幕", ""), summary.get("节奏", ""),
                        summary.get("BGM", ""), summary.get("核心卖点", ""),
                        tags.get("hook_策略", ""), tags.get("角色元素", ""),
                        tags.get("场景元素", ""), tags.get("制作方式", ""),
                        tags.get("叙事模式", ""), tags.get("画面风格", ""),
                        to_cell(summary.get("目标受众", "")), to_cell(summary.get("情绪触发", "")),
                        summary.get("营销策略", ""), summary.get("可优化方向", ""),
                        "AI建议", summary.get("AI置信度", 0),
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"), ""])
        ws_overview = wb["素材总览"]
        headers = [ws_overview.cell(row=1, column=c).value for c in range(1, ws_overview.max_column + 1)]
        status_col = headers.index("状态") + 1
        for row in range(2, ws_overview.max_row + 1):
            if ws_overview.cell(row=row, column=1).value == name:
                ws_overview.cell(row=row, column=status_col).value = "AI已分析"
                break

def sync_to_nocodb(result, meta, row_data):
    summary = result.get("总结", result)
    tags = summary.get("标签", {})
    row = {
        "素材名称": row_data.get("素材名称", ""), "游戏名称": row_data.get("游戏名称", ""),
        "素材类型": summary.get("素材类型", "视频"),
        "Hook内容": summary.get("Hook内容", ""), "Hook类型": summary.get("Hook类型", ""),
        "人物": summary.get("人物", ""), "UI元素": summary.get("UI元素", ""),
        "特效": summary.get("特效", ""), "色彩": summary.get("色彩", ""),
        "字幕": summary.get("字幕", ""), "节奏": summary.get("节奏", ""),
        "BGM": summary.get("BGM", ""), "核心卖点": summary.get("核心卖点", ""),
        "hook_策略": tags.get("hook_策略", ""), "角色元素": tags.get("角色元素", ""),
        "场景元素": tags.get("场景元素", ""), "制作方式": tags.get("制作方式", ""),
        "叙事模式": tags.get("叙事模式", ""), "画面风格": tags.get("画面风格", ""),
        "目标受众": summary.get("目标受众", ""), "情绪触发": summary.get("情绪触发", ""),
        "营销策略": summary.get("营销策略", ""), "可优化方向": summary.get("可优化方向", ""),
        "AI置信度": summary.get("AI置信度", 0),
        "AI原始输出": json.dumps(result, ensure_ascii=False, indent=2),
        "分析时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "标签状态": "AI建议",
    }
    url = f"{NOCODB_BASE}/api/v2/tables/{TABLE_ANALYSIS}/records"
    requests.post(url, headers=NOCODB_HEADERS, json=[row], timeout=30)

    # 逐帧拆解
    timeline = result.get("逐帧拆解", [])
    if timeline:
        tl_rows = [{"素材名称": row_data.get("素材名称", ""), "游戏名称": row_data.get("游戏名称", ""),
                     "时间轴": t.get("时间轴", ""), "画面内容": t.get("画面内容", ""),
                     "画面元素": t.get("画面元素", ""), "文案/字幕": t.get("文案/字幕", ""),
                     "音效": t.get("音效", ""), "目的": t.get("目的", ""), "可借鉴点": t.get("可借鉴点", "")}
                    for t in timeline]
        url2 = f"{NOCODB_BASE}/api/v2/tables/{TABLE_TIMELINE}/records"
        requests.post(url2, headers=NOCODB_HEADERS, json=tl_rows, timeout=30)

    # 更新素材主表状态
    url3 = f"{NOCODB_BASE}/api/v2/tables/{TABLE_ASSETS}/records"
    r = requests.get(url3, headers=NOCODB_HEADERS, params={"where": f'@("素材名称",eq,"{row_data.get("素材名称", "")}")', "limit": 1}, timeout=30)
    if r.ok and r.json().get("list"):
        rid = r.json()["list"][0]["Id"]
        requests.patch(url3, headers=NOCODB_HEADERS, json=[{"Id": rid, "状态": "AI已分析"}], timeout=30)


# ═══════════════════════════════════════════════════════════
# 单条分析任务
# ═══════════════════════════════════════════════════════════

def analyze_one(worker_id, asset, oss_key, oss_size, game_tags, wb, progress):
    name = asset["素材名称"]
    game = asset["游戏名称"]
    local_file = DOWNLOAD_DIR / oss_key.split('/')[-1]

    try:
        # 下载
        progress.update(worker_id, name, "下载中...")
        if not local_file.exists():
            with oss_bucket_lock:
                bucket = get_oss_bucket()
                bucket.get_object_to_file(oss_key, str(local_file))

        # 元数据
        progress.update(worker_id, name, "分析中...")
        meta = extract_video_metadata(local_file)

        # 判断真实文件类型（用实际下载文件的扩展名，不依赖 OSS key）
        actual_ext = Path(local_file).suffix.lower()
        is_video = actual_ext in ('.mp4', '.mov', '.avi', '.mkv', '.flv', '.wmv')
        is_image = actual_ext in ('.png', '.jpg', '.jpeg', '.gif', '.bmp', '.webp')

        # 关键帧（仅视频）
        keyframes = []
        frame_analysis = {}
        if is_video:
            keyframes = extract_keyframes(local_file, max_frames=5)
            frame_analysis = analyze_keyframes(keyframes)
        elif is_image:
            # 图片直接用 opencv 分析
            img = read_image_cv2(local_file)
            if img is not None:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                h, w = img.shape[:2]
                meta['width'] = w
                meta['height'] = h
                frame_analysis = {
                    'frame_count': 1,
                    'brightness': round(float(gray.mean()), 1),
                    'complexity': round(float(cv2.Laplacian(gray, cv2.CV_64F).var()), 1),
                    'colors': [],
                }
                pixels = img.reshape(-1, 3).astype(np.float32)
                if len(pixels) > 100:
                    sample = pixels[np.random.choice(len(pixels), 100, replace=False)]
                    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
                    _, _, centers = cv2.kmeans(sample, 3, None, criteria, 3, cv2.KMEANS_RANDOM_CENTERS)
                    for c in centers:
                        frame_analysis['colors'].append(f'#{int(c[2]):02x}{int(c[1]):02x}{int(c[0]):02x}')

        # 构建 prompt
        asset_type = "图片" if is_image else "视频"
        tags_str = format_tags_for_prompt(game_tags.get(game, {}))
        resolution = meta.get("resolution", f"{meta.get('width', '?')}x{meta.get('height', '?')}")
        content_section = f"文件名: {meta['filename']}\n大小: {meta['size_mb']:.1f}MB"
        if meta.get('duration'):
            content_section += f"\n时长: {meta['duration']:.1f}秒"
        if meta.get('video_codec'):
            content_section += f"\n编码: {meta['video_codec']} FPS={meta.get('fps', '?')}"
        if meta.get('audio_codec'):
            content_section += f"\n音频: {meta['audio_codec']}"
        if frame_analysis.get('colors'):
            content_section += f"\n主色调: {', '.join(frame_analysis['colors'][:3])}"
        if frame_analysis.get('brightness'):
            content_section += f"\n亮度: {frame_analysis['brightness']} 复杂度: {frame_analysis['complexity']}"

        # AI 分析
        progress.update(worker_id, name, "AI 分析中...")
        system = SYSTEM_PROMPT.format(tags_str=tags_str)
        user = USER_PROMPT.format(
            name=name, game=game, platform=asset.get("平台", ""), type=asset_type,
            resolution=resolution, size_mb=meta['size_mb'],
            spend=asset.get("花费", ""), installs=asset.get("安装", ""),
            d1_roi=asset.get("首日ROI", ""), cum_roi=asset.get("累计ROI", ""),
            retention=asset.get("次留", ""), content_section=content_section)
        result = call_mimo_ai(system, user)

        if not result:
            progress.update(worker_id, name, "failed", "AI返回解析失败")
            return False

        # 写回
        progress.update(worker_id, name, "写入数据...")
        write_to_excel(wb, name, game, result, meta)
        sync_to_nocodb(result, meta, asset)

        progress.update(worker_id, name, "done", f"关键帧={len(keyframes)} 置信度={result.get('总结', result).get('AI置信度', '?')}")
        return True

    except Exception as e:
        progress.update(worker_id, name, "failed", str(e)[:100])
        return False


# ═══════════════════════════════════════════════════════════
# OSS
# ═══════════════════════════════════════════════════════════

def get_oss_bucket():
    auth = oss2.Auth(OSS_AK_ID, OSS_AK_SECRET)
    return oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)

def build_oss_index(bucket, prefix='心动防线-日/'):
    index = {}
    for obj in oss2.ObjectIterator(bucket, prefix=prefix):
        if obj.key.endswith('/'):
            continue
        filename = obj.key.split('/')[-1]
        if filename not in index:
            index[filename] = []
        index[filename].append((obj.key, obj.size))
    return index

def match_asset_to_oss(asset_name, oss_index):
    clean = asset_name.strip()
    clean = re.sub(r'\.mp4$', '', clean)
    clean = re.sub(r'_广告名称.*$', '', clean)
    if clean + '.mp4' in oss_index:
        return oss_index[clean + '.mp4'][0]
    if clean in oss_index:
        return oss_index[clean][0]
    for oss_name, entries in oss_index.items():
        oss_clean = oss_name.replace('.mp4', '')
        if clean in oss_clean or oss_clean in clean:
            return entries[0]
    return None


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════

def run(workers=6, limit=999, game_filter=None):
    log.info(f"开始 | workers={workers} | limit={limit}")

    wb = load_workbook(str(XLSX_PATH))
    ws = wb["素材总览"]
    headers = [ws.cell(row=1, column=c).value for c in range(1, ws.max_column + 1)]
    col_map = {h: i + 1 for i, h in enumerate(headers)}

    # 读待分析
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
        assets.append(row_data)

    assets = assets[:limit]
    log.info(f"待分析: {len(assets)} 条")

    # OSS 匹配
    bucket = get_oss_bucket()
    oss_index = build_oss_index(bucket)
    matched = []
    for asset in assets:
        result = match_asset_to_oss(asset["素材名称"], oss_index)
        if result:
            matched.append((asset, result))
    log.info(f"匹配: {len(matched)}/{len(assets)}")

    # 加载标签
    games = set(a["游戏名称"] for a, _ in matched)
    game_tags = {g: load_game_tags(wb, g) for g in games}

    # 并行分析
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    progress = ProgressTracker(len(matched))

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {}
        for i, (asset, (oss_key, oss_size)) in enumerate(matched):
            future = executor.submit(analyze_one, f"W{i%workers}", asset, oss_key, oss_size, game_tags, wb, progress)
            futures[future] = asset["素材名称"]

        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                log.error(f"Task error: {e}")

    # 保存 Excel
    with xlsx_lock:
        wb.save(str(XLSX_PATH))

    log.info(f"\n完成 | 成功: {progress.success} | 失败: {progress.failed}")
    return progress


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="并行分析：OSS→视频分析→AI→Excel+NocoDB")
    parser.add_argument("--workers", type=int, default=6, help="并发数（默认6）")
    parser.add_argument("--limit", type=int, default=999, help="最多分析几条")
    parser.add_argument("--game", type=str, help="指定游戏")
    args = parser.parse_args()
    run(workers=args.workers, limit=args.limit, game_filter=args.game)
