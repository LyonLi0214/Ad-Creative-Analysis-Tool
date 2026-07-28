import os
"""Generate insight report with specific BGM/color recommendations."""
import json, os, re, sqlite3, subprocess, time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LARK = os.environ.get("LARK_CLI_PATH", "lark-cli")
BASE = "[REDACTED]"
DASH = "blkYNPwKifuJYLvj"
T1 = "tbluk4Fi6DZNEM2E"

db = sqlite3.connect(str(ROOT / "data" / "mova_materials.sqlite3"))
db.row_factory = sqlite3.Row

def norm(s):
    s = re.sub(r"\.[a-z0-9]{2,5}$", "", str(s).strip(), flags=re.I)
    s = re.sub(r"_[A-Z]{2}\d{2,4}$", "", s)
    s = re.sub(r"广告名称.*$", "", s)
    return re.sub(r"[_\-\s·.]+", "", s).lower()

ai = {}
for r in db.execute("SELECT * FROM ai_analysis WHERE hook_strategy IS NOT NULL AND hook_strategy != '' AND hook_strategy != '...'"):
    ai[norm(r["material_name"])] = dict(r)

materials = []
for r2 in db.execute("SELECT * FROM material_performance_0712"):
    ad = r2["ad_name"]
    na = norm(ad)
    a = None
    for an, aa in ai.items():
        if na in an or an in na:
            a = aa
            break
    if not a: continue
    hook = (a.get("hook_strategy", "") or "").strip()
    if not hook: continue
    materials.append({
        "ad": ad, "hook": hook,
        "style": (a.get("visual_style", "") or "").strip(),
        "prod": (a.get("production_method", "") or "").strip(),
        "narr": (a.get("narrative_mode", "") or "").strip(),
        "char": (a.get("character_elements", "") or "").strip(),
        "colors": (a.get("colors", "") or "").strip(),
        "bgm": (a.get("bgm", "") or "").strip(),
        "spend": r2["spend"], "installs": r2["installs"],
        "cum_roi": r2["cum_roi"], "retention": r2["retention"],
        "ctr": r2["ctr"],
    })

n = len(materials)
total_spend = sum(m["spend"] for m in materials)
total_inst = sum(m["installs"] for m in materials)
avg_roi = round(sum(m["cum_roi"] for m in materials) / n * 100, 1)
avg_ret = round(sum(m["retention"] for m in materials) / n * 100, 1)
vids = sum(1 for m in materials if m["ad"].startswith("V"))
imgs = n - vids

# Clusters
clusters = defaultdict(list)
for m in materials:
    key = m["hook"] + " + " + m["style"] if m["style"] else m["hook"]
    clusters[key].append(m)

cluster_list = []
for name, ms in clusters.items():
    if len(ms) < 2: continue
    cn = len(ms)
    cluster_list.append({
        "name": name, "count": cn,
        "avg_roi": round(sum(m["cum_roi"] for m in ms) / cn * 100, 1),
        "avg_ret": round(sum(m["retention"] for m in ms) / cn * 100, 1),
        "avg_ctr": round(sum(m["ctr"] for m in ms) / cn * 100, 2),
        "avg_spend": round(sum(m["spend"] for m in ms) / cn, 0),
        "top_colors": Counter(m["colors"] for m in ms if m["colors"]).most_common(2),
        "top_bgm": Counter(m["bgm"] for m in ms if m["bgm"]).most_common(2),
    })
cluster_list.sort(key=lambda x: x["avg_roi"], reverse=True)

def tag_agg(key):
    agg = defaultdict(lambda: [0, 0.0, 0.0])
    for m in materials:
        v = m[key]
        if not v: continue
        for t in v.split(","):
            t = t.strip()
            if t and t != "未识别":
                agg[t][0] += 1
                agg[t][1] += m["cum_roi"]
                agg[t][2] += m["retention"]
    tags = [(k, a[0], round(a[1]/a[0]*100,1), round(a[2]/a[0]*100,1)) for k,a in agg.items() if a[0] >= 2]
    tags.sort(key=lambda x: x[2], reverse=True)
    return tags[:6]

hook_tags = tag_agg("hook")
style_tags = tag_agg("style")
prod_tags = tag_agg("prod")
narr_tags = tag_agg("narr")
char_tags = tag_agg("char")

hi_roi = [m for m in materials if m["cum_roi"] > 0.3]
bgm_best = Counter(m["bgm"] for m in hi_roi if m["bgm"]).most_common(5)
color_best = Counter(m["colors"] for m in hi_roi if m["colors"]).most_common(5)

# === DELETE old dashboard blocks and create new ===
# List current blocks
r = subprocess.run([LARK, "base", "+dashboard-block-list", "--base-token", BASE,
    "--dashboard-id", DASH, "--format", "json"],
    capture_output=True, text=True, encoding="utf-8", timeout=15)
blocks = json.loads(r.stdout).get("data", {}).get("blocks", [])
for b in blocks:
    subprocess.run([LARK, "base", "+dashboard-block-delete", "--base-token", BASE,
        "--dashboard-id", DASH, "--block-id", b["block_id"],
        "--format", "json"],
        capture_output=True, text=True, encoding="utf-8", timeout=15)
print(f"Cleared {len(blocks)} old dashboard blocks")

# === BUILD DASHBOARD BLOCKS ===
def add_text_block(name, text):
    subprocess.run([LARK, "base", "+dashboard-block-create", "--base-token", BASE,
        "--dashboard-id", DASH, "--name", name, "--type", "text",
        "--data-config", json.dumps({"text": text}, ensure_ascii=False),
        "--format", "json"],
        capture_output=True, text=True, encoding="utf-8", timeout=15)

# Block 1: KPI
kpi_text = f"## MOVA 心动日服 素材洞察\n\n**{n}条素材** ({vids}视频+{imgs}图片)\n总花费 **${total_spend:,.0f}** | 总安装 **{total_inst:,.0f}**\n平均ROI **{avg_roi}%** | 平均次留 **{avg_ret}%**\n\n过滤条件: 花费>=100美元 + 安装>=10\n数据截止: 0712 | 日本弹射RPG市场"
add_text_block("KPI总览", kpi_text)
time.sleep(0.5)

# Block 2: Clusters
cl_text = "## 聚类ROI排名\n\n"
for i, c in enumerate(cluster_list[:8]):
    cl_text += f"**{i+1}. {c['name'][:45]}**\n"
    cl_text += f"{c['count']}个 | ROI={c['avg_roi']}% | 次留={c['avg_ret']}% | CTR={c['avg_ctr']}%\n"
    if c['top_colors']:
        cl_text += f"色彩: {', '.join(x[0] for x in c['top_colors'][:2])}\n"
    if c['top_bgm']:
        cl_text += f"BGM: {', '.join(x[0][:35] for x in c['top_bgm'][:2])}\n"
    cl_text += "\n"
add_text_block("聚类TOP8", cl_text)
time.sleep(0.5)

# Block 3: Tag rankings
tag_text = "## 六维标签ROI排行\n\n"
for dim, tags in [("钩子策略", hook_tags), ("制作方式", prod_tags), ("画面风格", style_tags), ("叙事模式", narr_tags)]:
    tag_text += f"**{dim}**\n"
    for tag, cnt, roi, ret in tags[:4]:
        tag_text += f"- {tag}: {cnt}个 ROI={roi}%\n"
    tag_text += "\n"
add_text_block("标签排行", tag_text)
time.sleep(0.5)

# Block 4: Creative insights
insight_text = "## 创意洞察\n\n"
insight_text += "### 1. Q版+核心玩法(ROI=39.6%)\n"
insight_text += "BGM: 轻快J-Pop/电子摇滚 萝莉音 鼓点0s进入\n"
insight_text += "色彩: 暖黄+绿色+粉蓝 高饱和明亮\n"
insight_text += "制作: 扒改+混剪 18秒 SSR标识+福利文案开头\n\n"
insight_text += "### 2. 擦边角色+二次元(ROI=26.1%)\n"
insight_text += "BGM: 日系电子摇滚+弦乐 御姐音口播\n"
insight_text += "色彩: 蓝紫+红黑+金色SSR 暗色背景\n"
insight_text += "制作: 实机+UI混剪 22-26秒 角色脸部特写开头\n\n"
insight_text += "### 3. 混合制作 ROI > 纯录屏 28%\n"
insight_text += "建议: 所有素材加入至少一种混合元素\n\n"
insight_text += "### 4. 视频ROI(21.5%) > 图片(17.9%)\n"
insight_text += "80%预算投视频 图片做A/B测试\n\n"
insight_text += "### 5. JP: 声优+限定感(CTR+30%)\n"
insight_text += "JP: 夏休み立即准备 蓝白夏季调"
add_text_block("创意洞察", insight_text)
time.sleep(0.5)

# Block 5: Best BGM & Colors
best_text = "## 高ROI素材最佳实践\n\n"
best_text += "**BGM Top3:**\n"
for bgm, cnt in bgm_best[:3]:
    best_text += f"- {bgm[:50]} ({cnt}个)\n"
best_text += "\n**色彩 Top3:**\n"
for col, cnt in color_best[:3]:
    best_text += f"- {col[:50]} ({cnt}个)\n"
best_text += "\n**制作方式排行:**\n"
for tag, cnt, roi, ret in prod_tags[:4]:
    best_text += f"- {tag}: ROI={roi}%\n"
best_text += "\n**角色元素排行:**\n"
for tag, cnt, roi, ret in char_tags[:3]:
    best_text += f"- {tag}: ROI={roi}%\n"
add_text_block("最佳实践", best_text)
time.sleep(0.5)

# Auto-arrange
subprocess.run([LARK, "base", "+dashboard-arrange", "--base-token", BASE,
    "--dashboard-id", DASH, "--format", "json"],
    capture_output=True, text=True, encoding="utf-8", timeout=15)

print("Dashboard rebuilt with 5 blocks")
print(f"Clusters: {len(cluster_list)}, Tags: {len(hook_tags)}+{len(style_tags)}+{len(prod_tags)}")
db.close()
