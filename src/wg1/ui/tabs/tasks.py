
from __future__ import annotations

import os
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict

import tkinter as tk
from tkinter import messagebox, ttk

from wg1.core.task_runner import TaskRunner

from .base import BaseTab

if TYPE_CHECKING:
    from wg1.ui.app import App


class SingleFastBuyTab(BaseTab):
    """单商品极速购买模式：整合任务配置与执行控制。"""

    tab_text = "单商品极速购买模式"

    def __init__(self, app: "App", notebook: ttk.Notebook) -> None:
        super().__init__(app, notebook)
        # 兼容旧引用
        self.tab_fast = self
        self.tab_tasks = self
        self._editing_task_index: int | None = None
        self._task_draft_alive = False
        self._task_mode_radios: tuple[ttk.Radiobutton, ttk.Radiobutton] | None = None
        self.exec_log_level_var: tk.StringVar | None = None
        self.exec_txt: tk.Text | None = None
        self.btn_exec_start: ttk.Button | None = None
        self.btn_exec_pause: ttk.Button | None = None
        self.btn_exec_stop: ttk.Button | None = None
        self.lab_exec_status: ttk.Label | None = None
        self._runner: TaskRunner | None = None
        self._exec_state_after_id: str | None = None
        self.cards_canvas: tk.Canvas | None = None
        self.cards_inner: ttk.Frame | None = None
        self.cards_window: int | None = None
        self.btn_add_task: ttk.Button | None = None
        self._task_modal_top: tk.Toplevel | None = None
        self.lab_task_summary: ttk.Label | None = None
        self._build_tab_fast()

    def _build_tab_fast(self) -> None:
        outer = self.tab_fast
        container = ttk.Frame(outer)
        container.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        box_top = ttk.Frame(container)
        box_top.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(box_top, text="配置任务…", command=self._open_tasks_modal).pack(side=tk.LEFT)
        self.lab_task_summary = ttk.Label(box_top, text="", foreground="#666666")
        self.lab_task_summary.pack(side=tk.LEFT, padx=(12, 0))

        # 高级配置放置于主页面（执行控制下方）
        self._tasks_root_frame = container

        ctrl_box = ttk.LabelFrame(container, text="执行控制")
        ctrl_box.pack(fill=tk.X, padx=0, pady=(0, 8))
        ctrl_inner = ttk.Frame(ctrl_box)
        ctrl_inner.pack(fill=tk.X, padx=8, pady=6)
        self.btn_exec_start = ttk.Button(ctrl_inner, text="开始执行", command=self._exec_start)
        self.btn_exec_start.pack(side=tk.LEFT)
        self.btn_exec_pause = ttk.Button(ctrl_inner, text="暂停", command=self._exec_toggle_pause)
        self.btn_exec_pause.pack(side=tk.LEFT, padx=6)
        self.btn_exec_stop = ttk.Button(ctrl_inner, text="终止", command=self._exec_stop)
        self.btn_exec_stop.pack(side=tk.LEFT)
        self.lab_exec_status = ttk.Label(ctrl_inner, text="idle", foreground="#666")
        self.lab_exec_status.pack(side=tk.RIGHT)

        log_box = ttk.LabelFrame(container, text="执行日志")
        log_box.pack(fill=tk.BOTH, expand=True)
        log_top = ttk.Frame(log_box)
        log_top.pack(fill=tk.X, padx=8, pady=(8, 0))
        ttk.Label(log_top, text="日志等级").pack(side=tk.LEFT)
        self.exec_log_level_var = tk.StringVar(value="info")
        exec_level = ttk.Combobox(
            log_top,
            width=8,
            state="readonly",
            values=["debug", "info", "error"],
            textvariable=self.exec_log_level_var,
        )
        exec_level.pack(side=tk.LEFT, padx=6)
        try:
            exec_level.bind(
                "<<ComboboxSelected>>",
                lambda _e: (getattr(self, "_runner", None) and self._runner.set_log_level(self.exec_log_level_var.get())),
            )
        except Exception:
            pass
        self.exec_txt = tk.Text(log_box, height=14, wrap="word")
        self.exec_txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.exec_txt.configure(state=tk.DISABLED)

        # State: whether a draft card exists
        self._task_draft_alive = False
        self._render_task_cards()
        self._update_exec_controls()

    def _render_task_cards(self) -> None:
        container = getattr(self, "cards_inner", None)
        if container is None:
            try:
                self._build_step_delay_panel(self._tasks_root_frame)
            except Exception:
                pass
            self._update_task_summary()
            return
        for w in container.winfo_children():
            w.destroy()
        items = list((self.tasks_data.get("tasks", []) or []))
        try:
            items.sort(key=lambda d: (int(d.get("order", 0)) if isinstance(d, dict) else 0))
        except Exception:
            pass
        # Show existing items; if one is in editing state, render it as editable
        for i, it in enumerate(items):
            editable = (self._editing_task_index == i)
            self._build_task_card(container, i, it, editable=editable, draft=False)
        # Update add button availability: disable when a draft exists or editing an existing item
        self._task_draft_alive = any(getattr(w, "_is_draft", False) for w in container.winfo_children())
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
        self._update_task_summary()

    def _update_task_summary(self) -> None:
        label = getattr(self, "lab_task_summary", None)
        if label is None:
            return
        tasks = list((self.tasks_data.get("tasks", []) or []))
        total = len(tasks)
        try:
            enabled = sum(1 for t in tasks if bool(t.get("enabled", True)))
        except Exception:
            enabled = total
        text = f"任务数: {total}，启用: {enabled}"
        try:
            label.configure(text=text)
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
        container = getattr(self, "cards_inner", None)
        if container is None:
            return
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
        self._build_task_card(container, None, draft, editable=True, draft=True)
        self._task_draft_alive = True
        try:
            self.btn_add_task.configure(state=tk.DISABLED)
        except Exception:
            pass
        self._update_task_mode_controls_state()

    def _open_tasks_modal(self) -> None:
        existing = getattr(self, "_task_modal_top", None)
        if existing is not None and existing.winfo_exists():
            try:
                existing.lift()
                existing.focus_force()
            except Exception:
                pass
            return
        top = tk.Toplevel(self)
        top.title("配置任务")
        top.transient(self)
        try:
            self._place_modal(top, 880, 640)
        except Exception:
            try:
                top.geometry("880x640")
            except Exception:
                pass
        try:
            top.grab_set()
        except Exception:
            pass
        self._task_modal_top = top

        def _cleanup_modal() -> None:
            if getattr(self, "_task_modal_top", None) is not top:
                return
            self._task_modal_top = None
            self.cards_inner = None
            self.cards_canvas = None
            self.cards_window = None
            self.btn_add_task = None
            self._task_draft_alive = False
            self._editing_task_index = None
            self._update_task_mode_controls_state()
            self._update_task_summary()

        def _on_destroy(event) -> None:
            if event.widget is top:
                _cleanup_modal()

        def _on_close() -> None:
            if top.winfo_exists():
                top.destroy()

        top.protocol("WM_DELETE_WINDOW", _on_close)
        top.bind("<Destroy>", _on_destroy)

        frm = ttk.Frame(top)
        frm.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        tb = ttk.Frame(frm)
        tb.pack(fill=tk.X)
        self.btn_add_task = ttk.Button(tb, text="新增…", command=self._add_task_card)
        self.btn_add_task.pack(side=tk.LEFT)

        wrap = ttk.Frame(frm)
        wrap.pack(fill=tk.BOTH, expand=True, pady=(6, 6))
        canvas = tk.Canvas(wrap, highlightthickness=0)
        vsb = ttk.Scrollbar(wrap, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        cards = ttk.Frame(canvas)
        win = canvas.create_window((0, 0), window=cards, anchor="nw")

        def _on_cards_configure(_event=None) -> None:
            try:
                canvas.configure(scrollregion=canvas.bbox("all"))
            except Exception:
                pass

        def _on_canvas_configure(event) -> None:
            try:
                canvas.itemconfigure(win, width=event.width)
            except Exception:
                pass

        cards.bind("<Configure>", _on_cards_configure)
        canvas.bind("<Configure>", _on_canvas_configure)
        try:
            self._bind_mousewheel(cards, canvas)
        except Exception:
            pass

        self.cards_canvas = canvas
        self.cards_inner = cards
        self.cards_window = win

        self._task_draft_alive = False
        self._editing_task_index = None
        self._render_task_cards()

        bf = ttk.Frame(frm)
        bf.pack(fill=tk.X)
        ttk.Button(bf, text="关闭", command=_on_close).pack(side=tk.RIGHT)
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
            b = var_big.get()
            s = var_sub.get()
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
                top.destroy()
                return
            iid = sel[0]
            item = next((g for g in goods if str(g.get("id", "")) == iid or str(g.get("name", "")) == iid), None)
            if item is None:
                top.destroy()
                return
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

    # ---------- 执行控制与日志 ----------
    def _append_exec_log(self, s: str) -> None:
        try:
            import threading as _th
            if _th.current_thread() is not _th.main_thread():
                self.after(0, self._append_exec_log, s)
                return
        except Exception:
            pass
        try:
            lvl = self._parse_log_level(s)
            target_lvl = self.exec_log_level_var.get() if isinstance(self.exec_log_level_var, tk.StringVar) else "info"
            if self._level_value(lvl) < self._level_value(target_lvl):
                return
        except Exception:
            pass
        log_widget = self.exec_txt
        if log_widget is None:
            return
        with self._exec_log_lock:
            try:
                log_widget.configure(state=tk.NORMAL)
                log_widget.insert(tk.END, s + "\n")
                log_widget.see(tk.END)
            finally:
                log_widget.configure(state=tk.DISABLED)

    def _exec_is_running(self) -> bool:
        r = getattr(self, "_runner", None)
        try:
            t = getattr(r, "_thread", None)
            return bool(r and t and t.is_alive())
        except Exception:
            return False

    def _update_exec_controls(self) -> None:
        running = self._exec_is_running()
        btn_start = self.btn_exec_start
        btn_pause = self.btn_exec_pause
        btn_stop = self.btn_exec_stop
        lab_status = self.lab_exec_status
        try:
            if btn_start is not None:
                btn_start.configure(state=(tk.DISABLED if running else tk.NORMAL))
            if btn_stop is not None:
                btn_stop.configure(state=(tk.NORMAL if running else tk.DISABLED))
            paused = bool(getattr(self._runner, "_pause", None) and self._runner._pause.is_set()) if self._runner else False
            if btn_pause is not None:
                btn_pause.configure(state=(tk.NORMAL if running else tk.DISABLED), text=("继续" if paused else "暂停"))
            if lab_status is not None:
                lab_status.configure(text=("running" if running else "idle"))
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
        if self._runner is not None:
            try:
                self._exec_state_after_id = self.after(500, self._on_exec_state_tick)
            except Exception:
                pass

    def _exec_start(self) -> None:
        try:
            self._save_tasks_data()
        except Exception:
            pass
        if self.exec_txt is not None:
            try:
                self.exec_txt.configure(state=tk.NORMAL)
                self.exec_txt.delete("1.0", tk.END)
                self.exec_txt.configure(state=tk.DISABLED)
            except Exception:
                pass
        self._runner = TaskRunner(
            tasks_data=dict(self.tasks_data),
            cfg_path=self.config_path,
            goods_path=self.paths.root / "goods.json",
            output_dir=self.paths.output_dir,
            on_log=self._append_exec_log,
            on_task_update=self._on_task_exec_update,
        )
        try:
            if isinstance(self.exec_log_level_var, tk.StringVar):
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
        try:
            items = self.tasks_data.get("tasks", []) or []
            if 0 <= idx < len(items):
                items[idx]["purchased"] = int(t.get("purchased", 0) or 0)
                self._save_tasks_data()
                try:
                    if (getattr(self, "_editing_task_index", None) is None) and (not bool(self._task_draft_alive)):
                        self.after(0, self._render_task_cards)
                except Exception:
                    pass
        except Exception:
            pass

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
        var_h1 = tk.StringVar(value=(f"{h1:02d}" if ts_raw else ""))
        var_s1 = tk.StringVar(value=(f"{s1:02d}" if ts_raw else ""))
        var_h2 = tk.StringVar(value=(f"{h2:02d}" if te_raw else ""))
        var_s2 = tk.StringVar(value=(f"{s2:02d}" if te_raw else ""))

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
        lbl_name = ttk.Label(row, textvariable=var_item_name, width=18)
        widgets.append(lbl_name)
        btn_pick = ttk.Button(row, text="选择…", width=8, command=lambda: self._open_goods_picker(lambda g: (var_item_name.set(str(g.get('name',''))), var_item_id.set(str(g.get('id',''))))))
        widgets.append(btn_pick)
        widgets.append(ttk.Label(row, text="，小于"))
        ent_thr = ttk.Entry(row, textvariable=var_thr, width=8)
        widgets.append(ent_thr)
        lbl_fast = ttk.Label(row, text="的时候进行快速购买")
        widgets.append(lbl_fast)
        self._attach_tooltip(lbl_fast, "价格<=阈值时直接购买（默认数量，不调数量）")
        widgets.append(ttk.Label(row, text="，允许价格浮动"))
        ent_prem = ttk.Entry(row, textvariable=var_prem, width=5)
        widgets.append(ent_prem)
        widgets.append(ttk.Label(row, text="% ，小于"))
        ent_rest = ttk.Entry(row, textvariable=var_restock, width=8)
        widgets.append(ent_rest)
        widgets.append(ttk.Label(row, text="的时候启用补货模式（自动点击Max买满），允许补货价浮动"))
        ent_rprem = ttk.Entry(row, textvariable=var_rprem, width=5)
        widgets.append(ent_rprem)
        widgets.append(ttk.Label(row, text="% ，"))
        widgets.append(ttk.Label(row, text="一共购买"))
        ent_target = ttk.Entry(row, textvariable=var_target, width=8)
        widgets.append(ent_target)
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
            colon1 = ttk.Label(row, text=":")
            widgets.append(colon1)
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
            colon2 = ttk.Label(row, text=":")
            widgets.append(colon2)
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
                h1s = (var_h1.get() or "").strip()
                m1s = (var_s1.get() or "").strip()
                h2s = (var_h2.get() or "").strip()
                m2s = (var_s2.get() or "").strip()
                def _mk_hhmm(hs: str, ms: str) -> str | None:
                    try:
                        if hs == "" or ms == "":
                            return None
                        hh = int(hs)
                        mm = int(ms)
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
                        h = int(hh)
                        m = int(mm)
                        if 0 <= h <= 23 and 0 <= m <= 59:
                            return h*60 + m
                    except Exception:
                        return None
                    return None
                new_s = _to_min(ts)
                new_e = _to_min(te)
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
                    a1, a2 = a
                    b1, b2 = b
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
                        os = _to_min(o_ts)
                        oe = _to_min(o_te)
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
                _cancel()
                return
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
