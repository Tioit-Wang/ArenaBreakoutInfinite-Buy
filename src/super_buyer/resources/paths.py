"""
资源路径辅助函数。
"""

from __future__ import annotations

import contextlib
import importlib.resources as pkg_resources
from pathlib import Path
from typing import Iterator


def resource_path(package: str, name: str) -> Path:
    """返回指定包内资源的真实路径。"""
    with pkg_resources.as_file(pkg_resources.files(package) / name) as path:
        return Path(path)


def image_path(name: str) -> Path:
    return resource_path("super_buyer.resources.images", name)


def asset_path(name: str) -> Path:
    return resource_path("super_buyer.resources.assets", name)


@contextlib.contextmanager
def open_resource(package: str, name: str, mode: str = "rb"):
    """打开资源文件，返回上下文管理器。"""
    with pkg_resources.open_binary(package, name) as fh:
        yield fh


__all__ = ["asset_path", "image_path", "open_resource", "resource_path"]

