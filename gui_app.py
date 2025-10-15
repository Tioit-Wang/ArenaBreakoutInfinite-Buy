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
        self.tab3 = ttk.Frame(nb)
        nb.add(self.tab3, text="OCR调参")

        self._build_tab1()
        self._build_tab2()
        self._build_tab3()

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
    def _save_price_roi_shot(self) -> str | None:
        try:
            import pyautogui  # type: ignore
        except Exception as e:
            messagebox.showerror("截图", f"缺少依赖或导入失败: {e}")
            return None
        x1 = int(self.var_px1.get()); y1 = int(self.var_py1.get())
        x2 = int(self.var_px2.get()); y2 = int(self.var_py2.get())
        if x2 <= x1 or y2 <= y1:
            messagebox.showwarning("截图", "价格区域坐标无效，请先设置左上/右下。")
            return None
        w, h = x2 - x1, y2 - y1
        try:
            img = pyautogui.screenshot(region=(x1, y1, w, h))
        except Exception as e:
            messagebox.showerror("截图", f"截取失败: {e}")
            return None
        os.makedirs("images", exist_ok=True)
        path = os.path.join("images", "_debug_price_roi.png")
        try:
            img.save(path)
        except Exception as e:
            messagebox.showerror("截图", f"保存失败: {e}")
            return None
        self._preview_image(path, "预览 - 价格区域")
        return path

    def _preview_price_roi(self) -> None:
        # 截图并展示，同时保存一份处理流程供查看
        path = self._save_price_roi_shot()
        if not path:
            return
        try:
            from PIL import Image  # type: ignore
            pil = Image.open(path).convert("RGB")
        except Exception:
            return
        # 保存流程（与当前调参面板一致）
        try:
            old = self.var_lab_img.get()
            self.var_lab_img.set(path)
            self._lab_save_steps()
            self.var_lab_img.set(old)
        except Exception:
            pass

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

        # Help text
        tip = ttk.Label(frm, text="说明：先检测数字，再自动或按阈值分为左(价格)/右(数量)。数值越小，左侧范围越窄；越大，右侧范围越窄。",
                        foreground="#666")
        tip.pack(fill=tk.X, pady=(0, 4))

        # Preview (single annotated preview for quick glance)
        prev = ttk.Frame(frm)
        prev.pack(fill=tk.X)
        self.lab_prev = ttk.Label(prev)
        self.lab_prev.pack(padx=10, pady=10, anchor="w")
        self.lab_result = ttk.Label(frm, text="未加载")
        self.lab_result.pack(pady=(0, 4), anchor="w")

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
        # Show
        self._lab_show_image(img)
        # Also render step-by-step panel and crop previews
        try:
            self._lab_render_steps(raw_pil=self._lab_pil or pil, variant_pil=pil)
        except Exception:
            # steps are auxiliary; ignore rendering errors
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
        # Reproduce steps similar to _lab_render_steps and dump to files
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
