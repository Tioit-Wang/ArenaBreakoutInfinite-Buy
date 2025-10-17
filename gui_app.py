import os
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from typing import Any, Dict

from app_config import ensure_default_config, load_config, save_config, sync_to_key_mapping
from autobuyer import AutoBuyer, MultiBuyer


class _RegionSelector:
    """Overlay to select a screen region by dragging.

    Calls on_done((x1,y1,x2,y2)) after overlay is closed, or on_done(None) on cancel.
    """

    def __init__(self, root: tk.Tk, on_done):
        self.root = root
        self.on_done = on_done
        self.top: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self.start: tuple[int, int] | None = None
        self.rect = None

    def show(self) -> None:
        top = tk.Toplevel(self.root)
        self.top = top
        # Fullscreen-like overlay (geometry avoids some -fullscreen quirks)
        w = self.root.winfo_screenwidth()
        h = self.root.winfo_screenheight()
        top.geometry(f"{w}x{h}+0+0")
        try:
            top.attributes("-alpha", 0.25)
        except Exception:
            pass
        try:
            top.attributes("-topmost", True)
        except Exception:
            pass
        top.configure(bg="black")
        top.overrideredirect(True)
        cv = tk.Canvas(top, bg="black", highlightthickness=0)
        cv.pack(fill=tk.BOTH, expand=True)
        self.canvas = cv
        try:
            cv.create_text(w // 2, 30, text="拖拽选择区域，Esc/右键取消", fill="white", font=("Segoe UI", 12))
        except Exception:
            pass
        cv.bind("<ButtonPress-1>", self._on_press)
        cv.bind("<B1-Motion>", self._on_drag)
        cv.bind("<ButtonRelease-1>", self._on_release)
        cv.bind("<ButtonPress-3>", self._on_cancel)
        cv.bind("<Escape>", self._on_cancel)
        try:
            cv.focus_force()
        except Exception:
            cv.focus_set()
        try:
            top.grab_set()
        except Exception:
            pass

    def _on_press(self, e):
        self.start = (e.x_root, e.y_root)
        if self.canvas is not None and self.rect is None:
            self.rect = self.canvas.create_rectangle(0, 0, 1, 1, outline="red", width=2)

    def _on_drag(self, e):
        if not self.start or self.canvas is None or self.rect is None:
            return
        x0, y0 = self.start
        x1, y1 = e.x_root, e.y_root
        self.canvas.coords(self.rect, x0, y0, x1, y1)

    def _on_release(self, e):
        if not self.start:
            self._finish(None)
            return
        x0, y0 = self.start
        x1, y1 = e.x_root, e.y_root
        if abs(x1 - x0) < 3 or abs(y1 - y0) < 3:
            self._finish(None)
            return
        self._finish((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))

    def _on_cancel(self, _):
        self._finish(None)

    def _finish(self, bounds):
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


class _RegionSelector:
    """Simple overlay to select a screen region by dragging.

    Calls `on_done((x1,y1,x2,y2))` or `on_done(None)` on cancel.
    """

    def __init__(self, root: tk.Tk, on_done):
        self.root = root
        self.on_done = on_done
        self.top: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self.rid = None
        self.start: tuple[int, int] | None = None
        self.rect = None

    def show(self) -> None:
        top = tk.Toplevel(self.root)
        self.top = top
        # Use fullscreen-like window sized to screen to reduce platform quirks
        w = self.root.winfo_screenwidth()
        h = self.root.winfo_screenheight()
        top.geometry(f"{w}x{h}+0+0")
        try:
            top.attributes("-alpha", 0.25)
        except Exception:
            pass
        try:
            top.attributes("-topmost", True)
        except Exception:
            pass
        top.configure(bg="black")
        top.overrideredirect(True)
        cv = tk.Canvas(top, bg="black", highlightthickness=0)
        cv.pack(fill=tk.BOTH, expand=True)
        self.canvas = cv
        # Instructions
        try:
            cv.create_text(
                w // 2,
                30,
                text="拖拽选择区域，Esc/右键 取消",
                fill="white",
                font=("Segoe UI", 12),
            )
        except Exception:
            pass
        cv.bind("<ButtonPress-1>", self._on_press)
        cv.bind("<B1-Motion>", self._on_drag)
        cv.bind("<ButtonRelease-1>", self._on_release)
        cv.bind("<ButtonPress-3>", self._on_right_cancel)
        cv.bind("<Escape>", self._on_escape)
        try:
            cv.focus_force()
        except Exception:
            cv.focus_set()
        # Grab input so events are guaranteed to reach the overlay
        try:
            top.grab_set()
        except Exception:
            pass

    def _on_press(self, e):
        self.start = (e.x_root, e.y_root)
        if self.canvas is not None and self.rect is None:
            self.rect = self.canvas.create_rectangle(0, 0, 1, 1, outline="red", width=2)

    def _on_drag(self, e):
        if not self.start or self.canvas is None or self.rect is None:
            return
        x0, y0 = self.start
        x1, y1 = e.x_root, e.y_root
        self.canvas.coords(self.rect, x0, y0, x1, y1)

    def _on_release(self, e):
        if not self.start:
            self._finish(None)
            return
        x0, y0 = self.start
        x1, y1 = e.x_root, e.y_root
        if abs(x1 - x0) < 3 or abs(y1 - y0) < 3:
            self._finish(None)
            return
        self._finish((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))

    def _on_escape(self, _):
        self._finish(None)

    def _on_right_cancel(self, _):
        self._finish(None)

    def _finish(self, bounds):
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


class TemplateRow(ttk.Frame):
    def __init__(self, master, name: str, data: Dict[str, Any], on_test, on_capture, on_preview, on_change=None):
        super().__init__(master)
        self.name = name
        self.var_path = tk.StringVar(value=data.get("path", ""))
        self.var_conf = tk.DoubleVar(value=float(data.get("confidence", 0.85)))
        self.on_test = on_test
        self.on_capture = on_capture
        self.on_preview = on_preview
        self.on_change = on_change

        ttk.Label(self, text=name, width=12).grid(row=0, column=0, sticky="w", padx=4, pady=2)

        # 路径状态：未设置 / 已设置 / 缺失
        self.path_status = ttk.Label(self, text="", width=8)
        self.path_status.grid(row=0, column=1, sticky="w", padx=4)
        # 当路径变量变化时，更新状态文案（并检测文件是否存在）
        def _update_path_status() -> None:
            p = self.get_path()
            if not p:
                self.path_status.configure(text="未设置")
            elif os.path.exists(p):
                self.path_status.configure(text="已设置")
            else:
                self.path_status.configure(text="缺失")
        def _on_path_change(*_):
            _update_path_status()
            if self.on_change:
                try:
                    self.on_change()
                except Exception:
                    pass
        try:
            self.var_path.trace_add("write", _on_path_change)
        except Exception:
            pass
        _update_path_status()

        ttk.Label(self, text="置信度").grid(row=0, column=3, padx=4)
        # 数值输入框 0-1，步长 0.01
        try:
            sp = ttk.Spinbox(self, from_=0.0, to=1.0, increment=0.01, textvariable=self.var_conf, width=6, format="%.2f")
        except Exception:
            sp = tk.Spinbox(self, from_=0.0, to=1.0, increment=0.01, textvariable=self.var_conf, width=6)
        sp.grid(row=0, column=4, sticky="w", padx=4)
        # autosave on change
        if self.on_change:
            try:
                self.var_conf.trace_add("write", lambda *_: self.on_change())
            except Exception:
                pass
        ttk.Button(self, text="测试识别", command=lambda: self.on_test(self.name, self.get_path(), self.get_confidence())).grid(row=0, column=5, padx=4)
        ttk.Button(self, text="截图", command=lambda: self.on_capture(self)).grid(row=0, column=6, padx=4)
        ttk.Button(self, text="预览", command=lambda: self.on_preview(self.get_path(), f"预览 - {self.name}")).grid(row=0, column=7, padx=4)

        # 保持布局稳定
        self.columnconfigure(4, weight=0)

    def get_path(self) -> str:
        return self.var_path.get().strip()

    def get_confidence(self) -> float:
        try:
            return float(self.var_conf.get())
        except Exception:
            return 0.85


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("基于图像识别的自动购买助手")
        self.geometry("980x680")
        # Autosave scheduler
        self._autosave_after_id: str | None = None
        self._autosave_delay_ms: int = 300

        # Config
        ensure_default_config("config.json")
        self.cfg: Dict[str, Any] = load_config("config.json")

        # UI
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True)

        self.tab1 = ttk.Frame(nb)
        self.tab2 = ttk.Frame(nb)
        nb.add(self.tab1, text="初始化配置")
        nb.add(self.tab2, text="自动购买")
        self.tab3 = ttk.Frame(nb)
        nb.add(self.tab3, text="OCR调参")

        self._build_tab1()
        self._build_tab2()
        self._build_tab3()

        # State
        self._buyer: AutoBuyer | None = None
        self._log_lock = threading.Lock()
        # PaddleOCR instance (lazy init when previewing ROI)
        self._paddle_ocr = None  # type: ignore
        self._ocr_lock = threading.Lock()
        self._ocr_warm_started = False
        self._ocr_warm_ready = False
        self._tpl_slug_map = {
            "首页按钮": "btn_home",
            "市场按钮": "btn_market",
            "市场搜索栏": "input_search",
            "市场搜索按钮": "btn_search",
            "购买按钮": "btn_buy",
            "购买成功": "buy_ok",
            "商品关闭位置": "btn_close",
            "刷新按钮": "btn_refresh",
        }

        # Background warm-up OCR to reduce first-click latency
        try:
            self.after(200, self._start_ocr_warmup)
        except Exception:
            pass

    # ---------- Autosave ----------
    def _schedule_autosave(self) -> None:
        try:
            if self._autosave_after_id is not None:
                try:
                    self.after_cancel(self._autosave_after_id)
                except Exception:
                    pass
                self._autosave_after_id = None
            self._autosave_after_id = self.after(self._autosave_delay_ms, self._do_autosave)
        except Exception:
            # Fallback to immediate save if scheduling fails
            self._do_autosave()

    def _do_autosave(self) -> None:
        self._autosave_after_id = None
        try:
            self._save_and_sync(silent=True)
        except Exception:
            pass

    # ---------- Tab1 ----------
    def _build_tab1(self) -> None:
        outer = self.tab1

        # Template manager
        box_tpl = ttk.LabelFrame(outer, text="模板管理")
        box_tpl.pack(fill=tk.X, padx=8, pady=8)

        self.template_rows: Dict[str, TemplateRow] = {}

        def test_match(name: str, path: str, conf: float):
            if not os.path.exists(path):
                messagebox.showwarning("测试识别", f"文件不存在: {path}")
                return
            try:
                import pyautogui  # type: ignore
                loc = pyautogui.locateCenterOnScreen(path, confidence=conf)
            except Exception as e:
                messagebox.showerror("测试识别", f"调用失败: {e}")
                return
            if loc:
                messagebox.showinfo("测试识别", f"{name} 匹配成功: ({loc.x}, {loc.y})")
            else:
                messagebox.showwarning("测试识别", f"{name} 未匹配到。可降低置信度或重截图片。")

        def capture_into_row(row: "TemplateRow"):
            # User drag-select a region; then capture and save under images/<name>.png
            def _after(bounds: tuple[int, int, int, int] | None):
                if not bounds:
                    return
                x1, y1, x2, y2 = bounds
                w, h = max(1, x2 - x1), max(1, y2 - y1)
                try:
                    import pyautogui  # type: ignore
                    img = pyautogui.screenshot(region=(x1, y1, w, h))
                except Exception as e:
                    messagebox.showerror("截图", f"截屏失败: {e}")
                    return
                os.makedirs("images", exist_ok=True)
                slug = self._template_slug(row.name)
                path = os.path.join("images", f"{slug}.png")
                try:
                    img.save(path)
                except Exception as e:
                    messagebox.showerror("截图", f"保存失败: {e}")
                    return
                row.var_path.set(path)
                # Autosave (debounced)
                self._schedule_autosave()
                # Modal preview
                self._preview_image(path, f"预览 - {row.name}")

            self._select_region(_after)

        # render rows
        rowc = 0
        for key, data in self.cfg.get("templates", {}).items():
            r = TemplateRow(
                box_tpl,
                key,
                data,
                on_test=test_match,
                on_capture=capture_into_row,
                on_preview=self._preview_image,
                on_change=self._schedule_autosave,
            )
            r.grid(row=rowc, column=0, sticky="we", padx=6, pady=2)
            self.template_rows[key] = r
            # 覆盖行的 on_test，以加入详细日志而不修改原控件布局
            try:
                def _logged_on_test(nm: str, pth: str, cf: float, *, _row=r):
                    try:
                        self._append_log(f"[模板测试] 名称={nm} 路径={pth} 置信度={cf:.2f}")
                    except Exception:
                        pass
                    if not os.path.exists(pth):
                        msg = f"文件不存在: {pth}"
                        try:
                            self._append_log(f"[模板测试] 失败: {msg}")
                        except Exception:
                            pass
                        messagebox.showwarning("模板识别", msg)
                        return
                    try:
                        import pyautogui  # type: ignore
                        center = pyautogui.locateCenterOnScreen(pth, confidence=cf)
                        try:
                            self._append_log("[模板测试] locateCenterOnScreen=" + (f"({center.x},{center.y})" if center else "None"))
                        except Exception:
                            pass
                        box = None
                        if center is None:
                            box = pyautogui.locateOnScreen(pth, confidence=cf)
                            try:
                                if box:
                                    self._append_log(f"[模板测试] locateOnScreen=({int(getattr(box,'left',0))},{int(getattr(box,'top',0))},{int(getattr(box,'width',0))},{int(getattr(box,'height',0))})")
                                else:
                                    self._append_log("[模板测试] locateOnScreen=None")
                            except Exception:
                                pass
                    except Exception as e:
                        try:
                            self._append_log(f"[模板测试] 异常: {e}")
                        except Exception:
                            pass
                        messagebox.showerror("模板识别", f"识别失败: {e}")
                        return
                    if center:
                        messagebox.showinfo("模板识别", f"{nm} 匹配成功: ({center.x}, {center.y})")
                    elif box:
                        messagebox.showinfo("模板识别", f"{nm} 匹配成功: 区域=({int(getattr(box,'left',0))},{int(getattr(box,'top',0))},{int(getattr(box,'width',0))},{int(getattr(box,'height',0))})")
                    else:
                        messagebox.showwarning("模板识别", f"{nm} 未匹配到，请降低阈值或重截清晰模板。")

                r.on_test = _logged_on_test
            except Exception:
                pass
            rowc += 1

        # 价格区域模板与ROI
        box_roi = ttk.LabelFrame(outer, text="价格区域模板与ROI")
        box_roi.pack(fill=tk.X, padx=8, pady=8)

        roi_cfg = self.cfg.get("price_roi", {}) if isinstance(self.cfg.get("price_roi"), dict) else {}
        self.var_roi_top_tpl = tk.StringVar(value=str(roi_cfg.get("top_template", os.path.join(".", "buy_data_top.png"))))
        self.var_roi_top_thr = tk.DoubleVar(value=float(roi_cfg.get("top_threshold", 0.55)))
        self.var_roi_btm_tpl = tk.StringVar(value=str(roi_cfg.get("bottom_template", os.path.join(".", "buy_data_btm.png"))))
        self.var_roi_btm_thr = tk.DoubleVar(value=float(roi_cfg.get("bottom_threshold", 0.55)))
        self.var_roi_top_off = tk.IntVar(value=int(roi_cfg.get("top_offset", 0)))
        self.var_roi_btm_off = tk.IntVar(value=int(roi_cfg.get("bottom_offset", 0)))
        self.var_roi_lr_pad = tk.IntVar(value=int(roi_cfg.get("lr_pad", 0)))

        # 顶部模板（样式与模板管理一致）
        ttk.Label(box_roi, text="顶部模板", width=12).grid(row=0, column=0, sticky="w", padx=4, pady=2)
        self.lab_roi_top_status = ttk.Label(box_roi, text="", width=8)
        self.lab_roi_top_status.grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(box_roi, text="置信度").grid(row=0, column=3, padx=4)
        try:
            sp_top = ttk.Spinbox(box_roi, from_=0.0, to=1.0, increment=0.01, textvariable=self.var_roi_top_thr, width=6, format="%.2f")
        except Exception:
            sp_top = tk.Spinbox(box_roi, from_=0.0, to=1.0, increment=0.01, textvariable=self.var_roi_top_thr, width=6)
        sp_top.grid(row=0, column=4, sticky="w", padx=4)
        ttk.Button(box_roi, text="测试识别", command=lambda: test_match("价格区域-顶部模板", self.var_roi_top_tpl.get().strip(), float(self.var_roi_top_thr.get() or 0.55))).grid(row=0, column=5, padx=4)
        ttk.Button(box_roi, text="截图", command=lambda: _capture_roi_into(self.var_roi_top_tpl, slug="buy_data_top", title="顶部模板")).grid(row=0, column=6, padx=4)
        ttk.Button(box_roi, text="预览", command=lambda: self._preview_image(self.var_roi_top_tpl.get().strip(), "预览 - 顶部模板")).grid(row=0, column=7, padx=4)

        # 底部模板（样式与模板管理一致）
        ttk.Label(box_roi, text="底部模板", width=12).grid(row=1, column=0, sticky="w", padx=4, pady=2)
        self.lab_roi_btm_status = ttk.Label(box_roi, text="", width=8)
        self.lab_roi_btm_status.grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(box_roi, text="置信度").grid(row=1, column=3, padx=4)
        try:
            sp_btm = ttk.Spinbox(box_roi, from_=0.0, to=1.0, increment=0.01, textvariable=self.var_roi_btm_thr, width=6, format="%.2f")
        except Exception:
            sp_btm = tk.Spinbox(box_roi, from_=0.0, to=1.0, increment=0.01, textvariable=self.var_roi_btm_thr, width=6)
        sp_btm.grid(row=1, column=4, sticky="w", padx=4)
        ttk.Button(box_roi, text="测试识别", command=lambda: test_match("价格区域-底部模板", self.var_roi_btm_tpl.get().strip(), float(self.var_roi_btm_thr.get() or 0.55))).grid(row=1, column=5, padx=4)
        ttk.Button(box_roi, text="截图", command=lambda: _capture_roi_into(self.var_roi_btm_tpl, slug="buy_data_btm", title="底部模板")).grid(row=1, column=6, padx=4)
        ttk.Button(box_roi, text="预览", command=lambda: self._preview_image(self.var_roi_btm_tpl.get().strip(), "预览 - 底部模板")).grid(row=1, column=7, padx=4)

        # 偏移/边距 + 预览（保持在下一行）
        ttk.Label(box_roi, text="顶部偏移").grid(row=2, column=0, padx=4, pady=4, sticky="e")
        ttk.Entry(box_roi, textvariable=self.var_roi_top_off, width=8).grid(row=2, column=1, sticky="w")
        ttk.Label(box_roi, text="底部偏移").grid(row=2, column=2, padx=4, pady=4, sticky="e")
        ttk.Entry(box_roi, textvariable=self.var_roi_btm_off, width=8).grid(row=2, column=3, sticky="w")
        ttk.Label(box_roi, text="左右边距").grid(row=2, column=4, padx=4, pady=4, sticky="e")
        ttk.Entry(box_roi, textvariable=self.var_roi_lr_pad, width=8).grid(row=2, column=5, sticky="w")
        ttk.Button(box_roi, text="预览", command=self._roi_preview_from_screen).grid(row=2, column=6, padx=6)

        # 状态文本更新器
        def _upd_status(var: tk.StringVar, lab: ttk.Label) -> None:
            p = var.get().strip()
            if not p:
                lab.configure(text="未设置")
            elif os.path.exists(p):
                lab.configure(text="已设置")
            else:
                lab.configure(text="缺失")
        def _on_top_tpl(*_):
            _upd_status(self.var_roi_top_tpl, self.lab_roi_top_status)
            self._schedule_autosave()
        def _on_btm_tpl(*_):
            _upd_status(self.var_roi_btm_tpl, self.lab_roi_btm_status)
            self._schedule_autosave()
        try:
            self.var_roi_top_tpl.trace_add("write", _on_top_tpl)
            self.var_roi_btm_tpl.trace_add("write", _on_btm_tpl)
        except Exception:
            pass
        _upd_status(self.var_roi_top_tpl, self.lab_roi_top_status)
        _upd_status(self.var_roi_btm_tpl, self.lab_roi_btm_status)

        # 截图到变量（与模板管理一致的交互）
        def _capture_roi_into(var: tk.StringVar, *, slug: str, title: str):
            def _after(bounds: tuple[int, int, int, int] | None):
                if not bounds:
                    return
                x1, y1, x2, y2 = bounds
                w, h = max(1, x2 - x1), max(1, y2 - y1)
                try:
                    import pyautogui  # type: ignore
                    img = pyautogui.screenshot(region=(x1, y1, w, h))
                except Exception as e:
                    messagebox.showerror("截图", f"截屏失败: {e}")
                    return
                os.makedirs("images", exist_ok=True)
                path = os.path.join("images", f"{slug}.png")
                try:
                    img.save(path)
                except Exception as e:
                    messagebox.showerror("截图", f"保存失败: {e}")
                    return
                var.set(path)
                # 自动保存（去抖）
                self._schedule_autosave()
                # 预览
                self._preview_image(path, f"预览 - {title}")

            self._select_region(_after)

        for i in range(0, 8):
            box_roi.columnconfigure(i, weight=0)

        # 平均单价区域设置（使用“购买按钮”宽度，上方固定距离 + 固定高度）
        box_avg = ttk.LabelFrame(outer, text="平均单价区域设置")
        box_avg.pack(fill=tk.X, padx=8, pady=8)

        avg_cfg = self.cfg.get("avg_price_area", {}) if isinstance(self.cfg.get("avg_price_area"), dict) else {}
        # Defaults: distance 5px, height 45px
        self.var_avg_dist = tk.IntVar(value=int(avg_cfg.get("distance_from_buy_top", 5)))
        self.var_avg_height = tk.IntVar(value=int(avg_cfg.get("height", 45)))

        ttk.Label(box_avg, text="距离(px)").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        try:
            sp_dist = ttk.Spinbox(box_avg, from_=0, to=2000, increment=1, textvariable=self.var_avg_dist, width=8)
        except Exception:
            sp_dist = tk.Spinbox(box_avg, from_=0, to=2000, increment=1, textvariable=self.var_avg_dist, width=8)
        sp_dist.grid(row=0, column=1, sticky="w")

        ttk.Label(box_avg, text="高度(px)").grid(row=0, column=2, padx=8, pady=4, sticky="e")
        try:
            sp_h = ttk.Spinbox(box_avg, from_=1, to=2000, increment=1, textvariable=self.var_avg_height, width=8)
        except Exception:
            sp_h = tk.Spinbox(box_avg, from_=1, to=2000, increment=1, textvariable=self.var_avg_height, width=8)
        sp_h.grid(row=0, column=3, sticky="w")

        ttk.Button(box_avg, text="预览", command=self._avg_price_roi_preview).grid(row=0, column=5, padx=8)

        for i in range(0, 6):
            box_avg.columnconfigure(i, weight=0)

        # 自动保存（距离/高度）
        for v in [self.var_avg_dist, self.var_avg_height]:
            try:
                v.trace_add("write", lambda *_: self._schedule_autosave())
            except Exception:
                pass

        # Points（移除价格区域坐标配置，仅保留单点捕获）
        box_pos = ttk.LabelFrame(outer, text="坐标与区域配置")
        box_pos.pack(fill=tk.X, padx=8, pady=8)

        # 第一个商品点
        p_first = self.cfg.get("points", {}).get("第一个商品", {"x": 0, "y": 0})
        self.var_first_x = tk.IntVar(value=int(p_first.get("x", 0)))
        self.var_first_y = tk.IntVar(value=int(p_first.get("y", 0)))
        ttk.Label(box_pos, text="第一个商品").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        ttk.Entry(box_pos, textvariable=self.var_first_x, width=8).grid(row=0, column=1)
        ttk.Entry(box_pos, textvariable=self.var_first_y, width=8).grid(row=0, column=2)
        ttk.Button(box_pos, text="捕获", command=lambda: self._capture_point(self.var_first_x, self.var_first_y, label="请将鼠标移动到 第一个商品 上…")).grid(row=0, column=3, padx=4)

        # 数量输入框点
        p_qty = self.cfg.get("points", {}).get("数量输入框", {"x": 0, "y": 0})
        self.var_qty_x = tk.IntVar(value=int(p_qty.get("x", 0)))
        self.var_qty_y = tk.IntVar(value=int(p_qty.get("y", 0)))
        ttk.Label(box_pos, text="数量输入框").grid(row=1, column=0, padx=4, pady=4, sticky="e")
        ttk.Entry(box_pos, textvariable=self.var_qty_x, width=8).grid(row=1, column=1)
        ttk.Entry(box_pos, textvariable=self.var_qty_y, width=8).grid(row=1, column=2)
        ttk.Button(box_pos, text="捕获", command=lambda: self._capture_point(self.var_qty_x, self.var_qty_y, label="请将鼠标移动到 数量输入框 上…")).grid(row=1, column=3, padx=4)

        for i in range(4):
            box_pos.columnconfigure(i, weight=1)

        # ROI 分组变量
        for v in [
            self.var_roi_top_tpl,
            self.var_roi_top_thr,
            self.var_roi_btm_tpl,
            self.var_roi_btm_thr,
            self.var_roi_top_off,
            self.var_roi_btm_off,
            self.var_roi_lr_pad,
        ]:
            try:
                v.trace_add("write", lambda *_: self._schedule_autosave())
            except Exception:
                pass

        # 单点坐标变量
        for v in [self.var_first_x, self.var_first_y, self.var_qty_x, self.var_qty_y]:
            try:
                v.trace_add("write", lambda *_: self._schedule_autosave())
            except Exception:
                pass

    def _capture_point(self, var_x: tk.IntVar, var_y: tk.IntVar, *, label: str) -> None:
        # Simple countdown prompt
        top = tk.Toplevel(self)
        top.title("捕获坐标")
        ttk.Label(top, text=label).pack(padx=10, pady=8)
        lb = ttk.Label(top, text="3")
        lb.pack(pady=6)

        def countdown(n: int):
            if n <= 0:
                try:
                    import pyautogui  # type: ignore
                    x, y = pyautogui.position()
                    var_x.set(int(x)); var_y.set(int(y))
                except Exception as e:
                    messagebox.showerror("捕获坐标", f"失败: {e}")
                top.destroy()
                return
            lb.config(text=str(n))
            self.after(1000, lambda: countdown(n - 1))

        countdown(3)

    def _save_and_sync(self, *, silent: bool = False) -> None:
        # Flush templates
        for key, row in self.template_rows.items():
            self.cfg.setdefault("templates", {}).setdefault(key, {})
            self.cfg["templates"][key]["path"] = row.get_path()
            self.cfg["templates"][key]["confidence"] = float(row.get_confidence())

        # Flush ROI config
        self.cfg.setdefault("price_roi", {})
        self.cfg["price_roi"]["top_template"] = self.var_roi_top_tpl.get().strip()
        self.cfg["price_roi"]["top_threshold"] = float(self.var_roi_top_thr.get() or 0.55)
        self.cfg["price_roi"]["bottom_template"] = self.var_roi_btm_tpl.get().strip()
        self.cfg["price_roi"]["bottom_threshold"] = float(self.var_roi_btm_thr.get() or 0.55)
        self.cfg["price_roi"]["top_offset"] = int(self.var_roi_top_off.get() or 0)
        self.cfg["price_roi"]["bottom_offset"] = int(self.var_roi_btm_off.get() or 0)
        self.cfg["price_roi"]["lr_pad"] = int(self.var_roi_lr_pad.get() or 0)

        # Flush average price area (distance/height)
        try:
            self.cfg.setdefault("avg_price_area", {})
            self.cfg["avg_price_area"]["distance_from_buy_top"] = int(self.var_avg_dist.get() or 0)
            self.cfg["avg_price_area"]["height"] = int(self.var_avg_height.get() or 0)
        except Exception:
            pass

        # Flush points
        self.cfg.setdefault("points", {})
        self.cfg["points"]["第一个商品"] = {"x": int(self.var_first_x.get()), "y": int(self.var_first_y.get())}
        self.cfg["points"]["数量输入框"] = {"x": int(self.var_qty_x.get()), "y": int(self.var_qty_y.get())}

        save_config(self.cfg, "config.json")
        sync_to_key_mapping(self.cfg, mapping_path="key_mapping.json")
        if not silent:
            messagebox.showinfo("配置", "已保存并同步至 key_mapping.json")

    # ---------- Region selection & Modal image preview ----------

    # ---------- ROI config helpers ----------
    def _pick_file_into(self, var: tk.StringVar) -> None:
        path = filedialog.askopenfilename(title="选择图片", filetypes=[("Image", ".png .jpg .jpeg .bmp"), ("All", "*.*")])
        if path:
            var.set(path)

    # ---------- ROI preview using templates ----------
    def _roi_preview_from_screen(self) -> None:
        # Validate template paths
        top_path = self.var_roi_top_tpl.get().strip()
        btm_path = self.var_roi_btm_tpl.get().strip()
        if not top_path or not os.path.exists(top_path):
            messagebox.showwarning("预览", "顶部模板未选择或文件不存在。")
            return
        if not btm_path or not os.path.exists(btm_path):
            messagebox.showwarning("预览", "底部模板未选择或文件不存在。")
            return
        try:
            import pyautogui  # type: ignore
            import numpy as _np  # type: ignore
            import cv2 as _cv2  # type: ignore
        except Exception as e:
            messagebox.showerror("预览", f"缺少依赖或导入失败: {e}")
            return
        try:
            img_pil = pyautogui.screenshot()
        except Exception as e:
            messagebox.showerror("预览", f"截屏失败: {e}")
            return
        # PIL -> OpenCV BGR
        try:
            img_rgb = _np.array(img_pil)
            img_bgr = img_rgb[:, :, ::-1].copy()
        except Exception as e:
            messagebox.showerror("预览", f"图像转换失败: {e}")
            return

        # Prepare
        gray = _cv2.cvtColor(img_bgr, _cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        top_tmpl = _cv2.imread(str(top_path), _cv2.IMREAD_COLOR)
        btm_tmpl = _cv2.imread(str(btm_path), _cv2.IMREAD_COLOR)
        if top_tmpl is None or btm_tmpl is None:
            messagebox.showwarning("预览", "无法读取模板图片。")
            return
        tthr = float(self.var_roi_top_thr.get() or 0.55)
        bthr = float(self.var_roi_btm_thr.get() or 0.55)

        # Search regions: top in upper 50%，bottom in lower 65%
        top_roi = (0, 0, w, int(h * 0.5))
        btm_roi = (0, int(h * 0.35), w, int(h * 0.65))

        # Match top template
        tgray = _cv2.cvtColor(top_tmpl, _cv2.COLOR_BGR2GRAY) if top_tmpl.ndim == 3 else top_tmpl
        (tx, ty), ts = self._roi_match_template(gray, tgray, top_roi, method=_cv2.TM_CCOEFF_NORMED)
        # Match bottom template
        bgray = _cv2.cvtColor(btm_tmpl, _cv2.COLOR_BGR2GRAY) if btm_tmpl.ndim == 3 else btm_tmpl
        (bx, by), bs = self._roi_match_template(gray, bgray, btm_roi, method=_cv2.TM_CCOEFF_NORMED)

        if ts < tthr or bs < bthr:
            messagebox.showwarning("预览", f"模板匹配失败：top={ts:.2f} (阈值 {tthr:.2f}), bottom={bs:.2f} (阈值 {bthr:.2f})")
            return

        # Compute four anchor points
        tw, th = tgray.shape[1], tgray.shape[0]
        bw, bh = bgray.shape[1], bgray.shape[0]
        # top template bottom edge
        top_bl = (int(tx), int(ty + th - 1))
        top_br = (int(tx + tw - 1), int(ty + th - 1))
        # bottom template top edge
        btm_tl = (int(bx), int(by))
        btm_tr = (int(bx + bw - 1), int(by))

        # Determine rectangle based on the longer width
        pick_top = tw >= bw
        if pick_top:
            rx1, rx2 = top_bl[0], top_br[0]
        else:
            rx1, rx2 = btm_tl[0], btm_tr[0]
        ry1 = top_bl[1]  # bottom of top template
        ry2 = btm_tl[1]  # top of bottom template

        # Apply offsets and LR pad
        try:
            ry1 += int(self.var_roi_top_off.get() or 0)
            ry2 += int(self.var_roi_btm_off.get() or 0)
            pad = int(self.var_roi_lr_pad.get() or 0)
        except Exception:
            pad = 0
        rx1 -= pad
        rx2 += pad

        # Normalize coordinates
        x_left = max(0, min(rx1, rx2))
        x_right = min(w - 1, max(rx1, rx2))
        y_top = max(0, min(ry1, ry2))
        y_bot = min(h - 1, max(ry1, ry2))
        if y_bot - y_top < 3:
            y_bot = min(h - 1, y_top + 3)

        # Save outputs and draw debug
        os.makedirs("images", exist_ok=True)
        crop = img_bgr[y_top:y_bot, x_left:x_right]
        crop_path = os.path.join("images", "price_area_roi.png")
        _cv2.imwrite(crop_path, crop)

        # Skip debug image generation; only preview the crop

        # 预览：左右并排显示 调试图 与 截取图（固定大小、不可调整）
        try:
            self._preview_roi_simple(crop_path)
        except Exception as e:
            messagebox.showerror("预览", f"显示失败: {e}")

    @staticmethod
    def _roi_match_template(gray, tmpl_gray, search_roi=None, method=0):
        try:
            import cv2 as _cv2  # type: ignore
        except Exception:
            return (0, 0), 0.0
        if search_roi is not None:
            x, y, w, h = search_roi
            region = gray[y : y + h, x : x + w]
        else:
            x = y = 0
            region = gray
        if method == 0:
            method = _cv2.TM_CCOEFF_NORMED
        res = _cv2.matchTemplate(region, tmpl_gray, method)
        _, max_val, _, max_loc = _cv2.minMaxLoc(res)
        top_left = (max_loc[0] + x, max_loc[1] + y)
        return top_left, float(max_val)

    def _roi_locate_top(self, gray, bin_img, tmpl_bgr, thr: float):
        try:
            import cv2 as _cv2  # type: ignore
            import numpy as _np  # type: ignore
            from extract_price_roi import detect_horizontal_lines as _det_hlines  # type: ignore
        except Exception:
            return None
        h, w = gray.shape[:2]
        tmpl_gray = _cv2.cvtColor(tmpl_bgr, _cv2.COLOR_BGR2GRAY) if tmpl_bgr.ndim == 3 else tmpl_bgr
        roi = (0, 0, w, int(h * 0.35))
        (tx, ty), score = self._roi_match_template(gray, tmpl_gray, roi)
        if score < float(thr or 0.0):
            return None
        y_after_template = ty + tmpl_gray.shape[0]
        band_top = min(h - 1, y_after_template + 1)
        band_bot = min(h, y_after_template + max(12, int(h * 0.03)))
        if band_bot <= band_top:
            return None
        sub = bin_img[band_top:band_bot, :]
        lines = _det_hlines(sub, min_rel_len=0.5)
        if not lines:
            proj = sub.sum(axis=1)
            y_local = int(_np.argmax(proj))
            return band_top + y_local
        y_local = min(l.y for l in lines)
        return band_top + y_local

    def _roi_locate_bottom(self, gray, tmpl_bgr, thr: float):
        try:
            import cv2 as _cv2  # type: ignore
        except Exception:
            return None
        h, w = gray.shape[:2]
        tmpl_gray = _cv2.cvtColor(tmpl_bgr, _cv2.COLOR_BGR2GRAY) if tmpl_bgr.ndim == 3 else tmpl_bgr
        roi = (0, int(h * 0.35), w, int(h * 0.55))
        (bx, by), score = self._roi_match_template(gray, tmpl_gray, roi)
        if score < float(thr or 0.0):
            return None
        return int(by + tmpl_gray.shape[0] // 2 - 20)

    @staticmethod
    def _roi_find_buy_button_top(hsv):
        try:
            import numpy as _np  # type: ignore
            import cv2 as _cv2  # type: ignore
        except Exception:
            return None
        h, w = hsv.shape[:2]
        lower1 = _np.array([5, 80, 120], dtype=_np.uint8)
        upper1 = _np.array([25, 255, 255], dtype=_np.uint8)
        mask = _cv2.inRange(hsv, lower1, upper1)
        mask = _cv2.morphologyEx(mask, _cv2.MORPH_CLOSE, _np.ones((5, 15), _np.uint8), iterations=2)
        contours, _ = _cv2.findContours(mask, _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)
        cand = []
        for c in contours:
            x, y, cw, ch = _cv2.boundingRect(c)
            area = cw * ch
            ar = cw / max(1.0, float(ch))
            if y > int(h * 0.6) and area > (w * h) * 0.002 and ar > 2.0:
                cand.append((area, y))
        if not cand:
            return None
        cand.sort(reverse=True)
        return int(cand[0][1])

    # ---------- Region selection & Modal image preview ----------
    def _select_region(self, on_done):
        sel = _RegionSelector(self, on_done)
        sel.show()

    def _template_slug(self, name: str) -> str:
        slug = self._tpl_slug_map.get(name)
        if slug:
            return slug
        # fallback: ascii-only slug from hash
        return f"tpl_{abs(hash(name)) % 100000}"

    def _preview_image(self, path: str, title: str = "预览") -> None:
        if not path or not os.path.exists(path):
            messagebox.showwarning("预览", "图片不存在或路径为空。")
            return
        top = tk.Toplevel(self)
        top.title(title)
        top.transient(self)
        top.grab_set()
        frm = ttk.Frame(top)
        frm.pack(fill=tk.BOTH, expand=True)
        try:
            from PIL import Image, ImageTk  # type: ignore
            img = Image.open(path)
            max_w, max_h = 900, 600
            w, h = img.size
            scale = min(max_w / max(1, w), max_h / max(1, h), 1.0)
            if scale < 1.0:
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            tkimg = ImageTk.PhotoImage(img)
            lbl = ttk.Label(frm, image=tkimg)
            lbl.image = tkimg  # keep ref
            lbl.pack(padx=10, pady=10)
        except Exception:
            # Fallback to PhotoImage for PNG
            try:
                pimg = tk.PhotoImage(file=path)
                lbl = ttk.Label(frm, image=pimg)
                lbl.image = pimg
                lbl.pack(padx=10, pady=10)
            except Exception as e:
                ttk.Label(frm, text=f"无法加载图片: {e}").pack(padx=10, pady=10)
        ttk.Button(frm, text="关闭", command=top.destroy).pack(pady=(0, 10))

    def _preview_roi_simple(self, crop_path: str) -> None:
        # Simplified preview: left (crop image), right (OCR text + timing)
        if not os.path.exists(crop_path):
            messagebox.showwarning("预览", "截取图不存在。")
            return
        # Run OCR first to get timing/text
        try:
            _ann, ocr_texts, ocr_scores, ocr_ms = self._run_paddle_ocr(crop_path)
        except Exception as e:
            ocr_texts, ocr_scores, ocr_ms = [], [], -1.0

        top = tk.Toplevel(self)
        try:
            top.title("预览 - 价格区域（截取图 + OCR结果）")
        except Exception:
            pass
        top.transient(self)
        try:
            top.grab_set()
        except Exception:
            pass

        # Layout metrics: one image + one text panel
        margin = 12
        img_w, img_h = 520, 560
        gap = 12
        pane_w = 420
        total_w = margin + img_w + gap + pane_w + margin
        total_h = margin + img_h + 70 + margin
        try:
            top.geometry(f"{total_w}x{total_h}")
            top.resizable(False, False)
        except Exception:
            pass

        frm = ttk.Frame(top)
        frm.pack(fill=tk.BOTH, expand=True)

        # Header row: labels and time
        header = ttk.Frame(frm)
        header.pack(fill=tk.X, padx=margin, pady=(margin, 4))
        ttk.Label(header, text="截取图").pack(side=tk.LEFT)
        lab_time = ttk.Label(header, text=(f"OCR耗时: {int(ocr_ms)} ms" if ocr_ms >= 0 else "OCR耗时: -"))
        lab_time.pack(side=tk.RIGHT)

        body = ttk.Frame(frm)
        body.pack(fill=tk.BOTH, expand=True, padx=margin)

        # Left: crop image canvas
        cv = tk.Canvas(body, width=img_w, height=img_h, highlightthickness=1, highlightbackground="#888")
        cv.pack(side=tk.LEFT)

        # Right: text results
        right = ttk.Frame(body, width=pane_w)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(gap, 0))
        ttk.Label(right, text="OCR结果").pack(anchor="w")
        txt = tk.Text(right, height=28, wrap="word")
        txt.pack(fill=tk.BOTH, expand=True)

        # Load crop image
        try:
            from PIL import Image, ImageTk  # type: ignore
            crop = Image.open(crop_path).convert("RGB")
            w0, h0 = crop.size
            sc = min(img_w / max(1.0, float(w0)), img_h / max(1.0, float(h0)))
            nw, nh = max(1, int(w0 * sc)), max(1, int(h0 * sc))
            crop_fit = crop.resize((nw, nh), Image.LANCZOS)
            tk_crop = ImageTk.PhotoImage(crop_fit)
            x = (img_w - tk_crop.width()) // 2
            y = (img_h - tk_crop.height()) // 2
            cv.create_image(x, y, image=tk_crop, anchor=tk.NW)
            cv.image = tk_crop
        except Exception as e:
            try:
                cv.create_text(10, 10, anchor=tk.NW, text=f"加载截取图失败: {e}")
            except Exception:
                pass

        # Compose troubleshooting-friendly text
        from pathlib import Path as _Path
        _stem = _Path(crop_path).stem
        _json_path = os.path.join("output", f"{_stem}_res.json")
        meta_lines = [
            f"文件: {crop_path}",
            f"耗时: {int(ocr_ms) if ocr_ms>=0 else '-'} ms",
            f"结果数: {len(ocr_texts)}",
            f"JSON: {_json_path}",
            "",
        ]
        body_lines = []
        if ocr_texts:
            for i, t in enumerate(ocr_texts):
                s = ocr_scores[i] if i < len(ocr_scores) else None
                if s is None:
                    body_lines.append(f"[{i+1:02d}] {t}")
                else:
                    body_lines.append(f"[{i+1:02d}] {t}  (score={s:.3f})")
        else:
            body_lines.append("未识别到文本；可能是 ROI 太小/对比度低/字体异常。")
            body_lines.append("建议：放大 ROI、提升清晰度或对比度后再试。")
        try:
            txt.insert("1.0", "\n".join(meta_lines + body_lines))
            txt.configure(state=tk.DISABLED)
        except Exception:
            pass

        footer = ttk.Frame(frm)
        footer.pack(fill=tk.X, padx=margin, pady=(8, margin))
        ttk.Button(footer, text="关闭", command=top.destroy).pack(side=tk.RIGHT)
    def _run_paddle_ocr(self, img_path: str):
        """Run PaddleOCR and return (annotated_img_path, texts, scores, elapsed_ms)."""
        ocr = self._ensure_paddle_ocr()
        os.makedirs("output", exist_ok=True)
        t0 = time.perf_counter()
        result = ocr.predict(input=img_path)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        texts: list[str] = []
        scores: list[float] = []
        ann_path = ""
        from pathlib import Path
        stem = Path(img_path).stem
        json_path = os.path.join("output", f"{stem}_res.json")
        ann_path = os.path.join("output", f"{stem}_ocr_res_img.png")
        for r in result:
            try:
                r.save_to_img("output")
            except Exception:
                pass
            try:
                r.save_to_json("output")
            except Exception:
                pass
        try:
            if os.path.exists(json_path):
                import json
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                texts = list(map(str, data.get("rec_texts", []) or []))
                scores = [float(x) for x in (data.get("rec_scores", []) or [])]
        except Exception:
            pass
        return ann_path, texts, scores, float(elapsed_ms)

    def _ensure_paddle_ocr(self):
        """Ensure a single PaddleOCR instance is initialized and returned."""
        try:
            from paddleocr import PaddleOCR  # type: ignore
        except Exception as e:
            raise RuntimeError(f"PaddleOCR 导入失败: {e}")

        if getattr(self, "_paddle_ocr", None) is None:
            try:
                self._ocr_lock.acquire()
                if self._paddle_ocr is None:
                    # Force CPU-only initialization, using default models.
                    try:
                        self._paddle_ocr = PaddleOCR(
                            device="cpu",
                            use_doc_orientation_classify=False,
                            use_doc_unwarping=False,
                            use_textline_orientation=False,
                        )
                    except TypeError:
                        self._paddle_ocr = PaddleOCR(
                            use_gpu=False,
                            use_doc_orientation_classify=False,
                            use_doc_unwarping=False,
                            use_textline_orientation=False,
                        )
            finally:
                try:
                    self._ocr_lock.release()
                except Exception:
                    pass
        return self._paddle_ocr

    def _start_ocr_warmup(self) -> None:
        if getattr(self, "_ocr_warm_started", False):
            return
        self._ocr_warm_started = True
        try:
            t = threading.Thread(target=self._warmup_paddle_ocr, name="ocr-warmup", daemon=True)
            t.start()
        except Exception:
            self._ocr_warm_started = False

    def _warmup_paddle_ocr(self) -> None:
        try:
            ocr = self._ensure_paddle_ocr()
            # Tiny inference to init graph; ignore outputs
            try:
                from PIL import Image  # type: ignore
                os.makedirs("output", exist_ok=True)
                warm_img = os.path.join("output", "_warmup_ocr.png")
                if not os.path.exists(warm_img):
                    img = Image.new("RGB", (64, 24), color=(255, 255, 255))
                    img.save(warm_img)
                _ = ocr.predict(input=warm_img)
            except Exception:
                pass
            self._ocr_warm_ready = True
        except Exception:
            self._ocr_warm_ready = False

    def _avg_price_roi_preview(self) -> None:
        try:
            import pyautogui  # type: ignore
            from PIL import Image  # type: ignore
        except Exception as e:
            messagebox.showerror("预览", f"缺少依赖或导入失败: {e}")
            return

        buy_row = None
        # early fallback by filename contains 'btn_buy'
        try:
            for _name, _row in self.template_rows.items():
                _p = (_row.get_path() or "").lower()
                if os.path.basename(_p).startswith("btn_buy") or "btn_buy" in _p:
                    buy_row = _row
                    break
        except Exception:
            pass
        for name, row in self.template_rows.items():
            try:
                slug = self._template_slug(name)
            except Exception:
                slug = None
            if slug == "btn_buy":
                buy_row = row
                break
        if buy_row is None:
            messagebox.showwarning("预览", "未在模板管理中找到‘购买按钮’模板，请先配置并截图保存。")
            return
        path = buy_row.get_path()
        if not path or not os.path.exists(path):
            messagebox.showwarning("预览", "‘购买按钮’模板路径为空或文件不存在。")
            return
        try:
            conf = float(buy_row.get_confidence() or 0.85)
        except Exception:
            conf = 0.85

        # 与“识别测试”保持一致：先使用 locateCenterOnScreen，再回退到 locateOnScreen
        center = None
        box = None
        try:
            center = pyautogui.locateCenterOnScreen(path, confidence=conf)
        except Exception:
            center = None
        diag = []
        try:
            _ = center.x  # type: ignore[attr-defined]
            diag.append("center=ok")
        except Exception:
            diag.append("center=none")
        if center is None:
            try:
                box = pyautogui.locateOnScreen(path, confidence=conf)
            except Exception as e:
                messagebox.showerror("预览", f"模板匹配失败: {e}")
                return
        if center is None and not box:
            try:
                flow = "center=none"
                if 'diag' in locals():
                    flow = " / ".join(diag)
            except Exception:
                flow = "center=none"
            messagebox.showwarning("预览", f"未匹配到购买按钮\n路径: {path}\n阈值: {conf:.2f}\n流程: {flow}\n建议: 1) DPI=100% 2) 重新截图 3) 降低阈值")
            return

        # 计算按钮矩形
        if center is not None:
            # 用模板尺寸推断矩形
            try:
                from PIL import Image as _Image  # type: ignore
                _img = _Image.open(path)
                tpl_w, tpl_h = _img.size
            except Exception:
                tpl_w, tpl_h = 120, 40  # 兜底尺寸
            b_w, b_h = int(tpl_w), int(tpl_h)
            try:
                cx, cy = int(center.x), int(center.y)
            except Exception:
                cx, cy = int(getattr(center, 'x', 0)), int(getattr(center, 'y', 0))
            b_left = int(cx - b_w // 2)
            b_top = int(cy - b_h // 2)
        else:
            try:
                b_left, b_top, b_w, b_h = int(box.left), int(box.top), int(box.width), int(box.height)
            except Exception:
                b_left = int(getattr(box, 'left', 0))
                b_top = int(getattr(box, 'top', 0))
                b_w = int(getattr(box, 'width', 0))
                b_h = int(getattr(box, 'height', 0))
        if b_w <= 1 or b_h <= 1:
            messagebox.showwarning("预览", "检测到的按钮尺寸异常，请重新截图模板。")
            return

        try:
            dist = int(self.var_avg_dist.get() or 0)
            hei = max(1, int(self.var_avg_height.get() or 1))
        except Exception:
            dist, hei = 160, 100
        y_bottom = b_top - dist
        y_top = y_bottom - hei
        x_left = b_left
        width = b_w

        scr_w = self.winfo_screenwidth()
        scr_h = self.winfo_screenheight()
        y_top = max(0, min(scr_h - 2, y_top))
        y_bottom = max(y_top + 1, min(scr_h - 1, y_bottom))
        x_left = max(0, min(scr_w - 2, x_left))
        width = max(1, min(width, scr_w - x_left))
        height = max(1, y_bottom - y_top)

        # 记录ROI与屏幕/按钮信息
        try:
            self._append_log(
                f"[平均单价预览] 屏幕={scr_w}x{scr_h} 按钮=({b_left},{b_top},{b_w},{b_h}) "
                f"ROI=({x_left},{y_top},{width},{height}) 参数: 距离={dist} 高度={hei}"
            )
        except Exception:
            pass

        try:
            img = pyautogui.screenshot(region=(x_left, y_top, width, height))
        except Exception as e:
            messagebox.showerror("预览", f"截图失败: {e}")
            return
        os.makedirs("images", exist_ok=True)
        crop_path = os.path.join("images", "_avg_price_roi.png")
        try:
            img.save(crop_path)
        except Exception as e:
            messagebox.showerror("预览", f"保存截图失败: {e}")
            return

        raw_text = ""
        cleaned = ""
        parsed_val = None
        elapsed_ms = -1.0
        try:
            import time as _time
            import pytesseract  # type: ignore
            try:
                from price_reader import _maybe_init_tesseract  # type: ignore
                _maybe_init_tesseract()
            except Exception:
                pass
            allow = str(self.cfg.get("avg_price_area", {}).get("ocr_allowlist", "0123456789KM"))
            # Ensure both K/M (upper & lower) are included
            need = "KMkm"
            allow_ex = allow + "".join(ch for ch in need if ch not in allow)
            cfg = f"--oem 3 --psm 6 -c tessedit_char_whitelist={allow_ex}"
            t0 = _time.perf_counter()
            raw_text = pytesseract.image_to_string(img, config=cfg) or ""
            elapsed_ms = (_time.perf_counter() - t0) * 1000.0
            try:
                self._append_log(f"[平均单价预览] OCR耗时={int(elapsed_ms)}ms 原始='{(raw_text or '').strip()}'")
            except Exception:
                pass
            up = raw_text.upper()
            cleaned = "".join(ch for ch in up if ch in "0123456789KM")
            t = cleaned.strip().upper()
            if t.endswith("K"):
                digits = "".join(ch for ch in t[:-1] if ch.isdigit())
                if digits:
                    parsed_val = int(digits) * 1000
            else:
                digits = "".join(ch for ch in t if ch.isdigit())
                if digits:
                    parsed_val = int(digits)
            try:
                self._append_log(f"[平均单价预览] 清洗='{cleaned}' 数值={parsed_val if parsed_val is not None else '-'}")
            except Exception:
                pass
        except Exception as e:
            raw_text = f"[OCR] 失败: {e}"
            cleaned = ""
            parsed_val = None

        try:
            self._preview_avg_price_window(crop_path, raw_text, cleaned, parsed_val, elapsed_ms)
        except Exception as e:
            messagebox.showerror("预览", f"显示失败: {e}")

    def _preview_avg_price_window(self, crop_path: str, raw_text: str, cleaned: str, parsed_val, elapsed_ms: float) -> None:
        if not os.path.exists(crop_path):
            messagebox.showwarning("预览", "ROI 截图不存在。")
            return
        top = tk.Toplevel(self)
        try:
            top.title("预览 - 平均单价区域（截图 + PyTesseract OCR）")
        except Exception:
            pass
        top.transient(self)
        try:
            top.grab_set()
        except Exception:
            pass

        margin = 12
        img_w, img_h = 520, 560
        gap = 12
        pane_w = 460
        total_w = margin + img_w + gap + pane_w + margin
        total_h = margin + img_h + 70 + margin
        try:
            top.geometry(f"{total_w}x{total_h}")
            top.resizable(False, False)
        except Exception:
            pass

        frm = ttk.Frame(top)
        frm.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(frm)
        header.pack(fill=tk.X, padx=margin, pady=(margin, 4))
        ttk.Label(header, text="ROI 截图").pack(side=tk.LEFT)
        lab_time = ttk.Label(header, text=(f"OCR耗时: {int(elapsed_ms)} ms" if elapsed_ms >= 0 else "OCR耗时: -"))
        lab_time.pack(side=tk.RIGHT)

        body = ttk.Frame(frm)
        body.pack(fill=tk.BOTH, expand=True, padx=margin)

        cv = tk.Canvas(body, width=img_w, height=img_h, highlightthickness=1, highlightbackground="#888")
        cv.pack(side=tk.LEFT)

        right = ttk.Frame(body, width=pane_w)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(gap, 0))
        ttk.Label(right, text="OCR结果（原始 与 预览）").pack(anchor="w")
        txt = tk.Text(right, height=28, wrap="word")
        txt.pack(fill=tk.BOTH, expand=True)

        try:
            from PIL import Image, ImageTk  # type: ignore
            crop = Image.open(crop_path).convert("RGB")
            w0, h0 = crop.size
            sc = min(img_w / max(1.0, float(w0)), img_h / max(1.0, float(h0)))
            nw, nh = max(1, int(w0 * sc)), max(1, int(h0 * sc))
            crop_fit = crop.resize((nw, nh), Image.LANCZOS)
            tk_crop = ImageTk.PhotoImage(crop_fit)
            x = (img_w - tk_crop.width()) // 2
            y = (img_h - tk_crop.height()) // 2
            cv.create_image(x, y, image=tk_crop, anchor=tk.NW)
            cv.image = tk_crop
        except Exception as e:
            try:
                cv.create_text(10, 10, anchor=tk.NW, text=f"加载截图失败: {e}")
            except Exception:
                pass

        # Build preview as: 清洗前: <unit> <total>  清洗后: {json}
        raw_disp = raw_text.strip() if isinstance(raw_text, str) else raw_text
        try:
            lines_raw = [ln.strip() for ln in (raw_text or "").splitlines() if ln.strip()]
        except Exception:
            lines_raw = []
        raw_unit = lines_raw[0] if len(lines_raw) >= 1 else ""
        raw_total = lines_raw[1] if len(lines_raw) >= 2 else ""

        def _parse_val(s: str):
            try:
                up = str(s).upper()
                mult = 1_000_000 if ("M" in up) else (1000 if ("K" in up) else 1)
                digits = "".join(ch for ch in up if ch.isdigit())
                return (int(digits) * mult) if digits else None
            except Exception:
                return None

        val_unit = _parse_val(raw_unit)
        val_total = _parse_val(raw_total)
        try:
            import json as _json
            json_str = _json.dumps({
                "unit_price_raw": raw_unit,
                "total_price_raw": raw_total,
                "unit_price": val_unit,
                "total_price": val_total,
            }, ensure_ascii=False)
        except Exception:
            json_str = f"{{'unit_price_raw': '{raw_unit}', 'total_price_raw': '{raw_total}', 'unit_price': {val_unit}, 'total_price': {val_total}}}"

        pre_clean_str = f"{raw_unit} {raw_total}".strip()
        lines = [
            f"文件: {crop_path}",
            f"原始:",
            f"{raw_disp}",
            f"清洗前: {pre_clean_str if pre_clean_str else '-'}",
            f"清洗后: {json_str}",
        ]
        try:
            txt.insert("1.0", "\n".join(lines))
            txt.configure(state=tk.DISABLED)
        except Exception:
            pass

        footer = ttk.Frame(frm)
        footer.pack(fill=tk.X, padx=margin, pady=(8, margin))
        ttk.Button(footer, text="关闭", command=top.destroy).pack(side=tk.RIGHT)

    # ---------- Tab2 ----------
    def _build_tab2(self) -> None:
        outer = self.tab2
        main = ttk.Frame(outer)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Left: items list
        left = ttk.Frame(main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        cols = ("enabled", "name", "thr", "target", "max", "purchased")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=10)
        self.tree.heading("enabled", text="启用(点切换)")
        self.tree.heading("name", text="商品")
        self.tree.heading("thr", text="阈值")
        self.tree.heading("target", text="目标")
        self.tree.heading("max", text="每单上限")
        self.tree.heading("purchased", text="进度")
        self.tree.column("enabled", width=46, anchor="center")
        self.tree.column("name", width=160)
        self.tree.column("thr", width=70, anchor="e")
        self.tree.column("target", width=80, anchor="e")
        self.tree.column("max", width=90, anchor="e")
        self.tree.column("purchased", width=100, anchor="e")
        self.tree.pack(fill=tk.BOTH, expand=True)

        # Selection change updates progress bar
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_selected_progress())
        # Toggle enable on left-click first column
        self.tree.bind("<Button-1>", self._tree_on_click, add=True)
        # Open editor modal on double-click
        self.tree.bind("<Double-1>", self._tree_on_double_click)
        # Context menu: right-click
        self._ctx_menu = tk.Menu(self, tearoff=0)
        self._ctx_menu.add_command(label="编辑…", command=lambda: self._open_item_modal(self._get_clicked_index()))
        self._ctx_menu.add_command(label="删除", command=self._delete_item)
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="启用/禁用", command=self._toggle_item_enable)
        self.tree.bind("<Button-3>", self._on_tree_right_click)

        # Bottom controls
        ctrl = ttk.Frame(outer)
        ctrl.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(ctrl, text="新增…", command=lambda: self._open_item_modal(None)).pack(side=tk.LEFT)
        ttk.Button(ctrl, text="开始", command=self._start_multi).pack(side=tk.LEFT, padx=6)
        ttk.Button(ctrl, text="停止", command=self._stop).pack(side=tk.LEFT, padx=6)

        # Progress + Log
        progf = ttk.Frame(outer)
        progf.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Label(progf, text="当前选中 进度").pack(side=tk.LEFT)
        self.sel_prog = ttk.Progressbar(progf, orient=tk.HORIZONTAL, mode="determinate", length=220)
        self.sel_prog.pack(side=tk.LEFT, padx=8)
        self.sel_prog_lab = ttk.Label(progf, text="0/0")
        self.sel_prog_lab.pack(side=tk.LEFT)

        logf = ttk.LabelFrame(outer, text="运行日志")
        logf.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.txt = tk.Text(logf, height=12, wrap="word")
        self.txt.pack(fill=tk.BOTH, expand=True)
        self.txt.configure(state=tk.DISABLED)

        # Load items from config
        self._load_items_from_cfg()

    # ---------- Tab3: OCR Lab ----------
    def _build_tab3(self) -> None:
        outer = self.tab3
        frm = ttk.Frame(outer)
        frm.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Top controls
        ctrl = ttk.Frame(frm)
        ctrl.pack(fill=tk.X)

        ttk.Label(ctrl, text="图片路径").pack(side=tk.LEFT)
        self.var_lab_img = tk.StringVar(value="")
        ent = ttk.Entry(ctrl, textvariable=self.var_lab_img, width=60)
        ent.pack(side=tk.LEFT, padx=6)
        ttk.Button(ctrl, text="选择…", command=self._lab_pick_image).pack(side=tk.LEFT)

        # Params row 1 - split + zoom
        p1 = ttk.Frame(frm)
        p1.pack(fill=tk.X, pady=(8, 4))
        ttk.Label(p1, text="左右分割阈值").pack(side=tk.LEFT)
        self.var_lab_split = tk.DoubleVar(value=0.50)
        # 使用 tk.Scale 支持 resolution 步进更细腻
        s = tk.Scale(p1, from_=0.30, to=0.70, resolution=0.01, orient=tk.HORIZONTAL, showvalue=False,
                     variable=self.var_lab_split, command=lambda _=None: self._lab_render(), length=260)
        s.pack(side=tk.LEFT, padx=6)
        self.lab_split_val = ttk.Label(p1, text="0.50")
        self.lab_split_val.pack(side=tk.LEFT, padx=(0, 12))
        ttk.Label(p1, text="缩放").pack(side=tk.LEFT)
        self.var_lab_zoom = tk.DoubleVar(value=1.5)
        try:
            sp = tk.Spinbox(p1, from_=0.5, to=3.0, increment=0.1, textvariable=self.var_lab_zoom, width=5, command=self._lab_render)
        except Exception:
            sp = ttk.Entry(p1, textvariable=self.var_lab_zoom, width=6)
        sp.pack(side=tk.LEFT, padx=6)

        # Params row 2
        p2 = ttk.Frame(frm)
        p2.pack(fill=tk.X, pady=(4, 4))
        ttk.Label(p2, text="价格范围").pack(side=tk.LEFT)
        self.var_lab_price_min = tk.IntVar(value=10)
        self.var_lab_price_max = tk.IntVar(value=10_000_000)
        ttk.Entry(p2, textvariable=self.var_lab_price_min, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Label(p2, text="~").pack(side=tk.LEFT)
        ttk.Entry(p2, textvariable=self.var_lab_price_max, width=10).pack(side=tk.LEFT, padx=2)
        ttk.Label(p2, text="数量范围").pack(side=tk.LEFT, padx=(12, 0))
        self.var_lab_qty_min = tk.IntVar(value=0)
        self.var_lab_qty_max = tk.IntVar(value=1_000_000)
        ttk.Entry(p2, textvariable=self.var_lab_qty_min, width=8).pack(side=tk.LEFT, padx=2)
        ttk.Label(p2, text="~").pack(side=tk.LEFT)
        ttk.Entry(p2, textvariable=self.var_lab_qty_max, width=10).pack(side=tk.LEFT, padx=2)

        # Params row 3
        p3 = ttk.Frame(frm)
        p3.pack(fill=tk.X, pady=(4, 8))
        ttk.Label(p3, text="显示变体").pack(side=tk.LEFT)
        self.var_lab_variant = tk.StringVar(value="auto")
        self.cmb_variant = ttk.Combobox(p3, textvariable=self.var_lab_variant, state="readonly", values=["auto", "raw"], width=14)
        self.cmb_variant.pack(side=tk.LEFT, padx=6)
        self.cmb_variant.bind("<<ComboboxSelected>>", lambda _e: self._lab_render())
        ttk.Button(p3, text="重新计算", command=self._lab_compute_variants).pack(side=tk.LEFT)
        ttk.Button(p3, text="导出当前标注图", command=self._lab_save_annotated).pack(side=tk.LEFT, padx=6)
        # Auto split & refine options
        self.var_lab_auto_split = tk.BooleanVar(value=True)
        ttk.Checkbutton(p3, text="自动分割", variable=self.var_lab_auto_split, command=self._lab_render).pack(side=tk.LEFT, padx=(12, 0))
        self.var_lab_refine = tk.BooleanVar(value=True)
        ttk.Checkbutton(p3, text="裁剪细读", variable=self.var_lab_refine, command=self._lab_render).pack(side=tk.LEFT)
        # Chart mode controls (for bar-chart style images)
        self.var_lab_chart_mode = tk.BooleanVar(value=False)
        ttk.Checkbutton(p3, text="图表模式(条形图)", variable=self.var_lab_chart_mode, command=self._lab_render).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(p3, text="最大刻度").pack(side=tk.LEFT, padx=(12, 0))
        self.var_lab_chart_max = tk.IntVar(value=100_000_000)
        try:
            tk.Spinbox(p3, from_=1, to=2_000_000_000, increment=1000, textvariable=self.var_lab_chart_max, width=12, command=self._lab_render).pack(side=tk.LEFT, padx=(4, 0))
        except Exception:
            ttk.Entry(p3, textvariable=self.var_lab_chart_max, width=12).pack(side=tk.LEFT, padx=(4, 0))
        self.var_lab_chart_k = tk.BooleanVar(value=True)
        ttk.Checkbutton(p3, text="K单位", variable=self.var_lab_chart_k, command=self._lab_render).pack(side=tk.LEFT, padx=(8, 0))
        # Interference removal toggles
        self.var_lab_rm_hbars = tk.BooleanVar(value=True)
        self.var_lab_rm_vsep = tk.BooleanVar(value=True)
        ttk.Checkbutton(p3, text="去横条", variable=self.var_lab_rm_hbars, command=self._lab_render).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Checkbutton(p3, text="去中线", variable=self.var_lab_rm_vsep, command=self._lab_render).pack(side=tk.LEFT)

        # Help text
        tip = ttk.Label(frm, text="说明：普通模式：检测数字并按左右分割为价格/数量；图表模式：在深色条形图中，条长映射数量，条末数值为价格，支持K单位。",
                        foreground="#666")
        tip.pack(fill=tk.X, pady=(0, 4))

        # Preview (single annotated preview for quick glance)
        prev = ttk.Frame(frm)
        prev.pack(fill=tk.X)
        self.lab_prev = ttk.Label(prev)
        self.lab_prev.pack(padx=10, pady=10, anchor="w")
        self.lab_result = ttk.Label(frm, text="未加载")
        self.lab_result.pack(pady=(0, 4), anchor="w")

        # Pairing results table (chart mode)
        pairs_box = ttk.LabelFrame(frm, text="配对结果（自上而下）")
        pairs_box.pack(fill=tk.X, pady=(2, 6))
        self.pairs_tree = ttk.Treeview(pairs_box, columns=("idx", "price", "qty"), show="headings", height=6)
        self.pairs_tree.heading("idx", text="序")
        self.pairs_tree.heading("price", text="价格")
        self.pairs_tree.heading("qty", text="数量")
        self.pairs_tree.column("idx", width=40, anchor="e")
        self.pairs_tree.column("price", width=120, anchor="e")
        self.pairs_tree.column("qty", width=160, anchor="e")
        self.pairs_tree.pack(side=tk.LEFT, fill=tk.X, expand=True)
        sbp = ttk.Scrollbar(pairs_box, orient=tk.VERTICAL, command=self.pairs_tree.yview)
        sbp.pack(side=tk.RIGHT, fill=tk.Y)
        self.pairs_tree.configure(yscrollcommand=sbp.set)
        act2 = ttk.Frame(frm)
        act2.pack(fill=tk.X)
        ttk.Button(act2, text="复制结果", command=self._pairs_copy_to_clipboard).pack(side=tk.LEFT)

        # Step-by-step panel with scroll
        step_box = ttk.LabelFrame(frm, text="步骤预览（图像处理 → 候选框 → 分割 → 裁剪 → 识别）")
        step_box.pack(fill=tk.BOTH, expand=True, pady=(4, 6))
        step_outer = ttk.Frame(step_box)
        step_outer.pack(fill=tk.BOTH, expand=True)
        self.lab_steps_canvas = tk.Canvas(step_outer, highlightthickness=0, height=300)
        self.lab_steps_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb = ttk.Scrollbar(step_outer, orient=tk.VERTICAL, command=self.lab_steps_canvas.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.lab_steps_canvas.configure(yscrollcommand=sb.set)
        self.lab_steps_inner = ttk.Frame(self.lab_steps_canvas)
        self.lab_steps_canvas.create_window((0, 0), window=self.lab_steps_inner, anchor="nw")
        self.lab_steps_inner.bind("<Configure>", lambda e: self.lab_steps_canvas.configure(scrollregion=self.lab_steps_canvas.bbox("all")))
        # keep references to Tk images to avoid GC
        self._lab_step_tkimgs: list[Any] = []

        # Crops preview
        crop_box = ttk.Frame(frm)
        crop_box.pack(fill=tk.X, pady=(2, 4))
        ttk.Label(crop_box, text="裁剪：左(价格)").pack(side=tk.LEFT)
        self.lab_left_crop = ttk.Label(crop_box)
        self.lab_left_crop.pack(side=tk.LEFT, padx=6)
        ttk.Label(crop_box, text="右(数量)").pack(side=tk.LEFT, padx=(14, 0))
        self.lab_right_crop = ttk.Label(crop_box)
        self.lab_right_crop.pack(side=tk.LEFT, padx=6)

        # Save steps button
        act = ttk.Frame(frm)
        act.pack(fill=tk.X)
        ttk.Button(act, text="保存本次流程", command=self._lab_save_steps).pack(side=tk.LEFT)

        # Diagnostics box
        diagf = ttk.Frame(frm)
        diagf.pack(fill=tk.BOTH, expand=True)
        ttk.Label(diagf, text="诊断").pack(anchor="w")
        self.lab_diag = tk.Text(diagf, height=6, wrap="word")
        self.lab_diag.pack(fill=tk.BOTH, expand=True)
        try:
            self.lab_diag.configure(state=tk.DISABLED)
        except Exception:
            pass

        # State
        self._lab_pil = None
        self._lab_variants = []  # list of (name, PIL.Image)
        self._lab_cur_img = None

        # Trace entries to re-render
        for v in [self.var_lab_price_min, self.var_lab_price_max, self.var_lab_qty_min, self.var_lab_qty_max, self.var_lab_zoom]:
            try:
                v.trace_add("write", lambda *_: self._lab_render())
            except Exception:
                pass

    def _lab_pick_image(self) -> None:
        path = filedialog.askopenfilename(title="选择图片", filetypes=[("Image", ".png .jpg .jpeg .bmp"), ("All", "*.*")])
        if not path:
            return
        self.var_lab_img.set(path)
        try:
            from PIL import Image  # type: ignore
            self._lab_pil = Image.open(path).convert("RGB")
        except Exception as e:
            messagebox.showerror("打开图片", f"失败: {e}")
            self._lab_pil = None
            return
        self._lab_compute_variants()

    def _lab_compute_variants(self) -> None:
        if self._lab_pil is None:
            return
        self._lab_variants = [("raw", self._lab_pil.copy())]
        # Use price_reader's preprocess variants
        try:
            import numpy as _np  # type: ignore
            import cv2 as _cv2  # type: ignore
            from price_reader import _preprocess_variants_for_digits  # type: ignore
            arrs = _preprocess_variants_for_digits(self._lab_pil)
            for i, a in enumerate(arrs):
                try:
                    if len(a.shape) == 2:
                        pil = self._pil_from_cv_gray(a)
                    else:
                        pil = self._pil_from_cv_bgr(a)
                    self._lab_variants.append((f"v{i:02d}", pil))
                except Exception:
                    pass
        except Exception:
            pass
        # Update combobox options
        vals = ["auto"] + [n for (n, _) in self._lab_variants]
        try:
            self.cmb_variant.configure(values=vals)
        except Exception:
            self.cmb_variant["values"] = vals
        if self.var_lab_variant.get() not in vals:
            self.var_lab_variant.set("auto")
        self._lab_render()

    # ---------- Chart-mode helpers for OCR Lab ----------
    def _fmt_k(self, n: int) -> str:
        try:
            use_k = bool(self.var_lab_chart_k.get())
        except Exception:
            use_k = True
        if not use_k:
            return f"{n:,}"
        sgn = "-" if n < 0 else ""
        a = abs(int(n))
        if a >= 1000:
            val = a / 1000.0
            if val >= 100:
                txt = f"{val:,.0f}K"
            else:
                txt = f"{val:,.1f}K"
            return sgn + txt
        return sgn + f"{a:,}"

    @staticmethod
    def _parse_number_k(txt: str) -> int | None:
        if not txt:
            return None
        s = txt.strip()
        # keep digits, dot, comma, and K/k
        filt = []
        for ch in s:
            if ch.isdigit() or ch in ".,kK":
                filt.append(ch)
            # Treat common lookalikes
            elif ch in ["·", "•"]:
                filt.append(".")
        s2 = "".join(filt)
        if not s2:
            return None
        mult = 1
        if s2.endswith("k") or s2.endswith("K"):
            mult = 1000
            s2 = s2[:-1]
        # normalize comma to dot for decimals
        s2 = s2.replace(",", ".")
        try:
            val = float(s2)
        except Exception:
            # try to extract pure digits
            digits = "".join(ch for ch in s2 if ch.isdigit())
            if not digits:
                return None
            try:
                val = float(digits)
            except Exception:
                return None
        try:
            return int(round(val * mult))
        except Exception:
            return None

    def _lab_detect_chart_and_draw(self, pil_img):
        try:
            import numpy as _np  # type: ignore
            import cv2 as _cv2  # type: ignore
            import pytesseract as _pt  # type: ignore
            from PIL import ImageDraw  # type: ignore
        except Exception:
            return pil_img, 0, 0, "[Chart] 缺少 OpenCV/Tesseract 依赖，无法进行条形图模式识别。"

        # Parameters
        try:
            vmax = int(self.var_lab_chart_max.get() or 100_000_000)
        except Exception:
            vmax = 100_000_000

        bgr = _cv2.cvtColor(_np.array(pil_img), _cv2.COLOR_RGB2BGR)
        H, W = bgr.shape[:2]
        bgr32 = bgr.astype(_np.float32)

        # Color projection tailored for dark bg and gray bars
        bg_bgr = _np.array([8, 7, 7], dtype=_np.float32)
        bar_bgr = _np.array([96, 104, 103], dtype=_np.float32)
        V = bar_bgr - bg_bgr
        Vn = float(_np.dot(V, V)) or 1.0
        proj = _np.sum((bgr32 - bg_bgr[None, None, :]) * V[None, None, :], axis=2) / Vn
        proj = _np.clip(proj, 0.0, 1.0)
        proj8 = (proj * 255.0).astype(_np.uint8)
        try:
            _, th = _cv2.threshold(proj8, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU)
        except Exception:
            th = (proj8 > 32).astype(_np.uint8) * 255

        # Morph to bridge bars; keep dashed gridlines thin so they won't form big contours
        k = _cv2.getStructuringElement(_cv2.MORPH_RECT, (15, 3))
        x = _cv2.morphologyEx(th, _cv2.MORPH_CLOSE, k, iterations=1)
        k2 = _cv2.getStructuringElement(_cv2.MORPH_RECT, (3, 3))
        x = _cv2.morphologyEx(x, _cv2.MORPH_OPEN, k2, iterations=1)

        # Optional: remove wide thin horizontal bars (进度条)
        try:
            if bool(getattr(self, "var_lab_rm_hbars", tk.BooleanVar(value=True)).get()):
                cnts_tmp, _ = _cv2.findContours((x > 0).astype(_np.uint8), _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)
                for c in cnts_tmp:
                    rx, ry, rw, rh = _cv2.boundingRect(c)
                    if rw >= max(60, W // 6) and rh > 0 and (rw / max(1, rh)) >= 8.0:
                        _cv2.rectangle(x, (rx, ry), (rx + rw, ry + rh), color=0, thickness=-1)
        except Exception:
            pass

        # Optional: remove middle vertical separator
        try:
            if bool(getattr(self, "var_lab_rm_vsep", tk.BooleanVar(value=True)).get()):
                colsum = x.sum(axis=0).astype(_np.float32) / 255.0
                xc = int(colsum.argmax()) if colsum.size else -1
                ratio = float(colsum[xc] / max(1.0, H)) if xc >= 0 else 0.0
                if 0 <= xc < W and 0.35 * W <= xc <= 0.65 * W and ratio >= 0.55:
                    left = max(0, xc - 3); right = min(W, xc + 4)
                    x[:, left:right] = 0
        except Exception:
            pass

        try:
            cnts, _ = _cv2.findContours((x > 0).astype(_np.uint8), _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)
        except ValueError:
            _tmp, cnts, _ = _cv2.findContours((x > 0).astype(_np.uint8), _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)  # type: ignore

        cand = []  # list of (y, x, w, h)
        for c in cnts:
            rx, ry, rw, rh = _cv2.boundingRect(c)
            ar = rw / max(1.0, float(rh))
            if rw >= max(60, int(W * 0.15)) and 6 <= rh <= int(H * 0.12) and ar >= 4.0:
                cand.append((ry, rx, rw, rh))
        # merge overlapping/stacked candidates by proximity of Y and overlap in X
        cand.sort(key=lambda t: t[0])
        bars = []
        for (ry, rx, rw, rh) in cand:
            if not bars:
                bars.append([ry, rx, rx + rw, ry + rh])
            else:
                y_t, x_l, x_r, y_b = bars[-1]
                if abs(ry - y_t) <= 4 and not (rx > x_r or (rx + rw) < x_l):
                    bars[-1][0] = min(y_t, ry)
                    bars[-1][1] = min(x_l, rx)
                    bars[-1][2] = max(x_r, rx + rw)
                    bars[-1][3] = max(y_b, ry + rh)
                else:
                    bars.append([ry, rx, rx + rw, ry + rh])

        if not bars:
            return self._lab_detect_and_draw(pil_img)  # fallback to generic path

        # Normalize geometry
        min_x = min(b[1] for b in bars)
        max_r = max(b[2] for b in bars)
        chart_w = max(1, max_r - min_x)

        # OCR prices near bar ends
        results = []  # (price, qty, (bar_x1,bar_y1,bar_x2,bar_y2), (px1,py1,px2,py2))
        for (y_t, x_l, x_r, y_b) in bars:
            y_mid = int((y_t + y_b) / 2)
            h = max(1, y_b - y_t)
            # ROI to the right of bar end
            x1 = min(W - 1, x_r + 2)
            x2 = min(W, x_r + max(40, int(W * 0.25)))
            y1 = max(0, int(y_mid - h * 0.7))
            y2 = min(H, int(y_mid + h * 0.7))
            crop = pil_img.crop((x1, y1, x2, y2))
            price_val = 0
            price_box = None
            try:
                for psm in (7, 6, 13):
                    cfg = f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789kK.,"
                    d = _pt.image_to_data(crop, config=cfg, output_type=_pt.Output.DICT)
                    n = len(d.get("text", []))
                    for i in range(n):
                        s = d.get("text", [""])[i] or ""
                        v = self._parse_number_k(s)
                        if v is not None:
                            if int(v) >= int(price_val or 0):
                                price_val = int(v)
                                try:
                                    l = int(d.get("left", [0])[i]); t = int(d.get("top", [0])[i])
                                    w = int(d.get("width", [0])[i]); h = int(d.get("height", [0])[i])
                                    price_box = (x1 + l, y1 + t, x1 + l + w, y1 + t + h)
                                except Exception:
                                    price_box = (x1, y1, x2, y2)
                # derive qty from bar length
                L = max(0, x_r - min_x)
                qty_val = int(round(vmax * (L / float(chart_w))))
                if price_box is None:
                    price_box = (x1, y1, x2, y2)
                results.append((int(price_val or 0), int(qty_val or 0), (x_l, y_t, x_r, y_b), price_box))
            except Exception:
                continue

        # choose min price
        price, qty = 0, 0
        sel_bar = None
        sel_price_box = None
        if results:
            results.sort(key=lambda t: (t[0] if t[0] > 0 else 1e18, -t[1]))
            price, qty, sel_bar, sel_price_box = results[0]

        # Save pairs for UI table (top-to-bottom order)
        try:
            results_sorted = sorted(results, key=lambda t: (t[2][1] + t[2][3]) / 2.0)
            self._lab_pairs = [(i + 1, r[0], r[1]) for i, r in enumerate(results_sorted)]
        except Exception:
            self._lab_pairs = []

        # Draw minimalist overlay
        out = pil_img.copy()
        dr = ImageDraw.Draw(out)
        barc = (93, 100, 107)
        grid = (42, 45, 49)
        textc = (169, 176, 184)
        # row separators
        for (y_t, x_l, x_r, y_b) in bars:
            yy = int((y_t + y_b) / 2)
            try:
                dr.line([(min_x, yy), (max_r, yy)], fill=grid, width=1)
            except Exception:
                pass
        for (_, _, (x_l, y_t, x_r, y_b), _) in results:
            dr.rectangle([x_l, y_t, x_r, y_b], outline=barc, width=1)
        # candidate price boxes (thin, de-emphasized)
        candc = (120, 180, 210)
        for (_pval, _q, _bar, pbox) in results:
            if pbox:
                x1, y1, x2, y2 = pbox
                dr.rectangle([x1, y1, x2, y2], outline=candc, width=1)
        # selected price box (distinct color, 1px)
        selc = (0, 213, 255)
        if sel_price_box:
            x1, y1, x2, y2 = sel_price_box
            dr.rectangle([x1, y1, x2, y2], outline=selc, width=1)
        # labels
        for (pval, _q, (x_l, y_t, x_r, y_b), _) in results:
            lbl = self._fmt_k(int(pval or 0))
            try:
                dr.text((x_r + 4, int((y_t + y_b) / 2) - 7), lbl, fill=textc)
            except Exception:
                pass

        diag = []
        diag.append(f"[Chart] bars={len(bars)} vmax={vmax} chart_w={chart_w}")
        show = results[:6]
        for (pv, qv, (x_l, y_t, x_r, y_b), pbox) in show:
            pb = f"({pbox[0]},{pbox[1]},{pbox[2]-pbox[0]},{pbox[3]-pbox[1]})" if pbox else "()"
            diag.append(f"  price={pv} qty={qv} bar=({x_l},{y_t},{x_r-x_l},{y_b-y_t}) price_box={pb}")
        return out, int(price or 0), int(qty or 0), "\n".join(diag)

    # (Tab4 UI removed per需求)

    def _lab_render(self) -> None:
        # Update split label & ensure formatting
        try:
            # 统一两位小数显示
            split_val = round(float(self.var_lab_split.get()), 2)
            self.var_lab_split.set(split_val)
            self.lab_split_val.configure(text=f"{split_val:.2f}")
        except Exception:
            pass
        if not self._lab_variants:
            self.lab_result.configure(text="未生成变体")
            return
        variant = self.var_lab_variant.get().strip() or "auto"
        if variant == "auto":
            # pick best variant by our heuristic
            name, pil = self._lab_pick_best_variant()
        else:
            name, pil = next(((n, p) for (n, p) in self._lab_variants if n == variant), self._lab_variants[0])

        # Chart mode branch
        if bool(getattr(self, "var_lab_chart_mode", tk.BooleanVar(value=False)).get()):
            img, price, qty, diag = self._lab_detect_chart_and_draw(pil)
        else:
            img, price, qty, diag = self._lab_detect_and_draw(pil)
        self._lab_cur_img = img
        if price and qty:
            status, color = "正常", "#0a7e07"
        elif price and not qty:
            status, color = "仅价格", "#c97a00"
        elif qty and not price:
            status, color = "仅数量", "#c97a00"
        else:
            status, color = "异常", "#c1121f"
        try:
            self.lab_result.configure(text=f"变体: {name}    价格: {price}    数量: {qty}    状态: {status}", foreground=color)
        except Exception:
            self.lab_result.configure(text=f"变体: {name}    价格: {price}    数量: {qty}    状态: {status}")
        # Diagnostics
        try:
            self.lab_diag.configure(state=tk.NORMAL)
            self.lab_diag.delete("1.0", tk.END)
            self.lab_diag.insert(tk.END, diag or "")
            self.lab_diag.configure(state=tk.DISABLED)
        except Exception:
            pass
        # Update pairs table (chart mode only)
        try:
            for iid in self.pairs_tree.get_children(""):
                self.pairs_tree.delete(iid)
            if bool(getattr(self, "var_lab_chart_mode", tk.BooleanVar(value=False)).get()):
                pairs = getattr(self, "_lab_pairs", []) or []
                for (idx, p, q) in pairs:
                    self.pairs_tree.insert("", tk.END, values=(idx, p, q))
        except Exception:
            pass
        # Show
        self._lab_show_image(img)
        # Also render step-by-step panel
        try:
            if bool(getattr(self, "var_lab_chart_mode", tk.BooleanVar(value=False)).get()):
                self._lab_render_steps_chart(raw_pil=self._lab_pil or pil)
            else:
                self._lab_render_steps(raw_pil=self._lab_pil or pil, variant_pil=pil)
        except Exception:
            pass

    def _lab_pick_best_variant(self):
        best = self._lab_variants[0]
        best_score = (float("inf"), 0)  # (price min asc, qty desc)
        for (n, p) in self._lab_variants:
            _, pr, qt, _ = self._lab_detect_and_draw(p, draw=False)
            pr = int(pr or 0)
            qt = int(qt or 0)
            score = (pr if pr > 0 else float("inf"), -qt)
            if score < best_score:
                best, best_score = (n, p), score
        return best

    def _lab_detect_and_draw(self, pil_img, draw=True):
        # OCR via pytesseract
        try:
            import pytesseract  # type: ignore
            from PIL import ImageDraw  # type: ignore
        except Exception:
            return pil_img, 0, 0, "[OCR] pytesseract 不可用。请安装并确保 tesseract 在系统路径。"

        # OCR tokens
        psm_list = [6, 7, 11, 13]
        boxes = []  # (l,t,w,h,val,conf,psm)
        diag_lines = []
        for psm in psm_list:
            config = f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789,"
            try:
                data = pytesseract.image_to_data(pil_img, config=config, output_type=pytesseract.Output.DICT)
            except Exception as e:
                diag_lines.append(f"[OCR] psm={psm} 调用异常: {e}")
                continue
            n = len(data.get("text", []))
            for i in range(n):
                txt = data.get("text", [""])[i] or ""
                digits = "".join(ch for ch in txt if ch.isdigit())
                if not digits:
                    continue
                try:
                    l = int(data.get("left", [0])[i]); t = int(data.get("top", [0])[i])
                    w = int(data.get("width", [0])[i]); h = int(data.get("height", [0])[i])
                    conf = float(data.get("conf", [0])[i] or 0)
                except Exception:
                    continue
                try:
                    val = int(digits)
                except Exception:
                    continue
                boxes.append((l, t, w, h, val, conf, psm))

        W, H = pil_img.size
        # Compute auto split if requested
        if bool(self.var_lab_auto_split.get()):
            xs = sorted(((l + w / 2) / max(1, W) for (l, t, w, h, v, c, p) in boxes))
            split = 0.5
            if len(xs) >= 2:
                # largest gap heuristic, avoid extreme gaps at edges
                gaps = []  # (gap, mid)
                for a, b in zip(xs[:-1], xs[1:]):
                    gaps.append((b - a, (a + b) / 2.0))
                gaps.sort(reverse=True, key=lambda g: g[0])
                # pick first mid that yields both sides non-empty
                for g, mid in gaps:
                    left_n = sum(1 for x in xs if x <= mid)
                    right_n = sum(1 for x in xs if x > mid)
                    if left_n > 0 and right_n > 0:
                        split = float(max(0.05, min(0.95, mid)))
                        break
        else:
            split = float(self.var_lab_split.get() or 0.5)

        left_tokens = [(l, t, w, h, v, c, p) for (l, t, w, h, v, c, p) in boxes if (l + w / 2) / max(1, W) <= split]
        right_tokens = [(l, t, w, h, v, c, p) for (l, t, w, h, v, c, p) in boxes if (l + w / 2) / max(1, W) > split]

        pr_min = int(self.var_lab_price_min.get() or 10); pr_max = int(self.var_lab_price_max.get() or 10_000_000)
        qt_min = int(self.var_lab_qty_min.get() or 0); qt_max = int(self.var_lab_qty_max.get() or 1_000_000)
        price_vals = [v for (_, _, _, _, v, _, _) in left_tokens if pr_min <= v <= pr_max]
        qty_vals = [v for (_, _, _, _, v, _, _) in right_tokens if qt_min <= v <= qt_max]
        price = min(price_vals) if price_vals else 0
        qty = max(qty_vals) if qty_vals else 0

        # Optional refine by cropping detected blocks and re-OCR each side
        if bool(self.var_lab_refine.get()) and (left_tokens or right_tokens):
            try:
                import pytesseract as _pt  # type: ignore
                from PIL import Image  # type: ignore
                def _ocr_numbers(pil_crop):
                    vals = []
                    for psm in (7, 6, 13):
                        cfg = f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789,"
                        try:
                            d = _pt.image_to_data(pil_crop, config=cfg, output_type=_pt.Output.DICT)
                        except Exception:
                            continue
                        for txt in d.get("text", []) or []:
                            if not txt:
                                continue
                            ds = "".join(ch for ch in txt if ch.isdigit())
                            if not ds:
                                continue
                            try:
                                vals.append(int(ds))
                            except Exception:
                                pass
                    return vals
                # build crops with small margins
                def _crop_from_tokens(tokens):
                    if not tokens:
                        return None
                    x1 = min(l for (l, t, w, h, *_ ) in tokens)
                    y1 = min(t for (l, t, w, h, *_ ) in tokens)
                    x2 = max(l + w for (l, t, w, h, *_ ) in tokens)
                    y2 = max(t + h for (l, t, w, h, *_ ) in tokens)
                    pad = 2
                    x1 = max(0, x1 - pad); y1 = max(0, y1 - pad)
                    x2 = min(W, x2 + pad); y2 = min(H, y2 + pad)
                    try:
                        return pil_img.crop((x1, y1, x2, y2))
                    except Exception:
                        return None
                left_crop = _crop_from_tokens(left_tokens)
                right_crop = _crop_from_tokens(right_tokens)
                # recompute with refine crops (prefer refined values if valid)
                if left_crop is not None:
                    lv = _ocr_numbers(left_crop)
                    lv = [v for v in lv if pr_min <= v <= pr_max]
                    if lv:
                        price = min(lv)
                if right_crop is not None:
                    rv = _ocr_numbers(right_crop)
                    rv = [v for v in rv if qt_min <= v <= qt_max]
                    if rv:
                        qty = max(rv)
            except Exception:
                pass

        # Build diagnostics
        if not boxes:
            diag_lines.append("未检测到任何数字 token。可能原因：阈值/变体不适合、图片过小或模糊、OCR psm 不匹配。")
        else:
            diag_lines.append(f"共检测到 {len(boxes)} 个数字 token（含多种 psm 重试）。")
            l_cnt, r_cnt = len(left_tokens), len(right_tokens)
            diag_lines.append(f"左侧(价格) token: {l_cnt} 个；右侧(数量) token: {r_cnt} 个；分割阈值: {split:.2f}（{'自动' if bool(self.var_lab_auto_split.get()) else '手动'}）")
            if not price_vals and l_cnt:
                vals = ", ".join(str(v) for (_, _, _, _, v, _, _) in left_tokens[:6])
                diag_lines.append(f"左侧原始数值样本: {vals}")
                diag_lines.append(f"价格范围过滤: [{pr_min}, {pr_max}] 导致当前无有效价格。")
            if not qty_vals and r_cnt:
                vals = ", ".join(str(v) for (_, _, _, _, v, _, _) in right_tokens[:6])
                diag_lines.append(f"右侧原始数值样本: {vals}")
                diag_lines.append(f"数量范围过滤: [{qt_min}, {qt_max}] 导致当前无有效数量。")
            # Show first few tokens detail
            show = boxes[:8]
            for (l, t, w, h, v, c, psm) in show:
                side = "L" if (l + w / 2) / max(1, W) <= split else "R"
                xn = (l + w / 2) / max(1, W)
                diag_lines.append(f"  val={v} side={side} x={xn:.2f} conf={c:.0f} psm={psm} box=({l},{t},{w},{h})")

        if not draw:
            return pil_img, int(price or 0), int(qty or 0), "\n".join(diag_lines)

        try:
            from PIL import ImageDraw  # type: ignore
            img = pil_img.copy()
            drawr = ImageDraw.Draw(img)
            if left_tokens:
                x1 = min(l for (l, t, w, h, v, _, _) in left_tokens)
                y1 = min(t for (l, t, w, h, v, _, _) in left_tokens)
                x2 = max(l + w for (l, t, w, h, v, _, _) in left_tokens)
                y2 = max(t + h for (l, t, w, h, v, _, _) in left_tokens)
                drawr.rectangle([x1, y1, x2, y2], outline=(0, 128, 255), width=3)
            if right_tokens:
                x1 = min(l for (l, t, w, h, v, _, _) in right_tokens)
                y1 = min(t for (l, t, w, h, v, _, _) in right_tokens)
                x2 = max(l + w for (l, t, w, h, v, _, _) in right_tokens)
                y2 = max(t + h for (l, t, w, h, v, _, _) in right_tokens)
                drawr.rectangle([x1, y1, x2, y2], outline=(255, 165, 0), width=3)
        except Exception:
            img = pil_img
        return img, int(price or 0), int(qty or 0), "\n".join(diag_lines)

    def _lab_show_image(self, pil_img):
        try:
            from PIL import ImageTk  # type: ignore
        except Exception:
            return
        # 应用用户放大倍数（默认1.5倍），再适配最大窗口尺寸
        try:
            zoom = float(self.var_lab_zoom.get() or 1.5)
        except Exception:
            zoom = 1.5
        max_w, max_h = 1200, 700
        w, h = pil_img.size
        zw, zh = int(w * zoom), int(h * zoom)
        # 若超出最大尺寸，则再做一次整体等比缩放
        scale_fit = min(max_w / max(1, zw), max_h / max(1, zh), 1.0)
        tw, th = int(zw * scale_fit), int(zh * scale_fit)
        disp = pil_img.resize((tw, th)) if (tw != w or th != h) else pil_img
        tkimg = ImageTk.PhotoImage(disp)
        self.lab_prev.configure(image=tkimg)
        self.lab_prev.image = tkimg

    def _lab_save_annotated(self) -> None:
        if self._lab_cur_img is None:
            return
        base = self.var_lab_img.get().strip() or "annotated"
        root, ext = os.path.splitext(base)
        out = root + "_ann.png"
        try:
            self._lab_cur_img.save(out)
            messagebox.showinfo("保存", f"已保存: {out}")
        except Exception as e:
            messagebox.showerror("保存", f"失败: {e}")

    def _pairs_copy_to_clipboard(self) -> None:
        try:
            pairs = getattr(self, "_lab_pairs", []) or []
            if not pairs:
                return
            lines = ["序\t价格\t数量"]
            for (idx, p, q) in pairs:
                lines.append(f"{idx}\t{p}\t{q}")
            txt = "\n".join(lines)
            self.clipboard_clear()
            self.clipboard_append(txt)
            try:
                self.update()
            except Exception:
                pass
            messagebox.showinfo("复制", "已复制到剪贴板。")
        except Exception:
            pass

    # ---------- Lab: step-by-step rendering & saving ----------
    def _lab_render_steps(self, *, raw_pil, variant_pil):
        """Render step-by-step images into the scroll area, and update crops.

        Steps include: raw, CLAHE+Otsu, color masks, debar, tokens, split+boxes, final with thin boxes.
        """
        try:
            from PIL import Image, ImageTk, ImageDraw  # type: ignore
            import numpy as _np  # type: ignore
            import cv2 as _cv2  # type: ignore
            import pytesseract as _pt  # type: ignore
        except Exception:
            # Minimal fallback: just show raw
            for w in self.lab_steps_inner.winfo_children():
                w.destroy()
            self._lab_step_tkimgs.clear()
            lbl = ttk.Label(self.lab_steps_inner, text="缺少 OpenCV/Tesseract，无法展示详细步骤。")
            lbl.grid(row=0, column=0, sticky="w", padx=8, pady=6)
            imgtk = ImageTk.PhotoImage(raw_pil)
            self._lab_step_tkimgs.append(imgtk)
            ttk.Label(self.lab_steps_inner, image=imgtk).grid(row=1, column=0, sticky="w", padx=8, pady=4)
            return

        # helpers
        def _to_pil_gray(arr):
            try:
                return Image.fromarray(arr)
            except Exception:
                return raw_pil
        def _to_pil_bgr(arr):
            try:
                rgb = _cv2.cvtColor(arr, _cv2.COLOR_BGR2RGB)
                return Image.fromarray(rgb)
            except Exception:
                return raw_pil

        # Clear container
        for w in self.lab_steps_inner.winfo_children():
            w.destroy()
        self._lab_step_tkimgs.clear()

        # Build variants once
        try:
            from price_reader import _preprocess_variants_for_digits as _pre_v  # type: ignore
        except Exception:
            _pre_v = None

        steps: list[tuple[str, Any]] = []
        steps.append(("原图", raw_pil.copy()))

        # 1) CLAHE + Otsu (gray)
        try:
            bgr = _cv2.cvtColor(_np.array(raw_pil), _cv2.COLOR_RGB2BGR)
            gray = _cv2.cvtColor(bgr, _cv2.COLOR_BGR2GRAY)
            clahe = _cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
            cg = clahe.apply(gray)
            _, otsu = _cv2.threshold(cg, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU)
            steps.append(("CLAHE+Otsu", _to_pil_gray(otsu)))
        except Exception:
            pass

        # 2) Color-aware masks tailored for #070708 bg / #606867 text
        try:
            bg_bgr = _np.array([8, 7, 7], dtype=_np.float32)
            txt_bgr = _np.array([103, 104, 96], dtype=_np.float32)
            bgr = _cv2.cvtColor(_np.array(raw_pil), _cv2.COLOR_RGB2BGR).astype(_np.float32)
            diff_bg = bgr - bg_bgr[None, None, :]
            diff_txt = bgr - txt_bgr[None, None, :]
            d_bg = _np.sqrt(_np.maximum(0.0, _np.sum(diff_bg * diff_bg, axis=2)))
            d_txt = _np.sqrt(_np.maximum(0.0, _np.sum(diff_txt * diff_txt, axis=2)))
            m_close = (d_txt + 5.0 < d_bg)
            m_close &= (d_txt < 220.0)
            mask1 = (m_close.astype(_np.uint8)) * 255
            steps.append(("颜色掩膜1(近似文本)", _to_pil_gray(mask1)))
            V = (txt_bgr - bg_bgr)
            Vn = float(_np.dot(V, V)) or 1.0
            proj = _np.sum((bgr - bg_bgr[None, None, :]) * V[None, None, :], axis=2) / Vn
            proj = _np.clip(proj, 0.0, 1.0)
            proj8 = (proj * 255.0).astype(_np.uint8)
            _, th_proj = _cv2.threshold(proj8, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU)
            steps.append(("颜色投影+Otsu", _to_pil_gray(th_proj)))
        except Exception:
            pass

        # 3) Remove wide thin bars from a bin image (pick last step available)
        try:
            pick = None
            for name, imgp in reversed(steps):
                if name == "原图":
                    continue
                # use grayscale/binary images only
                try:
                    arr = _np.array(imgp)
                    if len(arr.shape) == 2:
                        pick = arr
                        break
                except Exception:
                    continue
            if pick is not None:
                x = pick.copy()
                H, W = x.shape[:2]
                cnts, _ = _cv2.findContours((x > 0).astype(_np.uint8), _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)
                for c in cnts:
                    rx, ry, rw, rh = _cv2.boundingRect(c)
                    if rw >= max(60, W // 6) and rh > 0 and (rw / max(1, rh)) >= 8.0:
                        _cv2.rectangle(x, (rx, ry), (rx + rw, ry + rh), color=0, thickness=-1)
                steps.append(("去进度条", _to_pil_gray(x)))
        except Exception:
            pass

        # 4) OCR tokens on the chosen variant (best/selected)
        try:
            variant_img = variant_pil  # annotated on a copy
            W, H = variant_img.size
            img_tokens = variant_img.copy()
            dr = ImageDraw.Draw(img_tokens)
            cfgs = [6, 7, 11, 13]
            tokens = []  # (l,t,w,h,val,conf)
            for psm in cfgs:
                cfg = f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789,"
                try:
                    data = _pt.image_to_data(variant_img, config=cfg, output_type=_pt.Output.DICT)
                except Exception:
                    continue
                n = len(data.get("text", []))
                for i in range(n):
                    txt = data.get("text", [""])[i] or ""
                    digits = "".join(ch for ch in txt if ch.isdigit())
                    if not digits:
                        continue
                    try:
                        l = int(data.get("left", [0])[i]); t = int(data.get("top", [0])[i])
                        w = int(data.get("width", [0])[i]); h = int(data.get("height", [0])[i])
                        conf = float(data.get("conf", [0])[i] or 0)
                        val = int(digits)
                    except Exception:
                        continue
                    tokens.append((l, t, w, h, val, conf))
            # draw fine boxes per token
            for (l, t, w, h, _, _) in tokens:
                dr.rectangle([l, t, l + w, t + h], outline=(50, 205, 50), width=1)
            steps.append(("候选数字框(细)", img_tokens))

            # 5) Split and union per side
            try:
                split = float(self.var_lab_split.get() or 0.5)
            except Exception:
                split = 0.5
            if bool(self.var_lab_auto_split.get()):
                xs = sorted(((l + w / 2) / max(1, W) for (l, t, w, h, _, _) in tokens))
                if len(xs) >= 2:
                    gaps = []
                    for a, b in zip(xs[:-1], xs[1:]):
                        gaps.append((b - a, (a + b) / 2.0))
                    gaps.sort(reverse=True, key=lambda g: g[0])
                    for g, mid in gaps:
                        left_n = sum(1 for x in xs if x <= mid)
                        right_n = sum(1 for x in xs if x > mid)
                        if left_n > 0 and right_n > 0:
                            split = float(max(0.05, min(0.95, mid)))
                            break

            left = [(l, t, w, h, v, c) for (l, t, w, h, v, c) in tokens if (l + w / 2) / max(1, W) <= split]
            right = [(l, t, w, h, v, c) for (l, t, w, h, v, c) in tokens if (l + w / 2) / max(1, W) > split]
            img_lr = variant_img.copy()
            dr2 = ImageDraw.Draw(img_lr)
            # draw split line
            dr2.line([(int(W * split), 0), (int(W * split), H)], fill=(200, 200, 0), width=1)
            def _union(boxes):
                if not boxes:
                    return None
                x1 = min(l for (l, t, w, h, *_ ) in boxes)
                y1 = min(t for (l, t, w, h, *_ ) in boxes)
                x2 = max(l + w for (l, t, w, h, *_ ) in boxes)
                y2 = max(t + h for (l, t, w, h, *_ ) in boxes)
                return [x1, y1, x2, y2]
            ub_l = _union(left)
            ub_r = _union(right)
            if ub_l:
                dr2.rectangle(ub_l, outline=(0, 128, 255), width=1)
            if ub_r:
                dr2.rectangle(ub_r, outline=(255, 165, 0), width=1)
            steps.append(("分割+框出(极细)", img_lr))

            # 6) Crops (from raw base to preserve quality)
            raw = steps[0][1]
            left_crop = None
            right_crop = None
            if ub_l:
                try:
                    x1, y1, x2, y2 = ub_l
                    left_crop = raw.crop((x1, y1, x2, y2))
                except Exception:
                    pass
            if ub_r:
                try:
                    x1, y1, x2, y2 = ub_r
                    right_crop = raw.crop((x1, y1, x2, y2))
                except Exception:
                    pass

            # update crop previews
            def _show_crop(lbl, img):
                if img is None:
                    lbl.configure(image="")
                    lbl.image = None
                    return
                # upscale a bit for readability
                try:
                    zw = max(1, int(img.size[0] * 1.5)); zh = max(1, int(img.size[1] * 1.5))
                    disp = img.resize((zw, zh))
                except Exception:
                    disp = img
                imgtk2 = ImageTk.PhotoImage(disp)
                lbl.configure(image=imgtk2)
                lbl.image = imgtk2
                self._lab_step_tkimgs.append(imgtk2)
            _show_crop(self.lab_left_crop, left_crop)
            _show_crop(self.lab_right_crop, right_crop)

            # 7) OCR result on crops
            price = 0
            qty = 0
            try:
                pr_min = int(self.var_lab_price_min.get() or 10); pr_max = int(self.var_lab_price_max.get() or 10_000_000)
                qt_min = int(self.var_lab_qty_min.get() or 0); qt_max = int(self.var_lab_qty_max.get() or 1_000_000)
            except Exception:
                pr_min, pr_max, qt_min, qt_max = 10, 10_000_000, 0, 1_000_000
            def _ocr_vals(pil_crop):
                vals = []
                for psm in (7, 6, 13):
                    cfg = f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789,"
                    try:
                        d = _pt.image_to_data(pil_crop, config=cfg, output_type=_pt.Output.DICT)
                    except Exception:
                        continue
                    for txt in d.get("text", []) or []:
                        ds = "".join(ch for ch in (txt or "") if ch.isdigit())
                        if not ds:
                            continue
                        try:
                            vals.append(int(ds))
                        except Exception:
                            pass
                return vals
            if left_crop is not None:
                lv = [v for v in _ocr_vals(left_crop) if pr_min <= v <= pr_max]
                if lv:
                    price = min(lv)
            if right_crop is not None:
                rv = [v for v in _ocr_vals(right_crop) if qt_min <= v <= qt_max]
                if rv:
                    qty = max(rv)

            # Final annotated image with bottom text
            final = steps[0][1].copy()
            draw_final = ImageDraw.Draw(final)
            if ub_l:
                draw_final.rectangle(ub_l, outline=(0, 128, 255), width=1)
            if ub_r:
                draw_final.rectangle(ub_r, outline=(255, 165, 0), width=1)
            # extend canvas bottom
            try:
                W0, H0 = final.size
                ext = Image.new("RGB", (W0, H0 + 28), (12, 12, 12))
                ext.paste(final, (0, 0))
                final = ext
                draw_final = ImageDraw.Draw(final)
            except Exception:
                pass
            status = "正常" if (price and qty) else ("仅价格" if price else ("仅数量" if qty else "异常"))
            txt = f"价格: {int(price or 0)}    数量: {int(qty or 0)}    状态: {status}"
            try:
                draw_final.text((6, final.size[1] - 22), txt, fill=(230, 230, 230))
            except Exception:
                pass
            steps.append(("最终结果", final))
        except Exception:
            pass

        # Render steps to UI
        r = 0
        for name, imgp in steps:
            ttk.Label(self.lab_steps_inner, text=name).grid(row=r, column=0, sticky="w", padx=8)
            try:
                imgtk = ImageTk.PhotoImage(imgp)
            except Exception:
                # if not PIL img, skip
                r += 1
                continue
            lbl = ttk.Label(self.lab_steps_inner, image=imgtk)
            lbl.grid(row=r + 1, column=0, sticky="w", padx=8, pady=(0, 6))
            lbl.image = imgtk
            self._lab_step_tkimgs.append(imgtk)
            r += 2

    def _lab_save_steps(self) -> None:
        # Save current steps by re-running pipeline on selected image
        base = self.var_lab_img.get().strip()
        if not base or not os.path.exists(base):
            messagebox.showwarning("保存流程", "请先选择图片。")
            return
        try:
            from PIL import Image  # type: ignore
            import cv2 as _cv2  # type: ignore
            import numpy as _np  # type: ignore
        except Exception as e:
            messagebox.showerror("保存流程", f"缺少依赖: {e}")
            return
        try:
            pil = Image.open(base).convert("RGB")
        except Exception as e:
            messagebox.showerror("保存流程", f"打开失败: {e}")
            return
        ts = time.strftime("%Y%m%d_%H%M%S")
        out_dir = os.path.join("images", f"proc_{ts}")
        os.makedirs(out_dir, exist_ok=True)
        # Branch by mode
        if bool(getattr(self, "var_lab_chart_mode", tk.BooleanVar(value=False)).get()):
            # Chart-mode dump
            dumps: list[tuple[str, Any]] = []
            # 01 原图
            dumps.append(("step_01_raw.png", pil.copy()))
            # 02 颜色投影
            bg_bgr = _np.array([8, 7, 7], dtype=_np.float32)
            bar_bgr = _np.array([96, 104, 103], dtype=_np.float32)
            bgr = _cv2.cvtColor(_np.array(pil), _cv2.COLOR_RGB2BGR)
            V = bar_bgr - bg_bgr
            Vn = float(_np.dot(V, V)) or 1.0
            proj = _np.sum((bgr.astype(_np.float32) - bg_bgr[None, None, :]) * V[None, None, :], axis=2) / Vn
            proj = _np.clip(proj, 0.0, 1.0)
            proj8 = (proj * 255.0).astype(_np.uint8)
            dumps.append(("step_02_projection.png", self._pil_from_cv_gray(proj8)))
            # 03 阈值
            try:
                _, th = _cv2.threshold(proj8, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU)
            except Exception:
                th = (proj8 > 32).astype(_np.uint8) * 255
            dumps.append(("step_03_otsu.png", self._pil_from_cv_gray(th)))
            # 04 形态学
            k = _cv2.getStructuringElement(_cv2.MORPH_RECT, (15, 3))
            x = _cv2.morphologyEx(th, _cv2.MORPH_CLOSE, k, iterations=1)
            k2 = _cv2.getStructuringElement(_cv2.MORPH_RECT, (3, 3))
            x = _cv2.morphologyEx(x, _cv2.MORPH_OPEN, k2, iterations=1)
            dumps.append(("step_04_morph.png", self._pil_from_cv_gray(x)))
            # 05 去横条
            try:
                if bool(getattr(self, "var_lab_rm_hbars", tk.BooleanVar(value=True)).get()):
                    x2b = x.copy(); H,W = x.shape[:2]
                    cnts_tmp, _ = _cv2.findContours((x2b > 0).astype(_np.uint8), _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)
                    for c in cnts_tmp:
                        rx, ry, rw, rh = _cv2.boundingRect(c)
                        if rw >= max(60, W // 6) and rh > 0 and (rw / max(1, rh)) >= 8.0:
                            _cv2.rectangle(x2b, (rx, ry), (rx + rw, ry + rh), color=0, thickness=-1)
                    x = x2b
            except Exception:
                pass
            dumps.append(("step_05_rm_hbar.png", self._pil_from_cv_gray(x)))
            # 06 去中线
            try:
                if bool(getattr(self, "var_lab_rm_vsep", tk.BooleanVar(value=True)).get()):
                    H,W = x.shape[:2]
                    colsum = x.sum(axis=0).astype(_np.float32) / 255.0
                    xc = int(colsum.argmax()) if colsum.size else -1
                    ratio = float(colsum[xc] / max(1.0, H)) if xc >= 0 else 0.0
                    if 0 <= xc < W and 0.35 * W <= xc <= 0.65 * W and ratio >= 0.55:
                        left = max(0, xc - 3); right = min(W, xc + 4)
                        x[:, left:right] = 0
            except Exception:
                pass
            dumps.append(("step_06_rm_vsep.png", self._pil_from_cv_gray(x)))
            # 07 候选条形 + 候选价格框 + 选择
            try:
                import pytesseract as _pt2  # type: ignore
                from PIL import ImageDraw as _ID  # type: ignore
                H,W = x.shape[:2]
                cnts, _ = _cv2.findContours((x > 0).astype(_np.uint8), _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)
                bars = []
                for c in cnts:
                    rx, ry, rw, rh = _cv2.boundingRect(c)
                    ar = rw / max(1.0, float(rh))
                    if rw >= max(60, int(W * 0.15)) and 6 <= rh <= int(H * 0.12) and ar >= 4.0:
                        bars.append((ry, rx, rx + rw, ry + rh))
                bars.sort(key=lambda t: t[0])
                # Draw candidate bars image
                im_bars = pil.copy(); dr = _ID.Draw(im_bars)
                for (y1,x1,x2,y2) in [(b[0], b[1], b[2], b[3]) for b in bars]:
                    dr.rectangle([x1,y1,x2,y2], outline=(93,100,107), width=1)
                dumps.append(("step_07_bars.png", im_bars))
                # OCR candidates and select
                try:
                    vmax = int(self.var_lab_chart_max.get() or 100_000_000)
                except Exception:
                    vmax = 100_000_000
                min_x = min(b[1] for b in bars) if bars else 0
                chart_w = max(1, max((b[2] for b in bars), default=0) - min_x)
                results = []
                for (y_t, x_l, x_r, y_b) in bars:
                    y_mid = int((y_t + y_b) / 2)
                    h = max(1, y_b - y_t)
                    rx1 = min(W - 1, x_r + 2); rx2 = min(W, x_r + max(40, int(W * 0.25)))
                    ry1 = max(0, int(y_mid - h * 0.7)); ry2 = min(H, int(y_mid + h * 0.7))
                    crop = pil.crop((rx1, ry1, rx2, ry2))
                    price_val = 0; price_box = None
                    for psm in (7,6,13):
                        cfg = f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789kK.,"
                        try:
                            d = _pt2.image_to_data(crop, config=cfg, output_type=_pt2.Output.DICT)
                        except Exception:
                            continue
                        n = len(d.get("text", []))
                        for i in range(n):
                            s = d.get("text", [""])[i] or ""
                            v = self._parse_number_k(s)
                            if v is None:
                                continue
                            v = int(v)
                            if v >= int(price_val or 0):
                                price_val = v
                                try:
                                    l = int(d.get("left", [0])[i]); t = int(d.get("top", [0])[i])
                                    w = int(d.get("width", [0])[i]); h = int(d.get("height", [0])[i])
                                    price_box = (rx1 + l, ry1 + t, rx1 + l + w, ry1 + t + h)
                                except Exception:
                                    price_box = (rx1, ry1, rx2, ry2)
                    L = max(0, x_r - min_x)
                    qty_val = int(round(vmax * (L / float(chart_w)))) if chart_w > 0 else 0
                    if price_box is None:
                        price_box = (rx1, ry1, rx2, ry2)
                    results.append((int(price_val or 0), int(qty_val or 0), (x_l, y_t, x_r, y_b), price_box))
                # draw candidate price boxes
                im_cand = im_bars.copy(); dr = _ID.Draw(im_cand)
                for (_pv,_q,_bar,px) in results:
                    dr.rectangle([px[0],px[1],px[2],px[3]], outline=(120,180,210), width=1)
                dumps.append(("step_08_price_candidates.png", im_cand))
                # selected
                if results:
                    results.sort(key=lambda t: (t[0] if t[0] > 0 else 1e18, -t[1]))
                    pv, qv, bar, px = results[0]
                    im_sel = im_cand.copy(); dr = _ID.Draw(im_sel)
                    dr.rectangle([px[0],px[1],px[2],px[3]], outline=(0,213,255), width=1)
                    dumps.append(("step_09_selected_price.png", im_sel))
                    # final annotated
                    final = im_sel.copy(); dr = _ID.Draw(final)
                    dr.text((px[2] + 4, int((px[1] + px[3]) / 2) - 7), self._fmt_k(pv), fill=(169,176,184))
                    dumps.append(("step_10_final.png", final))
            except Exception:
                pass

            # Write
            ok = 0
            for name, im in dumps:
                try:
                    p = os.path.join(out_dir, name)
                    im.save(p)
                    ok += 1
                except Exception:
                    continue
            messagebox.showinfo("保存流程", f"已保存 {ok} 步到 {out_dir}")
            return

        # Reproduce steps similar to _lab_render_steps and dump to files (normal mode)
        # For brevity, we reuse the same code path but capture intermediate images
        dumps: list[tuple[str, Any]] = []
        try:
            # run once and collect via a local inner collector mimicking steps
            from price_reader import _preprocess_variants_for_digits as _pre_v  # type: ignore
        except Exception:
            _pre_v = None
        # Raw
        dumps.append(("step_01_raw.png", pil.copy()))
        # CLAHE+Otsu
        try:
            bgr = _cv2.cvtColor(_np.array(pil), _cv2.COLOR_RGB2BGR)
            gray = _cv2.cvtColor(bgr, _cv2.COLOR_BGR2GRAY)
            clahe = _cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
            cg = clahe.apply(gray)
            _, otsu = _cv2.threshold(cg, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU)
            dumps.append(("step_02_clahe_otsu.png", self._pil_from_cv_gray(otsu)))
        except Exception:
            pass
        # Color masks
        try:
            bg_bgr = _np.array([8, 7, 7], dtype=_np.float32)
            txt_bgr = _np.array([103, 104, 96], dtype=_np.float32)
            bgr = _cv2.cvtColor(_np.array(pil), _cv2.COLOR_RGB2BGR).astype(_np.float32)
            diff_bg = bgr - bg_bgr[None, None, :]
            diff_txt = bgr - txt_bgr[None, None, :]
            d_bg = _np.sqrt(_np.maximum(0.0, _np.sum(diff_bg * diff_bg, axis=2)))
            d_txt = _np.sqrt(_np.maximum(0.0, _np.sum(diff_txt * diff_txt, axis=2)))
            m_close = (d_txt + 5.0 < d_bg)
            m_close &= (d_txt < 220.0)
            mask1 = (m_close.astype(_np.uint8)) * 255
            dumps.append(("step_03_color_mask.png", self._pil_from_cv_gray(mask1)))
            V = (txt_bgr - bg_bgr)
            Vn = float(_np.dot(V, V)) or 1.0
            proj = _np.sum((bgr - bg_bgr[None, None, :]) * V[None, None, :], axis=2) / Vn
            proj = _np.clip(proj, 0.0, 1.0)
            proj8 = (proj * 255.0).astype(_np.uint8)
            _, th_proj = _cv2.threshold(proj8, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU)
            dumps.append(("step_04_color_proj.png", self._pil_from_cv_gray(th_proj)))
        except Exception:
            pass
        # Best/selected variant annotated result; compute locally to avoid UI state dependency
        try:
            from price_reader import _preprocess_variants_for_digits as _pre_v  # type: ignore
            locals_variants = [("raw", pil.copy())]
            try:
                arrs = _pre_v(pil)
                for i, a in enumerate(arrs):
                    try:
                        if len(a.shape) == 2:
                            locals_variants.append((f"v{i:02d}", self._pil_from_cv_gray(a)))
                        else:
                            locals_variants.append((f"v{i:02d}", self._pil_from_cv_bgr(a)))
                    except Exception:
                        pass
            except Exception:
                pass
            # Pick best by our heuristic (min price then max qty)
            best_imgp = locals_variants[0][1]
            best_score = (float("inf"), 0)
            for name, cand in locals_variants:
                _, pr, qt, _ = self._lab_detect_and_draw(cand, draw=False)
                pr = int(pr or 0); qt = int(qt or 0)
                sc = (pr if pr > 0 else float("inf"), -qt)
                if sc < best_score:
                    best_imgp, best_score = cand, sc
            img, price, qty, _ = self._lab_detect_and_draw(best_imgp)
            dumps.append(("step_05_tokens_and_boxes.png", img))
            # Build final with bottom text
            try:
                from PIL import ImageDraw  # type: ignore
                final = img.copy()
                W0, H0 = final.size
                ext = None
                try:
                    from PIL import Image as _PILImage  # type: ignore
                    ext = _PILImage.new("RGB", (W0, H0 + 28), (12, 12, 12))
                    ext.paste(final, (0, 0))
                    final = ext
                except Exception:
                    pass
                draw_final = ImageDraw.Draw(final)
                status = "正常" if (price and qty) else ("仅价格" if price else ("仅数量" if qty else "异常"))
                txt = f"价格: {int(price or 0)}    数量: {int(qty or 0)}    状态: {status}"
                draw_final.text((6, final.size[1] - 22), txt, fill=(230, 230, 230))
                dumps.append(("step_06_final.png", final))
            except Exception:
                pass
        except Exception:
            pass
        # Write
        ok = 0
        for name, im in dumps:
            try:
                p = os.path.join(out_dir, name)
                im.save(p)
                ok += 1
            except Exception:
                continue
        messagebox.showinfo("保存流程", f"已保存 {ok} 步到 {out_dir}")

    def _lab_render_steps_chart(self, *, raw_pil):
        try:
            from PIL import Image, ImageTk, ImageDraw  # type: ignore
            import numpy as _np  # type: ignore
            import cv2 as _cv2  # type: ignore
            import pytesseract as _pt  # type: ignore
        except Exception:
            # Minimal fallback: just show raw
            for w in self.lab_steps_inner.winfo_children():
                w.destroy()
            self._lab_step_tkimgs.clear()
            lbl = ttk.Label(self.lab_steps_inner, text="缺少 OpenCV/Tesseract，无法展示图表模式步骤。")
            lbl.grid(row=0, column=0, sticky="w", padx=8, pady=6)
            imgtk = ImageTk.PhotoImage(raw_pil)
            self._lab_step_tkimgs.append(imgtk)
            ttk.Label(self.lab_steps_inner, image=imgtk).grid(row=1, column=0, sticky="w", padx=8, pady=4)
            return

        # Clear container
        for w in self.lab_steps_inner.winfo_children():
            w.destroy()
        self._lab_step_tkimgs.clear()

        def _to_pil_gray(arr):
            try:
                return Image.fromarray(arr)
            except Exception:
                return raw_pil
        def _draw_rects(base, rects, color, w=1):
            im = base.copy()
            dr = ImageDraw.Draw(im)
            for (x1, y1, x2, y2) in rects:
                try:
                    dr.rectangle([x1, y1, x2, y2], outline=color, width=w)
                except Exception:
                    pass
            return im

        steps: list[tuple[str, Any]] = []
        steps.append(("原图", raw_pil.copy()))

        # 颜色投影
        bgr = _cv2.cvtColor(_np.array(raw_pil), _cv2.COLOR_RGB2BGR)
        H, W = bgr.shape[:2]
        bg_bgr = _np.array([8, 7, 7], dtype=_np.float32)
        bar_bgr = _np.array([96, 104, 103], dtype=_np.float32)
        V = bar_bgr - bg_bgr
        Vn = float(_np.dot(V, V)) or 1.0
        proj = _np.sum((bgr.astype(_np.float32) - bg_bgr[None, None, :]) * V[None, None, :], axis=2) / Vn
        proj = _np.clip(proj, 0.0, 1.0)
        proj8 = (proj * 255.0).astype(_np.uint8)
        steps.append(("颜色投影", _to_pil_gray(proj8)))

        # 阈值
        try:
            _, th = _cv2.threshold(proj8, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU)
        except Exception:
            th = (proj8 > 32).astype(_np.uint8) * 255
        steps.append(("阈值(Otsu)", _to_pil_gray(th)))

        # 形态学(闭->开)
        k = _cv2.getStructuringElement(_cv2.MORPH_RECT, (15, 3))
        x = _cv2.morphologyEx(th, _cv2.MORPH_CLOSE, k, iterations=1)
        k2 = _cv2.getStructuringElement(_cv2.MORPH_RECT, (3, 3))
        x = _cv2.morphologyEx(x, _cv2.MORPH_OPEN, k2, iterations=1)
        steps.append(("形态学(闭→开)", _to_pil_gray(x)))

        # 去横条
        if bool(getattr(self, "var_lab_rm_hbars", tk.BooleanVar(value=True)).get()):
            try:
                x2b = x.copy()
                cnts_tmp, _ = _cv2.findContours((x2b > 0).astype(_np.uint8), _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)
                for c in cnts_tmp:
                    rx, ry, rw, rh = _cv2.boundingRect(c)
                    if rw >= max(60, W // 6) and rh > 0 and (rw / max(1, rh)) >= 8.0:
                        _cv2.rectangle(x2b, (rx, ry), (rx + rw, ry + rh), color=0, thickness=-1)
                x = x2b
            except Exception:
                pass
        steps.append(("去横条", _to_pil_gray(x)))

        # 去中线
        if bool(getattr(self, "var_lab_rm_vsep", tk.BooleanVar(value=True)).get()):
            try:
                colsum = x.sum(axis=0).astype(_np.float32) / 255.0
                xc = int(colsum.argmax()) if colsum.size else -1
                ratio = float(colsum[xc] / max(1.0, H)) if xc >= 0 else 0.0
                if 0 <= xc < W and 0.35 * W <= xc <= 0.65 * W and ratio >= 0.55:
                    left = max(0, xc - 3); right = min(W, xc + 4)
                    x[:, left:right] = 0
            except Exception:
                pass
        steps.append(("去中线", _to_pil_gray(x)))

        # 候选条形
        cnts, _ = _cv2.findContours((x > 0).astype(_np.uint8), _cv2.RETR_EXTERNAL, _cv2.CHAIN_APPROX_SIMPLE)
        cand = []
        for c in cnts:
            rx, ry, rw, rh = _cv2.boundingRect(c)
            ar = rw / max(1.0, float(rh))
            if rw >= max(60, int(W * 0.15)) and 6 <= rh <= int(H * 0.12) and ar >= 4.0:
                cand.append((ry, rx, rx + rw, ry + rh))
        cand.sort(key=lambda t: t[0])
        steps.append(("候选条形(1px)", _draw_rects(raw_pil, cand, (93, 100, 107), 1)))

        # OCR候选与选择
        try:
            try:
                vmax = int(self.var_lab_chart_max.get() or 100_000_000)
            except Exception:
                vmax = 100_000_000
            min_x = min(b[1] for b in cand) if cand else 0
            chart_w = max(1, max((b[2] for b in cand), default=0) - min_x)

            results = []  # (price, qty, bar_rect, price_box)
            for (y_t, x_l, x_r, y_b) in cand:
                y_mid = int((y_t + y_b) / 2)
                h = max(1, y_b - y_t)
                rx1 = min(W - 1, x_r + 2)
                rx2 = min(W, x_r + max(40, int(W * 0.25)))
                ry1 = max(0, int(y_mid - h * 0.7))
                ry2 = min(H, int(y_mid + h * 0.7))
                crop = raw_pil.crop((rx1, ry1, rx2, ry2))
                price_val = 0
                price_box = None
                for psm in (7, 6, 13):
                    cfg = f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789kK.,"
                    try:
                        d = _pt.image_to_data(crop, config=cfg, output_type=_pt.Output.DICT)
                    except Exception:
                        continue
                    n = len(d.get("text", []))
                    for i in range(n):
                        s = d.get("text", [""])[i] or ""
                        v = self._parse_number_k(s)
                        if v is None:
                            continue
                        v = int(v)
                        if v >= int(price_val or 0):
                            price_val = v
                            try:
                                l = int(d.get("left", [0])[i]); t = int(d.get("top", [0])[i])
                                w = int(d.get("width", [0])[i]); h = int(d.get("height", [0])[i])
                                price_box = (rx1 + l, ry1 + t, rx1 + l + w, ry1 + t + h)
                            except Exception:
                                price_box = (rx1, ry1, rx2, ry2)
                L = max(0, x_r - min_x)
                qty_val = int(round(vmax * (L / float(chart_w)))) if chart_w > 0 else 0
                if price_box is None:
                    price_box = (rx1, ry1, rx2, ry2)
                results.append((int(price_val or 0), int(qty_val or 0), (x_l, y_t, x_r, y_b), price_box))

            # 可视化：候选价格框（淡色），最终选择（高亮1px）
            candc = (120, 180, 210)
            selc = (0, 213, 255)
            img_cand = _draw_rects(raw_pil, [p for (_a,_b,_c,p) in results], candc, 1)
            steps.append(("候选价格框(1px)", img_cand))
            if results:
                results.sort(key=lambda t: (t[0] if t[0] > 0 else 1e18, -t[1]))
                _, _, sel_bar, sel_pbox = results[0]
                img_sel = _draw_rects(img_cand, [sel_pbox], selc, 1)
                steps.append(("选择价格(1px高亮)", img_sel))
            # 最终图（含文本）
            if results:
                try:
                    from PIL import ImageDraw as _ID  # type: ignore
                    final = raw_pil.copy()
                    dr = _ID.Draw(final)
                    for (_pv, _qv, (x1,y1,x2,y2), _px) in results:
                        dr.rectangle([x1,y1,x2,y2], outline=(93,100,107), width=1)
                    pv, qv, _bar, px = results[0]
                    dr.rectangle([px[0],px[1],px[2],px[3]], outline=selc, width=1)
                    dr.text((px[2] + 4, int((px[1] + px[3]) / 2) - 7), self._fmt_k(pv), fill=(169,176,184))
                    # 结果文案
                    W0,H0 = final.size
                    try:
                        ext = Image.new("RGB", (W0, H0 + 28), (12,12,12))
                        ext.paste(final, (0,0))
                        final = ext
                        dr = _ID.Draw(final)
                    except Exception:
                        pass
                    dr.text((6, final.size[1]-22), f"价格:{pv} 数量:{qv}", fill=(230,230,230))
                    steps.append(("最终结果", final))
                except Exception:
                    pass
        except Exception:
            pass

        # Render
        r = 0
        for name, imgp in steps:
            ttk.Label(self.lab_steps_inner, text=name).grid(row=r, column=0, sticky="w", padx=8)
            try:
                imgtk = ImageTk.PhotoImage(imgp)
            except Exception:
                r += 1
                continue
            lbl = ttk.Label(self.lab_steps_inner, image=imgtk)
            lbl.grid(row=r + 1, column=0, sticky="w", padx=8, pady=(0, 6))
            lbl.image = imgtk
            self._lab_step_tkimgs.append(imgtk)
            r += 2

    @staticmethod
    def _pil_from_cv_gray(arr):
        try:
            from PIL import Image  # type: ignore
            return Image.fromarray(arr)
        except Exception:
            raise

    @staticmethod
    def _pil_from_cv_bgr(arr):
        try:
            from PIL import Image  # type: ignore
            import cv2 as _cv2  # type: ignore
            rgb = _cv2.cvtColor(arr, _cv2.COLOR_BGR2RGB)
            return Image.fromarray(rgb)
        except Exception:
            raise

    def _append_log(self, s: str) -> None:
        with self._log_lock:
            self.txt.configure(state=tk.NORMAL)
            self.txt.insert(tk.END, time.strftime("[%H:%M:%S] ") + s + "\n")
            self.txt.see(tk.END)
            self.txt.configure(state=tk.DISABLED)

    def _start_multi(self) -> None:
        if hasattr(self, "_multi") and getattr(self._multi, "_thread", None) and self._multi._thread.is_alive():
            messagebox.showwarning("运行", "任务已在运行中。")
            return
        items = self.cfg.get("purchase_items", [])
        if not items:
            messagebox.showwarning("运行", "请先添加至少一个商品任务。")
            return
        # Reset purchased counts for enabled items
        for it in items:
            it.setdefault("purchased", 0)
            if it.get("enabled", True):
                it["purchased"] = int(it.get("purchased", 0))
        save_config(self.cfg, "config.json")
        self._multi = MultiBuyer(
            items,
            on_log=self._append_log,
            on_item_update=lambda idx, it: self.after(0, self._on_item_update, idx, it),
        )
        self._append_log("启动多商品轮询…")
        self._multi.start()

    def _stop(self) -> None:
        if hasattr(self, "_multi") and self._multi:
            self._multi.stop()
            self._append_log("停止信号已发送。")

    # ---------- Items list management ----------
    def _load_items_from_cfg(self) -> None:
        self.tree.delete(*self.tree.get_children())
        items = self.cfg.get("purchase_items", [])
        for i, it in enumerate(items):
            self.tree.insert("", tk.END, iid=str(i), values=(
                "是" if it.get("enabled", True) else "否",
                it.get("item_name", ""),
                int(it.get("price_threshold", 0)),
                int(it.get("target_total", 0)),
                int(it.get("max_per_order", 120)),
                f"{int(it.get('purchased', 0))}/{int(it.get('target_total', 0))}",
            ))
        if items:
            self.tree.selection_set("0")
            self._update_selected_progress()

    def _update_selected_progress(self) -> None:
        sel = self.tree.selection()
        if not sel:
            self._set_selected_progress(0, 0)
            return
        idx = int(sel[0])
        items = self.cfg.get("purchase_items", [])
        if 0 <= idx < len(items):
            it = items[idx]
            p = int(it.get("purchased", 0)); t = int(it.get("target_total", 0))
            self._set_selected_progress(p, t)

    def _set_selected_progress(self, purchased: int, target: int) -> None:
        self.sel_prog["maximum"] = max(1, target)
        self.sel_prog["value"] = min(target, purchased)
        self.sel_prog_lab.config(text=f"{purchased}/{target}")

    # ---------- Modal editor ----------
    def _open_item_modal(self, idx: int | None) -> None:
        items = self.cfg.setdefault("purchase_items", [])
        data = {
            "enabled": True,
            "item_name": "",
            "price_threshold": 0,
            "target_total": 0,
            "max_per_order": 120,
        }
        if idx is not None and 0 <= idx < len(items):
            data.update({k: items[idx].get(k, data[k]) for k in data.keys()})

        top = tk.Toplevel(self)
        top.title("编辑商品" if idx is not None else "新增商品")
        top.transient(self)
        top.grab_set()

        v_enabled = tk.BooleanVar(value=bool(data.get("enabled", True)))
        v_name = tk.StringVar(value=str(data.get("item_name", "")))
        v_thr = tk.IntVar(value=int(data.get("price_threshold", 0)))
        v_target = tk.IntVar(value=int(data.get("target_total", 0)))
        v_max = tk.IntVar(value=int(data.get("max_per_order", 120)))

        frm = ttk.Frame(top)
        frm.pack(padx=10, pady=10)
        ttk.Checkbutton(frm, text="启用", variable=v_enabled).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(frm, text="商品名称").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        ttk.Entry(frm, textvariable=v_name, width=28).grid(row=1, column=1, padx=4, pady=4)
        ttk.Label(frm, text="目标价格(整数)").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        ttk.Spinbox(frm, from_=0, to=10_000_000, textvariable=v_thr, width=12).grid(row=2, column=1, padx=4, pady=4)
        ttk.Label(frm, text="目标购买总量").grid(row=3, column=0, sticky="e", padx=4, pady=4)
        ttk.Spinbox(frm, from_=0, to=999999, textvariable=v_target, width=12).grid(row=3, column=1, padx=4, pady=4)
        ttk.Label(frm, text="单次购买上限").grid(row=4, column=0, sticky="e", padx=4, pady=4)
        ttk.Spinbox(frm, from_=1, to=120, textvariable=v_max, width=12).grid(row=4, column=1, padx=4, pady=4)

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=2, pady=(8, 0))
        def on_save():
            name = v_name.get().strip()
            if not name:
                messagebox.showwarning("校验", "商品名称不能为空。", parent=top)
                return
            item = {
                "enabled": bool(v_enabled.get()),
                "item_name": name,
                "price_threshold": int(v_thr.get()),
                "target_total": int(v_target.get()),
                "max_per_order": int(v_max.get()),
                "purchased": 0 if idx is None else int(items[idx].get("purchased", 0)),
            }
            if idx is None:
                items.append(item)
                new_idx = len(items) - 1
            else:
                items[idx].update(item)
                new_idx = idx
            save_config(self.cfg, "config.json")
            self._load_items_from_cfg()
            try:
                self.tree.selection_set(str(new_idx))
            except Exception:
                pass
            top.destroy()
        ttk.Button(btns, text="保存", command=on_save).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="取消", command=top.destroy).pack(side=tk.LEFT)

    def _delete_item(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        items = self.cfg.get("purchase_items", [])
        if 0 <= idx < len(items):
            del items[idx]
            save_config(self.cfg, "config.json")
            self._load_items_from_cfg()

    def _toggle_item_enable(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        items = self.cfg.get("purchase_items", [])
        if 0 <= idx < len(items):
            items[idx]["enabled"] = not bool(items[idx].get("enabled", True))
            save_config(self.cfg, "config.json")
            self._load_items_from_cfg()

    def _on_item_update(self, idx: int, it: dict) -> None:
        # Update runtime purchased and refresh view + selected progress
        items = self.cfg.get("purchase_items", [])
        if 0 <= idx < len(items):
            items[idx].update({"purchased": int(it.get("purchased", 0))})
            # Refresh one row
            try:
                self.tree.item(str(idx), values=(
                    "是" if items[idx].get("enabled", True) else "否",
                    items[idx].get("item_name", ""),
                    int(items[idx].get("price_threshold", 0)),
                    int(items[idx].get("target_total", 0)),
                    int(items[idx].get("max_per_order", 120)),
                    f"{int(items[idx].get('purchased', 0))}/{int(items[idx].get('target_total', 0))}",
                ))
            except Exception:
                self._load_items_from_cfg()
            # Selected progress
            sel = self.tree.selection()
            if sel and int(sel[0]) == idx:
                p = int(items[idx].get("purchased", 0))
                t = int(items[idx].get("target_total", 0))
                self._set_selected_progress(p, t)

    # ---------- Tree helpers ----------
    def _on_tree_right_click(self, e) -> None:
        row = self.tree.identify_row(e.y)
        if row:
            self.tree.selection_set(row)
            self._ctx_clicked_idx = int(row)
            try:
                self._ctx_menu.tk_popup(e.x_root, e.y_root)
            finally:
                self._ctx_menu.grab_release()

    def _get_clicked_index(self) -> int | None:
        return getattr(self, "_ctx_clicked_idx", None)

    def _tree_on_double_click(self, e) -> None:
        row = self.tree.identify_row(e.y)
        if not row:
            return
        self._open_item_modal(int(row))

    def _tree_on_click(self, e) -> None:
        # Allow column-1 click to toggle enabled
        region = self.tree.identify("region", e.x, e.y)
        if region != "cell":
            return
        row = self.tree.identify_row(e.y)
        col = self.tree.identify_column(e.x)
        if not row:
            return
        # Ensure select row
        self.tree.selection_set(row)
        if col == "#1":  # enabled column
            self._toggle_item_enable()


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

