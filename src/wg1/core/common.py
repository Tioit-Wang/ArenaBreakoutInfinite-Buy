"""
核心通用工具。
"""

from __future__ import annotations

import time
from typing import Optional


def now_label() -> str:
    return time.strftime("%H:%M:%S")


def safe_int(value: str, default: int = -1) -> int:
    try:
        return int(value)
    except Exception:
        return default


def parse_price_text(text: str) -> Optional[int]:
    if not text:
        return None
    source = (
        text.strip()
        .upper()
        .replace(",", "")
        .replace(" ", "")
    )
    import re

    match = re.search(r"([0-9]+(?:\.[0-9]+)?)([MK])?", source)
    if not match:
        digits = "".join(ch for ch in source if ch.isdigit())
        if digits:
            try:
                return int(digits)
            except Exception:
                return None
        return None
    raw_number = match.group(1)
    suffix = match.group(2)
    try:
        value = float(raw_number) if "." in raw_number else int(raw_number)
    except Exception:
        return None
    if suffix == "K":
        value = float(value) * 1_000.0
    elif suffix == "M":
        value = float(value) * 1_000_000.0
    try:
        return int(round(value))
    except Exception:
        return None


def safe_sleep(seconds: float) -> None:
    try:
        time.sleep(max(0.0, float(seconds)))
    except Exception:
        pass


__all__ = ["now_label", "parse_price_text", "safe_int", "safe_sleep"]

