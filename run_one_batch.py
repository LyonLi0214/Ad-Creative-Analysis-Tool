# -*- coding: utf-8 -*-
"""Run one batch of materials through Claude analysis."""
import hashlib, json, os, re, sqlite3, sys, time, random
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from feishu_bot.claude_adapter import call_claude

batch_id = int(sys.argv[1])
materials = json.loads((ROOT / f"data/analysis_batch_{batch_id}.json").read_text(encoding="utf-8"))
print(f"Batch {batch_id}: {len(materials)} materials", flush=True)

PROMPT = """你是游戏广告素材分析专家。分析日本市场素材,游戏名「心动防线」(弹射RPG,日服)。

素材文件名已编码关键信息。请结合命名规律和游戏知识进行专业拆解。
命名规律: V=视频 P=图片 | 心动=游戏 | 扒改/AI/实机/口播/迭代=制作方式 | 角色/战斗/玩法/剧情/福利/选择=内容类型 | 黛梦/特蕾西亚/菲迪/婕拉/阿星=角色名 | Q版/日系/像素/二次元=风格 | 9x16=竖版 1x1=方版 16x9=横版 | HSM/ZQ/WHY/MYY/XM/WYX=制作团队 | 无贴片=无品牌遮盖 | 别选=反常识hook | 倒计时=紧迫感

输出JSON(不要markdown, 不要```):
{"前3秒钩子":{"画面":"钩子画面描述","文案":"屏幕文字","音效":"音频描述","机制":"抓注意力方式"},
"核心卖点主体":{"展示内容":"主体内容","角色":"出现的角色","玩法":"展示的玩法","差异化":"与同类素材的差异"},
"视听语言包装":{"画面风格":"视觉风格","BGM":"背景音乐","节奏":"剪辑节奏","色彩":"色彩主调"},
"转化路径CTA":{"类型":"CTA类型","文案":"CTA文案","位置":"出现位置","紧迫感":"紧迫感设计"},
"hook_strategy":"反常识标题|教学攻略|福利展示|擦边角色吸引|剧情抓马|口播推荐|问题型标题|选择互动|福利激励|核心玩法展示|来电钩子|定制互动|噱头爆点",
"character_elements":"黑皮|猫耳兽耳|Q版角色|秘书职场|多角色|立绘展示|真人出镜|AI虚拟人|无角色",
"scene_elements":"弹球战斗|选择界面|养成界面|Boss战|胜利结算|福利界面|模拟UI|直播间",
"production_method":"K帧动画|扒改|真人口播|AI口播|实机录屏|图片轮播|混合",
"narrative_mode":"问题解法|反差逆袭|攻略教学|角色推荐|悬念揭秘|福利直给|选项驱动|定制互动|擦边吸引|角色展示|聊天模拟|口播推荐",
"visual_style":"二次元精致渲染|Q版可爱风|AI合成|实机录屏|实拍|混剪|游戏录屏|图文",
"target_audience":"目标受众描述","emotional_trigger":"情绪触发描述","ai_confidence":0.8,
"可复制做法":["具体可复制的技巧1","技巧2","技巧3"],
"总结":"为什么这个素材表现好/差?一句话核心原因"}
图片素材BGM/音效写图片素材无音频。未识别到的写未识别。每个维度要具体描述，不只选标签。只输出JSON。"""

def norm(s):
    s = re.sub(r"\.[a-z0-9]{2,5}$", "", s.strip(), flags=re.I)
    s = re.sub(r"_[A-Z]{2}\d{2,4}$", "", s)
    s = re.sub(r"广告名称.*$", "", s)
    return re.sub(r"[_\-\s·.]+", "", s).lower()

def _s(v):
    if isinstance(v, list): return ", ".join(str(x) for x in v)
    return str(v) if v else ""

def safe_write(mk, ad_name, media, analysis, na, now):
    hook = analysis.get("前3秒钩子", {}) if isinstance(analysis.get("前3秒钩子"), dict) else {}
    core = analysis.get("核心卖点主体", {}) if isinstance(analysis.get("核心卖点主体"), dict) else {}
    av = analysis.get("视听语言包装", {}) if isinstance(analysis.get("视听语言包装"), dict) else {}
    cta = analysis.get("转化路径CTA", {}) if isinstance(analysis.get("转化路径CTA"), dict) else {}

    for attempt in range(5):
        try:
            conn = sqlite3.connect(str(ROOT / "data" / "mova_materials.sqlite3"))
            conn.execute("INSERT OR IGNORE INTO material_registry (material_key,game_name,material_name,normalized_name,updated_at) VALUES (?,?,?,?,?)",
                         (mk, "心动防线", ad_name, na, now))
            conn.execute("""INSERT OR REPLACE INTO ai_analysis
                (material_key,material_name,game_name,asset_type,
                 hook_content,hook_type,characters,core_selling_point,
                 ui_elements,effects,colors,subtitles,rhythm,bgm,
                 hook_strategy,character_elements,scene_elements,
                 production_method,narrative_mode,visual_style,
                 target_audience,emotional_trigger,marketing_strategy,optimization_direction,
                 ai_confidence,analysis_time,label_status,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (mk, ad_name, "心动防线", media,
                 json.dumps(hook, ensure_ascii=False), _s(hook.get("机制","")),
                 _s(core.get("角色","")), _s(core.get("展示内容","")),
                 _s(hook.get("画面","")), _s(av.get("色彩","")), _s(av.get("色彩","")),
                 _s(hook.get("文案","")), _s(av.get("节奏","")), _s(av.get("BGM","")),
                 _s(analysis.get("hook_strategy","")), _s(analysis.get("character_elements","")),
                 _s(analysis.get("scene_elements","")), _s(analysis.get("production_method","")),
                 _s(analysis.get("narrative_mode","")), _s(analysis.get("visual_style","")),
                 _s(analysis.get("target_audience","")), _s(analysis.get("emotional_trigger","")),
                 _s(cta.get("文案","")), _s(analysis.get("可复制做法","")),
                 analysis.get("ai_confidence", 0.8), now, "AI已分析", now))
            conn.commit()
            conn.close()
            return True
        except sqlite3.OperationalError:
            time.sleep(0.3 * (attempt + 1))
    return False

done = 0
for ad_name in materials:
    media = "video" if ad_name.startswith("V") or ".mp4" in ad_name.lower() else "image"
    t0 = time.time()

    conn = sqlite3.connect(str(ROOT / "data" / "mova_materials.sqlite3"))
    p = conn.execute("SELECT spend, installs, cum_roi, retention, ctr FROM material_performance_0712 WHERE ad_name=?", (ad_name,)).fetchone()
    conn.close()
    perf = f"花费${p[0]:.0f} 安装{p[1]:.0f} ROI={p[2]*100:.0f}% 次留={p[3]*100:.0f}% CTR={p[4]*100:.2f}%" if p else ""

    try:
        result = call_claude(PROMPT, [{"role": "user", "content": f"素材: {ad_name}\n类型: {media}\n{perf}"}])
        raw = result.strip()
        if raw.startswith("```"): raw = re.sub(r"^```\w*\n?|```$", "", raw).strip()
        analysis = json.loads(raw)
    except Exception as e:
        print(f"  FAIL[{batch_id}]: {ad_name[:40]} - {str(e)[:60]}", flush=True)
        continue

    mk = "mat_" + hashlib.sha256(ad_name.encode()).hexdigest()[:16]
    now = datetime.now(timezone.utc).isoformat()
    if safe_write(mk, ad_name, media, analysis, norm(ad_name), now):
        done += 1
        t = time.time() - t0
        hs = analysis.get('hook_strategy','?')
        vs = analysis.get('visual_style','?')
        summary = analysis.get('总结','?')[:50]
        print(f"  [{batch_id}] {done}/{len(materials)} OK({t:.0f}s): {ad_name[:40]}", flush=True)

print(f"Batch {batch_id} DONE: {done}/{len(materials)}", flush=True)
