"""
核心异常定义。
"""

from __future__ import annotations


class FatalOcrError(RuntimeError):
    """Umi OCR 致命错误。"""


__all__ = ["FatalOcrError"]

