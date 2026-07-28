import os
"""MOVA 素材基因看板 — 聚焦标签维度分析，而非大盘数据.

Usage:
    python gen_dashboard.py                    → dashboard.html
    python gen_dashboard.py --send-chat        → 生成 PNG 长图并发群
"""

from __future__ import annotations

import argparse, json, os, sqlite3, subprocess
from datetime import datetime, timezone
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "mova_materials.sqlite3"
SECRET_PATH = ROOT / ".env"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"

CSS = """<style>
:root{--bg:#0f1117;--card:#181b23;--card-hover:#1e2230;--border:#262a36;--text:#e8eaed;--text-dim:#7c8299;--accent:#5b8af5;--accent2:#f5a34b;--accent3:#4cd9a0;--accent4:#f56b6b;--accent5:#a78bfa}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","PingFang SC","Helvetica Neue",sans-serif;background:var(--bg);color:var(--text);padding:32px;min-height:100vh}
.header{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:28px;padding-bottom:18px;border-bottom:1px solid var(--border)}
.header-left h1{font-size:22px;font-weight:600;background:linear-gradient(135deg,#e8eaed,#a78bfa);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.header-left p{font-size:13px;color:var(--text-dim);margin-top:4px}
.header-right{display:flex;gap:12px;align-items:center}
.date-badge{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:6px 14px;font-size:12px;color:var(--text-dim)}
.tag{display:inline-block;background:rgba(167,139,250,.12);color:var(--accent5);font-size:11px;padding:2px 10px;border-radius:10px;font-weight:500}
.stats-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}
.stat-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px 20px}
.stat-label{font-size:12px;color:var(--text-dim);margin-bottom:4px}
.stat-value{font-size:24px;font-weight:600;letter-spacing:-.5px}
.stat-sub{font-size:11px;color:var(--text-dim);margin-top:4px}
.chart-grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.chart-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:18px}
.chart-card.full{grid-column:1/-1}
.chart-title{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
.chart-title h3{font-size:14px;font-weight:600;color:var(--text)}
.chart-title .sub{font-size:11px;color:var(--text-dim)}
.chart-container{position:relative;width:100%}
canvas{width:100%;height:auto;display:block;border-radius:8px}
.legend{display:flex;gap:18px;margin-top:10px;flex-wrap:wrap}
.legend-item{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--text-dim)}
.legend-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.insights{background:linear-gradient(135deg,rgba(91,138,245,.06),rgba(167,139,250,.06));border:1px solid rgba(91,138,245,.15);border-radius:12px;padding:16px 20px;margin-top:16px}
.insights h4{font-size:12px;color:var(--accent);margin-bottom:8px;font-weight:600}
.insights p{font-size:13px;color:var(--text-dim);line-height:1.7}
.insights strong{color:var(--text);font-weight:600}
.watermark{text-align:center;padding:32px 0 16px;font-size:11px;color:rgba(124,130,153,.35);letter-spacing:2px}
@media(max-width:900px){body{padding:16px}.stats-row{grid-template-columns:repeat(2,1fr)}.chart-grid{grid-template-columns:1fr}}
</style>"""


def load_config():
    for path in (ROOT / ".env", SECRET_PATH):
        if not path.is_file(): continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            os.environ[k.strip()] = v.strip()


def query():
    conn = sqlite3.connect(str(DB_PATH)); conn.row_factory = sqlite3.Row

    d = {}
    # 聚合数据
    agg_rows = list(conn.execute("SELECT * FROM aggregation_results ORDER BY dimension, material_count DESC"))
    by_dim = {}
    for r in agg_rows:
        dim = r["dimension"]; by_dim.setdefault(dim, []).append(dict(r))
    d["aggregation"] = by_dim

    # 素材统计
    d["material_count"] = conn.execute("SELECT COUNT(*) FROM material_registry").fetchone()[0]
    d["analyzed_count"] = conn.execute("SELECT COUNT(*) FROM ai_analysis").fetchone()[0]
    d["mapping_count"] = conn.execute("SELECT COUNT(*) FROM material_appsflyer_mapping").fetchone()[0]

    # 看哪些标签有实际投放数据
    d["tags_with_data"] = conn.execute(
        "SELECT COUNT(DISTINCT tag_value) FROM aggregation_results WHERE total_installs > 0"
    ).fetchone()[0]
    d["tags_total"] = conn.execute("SELECT COUNT(DISTINCT tag_value) FROM aggregation_results").fetchone()[0]

    # 有收入的标签 Top
    d["top_revenue_tags"] = []
    for r in conn.execute(
        "SELECT dimension, tag_value, total_revenue, material_count FROM aggregation_results WHERE total_revenue > 0 ORDER BY total_revenue DESC LIMIT 8"
    ):
        d["top_revenue_tags"].append(dict(r))

    conn.close()
    return d


def generate_html(d: dict) -> str:
    agg_j = json.dumps(d["aggregation"], ensure_ascii=False)
    top_rev_j = json.dumps(d["top_revenue_tags"], ensure_ascii=False)
    dim_names_j = json.dumps(list(d["aggregation"].keys()), ensure_ascii=False)

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>MOVA 素材基因看板</title>{CSS}</head>
<body>

<div class="header">
<div class="header-left"><h1>🧬 素材基因分析看板</h1><p>AI 拆解标签 × 投放效果交叉聚合 · 找到模因，指导创意</p></div>
<div class="header-right"><span class="date-badge">{datetime.now().strftime('%Y-%m-%d %H:%M')}</span><span class="tag">标签维度</span></div>
</div>

<div class="stats-row">
<div class="stat-card"><div class="stat-label">🧩 已拆解素材</div><div class="stat-value">{d['analyzed_count']}</div><div class="stat-sub">注册 {d['material_count']} / 映射 {d['mapping_count']}</div></div>
<div class="stat-card"><div class="stat-label">🏷 标签总数</div><div class="stat-value">{d['tags_total']}</div><div class="stat-sub">6 维度 × 合计 {d['tags_total']} 个标签值</div></div>
<div class="stat-card"><div class="stat-label">📊 有投放数据标签</div><div class="stat-value">{d['tags_with_data']}</div><div class="stat-sub">已关联到 AppsFlyer 效果</div></div>
<div class="stat-card"><div class="stat-label">🎯 标签覆盖率</div><div class="stat-value">{d['tags_with_data']/max(d['tags_total'],1)*100:.0f}%</div><div class="stat-sub">效果数据关联进度</div></div>
</div>

<div class="chart-grid">
<div class="chart-card full">
<div class="chart-title"><h3>📊 六维标签素材分布</h3><span class="sub">每个维度下的标签值素材数量（反映创意偏好）</span></div>
<div class="chart-container"><canvas id="tagDistChart" width="1040" height="320"></canvas></div>
<div class="legend">
<div class="legend-item"><span class="legend-dot" style="background:var(--accent5);"></span> hook_策略</div>
<div class="legend-item"><span class="legend-dot" style="background:var(--accent3);"></span> 角色元素</div>
<div class="legend-item"><span class="legend-dot" style="background:var(--accent2);"></span> 场景元素</div>
<div class="legend-item"><span class="legend-dot" style="background:var(--accent);"></span> 制作方式</div>
<div class="legend-item"><span class="legend-dot" style="background:var(--accent4);"></span> 叙事模式</div>
<div class="legend-item"><span class="legend-dot" style="background:#e8eaed;"></span> 画面风格</div>
</div>
</div>
</div>

<div class="chart-grid">
<div class="chart-card full">
<div class="chart-title"><h3>💰 有效果数据的标签排行</h3><span class="sub">已关联到投放收入的标签（数据持续更新中）</span></div>
<div class="chart-container"><canvas id="revenueChart" width="1040" height="280"></canvas></div>
</div>
</div>

<div class="chart-grid">
<div class="chart-card">
<div class="chart-title"><h3>🧩 hook_策略 分布</h3><span class="sub">素材开头策略偏好</span></div>
<div class="chart-container"><canvas id="hookChart" width="500" height="260"></canvas></div>
</div>
<div class="chart-card">
<div class="chart-title"><h3>🎨 画面风格 分布</h3><span class="sub">视觉风格偏好</span></div>
<div class="chart-container"><canvas id="visualChart" width="500" height="260"></canvas></div>
</div>
</div>

<div class="insights"><h4>基因洞察</h4><p>
已拆解 <strong>{d['analyzed_count']} 个素材</strong>，覆盖 6 个维度共 <strong>{d['tags_total']} 个标签值</strong>。
{d['tags_with_data']} 个标签已有投放效果数据关联。
随着投放映射链路完善，标签维度的 ROI/CPI/CTR 对比将自动填充。
核心价值：不是看花了多少钱，而是看哪种创意基因在驱动效果。</p></div>

<div class="watermark">MOVA 素材基因分析系统 · 找模因 · 指导创意</div>

<script>
var DPR=window.devicePixelRatio||1;
function setup(id,w,h){{var c=document.getElementById(id);c.width=w*DPR;c.height=h*DPR;c.style.width=w+'px';c.style.height=h+'px';var x=c.getContext('2d');x.scale(DPR,DPR);return x}}

var agg={agg_j};
var topRev={top_rev_j};
var dimNames={dim_names_j};
var COLORS=['#a78bfa','#4cd9a0','#f5a34b','#5b8af5','#f56b6b','#e8eaed'];

// ── 六维标签分布横向堆叠条 ──
(function(){{
var ctx=setup('tagDistChart',1040,320),W=1040,H=320;
var pad={{top:20,right:30,bottom:30,left:100}},cw=W-pad.left-pad.right,ch=H-pad.top-pad.bottom;
var dims=dimNames,rowH=Math.min(36,ch/dims.length-4);
dims.forEach(function(dim,di){{
var items=(agg[dim]||[]).slice(0,8);
var maxN=items.length>0?items[0].素材数:1;
var y=pad.top+di*(rowH+8);
ctx.fillStyle='rgba(255,255,255,.35)';ctx.font='11px -apple-system,sans-serif';ctx.textAlign='right';
ctx.fillText(dim,pad.left-10,y+rowH/2+4);
items.forEach(function(it,si){{
var bw=it.素材数/maxN*cw*0.8,x=pad.left;
for(var j=0;j<si;j++){{x+=(items[j].素材数/maxN*cw*0.8)+2}}
var grad=ctx.createLinearGradient(x,y,x+bw,y);
grad.addColorStop(0,COLORS[di]);grad.addColorStop(1,COLORS[di]+'40');
ctx.fillStyle=grad;ctx.beginPath();ctx.roundRect(x,y,bw,rowH,[3]);ctx.fill();
ctx.fillStyle='rgba(255,255,255,.7)';ctx.font='9px -apple-system,sans-serif';ctx.textAlign='left';
ctx.fillText(it.标签值.slice(0,14)+' ('+it.素材数+')',x+4,y+rowH/2+3);
}});
}});
}})();

// ── 有收入标签排行 ──
(function(){{
var ctx=setup('revenueChart',1040,280),W=1040,H=280;
var pad={{top:10,right:160,bottom:10,left:10}},cw=W-pad.left-pad.right,ch=H-pad.top-pad.bottom;
if(topRev.length===0){{ctx.fillStyle='rgba(255,255,255,.2)';ctx.font='14px -apple-system,sans-serif';ctx.textAlign='center';ctx.fillText('暂无效果数据关联，映射链路完善后将自动展示',W/2,H/2);return}}
var maxR=Math.max.apply(null,topRev.map(function(r){{return r.total_revenue}}))||1;
var barH=Math.min(22,ch/topRev.length-4);
topRev.forEach(function(r,i){{
var y=pad.top+i*(barH+4),bw=r.total_revenue/maxR*cw;
var grad=ctx.createLinearGradient(pad.left,y,pad.left+bw,y);
grad.addColorStop(0,'#4cd9a0');grad.addColorStop(1,'#4cd9a040');
ctx.fillStyle=grad;ctx.beginPath();ctx.roundRect(pad.left,y,bw,barH,[0,3,3,0]);ctx.fill();
ctx.fillStyle='var(--text)';ctx.font='11px -apple-system,sans-serif';ctx.textAlign='left';
ctx.fillText(r.dimension+': '+r.tag_value,pad.left+bw+6,y+barH/2+4);
ctx.fillStyle='var(--accent3)';ctx.textAlign='right';
ctx.fillText('$'+r.total_revenue.toFixed(1)+' ('+r.material_count+'素材)',pad.left+bw-6,y+barH/2+4);
}});
}})();

// ── hook_策略 横向条 ──
(function(){{
var ctx=setup('hookChart',500,260),W=500,H=260;
var pad={{top:10,right:10,bottom:10,left:10}},cw=W-pad.left-pad.right,ch=H-pad.top-pad.bottom;
var items=(agg['hook_策略']||[]).slice(0,10);
var maxN=items.length>0?items[0].素材数:1;
var barH=Math.min(20,ch/items.length-3);
items.forEach(function(it,i){{
var y=pad.top+i*(barH+3),bw=it.素材数/maxN*cw;
var grad=ctx.createLinearGradient(pad.left,y,pad.left+bw,y);
grad.addColorStop(0,'#a78bfa');grad.addColorStop(1,'#a78bfa40');
ctx.fillStyle=grad;ctx.beginPath();ctx.roundRect(pad.left,y,bw,barH,[0,3,3,0]);ctx.fill();
ctx.fillStyle='var(--text-dim)';ctx.font='10px -apple-system,sans-serif';ctx.textAlign='right';
ctx.fillText(it.标签值,pad.left+bw-6,y+barH/2+4);
}}}})();
}})();

// ── 画面风格 横向条 ──
(function(){{
var ctx=setup('visualChart',500,260),W=500,H=260;
var pad={{top:10,right:10,bottom:10,left:10}},cw=W-pad.left-pad.right,ch=H-pad.top-pad.bottom;
var items=(agg['画面风格']||[]).slice(0,10);
var maxN=items.length>0?items[0].素材数:1;
var barH=Math.min(20,ch/items.length-3);
items.forEach(function(it,i){{
var y=pad.top+i*(barH+3),bw=it.素材数/maxN*cw;
var grad=ctx.createLinearGradient(pad.left,y,pad.left+bw,y);
grad.addColorStop(0,'#e8eaed');grad.addColorStop(1,'#e8eaed20');
ctx.fillStyle=grad;ctx.beginPath();ctx.roundRect(pad.left,y,bw,barH,[0,3,3,0]);ctx.fill();
ctx.fillStyle='var(--text-dim)';ctx.font='10px -apple-system,sans-serif';ctx.textAlign='right';
ctx.fillText(it.标签值,pad.left+bw-6,y+barH/2+4);
}}}})();
}})();
</script>
</body></html>"""


def html_to_png(html_path, png_path):
    browser = CHROME if os.path.exists(CHROME) else ""
    if not browser: return False
    est_h = max(2400, html_path.stat().st_size // 2)
    subprocess.run([browser, "--headless", "--disable-gpu", f"--screenshot={png_path}", f"--window-size=800,{est_h}", "--virtual-time-budget=3000", f"file:///{html_path.as_posix()}"], capture_output=True, text=True, timeout=30, encoding="utf-8", errors="replace")
    return png_path.exists()


def send_image(png_path):
    load_config()
    r = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal", json={"app_id": os.environ["FEISHU_APP_ID"], "app_secret": os.environ["FEISHU_APP_SECRET"]}, timeout=10)
    token = r.json()["tenant_access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    state = json.loads((ROOT / "logs" / "feishu_bot_state.json").read_text(encoding="utf-8"))
    chat_id = state.get("default_chat_id", "")
    if not chat_id: return print("No chat_id")
    with open(png_path, "rb") as f:
        r = requests.post("https://open.feishu.cn/open-apis/im/v1/images", headers=headers, files={"image": (png_path.name, f, "image/png")}, data={"image_type": "message"}, timeout=30)
    image_key = r.json().get("data", {}).get("image_key", "")
    if not image_key: return print(f"Upload failed: {r.json()}")
    body = {"receive_id": chat_id, "msg_type": "image", "content": json.dumps({"image_key": image_key}, ensure_ascii=False)}
    r = requests.post("https://open.feishu.cn/open-apis/im/v1/messages", params={"receive_id_type": "chat_id"}, headers={**headers, "Content-Type": "application/json"}, json=body, timeout=10)
    print("Sent" if r.json().get("code") == 0 else f"Failed: {r.json()}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--send-chat", action="store_true")
    p.add_argument("--output", default="dashboard")
    args = p.parse_args()
    d = query()
    html = generate_html(d)
    hp = ROOT / f"{args.output}.html"; hp.write_text(html, encoding="utf-8")
    print(f"HTML: {hp} ({hp.stat().st_size/1024:.0f}KB)")
    if args.send_chat:
        pp = ROOT / f"{args.output}.png"
        if html_to_png(hp, pp):
            print(f"PNG: {pp} ({pp.stat().st_size/1024:.0f}KB)")
            send_image(pp)

if __name__ == "__main__":
    main()
