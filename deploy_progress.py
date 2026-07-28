import os, paramiko, time, requests, json
from pathlib import Path

# ── NocoDB 建日志表 ──
H = {'xc-token': os.environ.get('NOCODB_TOKEN', ''), 'Content-Type': 'application/json'}
BASE = os.environ.get('NOCODB_BASE', 'http://localhost:8081')
BASE_ID = os.environ.get('NOCODB_BASE_ID', '')

print("=== 创建日志表 ===")
r = requests.post(f'{BASE}/api/v2/meta/bases/{BASE_ID}/tables', headers=H, json={
    'table_name': '运行日志', 'title': '运行日志',
    'columns': [
        {'title': '时间', 'uidt': 'SingleLineText'},
        {'title': '操作', 'uidt': 'SingleLineText'},
        {'title': '详情', 'uidt': 'LongText'},
        {'title': '状态', 'uidt': 'SingleLineText'},
        {'title': '耗时', 'uidt': 'SingleLineText'},
    ]
}, timeout=30)
if r.ok:
    tid = r.json()['id']
    print(f"日志表: {tid}")
else:
    print(f"失败: {r.text[:200]}")
    tid = None

# ── 部署到 ECS ──
print("\n=== 部署到 ECS ===")
ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(os.environ.get("ECS_HOST", "localhost"), username="root", password=os.environ.get("ECS_PASSWORD", ""), timeout=10)
print("已连接")

# 上传 progress_server.py
sftp = ssh.open_sftp()
ROOT = Path(__file__).resolve().parent
sftp.put(str(ROOT / "progress_server.py"), "/root/nocodb-素材库/progress_server.py")
print("已上传 progress_server.py")
if tid:
    sftp.put(str(ROOT / ".env"), "/root/nocodb-素材库/.env")
    print("已上传 .env")

sftp.close()
ssh.exec_command("supervisorctl restart mova-progress")
ssh.close()
print("已重启")
