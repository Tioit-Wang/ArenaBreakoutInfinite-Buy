"""
字体加载工具。
"""

from __future__ import annotations

import os
import sys
from typing import Optional

from wg1.resources.paths import asset_path


def font_path() -> Optional[str]:
    """返回内置字体的绝对路径。"""
    try:
        path = asset_path("fangzhenglanting.ttf")
    except FileNotFoundError:
        return None
    return str(path)


def pil_font(size: int = 14):
    """返回 PIL 字体对象。"""
    try:
        from PIL import ImageFont  # type: ignore
    except Exception:
        return None
    path = font_path()
    if not path:
        return None
    try:
        return ImageFont.truetype(path, size=size)
    except Exception:
        return None


def setup_matplotlib_chinese() -> None:
    """配置 Matplotlib 中文字体。"""
    try:
        import matplotlib
        from matplotlib import font_manager
        from matplotlib import rcParams
    except Exception:
        return
    path = font_path()
    if not path:
        try:
            rcParams["font.family"] = [
                "Microsoft YaHei UI",
                "Microsoft YaHei",
                "SimHei",
                "SimSun",
            ]
            rcParams["axes.unicode_minus"] = False
        except Exception:
            pass
        return
    try:
        font_manager.fontManager.addfont(path)
        from matplotlib.font_manager import FontProperties

        props = FontProperties(fname=path)
        family = props.get_name() or os.path.splitext(os.path.basename(path))[0]
        matplotlib.rcParams["font.family"] = [family]
        matplotlib.rcParams["axes.unicode_minus"] = False
    except Exception:
        try:
            rcParams["axes.unicode_minus"] = False
        except Exception:
            pass


def _win_add_font_private(path: str) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes

        gdi32 = ctypes.windll.gdi32
        return bool(gdi32.AddFontResourceExW(path, 0x10, 0))
    except Exception:
        return False


def _font_family_name_from_file(path: str) -> Optional[str]:
    try:
        from matplotlib.font_manager import FontProperties

        return FontProperties(fname=path).get_name()
    except Exception:
        return None


def tk_font(root, size: int = 12):
    """返回 Tk 使用的中文字体对象。"""
    try:
        import tkinter.font as tkfont
    except Exception:
        return None

    path = font_path()
    family: Optional[str] = None
    if path:
        _win_add_font_private(path)
        family = _font_family_name_from_file(path)

    candidates = [
        family,
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "SimHei",
        "SimSun",
    ]
    for fam in candidates:
        if not fam:
            continue
        try:
            return tkfont.Font(root=root, family=fam, size=size)
        except Exception:
            continue
    return None


def draw_text(draw, xy, text: str, fill=(255, 255, 255), size: int = 14) -> None:
    """在 PIL 画布上绘制中文文本。"""
    try:
        font = pil_font(size)
        if font is not None:
            draw.text(xy, text, fill=fill, font=font)
            return
    except Exception:
        pass
    try:
        draw.text(xy, text, fill=fill)
    except Exception:
        pass


__all__ = [
    "draw_text",
    "font_path",
    "pil_font",
    "setup_matplotlib_chinese",
    "tk_font",
]

