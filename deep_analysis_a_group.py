"""A组12条高ROI素材深度四维拆解（只拆未拆的7条）"""
import hashlib, json, os, re, sqlite3, sys, time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from feishu_bot.claude_adapter import call_claude
import oss2

oss_files = json.loads((ROOT / "data/oss_index.json").read_text(encoding="utf-8"))
auth = oss2.Auth("[REDACTED]", "[REDACTED]")
bucket = oss2.Bucket(auth, "https://oss-cn-shanghai.aliyuncs.com", "mova-eu-game-uxd")
DL = ROOT / "downloads" / "batch_analysis"
DL.mkdir(parents=True, exist_ok=True)

def _norm(s):
    s = re.sub(r"\.[a-z0-9]{2,5}$", "", str(s).strip(), flags=re.I)
    s = re.sub(r"_[A-Z]{2}\d{2,4}$", "", s)
    s = re.sub(r"广告名称.*$", "", s)
    return re.sub(r"[_\-\s·.]+", "", s).lower()

PROMPT = """你是游戏广告素材分析专家。深度拆解日本市场素材「心动防线」(弹射RPG,日服)。

输出JSON(不要markdown, 不要```):
{
"前3秒钩子": {"画面":"逐帧描述","文案":"屏幕文字","音效":"音频描述","机制":"抓注意力方式"},
"核心卖点主体": {"展示内容":"主体展示什么","角色":"出现角色","玩法":"玩法元素","差异化":"差异点"},
"视听语言包装": {"画面风格":"视觉风格","BGM":"背景音乐","节奏":"剪辑节奏","色彩":"色彩主调"},
"转化路径CTA": {"类型":"CTA类型","文案":"具体文案","位置":"出现位置","紧迫感":"紧迫感设计"},
"标签": {
"hook_strategy":"反常识标题|教学攻略|福利展示|擦边角色吸引|剧情抓马|口播推荐|问题型标题|选择互动|福利激励|核心玩法展示|来电钩子|定制互动|噱头爆点",
"character_elements":"黑皮|猫耳兽耳|Q版角色|秘书职场|多角色|立绘展示|真人出镜|AI虚拟人|无角色",
"scene_elements":"弹球战斗|选择界面|养成界面|Boss战|胜利结算|福利界面|模拟UI|直播间",
"production_method":"K帧动画|扒改|真人口播|AI口播|实机录屏|图片轮播|混合",
"narrative_mode":"问题解法|反差逆袭|攻略教学|角色推荐|悬念揭秘|福利直给|选项驱动|定制互动|擦边吸引|角色展示|聊天模拟|口播推荐",
"visual_style":"二次元精致渲染|Q版可爱风|AI合成|实机录屏|实拍|混剪|游戏录屏|图文"
},
"总结":"为什么高ROI/高留存? 一句话核心原因",
"可复制做法":["技巧1","技巧2","技巧3"]
}
图片素材BGM/音效写"图片素材无音频"。每个字段具体描述，不只选标签。只输出JSON。"""

TODO = [
    "P_心动_20260109_日_人物选择_1x1_ZQ_JP",
    "V0005_心动_20260707_日_无贴片竖版日系黛梦_9x16_HSM_JP.mp4_JP0711",
    "V0005_心动_20260617_日_迭代日系黛梦_9x16_HSM_JP.mp4_0623",
    "P0231_心动_20260604_扒改_Q版本立绘+战斗_1x1_MYY_JP.mp4",
    "P0340_心动_20260623_日_升级_1x1_ZQ_JP",
    "V0278_心动_20260624_美女开头按摩+反弹球口播_9x16_MYY_JP.mp4_广告名称2026-06-20 11:14:08",
    "V0268_心动_20260616_黛梦动漫变强选我_9x16_MYY_JP.mp4_广告名称2026-06-20 11:14:08",
]

done = 0
for i, ad in enumerate(TODO):
    media = "video" if ".mp4" in ad.lower() else "image"
    t0 = time.time()
    print(f"[{i+1}/7] {media}: {ad[:50]}...")

    tn = _norm(ad)
    td = set(re.findall(r"\d{8}", ad))
    best, best_score = None, 0
    for f in oss_files:
        score = 0
        fn = _norm(f["name"])
        fd = set(re.findall(r"\d{8}", f["name"]))
        if tn in fn or fn in tn: score += 50
        if td & fd: score += 40
        if score > best_score: best_score, best = score, f

    if not best or best_score < 40:
        print(f"  SKIP: OSS未匹配 (score={best_score})")
        continue

    local = DL / best["name"]
    if not local.exists():
        bucket.get_object_to_file(best["key"], str(local))

    try:
        result = call_claude(PROMPT, [{"role": "user", "content": f"素材: {ad}\n文件: {local}\n类型: {media}"}])
        raw = result.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```\w*\n?|```$", "", raw).strip()
        analysis = json.loads(raw)
    except Exception as e:
        print(f"  FAIL: {str(e)[:60]}")
        continue

    mk = "mat_" + hashlib.sha256(ad.encode()).hexdigest()[:16]
    now = datetime.now(timezone.utc).isoformat()
    tags = analysis.get("标签", {})

    conn = sqlite3.connect(str(ROOT / "data" / "mova_materials.sqlite3"))
    conn.execute(
        "INSERT OR IGNORE INTO material_registry (material_key,game_name,material_name,normalized_name,updated_at) VALUES (?,?,?,?,?)",
        (mk, "心动防线", ad, _norm(ad), now))
    conn.execute(
        """INSERT OR REPLACE INTO ai_analysis
        (material_key,material_name,game_name,asset_type,
         hook_content,hook_type,characters,core_selling_point,
         hook_strategy,character_elements,scene_elements,production_method,narrative_mode,visual_style,
         target_audience,emotional_trigger,ai_confidence,analysis_time,label_status,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (mk, ad, "心动防线", media,
         json.dumps(analysis.get("前3秒钩子", ""), ensure_ascii=False),
         tags.get("hook_strategy", ""),
         json.dumps(analysis.get("核心卖点主体", ""), ensure_ascii=False),
         analysis.get("总结", ""),
         tags.get("hook_strategy", ""), tags.get("character_elements", ""),
         tags.get("scene_elements", ""), tags.get("production_method", ""),
         tags.get("narrative_mode", ""), tags.get("visual_style", ""),
         "", "", 0.8, now, "AI已分析", now))
    conn.commit()
    conn.close()

    elapsed = time.time() - t0
    summary = analysis.get("总结", "?")[:80]
    copies = analysis.get("可复制做法", [])
    print(f"  OK({elapsed:.0f}s): {summary}")
    if copies:
        for c in copies[:3]:
            print(f"    -> {c}")
    done += 1

print(f"\nDone: {done}/{len(TODO)}")
