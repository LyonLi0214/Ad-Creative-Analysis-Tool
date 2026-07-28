import paramiko

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("[SERVER_IP]", username="root", password="[REDACTED]", timeout=10)

# Check actual HTML content
print("=== /app/ 返回内容 ===")
stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost/app/ | head -20", timeout=10)
print(stdout.read().decode().strip())

print("\n=== /nc/ 返回内容 ===")
stdin, stdout, stderr = ssh.exec_command("curl -s http://localhost/nc/ | head -20", timeout=10)
print(stdout.read().decode().strip())

# Check if React app needs base path
print("\n=== 新平台前端源码 ===")
stdin, stdout, stderr = ssh.exec_command("docker exec decision-platform-web-1 ls /usr/share/nginx/html/ 2>/dev/null || docker exec decision-platform-web-1 ls /var/www/html/ 2>/dev/null || echo 'not found'", timeout=10)
print(stdout.read().decode().strip())

# Check React index.html for base path
stdin, stdout, stderr = ssh.exec_command("docker exec decision-platform-web-1 cat /usr/share/nginx/html/index.html 2>/dev/null | head -10 || echo 'no index.html'", timeout=10)
print(f"\nReact index.html:\n{stdout.read().decode().strip()}")

ssh.close()
