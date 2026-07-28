import os
"""
SQLite → 飞书 Base 素材总览 详情字段补全模块.

将 ai_analysis 的 12 个详情字段 (人物/音效BGM/色彩/节奏/特效等)
通过 material_registry 匹配后推送到 Base 素材总览表。

作为 pipeline_daily.py 的独立步骤使用，也可单独运行。

Usage:
    python sync_detail_fields.py                   # 全量更新
    python sync_detail_fields.py --dry-run         # 只看不改
"""
import json, os, re, sqlite3, sys, time
from collections import Counter
from pathlib import Path
import requests

ROOT = Path(__file__).resolve().parent
SECRET_PATH = ROOT / ".env"
DB_PATH = ROOT / "data" / "mova_materials.sqlite3"

BASE_TOKEN = os.environ.get("FEISHU_BASE_TOKEN", "")
TABLE_MATERIAL = "tbluk4Fi6DZNEM2E"      # 素材总览

# 字段映射: Base字段名 → SQLite列名
FIELD_MAP = {
    '人物': 'characters',
    '音效BGM': 'bgm',
    '色彩': 'colors',
    '节奏': 'rhythm',
    '特效': 'effects',
    '文案字幕': 'subtitles',
    'Hook类型': 'hook_type',
    '前3秒钩子': 'hook_content',
    '核心卖点': 'core_selling_point',
    '目标受众': 'target_audience',
    '情绪触发': 'emotional_trigger',
    '营销策略': 'marketing_strategy',
}

def load_secrets():
    for path in [SECRET_PATH, ROOT / ".env"]:
        if path.is_file():
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                if "=" in line and not line.startswith("#"):
                    k, v = line.strip().split("=", 1)
                    os.environ[k.strip()] = v.strip()
    return os.environ.get("FEISHU_APP_ID", ""), os.environ.get("FEISHU_APP_SECRET", "")

def get_token(app_id, app_secret):
    r = requests.post(
        'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',
        json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
    return r.json()["tenant_access_token"]

def norm(s):
    s = re.sub(r'\.[a-z0-9]{2,5}$', '', str(s).strip(), flags=re.I)
    s = re.sub(r'_[A-Z]{2}\d{2,4}$', '', s)
    s = re.sub(r'广告名称.*$', '', s)
    return re.sub(r'[_\-\s·.]+', '', s).lower()

def safe(val):
    if val is None: return None
    s = str(val).strip()
    return None if s in ("", "...", "None") else s

def match_sqlite_data():
    """从 SQLite 读取并匹配 136条 → detail data."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    cur.execute("""
        SELECT material_key, material_name,
               characters, bgm, colors, rhythm, effects, subtitles,
               hook_type, hook_content, core_selling_point,
               target_audience, emotional_trigger, marketing_strategy
        FROM ai_analysis
        WHERE hook_strategy IS NOT NULL AND hook_strategy != '' AND hook_strategy != '...'
    """)
    analysis = {r["material_key"]: dict(r) for r in cur.fetchall()}

    cur.execute("SELECT material_name, material_key FROM material_registry")
    registry_by_norm = {}
    for r in cur.fetchall():
        n = norm(r["material_name"])
        registry_by_norm.setdefault(n, []).append((r["material_name"], r["material_key"]))

    cur.execute("SELECT ad_name FROM material_performance_0712")
    perf_rows = [r["ad_name"] for r in cur.fetchall()]
    conn.close()

    matches = []
    for ad_name in perf_rows:
        n = norm(ad_name)
        found_key = None
        if n in registry_by_norm:
            for mat_name, mat_key in registry_by_norm[n]:
                if mat_key in analysis:
                    found_key = mat_key
                    break
        if found_key:
            matches.append((ad_name, found_key))

    print(f"  SQLite: {len(perf_rows)}条, 匹配: {len(matches)}")
    return matches, analysis

def build_base_index(token):
    """读取 Base 136条记录: 素材名称 → record_id."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    records = []
    page_token = None
    while True:
        params = {"page_size": 200}
        if page_token: params["page_token"] = page_token
        r = requests.get(
            f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_MATERIAL}/records",
            headers=headers, params=params, timeout=30)
        data = r.json()["data"]
        records.extend(data["items"])
        if not data.get("has_more"): break
        page_token = data.get("page_token")

    idx = {}
    for rec in records:
        name = (rec.get("fields", {}).get("素材名称", "") or "").strip()
        if name:
            idx[name] = rec["record_id"]
    print(f"  Base: {len(records)}条, 可匹配: {len(idx)}")
    return idx

def build_updates(matches, analysis, base_idx):
    updates = []
    for ad_name, mat_key in matches:
        a = analysis[mat_key]
        rid = base_idx.get(ad_name)
        if not rid:
            n = norm(ad_name)
            rid = next((base_idx[bn] for bn in base_idx if norm(bn) == n), None)
        if not rid:
            continue

        fields = {}
        for base_f, sql_c in FIELD_MAP.items():
            v = safe(a.get(sql_c))
            if v is not None:
                fields[base_f] = v

        hc = safe(a.get("hook_content"))
        cp = safe(a.get("core_selling_point"))
        if hc or cp:
            fields["画面内容"] = " | ".join(p for p in [hc, cp] if p)

        if fields:
            updates.append((rid, fields, ad_name))
    return updates

def push_updates(updates, token, dry_run=False):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    success = fail = 0
    for i, (rid, fields, name) in enumerate(updates):
        if dry_run:
            success += 1
            continue
        try:
            r = requests.put(
                f"https://open.feishu.cn/open-apis/bitable/v1/apps/{BASE_TOKEN}/tables/{TABLE_MATERIAL}/records/{rid}",
                headers=headers, json={"fields": fields}, timeout=15)
            resp = r.json()
            if resp.get("code") == 0:
                success += 1
            else:
                fail += 1
                print(f"  FAIL [{name[:30]}]: code={resp.get('code')} {resp.get('msg')}")
        except Exception as e:
            fail += 1
            print(f"  EXCEPT [{name[:30]}]: {e}")
        if (i+1) % 30 == 0:
            app_id, app_secret = load_secrets()
            if app_id:
                token = get_token(app_id, app_secret)
                headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            print(f"    进度: {i+1}/{len(updates)}, 成功: {success}")
    return success, fail

def main():
    dry_run = "--dry-run" in sys.argv
    if dry_run: sys.argv.remove("--dry-run")
    t0 = time.time()
    print(f"{'[DRY RUN] ' if dry_run else ''}Sync detail fields to 素材总览")
    app_id, app_secret = load_secrets()
    if not app_id: print("ERROR: 飞书凭证未配置"); return 1
    matches, analysis = match_sqlite_data()
    token = get_token(app_id, app_secret)
    base_idx = build_base_index(token)
    updates = build_updates(matches, analysis, base_idx)
    print(f"  待更新: {len(updates)} 条")
    success, fail = push_updates(updates, token, dry_run)
    print(f"\n完成: {success}成功, {fail}失败 ({time.time()-t0:.0f}s)")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
