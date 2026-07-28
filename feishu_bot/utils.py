"""
工具函数（参考 oc-feishu-bot 的通用模式）

包含：
- 原子文件操作
- API 重试（指数退避）
- Map 容量淘汰
"""

import json
import os
import random
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

import logging

logger = logging.getLogger("mova_feishu_bot")

T = TypeVar("T")


def atomic_write(path: Path, data: str):
    """原子写入：先写临时文件再 rename"""
    tmp = path.with_suffix(f".tmp-{os.getpid()}-{random.randint(0, 9999):04d}")
    tmp.write_text(data, encoding="utf-8")
    try:
        tmp.rename(path)
    except Exception:
        try:
            path.write_text(data, encoding="utf-8")
        except Exception as e:
            logger.error(f"原子写入失败: {e}")


def atomic_write_json(path: Path, data: Any, indent: int = 2):
    """原子写入 JSON"""
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=indent))


def retry_with_backoff(
    fn: Callable[..., T],
    retries: int = 2,
    base_delay: float = 0.5,
    retryable_status: set[int] = {429, 500, 502, 503, 504},
) -> T:
    """指数退避重试
    
    只对 retryable_status 重试，其他错误直接抛出。
    """
    for attempt in range(retries + 1):
        try:
            return fn()
        except Exception as e:
            # 检查是否可重试
            status = getattr(e, "status_code", None) or getattr(e, "code", None)
            if status and status not in retryable_status:
                raise
            if attempt == retries:
                raise
            
            delay = base_delay * (2 ** attempt)
            logger.warning(f"调用失败({status}), {delay:.1f}s 后重试 {attempt + 1}/{retries}: {e}")
            time.sleep(delay)


class BoundedMap:
    """有容量上限的 Map，超过上限时淘汰最早插入的一半"""
    
    def __init__(self, max_size: int = 1000):
        self._data: dict[str, Any] = {}
        self._max_size = max_size
    
    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)
    
    def set(self, key: str, value: Any):
        self._data[key] = value
        self._evict()
    
    def pop(self, key: str, default: Any = None) -> Any:
        return self._data.pop(key, default)
    
    def __contains__(self, key: str) -> bool:
        return key in self._data
    
    def __len__(self) -> int:
        return len(self._data)
    
    def items(self):
        return self._data.items()
    
    def keys(self):
        return self._data.keys()
    
    def values(self):
        return self._data.values()
    
    def _evict(self):
        """淘汰超过上限时删除最早插入的一半"""
        if len(self._data) <= self._max_size:
            return
        half = len(self._data) // 2
        keys = list(self._data.keys())[:half]
        for k in keys:
            del self._data[k]
        logger.info(f"[BoundedMap] 淘汰 {half} 条记录")


def safe_json_parse(text: str) -> dict | None:
    """安全解析 JSON，返回 None 表示失败"""
    if not text or not text.strip():
        return None
    
    text = text.strip()
    
    # 尝试直接解析
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    
    # 尝试提取第一个 { 到最后一个 } 之间的内容
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            result = json.loads(text[start:end + 1])
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    
    # 尝试去掉 markdown 代码块
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
        try:
            result = json.loads(text)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
    
    return None
