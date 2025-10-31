
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
        self._build_tab_multi()

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


    # ---------- Tab: 利润计算 ----------
