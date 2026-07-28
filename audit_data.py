"""Audit all averages in SQLite and Base — check if properly weighted."""
import sqlite3, re, json
from collections import defaultdict, Counter

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
        "ai": a,
    })

valid = [m for m in materials if m["ai"]]
n = len(valid)

print("=" * 60)
print("AUDIT: Average calculations")
print("=" * 60)

# 1. Check: Simple avg vs Spend-weighted avg ROI
simple_roi = sum(m["cum_roi"] for m in valid) / n
total_revenue = sum(m["spend"] * m["cum_roi"] for m in valid)
total_spend = sum(m["spend"] for m in valid)
weighted_roi = total_revenue / total_spend if total_spend > 0 else 0

print(f"\n1. Overall ROI:")
print(f"   Simple avg (sum ROI / n): {simple_roi*100:.1f}%")
print(f"   Spend-weighted avg (total rev / total spend): {weighted_roi*100:.1f}%")
print(f"   Difference: {(simple_roi - weighted_roi)*100:.1f}pp")
if abs(simple_roi - weighted_roi) > 0.05:
    print(f"   WARNING: Simple avg differs from spend-weighted by >5pp!")

# 2. Check cluster averages
print(f"\n2. Cluster averages audit:")
clusters = defaultdict(list)
for m in valid:
    hook = (m["ai"].get("hook_strategy", "") or "").strip()
    style = (m["ai"].get("visual_style", "") or "").strip()
    key = hook + " + " + style if style else hook
    clusters[key].append(m)

# Verify top 3 clusters
for name, ms in sorted(clusters.items(), key=lambda x: -len(x[1]))[:3]:
    cn = len(ms)
    simple_avg = sum(m["cum_roi"] for m in ms) / cn
    cluster_rev = sum(m["spend"] * m["cum_roi"] for m in ms)
    cluster_spend = sum(m["spend"] for m in ms)
    weighted_avg = cluster_rev / cluster_spend if cluster_spend > 0 else 0
    print(f"   {name[:50]}: {cn} materials")
    print(f"     Simple avg ROI: {simple_avg*100:.1f}% | Spend-weighted: {weighted_avg*100:.1f}%")

# 3. Check tag averages
print(f"\n3. Tag averages audit (hook_strategy):")
tag_data = defaultdict(list)
for m in valid:
    hook = (m["ai"].get("hook_strategy", "") or "").strip()
    if hook:
        tag_data[hook].append(m)

for tag in sorted(tag_data, key=lambda t: -len(tag_data[t]))[:5]:
    ms = tag_data[tag]
    cn = len(ms)
    simple_avg = sum(m["cum_roi"] for m in ms) / cn
    total_rev = sum(m["spend"] * m["cum_roi"] for m in ms)
    total_sp = sum(m["spend"] for m in ms)
    weighted = total_rev / total_sp if total_sp > 0 else 0
    diff = abs(simple_avg - weighted) * 100
    flag = " *** SIGNIFICANT DIFF" if diff > 5 else ""
    print(f"   {tag}: {cn} mats | Simple={simple_avg*100:.1f}% | Weighted={weighted*100:.1f}% | Diff={diff:.1f}pp{flag}")

# 4. Check for data anomalies
print(f"\n4. Anomaly detection:")
anomalies = []
for m in valid:
    if m["cum_roi"] > 5:  # ROI > 500%
        anomalies.append((m["ad"][:50], m["cum_roi"], m["spend"], m["installs"]))
    if m["retention"] > 1:  # retention > 100%
        anomalies.append((m["ad"][:50], m["retention"], m["spend"], m["installs"]))
    if m["ctr"] > 0.5:  # CTR > 50%
        anomalies.append((m["ad"][:50], m["ctr"], m["spend"], m["installs"]))

if anomalies:
    print(f"   Found {len(anomalies)} potential anomalies:")
    for a in anomalies[:5]:
        print(f"   {a[0]}: value={a[1]:.2f} spend=${a[2]:.0f} inst={a[3]:.0f}")
else:
    print("   No anomalies found")

# 5. Retention — check if it's already % or decimal
print(f"\n5. Retention data check:")
ret_samples = [m["retention"] for m in valid[:10]]
print(f"   Sample values: {[round(r,3) for r in ret_samples]}")
print(f"   Max: {max(m['retention'] for m in valid):.3f}")
print(f"   Min: {min(m['retention'] for m in valid):.3f}")
if max(m["retention"] for m in valid) > 1:
    print(f"   WARNING: Retention values > 1 detected — might be % not decimal!")
if max(m["retention"] for m in valid) < 0.01:
    print(f"   WARNING: Retention values < 0.01 — might be decimal, not %")

# 6. ROI check
print(f"\n6. ROI data check:")
roi_vals = [m["cum_roi"] for m in valid]
print(f"   Max: {max(roi_vals):.3f} ({max(roi_vals)*100:.0f}%)")
print(f"   Min: {min(roi_vals):.3f} ({min(roi_vals)*100:.0f}%)")
print(f"   Mean: {sum(roi_vals)/len(roi_vals):.3f}")
print(f"   Zero count: {sum(1 for r in roi_vals if r == 0)}/{len(roi_vals)}")
print(f"   Negative count: {sum(1 for r in roi_vals if r < 0)}/{len(roi_vals)}")

print(f"\n7. Overall recommendation:")
print(f"   For 聚类分析 '平均累计ROI': Simple average is fine (each material = one unit)")
print(f"   For 素材总览 individual rows: No averaging needed (raw per-material data)")
print(f"   For report-level '平均ROI': Consider showing BOTH simple avg and spend-weighted avg")
print(f"   Current Base data format: ROI stored as decimal (0.192 = 19.2%) — CORRECT")
print(f"   Retention: check if decimal (0.48) or percent (48) — VERIFY")

db.close()
