#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""sync_excel_to_nocodb.py — 把 Excel 素材总览的数据同步到 NocoDB"""
import requests, time
from pathlib import Path
from openpyxl import load_workbook

load_env = lambda p: None  # .env already loaded by agent_tagger
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            import os
            os.environ.setdefault(k.strip(), v.strip())

import os
NOCODB_BASE = os.environ["NOCODB_BASE"]
NOCODB_TOKEN = os.environ["NOCODB_TOKEN"]
TABLE_ASSETS = os.environ["NOCODB_TABLE_ASSETS"]

H = {"xc-token": NOCODB_TOKEN, "Content-Type": "application/json"}
XLSX = Path(__file__).parent / "素材拆解分析模板.xlsx"

# 列映射：Excel表头 → NocoDB列名
COL_MAP = {
    "素材名称": "素材名称",
    "游戏名称": "游戏名称",
    "平台": "平台",
    "类型": "类型",
    "Campaign": "Campaign",
    "链接": "链接",
    "入库时间": "入库时间",
    "状态": "状态",
    "上传人": "上传人",
    "花费": "消耗($)",
    "点击率": "点击率",
    "安装": "安装",
    "转化率": "转化率",
    "单价": "CPI($)",
    "首充用户": "首充用户",
    "首充率": "首充率",
    "首日回收": "首日回收",
    "单个付费用户成本": "单个付费用户成本",
    "首日ROI": "D1_ROI",
    "累计付费": "累计付费",
    "累计ROI": "累计ROI",
    "次留": "次留",
    "时长(秒)": "时长(秒)",
    "分辨率": "分辨率",
    "备注": "备注",
}

def get_existing_names():
    """从 NocoDB 获取已有的素材名称"""
    names = set()
    offset = 0
    while True:
        url = f"{NOCODB_BASE}/api/v2/tables/{TABLE_ASSETS}/records"
        r = requests.get(url, headers=H, params={"limit": 100, "offset": offset}, timeout=30)
        r.raise_for_status()
        data = r.json()
        rows = data.get("list", [])
        if not rows:
            break
        for row in rows:
            name = row.get("素材名称", "")
            if name:
                names.add(str(name).strip())
        if len(rows) < 100:
            break
        offset += 100
    return names

# SingleSelect 值映射
TYPE_MAP = {"视频": "ShortVideo", "图片": "Image", "Banner": "Banner"}

def main():
    wb = load_workbook(str(XLSX), data_only=True)
    ws = wb["素材总览"]

    # 读表头
    headers = {}
    for col in range(1, ws.max_column + 1):
        h = ws.cell(row=1, column=col).value
        if h:
            headers[str(h).strip()] = col

    # 获取已有数据
    print("检查 NocoDB 已有数据...")
    existing = get_existing_names()
    print(f"  已有 {len(existing)} 条")

    # 构建待写入数据
    to_insert = []
    for row in range(2, ws.max_row + 1):
        name = ws.cell(row=row, column=headers.get("素材名称", 1)).value
        if not name:
            continue
        name = str(name).strip()
        if name in existing:
            continue

        record = {}
        for excel_col, noco_col in COL_MAP.items():
            col_idx = headers.get(excel_col)
            if col_idx:
                val = ws.cell(row=row, column=col_idx).value
                if val is not None:
                    record[noco_col] = val

        if record.get("素材名称"):
            # SingleSelect 值映射
            if record.get("类型") in TYPE_MAP:
                record["类型"] = TYPE_MAP[record["类型"]]
            to_insert.append(record)

    print(f"待同步: {len(to_insert)} 条新素材")

    if not to_insert:
        print("没有新数据需要同步")
        return

    # 批量写入（每批 20 条）
    ok = 0
    fail = 0
    for i in range(0, len(to_insert), 20):
        batch = to_insert[i:i+20]
        url = f"{NOCODB_BASE}/api/v2/tables/{TABLE_ASSETS}/records"
        r = requests.post(url, headers=H, json=batch, timeout=30)
        if r.ok:
            ok += len(batch)
            print(f"  批次 {i//20+1}: {len(batch)} 条写入成功")
        else:
            fail += len(batch)
            print(f"  批次 {i//20+1} 失败: {r.text[:200]}")
        time.sleep(0.5)

    print(f"\n同步完成: {ok} 成功, {fail} 失败")
    print(f"打开 http://[SERVER_IP] 查看")

if __name__ == "__main__":
    main()
