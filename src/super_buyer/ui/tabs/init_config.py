
from __future__ import annotations

import os
import threading
import time
import tkinter as tk
from typing import TYPE_CHECKING, Dict

from tkinter import filedialog, messagebox, ttk

from super_buyer.core.launcher import run_launch_flow
from super_buyer.config.loader import save_config
from super_buyer.services.font_loader import tk_font
from super_buyer.ui.widgets.selectors import RegionSelector
from super_buyer.ui.widgets.template_row import TemplateRow

from .base import BaseTab

if TYPE_CHECKING:
    from super_buyer.ui.app import App


class InitConfigTab(BaseTab):
    """初始化配置标签页，负责快捷配置、模板管理与调试工具。"""

    tab_text = "初始化配置"

    def __init__(self, app: "App", notebook: ttk.Notebook) -> None:
        super().__init__(app, notebook)
        self.tab1 = self  # 兼容旧逻辑对 tab1 的引用
        self._build_tab1()

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

        # 全局快捷键设置
        try:
            hot_cfg = self.cfg.get("hotkeys", {}) if isinstance(self.cfg.get("hotkeys"), dict) else {}
        except Exception:
            hot_cfg = {}
        raw_hot = str(hot_cfg.get("toggle") or hot_cfg.get("stop") or "<Control-Alt-t>")
        try:
            disp_hot = self.app._hotkey_to_display(raw_hot)
        except Exception:
            disp_hot = raw_hot or "Ctrl+Alt+T"
        if not disp_hot:
            disp_hot = "Ctrl+Alt+T"
        self.var_hotkey_toggle = tk.StringVar(value=disp_hot)
        box_hotkey = ttk.LabelFrame(outer, text="快捷键")
        box_hotkey.pack(fill=tk.X, padx=8, pady=8)
        ttk.Label(box_hotkey, text="暂停/继续").grid(row=0, column=0, sticky="e", padx=4, pady=6)
        ent_hotkey = ttk.Entry(box_hotkey, textvariable=self.var_hotkey_toggle, width=20)
        ent_hotkey.grid(row=0, column=1, sticky="w")
        ent_hotkey.bind("<FocusOut>", lambda _e: self._apply_hotkey_setting())
        ent_hotkey.bind("<Return>", lambda _e: self._apply_hotkey_setting())
        ttk.Button(box_hotkey, text="恢复默认", command=self._reset_hotkey_default).grid(row=0, column=2, sticky="w", padx=6)
        self.lab_hotkey_status = ttk.Label(box_hotkey, text="")
        self.lab_hotkey_status.grid(row=0, column=3, sticky="w", padx=4)
        ttk.Label(box_hotkey, text="支持格式：Ctrl+Alt+P、Shift+F9 或 <Control-Alt-p>").grid(row=1, column=0, columnspan=4, sticky="w", padx=4, pady=(0, 6))
        try:
            box_hotkey.columnconfigure(3, weight=1)
        except Exception:
            pass
        try:
            self.var_hotkey_toggle.trace_add("write", lambda *_: self._on_hotkey_change())
        except Exception:
            pass
        self._update_hotkey_status()

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
            """在屏幕上查找模板，找到则移动鼠标并点击一次；结果以轻提示反馈。"""
            if not path or not os.path.exists(path):
                return False, f"{name} 模板不存在"
            try:
                import pyautogui  # type: ignore
                center = pyautogui.locateCenterOnScreen(path, confidence=conf)
                if center:
                    try:
                        pyautogui.moveTo(center.x, center.y, duration=0.1)
                        pyautogui.click(center.x, center.y)
                    except Exception:
                        pass
                    return True, "识别成功"
                _ = pyautogui.locateOnScreen(path, confidence=conf)
            except Exception as e:
                return False, f"识别异常：{e}"
            return False, f"{name} 未匹配到"

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
                slug = self._template_slug(row.name)
                path = self._images_path(f"{slug}.png", ensure_parent=True)
                try:
                    img.save(path)
                except Exception as e:
                    messagebox.showerror("选图片", f"失败: {e}")
                    return
                try:
                    rel = Path(path).resolve().relative_to(self.paths.root)
                    row.var_path.set(rel.as_posix())
                except Exception:
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
                readonly=True,
                root_dir=self.paths.root,
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
                readonly=True,
                root_dir=self.paths.root,
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
                readonly=True,
                root_dir=self.paths.root,
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
                readonly=True,
                root_dir=self.paths.root,
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
            readonly=True,
            root_dir=self.paths.root,
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
            readonly=True,
            root_dir=self.paths.root,
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
            # 新增：处罚识别
            "penalty_warning": "处罚识别模板",
            "btn_penalty_confirm": "处罚识别确认模板",
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
                readonly=True,
                root_dir=self.paths.root,
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
                        msg = f"{nm} 模板不存在"
                        try:
                            self._append_log(f"[模板测试] 失败: {msg}")
                        except Exception:
                            pass
                        return False, msg
                    try:
                        import pyautogui  # type: ignore
                        center = pyautogui.locateCenterOnScreen(pth, confidence=cf)
                        try:
                            self._append_log("[模板测试] locateCenterOnScreen=" + (f"({center.x},{center.y})" if center else "None"))
                        except Exception:
                            pass
                        if center:
                            try:
                                pyautogui.moveTo(center.x, center.y, duration=0.1)
                                pyautogui.click(center.x, center.y)
                            except Exception:
                                pass
                            return True, "识别成功"
                        box = pyautogui.locateOnScreen(pth, confidence=cf)
                        if box:
                            try:
                                self._append_log(f"[模板测试] locateOnScreen=({int(getattr(box,'left',0))},{int(getattr(box,'top',0))},{int(getattr(box,'width',0))},{int(getattr(box,'height',0))})")
                            except Exception:
                                pass
                    except Exception as e:
                        try:
                            self._append_log(f"[模板测试] 异常: {e}")
                        except Exception:
                            pass
                        return False, f"识别异常：{e}"
                    return False, f"{nm} 未匹配到"

                r.on_test = _logged_on_test
            except Exception:
                pass
            rowc += 1

        # 价格区域模板与ROI
        box_roi = ttk.LabelFrame(outer, text="价格区域模板与ROI")
        box_roi.pack(fill=tk.X, padx=8, pady=8)

        roi_cfg = self.cfg.get("price_roi", {}) if isinstance(self.cfg.get("price_roi"), dict) else {}
        self.var_roi_top_off = tk.IntVar(value=int(roi_cfg.get("top_offset", 0)))
        self.var_roi_btm_off = tk.IntVar(value=int(roi_cfg.get("bottom_offset", 0)))
        self.var_roi_lr_pad = tk.IntVar(value=int(roi_cfg.get("lr_pad", 0)))
        try:
            _sc_def_roi = float(roi_cfg.get("scale", 1.0))
        except Exception:
            _sc_def_roi = 1.0
        self.var_roi_scale = tk.DoubleVar(value=_sc_def_roi)

        def _capture_roi(row: TemplateRow, slug: str, title: str) -> None:
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
                path = self._images_path(f"{slug}.png", ensure_parent=True)
                try:
                    img.save(path)
                except Exception as e:
                    messagebox.showerror("选图片", f"失败: {e}")
                    return
                try:
                    rel = Path(path).resolve().relative_to(self.paths.root)
                    row.var_path.set(rel.as_posix())
                except Exception:
                    row.var_path.set(path)
                self._schedule_autosave()

            self._select_region(_after)

        top_data = roi_cfg.get("top_template", {})
        if isinstance(top_data, str):
            top_data = {"path": top_data, "confidence": roi_cfg.get("top_threshold", 0.55)}
        elif isinstance(top_data, dict):
            top_data = {**top_data}
            top_data.setdefault("confidence", roi_cfg.get("top_threshold", 0.55))
        else:
            top_data = {"confidence": roi_cfg.get("top_threshold", 0.55)}

        self.row_roi_top = TemplateRow(
            box_roi,
            "顶部模板",
            top_data,
            on_test=test_match,
            on_capture=lambda r: _capture_roi(r, slug="buy_data_top", title="顶部模板"),
            on_preview=self._preview_image,
            on_change=self._schedule_autosave,
            readonly=True,
            root_dir=self.paths.root,
        )
        self.row_roi_top.grid(row=0, column=0, columnspan=6, sticky="we", padx=6, pady=4)

        btm_data = roi_cfg.get("bottom_template", {})
        if isinstance(btm_data, str):
            btm_data = {"path": btm_data, "confidence": roi_cfg.get("bottom_threshold", 0.55)}
        elif isinstance(btm_data, dict):
            btm_data = {**btm_data}
            btm_data.setdefault("confidence", roi_cfg.get("bottom_threshold", 0.55))
        else:
            btm_data = {"confidence": roi_cfg.get("bottom_threshold", 0.55)}

        self.row_roi_btm = TemplateRow(
            box_roi,
            "底部模板",
            btm_data,
            on_test=test_match,
            on_capture=lambda r: _capture_roi(r, slug="buy_data_btm", title="底部模板"),
            on_preview=self._preview_image,
            on_change=self._schedule_autosave,
            readonly=True,
            root_dir=self.paths.root,
        )
        self.row_roi_btm.grid(row=1, column=0, columnspan=6, sticky="we", padx=6, pady=4)

        self.var_roi_top_tpl = self.row_roi_top.var_path
        self.var_roi_top_thr = self.row_roi_top.var_conf
        self.var_roi_btm_tpl = self.row_roi_btm.var_path
        self.var_roi_btm_thr = self.row_roi_btm.var_conf

        # 偏移/边距 + 预览
        row_opts = ttk.Frame(box_roi)
        row_opts.grid(row=2, column=0, columnspan=6, sticky="we", padx=6, pady=(4, 6))
        ttk.Label(row_opts, text="顶部偏移").grid(row=0, column=0, padx=(0, 4))
        ttk.Entry(row_opts, textvariable=self.var_roi_top_off, width=8).grid(row=0, column=1, padx=(0, 12))
        ttk.Label(row_opts, text="底部偏移").grid(row=0, column=2, padx=(0, 4))
        ttk.Entry(row_opts, textvariable=self.var_roi_btm_off, width=8).grid(row=0, column=3, padx=(0, 12))
        ttk.Label(row_opts, text="左右边距").grid(row=0, column=4, padx=(0, 4))
        ttk.Entry(row_opts, textvariable=self.var_roi_lr_pad, width=8).grid(row=0, column=5, padx=(0, 12))
        ttk.Button(row_opts, text="预览 ROI", command=self._roi_preview_from_screen).grid(row=0, column=6, padx=(0, 12))
        ttk.Label(row_opts, text="放大倍率").grid(row=0, column=7, padx=(0, 4))
        try:
            sp_roi_sc = ttk.Spinbox(row_opts, from_=0.6, to=2.5, increment=0.1, textvariable=self.var_roi_scale, width=6)
        except Exception:
            sp_roi_sc = tk.Spinbox(row_opts, from_=0.6, to=2.5, increment=0.1, textvariable=self.var_roi_scale, width=6)
        sp_roi_sc.grid(row=0, column=8, padx=(0, 4))

        try:
            self.var_roi_scale.trace_add("write", lambda *_: self._schedule_autosave())
        except Exception:
            pass

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
                readonly=True,
                root_dir=self.paths.root,
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
                readonly=True,
                root_dir=self.paths.root,
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

        # 调试（OCR/ROI）：识别轮最终失败时保存 ROI 图
        dbg_cfg = self.cfg.get("debug", {}) if isinstance(self.cfg.get("debug"), dict) else {}
        try:
            self.var_save_roi_on_fail = tk.BooleanVar(value=bool(dbg_cfg.get("save_roi_on_fail", False)))
        except Exception:
            self.var_save_roi_on_fail = tk.BooleanVar(value=False)
        box_dbg_roi = ttk.LabelFrame(outer, text="调试（OCR/ROI）")
        box_dbg_roi.pack(fill=tk.X, padx=8, pady=(0, 8))
        try:
            chk_roi = ttk.Checkbutton(
                box_dbg_roi,
                text="识别轮最终失败时保存 ROI 图（output/roi_debug）",
                variable=self.var_save_roi_on_fail,
            )
        except Exception:
            chk_roi = tk.Checkbutton(
                box_dbg_roi,
                text="识别轮最终失败时保存 ROI 图（output/roi_debug）",
                variable=self.var_save_roi_on_fail,
            )
        chk_roi.pack(anchor="w", padx=8, pady=6)
        try:
            self.var_save_roi_on_fail.trace_add("write", lambda *_: self._schedule_autosave())
        except Exception:
            pass

        # 调试模式（已迁移至“多商品抢购模式”页面）

    def _on_hotkey_change(self, *_: object) -> None:
        """变量变动时刷新展示并触发自动保存。"""
        self._update_hotkey_status()
        self._schedule_autosave()

    def _reset_hotkey_default(self) -> None:
        """恢复默认的暂停快捷键。"""
        self.var_hotkey_toggle.set("Ctrl+Alt+T")
        self._apply_hotkey_setting()

    def _persist_hotkey_to_cfg(self) -> str:
        """将暂停快捷键写入配置字典并返回标准 Tk 序列。"""
        raw = (self.var_hotkey_toggle.get() or "").strip()
        if not raw:
            raw = "<Control-Alt-t>"
        try:
            normalized = self.app._normalize_tk_hotkey(raw)
        except Exception:
            normalized = "<Control-Alt-t>"
        try:
            self.cfg.setdefault("hotkeys", {})
            self.cfg["hotkeys"]["toggle"] = normalized
            # 兼容旧字段
            self.cfg["hotkeys"].pop("stop", None)
        except Exception:
            pass
        return normalized

    def _apply_hotkey_setting(self) -> None:
        """应用快捷键设置并立即更新绑定。"""
        normalized = self._persist_hotkey_to_cfg()
        self._update_hotkey_status(normalized)
        try:
            self.app._rebind_toggle_hotkey()
        except Exception:
            pass

    def _update_hotkey_status(self, normalized: str | None = None) -> None:
        """刷新状态标签，显示当前快捷键的友好文本与 Tk 序列。"""
        seq = normalized
        if seq is None:
            raw = (self.var_hotkey_toggle.get() or "").strip()
            if not raw:
                raw = "<Control-Alt-t>"
            try:
                seq = self.app._normalize_tk_hotkey(raw)
            except Exception:
                seq = "<Control-Alt-t>"
        try:
            disp = self.app._hotkey_to_display(seq)
        except Exception:
            disp = seq
        try:
            self.lab_hotkey_status.configure(text=f"当前: {disp}    Tk: {seq}")
        except Exception:
            pass

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
        self._apply_hotkey_setting()
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
                _dir = self._images_path("debug", "可视化调试", ensure_parent=True)
            self.cfg["debug"]["overlay_dir"] = _dir
            # 新增：识别轮最终失败时保存 ROI 图（默认关闭）
            try:
                self.cfg["debug"]["save_roi_on_fail"] = bool(self.var_save_roi_on_fail.get())
            except Exception:
                self.cfg["debug"]["save_roi_on_fail"] = False
        except Exception:
            pass

        save_config(self.cfg, path=self.config_path)
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
        top_path_rel = self.var_roi_top_tpl.get().strip()
        btm_path_rel = self.var_roi_btm_tpl.get().strip()
        top_path = self.row_roi_top.get_abs_path()
        btm_path = self.row_roi_btm.get_abs_path()
        if not top_path_rel or not os.path.exists(top_path):
            messagebox.showwarning("预览", "顶部模板未选择或文件不存在。")
            return
        if not btm_path_rel or not os.path.exists(btm_path):
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
        bw = bgray.shape[1]
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
        crop_path = self._images_path("_price_roi.png", ensure_parent=True)
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
            from super_buyer.services.ocr import recognize_text  # type: ignore
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
        path = self._images_path("_qty_input_roi.png", ensure_parent=True)
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
            from super_buyer.services.ocr import recognize_text  # type: ignore
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
        """Return (path, confidence) for template key, fallback到用户模板目录。"""
        try:
            tpls = self.cfg.get("templates", {}) if isinstance(self.cfg.get("templates"), dict) else {}
            d = tpls.get(key, {}) if isinstance(tpls, dict) else {}
            p = str((d or {}).get("path", ""))
            if not p:
                p = self._images_path(f"{key}.png")
            try:
                conf = float((d or {}).get("confidence", 0.85))
            except Exception:
                conf = 0.85
            return p, conf
        except Exception:
            return self._images_path(f"{key}.png"), 0.85

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
        y_local = min(line.y for line in lines)
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
        sel = RegionSelector(self, on_done)
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
            from super_buyer.services.ocr import recognize_text  # type: ignore
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
        except Exception:
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
        _json_path = os.path.join(str(self.paths.output_dir), f"{_stem}_res.json")
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
                import cv2 as _cv2  # type: ignore
                import numpy as _np  # type: ignore
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
        path_top = self._images_path("_avg_price_roi_top.png", ensure_parent=True)
        path_bot = self._images_path("_avg_price_roi_bottom.png", ensure_parent=True)
        try:
            bin_top.save(path_top)
            bin_bot.save(path_bot)
        except Exception as e:
            messagebox.showerror("选图片", f"失败: {e}")
            return

        # OCR both halves
        import time as _time
        def _ocr(pil_img):
            raw = ""
            ms = -1.0
            t0 = _time.perf_counter()
            try:
                from super_buyer.services.ocr import recognize_text  # type: ignore
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
            if ms < 0:
                ms = (_time.perf_counter() - t0) * 1000.0
            up = (raw or "").upper()
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
