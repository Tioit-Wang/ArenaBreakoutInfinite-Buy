"""
屏幕区域选择组件。
"""

from __future__ import annotations

import tkinter as tk
from typing import Callable, Optional, Tuple

from super_buyer.services.font_loader import tk_font


class RegionSelector:
    """全屏遮罩拖拽选取矩形区域。"""

    def __init__(self, root: tk.Tk, on_done: Callable[[Optional[Tuple[int, int, int, int]]], None]):
        self.root = root
        self.on_done = on_done
        self.top: Optional[tk.Toplevel] = None
        self.canvas: Optional[tk.Canvas] = None
        self.start: Optional[Tuple[int, int]] = None
        self.rect_id: Optional[int] = None

    def show(self) -> None:
        top = tk.Toplevel(self.root)
        self.top = top
        width = self.root.winfo_screenwidth()
        height = self.root.winfo_screenheight()
        top.geometry(f"{width}x{height}+0+0")
        for attr, value in (("-alpha", 0.25), ("-topmost", True)):
            try:
                top.attributes(attr, value)
            except Exception:
                pass
        top.configure(bg="black")
        top.overrideredirect(True)

        canvas = tk.Canvas(top, bg="black", highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas = canvas

        try:
            font = tk_font(self.root, 12)
        except Exception:
            font = None
        try:
            canvas.create_text(
                width // 2,
                30,
                text="拖拽选择区域，Esc/右键取消",
                fill="white",
                font=font,
            )
        except Exception:
            pass

        canvas.bind("<ButtonPress-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_drag)
        canvas.bind("<ButtonRelease-1>", self._on_release)
        canvas.bind("<ButtonPress-3>", self._on_cancel)
        canvas.bind("<Escape>", self._on_cancel)
        try:
            canvas.focus_force()
        except Exception:
            canvas.focus_set()
        try:
            top.grab_set()
        except Exception:
            pass

    def _on_press(self, event):
        self.start = (event.x_root, event.y_root)
        if self.canvas is not None and self.rect_id is None:
            self.rect_id = self.canvas.create_rectangle(0, 0, 1, 1, outline="red", width=2)

    def _on_drag(self, event):
        if not self.start or self.canvas is None or self.rect_id is None:
            return
        x0, y0 = self.start
        x1, y1 = event.x_root, event.y_root
        self.canvas.coords(self.rect_id, x0, y0, x1, y1)

    def _on_release(self, event):
        if not self.start:
            self._finish(None)
            return
        x0, y0 = self.start
        x1, y1 = event.x_root, event.y_root
        if abs(x1 - x0) < 3 or abs(y1 - y0) < 3:
            self._finish(None)
            return
        self._finish((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))

    def _on_cancel(self, _event):
        self._finish(None)

    def _finish(self, bounds: Optional[Tuple[int, int, int, int]]) -> None:
        if self.top is not None:
            try:
                try:
                    self.top.grab_release()
                except Exception:
                    pass
                self.top.destroy()
            except Exception:
                pass
        self.on_done(bounds)


class FixedSizeSelector:
    """固定尺寸的跟随鼠标的选择框。"""

    def __init__(self, root: tk.Tk, width: int, height: int, on_done: Callable[[Optional[Tuple[int, int, int, int]]], None]):
        self.root = root
        self.width = int(max(1, width))
        self.height = int(max(1, height))
        self.on_done = on_done
        self.top: Optional[tk.Toplevel] = None
        self.canvas: Optional[tk.Canvas] = None
        self.rect_id: Optional[int] = None
        self._x = 0
        self._y = 0

    def show(self) -> None:
        top = tk.Toplevel(self.root)
        self.top = top
        screen_w = self.root.winfo_screenwidth()
        screen_h = self.root.winfo_screenheight()
        top.geometry(f"{screen_w}x{screen_h}+0+0")
        for attr, value in (("-alpha", 0.25), ("-topmost", True)):
            try:
                top.attributes(attr, value)
            except Exception:
                pass
        top.configure(bg="black")
        top.overrideredirect(True)

        canvas = tk.Canvas(top, bg="black", highlightthickness=0)
        canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas = canvas
        self.rect_id = canvas.create_rectangle(0, 0, self.width, self.height, outline="red", width=2)

        canvas.bind("<Motion>", self._on_move)
        canvas.bind("<ButtonPress-1>", self._on_confirm)
        canvas.bind("<ButtonPress-3>", self._on_cancel)
        canvas.bind("<Escape>", self._on_cancel)
        try:
            canvas.focus_force()
        except Exception:
            canvas.focus_set()
        try:
            top.grab_set()
        except Exception:
            pass

    def _on_move(self, event):
        self._x = event.x_root
        self._y = event.y_root
        if self.canvas is None or self.rect_id is None:
            return
        w, h = self.width, self.height
        x0 = max(0, self._x - w // 2)
        y0 = max(0, self._y - h // 2)
        x1 = x0 + w
        y1 = y0 + h
        self.canvas.coords(self.rect_id, x0, y0, x1, y1)

    def _on_confirm(self, _event):
        if self.canvas is None or self.rect_id is None:
            self._finish(None)
            return
        coords = self.canvas.coords(self.rect_id)
        if len(coords) != 4:
            self._finish(None)
            return
        x0, y0, x1, y1 = map(int, coords)
        self._finish((x0, y0, x1, y1))

    def _on_cancel(self, _event):
        self._finish(None)

    def _finish(self, bounds: Optional[Tuple[int, int, int, int]]) -> None:
        if self.top is not None:
            try:
                try:
                    self.top.grab_release()
                except Exception:
                    pass
                self.top.destroy()
            except Exception:
                pass
        self.on_done(bounds)


__all__ = ["FixedSizeSelector", "RegionSelector"]

