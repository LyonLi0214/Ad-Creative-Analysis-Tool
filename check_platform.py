import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("[SERVER_IP]", username="root", password="[REDACTED]", timeout=10)

# Health check
stdin, stdout, stderr = ssh.exec_command('curl -s -o /dev/null -w "%{http_code}" http://localhost:8088/', timeout=10)
print(f"平台首页: {stdout.read().decode().strip()}")

stdin, stdout, stderr = ssh.exec_command('curl -s http://localhost:8088/api/health 2>/dev/null || curl -s http://localhost:8088/health 2>/dev/null || echo "no-health-endpoint"', timeout=10)
print(f"健康检查: {stdout.read().decode().strip()[:200]}")

# Check disk usage
stdin, stdout, stderr = ssh.exec_command("df -h / | tail -1", timeout=10)
print(f"\n磁盘: {stdout.read().decode().strip()}")

# Check memory
stdin, stdout, stderr = ssh.exec_command("free -h | head -2", timeout=10)
print(f"内存:\n{stdout.read().decode().strip()}")

# Docker compose project location
stdin, stdout, stderr = ssh.exec_command("find / -name 'docker-compose.yml' -path '*/decision*' 2>/dev/null | head -5", timeout=10)
print(f"\n项目位置: {stdout.read().decode().strip()}")

ssh.close()
