"""MOVA 全量分析管道 — 累计表300条 → OSS匹配 → 下载 → AI拆解 → 入库 → 回归分析.

Usage:
    python batch_analysis_full.py --chat-id oc_xxx
    python batch_analysis_full.py --limit 5 --dry-run  # 测试5条
"""

from __future__ import annotations

import argparse, hashlib, json, os, re, shutil, sqlite3, subprocess, sys, threading, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import oss2

ROOT = Path(__file__).resolve().parent
SECRET_PATH = ROOT / ".env"
DB_PATH = ROOT / "data" / "mova_materials.sqlite3"
DOWNLOAD_DIR = ROOT / "downloads" / "batch_analysis"
LARK_CLI = os.environ.get("LARK_CLI_PATH", "lark-cli")
CLAUDE_CLI = os.environ.get("CLAUDE_CLI_PATH", "claude")
SHEET_URL = "https://dreametech.feishu.cn/wiki/JjgNwLEFaitohZk0g73c0yyfnMI"
SHEET_ID = "8wvQBI"  # 截止到0712（心动-日）累计数据
BASE_TOKEN = os.environ.get("FEISHU_BASE_TOKEN", "")
TABLE_AI = "tblNyyHI4nFhl6aQ"
TABLE_RANKING = "tblD8tjxvItKsElO"
TEST_GROUP = "oc_bb4354db8aaa610f03f0c1e095dc4bc8"

OSS_AK = os.environ.get("OSS_AK_ID", "")
OSS_SK = os.environ.get("OSS_AK_SECRET", "")
OSS_ENDPOINT = os.environ.get("OSS_ENDPOINT", "https://oss-cn-shanghai.aliyuncs.com")
OSS_BUCKET = os.environ.get("OSS_BUCKET", "")
OSS_PREFIX = "心动防线-日/"

# 视频 vs 图片区分
VIDEO_EXTS = {'.mp4', '.mov', '.avi', '.webm'}
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp'}

HEADERS = ['ad_name','spend','ctr','installs','cvr','cpi','first_pay','first_pay_rate',
           'd1_recovery','pay_user_cost','d1_roi','cum_pay','cum_roi','retention']

_lock = threading.Lock()
_stats = {"done": 0, "skipped": 0, "failed": 0, "start_time": 0}
_oss_cache: list[dict] | None = None
_chat_id = ""


def _load_token() -> str:
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


def notify(msg: str):
    global _chat_id
    print(f"  >> {msg}")
    if not _chat_id: return
    try:
        import requests as req
        token = _load_token()
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = {"receive_id": _chat_id, "msg_type": "text",
                "content": json.dumps({"text": f">> MOVA分析: {msg}"[:8000]}, ensure_ascii=False)}
        req.post("https://open.feishu.cn/open-apis/im/v1/messages",
                 params={"receive_id_type": "chat_id"}, headers=headers, json=body, timeout=10)
    except Exception as e:
        print(f"  notify err: {e}")


def get_oss_files() -> list[dict]:
    global _oss_cache
    if _oss_cache: return _oss_cache
    auth = oss2.Auth(OSS_AK, OSS_SK)
    bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)
    files = []
    for obj in oss2.ObjectIterator(bucket, prefix=OSS_PREFIX):
        if obj.key.endswith("/"): continue
        name = obj.key.split("/")[-1]
        ext = Path(name).suffix.lower()
        files.append({"key": obj.key, "name": name, "size": obj.size, "ext": ext,
                       "norm": _norm(name), "tokens": _tokens(name)})
    _oss_cache = files
    print(f"  OSS indexed: {len(files)} files")
    return files


def _norm(s): return re.sub(r"[_\-\s·\.]+", "", str(s).strip()).lower()
def _tokens(s):
    ts = []
    for m in re.finditer(r"\d{8}", s): ts.append(m.group())
    for m in re.finditer(r"[a-z0-9]{4,}", s, re.I): ts.append(m.group().lower())
    return ts


def oss_search(ad_name: str) -> dict | None:
    """匹配 OSS 文件."""
    files = get_oss_files()
    target_norm = _norm(ad_name)
    target_tokens = set(_tokens(ad_name))
    ad_ext = Path(ad_name).suffix.lower()
    best, best_score = None, 0
    for f in files:
        score = 0
        if target_norm in f["norm"] or f["norm"] in target_norm: score += 50
        common = target_tokens & set(f["tokens"])
        score += len(common) * 30
        dt = {t for t in target_tokens if t.isdigit() and len(t) == 8}
        df = {t for t in f["tokens"] if t.isdigit() and len(t) == 8}
        if dt & df: score += 40
        if ad_ext and ad_ext == f["ext"]: score += 10
        if score > best_score: best_score = score; best = f
    if best and best_score >= 40:
        return {"key": best["key"], "name": best["name"], "size": best["size"],
                "ext": best["ext"], "score": best_score}
    return None


def analyze_material(ad_name: str, file_path: str, media_type: str) -> dict:
    """用 Claude 分析素材 -> 返回四维拆解+六维标签."""
    tag_prompt = f"""你是游戏广告素材分析专家。分析以下素材并返回JSON。

素材信息: {ad_name}
类型: {media_type}

请按以下结构输出JSON（不要markdown）:
{{
    "hook_content": "前3秒钩子内容",
    "hook_type": "钩子类型",
    "characters": "角色元素",
    "core_selling_point": "核心卖点",
    "ui_elements": "UI元素",
    "effects": "特效",
    "colors": "色彩风格",
    "rhythm": "节奏",
    "bgm": "BGM",
    "hook_strategy": "hook_策略标签(从: 反常识标题/教学攻略/福利展示/擦边角色吸引/剧情抓马/口播推荐/问题型标题/选择互动/福利激励/核心玩法展示/来电钩子/定制互动/噱头爆点 中选择)",
    "character_elements": "角色元素标签(从: 黑皮/猫耳兽耳/Q版角色/秘书职场/多角色/立绘展示/真人出镜/AI虚拟人/无角色 中选择)",
    "scene_elements": "场景元素标签(从: 弹球战斗/选择界面/养成界面/Boss战/胜利结算/福利界面/模拟UI/直播间 中选择)",
    "production_method": "制作方式标签(从: K帧动画/扒改/真人口播/AI口播/实机录屏/图片轮播/混合 中选择)",
    "narrative_mode": "叙事模式标签(从: 问题解法/反差逆袭/攻略教学/角色推荐/悬念揭秘/福利直给/选项驱动/定制互动/擦边吸引/角色展示/聊天模拟/口播推荐 中选择)",
    "visual_style": "画面风格标签(从: 二次元精致渲染/Q版可爱风/AI合成/实机录屏/实拍/混剪/游戏录屏/图文 中选择)",
    "target_audience": "目标受众",
    "emotional_trigger": "情绪触发",
    "analysis_summary": "一句话分析总结",
    "ai_confidence": 0.8
}}

规则:
- 这是日本市场素材，游戏名「心动防线」
- 游戏类型: 弹射RPG，核心玩法是弹球战斗+角色养成
- 未识别到的写"未识别"
- 图片素材不能写BGM/口播/动态描述
- 只输出JSON，不要解释
"""
    sys_file = ROOT / "temp_analysis_prompt.txt"
    sys_file.write_text(tag_prompt, encoding="utf-8")
    user_prompt = f"请分析素材文件: {file_path}\n素材名: {ad_name}\n类型: {media_type}"

    try:
        result = subprocess.run([
            CLAUDE_CLI, "--bare", "--print", "--output-format", "text",
            "--system-prompt-file", str(sys_file), "-p", user_prompt,
        ], capture_output=True, text=True, encoding='utf-8', timeout=120,
           cwd=str(ROOT), env={**os.environ, "CLAUDE_CODE_SIMPLE": "1"})
        raw = result.stdout.strip()
        if raw.startswith("```"): raw = re.sub(r"^```\w*\n?|```$", "", raw).strip()
        return json.loads(raw)
    except Exception as e:
        print(f"  analyze err: {e}")
        return {"error": str(e)}


def save_to_db(ad_name: str, perf: dict, analysis: dict, material_key: str):
    """保存到 SQLite ai_analysis 表."""
    conn = sqlite3.connect(str(DB_PATH))
    now = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT OR REPLACE INTO ai_analysis
        (material_key, material_name, game_name, asset_type, hook_content, hook_type,
         characters, core_selling_point, ui_elements, effects, colors, rhythm, bgm,
         hook_strategy, character_elements, scene_elements, production_method,
         narrative_mode, visual_style, target_audience, emotional_trigger,
         ai_confidence, analysis_time, label_status, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        material_key, ad_name, "心动防线",
        "video" if Path(ad_name).suffix.lower() in VIDEO_EXTS else "image",
        analysis.get("hook_content", ""), analysis.get("hook_type", ""),
        analysis.get("characters", ""), analysis.get("core_selling_point", ""),
        analysis.get("ui_elements", ""), analysis.get("effects", ""),
        analysis.get("colors", ""), analysis.get("rhythm", ""),
        analysis.get("bgm", ""), analysis.get("hook_strategy", ""),
        analysis.get("character_elements", ""), analysis.get("scene_elements", ""),
        analysis.get("production_method", ""), analysis.get("narrative_mode", ""),
        analysis.get("visual_style", ""), analysis.get("target_audience", ""),
        analysis.get("emotional_trigger", ""),
        analysis.get("ai_confidence", 0.8), now, "AI已分析", now
    ))
    conn.commit()
    conn.close()


def is_analyzed(ad_name: str) -> tuple[bool, str]:
    """检查素材是否已拆解."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    r = conn.execute(
        "SELECT material_key FROM ai_analysis WHERE material_name = ?", (ad_name,)).fetchone()
    # Fuzzy match
    if not r:
        clean = re.sub(r"\.[a-z0-9]{2,5}$", "", str(ad_name).strip(), flags=re.I)
        clean = re.sub(r"_[A-Z]{2}\d{2,4}$", "", clean)
        clean = re.sub(r"广告名称.*$", "", clean)
        r = conn.execute(
            "SELECT material_key FROM ai_analysis WHERE material_name LIKE ?",
            (f"%{clean[:30]}%",)).fetchone()
    conn.close()
    return (True, r["material_key"]) if r else (False, "")


def cleanup_downloads(min_free_gb: int = 10):
    """磁盘不足时清理旧下载文件."""
    free = shutil.disk_usage("D:/").free // (1024**3)
    if free > min_free_gb: return
    dl = ROOT / "downloads"
    if not dl.exists(): return
    files = sorted(dl.rglob("*"), key=lambda f: f.stat().st_mtime)
    freed = 0
    for f in files:
        if f.is_file() and f.suffix.lower() in VIDEO_EXTS | IMAGE_EXTS:
            try:
                sz = f.stat().st_size
                f.unlink()
                freed += sz
            except: pass
        if shutil.disk_usage("D:/").free // (1024**3) > min_free_gb: break
    if freed:
        notify(f"磁盘清理: 释放 {freed//1024**2}MB")


def process_one(perf: dict, idx: int, total: int) -> dict:
    """处理单个素材."""
    global _stats
    ad_name = perf["ad_name"]
    media = "video" if Path(ad_name).suffix.lower() in VIDEO_EXTS else "image"
    result = {"ad_name": ad_name, "media": media, "status": "unknown"}

    # 1. 检查已拆解
    done, mk = is_analyzed(ad_name)
    if done:
        with _lock: _stats["skipped"] += 1
        result["status"] = "skipped"
        result["material_key"] = mk
        return result

    # 2. OSS匹配
    match = oss_search(ad_name)
    if not match:
        with _lock: _stats["failed"] += 1
        result["status"] = "no_oss_match"
        return result

    # 3. 下载
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    auth = oss2.Auth(OSS_AK, OSS_SK)
    bucket = oss2.Bucket(auth, OSS_ENDPOINT, OSS_BUCKET)
    local_path = DOWNLOAD_DIR / match["name"]
    if not local_path.exists():
        bucket.get_object_to_file(match["key"], str(local_path))

    # 4. AI分析
    analysis = analyze_material(ad_name, str(local_path), media)
    if "error" in analysis:
        with _lock: _stats["failed"] += 1
        result["status"] = "analysis_failed"
        return result

    # 5. 注册+入库
    mk = "mat_" + hashlib.sha256(ad_name.encode()).hexdigest()[:16]
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("INSERT OR IGNORE INTO material_registry (material_key, game_name, material_name, normalized_name, updated_at) VALUES (?,?,?,?,?)",
                 (mk, "心动防线", ad_name, _norm(ad_name), datetime.now(timezone.utc).isoformat()))
    conn.commit()
    conn.close()

    save_to_db(ad_name, perf, analysis, mk)

    # 6. 推送 Base
    try:
        fields = {
            "素材名称": ad_name[:200], "游戏名称": "心动防线", "素材类型": "视频" if media == "video" else "图片",
            "Hook内容": analysis.get("hook_content","")[:500],
            "Hook类型": analysis.get("hook_type","")[:100],
            "人物": analysis.get("characters","")[:200],
            "核心卖点": analysis.get("core_selling_point","")[:200],
            "hook_策略": analysis.get("hook_strategy","")[:100],
            "角色元素": analysis.get("character_elements","")[:100],
            "场景元素": analysis.get("scene_elements","")[:100],
            "制作方式": analysis.get("production_method","")[:100],
            "叙事模式": analysis.get("narrative_mode","")[:100],
            "画面风格": analysis.get("visual_style","")[:100],
            "目标受众": analysis.get("target_audience","")[:100],
            "AI置信度": analysis.get("ai_confidence",0.8),
            "分析时间": datetime.now(timezone.utc).isoformat(),
        }
        subprocess.run([LARK_CLI, "base", "+record-upsert", "--base-token", BASE_TOKEN,
                        "--table-id", TABLE_AI, "--json", json.dumps(fields, ensure_ascii=False),
                        "--format", "json"],
                       capture_output=True, text=True, encoding='utf-8', timeout=15)
    except: pass

    with _lock: _stats["done"] += 1
    result["status"] = "done"
    result["material_key"] = mk
    return result


def regression_analysis(materials: list[dict]) -> str:
    """回归分析: 哪些标签组合驱动高ROI."""
    tagged = [m for m in materials if m.get("tags") and m.get("installs", 0) > 0]
    if len(tagged) < 10: return "数据不足（需>=10个已拆解有数据的素材）"

    from sklearn.linear_model import LinearRegression
    from sklearn.preprocessing import OneHotEncoder
    import numpy as np

    dims = ["hook_策略", "角色元素", "场景元素", "制作方式", "叙事模式", "画面风格"]
    features = []
    y = []
    for m in tagged:
        feats = []
        for d in dims:
            feats.append(str(m["tags"].get(d, "")))
        features.append(feats)
        y.append(m.get("d1_roi", 0))

    try:
        enc = OneHotEncoder(sparse_output=False, handle_unknown='ignore')
        X = enc.fit_transform(features)
        model = LinearRegression()
        model.fit(X, y)

        coefs = list(zip(enc.get_feature_names_out(), model.coef_))
        coefs.sort(key=lambda x: abs(x[1]), reverse=True)

        lines = ["[回归分析: 标签×首日ROI]", f"样本: {len(tagged)}个素材", "Top 10 特征权重:"]
        for name, coef in coefs[:10]:
            dim, tag = name.split("_", 1)
            lines.append(f"  {dim}={tag}: {'+'if coef>0 else ''}{coef*100:.1f}% ROI")
        return "\n".join(lines)
    except Exception as e:
        return f"回归分析失败: {e}"


def main():
    global _chat_id, _stats
    parser = argparse.ArgumentParser()
    parser.add_argument("--chat-id", default=TEST_GROUP)
    parser.add_argument("--limit", type=int, default=300)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    _chat_id = args.chat_id
    _stats["start_time"] = time.time()

    notify(f"开始全量分析管道 (前{args.limit}条)...")

    # Step 1: 读累计表
    result = subprocess.run([
        LARK_CLI, "sheets", "+csv-get", "--url", SHEET_URL,
        "--sheet-id", SHEET_ID, "--range", f"A35:N{35+args.limit}"],
        capture_output=True, text=True, encoding='utf-8', timeout=60)
    csv = json.loads(result.stdout)["data"]["annotated_csv"]

    materials = []
    for line in csv.splitlines():
        m = re.match(r"\[row=(\d+)\]\s*(.*)", line)
        if not m: continue
        vals = m.group(2).split(",")
        if len(vals) < 2 or not vals[0].strip(): continue
        row = {}
        for i, h in enumerate(HEADERS):
            val = vals[i].strip() if i < len(vals) else ""
            if h == "ad_name": row[h] = val
            else:
                try:
                    clean = val.replace("$","").replace("%","").replace(",","").strip()
                    if not clean: row[h] = 0
                    elif "%" in val and h not in ("spend","installs","first_pay","cum_pay","pay_user_cost","d1_recovery"):
                        row[h] = float(clean)/100
                    else: row[h] = float(clean)
                except: row[h] = 0
        materials.append(row)

    total = len(materials)
    # Count by type
    vids = sum(1 for m in materials if Path(m["ad_name"]).suffix.lower() in VIDEO_EXTS)
    imgs = sum(1 for m in materials if Path(m["ad_name"]).suffix.lower() in IMAGE_EXTS)
    notify(f"读取{total}条 | 视频{vids} | 图片{imgs} | 无扩展名{total-vids-imgs}")

    # Step 2: 检查已拆解
    already = 0
    for m in materials:
        done, mk = is_analyzed(m["ad_name"])
        m["analyzed"] = done
        m["material_key"] = mk
        if done: already += 1
    todo = total - already
    notify(f"已拆解{already} | 待处理{todo}")

    if args.dry_run:
        notify(f"[DRY RUN] 将处理{todo}条")
        return 0

    # Step 3: 分批处理
    batch_size = 5
    for batch_start in range(0, total, batch_size):
        batch = materials[batch_start:batch_start + batch_size]
        # Filter: only process not-yet-analyzed
        todo_batch = [m for m in batch if not m["analyzed"]]
        if not todo_batch:
            continue

        for m in todo_batch:
            process_one(m, batch_start, total)

        elapsed = time.time() - _stats["start_time"]
        rate = (_stats["done"] + _stats["failed"]) / max(elapsed / 60, 1)  # per min
        remaining = len([m for m in materials if not m["analyzed"]])
        eta = remaining / max(rate, 0.1)

        notify(f"进度: {_stats['done']}/{total}完成 | "
               f"跳过{_stats['skipped']} | 失败{_stats['failed']} | "
               f"速率{rate:.0f}/min | 预计{eta:.0f}min完成")

        cleanup_downloads()

    # Step 4: 回归分析
    notify("分析完成，开始回归分析...")
    report = regression_analysis(materials)
    notify(report)

    # Step 5: 汇总
    elapsed = time.time() - _stats["start_time"]
    notify(f"全量分析完成! {_stats['done']}新增 | {_stats['skipped']}跳过 | {_stats['failed']}失败 | 耗时{elapsed/60:.0f}min")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
