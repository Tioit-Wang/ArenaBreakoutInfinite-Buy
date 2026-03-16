"""运行日志标准化与上下文格式化工具。"""

from __future__ import annotations

import re
import time
from typing import Dict

from .common import now_label

LOG_LEVELS: Dict[str, int] = {
    "detail": 10,
    "debug": 10,
    "info": 10,
    "error": 10,
}

_TIME_TAG_RE = re.compile(r"^【\d{2}:\d{2}:\d{2}】")
_LEVEL_PREFIX_RE = re.compile(r"^\[(DEBUG|INFO|ERROR)\]\s*", re.IGNORECASE)


def level_name(level: str) -> str:
    """兼容旧设置项，统一返回 detail。"""
    _ = level
    return "detail"


def extract_level_from_msg(msg: str) -> str:
    try:
        upper = str(msg or "").upper()
        if "【ERROR】" in upper or upper.startswith("[ERROR]"):
            return "error"
        if "【DEBUG】" in upper or upper.startswith("[DEBUG]"):
            return "debug"
        if "【INFO】" in upper or upper.startswith("[INFO]"):
            return "info"
    except Exception:
        pass
    return "detail"


def strip_level_tag(msg: str) -> str:
    text = str(msg or "").strip()
    if not text:
        return ""
    text = _LEVEL_PREFIX_RE.sub("", text).strip()
    for tag in ("【DEBUG】", "【INFO】", "【ERROR】"):
        text = text.replace(tag, "")
    return text.strip()


def build_context_message(
    context: str,
    *,
    phase: str | None = None,
    state: str | None = None,
    message: str | None = None,
) -> str:
    """构造统一的上下文日志文本。

    统一格式：
    - `<context>`
    - `<context> | 阶段=<phase>`
    - `<context> | 阶段=<phase> | 状态=<state> | <message>`
    """
    parts: list[str] = []
    raw_context = str(context or "").strip()
    if raw_context:
        parts.append(raw_context)
    raw_phase = str(phase or "").strip()
    if raw_phase:
        parts.append(f"阶段={raw_phase}")
    raw_state = str(state or "").strip()
    if raw_state:
        parts.append(f"状态={raw_state}")
    raw_message = str(message or "").strip()
    if raw_message:
        parts.append(raw_message)
    return " | ".join(parts)


def ensure_level_tag(msg: str, level: str, *, ts: float | None = None) -> str:
    """标准化运行日志。

    现阶段不再区分 debug/info/error 展示层级，统一输出最详细文本：
    - 去掉旧的等级标签；
    - 若缺少时间标签，则补齐 `【HH:MM:SS】`。
    """
    _ = level
    text = strip_level_tag(msg)
    if not text:
        label = now_label() if ts is None else time.strftime("%H:%M:%S", time.localtime(float(ts)))
        return f"【{label}】"
    if _TIME_TAG_RE.match(text):
        return text
    label = now_label() if ts is None else time.strftime("%H:%M:%S", time.localtime(float(ts)))
    return f"【{label}】{text}"


__all__ = [
    "LOG_LEVELS",
    "build_context_message",
    "ensure_level_tag",
    "extract_level_from_msg",
    "level_name",
    "strip_level_tag",
]

