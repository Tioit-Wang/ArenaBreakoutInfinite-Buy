"""
日志等级与格式化工具。
"""

from __future__ import annotations

from typing import Dict

from .common import now_label

LOG_LEVELS: Dict[str, int] = {"debug": 10, "info": 20, "error": 40}


def level_name(level: str) -> str:
    name = str(level or "").lower()
    return name if name in LOG_LEVELS else "info"


def extract_level_from_msg(msg: str) -> str:
    try:
        if "【ERROR】" in msg:
            return "error"
        if "【DEBUG】" in msg:
            return "debug"
        if "【INFO】" in msg:
            return "info"
        keywords_error = ("失败", "错误", "超时", "未找到", "缺少")
        if any(key in msg for key in keywords_error):
            return "error"
        keywords_debug = ("耗时", "匹配", "打开", "cost=", "match=", "open=", "ms")
        if any(key in msg for key in keywords_debug):
            return "debug"
    except Exception:
        pass
    return "info"


def ensure_level_tag(msg: str, level: str) -> str:
    if "【DEBUG】" in msg or "【INFO】" in msg or "【ERROR】" in msg:
        return msg
    try:
        idx = msg.find("】")
        if idx >= 0:
            return msg[: idx + 1] + f"【{level.upper()}】" + msg[idx + 1 :]
    except Exception:
        pass
    return f"【{now_label()}】【{level.upper()}】" + msg


__all__ = ["LOG_LEVELS", "ensure_level_tag", "extract_level_from_msg", "level_name"]

