from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING, Any

from tkinter import ttk

if TYPE_CHECKING:
    from super_buyer.ui.app import App


class BaseTab(ttk.Frame):
    """Notebook 标签页基类，向下委托 App 公共状态。"""

    def __init__(self, app: "App", notebook: ttk.Notebook) -> None:
        super().__init__(notebook)
        self.app = app
        self.notebook = notebook

    def __getattr__(self, name: str):
        try:
            return super().__getattribute__(name)
        except AttributeError:
            return getattr(self.app, name)

    def _build_section(self, parent, title: str, *, expand: bool = False, pady=(0, 6)):
        """创建统一的页面分区容器，保持各页的间距与外轮廓一致。"""
        section = ttk.LabelFrame(parent, text=title)
        section.pack(
            fill=(tk.BOTH if expand else tk.X),
            expand=expand,
            padx=4,
            pady=pady,
        )
        return section

    def _build_modal_shell(self, top: tk.Toplevel, *, title: str, description: str | None = None) -> dict[str, Any]:
        """创建统一的弹窗骨架：标题区、工具区、内容区、底部区。"""
        root = ttk.Frame(top)
        try:
            top.rowconfigure(0, weight=1)
            top.columnconfigure(0, weight=1)
        except Exception:
            pass
        root.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        try:
            root.columnconfigure(0, weight=1)
            # 中间内容区独占可伸缩空间，确保头部/底部固定。
            root.rowconfigure(2, weight=1)
        except Exception:
            pass

        header = ttk.Frame(root)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))

        header_text = ttk.Frame(header)
        header_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        ttk.Label(header_text, text=title).pack(anchor="w")
        if description:
            ttk.Label(
                header_text,
                text=description,
                foreground="#666666",
                justify=tk.LEFT,
            ).pack(anchor="w", pady=(2, 0))

        summary = ttk.Label(header, text="", foreground="#666666")
        summary.pack(side=tk.RIGHT, padx=(12, 0))

        toolbar = ttk.Frame(root)
        toolbar.grid(row=1, column=0, sticky="ew")

        content = ttk.Frame(root)
        content.grid(row=2, column=0, sticky="nsew")
        try:
            content.columnconfigure(0, weight=1)
            content.rowconfigure(0, weight=1)
        except Exception:
            pass

        footer = ttk.Frame(root)
        footer.grid(row=3, column=0, sticky="ew", pady=(6, 0))

        return {
            "root": root,
            "header": header,
            "toolbar": toolbar,
            "content": content,
            "footer": footer,
            "summary": summary,
        }

    def _build_scrollable_canvas(self, parent):
        """创建统一的滚动内容区域。"""
        wrap = ttk.Frame(parent)
        wrap.pack(fill=tk.BOTH, expand=True, pady=(6, 6))

        canvas = tk.Canvas(wrap, highlightthickness=0)
        vsb = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        inner = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_inner_configure(_event=None) -> None:
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
            except Exception:
                pass

        def _on_canvas_configure(event) -> None:
            try:
                canvas.itemconfigure(win, width=event.width)
            except Exception:
                pass

        inner.bind("<Configure>", _on_inner_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        try:
            self._bind_mousewheel(inner, canvas)
        except Exception:
            pass

        return {
            "wrap": wrap,
            "canvas": canvas,
            "scrollbar": vsb,
            "inner": inner,
            "window": win,
        }
