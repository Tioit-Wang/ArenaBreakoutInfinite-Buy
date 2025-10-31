from __future__ import annotations

import os
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, ttk
from typing import Callable, Dict, Optional, Tuple, Union

__all__ = ["TemplateRow"]


TestResult = Union[bool, Tuple[bool, str], None]


class TemplateRow(ttk.Frame):
    """模板配置行组件。

    - 支持只读模式（隐藏路径输入与文件选择）。
    - 通过红/绿状态点提示模板是否存在，悬停显示详情。
    - 测试结果采用轻量浮动提示反馈。
    """

    def __init__(
        self,
        master: tk.Widget,
        name: str,
        data: Optional[Dict[str, object]] = None,
        *,
        on_test: Optional[Callable[[str, str, float], TestResult]] = None,
        on_capture: Optional[Callable[["TemplateRow"], None]] = None,
        on_preview: Optional[Callable[[str, str], None]] = None,
        on_change: Optional[Callable[[], None]] = None,
        readonly: bool = False,
        root_dir: Optional[Union[str, Path]] = None,
    ) -> None:
        super().__init__(master)
        self.name = name
        self._on_test = on_test
        self._on_capture = on_capture
        self._on_preview = on_preview
        self._on_change = on_change
        self._readonly = bool(readonly)
        self._root_dir = Path(root_dir).resolve() if root_dir else Path.cwd()
        self._suspend_path = False
        self._tooltip_win: tk.Toplevel | None = None
        self._toast_win: tk.Toplevel | None = None
        self._toast_after_id: str | None = None
        self._status_text = ""

        base = data or {}
        default_conf = 0.85
        try:
            conf_val = float(base.get("confidence", default_conf) or default_conf)
        except Exception:
            conf_val = default_conf

        self.var_path = tk.StringVar(value=str(base.get("path", "")))
        self.var_conf = tk.DoubleVar(value=conf_val)

        bg_color = self._get_background_color()
        self._status_canvas = tk.Canvas(self, width=12, height=12, highlightthickness=0, bg=bg_color)
        self._status_canvas.grid(row=0, column=0, padx=(0, 4), pady=2)
        self._status_dot = self._status_canvas.create_oval(2, 2, 10, 10, fill="#d93025", outline="")

        self._lbl_name = ttk.Label(self, text=name, width=14)
        self._lbl_name.grid(row=0, column=1, sticky="w", padx=(0, 6))

        col = 2
        if not self._readonly:
            self._entry_path = ttk.Entry(self, textvariable=self.var_path, width=42)
            self._entry_path.grid(row=0, column=col, sticky="we", padx=(0, 4))
            col += 1
            ttk.Button(self, text="浏览…", command=self._browse_file).grid(row=0, column=col, padx=(0, 4))
            col += 1
        else:
            self._entry_path = None

        if self._on_capture is not None:
            ttk.Button(self, text="截图", command=lambda: self._on_capture(self)).grid(row=0, column=col, padx=(0, 4))
        else:
            ttk.Label(self, text="").grid(row=0, column=col, padx=(0, 4))
        col += 1

        if self._on_test is not None:
            ttk.Button(self, text="测试", command=self._handle_test).grid(row=0, column=col, padx=(0, 4))
        else:
            ttk.Label(self, text="").grid(row=0, column=col, padx=(0, 4))
        col += 1

        if self._on_preview is not None:
            ttk.Button(self, text="预览", command=self._handle_preview).grid(row=0, column=col, padx=(0, 4))
        else:
            ttk.Label(self, text="").grid(row=0, column=col, padx=(0, 4))
        col += 1

        ttk.Label(self, text="置信度").grid(row=0, column=col, sticky="e", padx=(6, 2))
        col += 1
        try:
            self._sp_conf = ttk.Spinbox(
                self,
                from_=0.1,
                to=1.0,
                increment=0.01,
                width=6,
                textvariable=self.var_conf,
                format="%.2f",
            )
        except Exception:
            self._sp_conf = tk.Spinbox(self, from_=0.1, to=1.0, increment=0.01, width=6, textvariable=self.var_conf)
        self._sp_conf.grid(row=0, column=col, sticky="w")

        self.columnconfigure(max(3, col), weight=1)

        # 监听变化
        self.var_path.trace_add("write", lambda *_: self._handle_change())
        self.var_conf.trace_add("write", lambda *_: self._handle_change())
        if self._entry_path is not None:
            self._entry_path.bind("<FocusOut>", lambda _e: self._handle_change())
        self._sp_conf.bind("<FocusOut>", lambda _e: self._handle_change())
        self._sp_conf.bind("<Return>", lambda _e: self._handle_change())

        self._bind_tooltip(self._status_canvas)
        self._bind_tooltip(self._lbl_name)
        self._handle_change()

    # ---- 公共接口 ----

    def get_path(self) -> str:
        return (self.var_path.get() or "").strip()

    def get_confidence(self) -> float:
        try:
            return float(self.var_conf.get() or 0.0)
        except Exception:
            return 0.0

    def get_abs_path(self) -> str:
        return self._resolve_path(self.get_path())

    # ---- 内部工具 ----

    def _browse_file(self) -> None:
        if self._readonly:
            return
        try:
            path = filedialog.askopenfilename(
                title=f"选择模板文件 - {self.name}",
                filetypes=[("PNG 图片", "*.png"), ("所有文件", "*.*")],
            )
        except Exception:
            path = None
        if path:
            self._set_path(path)

    def _handle_test(self) -> None:
        if self._on_test is None:
            return
        path = self.get_path()
        abs_path = self._resolve_path(path)
        if not path:
            self._toast("模板未配置", "warn")
            return
        if not os.path.exists(abs_path):
            self._toast("模板文件缺失", "error")
            return
        conf = self.get_confidence() or 0.85
        try:
            result = self._on_test(self.name, abs_path, float(conf))
        except Exception as exc:
            self._toast(f"测试失败：{exc}", "error")
            return
        success, message = self._normalize_test_result(result)
        if success:
            self._toast(message or "识别成功", "success")
        else:
            self._toast(message or "未匹配到目标", "warn")

    def _handle_preview(self) -> None:
        if self._on_preview is None:
            return
        path = self.get_path()
        abs_path = self._resolve_path(path)
        if not path:
            self._toast("模板未配置", "warn")
            return
        if not os.path.exists(abs_path):
            self._toast("模板文件缺失", "error")
            return
        try:
            self._on_preview(abs_path, f"预览 - {self.name}")
        except Exception as exc:
            self._toast(f"预览失败：{exc}", "error")

    def _handle_change(self) -> None:
        self._normalize_path()
        self._normalize_confidence()
        self._update_status()
        if self._on_change:
            try:
                self._on_change()
            except Exception:
                pass

    def _normalize_confidence(self) -> None:
        try:
            val = float(self.var_conf.get())
        except Exception:
            val = 0.85
        if val <= 0:
            val = 0.1
        if val > 1.0:
            val = 1.0
        self.var_conf.set(val)

    def _normalize_path(self) -> None:
        if self._suspend_path:
            return
        path = (self.var_path.get() or "").strip()
        if not path:
            return
        p = Path(path)
        if p.is_absolute():
            try:
                rel = p.resolve().relative_to(self._root_dir)
                self._set_path(rel.as_posix())
            except Exception:
                pass

    def _resolve_path(self, path: str) -> str:
        if not path:
            return ""
        p = Path(path)
        if p.is_absolute():
            return str(p)
        return str((self._root_dir / p).resolve())

    def _set_path(self, value: str) -> None:
        self._suspend_path = True
        try:
            self.var_path.set(value)
        finally:
            self._suspend_path = False

    def _update_status(self) -> None:
        path = self.get_path()
        abs_path = self._resolve_path(path)
        if not path:
            color = "#d93025"
            self._status_text = "未配置"
        elif os.path.exists(abs_path):
            color = "#1e8e3e"
            self._status_text = "已配置"
        else:
            color = "#d93025"
            self._status_text = "文件缺失"
        try:
            self._status_canvas.itemconfigure(self._status_dot, fill=color)
        except Exception:
            pass

    # ---- 反馈机制 ----

    def _bind_tooltip(self, widget: tk.Widget) -> None:
        widget.bind("<Enter>", self._show_tooltip, add="+")
        widget.bind("<Leave>", self._hide_tooltip, add="+")

    def _show_tooltip(self, event: tk.Event | None = None) -> None:
        if not self._status_text:
            return
        self._hide_tooltip()
        win = tk.Toplevel(self)
        win.wm_overrideredirect(True)
        win.configure(bg="#333333")
        lbl = ttk.Label(win, text=self._status_text, foreground="#ffffff", background="#333333")
        lbl.pack(ipadx=6, ipady=2)
        x = self.winfo_rootx() + 18
        y = self.winfo_rooty() - 4
        try:
            win.wm_geometry(f"+{x}+{y}")
        except Exception:
            pass
        self._tooltip_win = win

    def _hide_tooltip(self, _event: tk.Event | None = None) -> None:
        if self._tooltip_win is not None:
            try:
                self._tooltip_win.destroy()
            except Exception:
                pass
            self._tooltip_win = None

    def _toast(self, message: str, kind: str = "info") -> None:
        colors = {
            "success": ("#1e8e3e", "#ffffff"),
            "error": ("#b3261e", "#ffffff"),
            "warn": ("#f9ab00", "#202124"),
            "info": ("#202124", "#ffffff"),
        }
        bg, fg = colors.get(kind, ("#202124", "#ffffff"))
        if self._toast_win is not None:
            try:
                self._toast_win.destroy()
            except Exception:
                pass
            self._toast_win = None
        top = tk.Toplevel(self)
        top.wm_overrideredirect(True)
        top.configure(bg=bg)
        lbl = ttk.Label(top, text=message, foreground=fg, background=bg)
        lbl.pack(ipadx=12, ipady=6)
        x = self.winfo_rootx() + 80
        y = self.winfo_rooty() + max(self.winfo_height(), 30) + 8
        try:
            top.wm_geometry(f"+{x}+{y}")
        except Exception:
            pass
        if self._toast_after_id is not None:
            try:
                self.after_cancel(self._toast_after_id)
            except Exception:
                pass
        self._toast_after_id = self.after(1400, self._hide_toast)
        self._toast_win = top

    def _hide_toast(self) -> None:
        if self._toast_win is not None:
            try:
                self._toast_win.destroy()
            except Exception:
                pass
        self._toast_win = None
        self._toast_after_id = None

    @staticmethod
    def _normalize_test_result(result: TestResult) -> Tuple[bool, str]:
        if isinstance(result, tuple) and len(result) >= 1:
            success = bool(result[0])
            message = str(result[1]) if len(result) > 1 and result[1] is not None else ""
            return success, message
        if isinstance(result, bool):
            return result, ""
        return True, ""

    def _get_background_color(self) -> str:
        """获取状态画布需要使用的背景颜色。"""
        style = ttk.Style(self)
        style_name = self.cget("style") or self.winfo_class()
        candidates = [style_name, "TFrame"]
        for candidate in candidates:
            try:
                color = style.lookup(candidate, "background")
            except Exception:
                color = ""
            if color:
                return color
        for widget in (self.master, self.winfo_toplevel()):
            if widget is None:
                continue
            try:
                color = widget.cget("background")
            except Exception:
                color = ""
            if color:
                return color
        return "#f0f0f0"
