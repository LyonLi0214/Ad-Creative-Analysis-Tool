"""
文件任务队列（参考 oc-feishu-bot 的 taskqueue.js）

解决：内存队列重启丢任务的问题。
机制：
- 待执行: tasks/<id>.json
- 已认领: tasks/<id>.<pid>.claimed
- 完成后: 删除文件
- 原子写入: 先写临时文件再 rename
"""

import json
import os
import random
import string
import time
from pathlib import Path
from typing import Any, Callable

import logging

logger = logging.getLogger("mova_feishu_bot")

TASKS_DIR = Path.home() / ".mova" / "feishu-tasks"
CLAIM_TIMEOUT_MS = 300000  # 5 分钟
POLL_INTERVAL_MS = 1000  # 1 秒


def _ensure_dir():
    TASKS_DIR.mkdir(parents=True, exist_ok=True)


def _new_id() -> str:
    ts = int(time.time() * 1000)
    rand = random.randint(0, 0xFFFFFF)
    return f"{ts:x}-{rand:06x}"


def _atomic_write(path: Path, data: str):
    """原子写入：先写临时文件再 rename"""
    tmp = path.with_suffix(f".tmp-{os.getpid()}-{random.randint(0, 9999):04d}")
    tmp.write_text(data, encoding="utf-8")
    tmp.rename(path)


def _fingerprint(task: dict) -> str:
    """任务指纹：用于幂等检查"""
    sid = task.get("session_id", "")
    task_type = task.get("type", "")
    if task_type == "prompt":
        text = task.get("text", "")[:80]
        return f"{sid}|prompt|{text}"
    if task_type == "reply":
        return f"{sid}|reply"
    if task_type == "stop":
        return f"{sid}|stop"
    return ""


def enqueue(task: dict) -> str:
    """入队一个任务"""
    _ensure_dir()
    fp = _fingerprint(task)
    if fp:
        # 幂等检查：扫描待执行和已认领任务
        for f in TASKS_DIR.iterdir():
            if not (f.suffix == ".json" or f.suffix == ".claimed"):
                continue
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
                if _fingerprint(t) == fp and time.time() * 1000 - t.get("createdAt", 0) < 120000:
                    logger.info(f"[TaskQueue] 幂等跳过(已有相同任务 {t.get('id')})")
                    return t.get("id", "")
            except Exception:
                continue
    
    task_id = task.get("id") or _new_id()
    full_task = {"id": task_id, "createdAt": int(time.time() * 1000), **task}
    path = TASKS_DIR / f"{task_id}.json"
    try:
        _atomic_write(path, json.dumps(full_task, ensure_ascii=False))
        logger.info(f"[TaskQueue] 入队 {task_id} type={task.get('type')}")
    except Exception as e:
        logger.error(f"[TaskQueue] 入队失败: {e}")
    return task_id


def claim_next(my_directory: str, filter_fn: Callable[[dict], bool] | None = None) -> dict | None:
    """认领下一个匹配的任务"""
    _ensure_dir()
    _requeue_stale()
    
    for f in sorted(TASKS_DIR.iterdir()):
        if not f.name.endswith(".json") or f.name.endswith(".tmp"):
            continue
        try:
            task = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        
        task_dir = task.get("directory")
        if task_dir and task_dir != my_directory:
            continue
        if filter_fn and not filter_fn(task):
            continue
        
        # 原子认领
        claim_path = TASKS_DIR / f"{task['id']}.{os.getpid()}.claimed"
        try:
            f.rename(claim_path)
            claimed = {**task, "claimedBy": os.getpid(), "claimedAt": int(time.time() * 1000)}
            _atomic_write(claim_path, json.dumps(claimed, ensure_ascii=False))
            logger.info(f"[TaskQueue] 认领 {task['id']} type={task.get('type')}")
            return claimed
        except Exception:
            continue
    return None


def complete(task_id: str):
    """完成任务（删除认领文件）"""
    claim_path = TASKS_DIR / f"{task_id}.{os.getpid()}.claimed"
    try:
        claim_path.unlink()
    except Exception:
        pass


def _requeue_stale():
    """回收陈旧任务"""
    now = int(time.time() * 1000)
    
    for f in list(TASKS_DIR.iterdir()):
        # 回收孤儿 .json（30分钟无认领）
        if f.name.endswith(".json"):
            try:
                t = json.loads(f.read_text(encoding="utf-8"))
                created = t.get("createdAt", 0)
                if created and now - created > 1800000:  # 30 分钟
                    f.unlink()
                    logger.warning(f"[TaskQueue] 回收孤儿任务 {f.name}")
            except Exception:
                continue
        
        # 回收陈旧 .claimed（认领进程已死）
        if not f.name.endswith(".claimed"):
            continue
        try:
            t = json.loads(f.read_text(encoding="utf-8"))
            claimed_at = t.get("claimedAt", 0)
            claimed_by = t.get("claimedBy", 0)
            if now - claimed_at <= CLAIM_TIMEOUT_MS:
                continue
            # 检查进程是否存活
            try:
                os.kill(claimed_by, 0)
                continue  # 进程还活着
            except OSError:
                pass
            # 进程已死，重新排队
            task_id = f.name.split(".")[0]
            target = TASKS_DIR / f"{task_id}.json"
            f.rename(target)
            logger.warning(f"[TaskQueue] 回收陈旧任务 {task_id}(认领进程 {claimed_by} 已死)")
        except Exception:
            continue


def start_poller(my_directory: str, handler: Callable[[dict], Any], filter_fn: Callable[[dict], bool] | None = None):
    """启动轮询"""
    import threading
    
    stopped = False
    in_flight = False
    
    def tick():
        nonlocal in_flight
        if stopped or in_flight:
            return
        in_flight = True
        try:
            task = claim_next(my_directory, filter_fn)
            if task:
                try:
                    handler(task)
                except Exception as e:
                    logger.error(f"[TaskQueue] 任务 {task.get('id')} 执行失败: {e}")
                finally:
                    complete(task.get("id", ""))
        except Exception as e:
            logger.error(f"[TaskQueue] 轮询错误: {e}")
        finally:
            in_flight = False
    
    def poll():
        while not stopped:
            tick()
            time.sleep(POLL_INTERVAL_MS / 1000)
    
    thread = threading.Thread(target=poll, name="taskqueue-poller", daemon=True)
    thread.start()
    return lambda: setattr(thread, "_stop", True)
