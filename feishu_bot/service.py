import os
"""Always-on Feishu long-connection bot backed by Claude Code.

参考 oc-feishu-bot 架构优化：
- 文件任务队列（重启不丢任务）
- Per-Chat 会话绑定（按群隔离）
- 原子文件操作（避免写一半崩溃）
- API 指数退避重试
- Map 容量淘汰（防内存泄漏）

AI 引擎：本地 Claude CLI（claude --print），已替换 Mimo API。
"""
from __future__ import annotations
import argparse, json, logging, logging.handlers, os, queue, re, sys, threading, time
from datetime import datetime
from pathlib import Path
from typing import Any
import requests

try:
    from feishu_bot.claude_adapter import call_claude
    from feishu_bot.report_engine import ReportEngine
    from feishu_bot.tools import BUILD_ID, FeishuBaseTools, ToolError, ToolRegistry, execute_base_request, knowledge_context, parse_tool_request
    from feishu_bot.session_manager import SessionManager
    from feishu_bot.utils import BoundedMap, atomic_write_json, retry_with_backoff, safe_json_parse
except ModuleNotFoundError:
    from claude_adapter import call_claude
    from report_engine import ReportEngine
    from tools import BUILD_ID, FeishuBaseTools, ToolError, ToolRegistry, execute_base_request, knowledge_context, parse_tool_request
    from session_manager import SessionManager
    from utils import BoundedMap, atomic_write_json, retry_with_backoff, safe_json_parse

ROOT = Path(__file__).resolve().parents[1]
IDENTITY_PATH = Path(__file__).with_name("机器人身份.md")
SECRET_PATH = ROOT / ".env"
PROJECT_ENV_PATH = ROOT / ".env"
LOG_PATH = ROOT / "logs" / "feishu_bot.log"
LOCK_PATH = ROOT / "logs" / "feishu_bot.lock"
STATE_PATH = ROOT / "logs" / "feishu_bot_state.json"
UPGRADE_NOTIFIED = ROOT / "logs" / ".claude_upgrade_notified"
_LOCK_HANDLE = None

def acquire_single_instance() -> bool:
    global _LOCK_HANDLE
    if sys.platform == "win32":
        import msvcrt
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a+b")
    if handle.tell() == 0:
        handle.write(b"0")
        handle.flush()
    handle.seek(0)
    try:
        if sys.platform == "win32":
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, ImportError):
        handle.close()
        return False
    _LOCK_HANDLE = handle
    return True

def load_env_file(path: Path, overwrite: bool = False) -> None:
    if not path.is_file(): return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line: continue
        key, value = line.split("=", 1); key, value = key.strip(), value.strip()
        if key and (overwrite or key not in os.environ): os.environ[key] = value

def load_configuration() -> None:
    load_env_file(PROJECT_ENV_PATH)
    load_env_file(SECRET_PATH, overwrite=True)

def setup_logging() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("mova_feishu_bot"); logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        fh = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=5*1024*1024, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt); logger.addHandler(fh)
        sh = logging.StreamHandler(sys.stdout); sh.setFormatter(fmt); logger.addHandler(sh)
    return logger

LOGGER = setup_logging()

def model_config() -> tuple[str, str, str]:
    """保留用于健康检查的模型配置信息。"""
    return "claude-local", "claude-cli", "Claude (via claude --print)"


def required_status() -> dict[str, bool]:
    return {
        "FEISHU_APP_ID": bool(os.environ.get("FEISHU_APP_ID", "").strip()),
        "FEISHU_APP_SECRET": bool(os.environ.get("FEISHU_APP_SECRET", "").strip()),
        "CLAUDE_CLI_AVAILABLE": Path(os.environ.get("CLAUDE_CLI_PATH", "claude")).exists(),
        "IDENTITY_MD": IDENTITY_PATH.is_file(),
    }

def call_llm(system_prompt: str, messages: list[dict[str, str]]) -> str:
    """统一的 LLM 调用入口 — 当前使用 Claude CLI。"""
    return call_claude(system_prompt, messages)

def clean_text(content: str) -> str:
    try:
        text = str(json.loads(content).get("text", ""))
    except (json.JSONDecodeError, AttributeError):
        return ""
    return re.sub(r"@_user_\d+", "", text).strip()

class BotRuntime:
    def __init__(self) -> None:
        import lark_oapi as lark
        self.lark = lark
        self.app_id = os.environ["FEISHU_APP_ID"]
        self.app_secret = os.environ["FEISHU_APP_SECRET"]
        self.identity = IDENTITY_PATH.read_text(encoding="utf-8") + "\n\n## 当前游戏与 AppsFlyer 知识库\n" + json.dumps(knowledge_context(), ensure_ascii=False)
        
        # 使用有容量上限的 Map（防内存泄漏）
        self.history = BoundedMap(max_size=500)  # chat_id -> deque
        self.last_tool_result = BoundedMap(max_size=500)  # chat_id -> dict
        
        # 消息去重
        self.seen = set()
        self.seen_lock = threading.Lock()
        
        # Per-Chat 会话管理器
        self.session_mgr = SessionManager()
        
        # 飞书客户端
        self.client = lark.Client.builder().app_id(self.app_id).app_secret(self.app_secret).build()
        self.tools = ToolRegistry(FeishuBaseTools(self.app_id, self.app_secret), call_llm)
        self.report = ReportEngine()
        self.state = self._load_state()
        
        # 消息队列（内存，用于实时消息）
        self.jobs: queue.Queue[dict[str, str]] = queue.Queue(maxsize=100)
        
        # 定期清理过期绑定
        threading.Thread(target=self._cleanup_loop, name="cleanup", daemon=True).start()

    def _cleanup_loop(self):
        """定期清理过期绑定"""
        while True:
            try:
                time.sleep(3600)  # 每小时清理一次
                count = self.session_mgr.cleanup_expired(ttl_ms=86400000)  # 24h
                if count > 0:
                    LOGGER.info(f"清理了 {count} 条过期绑定")
            except Exception:
                LOGGER.exception("cleanup failed")

    def _load_state(self) -> dict[str, Any]:
        if STATE_PATH.is_file():
            try:
                return json.loads(STATE_PATH.read_text(encoding="utf-8"))
            except Exception:
                LOGGER.exception("state load failed")
        return {"default_chat_id": "", "last_daily_report": ""}

    def _save_state(self) -> None:
        atomic_write_json(STATE_PATH, self.state)

    def _get_history(self, chat_id: str) -> list[dict]:
        """获取历史记录（从 BoundedMap）"""
        dq = self.history.get(chat_id)
        if dq is None:
            from collections import deque
            dq = deque(maxlen=8)
            self.history.set(chat_id, dq)
        return list(dq)

    def _append_history(self, chat_id: str, role: str, content: str):
        """追加历史记录"""
        dq = self.history.get(chat_id)
        if dq is None:
            from collections import deque
            dq = deque(maxlen=8)
            self.history.set(chat_id, dq)
        dq.append({"role": role, "content": content})

    def mark_seen(self, message_id: str) -> bool:
        with self.seen_lock:
            if message_id in self.seen:
                return False
            # 防止 seen 无限增长
            if len(self.seen) > 1000:
                self.seen = set(list(self.seen)[500:])
            self.seen.add(message_id)
            return True

    def on_message(self, data: Any) -> None:
        try:
            m = data.event.message
            mid = m.message_id
            if not mid or not self.mark_seen(mid):
                return
            text = clean_text(m.content) if m.message_type == "text" else "[非文本消息]"
            if not text:
                return
            if m.chat_id and self.state.get("default_chat_id") != m.chat_id:
                self.state["default_chat_id"] = m.chat_id
                self._save_state()
            self.jobs.put_nowait({"message_id": mid, "chat_id": m.chat_id, "text": text})
            LOGGER.info("message queued id=%s chat=%s chars=%d", mid, m.chat_id, len(text))
        except queue.Full:
            LOGGER.error("message queue is full")
        except Exception:
            LOGGER.exception("message handler failed")

    def reply(self, message_id: str, text: str) -> None:
        from lark_oapi.api.im.v1 import ReplyMessageRequest, ReplyMessageRequestBody
        
        def _reply():
            safe = (text or "暂时没有生成有效回复。")[:8000]
            req = (ReplyMessageRequest.builder()
                   .message_id(message_id)
                   .request_body(ReplyMessageRequestBody.builder()
                                .content(json.dumps({"text": safe}, ensure_ascii=False))
                                .msg_type("text")
                                .build())
                   .build())
            resp = self.client.im.v1.message.reply(req)
            if not resp.success():
                raise RuntimeError(f"Feishu reply failed code={resp.code} msg={resp.msg} log_id={resp.get_log_id()}")
        
        retry_with_backoff(_reply, retries=2)

    def send_to_chat(self, text: str, chat_id: str | None = None) -> None:
        from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
        target = chat_id or self.state.get("default_chat_id")
        if not target:
            raise ToolError("还没有记录默认通知群，请先在群里 @ 我一次")
        
        def _send():
            body = (CreateMessageRequestBody.builder()
                    .receive_id(target)
                    .msg_type("text")
                    .content(json.dumps({"text": text[:8000]}, ensure_ascii=False))
                    .build())
            req = CreateMessageRequest.builder().receive_id_type("chat_id").request_body(body).build()
            resp = self.client.im.v1.message.create(req)
            if not resp.success():
                raise ToolError(f"主动发送失败 code={resp.code} msg={resp.msg}")
        
        retry_with_backoff(_send, retries=2)

    def process_job(self, job: dict[str, str]) -> None:
        text = job["text"]
        cid = job["chat_id"]
        
        if text == "[非文本消息]":
            self.reply(job["message_id"], "目前先支持文本消息。")
            return
        
        if text.lower() in {"/health", "健康检查", "能力状态"}:
            status = self.tools.execute("system_status", {})
            self.reply(job["message_id"], "Bot在线。构建版本：" + status["build"] + "；Base能力：" + "、".join(status["capabilities"]) + "。")
            return
        
        if text.lower() in {"帮助", "help", "/help"}:
            self.reply(job["message_id"], (
                "📋 可用命令：\n"
                "/日报 — 查看今日投放日报\n"
                "/周报 — 查看本周投放周报\n"
                "/趋势 — 最近 7 天走势\n"
                "/概况 — 系统总览\n"
                "也可以直接提问，如「昨天总安装多少」"
            ))
            return

        # 报告命令 — /日报 触发全流程管道
        if text.lower() in {"/日报", "/daily"}:
            self.reply(job["message_id"], "日报管道启动中... 进度会在群里同步。")
            import subprocess as _sp
            def _run():
                _sp.run([
                    os.environ.get("MOVABOT_PYTHON", sys.executable),
                    str(ROOT / "pipeline_commander.py"),
                    "--chat-id", cid,
                ], timeout=300)
            threading.Thread(target=_run, name="pipeline-daily", daemon=True).start()
            return
        if text.lower() in {"/周报", "/weekly"}:
            self.reply(job["message_id"], self.report.build_weekly_report())
            return
        if text.lower() in {"/趋势", "/trend"}:
            self.reply(job["message_id"], self.report.build_trend(7))
            return
        if text.lower() in {"/概况", "/overview"}:
            self.reply(job["message_id"], self.report.build_overview())
            return
        
        history = self._get_history(cid)
        
        # 检查是否是查询上一轮结果
        if re.search(r"(?:结果|进度|完成了吗|做好了吗|成功了吗|回传)", text) and self.last_tool_result.get(cid):
            prior = self.last_tool_result.get(cid)
            answer = "上一轮真实工具结果：" + json.dumps(prior, ensure_ascii=False)
            self.reply(job["message_id"], answer)
            self._append_history(cid, "user", text)
            self._append_history(cid, "assistant", answer)
            return
        
        # 路由
        route = parse_tool_request(text, call_llm, history)
        LOGGER.info("route action=%s reason=%s", route.get("action"), route.get("reason"))
        
        if route["action"] != "chat":
            try:
                if route["action"] == "base_agent":
                    result = execute_base_request(text, self.tools.base, call_llm, history)
                elif route["action"] == "proactive_test":
                    self.send_to_chat("MOVA 主动消息链路测试成功。后续可用于同步完成、异常提醒、日报和周报。")
                    result = {"sent": True, "target": "current_chat"}
                else:
                    result = self.tools.execute(route["action"], route.get("params", {}))
                
                tool_prompt = self.identity + "\n\n你刚调用了受控工具。必须严格依据工具结果回答，标明数据来源和查询范围，不得补写不存在的数据。"
                
                if route["action"] == "base_agent" and result.get("operation") in {"create", "update", "noop"}:
                    verbs = {"create": "已创建", "update": "已更新", "noop": "数据已一致，无需重复写入"}
                    answer = f"{verbs[result['operation']]}。表：{result['table_name']}；记录ID：{result['record_id']}；匹配方式：{result['matched_by']}；变更字段：{'、'.join(result['changed_fields']) or '无'}。"
                else:
                    answer = call_llm(tool_prompt, [
                        {"role": "user", "content": text},
                        {"role": "system", "content": "工具结果：" + json.dumps(result, ensure_ascii=False)}
                    ])
                
                self.last_tool_result.set(cid, {"action": route["action"], "result": result})
                LOGGER.info("tool completed action=%s", route["action"])
            except ToolError as e:
                answer = f"操作未能完成：{str(e)}"
            except Exception as e:
                LOGGER.exception("tool execution failed")
                answer = f"工具执行出错：{str(e)[:200]}"
        else:
            chat_prompt = self.identity + "\n\n## 本轮执行状态\n本轮没有调用任何工具。禁止声称已查看字段、正在执行、已经写入或稍后会自行完成；需要执行动作时，明确说明本轮未执行。"
            answer = call_llm(chat_prompt, [*history, {"role": "user", "content": text}])
        
        self.reply(job["message_id"], answer)
        self._append_history(cid, "user", text)
        self._append_history(cid, "assistant", answer)

    def worker(self) -> None:
        while True:
            job = self.jobs.get()
            try:
                self.process_job(job)
                LOGGER.info("message completed id=%s", job["message_id"])
            except ToolError as e:
                LOGGER.warning("tool error id=%s: %s", job.get("message_id"), str(e))
                try:
                    self.reply(job["message_id"], f"操作未能完成：{str(e)}")
                except Exception:
                    LOGGER.exception("error reply failed")
            except Exception:
                LOGGER.exception("message processing failed id=%s", job.get("message_id"))
                try:
                    self.reply(job["message_id"], "处理消息时出现异常，已记录日志，请稍后重试。")
                except Exception:
                    LOGGER.exception("error reply failed")
            finally:
                self.jobs.task_done()

    def scheduler(self) -> None:
        """定时推送：日报 09:30 / 周报 周一 10:00."""
        while True:
            try:
                now = datetime.now()
                today = now.date().isoformat()
                weekday = now.weekday()  # 0=Monday

                # 日报 09:30
                if (now.strftime("%H:%M") == "09:30"
                        and self.state.get("last_daily_report") != today
                        and self.state.get("default_chat_id")):
                    report_text = self.report.build_daily_report()
                    self.send_to_chat(report_text)
                    self.state["last_daily_report"] = today
                    self._save_state()
                    LOGGER.info("daily report sent")

                # 周报 周一 10:00
                if (weekday == 0 and now.strftime("%H:%M") == "10:00"
                        and self.state.get("last_weekly_report") != today
                        and self.state.get("default_chat_id")):
                    report_text = self.report.build_weekly_report()
                    self.send_to_chat(report_text)
                    self.state["last_weekly_report"] = today
                    self._save_state()
                    LOGGER.info("weekly report sent")

            except Exception:
                LOGGER.exception("scheduler failed")
            time.sleep(30)

    def _notify_upgrade(self) -> None:
        """Send a one-time notification when the Bot upgrades to Claude."""
        if UPGRADE_NOTIFIED.exists():
            return
        # Wait for the WebSocket to fully establish and state to settle
        time.sleep(8)
        chat_id = self.state.get("default_chat_id")
        if not chat_id:
            LOGGER.info("upgrade notification skipped — no default_chat_id yet")
            return
        try:
            self.send_to_chat(
                "🎉 MOVA Bot 已升级！AI 引擎已从 Mimo 切换为 Claude（本地 CLI），"
                "分析能力和对话质量已提升。如有任何问题请 @ 我反馈。",
                chat_id=chat_id,
            )
            UPGRADE_NOTIFIED.write_text(
                f"notified at {datetime.now().isoformat()}\nchat_id={chat_id}\n",
                encoding="utf-8",
            )
            LOGGER.info("upgrade notification sent to chat_id=%s", chat_id)
        except Exception:
            LOGGER.exception("upgrade notification failed")

    def run(self) -> None:
        lark = self.lark
        threading.Thread(target=self.worker, name="mova-bot-worker", daemon=True).start()
        threading.Thread(target=self.scheduler, name="mova-bot-scheduler", daemon=True).start()
        # One-time upgrade notification (if applicable)
        threading.Thread(target=self._notify_upgrade, name="mova-upgrade-notify", daemon=True).start()
        handler = (lark.EventDispatcherHandler.builder("", "")
                   .register_p2_im_message_receive_v1(self.on_message)
                   .build())
        ws = lark.ws.Client(self.app_id, self.app_secret, event_handler=handler, log_level=lark.LogLevel.INFO)
        LOGGER.info("starting Feishu long connection build=%s", BUILD_ID)
        ws.start()

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--test-llm", action="store_true")
    args = parser.parse_args()
    load_configuration()
    status = required_status()

    if args.check:
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return 0 if all(status.values()) else 2

    if not all(status.values()):
        raise RuntimeError("missing configuration: " + ", ".join(k for k, v in status.items() if not v))

    if args.test_llm:
        print(call_llm(IDENTITY_PATH.read_text(encoding="utf-8"), [{"role": "user", "content": "用一句话介绍你是谁，不要提及任何密钥或内部配置。"}]))
        return 0
    
    if not acquire_single_instance():
        LOGGER.info("another Feishu bot instance is already running")
        return 0
    
    BotRuntime().run()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
