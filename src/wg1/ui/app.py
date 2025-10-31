import threading
import time
import tkinter as tk
import uuid
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Dict, List

from wg1.config import ConfigPaths, ensure_default_config, load_config, save_config
from wg1.core.task_runner import TaskRunner
from wg1.services.compat import ensure_pyautogui_confidence_compat
from wg1.services.font_loader import setup_matplotlib_chinese
from wg1.ui.goods_market import GoodsMarketUI
from wg1.ui.tabs.init_config import InitConfigTab
from wg1.ui.tabs.multi_snipe import MultiSnipeTab
from wg1.ui.tabs.profit import ProfitTab
from wg1.ui.widgets import LightTipManager
from wg1.ui.tabs.tasks import SingleFastBuyTab

ensure_pyautogui_confidence_compat()
_multi_import_error: str | None = None


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("基于图像识别的自动购买助手")
        self.geometry("1120x740")
        self.tip_manager = LightTipManager(self)
        # Autosave scheduler
        self._autosave_after_id: str | None = None
        self._autosave_delay_ms: int = 300

        # Config paths
        self.paths = ConfigPaths.from_root(Path.cwd())
        ensure_default_config(self.paths)
        self.config_path = self.paths.config_file
        self.images_dir = self.paths.images_dir
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.cfg: Dict[str, Any] = load_config(paths=self.paths)
        # Independent tasks store (not reusing auto-buy purchase_items)
        self.tasks_path = self.paths.root / "buy_tasks.json"
        self.tasks_data: Dict[str, Any] = self._load_tasks_data(self.tasks_path)
        # Ensure each item has a stable id for history mapping
        try:
            self._ensure_item_ids()
        except Exception:
            pass

        # UI
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True)

        self.init_tab = InitConfigTab(self, nb)
        nb.add(self.init_tab, text=self.init_tab.tab_text)

        self.fast_tab = SingleFastBuyTab(self, nb)
        self.tasks_tab = self.fast_tab
        nb.add(self.fast_tab, text=self.fast_tab.tab_text)

        # 初始化多商品抢购任务状态（需在构建多商品页前完成）
        self.snipe_tasks_path = self.paths.root / "snipe_tasks.json"
        self.snipe_tasks_data: Dict[str, Any] = self._load_snipe_tasks_data(
            self.snipe_tasks_path
        )
        self._snipe_thread = None
        self._snipe_stop = threading.Event()
        self._snipe_runner = None
        self._snipe_log_lock = threading.Lock()
        self._snipe_editing_index: int | None = None

        self.multi_tab = MultiSnipeTab(self, nb)
        nb.add(self.multi_tab, text=self.multi_tab.tab_text)

        self.profit_tab = ProfitTab(self, nb)
        nb.add(self.profit_tab, text=self.profit_tab.tab_text)

        try:
            self.goods_tab = ttk.Frame(nb)
            nb.add(self.goods_tab, text="物品市场")
            self.goods_ui = GoodsMarketUI(
                self.goods_tab,
                images_dir=self.images_dir,
                goods_path=self.paths.root / "goods.json",
            )
        except Exception:
            self.goods_ui = None

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

    # ---------- 基础工具 ----------

    def _images_path(self, *parts: str, ensure_parent: bool = False) -> str:
        path = self.images_dir.joinpath(*parts)
        if ensure_parent:
            path.parent.mkdir(parents=True, exist_ok=True)
        return str(path)

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

    def _attach_tooltip(self, widget, text_or_fn) -> None:
        """为 widget 添加悬浮提示。"""
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
            lbl = ttk.Label(
                tip, text=txt, relief=tk.SOLID, borderwidth=1, background="#ffffe0"
            )
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

    def _flow_layout(
        self, container, widgets: List[object], *, padx: int = 4, pady: int = 2
    ) -> None:
        """根据容器宽度自动换行排列控件。"""

        def _relayout(_e=None):
            try:
                width = int(container.winfo_width())
            except Exception:
                width = 0
            if width <= 1:
                try:
                    container.after(50, _relayout)
                except Exception:
                    pass
                return
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
                if curw + need > width and col > 0:
                    row += 1
                    col = 0
                    curw = 0
                try:
                    w.grid(
                        row=row, column=col, padx=(2, 2), pady=(pady, pady), sticky="w"
                    )
                except Exception:
                    pass
                curw += need
                col += 1

        try:
            container.bind("<Configure>", _relayout)
        except Exception:
            pass

    def _template_slug(self, name: str) -> str:
        tab = getattr(self, "init_tab", None)
        if tab is not None:
            try:
                return tab._template_slug(name)
            except Exception:
                pass
        return str(name or "")

    def _select_region(self, on_done):
        tab = getattr(self, "init_tab", None)
        if tab is not None:
            return tab._select_region(on_done)
        return None

    def _preview_image(self, path: str, title: str = "预览") -> None:
        tab = getattr(self, "init_tab", None)
        if tab is not None:
            tab._preview_image(path, title)
            return
        try:
            messagebox.showwarning("预览", "预览功能暂不可用。")
        except Exception:
            pass

    def _debug_test_overlay(self) -> None:
        tab = getattr(self, "init_tab", None)
        if tab is not None:
            tab._debug_test_overlay()

    def _open_goods_picker(self, on_pick) -> None:
        tab = getattr(self, "tasks_tab", None)
        if tab is not None:
            tab._open_goods_picker(on_pick)
            return
        try:
            messagebox.showwarning("选择商品", "任务标签页尚未就绪，无法选择。")
        except Exception:
            pass

    # ---------- Tasks data I/O ----------
    def _load_tasks_data(self, path: Path) -> Dict[str, Any]:
        try:
            import json

            path = Path(path)
            if path.exists():
                with path.open("r", encoding="utf-8") as f:
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
        return {
            "tasks": [],
            "step_delays": {"default": 0.01},
            "task_mode": "time",
            "restart_every_min": 60,
        }

    def _save_tasks_data(self) -> None:
        try:
            import json

            with Path(self.tasks_path).open("w", encoding="utf-8") as f:
                json.dump(self.tasks_data, f, ensure_ascii=False, indent=4)
        except Exception:
            pass

    def _load_snipe_tasks_data(self, path: Path) -> Dict[str, Any]:
        try:
            import json

            path = Path(path)
            if path.exists():
                with path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    data.setdefault("items", [])
                    return data
        except Exception:
            pass
        return {"items": []}

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

    def _on_toggle_hotkey(self, _event: tk.Event | None = None) -> None:
        """全局热键：切换任务执行的暂停状态。"""
        fast_tab = getattr(self, "fast_tab", None)
        if fast_tab is None:
            return
        try:
            fast_tab._exec_toggle_pause()
        except Exception:
            pass

    def _bind_toggle_hotkey(self, sequence: str) -> None:
        """绑定单个热键序列并记录，重复绑定自动跳过。"""
        if not sequence or sequence in self._bound_toggle_hotkeys:
            return
        try:
            self.bind_all(sequence, self._on_toggle_hotkey)
        except Exception:
            return
        self._bound_toggle_hotkeys.append(sequence)

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
        self._bind_toggle_hotkey(cfg_seq)
        self._bind_toggle_hotkey(fall_seq)

    # ---------- Autosave ----------
    def _schedule_autosave(self) -> None:
        try:
            if self._autosave_after_id is not None:
                try:
                    self.after_cancel(self._autosave_after_id)
                except Exception:
                    pass
                self._autosave_after_id = None
            self._autosave_after_id = self.after(
                self._autosave_delay_ms, self._do_autosave
            )
        except Exception:
            # Fallback to immediate save if scheduling fails
            self._do_autosave()

    def _do_autosave(self) -> None:
        self._autosave_after_id = None
        try:
            self.save_config(silent=True)
        except Exception:
            pass

    def save_config(self, *, silent: bool = False) -> None:
        """委托初始化配置标签页保存配置。"""
        tab = getattr(self, "init_tab", None)
        if tab is None:
            return
        try:
            tab._save_and_sync(silent=silent)
        except Exception:
            pass

    # ---------- Tab1 ----------
    # ---------- Tab3: OCR Lab（已移除） ----------

    # ---------- 执行日志（新逻辑） ----------
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
            if self._level_value(lvl) < self._level_value(
                self.run_log_level_var.get()
                if hasattr(self, "run_log_level_var")
                else "info"
            ):
                return
        except Exception:
            pass
        with self._log_lock:
            txt = getattr(self, "txt", None)
            if txt is None:
                try:
                    print(s)
                except Exception:
                    pass
                return
            txt.configure(state=tk.NORMAL)
            txt.insert(tk.END, time.strftime("[%H:%M:%S] ") + s + "\n")
            txt.see(tk.END)
            txt.configure(state=tk.DISABLED)

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
        return m.get(str(name or "").lower(), 20)

    # 旧自动购买相关方法已移除

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
            save_config(self.cfg, path=self.config_path)

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
        cmb = ttk.Combobox(
            ctrl,
            textvariable=rng_var,
            state="readonly",
            values=["近1小时", "近1天", "近7天", "近1月"],
            width=10,
        )
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
                ax.plot_date(x, y_avg, "-", linewidth=1.5, label="平均价")
                try:
                    ax.fill_between(
                        x,
                        y_min,
                        y_max,
                        color="#90CAF9",
                        alpha=0.25,
                        label="区间[最低,最高]",
                    )
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
                        return f"{v / 1_000_000:.1f}M"
                    if abs(v) >= 1_000:
                        return f"{v / 1_000:.1f}K"
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
            if not messagebox.askokcancel(
                "清空历史", f"确定清空 [{name}] 的历史价格记录吗？该操作不可恢复。"
            ):
                return
            removed = 0
            try:
                removed = int(clear_price_history(item_id))
            except Exception:
                pass
            messagebox.showinfo("清空历史", f"已清空 {removed} 条记录。")
            _render()

        ttk.Button(btnf, text="清空历史", command=_clear_price).pack(
            side=tk.RIGHT, padx=6
        )

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
            if not messagebox.askokcancel(
                "清空记录", f"确定清空 [{name}] 的购买记录吗？该操作不可恢复。"
            ):
                return
            removed = 0
            try:
                removed = int(clear_purchase_history(item_id))
            except Exception:
                pass
            messagebox.showinfo("清空记录", f"已清空 {removed} 条记录。")
            _reload()

        ttk.Button(btnf, text="导出CSV", command=_export_csv).pack(
            side=tk.RIGHT, padx=6
        )
        ttk.Button(btnf, text="清空记录", command=_clear_purchase).pack(
            side=tk.RIGHT, padx=6
        )

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


def run_app() -> None:
    """启动主应用。"""
    app = App()
    app.mainloop()


if __name__ == "__main__":
    run_app()
