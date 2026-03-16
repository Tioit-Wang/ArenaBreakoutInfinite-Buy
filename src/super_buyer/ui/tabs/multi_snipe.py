
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List

import tkinter as tk
from tkinter import messagebox, ttk

from super_buyer.core.launcher import run_launch_flow
from super_buyer.core.multi_snipe import MultiSnipeRunner
from super_buyer.services.screen_ops import ScreenOps
from super_buyer.ui.widgets.template_row import TemplateRow

from .base import BaseTab

if TYPE_CHECKING:
    from super_buyer.ui.app import App


class MultiSnipeTab(BaseTab):
    """多商品抢购标签页，负责多任务的配置与执行。"""

    tab_text = "多商品抢购模式"

    def __init__(self, app: "App", notebook: ttk.Notebook) -> None:
        super().__init__(app, notebook)
        self.tab_multi = self
        self.template_rows: Dict[str, Any] = {}
        self.lab_snipe_modal_summary: ttk.Label | None = None
        self._build_tab_multi()

    def _build_tab_multi(self) -> None:
        outer = self.tab_multi
        frm = ttk.Frame(outer)
        frm.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

        # 必备设置：最近购买 / 我的收藏 模板
        box_req = self._build_section(frm, "任务准备", pady=(0, 6))
        # 确保单行模板配置可横向拉伸
        try:
            box_req.columnconfigure(0, weight=1)
        except Exception:
            pass

        # 工具：模板测试/截图/预览（本页私有）
        def _test_match(name: str, path: str, conf: float):
            if not path or not os.path.exists(path):
                return False, f"{name} 模板文件不存在"
            try:
                import pyautogui  # type: ignore
                center = pyautogui.locateCenterOnScreen(path, confidence=conf)
                if center:
                    try:
                        pyautogui.moveTo(center.x, center.y, duration=0.08)
                        pyautogui.click(center.x, center.y)
                    except Exception:
                        pass
                    return True, "识别成功"
                _ = pyautogui.locateOnScreen(path, confidence=conf)
            except Exception as e:
                return False, f"识别异常：{e}"
            return False, f"{name} 未匹配到"

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
                slug = self._template_slug(row.name)
                p = self._images_path(f"{slug}.png", ensure_parent=True)
                try:
                    img.save(p)
                except Exception as e:
                    messagebox.showerror("选图片", f"失败: {e}")
                    return
                try:
                    rel = Path(p).resolve().relative_to(self.paths.root)
                    row.var_path.set(rel.as_posix())
                except Exception:
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
            readonly=True,
            root_dir=self.paths.root,
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
            readonly=True,
            root_dir=self.paths.root,
        )
        r_fav.grid(row=1, column=0, sticky="we", padx=6, pady=(0, 6))
        self.template_rows["favorites_tab"] = r_fav

        # 任务列表改为弹窗配置

        # 控制区
        ctrl_box = self._build_section(frm, "任务控制")
        ctrl = ttk.Frame(ctrl_box)
        ctrl.pack(fill=tk.X, padx=8, pady=8)
        self.btn_snipe_start = ttk.Button(ctrl, text="开始任务", command=self._snipe_start)
        self.btn_snipe_start.pack(side=tk.LEFT)
        self.btn_snipe_stop = ttk.Button(ctrl, text="终止任务", command=self._snipe_stop_clicked)
        self.btn_snipe_stop.pack(side=tk.LEFT, padx=6)
        ttk.Button(ctrl, text="清空全部商品购买记录", command=self._snipe_clear_records).pack(side=tk.LEFT, padx=6)
        ttk.Button(ctrl, text="配置任务", command=self._open_snipe_tasks_modal).pack(side=tk.LEFT, padx=(12, 0))
        self.lab_snipe_task_summary = ttk.Label(ctrl, text="", foreground="#666666")
        self.lab_snipe_task_summary.pack(side=tk.LEFT, padx=(12, 0))
        self.lab_snipe_status = ttk.Label(ctrl, text="idle", foreground="#666666")
        self.lab_snipe_status.pack(side=tk.RIGHT)

        # 调试模式（专属于多商品抢购）
        box_dbg = self._build_section(frm, "调试模式（仅本页面生效）")
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
            _dir = str(dbg_cfg.get("overlay_dir", self._images_path("debug", "可视化调试")))
        except Exception:
            _dir = self._images_path("debug", "可视化调试")
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

        # 高级设置（购买时序 / 连击 / 重启）
        tuning = self.cfg.get("multi_snipe_tuning") if isinstance(self.cfg.get("multi_snipe_tuning"), dict) else {}
        def _get_f(key: str, default: float) -> float:
            try:
                v = float(tuning.get(key, default) or default)
            except Exception:
                v = default
            return v
        def _get_i(key: str, default: int) -> int:
            try:
                v = int(tuning.get(key, default) or default)
            except Exception:
                v = default
            return v
        self.var_ms_buy_timeout = tk.DoubleVar(value=_get_f("buy_result_timeout_sec", 0.35))
        self.var_ms_buy_poll = tk.DoubleVar(value=_get_f("buy_result_poll_step_sec", 0.02))
        self.var_ms_post_success = tk.DoubleVar(value=_get_f("post_success_click_sec", 0.05))
        self.var_ms_post_close = tk.DoubleVar(value=_get_f("post_close_detail_sec", 0.05))
        self.var_ms_post_nav = tk.DoubleVar(value=_get_f("post_nav_sec", 0.05))
        try:
            self.var_ms_fast_mode = tk.BooleanVar(value=bool(tuning.get("fast_chain_mode", True)))
        except Exception:
            self.var_ms_fast_mode = tk.BooleanVar(value=True)
        self.var_ms_fast_max = tk.IntVar(value=_get_i("fast_chain_max", 10))
        self.var_ms_fast_interval = tk.DoubleVar(value=_get_f("fast_chain_interval_ms", 35.0))

        box_adv = self._build_section(frm, "高级设置（购买/连击）")
        adv_sections = ttk.Frame(box_adv)
        adv_sections.pack(fill=tk.X, expand=True, padx=8, pady=6)
        try:
            adv_sections.columnconfigure(0, weight=1)
            adv_sections.columnconfigure(1, weight=1)
        except Exception:
            pass

        box_chain = ttk.LabelFrame(adv_sections, text="购买 / 连击")
        box_result = ttk.LabelFrame(adv_sections, text="结果 / 收尾节奏")
        for box in (box_chain, box_result):
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

        adv_layout_state = {"stacked": None}

        def _relayout_adv_sections(event=None) -> None:
            try:
                width = int(getattr(event, "width", 0) or adv_sections.winfo_width() or 0)
            except Exception:
                width = 0
            stacked = width < 900
            if adv_layout_state["stacked"] is stacked:
                return
            adv_layout_state["stacked"] = stacked
            try:
                box_chain.grid_forget()
                box_result.grid_forget()
            except Exception:
                pass
            try:
                if stacked:
                    adv_sections.columnconfigure(0, weight=1)
                    adv_sections.columnconfigure(1, weight=0)
                    box_chain.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 6))
                    box_result.grid(row=1, column=0, columnspan=2, sticky="ew")
                else:
                    adv_sections.columnconfigure(0, weight=1)
                    adv_sections.columnconfigure(1, weight=1)
                    box_chain.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
                    box_result.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
            except Exception:
                pass

        _make_row(
            box_chain,
            0,
            "快速连击模式",
            lambda row: ttk.Checkbutton(row, text="启用", variable=self.var_ms_fast_mode),
            tooltip=(
                "开启后会优先压缩单轮购买间隔，适合短时爆量上架场景。\n"
                "价格剧烈波动时风险更高，建议结合连击上限一起控制。"
            ),
        )
        _make_row(
            box_chain,
            1,
            "连击上限(次)",
            lambda row: _build_spinbox(row, from_=1, to=30, increment=1, textvariable=self.var_ms_fast_max),
            tooltip="单次识别后允许连续买入的最大次数，数值越大越激进。",
        )
        _make_row(
            box_chain,
            2,
            "连击间隔(ms)",
            lambda row: _build_spinbox(row, from_=30, to=500, increment=5, textvariable=self.var_ms_fast_interval),
            tooltip="连续点击之间的间隔，建议保守从 35ms 起调。",
            pady=(0, 8),
        )

        _make_row(
            box_result,
            0,
            "结果超时(秒)",
            lambda row: _build_spinbox(row, from_=0.1, to=5.0, increment=0.05, textvariable=self.var_ms_buy_timeout),
            tooltip="点击购买后等待成功/失败提示的最大时间。",
        )
        _make_row(
            box_result,
            1,
            "轮询步进(秒)",
            lambda row: _build_spinbox(row, from_=0.005, to=0.5, increment=0.005, textvariable=self.var_ms_buy_poll),
            tooltip="在结果窗口内检查提示出现的轮询频率。",
        )
        _make_row(
            box_result,
            2,
            "成功遮罩等待(秒)",
            lambda row: _build_spinbox(row, from_=0.0, to=1.0, increment=0.01, textvariable=self.var_ms_post_success),
            tooltip="购买成功后，关闭成功反馈前保留的等待时长。",
        )
        _make_row(
            box_result,
            3,
            "关闭详情等待(秒)",
            lambda row: _build_spinbox(row, from_=0.0, to=1.0, increment=0.01, textvariable=self.var_ms_post_close),
            tooltip="关闭详情弹层后的缓冲时间，避免界面尚未稳定就继续操作。",
        )
        _make_row(
            box_result,
            4,
            "导航等待(秒)",
            lambda row: _build_spinbox(row, from_=0.0, to=1.0, increment=0.01, textvariable=self.var_ms_post_nav),
            tooltip="页面切换或返回列表后的缓冲等待，数值越小越激进。",
        )
        try:
            ttk.Label(
                box_result,
                text="这些参数会写回全局 multi_snipe_tuning，并供多商品运行器直接读取。",
                foreground="#666666",
                justify=tk.LEFT,
            ).grid(row=5, column=0, sticky="w", padx=8, pady=(0, 8))
        except Exception:
            pass

        try:
            adv_sections.bind("<Configure>", _relayout_adv_sections)
            self.after_idle(_relayout_adv_sections)
        except Exception:
            try:
                _relayout_adv_sections()
            except Exception:
                pass

        for v in [
            self.var_ms_buy_timeout,
            self.var_ms_buy_poll,
            self.var_ms_post_success,
            self.var_ms_post_close,
            self.var_ms_post_nav,
            self.var_ms_fast_mode,
            self.var_ms_fast_max,
            self.var_ms_fast_interval,
        ]:
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
                path = self._resolve_data_path(str(g.get("image_path", "") or "").strip())
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
                        _, btm_rect = MultiSnipeRunner._rois_from_card(card)  # type: ignore[attr-defined]
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
        logf = self._build_section(frm, "执行日志", expand=True)
        topbar = ttk.Frame(logf)
        topbar.pack(fill=tk.X, padx=6, pady=(6, 0))
        ttk.Label(topbar, text="自动展示最详细日志，界面仅保留最新 5000 条").pack(side=tk.LEFT)
        self.multi_txt = tk.Text(logf, height=12, wrap="word")
        self.multi_txt.pack(fill=tk.BOTH, expand=True)
        self.multi_txt.configure(state=tk.DISABLED)
        self._restore_runtime_log_widget("multi", self.multi_txt)
        self._update_snipe_task_summary()
        self._update_snipe_buttons()

    # ---- 多商品抢购：数据加载/保存 ----
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

    def _save_snipe_tasks_data(self) -> None:
        try:
            import json
            with Path(self.snipe_tasks_path).open("w", encoding="utf-8") as f:
                json.dump(self.snipe_tasks_data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _has_pending_snipe_editor(self) -> bool:
        try:
            if self._snipe_editing_index is not None:
                return True
        except Exception:
            pass
        try:
            items = list(self.snipe_tasks_data.get("items", []) or [])
        except Exception:
            items = []
        return any(isinstance(it, dict) and bool(it.get("__draft__")) for it in items)

    def _discard_snipe_drafts(self) -> None:
        try:
            items = list(self.snipe_tasks_data.get("items", []) or [])
        except Exception:
            items = []
        kept: list[dict] = []
        for it in items:
            if isinstance(it, dict) and bool(it.get("__draft__")):
                continue
            if isinstance(it, dict):
                kept.append(it)
        self.snipe_tasks_data["items"] = kept
        self._snipe_editing_index = None
        self._update_snipe_task_summary(kept)

    # ---- 多商品抢购：日志 ----
    def _append_multi_log(self, s: str) -> None:
        self._append_runtime_log("multi", s, widget=self.multi_txt, lock=self._snipe_log_lock)

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
        self._update_snipe_task_summary(items)
        if not items:
            ttk.Label(parent, text="暂无任务，点击‘新增…’添加。", foreground="#666").pack(anchor="w", padx=4, pady=6)
            return
        for i, it in enumerate(items):
            self._build_snipe_task_card(parent, i, it, editable=(self._snipe_editing_index == i), on_refresh=on_refresh)

    def _update_snipe_task_summary(self, items: List[dict] | None = None) -> None:
        rows = list(items if items is not None else (self.snipe_tasks_data.get("items", []) or []))
        total = len(rows)
        try:
            enabled = sum(1 for it in rows if bool(it.get("enabled", True)))
        except Exception:
            enabled = total
        text = f"任务数: {total}，启用: {enabled}"
        for label in (
            getattr(self, "lab_snipe_task_summary", None),
            getattr(self, "lab_snipe_modal_summary", None),
        ):
            if label is None:
                continue
            try:
                label.configure(text=text)
            except Exception:
                pass

    def _build_snipe_task_card(self, parent, idx: int | None, it: dict, *, editable: bool, on_refresh=None) -> None:
        f = ttk.Frame(parent, relief=tk.SOLID, borderwidth=1)
        f.pack(fill=tk.X, padx=6, pady=6)
        var_enabled = tk.BooleanVar(value=bool(it.get("enabled", True)))
        var_item_name = tk.StringVar(value=str(it.get("name", "")))
        var_item_id = tk.StringVar(value=str(it.get("item_id", "")))
        var_price = tk.IntVar(value=int(it.get("price", it.get("price_threshold", 0)) or 0))
        var_prem = tk.DoubleVar(value=float(it.get("premium_pct", 0.0) or 0.0))
        var_mode = tk.StringVar(value=str(it.get("purchase_mode", it.get("mode", "normal")) or "normal").lower())
        var_qty = tk.IntVar(value=int(it.get("target_total", it.get("buy_qty", 0)) or 0))
        var_img = tk.StringVar(value=str(it.get("image_path", it.get("template", "")) or ""))
        var_big = tk.StringVar(value=str(it.get("big_category", "") or ""))

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

        def _fmt_pct(value: Any) -> str:
            num = _safe_float(value)
            if float(num).is_integer():
                return str(int(num))
            return f"{num:.2f}".rstrip("0").rstrip(".")

        def _limit_summary() -> str:
            base = _safe_int(var_price.get())
            prem = max(0.0, _safe_float(var_prem.get()))
            if base <= 0:
                return "未设置"
            limit = base + int(round(base * prem / 100.0))
            return f"{base}，浮动 {_fmt_pct(prem)}%，上限 {limit}"

        def _mode_label() -> str:
            return "补货" if str(var_mode.get() or "normal").lower() == "restock" else "正常"

        def _binding_text() -> str:
            if str(var_item_id.get() or "").strip():
                return "已绑定物品库商品"
            return "请选择物品库中的商品，保存时必须绑定 item_id"

        var_title = tk.StringVar()
        var_mode_display = tk.StringVar()
        var_target_display = tk.StringVar()
        var_price_summary = tk.StringVar()
        var_binding_display = tk.StringVar()

        def _refresh_texts(*_args) -> None:
            name = str(var_item_name.get() or "").strip() or "未选择商品"
            var_title.set(f"商品：{name}")
            var_mode_display.set(_mode_label())
            var_target_display.set(f"{_safe_int(var_qty.get())} 个")
            var_price_summary.set(_limit_summary())
            var_binding_display.set(_binding_text())

        _refresh_texts()
        for _var in (var_item_name, var_item_id, var_price, var_prem, var_mode, var_qty):
            try:
                _var.trace_add("write", lambda *_args: _refresh_texts())
            except Exception:
                pass

        header = ttk.Frame(f)
        header.pack(fill=tk.X, padx=8, pady=(8, 4))
        chk_enabled = ttk.Checkbutton(header, text="启用", variable=var_enabled)
        chk_enabled.pack(side=tk.LEFT)
        ttk.Label(header, text=f"顺序：{(idx + 1) if idx is not None else '新增'}").pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(header, textvariable=var_title).pack(side=tk.LEFT, padx=(12, 0))
        ttk.Label(header, textvariable=var_mode_display, foreground="#666666").pack(side=tk.RIGHT)

        if not editable and idx is not None:
            def _save_enabled(*_):
                try:
                    items = list(self.snipe_tasks_data.get("items", []) or [])
                    if 0 <= int(idx) < len(items):
                        items[int(idx)]["enabled"] = bool(var_enabled.get())
                        self.snipe_tasks_data["items"] = items
                        self._save_snipe_tasks_data()
                        self._update_snipe_task_summary(items)
                except Exception:
                    pass

            try:
                var_enabled.trace_add("write", _save_enabled)
            except Exception:
                pass

        def _pick_goods():
            def _on_pick(g):
                var_item_name.set(str(g.get("name", "")))
                var_item_id.set(str(g.get("id", "")))
                p = str(g.get("image_path", "") or "")
                if p:
                    var_img.set(p)
                bc = str(g.get("big_category", "") or "")
                if bc:
                    var_big.set(bc)
            self._open_goods_picker(_on_pick)

        if editable:
            box_basic = ttk.LabelFrame(f, text="基本信息")
            box_basic.pack(fill=tk.X, padx=8, pady=(0, 6))
            try:
                box_basic.columnconfigure(1, weight=1)
            except Exception:
                pass
            ttk.Label(box_basic, text="商品").grid(row=0, column=0, sticky="e", padx=6, pady=6)
            ent_name = ttk.Entry(box_basic, textvariable=var_item_name, state="readonly")
            ent_name.grid(row=0, column=1, sticky="we", padx=6, pady=6)
            ttk.Button(box_basic, text="选择…", command=_pick_goods).grid(row=0, column=2, padx=(0, 6), pady=6)
            ttk.Label(box_basic, text="绑定状态").grid(row=1, column=0, sticky="ne", padx=6, pady=(0, 6))
            ttk.Label(
                box_basic,
                textvariable=var_binding_display,
                foreground="#666666",
                justify=tk.LEFT,
            ).grid(row=1, column=1, columnspan=2, sticky="w", padx=6, pady=(0, 6))

            box_strategy = ttk.LabelFrame(f, text="价格与目标")
            box_strategy.pack(fill=tk.X, padx=8, pady=(0, 6))
            ttk.Label(box_strategy, text="目标价格").grid(row=0, column=0, sticky="e", padx=6, pady=6)
            ttk.Entry(box_strategy, textvariable=var_price, width=10).grid(row=0, column=1, sticky="w", padx=6, pady=6)
            ttk.Label(box_strategy, text="浮动(%)").grid(row=0, column=2, sticky="e", padx=6, pady=6)
            try:
                sp_p = ttk.Spinbox(box_strategy, from_=0.0, to=200.0, increment=0.5, textvariable=var_prem, width=8)
            except Exception:
                sp_p = tk.Spinbox(box_strategy, from_=0.0, to=200.0, increment=0.5, textvariable=var_prem, width=8)
            sp_p.grid(row=0, column=3, sticky="w", padx=6, pady=6)
            ttk.Label(box_strategy, text="购买模式").grid(row=0, column=4, sticky="e", padx=6, pady=6)
            var_mode_disp = tk.StringVar(value=_mode_label())
            cmb = ttk.Combobox(box_strategy, values=["正常", "补货"], state="readonly", textvariable=var_mode_disp, width=10)

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
            cmb.grid(row=0, column=5, sticky="w", padx=6, pady=6)
            ttk.Label(box_strategy, text="目标数量").grid(row=1, column=0, sticky="e", padx=6, pady=(0, 6))
            try:
                sp_q = ttk.Spinbox(box_strategy, from_=1, to=120, increment=1, textvariable=var_qty, width=8)
            except Exception:
                sp_q = tk.Spinbox(box_strategy, from_=1, to=120, increment=1, textvariable=var_qty, width=8)
            sp_q.grid(row=1, column=1, sticky="w", padx=6, pady=(0, 6))
            ttk.Label(
                box_strategy,
                textvariable=var_price_summary,
                foreground="#666666",
                justify=tk.LEFT,
            ).grid(row=1, column=2, columnspan=4, sticky="w", padx=6, pady=(0, 6))
        else:
            summary = ttk.Frame(f)
            summary.pack(fill=tk.X, padx=8, pady=(0, 4))
            try:
                summary.columnconfigure(1, weight=1)
                summary.columnconfigure(3, weight=1)
            except Exception:
                pass
            purchased = _safe_int(it.get("purchased", 0))
            target = _safe_int(it.get("target_total", it.get("buy_qty", 0)))
            ttk.Label(summary, text="商品").grid(row=0, column=0, sticky="e", padx=6, pady=4)
            ttk.Label(summary, text=str(var_item_name.get() or "未选择商品")).grid(row=0, column=1, sticky="w", padx=6, pady=4)
            ttk.Label(summary, text="目标").grid(row=0, column=2, sticky="e", padx=6, pady=4)
            ttk.Label(summary, textvariable=var_target_display).grid(row=0, column=3, sticky="w", padx=6, pady=4)
            ttk.Label(summary, text="模式").grid(row=1, column=0, sticky="e", padx=6, pady=4)
            ttk.Label(summary, textvariable=var_mode_display).grid(row=1, column=1, sticky="w", padx=6, pady=4)
            ttk.Label(summary, text="进度").grid(row=1, column=2, sticky="e", padx=6, pady=4)
            ttk.Label(summary, text=f"{purchased}/{target}").grid(row=1, column=3, sticky="w", padx=6, pady=4)
            ttk.Label(summary, text="价格策略").grid(row=2, column=0, sticky="ne", padx=6, pady=4)
            ttk.Label(summary, textvariable=var_price_summary, justify=tk.LEFT).grid(row=2, column=1, sticky="w", padx=6, pady=4)
            ttk.Label(summary, text="绑定状态").grid(row=2, column=2, sticky="ne", padx=6, pady=4)
            ttk.Label(summary, textvariable=var_binding_display, foreground="#666666", justify=tk.LEFT).grid(
                row=2, column=3, sticky="w", padx=6, pady=4
            )

        row3 = ttk.Frame(f)
        row3.pack(fill=tk.X, padx=8, pady=(0, 8))
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
            self._update_snipe_task_summary(items)
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
            try:
                items = list(self.snipe_tasks_data.get("items", []) or [])
            except Exception:
                items = []
            try:
                if idx is not None and 0 <= idx < len(items):
                    cur = items[idx]
                    if isinstance(cur, dict) and bool(cur.get("__draft__")):
                        items.pop(idx)
                        self.snipe_tasks_data["items"] = items
            except Exception:
                pass
            self._snipe_editing_index = None
            self._update_snipe_task_summary(items)
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
            self._update_snipe_task_summary(items)
            if on_refresh:
                on_refresh()
            else:
                self._render_snipe_task_cards()

        if editable:
            ttk.Button(row3, text="保存", command=_do_save).pack(side=tk.RIGHT)
            ttk.Button(row3, text="取消", command=_do_cancel).pack(side=tk.RIGHT, padx=(0, 6))
        else:
            ttk.Button(row3, text="编辑", command=_do_edit).pack(side=tk.RIGHT)
            ttk.Button(row3, text="删除", command=_do_delete).pack(side=tk.RIGHT, padx=(0, 6))

    def _snipe_add_task(self) -> None:
        if self._has_pending_snipe_editor():
            messagebox.showwarning("新增", "请先保存或取消当前正在编辑的任务。")
            return
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
            "__draft__": True,
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
        shell = self._build_modal_shell(
            top,
            title="配置多商品任务",
            description="统一管理多商品的目标价格、数量和购买模式。",
        )
        self.lab_snipe_modal_summary = shell["summary"]

        # Top bar
        tb = shell["toolbar"]
        def _refresh():
            self._render_snipe_task_cards_into(cards, on_refresh=_refresh)
            self._update_snipe_task_summary()
        def _add():
            if self._has_pending_snipe_editor():
                messagebox.showwarning("新增", "请先保存或取消当前正在编辑的任务。")
                return
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
                "__draft__": True,
            }
            items.append(draft)
            self.snipe_tasks_data["items"] = items
            self._snipe_editing_index = len(items) - 1
            _refresh()
        ttk.Button(tb, text="新增…", command=_add).pack(side=tk.LEFT)

        def _close() -> None:
            try:
                if self._has_pending_snipe_editor():
                    if not messagebox.askokcancel(
                        "关闭",
                        "当前有未保存的多商品任务编辑，关闭后这些更改会丢失，是否继续？",
                    ):
                        return
            except Exception:
                pass
            try:
                self._discard_snipe_drafts()
            except Exception:
                pass
            self.lab_snipe_modal_summary = None
            try:
                top.destroy()
            except Exception:
                pass

        try:
            top.protocol("WM_DELETE_WINDOW", _close)
        except Exception:
            pass

        # Cards area with scrolling container
        scroll = self._build_scrollable_canvas(shell["content"])
        cards = scroll["inner"]
        _refresh()

        # Bottom buttons
        bf = shell["footer"]
        ttk.Button(bf, text="关闭", command=_close).pack(side=tk.RIGHT)

    # ---- 多商品抢购：运行控制 ----
    def _snipe_start(self) -> None:
        if MultiSnipeRunner is None:
            detail = globals().get("_multi_import_error") or "unknown"
            messagebox.showerror("选图片", f"失败: {detail}")
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
                reason = res.error or f"code={res.code}"
                messagebox.showerror("选图片", f"失败: {reason}")
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

        # 构造运行器：在本页注入调试覆盖（仅本页面生效，不落盘）
        try:
            self._save_templates_into_cfg()
        except Exception:
            pass
        try:
            import copy as _copy
            cfg_copy = _copy.deepcopy(self.cfg)
        except Exception:
            cfg_copy = dict(self.cfg)
        try:
            dbg_base = (cfg_copy.get("debug", {}) or {}) if isinstance(cfg_copy.get("debug"), dict) else {}
        except Exception:
            dbg_base = {}
        try:
            overrides = self._collect_debug_overrides()
        except Exception:
            overrides = {}
        try:
            dbg_new = dict(dbg_base)
            dbg_new.update(overrides)
            cfg_copy["debug"] = dbg_new
        except Exception:
            cfg_copy["debug"] = overrides or dbg_base

        # 启动前打印高级设置摘要
        try:
            tcfg = self._collect_tuning_from_ui()
            fast_tag = "开" if tcfg.get("fast_chain_mode") else "关"
            self._append_multi_log(
                "【INFO】高级设置：超时={:.2f}s 步进={:.3f}s 成功等待={:.2f}s 关闭等待={:.2f}s 连击={} max={} 间隔={}ms".format(
                    tcfg.get("buy_result_timeout_sec", 0.0),
                    tcfg.get("buy_result_poll_step_sec", 0.0),
                    tcfg.get("post_success_click_sec", 0.0),
                    tcfg.get("post_close_detail_sec", 0.0),
                    fast_tag,
                    int(tcfg.get("fast_chain_max", 0) or 0),
                    int(round(float(tcfg.get("fast_chain_interval_ms", 0.0) or 0.0))),
                )
            )
        except Exception:
            pass

        runner = MultiSnipeRunner(cfg_copy, items_raw, on_log=lambda s: self._append_multi_log(s))
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
        try:
            self.lab_snipe_status.configure(text=("running" if running else "idle"))
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
        out_root = str(self.paths.output_dir)
        outp = os.path.join(out_root, "multi_snipe_purchases.json")
        try:
            if os.path.exists(outp):
                os.remove(outp)
            self._append_multi_log("【INFO】已清空本模式购买记录。")
        except Exception:
            pass

    def _append_multi_purchase_records(self, recs: List[Dict[str, Any]]) -> None:
        out_root = str(self.paths.output_dir)
        os.makedirs(out_root, exist_ok=True)
        path = os.path.join(out_root, "multi_snipe_purchases.json")
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

    # ---------- 将本页模板持久化到 cfg（供 App.save_config 调用） ----------
    def _save_templates_into_cfg(self) -> None:
        try:
            tpls = self.cfg.setdefault("templates", {})
            for key, row in (self.template_rows or {}).items():
                try:
                    tpls.setdefault(key, {})
                    tpls[key]["path"] = row.get_abs_path()
                    tpls[key]["confidence"] = float(row.get_confidence())
                except Exception:
                    pass
            try:
                tcfg = self.cfg.setdefault("multi_snipe_tuning", {})
                tcfg.update(self._collect_tuning_from_ui())
                tcfg.pop("restart_every_min", None)
            except Exception:
                pass
            try:
                dbg = self.cfg.setdefault("debug", {})
                overrides = self._collect_debug_overrides()
                if not str(overrides.get("overlay_dir", "") or "").strip():
                    overrides["overlay_dir"] = self._images_path(
                        "debug", "可视化调试", ensure_parent=True
                    )
                dbg.update(overrides)
            except Exception:
                pass
        except Exception:
            pass

    # ---- 多商品抢购：调试配置（仅本页生效） ----
    def _collect_debug_overrides(self) -> Dict[str, Any]:
        """从本页 UI 变量收集调试覆盖配置，不落盘，仅在本页构造 Runner 时注入。

        返回字段：
        - enabled: bool
        - overlay_sec: float [0.5, 15]
        - step_sleep: float [0.0, 1.0]
        - save_overlay_images: bool
        - overlay_dir: str（为空时使用默认 images/debug 目录，由 Runner 规范化）
        """
        enabled = bool(self.var_debug_enabled.get()) if hasattr(self, "var_debug_enabled") else False
        try:
            ov = float(self.var_debug_overlay.get())
        except Exception:
            ov = 5.0
        if ov < 0.5:
            ov = 0.5
        if ov > 15.0:
            ov = 15.0
        try:
            st = float(self.var_debug_step.get())
        except Exception:
            st = 0.0
        if st < 0.0:
            st = 0.0
        if st > 1.0:
            st = 1.0
        try:
            save_imgs = bool(self.var_debug_save_imgs.get())
        except Exception:
            save_imgs = False
        try:
            od = (self.var_debug_overlay_dir.get() or "").strip()
        except Exception:
            od = ""
        return {
            "enabled": enabled,
            "overlay_sec": float(ov),
            "step_sleep": float(st),
            "save_overlay_images": save_imgs,
            "overlay_dir": od,
        }

    def _collect_tuning_from_ui(self) -> Dict[str, Any]:
        """收集并裁剪多商品高级设置，写入 cfg.multi_snipe_tuning。"""
        def _clamp_float(val, lo, hi, default):
            try:
                v = float(val)
            except Exception:
                return float(default)
            if v < lo:
                v = lo
            if v > hi:
                v = hi
            return float(v)

        def _clamp_int(val, lo, hi, default):
            try:
                v = int(val)
            except Exception:
                return int(default)
            if v < lo:
                v = lo
            if v > hi:
                v = hi
            return int(v)

        return {
            "buy_result_timeout_sec": _clamp_float(self.var_ms_buy_timeout.get() if hasattr(self, "var_ms_buy_timeout") else 0.35, 0.1, 5.0, 0.35),
            "buy_result_poll_step_sec": _clamp_float(self.var_ms_buy_poll.get() if hasattr(self, "var_ms_buy_poll") else 0.02, 0.005, 0.5, 0.02),
            "post_success_click_sec": _clamp_float(self.var_ms_post_success.get() if hasattr(self, "var_ms_post_success") else 0.05, 0.0, 1.0, 0.05),
            "post_close_detail_sec": _clamp_float(self.var_ms_post_close.get() if hasattr(self, "var_ms_post_close") else 0.05, 0.0, 1.0, 0.05),
            "post_nav_sec": _clamp_float(self.var_ms_post_nav.get() if hasattr(self, "var_ms_post_nav") else 0.05, 0.0, 1.0, 0.05),
            "fast_chain_mode": bool(self.var_ms_fast_mode.get()) if hasattr(self, "var_ms_fast_mode") else True,
            "fast_chain_max": _clamp_int(self.var_ms_fast_max.get() if hasattr(self, "var_ms_fast_max") else 10, 1, 30, 10),
            "fast_chain_interval_ms": _clamp_float(self.var_ms_fast_interval.get() if hasattr(self, "var_ms_fast_interval") else 35.0, 30.0, 500.0, 35.0),
        }

    def _debug_test_overlay(self) -> None:
        """在屏幕上显示一个半透明全屏蒙版，使用本页设置的时长参数。"""
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
                from super_buyer.services.font_loader import tk_font as _tk_font  # 延迟导入
            except Exception:
                _tk_font = None
            try:
                f1 = _tk_font(self, 14) if _tk_font else None
                if f1 is not None:
                    cv.create_text(W // 2, 40, text="调试蒙版预览（本页参数）", fill="white", font=f1)
                else:
                    cv.create_text(W // 2, 40, text="调试蒙版预览（本页参数）", fill="white")
            except Exception:
                pass
            try:
                top.after(int(ov * 1000), top.destroy)
            except Exception:
                pass
        except Exception:
            pass


    # ---------- Tab: 利润计算 ----------
