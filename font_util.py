"""
字体工具：统一管理中文字体在 Matplotlib、PIL、Tk 中的加载与使用（Windows 优先）。

- 首选使用项目 `assets/` 下的 `fangzhenglanting.ttf`。
- 提供 `pil_font()` 供 PIL.Draw.text 使用；
- 提供 `setup_matplotlib_chinese()` 以全局启用 Matplotlib 中文；
- 在 Windows 上，提供 `tk_font()` 尝试注册字体并返回 Tk 字体对象，便于 Canvas 使用。

注意：
- 本模块尽量懒加载依赖，失败时回退为 None 或不抛异常。
"""

from __future__ import annotations

import os
import sys
from typing import Optional


def _assets_font_candidates() -> list[str]:
    """返回候选的中文字体路径列表（按优先级降序）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cwd = os.getcwd()
    names = [
        "fangzhenglanting.ttf",
        # 未来如需支持其它字体，可在此追加更多文件名
    ]
    cands: list[str] = []
    for n in names:
        for base in (os.path.join(here, "assets"), os.path.join(cwd, "assets")):
            p = os.path.join(base, n)
            if os.path.exists(p):
                cands.append(p)
    return cands


def font_path() -> Optional[str]:
    """返回可用的中文字体绝对路径，找不到则返回 None。"""
    cands = _assets_font_candidates()
    return cands[0] if cands else None


def pil_font(size: int = 14):
    """返回 PIL TrueTypeFont；若失败返回 None。

    参数:
        size: 字号
    """
    try:
        from PIL import ImageFont  # type: ignore
    except Exception:
        return None
    p = font_path()
    if not p:
        return None
    try:
        return ImageFont.truetype(p, size=size)
    except Exception:
        return None


def setup_matplotlib_chinese() -> None:
    """配置 Matplotlib 使用 assets 中文字体，并关闭负号乱码。

    - 仅在检测到 matplotlib 可用时生效；
    - 若找不到字体，则不抛异常，仅跳过设置。
    """
    try:
        import matplotlib
        from matplotlib import font_manager
        from matplotlib import rcParams
    except Exception:
        return
    p = font_path()
    if not p:
        # 尝试常见中文字体族（作为兜底）
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
        font_manager.fontManager.addfont(p)
        # 通过 FontProperties 解析出族名
        try:
            from matplotlib.font_manager import FontProperties
            fp = FontProperties(fname=p)
            family = fp.get_name() or os.path.splitext(os.path.basename(p))[0]
        except Exception:
            family = os.path.splitext(os.path.basename(p))[0]
        matplotlib.rcParams["font.family"] = [family]
        matplotlib.rcParams["axes.unicode_minus"] = False
    except Exception:
        # 兜底：尽量避免抛异常影响主流程
        try:
            rcParams["axes.unicode_minus"] = False
        except Exception:
            pass


def _win_add_font_private(p: str) -> bool:
    """在 Windows 中以私有方式注册字体（仅当前进程可见）。"""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        gdi32 = ctypes.windll.gdi32
        FR_PRIVATE = 0x10
        # 返回新增字体资源数量，0 为失败
        added = gdi32.AddFontResourceExW(p, FR_PRIVATE, 0)
        return bool(added)
    except Exception:
        return False


def _font_family_name_from_file(p: str) -> Optional[str]:
    """尝试从字体文件推断族名（借助 matplotlib 的 FontProperties）。"""
    try:
        from matplotlib.font_manager import FontProperties
        return FontProperties(fname=p).get_name()
    except Exception:
        return None


def tk_font(root, size: int = 12):
    """返回可用于 Tk Canvas 的字体对象（优先使用 assets 中文字体）。

    - Windows 上尝试通过 GDI 私有注册字体，再以族名创建 Tk 字体；
    - 若失败，回退到常见中文字体族；
    - 任一环节失败则返回 None，调用方可继续走 Tk 默认字体或自行兜底。
    """
    try:
        import tkinter.font as tkfont
    except Exception:
        return None

    p = font_path()
    family: Optional[str] = None
    if p and os.path.exists(p):
        # Windows：尝试注册并解析族名
        _win_add_font_private(p)
        family = _font_family_name_from_file(p)

    # 族名不可用时，回退几个常见中文字体
    families = [
        family,
        "Microsoft YaHei UI",
        "Microsoft YaHei",
        "SimHei",
        "SimSun",
    ]
    for fam in families:
        if not fam:
            continue
        try:
            return tkfont.Font(root=root, family=fam, size=size)
        except Exception:
            continue
    return None


def draw_text(draw, xy, text: str, fill=(255, 255, 255), size: int = 14) -> None:
    """在 PIL.ImageDraw 上绘制文本，自动选择中文字体；失败则使用默认字体。

    参数:
        draw: PIL.ImageDraw.Draw 实例
        xy: 左上角坐标 (x, y)
        text: 文本
        fill: 颜色 (r,g,b) 或 (r,g,b,a)
        size: 字号
    """
    try:
        f = pil_font(size)
        if f is not None:
            draw.text(xy, text, fill=fill, font=f)
        else:
            draw.text(xy, text, fill=fill)
    except Exception:
        try:
            draw.text(xy, text, fill=fill)
        except Exception:
            pass

