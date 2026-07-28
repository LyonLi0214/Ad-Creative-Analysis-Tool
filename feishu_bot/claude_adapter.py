"""Claude Code adapter — drop-in replacement for call_mimo.

Uses the local Claude CLI (`claude --print`) for all LLM calls.
Signature-compatible with call_mimo: (system_prompt, messages) -> str
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

logger = logging.getLogger("mova_feishu_bot")

CLAUDE_CLI = os.environ.get("CLAUDE_CLI_PATH", "claude")

# Ensure temp directory for system prompt files exists
_TMPDIR = Path(tempfile.gettempdir()) / "mova_claude"
_TMPDIR.mkdir(parents=True, exist_ok=True)

# Per-process counter to avoid temp-file collisions across threads
import threading
_COUNTER = 0
_COUNTER_LOCK = threading.Lock()

_RESPONSE_RULES = (
    "\n\n## 响应规则\n"
    "1. 只用纯文本回复，不要使用任何工具（Read/Write/Bash/Glob/Grep 等）。\n"
    "2. 需要输出 JSON 时直接输出 JSON 文本，不要加 markdown 代码块。\n"
    "3. 普通对话回复控制在 600 字以内，简洁直接，先结论后依据。\n"
    "4. 你不知道的内容直说不知道，不要编造。\n"
)


def _build_prompt(messages: list[dict[str, str]]) -> str:
    """Format conversation messages into a single prompt string.

    Uses a natural conversation transcript format that Claude handles well.
    Single user messages are passed through as-is.
    """
    if not messages:
        return ""

    # Single user message — return as-is (most common case for routing/planning)
    if len(messages) == 1 and messages[0].get("role") == "user":
        return messages[0].get("content", "")

    # Multi-message: natural conversation transcript
    parts: list[str] = []
    parts.append("以下是对话记录：")
    parts.append("")

    for i, msg in enumerate(messages):
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if role == "system":
            parts.append(f"【系统消息】{content}")
        elif role == "assistant":
            parts.append(f"Bot: {content}")
        elif role == "user":
            if i == len(messages) - 1:
                parts.append(f"用户（本轮需要回复的消息）: {content}")
            else:
                parts.append(f"用户: {content}")

    parts.append("")
    parts.append("请针对最后一轮「用户（本轮需要回复的消息）」进行回复。")
    return "\n".join(parts)


def call_claude(
    system_prompt: str,
    messages: list[dict[str, str]],
    *,
    retries: int = 1,
    timeout: int = 180,
) -> str:
    """Call Claude CLI as a drop-in replacement for call_mimo.

    Args:
        system_prompt: System-level instructions (identity, rules, context).
        messages: List of message dicts with 'role' and 'content'.
        retries: Number of retry attempts on failure.
        timeout: Subprocess timeout in seconds.

    Returns:
        Claude's response text (stripped).
    """
    global _COUNTER

    user_prompt = _build_prompt(messages)

    # Write system prompt + response rules to a temp file.
    with _COUNTER_LOCK:
        _COUNTER += 1
        _counter_val = _COUNTER
    system_file = _TMPDIR / f"system_{os.getpid()}_{threading.get_ident()}_{_counter_val}.txt"
    full_system = system_prompt + _RESPONSE_RULES
    system_file.write_text(full_system, encoding="utf-8")

    cmd = [
        CLAUDE_CLI,
        "--bare",
        "--print",
        "--output-format", "text",
        "--system-prompt-file", str(system_file),
        "-p", user_prompt,
    ]

    env = os.environ.copy()
    env["CLAUDE_CODE_SIMPLE"] = "1"

    last_error: str | None = None

    for attempt in range(retries + 1):
        logger.info(
            "claude call attempt=%d/%d prompt_len=%d sys_len=%d",
            attempt + 1, retries + 1, len(user_prompt), len(system_prompt),
        )
        t0 = time.time()

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=timeout,
                env=env,
                cwd=str(Path.home()),
            )
        except subprocess.TimeoutExpired:
            logger.error("claude call timed out after %ds", timeout)
            last_error = "Claude 响应超时，请稍后重试"
            if attempt < retries:
                time.sleep(2)
            continue
        except FileNotFoundError:
            raise RuntimeError(
                f"Claude CLI 未找到 ({CLAUDE_CLI})，请确认已安装"
            )

        elapsed = time.time() - t0
        stderr = result.stderr.strip() if result.stderr else ""

        if result.returncode != 0:
            logger.error(
                "claude exit=%d stderr=%s", result.returncode, stderr[:300]
            )
            last_error = f"Claude 调用失败 (exit={result.returncode})"

            if attempt < retries and (
                "rate" in stderr.lower()
                or "timeout" in stderr.lower()
                or "overloaded" in stderr.lower()
                or "503" in stderr
                or "429" in stderr
                or "5xx" in stderr
            ):
                delay = 2 ** attempt
                logger.info("retrying in %ds", delay)
                time.sleep(delay)
                continue
            raise RuntimeError(f"{last_error}: {stderr[:300]}")

        output = result.stdout.strip()

        # Strip markdown code fences (Claude sometimes wraps output)
        if output.startswith("```"):
            lines = output.splitlines()
            # Remove opening fence (with optional language tag)
            if lines[0].startswith("```"):
                lines = lines[1:]
            # Remove closing fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            output = "\n".join(lines).strip()

        logger.info(
            "claude completed chars=%d elapsed=%.1fs", len(output), elapsed
        )

        if not output:
            raise RuntimeError("Claude 返回了空响应")

        # Clean up temp file (best-effort)
        try:
            system_file.unlink(missing_ok=True)
        except Exception:
            pass

        return output

    raise RuntimeError(last_error or "Claude 调用失败")
