import paramiko, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("[SERVER_IP]", username="root", password="[REDACTED]", timeout=10)
print("已连接")

# 检查 nginx
stdin, stdout, stderr = ssh.exec_command("which nginx 2>/dev/null || echo 'not installed'", timeout=10)
nginx = stdout.read().decode().strip()
print(f"nginx: {nginx}")

# 检查 80 端口占用
stdin, stdout, stderr = ssh.exec_command("ss -tlnp | grep :80", timeout=10)
print(f"端口80: {stdout.read().decode().strip()}")

# 检查 docker
stdin, stdout, stderr = ssh.exec_command("docker ps --format '{{.Names}} {{.Ports}}'", timeout=10)
print(f"Docker: {stdout.read().decode().strip()}")

ssh.close()
