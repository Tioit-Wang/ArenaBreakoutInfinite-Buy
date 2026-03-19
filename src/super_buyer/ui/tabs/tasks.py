
from __future__ import annotations

import os
import time
import uuid
from typing import TYPE_CHECKING, Any, Dict

import tkinter as tk
from tkinter import messagebox, ttk

from super_buyer.core.single_purchase_runner_v2 import (
    SinglePurchaseTaskRunnerV2 as TaskRunner,
)

from .base import BaseTab

if TYPE_CHECKING:
    from super_buyer.ui.app import App


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
        self.lab_task_modal_summary: ttk.Label | None = None
        self._build_tab_fast()

    def _build_tab_fast(self) -> None:
        outer = self.tab_fast
        container = ttk.Frame(outer)
        container.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        ctrl_box = self._build_section(container, "任务控制")
        ctrl = ttk.Frame(ctrl_box)
        ctrl.pack(fill=tk.X, padx=8, pady=8)
        self.btn_exec_start = ttk.Button(ctrl, text="开始任务", command=self._exec_start)
        self.btn_exec_start.pack(side=tk.LEFT)
        self.btn_exec_stop = ttk.Button(ctrl, text="终止任务", command=self._exec_stop)
        self.btn_exec_stop.pack(side=tk.LEFT, padx=6)
        ttk.Button(ctrl, text="配置任务", command=self._open_tasks_modal).pack(side=tk.LEFT, padx=(12, 0))
        self.lab_task_summary = ttk.Label(ctrl, text="", foreground="#666666")
        self.lab_task_summary.pack(side=tk.LEFT, padx=(12, 0))
        # 主界面：任务模式切换
        try:
            ttk.Label(ctrl, text="  任务模式").pack(side=tk.LEFT, padx=(12, 4))
            # 使用显示文案与内部值的映射
            self._task_mode_combo_var = tk.StringVar()
            _mode_map = {"按时间区间": "time", "轮流执行": "round"}
            _rev_map = {v: k for k, v in _mode_map.items()}
            cur_mode = str(self.tasks_data.get("task_mode", "time"))
            self._task_mode_combo_var.set(_rev_map.get(cur_mode, "按时间区间"))
            mode_combo = ttk.Combobox(
                ctrl,
                width=10,
                state="readonly",
                values=list(_mode_map.keys()),
                textvariable=self._task_mode_combo_var,
            )
            def _on_mode_selected(_e=None):
                try:
                    name = self._task_mode_combo_var.get()
                    val = _mode_map.get(name, "time")
                    if val not in ("time", "round"):
                        val = "time"
                    self.tasks_data["task_mode"] = val
                    self._sync_task_mode_ui(val)
                    self._save_tasks_data()
                    # 重新渲染任务卡片以反映字段变化
                    self._render_task_cards()
                except Exception:
                    pass
            mode_combo.bind("<<ComboboxSelected>>", _on_mode_selected)
            mode_combo.pack(side=tk.LEFT)
            self._task_mode_combo = mode_combo
        except Exception:
            self._task_mode_combo = None
        self.lab_exec_status = ttk.Label(ctrl, text="idle", foreground="#666")
        self.lab_exec_status.pack(side=tk.RIGHT)

        self._build_step_delay_panel(container)

        log_box = self._build_section(container, "执行日志", expand=True)
        log_top = ttk.Frame(log_box)
        log_top.pack(fill=tk.X, padx=8, pady=(8, 0))
        ttk.Label(log_top, text="自动展示最详细日志，界面仅保留最新 5000 条").pack(side=tk.LEFT)
        self.exec_txt = tk.Text(log_box, height=14, wrap="word")
        self.exec_txt.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        self.exec_txt.configure(state=tk.DISABLED)
        self._restore_runtime_log_widget("exec", self.exec_txt)

        # State: whether a draft card exists
        self._task_draft_alive = False
        self._render_task_cards()
        self._update_exec_controls()

    def _render_task_cards(self) -> None:
        container = getattr(self, "cards_inner", None)
        if container is None:
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
        self._update_task_summary()

    def _update_task_summary(self) -> None:
        tasks = list((self.tasks_data.get("tasks", []) or []))
        total = len(tasks)
        try:
            enabled = sum(1 for t in tasks if bool(t.get("enabled", True)))
        except Exception:
            enabled = total
        text = f"任务数: {total}，启用: {enabled}"
        for label in (
            getattr(self, "lab_task_summary", None),
            getattr(self, "lab_task_modal_summary", None),
        ):
            if label is None:
                continue
            try:
                label.configure(text=text)
            except Exception:
                pass

    def _sync_task_mode_ui(self, mode: str | None = None) -> str:
        """同步主界面下拉与弹窗单选，避免任务模式展示与真实值不一致。"""
        val = str(mode or self.tasks_data.get("task_mode", "time"))
        if val not in ("time", "round"):
            val = "time"
        combo_var = getattr(self, "_task_mode_combo_var", None)
        if isinstance(combo_var, tk.StringVar):
            disp = "轮流执行" if val == "round" else "按时间区间"
            try:
                if combo_var.get() != disp:
                    combo_var.set(disp)
            except Exception:
                pass
        modal_var = getattr(self, "_task_mode_var", None)
        if isinstance(modal_var, tk.StringVar):
            try:
                if modal_var.get() != val:
                    modal_var.set(val)
            except Exception:
                pass
        return val

    def _update_task_mode_controls_state(self) -> None:
        """Enable/disable task-mode radio buttons based on editing/draft state."""
        try:
            radios = self._task_mode_radios
        except Exception:
            radios = None
        combo = getattr(self, "_task_mode_combo", None)
        if not radios:
            # 仍需处理主界面下拉的禁用
            pass
        disable = False
        try:
            disable = bool((self._editing_task_index is not None) or self._task_draft_alive)
        except Exception:
            pass
        state = (tk.DISABLED if disable else tk.NORMAL)
        if radios:
            for rb in radios:
                try:
                    rb.configure(state=state)
                except Exception:
                    pass
        if combo is not None:
            try:
                combo.configure(state=("readonly" if not disable else tk.DISABLED))
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
            self.lab_task_modal_summary = None
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
            try:
                if self._task_draft_alive or (self._editing_task_index is not None):
                    if not messagebox.askokcancel(
                        "关闭",
                        "当前有未保存的任务编辑，关闭后这些更改会丢失，是否继续？",
                    ):
                        return
            except Exception:
                pass
            if top.winfo_exists():
                top.destroy()

        top.protocol("WM_DELETE_WINDOW", _on_close)
        top.bind("<Destroy>", _on_destroy)

        shell = self._build_modal_shell(
            top,
            title="配置单商品任务",
            description="统一管理单商品购买条件、任务模式与时间安排。",
        )
        self.lab_task_modal_summary = shell["summary"]

        tb = shell["toolbar"]
        self.btn_add_task = ttk.Button(tb, text="新增…", command=self._add_task_card)
        self.btn_add_task.pack(side=tk.LEFT)
        # 全局任务模式选择（时间区间 / 轮流执行）
        try:
            ttk.Label(tb, text="  任务模式：").pack(side=tk.LEFT, padx=(8, 0))
            self._task_mode_var = tk.StringVar(value=str(self.tasks_data.get("task_mode", "time")))
            def _on_mode_change():
                try:
                    val = str(self._task_mode_var.get() or "time")
                    if val not in ("time", "round"):
                        val = "time"
                    # 编辑/新增中时不允许切换（由 _update_task_mode_controls_state 控制）
                    self.tasks_data["task_mode"] = val
                    self._sync_task_mode_ui(val)
                    self._save_tasks_data()
                    # 重新渲染任务卡片以反映字段变化
                    self._render_task_cards()
                except Exception:
                    pass
            rb_time = ttk.Radiobutton(tb, text="按时间区间", value="time", variable=self._task_mode_var, command=_on_mode_change)
            rb_round = ttk.Radiobutton(tb, text="轮流执行", value="round", variable=self._task_mode_var, command=_on_mode_change)
            rb_time.pack(side=tk.LEFT, padx=(0, 4))
            rb_round.pack(side=tk.LEFT, padx=(0, 8))
            self._task_mode_radios = (rb_time, rb_round)
            # 轻提示：在编辑/新增时提示模式锁定
            self._task_mode_hint = ttk.Label(tb, text="", foreground="#666666")
        except Exception:
            self._task_mode_radios = None

        scroll = self._build_scrollable_canvas(shell["content"])
        self.cards_canvas = scroll["canvas"]
        self.cards_inner = scroll["inner"]
        self.cards_window = scroll["window"]

        self._task_draft_alive = False
        self._editing_task_index = None
        self._render_task_cards()

        bf = shell["footer"]
        ttk.Button(bf, text="关闭", command=_on_close).pack(side=tk.RIGHT)
        # Disable task mode radios while drafting
        try:
            self._update_task_mode_controls_state()
        except Exception:
            pass

    # ---------- Goods picker ----------
    def _open_goods_picker(self, on_pick) -> None:
        """统一委托到 App 层通用选择器（保持兼容的入口）。"""
        try:
            # 直接调用 App 的统一实现，确保使用“物品市场”的内存数据
            self.app._open_goods_picker(on_pick)
        except Exception:
            # 兜底：直接使用通用组件（避免因 app 方法不可用导致无法选择）
            try:
                from super_buyer.ui.widgets.goods_picker import open_goods_picker
                goods_mem = None
                try:
                    goods_ui = getattr(self.app, "goods_ui", None)
                    if goods_ui is not None:
                        goods_mem = list(getattr(goods_ui, "goods", []) or [])
                except Exception:
                    goods_mem = None
                open_goods_picker(self, self.paths, on_pick, goods=goods_mem)
            except Exception:
                pass

        # ---------- Step delay & timings config module ----------
    def _build_step_delay_panel(self, parent) -> None:
        # Remove previous panel if any
        old = getattr(self, "_step_panel", None)
        if old is not None:
            try:
                old.destroy()
            except Exception:
                pass
        panel = ttk.LabelFrame(parent, text="单商品运行参数")
        panel.pack(fill=tk.X, padx=4, pady=(0, 6))
        self._step_panel = panel
        sections = ttk.Frame(panel)
        sections.pack(fill=tk.X, expand=True, padx=8, pady=6)
        try:
            sections.columnconfigure(0, weight=1)
            sections.columnconfigure(1, weight=1)
        except Exception:
            pass

        box_chain = ttk.LabelFrame(sections, text="购买 / 连击")
        box_timing = ttk.LabelFrame(sections, text="OCR / 结果时序")
        for box in (box_chain, box_timing):
            try:
                box.columnconfigure(0, weight=1)
            except Exception:
                pass

        def _build_spinbox(row_parent, **kwargs):
            try:
                return ttk.Spinbox(row_parent, **kwargs)
            except Exception:
                return tk.Spinbox(row_parent, **kwargs)

        def _make_row(parent_box, row_index: int, label_text: str, build_widget, *, tooltip: str | None = None, pady=(0, 6)):
            row = ttk.Frame(parent_box)
            row.grid(row=row_index, column=0, sticky="ew", padx=8, pady=pady)
            try:
                row.columnconfigure(1, weight=1)
            except Exception:
                pass
            ttk.Label(row, text=label_text).grid(row=0, column=0, sticky="w")
            widget = build_widget(row)
            try:
                widget.grid(row=0, column=1, sticky="ew", padx=(12, 0))
            except Exception:
                widget.grid(row=0, column=1, sticky="w", padx=(12, 0))
            if tooltip:
                try:
                    hint = ttk.Label(row, text="？", foreground="#666666", width=2)
                    hint.grid(row=0, column=2, sticky="w", padx=(8, 0))
                    self._attach_tooltip(hint, tooltip)
                except Exception:
                    pass
            return widget

        layout_state = {"stacked": None}

        def _relayout_advanced_sections(event=None) -> None:
            try:
                width = int(getattr(event, "width", 0) or sections.winfo_width() or 0)
            except Exception:
                width = 0
            stacked = width < 900
            if layout_state["stacked"] is stacked:
                return
            layout_state["stacked"] = stacked
            try:
                box_chain.grid_forget()
                box_timing.grid_forget()
            except Exception:
                pass
            try:
                if stacked:
                    sections.columnconfigure(0, weight=1)
                    sections.columnconfigure(1, weight=0)
                    box_chain.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
                    box_timing.grid(row=1, column=0, columnspan=2, sticky="ew")
                else:
                    sections.columnconfigure(0, weight=1)
                    sections.columnconfigure(1, weight=1)
                    box_chain.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
                    box_timing.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
            except Exception:
                pass

        # 新：延时(ms)，默认15ms；保存到 tasks_data.advanced.delay_ms
        adv = self.tasks_data.get("advanced") if isinstance(self.tasks_data.get("advanced"), dict) else {}
        try:
            cur_ms = int((adv or {}).get("delay_ms", 15))
        except Exception:
            cur_ms = 15
        var_delay_ms = tk.IntVar(value=cur_ms)
        sp = _make_row(
            box_chain,
            0,
            "基础延时(ms)",
            lambda row: _build_spinbox(row, from_=1, to=2000, increment=1, textvariable=var_delay_ms),
            tooltip=(
                "控制基础鼠标/键盘操作的等待间隔，数值越小点击越密集。\n"
                "建议 5–20ms，过小可能在卡顿时导致点击不稳定。"
            ),
        )

        # 购买结果 / OCR / 连击时序参数统一读取自 cfg.multi_snipe_tuning
        tuning = self.cfg.get("multi_snipe_tuning") if isinstance(self.cfg.get("multi_snipe_tuning"), dict) else {}

        # 快速连击模式：是否启用
        try:
            fast_mode_cur = bool((tuning or {}).get("fast_chain_mode", True))
        except Exception:
            fast_mode_cur = True
        var_fast_mode = tk.BooleanVar(value=fast_mode_cur)
        chk_fast = _make_row(
            box_chain,
            1,
            "快速连击模式",
            lambda row: ttk.Checkbutton(row, text="启用", variable=var_fast_mode),
            tooltip=(
                "开启后一次均价识别可连续多次购买，适合短时间大量挂出的抢购场景。\n"
                "价格变化时存在多买风险，建议仅在需要极限速度的任务中开启。"
            ),
        )

        # 快速连击：每次 OCR 后最多连续购买次数
        try:
            fast_max_cur = int((tuning or {}).get("fast_chain_max", 10) or 10)
        except Exception:
            fast_max_cur = 10
        var_fast_max = tk.IntVar(value=fast_max_cur)
        sp_fast_max = _make_row(
            box_chain,
            2,
            "每轮连击上限",
            lambda row: _build_spinbox(row, from_=1, to=50, increment=1, textvariable=var_fast_max),
            tooltip=(
                "一次均价识别之后，最多允许连续购买的次数。\n"
                "建议 5–15，数值越大速度越快，但一旦价格上涨可能连续多次买入。"
            ),
        )

        # 快速连击：遮罩关闭与再次点击的间隔(ms)
        try:
            fast_interval_cur = float((tuning or {}).get("fast_chain_interval_ms", 35.0) or 35.0)
        except Exception:
            fast_interval_cur = 35.0
        var_fast_interval = tk.DoubleVar(value=fast_interval_cur)
        sp_fast_interval = _make_row(
            box_chain,
            3,
            "连击间隔(ms)",
            lambda row: _build_spinbox(row, from_=30, to=500, increment=5, textvariable=var_fast_interval),
            tooltip=(
                "关闭购买成功遮罩到再次点击购买按钮之间的间隔。\n"
                "必须 ≥30ms，建议 35–80ms。越小速度越快，但过小可能导致点击无效或被判定为脚本。"
            ),
            pady=(0, 8),
        )

        try:
            cur_buy_timeout = float((tuning or {}).get("buy_result_timeout_sec", 0.35) or 0.35)
        except Exception:
            cur_buy_timeout = 0.35
        try:
            cur_buy_step = float((tuning or {}).get("buy_result_poll_step_sec", 0.01) or 0.01)
        except Exception:
            cur_buy_step = 0.01
        try:
            cur_ocr_win = float((tuning or {}).get("ocr_round_window_sec", 0.25) or 0.25)
        except Exception:
            cur_ocr_win = 0.25
        try:
            cur_ocr_step = float((tuning or {}).get("ocr_round_step_sec", 0.015) or 0.015)
        except Exception:
            cur_ocr_step = 0.015
        try:
            cur_post_success = float((tuning or {}).get("post_success_click_sec", 0.05) or 0.05)
        except Exception:
            cur_post_success = 0.05

        # 将秒转换为毫秒以便在界面中统一使用 ms
        try:
            cur_buy_timeout_ms = int(round(cur_buy_timeout * 1000.0))
        except Exception:
            cur_buy_timeout_ms = 350
        try:
            cur_buy_step_ms = int(round(cur_buy_step * 1000.0))
        except Exception:
            cur_buy_step_ms = 10
        try:
            cur_ocr_win_ms = int(round(cur_ocr_win * 1000.0))
        except Exception:
            cur_ocr_win_ms = 250
        try:
            cur_ocr_step_ms = int(round(cur_ocr_step * 1000.0))
        except Exception:
            cur_ocr_step_ms = 15
        try:
            cur_post_success_ms = int(round(cur_post_success * 1000.0))
        except Exception:
            cur_post_success_ms = 50

        var_buy_timeout = tk.IntVar(value=cur_buy_timeout_ms)
        sp_buy_timeout = _make_row(
            box_timing,
            0,
            "结果窗口(ms)",
            lambda row: _build_spinbox(row, from_=100, to=2000, increment=10, textvariable=var_buy_timeout),
            tooltip=(
                "从点击购买到检测到“购买成功/失败”提示的最大等待时间。\n"
                "建议 300–400ms，过大浪费时间，过小可能在网络波动时漏判。"
            ),
        )

        var_buy_step = tk.IntVar(value=cur_buy_step_ms)
        sp_buy_step = _make_row(
            box_timing,
            1,
            "结果轮询步进(ms)",
            lambda row: _build_spinbox(row, from_=5, to=100, increment=1, textvariable=var_buy_step),
            tooltip=(
                "在结果窗口内每次检查成功/失败提示的间隔。\n"
                "建议约 10ms，小幅调整即可，不建议过大。"
            ),
        )

        var_ocr_win = tk.IntVar(value=cur_ocr_win_ms)
        sp_ocr_win = _make_row(
            box_timing,
            2,
            "均价识别窗口(ms)",
            lambda row: _build_spinbox(row, from_=100, to=1500, increment=10, textvariable=var_ocr_win),
            tooltip=(
                "单次均价 OCR 尝试的时间窗口。\n"
                "建议 300–500ms，过大浪费时间，过小可能在 OCR 服务抖动时频繁失败。"
            ),
        )

        var_ocr_step = tk.IntVar(value=cur_ocr_step_ms)
        sp_ocr_step = _make_row(
            box_timing,
            3,
            "均价轮询步进(ms)",
            lambda row: _build_spinbox(row, from_=5, to=100, increment=1, textvariable=var_ocr_step),
            tooltip=(
                "均价 OCR 窗口内部的调用间隔。\n"
                "建议 10–20ms，根据 OCR 服务速度微调。"
            ),
        )

        var_post_success = tk.IntVar(value=cur_post_success_ms)
        sp_post_success = _make_row(
            box_timing,
            4,
            "成功遮罩等待(ms)",
            lambda row: _build_spinbox(row, from_=30, to=500, increment=5, textvariable=var_post_success),
            tooltip=(
                "普通模式下关闭购买成功遮罩后的固定等待时间。\n"
                "快速连击模式主要使用上面的“快速连击间隔(ms)”。建议 60–120ms。"
            ),
        )

        dbg_cfg = self.cfg.get("debug") if isinstance(self.cfg.get("debug"), dict) else {}
        try:
            save_roi_cur = bool((dbg_cfg or {}).get("save_roi_on_fail", False))
        except Exception:
            save_roi_cur = False
        var_save_roi_on_fail = tk.BooleanVar(value=save_roi_cur)
        chk_save_roi = _make_row(
            box_timing,
            5,
            "单商品抓图存档",
            lambda row: ttk.Checkbutton(
                row,
                text="启用失败抓图（输出目录/roi_debug）",
                variable=var_save_roi_on_fail,
            ),
            tooltip=(
                "仅在单商品均价识别轮最终失败时，保存最近一次 ROI 原图、上下半图和二值图。\n"
                "文件写入当前输出目录下的 roi_debug，便于排查 OCR 与 ROI 配置问题。"
            ),
        )

        try:
            ttk.Label(
                box_timing,
                text="这些时序参数会同步到全局 multi_snipe_tuning。",
                foreground="#666666",
                justify=tk.LEFT,
            ).grid(row=6, column=0, sticky="w", padx=8, pady=(0, 8))
        except Exception:
            pass

        try:
            sections.bind("<Configure>", _relayout_advanced_sections)
            self.after_idle(_relayout_advanced_sections)
        except Exception:
            try:
                _relayout_advanced_sections()
            except Exception:
                pass

        def _apply_delay_ms_from_widget() -> None:
            try:
                val_ms = int(var_delay_ms.get())
            except Exception:
                return
            # Clamp to [1, 2000]
            if val_ms < 1:
                val_ms = 1
            if val_ms > 2000:
                val_ms = 2000
            # 写入 advanced.delay_ms，并为兼容旧逻辑同步 step_delays.default
            adv = self.tasks_data.setdefault("advanced", {})
            adv["delay_ms"] = int(val_ms)
            try:
                self.tasks_data.setdefault("step_delays", {})["default"] = float(val_ms) / 1000.0
            except Exception:
                pass
            self._save_tasks_data()

        def _apply_fast_chain_from_widget() -> None:
            """将快速连击配置写入 cfg.multi_snipe_tuning，并触发自动保存。"""
            try:
                mode_val = bool(var_fast_mode.get())
            except Exception:
                mode_val = True
            try:
                max_val = int(var_fast_max.get())
            except Exception:
                max_val = 10
            if max_val < 1:
                max_val = 1
            if max_val > 50:
                max_val = 50
            try:
                interval_ms = float(var_fast_interval.get())
            except Exception:
                interval_ms = 35.0
            if interval_ms < 30.0:
                interval_ms = 30.0
            if interval_ms > 500.0:
                interval_ms = 500.0
            try:
                cfg = dict(self.app.cfg)
            except Exception:
                cfg = self.app.cfg
            tuning = cfg.get("multi_snipe_tuning") if isinstance(cfg.get("multi_snipe_tuning"), dict) else {}
            if not isinstance(tuning, dict):
                tuning = {}
            tuning["fast_chain_mode"] = bool(mode_val)
            tuning["fast_chain_max"] = int(max_val)
            tuning["fast_chain_interval_ms"] = float(interval_ms)
            cfg["multi_snipe_tuning"] = tuning
            try:
                adv = self.tasks_data.get("advanced")
                if isinstance(adv, dict):
                    adv.pop("fast_chain_mode", None)
                    adv.pop("fast_chain_max", None)
                    adv.pop("fast_chain_interval_ms", None)
            except Exception:
                pass
            try:
                from super_buyer.config import save_config  # type: ignore
                save_config(cfg, path=self.config_path)
                self.app.cfg = cfg
                try:
                    self.__dict__.pop("cfg", None)
                except Exception:
                    pass
            except Exception:
                pass
            self._save_tasks_data()

        def _apply_timing_from_widget() -> None:
            """将购买结果与 OCR 时序参数写回 cfg.multi_snipe_tuning 并保存到配置文件。"""
            try:
                cfg = dict(self.app.cfg)
            except Exception:
                cfg = self.app.cfg
            tuning = cfg.get("multi_snipe_tuning") if isinstance(cfg.get("multi_snipe_tuning"), dict) else {}
            if not isinstance(tuning, dict):
                tuning = {}
            try:
                timeout_ms = float(var_buy_timeout.get())
                tuning["buy_result_timeout_sec"] = max(0.05, timeout_ms / 1000.0)
            except Exception:
                pass
            try:
                step_ms = float(var_buy_step.get())
                tuning["buy_result_poll_step_sec"] = max(0.001, step_ms / 1000.0)
            except Exception:
                pass
            try:
                win_ms = float(var_ocr_win.get())
                tuning["ocr_round_window_sec"] = max(0.05, win_ms / 1000.0)
            except Exception:
                pass
            try:
                ostep_ms = float(var_ocr_step.get())
                tuning["ocr_round_step_sec"] = max(0.001, ostep_ms / 1000.0)
            except Exception:
                pass
            try:
                post_ms = float(var_post_success.get())
                tuning["post_success_click_sec"] = max(0.03, post_ms / 1000.0)
            except Exception:
                pass
            cfg["multi_snipe_tuning"] = tuning
            try:
                from super_buyer.config import save_config  # type: ignore
                save_config(cfg, path=self.config_path)
                # 同步到内存态 cfg，避免重启前不一致
                self.app.cfg = cfg
                try:
                    self.__dict__.pop("cfg", None)
                except Exception:
                    pass
            except Exception:
                pass

        def _apply_debug_from_widget() -> None:
            """将单商品失败抓图开关写回 cfg.debug 并保存到配置文件。"""
            try:
                cfg = dict(self.app.cfg)
            except Exception:
                cfg = self.app.cfg
            debug_cfg = cfg.get("debug") if isinstance(cfg.get("debug"), dict) else {}
            if not isinstance(debug_cfg, dict):
                debug_cfg = {}
            try:
                debug_cfg["save_roi_on_fail"] = bool(var_save_roi_on_fail.get())
            except Exception:
                debug_cfg["save_roi_on_fail"] = False
            cfg["debug"] = debug_cfg
            try:
                from super_buyer.config import save_config  # type: ignore
                save_config(cfg, path=self.config_path)
                self.app.cfg = cfg
                try:
                    self.__dict__.pop("cfg", None)
                except Exception:
                    pass
            except Exception:
                pass

        # Real-time save: on value change, focus out, and Enter
        try:
            var_delay_ms.trace_add("write", lambda *_: _apply_delay_ms_from_widget())
        except Exception:
            pass
        try:
            sp.bind("<FocusOut>", lambda _e=None: _apply_delay_ms_from_widget())
            sp.bind("<Return>", lambda _e=None: _apply_delay_ms_from_widget())
        except Exception:
            pass
        # 快速连击参数的实时保存
        try:
            var_fast_mode.trace_add("write", lambda *_: _apply_fast_chain_from_widget())
        except Exception:
            pass
        try:
            var_fast_max.trace_add("write", lambda *_: _apply_fast_chain_from_widget())
        except Exception:
            pass
        try:
            var_fast_interval.trace_add("write", lambda *_: _apply_fast_chain_from_widget())
        except Exception:
            pass
        try:
            chk_fast.bind("<Return>", lambda _e=None: _apply_fast_chain_from_widget())
        except Exception:
            pass
        try:
            var_save_roi_on_fail.trace_add("write", lambda *_: _apply_debug_from_widget())
        except Exception:
            pass
        try:
            chk_save_roi.bind("<Return>", lambda _e=None: _apply_debug_from_widget())
        except Exception:
            pass

        # 时序参数：在组件失焦或回车时保存（避免频繁写文件）
        try:
            for w in (sp_buy_timeout, sp_buy_step, sp_ocr_win, sp_ocr_step, sp_post_success):
                w.bind("<FocusOut>", lambda _e=None: _apply_timing_from_widget())
                w.bind("<Return>", lambda _e=None: _apply_timing_from_widget())
        except Exception:
            pass

    # ---------- 执行控制与日志 ----------
    def _append_exec_log(self, s: str) -> None:
        self._append_runtime_log("exec", s, widget=self.exec_txt, lock=self._exec_log_lock)

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
            # v2 Runner 仅保留开始/终止；暂停控件在 UI 隐藏，不更新
            if btn_pause is not None:
                try:
                    btn_pause.pack_forget()
                except Exception:
                    pass
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
        self._runner = TaskRunner(
            tasks_data=dict(self.tasks_data),
            cfg_path=self.config_path,
            goods_path=self.paths.root / "goods.json",
            output_dir=self.paths.output_dir,
            on_log=self._append_exec_log,
            on_task_update=self._on_task_exec_update,
        )
        self._append_exec_log("【%s】【全局】【-】：开始执行" % time.strftime("%H:%M:%S"))
        self._runner.start()
        self._update_exec_controls()
        self._schedule_exec_state_poll()


    def _exec_toggle_pause(self) -> None:
        # v2 Runner 不支持暂停/继续，此函数保留以兼容热键/旧入口，但不执行任何动作。
        try:
            self._append_exec_log("【%s】【全局】【-】：v2模式不支持暂停/继续" % time.strftime("%H:%M:%S"))
        except Exception:
            pass

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
        mode = str(self.tasks_data.get("task_mode", "time"))

        def _safe_int(value: Any, default: int = 0) -> int:
            try:
                return int(value or 0)
            except Exception:
                return default

        def _safe_float(value: Any, default: float = 0.0) -> float:
            try:
                return float(value or 0.0)
            except Exception:
                return default

        def _safe_var_value(var: tk.Variable, default: Any = "") -> Any:
            try:
                return var.get()
            except tk.TclError:
                try:
                    name = getattr(var, "_name", None) or str(var)
                    return var._tk.globalgetvar(name)  # type: ignore[attr-defined]
                except Exception:
                    return default
            except Exception:
                return default

        def _fmt_num(value: int) -> str:
            try:
                return f"{int(value):,}"
            except Exception:
                return str(value)

        def _fmt_pct(value: Any) -> str:
            num = _safe_float(value)
            if float(num).is_integer():
                return str(int(num))
            return f"{num:.2f}".rstrip("0").rstrip(".")

        def _price_summary(base_var: tk.Variable, prem_var: tk.Variable) -> str:
            base = _safe_int(_safe_var_value(base_var))
            prem = max(0.0, _safe_float(_safe_var_value(prem_var)))
            if base <= 0:
                return "未设置"
            limit = base + int(round(base * prem / 100.0))
            return f"{_fmt_num(base)}，浮动 {_fmt_pct(prem)}%，上限 {_fmt_num(limit)}"

        def _time_text(hh_var: tk.StringVar, mm_var: tk.StringVar) -> str:
            hh = str(_safe_var_value(hh_var) or "").strip()
            mm = str(_safe_var_value(mm_var) or "").strip()
            if hh == "" or mm == "":
                return "--:--"
            return f"{hh.zfill(2)}:{mm.zfill(2)}"

        def _schedule_summary() -> str:
            if mode == "round":
                dur = max(1, _safe_int(_safe_var_value(var_duration), 10))
                return f"轮流执行，每次运行 {dur} 分钟"
            start = _time_text(var_h1, var_s1)
            end = _time_text(var_h2, var_s2)
            if start == "--:--" or end == "--:--":
                return "按时间区间执行，开始/结束时间待设置"
            return f"按时间区间执行：{start} - {end}"

        def _binding_summary() -> str:
            if str(_safe_var_value(var_item_id) or "").strip():
                return "已绑定物品库商品"
            return "请选择物品库中的商品，保存时必须绑定 item_id"

        var_title = tk.StringVar()
        var_item_display = tk.StringVar()
        var_target_display = tk.StringVar()
        var_normal_summary = tk.StringVar()
        var_restock_summary = tk.StringVar()
        var_schedule_display = tk.StringVar()
        var_binding_display = tk.StringVar()
        var_price_hint = tk.StringVar()

        def _refresh_card_texts(*_args) -> None:
            name = str(_safe_var_value(var_item_name) or "").strip()
            item_text = name or "未选择商品"
            var_title.set(f"商品：{item_text}")
            var_item_display.set(item_text)
            var_target_display.set(f"{_safe_int(_safe_var_value(var_target))} 个")
            var_normal_summary.set(_price_summary(var_thr, var_prem))
            var_restock_summary.set(_price_summary(var_restock, var_rprem))
            var_schedule_display.set(_schedule_summary())
            var_binding_display.set(_binding_summary())
            var_price_hint.set(
                "普通购买：%s\n补货模式：%s"
                % (_price_summary(var_thr, var_prem), _price_summary(var_restock, var_rprem))
            )

        _refresh_card_texts()
        for _var in (var_item_name, var_item_id, var_thr, var_prem, var_restock, var_rprem, var_target, var_h1, var_s1, var_h2, var_s2, var_duration):
            try:
                _var.trace_add("write", lambda *_args: _refresh_card_texts())
            except Exception:
                pass

        try:
            order_num = (int(it.get("order", idx if idx is not None else 0)) if isinstance(it, dict) else (idx or 0)) + 1
        except Exception:
            order_num = (idx or 0) + 1

        row_header = ttk.Frame(card)
        row_header.pack(fill=tk.X, padx=8, pady=(8, 4))
        chk = ttk.Checkbutton(row_header, text="启用", variable=var_enabled, onvalue=1, offvalue=0)
        try:
            chk.pack(side=tk.LEFT)
        except Exception:
            pass
        if not editable:
            def _on_toggle_enable_immediate() -> None:
                try:
                    it["enabled"] = bool(_safe_int(_safe_var_value(var_enabled), 0) != 0)
                    self._save_tasks_data()
                except Exception:
                    pass
            try:
                chk.configure(command=_on_toggle_enable_immediate)
            except Exception:
                pass
        ttk.Label(row_header, text=f"顺序：{order_num}").pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(row_header, textvariable=var_title).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(
            row_header,
            text=("模式：轮流执行" if mode == "round" else "模式：按时间区间"),
            foreground="#666666",
        ).pack(side=tk.RIGHT)

        ent_item_name = ent_thr = ent_prem = ent_rest = ent_rprem = ent_target = None
        btn_pick = ent_dur = None
        sp_h1 = sp_s1 = sp_h2 = sp_s2 = None

        def _pick_goods() -> None:
            self._open_goods_picker(
                lambda g: (
                    var_item_name.set(str(g.get("name", ""))),
                    var_item_id.set(str(g.get("id", ""))),
                )
            )

        if editable:
            box_basic = ttk.LabelFrame(card, text="基本信息")
            box_basic.pack(fill=tk.X, padx=8, pady=(0, 6))
            try:
                box_basic.columnconfigure(1, weight=1)
            except Exception:
                pass
            ttk.Label(box_basic, text="商品").grid(row=0, column=0, sticky="e", padx=6, pady=6)
            ent_item_name = ttk.Entry(box_basic, textvariable=var_item_name, state="readonly")
            ent_item_name.grid(row=0, column=1, sticky="we", padx=6, pady=6)
            btn_pick = ttk.Button(box_basic, text="选择…", width=10, command=_pick_goods)
            btn_pick.grid(row=0, column=2, padx=(0, 6), pady=6)
            ttk.Label(box_basic, text="绑定状态").grid(row=1, column=0, sticky="ne", padx=6, pady=(0, 6))
            ttk.Label(
                box_basic,
                textvariable=var_binding_display,
                foreground="#666666",
                justify=tk.LEFT,
            ).grid(row=1, column=1, columnspan=2, sticky="w", padx=6, pady=(0, 6))

            box_price = ttk.LabelFrame(card, text="价格与目标")
            box_price.pack(fill=tk.X, padx=8, pady=(0, 6))
            ttk.Label(box_price, text="普通阈值").grid(row=0, column=0, sticky="e", padx=6, pady=6)
            ent_thr = ttk.Entry(box_price, textvariable=var_thr, width=10)
            ent_thr.grid(row=0, column=1, sticky="w", padx=6, pady=6)
            ttk.Label(box_price, text="普通浮动(%)").grid(row=0, column=2, sticky="e", padx=6, pady=6)
            ent_prem = ttk.Entry(box_price, textvariable=var_prem, width=8)
            ent_prem.grid(row=0, column=3, sticky="w", padx=6, pady=6)
            ttk.Label(box_price, text="目标总数").grid(row=0, column=4, sticky="e", padx=6, pady=6)
            ent_target = ttk.Entry(box_price, textvariable=var_target, width=10)
            ent_target.grid(row=0, column=5, sticky="w", padx=6, pady=6)
            ttk.Label(box_price, text="补货阈值").grid(row=1, column=0, sticky="e", padx=6, pady=(0, 6))
            ent_rest = ttk.Entry(box_price, textvariable=var_restock, width=10)
            ent_rest.grid(row=1, column=1, sticky="w", padx=6, pady=(0, 6))
            ttk.Label(box_price, text="补货浮动(%)").grid(row=1, column=2, sticky="e", padx=6, pady=(0, 6))
            ent_rprem = ttk.Entry(box_price, textvariable=var_rprem, width=8)
            ent_rprem.grid(row=1, column=3, sticky="w", padx=6, pady=(0, 6))
            ttk.Label(
                box_price,
                textvariable=var_price_hint,
                foreground="#666666",
                justify=tk.LEFT,
            ).grid(row=2, column=0, columnspan=6, sticky="w", padx=6, pady=(0, 6))

            box_exec = ttk.LabelFrame(card, text="执行设置")
            box_exec.pack(fill=tk.X, padx=8, pady=(0, 4))
            if mode == "round":
                ttk.Label(box_exec, text="执行时长(分钟)").grid(row=0, column=0, sticky="e", padx=6, pady=6)
                try:
                    ent_dur = ttk.Spinbox(box_exec, from_=1, to=1440, increment=1, width=8, textvariable=var_duration)
                except Exception:
                    ent_dur = tk.Spinbox(box_exec, from_=1, to=1440, increment=1, width=8, textvariable=var_duration)
                ent_dur.grid(row=0, column=1, sticky="w", padx=6, pady=6)
                ttk.Label(box_exec, textvariable=var_schedule_display, foreground="#666666").grid(
                    row=1, column=0, columnspan=2, sticky="w", padx=6, pady=(0, 6)
                )
            else:
                hours_vals = [f"{i:02d}" for i in range(24)]
                mins_vals = [f"{i:02d}" for i in range(60)]
                ttk.Label(box_exec, text="开始时间").grid(row=0, column=0, sticky="e", padx=6, pady=6)
                frm_start = ttk.Frame(box_exec)
                frm_start.grid(row=0, column=1, sticky="w", padx=6, pady=6)
                try:
                    sp_h1 = ttk.Combobox(frm_start, width=3, values=hours_vals, textvariable=var_h1, state="readonly")
                except Exception:
                    sp_h1 = ttk.Entry(frm_start, width=3, textvariable=var_h1)
                sp_h1.pack(side=tk.LEFT)
                ttk.Label(frm_start, text=":").pack(side=tk.LEFT, padx=2)
                try:
                    sp_s1 = ttk.Combobox(frm_start, width=3, values=mins_vals, textvariable=var_s1, state="readonly")
                except Exception:
                    sp_s1 = ttk.Entry(frm_start, width=3, textvariable=var_s1)
                sp_s1.pack(side=tk.LEFT)
                ttk.Label(box_exec, text="结束时间").grid(row=0, column=2, sticky="e", padx=6, pady=6)
                frm_end = ttk.Frame(box_exec)
                frm_end.grid(row=0, column=3, sticky="w", padx=6, pady=6)
                try:
                    sp_h2 = ttk.Combobox(frm_end, width=3, values=hours_vals, textvariable=var_h2, state="readonly")
                except Exception:
                    sp_h2 = ttk.Entry(frm_end, width=3, textvariable=var_h2)
                sp_h2.pack(side=tk.LEFT)
                ttk.Label(frm_end, text=":").pack(side=tk.LEFT, padx=2)
                try:
                    sp_s2 = ttk.Combobox(frm_end, width=3, values=mins_vals, textvariable=var_s2, state="readonly")
                except Exception:
                    sp_s2 = ttk.Entry(frm_end, width=3, textvariable=var_s2)
                sp_s2.pack(side=tk.LEFT)
                ttk.Label(box_exec, textvariable=var_schedule_display, foreground="#666666").grid(
                    row=1, column=0, columnspan=4, sticky="w", padx=6, pady=(0, 6)
                )

            try:
                if ent_thr is not None:
                    self._attach_tooltip(ent_thr, "价格 <= 普通阈值时直接购买（默认数量，不调数量）。")
                if ent_prem is not None:
                    self._attach_tooltip(ent_prem, lambda: "普通购买允许上浮百分比。\n%s" % var_price_hint.get())
                if ent_rest is not None:
                    self._attach_tooltip(ent_rest, lambda: "价格 <= 补货阈值时启用补货模式（自动点击 Max）。\n%s" % var_price_hint.get())
                if ent_rprem is not None:
                    self._attach_tooltip(ent_rprem, lambda: "补货模式允许上浮百分比。\n%s" % var_price_hint.get())
            except Exception:
                pass
        else:
            summary = ttk.Frame(card)
            summary.pack(fill=tk.X, padx=8, pady=(0, 4))
            try:
                summary.columnconfigure(1, weight=1)
                summary.columnconfigure(3, weight=1)
            except Exception:
                pass
            ttk.Label(summary, text="商品").grid(row=0, column=0, sticky="e", padx=6, pady=4)
            ttk.Label(summary, textvariable=var_item_display).grid(row=0, column=1, sticky="w", padx=6, pady=4)
            ttk.Label(summary, text="目标").grid(row=0, column=2, sticky="e", padx=6, pady=4)
            ttk.Label(summary, textvariable=var_target_display).grid(row=0, column=3, sticky="w", padx=6, pady=4)
            ttk.Label(summary, text="普通购买").grid(row=1, column=0, sticky="ne", padx=6, pady=4)
            ttk.Label(summary, textvariable=var_normal_summary, justify=tk.LEFT).grid(
                row=1, column=1, sticky="w", padx=6, pady=4
            )
            ttk.Label(summary, text="补货模式").grid(row=1, column=2, sticky="ne", padx=6, pady=4)
            ttk.Label(summary, textvariable=var_restock_summary, justify=tk.LEFT).grid(
                row=1, column=3, sticky="w", padx=6, pady=4
            )
            ttk.Label(summary, text="执行设置").grid(row=2, column=0, sticky="ne", padx=6, pady=4)
            ttk.Label(summary, textvariable=var_schedule_display, justify=tk.LEFT).grid(
                row=2, column=1, sticky="w", padx=6, pady=4
            )
            ttk.Label(summary, text="绑定状态").grid(row=2, column=2, sticky="ne", padx=6, pady=4)
            ttk.Label(summary, textvariable=var_binding_display, foreground="#666666", justify=tk.LEFT).grid(
                row=2, column=3, sticky="w", padx=6, pady=4
            )

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
                        return f"进度：{cur_pur}/{_safe_int(_safe_var_value(var_target), 0)}"
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
                                _ = clear_purchase_history(str(_safe_var_value(var_item_id) or ""))
                            except Exception:
                                pass
                            # 更新本地显示并重渲染以同步
                            var_prog.set(f"进度：0/{_safe_int(_safe_var_value(var_target), 0)}")
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
            name = str(_safe_var_value(var_item_name) or "").strip()
            if not name:
                messagebox.showwarning("保存", "请先选择‘购买物品’。")
                return
            # Compose record (time fields handled per mode below)
            rec: Dict[str, Any] = {
                "enabled": bool(_safe_int(_safe_var_value(var_enabled), 0) != 0),
                "item_name": name,
                "item_id": str(_safe_var_value(var_item_id) or ""),
                "price_threshold": _safe_int(_safe_var_value(var_thr), 0),
                "price_premium_pct": _safe_float(_safe_var_value(var_prem), 0.0),
                "restock_price": _safe_int(_safe_var_value(var_restock), 0),
                # 新增：补货模式的价格浮动百分比
                "restock_premium_pct": _safe_float(_safe_var_value(var_rprem), 0.0),
                "target_total": _safe_int(_safe_var_value(var_target), 0),
                "duration_min": _safe_int(_safe_var_value(var_duration), 10),
            }
            if not rec["item_id"]:
                messagebox.showwarning("保存", "必须通过‘选择…’绑定 goods.json 的物品（缺少 item_id）。")
                return
            # Validation depends on task mode
            mode_now = str(self.tasks_data.get("task_mode", "time"))
            if mode_now == "time":
                # Build and validate HH:MM inputs; both required
                h1s = str(_safe_var_value(var_h1) or "").strip()
                m1s = str(_safe_var_value(var_s1) or "").strip()
                h2s = str(_safe_var_value(var_h2) or "").strip()
                m2s = str(_safe_var_value(var_s2) or "").strip()
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

        # Keep a strong reference to Tk variables to avoid GC issues (checkbox display)
        # 保持 Tk 变量引用，避免预览文本与复选框状态被回收
        card._vars = (
            var_enabled,
            var_item_name,
            var_item_id,
            var_thr,
            var_prem,
            var_restock,
            var_rprem,
            var_target,
            var_h1,
            var_s1,
            var_h2,
            var_s2,
            var_duration,
            var_title,
            var_item_display,
            var_target_display,
            var_normal_summary,
            var_restock_summary,
            var_schedule_display,
            var_binding_display,
            var_price_hint,
        )  # type: ignore
