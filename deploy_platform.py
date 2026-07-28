import paramiko, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("[SERVER_IP]", username="root", password="[REDACTED]", timeout=10)

# 更新 nginx，把 /app/ 代理到新平台 8088
nginx_conf = """
server {
    listen 80;
    server_name _;

    # 进度面板 (Flask)
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }

    # NocoDB
    location /nc/ {
        proxy_pass http://127.0.0.1:8081/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }

    # 新决策平台
    location /app/ {
        proxy_pass http://127.0.0.1:8088/;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
"""

sftp = ssh.open_sftp()
with sftp.file("/etc/nginx/sites-available/progress", "w") as f:
    f.write(nginx_conf)
sftp.close()

# 测试并重载
stdin, stdout, stderr = ssh.exec_command("nginx -t 2>&1", timeout=10)
print(f"nginx: {stdout.read().decode().strip()}")

ssh.exec_command("systemctl reload nginx")
time.sleep(2)

# 验证
stdin, stdout, stderr = ssh.exec_command('curl -s -o /dev/null -w "%{http_code}" http://localhost/app/', timeout=10)
print(f"新平台 /app/: {stdout.read().decode().strip()}")

stdin, stdout, stderr = ssh.exec_command('curl -s -o /dev/null -w "%{http_code}" http://localhost/', timeout=10)
print(f"进度面板 /: {stdout.read().decode().strip()}")

stdin, stdout, stderr = ssh.exec_command('curl -s -o /dev/null -w "%{http_code}" http://localhost/nc/dashboard', timeout=10)
print(f"NocoDB /nc/: {stdout.read().decode().strip()}")

ssh.close()
print("\n完成")
