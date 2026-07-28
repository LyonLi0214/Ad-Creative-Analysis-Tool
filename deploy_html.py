import paramiko

ECS_HOST = "[SERVER_IP]"
ECS_USER = "root"
ECS_PASS = "[REDACTED]"
REMOTE_DIR = "/root/nocodb-素材库"

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect(ECS_HOST, username=ECS_USER, password=ECS_PASS, timeout=10)
print("已连接")

# 创建 templates 目录
ssh.exec_command(f"mkdir -p {REMOTE_DIR}/templates")
import time; time.sleep(1)

# 上传 HTML
sftp = ssh.open_sftp()
ROOT = Path(__file__).resolve().parent
sftp.put(str(ROOT / "workflow_report.html"), f"{REMOTE_DIR}/templates/workflow_report.html")
print("已上传 workflow_report.html")
sftp.close()

# 更新 progress_server.py 添加 HTML 路由
# 先读取当前文件
sftp = ssh.open_sftp()
with sftp.file(f"{REMOTE_DIR}/progress_server.py", "r") as f:
    content = f.read().decode("utf-8")

# 在 @app.route("/health") 前添加 HTML 路由
if "workflow_report" not in content:
    route_code = '''
@app.route("/report")
def report():
    report_path = Path(__file__).parent / "templates" / "workflow_report.html"
    if report_path.exists():
        return Response(report_path.read_text(encoding="utf-8"), content_type="text/html; charset=utf-8")
    return "Report not found", 404

'''
    content = content.replace('@app.route("/health")', route_code + '@app.route("/health")')

with sftp.file(f"{REMOTE_DIR}/progress_server.py", "w") as f:
    f.write(content)
print("已更新 progress_server.py")
sftp.close()

# 重启服务
ssh.exec_command("pkill -f progress_server.py")
time.sleep(1)
transport = ssh.get_transport()
channel = transport.open_session()
channel.exec_command(f"cd {REMOTE_DIR} && nohup python3 progress_server.py --port 8080 > logs/progress.log 2>&1 &")
time.sleep(3)
channel.close()

# 验证
stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8080/health", timeout=10)
print(f"健康检查: {stdout.read().decode().strip()}")

ssh.close()
print(f"\n✅ 部署完成")
print(f"进度面板: http://{ECS_HOST}:8080")
print(f"工作流报告: http://{ECS_HOST}:8080/report")
