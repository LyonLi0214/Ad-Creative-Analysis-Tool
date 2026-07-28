"""素材批量分析: 读累计表 → OSS匹配(缓存) → Claude分析 → 入库SQLite + 群同步"""
import hashlib, json, os, re, sqlite3, subprocess, sys, time
from datetime import datetime, timezone
from pathlib import Path

import oss2

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from feishu_bot.claude_adapter import call_claude

LARK = os.environ.get("LARK_CLI_PATH", "lark-cli")
DB_PATH = ROOT / "data" / "mova_materials.sqlite3"
DL_DIR = ROOT / "downloads" / "batch_analysis"
DL_DIR.mkdir(parents=True, exist_ok=True)
OSS_CACHE = ROOT / "data" / "oss_index.json"
CHAT = sys.argv[1] if len(sys.argv) > 1 else "oc_bb4354db8aaa610f03f0c1e095dc4bc8"
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 10


def _norm(s):
    s = re.sub(r"\.[a-z0-9]{2,5}$", "", str(s).strip(), flags=re.I)
    s = re.sub(r"_[A-Z]{2}\d{2,4}$", "", s)
    s = re.sub(r"广告名称.*$", "", s)
    return re.sub(r"[_\-\s·.]+", "", s).lower()


def notify(msg):
    print(f"  >> {msg}")
    try:
        import requests as req
        for p in (ROOT, ROOT / ".env"):
            if not p.is_file(): continue
            for line in p.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line: continue
                k, v = line.split("=", 1)
                os.environ[k.strip()] = v.strip()
        app_id = os.environ.get("FEISHU_APP_ID", "")
        app_secret = os.environ.get("FEISHU_APP_SECRET", "")
        r = req.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                     json={"app_id": app_id, "app_secret": app_secret}, timeout=10)
        token = r.json()["tenant_access_token"]
        h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        body = {"receive_id": CHAT, "msg_type": "text",
                "content": json.dumps({"text": f">> MOVA: {msg}"[:8000]}, ensure_ascii=False)}
        req.post("https://open.feishu.cn/open-apis/im/v1/messages",
                 params={"receive_id_type": "chat_id"}, headers=h, json=body, timeout=10)
    except Exception as e:
        print(f"    notify err: {e}")


# Load OSS cache
if OSS_CACHE.exists():
    oss_files = json.loads(OSS_CACHE.read_text(encoding="utf-8"))
    print(f"OSS cached: {len(oss_files)} files")
else:
    print("OSS cache not found, indexing...")
    auth = oss2.Auth("[REDACTED]", "[REDACTED]")
    bucket = oss2.Bucket(auth, "https://oss-cn-shanghai.aliyuncs.com", "mova-eu-game-uxd")
    oss_files = []
    for obj in oss2.ObjectIterator(bucket, prefix="心动防线-日/"):
        if not obj.key.endswith("/"):
            oss_files.append({"key": obj.key, "name": obj.key.split("/")[-1], "size": obj.size})
    oss_files = list(oss_files)  # ensure it's a list
    OSS_CACHE.parent.mkdir(parents=True, exist_ok=True)
    OSS_CACHE.write_text(json.dumps(oss_files, ensure_ascii=False), encoding="utf-8")
    print(f"OSS indexed: {len(oss_files)} files")


def oss_search(ad_name):
    target_n = _norm(ad_name)
    target_dates = set(re.findall(r"\d{8}", ad_name))
    best_score, best_file = 0, None
    for f in oss_files:
        score = 0
        fn = _norm(f["name"])
        if target_n in fn or fn in target_n: score += 50
        fd = set(re.findall(r"\d{8}", f["name"]))
        if target_dates & fd: score += 40
        if score > best_score: best_score, best_file = score, f
    if best_file and best_score >= 40:
        return {"key": best_file["key"], "name": best_file["name"], "size": best_file["size"], "score": best_score}
    return None


def get_media(ad):
    al = ad.lower()
    if ".mp4" in al: return "video"
    if ".jpg" in al or ".png" in al: return "image"
    return "unknown"


# Read sheet
result = subprocess.run([
    LARK, "sheets", "+csv-get",
    "--url", "https://dreametech.feishu.cn/wiki/JjgNwLEFaitohZk0g73c0yyfnMI",
    "--sheet-id", "8wvQBI", "--range", "A35:N330"],
    capture_output=True, text=True, encoding="utf-8", timeout=60)
csv_text = json.loads(result.stdout)["data"]["annotated_csv"]

# Get analyzed
conn = sqlite3.connect(str(DB_PATH))
conn.row_factory = sqlite3.Row
analyzed_norm = {_norm(r["material_name"]) for r in conn.execute("SELECT material_name FROM ai_analysis")}

# Parse & filter
todo = []
for line in csv_text.splitlines():
    m = re.match(r"\[row=(\d+)\]\s*(.*)", line)
    if not m: continue
    vals = m.group(2).split(",")
    if len(vals) < 2 or not vals[0].strip(): continue
    ad = vals[0].strip()
    if any(_norm(ad) in an or an in _norm(ad) for an in analyzed_norm):
        continue
    todo.append(ad)
    if len(todo) >= LIMIT: break
conn.close()

print(f"To analyze: {len(todo)}")
if not todo:
    notify("没有待分析素材")
    sys.exit(0)

notify(f"开始分析 {len(todo)} 条素材...")

# OSS auth for downloads
auth = oss2.Auth("[REDACTED]", "[REDACTED]")
bucket = oss2.Bucket(auth, "https://oss-cn-shanghai.aliyuncs.com", "mova-eu-game-uxd")

# Process
done = 0
for i, ad in enumerate(todo):
    media = get_media(ad)
    t0 = time.time()
    notify(f"[{i+1}/{len(todo)}] {media}: {ad[:50]}...")

    # 1. OSS匹配
    match = oss_search(ad)
    if not match:
        notify(f"  SKIP: OSS未匹配")
        continue

    # 2. 下载
    local = DL_DIR / match["name"]
    if not local.exists():
        bucket.get_object_to_file(match["key"], str(local))

    # 3. Claude分析
    image_rule = "图片素材不写BGM/口播/动态过程。" if media == "image" else ""
    prompt = f"""你是游戏广告素材分析专家。分析日本市场素材「心动防线」(弹射RPG日服)。

素材: {ad}  类型: {media}

输出JSON(不要markdown):
{{"hook_content":"前3秒钩子","hook_type":"钩子类型","characters":"角色","core_selling_point":"卖点",
"hook_strategy":"反常识标题|教学攻略|福利展示|擦边角色吸引|剧情抓马|口播推荐|问题型标题|选择互动|福利激励|核心玩法展示|来电钩子|定制互动|噱头爆点",
"character_elements":"黑皮|猫耳兽耳|Q版角色|秘书职场|多角色|立绘展示|真人出镜|AI虚拟人|无角色",
"scene_elements":"弹球战斗|选择界面|养成界面|Boss战|胜利结算|福利界面|模拟UI|直播间",
"production_method":"K帧动画|扒改|真人口播|AI口播|实机录屏|图片轮播|混合",
"narrative_mode":"问题解法|反差逆袭|攻略教学|角色推荐|悬念揭秘|福利直给|选项驱动|定制互动|擦边吸引|角色展示|聊天模拟|口播推荐",
"visual_style":"二次元精致渲染|Q版可爱风|AI合成|实机录屏|实拍|混剪|游戏录屏|图文",
"target_audience":"目标受众","emotional_trigger":"情绪触发","ai_confidence":0.8,"analysis_summary":"一句话总结"}}
{image_rule} 未识别写"未识别"。只输出JSON。"""

    try:
        result = call_claude(prompt, [{"role": "user", "content": f"分析素材: {ad}\n文件: {local}"}])
        raw = result.strip()
        if raw.startswith("```"): raw = re.sub(r"^```\w*\n?|```$", "", raw).strip()
        analysis = json.loads(raw)
    except Exception as e:
        notify(f"  FAIL: {str(e)[:60]}")
        continue

    # 4. 入库
    mk = "mat_" + hashlib.sha256(ad.encode()).hexdigest()[:16]
    now = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("INSERT OR IGNORE INTO material_registry (material_key,game_name,material_name,normalized_name,updated_at) VALUES (?,?,?,?,?)",
                 (mk, "心动防线", ad, _norm(ad), now))
    conn.execute("""INSERT OR REPLACE INTO ai_analysis
        (material_key,material_name,game_name,asset_type,hook_content,hook_type,characters,core_selling_point,
         hook_strategy,character_elements,scene_elements,production_method,narrative_mode,visual_style,
         target_audience,emotional_trigger,ai_confidence,analysis_time,label_status,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (mk, ad, "心动防线", media,
         analysis.get("hook_content",""), analysis.get("hook_type",""),
         analysis.get("characters",""), analysis.get("core_selling_point",""),
         analysis.get("hook_strategy",""), analysis.get("character_elements",""),
         analysis.get("scene_elements",""), analysis.get("production_method",""),
         analysis.get("narrative_mode",""), analysis.get("visual_style",""),
         analysis.get("target_audience",""), analysis.get("emotional_trigger",""),
         analysis.get("ai_confidence",0.8), now, "AI已分析", now))
    conn.commit(); conn.close()

    done += 1
    t = time.time() - t0
    notify(f"  OK({t:.0f}s): {ad[:35]} | {analysis.get('hook_strategy','?')} | {analysis.get('visual_style','?')}")

notify(f"批次完成: {done}/{len(todo)} 成功")
print(f"Done: {done}/{len(todo)}")
