"""
核心数据模型定义。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class Goods:
    id: str
    name: str
    search_name: str
    image_path: str
    big_category: str
    sub_category: str = ""
    exchangeable: bool = False


@dataclass
class LaunchResult:
    ok: bool
    code: str
    error: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


__all__ = ["Goods", "LaunchResult"]

