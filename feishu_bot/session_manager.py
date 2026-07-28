"""
会话管理器（参考 oc-feishu-bot 的 session-manager.js）

Per-Chat 绑定：每个群一个绑定文件
- bindings/<chatID>.json = {chatID, sessionID, lastActivity, ...}
- 内存缓存 + 磁盘持久化
- 反向索引：sessionID → chatID
"""

import json
import os
import time
from pathlib import Path
from typing import Any

import logging

logger = logging.getLogger("mova_feishu_bot")

BINDINGS_DIR = Path.home() / ".mova" / "feishu-bindings"


def _ensure_dir():
    BINDINGS_DIR.mkdir(parents=True, exist_ok=True)


def _chat_path(chat_id: str) -> Path:
    """获取绑定文件路径（防路径穿越）"""
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in str(chat_id))
    return BINDINGS_DIR / f"{safe}.json"


def _atomic_write(path: Path, data: str):
    """原子写入"""
    tmp = path.with_suffix(f".tmp-{os.getpid()}-{int(time.time())}")
    tmp.write_text(data, encoding="utf-8")
    try:
        tmp.rename(path)
    except Exception:
        try:
            path.write_text(data, encoding="utf-8")
        except Exception:
            pass


class SessionManager:
    """会话管理器"""
    
    def __init__(self):
        _ensure_dir()
        self._cache: dict[str, dict] = {}  # chatID → binding
        self._reverse: dict[str, str] = {}  # sessionID → chatID
        self._load_all()
    
    def _load_all(self):
        """启动时全量加载"""
        n = 0
        for f in BINDINGS_DIR.iterdir():
            if not f.name.endswith(".json"):
                continue
            try:
                b = json.loads(f.read_text(encoding="utf-8"))
                chat_id = b.get("chatID")
                session_id = b.get("sessionID")
                if chat_id and session_id:
                    self._cache[chat_id] = {
                        "sessionID": session_id,
                        "directory": b.get("directory", ""),
                        "lastSyncTime": b.get("lastSyncTime", int(time.time() * 1000)),
                        "lastActivity": b.get("lastActivity", 0),
                    }
                    self._reverse[session_id] = chat_id
                    n += 1
            except Exception:
                continue
        logger.info(f"[SessionManager] 已加载 {n} 条群↔会话绑定")
    
    def get_binding(self, chat_id: str) -> dict | None:
        """获取绑定"""
        cached = self._cache.get(chat_id)
        if cached:
            # 验证文件仍存在
            if not _chat_path(chat_id).exists():
                self._cache.pop(chat_id, None)
                if cached.get("sessionID"):
                    self._reverse.pop(cached["sessionID"], None)
                return None
            return cached
        
        try:
            b = json.loads(_chat_path(chat_id).read_text(encoding="utf-8"))
            result = {
                "sessionID": b.get("sessionID"),
                "directory": b.get("directory", ""),
                "lastSyncTime": b.get("lastSyncTime", int(time.time() * 1000)),
                "lastActivity": b.get("lastActivity", 0),
            }
            self._cache[chat_id] = result
            return result
        except Exception:
            return None
    
    def set_binding(self, chat_id: str, session_id: str, directory: str = "", auto_created: bool = False):
        """设置绑定"""
        _ensure_dir()
        old = self._cache.get(chat_id)
        if old and old.get("sessionID") and old["sessionID"] != session_id:
            self._reverse.pop(old["sessionID"], None)
        
        now = int(time.time() * 1000)
        last_sync = now
        if old and old.get("sessionID") == session_id:
            last_sync = old.get("lastSyncTime", now)
        
        data = {
            "chatID": chat_id,
            "sessionID": session_id,
            "directory": directory or (old or {}).get("directory", ""),
            "lastActivity": now,
            "lastSyncTime": last_sync,
            "autoCreated": auto_created,
        }
        _atomic_write(_chat_path(chat_id), json.dumps(data, ensure_ascii=False, indent=2))
        self._cache[chat_id] = data
        self._reverse[session_id] = chat_id
    
    def clear_binding(self, chat_id: str):
        """解除绑定"""
        old = self._cache.pop(chat_id, None)
        if old and old.get("sessionID"):
            self._reverse.pop(old["sessionID"], None)
        try:
            _chat_path(chat_id).unlink()
        except Exception:
            pass
    
    def touch_binding(self, chat_id: str):
        """刷新活跃时间（只更新内存，减少磁盘 IO）"""
        cached = self._cache.get(chat_id)
        if cached:
            cached["lastActivity"] = int(time.time() * 1000)
    
    def get_chat_by_session(self, session_id: str) -> str | None:
        """通过 sessionID 获取 chatID"""
        cached = self._reverse.get(session_id)
        if cached:
            if _chat_path(cached).exists():
                return cached
            self._reverse.pop(session_id, None)
            self._cache.pop(cached, None)
        
        # 回退到全量扫描
        for f in BINDINGS_DIR.iterdir():
            if not f.name.endswith(".json"):
                continue
            try:
                b = json.loads(f.read_text(encoding="utf-8"))
                if b.get("sessionID") == session_id:
                    self._reverse[session_id] = b["chatID"]
                    return b["chatID"]
            except Exception:
                continue
        return None
    
    def get_all_bindings(self) -> list[dict]:
        """获取所有绑定"""
        result = []
        for f in BINDINGS_DIR.iterdir():
            if not f.name.endswith(".json"):
                continue
            try:
                b = json.loads(f.read_text(encoding="utf-8"))
                if b.get("chatID") and b.get("sessionID"):
                    result.append(b)
            except Exception:
                continue
        return result
    
    def cleanup_expired(self, ttl_ms: int = 86400000):
        """清理过期绑定（默认 24h）"""
        now = int(time.time() * 1000)
        expired = []
        for b in self.get_all_bindings():
            last_activity = b.get("lastActivity", 0)
            if last_activity and now - last_activity > ttl_ms:
                expired.append(b["chatID"])
        
        for chat_id in expired:
            self.clear_binding(chat_id)
            logger.info(f"[SessionManager] 清理过期绑定 {chat_id}")
        
        return len(expired)
