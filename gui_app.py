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
    def __init__(self, master, name: str, data: Dict[str, Any], on_test, on_capture, on_preview):
        super().__init__(master)
        self.name = name
        self.var_path = tk.StringVar(value=data.get("path", ""))
        self.var_conf = tk.DoubleVar(value=float(data.get("confidence", 0.85)))
        self.on_test = on_test
        self.on_capture = on_capture
        self.on_preview = on_preview

        ttk.Label(self, text=name, width=12).grid(row=0, column=0, sticky="w", padx=4, pady=2)

        # 路径不再展示具体文件路径，仅显示是否已设置
        self.path_status = ttk.Label(self, text=("未设置" if not self.var_path.get().strip() else "已设置"), width=8)
        self.path_status.grid(row=0, column=1, sticky="w", padx=4)
        # 当路径变量变化时，更新状态文案
        try:
            self.var_path.trace_add("write", lambda *_: self.path_status.configure(
                text=("未设置" if not self.get_path() else "已设置")
            ))
        except Exception:
            pass

        ttk.Label(self, text="置信度").grid(row=0, column=3, padx=4)
        s = ttk.Scale(self, from_=0.5, to=0.99, orient=tk.HORIZONTAL, variable=self.var_conf)
        s.grid(row=0, column=4, sticky="we", padx=4)
        ttk.Button(self, text="测试识别", command=lambda: self.on_test(self.name, self.get_path(), self.get_confidence())).grid(row=0, column=5, padx=4)
        ttk.Button(self, text="截图", command=lambda: self.on_capture(self)).grid(row=0, column=6, padx=4)
        ttk.Button(self, text="预览", command=lambda: self.on_preview(self.get_path(), f"预览 - {self.name}")).grid(row=0, column=7, padx=4)

        # 仅让置信度滑条所在列可拉伸
        self.columnconfigure(4, weight=1)

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

        self._build_tab1()
        self._build_tab2()

        # State
        self._buyer: AutoBuyer | None = None
        self._log_lock = threading.Lock()
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
                # Persist immediately
                self._save_and_sync(silent=True)
                # Modal preview
                self._preview_image(path, f"预览 - {row.name}")

            self._select_region(_after)

        # render rows
        rowc = 0
        for key, data in self.cfg.get("templates", {}).items():
            r = TemplateRow(box_tpl, key, data, on_test=test_match, on_capture=capture_into_row, on_preview=self._preview_image)
            r.grid(row=rowc, column=0, sticky="we", padx=6, pady=2)
            self.template_rows[key] = r
            rowc += 1

        # Points / Rects
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

        # 价格区域
        rect = self.cfg.get("rects", {}).get("价格区域", {"x1": 0, "y1": 0, "x2": 0, "y2": 0})
        self.var_px1 = tk.IntVar(value=int(rect.get("x1", 0)))
        self.var_py1 = tk.IntVar(value=int(rect.get("y1", 0)))
        self.var_px2 = tk.IntVar(value=int(rect.get("x2", 0)))
        self.var_py2 = tk.IntVar(value=int(rect.get("y2", 0)))
        ttk.Label(box_pos, text="价格区域 左上(x,y)").grid(row=2, column=0, padx=4, pady=4, sticky="e")
        ttk.Entry(box_pos, textvariable=self.var_px1, width=8).grid(row=2, column=1)
        ttk.Entry(box_pos, textvariable=self.var_py1, width=8).grid(row=2, column=2)
        ttk.Button(box_pos, text="捕获左上", command=lambda: self._capture_point(self.var_px1, self.var_py1, label="请移动到 价格区域 左上…")).grid(row=2, column=3, padx=4)
        ttk.Label(box_pos, text="价格区域 右下(x,y)").grid(row=3, column=0, padx=4, pady=4, sticky="e")
        ttk.Entry(box_pos, textvariable=self.var_px2, width=8).grid(row=3, column=1)
        ttk.Entry(box_pos, textvariable=self.var_py2, width=8).grid(row=3, column=2)
        ttk.Button(box_pos, text="捕获右下", command=lambda: self._capture_point(self.var_px2, self.var_py2, label="请移动到 价格区域 右下…")).grid(row=3, column=3, padx=4)

        for i in range(4):
            box_pos.columnconfigure(i, weight=1)

        # Save / Sync controls
        ctrl = ttk.Frame(outer)
        ctrl.pack(fill=tk.X, padx=8, pady=8)
        ttk.Button(ctrl, text="保存配置并同步", command=self._save_and_sync).pack(side=tk.LEFT)
        ttk.Button(ctrl, text="保存价格区域截图", command=self._save_price_roi_shot).pack(side=tk.LEFT, padx=8)
        ttk.Button(ctrl, text="预览价格区域", command=self._preview_price_roi).pack(side=tk.LEFT)

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

        # Flush points
        self.cfg.setdefault("points", {})
        self.cfg["points"]["第一个商品"] = {"x": int(self.var_first_x.get()), "y": int(self.var_first_y.get())}
        self.cfg["points"]["数量输入框"] = {"x": int(self.var_qty_x.get()), "y": int(self.var_qty_y.get())}

        # Flush rects
        self.cfg.setdefault("rects", {})
        self.cfg["rects"]["价格区域"] = {
            "x1": int(self.var_px1.get()),
            "y1": int(self.var_py1.get()),
            "x2": int(self.var_px2.get()),
            "y2": int(self.var_py2.get()),
        }

        save_config(self.cfg, "config.json")
        sync_to_key_mapping(self.cfg, mapping_path="key_mapping.json")
        if not silent:
            messagebox.showinfo("配置", "已保存并同步至 key_mapping.json")

    # ---------- Region select & ROI snapshot ----------
    def _save_price_roi_shot(self) -> None:
        try:
            import pyautogui  # type: ignore
        except Exception as e:
            messagebox.showerror("截图", f"缺少依赖或导入失败: {e}")
            return
        x1 = int(self.var_px1.get()); y1 = int(self.var_py1.get())
        x2 = int(self.var_px2.get()); y2 = int(self.var_py2.get())
        if x2 <= x1 or y2 <= y1:
            messagebox.showwarning("截图", "价格区域坐标无效，请先设置左上/右下。")
            return
        w, h = x2 - x1, y2 - y1
        try:
            img = pyautogui.screenshot(region=(x1, y1, w, h))
        except Exception as e:
            messagebox.showerror("截图", f"截取失败: {e}")
            return
        os.makedirs("images", exist_ok=True)
        path = os.path.join("images", "_debug_price_roi.png")
        try:
            img.save(path)
        except Exception as e:
            messagebox.showerror("截图", f"保存失败: {e}")
            return
        self._preview_image(path, "预览 - 价格区域")

    def _preview_price_roi(self) -> None:
        # 直接按照当前坐标截图并展示（不再要求先保存现成文件）
        self._save_price_roi_shot()

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
    # Wrap preview to also display OCR results for price ROI
    try:
        _ORIG_PREVIEW = App._preview_image  # type: ignore[attr-defined]

        def _preview_with_values(self: App, path: str, title: str = "Ԥ��") -> None:  # type: ignore[name-defined]
            _ORIG_PREVIEW(self, path, title)
            try:
                import os as _os
                if _os.path.basename(path) == "_debug_price_roi.png":
                    from price_reader import read_price_and_stock_from_config
                    p, q = read_price_and_stock_from_config(mapping_path="key_mapping.json", debug=False)
                    messagebox.showinfo("OCR ���", f"�۸�: {p}    ����: {q}")
            except Exception:
                pass

        App._preview_image = _preview_with_values  # type: ignore[assignment]
    except Exception:
        pass

    main()
