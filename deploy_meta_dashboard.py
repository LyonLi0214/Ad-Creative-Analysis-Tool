from __future__ import annotations

import argparse
import ast
import json
import os
import re
from datetime import datetime
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parent
LOCAL_HTML = Path.home() / "Desktop" / "FB单素材表现看板" / "FB单素材Dashboard_v1.html"
LOG_DIR = ROOT / "logs" / "meta_dashboard"
REMOTE_DIR = "/var/www/meta-dashboard"
REMOTE_HTML = f"{REMOTE_DIR}/index.html"
NGINX_CONF = "/etc/nginx/sites-available/progress"


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def legacy_credentials() -> tuple[str, str, str]:
    """兼容读取现有部署脚本中的本机凭证，不执行该脚本。"""
    path = ROOT / "deploy_html.py"
    values: dict[str, str] = {}
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id in {"ECS_HOST", "ECS_USER", "ECS_PASS"} and isinstance(node.value, ast.Constant):
                values[node.targets[0].id] = str(node.value.value)
    return values.get("ECS_HOST", ""), values.get("ECS_USER", ""), values.get("ECS_PASS", "")


def credentials() -> tuple[str, str, str]:
    load_env(ROOT / ".env")
    host = os.getenv("DASHBOARD_ECS_HOST", "")
    user = os.getenv("DASHBOARD_ECS_USER", "")
    password = os.getenv("DASHBOARD_ECS_PASSWORD", "")
    if host and user and password:
        return host, user, password
    return legacy_credentials()


def run(ssh: paramiko.SSHClient, command: str) -> str:
    _, stdout, stderr = ssh.exec_command(command, timeout=30)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    if code:
        raise RuntimeError(f"remote command failed ({code}): {err[-500:]}")
    return out.strip()


def configure_nginx(ssh: paramiko.SSHClient) -> bool:
    sftp = ssh.open_sftp()
    with sftp.file(NGINX_CONF, "r") as fp:
        original = fp.read().decode("utf-8")
    if "location /meta-dashboard/" in original:
        sftp.close()
        return False
    block = """

    location = /meta-dashboard {
        return 301 /meta-dashboard/;
    }

    location /meta-dashboard/ {
        alias /var/www/meta-dashboard/;
        try_files $uri $uri/ /meta-dashboard/index.html;
        add_header Cache-Control "no-cache, no-store, must-revalidate";
    }
"""
    pos = original.rfind("}")
    if pos < 0:
        raise RuntimeError("nginx server config malformed")
    updated = original[:pos] + block + original[pos:]
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    backup = f"{NGINX_CONF}.bak.{stamp}"
    run(ssh, f"cp '{NGINX_CONF}' '{backup}'")
    with sftp.file(NGINX_CONF, "w") as fp:
        fp.write(updated)
    sftp.close()
    try:
        run(ssh, "nginx -t")
        run(ssh, "systemctl reload nginx")
    except Exception:
        run(ssh, f"cp '{backup}' '{NGINX_CONF}'")
        run(ssh, "systemctl reload nginx")
        raise
    return True


def deploy(configure: bool) -> dict:
    if not LOCAL_HTML.exists():
        raise FileNotFoundError(LOCAL_HTML)
    host, user, password = credentials()
    if not all((host, user, password)):
        raise RuntimeError("ECS credentials not configured")
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, username=user, password=password, timeout=15)
    try:
        run(ssh, f"mkdir -p '{REMOTE_DIR}'")
        sftp = ssh.open_sftp()
        temp = REMOTE_HTML + ".uploading"
        sftp.put(str(LOCAL_HTML), temp)
        sftp.close()
        run(ssh, f"chmod 644 '{temp}' && mv -f '{temp}' '{REMOTE_HTML}'")
        changed = configure_nginx(ssh) if configure else False
        status = run(ssh, "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1/meta-dashboard/")
        if status != "200":
            raise RuntimeError(f"dashboard health check returned HTTP {status}")
        return {"ok": True, "url": f"http://{host}/meta-dashboard/", "nginx_changed": changed,
                "bytes": LOCAL_HTML.stat().st_size, "deployed_at": datetime.now().astimezone().isoformat(timespec="seconds")}
    finally:
        ssh.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--configure-nginx", action="store_true")
    args = parser.parse_args()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        result = deploy(args.configure_nginx)
        code = 0
    except Exception as exc:
        result = {"ok": False, "error": str(exc), "deployed_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        code = 1
    log = LOG_DIR / f"deploy_{datetime.now():%Y-%m-%d}.json"
    log.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
