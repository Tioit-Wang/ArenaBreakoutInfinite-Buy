"""
配置模块入口。

提供用于加载、保存与初始化应用配置的便捷导出。
"""

from __future__ import annotations

from .defaults import DEFAULT_CONFIG  # noqa: F401
from .loader import (
    ConfigPaths,
    ensure_default_config,
    load_config,
    save_config,
)

__all__ = [
    "DEFAULT_CONFIG",
    "ConfigPaths",
    "ensure_default_config",
    "load_config",
    "save_config",
]

