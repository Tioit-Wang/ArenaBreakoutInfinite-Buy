from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional

import tkinter as tk
from tkinter import font as tkfont

__all__ = ["LightTipManager"]


@dataclass
class _TipPayload:
    """轻提示请求实体。"""

    message: str
    kind: str
    duration: float


class LightTipManager:
    """
    全局轻提示管理器。

    - 以队列方式顺序展示轻提示。
    - 支持按提示等级配置配色及停留时长。
    - 顶部浮出展示，带微圆角与轻微进入动画。
    """

    _COLOR_MAP = {
        "success": ("#10893e", "#ffffff"),
        "error": ("#d13438", "#ffffff"),
        "warn": ("#f9ab00", "#202124"),
        "info": ("#2563eb", "#ffffff"),
    }
    _DEFAULT_KIND = "info"
    _MIN_DURATION = 0.8

    def __init__(self, root: tk.Misc) -> None:
        self._root = root
        self._queue: Deque[_TipPayload] = deque()
        self._current: Optional[_TipPayload] = None
        self._window: Optional[tk.Toplevel] = None
        self._close_after: Optional[str] = None
        self._anim_after: Optional[str] = None
        self._transparent_color = "#010101"
        self._transparent_ready = self._detect_transparent_support()
        self._font = self._build_font()

    # ---- 对外接口 ----

    def show(self, message: str, *, kind: str = "info", duration: float = 2.6) -> None:
        """
        展示一条轻提示。

        :param message: 需要展示的提示文本。
        :param kind: 提示等级，可选 info/success/warn/error。
        :param duration: 停留时长（秒）。
        """
        if not message or not self._root.winfo_exists():
            return
        kind = (kind or self._DEFAULT_KIND).lower()
        duration = max(self._MIN_DURATION, float(duration or 0))
        payload = _TipPayload(message=message.strip(), kind=kind, duration=duration)
        self._queue.append(payload)
        if self._current is None:
            self._root.after_idle(self._display_next)

    def close_all(self) -> None:
        """立即关闭并清空所有轻提示。"""
        self._queue.clear()
        self._teardown_window()
        self._current = None

    # ---- 内部流程 ----

    def _display_next(self) -> None:
        if self._current is not None or not self._queue:
            return
        self._current = self._queue.popleft()
        self._render_current()

    def _render_current(self) -> None:
        tip = self._current
        if tip is None:
            return
        bg, fg = self._COLOR_MAP.get(tip.kind, self._COLOR_MAP[self._DEFAULT_KIND])
        text = tip.message
        if self._window is not None:
            self._teardown_window()
        win = tk.Toplevel(self._root)
        win.withdraw()
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", True)
        except Exception:
            pass
        content_width, content_height = self._measure_content(text)
        final_width = max(260, content_width)
        final_height = max(44, content_height)
        if self._transparent_ready:
            self._setup_transparent_window(win, final_width, final_height, bg, fg, text)
        else:
            self._setup_basic_window(win, final_width, final_height, bg, fg, text)
        self._place_window(win, final_width, final_height)
        self._window = win
        win.deiconify()
        self._play_slide_in(win)
        self._schedule_close(tip.duration)

    def _schedule_close(self, duration: float) -> None:
        if self._close_after is not None:
            try:
                self._root.after_cancel(self._close_after)
            except Exception:
                pass
        try:
            delay = int(duration * 1000)
        except Exception:
            delay = int(self._MIN_DURATION * 1000)
        self._close_after = self._root.after(delay, self._on_tip_timeout)

    def _on_tip_timeout(self) -> None:
        self._close_after = None
        self._teardown_window()
        self._current = None
        self._root.after_idle(self._display_next)

    def _teardown_window(self) -> None:
        if self._close_after is not None:
            try:
                self._root.after_cancel(self._close_after)
            except Exception:
                pass
        self._close_after = None
        if self._anim_after is not None:
            try:
                self._root.after_cancel(self._anim_after)
            except Exception:
                pass
        self._anim_after = None
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass
        self._window = None

    # ---- 窗口绘制 ----

    def _setup_transparent_window(
        self,
        win: tk.Toplevel,
        width: int,
        height: int,
        bg: str,
        fg: str,
        text: str,
    ) -> None:
        try:
            win.configure(bg=self._transparent_color)
            win.wm_attributes("-transparentcolor", self._transparent_color)
        except Exception:
            self._transparent_ready = False
            self._setup_basic_window(win, width, height, bg, fg, text)
            return
        canvas = tk.Canvas(
            win,
            width=width,
            height=height,
            highlightthickness=0,
            bd=0,
            bg=self._transparent_color,
        )
        canvas.pack()
        radius = 10
        self._draw_rounded_rect(canvas, 0, 0, width, height, radius, fill=bg, outline=bg)
        canvas.create_text(
            width // 2,
            height // 2,
            text=text,
            fill=fg,
            font=self._font,
        )

    def _setup_basic_window(
        self,
        win: tk.Toplevel,
        width: int,
        height: int,
        bg: str,
        fg: str,
        text: str,
    ) -> None:
        wrapper = tk.Frame(win, bg=bg, padx=18, pady=12)
        wrapper.pack()
        lbl = tk.Label(
            wrapper,
            text=text,
            bg=bg,
            fg=fg,
            font=self._font,
        )
        lbl.pack()
        # 强制窗口尺寸，保持与透明模式一致
        win.update_idletasks()
        current_w = wrapper.winfo_reqwidth()
        current_h = wrapper.winfo_reqheight()
        extra_w = max(0, width - current_w)
        extra_h = max(0, height - current_h)
        if extra_w or extra_h:
            pad_w = max(0, extra_w // 2)
            pad_h = max(0, extra_h // 2)
            wrapper.configure(padx=wrapper.cget("padx") + pad_w, pady=wrapper.cget("pady") + pad_h)

    def _place_window(self, win: tk.Toplevel, width: int, height: int) -> None:
        self._root.update_idletasks()
        try:
            root_x = self._root.winfo_rootx()
            root_y = self._root.winfo_rooty()
            root_w = self._root.winfo_width()
        except Exception:
            root_x = root_y = 0
            root_w = width
        visible_width = root_w if root_w > 0 else width
        target_x = int(root_x + (visible_width - width) / 2)
        target_y = int(root_y + 16)
        win.geometry(f"{width}x{height}+{target_x}+{target_y - 24}")
        win.update_idletasks()
        setattr(win, "_target_xy", (target_x, target_y))

    def _play_slide_in(self, win: tk.Toplevel) -> None:
        target = getattr(win, "_target_xy", None)
        if not target:
            return
        target_x, target_y = target
        start_y = target_y - 28
        steps = 8
        delta = (target_y - start_y) / steps if steps else 0
        frame_time = 16

        def _step(index: int) -> None:
            if not win.winfo_exists():
                return
            if index >= steps:
                win.geometry(f"+{target_x}+{target_y}")
                self._anim_after = None
                return
            current_y = math.floor(start_y + delta * (index + 1))
            win.geometry(f"+{target_x}+{current_y}")
            self._anim_after = self._root.after(frame_time, lambda: _step(index + 1))

        _step(0)

    # ---- 工具函数 ----

    def _measure_content(self, text: str) -> tuple[int, int]:
        padding_x, padding_y = 32, 18
        width = self._font.measure(text) + padding_x
        height = self._font.metrics("linespace") + padding_y
        return int(width), int(height)

    def _draw_rounded_rect(
        self,
        canvas: tk.Canvas,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        radius: int,
        **kwargs,
    ) -> None:
        points = [
            x1 + radius,
            y1,
            x2 - radius,
            y1,
            x2,
            y1,
            x2,
            y1 + radius,
            x2,
            y2 - radius,
            x2,
            y2,
            x2 - radius,
            y2,
            x1 + radius,
            y2,
            x1,
            y2,
            x1,
            y2 - radius,
            x1,
            y1 + radius,
            x1,
            y1,
        ]
        canvas.create_polygon(points, smooth=True, **kwargs)

    def _detect_transparent_support(self) -> bool:
        try:
            system = self._root.tk.call("tk", "windowingsystem")
        except Exception:
            return False
        return system in {"win32", "aqua"}

    def _build_font(self) -> tkfont.Font:
        try:
            base = tkfont.nametofont("TkDefaultFont")
            font = tkfont.Font(self._root, font=base)
        except Exception:
            font = tkfont.Font(self._root)
        try:
            font.configure(size=max(11, int(font.cget("size") or 11)))
        except Exception:
            pass
        return font
