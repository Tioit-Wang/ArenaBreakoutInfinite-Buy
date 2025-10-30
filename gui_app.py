import os
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import uuid
from typing import Any, Dict, List, Optional

# 中文字体工具（Matplotlib/PIL/Tk）
try:
    from font_util import pil_font, setup_matplotlib_chinese, tk_font, draw_text  # type: ignore
except Exception:
    def pil_font(_size: int = 14):  # type: ignore
        return None
    def setup_matplotlib_chinese() -> None:  # type: ignore
        return
    def tk_font(_root, _size: int = 12):  # type: ignore
        return None
    def draw_text(draw, xy, text: str, fill=(255, 255, 255), size: int = 14):  # type: ignore
        try:
            draw.text(xy, text, fill=fill)
        except Exception:
            pass

from app_config import ensure_default_config, load_config, save_config
try:
    # Ensure PyAutoGUI calls won’t crash if OpenCV is missing
    from compat import ensure_pyautogui_confidence_compat

    ensure_pyautogui_confidence_compat()
except Exception:
    pass
from task_runner import TaskRunner, run_launch_flow, ScreenOps
# 尝试稳健加载 multi_snipe_runner：优先常规导入；失败时回退到按文件路径加载，并记录错误信息
_multi_import_error: str | None = None
try:
    from multi_snipe_runner import MultiSnipeRunner, SnipeItem  # type: ignore
except ImportError as e:
    try:
        import importlib.util as _ilu  # type: ignore
        _here = os.path.dirname(os.path.abspath(__file__))
        _path = os.path.join(_here, 'multi_snipe_runner.py')
        if os.path.exists(_path):
            _spec = _ilu.spec_from_file_location('multi_snipe_runner', _path)
            if _spec and _spec.loader:
                _mod = _ilu.module_from_spec(_spec)
                _spec.loader.exec_module(_mod)  # type: ignore
                MultiSnipeRunner = getattr(_mod, 'MultiSnipeRunner', None)  # type: ignore
                SnipeItem = getattr(_mod, 'SnipeItem', None)  # type: ignore
                if MultiSnipeRunner is None or SnipeItem is None:
                    _multi_import_error = f"模块加载不完整：{_path}"
        else:
            _multi_import_error = f"文件不存在：{_path}"
    except Exception as e2:
        _multi_import_error = f"导入失败：{e!r}; 回退失败：{e2!r}"
        MultiSnipeRunner = None  # type: ignore
        SnipeItem = None  # type: ignore
except Exception as e:
    # 非 ImportError（如语法/运行时错误），保留详细异常用于 UI 提示
    _multi_import_error = f"导入异常：{e!r}"
    MultiSnipeRunner = None  # type: ignore
    SnipeItem = None  # type: ignore


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
            f = tk_font(self.root, 12)
        except Exception:
            f = None
        try:
            if f is not None:
                cv.create_text(w // 2, 30, text="拖拽选择区域，Esc/右键取消", fill="white", font=f)
            else:
                cv.create_text(w // 2, 30, text="拖拽选择区域，Esc/右键取消", fill="white")
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


class _FixedSizeSelector:
    """Overlay to position a fixed-size capture box that follows the mouse.

    - Size fixed to (w, h) pixels.
    - Moves with mouse motion; left click confirms; right click/Esc cancels.
    Calls on_done((x1, y1, x2, y2)) on confirm, or on_done(None) on cancel.
    """

    def __init__(self, root: tk.Tk, w: int, h: int, on_done):
        self.root = root
        self.w = int(max(1, w))
        self.h = int(max(1, h))
        self.on_done = on_done
        self.top: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self.rect = None
        self._x = 0
        self._y = 0

    def show(self) -> None:
        top = tk.Toplevel(self.root)
        self.top = top
        W = self.root.winfo_screenwidth()
        H = self.root.winfo_screenheight()
        top.geometry(f"{W}x{H}+0+0")
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
            f = tk_font(self.root, 12)
        except Exception:
            f = None
        try:
            if f is not None:
                cv.create_text(W // 2, 30, text=f"移动鼠标定位，左键确认（{self.w}x{self.h}），右键/ESC取消", fill="white", font=f)
            else:
                cv.create_text(W // 2, 30, text=f"移动鼠标定位，左键确认（{self.w}x{self.h}），右键/ESC取消", fill="white")
        except Exception:
            pass
        self.rect = cv.create_rectangle(0, 0, 1, 1, outline="red", width=2)
        cv.bind("<Motion>", self._on_motion)
        cv.bind("<Button-1>", self._on_confirm)
        cv.bind("<Button-3>", self._on_cancel)
        cv.bind("<Escape>", self._on_cancel)
        try:
            cv.focus_force()
            top.grab_set()
        except Exception:
            pass

    def _on_motion(self, e):
        self._x, self._y = int(e.x_root), int(e.y_root)
        self._redraw()

    def _redraw(self):
        if not (self.canvas and self.rect):
            return
        x1 = self._x - self.w // 2
        y1 = self._y - self.h // 2
        x2 = x1 + self.w
        y2 = y1 + self.h
        self.canvas.coords(self.rect, x1, y1, x2, y2)

    def _on_confirm(self, _e):
        if self.top is None:
            return
        x1 = self._x - self.w // 2
        y1 = self._y - self.h // 2
        x2 = x1 + self.w
        y2 = y1 + self.h
        try:
            self.top.grab_release()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass
        self.on_done((x1, y1, x2, y2))

    def _on_cancel(self, _e):
        if self.top is not None:
            try:
                self.top.grab_release()
            except Exception:
                pass
            try:
                self.top.destroy()
            except Exception:
                pass
        self.on_done(None)


class _CardSelector:
    """固定 165x212 的跟随定位选择器，按卡片样式渲染三段色块与图片区域虚线框。

    - 顶部 20（蓝色）、底部 30（绿色）、中间剩余（黄色）。
    - 图片区域：居中，左右与上下各缩进 30（如需仅左右缩进，可将 margin_tb 设为 0）。
    - 左键确认；右键/ESC 取消。
    - 回调 on_done((x1,y1,x2,y2)) 或 on_done(None)。
    """

    def __init__(
        self,
        root: tk.Tk,
        on_done,
        *,
        w: int = 165,
        h: int = 212,
        top_h: int = 20,
        bottom_h: int = 30,
        margin_lr: int = 30,
        margin_tb: int = 30,
    ) -> None:
        self.root = root
        self.on_done = on_done
        self.w = int(max(1, w))
        self.h = int(max(1, h))
        self.top_h = int(max(0, top_h))
        self.bottom_h = int(max(0, bottom_h))
        self.margin_lr = int(max(0, margin_lr))
        self.margin_tb = int(max(0, margin_tb))
        self.top: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self._x = 0
        self._y = 0
        # Canvas item ids
        self.item_top = None
        self.item_mid = None
        self.item_bot = None
        self.item_outline = None
        self.item_img_rect = None

    def show(self) -> None:
        top = tk.Toplevel(self.root)
        self.top = top
        W = self.root.winfo_screenwidth()
        H = self.root.winfo_screenheight()
        top.geometry(f"{W}x{H}+0+0")
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
            f = tk_font(self.root, 12)
        except Exception:
            f = None
        try:
            if f is not None:
                cv.create_text(
                    W // 2,
                    30,
                    text=f"移动鼠标定位，左键确认（{self.w}x{self.h}），右键/ESC取消",
                    fill="white",
                    font=f,
                )
            else:
                cv.create_text(
                    W // 2,
                    30,
                    text=f"移动鼠标定位，左键确认（{self.w}x{self.h}），右键/ESC取消",
                    fill="white",
                )
        except Exception:
            pass
        # Pre-create items for fast redraw
        self.item_top = cv.create_rectangle(0, 0, 1, 1, fill="#2d7cff", outline="")
        self.item_mid = cv.create_rectangle(0, 0, 1, 1, fill="#ffd84d", outline="")
        self.item_bot = cv.create_rectangle(0, 0, 1, 1, fill="#2ea043", outline="")
        # 外部浅灰色边框，宽度尽量接近 0.5px（Tk 允许小数宽度；若不支持则退化为 1px）
        try:
            self.item_outline = cv.create_rectangle(0, 0, 1, 1, outline="#cccccc", width=0.5)
        except Exception:
            self.item_outline = cv.create_rectangle(0, 0, 1, 1, outline="#cccccc", width=1)
        self.item_img_rect = cv.create_rectangle(0, 0, 1, 1, outline="#333", dash=(4, 2))

        cv.bind("<Motion>", self._on_motion)
        cv.bind("<Button-1>", self._on_confirm)
        cv.bind("<Button-3>", self._on_cancel)
        cv.bind("<Escape>", self._on_cancel)
        try:
            cv.focus_force()
            top.grab_set()
        except Exception:
            pass

    def _on_motion(self, e) -> None:
        self._x, self._y = int(e.x_root), int(e.y_root)
        self._redraw()

    def _redraw(self) -> None:
        if not self.canvas:
            return
        x1 = self._x - self.w // 2
        y1 = self._y - self.h // 2
        x2 = x1 + self.w
        y2 = y1 + self.h
        # Sections
        top_h = self.top_h
        bot_h = self.bottom_h
        mid_top = y1 + top_h
        mid_btm = y2 - bot_h
        # Update shapes
        if self.item_top is not None:
            self.canvas.coords(self.item_top, x1, y1, x2, y1 + top_h)
        if self.item_mid is not None:
            self.canvas.coords(self.item_mid, x1, mid_top, x2, mid_btm)
        if self.item_bot is not None:
            self.canvas.coords(self.item_bot, x1, y2 - bot_h, x2, y2)
        if self.item_outline is not None:
            self.canvas.coords(self.item_outline, x1 + 1, y1 + 1, x2 - 1, y2 - 1)
        # Inner image rect inside middle area with margins
        ix1 = x1 + self.margin_lr
        ix2 = x2 - self.margin_lr
        iy1 = mid_top + self.margin_tb
        iy2 = mid_btm - self.margin_tb
        if self.item_img_rect is not None:
            self.canvas.coords(self.item_img_rect, ix1, iy1, ix2, iy2)

    def _on_confirm(self, _e) -> None:
        if self.top is None:
            return
        x1 = self._x - self.w // 2
        y1 = self._y - self.h // 2
        x2 = x1 + self.w
        y2 = y1 + self.h
        try:
            self.top.grab_release()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass
        self.on_done((x1, y1, x2, y2))

    def _on_cancel(self, _e) -> None:
        if self.top is not None:
            try:
                self.top.grab_release()
            except Exception:
                pass
            try:
                self.top.destroy()
            except Exception:
                pass
        self.on_done(None)

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

        # 状态列（红/绿圆点）
        self.path_status = tk.Label(self, text="", width=2, anchor="center")
        self.path_status.grid(row=0, column=0, sticky="w", padx=(2, 4))

        # 名称列
        ttk.Label(self, text=name, width=12).grid(row=0, column=1, sticky="w", padx=4, pady=2)
        # 当路径变量变化时，更新状态文案（并检测文件是否存在）
        def _update_path_status() -> None:
            p = self.get_path()
            if not p:
                self.path_status.configure(text="●", fg="#d74c4c")  # red
            elif os.path.exists(p):
                self.path_status.configure(text="●", fg="#2ea043")  # green
            else:
                self.path_status.configure(text="●", fg="#d74c4c")  # red
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

        ttk.Label(self, text="置信度").grid(row=0, column=2, padx=4)
        # 数值输入框 0-1，步长 0.01
        try:
            sp = ttk.Spinbox(self, from_=0.0, to=1.0, increment=0.01, textvariable=self.var_conf, width=6, format="%.2f")
        except Exception:
            sp = tk.Spinbox(self, from_=0.0, to=1.0, increment=0.01, textvariable=self.var_conf, width=6)
        sp.grid(row=0, column=3, sticky="w", padx=4)
        # autosave on change
        if self.on_change:
            try:
                self.var_conf.trace_add("write", lambda *_: self.on_change())
            except Exception:
                pass
        ttk.Button(self, text="点击测试", command=lambda: self.on_test(self.name, self.get_path(), self.get_confidence())).grid(row=0, column=4, padx=4)
        ttk.Button(self, text="模板捕获", command=lambda: self.on_capture(self)).grid(row=0, column=5, padx=4)
        ttk.Button(self, text="模版预览", command=lambda: self.on_preview(self.get_path(), f"预览 - {self.name}")).grid(row=0, column=6, padx=4)

        # 保持布局稳定
        for c in range(0, 7):
            self.columnconfigure(c, weight=0)

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
        self.geometry("1120x740")
        # Autosave scheduler
        self._autosave_after_id: str | None = None
        self._autosave_delay_ms: int = 300

        # Config
        ensure_default_config("config.json")
        self.cfg: Dict[str, Any] = load_config("config.json")
        # Independent tasks store (not reusing auto-buy purchase_items)
        self.tasks_path = "buy_tasks.json"
        self.tasks_data: Dict[str, Any] = self._load_tasks_data(self.tasks_path)
        # Index of the task currently being edited (None if not editing)
        self._editing_task_index: int | None = None
        # References to task mode radio buttons (time/round)
        self._task_mode_radios: tuple[ttk.Radiobutton, ttk.Radiobutton] | None = None  # type: ignore
        # Ensure each item has a stable id for history mapping
        try:
            self._ensure_item_ids()
        except Exception:
            pass

        # UI
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True)

        self.tab1 = ttk.Frame(nb)
        nb.add(self.tab1, text="初始化配置")
        # New: 购买任务配置（仅配置，不含启动）
        self.tab_tasks = ttk.Frame(nb)
        nb.add(self.tab_tasks, text="购买任务配置")
        # New: 执行日志（独立任务执行/调度）
        self.tab_exec = ttk.Frame(nb)
        nb.add(self.tab_exec, text="执行日志")

        # 新增：测试
        self.tab_test = ttk.Frame(nb)
        nb.add(self.tab_test, text="测试")

        # 新增：多商品抢购模式（独立于其他任务）
        self.tab_multi = ttk.Frame(nb)
        nb.add(self.tab_multi, text="多商品抢购模式")

        # New: 利润计算器
        self.tab_profit = ttk.Frame(nb)
        nb.add(self.tab_profit, text="利润计算")

        # 初始化多商品抢购任务状态（需在构建多商品页前完成）
        self.snipe_tasks_path = "snipe_tasks.json"
        self.snipe_tasks_data: Dict[str, Any] = self._load_snipe_tasks_data(self.snipe_tasks_path)
        self._snipe_thread = None
        self._snipe_stop = threading.Event()
        self._snipe_runner = None
        self._snipe_log_lock = threading.Lock()
        self._snipe_editing_index: int | None = None

        self._build_tab1()
        self._build_tab_tasks()
        self._build_tab_exec()
        self._build_tab_test()
        self._build_tab_multi()
        self._build_tab_profit()
        try:
            nb2 = self.tab1.nametowidget(self.tab1.winfo_parent())
            self.tab_goods = ttk.Frame(nb2)
            nb2.add(self.tab_goods, text="物品市场")
            self.goods_ui = GoodsMarketUI(self.tab_goods)
        except Exception:
            pass
        # 旧自动购买运行状态已移除
        # OCR 调参面板已移除

        # State
        # 单商品模式已移除
        self._log_lock = threading.Lock()
        self._exec_log_lock = threading.Lock()
        # Test launch/exit running flags
        self._test_launch_running = False
        self._test_exit_running = False
        self._tpl_slug_map = {
            # Chinese labels
            "启动按钮": "btn_launch",
            "设置按钮": "btn_settings",
            "退出按钮": "btn_exit",
            "退出确认按钮": "btn_exit_confirm",
            "首页按钮": "btn_home",
            "市场按钮": "btn_market",
            # 新增：标识模板（用于页面就绪/所在页判断）
            "首页标识模板": "home_indicator",
            "市场标识模板": "market_indicator",
            "市场搜索栏": "input_search",
            "市场搜索按钮": "btn_search",
            "购买按钮": "btn_buy",
            "购买成功": "buy_ok",
            "购买失败": "buy_fail",
            "数量最大按钮": "btn_max",
            "数量+": "qty_plus",
            "数量-": "qty_minus",
            "商品关闭位置": "btn_close",
            "刷新按钮": "btn_refresh",
            "返回按钮": "btn_back",
            # ASCII keys map to themselves
            "btn_launch": "btn_launch",
            "btn_settings": "btn_settings",
            "btn_exit": "btn_exit",
            "btn_exit_confirm": "btn_exit_confirm",
            "home_indicator": "home_indicator",
            "market_indicator": "market_indicator",
            "btn_home": "btn_home",
            "btn_market": "btn_market",
            "input_search": "input_search",
            "btn_search": "btn_search",
            "btn_buy": "btn_buy",
            "buy_ok": "buy_ok",
            "buy_fail": "buy_fail",
            "btn_max": "btn_max",
            "qty_plus": "qty_plus",
            "qty_minus": "qty_minus",
            "btn_close": "btn_close",
            "btn_refresh": "btn_refresh",
            "btn_back": "btn_back",
            # 多商品抢购：标签模板
            "最近购买模板": "recent_purchases_tab",
            "我的收藏模板": "favorites_tab",
        }

        # OCR warm-up removed (PaddleOCR path removed)

        # Global hotkey (Tk sequence). Bind configured + safe fallback
        self._bound_toggle_hotkeys: list[str] = []
        try:
            self._rebind_toggle_hotkey()
        except Exception:
            pass

        # Timer for reflecting run state in UI
        self._run_state_after_id: str | None = None
        # Runner state poll id
        self._exec_state_after_id: str | None = None
        # Background runner instance (独立新逻辑)
        self._runner: TaskRunner | None = None

        # 多商品抢购：任务与运行状态已在构建标签页前初始化

    # ---------- Mouse wheel binding helper ----------
    def _bind_mousewheel(self, area, target=None) -> None:
        """Enable mouse wheel scrolling on `target` when cursor is over `area`.

        - Works for Canvas, Treeview, Text, Listbox (anything with yview/xview).
        - Cross-platform: Windows/macOS via <MouseWheel>, Linux via <Button-4/5>.
        """
        if target is None:
            target = area

        def _y_scroll(units: int) -> None:
            try:
                target.yview_scroll(int(units), "units")
            except Exception:
                pass

        def _x_scroll(units: int) -> None:
            try:
                target.xview_scroll(int(units), "units")
            except Exception:
                pass

        def _on_mousewheel(e):  # Windows / macOS
            try:
                delta = int(e.delta)
            except Exception:
                delta = 0
            if delta == 0:
                return
            step = -1 if delta > 0 else 1
            _y_scroll(step)

        def _on_shift_mousewheel(e):  # Horizontal scroll when Shift pressed
            try:
                delta = int(getattr(e, "delta", 0))
            except Exception:
                delta = 0
            if delta == 0:
                return
            step = -1 if delta > 0 else 1
            _x_scroll(step)

        def _on_linux_up(_e):
            _y_scroll(-1)

        def _on_linux_down(_e):
            _y_scroll(1)

        def _bind_all(_e=None):
            try:
                area.bind_all("<MouseWheel>", _on_mousewheel)
                area.bind_all("<Shift-MouseWheel>", _on_shift_mousewheel)
                area.bind_all("<Button-4>", _on_linux_up)
                area.bind_all("<Button-5>", _on_linux_down)
            except Exception:
                pass

        def _unbind_all(_e=None):
            try:
                area.unbind_all("<MouseWheel>")
                area.unbind_all("<Shift-MouseWheel>")
                area.unbind_all("<Button-4>")
                area.unbind_all("<Button-5>")
            except Exception:
                pass

        try:
            area.bind("<Enter>", _bind_all)
            area.bind("<Leave>", _unbind_all)
        except Exception:
            pass

    # ---------- Window placement helper ----------
    def _place_modal(self, top: tk.Toplevel, width: int, height: int) -> None:
        """Place modal near the center of the current window within screen bounds."""
        try:
            sw, sh = int(self.winfo_screenwidth()), int(self.winfo_screenheight())
        except Exception:
            sw, sh = 1920, 1080
        try:
            px, py = int(self.winfo_rootx()), int(self.winfo_rooty())
            pw, ph = int(self.winfo_width() or 0), int(self.winfo_height() or 0)
        except Exception:
            px, py, pw, ph = 100, 100, 980, 680
        if pw <= 0 or ph <= 0:
            pw, ph = 980, 680
        x = px + max(0, (pw - int(width)) // 2)
        y = py + max(0, (ph - int(height)) // 2)
        # Clamp inside screen
        x = max(0, min(x, sw - int(width)))
        y = max(0, min(y, sh - int(height)))
        try:
            top.geometry(f"{int(width)}x{int(height)}+{int(x)}+{int(y)}")
        except Exception:
            try:
                top.geometry(f"{int(width)}x{int(height)}")
            except Exception:
                pass

    # ---------- Tasks data I/O ----------
    def _load_tasks_data(self, path: str) -> Dict[str, Any]:
        try:
            import json
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if not isinstance(data.get("tasks"), list):
                        data["tasks"] = []
                    if not isinstance(data.get("step_delays"), dict):
                        data["step_delays"] = {"default": 0.01}
                    # New defaults for task mode and restart policy
                    if str(data.get("task_mode") or "") not in ("time", "round"):
                        data["task_mode"] = "time"
                    try:
                        rmin = int(data.get("restart_every_min", 60) or 60)
                    except Exception:
                        rmin = 60
                    if rmin <= 0:
                        rmin = 60
                    data["restart_every_min"] = rmin
                    # Ensure each task has an explicit order field
                    for i, it in enumerate(data["tasks"]):
                        if isinstance(it, dict) and "order" not in it:
                            it["order"] = i
                    return data
        except Exception:
            pass
        return {"tasks": [], "step_delays": {"default": 0.01}, "task_mode": "time", "restart_every_min": 60}

    def _save_tasks_data(self) -> None:
        try:
            import json
            with open(self.tasks_path, "w", encoding="utf-8") as f:
                json.dump(self.tasks_data, f, ensure_ascii=False, indent=4)
        except Exception:
            pass

    def _get_toggle_hotkey(self) -> str:
        try:
            hot = self.cfg.get("hotkeys", {})
            hk = hot.get("toggle") or hot.get("stop") or "<Control-Alt-t>"
            if not isinstance(hk, str) or not hk:
                return "<Control-Alt-t>"
            return hk
        except Exception:
            return "<Control-Alt-t>"

    # ---------- Tab: 购买任务配置 ----------
    def _build_tab_tasks(self) -> None:
        outer = self.tab_tasks
        frm = ttk.Frame(outer)
        frm.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Top controls
        top = ttk.Frame(frm)
        top.pack(fill=tk.X)
        self.btn_add_task = ttk.Button(top, text="新增任务", command=self._add_task_card)
        self.btn_add_task.pack(side=tk.LEFT)
        ttk.Label(top, text="附加：高级配置见下方模块").pack(side=tk.RIGHT)

        # Task mode configuration
        mode_box = ttk.LabelFrame(frm, text="任务模式配置")
        mode_box.pack(fill=tk.X, padx=0, pady=(8, 6))
        self.var_task_mode = tk.StringVar(value=str(self.tasks_data.get("task_mode", "time")))
        r1 = ttk.Radiobutton(mode_box, text="按时间区间执行", value="time", variable=self.var_task_mode)
        r2 = ttk.Radiobutton(mode_box, text="轮流执行", value="round", variable=self.var_task_mode)
        r1.pack(side=tk.LEFT, padx=8, pady=4)
        r2.pack(side=tk.LEFT, padx=8, pady=4)
        # Keep references for enabling/disabling during editing
        self._task_mode_radios = (r1, r2)
        # Subtle hint shown when mode is locked during editing/drafting
        try:
            self._task_mode_hint = ttk.Label(mode_box, text="", foreground="#666")
        except Exception:
            self._task_mode_hint = None
        def _on_mode_change(*_):
            # Block mode change while editing or drafting to avoid losing unsaved changes
            try:
                if (self._editing_task_index is not None) or self._task_draft_alive:
                    cur = str(self.tasks_data.get("task_mode", "time"))
                    # Only warn and revert if user actually attempted to change value
                    if self.var_task_mode.get() != cur:
                        try:
                            messagebox.showwarning("任务模式", "请先保存或取消当前正在编辑/新增的任务，再切换任务模式。")
                        except Exception:
                            pass
                        # Revert UI selection back to persisted value
                        try:
                            self.var_task_mode.set(cur)
                        except Exception:
                            pass
                    return
            except Exception:
                pass
            m = self.var_task_mode.get()
            if m not in ("time", "round"):
                m = "time"
            self.tasks_data["task_mode"] = m
            # Persist and re-render cards to reflect mode-specific fields
            self._save_tasks_data()
            self._render_task_cards()
        try:
            self.var_task_mode.trace_add("write", _on_mode_change)
        except Exception:
            pass
        # Apply initial enable/disable state
        try:
            self._update_task_mode_controls_state()
        except Exception:
            pass

        # Scroll container for cards
        wrapper = ttk.Frame(frm)
        wrapper.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        # Keep root for placing panels under the list
        self._tasks_root_frame = frm
        self.cards_canvas = tk.Canvas(wrapper, highlightthickness=0)
        vsb = ttk.Scrollbar(wrapper, orient=tk.VERTICAL, command=self.cards_canvas.yview)
        self.cards_canvas.configure(yscrollcommand=vsb.set)
        self.cards_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.cards_inner = ttk.Frame(self.cards_canvas)
        self.cards_window = self.cards_canvas.create_window(0, 0, anchor=tk.NW, window=self.cards_inner)

        def _on_inner(_e=None):
            try:
                self.cards_canvas.configure(scrollregion=self.cards_canvas.bbox("all"))
            except Exception:
                pass
        def _on_canvas(_e=None):
            try:
                w = self.cards_canvas.winfo_width()
                self.cards_canvas.itemconfigure(self.cards_window, width=w)
            except Exception:
                pass
        self.cards_inner.bind("<Configure>", _on_inner)
        self.cards_canvas.bind("<Configure>", _on_canvas)
        # Enable mouse-wheel scrolling over the task cards area
        try:
            self._bind_mousewheel(self.cards_inner, self.cards_canvas)
        except Exception:
            pass

        # State: whether a draft card exists
        self._task_draft_alive = False
        self._render_task_cards()

    def _render_task_cards(self) -> None:
        for w in self.cards_inner.winfo_children():
            w.destroy()
        items = list((self.tasks_data.get("tasks", []) or []))
        try:
            items.sort(key=lambda d: (int(d.get("order", 0)) if isinstance(d, dict) else 0))
        except Exception:
            pass
        # Show existing items; if one is in editing state, render it as editable
        for i, it in enumerate(items):
            editable = (self._editing_task_index == i)
            self._build_task_card(self.cards_inner, i, it, editable=editable, draft=False)
        # Update add button availability: disable when a draft exists or editing an existing item
        self._task_draft_alive = any(getattr(w, "_is_draft", False) for w in self.cards_inner.winfo_children())
        try:
            disable_add = bool(self._task_draft_alive or (self._editing_task_index is not None))
        except Exception:
            disable_add = self._task_draft_alive
        try:
            self.btn_add_task.configure(state=(tk.DISABLED if disable_add else tk.NORMAL))
        except Exception:
            pass
        # Also enable/disable task mode radios accordingly
        try:
            self._update_task_mode_controls_state()
        except Exception:
            pass
        # Advanced config panel
        try:
            self._build_step_delay_panel(self._tasks_root_frame)
        except Exception:
            pass

    def _update_task_mode_controls_state(self) -> None:
        """Enable/disable task-mode radio buttons based on editing/draft state."""
        try:
            radios = self._task_mode_radios
        except Exception:
            radios = None
        if not radios:
            return
        disable = False
        try:
            disable = bool((self._editing_task_index is not None) or self._task_draft_alive)
        except Exception:
            pass
        state = (tk.DISABLED if disable else tk.NORMAL)
        for rb in radios:
            try:
                rb.configure(state=state)
            except Exception:
                pass
        # Update subtle hint visibility/content
        hint = getattr(self, "_task_mode_hint", None)
        if hint is not None:
            try:
                if disable:
                    hint.configure(text="编辑/新增中：任务模式已锁定")
                    if not bool(hint.winfo_ismapped()):
                        hint.pack(side=tk.LEFT, padx=8)
                else:
                    hint.configure(text="")
                    if bool(hint.winfo_ismapped()):
                        hint.pack_forget()
            except Exception:
                pass

    def _add_task_card(self) -> None:
        # Disallow adding when editing an existing item or a draft already exists
        try:
            if self._editing_task_index is not None:
                messagebox.showwarning("新增任务", "请先保存或取消当前正在编辑的任务。")
                return
        except Exception:
            pass
        if self._task_draft_alive:
            messagebox.showwarning("新增任务", "已存在一个正在新增的任务，请先保存或取消。")
            return
        # Create an empty draft card
        draft = {
            "enabled": True,
            "item_name": "",
            "price_threshold": 0,
            "price_premium_pct": 0,
            "restock_price": 0,
            # 新增字段默认值（补货模式溢价%）
            "restock_premium_pct": 0,
            "target_total": 0,
            "time_start": "",
            "time_end": "",
        }
        self._build_task_card(self.cards_inner, None, draft, editable=True, draft=True)
        self._task_draft_alive = True
        try:
            self.btn_add_task.configure(state=tk.DISABLED)
        except Exception:
            pass
        # Disable task mode radios while drafting
        try:
            self._update_task_mode_controls_state()
        except Exception:
            pass

    # ---------- Goods picker ----------
    def _load_goods_for_picker(self) -> list[dict]:
        goods: list[dict] = []
        try:
            import json
            if os.path.exists("goods.json"):
                with open("goods.json", "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    goods = [d for d in data if isinstance(d, dict)]
        except Exception:
            goods = []
        return goods

    def _open_goods_picker(self, on_pick) -> None:
        goods = self._load_goods_for_picker()
        top = tk.Toplevel(self)
        top.title("选择物品")
        top.geometry("720x480")
        top.transient(self)
        try:
            top.grab_set()
        except Exception:
            pass

        # Filters
        ctrl = ttk.Frame(top)
        ctrl.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(ctrl, text="搜索").pack(side=tk.LEFT)
        var_q = tk.StringVar(value="")
        ent = ttk.Entry(ctrl, textvariable=var_q, width=24)
        ent.pack(side=tk.LEFT, padx=6)
        try:
            ent.focus_set()
        except Exception:
            pass
        var_big = tk.StringVar(value="全部")
        var_sub = tk.StringVar(value="全部")
        # derive categories from data
        bigs = ["全部"] + sorted({str(g.get("big_category", "")) for g in goods if g.get("big_category")})
        cmb_big = ttk.Combobox(ctrl, values=bigs, state="readonly", width=12, textvariable=var_big)
        cmb_big.pack(side=tk.LEFT, padx=6)
        cmb_sub = ttk.Combobox(ctrl, values=["全部"], state="readonly", width=16, textvariable=var_sub)
        cmb_sub.pack(side=tk.LEFT, padx=6)
        def _refresh_sub():
            sel_big = var_big.get()
            subs = sorted({str(g.get("sub_category", "")) for g in goods if (sel_big in ("全部", str(g.get("big_category", ""))))})
            vals = ["全部"] + [s for s in subs if s]
            try:
                cmb_sub.configure(values=vals)
            except Exception:
                pass
            if var_sub.get() not in vals:
                var_sub.set("全部")
        cmb_big.bind("<<ComboboxSelected>>", lambda _e=None: _refresh_sub())
        _refresh_sub()

        # List area
        body = ttk.Frame(top)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0,8))
        cols = ("name", "big", "sub")
        tree = ttk.Treeview(body, columns=cols, show="headings")
        tree.heading("name", text="名称")
        tree.heading("big", text="大类")
        tree.heading("sub", text="子类")
        tree.column("name", width=280)
        tree.column("big", width=120)
        tree.column("sub", width=180)
        # Attach vertical scrollbar
        sb = ttk.Scrollbar(body, orient=tk.VERTICAL, command=tree.yview)
        tree.configure(yscrollcommand=sb.set)
        tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.LEFT, fill=tk.Y)
        # Mouse-wheel support for picker list
        try:
            self._bind_mousewheel(tree, tree)
        except Exception:
            pass

        def _apply_filter(_e=None):
            q = (var_q.get() or "").strip().lower()
            b = var_big.get(); s = var_sub.get()
            # Clear existing rows before rebuilding the list
            for iid in tree.get_children():
                tree.delete(iid)
            # Guard against duplicate iids within the current dataset
            seen_iids: set[str] = set()
            for g in goods:
                name = str(g.get("name", ""))
                big = str(g.get("big_category", ""))
                sub = str(g.get("sub_category", ""))
                if b not in ("全部", big):
                    continue
                if s not in ("全部", sub):
                    continue
                if q and (q not in name.lower() and q not in str(g.get("search_name", "")).lower()):
                    continue
                iid = str(g.get("id", name))
                if iid in seen_iids or tree.exists(iid):
                    # Skip duplicates to avoid TclError: Item ... already exists
                    continue
                seen_iids.add(iid)
                tree.insert("", tk.END, iid=iid, values=(name, big, sub))
        ent.bind("<KeyRelease>", _apply_filter)
        cmb_big.bind("<<ComboboxSelected>>", _apply_filter)
        cmb_sub.bind("<<ComboboxSelected>>", _apply_filter)
        _apply_filter()

        def _ok():
            sel = tree.selection()
            if not sel:
                top.destroy(); return
            iid = sel[0]
            item = next((g for g in goods if str(g.get("id", "")) == iid or str(g.get("name", "")) == iid), None)
            if item is None:
                top.destroy(); return
            try:
                on_pick(item)
            finally:
                top.destroy()
        btns = ttk.Frame(top)
        btns.pack(fill=tk.X, padx=8, pady=6)
        ttk.Button(btns, text="确定", command=_ok).pack(side=tk.RIGHT)
        ttk.Button(btns, text="取消", command=top.destroy).pack(side=tk.RIGHT, padx=(0,6))
        # Double-click select
        def _on_dbl(_e=None):
            _ok()
        tree.bind("<Double-1>", _on_dbl)

    # ---------- Step delay config module ----------
    def _build_step_delay_panel(self, parent) -> None:
        # Remove previous panel if any
        old = getattr(self, "_step_panel", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        panel = ttk.LabelFrame(parent, text="高级配置")
        panel.pack(fill=tk.X, padx=8, pady=8)
        self._step_panel = panel
        inner = ttk.Frame(panel)
        inner.pack(fill=tk.X, padx=8, pady=6)

        # Use a single global delay value, default 0.01s; save in real time
        delays = dict(self.tasks_data.get("step_delays", {}) or {})
        if not delays or not isinstance(delays, dict):
            delays = {"default": 0.01}
        cur_val = 0.01
        try:
            cur_val = float(delays.get("default", 0.01) or 0.01)
        except Exception:
            cur_val = 0.01

        ttk.Label(inner, text="延时(秒)", width=14).grid(row=0, column=0, sticky="w")
        var_delay = tk.DoubleVar(value=cur_val)
        sp = ttk.Spinbox(inner, from_=0.0, to=1.0, increment=0.001, width=10, textvariable=var_delay)
        sp.grid(row=0, column=1, sticky="w")

        # Restart policy: restart game every N minutes (default 60)
        ttk.Label(inner, text="重启周期(分钟)", width=14).grid(row=1, column=0, sticky="w", pady=(6,0))
        try:
            cur_restart = int(self.tasks_data.get("restart_every_min", 60) or 60)
        except Exception:
            cur_restart = 60
        var_restart = tk.IntVar(value=cur_restart)
        sp2 = ttk.Spinbox(inner, from_=5, to=600, increment=5, width=10, textvariable=var_restart)
        sp2.grid(row=1, column=1, sticky="w", pady=(6,0))

        def _apply_delay_from_widget() -> None:
            try:
                val = float(var_delay.get())
            except Exception:
                return
            # Clamp to a reasonable range [0.0, 1.0]
            try:
                if val < 0.0:
                    val = 0.0
                if val > 1.0:
                    val = 1.0
            except Exception:
                pass
            self.tasks_data.setdefault("step_delays", {})["default"] = float(val)
            self._save_tasks_data()

        def _apply_restart_from_widget() -> None:
            try:
                val = int(var_restart.get())
            except Exception:
                return
            if val <= 0:
                val = 60
            self.tasks_data["restart_every_min"] = int(val)
            self._save_tasks_data()

        # Real-time save: on value change, focus out, and Enter
        try:
            var_delay.trace_add("write", lambda *_: _apply_delay_from_widget())
        except Exception:
            pass
        try:
            sp.bind("<FocusOut>", lambda _e=None: _apply_delay_from_widget())
            sp.bind("<Return>", lambda _e=None: _apply_delay_from_widget())
        except Exception:
            pass
        try:
            var_restart.trace_add("write", lambda *_: _apply_restart_from_widget())
        except Exception:
            pass
        try:
            sp2.bind("<FocusOut>", lambda _e=None: _apply_restart_from_widget())
            sp2.bind("<Return>", lambda _e=None: _apply_restart_from_widget())
        except Exception:
            pass

    # Small tooltip helper
    def _attach_tooltip(self, widget, text_or_fn) -> None:
        """为 widget 添加简易悬浮提示。

        - text_or_fn: 可为 str 或可调用对象（进入时动态求值返回 str）。
        """
        tip = None
        def _enter(_e=None):
            nonlocal tip
            if tip is not None:
                return
            tip = tk.Toplevel(widget)
            tip.overrideredirect(True)
            try:
                tip.attributes("-topmost", True)
            except Exception:
                pass
            x = widget.winfo_rootx() + 10
            y = widget.winfo_rooty() + widget.winfo_height() + 6
            tip.geometry(f"+{x}+{y}")
            try:
                txt = text_or_fn() if callable(text_or_fn) else str(text_or_fn)
            except Exception:
                txt = str(text_or_fn)
            lbl = ttk.Label(tip, text=txt, relief=tk.SOLID, borderwidth=1, background="#ffffe0")
            lbl.pack(ipadx=6, ipady=3)
        def _leave(_e=None):
            nonlocal tip
            if tip is not None:
                try:
                    tip.destroy()
                except Exception:
                    pass
            tip = None
        widget.bind("<Enter>", _enter)
        widget.bind("<Leave>", _leave)

    # Responsive flow layout: arrange widgets left-to-right and wrap by container width
    def _flow_layout(self, container, widgets: List[object], *, padx: int = 4, pady: int = 2) -> None:
        def _relayout(_e=None):
            try:
                W = int(container.winfo_width())
            except Exception:
                W = 0
            if W <= 1:
                try:
                    container.after(50, _relayout)
                except Exception:
                    pass
                return
            # Clear existing grid placements
            for w in widgets:
                try:
                    w.grid_forget()
                except Exception:
                    pass
            try:
                container.update_idletasks()
            except Exception:
                pass
            row = 0
            col = 0
            curw = 0
            for w in widgets:
                try:
                    ww = int(w.winfo_reqwidth())
                except Exception:
                    ww = 80
                need = ww if col == 0 else ww + padx
                if curw + need > W and col > 0:
                    row += 1
                    col = 0
                    curw = 0
                try:
                    w.grid(row=row, column=col, padx=(2, 2), pady=(pady, pady), sticky="w")
                except Exception:
                    pass
                curw += need
                col += 1
        try:
            container.bind("<Configure>", _relayout)
        except Exception:
            pass
        _relayout()

    def _build_task_card(self, parent, idx: int | None, it: Dict[str, Any], *, editable: bool, draft: bool) -> None:
        card = ttk.Frame(parent, relief=tk.SOLID, borderwidth=1)
        card.pack(fill=tk.X, padx=6, pady=6)
        card._is_draft = bool(draft)  # type: ignore

        # Variables
        # Use IntVar with explicit on/off to ensure check mark renders reliably
        var_enabled = tk.IntVar(value=1 if bool(it.get("enabled", True)) else 0)
        var_item_name = tk.StringVar(value=str(it.get("item_name", "")))
        # Link to goods.json entry via item_id (used to resolve search_name)
        var_item_id = tk.StringVar(value=str(it.get("item_id", "")))
        var_thr = tk.IntVar(value=int(it.get("price_threshold", 0) or 0))
        var_prem = tk.DoubleVar(value=float(it.get("price_premium_pct", 0) or 0))
        var_restock = tk.IntVar(value=int(it.get("restock_price", 0) or 0))
        # 新增：补货模式的价格浮动百分比（restock 专用溢价%）
        var_rprem = tk.DoubleVar(value=float(it.get("restock_premium_pct", 0) or 0))
        var_target = tk.IntVar(value=int(it.get("target_total", 0) or 0))
        # For round-robin mode, execution duration (minutes)
        try:
            _dur_def = int(it.get("duration_min", 10) or 10)
        except Exception:
            _dur_def = 10
        var_duration = tk.IntVar(value=max(1, _dur_def))
        # time_start/time_end as HH:MM only
        def _split_hhss(s: str) -> tuple[int, int]:
            s = str(s or "").strip()
            try:
                hh, ss = s.split(":")
                return max(0, min(23, int(hh))), max(0, min(59, int(ss)))
            except Exception:
                return 0, 0
        ts_raw = str(it.get("time_start", "")).strip()
        te_raw = str(it.get("time_end", "")).strip()
        h1, s1 = _split_hhss(ts_raw)
        h2, s2 = _split_hhss(te_raw)
        # Use StringVar to preserve leading zeros in HH/MM display; leave empty if original was empty
        var_h1 = tk.StringVar(value=(f"{h1:02d}" if ts_raw else "")); var_s1 = tk.StringVar(value=(f"{s1:02d}" if ts_raw else ""))
        var_h2 = tk.StringVar(value=(f"{h2:02d}" if te_raw else "")); var_s2 = tk.StringVar(value=(f"{s2:02d}" if te_raw else ""))

        # Row 0: enable checkbox occupies its own line
        row_enable = ttk.Frame(card)
        row_enable.pack(fill=tk.X, padx=8, pady=(6, 0))
        chk = ttk.Checkbutton(row_enable, text="启用", variable=var_enabled, onvalue=1, offvalue=0)
        try:
            chk.pack(side=tk.LEFT)
        except Exception:
            pass
        # 当非编辑模式下，勾选/取消“启用”时立刻持久化，避免状态在刷新或重启后丢失
        if not editable:
            def _on_toggle_enable_immediate() -> None:
                try:
                    it["enabled"] = bool(int(var_enabled.get()) != 0)
                    self._save_tasks_data()
                except Exception:
                    pass
            try:
                chk.configure(command=_on_toggle_enable_immediate)
            except Exception:
                pass
        # Show order if available
        try:
            order_num = (int(it.get("order", idx if idx is not None else 0)) if isinstance(it, dict) else (idx or 0)) + 1
        except Exception:
            order_num = (idx or 0) + 1
        ttk.Label(row_enable, text=f"顺序：{order_num}").pack(side=tk.LEFT, padx=(12,0))

        # Row 1: semantic sentence with inline inputs (responsive flow layout)
        row = ttk.Frame(card)
        row.pack(fill=tk.X, padx=8, pady=(2, 2))
        widgets: list[tk.Widget] = []
        widgets.append(ttk.Label(row, text="：购买物品："))
        lbl_name = ttk.Label(row, textvariable=var_item_name, width=18); widgets.append(lbl_name)
        btn_pick = ttk.Button(row, text="选择…", width=8, command=lambda: self._open_goods_picker(lambda g: (var_item_name.set(str(g.get('name',''))), var_item_id.set(str(g.get('id',''))))))
        widgets.append(btn_pick)
        widgets.append(ttk.Label(row, text="，小于"))
        ent_thr = ttk.Entry(row, textvariable=var_thr, width=8); widgets.append(ent_thr)
        lbl_fast = ttk.Label(row, text="的时候进行快速购买"); widgets.append(lbl_fast)
        self._attach_tooltip(lbl_fast, "价格<=阈值时直接购买（默认数量，不调数量）")
        widgets.append(ttk.Label(row, text="，允许价格浮动"))
        ent_prem = ttk.Entry(row, textvariable=var_prem, width=5); widgets.append(ent_prem)
        widgets.append(ttk.Label(row, text="% ，小于"))
        ent_rest = ttk.Entry(row, textvariable=var_restock, width=8); widgets.append(ent_rest)
        widgets.append(ttk.Label(row, text="的时候启用补货模式（自动点击Max买满），允许补货价浮动"))
        ent_rprem = ttk.Entry(row, textvariable=var_rprem, width=5); widgets.append(ent_rprem)
        widgets.append(ttk.Label(row, text="% ，"))
        widgets.append(ttk.Label(row, text="一共购买"))
        ent_target = ttk.Entry(row, textvariable=var_target, width=8); widgets.append(ent_target)
        widgets.append(ttk.Label(row, text="个，"))

        # Mode-specific fields
        mode = str(self.tasks_data.get("task_mode", "time"))
        ent_dur = None
        sp_h1 = sp_s1 = sp_h2 = sp_s2 = None
        if mode == "round":
            widgets.append(ttk.Label(row, text="执行时长(分钟)"))
            try:
                ent_dur = ttk.Spinbox(row, from_=1, to=1440, increment=1, width=6, textvariable=var_duration)
            except Exception:
                ent_dur = tk.Spinbox(row, from_=1, to=1440, increment=1, width=6, textvariable=var_duration)
            widgets.append(ent_dur)
        else:
            widgets.append(ttk.Label(row, text="在"))
            # Time start/end HH:MM via read-only comboboxes
            hours_vals = [f"{i:02d}" for i in range(24)]
            mins_vals = [f"{i:02d}" for i in range(60)]
            try:
                sp_h1 = ttk.Combobox(row, width=3, values=hours_vals, textvariable=var_h1, state="readonly")
            except Exception:
                sp_h1 = ttk.Entry(row, width=3, textvariable=var_h1)
            widgets.append(sp_h1)
            colon1 = ttk.Label(row, text=":"); widgets.append(colon1)
            try:
                sp_s1 = ttk.Combobox(row, width=3, values=mins_vals, textvariable=var_s1, state="readonly")
            except Exception:
                sp_s1 = ttk.Entry(row, width=3, textvariable=var_s1)
            widgets.append(sp_s1)
            widgets.append(ttk.Label(row, text="到"))
            try:
                sp_h2 = ttk.Combobox(row, width=3, values=hours_vals, textvariable=var_h2, state="readonly")
            except Exception:
                sp_h2 = ttk.Entry(row, width=3, textvariable=var_h2)
            widgets.append(sp_h2)
            colon2 = ttk.Label(row, text=":"); widgets.append(colon2)
            try:
                sp_s2 = ttk.Combobox(row, width=3, values=mins_vals, textvariable=var_s2, state="readonly")
            except Exception:
                sp_s2 = ttk.Entry(row, width=3, textvariable=var_s2)
            widgets.append(sp_s2)
            widgets.append(ttk.Label(row, text="启动（时间）"))
        # 价格浮动预览（悬浮提示）：展示阈值/补货价在溢价后对应的上限值
        def _fmt(n: int) -> str:
            try:
                return f"{int(n):,}"
            except Exception:
                return str(n)
        def _preview_text() -> str:
            try:
                thr = int(var_thr.get() or 0)
            except Exception:
                thr = 0
            try:
                prem = float(var_prem.get() or 0.0)
            except Exception:
                prem = 0.0
            try:
                rs = int(var_restock.get() or 0)
            except Exception:
                rs = 0
            try:
                rp = float(var_rprem.get() or 0.0)
            except Exception:
                rp = 0.0
            lim_n = thr + int(round(thr * max(0.0, prem) / 100.0)) if thr > 0 else 0
            lim_r = rs + int(round(rs * max(0.0, rp) / 100.0)) if rs > 0 else 0
            parts: list[str] = []
            if thr > 0:
                parts.append(f"普通：阈值 {_fmt(thr)} → 上限 {_fmt(lim_n)} (+{int(prem)}%)")
            if rs > 0:
                parts.append(f"补货：补货价 {_fmt(rs)} → 上限 {_fmt(lim_r)} (+{int(rp)}%)")
            return "\n".join(parts) if parts else "未设置阈值/补货价"
        try:
            self._attach_tooltip(ent_thr, _preview_text)
            self._attach_tooltip(ent_prem, _preview_text)
            self._attach_tooltip(ent_rest, _preview_text)
            self._attach_tooltip(ent_rprem, _preview_text)
        except Exception:
            pass

        # Apply responsive flow layout
        self._flow_layout(row, widgets, padx=4, pady=2)

        # 进度行：展示 purchased/target，并提供清空按钮
        # 对草稿（idx 为 None）不显示
        if idx is not None:
            try:
                row_prog = ttk.Frame(card)
                row_prog.pack(fill=tk.X, padx=8, pady=(0, 2))
                # 使用变量以便目标变更时可更新显示
                try:
                    cur_pur = int(it.get("purchased", 0) or 0)
                except Exception:
                    cur_pur = 0
                def _fmt_prog() -> str:
                    try:
                        return f"进度：{cur_pur}/{int(var_target.get() or 0)}"
                    except Exception:
                        return f"进度：{cur_pur}/0"
                var_prog = tk.StringVar(value=_fmt_prog())
                try:
                    var_target.trace_add("write", lambda *_: var_prog.set(_fmt_prog()))
                except Exception:
                    pass
                ttk.Label(row_prog, textvariable=var_prog).pack(side=tk.LEFT)
                # 购买历史入口（在进度旁）
                def _open_hist():
                    try:
                        self._open_purchase_history_for_item(str(var_item_id.get() or ""), str(var_item_name.get() or ""))
                    except Exception:
                        pass
                ttk.Button(row_prog, text="购买记录", width=10, command=_open_hist).pack(side=tk.RIGHT, padx=(0, 4))
                def _clear_progress() -> None:
                    try:
                        items = self.tasks_data.get("tasks", [])
                        if 0 <= int(idx) < len(items):
                            items[int(idx)]["purchased"] = 0
                            self._save_tasks_data()
                            # 同时清空该物品的购买记录（需求变更：清空进度=清空历史）
                            try:
                                from history_store import clear_purchase_history  # type: ignore
                                _ = clear_purchase_history(str(var_item_id.get() or ""))
                            except Exception:
                                pass
                            # 更新本地显示并重渲染以同步
                            var_prog.set(f"进度：0/{int(var_target.get() or 0)}")
                            self._render_task_cards()
                    except Exception:
                        pass
                btn_clear = ttk.Button(row_prog, text="清空进度", width=10, command=_clear_progress)
                btn_clear.pack(side=tk.RIGHT)
            except Exception:
                pass

        # Buttons
        btns = ttk.Frame(card)
        btns.pack(fill=tk.X, padx=8, pady=(0, 8))

        def _save():
            # Validate minimal fields
            name = (var_item_name.get() or "").strip()
            if not name:
                messagebox.showwarning("保存", "请先选择‘购买物品’。")
                return
            # Compose record (time fields handled per mode below)
            rec: Dict[str, Any] = {
                "enabled": bool(int(var_enabled.get()) != 0),
                "item_name": name,
                "item_id": (var_item_id.get() or ""),
                "price_threshold": int(var_thr.get() or 0),
                "price_premium_pct": float(var_prem.get() or 0),
                "restock_price": int(var_restock.get() or 0),
                # 新增：补货模式的价格浮动百分比
                "restock_premium_pct": float(var_rprem.get() or 0),
                "target_total": int(var_target.get() or 0),
                "duration_min": int(var_duration.get() or 10),
            }
            if not rec["item_id"]:
                messagebox.showwarning("保存", "必须通过‘选择…’绑定 goods.json 的物品（缺少 item_id）。")
                return
            # Validation depends on task mode
            mode_now = str(self.tasks_data.get("task_mode", "time"))
            if mode_now == "time":
                # Build and validate HH:MM inputs; both required
                h1s = (var_h1.get() or "").strip(); m1s = (var_s1.get() or "").strip()
                h2s = (var_h2.get() or "").strip(); m2s = (var_s2.get() or "").strip()
                def _mk_hhmm(hs: str, ms: str) -> str | None:
                    try:
                        if hs == "" or ms == "":
                            return None
                        hh = int(hs); mm = int(ms)
                        if 0 <= hh <= 23 and 0 <= mm <= 59:
                            return f"{hh:02d}:{mm:02d}"
                    except Exception:
                        return None
                    return None
                ts = _mk_hhmm(h1s, m1s)
                te = _mk_hhmm(h2s, m2s)
                if not ts or not te:
                    messagebox.showwarning("保存", "按时间区间执行：请设置开始时间与结束时间（小时:分钟）。")
                    return
                if ts == te:
                    messagebox.showwarning("保存", "按时间区间执行：开始时间与结束时间不能相同。")
                    return
                # Parse HH:MM to minutes [0, 1440)
                def _to_min(hhmm: str) -> int | None:
                    try:
                        hh, mm = hhmm.split(":")
                        h = int(hh); m = int(mm)
                        if 0 <= h <= 23 and 0 <= m <= 59:
                            return h*60 + m
                    except Exception:
                        return None
                    return None
                new_s = _to_min(ts); new_e = _to_min(te)
                if new_s is None or new_e is None:
                    messagebox.showwarning("保存", "时间格式无效，请使用 HH:MM。")
                    return
                # Represent possibly-wrapping interval as 1 or 2 non-wrapping segments (half-open)
                def _segments(s: int, e: int) -> list[tuple[int, int]]:
                    if s < e:
                        return [(s, e)]
                    else:
                        # Wrap across midnight: [s, 1440) U [0, e)
                        return [(s, 1440), (0, e)]
                def _overlap(a: tuple[int, int], b: tuple[int, int]) -> bool:
                    a1, a2 = a; b1, b2 = b
                    # Half-open intervals: [x1, x2) overlaps if max(starts) < min(ends)
                    return max(a1, b1) < min(a2, b2)
                new_segs = _segments(new_s, new_e)
                # Disallow duplicate or overlapping time windows (including cross-midnight)
                items_all = self.tasks_data.setdefault("tasks", [])
                for j, other in enumerate(items_all):
                    if idx is not None and j == idx:
                        continue
                    try:
                        o_ts = str(other.get("time_start", "")).strip()
                        o_te = str(other.get("time_end", "")).strip()
                        if not o_ts or not o_te:
                            continue
                        # Duplicate exact window
                        if o_ts == ts and o_te == te:
                            messagebox.showwarning("保存", "存在相同的时间区间任务，请调整后再保存。")
                            return
                        os = _to_min(o_ts); oe = _to_min(o_te)
                        if os is None or oe is None:
                            continue
                        other_segs = _segments(os, oe)
                        if any(_overlap(a, b) for a in new_segs for b in other_segs):
                            try:
                                messagebox.showwarning(
                                    "保存",
                                    f"时间区间不能重叠：与已有任务 [{o_ts} - {o_te}] 存在重叠。请调整后再保存。",
                                )
                            except Exception:
                                pass
                            return
                    except Exception:
                        pass
                rec["time_start"] = ts
                rec["time_end"] = te
            else:
                # Round-robin: require positive duration
                try:
                    dur = int(rec.get("duration_min", 0))
                except Exception:
                    dur = 0
                if dur <= 0:
                    messagebox.showwarning("保存", "轮流执行：请设置大于 0 的执行时长(分钟)。")
                    return
                # Preserve existing time window values (not used in round mode)
                try:
                    rec["time_start"] = str(it.get("time_start", ""))
                    rec["time_end"] = str(it.get("time_end", ""))
                except Exception:
                    pass
            # If editing existing
            items = self.tasks_data.setdefault("tasks", [])
            if idx is not None and 0 <= idx < len(items):
                # keep existing id/purchased if present
                rec["id"] = items[idx].get("id") or str(uuid.uuid4())
                rec["purchased"] = int(items[idx].get("purchased", 0))
                rec["order"] = int(items[idx].get("order", idx))
                items[idx] = rec
            else:
                rec["id"] = str(uuid.uuid4())
                rec["order"] = len(items)
                items.append(rec)
            # Normalize order fields to match list order
            for k, obj in enumerate(items):
                try:
                    obj["order"] = k
                except Exception:
                    pass
            self._save_tasks_data()
            self._task_draft_alive = False
            try:
                self.btn_add_task.configure(state=tk.NORMAL)
            except Exception:
                pass
            # If we were editing an existing item, exit editing mode
            try:
                self._editing_task_index = None
            except Exception:
                pass
            self._render_task_cards()

        def _edit():
            # Prevent editing while a draft card exists
            if self._task_draft_alive:
                try:
                    messagebox.showwarning("编辑", "请先保存或取消‘新增任务’卡片后再编辑其他任务。")
                except Exception:
                    pass
                return
            # If another item is currently being edited, confirm switching
            if (self._editing_task_index is not None) and (self._editing_task_index != idx):
                try:
                    if not messagebox.askokcancel("编辑", "已有任务在编辑中，切换将丢弃未保存更改，是否继续？"):
                        return
                except Exception:
                    pass
            # Switch this card into editing mode with Save/Cancel
            try:
                self._editing_task_index = idx
            except Exception:
                self._editing_task_index = None
            self._render_task_cards()

        def _cancel():
            if draft and (idx is None):
                # Remove the draft card
                try:
                    card.destroy()
                except Exception:
                    pass
                self._task_draft_alive = False
                try:
                    self.btn_add_task.configure(state=tk.NORMAL)
                except Exception:
                    pass
            else:
                # If cancelling editing for an existing item, exit editing mode
                try:
                    self._editing_task_index = None
                except Exception:
                    pass
                self._render_task_cards()

        def _delete():
            if idx is None:
                _cancel(); return
            items = self.tasks_data.get("tasks", [])
            if not (0 <= idx < len(items)):
                return
            if not messagebox.askokcancel("删除", f"确定删除任务 [{items[idx].get('item_name','')}]？"):
                return
            # Adjust current editing index if needed
            try:
                if self._editing_task_index is not None:
                    if self._editing_task_index == idx:
                        self._editing_task_index = None
                    elif idx < self._editing_task_index:
                        self._editing_task_index -= 1
            except Exception:
                pass
            del items[idx]
            self._save_tasks_data()
            self._render_task_cards()

        # Buttons depending on mode
        if editable:
            ttk.Button(btns, text="保存", command=_save).pack(side=tk.RIGHT)
            ttk.Button(btns, text="取消", command=_cancel).pack(side=tk.RIGHT, padx=(0,6))
        else:
            ttk.Button(btns, text="编辑", command=_edit).pack(side=tk.RIGHT)
            ttk.Button(btns, text="删除", command=_delete).pack(side=tk.RIGHT, padx=(0,6))

        # Reorder controls (only for existing items when not editing)
        if (not draft) and (idx is not None) and (not editable):
            def _move_up():
                items = self.tasks_data.get("tasks", [])
                i = idx
                if not (0 <= i < len(items)):
                    return
                if i == 0:
                    return
                items[i-1], items[i] = items[i], items[i-1]
                for k, obj in enumerate(items):
                    if isinstance(obj, dict):
                        obj["order"] = k
                self._save_tasks_data()
                self._render_task_cards()
            def _move_down():
                items = self.tasks_data.get("tasks", [])
                i = idx
                if not (0 <= i < len(items)):
                    return
                if i >= len(items) - 1:
                    return
                items[i+1], items[i] = items[i], items[i+1]
                for k, obj in enumerate(items):
                    if isinstance(obj, dict):
                        obj["order"] = k
                self._save_tasks_data()
                self._render_task_cards()
            # Place on the left
            ttk.Button(btns, text="上移", command=_move_up).pack(side=tk.LEFT)
            ttk.Button(btns, text="下移", command=_move_down).pack(side=tk.LEFT, padx=(6,0))

        # Disable editing if not editable
        if not editable:
            # Disable appropriate fields depending on mode
            mode_now = str(self.tasks_data.get("task_mode", "time"))
            to_disable = [ent_thr, ent_prem, ent_rest, ent_target, btn_pick, ent_rprem]
            if mode_now == "round":
                if ent_dur is not None:
                    to_disable.append(ent_dur)
            else:
                for w_ in (sp_h1, sp_s1, sp_h2, sp_s2):
                    if w_ is not None:
                        to_disable.append(w_)
            for w in to_disable:
                try:
                    w.configure(state=tk.DISABLED)
                except Exception:
                    pass

        # Keep a strong reference to Tk variables to avoid GC issues (checkbox display)
        # 保持 Tk 变量引用，新增 var_rprem
        card._vars = (var_enabled, var_item_name, var_item_id, var_thr, var_prem, var_restock, var_rprem, var_target, var_h1, var_s1, var_h2, var_s2, var_duration)  # type: ignore

    def _hotkey_to_display(self, seq: str) -> str:
        s = str(seq or "").strip()
        if s.startswith("<") and s.endswith(">"):
            s = s[1:-1]
        s = s.replace("-", "+")
        parts = [p for p in s.split("+") if p]
        disp: list[str] = []
        for p in parts:
            lp = p.lower()
            if lp in ("control", "ctrl"):
                disp.append("Ctrl")
            elif lp == "alt":
                disp.append("Alt")
            elif lp == "shift":
                disp.append("Shift")
            else:
                if lp.startswith("f") and lp[1:].isdigit():
                    disp.append("F" + lp[1:])
                elif len(p) == 1:
                    disp.append(p.upper())
                else:
                    disp.append(p)
        return "+".join(disp) if disp else "Ctrl+Alt+T"

    def _normalize_tk_hotkey(self, seq: str) -> str:
        s = str(seq or "").strip()
        if not s:
            return "<Control-Alt-t>"
        # Already Tk-style
        if s.startswith("<") and s.endswith(">"):
            return s
        # Accept forms like Ctrl+Alt+T, ctrl-alt-t, F5, Alt+F5
        s = s.replace(" ", "").replace("-", "+")
        parts = [p for p in s.split("+") if p]
        mods = []
        key = None
        for p in parts:
            lp = p.lower()
            if lp in ("ctrl", "control"):
                if "Control" not in mods:
                    mods.append("Control")
            elif lp == "alt":
                if "Alt" not in mods:
                    mods.append("Alt")
            elif lp == "shift":
                if "Shift" not in mods:
                    mods.append("Shift")
            else:
                key = p
        if key is None:
            key = "t"
        lk = key
        # Function keys keep case (e.g., F5)
        if len(lk) == 1:
            lk = lk.lower()
        return "<" + "-".join(mods + [lk]) + ">"

    def _rebind_toggle_hotkey(self) -> None:
        # Unbind previous
        for seq in getattr(self, "_bound_toggle_hotkeys", []) or []:
            try:
                self.unbind_all(seq)
            except Exception:
                pass
        self._bound_toggle_hotkeys = []
        # Bind configured sequence (normalized) and a fallback default
        cfg_seq = self._normalize_tk_hotkey(self._get_toggle_hotkey())
        fall_seq = "<Control-Alt-t>"
        # 旧自动购买切换热键已移除，不再绑定全局热键
        self._bound_toggle_hotkeys = []

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
        # Wrap Tab1 in a scrollable container so content adapts to window height
        container = ttk.Frame(self.tab1)
        try:
            container.pack(fill=tk.BOTH, expand=True)
        except Exception:
            container.pack(fill=tk.BOTH)

        self.tab1_canvas = tk.Canvas(container, highlightthickness=0)
        vsb = ttk.Scrollbar(container, orient="vertical", command=self.tab1_canvas.yview)
        try:
            self.tab1_canvas.configure(yscrollcommand=vsb.set)
        except Exception:
            pass
        self.tab1_canvas.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        try:
            container.rowconfigure(0, weight=1)
            container.columnconfigure(0, weight=1)
        except Exception:
            pass

        # Inner frame acts as original parent for Tab1 widgets
        self.tab1_inner = ttk.Frame(self.tab1_canvas)
        self.tab1_window = self.tab1_canvas.create_window((0, 0), window=self.tab1_inner, anchor="nw")

        def _on_inner(_e=None):
            try:
                self.tab1_canvas.configure(scrollregion=self.tab1_canvas.bbox("all"))
            except Exception:
                pass

        def _on_canvas(_e=None):
            try:
                w = self.tab1_canvas.winfo_width()
                self.tab1_canvas.itemconfigure(self.tab1_window, width=w)
            except Exception:
                pass

        try:
            self.tab1_inner.bind("<Configure>", _on_inner)
            self.tab1_canvas.bind("<Configure>", _on_canvas)
        except Exception:
            pass
        # Enable mouse-wheel scroll for Tab1 content
        try:
            self._bind_mousewheel(self.tab1_inner, self.tab1_canvas)
        except Exception:
            pass

        # Use inner frame as the layout root for this tab
        outer = self.tab1_inner

        # Game launcher
        game_cfg = self.cfg.get("game", {}) if isinstance(self.cfg.get("game"), dict) else {}
        self.var_game_path = tk.StringVar(value=str(game_cfg.get("exe_path", "")))
        self.var_game_args = tk.StringVar(value=str(game_cfg.get("launch_args", "")))
        # New tolerant-launch variables
        try:
            self.var_game_launcher_to = tk.IntVar(value=int(game_cfg.get("launcher_timeout_sec", 60)))
        except Exception:
            self.var_game_launcher_to = tk.IntVar(value=60)
        try:
            self.var_game_launch_delay = tk.IntVar(value=int(game_cfg.get("launch_click_delay_sec", 20)))
        except Exception:
            self.var_game_launch_delay = tk.IntVar(value=20)
        try:
            self.var_game_timeout = tk.IntVar(value=int(game_cfg.get("startup_timeout_sec", 180)))
        except Exception:
            self.var_game_timeout = tk.IntVar(value=180)
        box_game = ttk.LabelFrame(outer, text="游戏启动")
        box_game.pack(fill=tk.X, padx=8, pady=(8, 0))
        ttk.Label(box_game, text="启动路径").grid(row=0, column=0, sticky="e", padx=4, pady=6)
        ent_game = ttk.Entry(box_game, textvariable=self.var_game_path, width=64)
        ent_game.grid(row=0, column=1, sticky="w", padx=4)
        def _pick_game():
            path = filedialog.askopenfilename(title="选择游戏可执行文件", filetypes=[("Executable", ".exe .bat .cmd"), ("All", "*.*")])
            if path:
                self.var_game_path.set(path)
        ttk.Button(box_game, text="选择…", command=_pick_game).grid(row=0, column=2, padx=6)
        self.lab_game_status = ttk.Label(box_game, text="")
        self.lab_game_status.grid(row=0, column=3, sticky="w", padx=6)
        def _upd_game_status(*_):
            p = (self.var_game_path.get() or "").strip()
            if not p:
                self.lab_game_status.configure(text="未设置")
            elif os.path.exists(p):
                self.lab_game_status.configure(text="已设置")
            else:
                self.lab_game_status.configure(text="缺失")
            self._schedule_autosave()
        try:
            self.var_game_path.trace_add("write", _upd_game_status)
        except Exception:
            pass
        _upd_game_status()
        # Launch args
        ttk.Label(box_game, text="启动参数").grid(row=1, column=0, sticky="e", padx=4, pady=(0,6))
        ent_args = ttk.Entry(box_game, textvariable=self.var_game_args, width=64)
        ent_args.grid(row=1, column=1, sticky="we", padx=4, pady=(0,6))
        try:
            self.var_game_args.trace_add("write", lambda *_: self._schedule_autosave())
        except Exception:
            pass
        # Horizontal layout row for 3 timing fields
        frm_timing = ttk.Frame(box_game)
        frm_timing.grid(row=2, column=0, columnspan=4, sticky="we", padx=2, pady=(0, 6))
        # 启动检测超时(秒)
        ttk.Label(frm_timing, text="启动检测超时(秒)").grid(row=0, column=0, sticky="e", padx=(0,4))
        try:
            sp_to = ttk.Spinbox(frm_timing, from_=10, to=600, increment=10, textvariable=self.var_game_timeout, width=10)
        except Exception:
            sp_to = tk.Spinbox(frm_timing, from_=10, to=600, increment=10, textvariable=self.var_game_timeout, width=10)
        sp_to.grid(row=0, column=1, sticky="w", padx=(0,10))
        # 启动器检测超时(秒)
        ttk.Label(frm_timing, text="启动器检测超时(秒)").grid(row=0, column=2, sticky="e", padx=(0,4))
        try:
            sp_launcher = ttk.Spinbox(frm_timing, from_=10, to=600, increment=10, textvariable=self.var_game_launcher_to, width=10)
        except Exception:
            sp_launcher = tk.Spinbox(frm_timing, from_=10, to=600, increment=10, textvariable=self.var_game_launcher_to, width=10)
        sp_launcher.grid(row=0, column=3, sticky="w", padx=(0,10))
        # 启动点击延时(秒)
        ttk.Label(frm_timing, text="启动点击延时(秒)").grid(row=0, column=4, sticky="e", padx=(0,4))
        try:
            sp_delay = ttk.Spinbox(frm_timing, from_=0, to=120, increment=5, textvariable=self.var_game_launch_delay, width=10)
        except Exception:
            sp_delay = tk.Spinbox(frm_timing, from_=0, to=120, increment=5, textvariable=self.var_game_launch_delay, width=10)
        sp_delay.grid(row=0, column=5, sticky="w", padx=(0,0))
        # Autosave on value change
        try:
            self.var_game_timeout.trace_add("write", lambda *_: self._schedule_autosave())
            self.var_game_launcher_to.trace_add("write", lambda *_: self._schedule_autosave())
            self.var_game_launch_delay.trace_add("write", lambda *_: self._schedule_autosave())
        except Exception:
            pass
        for c in range(0, 6):
            try:
                frm_timing.columnconfigure(c, weight=0)
            except Exception:
                pass
        # Test buttons bar (full-width row to avoid being clipped/overlapped)
        btn_bar = ttk.Frame(box_game)
        # Place after template rows (which occupy rows 3..6). Use row=7 to avoid overlap.
        btn_bar.grid(row=7, column=0, columnspan=4, sticky="we", padx=4, pady=(6, 8))
        try:
            btn_bar.columnconfigure(0, weight=0)
            btn_bar.columnconfigure(1, weight=0)
            btn_bar.columnconfigure(2, weight=1)  # spacer for right-side additions if needed
        except Exception:
            pass
        self.btn_test_launch = ttk.Button(btn_bar, text="预览启动", command=self._test_game_launch)
        self.btn_test_launch.pack(side=tk.LEFT, padx=(0, 8))
        self.btn_test_exit = ttk.Button(btn_bar, text="预览退出", command=self._test_game_exit)
        self.btn_test_exit.pack(side=tk.LEFT)
        # Layout: make entry column flexible
        try:
            box_game.columnconfigure(1, weight=1)
        except Exception:
            pass

        # Template manager（新增二级分组：标识模板）
        box_tpl = ttk.LabelFrame(outer, text="模板管理")
        box_tpl.pack(fill=tk.X, padx=8, pady=8)
        # 保证下方 grid 子项（单个模板行容器）可横向拉伸
        try:
            box_tpl.columnconfigure(0, weight=1)
        except Exception:
            pass

        self.template_rows: Dict[str, TemplateRow] = {}

        # — 在游戏启动分组内渲染 启动按钮 模板行（从通用模板管理中移出） —
        # 相关回调用于模板测试/截图/预览，定义在下方；此处先记住容器
        _game_box_container = box_game

        def test_match(name: str, path: str, conf: float):
            """在屏幕上查找模板，找到则移动鼠标并点击一次；失败才提示。"""
            if not os.path.exists(path):
                messagebox.showwarning("测试识别", f"文件不存在: {path}")
                return
            try:
                import pyautogui  # type: ignore
                center = pyautogui.locateCenterOnScreen(path, confidence=conf)
                if center:
                    try:
                        pyautogui.moveTo(center.x, center.y, duration=0.1)
                        pyautogui.click(center.x, center.y)
                    except Exception:
                        pass
                    return  # 成功不弹窗
                # 未找到时，尝试返回矩形以辅助日志
                box = pyautogui.locateOnScreen(path, confidence=conf)
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return
            # 失败：提示
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
                    messagebox.showerror("选图片", f"失败: {e}")
                    return
                os.makedirs("images", exist_ok=True)
                slug = self._template_slug(row.name)
                path = os.path.join("images", f"{slug}.png")
                try:
                    img.save(path)
                except Exception as e:
                    messagebox.showerror("选图片", f"失败: {e}")
                    return
                row.var_path.set(path)
                # Autosave (debounced)
                self._schedule_autosave()
                # 截图成功后仅更新状态，不弹窗，不自动预览

            self._select_region(_after)

        # 在“游戏启动”分组放置【启动按钮】【设置按钮】【退出按钮】【退出确认按钮】模板配置行
        try:
            launch_data = (self.cfg.get("templates", {}) or {}).get("btn_launch", {})
            r_launch = TemplateRow(
                _game_box_container,
                "启动按钮",
                launch_data if isinstance(launch_data, dict) else {},
                on_test=test_match,
                on_capture=capture_into_row,
                on_preview=self._preview_image,
                on_change=self._schedule_autosave,
            )
            r_launch.grid(row=3, column=0, columnspan=4, sticky="we", padx=6, pady=(4, 6))
            self.template_rows["btn_launch"] = r_launch

            settings_data = (self.cfg.get("templates", {}) or {}).get("btn_settings", {})
            r_settings = TemplateRow(
                _game_box_container,
                "设置按钮",
                settings_data if isinstance(settings_data, dict) else {},
                on_test=test_match,
                on_capture=capture_into_row,
                on_preview=self._preview_image,
                on_change=self._schedule_autosave,
            )
            r_settings.grid(row=4, column=0, columnspan=4, sticky="we", padx=6, pady=(0, 6))
            self.template_rows["btn_settings"] = r_settings

            exit_data = (self.cfg.get("templates", {}) or {}).get("btn_exit", {})
            r_exit = TemplateRow(
                _game_box_container,
                "退出按钮",
                exit_data if isinstance(exit_data, dict) else {},
                on_test=test_match,
                on_capture=capture_into_row,
                on_preview=self._preview_image,
                on_change=self._schedule_autosave,
            )
            r_exit.grid(row=5, column=0, columnspan=4, sticky="we", padx=6, pady=(0, 6))
            self.template_rows["btn_exit"] = r_exit

            exit_cfm_data = (self.cfg.get("templates", {}) or {}).get("btn_exit_confirm", {})
            r_exit_cfm = TemplateRow(
                _game_box_container,
                "退出确认按钮",
                exit_cfm_data if isinstance(exit_cfm_data, dict) else {},
                on_test=test_match,
                on_capture=capture_into_row,
                on_preview=self._preview_image,
                on_change=self._schedule_autosave,
            )
            r_exit_cfm.grid(row=6, column=0, columnspan=4, sticky="we", padx=6, pady=(0, 6))
            self.template_rows["btn_exit_confirm"] = r_exit_cfm
        except Exception:
            pass

        # 二级分组 A：标识模板
        box_ident = ttk.LabelFrame(box_tpl, text="标识模板")
        try:
            box_ident.grid(row=0, column=0, sticky="we", padx=6, pady=(6, 4))
        except Exception:
            # Fallback to pack if grid not available (shouldn't happen here)
            box_ident.pack(fill=tk.X, padx=6, pady=(6, 4))
        # 标识模板分组内仅 1 列，需允许列宽拉伸到 100%
        try:
            box_ident.columnconfigure(0, weight=1)
        except Exception:
            pass

        # 渲染【首页标识模板】【市场标识模板】（底层键：home_indicator / market_indicator）
        try:
            _tpls = self.cfg.get("templates", {}) if isinstance(self.cfg.get("templates"), dict) else {}
        except Exception:
            _tpls = {}
        try:
            home_data = (_tpls.get("home_indicator", {}) if isinstance(_tpls, dict) else {}) or {}
        except Exception:
            home_data = {}
        try:
            market_data = (
                (_tpls.get("market_indicator", {}) if isinstance(_tpls, dict) else {})
            ) or {}
        except Exception:
            market_data = {}

        r_id_home = TemplateRow(
            box_ident,
            "首页标识模板",
            home_data,
            on_test=test_match,
            on_capture=capture_into_row,
            on_preview=self._preview_image,
            on_change=self._schedule_autosave,
        )
        r_id_home.grid(row=0, column=0, sticky="we", padx=6, pady=2)
        self.template_rows["home_indicator"] = r_id_home

        r_id_market = TemplateRow(
            box_ident,
            "市场标识模板",
            market_data,
            on_test=test_match,
            on_capture=capture_into_row,
            on_preview=self._preview_image,
            on_change=self._schedule_autosave,
        )
        r_id_market.grid(row=1, column=0, sticky="we", padx=6, pady=2)
        self.template_rows["market_indicator"] = r_id_market

        # 二级分组 B：其他模板（原通用列表）
        box_tpl_general = ttk.LabelFrame(box_tpl, text="其他模板")
        try:
            box_tpl_general.grid(row=1, column=0, sticky="we", padx=6, pady=(4, 6))
        except Exception:
            box_tpl_general.pack(fill=tk.X, padx=6, pady=(4, 6))
        # 同样确保单列容器可横向拉伸
        try:
            box_tpl_general.columnconfigure(0, weight=1)
        except Exception:
            pass

        # render rows (display Chinese name for known ASCII keys)
        rowc = 0
        DISPLAY_NAME = {
            "btn_launch": "启动按钮",
            "btn_settings": "设置按钮",
            "btn_exit": "退出按钮",
            "btn_exit_confirm": "退出确认按钮",
            "home_indicator": "首页标识模板",
            "market_indicator": "市场标识模板",
            "btn_home": "首页按钮",
            "btn_market": "市场按钮",
            "input_search": "市场搜索栏",
            "btn_search": "市场搜索按钮",
            "btn_buy": "购买按钮",
            "buy_ok": "购买成功",
            "buy_fail": "购买失败",
            "btn_max": "数量最大按钮",
            "qty_minus": "数量-",
            "qty_plus": "数量+",
            "btn_close": "商品关闭位置",
            "btn_refresh": "刷新按钮",
            "btn_back": "返回按钮",
        }
        # 从“其他模板”列表中排除：已在其他分组中出现的键
        _skip_in_general = {
            "btn_launch", "btn_settings", "btn_exit", "btn_exit_confirm",
            "qty_minus", "qty_plus",
            # 新增：标识模板在单独分组中维护
            "home_indicator", "market_indicator",
        }
        for key, data in self.cfg.get("templates", {}).items():
            if str(key) in _skip_in_general:
                # 已在“游戏启动”分组渲染
                continue
            disp = DISPLAY_NAME.get(key, key)
            r = TemplateRow(
                box_tpl_general,
                disp,
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
                        messagebox.showerror("选图片", f"失败: {e}")
                        return
                    if center:
                        try:
                            pyautogui.moveTo(center.x, center.y, duration=0.1)
                            pyautogui.click(center.x, center.y)
                        except Exception:
                            pass
                        return  # 成功不弹窗
                    # 失败提示
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
        # 新增：初始化区域模板/ROI 的放大倍率（OCR 引擎已统一为 Umi-OCR）
        try:
            _sc_def_roi = float(roi_cfg.get("scale", 1.0))
        except Exception:
            _sc_def_roi = 1.0
        self.var_roi_scale = tk.DoubleVar(value=_sc_def_roi)

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
        ttk.Button(box_roi, text="点击测试", command=lambda: test_match("价格区域-顶部模板", self.var_roi_top_tpl.get().strip(), float(self.var_roi_top_thr.get() or 0.55))).grid(row=0, column=5, padx=4)
        ttk.Button(box_roi, text="模板捕获", command=lambda: _capture_roi_into(self.var_roi_top_tpl, slug="buy_data_top", title="顶部模板")).grid(row=0, column=6, padx=4)
        ttk.Button(box_roi, text="模版预览", command=lambda: self._preview_image(self.var_roi_top_tpl.get().strip(), "预览 - 顶部模板")).grid(row=0, column=7, padx=4)

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
        ttk.Button(box_roi, text="点击测试", command=lambda: test_match("价格区域-底部模板", self.var_roi_btm_tpl.get().strip(), float(self.var_roi_btm_thr.get() or 0.55))).grid(row=1, column=5, padx=4)
        ttk.Button(box_roi, text="模板捕获", command=lambda: _capture_roi_into(self.var_roi_btm_tpl, slug="buy_data_btm", title="底部模板")).grid(row=1, column=6, padx=4)
        ttk.Button(box_roi, text="模版预览", command=lambda: self._preview_image(self.var_roi_btm_tpl.get().strip(), "预览 - 底部模板")).grid(row=1, column=7, padx=4)

        # 偏移/边距 + 预览（保持在下一行）
        ttk.Label(box_roi, text="顶部偏移").grid(row=2, column=0, padx=4, pady=4, sticky="e")
        ttk.Entry(box_roi, textvariable=self.var_roi_top_off, width=8).grid(row=2, column=1, sticky="w")
        ttk.Label(box_roi, text="底部偏移").grid(row=2, column=2, padx=4, pady=4, sticky="e")
        ttk.Entry(box_roi, textvariable=self.var_roi_btm_off, width=8).grid(row=2, column=3, sticky="w")
        ttk.Label(box_roi, text="左右边距").grid(row=2, column=4, padx=4, pady=4, sticky="e")
        ttk.Entry(box_roi, textvariable=self.var_roi_lr_pad, width=8).grid(row=2, column=5, sticky="w")
        ttk.Button(box_roi, text="预览", command=self._roi_preview_from_screen).grid(row=2, column=6, padx=6)

        # 行3：识别引擎 + 放大倍率（供基于 ROI 的读取流程使用）
        # 引擎选择已移除（统一使用 Umi-OCR 封装，不再显示 UI）
        ttk.Label(box_roi, text="放大倍率").grid(row=3, column=2, padx=8, pady=4, sticky="e")
        try:
            sp_roi_sc = ttk.Spinbox(box_roi, from_=0.6, to=2.5, increment=0.1, textvariable=self.var_roi_scale, width=6)
        except Exception:
            sp_roi_sc = tk.Spinbox(box_roi, from_=0.6, to=2.5, increment=0.1, textvariable=self.var_roi_scale, width=6)
        sp_roi_sc.grid(row=3, column=3, sticky="w")

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
        try:
            self.var_roi_scale.trace_add("write", lambda *_: self._schedule_autosave())
        except Exception:
            pass

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
                    messagebox.showerror("选图片", f"失败: {e}")
                    return
                os.makedirs("images", exist_ok=True)
                path = os.path.join("images", f"{slug}.png")
                try:
                    img.save(path)
                except Exception as e:
                    messagebox.showerror("选图片", f"失败: {e}")
                    return
                var.set(path)
                # 自动保存（去抖）
                self._schedule_autosave()
                # 截图后不自动预览

            self._select_region(_after)

        for i in range(0, 8):
            box_roi.columnconfigure(i, weight=0)

        # 数量输入区域（模板对）
        box_qty = ttk.LabelFrame(outer, text="数量输入区域（模板对）")
        box_qty.pack(fill=tk.X, padx=8, pady=8)
        try:
            minus_data = (self.cfg.get("templates", {}) or {}).get("qty_minus", {})
            r_minus = TemplateRow(
                box_qty,
                "数量-",
                minus_data if isinstance(minus_data, dict) else {},
                on_test=test_match,
                on_capture=capture_into_row,
                on_preview=self._preview_image,
                on_change=self._schedule_autosave,
            )
            r_minus.grid(row=0, column=0, columnspan=4, sticky="we", padx=6, pady=(4, 6))
            self.template_rows["qty_minus"] = r_minus

            plus_data = (self.cfg.get("templates", {}) or {}).get("qty_plus", {})
            r_plus = TemplateRow(
                box_qty,
                "数量+",
                plus_data if isinstance(plus_data, dict) else {},
                on_test=test_match,
                on_capture=capture_into_row,
                on_preview=self._preview_image,
                on_change=self._schedule_autosave,
            )
            r_plus.grid(row=1, column=0, columnspan=4, sticky="we", padx=6, pady=(0, 6))
            self.template_rows["qty_plus"] = r_plus

            # 操作按钮：点击输入区域 / 预览输入区域截图
            btns = ttk.Frame(box_qty)
            btns.grid(row=2, column=0, sticky="w", padx=6, pady=(2, 6))
            ttk.Button(btns, text="点击输入区域", command=self._qty_click_input_region).pack(side=tk.LEFT, padx=(0, 8))
            ttk.Button(btns, text="预览输入区域", command=self._qty_preview_input_region).pack(side=tk.LEFT)
        except Exception:
            pass

        # 平均单价区域设置（使用“购买按钮”宽度，上方固定距离 + 固定高度）
        box_avg = ttk.LabelFrame(outer, text="平均单价区域设置")
        box_avg.pack(fill=tk.X, padx=8, pady=8)

        avg_cfg = self.cfg.get("avg_price_area", {}) if isinstance(self.cfg.get("avg_price_area"), dict) else {}
        # Defaults
        self.var_avg_dist = tk.IntVar(value=int(avg_cfg.get("distance_from_buy_top", 5)))
        self.var_avg_height = tk.IntVar(value=int(avg_cfg.get("height", 45)))
        # OCR 引擎统一为 Umi-OCR，不再设置引擎变量
        try:
            _sc_def = float(avg_cfg.get("scale", 1.0))
        except Exception:
            _sc_def = 1.0
        self.var_avg_scale = tk.DoubleVar(value=_sc_def)

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

        # Row 1: 仅保留缩放（引擎选择 UI 已移除，统一使用 Umi-OCR）
        ttk.Label(box_avg, text="放大倍率").grid(row=1, column=2, padx=8, pady=4, sticky="e")
        try:
            sp_sc = ttk.Spinbox(box_avg, from_=0.6, to=2.5, increment=0.1, textvariable=self.var_avg_scale, width=6)
        except Exception:
            sp_sc = tk.Spinbox(box_avg, from_=0.6, to=2.5, increment=0.1, textvariable=self.var_avg_scale, width=6)
        sp_sc.grid(row=1, column=3, sticky="w")

        for i in range(0, 6):
            box_avg.columnconfigure(i, weight=0)

        # 自动保存（距离/高度）
        for v in [self.var_avg_dist, self.var_avg_height, self.var_avg_scale]:
            try:
                v.trace_add("write", lambda *_: self._schedule_autosave())
            except Exception:
                pass

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

        # 调试模式（已迁移至“多商品抢购模式”页面）

    def _debug_test_overlay(self) -> None:
        """显示一个 2 秒的半透明全屏蒙版，验证叠加行为。"""
        try:
            ov = float(self.var_debug_overlay.get() or 5.0)
        except Exception:
            ov = 5.0
        if ov < 0.5:
            ov = 0.5
        if ov > 15.0:
            ov = 15.0
        try:
            top = tk.Toplevel(self)
            W = int(self.winfo_screenwidth())
            H = int(self.winfo_screenheight())
            top.geometry(f"{W}x{H}+0+0")
            try:
                top.attributes("-alpha", 0.3)
            except Exception:
                pass
            try:
                top.attributes("-topmost", True)
            except Exception:
                pass
            top.overrideredirect(True)
            cv = tk.Canvas(top, bg="black", highlightthickness=0)
            cv.pack(fill=tk.BOTH, expand=True)
            try:
                f1 = tk_font(self, 14)
                if f1 is not None:
                    cv.create_text(W // 2, 40, text="调试蒙版预览（ROI/模板可视化开关位于此区）", fill="white", font=f1)
                else:
                    cv.create_text(W // 2, 40, text="调试蒙版预览（ROI/模板可视化开关位于此区）", fill="white")
                cx, cy = W // 2, H // 2
                cv.create_rectangle(cx - 220, cy - 120, cx + 220, cy + 120, outline="#2ea043", width=3)
                f2 = tk_font(self, 16)
                if f2 is not None:
                    cv.create_text(cx, cy, text="示例区域", fill="white", font=f2)
                else:
                    cv.create_text(cx, cy, text="示例区域", fill="white")
            except Exception:
                pass
            try:
                top.after(int(ov * 1000), top.destroy)
            except Exception:
                pass
        except Exception:
            pass


    def _save_and_sync(self, *, silent: bool = False) -> None:
        # Flush templates
        for key, row in self.template_rows.items():
            self.cfg.setdefault("templates", {}).setdefault(key, {})
            self.cfg["templates"][key]["path"] = row.get_path()
            self.cfg["templates"][key]["confidence"] = float(row.get_confidence())

        # Flush game launcher
        self.cfg.setdefault("game", {})
        self.cfg["game"]["exe_path"] = (self.var_game_path.get() or "").strip()
        self.cfg["game"]["launch_args"] = (self.var_game_args.get() or "").strip()
        try:
            ready_to = int(self.var_game_timeout.get() or 180)
        except Exception:
            ready_to = 180
        self.cfg["game"]["startup_timeout_sec"] = ready_to
        # New tolerant-launch fields
        try:
            self.cfg["game"]["launcher_timeout_sec"] = int(self.var_game_launcher_to.get() or 60)
        except Exception:
            self.cfg["game"]["launcher_timeout_sec"] = 60
        try:
            self.cfg["game"]["launch_click_delay_sec"] = int(self.var_game_launch_delay.get() or 20)
        except Exception:
            self.cfg["game"]["launch_click_delay_sec"] = 20
        # 移除 game_ready_timeout_sec（不再使用）
        try:
            self.cfg["game"].pop("game_ready_timeout_sec", None)
        except Exception:
            pass

        # Flush ROI config
        self.cfg.setdefault("price_roi", {})
        self.cfg["price_roi"]["top_template"] = self.var_roi_top_tpl.get().strip()
        self.cfg["price_roi"]["top_threshold"] = float(self.var_roi_top_thr.get() or 0.55)
        self.cfg["price_roi"]["bottom_template"] = self.var_roi_btm_tpl.get().strip()
        self.cfg["price_roi"]["bottom_threshold"] = float(self.var_roi_btm_thr.get() or 0.55)
        self.cfg["price_roi"]["top_offset"] = int(self.var_roi_top_off.get() or 0)
        self.cfg["price_roi"]["bottom_offset"] = int(self.var_roi_btm_off.get() or 0)
        self.cfg["price_roi"]["lr_pad"] = int(self.var_roi_lr_pad.get() or 0)
        # 放大倍率
        try:
            sc = float(self.var_roi_scale.get() or 1.0)
        except Exception:
            sc = 1.0
        if sc < 0.6:
            sc = 0.6
        if sc > 2.5:
            sc = 2.5
        self.cfg["price_roi"]["scale"] = float(sc)
        # 已弃用字段（ocr_engine）不再保存

        # Flush average price area (distance/height)
        try:
            self.cfg.setdefault("avg_price_area", {})
            self.cfg["avg_price_area"]["distance_from_buy_top"] = int(self.var_avg_dist.get() or 0)
            self.cfg["avg_price_area"]["height"] = int(self.var_avg_height.get() or 0)
            try:
                sc = float(self.var_avg_scale.get() or 1.0)
            except Exception:
                sc = 1.0
            if sc < 0.6:
                sc = 0.6
            if sc > 2.5:
                sc = 2.5
            self.cfg["avg_price_area"]["scale"] = float(sc)
            # 已弃用字段（ocr_engine）不再保存
        except Exception:
            pass

        # Flush debug config
        try:
            self.cfg.setdefault("debug", {})
            self.cfg["debug"]["enabled"] = bool(self.var_debug_enabled.get())
            try:
                ov = float(self.var_debug_overlay.get() or 5.0)
            except Exception:
                ov = 5.0
            if ov < 0.5:
                ov = 0.5
            if ov > 15.0:
                ov = 15.0
            self.cfg["debug"]["overlay_sec"] = float(ov)
            try:
                st = float(self.var_debug_step.get() or 0.0)
            except Exception:
                st = 0.0
            if st < 0.0:
                st = 0.0
            if st > 1.0:
                st = 1.0
            self.cfg["debug"]["step_sleep"] = float(st)
            # 新增：保存可视化截图与目录
            try:
                self.cfg["debug"]["save_overlay_images"] = bool(self.var_debug_save_imgs.get())
            except Exception:
                self.cfg["debug"]["save_overlay_images"] = False
            try:
                _dir = (self.var_debug_overlay_dir.get() or "").strip()
            except Exception:
                _dir = ""
            if not _dir:
                _dir = os.path.join("images", "debug", "可视化调试")
            self.cfg["debug"]["overlay_dir"] = _dir
        except Exception:
            pass

        save_config(self.cfg, "config.json")
        if not silent:
            messagebox.showinfo("配置", "已保存")

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
            messagebox.showerror("选图片", f"失败: {e}")
            return
        try:
            img_pil = pyautogui.screenshot()
        except Exception as e:
            messagebox.showerror("选图片", f"失败: {e}")
            return
        # PIL -> OpenCV BGR
        try:
            img_rgb = _np.array(img_pil)
            img_bgr = img_rgb[:, :, ::-1].copy()
        except Exception as e:
            messagebox.showerror("选图片", f"失败: {e}")
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

        # Save outputs and OCR preview（与“平均单价区域”一致的处理：放大+二值化+可选引擎）
        os.makedirs("images", exist_ok=True)
        crop_bgr = img_bgr[y_top:y_bot, x_left:x_right]
        # Scale from price_roi, clamp
        try:
            sc = float(self.var_roi_scale.get() or 1.0)
        except Exception:
            sc = 1.0
        if sc < 0.6:
            sc = 0.6
        if sc > 2.5:
            sc = 2.5
        try:
            if abs(sc - 1.0) > 1e-3:
                h0, w0 = crop_bgr.shape[:2]
                crop_bgr = _cv2.resize(crop_bgr, (max(1, int(w0 * sc)), max(1, int(h0 * sc))), interpolation=_cv2.INTER_CUBIC)
        except Exception:
            pass
        # Binarize (Otsu)
        try:
            grayc = _cv2.cvtColor(crop_bgr, _cv2.COLOR_BGR2GRAY)
            _thr, thb = _cv2.threshold(grayc, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU)
        except Exception:
            thb = None
        # Save preview crop (binary if available)
        crop_path = os.path.join("images", "_price_roi.png")
        try:
            if thb is not None:
                _cv2.imwrite(crop_path, thb)
            else:
                _cv2.imwrite(crop_path, crop_bgr)
        except Exception:
            pass

        # OCR 预览（统一使用 Umi-OCR via utils/ocr_utils）
        raw_text = ""
        cleaned = ""
        parsed_val = None
        elapsed_ms = -1.0
        try:
            import time as _time
            from PIL import Image as _Image  # type: ignore
            import numpy as _np  # type: ignore
            from utils.ocr_utils import recognize_text  # type: ignore
            # Compose PIL image from bin/crop
            img = None
            if thb is not None:
                try:
                    img = _Image.fromarray(thb)
                except Exception:
                    img = None
            if img is None:
                try:
                    arr = _np.array(crop_bgr)
                    img = _Image.fromarray(arr[:, :, ::-1])
                except Exception:
                    img = None
            # Call Umi-OCR
            ocfg = self.cfg.get("umi_ocr") or {}
            t0 = _time.perf_counter()
            boxes = recognize_text(
                img,
                base_url=str(ocfg.get("base_url", "http://127.0.0.1:1224")),
                timeout=float(ocfg.get("timeout_sec", 2.5) or 2.5),
                options=dict(ocfg.get("options", {}) or {}),
            ) if img is not None else []
            elapsed_ms = (_time.perf_counter() - t0) * 1000.0
            raw_text = "\n".join((b.text or "").strip() for b in boxes if (b.text or "").strip())
            up = (raw_text or "").upper()
            cleaned = "".join(ch for ch in up if ch in "0123456789KM")
            t = cleaned.strip().upper()
            mult = 1
            if t.endswith("M"):
                mult = 1_000_000
                t = t[:-1]
            elif t.endswith("K"):
                mult = 1_000
                t = t[:-1]
            digits = "".join(ch for ch in t if ch.isdigit())
            if digits:
                parsed_val = int(digits) * mult
        except Exception as e:
            raw_text = f"[OCR失败] {e}"
            cleaned = ""
            parsed_val = None

        try:
            self._preview_avg_price_window(crop_path, raw_text, cleaned, parsed_val, elapsed_ms,
                                           engine="umi", title="价格区域（模板ROI）")
        except Exception as e:
            messagebox.showerror("选图片", f"失败: {e}")

    

    # ---------- 数量输入区域（基于数量-/数量+ 模板的水平ROI） ----------
    def _qty__locate_boxes(self):
        """Locate minus/plus template boxes on the current screen.

        Returns (m_box, p_box) where each is a tuple (left, top, width, height), or (None, None) on failure.
        """
        try:
            r_minus = self.template_rows.get("qty_minus")
            r_plus = self.template_rows.get("qty_plus")
        except Exception:
            r_minus = None
            r_plus = None
        if r_minus is None or r_plus is None:
            messagebox.showwarning("数量输入区域", "未找到‘数量-’或‘数量+’模板行，请先在模板管理中配置。")
            return None, None
        p_m = (r_minus.get_path() or "").strip()
        p_p = (r_plus.get_path() or "").strip()
        if not p_m or not os.path.exists(p_m):
            messagebox.showwarning("数量输入区域", "‘数量-’模板路径为空或文件不存在。")
            return None, None
        if not p_p or not os.path.exists(p_p):
            messagebox.showwarning("数量输入区域", "‘数量+’模板路径为空或文件不存在。")
            return None, None
        try:
            import pyautogui  # type: ignore
            m_box = pyautogui.locateOnScreen(p_m, confidence=float(r_minus.get_confidence() or 0.85))
            p_box = pyautogui.locateOnScreen(p_p, confidence=float(r_plus.get_confidence() or 0.85))
        except Exception as e:
            messagebox.showerror("选图片", f"失败: {e}")
            return None, None
        if not m_box or not p_box:
            messagebox.showwarning("数量输入区域", "未匹配到‘数量-’或‘数量+’，请降低阈值或重截清晰模板。")
            return None, None
        # Convert to simple tuples
        try:
            m = (int(getattr(m_box, "left", 0)), int(getattr(m_box, "top", 0)), int(getattr(m_box, "width", 0)), int(getattr(m_box, "height", 0)))
            p = (int(getattr(p_box, "left", 0)), int(getattr(p_box, "top", 0)), int(getattr(p_box, "width", 0)), int(getattr(p_box, "height", 0)))
        except Exception:
            messagebox.showwarning("数量输入区域", "匹配到的框读取失败。")
            return None, None
        return m, p

    def _qty__compute_roi(self):
        """Compute the quantity input ROI between ‘数量-’ and ‘数量+’ boxes.

        Returns (x1, y1, x2, y2) or None on failure.
        """
        mp = self._qty__locate_boxes()
        if not isinstance(mp, tuple) or len(mp) != 2:
            return None
        m, p = mp
        if m is None or p is None:
            return None
        ml, mt, mw, mh = m
        pl, pt, pw, ph = p
        # Ensure minus is on the left of plus; swap if needed
        if ml > pl:
            ml, mt, mw, mh, pl, pt, pw, ph = pl, pt, pw, ph, ml, mt, mw, mh
        m_r = ml + max(1, mw)
        p_l = pl
        # Compute vertical band as intersection; fallback to union if tiny
        y1 = max(mt, pt)
        y2 = min(mt + max(1, mh), pt + max(1, ph))
        if y2 - y1 < 6:
            y1 = min(mt, pt)
            y2 = max(mt + max(1, mh), pt + max(1, ph))
        # Horizontal gap between minus-right and plus-left
        x1 = m_r
        x2 = p_l
        if x2 - x1 < 4:
            messagebox.showwarning("数量输入区域", "两个模板间距过小或位置异常，无法确定输入区域。")
            return None
        # Small inner padding to avoid borders
        pad = 2
        x1 += pad
        x2 -= pad
        if x2 <= x1:
            messagebox.showwarning("数量输入区域", "输入区域宽度不足。")
            return None
        return int(x1), int(y1), int(x2), int(y2)

    def _qty_preview_input_region(self) -> None:
        roi = self._qty__compute_roi()
        if not roi:
            return
        x1, y1, x2, y2 = roi
        w = max(1, x2 - x1)
        h = max(1, y2 - y1)
        try:
            import pyautogui  # type: ignore
            img = pyautogui.screenshot(region=(x1, y1, w, h))
        except Exception as e:
            messagebox.showerror("选图片", f"失败: {e}")
            return
        os.makedirs("images", exist_ok=True)
        path = os.path.join("images", "_qty_input_roi.png")
        try:
            img.save(path)
        except Exception as e:
            messagebox.showerror("选图片", f"失败: {e}")
            return
        try:
            self._append_log(f"[数量输入区域] 截取: ({x1},{y1},{x2},{y2}) -> {path}")
        except Exception:
            pass
        self._preview_image(path, "预览 - 数量输入区域")

    def _qty_click_input_region(self) -> None:
        roi = self._qty__compute_roi()
        if not roi:
            return
        x1, y1, x2, y2 = roi
        cx = int((x1 + x2) / 2)
        cy = int((y1 + y2) / 2)
        try:
            import pyautogui  # type: ignore
            pyautogui.moveTo(cx, cy, duration=0.1)
            pyautogui.click(cx, cy)
        except Exception as e:
            messagebox.showerror("选图片", f"失败: {e}")
            return
        try:
            self._append_log(f"[数量输入区域] 点击中心: ({cx},{cy})，区域=({x1},{y1},{x2},{y2})")
        except Exception:
            pass

    def _ocr_text_parse_km(self, bin_img, *, fallback_img=None):
        """对二值化或彩色图片做 OCR，并解析为整数（支持 K/M 后缀）。统一使用 Umi-OCR。"""
        raw_text = ""
        elapsed_ms = -1.0
        cleaned = ""
        parsed_val = None
        try:
            import time as _time
            import numpy as _np  # type: ignore
            from PIL import Image as _Image  # type: ignore
            from utils.ocr_utils import recognize_text  # type: ignore
            # 构造 PIL.Image
            img = None
            if bin_img is not None:
                try:
                    img = _Image.fromarray(bin_img)
                except Exception:
                    img = None
            if img is None and fallback_img is not None:
                try:
                    arr = _np.array(fallback_img)
                    img = _Image.fromarray(arr[:, :, ::-1])
                except Exception:
                    img = None
            ocfg = self.cfg.get("umi_ocr") or {}
            t0 = _time.perf_counter()
            boxes = recognize_text(
                img,
                base_url=str(ocfg.get("base_url", "http://127.0.0.1:1224")),
                timeout=float(ocfg.get("timeout_sec", 2.5) or 2.5),
                options=dict(ocfg.get("options", {}) or {}),
            ) if img is not None else []
            elapsed_ms = (_time.perf_counter() - t0) * 1000.0
            raw_text = "\n".join((b.text or "").strip() for b in boxes if (b.text or "").strip())
            up = (raw_text or "").upper()
            cleaned = "".join(ch for ch in up if ch in "0123456789KM")
            t = cleaned.strip().upper()
            mult = 1
            if t.endswith("M"):
                mult = 1_000_000
                t = t[:-1]
            elif t.endswith("K"):
                mult = 1_000
                t = t[:-1]
            digits = "".join(ch for ch in t if ch.isdigit())
            if digits:
                parsed_val = int(digits) * mult
        except Exception:
            pass
        return raw_text, cleaned, parsed_val, float(elapsed_ms)

    # 货币ROI预览窗口（已废弃）
    # def _preview_currency_window(self, crops, results) -> None:
    #     pass

    # ---------- Template helpers (shared by preview launch/exit) ----------
    def _get_tpl_path_conf(self, key: str) -> tuple[str, float]:
        """Return (path, confidence) for template key, with images/<key>.png fallback."""
        try:
            tpls = self.cfg.get("templates", {}) if isinstance(self.cfg.get("templates"), dict) else {}
            d = tpls.get(key, {}) if isinstance(tpls, dict) else {}
            p = str((d or {}).get("path", ""))
            if not p:
                p = os.path.join("images", f"{key}.png")
            try:
                conf = float((d or {}).get("confidence", 0.85))
            except Exception:
                conf = 0.85
            return p, conf
        except Exception:
            return os.path.join("images", f"{key}.png"), 0.85

    def _wait_template(self, key: str, timeout_sec: int) -> bool:
        """Wait until a template appears on screen within timeout (no click)."""
        path, conf = self._get_tpl_path_conf(key)
        try:
            import pyautogui  # type: ignore
        except Exception:
            return False
        if not os.path.exists(path):
            return False
        end = time.time() + max(0, int(timeout_sec))
        while time.time() < end:
            try:
                if pyautogui.locateOnScreen(path, confidence=float(conf)):
                    return True
            except Exception:
                pass
            time.sleep(1.0)
        return False

    def _wait_click_template(self, key: str, timeout_sec: int) -> bool:
        """Wait for a template and click its center once found."""
        path, conf = self._get_tpl_path_conf(key)
        try:
            import pyautogui  # type: ignore
        except Exception:
            return False
        if not os.path.exists(path):
            return False
        end = time.time() + max(0, int(timeout_sec))
        while time.time() < end:
            center = None
            try:
                center = pyautogui.locateCenterOnScreen(path, confidence=float(conf))
            except Exception:
                center = None
            if center is None:
                try:
                    box = pyautogui.locateOnScreen(path, confidence=float(conf))
                except Exception:
                    box = None
                if box:
                    try:
                        x = int(getattr(box, 'left', 0) + getattr(box, 'width', 0) // 2)
                        y = int(getattr(box, 'top', 0) + getattr(box, 'height', 0) // 2)
                        pyautogui.click(x, y)
                        return True
                    except Exception:
                        pass
            else:
                try:
                    pyautogui.click(int(getattr(center, 'x', 0)), int(getattr(center, 'y', 0)))
                    return True
                except Exception:
                    pass
            time.sleep(1.0)
        return False

    # ---------- Game launch test ----------
    def _test_game_launch(self) -> None:
        # Avoid concurrent test
        if getattr(self, "_test_launch_running", False):
            messagebox.showinfo("预览启动", "已有测试在进行中，请稍候…")
            return
        self._test_launch_running = True
        try:
            self.btn_test_launch.configure(state=tk.DISABLED)
            self.btn_test_exit.configure(state=tk.DISABLED)
        except Exception:
            pass
        # Save current config first
        try:
            self._save_and_sync(silent=True)
        except Exception:
            pass

        def _run():
            def _on_log(s: str) -> None:
                try:
                    self._append_log(s)
                except Exception:
                    pass
            res = run_launch_flow(self.cfg, on_log=_on_log)
            def _finish():
                try:
                    if res.ok:
                        messagebox.showinfo("预览启动", "已检测到首页标识，启动流程验证成功。")
                    else:
                        messagebox.showwarning("预览启动", f"启动失败：{res.error or res.code}")
                finally:
                    try:
                        self.btn_test_launch.configure(state=tk.NORMAL)
                        self.btn_test_exit.configure(state=tk.NORMAL)
                    except Exception:
                        pass
                    self._test_launch_running = False
            try:
                self.after(0, _finish)
            except Exception:
                _finish()

        try:
            t = threading.Thread(target=_run, name="test-launch", daemon=True)
            t.start()
        except Exception:
            self._test_launch_running = False
            try:
                self.btn_test_launch.configure(state=tk.NORMAL)
            except Exception:
                pass

    # ---------- Game exit test ----------
    def _test_game_exit(self) -> None:
        # Avoid concurrent test
        if getattr(self, "_test_launch_running", False) or getattr(self, "_test_exit_running", False):
            messagebox.showinfo("预览退出", "已有测试在进行中，请稍候…")
            return
        self._test_exit_running = True
        try:
            self.btn_test_exit.configure(state=tk.DISABLED)
            self.btn_test_launch.configure(state=tk.DISABLED)
        except Exception:
            pass
        # Save current config first
        try:
            self._save_and_sync(silent=True)
        except Exception:
            pass

        def _run():
            try:
                self._append_log("[预览退出] 尝试依次点击：设置按钮 → 退出按钮 → 退出确认按钮…")
            except Exception:
                pass
            # 1) Optional: click settings to open menu (up to 30s)
            s_path, _ = self._get_tpl_path_conf("btn_settings")
            if os.path.exists(s_path):
                if self._wait_click_template("btn_settings", 30):
                    try:
                        self._append_log("[预览退出] 已点击设置按钮。")
                    except Exception:
                        pass
                    time.sleep(1.0)

            # 2) Click exit (up to 60s)
            e_path, _ = self._get_tpl_path_conf("btn_exit")
            clicked_exit = False
            if os.path.exists(e_path):
                clicked_exit = self._wait_click_template("btn_exit", 60)
                if clicked_exit:
                    try:
                        self._append_log("[预览退出] 已点击退出按钮。")
                    except Exception:
                        pass
                    time.sleep(0.8)

            # 3) Optional: click exit confirm (up to 30s)
            c_path, _ = self._get_tpl_path_conf("btn_exit_confirm")
            clicked_confirm = False
            if os.path.exists(c_path):
                clicked_confirm = self._wait_click_template("btn_exit_confirm", 30)
                if clicked_confirm:
                    try:
                        self._append_log("[预览退出] 已点击退出确认按钮。")
                    except Exception:
                        pass

            ok = bool(clicked_exit or clicked_confirm)
            def _finish_done():
                try:
                    if ok:
                        messagebox.showinfo("预览退出", "已尝试点击退出/确认按钮，请在游戏内确认是否生效。")
                    else:
                        tips = []
                        if not os.path.exists(e_path):
                            tips.append("请在‘游戏启动’中设置‘退出按钮’模板")
                        if not os.path.exists(c_path):
                            tips.append("请在‘游戏启动’中设置‘退出确认按钮’模板")
                        if not os.path.exists(s_path):
                            tips.append("如需先打开菜单，请设置‘设置按钮’模板")
                        tip_text = ("；".join(tips)) if tips else "请检查模板/路径/超时设置"
                        messagebox.showwarning("预览退出", f"未检测到退出相关按钮。{tip_text}")
                finally:
                    try:
                        self.btn_test_exit.configure(state=tk.NORMAL)
                        self.btn_test_launch.configure(state=tk.NORMAL)
                    except Exception:
                        pass
                    self._test_exit_running = False
            try:
                self.after(0, _finish_done)
            except Exception:
                _finish_done()

        try:
            threading.Thread(target=_run, name="test-exit", daemon=True).start()
        except Exception:
            _run()

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
        # If already ASCII-friendly, use it directly
        try:
            import re
            if re.fullmatch(r"[A-Za-z0-9_]+", str(name)):
                return str(name)
        except Exception:
            pass
        # fallback: generated slug
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
        # Place window at a suitable position (near center of main window)
        try:
            self._place_modal(top, 940, 640)
        except Exception:
            pass

    def _preview_roi_simple(self, crop_path: str) -> None:
        # Simplified preview: left (crop image), right (OCR text + timing)
        if not os.path.exists(crop_path):
            messagebox.showwarning("预览", "截取图不存在。")
            return
        # Run OCR first to get timing/text via utils (Umi-OCR)
        try:
            import time as _time
            from PIL import Image as _Image  # type: ignore
            from utils.ocr_utils import recognize_text  # type: ignore
            imgp = _Image.open(crop_path)
            umi = (self.cfg.get("umi_ocr", {}) if hasattr(self, "cfg") else {}) or {}
            base_url = str(umi.get("base_url", "http://127.0.0.1:1224"))
            timeout = float(umi.get("timeout_sec", 2.5) or 2.5)
            options = dict(umi.get("options", {}) or {})
            t0 = _time.perf_counter()
            _boxes = recognize_text(imgp, base_url=base_url, timeout=timeout, options=options)
            ocr_ms = (_time.perf_counter() - t0) * 1000.0
            ocr_texts = [b.text.strip() for b in _boxes if (b.text or "").strip()]
            ocr_scores = [float(b.score) if getattr(b, "score", None) is not None else None for b in _boxes]
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
                f = tk_font(self, 12)
            except Exception:
                f = None
            try:
                if f is not None:
                    cv.create_text(10, 10, anchor=tk.NW, text=f"加载截取图失败: {e}", font=f)
                else:
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
        # Place window relative to main window
        try:
            self._place_modal(top, total_w, total_h)
        except Exception:
            pass
    # PaddleOCR path removed

    # PaddleOCR ensure removed

    # OcrLite path removed

    # OCR 直连函数已移除（统一使用 utils/ocr_utils）

    # Warm-up removed

    def _avg_price_roi_preview(self) -> None:
        try:
            import pyautogui  # type: ignore
            from PIL import Image  # type: ignore
        except Exception as e:
            messagebox.showerror("选图片", f"失败: {e}")
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
                messagebox.showerror("选图片", f"失败: {e}")
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
            messagebox.showerror("选图片", f"失败: {e}")
            return
        # Split ROI into two halves: top (平均价), bottom (合计)
        try:
            w, h = img.size
        except Exception:
            messagebox.showerror("预览", "ROI 尺寸无效")
            return
        if h < 2:
            messagebox.showwarning("预览", "ROI 高度过小，无法二分")
            return
        mid = h // 2
        img_top = img.crop((0, 0, w, mid))
        img_bot = img.crop((0, mid, w, h))

        # Apply scale
        try:
            sc = float(self.var_avg_scale.get() or 1.0)
        except Exception:
            sc = 1.0
        if sc < 0.6:
            sc = 0.6
        if sc > 2.5:
            sc = 2.5
        try:
            from PIL import Image as _Image  # type: ignore
            if abs(sc - 1.0) > 1e-3:
                img_top = img_top.resize((max(1, int(img_top.width * sc)), max(1, int(img_top.height * sc))), resample=getattr(_Image, 'LANCZOS', 1))
                img_bot = img_bot.resize((max(1, int(img_bot.width * sc)), max(1, int(img_bot.height * sc))), resample=getattr(_Image, 'LANCZOS', 1))
        except Exception:
            pass

        # Binarize both
        def _bin(pil_img):
            try:
                import cv2 as _cv2, numpy as _np  # type: ignore
                arr = _np.array(pil_img)
                bgr = _cv2.cvtColor(arr, _cv2.COLOR_RGB2BGR)
                gray = _cv2.cvtColor(bgr, _cv2.COLOR_BGR2GRAY)
                _thr, th = _cv2.threshold(gray, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU)
                from PIL import Image as _Image  # type: ignore
                return _Image.fromarray(th)
            except Exception:
                try:
                    return pil_img.convert("L").point(lambda p: 255 if p > 128 else 0)
                except Exception:
                    return pil_img

        bin_top = _bin(img_top)
        bin_bot = _bin(img_bot)

        # Save both images
        os.makedirs("images", exist_ok=True)
        path_top = os.path.join("images", "_avg_price_roi_top.png")
        path_bot = os.path.join("images", "_avg_price_roi_bottom.png")
        try:
            bin_top.save(path_top)
            bin_bot.save(path_bot)
        except Exception as e:
            messagebox.showerror("选图片", f"失败: {e}")
            return

        # OCR both halves
        import time as _time
        try:
        except Exception:
            pass
        def _ocr(pil_img):
            raw = ""; ms = -1.0
            t0 = _time.perf_counter()
            try:
                from utils.ocr_utils import recognize_text  # type: ignore
                ocfg = self.cfg.get("umi_ocr") or {}
                boxes = recognize_text(
                    pil_img,
                    base_url=str(ocfg.get("base_url", "http://127.0.0.1:1224")),
                    timeout=float(ocfg.get("timeout_sec", 2.5) or 2.5),
                    options=dict(ocfg.get("options", {}) or {}),
                ) if pil_img is not None else []
                raw = "\n".join((b.text or "").strip() for b in boxes if (b.text or "").strip())
            except Exception as _e:
                raw = f"[umi失败] {_e}"
            if ms < 0: ms = (_time.perf_counter() - t0) * 1000.0
            up = (raw or "").upper(); cleaned = "".join(ch for ch in up if ch in "0123456789KM")
            t = cleaned.strip().upper(); mult = 1
            if t.endswith("M"): mult = 1_000_000; t = t[:-1]
            elif t.endswith("K"): mult = 1_000; t = t[:-1]
            digits = "".join(ch for ch in t if ch.isdigit())
            parsed = int(digits) * mult if digits else None
            return raw, cleaned, parsed, ms

        raw_t, clean_t, parsed_t, ms_t = _ocr(bin_top)
        raw_b, clean_b, parsed_b, ms_b = _ocr(bin_bot)

        # Show dual preview window
        try:
            self._preview_avg_price_window_dual(path_top, raw_t, clean_t, parsed_t, ms_t,
                                                path_bot, raw_b, clean_b, parsed_b, ms_b,
                                                engine="umi")
        except Exception as e:
            messagebox.showerror("选图片", f"失败: {e}")

    def _preview_avg_price_window(self, crop_path: str, raw_text: str, cleaned: str, parsed_val, elapsed_ms: float, *, engine: str = "umi", title: str | None = None) -> None:
        if not os.path.exists(crop_path):
            messagebox.showwarning("预览", "ROI 截图不存在。")
            return
        top = tk.Toplevel(self)
        try:
            eng_map = {"umi": "Umi-OCR"}
            eng_name = eng_map.get(engine.lower(), engine)
            ttl_base = title if isinstance(title, str) and title else "平均单价区域"
            top.title(f"预览 - {ttl_base}（截图 + {eng_name}）")
        except Exception:
            pass
        top.transient(self)
        try:
            top.grab_set()
        except Exception:
            pass

    def _preview_avg_price_window_dual(self, path_top: str, raw_t: str, clean_t: str, parsed_t, ms_t: float,
                                        path_bot: str, raw_b: str, clean_b: str, parsed_b, ms_b: float,
                                        *, engine: str = "umi") -> None:
        for p in (path_top, path_bot):
            if not os.path.exists(p):
                messagebox.showwarning("预览", f"ROI 截图不存在: {p}")
                return
        from PIL import Image, ImageTk  # type: ignore
        win = tk.Toplevel(self)
        win.title(f"预览 - 平均单价区域（上下分割，{engine}）")
        try:
            win.geometry("960x720")
        except Exception:
            pass
        win.transient(self)
        try:
            win.grab_set()
        except Exception:
            pass

        def _section(parent, label, img_path, raw, clean, parsed, ms):
            frm = ttk.LabelFrame(parent, text=label)
            frm.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
            left = ttk.Frame(frm)
            left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            right = ttk.Frame(frm, width=320)
            right.pack(side=tk.RIGHT, fill=tk.Y)
            try:
                img = Image.open(img_path)
                maxw, maxh = 560, 260
                w, h = img.size
                scale = min(maxw / max(1, w), maxh / max(1, h), 1.0)
                disp = img.resize((max(1, int(w*scale)), max(1, int(h*scale))), Image.LANCZOS)
                tkimg = ImageTk.PhotoImage(disp)
                lbl = ttk.Label(left, image=tkimg)
                lbl.image = tkimg
                lbl.pack(padx=6, pady=6)
            except Exception as e:
                ttk.Label(left, text=f"加载图片失败: {e}").pack()
            # OCR details
            ttk.Label(right, text="OCR结果（原始 与 预览）").pack(anchor="w", padx=6, pady=(6,2))
            txt = tk.Text(right, height=8, wrap="word")
            txt.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0,6))
            try:
                txt.insert("1.0", f"引擎: {engine}\n耗时: {int(ms)}ms\n原始: {(raw or '').strip()}\n清洗: {clean}\n数值: {parsed if parsed is not None else '-'}")
            except Exception:
                txt.insert("1.0", "无法显示OCR详情")
            txt.configure(state=tk.DISABLED)

        _section(win, "上半（平均价）", path_top, raw_t, clean_t, parsed_t, ms_t)
        _section(win, "下半（合计价）", path_bot, raw_b, clean_b, parsed_b, ms_b)

        # Footer with close button
        footer = ttk.Frame(win)
        footer.pack(fill=tk.X, padx=8, pady=8)
        ttk.Button(footer, text="关闭", command=win.destroy).pack(side=tk.RIGHT)

    # ---------- Tab2 ----------
    def _build_tab2(self) -> None:
        outer = self.tab2
        main = ttk.Frame(outer)
        main.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Left: items list
        left = ttk.Frame(main)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 商品列表首列改为复选框风格（☑/☐），不再通过点击整列文本切换
        cols = ("name", "thr", "restock", "premium", "mode", "maxbtn", "target", "max", "defqty", "purchased")
        self.tree = ttk.Treeview(left, columns=cols, show="tree headings", height=10)
        self.tree.heading("#0", text="启用")
        self.tree.heading("name", text="商品")
        self.tree.heading("thr", text="阈值")
        self.tree.heading("restock", text="补货价")
        self.tree.heading("premium", text="溢价(%)")
        self.tree.heading("mode", text="价格模式")
        self.tree.heading("maxbtn", text="Max数量")
        self.tree.heading("target", text="目标")
        self.tree.heading("max", text="每单上限")
        self.tree.heading("defqty", text="默认数量")
        self.tree.heading("purchased", text="进度")
        self.tree.column("#0", width=46, anchor="center")
        self.tree.column("name", width=160)
        self.tree.column("thr", width=70, anchor="e")
        self.tree.column("restock", width=80, anchor="e")
        self.tree.column("premium", width=80, anchor="e")
        self.tree.column("mode", width=80, anchor="center")
        self.tree.column("maxbtn", width=80, anchor="e")
        self.tree.column("target", width=80, anchor="e")
        self.tree.column("max", width=90, anchor="e")
        self.tree.column("defqty", width=80, anchor="e")
        self.tree.column("purchased", width=100, anchor="e")
        self.tree.pack(fill=tk.BOTH, expand=True)

        # Selection change: no per-item progress UI to update
        # 仅在勾选框列（#0）点击时切换启用状态
        self.tree.bind("<Button-1>", self._tree_on_click, add=True)
        # Open editor modal on double-click
        self.tree.bind("<Double-1>", self._tree_on_double_click)
        # Context menu: right-click
        self._ctx_menu = tk.Menu(self, tearoff=0)
        self._ctx_menu.add_command(label="编辑…", command=lambda: self._open_item_modal(self._get_clicked_index()))
        self._ctx_menu.add_command(label="删除", command=self._delete_item)
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="历史价格…", command=lambda: self._open_price_history(self._get_clicked_index()))
        self._ctx_menu.add_command(label="购买记录…", command=lambda: self._open_purchase_history(self._get_clicked_index()))
        self._ctx_menu.add_separator()
        self._ctx_menu.add_command(label="启用/禁用", command=self._toggle_item_enable)
        self._ctx_menu.add_command(label="清空进度", command=lambda: self._reset_item_progress(confirm=True))
        self.tree.bind("<Button-3>", self._on_tree_right_click)

        # Bottom controls
        ctrl = ttk.Frame(outer)
        ctrl.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(ctrl, text="新增…", command=lambda: self._open_item_modal(None)).pack(side=tk.LEFT)
        # Unified Run button (Start/Stop toggled by state)
        self.btn_run = ttk.Button(ctrl, text="开始", command=self._toggle_run)
        self.btn_run.pack(side=tk.LEFT, padx=6)
        ttk.Button(ctrl, text="清空选中进度", command=lambda: self._reset_item_progress(confirm=True)).pack(side=tk.LEFT, padx=6)

        # Log
        logf = ttk.LabelFrame(outer, text="运行日志")
        logf.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        # 日志等级选择
        topbar = ttk.Frame(logf)
        topbar.pack(fill=tk.X, padx=6, pady=(6, 0))
        ttk.Label(topbar, text="日志等级").pack(side=tk.LEFT)
        self.run_log_level_var = tk.StringVar(value="info")
        run_level = ttk.Combobox(topbar, width=8, state="readonly", values=["debug", "info", "error"], textvariable=self.run_log_level_var)
        run_level.pack(side=tk.LEFT, padx=6)
        self.txt = tk.Text(logf, height=12, wrap="word")
        self.txt.pack(fill=tk.BOTH, expand=True)
        self.txt.configure(state=tk.DISABLED)

        # Load items from config
        self._load_items_from_cfg()

    # ---------- Tab3: OCR Lab（已移除） ----------

    # ---------- 执行日志（新逻辑） ----------
    def _build_tab_exec(self) -> None:
        outer = self.tab_exec
        frm = ttk.Frame(outer)
        frm.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Controls row
        ctrl = ttk.Frame(frm)
        ctrl.pack(fill=tk.X)
        self.btn_exec_start = ttk.Button(ctrl, text="开始", command=self._exec_start)
        self.btn_exec_start.pack(side=tk.LEFT)
        self.btn_exec_pause = ttk.Button(ctrl, text="暂停", command=self._exec_toggle_pause)
        self.btn_exec_pause.pack(side=tk.LEFT, padx=6)
        self.btn_exec_stop = ttk.Button(ctrl, text="终止", command=self._exec_stop)
        self.btn_exec_stop.pack(side=tk.LEFT)
        self.lab_exec_status = ttk.Label(ctrl, text="idle", foreground="#666")
        self.lab_exec_status.pack(side=tk.RIGHT)

        # Log area
        logf = ttk.LabelFrame(frm, text="执行日志")
        logf.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        # 日志等级选择（执行日志）
        topbar = ttk.Frame(logf)
        topbar.pack(fill=tk.X, padx=6, pady=(6, 0))
        ttk.Label(topbar, text="日志等级").pack(side=tk.LEFT)
        self.exec_log_level_var = tk.StringVar(value="info")
        exec_level = ttk.Combobox(topbar, width=8, state="readonly", values=["debug", "info", "error"], textvariable=self.exec_log_level_var)
        exec_level.pack(side=tk.LEFT, padx=6)
        try:
            exec_level.bind(
                "<<ComboboxSelected>>",
                lambda e: (getattr(self, "_runner", None) and self._runner.set_log_level(self.exec_log_level_var.get())),
            )
        except Exception:
            pass
        self.exec_txt = tk.Text(logf, height=18, wrap="word")
        self.exec_txt.pack(fill=tk.BOTH, expand=True)
        self.exec_txt.configure(state=tk.DISABLED)
        # Initialize buttons state
        self._update_exec_controls()

    def _append_exec_log(self, s: str) -> None:
        try:
            import threading as _th
            if _th.current_thread() is not _th.main_thread():
                self.after(0, self._append_exec_log, s)
                return
        except Exception:
            pass
        # 过滤：根据当前选择的日志等级
        try:
            lvl = self._parse_log_level(s)
            if self._level_value(lvl) < self._level_value(self.exec_log_level_var.get() if hasattr(self, 'exec_log_level_var') else 'info'):
                return
        except Exception:
            pass
        with self._exec_log_lock:
            try:
                self.exec_txt.configure(state=tk.NORMAL)
                self.exec_txt.insert(tk.END, s + "\n")
                self.exec_txt.see(tk.END)
            finally:
                self.exec_txt.configure(state=tk.DISABLED)

    def _exec_is_running(self) -> bool:
        r = getattr(self, "_runner", None)
        try:
            t = getattr(r, "_thread", None)
            return bool(r and t and t.is_alive())
        except Exception:
            return False

    def _update_exec_controls(self) -> None:
        running = self._exec_is_running()
        try:
            self.btn_exec_start.configure(state=(tk.DISABLED if running else tk.NORMAL))
            self.btn_exec_stop.configure(state=(tk.NORMAL if running else tk.DISABLED))
            # Pause button toggles between 暂停/继续
            paused = bool(getattr(self._runner, "_pause", None) and self._runner._pause.is_set()) if self._runner else False
            self.btn_exec_pause.configure(state=(tk.NORMAL if running else tk.DISABLED), text=("继续" if paused else "暂停"))
            # Status label
            self.lab_exec_status.configure(text=("running" if running else "idle"))
        except Exception:
            pass

    def _schedule_exec_state_poll(self) -> None:
        self._cancel_exec_state_poll()
        try:
            self._exec_state_after_id = self.after(500, self._on_exec_state_tick)
        except Exception:
            pass

    def _cancel_exec_state_poll(self) -> None:
        aid = getattr(self, "_exec_state_after_id", None)
        if aid:
            try:
                self.after_cancel(aid)
            except Exception:
                pass
        self._exec_state_after_id = None

    def _on_exec_state_tick(self) -> None:
        self._update_exec_controls()
        # keep polling while runner exists
        if self._runner is not None:
            try:
                self._exec_state_after_id = self.after(500, self._on_exec_state_tick)
            except Exception:
                pass

    def _exec_start(self) -> None:
        # Persist any in-memory task edits
        try:
            self._save_tasks_data()
        except Exception:
            pass
        # Reset log
        try:
            self.exec_txt.configure(state=tk.NORMAL)
            self.exec_txt.delete("1.0", tk.END)
            self.exec_txt.configure(state=tk.DISABLED)
        except Exception:
            pass
        # Instantiate runner with current tasks_data snapshot
        self._runner = TaskRunner(
            tasks_data=dict(self.tasks_data),
            cfg_path="config.json",
            goods_path="goods.json",
            on_log=self._append_exec_log,
            on_task_update=self._on_task_exec_update,
        )
        # 同步日志等级到运行器（用于运行器侧过滤）
        try:
            self._runner.set_log_level(self.exec_log_level_var.get())
        except Exception:
            pass
        self._append_exec_log("【%s】【全局】【-】：开始执行" % time.strftime("%H:%M:%S"))
        self._runner.start()
        self._update_exec_controls()
        self._schedule_exec_state_poll()

    def _exec_toggle_pause(self) -> None:
        r = getattr(self, "_runner", None)
        if not r:
            return
        try:
            if r._pause.is_set():
                r.resume()
            else:
                r.pause()
        except Exception:
            pass
        self._update_exec_controls()

    def _exec_stop(self) -> None:
        r = getattr(self, "_runner", None)
        if r:
            try:
                r.stop()
            except Exception:
                pass
        self._update_exec_controls()

    def _on_task_exec_update(self, idx: int, t: Dict[str, Any]) -> None:
        # Update in-memory tasks_data purchased and persist to buy_tasks.json
        try:
            items = self.tasks_data.get("tasks", []) or []
            if 0 <= idx < len(items):
                items[idx]["purchased"] = int(t.get("purchased", 0) or 0)
                # Persist light-weight
                self._save_tasks_data()
                # 若当前未在编辑/新增草稿，刷新卡片以实时更新进度显示
                try:
                    if (getattr(self, "_editing_task_index", None) is None) and (not bool(getattr(self, "_task_draft_alive", False))):
                        self.after(0, self._render_task_cards)
                except Exception:
                    pass
        except Exception:
            pass
        # Lab UI 移除
        return
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
        return pil_img, 0, 0, []

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
                    d ={}
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
                f = pil_font(12)
                if f is not None:
                    dr.text((x_r + 4, int((y_t + y_b) / 2) - 7), lbl, fill=textc, font=f)
                else:
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
        return

def _lab_pick_best_variant(self):
        return None, None

def _lab_detect_and_draw(self, pil_img, draw=True):
        return pil_img, 0, 0, []

        # OCR tokens
        psm_list = [6, 7, 11, 13]
        boxes = []  # (l,t,w,h,val,conf,psm)
        diag_lines = []
        for psm in psm_list:
            config = f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789,"
            try:
                data = {}
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
                from PIL import Image  # type: ignore
                def _ocr_numbers(pil_crop):
                    vals = []
                    for psm in (7, 6, 13):
                        cfg = f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789,"
                        try:
                            d ={}
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
        return

def _lab_save_annotated(self) -> None:
        return

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
                    data ={}
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
                        d ={}
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
                f = pil_font(14)
                if f is not None:
                    draw_final.text((6, final.size[1] - 22), txt, fill=(230, 230, 230), font=f)
                else:
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
        return
        try:
            from PIL import Image  # type: ignore
            import cv2 as _cv2  # type: ignore
            import numpy as _np  # type: ignore
        except Exception as e:
            messagebox.showerror("选图片", f"失败: {e}")
            return
        try:
            pil = Image.open(base).convert("RGB")
        except Exception as e:
            messagebox.showerror("选图片", f"失败: {e}")
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
                        cfg = f"--oem 3 --psm {psm} -c tessedit_char_whitelist=0123456789k,"
                        try:
                            d = {}
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
                    try:
                        f = pil_font(12)
                    except Exception:
                        f = None
                    if f is not None:
                        dr.text((px[2] + 4, int((px[1] + px[3]) / 2) - 7), self._fmt_k(pv), fill=(169,176,184), font=f)
                    else:
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
                try:
                    f = pil_font(14)
                except Exception:
                    f = None
                if f is not None:
                    draw_final.text((6, final.size[1] - 22), txt, fill=(230, 230, 230), font=f)
                else:
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
        return

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
                        d ={}
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
                    try:
                        f = pil_font(12)
                    except Exception:
                        f = None
                    if f is not None:
                        dr.text((px[2] + 4, int((px[1] + px[3]) / 2) - 7), self._fmt_k(pv), fill=(169,176,184), font=f)
                    else:
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
                    try:
                        f2 = pil_font(14)
                    except Exception:
                        f2 = None
                    if f2 is not None:
                        dr.text((6, final.size[1]-22), f"价格:{pv} 数量:{qv}", fill=(230,230,230), font=f2)
                    else:
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
        # 确保在主线程更新 Tk 组件；后台线程调用时通过 after 切回主线程
        try:
            import threading as _th  # type: ignore
            if _th.current_thread() is not _th.main_thread():
                try:
                    self.after(0, self._append_log, s)
                except Exception:
                    pass
                return
        except Exception:
            # 回退：继续尝试直接写入（不推荐，但避免静默失败）
            pass

        # 过滤：根据“运行日志”选择的等级
        try:
            lvl = self._parse_log_level(s)
            if self._level_value(lvl) < self._level_value(self.run_log_level_var.get() if hasattr(self, 'run_log_level_var') else 'info'):
                return
        except Exception:
            pass
        with self._log_lock:
            self.txt.configure(state=tk.NORMAL)
            self.txt.insert(tk.END, time.strftime("[%H:%M:%S] ") + s + "\n")
            self.txt.see(tk.END)
            self.txt.configure(state=tk.DISABLED)

    # 日志等级解析与比较
    def _parse_log_level(self, s: str) -> str:
        try:
            if "【ERROR】" in s:
                return "error"
            if "【DEBUG】" in s:
                return "debug"
            if "【INFO】" in s:
                return "info"
        except Exception:
            pass
        return "info"

    def _level_value(self, name: str) -> int:
        m = {"debug": 10, "info": 20, "error": 40}
        return m.get(str(name or '').lower(), 20)

    # 旧自动购买相关方法已移除

    # ---------- Items list management ----------
    def _load_items_from_cfg(self) -> None:
        self.tree.delete(*self.tree.get_children())
        items = self.cfg.get("purchase_items", [])
        for i, it in enumerate(items):
            mode_disp = "固定" if str(it.get("price_mode", "fixed")).lower() != "average" else "平均"
            self.tree.insert(
                "",
                tk.END,
                iid=str(i),
                text=("☑" if it.get("enabled", True) else "☐"),
                values=(
                    it.get("item_name", ""),
                    int(it.get("price_threshold", 0)),
                    int(it.get("restock_price", 0)),
                    int(it.get("price_premium_pct", 0)),
                    mode_disp,
                    int(it.get("max_button_qty", 120)),
                    int(it.get("target_total", 0)),
                    int(it.get("max_per_order", 120)),
                    int(it.get("default_buy_qty", 1)),
                    f"{int(it.get('purchased', 0))}/{int(it.get('target_total', 0))}",
                ),
            )
        if items:
            self.tree.selection_set("0")

    # Per-item selected progress UI removed

    def _reset_item_progress(self, confirm: bool = False) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        idx = int(sel[0])
        items = self.cfg.get("purchase_items", [])
        if not (0 <= idx < len(items)):
            return
        name = str(items[idx].get("item_name", ""))
        if confirm:
            if not messagebox.askokcancel("清空进度", f"确定将 [{name}] 的已购进度清零吗？"):
                return
        items[idx]["purchased"] = 0
        # 同步清空该物品的购买记录（与任务卡片行为一致）
        try:
            from history_store import clear_purchase_history  # type: ignore
            _ = clear_purchase_history(str(items[idx].get("id", "")))
        except Exception:
            pass
        save_config(self.cfg, "config.json")
        # Refresh one row and selected progress
        try:
            mode_disp = "固定" if str(items[idx].get("price_mode", "fixed")).lower() != "average" else "平均"
            self.tree.item(
                str(idx),
                text=("☑" if items[idx].get("enabled", True) else "☐"),
                values=(
                    items[idx].get("item_name", ""),
                    int(items[idx].get("price_threshold", 0)),
                    int(items[idx].get("restock_price", 0)),
                    int(items[idx].get("price_premium_pct", 0)),
                    mode_disp,
                    int(items[idx].get("max_button_qty", 120)),
                    int(items[idx].get("target_total", 0)),
                    int(items[idx].get("max_per_order", 120)),
                    int(items[idx].get("default_buy_qty", 1)),
                    f"0/{int(items[idx].get('target_total', 0))}",
                ),
            )
        except Exception:
            self._load_items_from_cfg()
        # No selected progress UI to update
        # If running, also reset runtime copy
        try:
            if hasattr(self, "_multi") and self._multi and getattr(self._multi, "_thread", None) and self._multi._thread.is_alive():
                if 0 <= idx < len(self._multi.items):
                    self._multi.items[idx]["purchased"] = 0
                    # notify UI via callback to keep consistent
                    self._multi.on_item_update(idx, dict(self._multi.items[idx]))
                    self._append_log(f"已清零进度: [{name}]")
        except Exception:
            pass

    # def _set_selected_progress(...) removed with UI

    # ---------- Modal editor ----------
    def _open_item_modal(self, idx: int | None) -> None:
        items = self.cfg.setdefault("purchase_items", [])
        data = {
            "enabled": True,
            "item_name": "",
            "price_threshold": 0,
            "price_premium_pct": 0,
            "restock_price": 0,
            "target_total": 0,
            "max_per_order": 120,
            "max_button_qty": 120,
            "default_buy_qty": 1,
            "price_mode": "fixed",
            "avg_samples": 100,
            "avg_subtract": 0,
            # 执行时间段（可选）：HH:MM 到 HH:MM，支持跨天
            "time_start": "",
            "time_end": "",
            "id": "",
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
        v_restock = tk.IntVar(value=int(data.get("restock_price", 0)))
        v_target = tk.IntVar(value=int(data.get("target_total", 0)))
        v_max = tk.IntVar(value=int(data.get("max_per_order", 120)))
        v_maxbtn = tk.IntVar(value=int(data.get("max_button_qty", 120)))
        v_defqty = tk.IntVar(value=int(data.get("default_buy_qty", 1)))
        # Time window
        v_tstart = tk.StringVar(value=str(data.get("time_start", "")))
        v_tend = tk.StringVar(value=str(data.get("time_end", "")))
        # Parsed time values for Spinbox selector
        def _parse_hm(s: str):
            s2 = (s or "").strip()
            try:
                hh, mm = s2.split(":")
                h, m = int(hh), int(mm)
                if 0 <= h <= 23 and 0 <= m <= 59:
                    return h, m
            except Exception:
                pass
            return 0, 0
        _h1, _m1 = _parse_hm(v_tstart.get())
        _h2, _m2 = _parse_hm(v_tend.get())
        v_time_enabled = tk.BooleanVar(value=bool((v_tstart.get() or "").strip() or (v_tend.get() or "").strip()))
        v_ts_h = tk.IntVar(value=_h1)
        v_ts_m = tk.IntVar(value=_m1)
        v_te_h = tk.IntVar(value=_h2)
        v_te_m = tk.IntVar(value=_m2)
        # Price mode fields
        v_mode = tk.StringVar(value=str(data.get("price_mode", "fixed")))
        v_avg_samples = tk.IntVar(value=int(data.get("avg_samples", 100)))
        v_avg_sub = tk.IntVar(value=int(data.get("avg_subtract", 0)))

        frm = ttk.Frame(top)
        frm.pack(padx=10, pady=10)
        ttk.Checkbutton(frm, text="启用", variable=v_enabled).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        ttk.Label(frm, text="商品名称").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        ttk.Entry(frm, textvariable=v_name, width=28).grid(row=1, column=1, padx=4, pady=4)
        ttk.Label(frm, text="目标价格(整数)").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        ttk.Spinbox(frm, from_=0, to=10_000_000, textvariable=v_thr, width=12).grid(row=2, column=1, padx=4, pady=4)
        ttk.Label(frm, text="补货价格(整数)").grid(row=3, column=0, sticky="e", padx=4, pady=4)
        ttk.Spinbox(frm, from_=0, to=10_000_000, textvariable=v_restock, width=12).grid(row=3, column=1, padx=4, pady=4)
        # 溢价百分比（允许超过阈值的浮动百分比）
        v_premium = tk.IntVar(value=int(data.get("price_premium_pct", 0)))
        ttk.Label(frm, text="允许溢价(%)").grid(row=4, column=0, sticky="e", padx=4, pady=4)
        ttk.Spinbox(frm, from_=0, to=100, textvariable=v_premium, width=12).grid(row=4, column=1, padx=4, pady=4)
        # 新增：补货模式的价格浮动百分比
        v_rpremium = tk.IntVar(value=int(data.get("restock_premium_pct", 0)))
        ttk.Label(frm, text="补货价允许溢价(%)").grid(row=4, column=2, sticky="e", padx=8, pady=4)
        ttk.Spinbox(frm, from_=0, to=100, textvariable=v_rpremium, width=12).grid(row=4, column=3, padx=4, pady=4)
        ttk.Label(frm, text="目标购买总量").grid(row=5, column=0, sticky="e", padx=4, pady=4)
        ttk.Spinbox(frm, from_=0, to=999999, textvariable=v_target, width=12).grid(row=5, column=1, padx=4, pady=4)
        ttk.Label(frm, text="单次购买上限").grid(row=6, column=0, sticky="e", padx=4, pady=4)
        ttk.Spinbox(frm, from_=1, to=999999, textvariable=v_max, width=12).grid(row=6, column=1, padx=4, pady=4)
        ttk.Label(frm, text="Max数量").grid(row=7, column=0, sticky="e", padx=4, pady=4)
        ttk.Spinbox(frm, from_=1, to=999999, textvariable=v_maxbtn, width=12).grid(row=7, column=1, padx=4, pady=4)
        ttk.Label(frm, text="默认数量").grid(row=8, column=0, sticky="e", padx=4, pady=4)
        ttk.Spinbox(frm, from_=1, to=999999, textvariable=v_defqty, width=12).grid(row=8, column=1, padx=4, pady=4)

        # Time window row
        ttk.Label(frm, text="执行时间段").grid(row=9, column=0, sticky="e", padx=4, pady=4)
        tw = ttk.Frame(frm)
        tw.grid(row=9, column=1, sticky="w")
        ent_ts = ttk.Entry(tw, textvariable=v_tstart, width=8)
        ent_ts.pack(side=tk.LEFT)
        ttk.Label(tw, text="~").pack(side=tk.LEFT, padx=4)
        ent_te = ttk.Entry(tw, textvariable=v_tend, width=8)
        ent_te.pack(side=tk.LEFT)
        # Replace text entries with a time range selector (Spinbox hour:minute)
        try:
            for w in list(tw.winfo_children()):
                w.pack_forget()
        except Exception:
            pass
        def _set_time_widgets_state(*_args):
            st = "normal" if bool(v_time_enabled.get()) else "disabled"
            for w in (sb_ts_h, sb_ts_m, sb_te_h, sb_te_m):
                try:
                    w.configure(state=st)
                except Exception:
                    pass
        ttk.Checkbutton(tw, text="启用", variable=v_time_enabled, command=_set_time_widgets_state).pack(side=tk.LEFT, padx=(0,6))
        try:
            sb_ts_h = ttk.Spinbox(tw, from_=0, to=23, width=3, textvariable=v_ts_h)
        except Exception:
            sb_ts_h = tk.Spinbox(tw, from_=0, to=23, width=3, textvariable=v_ts_h)
        sb_ts_h.pack(side=tk.LEFT)
        ttk.Label(tw, text=":").pack(side=tk.LEFT)
        try:
            sb_ts_m = ttk.Spinbox(tw, from_=0, to=59, width=3, textvariable=v_ts_m)
        except Exception:
            sb_ts_m = tk.Spinbox(tw, from_=0, to=59, width=3, textvariable=v_ts_m)
        sb_ts_m.pack(side=tk.LEFT, padx=(0,6))
        ttk.Label(tw, text="~").pack(side=tk.LEFT, padx=4)
        try:
            sb_te_h = ttk.Spinbox(tw, from_=0, to=23, width=3, textvariable=v_te_h)
        except Exception:
            sb_te_h = tk.Spinbox(tw, from_=0, to=23, width=3, textvariable=v_te_h)
        sb_te_h.pack(side=tk.LEFT)
        ttk.Label(tw, text=":").pack(side=tk.LEFT)
        try:
            sb_te_m = ttk.Spinbox(tw, from_=0, to=59, width=3, textvariable=v_te_m)
        except Exception:
            sb_te_m = tk.Spinbox(tw, from_=0, to=59, width=3, textvariable=v_te_m)
        sb_te_m.pack(side=tk.LEFT)
        ttk.Label(tw, text="(HH:MM，可选；跨天请设置 结束<开始)").pack(side=tk.LEFT, padx=(6,0))
        _set_time_widgets_state()
        ttk.Label(tw, text="(HH:MM，可留空)").pack(side=tk.LEFT, padx=(6,0))

        # Price mode group
        grp_mode = ttk.LabelFrame(frm, text="价格模式")
        grp_mode.grid(row=10, column=0, columnspan=2, padx=0, pady=(6, 0), sticky="we")
        ttk.Label(grp_mode, text="价格模式").grid(row=0, column=0, sticky="e", padx=4, pady=4)
        cmb_mode = ttk.Combobox(grp_mode, state="readonly", width=12, values=["固定值", "平均值"])
        try:
            cmb_mode.set("平均值" if str(v_mode.get()).lower() == "average" else "固定值")
        except Exception:
            cmb_mode.set("固定值")
        cmb_mode.grid(row=0, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(grp_mode, text="平均采样次数").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        try:
            sp_samp = ttk.Spinbox(grp_mode, from_=1, to=500, increment=1, textvariable=v_avg_samples, width=12)
        except Exception:
            sp_samp = tk.Spinbox(grp_mode, from_=1, to=500, increment=1, textvariable=v_avg_samples, width=12)
        sp_samp.grid(row=1, column=1, sticky="w", padx=4, pady=4)
        ttk.Label(grp_mode, text="平均值减去").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        try:
            sp_sub = ttk.Spinbox(grp_mode, from_=0, to=10_000_000, increment=1, textvariable=v_avg_sub, width=12)
        except Exception:
            sp_sub = tk.Spinbox(grp_mode, from_=0, to=10_000_000, increment=1, textvariable=v_avg_sub, width=12)
        sp_sub.grid(row=2, column=1, sticky="w", padx=4, pady=4)

        def _update_mode_ui(*_):
            # sync v_mode with combobox and enable/disable fields
            try:
                val = cmb_mode.get()
                v_mode.set("average" if val == "平均值" else "fixed")
            except Exception:
                pass
            is_avg = str(v_mode.get()).lower() == "average"
            try:
                sp_samp.configure(state=("normal" if is_avg else "disabled"))
                sp_sub.configure(state=("normal" if is_avg else "disabled"))
            except Exception:
                pass

        cmb_mode.bind("<<ComboboxSelected>>", _update_mode_ui)
        _update_mode_ui()

        btns = ttk.Frame(frm)
        btns.grid(row=11, column=0, columnspan=2, pady=(8, 0))
        def on_save():
            name = v_name.get().strip()
            if not name:
                messagebox.showwarning("校验", "商品名称不能为空。", parent=top)
                return
            # Validate numeric fields
            try:
                thr_v = int(v_thr.get())
                restock_v = int(v_restock.get())
                tgt_v = int(v_target.get())
                max_v = int(v_max.get())
                maxbtn_v = int(v_maxbtn.get())
                defqty_v = int(v_defqty.get())
            except Exception:
                messagebox.showwarning("校验", "数值字段格式不正确。", parent=top)
                return
            if thr_v < 0 or restock_v < 0 or tgt_v < 0 or max_v < 1 or maxbtn_v < 1 or defqty_v < 1:
                messagebox.showwarning("校验", "请检查：阈值/补货价/目标≥0，及上限/Max/默认数量≥1。", parent=top)
                return
            mode_val = str(v_mode.get()).lower()
            try:
                avg_s = int(v_avg_samples.get())
                avg_sub = int(v_avg_sub.get())
            except Exception:
                avg_s, avg_sub = 100, 0
            if mode_val == "average":
                if avg_s < 1 or avg_s > 500 or avg_sub < 0:
                    messagebox.showwarning("校验", "平均采样次数需在1~500；平均值减去需≥0。", parent=top)
                    return
            # Validate time window (optional)
            def _valid_time(s: str) -> bool:
                s2 = (s or "").strip()
                if not s2:
                    return True
                try:
                    parts = s2.split(":")
                    if len(parts) != 2:
                        return False
                    hh = int(parts[0]); mm = int(parts[1])
                    return 0 <= hh <= 23 and 0 <= mm <= 59
                except Exception:
                    return False
            if bool(v_time_enabled.get()):
                try:
                    ts_val = f"{int(v_ts_h.get()):02d}:{int(v_ts_m.get()):02d}"
                    te_val = f"{int(v_te_h.get()):02d}:{int(v_te_m.get()):02d}"
                except Exception:
                    ts_val, te_val = "", ""
                try:
                    v_tstart.set(ts_val)
                    v_tend.set(te_val)
                except Exception:
                    pass
            else:
                ts_val = ""
                te_val = ""
            if not _valid_time(ts_val) or not _valid_time(te_val):
                messagebox.showwarning("校验", "执行时间段格式需为 HH:MM（可留空）", parent=top)
                return
            # Ensure ID
            if idx is None:
                item_id = str(uuid.uuid4())
            else:
                try:
                    item_id = str(items[idx].get("id") or str(uuid.uuid4()))
                except Exception:
                    item_id = str(uuid.uuid4())
            item = {
                "enabled": bool(v_enabled.get()),
                "item_name": name,
                "price_threshold": thr_v,
                "price_premium_pct": int(v_premium.get()),
                "restock_price": restock_v,
                "restock_premium_pct": int(v_rpremium.get()),
                "target_total": tgt_v,
                "max_per_order": max_v,
                "max_button_qty": maxbtn_v,
                "default_buy_qty": defqty_v,
                "price_mode": ("average" if mode_val == "average" else "fixed"),
                "avg_samples": int(avg_s),
                "avg_subtract": int(avg_sub),
                "time_start": ts_val,
                "time_end": te_val,
                "id": item_id,
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
            name = str(items[idx].get("item_name", ""))
            if not messagebox.askokcancel("删除", f"确定删除商品 [{name}] 吗？此操作不可撤销。"):
                return
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
                mode_disp = "固定" if str(items[idx].get("price_mode", "fixed")).lower() != "average" else "平均"
                self.tree.item(
                    str(idx),
                    text=("☑" if items[idx].get("enabled", True) else "☐"),
                    values=(
                        items[idx].get("item_name", ""),
                        int(items[idx].get("price_threshold", 0)),
                        int(items[idx].get("restock_price", 0)),
                        int(items[idx].get("price_premium_pct", 0)),
                        mode_disp,
                        int(items[idx].get("max_button_qty", 120)),
                        int(items[idx].get("target_total", 0)),
                        int(items[idx].get("max_per_order", 120)),
                        int(items[idx].get("default_buy_qty", 1)),
                        f"{int(items[idx].get('purchased', 0))}/{int(items[idx].get('target_total', 0))}",
                    ),
                )
            except Exception:
                self._load_items_from_cfg()
            # No per-item selected progress UI to update

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
        # 仅当点击树列(#0)区域时，切换启用状态
        region = self.tree.identify("region", e.x, e.y)
        row = self.tree.identify_row(e.y)
        col = self.tree.identify_column(e.x)
        if not row:
            return
        # 选中该行
        self.tree.selection_set(row)
        # 在显示为 tree 的首列点击时切换（region 可能为 'tree' 或 'cell'，列应为 '#0'）
        if col == "#0" and region in ("tree", "cell"):
            self._toggle_item_enable()

    # ---------- Item IDs ----------
    def _ensure_item_ids(self) -> None:
        items = self.cfg.setdefault("purchase_items", [])
        changed = False
        for it in items:
            if not isinstance(it, dict):
                continue
            if not it.get("id"):
                it["id"] = str(uuid.uuid4())
                changed = True
        if changed:
            save_config(self.cfg, "config.json")

    # ---------- History UI ----------
    def _get_item_by_index(self, idx: int | None) -> Dict[str, Any] | None:
        items = self.cfg.get("purchase_items", [])
        if idx is None:
            return None
        if not (0 <= idx < len(items)):
            return None
        return items[idx]

    def _open_price_history(self, idx: int | None) -> None:
        it = self._get_item_by_index(idx)
        if not it:
            return
        try:
            from history_store import query_price, query_price_minutely  # type: ignore
        except Exception:
            messagebox.showwarning("历史价格", "历史模块不可用。")
            return
        name = str(it.get("item_name", ""))
        item_id = str(it.get("id", ""))

        top = tk.Toplevel(self)
        top.title(f"历史价格 - {name}")
        top.geometry("720x420")
        top.transient(self)
        try:
            top.grab_set()
        except Exception:
            pass

        # Controls
        ctrl = ttk.Frame(top)
        ctrl.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(ctrl, text="时间范围").pack(side=tk.LEFT)
        rng_var = tk.StringVar(value="近1天")
        cmb = ttk.Combobox(ctrl, textvariable=rng_var, state="readonly",
                           values=["近1小时", "近1天", "近7天", "近1月"], width=10)
        cmb.pack(side=tk.LEFT, padx=6)
        lbl_stats = ttk.Label(ctrl, text="")
        lbl_stats.pack(side=tk.RIGHT)

        # Figure area
        figf = ttk.Frame(top)
        figf.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)

        def _sec_for_label(s: str) -> int:
            return {
                "近1小时": 3600,
                "近1天": 86400,
                "近7天": 7 * 86400,
                "近1月": 30 * 86400,
            }.get(s, 86400)

        def _render():
            # Lazy import matplotlib on demand
            try:
                import matplotlib
                matplotlib.use("TkAgg")
                import matplotlib.pyplot as plt  # type: ignore
                from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # type: ignore
                import matplotlib.dates as mdates  # type: ignore
                import matplotlib.ticker as mtick  # type: ignore
                from datetime import datetime
                try:
                    setup_matplotlib_chinese()
                except Exception:
                    pass
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return

            for w in figf.winfo_children():
                try:
                    w.destroy()
                except Exception:
                    pass

            sec = _sec_for_label(rng_var.get())
            since = time.time() - sec
            # Prefer minutely aggregate; fallback to raw ticks if aggregator unavailable
            x: List[Any] = []  # datetime for minutes
            y_avg: List[int] = []
            y_min: List[int] = []
            y_max: List[int] = []
            try:
                recs_m = query_price_minutely(item_id, since)
            except Exception:
                recs_m = []
            if recs_m:
                for r in recs_m:
                    try:
                        ts = float(r.get("ts_min", 0.0))
                        vmin = int(r.get("min", 0))
                        vmax = int(r.get("max", 0))
                        vavg = int(r.get("avg", 0))
                    except Exception:
                        continue
                    x.append(datetime.fromtimestamp(ts))
                    y_min.append(vmin)
                    y_max.append(vmax)
                    y_avg.append(vavg)
            else:
                recs = query_price(item_id, since)
                for r in recs:
                    try:
                        ts = float(r.get("ts", 0.0))
                        pr = int(r.get("price", 0))
                    except Exception:
                        continue
                    x.append(datetime.fromtimestamp(ts))
                    y_avg.append(pr)
                    y_min.append(pr)
                    y_max.append(pr)

            fig = plt.Figure(figsize=(6.4, 3.4), dpi=100)
            ax = fig.add_subplot(111)
            if x and y_avg:
                # Draw avg line and min-max band
                try:
                    import numpy as _np  # type: ignore
                    _has_np = True
                except Exception:
                    _has_np = False
                ax.plot_date(x, y_avg, "-", linewidth=1.5, label="平均价")
                try:
                    ax.fill_between(x, y_min, y_max, color="#90CAF9", alpha=0.25, label="区间[最低,最高]")
                except Exception:
                    pass
                ax.set_title(name)
                ax.set_ylabel("价格")
                ax.grid(True, linestyle=":", alpha=0.4)
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
                # y 轴使用 K/M 缩写
                def _fmt_tick(v, _p):
                    try:
                        v = float(v)
                    except Exception:
                        return str(v)
                    if abs(v) >= 1_000_000:
                        return f"{v/1_000_000:.1f}M"
                    if abs(v) >= 1_000:
                        return f"{v/1_000:.1f}K"
                    try:
                        return f"{int(v):,}"
                    except Exception:
                        return str(v)
                ax.yaxis.set_major_formatter(mtick.FuncFormatter(_fmt_tick))
                fig.autofmt_xdate()
                mn = min(y_min) if y_min else 0
                mx = max(y_max) if y_max else 0
                ax.legend(loc="upper right")
                # 文本显示千分位
                try:
                    lbl_stats.configure(text=f"最高价: {mx:,}    最低价: {mn:,}")
                except Exception:
                    lbl_stats.configure(text=f"最高价: {mx}    最低价: {mn}")
            else:
                ax.set_title("暂无数据")
                lbl_stats.configure(text="")
            canvas = FigureCanvasTkAgg(fig, master=figf)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        cmb.bind("<<ComboboxSelected>>", lambda _e: _render())
        _render()

        btnf = ttk.Frame(top)
        btnf.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(btnf, text="关闭", command=top.destroy).pack(side=tk.RIGHT)
        def _clear_price():
            try:
                from history_store import clear_price_history  # type: ignore
            except Exception:
                messagebox.showwarning("清空", "历史模块不可用。")
                return
            if not messagebox.askokcancel("清空历史", f"确定清空 [{name}] 的历史价格记录吗？该操作不可恢复。"):
                return
            removed = 0
            try:
                removed = int(clear_price_history(item_id))
            except Exception:
                pass
            messagebox.showinfo("清空历史", f"已清空 {removed} 条记录。")
            _render()
        ttk.Button(btnf, text="清空历史", command=_clear_price).pack(side=tk.RIGHT, padx=6)

    def _open_purchase_history(self, idx: int | None) -> None:
        it = self._get_item_by_index(idx)
        if not it:
            return
        try:
            from history_store import query_purchase, summarize_purchases  # type: ignore
        except Exception:
            messagebox.showwarning("购买记录", "历史模块不可用。")
            return
        name = str(it.get("item_name", ""))
        item_id = str(it.get("id", ""))

        top = tk.Toplevel(self)
        top.title(f"购买记录 - {name}")
        top.geometry("780x520")
        top.transient(self)
        try:
            top.grab_set()
        except Exception:
            pass

        # Metrics row（无筛选：展示总购买量、均价、最高/最低购买价）
        met = ttk.Frame(top)
        met.pack(fill=tk.X, padx=8, pady=(0, 6))
        lab_qty = ttk.Label(met, text="购买量: 0")
        lab_avg = ttk.Label(met, text="均价: 0")
        lab_max = ttk.Label(met, text="最高价: 0")
        lab_min = ttk.Label(met, text="最低价: 0")
        for w in (lab_qty, lab_avg, lab_max, lab_min):
            w.pack(side=tk.LEFT, padx=12)

        # Table
        cols = ("time", "task", "price", "qty", "amount")
        tree = ttk.Treeview(top, columns=cols, show="headings")
        tree.heading("time", text="时间")
        tree.heading("task", text="任务")
        tree.heading("price", text="单价")
        tree.heading("qty", text="数量")
        tree.heading("amount", text="总价")
        tree.column("time", width=160)
        tree.column("task", width=160)
        tree.column("price", width=80, anchor="e")
        tree.column("qty", width=80, anchor="e")
        tree.column("amount", width=100, anchor="e")
        tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        def _reload():
            # 无筛选：读取所有记录
            recs = query_purchase(item_id, 0)
            # Fill table
            for r in tree.get_children():
                tree.delete(r)
            for i, r in enumerate(recs):
                iso = str(r.get("iso", ""))
                task_name = str(r.get("task_name", "") or "-")
                price = int(r.get("price", 0))
                qty = int(r.get("qty", 0))
                amount = int(r.get("amount", price * qty))
                # 显示千分位
                try:
                    vs = (iso, task_name, f"{price:,}", f"{qty:,}", f"{amount:,}")
                except Exception:
                    vs = (iso, task_name, str(price), str(qty), str(amount))
                tree.insert("", tk.END, iid=str(i), values=vs)
            # Metrics（数量、均价、最高、最低）
            m = summarize_purchases(recs)
            # 最高/最低购买价按单价统计
            try:
                prices = [int(r.get("price", 0)) for r in recs]
                p_max = max(prices) if prices else 0
                p_min = min(prices) if prices else 0
            except Exception:
                p_max = 0
                p_min = 0
            def fmt(n):
                try:
                    return f"{int(n):,}"
                except Exception:
                    return str(n)
            lab_qty.configure(text=f"购买量: {fmt(m.get('quantity', 0))}")
            lab_avg.configure(text=f"均价: {fmt(m.get('avg_price', 0))}")
            lab_max.configure(text=f"最高价: {fmt(p_max)}")
            lab_min.configure(text=f"最低价: {fmt(p_min)}")

        _reload()

        btnf = ttk.Frame(top)
        btnf.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(btnf, text="关闭", command=top.destroy).pack(side=tk.RIGHT)
        def _export_csv():
            from tkinter import filedialog as _fd
            path = _fd.asksaveasfilename(
                title="导出CSV",
                defaultextension=".csv",
                filetypes=[("CSV", ".csv"), ("All", "*.*")],
                initialfile=f"{name}_purchase_history.csv",
            )
            if not path:
                return
            try:
                import csv
                recs = query_purchase(item_id, 0)
                with open(path, "w", encoding="utf-8-sig", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["时间", "任务", "单价", "数量", "总价"]) 
                    for r in recs:
                        iso = str(r.get("iso", ""))
                        task_name = str(r.get("task_name", "") or "-")
                        price = int(r.get("price", 0))
                        qty = int(r.get("qty", 0))
                        amount = int(r.get("amount", price * qty))
                        w.writerow([iso, task_name, price, qty, amount])
                messagebox.showinfo("导出CSV", f"已导出到: {path}")
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
        def _clear_purchase():
            try:
                from history_store import clear_purchase_history  # type: ignore
            except Exception:
                messagebox.showwarning("清空", "历史模块不可用。")
                return
            if not messagebox.askokcancel("清空记录", f"确定清空 [{name}] 的购买记录吗？该操作不可恢复。"):
                return
            removed = 0
            try:
                removed = int(clear_purchase_history(item_id))
            except Exception:
                pass
            messagebox.showinfo("清空记录", f"已清空 {removed} 条记录。")
            _reload()
        ttk.Button(btnf, text="导出CSV", command=_export_csv).pack(side=tk.RIGHT, padx=6)
        ttk.Button(btnf, text="清空记录", command=_clear_purchase).pack(side=tk.RIGHT, padx=6)

    def _open_purchase_history_for_item(self, item_id: str, name: str) -> None:
        try:
            from history_store import query_purchase, summarize_purchases  # type: ignore
        except Exception:
            messagebox.showwarning("购买记录", "历史模块不可用。")
            return
        if not item_id:
            return
        top = tk.Toplevel(self)
        top.title(f"购买记录 - {name}")
        top.geometry("780x520")
        top.transient(self)
        try:
            top.grab_set()
        except Exception:
            pass
        # Metrics row
        met = ttk.Frame(top)
        met.pack(fill=tk.X, padx=8, pady=(8, 6))
        lab_qty = ttk.Label(met, text="购买量: 0")
        lab_avg = ttk.Label(met, text="均价: 0")
        lab_max = ttk.Label(met, text="最高价: 0")
        lab_min = ttk.Label(met, text="最低价: 0")
        for w in (lab_qty, lab_avg, lab_max, lab_min):
            w.pack(side=tk.LEFT, padx=12)
        # Table
        cols = ("time", "task", "price", "qty", "amount")
        tree = ttk.Treeview(top, columns=cols, show="headings")
        tree.heading("time", text="时间")
        tree.heading("task", text="任务")
        tree.heading("price", text="单价")
        tree.heading("qty", text="数量")
        tree.heading("amount", text="总价")
        tree.column("time", width=160)
        tree.column("task", width=160)
        tree.column("price", width=80, anchor="e")
        tree.column("qty", width=80, anchor="e")
        tree.column("amount", width=100, anchor="e")
        tree.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        def _reload():
            recs = query_purchase(item_id, 0)
            for r in tree.get_children():
                tree.delete(r)
            for i, r in enumerate(recs):
                iso = str(r.get("iso", ""))
                task_name = str(r.get("task_name", "") or "-")
                price = int(r.get("price", 0))
                qty = int(r.get("qty", 0))
                amount = int(r.get("amount", price * qty))
                try:
                    vs = (iso, task_name, f"{price:,}", f"{qty:,}", f"{amount:,}")
                except Exception:
                    vs = (iso, task_name, str(price), str(qty), str(amount))
                tree.insert("", tk.END, iid=str(i), values=vs)
            m = summarize_purchases(recs)
            try:
                prices = [int(r.get("price", 0)) for r in recs]
                p_max = max(prices) if prices else 0
                p_min = min(prices) if prices else 0
            except Exception:
                p_max = 0
                p_min = 0
            def fmt(n):
                try:
                    return f"{int(n):,}"
                except Exception:
                    return str(n)
            lab_qty.configure(text=f"购买量: {fmt(m.get('quantity', 0))}")
            lab_avg.configure(text=f"均价: {fmt(m.get('avg_price', 0))}")
            lab_max.configure(text=f"最高价: {fmt(p_max)}")
            lab_min.configure(text=f"最低价: {fmt(p_min)}")
        _reload()
        btnf = ttk.Frame(top)
        btnf.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(btnf, text="关闭", command=top.destroy).pack(side=tk.RIGHT)


    # ---------- Tab: 测试（模板选择） ----------
    def _build_tab_test(self) -> None:
        """构建“测试”标签页：提供模板图片的选择与预览。

        参考物品市场的“选择图片”交互：按钮+文件对话框+预览画布。
        """
        outer = self.tab_test
        frm = ttk.Frame(outer)
        frm.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        box = ttk.LabelFrame(frm, text="模板选择")
        box.pack(fill=tk.X, padx=4, pady=4)

        row = ttk.Frame(box)
        row.pack(fill=tk.X, padx=8, pady=8)

        ttk.Label(row, text="模板图片").pack(side=tk.LEFT)
        self.var_test_tpl = tk.StringVar(value="")
        ent = ttk.Entry(row, textvariable=self.var_test_tpl, width=60)
        ent.pack(side=tk.LEFT, padx=6)

        # 选择区域画布：165x212（外浅灰细框），顶部20（蓝），底部30（绿），中间剩余（黄）
        selector = tk.Canvas(box, width=165, height=212, bg="#fafafa", highlightthickness=0)
        selector.pack(side=tk.LEFT, padx=8, pady=(0, 8))

        # 固定尺寸与内边距（左右 30；上下 20）
        W, H = 165, 212
        TOP_H, BTM_H = 20, 30
        MID_TOP, MID_BTM = TOP_H, H - BTM_H
        MID_H = MID_BTM - MID_TOP  # 162
        MARG_LR = 30
        MARG_TB = 20  # 若仅需左右留白，将其改为 0 即可

        def _render_selector() -> None:
            selector.delete("all")
            # 填充三段颜色（上蓝、中黄、下绿）
            selector.create_rectangle(0, 0, W, TOP_H, fill="#2d7cff", outline="")            # 顶部蓝
            selector.create_rectangle(0, MID_TOP, W, MID_BTM, fill="#ffd84d", outline="")     # 中间黄
            selector.create_rectangle(0, H - BTM_H, W, H, fill="#2ea043", outline="")         # 底部绿
            # 外部浅灰边框，尽量接近 0.5px
            try:
                selector.create_rectangle(1, 1, W - 1, H - 1, outline="#cccccc", width=0.5)
            except Exception:
                selector.create_rectangle(1, 1, W - 1, H - 1, outline="#cccccc", width=1)

            # 图片区域（在中间区域内，左右/上下各缩进 30；若仅左右，令 MARG_TB=0）
            ix1 = MARG_LR
            ix2 = W - MARG_LR
            iy1 = MID_TOP + MARG_TB
            iy2 = MID_BTM - MARG_TB
            # 辅助虚线框，标出图片可绘制区域
            selector.create_rectangle(ix1, iy1, ix2, iy2, outline="#333", dash=(4, 2))

            # 绘制所选图片（等比缩放以适配图片区域并居中）
            p = (self.var_test_tpl.get() or "").strip()
            if not p or not os.path.exists(p):
                return
            try:
                from PIL import Image, ImageTk  # type: ignore
                im = Image.open(p)
                max_w, max_h = max(1, int(ix2 - ix1)), max(1, int(iy2 - iy1))
                im.thumbnail((max_w, max_h))
                tkimg = ImageTk.PhotoImage(im)
            except Exception:
                return
            # 居中放置
            dx = (max(0, (ix2 - ix1) - tkimg.width())) // 2
            dy = (max(0, (iy2 - iy1) - tkimg.height())) // 2
            selector.image = tkimg  # 保持引用
            selector.create_image(ix1 + dx, iy1 + dy, anchor=tk.NW, image=tkimg)

        def _choose_tpl() -> None:
            try:
                initdir = os.path.join(os.getcwd(), "images")
            except Exception:
                initdir = None  # type: ignore
            path = filedialog.askopenfilename(
                title="选择图片",
                filetypes=[("Image", ".png .jpg .jpeg .bmp"), ("All", "*.*")],
                initialdir=(initdir if (initdir and os.path.exists(initdir)) else None),
            )
            if path:
                self.var_test_tpl.set(path)
                _render_selector()

        ttk.Button(row, text="选择图片", command=_choose_tpl).pack(side=tk.LEFT, padx=6)

        try:
            # 跟随文本框变动进行重绘
            self.var_test_tpl.trace_add("write", lambda *_: _render_selector())
        except Exception:
            pass

        # 初始渲染
        _render_selector()

        # 截图 + OCR 识别（名称/价格）
        box2 = ttk.LabelFrame(frm, text="截图识别（卡片样式 165×212）")
        box2.pack(fill=tk.X, padx=4, pady=(4, 4))

        row2 = ttk.Frame(box2)
        row2.pack(fill=tk.X, padx=8, pady=8)

        self.var_test_name = tk.StringVar(value="")
        self.var_test_price = tk.StringVar(value="")

        def _capture_and_ocr() -> None:
            root = self.winfo_toplevel()
            result_bounds: tuple[int, int, int, int] | None = None

            def _done(bounds):
                nonlocal result_bounds
                result_bounds = bounds

            sel = _CardSelector(root, _done, w=165, h=212, top_h=20, bottom_h=30, margin_lr=30, margin_tb=20)
            try:
                sel.show()
            except Exception:
                return
            # Wait overlay closed
            try:
                root.wait_window(sel.top)
            except Exception:
                pass
            if not result_bounds:
                return
            x1, y1, x2, y2 = result_bounds
            W, H = root.winfo_screenwidth(), root.winfo_screenheight()
            # Clamp to screen
            def _clamp_rect(x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
                x = max(0, min(x, max(0, W - 1)))
                y = max(0, min(y, max(0, H - 1)))
                w = max(1, min(w, W - x))
                h = max(1, min(h, H - y))
                return x, y, w, h

            card_w, card_h = 165, 212
            top_h, btm_h = 20, 30
            mid_h = card_h - top_h - btm_h  # 162
            # Regions in screen coords
            top_rect = _clamp_rect(x1, y1, card_w, top_h)
            btm_rect = _clamp_rect(x1, y1 + card_h - btm_h, card_w, btm_h)
            # Middle image region（左右 30；上下 20 → 高度更大）
            ix = x1 + 30
            iy = y1 + top_h + 20
            iw = card_w - 60
            ih = mid_h - 40
            mid_rect = _clamp_rect(ix, iy, iw, ih)
            card_rect = _clamp_rect(x1, y1, card_w, card_h)

            try:
                import pyautogui  # type: ignore
                name_img = pyautogui.screenshot(region=top_rect)
                price_img = pyautogui.screenshot(region=btm_rect)
                mid_img = pyautogui.screenshot(region=mid_rect)
                card_img = pyautogui.screenshot(region=card_rect)
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return

            # 保存推断 ROI（名称/中间/价格/整卡）到独立目录，并记录坐标
            try:
                import json
                ts = time.strftime("%Y%m%d_%H%M%S")
                out_dir = os.path.join(os.getcwd(), "images", "test", f"roi_{ts}_{uuid.uuid4().hex[:6]}")
                os.makedirs(out_dir, exist_ok=True)
                p_name = os.path.join(out_dir, "name.png")
                p_price = os.path.join(out_dir, "price.png")
                p_mid = os.path.join(out_dir, "middle.png")
                p_card = os.path.join(out_dir, "card.png")
                name_img.save(p_name)
                price_img.save(p_price)
                mid_img.save(p_mid)
                card_img.save(p_card)
                def _to_dict(r):
                    x, y, w, h = r
                    return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
                meta = {
                    "card": _to_dict(card_rect),
                    "top_name": _to_dict(top_rect),
                    "middle": _to_dict(mid_rect),
                    "bottom_price": _to_dict(btm_rect),
                    "margins": {"lr": 30, "tb": 20},
                    "sections": {"top": 20, "bottom": 30},
                }
                with open(os.path.join(out_dir, "roi.json"), "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
                # 更新模板路径为中间图便于预览
                self.var_test_tpl.set(p_mid)
                try:
                    messagebox.showinfo("保存", f"ROI 已保存到:\n{out_dir}")
                except Exception:
                    pass
            except Exception:
                # 保存失败不影响后续 OCR
                pass
            # 重绘预览
            try:
                _render_selector()
            except Exception:
                pass

            # OCR（Umi-OCR HTTP）
            try:
                from utils.ocr_utils import recognize_text, recognize_numbers  # type: ignore
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return

            umi = (self.cfg or {}).get("umi_ocr", {}) if hasattr(self, "cfg") else {}
            base_url = str(umi.get("base_url", "http://127.0.0.1:1224"))
            timeout = float(umi.get("timeout_sec", 5.0) or 5.0)
            options = umi.get("options", {}) or {}

            try:
                _boxes = recognize_text(name_img, base_url=base_url, timeout=timeout, options=options)
                name_txt = " ".join((b.text or "").strip() for b in _boxes if (b.text or "").strip())
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                name_txt = ""
            try:
                _nums = recognize_numbers(price_img, base_url=base_url, timeout=timeout, options=options)
                _cand = max([n for n in _nums if getattr(n, "value", None) is not None], key=lambda n: int(n.value)) if _nums else None  # type: ignore[arg-type]
                price_txt = (_cand.clean_text if _cand and getattr(_cand, "clean_text", None) else (_cand.text if _cand else ""))
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                price_txt = ""

            self.var_test_name.set((name_txt or "").strip())
            # 价格清洗：与 task_runner._parse_price_text 保持一致
            try:
                from task_runner import _parse_price_text  # type: ignore
            except Exception:
                _parse_price_text = None  # type: ignore
            val = None
            if _parse_price_text is not None:
                try:
                    val = _parse_price_text(price_txt or "")
                except Exception:
                    val = None
            if val is None:
                self.var_test_price.set((price_txt or "").strip())
            else:
                self.var_test_price.set(str(int(val)))

        # 识别阈值
        ttk.Label(row2, text="阈值").pack(side=tk.LEFT)
        try:
            self.var_test_conf = tk.DoubleVar(value=0.85)
        except Exception:
            self.var_test_conf = tk.StringVar(value="0.85")  # fallback
        try:
            spc = ttk.Spinbox(row2, from_=0.5, to=1.0, increment=0.01, textvariable=self.var_test_conf, width=6)
        except Exception:
            spc = tk.Spinbox(row2, from_=0.5, to=1.0, increment=0.01, textvariable=self.var_test_conf, width=6)
        spc.pack(side=tk.LEFT, padx=(4, 10))

        ttk.Button(row2, text="截图并识别", command=_capture_and_ocr).pack(side=tk.LEFT)
        # 基于模板定位屏幕并识别（无需再次框选）
        def _locate_and_ocr() -> None:
            p = (self.var_test_tpl.get() or "").strip()
            if not p or not os.path.exists(p):
                messagebox.showwarning("定位", "请先选择模板图片（中间区域图）")
                return
            try:
                import pyautogui  # type: ignore
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return
            # 读取模板尺寸，用于区分是整卡模板还是中间区域模板
            tpl_w = tpl_h = None
            try:
                from PIL import Image  # type: ignore
                _im = Image.open(p)
                tpl_w, tpl_h = _im.size
            except Exception:
                pass
            try:
                try:
                    conf = float(self.var_test_conf.get())  # type: ignore[arg-type]
                except Exception:
                    conf = 0.85
                box = pyautogui.locateOnScreen(p, confidence=conf)
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return
            if not box:
                messagebox.showwarning("定位", "未在屏幕上匹配到该图片，可尝试降低阈值或重新截图模板。")
                return
            # 以匹配到的中间图片区域为基准，反推卡片整体与上下 OCR 区域
            mid_left = int(getattr(box, 'left', 0))
            mid_top = int(getattr(box, 'top', 0))
            mid_w = int(getattr(box, 'width', 0))
            mid_h = int(getattr(box, 'height', 0))

            MARG_LR, MARG_TB = 30, 20
            TOP_H, BTM_H = 20, 30

            # 若模板疑似为整卡（165x212 附近），直接使用匹配矩形作为卡片；否则按中间区域推断整卡
            if tpl_w is not None and tpl_h is not None and abs(tpl_w - 165) <= 6 and abs(tpl_h - 212) <= 8:
                card_x1, card_y1, card_w, card_h = mid_left, mid_top, mid_w, mid_h
            else:
                card_x1 = mid_left - MARG_LR
                card_y1 = mid_top - (TOP_H + MARG_TB)
                card_w = mid_w + 2 * MARG_LR
                card_h = (TOP_H + MARG_TB) + mid_h + (MARG_TB + BTM_H)

            # 生成各区域矩形并截图
            W, H = self.winfo_screenwidth(), self.winfo_screenheight()
            def _clamp_rect(x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
                x = max(0, min(x, max(0, W - 1)))
                y = max(0, min(y, max(0, H - 1)))
                w = max(1, min(w, W - x))
                h = max(1, min(h, H - y))
                return x, y, w, h

            top_rect = _clamp_rect(card_x1, card_y1, card_w, TOP_H)
            btm_rect = _clamp_rect(card_x1, card_y1 + card_h - BTM_H, card_w, BTM_H)
            # 中间与整卡矩形
            if tpl_w is not None and tpl_h is not None and abs(tpl_w - 165) <= 6 and abs(tpl_h - 212) <= 8:
                mid_rect = _clamp_rect(
                    card_x1 + MARG_LR,
                    card_y1 + TOP_H + MARG_TB,
                    card_w - 2 * MARG_LR,
                    (card_h - TOP_H - BTM_H) - 2 * MARG_TB,
                )
            else:
                mid_rect = _clamp_rect(mid_left, mid_top, mid_w, mid_h)
            card_rect = _clamp_rect(card_x1, card_y1, card_w, card_h)

            try:
                name_img = pyautogui.screenshot(region=top_rect)
                price_img = pyautogui.screenshot(region=btm_rect)
                mid_img = pyautogui.screenshot(region=mid_rect)
                card_img = pyautogui.screenshot(region=card_rect)
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return

            # OCR with Umi
            try:
                from utils.ocr_utils import recognize_text, recognize_numbers  # type: ignore
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return

            umi = (self.cfg or {}).get("umi_ocr", {}) if hasattr(self, "cfg") else {}
            base_url = str(umi.get("base_url", "http://127.0.0.1:1224"))
            timeout = float(umi.get("timeout_sec", 5.0) or 5.0)
            options = umi.get("options", {}) or {}

            try:
                _boxes = recognize_text(name_img, base_url=base_url, timeout=timeout, options=options)
                name_txt = " ".join((b.text or "").strip() for b in _boxes if (b.text or "").strip())
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                name_txt = ""
            try:
                _nums = recognize_numbers(price_img, base_url=base_url, timeout=timeout, options=options)
                _cand = max([n for n in _nums if getattr(n, "value", None) is not None], key=lambda n: int(n.value)) if _nums else None  # type: ignore[arg-type]
                price_txt = (_cand.clean_text if _cand and getattr(_cand, "clean_text", None) else (_cand.text if _cand else ""))
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                price_txt = ""

            self.var_test_name.set((name_txt or "").strip())
            # 价格清洗：与 task_runner._parse_price_text 保持一致
            try:
                from task_runner import _parse_price_text  # type: ignore
            except Exception:
                _parse_price_text = None  # type: ignore
            val = None
            if _parse_price_text is not None:
                try:
                    val = _parse_price_text(price_txt or "")
                except Exception:
                    val = None
            if val is None:
                self.var_test_price.set((price_txt or "").strip())
            else:
                self.var_test_price.set(str(int(val)))

            # 保存推断 ROI（名称/中间/价格/整卡）
            try:
                import json
                ts = time.strftime("%Y%m%d_%H%M%S")
                out_dir = os.path.join(os.getcwd(), "images", "test", f"roi_{ts}_{uuid.uuid4().hex[:6]}")
                os.makedirs(out_dir, exist_ok=True)
                p_name = os.path.join(out_dir, "name.png")
                p_price = os.path.join(out_dir, "price.png")
                p_mid = os.path.join(out_dir, "middle.png")
                p_card = os.path.join(out_dir, "card.png")
                name_img.save(p_name)
                price_img.save(p_price)
                mid_img.save(p_mid)
                card_img.save(p_card)
                def _to_dict(r):
                    x, y, w, h = r
                    return {"x": int(x), "y": int(y), "w": int(w), "h": int(h)}
                meta = {
                    "card": _to_dict(card_rect),
                    "top_name": _to_dict(top_rect),
                    "middle": _to_dict(mid_rect),
                    "bottom_price": _to_dict(btm_rect),
                    "margins": {"lr": 30, "tb": 20},
                    "sections": {"top": 20, "bottom": 35},
                }
                with open(os.path.join(out_dir, "roi.json"), "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
                # 更新测试模板为中间图并重绘
                self.var_test_tpl.set(p_mid)
                try:
                    _render_selector()
                except Exception:
                    pass
                try:
                    messagebox.showinfo("保存", f"ROI 已保存到:\n{out_dir}")
                except Exception:
                    pass
            except Exception:
                pass

        ttk.Button(row2, text="定位并识别", command=_locate_and_ocr).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(row2, text="名称").pack(side=tk.LEFT, padx=(12, 4))
        ttk.Entry(row2, textvariable=self.var_test_name, width=24).pack(side=tk.LEFT)
        ttk.Label(row2, text="价格").pack(side=tk.LEFT, padx=(12, 4))
        ttk.Entry(row2, textvariable=self.var_test_price, width=14).pack(side=tk.LEFT)


    # ---------- Tab: 多商品抢购模式 ----------
    def _build_tab_multi(self) -> None:
        outer = self.tab_multi
        frm = ttk.Frame(outer)
        frm.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # 必备设置：最近购买 / 我的收藏 模板
        box_req = ttk.LabelFrame(frm, text="必备设置")
        box_req.pack(fill=tk.X, padx=4, pady=4)
        # 确保单行模板配置可横向拉伸
        try:
            box_req.columnconfigure(0, weight=1)
        except Exception:
            pass

        # 工具：模板测试/截图/预览（本页私有）
        def _test_match(name: str, path: str, conf: float) -> None:
            if not path or not os.path.exists(path):
                messagebox.showwarning("测试识别", f"文件不存在: {path}")
                return
            try:
                import pyautogui  # type: ignore
                center = pyautogui.locateCenterOnScreen(path, confidence=conf)
                if center:
                    try:
                        pyautogui.moveTo(center.x, center.y, duration=0.08)
                        pyautogui.click(center.x, center.y)
                    except Exception:
                        pass
                    return
                _ = pyautogui.locateOnScreen(path, confidence=conf)
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return
            messagebox.showwarning("测试识别", f"{name} 未匹配到。可降低置信度或重截图片。")

        def _capture_into(row: "TemplateRow") -> None:
            def _after(bounds):
                if not bounds:
                    return
                x1, y1, x2, y2 = bounds
                w, h = max(1, x2 - x1), max(1, y2 - y1)
                try:
                    import pyautogui  # type: ignore
                    img = pyautogui.screenshot(region=(x1, y1, w, h))
                except Exception as e:
                    messagebox.showerror("选图片", f"失败: {e}")
                    return
                os.makedirs("images", exist_ok=True)
                slug = self._template_slug(row.name)
                p = os.path.join("images", f"{slug}.png")
                try:
                    img.save(p)
                except Exception as e:
                    messagebox.showerror("选图片", f"失败: {e}")
                    return
                row.var_path.set(p)
                self._schedule_autosave()

            self._select_region(_after)

        # 两条模板配置行
        tpls = (self.cfg.get("templates", {}) or {})
        data_recent = tpls.get("recent_purchases_tab", {}) if isinstance(tpls.get("recent_purchases_tab"), dict) else {}
        r_recent = TemplateRow(
            box_req,
            "最近购买模板",
            data_recent,
            on_test=_test_match,
            on_capture=_capture_into,
            on_preview=self._preview_image,
            on_change=self._schedule_autosave,
        )
        r_recent.grid(row=0, column=0, sticky="we", padx=6, pady=4)
        self.template_rows["recent_purchases_tab"] = r_recent

        data_fav = tpls.get("favorites_tab", {}) if isinstance(tpls.get("favorites_tab"), dict) else {}
        r_fav = TemplateRow(
            box_req,
            "我的收藏模板",
            data_fav,
            on_test=_test_match,
            on_capture=_capture_into,
            on_preview=self._preview_image,
            on_change=self._schedule_autosave,
        )
        r_fav.grid(row=1, column=0, sticky="we", padx=6, pady=(0, 6))
        self.template_rows["favorites_tab"] = r_fav

        # 任务列表改为弹窗配置

        # 控制区
        ctrl = ttk.Frame(frm)
        ctrl.pack(fill=tk.X, padx=4, pady=(6, 4))
        self.btn_snipe_start = ttk.Button(ctrl, text="开始任务", command=self._snipe_start)
        self.btn_snipe_start.pack(side=tk.LEFT)
        self.btn_snipe_stop = ttk.Button(ctrl, text="终止任务", command=self._snipe_stop_clicked)
        self.btn_snipe_stop.pack(side=tk.LEFT, padx=6)
        ttk.Button(ctrl, text="清空全部商品购买记录", command=self._snipe_clear_records).pack(side=tk.LEFT, padx=6)
        ttk.Button(ctrl, text="配置任务", command=self._open_snipe_tasks_modal).pack(side=tk.LEFT, padx=(12, 0))

        # 调试模式（专属于多商品抢购）
        box_dbg = ttk.LabelFrame(frm, text="调试模式（仅本页面生效）")
        box_dbg.pack(fill=tk.X, padx=4, pady=(0, 6))
        dbg_cfg = self.cfg.get("debug", {}) if isinstance(self.cfg.get("debug"), dict) else {}
        try:
            self.var_debug_enabled = tk.BooleanVar(value=bool(dbg_cfg.get("enabled", False)))
        except Exception:
            self.var_debug_enabled = tk.BooleanVar(value=False)
        try:
            _ov = float(dbg_cfg.get("overlay_sec", 5.0))
        except Exception:
            _ov = 5.0
        self.var_debug_overlay = tk.DoubleVar(value=_ov)
        try:
            _st = float(dbg_cfg.get("step_sleep", 0.0))
        except Exception:
            _st = 0.0
        self.var_debug_step = tk.DoubleVar(value=_st)
        try:
            self.var_debug_save_imgs = tk.BooleanVar(value=bool(dbg_cfg.get("save_overlay_images", False)))
        except Exception:
            self.var_debug_save_imgs = tk.BooleanVar(value=False)
        try:
            _dir = str(dbg_cfg.get("overlay_dir", os.path.join("images", "debug", "可视化调试")))
        except Exception:
            _dir = os.path.join("images", "debug", "可视化调试")
        self.var_debug_overlay_dir = tk.StringVar(value=_dir)

        chk = ttk.Checkbutton(box_dbg, text="启用调试可视化（绘制ROI/模板）", variable=self.var_debug_enabled)
        chk.grid(row=0, column=0, columnspan=2, sticky="w", padx=6, pady=(6, 2))
        ttk.Label(box_dbg, text="蒙版时长(秒)").grid(row=1, column=0, sticky="e", padx=6, pady=6)
        try:
            sp_ov = ttk.Spinbox(box_dbg, from_=0.5, to=15.0, increment=0.5, width=8, textvariable=self.var_debug_overlay)
        except Exception:
            sp_ov = tk.Spinbox(box_dbg, from_=0.5, to=15.0, increment=0.5, width=8, textvariable=self.var_debug_overlay)
        sp_ov.grid(row=1, column=1, sticky="w")
        ttk.Label(box_dbg, text="步进延时(秒)").grid(row=1, column=2, sticky="e", padx=12)
        try:
            sp_st = ttk.Spinbox(box_dbg, from_=0.0, to=1.0, increment=0.01, width=8, textvariable=self.var_debug_step)
        except Exception:
            sp_st = tk.Spinbox(box_dbg, from_=0.0, to=1.0, increment=0.01, width=8, textvariable=self.var_debug_step)
        sp_st.grid(row=1, column=3, sticky="w")
        try:
            ttk.Button(box_dbg, text="立即预览蒙版", command=self._debug_test_overlay).grid(row=1, column=4, padx=10)
        except Exception:
            pass
        # Row 2: 保存开关
        try:
            chk_save = ttk.Checkbutton(box_dbg, text="保存可视化截图", variable=self.var_debug_save_imgs)
        except Exception:
            chk_save = tk.Checkbutton(box_dbg, text="保存可视化截图", variable=self.var_debug_save_imgs)
        chk_save.grid(row=2, column=0, sticky="w", padx=6, pady=(0, 4))
        # Row 2: 目录
        ttk.Label(box_dbg, text="保存目录").grid(row=2, column=1, sticky="e", padx=6)
        ent_dir = ttk.Entry(box_dbg, width=36, textvariable=self.var_debug_overlay_dir)
        ent_dir.grid(row=2, column=2, columnspan=2, sticky="we", padx=4)
        def _pick_overlay_dir():
            try:
                from tkinter import filedialog as _fd
                p = _fd.askdirectory(title="选择保存目录")
            except Exception:
                p = None
            if p:
                try:
                    self.var_debug_overlay_dir.set(p)
                except Exception:
                    pass
        try:
            ttk.Button(box_dbg, text="选择…", command=_pick_overlay_dir).grid(row=2, column=4, padx=6)
        except Exception:
            pass
        for c in range(0, 5):
            try:
                box_dbg.columnconfigure(c, weight=0)
            except Exception:
                pass
        # 自动保存（去抖）
        for v in [self.var_debug_enabled, self.var_debug_overlay, self.var_debug_step, self.var_debug_save_imgs, self.var_debug_overlay_dir]:
            try:
                v.trace_add("write", lambda *_: self._schedule_autosave())
            except Exception:
                pass

        # 任务列表改为弹窗进行配置；此处仅提供预览与控制入口。
        # 选择商品预览：弹出与任务配置相同的“选择物品”弹窗；
        # 选择后在屏幕上定位卡片中间模板，依此推断价格区域并将鼠标先移动到“商品（中间图片）”位置，
        # 再移动到“价格”位置，以示成功获取对应区域。
        def _pick_preview():
            def _after_pick(g: dict) -> None:
                path = str(g.get("image_path", "") or "").strip()
                name = str(g.get("name", "") or "")
                if not path or not os.path.exists(path):
                    messagebox.showwarning("预览", "所选物品缺少图片或路径不存在。")
                    return
                try:
                    import pyautogui  # type: ignore
                except Exception as e:
                    messagebox.showerror("选图片", f"失败: {e}")
                    return
                conf = 0.83
                # 尝试定位中间模板
                try:
                    box = pyautogui.locateOnScreen(path, confidence=float(conf))
                except Exception as e:
                    messagebox.showerror("选图片", f"失败: {e}")
                    return
                if box is None:
                    messagebox.showwarning("预览", f"未能在屏幕找到：{name}。请打开‘我的收藏/最近购买’并确保卡片清晰可见。")
                    return
                mid = (int(box.left), int(box.top), int(box.width), int(box.height))
                # 依据 MultiSnipeRunner 的几何推断卡片与价格区域
                try:
                    if MultiSnipeRunner is not None:
                        card = MultiSnipeRunner._infer_card_from_mid(mid)  # type: ignore[attr-defined]
                        top_rect, btm_rect = MultiSnipeRunner._rois_from_card(card)  # type: ignore[attr-defined]
                    else:
                        # 回退常量：与 runner 中保持一致
                        TOP_H, BTM_H = 20, 30
                        MARG_LR, MARG_TB = 30, 20
                        ml, mt, mw, mh = mid
                        x1 = ml - MARG_LR
                        y1 = mt - (TOP_H + MARG_TB)
                        w = mw + 2 * MARG_LR
                        h = (TOP_H + MARG_TB) + mh + (MARG_TB + BTM_H)
                        card = (int(x1), int(y1), int(w), int(h))
                        cl, ct, cw, ch = card
                        top_rect = (cl, ct, cw, TOP_H)
                        btm_rect = (cl, ct + ch - BTM_H, cw, BTM_H)
                except Exception:
                    messagebox.showerror("预览", "计算区域失败。")
                    return
                # 鼠标移动：先至中间模板中心，再至价格区域中心
                try:
                    cx_mid = int(mid[0] + mid[2] / 2)
                    cy_mid = int(mid[1] + mid[3] / 2)
                    cx_price = int(btm_rect[0] + btm_rect[2] / 2)
                    cy_price = int(btm_rect[1] + btm_rect[3] / 2)
                    pyautogui.moveTo(cx_mid, cy_mid, duration=0.12)
                    time.sleep(0.15)
                    pyautogui.moveTo(cx_price, cy_price, duration=0.12)
                except Exception:
                    pass
                try:
                    self._append_multi_log(f"[预览] {name} → mid={mid} price={btm_rect}")
                except Exception:
                    pass
            # 使用与任务配置相同的物品选择器
            self._open_goods_picker(_after_pick)

        ttk.Button(ctrl, text="选择商品预览", command=_pick_preview).pack(side=tk.LEFT, padx=(12, 0))

        # 日志
        logf = ttk.LabelFrame(frm, text="日志")
        logf.pack(fill=tk.BOTH, expand=True, padx=4, pady=(6, 4))
        topbar = ttk.Frame(logf)
        topbar.pack(fill=tk.X, padx=6, pady=(6, 0))
        ttk.Label(topbar, text="日志等级").pack(side=tk.LEFT)
        self.multi_log_level_var = tk.StringVar(value="info")
        cmb = ttk.Combobox(topbar, width=8, state="readonly", values=["debug", "info", "error"], textvariable=self.multi_log_level_var)
        cmb.pack(side=tk.LEFT, padx=6)
        self.multi_txt = tk.Text(logf, height=12, wrap="word")
        self.multi_txt.pack(fill=tk.BOTH, expand=True)
        self.multi_txt.configure(state=tk.DISABLED)

    # ---- 多商品抢购：数据加载/保存 ----
    def _load_snipe_tasks_data(self, path: str) -> Dict[str, Any]:
        try:
            if os.path.exists(path):
                import json
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("items", [])
                    return data
        except Exception:
            pass
        return {"items": []}

    def _save_snipe_tasks_data(self) -> None:
        try:
            import json
            with open(self.snipe_tasks_path, "w", encoding="utf-8") as f:
                json.dump(self.snipe_tasks_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---- 多商品抢购：日志 ----
    def _append_multi_log(self, s: str) -> None:
        try:
            import threading as _th
            if _th.current_thread() is not _th.main_thread():
                self.after(0, self._append_multi_log, s)
                return
        except Exception:
            pass
        try:
            lvl = self._parse_log_level(s)
            if self._level_value(lvl) < self._level_value(self.multi_log_level_var.get() if hasattr(self, 'multi_log_level_var') else 'info'):
                return
        except Exception:
            pass
        with self._snipe_log_lock:
            self.multi_txt.configure(state=tk.NORMAL)
            self.multi_txt.insert(tk.END, time.strftime("[%H:%M:%S] ") + s + "\n")
            self.multi_txt.see(tk.END)
            self.multi_txt.configure(state=tk.DISABLED)

    # ---- 多商品抢购：任务卡片 ----
    def _render_snipe_task_cards(self) -> None:
        self._render_snipe_task_cards_into(self.snipe_cards)

    def _render_snipe_task_cards_into(self, parent, *, on_refresh=None) -> None:
        for w in list(parent.winfo_children()):
            try:
                w.destroy()
            except Exception:
                pass
        items: list[dict] = list(self.snipe_tasks_data.get("items", []) or [])
        if not items:
            ttk.Label(parent, text="暂无任务，点击‘新增…’添加。", foreground="#666").pack(anchor="w", padx=4, pady=6)
            return
        for i, it in enumerate(items):
            self._build_snipe_task_card(parent, i, it, editable=(self._snipe_editing_index == i), on_refresh=on_refresh)

    def _build_snipe_task_card(self, parent, idx: int | None, it: dict, *, editable: bool, on_refresh=None) -> None:
        f = ttk.Frame(parent, relief=tk.GROOVE, borderwidth=1)
        f.pack(fill=tk.X, padx=4, pady=4)
        # 变量
        var_enabled = tk.BooleanVar(value=bool(it.get("enabled", True)))
        var_item_name = tk.StringVar(value=str(it.get("name", "")))
        var_item_id = tk.StringVar(value=str(it.get("item_id", "")))
        var_price = tk.IntVar(value=int(it.get("price", it.get("price_threshold", 0)) or 0))
        var_prem = tk.DoubleVar(value=float(it.get("premium_pct", 0.0) or 0.0))
        var_mode = tk.StringVar(value=str(it.get("purchase_mode", it.get("mode", "normal")) or "normal").lower())
        var_qty = tk.IntVar(value=int(it.get("target_total", it.get("buy_qty", 0)) or 0))
        var_img = tk.StringVar(value=str(it.get("image_path", it.get("template", "")) or ""))
        var_big = tk.StringVar(value=str(it.get("big_category", "") or ""))

        # 行1：启用、名称、选择
        row1 = ttk.Frame(f)
        row1.pack(fill=tk.X, padx=6, pady=(6, 4))
        chk_enabled = ttk.Checkbutton(row1, text="启用", variable=var_enabled)
        chk_enabled.pack(side=tk.LEFT)
        ttk.Label(row1, text="商品").pack(side=tk.LEFT, padx=(12, 4))
        ent_name = ttk.Entry(row1, textvariable=var_item_name, width=24, state=(tk.NORMAL if editable else tk.DISABLED))
        ent_name.pack(side=tk.LEFT)
        def _pick_goods():
            def _on_pick(g):
                var_item_name.set(str(g.get('name','')))
                var_item_id.set(str(g.get('id','')))
                p = str(g.get('image_path','') or '')
                if p:
                    var_img.set(p)
                bc = str(g.get('big_category','') or '')
                if bc:
                    var_big.set(bc)
            self._open_goods_picker(_on_pick)
        ttk.Button(row1, text="选择…", command=_pick_goods, state=(tk.NORMAL if editable else tk.DISABLED)).pack(side=tk.LEFT, padx=(6,0))
        # 只读态启用即时生效：切换即保存
        if not editable and idx is not None:
            def _save_enabled(*_):
                try:
                    items = list(self.snipe_tasks_data.get("items", []) or [])
                    if 0 <= int(idx) < len(items):
                        items[int(idx)]["enabled"] = bool(var_enabled.get())
                        self.snipe_tasks_data["items"] = items
                        self._save_snipe_tasks_data()
                except Exception:
                    pass
            try:
                var_enabled.trace_add("write", _save_enabled)
            except Exception:
                pass

        # 预览（只读）——参考“购买任务配置”关键参数展示
        if not editable:
            preview = ttk.Frame(f)
            preview.pack(fill=tk.X, padx=6, pady=(6, 0))
            try:
                base = int(var_price.get() or 0)
            except Exception:
                base = 0
            try:
                prem = float(var_prem.get() or 0.0)
            except Exception:
                prem = 0.0
            limit = base + int(round(base * max(0.0, prem) / 100.0)) if base > 0 else 0
            purchased = int(it.get("purchased", 0) or 0)
            target = int(it.get("target_total", it.get("buy_qty", 0)) or 0)
            mode_disp = "补货" if (var_mode.get() == "restock") else "正常"
            # 两行栅格：第1行（名称、模式、进度）；第2行（价格、浮动%、上限）
            r1 = ttk.Frame(preview)
            r1.pack(fill=tk.X)
            ttk.Label(r1, text=f"名称：{var_item_name.get()}").grid(row=0, column=0, sticky="w", padx=(0, 8))
            ttk.Label(r1, text=f"模式：{mode_disp}").grid(row=0, column=1, sticky="w", padx=(0, 8))
            ttk.Label(r1, text=f"进度：{purchased}/{target}").grid(row=0, column=2, sticky="w", padx=(0, 8))
            r2 = ttk.Frame(preview)
            r2.pack(fill=tk.X)
            ttk.Label(r2, text=f"价格：{base}").grid(row=0, column=0, sticky="w", padx=(0, 8))
            ttk.Label(r2, text=f"浮动：{int(prem)}%").grid(row=0, column=1, sticky="w", padx=(0, 8))
            if limit > 0:
                ttk.Label(r2, text=f"上限：{limit}").grid(row=0, column=2, sticky="w", padx=(0, 8))

        # 行2：价格、浮动%、模式、数量（仅编辑态显示，非编辑态用上方预览）
        if editable:
            row2 = ttk.Frame(f)
            row2.pack(fill=tk.X, padx=6, pady=(0, 6))
            ttk.Label(row2, text="价格").pack(side=tk.LEFT)
            ttk.Entry(row2, textvariable=var_price, width=10).pack(side=tk.LEFT, padx=(4, 8))
            ttk.Label(row2, text="浮动% ").pack(side=tk.LEFT)
            try:
                sp_p = ttk.Spinbox(row2, from_=0.0, to=200.0, increment=0.5, textvariable=var_prem, width=8)
            except Exception:
                sp_p = tk.Spinbox(row2, from_=0.0, to=200.0, increment=0.5, textvariable=var_prem, width=8)
            sp_p.pack(side=tk.LEFT)
            ttk.Label(row2, text="模式").pack(side=tk.LEFT, padx=(12, 4))
            # 中文展示/英文存储
            var_mode_disp = tk.StringVar(value=("补货" if var_mode.get() == "restock" else "正常"))
            cmb = ttk.Combobox(row2, values=["正常", "补货"], state="readonly", textvariable=var_mode_disp, width=10)
            def _on_mode_change(_e=None):
                try:
                    v = var_mode_disp.get()
                    var_mode.set("restock" if v == "补货" else "normal")
                except Exception:
                    pass
            try:
                cmb.bind("<<ComboboxSelected>>", _on_mode_change)
            except Exception:
                pass
            cmb.pack(side=tk.LEFT)
            ttk.Label(row2, text="目标数量").pack(side=tk.LEFT, padx=(12, 4))
            try:
                sp_q = ttk.Spinbox(row2, from_=1, to=120, increment=1, textvariable=var_qty, width=8)
            except Exception:
                sp_q = tk.Spinbox(row2, from_=1, to=120, increment=1, textvariable=var_qty, width=8)
            sp_q.pack(side=tk.LEFT)
            # 移除与补货无关的说明，保持列表简洁

        # 行3：按钮
        row3 = ttk.Frame(f)
        row3.pack(fill=tk.X, padx=6, pady=(0, 8))
        def _do_save():
            name = (var_item_name.get() or "").strip()
            iid = (var_item_id.get() or "").strip()
            if not name or not iid:
                messagebox.showwarning("保存", "请先选择商品。")
                return
            item = {
                "id": str(it.get("id") or uuid.uuid4().hex),
                "enabled": bool(var_enabled.get()),
                "item_id": iid,
                "name": name,
                "image_path": (var_img.get() or ""),
                "big_category": (var_big.get() or ""),
                "price": int(var_price.get() or 0),
                "premium_pct": float(var_prem.get() or 0.0),
                "purchase_mode": str(var_mode.get() or "normal").lower(),
                "target_total": int(var_qty.get() or 0),
                "purchased": int(it.get("purchased", 0) or 0),
            }
            items = list(self.snipe_tasks_data.get("items", []) or [])
            if idx is None:
                items.append(item)
            else:
                items[idx] = item
            self.snipe_tasks_data["items"] = items
            self._snipe_editing_index = None
            self._save_snipe_tasks_data()
            if on_refresh:
                try:
                    on_refresh()
                except Exception:
                    pass
            else:
                self._render_snipe_task_cards()
        def _do_edit():
            self._snipe_editing_index = idx
            if on_refresh:
                on_refresh()
            else:
                self._render_snipe_task_cards()
        def _do_cancel():
            self._snipe_editing_index = None
            if on_refresh:
                on_refresh()
            else:
                self._render_snipe_task_cards()
        def _do_delete():
            if not messagebox.askokcancel("删除", f"确认删除 [{var_item_name.get()}]？"):
                return
            items = list(self.snipe_tasks_data.get("items", []) or [])
            if idx is not None and 0 <= idx < len(items):
                items.pop(idx)
            self.snipe_tasks_data["items"] = items
            self._snipe_editing_index = None
            self._save_snipe_tasks_data()
            if on_refresh:
                on_refresh()
            else:
                self._render_snipe_task_cards()

        if editable:
            ttk.Button(row3, text="保存", command=_do_save).pack(side=tk.LEFT)
            ttk.Button(row3, text="取消", command=_do_cancel).pack(side=tk.LEFT, padx=6)
        else:
            ttk.Button(row3, text="编辑", command=_do_edit).pack(side=tk.LEFT)
            ttk.Button(row3, text="删除", command=_do_delete).pack(side=tk.LEFT, padx=6)

    def _snipe_add_task(self) -> None:
        self._snipe_editing_index = None
        items = list(self.snipe_tasks_data.get("items", []) or [])
        draft = {
            "id": uuid.uuid4().hex,
            "enabled": True,
            "item_id": "",
            "name": "",
            "price": 0,
            "premium_pct": 0.0,
            "purchase_mode": "normal",
            "buy_qty": 1,
        }
        items.append(draft)
        self.snipe_tasks_data["items"] = items
        self._snipe_editing_index = len(items) - 1
        self._render_snipe_task_cards()

    def _open_snipe_tasks_modal(self) -> None:
        top = tk.Toplevel(self)
        top.title("配置任务")
        top.transient(self)
        # 合适大小并居中于主窗口
        try:
            self._place_modal(top, 860, 600)
        except Exception:
            try:
                top.geometry("860x600")
            except Exception:
                pass
        try:
            top.grab_set()
        except Exception:
            pass
        frm = ttk.Frame(top)
        frm.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # Top bar
        tb = ttk.Frame(frm)
        tb.pack(fill=tk.X)
        def _refresh():
            self._render_snipe_task_cards_into(cards, on_refresh=_refresh)
        def _add():
            self._snipe_editing_index = None
            items = list(self.snipe_tasks_data.get("items", []) or [])
            draft = {
                "id": uuid.uuid4().hex,
                "enabled": True,
                "item_id": "",
                "name": "",
                "price": 0,
                "premium_pct": 0.0,
                "purchase_mode": "normal",
                "target_total": 0,
                "purchased": 0,
            }
            items.append(draft)
            self.snipe_tasks_data["items"] = items
            self._snipe_editing_index = len(items) - 1
            _refresh()
        ttk.Button(tb, text="新增…", command=_add).pack(side=tk.LEFT)

        # Cards area with scrolling container
        wrap = ttk.Frame(frm)
        wrap.pack(fill=tk.BOTH, expand=True, pady=(6, 6))
        canvas = tk.Canvas(wrap, highlightthickness=0)
        vsb = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cards = ttk.Frame(canvas)
        # Create window inside canvas
        _win = canvas.create_window((0, 0), window=cards, anchor="nw")
        # Update scrollregion on size change
        def _on_cards_configure(_e=None):
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
            except Exception:
                pass
        cards.bind("<Configure>", _on_cards_configure)
        # Resize inner frame width to canvas width
        def _on_canvas_configure(e):
            try:
                canvas.itemconfigure(_win, width=e.width)
            except Exception:
                pass
        canvas.bind("<Configure>", _on_canvas_configure)
        try:
            self._bind_mousewheel(canvas, canvas)
        except Exception:
            pass
        _refresh()

        # Bottom buttons
        bf = ttk.Frame(frm)
        bf.pack(fill=tk.X)
        ttk.Button(bf, text="关闭", command=top.destroy).pack(side=tk.RIGHT)

    # ---- 多商品抢购：运行控制 ----
    def _snipe_start(self) -> None:
        if MultiSnipeRunner is None:
            detail = globals().get('_multi_import_error') or 'unknown'
            messagebox.showerror("选图片", f"失败: {e}")
            return
        if self._snipe_thread and self._snipe_thread.is_alive():
            messagebox.showinfo("启动", "任务已在运行中。")
            return
        items_raw = list(self.snipe_tasks_data.get("items", []) or [])
        # 仅传入启用任务，禁用任务不应被读取并参与执行
        try:
            items_raw = [x for x in items_raw if bool(x.get("enabled", True))]
        except Exception:
            items_raw = [x for x in items_raw if x.get("enabled", True) is True]
        if not items_raw:
            messagebox.showwarning("启动", "任务清单为空，请先通过‘配置任务’添加。")
            return
        # 启动/恢复至市场页（与购买任务前奏一致）
        try:
            res = run_launch_flow(self.cfg, on_log=lambda s: self._append_multi_log(f"【INFO】{s}"))
            if not res.ok:
                messagebox.showerror("选图片", f"失败: {e}")
                return
        except Exception as e:
            messagebox.showerror("选图片", f"失败: {e}")
            return
        # 确保进入市场页（若位于首页则点击进入）
        try:
            screen = ScreenOps(self.cfg, step_delay=0.02)
            mk = screen.locate("market_indicator", timeout=0.4)
            if mk is None:
                btn = screen.locate("btn_market", timeout=0.8)
                if btn is not None:
                    screen.click_center(btn)
        except Exception:
            pass

        # 构造运行器（日志级别由 Runner 内部控制，直接透传）
        runner = MultiSnipeRunner(self.cfg, items_raw, on_log=lambda s: self._append_multi_log(s))
        self._snipe_runner = runner
        self._snipe_stop.clear()

        def _loop():
            while not self._snipe_stop.is_set():
                try:
                    res = runner.run_once()
                except Exception as e:
                    self._append_multi_log(f"【ERROR】运行异常：{e}")
                    break
                # 汇总输出
                recs = res.get("recognized", []) or []
                for r in recs:
                    it = r.get("item")
                    # 避免日志打印禁用项（双保险）
                    try:
                        if not bool(getattr(it, 'enabled', True)):
                            continue
                    except Exception:
                        pass
                    name = getattr(it, 'name', None) or (it.get('name') if isinstance(it, dict) else '')
                    raw_txt = (r.get("price_text") or "").strip()
                    val = r.get("price_value")
                    self._append_multi_log(
                        f"【DEBUG】识别 {name}：原始='{raw_txt}' 解析={val if val is not None else '-'}"
                    )
                bought = res.get("bought", []) or []
                for b in bought:
                    self._append_multi_log(f"【INFO】购买成功：{b.get('name','')} @ {b.get('price','')} (+{b.get('inc','?')})")
                if bought:
                    try:
                        self._append_multi_purchase_records(bought)
                    except Exception:
                        pass
                    # 同步进度到任务数据（按 id 累加 purchased）
                    try:
                        items = list(self.snipe_tasks_data.get('items', []) or [])
                        idmap = {str(x.get('id')): x for x in items}
                        for b in bought:
                            iid = str(b.get('id',''))
                            inc = int(b.get('inc', 0) or 0)
                            if iid and iid in idmap:
                                try:
                                    idmap[iid]['purchased'] = int(idmap[iid].get('purchased', 0) or 0) + inc
                                except Exception:
                                    pass
                        self.snipe_tasks_data['items'] = list(idmap.values())
                        self._save_snipe_tasks_data()
                    except Exception:
                        pass
                time.sleep(0.2)

        import threading as _th
        self._snipe_thread = _th.Thread(target=_loop, daemon=True)
        self._snipe_thread.start()
        self._append_multi_log("【INFO】多商品抢购：已启动。")
        # 更新按钮状态并开始轮询线程存活
        try:
            self._update_snipe_buttons()
            self._poll_snipe_thread()
        except Exception:
            pass

    def _snipe_stop_clicked(self) -> None:
        try:
            self._snipe_stop.set()
        except Exception:
            pass
        self._append_multi_log("【INFO】多商品抢购：已请求终止。")
        try:
            self._update_snipe_buttons()
        except Exception:
            pass

    def _update_snipe_buttons(self) -> None:
        """根据线程状态切换开始/终止按钮可用性。"""
        running = bool(self._snipe_thread and self._snipe_thread.is_alive())
        try:
            self.btn_snipe_start.configure(state=(tk.DISABLED if running else tk.NORMAL))
        except Exception:
            pass
        try:
            self.btn_snipe_stop.configure(state=(tk.NORMAL if running else tk.DISABLED))
        except Exception:
            pass

    def _poll_snipe_thread(self) -> None:
        """轮询线程状态，在线程结束时恢复按钮状态。"""
        try:
            if self._snipe_thread and self._snipe_thread.is_alive():
                self.after(400, self._poll_snipe_thread)
            else:
                self._update_snipe_buttons()
        except Exception:
            pass

    def _snipe_clear_records(self) -> None:
        # 清理本模式购买记录（简单实现：删除输出文件）
        outp = os.path.join("output", "multi_snipe_purchases.json")
        try:
            if os.path.exists(outp):
                os.remove(outp)
            self._append_multi_log("【INFO】已清空本模式购买记录。")
        except Exception:
            pass

    def _append_multi_purchase_records(self, recs: List[Dict[str, Any]]) -> None:
        os.makedirs("output", exist_ok=True)
        path = os.path.join("output", "multi_snipe_purchases.json")
        try:
            import json
            data: List[Dict[str, Any]]
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    cur = json.load(f)
                    data = cur if isinstance(cur, list) else []
            else:
                data = []
            # attach timestamp
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            for r in recs:
                r = dict(r)
                r["time"] = ts
                data.append(r)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _open_snipe_tasks_editor(self) -> None:
        top = tk.Toplevel(self)
        top.title("配置任务（JSON）")
        top.transient(self)
        try:
            top.grab_set()
        except Exception:
            pass
        frm = ttk.Frame(top)
        frm.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        txt = tk.Text(frm, wrap="word")
        txt.pack(fill=tk.BOTH, expand=True)
        # init content
        try:
            import json
            txt.insert("1.0", json.dumps(self.snipe_tasks_data, ensure_ascii=False, indent=2))
        except Exception:
            txt.insert("1.0", "{\n  \"items\": []\n}")
        btnf = ttk.Frame(frm)
        btnf.pack(fill=tk.X, pady=(6, 0))
        def _save():
            try:
                import json
                data = json.loads(txt.get("1.0", tk.END))
                if not isinstance(data, dict):
                    raise ValueError("顶层必须是对象 {…}")
                data.setdefault("items", [])
                self.snipe_tasks_data = data
                self._save_snipe_tasks_data()
                messagebox.showinfo("保存", "已保存。")
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
        ttk.Button(btnf, text="保存", command=_save).pack(side=tk.RIGHT)
        ttk.Button(btnf, text="关闭", command=top.destroy).pack(side=tk.RIGHT, padx=(0, 8))


    # ---------- Tab: 利润计算 ----------
    def _build_tab_profit(self) -> None:
        outer = self.tab_profit
        pad = {"padx": 8, "pady": 8}

        # 顶部说明
        hint = ttk.Label(
            outer,
            text="输入买入价/数量/卖出价，按卖出价收取6%交易税，剩余为净收入；利润=净收入-成本。",
            foreground="#444",
        )
        hint.pack(anchor="w", **pad)

        body = ttk.Frame(outer)
        body.pack(fill=tk.BOTH, expand=True, **pad)

        # 左：输入
        lf_in = ttk.LabelFrame(body, text="输入")
        lf_in.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, **pad)

        v_buy = tk.DoubleVar(value=0.0)
        v_qty = tk.IntVar(value=1)
        v_sell = tk.DoubleVar(value=0.0)
        TAX = 0.06  # 固定税率 6%

        def _sv(entry: tk.Entry) -> None:
            try:
                entry.selection_range(0, tk.END)
                entry.icursor(tk.END)
            except Exception:
                pass

        row = 0
        ttk.Label(lf_in, text="买入价").grid(row=row, column=0, sticky="e", padx=6, pady=6)
        ent_buy = ttk.Entry(lf_in, width=12, textvariable=v_buy)
        ent_buy.grid(row=row, column=1, sticky="w", padx=6, pady=6)
        self._attach_tooltip(ent_buy, "每件的买入单价（整数或小数）")
        try:
            ent_buy.bind("<FocusIn>", lambda _e=None: _sv(ent_buy))
        except Exception:
            pass

        row += 1
        ttk.Label(lf_in, text="购买数量").grid(row=row, column=0, sticky="e", padx=6, pady=6)
        ent_qty = ttk.Entry(lf_in, width=12, textvariable=v_qty)
        ent_qty.grid(row=row, column=1, sticky="w", padx=6, pady=6)
        self._attach_tooltip(ent_qty, "购买的总数量（整数）")
        try:
            ent_qty.bind("<FocusIn>", lambda _e=None: _sv(ent_qty))
        except Exception:
            pass

        row += 1
        ttk.Label(lf_in, text="卖出价").grid(row=row, column=0, sticky="e", padx=6, pady=6)
        ent_sell = ttk.Entry(lf_in, width=12, textvariable=v_sell)
        ent_sell.grid(row=row, column=1, sticky="w", padx=6, pady=6)
        self._attach_tooltip(ent_sell, "每件的卖出单价（整数或小数）")
        try:
            ent_sell.bind("<FocusIn>", lambda _e=None: _sv(ent_sell))
        except Exception:
            pass

        row += 1
        ttk.Label(lf_in, text="卖出税率").grid(row=row, column=0, sticky="e", padx=6, pady=6)
        lbl_tax = ttk.Label(lf_in, text="6%（固定）")
        lbl_tax.grid(row=row, column=1, sticky="w", padx=6, pady=6)

        for c in range(0, 2):
            try:
                lf_in.columnconfigure(c, weight=0)
            except Exception:
                pass

        # 右：结果
        lf_out = ttk.LabelFrame(body, text="结果")
        lf_out.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, **pad)

        def fmt(n: float | int) -> str:
            try:
                v = float(n)
            except Exception:
                return "0"
            try:
                return f"{int(round(v)):,}"
            except Exception:
                return str(int(round(v)))

        out_cost = tk.StringVar(value="0")
        out_rev_g = tk.StringVar(value="0")
        out_tax = tk.StringVar(value="0")
        out_rev_n = tk.StringVar(value="0")
        out_profit_t = tk.StringVar(value="0")
        out_profit_u = tk.StringVar(value="0")
        out_margin = tk.StringVar(value="0%")
        out_breakeven = tk.StringVar(value="0")

        def recalc(*_):
            try:
                buy = max(0.0, float(v_buy.get() or 0.0))
            except Exception:
                buy = 0.0
            try:
                qty = max(0, int(v_qty.get() or 0))
            except Exception:
                qty = 0
            try:
                sell = max(0.0, float(v_sell.get() or 0.0))
            except Exception:
                sell = 0.0
            cost = buy * qty
            rev_g = sell * qty
            tax = rev_g * TAX
            rev_n = rev_g - tax
            profit_t = rev_n - cost
            profit_u = (profit_t / qty) if qty > 0 else 0.0
            margin = (profit_t / cost * 100.0) if cost > 0 else 0.0
            breakeven = buy / (1.0 - TAX) if buy > 0 else 0.0
            out_cost.set(fmt(cost))
            out_rev_g.set(fmt(rev_g))
            out_tax.set(fmt(tax))
            out_rev_n.set(fmt(rev_n))
            out_profit_t.set(fmt(profit_t))
            out_profit_u.set(fmt(profit_u))
            try:
                out_margin.set(f"{margin:.1f}%")
            except Exception:
                out_margin.set("0%")
            out_breakeven.set(fmt(breakeven))

        try:
            v_buy.trace_add("write", recalc)
            v_qty.trace_add("write", recalc)
            v_sell.trace_add("write", recalc)
        except Exception:
            pass

        r = 0
        ttk.Label(lf_out, text="总成本").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        ttk.Label(lf_out, textvariable=out_cost, foreground="#37474F").grid(row=r, column=1, sticky="w", padx=6, pady=6)
        r += 1
        ttk.Label(lf_out, text="卖出总额").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        ttk.Label(lf_out, textvariable=out_rev_g).grid(row=r, column=1, sticky="w", padx=6, pady=6)
        r += 1
        ttk.Label(lf_out, text="交易税(6%)").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        ttk.Label(lf_out, textvariable=out_tax, foreground="#C62828").grid(row=r, column=1, sticky="w", padx=6, pady=6)
        r += 1
        ttk.Label(lf_out, text="净收入").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        ttk.Label(lf_out, textvariable=out_rev_n).grid(row=r, column=1, sticky="w", padx=6, pady=6)
        r += 1
        sep = ttk.Separator(lf_out, orient=tk.HORIZONTAL)
        sep.grid(row=r, column=0, columnspan=2, sticky="ew", padx=6, pady=4)
        r += 1
        ttk.Label(lf_out, text="总利润").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        ttk.Label(lf_out, textvariable=out_profit_t, font=("", 10, "bold"), foreground="#2E7D32").grid(row=r, column=1, sticky="w", padx=6, pady=6)
        r += 1
        ttk.Label(lf_out, text="每件利润").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        ttk.Label(lf_out, textvariable=out_profit_u).grid(row=r, column=1, sticky="w", padx=6, pady=6)
        r += 1
        ttk.Label(lf_out, text="毛利率").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        ttk.Label(lf_out, textvariable=out_margin).grid(row=r, column=1, sticky="w", padx=6, pady=6)
        r += 1
        ttk.Label(lf_out, text="保本卖价(单件)").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        ttk.Label(lf_out, textvariable=out_breakeven).grid(row=r, column=1, sticky="w", padx=6, pady=6)

        # 底部操作
        bar = ttk.Frame(outer)
        bar.pack(fill=tk.X, **pad)
        def _reset():
            try:
                v_buy.set(0.0)
                v_qty.set(1)
                v_sell.set(0.0)
            except Exception:
                pass
            recalc()
        ttk.Button(bar, text="清空", command=_reset).pack(side=tk.RIGHT)
        ttk.Label(bar, text="提示：保本卖价=买入价/0.94").pack(side=tk.LEFT)

        # 初始计算
        recalc()


class GoodsMarketUI(ttk.Frame):
    """物品市场子系统

    - 管理视图（表格 + 表单 + 截图存图）
    - 浏览视图（左侧类目树 + 右侧卡片网格），布局样式参考提供的截图，仅采用布局不限定配色

    数据：`goods.json`
    图片：`images/goods/<category_en>/<uuid>.png`
    """

    def __init__(self, master) -> None:
        super().__init__(master)
        self.pack(fill=tk.BOTH, expand=True)

        self.goods_path = "goods.json"
        self.goods: list[dict[str, object]] = []

        # 显示与存储的分类映射
        self.cat_map_en: dict[str, str] = {
            "装备": "equipment",
            "武器配件": "weapon_parts",
            "武器枪机": "firearms",
            "弹药": "ammo",
            "医疗用品": "medical",
            "战术道具": "tactical",
            "钥匙": "keys",
            "杂物": "misc",
            "饮食": "food",
        }
        self.sub_map: dict[str, list[str]] = {
            "装备": [
                "头盔",
                "面罩",
                "防弹衣",
                "无甲单挂",
                "有甲弹挂",
                "背包",
                "耳机 -防毒面具",
            ],
            "武器配件": [
                "瞄具",
                "弹匣",
                "前握把",
                "后握把",
                "枪托",
                "枪口",
                "镭指器",
                "枪管",
                "护木",
                "机匣&防尘盖",
                "导轨",
                "导气箍",
                "枪栓",
                "手电",
            ],
            "武器枪机": [
                "突击步枪",
                "冲锋枪",
                "霰弹枪",
                "轻机枪",
                "栓动步枪",
                "射手步枪",
                "卡宾枪",
                "手枪",
            ],
            "弹药": [
                "5.45×39毫米子弹",
                "5.56×45毫米子弹",
                "5.7×28毫米子弹",
                "5.8×42毫米子弹",
                "7.62×25毫米子弹",
                "7.62×39毫米子弹",
                "7.62×51毫米子弹",
                "7.62×54毫米子弹",
                "9×19毫米子弹",
                "9×39毫米子弹",
                "12×70毫米子弹",
                ".44口径子弹",
                ".45口径子弹",
                ".338口径子弹",
            ],
            "医疗用品": ["药物", "伤害救治", "医疗包", "药剂"],
            "战术道具": ["投掷物"],
            "钥匙": ["农场钥匙", "北山钥匙", "山谷钥匙", "前线要塞钥匙", "电视台钥匙"],
            "杂物": [
                "易燃物品",
                "建筑材料",
                "电脑配件",
                "能源物品",
                "工具",
                "生活用品",
                "医疗杂物",
                "收藏品",
                "纸制品",
                "仪器仪表",
                "军用杂物",
                "首领信物",
                "电子产品",
            ],
            "饮食": ["饮料", "食品"],
        }

        # 浏览视图相关状态
        self._thumb_cache: dict[str, tk.PhotoImage] = {}
        # 共享路径级缓存：默认占位图等可被多物品复用，减少重复解码
        self._img_cache_by_path: dict[str, tk.PhotoImage] = {}
        self._current_big_cat: str | None = None
        self._current_sub_cat: str | None = None
        self._card_width = 220  # 单卡近似宽度（含边距）

        # 画廊刷新与分批构建的调度控制，降低频繁重建导致的卡顿
        self._gallery_refresh_after: str | None = None
        self._gallery_build_after: str | None = None
        self._gallery_build_token: int = 0
        self._last_cols: int = 0

        self._load_goods()
        self._build_views()

    # ---------- Storage ----------
    def _load_goods(self) -> None:
        try:
            import json
            if os.path.exists(self.goods_path):
                with open(self.goods_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.goods = data
                elif isinstance(data, dict) and isinstance(data.get("items"), list):
                    self.goods = list(data.get("items") or [])
                else:
                    self.goods = []
            else:
                self.goods = []
        except Exception:
            self.goods = []

    def _save_goods(self) -> None:
        try:
            import json
            with open(self.goods_path, "w", encoding="utf-8") as f:
                json.dump(self.goods, f, ensure_ascii=False, indent=4)
        except Exception:
            pass

    # ---------- Utils ----------
    def _ensure_default_img(self) -> str:
        path = os.path.join("images", "goods", "_default.png")
        try:
            if not os.path.exists(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                from PIL import Image, ImageDraw  # type: ignore

                img = Image.new("RGBA", (160, 120), (240, 240, 240, 255))
                dr = ImageDraw.Draw(img)
                dr.rectangle([(0, 0), (159, 119)], outline=(200, 200, 200), width=2)
                try:
                    f = pil_font(16)
                except Exception:
                    f = None
                if f is not None:
                    dr.text((20, 48), "No Image", fill=(120, 120, 120), font=f)
                else:
                    dr.text((20, 48), "No Image", fill=(120, 120, 120))
                img.save(path)
        except Exception:
            pass
        return path

    def _category_dir(self, big_cat: str) -> str:
        slug = self.cat_map_en.get(big_cat) or "misc"
        return os.path.join("images", "goods", slug)

    def _capture_image(self) -> str | None:
        root = self.winfo_toplevel()
        result_path: str | None = None

        def _done(bounds):
            nonlocal result_path
            if not bounds:
                return
            x1, y1, x2, y2 = bounds
            w, h = max(1, x2 - x1), max(1, y2 - y1)
            try:
                import pyautogui  # type: ignore

                img = pyautogui.screenshot(region=(x1, y1, w, h))
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return

            # decide save dir by big category
            big_cat = self.var_big_cat.get().strip() or "杂物"
            base_dir = self._category_dir(big_cat)
            os.makedirs(base_dir, exist_ok=True)
            fname = f"{uuid.uuid4().hex}.png"
            path = os.path.join(base_dir, fname)
            try:
                img.save(path)
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return
            result_path = path

        sel = _RegionSelector(root, _done)
        try:
            sel.show()
        except Exception:
            pass
        # modal-like; actual selection returns via _done then overlay destroys itself
        # We cannot block here; result_path will be set in callback.
        # Provide a small polling to wait until overlay closes
        # but keep UI responsive.
        root.wait_window(sel.top) if getattr(sel, "top", None) else None
        return result_path

    # ---------- UI: views ----------
    def _build_views(self) -> None:
        # 采用单视图：浏览 + 卡片管理（编辑/删除在模态框中完成）
        self._build_browse_tab(self)

    # ---------- UI: 浏览（侧栏 + 卡片网格） ----------
    def _build_browse_tab(self, parent) -> None:
        outer = parent
        # 左侧：搜索 + 类目树
        left = ttk.Frame(outer)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 4), pady=8)

        srow = ttk.Frame(left)
        srow.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(srow, text="搜索").pack(side=tk.LEFT)
        self.var_browse_q = tk.StringVar(value="")
        ent = ttk.Entry(srow, textvariable=self.var_browse_q, width=22)
        ent.pack(side=tk.LEFT, padx=6)
        ent.bind("<Return>", lambda _e: self._schedule_refresh_gallery(0))
        ttk.Button(srow, text="查询", command=lambda: self._schedule_refresh_gallery(0)).pack(side=tk.LEFT)

        # 类目树
        self.cat_tree = ttk.Treeview(left, show="tree", height=24)
        self.cat_tree.pack(side=tk.TOP, fill=tk.Y, expand=True, pady=(8, 0))
        # 根节点：全部
        self.cat_tree.insert("", tk.END, iid="all", text="全部")
        # 填充大类/子类
        for big, subs in self.sub_map.items():
            self.cat_tree.insert("", tk.END, iid=f"b:{big}", text=big)
            for s in subs:
                self.cat_tree.insert(f"b:{big}", tk.END, iid=f"s:{big}:{s}", text=s)
        self.cat_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_cat_select())
        try:
            self.cat_tree.selection_set("all")
        except Exception:
            pass
        # Enable wheel scroll on the category tree
        try:
            self._bind_mousewheel(self.cat_tree, self.cat_tree)
        except Exception:
            pass

        # 右侧：顶部工具条 + 滚动卡片区
        right = ttk.Frame(outer)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 8), pady=8)

        topbar = ttk.Frame(right)
        topbar.pack(side=tk.TOP, fill=tk.X)
        self.lbl_cat_title = ttk.Label(topbar, text="全部")
        self.lbl_cat_title.pack(side=tk.LEFT)
        ttk.Button(topbar, text="新增物品", command=lambda: self._open_item_modal(None)).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(topbar, text="排序").pack(side=tk.RIGHT)
        self.var_sort = tk.StringVar(value="默认")
        self.cmb_sort = ttk.Combobox(topbar, width=12, state="readonly",
                                     values=["默认", "按名称"] ,
                                     textvariable=self.var_sort)
        self.cmb_sort.pack(side=tk.RIGHT, padx=(0, 6))
        self.cmb_sort.bind("<<ComboboxSelected>>", lambda _e: self._schedule_refresh_gallery(50))

        # Canvas + Scrollbar 包裹网格卡片
        wrapper = ttk.Frame(right)
        wrapper.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(6, 0))
        self.gallery_canvas = tk.Canvas(wrapper, highlightthickness=0)
        vsb = ttk.Scrollbar(wrapper, orient=tk.VERTICAL, command=self.gallery_canvas.yview)
        self.gallery_canvas.configure(yscrollcommand=vsb.set)
        self.gallery_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.gallery_inner = ttk.Frame(self.gallery_canvas)
        self.gallery_window = self.gallery_canvas.create_window(0, 0, anchor=tk.NW, window=self.gallery_inner)

        def _on_inner_config(_e=None):
            # 更新滚动区域
            try:
                self.gallery_canvas.configure(scrollregion=self.gallery_canvas.bbox("all"))
            except Exception:
                pass

        def _on_canvas_config(_e=None):
            # 撑满宽度并重排
            try:
                w = self.gallery_canvas.winfo_width()
                self.gallery_canvas.itemconfigure(self.gallery_window, width=w)
            except Exception:
                pass
            # 仅当列数发生变化时刷新，避免频繁重建
            try:
                cols_now = max(1, int(max(1, w) // self._card_width))
            except Exception:
                cols_now = 1
            if cols_now != self._last_cols:
                self._last_cols = cols_now
                self._schedule_refresh_gallery(50)

        self.gallery_inner.bind("<Configure>", _on_inner_config)
        self.gallery_canvas.bind("<Configure>", _on_canvas_config)
        # Enable wheel scroll over the gallery area
        try:
            self._bind_mousewheel(self.gallery_inner, self.gallery_canvas)
        except Exception:
            pass

        # 初次渲染
        self.after(50, lambda: self._schedule_refresh_gallery(0))

    # ---------- 浏览事件 & 渲染 ----------
    def _on_cat_select(self) -> None:
        sel = self.cat_tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid == "all":
            self._current_big_cat = None
            self._current_sub_cat = None
            self.lbl_cat_title.configure(text="全部")
        elif iid.startswith("s:"):
            _p, big, sub = iid.split(":", 2)
            self._current_big_cat = big
            self._current_sub_cat = sub
            self.lbl_cat_title.configure(text=f"{big} / {sub}")
        elif iid.startswith("b:"):
            _p, big = iid.split(":", 1)
            self._current_big_cat = big
            self._current_sub_cat = None
            self.lbl_cat_title.configure(text=big)
        self._schedule_refresh_gallery(0)

    def _filtered_goods_for_gallery(self) -> list[dict]:
        q = (self.var_browse_q.get() or "").strip().lower()
        items = list(self.goods or [])
        res: list[dict] = []
        for it in items:
            if self._current_big_cat and str(it.get("big_category", "")) != self._current_big_cat:
                continue
            if self._current_sub_cat and str(it.get("sub_category", "")) != self._current_sub_cat:
                continue
            if q:
                name = str(it.get("name", "")).lower()
                sname = str(it.get("search_name", "")).lower()
                if q not in name and q not in sname:
                    continue
            res.append(it)

        sort = (self.var_sort.get() or "默认").strip()
        if sort == "按名称":
            res.sort(key=lambda x: str(x.get("name", "")))
        return res

    def _schedule_refresh_gallery(self, delay_ms: int = 0) -> None:
        """延迟刷新画廊，合并短时间内的多次触发，降低卡顿。"""
        try:
            if self._gallery_refresh_after:
                self.after_cancel(self._gallery_refresh_after)
        except Exception:
            pass
        self._gallery_refresh_after = self.after(max(0, int(delay_ms)), self._refresh_gallery)

    def _refresh_gallery(self) -> None:
        # 取消在途分批构建任务
        try:
            if self._gallery_build_after:
                self.after_cancel(self._gallery_build_after)
        except Exception:
            pass
        self._gallery_build_after = None

        # 清空后分批重建卡片网格，避免主线程长时间阻塞
        for w in self.gallery_inner.winfo_children():
            w.destroy()

        items = self._filtered_goods_for_gallery()
        # 估算列数
        try:
            cw = max(1, self.gallery_canvas.winfo_width())
        except Exception:
            cw = 800
        col_w = max(1, self._card_width)
        cols = max(1, cw // col_w)
        self._last_cols = cols
        # 让每列等宽
        for c in range(cols):
            try:
                self.gallery_inner.grid_columnconfigure(c, weight=1)
            except Exception:
                pass

        batch = 24
        total = len(items)
        token = self._gallery_build_token = (self._gallery_build_token + 1) % 1_000_000

        def _build(i: int) -> None:
            # 若已启动新一轮刷新，停止旧批次
            if token != self._gallery_build_token:
                return
            end = min(i + batch, total)
            for idx in range(i, end):
                it = items[idx]
                r, c = divmod(idx, cols)
                card = self._build_card(self.gallery_inner, it)
                card.grid(row=r, column=c, padx=6, pady=6, sticky="nsew")
            if end < total:
                self._gallery_build_after = self.after(0, lambda: _build(end))
            else:
                self._gallery_build_after = None

        _build(0)

    def _build_card(self, parent, it: dict) -> ttk.Frame:
        frm = ttk.Frame(parent, relief=tk.SOLID, borderwidth=1)

        # 头部：操作按钮（编辑/删除）
        head = ttk.Frame(frm)
        head.pack(side=tk.TOP, fill=tk.X)
        # 右上：快速截图（仅默认图时显示）+ 编辑/删除
        try:
            img_path = str(it.get("image_path", "")).strip()
            is_default_img = (not img_path) or (img_path == self._ensure_default_img())
        except Exception:
            is_default_img = False
        if is_default_img:
            btn_cap = ttk.Button(
                head,
                text="📷",
                width=2,
                command=lambda it_=dict(it): self._quick_capture_item_image(it_),
            )
            btn_cap.pack(side=tk.RIGHT, padx=(2, 2), pady=2)
        # 历史价格入口（右上角）
        try:
            ttk.Button(head, text="📈", width=2, command=lambda it_=dict(it): self._open_price_history_for_goods(it_)).pack(side=tk.RIGHT, padx=(2, 2), pady=2)
        except Exception:
            pass
        btn_edit = ttk.Button(head, text="✎", width=2,
                              command=lambda it_=it: self._open_item_modal(dict(it_)))
        btn_edit.pack(side=tk.RIGHT, padx=(2, 2), pady=2)
        # 按需保留“删除”仅在管理界面/编辑对话框中提供，浏览卡片不再提供删除按钮

        # 图片
        cnv = tk.Canvas(frm, width=180, height=130, bg="#f0f0f0", highlightthickness=0)
        cnv.pack(side=tk.TOP, padx=8, pady=(0, 4))
        tkimg = self._thumb_for_item(it)
        if tkimg:
            cnv.create_image(90, 65, image=tkimg)
            cnv.image = tkimg

        # 名称 + 分类
        name = str(it.get("name", ""))
        ttk.Label(frm, text=name, wraplength=200, justify=tk.LEFT).pack(side=tk.TOP, padx=8)
        cat_txt = f"{it.get('big_category','')}/{it.get('sub_category','')}".strip("/")
        ttk.Label(frm, text=cat_txt, foreground="#666").pack(side=tk.TOP, anchor="w", padx=8)

        # 底栏：最近1天统计 + 当前价（可选）
        hi, lo, avg = self._price_stats_1d(str(it.get("id", "")))
        stats = f"1d 高:{hi if hi>0 else '—'} 低:{lo if lo>0 else '—'} 均:{avg if avg>0 else '—'}"
        footer = ttk.Frame(frm)
        footer.pack(side=tk.TOP, fill=tk.X, pady=(2, 6))
        ttk.Label(footer, text=stats).pack(side=tk.LEFT, padx=8)
        
        return frm

    def _open_price_history_for_goods(self, it: dict) -> None:
        try:
            from history_store import query_price, query_price_minutely  # type: ignore
        except Exception:
            messagebox.showwarning("历史价格", "历史模块不可用。")
            return
        name = str(it.get("name", ""))
        item_id = str(it.get("id", ""))
        if not item_id:
            return
        top = tk.Toplevel(self)
        top.title(f"历史价格 - {name}")
        top.geometry("720x420")
        top.transient(self)
        try:
            top.grab_set()
        except Exception:
            pass
        # Controls
        ctrl = ttk.Frame(top)
        ctrl.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(ctrl, text="时间范围").pack(side=tk.LEFT)
        rng_var = tk.StringVar(value="近1天")
        cmb = ttk.Combobox(ctrl, textvariable=rng_var, state="readonly",
                           values=["近1小时", "近1天", "近7天", "近1月"], width=10)
        cmb.pack(side=tk.LEFT, padx=6)
        lbl_stats = ttk.Label(ctrl, text="")
        lbl_stats.pack(side=tk.RIGHT)
        # Figure area
        figf = ttk.Frame(top)
        figf.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        def _sec_for_label(s: str) -> int:
            return {
                "近1小时": 3600,
                "近1天": 86400,
                "近7天": 7 * 86400,
                "近1月": 30 * 86400,
            }.get(s, 86400)
        def _render():
            # Lazy import
            try:
                import matplotlib
                matplotlib.use("TkAgg")
                import matplotlib.pyplot as plt  # type: ignore
                from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # type: ignore
                import matplotlib.dates as mdates  # type: ignore
                import matplotlib.ticker as mtick  # type: ignore
                from datetime import datetime
                import time as _time
                try:
                    setup_matplotlib_chinese()
                except Exception:
                    pass
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return
            for w in figf.winfo_children():
                try:
                    w.destroy()
                except Exception:
                    pass
            sec = _sec_for_label(rng_var.get())
            since = _time.time() - sec
            # Prefer minutely aggregate
            x = []
            y_avg = []
            y_min = []
            y_max = []
            try:
                recs_m = query_price_minutely(item_id, since)
            except Exception:
                recs_m = []
            if recs_m:
                for r in recs_m:
                    try:
                        ts = float(r.get("ts_min", 0.0))
                        vmin = int(r.get("min", 0))
                        vmax = int(r.get("max", 0))
                        vavg = int(r.get("avg", 0))
                    except Exception:
                        continue
                    x.append(datetime.fromtimestamp(ts))
                    y_min.append(vmin)
                    y_max.append(vmax)
                    y_avg.append(vavg)
            else:
                recs = query_price(item_id, since)
                for r in recs:
                    try:
                        ts = float(r.get("ts", 0.0))
                        pr = int(r.get("price", 0))
                    except Exception:
                        continue
                    x.append(datetime.fromtimestamp(ts))
                    y_avg.append(pr)
                    y_min.append(pr)
                    y_max.append(pr)
            fig = plt.Figure(figsize=(6.4, 3.4), dpi=100)
            ax = fig.add_subplot(111)
            if x and y_avg:
                ax.plot_date(x, y_avg, "-", linewidth=1.5, label="平均价")
                try:
                    ax.fill_between(x, y_min, y_max, color="#90CAF9", alpha=0.25, label="区间[最低,最高]")
                except Exception:
                    pass
                ax.set_title(name)
                ax.set_ylabel("价格")
                ax.grid(True, linestyle=":", alpha=0.4)
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
                def _fmt_tick(v, _p):
                    try:
                        v = float(v)
                    except Exception:
                        return str(v)
                    if abs(v) >= 1_000_000:
                        return f"{v/1_000_000:.1f}M"
                    if abs(v) >= 1_000:
                        return f"{v/1_000:.1f}K"
                    try:
                        return f"{int(v):,}"
                    except Exception:
                        return str(v)
                ax.yaxis.set_major_formatter(mtick.FuncFormatter(_fmt_tick))
                fig.autofmt_xdate()
                mn = min(y_min) if y_min else 0
                mx = max(y_max) if y_max else 0
                try:
                    lbl_stats.configure(text=f"最高价: {mx:,}    最低价: {mn:,}")
                except Exception:
                    lbl_stats.configure(text=f"最高价: {mx}    最低价: {mn}")
                try:
                    ax.legend(loc="upper right")
                except Exception:
                    pass
            else:
                ax.set_title("暂无数据")
                lbl_stats.configure(text="")
            canvas = FigureCanvasTkAgg(fig, master=figf)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        cmb.bind("<<ComboboxSelected>>", lambda _e: _render())
        _render()
        btnf = ttk.Frame(top)
        btnf.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(btnf, text="关闭", command=top.destroy).pack(side=tk.RIGHT)

    def _thumb_for_item(self, it: dict) -> Optional[tk.PhotoImage]:
        iid = str(it.get("id", ""))
        path = str(it.get("image_path", "")) or self._ensure_default_img()
        if not iid:
            return None
        if iid in self._thumb_cache:
            return self._thumb_cache[iid]
        if path in self._img_cache_by_path:
            tkimg = self._img_cache_by_path[path]
            self._thumb_cache[iid] = tkimg
            return tkimg
        try:
            from PIL import Image, ImageTk  # type: ignore

            im = Image.open(path)
            im.thumbnail((180, 130))
            tkimg = ImageTk.PhotoImage(im)
        except Exception:
            return None
        self._thumb_cache[iid] = tkimg
        self._img_cache_by_path[path] = tkimg
        return tkimg

    def _quick_capture_item_image(self, item: dict) -> None:
        """在卡片上执行快速截图（与“测试”页截取样式一致）。

        - 使用卡片样式固定框 165x212（上 20 / 下 30），中间图片区域左右 30、上下 20。
        - 仅截取中间图片区域并保存到对应大类目录，更新该物品的 `image_path`。
        - 刷新画廊并清理该物品缩略图缓存。
        """
        iid = str(item.get("id", ""))
        if not iid:
            return

        root = self.winfo_toplevel()
        result_path: str | None = None

        def _done(bounds):
            nonlocal result_path
            if not bounds:
                return
            # 卡片整体坐标（根据卡片样式 165x212 推导中间图片区域）
            x1, y1, x2, y2 = bounds
            card_w, card_h = max(1, int(x2 - x1)), max(1, int(y2 - y1))
            # 固定样式：若用户改变了外框大小，仍按 165x212 的比例与边距推导中间区域
            CARD_W, CARD_H = 165, 212
            TOP_H, BTM_H = 20, 30
            MID_H = CARD_H - TOP_H - BTM_H  # 162
            MARG_LR, MARG_TB = 30, 20

            # 以左上角为基准，推导中间图片区域（不因拖拽尺寸变化而改变）
            ix = int(x1 + MARG_LR)
            iy = int(y1 + TOP_H + MARG_TB)
            iw = int(CARD_W - 2 * MARG_LR)
            ih = int(MID_H - 2 * MARG_TB)

            # 屏幕裁剪
            try:
                W, H = root.winfo_screenwidth(), root.winfo_screenheight()
            except Exception:
                W = H = 10**6
            ix = max(0, min(ix, max(0, W - 1)))
            iy = max(0, min(iy, max(0, H - 1)))
            iw = max(1, min(iw, W - ix))
            ih = max(1, min(ih, H - iy))

            try:
                import pyautogui  # type: ignore
                img = pyautogui.screenshot(region=(ix, iy, iw, ih))
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return

            # 保存到对应大类目录
            big_cat = str(item.get("big_category", "") or "杂物").strip()
            base_dir = self._category_dir(big_cat)
            try:
                os.makedirs(base_dir, exist_ok=True)
            except Exception:
                pass
            fname = f"{uuid.uuid4().hex}.png"
            path = os.path.join(base_dir, fname)
            try:
                img.save(path)
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return
            result_path = path

        # 使用与“测试”页一致的卡片选择器样式
        sel = _CardSelector(root, _done, w=165, h=212, top_h=20, bottom_h=30, margin_lr=30, margin_tb=20)
        try:
            sel.show()
        except Exception:
            pass
        root.wait_window(sel.top) if getattr(sel, "top", None) else None

        if not result_path:
            return

        # 更新该物品的图片路径
        for i, g in enumerate(self.goods):
            if str(g.get("id", "")) == iid:
                g = dict(g)
                g["image_path"] = result_path
                self.goods[i] = g
                break
        else:
            return

        # 持久化与刷新
        self._save_goods()
        try:
            self._thumb_cache.pop(iid, None)
        except Exception:
            pass
        self._schedule_refresh_gallery(0)

    # 收藏功能已移除

    # ---------- 数据：价格统计（最近1天） ----------
    def _price_stats_1d(self, iid: str) -> tuple[int, int, int]:
        if not iid:
            return 0, 0, 0
        # 简易缓存，避免频繁 I/O
        now = time.time()
        cache = getattr(self, "_price_cache", None)
        if cache is None:
            cache = {}
            self._price_cache = cache  # type: ignore
        ent = cache.get(iid) if isinstance(cache, dict) else None
        if isinstance(ent, tuple) and len(ent) == 2:
            ts, val = ent
            if now - float(ts) <= 10.0:
                return tuple(val)  # type: ignore
        try:
            from history_store import query_price  # type: ignore
        except Exception:
            return 0, 0, 0
        since = now - 24 * 3600
        try:
            recs = query_price(iid, since)
        except Exception:
            recs = []
        prices = [int(r.get("price", 0)) for r in (recs or []) if int(r.get("price", 0)) > 0]
        if not prices:
            hi = lo = avg = 0
        else:
            hi = max(prices)
            lo = min(prices)
            avg = int(round(sum(prices) / len(prices)))
        cache[iid] = (now, (hi, lo, avg))  # type: ignore
        return hi, lo, avg

    # ---------- 管理模态框 ----------
    def _open_item_modal(self, item: Optional[dict]) -> None:
        top = tk.Toplevel(self)
        top.title("物品管理")
        top.geometry("560x420")
        top.transient(self)
        try:
            top.grab_set()
        except Exception:
            pass

        # 变量（局部，不污染主界面变量）
        var_id = tk.StringVar(value=str(item.get("id", "")) if item else "")
        var_name = tk.StringVar(value=str(item.get("name", "")) if item else "")
        var_sname = tk.StringVar(value=str(item.get("search_name", "")) if item else "")
        var_big = tk.StringVar(value=str(item.get("big_category", "")) if item else "弹药")
        var_sub = tk.StringVar(value=str(item.get("sub_category", "")) if item else "")
        var_ex = tk.BooleanVar(value=bool(item.get("exchangeable", False)) if item else False)
        var_cf = tk.BooleanVar(value=bool(item.get("craftable", False)) if item else False)
        var_img = tk.StringVar(value=str(item.get("image_path", "")) if item and item.get("image_path") else self._ensure_default_img())

        # 表单布局
        frm = ttk.Frame(top)
        frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        # 图片
        row0 = ttk.Frame(frm)
        row0.grid(row=0, column=0, columnspan=4, sticky="we")
        ttk.Label(row0, text="图片").pack(side=tk.LEFT)
        cnv = tk.Canvas(row0, width=140, height=100, bg="#f0f0f0")
        cnv.pack(side=tk.LEFT, padx=8)

        def _update_preview():
            cnv.delete("all")
            p = (var_img.get() or "").strip()
            if not p or not os.path.exists(p):
                return
            try:
                from PIL import Image, ImageTk  # type: ignore

                im = Image.open(p)
                im.thumbnail((140, 100))
                tkimg = ImageTk.PhotoImage(im)
            except Exception:
                return
            cnv.image = tkimg
            cnv.create_image(0, 0, anchor=tk.NW, image=tkimg)

        def _choose_file():
            p = filedialog.askopenfilename(title="选择图片", filetypes=[["Images", "*.png;*.jpg;*.jpeg;*.bmp"]])
            if p:
                var_img.set(p)
                _update_preview()

        def _capture_to_cat():
            # 卡片样式截取（165x212；上 20 / 下 30；中间图片区域左右 30、上下 20）
            root = self.winfo_toplevel()
            result_path: str | None = None

            def _done(bounds):
                nonlocal result_path
                if not bounds:
                    return
                # 按“测试”页样式从卡片整体推导中间图片区域
                x1, y1, x2, y2 = bounds
                CARD_W, CARD_H = 165, 212
                TOP_H, BTM_H = 20, 30
                MID_H = CARD_H - TOP_H - BTM_H
                MARG_LR, MARG_TB = 30, 20

                ix = int(x1 + MARG_LR)
                iy = int(y1 + TOP_H + MARG_TB)
                iw = int(CARD_W - 2 * MARG_LR)
                ih = int(MID_H - 2 * MARG_TB)

                # 屏幕裁剪
                try:
                    W, H = root.winfo_screenwidth(), root.winfo_screenheight()
                except Exception:
                    W = H = 10**6
                ix = max(0, min(ix, max(0, W - 1)))
                iy = max(0, min(iy, max(0, H - 1)))
                iw = max(1, min(iw, W - ix))
                ih = max(1, min(ih, H - iy))

                try:
                    import pyautogui  # type: ignore
                    img = pyautogui.screenshot(region=(ix, iy, int(iw), int(ih)))
                except Exception as e:
                    messagebox.showerror("选图片", f"失败: {e}")
                    return

                # 保存到对应大类
                big_cat = var_big.get().strip() or "misc"
                base_dir = self._category_dir(big_cat)
                try:
                    os.makedirs(base_dir, exist_ok=True)
                except Exception:
                    pass
                fname = f"{uuid.uuid4().hex}.png"
                path = os.path.join(base_dir, fname)
                try:
                    img.save(path)
                except Exception as e:
                    messagebox.showerror("选图片", f"失败: {e}")
                    return
                result_path = path

            sel = _CardSelector(root, _done, w=165, h=212, top_h=20, bottom_h=30, margin_lr=30, margin_tb=20)
            try:
                sel.show()
            except Exception:
                pass
            root.wait_window(sel.top) if getattr(sel, "top", None) else None
            if result_path:
                var_img.set(result_path)
                _update_preview()

        ttk.Button(row0, text="选择图片", command=_choose_file).pack(side=tk.LEFT)
        ttk.Button(row0, text="截图", command=_capture_to_cat).pack(side=tk.LEFT, padx=6)

        ttk.Label(frm, text="名称").grid(row=1, column=0, sticky="e", padx=4, pady=6)
        ttk.Entry(frm, textvariable=var_name, width=28).grid(row=1, column=1, sticky="w")
        ttk.Label(frm, text="搜索名").grid(row=1, column=2, sticky="e", padx=4)
        ttk.Entry(frm, textvariable=var_sname, width=18).grid(row=1, column=3, sticky="w")

        ttk.Label(frm, text="大分类").grid(row=2, column=0, sticky="e", padx=4, pady=6)
        cmb_big = ttk.Combobox(frm, textvariable=var_big, state="readonly", width=14,
                               values=list(self.cat_map_en.keys()))
        cmb_big.grid(row=2, column=1, sticky="w")
        ttk.Label(frm, text="子分类").grid(row=2, column=2, sticky="e", padx=4)
        cmb_sub = ttk.Combobox(frm, textvariable=var_sub, state="readonly", width=18)
        cmb_sub.grid(row=2, column=3, sticky="w")

        def _fill_sub():
            try:
                cmb_sub.configure(values=self.sub_map.get(var_big.get().strip(), []) or [])
            except Exception:
                pass

        cmb_big.bind("<<ComboboxSelected>>", lambda _e: _fill_sub())
        _fill_sub()

        ttk.Checkbutton(frm, text="当前赛季可兑换", variable=var_ex).grid(row=3, column=0, columnspan=2, sticky="w", padx=4)
        ttk.Checkbutton(frm, text="当前赛季可制造", variable=var_cf).grid(row=3, column=2, columnspan=2, sticky="w", padx=4)

        # 操作按钮
        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=4, sticky="we", pady=10)

        def _do_save():
            name = (var_name.get() or "").strip()
            if not name:
                messagebox.showwarning("保存", "名称不能为空。")
                return
            iid = (var_id.get() or "").strip() or uuid.uuid4().hex
            item2 = {
                "id": iid,
                "name": name,
                "search_name": (var_sname.get() or "").strip(),
                "big_category": var_big.get().strip(),
                "sub_category": var_sub.get().strip(),
                "exchangeable": bool(var_ex.get()),
                "craftable": bool(var_cf.get()),
                "image_path": (var_img.get() or "").strip(),
                # 可选字段：价格（若已有则保留）
                "price": item.get("price") if item and "price" in item else None,
            }
            found = False
            for i, g in enumerate(self.goods):
                if str(g.get("id", "")) == iid:
                    self.goods[i] = item2
                    found = True
                    break
            if not found:
                self.goods.append(item2)
            self._save_goods()
            self._refresh_gallery()
            try:
                top.grab_release()
            except Exception:
                pass
            top.destroy()

        def _do_delete():
            iid = (var_id.get() or "").strip()
            if not iid:
                return
            self._delete_item(iid)
            try:
                top.grab_release()
            except Exception:
                pass
            top.destroy()

        ttk.Button(btns, text="保存", command=_do_save).pack(side=tk.RIGHT)
        ttk.Button(btns, text="取消", command=lambda: top.destroy()).pack(side=tk.RIGHT, padx=6)
        if item and item.get("id"):
            ttk.Button(btns, text="删除", command=_do_delete).pack(side=tk.LEFT)

        # 首次预览
        _update_preview()

    def _delete_item(self, iid: str) -> None:
        if not iid:
            return
        it = next((x for x in self.goods if str(x.get("id")) == iid), None)
        if not it:
            return
        if not messagebox.askokcancel("删除", f"确认删除 [{it.get('name','')}]？"):
            return
        img_path = str(it.get("image_path", ""))
        self.goods = [x for x in self.goods if str(x.get("id")) != iid]
        self._save_goods()
        self._refresh_gallery()
        if img_path and os.path.exists(img_path):
            if messagebox.askyesno("删除图片", "同时删除对应图片文件？"):
                try:
                    os.remove(img_path)
                except Exception:
                    pass

    # ---------- UI: 管理（原有表格 + 表单） ----------
    def _build_manage_tab(self, parent) -> None:
        outer = parent
        # top: search + actions
        top = ttk.Frame(outer)
        top.pack(fill=tk.X, padx=8, pady=(8, 4))
        ttk.Label(top, text="搜索").pack(side=tk.LEFT)
        self.var_q = tk.StringVar(value="")
        ent = ttk.Entry(top, textvariable=self.var_q, width=24)
        ent.pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="查询", command=self._refresh_list).pack(side=tk.LEFT)
        ttk.Button(top, text="重置", command=self._reset_search).pack(side=tk.LEFT, padx=(6, 0))

        # center: list + form
        center = ttk.Frame(outer)
        center.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        # list
        lf = ttk.Frame(center)
        lf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cols = ("name", "sname", "bcat", "scat", "exch", "craft")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", height=18)
        self.tree.heading("name", text="名称")
        self.tree.heading("sname", text="搜索名")
        self.tree.heading("bcat", text="大分类")
        self.tree.heading("scat", text="子分类")
        self.tree.heading("exch", text="可兑换")
        self.tree.heading("craft", text="可制造")
        self.tree.column("name", width=200)
        self.tree.column("sname", width=90)
        self.tree.column("bcat", width=90)
        self.tree.column("scat", width=140)
        self.tree.column("exch", width=70, anchor="center")
        self.tree.column("craft", width=70, anchor="center")
        self.tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())

        btns = ttk.Frame(lf)
        btns.pack(side=tk.TOP, fill=tk.X, pady=6)
        ttk.Button(btns, text="新增", command=self._new_item).pack(side=tk.LEFT)
        ttk.Button(btns, text="保存", command=self._save_current).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="删除", command=self._delete_selected).pack(side=tk.LEFT)

        # form
        rf = ttk.LabelFrame(center, text="物品信息")
        rf.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 0))
        # variables
        self.var_id = tk.StringVar(value="")
        self.var_name = tk.StringVar(value="")
        self.var_sname = tk.StringVar(value="")
        self.var_big_cat = tk.StringVar(value="弹药")
        self.var_sub_cat = tk.StringVar(value="")
        self.var_exch = tk.BooleanVar(value=False)
        self.var_craft = tk.BooleanVar(value=False)
        self.var_img = tk.StringVar(value=self._ensure_default_img())

        # row 0: image preview + capture
        img_row = ttk.Frame(rf)
        img_row.grid(row=0, column=0, columnspan=4, sticky="we", pady=(6, 2))
        ttk.Label(img_row, text="图片").pack(side=tk.LEFT)
        self.img_preview_canvas = tk.Canvas(img_row, width=120, height=90, bg="#f0f0f0")
        self.img_preview_canvas.pack(side=tk.LEFT, padx=8)
        ttk.Button(img_row, text="截图", command=self._on_capture_img).pack(side=tk.LEFT)
        ttk.Button(img_row, text="预览", command=lambda: self._preview_image(self.var_img.get(), "预览 - 物品图片")).pack(side=tk.LEFT, padx=6)

        ttk.Label(rf, text="名称").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        ttk.Entry(rf, textvariable=self.var_name, width=30).grid(row=1, column=1, sticky="w")
        ttk.Label(rf, text="搜索名").grid(row=1, column=2, sticky="e", padx=4)
        ttk.Entry(rf, textvariable=self.var_sname, width=16).grid(row=1, column=3, sticky="w")

        ttk.Label(rf, text="大分类").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        self.cmb_big = ttk.Combobox(rf, textvariable=self.var_big_cat, state="readonly", width=14,
                                    values=list(self.cat_map_en.keys()))
        self.cmb_big.grid(row=2, column=1, sticky="w")
        self.cmb_big.bind("<<ComboboxSelected>>", lambda _e: self._fill_subcats())
        ttk.Label(rf, text="子分类").grid(row=2, column=2, sticky="e", padx=4)
        self.cmb_sub = ttk.Combobox(rf, textvariable=self.var_sub_cat, state="readonly", width=18)
        self.cmb_sub.grid(row=2, column=3, sticky="w")

        ttk.Checkbutton(rf, text="当前赛季可兑换", variable=self.var_exch).grid(row=3, column=0, columnspan=2, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(rf, text="当前赛季可制造", variable=self.var_craft).grid(row=3, column=2, columnspan=2, sticky="w", padx=4)

        for i in range(4):
            rf.columnconfigure(i, weight=0)

        self._fill_subcats()
        self._update_img_preview()

    # ---------- Events ----------
    def _reset_search(self) -> None:
        self.var_q.set("")
        self._refresh_list()

    def _filter_goods(self) -> list[dict]:
        q = (self.var_q.get() or "").strip().lower()
        items = self.goods or []
        if not q:
            return items
        res = []
        for it in items:
            name = str(it.get("name", "")).lower()
            sname = str(it.get("search_name", "")).lower()
            if q in name or q in sname:
                res.append(it)
        return res

    def _refresh_list(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for it in self._filter_goods():
            vals = (
                str(it.get("name", "")),
                str(it.get("search_name", "")),
                str(it.get("big_category", "")),
                str(it.get("sub_category", "")),
                "是" if bool(it.get("exchangeable", False)) else "否",
                "是" if bool(it.get("craftable", False)) else "否",
            )
            self.tree.insert("", tk.END, iid=str(it.get("id", "")), values=vals)

    def _on_select(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        it = next((x for x in self.goods if str(x.get("id")) == iid), None)
        if not it:
            return
        self.var_id.set(str(it.get("id", "")))
        self.var_name.set(str(it.get("name", "")))
        self.var_sname.set(str(it.get("search_name", "")))
        self.var_big_cat.set(str(it.get("big_category", "")) or "杂物")
        self._fill_subcats()
        self.var_sub_cat.set(str(it.get("sub_category", "")))
        self.var_exch.set(bool(it.get("exchangeable", False)))
        self.var_craft.set(bool(it.get("craftable", False)))
        self.var_img.set(str(it.get("image_path", "")) or self._ensure_default_img())
        self._update_img_preview()

    def _new_item(self) -> None:
        self.var_id.set("")
        self.var_name.set("")
        self.var_sname.set("")
        self.var_big_cat.set("弹药")
        self._fill_subcats()
        self.var_sub_cat.set("")
        self.var_exch.set(False)
        self.var_craft.set(False)
        self.var_img.set(self._ensure_default_img())
        self._update_img_preview()

    def _save_current(self) -> None:
        name = (self.var_name.get() or "").strip()
        if not name:
            messagebox.showwarning("保存", "名称不能为空。")
            return
        iid = (self.var_id.get() or "").strip()
        item = {
            "id": iid or uuid.uuid4().hex,
            "name": name,
            "search_name": (self.var_sname.get() or "").strip(),
            "big_category": self.var_big_cat.get().strip(),
            "sub_category": self.var_sub_cat.get().strip(),
            "exchangeable": bool(self.var_exch.get()),
            "craftable": bool(self.var_craft.get()),
            "image_path": (self.var_img.get() or "").strip(),
        }
        existed = False
        for i, g in enumerate(self.goods):
            if str(g.get("id", "")) == item["id"]:
                self.goods[i] = item
                existed = True
                break
        if not existed:
            self.goods.append(item)
        self.var_id.set(str(item["id"]))
        self._save_goods()
        self._refresh_list()
        messagebox.showinfo("保存", "已保存。")

    def _delete_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        it = next((x for x in self.goods if str(x.get("id")) == iid), None)
        if not it:
            return
        if not messagebox.askokcancel("删除", f"确认删除 [{it.get('name','')}]？"):
            return
        img_path = str(it.get("image_path", ""))
        self.goods = [x for x in self.goods if str(x.get("id")) != iid]
        self._save_goods()
        self._refresh_list()
        self._new_item()
        if img_path and os.path.exists(img_path):
            # ask whether to delete image file
            if messagebox.askyesno("删除图片", "同时删除对应图片文件？"):
                try:
                    os.remove(img_path)
                except Exception:
                    pass

    def _fill_subcats(self) -> None:
        b = self.var_big_cat.get().strip()
        vals = self.sub_map.get(b) or []
        try:
            self.cmb_sub.configure(values=vals)
        except Exception:
            pass

    def _update_img_preview(self) -> None:
        self.img_preview_canvas.delete("all")
        path = (self.var_img.get() or "").strip()
        if not path or not os.path.exists(path):
            return
        try:
            from PIL import Image, ImageTk  # type: ignore

            im = Image.open(path)
            im.thumbnail((120, 90))
            tkimg = ImageTk.PhotoImage(im)
        except Exception:
            return
        self.img_preview_canvas.image = tkimg
        self.img_preview_canvas.create_image(0, 0, anchor=tk.NW, image=tkimg)

    def _on_capture_img(self) -> None:
        p = self._capture_image()
        if p:
            self.var_img.set(p)
            self._update_img_preview()

    # ---------- Local image preview ----------
    def _preview_image(self, path: str, title: str = "预览") -> None:
        p = (path or "").strip()
        if not p or not os.path.exists(p):
            messagebox.showwarning("预览", "图片不存在或路径为空。")
            return
        top = tk.Toplevel(self)
        top.title(title)
        top.geometry("560x420")
        top.transient(self)
        try:
            top.grab_set()
        except Exception:
            pass
        cv = tk.Canvas(top, bg="#222", highlightthickness=0)
        cv.pack(fill=tk.BOTH, expand=True)
        try:
            from PIL import Image, ImageTk  # type: ignore

            img = Image.open(p)
            max_w, max_h = 1000, 800
            img.thumbnail((max_w, max_h))
            tkimg = ImageTk.PhotoImage(img)
            cv.image = tkimg
            cv.create_image(10, 10, anchor=tk.NW, image=tkimg)
        except Exception as e:
            try:
                f = tk_font(self, 12)
            except Exception:
                f = None
            if f is not None:
                cv.create_text(10, 10, anchor=tk.NW, text=f"加载失败: {e}", fill="#ddd", font=f)
            else:
                cv.create_text(10, 10, anchor=tk.NW, text=f"加载失败: {e}", fill="#ddd")

def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()

