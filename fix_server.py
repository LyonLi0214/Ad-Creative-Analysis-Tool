import paramiko, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("[SERVER_IP]", username="root", password="[REDACTED]", timeout=10)

# 检查进程
stdin, stdout, stderr = ssh.exec_command("ps aux | grep progress_server | grep -v grep", timeout=10)
print(f"进程: {stdout.read().decode().strip()}")

# 检查日志
stdin, stdout, stderr = ssh.exec_command("tail -20 /root/nocodb-素材库/logs/progress.log", timeout=10)
print(f"日志:\n{stdout.read().decode().strip()}")

# 重启
ssh.exec_command("pkill -f progress_server.py 2>/dev/null")
time.sleep(1)
transport = ssh.get_transport()
channel = transport.open_session()
channel.exec_command("cd /root/nocodb-素材库 && nohup python3 progress_server.py --port 8080 > logs/progress.log 2>&1 &")
time.sleep(3)
channel.close()

# 验证
stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost:8080/health", timeout=10)
print(f"\n健康检查: {stdout.read().decode().strip()}")

stdin, stdout, stderr = ssh.exec_command("curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/report", timeout=10)
print(f"报告页面: {stdout.read().decode().strip()}")

ssh.close()
