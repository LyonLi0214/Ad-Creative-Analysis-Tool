import paramiko, time, json

ssh = paramiko.SSHClient()
ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
ssh.connect("[SERVER_IP]", username="root", password="[REDACTED]", timeout=10)

# Create logs table using curl from localhost
cmd = '''curl -s -X POST http://localhost:8081/api/v2/meta/bases/pjg5uoh4uqz2xhp/tables \
  -H 'xc-token: [REDACTED]' \
  -H 'Content-Type: application/json' \
  -d '{"table_name":"运行日志","title":"运行日志","columns":[{"title":"时间","uidt":"SingleLineText"},{"title":"操作","uidt":"SingleLineText"},{"title":"详情","uidt":"LongText"},{"title":"状态","uidt":"SingleLineText"},{"title":"耗时","uidt":"SingleLineText"}]}' '''

stdin, stdout, stderr = ssh.exec_command(cmd, timeout=30)
result = stdout.read().decode().strip()
print(f"Create table: {result[:200]}")

if result:
    try:
        data = json.loads(result)
        tid = data.get('id')
        if tid:
            print(f"日志表 ID: {tid}")
            
            # Update .env
            sftp = ssh.open_sftp()
            with sftp.file("/root/nocodb-素材库/.env", "r") as f:
                env = f.read().decode()
            if 'NOCODB_TABLE_LOGS' not in env:
                env += f"NOCODB_TABLE_LOGS={tid}\n"
                with sftp.file("/root/nocodb-素材库/.env", "w") as f:
                    f.write(env)
                print("已更新 .env")
            sftp.close()
            
            # Restart Flask
            ssh.exec_command("pkill -f progress_server.py 2>/dev/null")
            time.sleep(1)
            transport = ssh.get_transport()
            channel = transport.open_session()
            channel.exec_command("cd /root/nocodb-素材库 && nohup python3 progress_server.py --port 8080 > logs/progress.log 2>&1 &")
            time.sleep(3)
            channel.close()
            print("已重启 Flask")
    except Exception as e:
        print(f"解析失败: {e}")

ssh.close()
print("\n完成")
