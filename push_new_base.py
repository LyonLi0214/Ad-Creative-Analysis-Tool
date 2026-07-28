import os
"""Push all data to new Base Dgu4bHvXvaVfDXsw6jBc1FUNnTg"""
import sqlite3, os, json, requests, time, re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
for p in [ROOT / ".env", ".env"]:
    if not __import__("pathlib").Path(p).is_file(): continue
    for line in open(p, encoding="utf-8-sig"):
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ[k.strip()] = v.strip()

app_id = os.environ["FEISHU_APP_ID"]
app_secret = os.environ["FEISHU_APP_SECRET"]
r = requests.post(
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
token = r.json()["tenant_access_token"]
h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
BASE = "Dgu4bHvXvaVfDXsw6jBc1FUNnTg"
T1 = "tblNCSIZh1DQD7VQ"
T2 = "tblnBnvdtqMPB9UZ"
T3 = "tblYXqdEBQXubgMO"
T4 = "tbl0cUouVmOzzcAB"

# Fix select field - delete multi-select, create single-select
rf = requests.get(f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE}/tables/{T1}/fields",
                   headers=h, timeout=10)
for f in rf.json()["data"]["items"]:
    if f["field_name"] == "类型":
        requests.delete(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE}/tables/{T1}/fields/{f['field_id']}",
            headers=h, timeout=10)
        print(f"Deleted multi-select field: {f['field_id']}")

requests.post(
    f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE}/tables/{T1}/fields",
    headers=h,
    json={"field_name": "类型", "type": 3,
          "options": [{"name": "视频", "color": 0}, {"name": "图片", "color": 1}]},
    timeout=10)
print("Created single-select field")

# Load data
def norm(s):
    s = re.sub(r"\.[a-z0-9]{2,5}$", "", str(s).strip(), flags=re.I)
    s = re.sub(r"_[A-Z]{2}\d{2,4}$", "", s)
    s = re.sub(r"广告名称.*$", "", s)
    return re.sub(r"[_\-\s·.]+", "", s).lower()

db = sqlite3.connect("data/mova_materials.sqlite3")
db.row_factory = sqlite3.Row

ai = {}
for r in db.execute(
    "SELECT * FROM ai_analysis WHERE hook_strategy IS NOT NULL AND hook_strategy != '' AND hook_strategy != '...'"):
    ai[norm(r["material_name"])] = dict(r)

# Push 素材总览
pushed = 0
for r2 in db.execute("SELECT * FROM material_performance_0712"):
    ad = r2["ad_name"]
    na = norm(ad)
    a = None
    for an, aa in ai.items():
        if na in an or an in na:
            a = aa
            break

    is_vid = ad.startswith("V") or ".mp4" in ad.lower()
    fields = {
        "素材名称": ad[:300],
        "类型": "视频" if is_vid else "图片",
        "花费USD": round(r2["spend"], 2),
        "安装": int(r2["installs"]),
        "累计ROI": round(r2["cum_roi"] * 100, 2),
        "次留": round(r2["retention"] * 100, 2),
        "CTR": round(r2["ctr"] * 100, 2),
    }
    if a:
        def ss(v):
            if isinstance(v, list): return ", ".join(str(x) for x in v)
            return str(v)[:500] if v else ""

        fields.update({
            "钩子策略": ss(a.get("hook_strategy", "")),
            "角色元素": ss(a.get("character_elements", "")),
            "场景元素": ss(a.get("scene_elements", "")),
            "制作方式": ss(a.get("production_method", "")),
            "叙事模式": ss(a.get("narrative_mode", "")),
            "画面风格": ss(a.get("visual_style", "")),
            "目标受众": ss(a.get("target_audience", "")),
            "情绪触发": ss(a.get("emotional_trigger", "")),
            "AI置信度": a.get("ai_confidence") or 0.8,
        })

    r = requests.post(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE}/tables/{T1}/records",
        headers=h, json={"fields": fields}, timeout=10)
    if r.json().get("code") == 0:
        pushed += 1
    elif pushed == 0:
        print(f"ERR: {r.json().get('msg')}")
    if pushed % 50 == 0:
        print(f"{pushed}/136")
    time.sleep(0.04)

print(f"素材总览: {pushed} records")

# Push 聚类分析
valid = []
for r2 in db.execute("SELECT * FROM material_performance_0712"):
    ad = r2["ad_name"]
    a = None
    for an, aa in ai.items():
        if norm(ad) in an or an in norm(ad):
            a = aa
            break
    if a and (a.get("hook_strategy") or "").strip():
        valid.append({"ad": ad, "ai": a, "spend": r2["spend"], "installs": r2["installs"],
                       "cum_roi": r2["cum_roi"], "retention": r2["retention"]})

clusters = defaultdict(list)
for m in valid:
    hook = (m["ai"].get("hook_strategy", "") or "").strip()
    style = (m["ai"].get("visual_style", "") or "").strip()
    key = hook + " + " + style if style else hook
    clusters[key].append(m)

c_pushed = 0
for name, ms in clusters.items():
    if len(ms) < 2: continue
    n = len(ms)
    hooks = Counter((m["ai"].get("hook_strategy", "") or "").strip() for m in ms)
    prods = Counter((m["ai"].get("production_method", "") or "").strip() for m in ms
                    if (m["ai"].get("production_method", "") or "").strip())
    fields = {
        "聚类名称": name[:100], "素材数量": n,
        "平均累计ROI": round(sum(m["cum_roi"] for m in ms) / n * 100, 1),
        "平均次留": round(sum(m["retention"] for m in ms) / n * 100, 1),
        "平均花费": round(sum(m["spend"] for m in ms) / n, 0),
        "平均安装": round(sum(m["installs"] for m in ms) / n, 0),
        "钩子策略": hooks.most_common(1)[0][0][:100],
        "制作方式": prods.most_common(1)[0][0][:100] if prods else "",
    }
    r = requests.post(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE}/tables/{T2}/records",
        headers=h, json={"fields": fields}, timeout=10)
    if r.json().get("code") == 0: c_pushed += 1
    time.sleep(0.04)
print(f"聚类分析: {c_pushed} records")

# Push 创意方向
ideas = [
    {"创意方向": "Q版角色 + 核心玩法展示", "优先级": "P0",
     "灵感来源": "ROI最高聚类(39.6%, 9个素材验证)", "参考素材数": 9,
     "钩子建议": "Q版像素角色+SSR标识闪烁+福利文案(登录送SSR)",
     "BGM推荐": "J-Pop电子摇滚, 萝莉音, 鼓点第1秒进入, 18秒短时长",
     "色彩推荐": "#EDA100暖黄 + #1BAF7A绿色 + #2A78D6粉蓝, 高饱和明亮",
     "制作建议": "扒改+混剪, 18秒, 前3秒SSR标识闪烁+福利冲击",
     "日本市场适配": "Q版在日本接受度极高, 强调收集养成, 参考V0057(ROI=259%)",
     "预期ROI": "40-80%"},
    {"创意方向": "日系角色 + 擦边吸引开头", "优先级": "P0",
     "灵感来源": "ROI第二聚类(26.1%, 9个素材)", "参考素材数": 9,
     "钩子建议": "日系御姐角色脸部特写+轻微动画+反常识字幕",
     "BGM推荐": "日系电子摇滚+弦乐, 御姐音口播, 0s启动鼓点",
     "色彩推荐": "#4A3AA7蓝紫 + 红黑 + 金色SSR, 暗色背景突出角色",
     "制作建议": "实机录屏+UI混剪, 22-26秒, Boss战作为中后段高潮节点",
     "日本市场适配": "日系御姐是二次元玩家最强吸引力, 声优品牌忠诚度极高",
     "预期ROI": "30-60%"},
    {"创意方向": "反常识/别选反弹球 + 阵容搭配", "优先级": "P1",
     "灵感来源": "选择互动+二次元(22.4%, 3版验证)", "参考素材数": 3,
     "钩子建议": "真人手持弹球+白墙+反常识字幕(千万别选反弹球!)",
     "BGM推荐": "电子摇滚打击感BGM, 0s启动, 悬疑铺垫+爽感高潮",
     "色彩推荐": "白色(纯色Hook背景)+绿色弹球+蓝紫技能+金色结算",
     "制作建议": "真人口播+实机录屏混剪, 26秒, 真人5秒内转游戏画面",
     "日本市场适配": "日本玩家对竞技/策略选择有天然兴趣, 选错惩罚制造悬念",
     "预期ROI": "35-70%"},
    {"创意方向": "JP策略: 声优+限定感组合拳", "优先级": "P1",
     "灵感来源": "日本市场特化策略", "参考素材数": 0,
     "钩子建议": "有名声优口播+限定/期間限定文字闪烁, 倒计时紧迫感",
     "BGM推荐": "日本声优原声(御姐/萝莉双声道), 限定感音效(钟声/警报)",
     "色彩推荐": "限定金色 #EDA100 + 红色警示 #E34948, 倒计时数字闪烁",
     "制作建议": "声优名字大字展示, 限定皮肤+限定活動, 季节性投放日历",
     "日本市场适配": "声优品牌>角色颜值, 限定感驱动转化, 夏休み立即准备",
     "预期ROI": "CTR预期+30%, ROI预期+20%"},
    {"创意方向": "JP策略: 成り上がり逆袭叙事", "优先级": "P1",
     "灵感来源": "日本叙事模板(40%日本广告在用)", "参考素材数": 0,
     "钩子建议": "角色被嘲笑-变强逆袭-打脸回击, 3秒内建立冲突",
     "BGM推荐": "暗色系开场→电子摇滚过渡→金色升调收尾, 配合情绪变化",
     "色彩推荐": "暗色#1A1A19开场→过渡蓝紫#4A3AA7→金色#EDA100胜利",
     "制作建议": "K帧动画或AI生成+配音, 15秒内完成逆袭弧线",
     "日本市场适配": "異世界+成り上がり是日本最成熟叙事, 魔王/龙Boss不需要解释",
     "预期ROI": "ROI预期+15-25%"},
]
for idea in ideas:
    r = requests.post(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE}/tables/{T3}/records",
        headers=h, json={"fields": idea}, timeout=10)
    time.sleep(0.04)
print(f"创意方向: {len(ideas)} records")

# Push 视频脚本
scripts = [
    {"脚本名称": "脚本1: 黛梦逆袭变强 22s 9x16", "对应方向": "日系角色+擦边吸引",
     "总时长": "22秒", "BGM": "日系电子摇滚, 鼓点0s进入, 御姐音口播, 0-8s铺垫→8-18s打击感强化→18s收束",
     "色彩": "蓝紫#4A3AA7(角色)+红黑(战斗)+金色SSR#EDA100, 全程高饱和",
     "时间轴": "0-3s角色特写 / 3-8s选择+Q版切换 / 8-18s战斗高潮+Boss / 18-22s胜利结算+CTA",
     "画面内容": "黛梦脸部特写→SSR三选一卡牌→弹球战斗+满屏伤害+Boss→MVP结算+角色返场+下载CTA",
     "文案字幕": "0-2s无字幕 / 3-8s选择角色! / 8-18s无 / 18-22s 胜利! 立即下载",
     "音效配音": "御姐音口播+UI点击音+攻击弹射+技能爆炸+Boss警报+胜利升调+金币掉落",
     "目的": "角色颜值拉住→选择互动参与→战斗高潮持续→胜利结算引导下载",
     "可借鉴点": "角色立绘+轻微动画降成本/SSR闪烁强化稀有度/三选一降跳出/Boss节点高潮/角色返场强化记忆"},
    {"脚本名称": "脚本2: Q版菲迪福利 18s 9x16", "对应方向": "Q版角色+福利激励",
     "总时长": "18秒", "BGM": "J-Pop轻快, 萝莉音, 鼓点0s启动, 全程明亮, 10-18s升调",
     "色彩": "暖黄#EDA100+绿色#1BAF7A+粉蓝#2A78D6, SSR金色, 高饱和明亮",
     "时间轴": "0-3s福利冲击 / 3-10s实机战斗+技能树 / 10-18s结算+抽卡动画+CTA",
     "画面内容": "Q版菲迪+SSR闪烁+登录送SSR字幕→弹球战斗+技能树三选一+升级→Boss击败+抽卡动画+免费下载CTA",
     "文案字幕": "0-3s 登录即得SSR! / 3-10s 技能选择 / 10-18s 每日报酬! 免费下载",
     "音效配音": "萝莉音口播+弹射攻击+技能释放+升级叮叮叮+抽卡翻牌闪光+奖励掉落",
     "目的": "福利冲击最大化CTR→快速切入玩法证明品质→抽卡动画提升下载转化",
     "可借鉴点": "Q版降成本+日本高接受度/登录送SSR打消下载顾虑/技能树展示深度/抽卡动画满足收集欲/18秒适合信息流"},
    {"脚本名称": "脚本3: 别选反弹球 26s 9x16", "对应方向": "反常识+阵容搭配",
     "总时长": "26秒", "BGM": "电子摇滚+弦乐, 0s鼓点, 0-6s悬疑→6-20s打击感→20s升调收束",
     "色彩": "白色#FCFCFB(Hook背景)+绿色弹球+蓝紫#4A3AA7技能+金色#EDA100结算",
     "时间轴": "0-2s反常识Hook / 2-6s阵容选择 / 6-20s战斗高潮 / 20-26s结算+角色全景",
     "画面内容": "真人手持弹球+白墙+别选反弹球!→5人阵容搭配→小怪潮+技能连放+Boss登场+满屏伤害→Boss反杀+胜利结算+5人全景+CTA",
     "文案字幕": "0-2s 千万别选反弹球! / 2-6s 选择角色! / 20-26s 胜利! 立即挑战!",
     "音效配音": "御姐音口播(语气强烈)+UI点击+弹射攻击+爆炸+击杀+Boss警报+胜利升调+金币掉落",
     "目的": "反常识Hook最大冲击→阵容展示策略深度→战斗高潮证明爽感→全景展示团队价值+引导挑战欲",
     "可借鉴点": "反常识Hook日本验证强/手+白墙+弹球极低成本/5人阵容突出养成/挑战欲CTA优于下载指令"},
]
for s in scripts:
    r = requests.post(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE}/tables/{T4}/records",
        headers=h, json={"fields": s}, timeout=10)
    time.sleep(0.04)
print(f"视频脚本: {len(scripts)} records")

# Verify
for tid, name in [(T1, "素材总览"), (T2, "聚类分析"), (T3, "创意方向"), (T4, "视频脚本")]:
    r = requests.get(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE}/tables/{tid}/records",
        headers=h, params={"page_size": 1}, timeout=10)
    print(f"{name}: {r.json()['data']['total']} records")

db.close()
print(f"\nhttps://dreametech.feishu.cn/base/{BASE}")
