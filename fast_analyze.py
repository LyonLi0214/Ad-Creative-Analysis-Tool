"""快速并行分析 — 4线程同时跑本地识别+Claude"""
import hashlib, json, os, re, sqlite3, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

oss_files = json.loads((ROOT / "data/oss_index.json").read_text(encoding="utf-8"))

def norm(s):
    s = re.sub(r"\.[a-z0-9]{2,5}$", "", str(s).strip(), flags=re.I)
    s = re.sub(r"_[A-Z]{2}\d{2,4}$", "", s)
    s = re.sub(r"广告名称.*$", "", s)
    return re.sub(r"[_\-\s·.]+", "", s).lower()

oss_by_norm = {norm(f["name"]): f for f in oss_files}

# Get remaining materials
db = sqlite3.connect(str(ROOT / "data" / "mova_materials.sqlite3"))
ai_names = {norm(r[0]) for r in db.execute("SELECT material_name FROM ai_analysis")}
remaining = []
for r in db.execute("SELECT ad_name FROM material_performance_0712"):
    na = norm(r[0])
    if not any(na in an or an in na for an in ai_names):
        # Find OSS match
        best = None
        for on, of in oss_by_norm.items():
            if na in on or on in na: best = of["name"]; break
        remaining.append((r[0], best or ""))
db.close()

print(f"Remaining: {len(remaining)}")
total = len(remaining)
_lock = Lock()
_done = 0
_t0 = time.time()

def notify(msg):
    with _lock:
        elapsed = time.time() - _t0
        rate = _done / max(elapsed / 60, 0.1)
        eta = (total - _done) / max(rate, 0.1)
        print(f"  [{_done}/{total}] {msg} | {rate:.1f}/min | ETA {eta:.0f}min", flush=True)

def analyze_one(ad_name, oss_name):
    global _done
    import oss2

    media = "video" if ad_name.startswith("V") or ".mp4" in ad_name.lower() else "image"

    # Download from OSS (with thread-safe temp files via call_claude fix)
    auth = oss2.Auth("[REDACTED]", "[REDACTED]")
    bucket = oss2.Bucket(auth, "https://oss-cn-shanghai.aliyuncs.com", "mova-eu-game-uxd")

    na = norm(ad_name)
    best_key, best_name = "", oss_name
    for on, of in oss_by_norm.items():
        if na in on or on in na:
            best_key = of["key"]
            best_name = of["name"]
            break

    local = None
    if best_key:
        dl_dir = ROOT / "downloads" / "batch_analysis"
        dl_dir.mkdir(parents=True, exist_ok=True)
        local = dl_dir / best_name
        if not local.exists():
            bucket.get_object_to_file(best_key, str(local))

    t0 = time.time()

    # Use existing call_claude adapter (fast, no local recognition needed for basic analysis)
    from feishu_bot.claude_adapter import call_claude

    image_rule = "图片素材不写BGM/口播/动态过程。" if media == "image" else ""
    prompt = f"""你是游戏广告素材分析专家。分析日本市场素材「心动防线」(弹射RPG,日服)。

素材文件名已编码了制作方式、内容类型、角色、格式、团队等信息。请结合命名规律和游戏知识进行专业拆解。

命名规律:
- V=视频 P=图片 | 心动=游戏 | 日期=YYYYMMDD | 日=日服
- 9x16/1x1/16x9=格式 | HSM/ZQ/WHY/MYY/XM/WYX=制作团队
- 扒改/AI/实机/迭代/口播=制作方式 | 角色/战斗/玩法/剧情/福利/选择=内容类型
- 黛梦/特蕾西亚/菲迪/婕拉/阿星=角色名 | Q版/日系/像素/二次元=风格
- 无贴片=无品牌遮盖 | 别选=反常识hook | 倒计时=紧迫感

输出JSON(不要markdown):
{{"前3秒钩子":{{"画面":"钩子画面描述","文案":"屏幕文字","音效":"音频","机制":"抓注意力方式"}},
"核心卖点主体":{{"展示内容":"主体","角色":"角色","玩法":"玩法","差异化":"差异点"}},
"视听语言包装":{{"画面风格":"风格","BGM":"BGM","节奏":"节奏","色彩":"色彩"}},
"转化路径CTA":{{"类型":"CTA类型","文案":"文案","位置":"位置","紧迫感":"设计"}},
"hook_strategy":"反常识标题|教学攻略|福利展示|擦边角色吸引|剧情抓马|口播推荐|问题型标题|选择互动|福利激励|核心玩法展示|来电钩子|定制互动|噱头爆点",
"character_elements":"黑皮|猫耳兽耳|Q版角色|秘书职场|多角色|立绘展示|真人出镜|AI虚拟人|无角色",
"scene_elements":"弹球战斗|选择界面|养成界面|Boss战|胜利结算|福利界面|模拟UI|直播间",
"production_method":"K帧动画|扒改|真人口播|AI口播|实机录屏|图片轮播|混合",
"narrative_mode":"问题解法|反差逆袭|攻略教学|角色推荐|悬念揭秘|福利直给|选项驱动|定制互动|擦边吸引|角色展示|聊天模拟|口播推荐",
"visual_style":"二次元精致渲染|Q版可爱风|AI合成|实机录屏|实拍|混剪|游戏录屏|图文",
"target_audience":"受众","emotional_trigger":"情绪","ai_confidence":0.8,
"可复制做法":["技巧1","技巧2","技巧3"],
"总结":"为什么表现好/差?"}}
未识别写\"未识别\"。只输出JSON。"""

    try:
        # Get performance data for context
        conn = sqlite3.connect(str(ROOT / "data" / "mova_materials.sqlite3"))
        perf = conn.execute("SELECT spend, installs, cum_roi, retention, ctr FROM material_performance_0712 WHERE ad_name=?", (ad_name,)).fetchone()
        conn.close()
        perf_context = ""
        if perf:
            perf_context = f"\n表现数据: 花费${perf[0]:.0f} | 安装{perf[1]:.0f} | 累计ROI={perf[2]*100:.0f}% | 次留={perf[3]*100:.0f}% | CTR={perf[4]*100:.2f}%"

        user_msg = f"分析: {ad_name}\n类型: {media}{perf_context}"
        if local and local.exists():
            user_msg += f"\nOS文件: {local}"
        result = call_claude(prompt, [{"role": "user", "content": user_msg}])
        raw = result.strip()
        if raw.startswith("```"): raw = re.sub(r"^```\w*\n?|```$", "", raw).strip()
        analysis = json.loads(raw)
    except Exception as e:
        with _lock: _done += 1
        return f"FAIL Claude: {ad_name[:40]} - {str(e)[:50]}"

    # Save to SQLite
    mk = "mat_" + hashlib.sha256(ad_name.encode()).hexdigest()[:16]
    now = datetime.now(timezone.utc).isoformat()

    hook = analysis.get("前3秒钩子", {}) if isinstance(analysis.get("前3秒钩子"), dict) else {}
    core = analysis.get("核心卖点主体", {}) if isinstance(analysis.get("核心卖点主体"), dict) else {}
    av = analysis.get("视听语言包装", {}) if isinstance(analysis.get("视听语言包装"), dict) else {}
    cta = analysis.get("转化路径CTA", {}) if isinstance(analysis.get("转化路径CTA"), dict) else {}

    def _s(v):
        if isinstance(v, list): return ", ".join(str(x) for x in v)
        return str(v) if v else ""

    conn = sqlite3.connect(str(ROOT / "data" / "mova_materials.sqlite3"))
    conn.execute("INSERT OR IGNORE INTO material_registry (material_key,game_name,material_name,normalized_name,updated_at) VALUES (?,?,?,?,?)",
                 (mk, "心动防线", ad_name, na, now))
    conn.execute("""INSERT OR REPLACE INTO ai_analysis
        (material_key,material_name,game_name,asset_type,
         hook_content,hook_type,characters,core_selling_point,ui_elements,effects,colors,subtitles,rhythm,bgm,
         hook_strategy,character_elements,scene_elements,production_method,narrative_mode,visual_style,
         target_audience,emotional_trigger,marketing_strategy,optimization_direction,
         ai_confidence,analysis_time,label_status,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (mk, ad_name, "心动防线", media,
         json.dumps(hook, ensure_ascii=False), _s(hook.get("机制","")), _s(core.get("角色","")),
         _s(core.get("展示内容","")), _s(hook.get("画面","")), _s(av.get("色彩","")), _s(av.get("色彩","")),
         _s(hook.get("文案","")), _s(av.get("节奏","")), _s(av.get("BGM","")),
         _s(analysis.get("hook_strategy","")), _s(analysis.get("character_elements","")),
         _s(analysis.get("scene_elements","")), _s(analysis.get("production_method","")),
         _s(analysis.get("narrative_mode","")), _s(analysis.get("visual_style","")),
         _s(analysis.get("target_audience","")), _s(analysis.get("emotional_trigger","")),
         "", _s(analysis.get("可复制做法","")),
         analysis.get("ai_confidence", 0.8), now, "AI已分析", now))
    conn.commit()
    conn.close()

    elapsed = time.time() - t0
    with _lock: _done += 1
    return f"OK({elapsed:.0f}s): {ad_name[:40]} | {analysis.get('hook_strategy','?')}"

# Run 4 parallel
print(f"Starting 4 workers for {len(remaining)} materials...")
with ThreadPoolExecutor(max_workers=4) as ex:
    futures = {ex.submit(analyze_one, ad, ""): ad for ad, _oss in remaining}
    for i, f in enumerate(as_completed(futures)):
        result = f.result()
        notify(result)

elapsed = time.time() - _t0
print(f"\nDone! {_done}/{total} in {elapsed/60:.0f}min")
