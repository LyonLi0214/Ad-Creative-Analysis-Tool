import paramiko, time

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("[SERVER_IP]", username="root", password="[REDACTED]", timeout=10)
print("已连接")

# 1. 安装 nginx
print("\n[1/5] 安装 nginx...")
stdin, stdout, stderr = ssh.exec_command("apt-get install -y nginx 2>&1 | tail -5", timeout=120)
print(stdout.read().decode().strip())

# 2. 停止 Flask (释放 8080)
print("\n[2/5] 停止 Flask...")
ssh.exec_command("pkill -f progress_server.py 2>/dev/null")
time.sleep(1)

# 3. 把 NocoDB 改到 8081 端口
print("\n[3/5] 重配 NocoDB 到 8081...")
ssh.exec_command("docker stop nocodb && docker rm nocodb")
time.sleep(2)
cmd = ("docker run -d --name nocodb "
       "-p 8081:8080 "
       "-v /opt/nocodb-data:/usr/app/data "
       "nocodb/nocodb:2026.06.2")
stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
print(f"Docker: {stdout.read().decode().strip()}")
time.sleep(3)

# 4. 配置 nginx 反向代理
print("\n[4/5] 配置 nginx...")
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
}
"""
sftp = ssh.open_sftp()
with sftp.file("/etc/nginx/sites-available/progress", "w") as f:
    f.write(nginx_conf)
ssh.exec_command("ln -sf /etc/nginx/sites-available/progress /etc/nginx/sites-enabled/")
ssh.exec_command("rm -f /etc/nginx/sites-enabled/default")
stdin, stdout, stderr = ssh.exec_command("nginx -t 2>&1", timeout=10)
print(f"nginx config: {stdout.read().decode().strip()}")

# 5. 启动 nginx + Flask
print("\n[5/5] 启动服务...")
ssh.exec_command("systemctl restart nginx")
time.sleep(1)

transport = ssh.get_transport()
channel = transport.open_session()
channel.exec_command("cd /root/nocodb-素材库 && nohup python3 progress_server.py --port 8080 > logs/progress.log 2>&1 &")
time.sleep(3)
channel.close()

# 验证
stdin, stdout, stderr = ssh.exec_command("curl -s -o /dev/null -w '%{http_code}' http://localhost/", timeout=10)
print(f"\n进度面板 (/:80): {stdout.read().decode().strip()}")

stdin, stdout, stderr = ssh.exec_command("curl -s -o /dev/null -w '%{http_code}' http://localhost:8081/dashboard", timeout=10)
print(f"NocoDB (:8081): {stdout.read().decode().strip()}")

stdin, stdout, stderr = ssh.exec_command("ss -tlnp | grep -E ':(80|8080|8081) '", timeout=10)
print(f"\n端口:\n{stdout.read().decode().strip()}")

ssh.close()
print("\n✅ 完成")
print("进度面板: http://[SERVER_IP]")
print("NocoDB: http://[SERVER_IP]:8081")
