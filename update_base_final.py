"""Update Base with all latest content. Rule: query fields first, then update."""
import os, json, requests, time, sqlite3, re
from collections import Counter, defaultdict
from pathlib import Path

# ── Auth ──
ROOT = Path(__file__).resolve().parent
for p in [ROOT / ".env", ".env"]:
    if not __import__("pathlib").Path(p).is_file(): continue
    for line in open(p, encoding="utf-8-sig"):
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            os.environ[k.strip()] = v.strip()

app_id = os.environ["FEISHU_APP_ID"]
app_secret = os.environ["FEISHU_APP_SECRET"]
r = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                   json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
TOKEN = r.json()["tenant_access_token"]
H = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
BASE = "[REDACTED]"

# ── Step 1: Query all table structures ──
print("=== Step 1: Query table structures ===")
table_fields = {}
for tid, tname in [("tbluk4Fi6DZNEM2E", "素材总览"),
                    ("tblHlBvHLetMv7oT", "聚类分析"),
                    ("tblHrhMuVTYlZT7H", "创意方向脑暴"),
                    ("tblu5lymhmtKWryJ", "视频脚本")]:
    r = requests.get(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE}/tables/{tid}/fields",
        headers=H, timeout=10)
    fields = {f["field_name"]: f["field_id"] for f in r.json()["data"]["items"]}
    table_fields[tname] = {"id": tid, "fields": fields}
    print(f"  {tname}: {len(fields)} fields")

# ── Step 2: Load SQLite data ──
print("\n=== Step 2: Load SQLite ===")
db = sqlite3.connect("data/mova_materials.sqlite3")
db.row_factory = sqlite3.Row

def norm(s):
    s = re.sub(r"\.[a-z0-9]{2,5}$", "", str(s).strip(), flags=re.I)
    s = re.sub(r"_[A-Z]{2}\d{2,4}$", "", s)
    s = re.sub(r"广告名称.*$", "", s)
    return re.sub(r"[_\-\s·.]+", "", s).lower()

ai = {}
for r in db.execute(
    "SELECT * FROM ai_analysis WHERE hook_strategy IS NOT NULL AND hook_strategy != '' AND hook_strategy != '...'"):
    ai[norm(r["material_name"])] = dict(r)

# Build materials list
materials = []
for r2 in db.execute("SELECT * FROM material_performance_0712"):
    ad = r2["ad_name"]
    a = None
    for an, aa in ai.items():
        if norm(ad) in an or an in norm(ad):
            a = aa
            break
    materials.append({
        "ad": ad, "spend": r2["spend"], "installs": r2["installs"],
        "cum_roi": r2["cum_roi"], "retention": r2["retention"], "ctr": r2["ctr"],
        "ai": a, "is_vid": ad.startswith("V") or ".mp4" in ad.lower(),
    })

n = len(materials)
valid = [m for m in materials if m["ai"]]
print(f"  {n} materials, {len(valid)} with valid AI tags")

# ── Step 3: Update 创意方向脑暴 (4 JP strategies) ──
print("\n=== Step 3: Update JP strategies ===")
T3 = table_fields["创意方向脑暴"]["id"]

# Get records with IDs
r = requests.get(
    f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE}/tables/{T3}/records",
    headers=H, params={"page_size": 50}, timeout=15)
records = {item["fields"].get("创意方向", ""): item["record_id"]
           for item in r.json()["data"]["items"]}

jp_updates = {
    "JP策略: 声优+限定感 组合拳": {
        "灵感来源聚类": "日本市场特化: Pangle/LINE Yahoo验证 + 声优厨文化 + 限定感驱动转化",
        "参考素材数": 11,
        "钩子建议": "有声优名大字幕+角色立绘+限定/期間限定文字闪烁+倒计时。声优推荐: [需确认CV人选, 参考学园偶像大师2024投放素材的三段式结构: 立绘-声优展示-玩法]",
        "制作建议": "口播质量>画面质量。AI配音用原生日语模型(CoeFont/VOICEVOX)。商务红线: 同一项目不可换声优对接窗口公司。0-3s声优名+角色特写, Pachinko式金属色粗体文字叠加",
        "风格建议": "#FF6B9D樱花粉+#FFD700限定金+#2A1B3D深紫背景, 文字密度高于欧美2-3倍, 参考LINE/Yahoo Japan游戏广告趋势",
        "日本市场适配": "声优厨是日本最核心转化人群, 素材中加声优名字CTR+30%。反面: 鸣潮首发生放送未请核心声优导致口碑崩盘。参考: Pangle日本4种创意方向 / Kaizen Ad日本本地化指南",
        "预期ROI提升": "CTR预期+30%, ROI预期+20%",
        "备注": "参考链接: https://www.pangleglobal.com/zh/resource/27799 | https://aixpost.com/creatives/game-ad-trends-japan/ | https://www.kaizen-ad.com/post/kaizen-ad-s-guide-to-japanese-localization-creative-market-trends",
    },
    "JP策略: 成り上がり(逆袭)+異世界転生 叙事模板": {
        "灵感来源聚类": "日本叙事特化: 40%日本手游广告使用逆袭/转生叙事 + 二选一互动hook(我们数据验证: 选择互动ROI=22.4%)",
        "参考素材数": 3,
        "钩子建议": "弱小キャラ→最強への道。0-3s被嘲笑/被欺负场景建立共情。魔王/ドラゴン作为Boss不需要解释。二选一互动问题: 选择角色/阵容/技能",
        "制作建议": "K帧动画或AI动漫风格, 日本玩家对纸芝居(幻灯片式)广告接受度高于欧美, 不需要全动画。BGM: 暗色开场→电子摇滚过渡→金色升调收尾, BPM 60→160",
        "风格建议": "#1A1A19暗色(失败)→#4A3AA7蓝紫(成长)→#EDA100金色(胜利)。Pachinko式排版(粗体金属色大字多层叠加), 不要用欧美简约风",
        "日本市场适配": "異世界+成り上がり是日本最成熟叙事模板。最弱から最強へ是万能标题。日本玩家天然理解魔王/龙Boss设定。参考LINE/Yahoo Japan游戏广告趋势",
        "预期ROI提升": "ROI预期+15-25%",
        "备注": "参考: https://aixpost.com/creatives/game-ad-trends-japan/ | 我们的数据: 选择互动ROI=22.4%(3个素材验证)",
    },
    "JP策略: 季节性投放日历 + 限定皮肤预告": {
        "灵感来源聚类": "日本运营策略: 四大节日节点 ROI高40-60%, Pangle APAC季节性投放数据验证",
        "参考素材数": 2,
        "钩子建议": "季节限定(正月/夏休み/ハロウィン/クリスマス)+限定キャラ登場+倒计时。7月底: 立即准备夏休み素材!",
        "制作建议": "提前2周制作季节性素材, 节日当天替换投放。保留通用版A/B测试。四季BGM: 正月和风电子+太鼓 / 夏J-Pop / 秋暗黑电子 / 冬铃铛弦乐。建立模板库, 每次换皮+换角色快速产出",
        "风格建议": "正月#C41E3A红金 / 夏#2A78D6蓝白 / 秋#EB6834橙棕 / 冬#FCFCFB白+#3987E5冰蓝。节日限定换装(和服/浴衣/圣诞装/万圣装)搭配SSR标识",
        "日本市场适配": "投放节奏: 1月正月(最大)、4-5月GW黄金周、7-8月夏休み(第二高峰, 立即准备!)、10月ハロウィン、12月クリスマス。每年至少踩4个节点",
        "预期ROI提升": "季节性素材CTR比普通素材高40-60%",
        "备注": "参考: https://www.pangleglobal.com/zh/resource/614d90353977d9fae12a07ac | 现在7月底, 夏休み素材立即准备!",
    },
    "JP策略: ソーシャルプルーフ(社交证明)+下载里程碑": {
        "灵感来源聚类": "日本信任策略: 社交证明驱动下载决策 + App Store排名>广告文案 + 广大大2025手游营销报告",
        "参考素材数": 0,
        "钩子建议": "XX万人突破! / App Store 1位獲得! / 有名YouTuber実況中! 真实UI截图>精美渲染图。素人感(真实玩家感)口播",
        "制作建议": "真实UI截图+玩家评论引用(日语)。素人配音>专业配音。BGM: 轻量J-Pop+真实UI音效。日本玩家对サクラ极其敏感, 必须用真实数据",
        "风格建议": "#FCFCFB白底+#2A78D6蓝CTA, 简洁真实感, 减少过度包装。App Store排名徽章+下载量数字大字展示",
        "日本市场适配": "下载决策高度依赖社交证明。App Store排名>任何广告文案。YouTuber推荐=最强背书。需要真实数据支撑, 数据不足时先准备模板",
        "预期ROI提升": "CTR+15-20%, CVR+10-15%",
        "备注": "参考: https://www.sohu.com/a/950861711_121840918 | 需要真实下载量/排名数据。心动日服如有App Store排名必须展示。目前数据不足但方向值得准备",
    },
}

updated = 0
for name, fields in jp_updates.items():
    rid = records.get(name)
    if not rid:
        print(f"  NOT FOUND: {name}")
        continue
    r = requests.put(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE}/tables/{T3}/records/{rid}",
        headers=H, json={"fields": fields}, timeout=10)
    ok = r.json().get("code") == 0
    print(f"  {'OK' if ok else 'FAIL: '+r.json().get('msg','?')} | {name[:40]}")
    if ok: updated += 1
    time.sleep(0.1)

print(f"  Updated: {updated}/{len(jp_updates)}")

# ── Step 4: Rebuild 聚类分析 ──
print("\n=== Step 4: Rebuild 聚类分析 ===")
T2 = table_fields["聚类分析"]["id"]
f2 = table_fields["聚类分析"]["fields"]

# Clear old
r = requests.get(f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE}/tables/{T2}/records",
                 headers=H, params={"page_size": 200}, timeout=15)
for item in r.json()["data"]["items"]:
    requests.delete(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE}/tables/{T2}/records/{item['record_id']}",
        headers=H, timeout=10)
    time.sleep(0.03)
print(f"  Cleared old clusters")

# Recompute clusters
clusters = defaultdict(list)
for m in valid:
    hook = (m["ai"].get("hook_strategy", "") or "").strip()
    style = (m["ai"].get("visual_style", "") or "").strip()
    key = hook + " + " + style if style else hook
    clusters[key].append(m)

cluster_pushed = 0
for name, ms in clusters.items():
    if len(ms) < 2: continue
    cn = len(ms)
    hooks = Counter((m["ai"].get("hook_strategy", "") or "").strip() for m in ms)
    prods = Counter((m["ai"].get("production_method", "") or "").strip() for m in ms
                    if (m["ai"].get("production_method", "") or "").strip())
    styles = Counter((m["ai"].get("visual_style", "") or "").strip() for m in ms
                     if (m["ai"].get("visual_style", "") or "").strip())

    fields = {
        "聚类名称": name[:100],
        "素材数量": cn,
        "平均累计ROI": round(sum(m["cum_roi"] for m in ms) / cn * 100, 1),
        "平均次留": round(sum(m["retention"] for m in ms) / cn * 100, 1),
        "平均花费": round(sum(m["spend"] for m in ms) / cn, 0),
        "平均安装": round(sum(m["installs"] for m in ms) / cn, 0),
        "共同特征": f"钩子:{hooks.most_common(1)[0][0]} | 制作:{prods.most_common(1)[0][0] if prods else '?'} | 风格:{styles.most_common(1)[0][0] if styles else '?'}",
        "钩子策略": hooks.most_common(1)[0][0][:100],
        "制作方式": (prods.most_common(1)[0][0][:100] if prods else ""),
        "画面风格": (styles.most_common(1)[0][0][:100] if styles else ""),
    }

    r = requests.post(
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE}/tables/{T2}/records",
        headers=H, json={"fields": fields}, timeout=10)
    if r.json().get("code") == 0:
        cluster_pushed += 1
    time.sleep(0.06)

print(f"  Pushed {cluster_pushed} clusters")

# ── Step 5: Summary ──
total_spend = sum(m["spend"] for m in materials)
total_inst = sum(m["installs"] for m in materials)
avg_roi = round(sum(m["cum_roi"] for m in valid) / len(valid) * 100, 1) if valid else 0
avg_ret = round(sum(m["retention"] for m in valid) / len(valid) * 100, 1) if valid else 0

print(f"\n=== Done ===")
print(f"  素材总览: {n} records (existing)")
print(f"  聚类分析: {cluster_pushed} clusters (rebuilt)")
print(f"  创意脑暴: {updated} JP strategies updated")
print(f"  KPI: {len(valid)}素材 | ${total_spend:,.0f} | {total_inst:.0f}安装 | ROI={avg_roi}% | 次留={avg_ret}%")
print(f"  https://dreametech.feishu.cn/base/{BASE}")

db.close()
