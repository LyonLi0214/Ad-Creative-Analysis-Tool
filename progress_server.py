#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
progress_server.py — 分析进度前端面板（含 tab 切换 + 历史日志）

部署到 ECS:
    nohup python3 progress_server.py --port 8080 > logs/progress.log 2>&1 &
"""
import os, json, argparse, time
from datetime import datetime
from pathlib import Path
from flask import Flask, Response, request

import requests

# ── 配置 ──
NOCODB_BASE = os.environ.get("NOCODB_BASE", "").rstrip("/")
NOCODB_TOKEN = os.environ.get("NOCODB_TOKEN", "")

TABLE_ASSETS = os.environ.get("NOCODB_TABLE_ASSETS", "mt35rdj4l214v6x")
TABLE_ANALYSIS = os.environ.get("NOCODB_TABLE_ANALYSIS", "mitt7nrv1t74e2l")
TABLE_TIMELINE = os.environ.get("NOCODB_TABLE_TIMELINE", "mev72vvwyl3gzyy")
TABLE_LOGS = os.environ.get("NOCODB_TABLE_LOGS", "")  # Will be created

H = {"xc-token": NOCODB_TOKEN, "Content-Type": "application/json"}

app = Flask(__name__)

# ── NocoDB 读取 ──

def noco_get(table_id, params=None):
    if not NOCODB_BASE or not NOCODB_TOKEN or not table_id:
        return {}
    try:
        r = requests.get(f"{NOCODB_BASE}/api/v2/tables/{table_id}/records",
                         headers=H, params=params or {}, timeout=10)
        if r.ok:
            return r.json()
    except Exception:
        pass
    return {}

def get_progress():
    stats = {
        "total": 0, "pending": 0, "analyzed": 0, "confirmed": 0,
        "timeline_count": 0, "analysis_count": 0, "recent_analyses": [],
    }
    try:
        data = noco_get(TABLE_ASSETS, {"limit": 1000})
        rows = data.get("list", [])
        stats["total"] = len(rows)
        for row in rows:
            status = str(row.get("状态", "")).strip()
            if status == "待分析":
                stats["pending"] += 1
            elif status == "AI已分析":
                stats["analyzed"] += 1
            elif status in ("人工确认", "已上线"):
                stats["confirmed"] += 1

        data2 = noco_get(TABLE_ANALYSIS, {"limit": 20, "sort": "-Id"})
        rows2 = data2.get("list", [])
        stats["analysis_count"] = data2.get("pageInfo", {}).get("totalRows", len(rows2))
        for row in rows2[:10]:
            name = row.get("素材名称", "")
            if name:
                stats["recent_analyses"].append({
                    "name": name,
                    "hook": (row.get("Hook内容", "") or "")[:40],
                    "tag": row.get("hook_策略", "") or "",
                    "confidence": row.get("AI置信度", ""),
                    "time": str(row.get("分析时间", ""))[:16],
                })

        data3 = noco_get(TABLE_TIMELINE, {"limit": 1})
        stats["timeline_count"] = data3.get("pageInfo", {}).get("totalRows", 0)

    except Exception as e:
        stats["error"] = str(e)

    stats["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return stats

def get_logs():
    """从 NocoDB 读取日志"""
    if not TABLE_LOGS:
        return []
    data = noco_get(TABLE_LOGS, {"limit": 50, "sort": "-Id"})
    return data.get("list", [])


# ── HTML ──

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>素材拆解分析库</title>
<style>
:root{--bg:#0f172a;--card:#1e293b;--border:#334155;--text:#e2e8f0;--muted:#94a3b8;--accent:#38bdf8;--green:#22c55e;--yellow:#facc15;--red:#ef4444;--purple:#a78bfa}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.topbar{background:var(--card);border-bottom:1px solid var(--border);padding:12px 20px;display:flex;align-items:center;gap:24px}
.topbar h1{font-size:1.1rem;color:var(--accent);margin:0}
.tabs{display:flex;gap:4px}
.tab{padding:6px 16px;border-radius:6px;font-size:0.85rem;cursor:pointer;border:1px solid transparent;background:transparent;color:var(--muted);transition:all 0.2s}
.tab:hover{color:var(--text);background:rgba(56,189,248,0.1)}
.tab.active{color:var(--accent);border-color:var(--accent);background:rgba(56,189,248,0.1)}
.container{max-width:1000px;margin:0 auto;padding:24px 20px}
.card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:24px;margin-bottom:16px}
.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:20px}
.stat{text-align:center;padding:16px;background:var(--bg);border-radius:8px}
.stat .num{font-size:2.2rem;font-weight:700}
.stat .label{font-size:0.8rem;color:var(--muted)}
.num.accent{color:var(--accent)}.num.green{color:var(--green)}.num.yellow{color:var(--yellow)}.num.purple{color:var(--purple)}
.progress-bar{background:var(--border);border-radius:8px;height:12px;margin:16px 0;overflow:hidden}
.progress-fill{height:100%;border-radius:8px;transition:width 0.5s;background:linear-gradient(90deg,var(--green),var(--accent))}
.eta{text-align:center;color:var(--muted);font-size:0.9rem;margin-top:8px}
.extra-stats{display:grid;grid-template-columns:repeat(2,1fr);gap:12px;margin-top:16px}
.extra-stat{text-align:center;padding:12px;background:var(--bg);border-radius:8px}
.extra-stat .val{font-size:1.3rem;font-weight:700;color:var(--accent)}
.extra-stat .lbl{font-size:0.75rem;color:var(--muted)}
.results h3,.logs h3{font-size:0.95rem;color:var(--muted);margin-bottom:12px}
.result-item,.log-item{display:flex;justify-content:space-between;padding:8px 12px;font-size:0.85rem;border-bottom:1px solid var(--bg)}
.result-name{color:var(--text);flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.result-tag{color:var(--accent);min-width:80px}
.result-conf{color:var(--green);min-width:50px;text-align:right}
.log-time{color:var(--muted);min-width:120px;font-size:0.8rem}
.log-action{color:var(--accent);min-width:100px}
.log-detail{color:var(--text);flex:1}
.footer{text-align:center;color:#475569;font-size:0.75rem;margin-top:24px}
.page{display:none}.page.active{display:block}
/* Report styles */
.report{line-height:1.7}
.report h2{font-size:1.2rem;color:var(--accent);margin:28px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--border)}
.report h3{font-size:1rem;margin:16px 0 8px}
.report table{width:100%;border-collapse:collapse;margin:8px 0}
.report th,.report td{padding:8px 12px;text-align:left;border-bottom:1px solid var(--border);font-size:0.85rem}
.report th{color:var(--muted);font-size:0.75rem;text-transform:uppercase}
.report code{background:var(--bg);padding:1px 5px;border-radius:3px;font-size:0.8rem;color:var(--accent)}
.report pre{background:var(--bg);padding:12px;border-radius:8px;overflow-x:auto;margin:8px 0;font-size:0.8rem}
.report .flow{display:flex;align-items:center;flex-wrap:wrap;gap:6px;margin:12px 0}
.report .flow-step{background:var(--bg);padding:6px 12px;border-radius:6px;border:1px solid var(--border);font-size:0.8rem}
.report .flow-arrow{color:var(--accent)}
.report .tag{display:inline-block;padding:2px 8px;border-radius:4px;font-size:0.7rem;margin:2px}
.report .tag-green{background:rgba(34,197,94,0.2);color:var(--green)}
.report .tag-blue{background:rgba(56,189,248,0.2);color:var(--accent)}
.report .tag-purple{background:rgba(167,139,250,0.2);color:var(--purple)}
.report .tag-yellow{background:rgba(250,204,21,0.2);color:var(--yellow)}
.report .badge{display:inline-block;padding:3px 8px;border-radius:5px;font-size:0.7rem;font-weight:600}
.report .badge-green{background:rgba(34,197,94,0.2);color:var(--green)}
.report .timeline{position:relative;padding-left:20px}
.report .timeline::before{content:'';position:absolute;left:6px;top:0;bottom:0;width:2px;background:var(--border)}
.report .tl-item{position:relative;margin-bottom:16px}
.report .tl-item::before{content:'';position:absolute;left:-17px;top:5px;width:8px;height:8px;border-radius:50%;background:var(--accent)}
.report .tl-date{font-size:0.75rem;color:var(--muted)}
.report .tl-content{font-size:0.85rem}
</style>
</head>
<body>

<div class="topbar">
  <h1>素材拆解分析库</h1>
  <div class="tabs">
    <div class="tab active" onclick="showPage('progress')">进度面板</div>
    <div class="tab" onclick="showPage('logs')">运行日志</div>
    <div class="tab" onclick="showPage('report')">工作流报告</div>
  </div>
</div>

<!-- Progress Page -->
<div class="page active" id="page-progress">
<div class="container">
  <div class="card">
    <div class="stats">
      <div class="stat"><div class="num accent" id="total">-</div><div class="label">总素材数</div></div>
      <div class="stat"><div class="num green" id="analyzed">-</div><div class="label">AI已分析</div></div>
      <div class="stat"><div class="num yellow" id="pending">-</div><div class="label">待分析</div></div>
      <div class="stat"><div class="num purple" id="confirmed">-</div><div class="label">已确认</div></div>
    </div>
    <div class="progress-bar"><div class="progress-fill" id="progress-fill" style="width:0%"></div></div>
    <div class="eta" id="eta"></div>
    <div class="extra-stats">
      <div class="extra-stat"><div class="val" id="timeline-count">-</div><div class="lbl">逐帧拆解记录</div></div>
      <div class="extra-stat"><div class="val" id="analysis-count">-</div><div class="lbl">分析结果总数</div></div>
    </div>
  </div>
  <div class="card results">
    <h3>最近分析结果</h3>
    <div id="results"></div>
  </div>
  <div class="footer">自动刷新 · 每5秒</div>
</div>
</div>

<!-- Logs Page -->
<div class="page" id="page-logs">
<div class="container">
  <div class="card logs">
    <h3>运行日志（最近50条）</h3>
    <div id="logs-list"></div>
  </div>
</div>
</div>

<!-- Report Page -->
<div class="page" id="page-report">
<div class="container report" id="report-content">
  <p style="color:var(--muted)">加载中...</p>
</div>
</div>

<script>
function showPage(name) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  document.getElementById('page-' + name).classList.add('active');
  document.querySelectorAll('.tab')[['progress','logs','report'].indexOf(name)].classList.add('active');
  if (name === 'report') loadReport();
  if (name === 'logs') loadLogs();
}

function formatTime(sec) {
  if (sec < 60) return sec + '秒';
  if (sec < 3600) return Math.floor(sec/60) + '分' + (sec%60) + '秒';
  return Math.floor(sec/3600) + '时' + Math.floor((sec%3600)/60) + '分';
}

function updateProgress() {
  fetch('/api/progress').then(r => r.json()).then(data => {
    document.getElementById('total').textContent = data.total || 0;
    document.getElementById('analyzed').textContent = data.analyzed || 0;
    document.getElementById('pending').textContent = data.pending || 0;
    document.getElementById('confirmed').textContent = data.confirmed || 0;
    document.getElementById('timeline-count').textContent = data.timeline_count || 0;
    document.getElementById('analysis-count').textContent = data.analysis_count || 0;
    const done = (data.analyzed || 0) + (data.confirmed || 0);
    const pct = data.total > 0 ? Math.round(done / data.total * 100) : 0;
    document.getElementById('progress-fill').style.width = pct + '%';
    document.getElementById('eta').textContent = '进度 ' + pct + '% · 已完成 ' + done + '/' + data.total;
    const rr = document.getElementById('results');
    const results = data.recent_analyses || [];
    if (results.length === 0) {
      rr.innerHTML = '<div class="result-item"><span class="result-name">暂无</span></div>';
    } else {
      rr.innerHTML = results.map(r =>
        '<div class="result-item"><span class="result-name">' + r.name + '</span><span class="result-tag">' + (r.tag||'') + '</span><span class="result-conf">' + (r.confidence ? (r.confidence*100).toFixed(0)+'%' : '') + '</span></div>'
      ).join('');
    }
  }).catch(() => {});
}

function loadLogs() {
  fetch('/api/logs').then(r => r.json()).then(data => {
    const el = document.getElementById('logs-list');
    if (data.length === 0) {
      el.innerHTML = '<div class="log-item"><span class="log-detail">暂无日志</span></div>';
    } else {
      el.innerHTML = data.map(l =>
        '<div class="log-item"><span class="log-time">' + (l.时间||'') + '</span><span class="log-action">' + (l.操作||'') + '</span><span class="log-detail">' + (l.详情||'') + '</span></div>'
      ).join('');
    }
  }).catch(() => {});
}

function loadReport() {
  fetch('/api/report').then(r => r.text()).then(html => {
    document.getElementById('report-content').innerHTML = html;
  }).catch(e => {
    document.getElementById('report-content').innerHTML = '<p>加载失败: ' + e + '</p>';
  });
}

setInterval(updateProgress, 5000);
updateProgress();
</script>
</body>
</html>"""


@app.route("/")
def index():
    return Response(HTML_TEMPLATE, content_type="text/html; charset=utf-8")

@app.route("/api/progress")
def api_progress():
    return Response(json.dumps(get_progress(), ensure_ascii=False), content_type="application/json")

@app.route("/api/logs")
def api_logs():
    logs = get_logs()
    return Response(json.dumps(logs, ensure_ascii=False), content_type="application/json")

@app.route("/api/report")
def api_report():
    report_path = Path(__file__).parent / "templates" / "workflow_report.html"
    if report_path.exists():
        html = report_path.read_text(encoding="utf-8")
        # Extract body content only
        start = html.find('<body>')
        end = html.find('</body>')
        if start >= 0 and end >= 0:
            body = html[start+6:end]
            # Remove outer container div to avoid nesting
            body = body.replace('<div class="container">', '<div>')
            return Response(body, content_type="text/html; charset=utf-8")
        return Response(html, content_type="text/html; charset=utf-8")
    return "Report not found", 404

@app.route("/health")
def health():
    return "ok"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    print(f"启动: http://0.0.0.0:{args.port}")
    print(f"NocoDB: {NOCODB_BASE}")
    app.run(host="0.0.0.0", port=args.port, debug=False)
