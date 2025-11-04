"""任务运行器核心实现。"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from super_buyer.config.loader import load_config
from super_buyer.core.common import (
    now_label as _now_label,
    parse_price_text as _parse_price_text,
    safe_int as _safe_int,
    safe_sleep as _sleep,
)
from super_buyer.core.exceptions import FatalOcrError
from super_buyer.core.logging import (
    LOG_LEVELS,
    ensure_level_tag as _ensure_level_tag,
    extract_level_from_msg as _extract_level_from_msg,
    level_name as _level_name,
)
from super_buyer.core.launcher import run_launch_flow
from super_buyer.core.models import Goods
from super_buyer.services.compat import ensure_pyautogui_confidence_compat
from super_buyer.services.history import (
    HistoryPaths,
    append_price as _append_price,
    append_purchase as _append_purchase,
    resolve_paths as _resolve_history_paths,
)
from super_buyer.services.ocr import recognize_numbers, recognize_text
from super_buyer.services.screen_ops import ScreenOps

ensure_pyautogui_confidence_compat()


# ------------------------------ 图像/点击辅助 ------------------------------

class Buyer:
    """单个商品的统一购买流程（严格对齐 purchase_flow.md）。

    模块职责：
    - 模块一：进入搜索结果页（处理阻碍性事件→按首页/市场标识分支→搜索→匹配并缓存商品坐标）。
    - 模块二：购买循环（首次缓存按钮→在详情内读价与购买→价格不合适后关闭）。
    - 失败与恢复：按文档进行恢复性搜索与本轮结束处理。
    """

    def __init__(
        self,
        cfg: Dict[str, Any],
        screen: ScreenOps,
        on_log: Callable[[str], None],
        *,
        history_paths: HistoryPaths,
    ) -> None:
        self.cfg = cfg
        self.screen = screen
        self.on_log = on_log
        self.history_paths = history_paths
        # 商品列表项坐标缓存（临时坐标缓存）
        self._pos_cache: Dict[str, Tuple[int, int, int, int]] = {}
        # 详情按钮首次缓存（跨详情会话复用）
        self._first_detail_cached: Dict[str, bool] = {}
        self._first_detail_buttons: Dict[str, Dict[str, Tuple[int, int, int, int]]] = {}

        # 当前详情页按钮缓存（本次进入详情周期内有效）
        self._detail_ui_cache: Dict[str, Tuple[int, int, int, int]] = {}
        # 最近一次平均价 OCR 是否成功（供上层累计“未识别”次数）
        self._last_avg_ocr_ok: bool = True
        # 平均价 OCR 连续未识别计数（在本类内维护，成功即清零）
        self._avg_ocr_streak: int = 0
        # 统一本模块内的延时（秒），来源于 ScreenOps 的 step_delay（由外层以 ms 配置），默认 15ms
        try:
            self._delay_sec: float = float(getattr(self.screen, "step_delay", 0.015))
        except Exception:
            self._delay_sec = 0.015

    # ------------------------------ 基础工具 ------------------------------
    def _log(self, item_disp: str, purchased: str, msg: str) -> None:
        self.on_log(f"【{_now_label()}】【{item_disp}】【{purchased}】：{msg}")

    @property
    def _pg(self):  # type: ignore
        import pyautogui  # type: ignore

        return pyautogui

    def _move_cursor_top_right(self) -> None:
        try:
            pg = self.screen._pg  # type: ignore[attr-defined]
            sw, sh = pg.size()
            pg.moveTo(max(0, int(sw) - 5), max(0, 5))
        except Exception:
                pass

    def _click_center_screen_once(self) -> None:
        try:
            pg = self.screen._pg  # type: ignore[attr-defined]
            sw, sh = pg.size()
            pg.click(int(sw // 2), int(sh // 2))
        except Exception:
            pass
        # 任意点击后的短等待：给弹层/遮罩关闭动画一点时间
        _sleep(self._delay_sec)
    def _dismiss_success_overlay(self, goods: Optional[Goods] = None) -> None:
        """关闭购买成功遮罩。

        速度优化：
        - 若本次详情已缓存“购买”按钮坐标，则无需大范围移动，直接在当前位置快速单击一次。
        - 若未缓存，则采用“移动至安全区→中间点击→回安全区”的保守路径，避免鼠标遮挡影响模板匹配。
        """
        def _click_here_once() -> None:
            try:
                self.screen._pg.click()  # type: ignore[attr-defined]
            except Exception:
                # 回退到屏幕中心点击
                self._click_center_screen_once()
            # 需求：该步用于关闭购买成功遮罩，点击后需要强制等待300ms
            _sleep(max(0.3, float(getattr(self, "_delay_sec", 0.015))))

        try:
            has_cache = False
            if goods is not None:
                has_cache = (
                    (self._first_detail_buttons.get(goods.id, {}) or {}).get("btn_buy") is not None
                )
            if has_cache:
                _click_here_once()
                # 已在 _click_here_once 内部保证≥100ms 的等待
                return
        except Exception:
            pass
        # 兜底：保守移动策略
        try:
            self._move_cursor_top_right()
            self._click_center_screen_once()
            self._move_cursor_top_right()
        except Exception:
            self._click_center_screen_once()
        # 需求：该步用于关闭购买成功遮罩，点击后需要强制等待100ms
        _sleep(max(0.1, float(getattr(self, "_delay_sec", 0.015))))

    # --- 详情按钮获取与关闭封装（缓存优先） ---
    def _get_btn_box(
        self, goods: Goods, key: str, timeout: float = 0.3
    ) -> Optional[Tuple[int, int, int, int]]:
        """获取详情页按钮的坐标框，优先使用缓存，其次回退到模板匹配。

        依据 purchase_flow.md 的约定：首次进入详情已缓存【购买/关闭/最大】按钮，
        后续点击应优先使用缓存坐标以提升稳定性与性能。
        """
        try:
            # 1) 首次缓存（跨会话）
            box = (self._first_detail_buttons.get(goods.id, {}) or {}).get(key)
            if box is not None:
                return box
        except Exception:
            pass
        try:
            # 2) 当前会话缓存
            box = self._detail_ui_cache.get(key)
            if box is not None:
                return box
        except Exception:
            pass
        # 3) 兜底：模板匹配
        return self.screen.locate(key, timeout=timeout)

    def _close_detail(self, goods: Goods, timeout: float = 0.3) -> bool:
        """关闭详情页（缓存坐标优先，失败回退匹配）。返回是否执行了关闭点击。"""
        c = self._get_btn_box(goods, "btn_close", timeout=timeout)
        if c is not None:
            # 调试：叠加关闭按钮区域
            try:
                self._debug_show_overlay(
                    overlays=[{"rect": c, "label": "按钮-关闭", "fill": (45, 124, 255, 80), "outline": (45, 124, 255)}],
                    stage="关闭详情",
                    template_path=None,
                    save_name="overlay_close_detail.png",
                )
            except Exception:
                pass
            self.screen.click_center(c)
            return True
        return False

    # 统一启动封装（供 TaskRunner 使用）
    def _ensure_ready_v2(self) -> bool:
        def _log(msg: str) -> None:
            try:
                self.on_log(f"【{_now_label()}】【全局】【-】：{msg}")
            except Exception:
                pass
        res = run_launch_flow(self.cfg, on_log=_log)
        if not res.ok:
            try:
                self.on_log(f"【{_now_label()}】【全局】【-】：启动失败：{res.error or res.code}")
            except Exception:
                pass
        return bool(res.ok)

    # ------------------------------ 模块一：进入搜索结果页 ------------------------------
    def _handle_obstacles(self) -> None:
        # 1) 详情未关闭：同时存在关闭与购买按钮 → 关闭
        b = self.screen.locate("btn_buy", timeout=0.1)
        c = self.screen.locate("btn_close", timeout=0.1)
        if (b is not None) and (c is not None):
            self.screen.click_center(c)
            # 关闭详情后短等待，确保返回到列表层级
            # 首页→市场：等待市场页加载到位，防止立即查找失败
            _sleep(0.02)
            return
        # 2) 购买成功弹层：存在 buy_ok → 任意点击 → 点击关闭
        ok = self.screen.locate("buy_ok", timeout=0.1)
        if ok is not None:
            self._click_center_screen_once()
            c2 = self.screen.locate("btn_close", timeout=0.5)
            if c2 is not None:
                self.screen.click_center(c2)
                # 关闭“购买成功”弹层后的沉淀，避免后续误触
                _sleep(0.02)

    def _type_and_search(self, query: str) -> bool:
        sbox = self.screen.locate("input_search", timeout=2.0)
        if sbox is None:
            return False
        self.screen.click_center(sbox)
        # 获取输入焦点的小步等待
        _sleep(0.03)
        self.screen.type_text(query or "", clear_first=True)
        # 等待输入法/联想稳定
        _sleep(0.03)
        btn = self.screen.locate("btn_search", timeout=1.0)
        if btn is None:
            return False
        self.screen.click_center(btn)
        # 触发搜索后留出最短渲染时间
        _sleep(0.02)
        return True

    def _match_and_cache_goods(self, goods: Goods) -> bool:
        if goods.image_path and os.path.exists(goods.image_path):
            box = self._pg_locate_image(goods.image_path, confidence=0.80, timeout=2.5)
            if box is not None:
                self._pos_cache[goods.id] = box
                return True
        return False

    def ensure_search_context(self, goods: Goods, *, item_disp: str, purchased_str: str) -> bool:
        """进入搜索结果页面并缓存商品坐标（模块一）。"""
        self._handle_obstacles()
        # 判断页面类型：优先用首页/市场标识
        in_home = self.screen.locate("home_indicator", timeout=0.4) is not None
        in_market = self.screen.locate("market_indicator", timeout=0.4) is not None
        query = (goods.search_name or "").strip()
        if not query:
            self._log(item_disp, purchased_str, "无有效 search_name，无法建立搜索上下文")
            return False

        if in_home:
            # 首页：市场 → 搜索框 → 输入 → 搜索 → 匹配并缓存
            m = self.screen.locate("btn_market", timeout=2.0)
            if m is None:
                self._log(item_disp, purchased_str, "未找到市场按钮")
                return False
            self.screen.click_center(m)
            # 市场→首页（重置）：等待主页稳定，清理状态残留
            _sleep(0.02)
            if not self._type_and_search(query):
                self._log(item_disp, purchased_str, "未能输入并点击搜索")
                return False
            if not self._match_and_cache_goods(goods):
                self._log(item_disp, purchased_str, "未匹配到商品模板，无法缓存坐标")
                return False
            self._log(item_disp, purchased_str, "已进入搜索结果并缓存坐标（首页分支）")
            return True

        if in_market:
            # 市场页（搜索状态不明，需要重置）：首页 → 市场 → 搜索框 → 输入 → 搜索 → 匹配并缓存
            h = self.screen.locate("btn_home", timeout=2.0)
            if h is None:
                self._log(item_disp, purchased_str, "未找到首页按钮用于重置")
                return False
            self.screen.click_center(h)
            # 首页→市场（重置）：等待市场页稳定
            _sleep(0.02)
            m = self.screen.locate("btn_market", timeout=2.0)
            if m is None:
                self._log(item_disp, purchased_str, "未找到市场按钮（重置阶段）")
                return False
            self.screen.click_center(m)
            _sleep(0.02)
            if not self._type_and_search(query):
                self._log(item_disp, purchased_str, "未能输入并点击搜索（重置阶段）")
                return False
            if not self._match_and_cache_goods(goods):
                self._log(item_disp, purchased_str, "未匹配到商品模板，无法缓存坐标（重置阶段）")
                return False
            self._log(item_disp, purchased_str, "已进入搜索结果并缓存坐标（市场重置分支）")
            return True

        # 无法判定页面：按照文档不继续
        self._log(item_disp, purchased_str, "无法判定当前页面（缺少首页/市场标识）")
        return False

    # ------------------------------ 模块二：购买循环 ------------------------------
    def _open_detail_from_cache_or_match(self, goods: Goods) -> bool:
        # 优先使用缓存坐标
        if goods.id in self._pos_cache:
            # 快速路径：使用缓存坐标直接点击打开详情；验证时缩短匹配超时，减少感知等待
            try:
                _rect = self._pos_cache[goods.id]
                self._debug_show_overlay(
                    overlays=[{"rect": _rect, "label": "进入详情", "fill": (0, 128, 255, 90), "outline": (0, 128, 255)}],
                    stage="进入详情点击",
                    template_path=None,
                    save_name="overlay_enter_detail.png",
                )
            except Exception:
                pass
            self.screen.click_center(self._pos_cache[goods.id])
            b = self.screen.locate("btn_buy", timeout=0.25)
            c = self.screen.locate("btn_close", timeout=0.25)
            if (b is not None) and (c is not None):
                return True
            # 缓存无效
            self._pos_cache.pop(goods.id, None)
        # 回到模板匹配
        if goods.image_path and os.path.exists(goods.image_path):
            box = self._pg_locate_image(goods.image_path, confidence=0.80, timeout=2.5)
            if box is not None:
                self._pos_cache[goods.id] = box
                try:
                    self._debug_show_overlay(
                        overlays=[{"rect": box, "label": "进入详情", "fill": (0, 128, 255, 90), "outline": (0, 128, 255)}],
                        stage="进入详情点击",
                        template_path=None,
                        save_name="overlay_enter_detail.png",
                    )
                except Exception:
                    pass
                self.screen.click_center(box)
                # 回退路径验证也缩短匹配时间，优先提升响应
                b = self.screen.locate("btn_buy", timeout=0.25)
                c = self.screen.locate("btn_close", timeout=0.25)
                if (b is not None) and (c is not None):
                    return True
        return False

    def _ensure_first_detail_buttons(self, goods: Goods) -> None:
        # 仅在本任务第一次进入详情时缓存按钮
        if self._first_detail_cached.get(goods.id):
            # 将首次缓存预热到当前会话缓存
            m = self._first_detail_buttons.get(goods.id) or {}
            self._detail_ui_cache.update(m)
            return
        b = self.screen.locate("btn_buy", timeout=0.4)
        c = self.screen.locate("btn_close", timeout=0.4)
        if (b is not None) and (c is not None):
            cache: Dict[str, Tuple[int, int, int, int]] = {"btn_buy": b, "btn_close": c}
            if (goods.big_category or "").strip() == "弹药":
                m = self.screen.locate("btn_max", timeout=0.3)
                if m is not None:
                    cache["btn_max"] = m
            self._first_detail_buttons[goods.id] = cache
            self._first_detail_cached[goods.id] = True
            self._detail_ui_cache.update(cache)

    def clear_pos(self, goods_id: Optional[str] = None) -> None:
        """清理商品坐标临时缓存。"""
        if goods_id is None:
            try:
                self._pos_cache.clear()
            except Exception:
                self._pos_cache = {}
        else:
            try:
                self._pos_cache.pop(goods_id, None)
            except Exception:
                pass

    def _pg_locate_image(
        self, path: str, confidence: float, timeout: float = 0.0
    ) -> Optional[Tuple[int, int, int, int]]:
        end = time.time() + max(0.0, float(timeout or 0.0))
        while True:
            try:
                box = self._pg.locateOnScreen(path, confidence=float(confidence))
                if box is not None:
                    return (int(box.left), int(box.top), int(box.width), int(box.height))
            except Exception:
                pass
            if time.time() >= end:
                return None
            # 模板匹配短轮询：等待屏幕刷新，降低 CPU 占用
            _sleep(self._delay_sec)

    # ------------------------------ 数量输入辅助（非弹药补货） ------------------------------
    def _find_qty_midpoint(self) -> Optional[Tuple[int, int]]:
        """定位数量输入区域：默认以 qty_minus/qty_plus 的几何中点作为输入框中心。

        返回 (x, y) 屏幕坐标；若模板缺失或未匹配返回 None。
        """
        m = self.screen.locate("qty_minus", timeout=0.2)
        p = self.screen.locate("qty_plus", timeout=0.2)
        if m is None or p is None:
            return None
        mx, my = int(m[0] + m[2] / 2), int(m[1] + m[3] / 2)
        px, py = int(p[0] + p[2] / 2), int(p[1] + p[3] / 2)
        return int((mx + px) / 2), int((my + py) / 2)

    def _focus_and_type_quantity(self, qty: int) -> bool:
        """点击数量输入框中心并输入数量（清空后输入）。"""
        mid = self._find_qty_midpoint()
        if mid is None:
            return False
        try:
            self.screen.click_point(mid[0], mid[1], clicks=1, interval=self._delay_sec)
            _sleep(self._delay_sec)
            self.screen.type_text(str(int(qty)), clear_first=True)
            _sleep(self._delay_sec)
            return True
        except Exception:
            return False

    # ------------------------------ 补货快速循环（Max 一次化/数量输入一次化） ------------------------------
    def _restock_fast_loop(
        self,
        goods: Goods,
        task: Dict[str, Any],
        purchased_so_far: int,
    ) -> Tuple[int, bool]:
        """进入补货快速循环。

        规则：
        - “弹药”：仅首次点击一次 Max，后续循环不再点击 Max。
        - “非弹药”：仅首次点击数量输入框并输入固定数量（统一为 5）。
        - 循环体：读取均价→仅以补货上限判定→购买→等待结果→成功则快速单击关闭遮罩→达标或不满足退出条件则关闭详情并（如达标）回首页。
        返回 (本次累计购买数量, 是否继续外层循环)。
        """
        item_disp = goods.name or goods.search_name or str(task.get("item_name", ""))
        target_total = int(task.get("target_total", 0) or 0)
        bought = 0

        # 计算补货上限
        try:
            restock = int(task.get("restock_price", 0) or 0)
        except Exception:
            restock = 0
        try:
            r_prem = float(task.get("restock_premium_pct", 0.0) or 0.0)
        except Exception:
            r_prem = 0.0
        restock_limit = restock + int(round(restock * max(0.0, r_prem) / 100.0)) if restock > 0 else 0

        # 会话初始化：Max 或 数量输入
        try:
            is_ammo = (goods.big_category or "").strip() == "弹药"
        except Exception:
            is_ammo = False
        used_max = False
        typed_qty = 0
        if is_ammo:
            mx = (self._first_detail_buttons.get(goods.id, {}) or {}).get("btn_max") or self.screen.locate("btn_max", timeout=0.3)
            if mx is not None:
                self.screen.click_center(mx)
                _sleep(self._delay_sec)
                used_max = True
        else:
            # 非弹药：默认数量为 5，无需配置
            if self._focus_and_type_quantity(5):
                typed_qty = 5
            else:
                # 未能定位数量区域：退化为默认 1，继续
                typed_qty = 1

        # 循环直到退出条件
        ocr_fail_streak = 0
        retry_delay = max(self._delay_sec, 0.02)
        while True:
            purchased_str = f"{purchased_so_far + bought}/{target_total}"
            # 价格读取（仅以补货为目标）：expected_floor 仍以阈值/补货价的较大者作为 OCR 合理性下限
            try:
                _thr_base = int(task.get("price_threshold", 0) or 0)
            except Exception:
                _thr_base = 0
            _base = _thr_base if _thr_base > 0 else restock
            unit_price = self._read_avg_unit_price(
                goods,
                item_disp,
                purchased_str,
                expected_floor=_base if _base > 0 else None,
                allow_bottom_fallback=False,
            )
            if unit_price is None or unit_price <= 0:
                ocr_fail_streak += 1
                if ocr_fail_streak < 3:
                    self._log(
                        item_disp,
                        purchased_str,
                        f"平均单价识别失败（补货），准备重试（第 {ocr_fail_streak} 次）",
                    )
                    _sleep(retry_delay)
                    continue
                _ = self._close_detail(goods, timeout=0.4)
                self._log(
                    item_disp,
                    purchased_str,
                    "平均单价识别失败（补货），已关闭详情（重试次数超限）",
                )
                return bought, True
            else:
                ocr_fail_streak = 0

            ok_restock = (restock > 0) and (unit_price <= restock_limit)
            if not ok_restock:
                # 价格不满足：退出补货循环
                _ = self._close_detail(goods, timeout=0.3)
                return bought, True

            # 点击购买
            b = (self._first_detail_buttons.get(goods.id, {}) or {}).get("btn_buy") or self.screen.locate("btn_buy", timeout=0.4)
            if b is None:
                _ = self._close_detail(goods, timeout=0.3)
                self._log(item_disp, purchased_str, "未找到“购买”按钮（补货），已关闭详情")
                return bought, True
            try:
                self._debug_show_overlay(
                    overlays=[{"rect": b, "label": "按钮-购买", "fill": (255, 99, 71, 70), "outline": (255, 99, 71)}],
                    stage="点击购买",
                    template_path=None,
                    save_name="overlay_click_buy.png",
                )
            except Exception:
                pass
            self.screen.click_center(b)

            # 等待结果
            t_end = time.time() + float(getattr(self, "_buy_result_timeout_sec", 0.8))
            got_ok = False
            found_fail = False
            ok_box = None
            fail_box = None
            while time.time() < t_end:
                _ok = self.screen.locate("buy_ok", timeout=0.0)
                if _ok is not None:
                    got_ok = True
                    ok_box = _ok
                    break
                _fail = self.screen.locate("buy_fail", timeout=0.0)
                if _fail is not None:
                    found_fail = True
                    fail_box = _fail
                _sleep(self._delay_sec)

            if got_ok:
                # 累加数量
                if is_ammo:
                    inc = 120 if used_max else 10
                else:
                    inc = max(1, int(typed_qty or 5))
                bought += int(inc)
                # 写历史
                try:
                    _append_purchase(
                        item_id=goods.id,
                        item_name=goods.name or goods.search_name or str(task.get("item_name", "")),
                        price=int(unit_price or 0),
                        qty=int(inc),
                        task_id=str(task.get("id", "")) if task.get("id") else None,
                        task_name=str(task.get("item_name", "")) or None,
                        category=(goods.big_category or "") or None,
                        used_max=bool(used_max),
                        paths=self.history_paths,
                    )
                except Exception:
                    pass
                # 快速关闭成功遮罩
                self._dismiss_success_overlay(goods)

                # 达标：关闭详情并回首页，结束当前片段
                if target_total > 0 and (purchased_so_far + bought) >= target_total:
                    _ = self._close_detail(goods, timeout=0.3)
                    h = self.screen.locate("btn_home", timeout=2.0)
                    if h is not None:
                        self.screen.click_center(h)
                        _sleep(self._delay_sec)
                    return bought, False
                # 否则继续下一轮
                continue

            if found_fail:
                _ = self._close_detail(goods, timeout=0.3)
                self._log(item_disp, purchased_str, "购买失败（补货），已关闭详情")
                return bought, True

            # 结果未知：关闭详情退出
            _ = self._close_detail(goods, timeout=0.3)
            self._log(item_disp, purchased_str, "结果未知（补货），已关闭详情")
            return bought, True

    # ------------------------------ 价格读取 ------------------------------

    def _read_avg_unit_price(
        self,
        goods: Goods,
        item_disp: str,
        purchased_str: str,
        *,
        expected_floor: Optional[int] = None,
        allow_bottom_fallback: bool = True,
    ) -> Optional[int]:
        # 以购买按钮为锚点（与“平均单价预览”一致）
        # 修复：补货购买后界面可能轻微重排，导致首次缓存的按钮坐标失效。
        # 这里优先在“缓存附近的小区域内”快速重定位按钮；失败才回退到缓存或全局匹配。
        t_btn = time.perf_counter()
        prev = self._detail_ui_cache.get("btn_buy")
        buy_box = None
        btn_source = "cache"
        try:
            if prev is not None:
                px, py, pw, ph = prev
                # 以此前坐标为中心扩展一个小区域进行快速匹配（更稳更快）
                try:
                    sw, sh = self._pg.size()  # type: ignore[attr-defined]
                except Exception:
                    sw, sh = 1920, 1080
                margin = int(max(8, min(80, max(int(pw), int(ph)))))
                x0 = max(0, int(px) - margin)
                y0 = max(0, int(py) - margin)
                x1 = min(max(1, int(sw) - 1), int(px + pw) + margin)
                y1 = min(max(1, int(sh) - 1), int(py + ph) + margin)
                region = (int(x0), int(y0), max(1, int(x1 - x0)), max(1, int(y1 - y0)))
                cand = self.screen.locate("btn_buy", region=region, timeout=max(0.0, self._delay_sec))
                if cand is not None:
                    buy_box = cand
                    # 更新本次会话缓存（不动首轮跨会话缓存）
                    self._detail_ui_cache["btn_buy"] = cand
                    btn_source = "region"
        except Exception:
            pass
        if buy_box is None and prev is not None:
            buy_box = prev
        if buy_box is None:
            cand_global = self.screen.locate("btn_buy", timeout=0.3)
            if cand_global is not None:
                buy_box = cand_global
                self._detail_ui_cache["btn_buy"] = cand_global
                btn_source = "global"
        btn_ms = int((time.perf_counter() - t_btn) * 1000.0)
        if buy_box is None:
            self._log(
                item_disp,
                purchased_str,
                f"未匹配到“购买”按钮模板，无法定位平均价格区域 匹配耗时={btn_ms}ms",
            )
            # OCR 失败：标记失败
            self._last_avg_ocr_ok = False
            self._avg_ocr_streak = int(getattr(self, "_avg_ocr_streak", 0)) + 1
            return None
        else:
            self._log(
                item_disp,
                purchased_str,
                f"DEBUG: 购买按钮坐标来源={btn_source} ROI_anchor=({buy_box[0]},{buy_box[1]},{buy_box[2]},{buy_box[3]}) 匹配耗时={btn_ms}ms",
            )
        b_left, b_top, b_w, b_h = buy_box
        avg_cfg = self.cfg.get("avg_price_area") or {}
        try:
            dist = int(avg_cfg.get("distance_from_buy_top", 5) or 5)
            hei = int(avg_cfg.get("height", 45) or 45)
        except Exception:
            dist, hei = 5, 45
        # 临时规则：支持联系人兑换的商品，将距离加 30
        try:
            if bool(getattr(goods, "exchangeable", False)):
                dist += 30
        except Exception:
            pass
        y_bottom = int(b_top - dist)
        y_top = int(y_bottom - hei)
        x_left = int(b_left)
        width = int(max(1, b_w))
        # 约束 ROI 在屏幕范围内
        try:
            sw, sh = self._pg.size()  # type: ignore[attr-defined]
        except Exception:
            sw, sh = 1920, 1080
        if sw <= 0:
            sw = 1920
        if sh <= 0:
            sh = 1080
        y_top = max(0, min(sh - 2, y_top))
        y_bottom = max(y_top + 1, min(sh - 1, y_bottom))
        x_left = max(0, min(sw - 2, x_left))
        width = max(1, min(width, sw - x_left))
        height = max(1, y_bottom - y_top)
        if height <= 0 or width <= 0:
            self._log(item_disp, purchased_str, "平均单价 ROI 计算失败（尺寸无效）")
            self._last_avg_ocr_ok = False
            self._avg_ocr_streak = int(getattr(self, "_avg_ocr_streak", 0)) + 1
            return None
        roi = (x_left, y_top, width, height)
        img = self.screen.screenshot_region(roi)
        # 调试可视化：显示购买按钮与均价 ROI（不影响已截取的 img 内容）
        try:
            self._debug_show_overlay(
                overlays=[
                    {"rect": (b_left, b_top, b_w, b_h), "label": "按钮-购买", "fill": (255, 99, 71, 70), "outline": (255, 99, 71)},
                    {"rect": (x_left, y_top, width, height), "label": "均价OCR区域", "fill": (255, 216, 77, 90), "outline": (255, 216, 77)},
                ],
                stage="详情价复核区域",
                template_path=None,
                save_name="overlay_detail_avg.png",
            )
        except Exception:
            pass
        if img is None:
            self._log(item_disp, purchased_str, "平均单价 ROI 截屏失败")
            self._last_avg_ocr_ok = False
            self._avg_ocr_streak = int(getattr(self, "_avg_ocr_streak", 0)) + 1
            return None
        # 横向分割 ROI：上=平均价，下=合计
        try:
            w0, h0 = img.size
        except Exception:
            self._log(item_disp, purchased_str, "平均单价 ROI 尺寸无效")
            self._last_avg_ocr_ok = False
            self._avg_ocr_streak = int(getattr(self, "_avg_ocr_streak", 0)) + 1
            return None
        if h0 < 2:
            self._log(item_disp, purchased_str, "平均单价 ROI 高度过小，无法二分")
            self._last_avg_ocr_ok = False
            self._avg_ocr_streak = int(getattr(self, "_avg_ocr_streak", 0)) + 1
            return None
        mid_h = h0 // 2
        img_top = img.crop((0, 0, w0, mid_h))
        img_bot = img.crop((0, mid_h, w0, h0))
        self._log(
            item_disp,
            purchased_str,
            f"DEBUG: ROI=({x_left},{y_top},{x_left + width},{y_top + height}) 上半尺寸=({img_top.width}x{img_top.height}) 下半尺寸=({img_bot.width}x{img_bot.height})",
        )
        # 可选缩放（与预览逻辑一致的约束）
        try:
            sc = float(avg_cfg.get("scale", 1.0) or 1.0)
        except Exception:
            sc = 1.0
        if sc < 0.6:
            sc = 0.6
        if sc > 2.5:
            sc = 2.5
        if abs(sc - 1.0) > 1e-3:
            try:
                img_top = img_top.resize(
                    (max(1, int(img_top.width * sc)), max(1, int(img_top.height * sc)))
                )
                img_bot = img_bot.resize(
                    (max(1, int(img_bot.width * sc)), max(1, int(img_bot.height * sc)))
                )
            except Exception:
                pass
        # 对上下两半分别二值化（优先用 cv2；回退 PIL）
        bin_top: Optional[_PIL.Image] = None
        bin_bot: Optional[_PIL.Image] = None
        try:
            import numpy as _np  # type: ignore
            import cv2 as _cv2  # type: ignore
            from PIL import Image as _PIL  # type: ignore

            for _src, _set in ((img_top, "top"), (img_bot, "bot")):
                arr = _np.array(_src)
                bgr = _cv2.cvtColor(arr, _cv2.COLOR_RGB2BGR)
                gray = _cv2.cvtColor(bgr, _cv2.COLOR_BGR2GRAY)
                _thr, th = _cv2.threshold(
                    gray, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU
                )
                if _set == "top":
                    bin_top = _PIL.fromarray(th)
                else:
                    bin_bot = _PIL.fromarray(th)
        except Exception:
            try:
                bin_top = img_top.convert("L").point(lambda p: 255 if p > 128 else 0)  # type: ignore
            except Exception:
                bin_top = img_top
            try:
                bin_bot = img_bot.convert("L").point(lambda p: 255 if p > 128 else 0)  # type: ignore
            except Exception:
                bin_bot = img_bot
        # 调试：保存 ROI 及上下半区（含二值化）到 output/debug/roi
        try:
            dbg = (self.cfg.get("debug", {}) or {})
            save_roi = bool(dbg.get("save_roi_images", True))
        except Exception:
            save_roi = True
        if save_roi:
            try:
                paths_cfg = (self.cfg.get("paths", {}) or {})
                out_root = str(paths_cfg.get("output_dir", "output") or "output")
                roi_dir = Path(out_root) / "debug" / "roi"
                roi_dir.mkdir(parents=True, exist_ok=True)
                ts = time.strftime("%Y%m%d-%H%M%S")
                gid = (goods.id or "item").replace("/", "-")
                base = f"{ts}_{gid}_{purchased_str.replace('/', '-') }"
                # 原始 ROI
                try:
                    img.save(str(roi_dir / f"{base}_roi.png"))
                except Exception:
                    pass
                # 上下半原图
                try:
                    img_top.save(str(roi_dir / f"{base}_top.png"))
                except Exception:
                    pass
                try:
                    img_bot.save(str(roi_dir / f"{base}_bot.png"))
                except Exception:
                    pass
                # 上下半二值图
                try:
                    if bin_top is not None:
                        bin_top.save(str(roi_dir / f"{base}_top_bin.png"))
                except Exception:
                    pass
                try:
                    if bin_bot is not None:
                        bin_bot.save(str(roi_dir / f"{base}_bot_bin.png"))
                except Exception:
                    pass
                try:
                    self._log(
                        item_disp,
                        purchased_str,
                        f"DEBUG: ROI样本已保存 {roi_dir}/{base}_*.png",
                    )
                except Exception:
                    pass
            except Exception:
                pass
        # 使用 utils.ocr_utils 进行数字识别（优先路径）
        try:
            ocfg = self.cfg.get("umi_ocr") or {}
            cands = recognize_numbers(
                bin_top,
                base_url=str(ocfg.get("base_url", "http://127.0.0.1:1224")),
                timeout=float(ocfg.get("timeout_sec", 2.5) or 2.5),
                options=dict(ocfg.get("options", {}) or {}),
                offset=(x_left, y_top),
            ) if bin_top is not None else []
            cand = max([c for c in cands if getattr(c, "value", None) is not None], key=lambda c: int(c.value)) if cands else None  # type: ignore[arg-type]
            val = int(getattr(cand, "value", 0)) if cand is not None and getattr(cand, "value", None) is not None else None
            self._log(
                item_disp,
                purchased_str,
                f"DEBUG: 上半数字候选数={len(cands) if isinstance(cands, list) else 0} 首选值={getattr(cand, 'value', None)}",
            )
        except Exception:
            val = None
        if isinstance(val, int) and val > 0:
            try:
                floor = int(expected_floor or 0)
            except Exception:
                floor = 0
            if floor > 0 and int(val) < max(1, floor // 2):
                # 数字 OCR 结果明显异常：记为失败并返回
                self._last_avg_ocr_ok = False
                self._avg_ocr_streak = int(getattr(self, "_avg_ocr_streak", 0)) + 1
                return None
            self._log(
                item_disp,
                purchased_str,
                f"平均价 OCR 成功 值={val} ROI=({x_left},{y_top},{x_left + width},{y_top + mid_h}) 缩放={sc:.2f} btn耗时={btn_ms}ms",
            )
            try:
                _append_price(
                    item_id=goods.id,
                    item_name=goods.name or goods.search_name or str(item_disp),
                    price=int(val),
                    category=(goods.big_category or "") or None,
                    paths=self.history_paths,
                )
            except Exception:
                pass
            # 数字 OCR 成功：标记成功并清零连续失败计数
            self._last_avg_ocr_ok = True
            self._avg_ocr_streak = 0
            return int(val)
        else:
            self._log(
                item_disp,
                purchased_str,
                "DEBUG: 上半数字识别无有效值，准备文本OCR",
            )
        # 仅对上半部分做 OCR（平均单价），统一使用 Umi-OCR（utils/ocr_utils）
        txt = ""
        ocr_ms = -1
        try:
            ocfg = self.cfg.get("umi_ocr") or {}
            t_ocr = time.perf_counter()
            if bin_top is not None:
                # 使用 recognize_text 获取原始文本，随后本地解析
                boxes = recognize_text(
                    bin_top,
                    base_url=str(ocfg.get("base_url", "http://127.0.0.1:1224")),
                    timeout=float(ocfg.get("timeout_sec", 2.5) or 2.5),
                    options=dict(ocfg.get("options", {}) or {}),
                )
                txt = " ".join((b.text or "").strip() for b in boxes if (b.text or "").strip())
            ocr_ms = int((time.perf_counter() - t_ocr) * 1000.0)
        except Exception as e:
            self._log(
                item_disp,
                purchased_str,
                f"Umi OCR 失败: {e} | ROI=({x_left},{y_top},{x_left + width},{y_top + mid_h}) 缩放={sc:.2f} btn耗时={btn_ms}ms OCR耗时={ocr_ms if ocr_ms >= 0 else '-'}ms",
            )
            raise FatalOcrError(str(e))
        self._log(
            item_disp,
            purchased_str,
            f"DEBUG: 上半文本OCR结果='{(txt or '').strip()[:80]}' 耗时={ocr_ms}ms",
        )
        val = _parse_price_text(txt or "")
        if val is None or val <= 0:
            self._log(
                item_disp,
                purchased_str,
                f"平均价 OCR 解析失败：'{(txt or '').strip()[:64]}' | ROI=({x_left},{y_top},{x_left + width},{y_top + mid_h}) 缩放={sc:.2f} btn耗时={btn_ms}ms OCR耗时={ocr_ms if ocr_ms >= 0 else '-'}ms",
            )
            # 新增：在允许的情况下，尝试对下半部分（合计区域）做数字识别作为兜底。
            # 说明：仅在预补货阶段安全（数量通常为1）；进入补货会话后可能为多数量，禁止兜底。
            if allow_bottom_fallback and bin_bot is not None:
                try:
                    ocfg = self.cfg.get("umi_ocr") or {}
                    cands2 = recognize_numbers(
                        bin_bot,
                        base_url=str(ocfg.get("base_url", "http://127.0.0.1:1224")),
                        timeout=float(ocfg.get("timeout_sec", 2.5) or 2.5),
                        options=dict(ocfg.get("options", {}) or {}),
                        offset=(x_left, y_top + mid_h),
                    )
                    cand2 = max([c for c in cands2 if getattr(c, "value", None) is not None], key=lambda c: int(c.value)) if cands2 else None  # type: ignore[arg-type]
                    val2 = int(getattr(cand2, "value", 0)) if cand2 is not None and getattr(cand2, "value", None) is not None else None
                    self._log(
                        item_disp,
                        purchased_str,
                        f"DEBUG: 下半数字候选数={len(cands2) if isinstance(cands2, list) else 0} 首选值={getattr(cand2, 'value', None)}",
                    )
                except Exception:
                    val2 = None
                if isinstance(val2, int) and val2 > 0:
                    try:
                        floor = int(expected_floor or 0)
                    except Exception:
                        floor = 0
                    if floor > 0 and int(val2) < max(1, floor // 2):
                        # 兜底数值也明显异常：仍记为失败
                        self._last_avg_ocr_ok = False
                        self._avg_ocr_streak = int(getattr(self, "_avg_ocr_streak", 0)) + 1
                        return None
                    # 接受兜底结果
                    self._log(
                        item_disp,
                        purchased_str,
                        f"平均价 OCR 成功(下部兜底) 值={val2} ROI=({x_left},{y_top + mid_h},{x_left + width},{y_top + height}) 缩放={sc:.2f} btn耗时={btn_ms}ms",
                    )
                    try:
                        _append_price(
                            item_id=goods.id,
                            item_name=goods.name or goods.search_name or str(item_disp),
                            price=int(val2),
                            category=(goods.big_category or "") or None,
                            paths=self.history_paths,
                        )
                    except Exception:
                        pass
                    self._last_avg_ocr_ok = True
                    self._avg_ocr_streak = 0
                    return int(val2)
            else:
                if not allow_bottom_fallback:
                    self._log(
                        item_disp,
                        purchased_str,
                        "DEBUG: 补货模式禁用下半兜底",
                    )
                elif bin_bot is None:
                    self._log(
                        item_disp,
                        purchased_str,
                        "DEBUG: 下半图像为空，无法兜底",
                    )
                else:
                    self._log(
                        item_disp,
                        purchased_str,
                        "DEBUG: 下半兜底无有效数值",
                    )
            # 顶部与兜底均失败：记录失败并退出
            self._last_avg_ocr_ok = False
            self._avg_ocr_streak = int(getattr(self, "_avg_ocr_streak", 0)) + 1
            return None
        # 验证：若设置价格存在，且识别值低于 50% 的设置价格，则视为识别错误，本次丢弃
        try:
            floor = int(expected_floor or 0)
        except Exception:
            floor = 0
        if floor > 0 and int(val) < max(1, floor // 2):
            try:
                self._log(
                    item_disp,
                    purchased_str,
                    f"平均价 OCR 异常：值={val} 低于设置价格50%({floor//2})，本次丢弃",
                )
            except Exception:
                pass
            self._last_avg_ocr_ok = False
            self._avg_ocr_streak = int(getattr(self, "_avg_ocr_streak", 0)) + 1
            return None
        self._log(
            item_disp,
            purchased_str,
            f"平均价 OCR 成功 值={val} ROI=({x_left},{y_top},{x_left + width},{y_top + mid_h}) 缩放={sc:.2f} btn耗时={btn_ms}ms OCR耗时={ocr_ms}ms",
        )
        # OCR 成功：标记成功
        self._last_avg_ocr_ok = True
        self._avg_ocr_streak = 0
        # 记录价格历史（按物品），供历史价格与分钟聚合使用
        try:
            _append_price(
                item_id=goods.id,
                item_name=goods.name or goods.search_name or str(item_disp),
                price=int(val),
                category=(goods.big_category or "") or None,
                paths=self.history_paths,
            )
        except Exception:
            pass
        return int(val)


    # ------------------------------ 新版：一次完整购买循环（模块二） ------------------------------
    def purchase_cycle(
        self,
        goods: Goods,
        task: Dict[str, Any],
        purchased_so_far: int,
    ) -> Tuple[int, bool]:
        """执行一次完整的“购买循环”。

        - 打开详情（支持缓存/匹配与一次恢复性搜索）
        - 在详情内读价→判断（补货/普通）→提交→结果判断
        - 在同一详情内重复购买，直到价格不合适，再点击关闭
        返回 (本次累计购买数量, 是否继续外层循环)。
        """
        item_disp = goods.name or goods.search_name or str(task.get("item_name", ""))
        target_total = int(task.get("target_total", 0) or 0)
        purchased_str = f"{purchased_so_far}/{target_total}"

        # 根据反馈优化：不要在每轮开始时先判定“是否已在详情页”（会引入不必要的等待与两次匹配）；
        # 直接尝试使用缓存坐标/模板匹配打开详情；仅在 ROI 识别失败或错误时再处理关闭/恢复。
        used_cache = goods.id in self._pos_cache
        if not self._open_detail_from_cache_or_match(goods):
            if not used_cache:
                ok_ctx = self.ensure_search_context(goods, item_disp=item_disp, purchased_str=purchased_str)
                if ok_ctx and self._open_detail_from_cache_or_match(goods):
                    pass
                else:
                    # 打开详情失败：根据是否有缓存给出具体原因
                    if used_cache:
                        self._log(item_disp, purchased_str, "缓存坐标无效，打开详情失败，本轮结束")
                    else:
                        self._log(item_disp, purchased_str, "未匹配到商品模板，打开详情失败，本轮结束")
                    return 0, True

        # 首次进入详情页：缓存按钮（跨会话复用）
        self._ensure_first_detail_buttons(goods)

        bought = 0
        while True:
            # 设置价格基准：优先阈值，其次补货价
            try:
                _thr_base = int(task.get("price_threshold", 0) or 0)
            except Exception:
                _thr_base = 0
            try:
                _rest_base = int(task.get("restock_price", 0) or 0)
            except Exception:
                _rest_base = 0
            _base = _thr_base if _thr_base > 0 else _rest_base
            unit_price = self._read_avg_unit_price(
                goods,
                item_disp,
                purchased_str,
                expected_floor=_base if _base > 0 else None,
            )
            if unit_price is None or unit_price <= 0:
                # 关闭详情（缓存优先）
                _ = self._close_detail(goods, timeout=0.4)
                self._log(item_disp, purchased_str, "平均单价识别失败，已关闭详情")
                return bought, True

            thr = int(task.get("price_threshold", 0) or 0)
            prem = float(task.get("price_premium_pct", 0.0) or 0.0)
            limit = thr + int(round(thr * max(0.0, prem) / 100.0)) if thr > 0 else 0
            restock = int(task.get("restock_price", 0) or 0)
            # 新增：补货模式允许浮动百分比（restock_premium_pct）。当 >0 时，提高补货上限。
            r_prem = float(task.get("restock_premium_pct", 0.0) or 0.0)
            restock_limit = restock + int(round(restock * max(0.0, r_prem) / 100.0)) if restock > 0 else 0
            ok_restock = (restock > 0) and (unit_price <= restock_limit)
            ok_normal = (limit <= 0) or (unit_price <= limit)
            # 输出同时包含两种模式的“预览线”信息，辅助界面与日志核对
            if restock > 0:
                self._log(
                    item_disp,
                    purchased_str,
                    f"平均单价={unit_price}，阈值≤{limit}(+{int(prem)}%)，补货≤{restock_limit}(+{int(r_prem)}%)",
                )
            else:
                self._log(item_disp, purchased_str, f"平均单价={unit_price}，阈值≤{limit}(+{int(prem)}%)")

            if not ok_restock and not ok_normal:
                # 价格不满足：关闭详情（缓存优先）
                _ = self._close_detail(goods, timeout=0.4)
                return bought, True

            # 一旦满足补货，进入专用的补货快速循环（弹药 Max 一次化 / 非弹药数量输入一次化）
            if ok_restock:
                got_more, cont = self._restock_fast_loop(goods, task, purchased_so_far + bought)
                bought += int(got_more)
                return bought, cont

            b = self._first_detail_buttons.get(goods.id, {}).get("btn_buy") or self.screen.locate("btn_buy", timeout=0.4)
            if b is None:
                # 未找到“购买”按钮：关闭详情（缓存优先）
                _ = self._close_detail(goods, timeout=0.3)
                self._log(item_disp, purchased_str, "未找到“购买”按钮，已关闭详情")
                return bought, True
            try:
                self._debug_show_overlay(
                    overlays=[{"rect": b, "label": "按钮-购买", "fill": (255, 99, 71, 70), "outline": (255, 99, 71)}],
                    stage="点击购买",
                    template_path=None,
                    save_name="overlay_click_buy.png",
                )
            except Exception:
                pass
            self.screen.click_center(b)

            t_end = time.time() + float(getattr(self, "_buy_result_timeout_sec", 0.8))
            got_ok = False
            found_fail = False
            ok_box = None
            fail_box = None
            while time.time() < t_end:
                _ok = self.screen.locate("buy_ok", timeout=0.0)
                if _ok is not None:
                    got_ok = True
                    ok_box = _ok
                    break
                _fail = self.screen.locate("buy_fail", timeout=0.0)
                if _fail is not None:
                    found_fail = True
                    fail_box = _fail
                _sleep(0.02)

            if got_ok:
                # 根据商品类别与是否使用 Max 调整进度增量
                try:
                    is_ammo = (goods.big_category or "").strip() == "弹药"
                except Exception:
                    is_ammo = False
                # 普通模式下不涉及 Max 的补货，会走到这里；保持原有规则
                if is_ammo:
                    inc = 10
                else:
                    inc = 1
                bought += int(inc)
                # 记录购买历史（关联任务与物品）
                try:
                    _append_purchase(
                        item_id=goods.id,
                        item_name=goods.name
                        or goods.search_name
                        or str(task.get("item_name", "")),
                        price=int(unit_price or 0),
                        qty=int(inc),
                        task_id=str(task.get("id", "")) if task.get("id") else None,
                        task_name=str(task.get("item_name", "")) or None,
                        category=(goods.big_category or "") or None,
                        used_max=False,
                        paths=self.history_paths,
                    )
                except Exception:
                    pass
                self._dismiss_success_overlay(goods)
                continue
            if found_fail:
                # 购买失败：关闭详情（缓存优先）
                _ = self._close_detail(goods, timeout=0.3)
                self._log(item_disp, purchased_str, "购买失败，已关闭详情")
                return bought, True
            # 结果未知：关闭详情（缓存优先）
            _ = self._close_detail(goods, timeout=0.3)
            self._log(item_disp, purchased_str, "结果未知，已关闭详情")
            return bought, True

# ------------------------------ 调度/运行器 ------------------------------


def _parse_hhmm(s: str) -> Optional[Tuple[int, int]]:
    try:
        ss = (s or "").strip()
        if not ss:
            return None
        hh, mm = ss.split(":")
        h, m = int(hh), int(mm)
        if 0 <= h <= 23 and 0 <= m <= 59:
            return h, m
    except Exception:
        return None
    return None


def _time_in_window(now_ts: float, start: Optional[str], end: Optional[str]) -> bool:
    """判断当前本地时间是否位于 [start, end] 窗口内，支持跨日。

    start/end 为空则始终返回 True。
    """
    if not start and not end:
        return True
    lt = time.localtime(now_ts)
    now_min = lt.tm_hour * 60 + lt.tm_min
    sp = _parse_hhmm(start or "")
    ep = _parse_hhmm(end or "")
    if sp is None and ep is None:
        return True
    if sp is None:
        sp = (0, 0)
    if ep is None:
        ep = (23, 59)
    smin = sp[0] * 60 + sp[1]
    emin = ep[0] * 60 + ep[1]
    if emin >= smin:
        return smin <= now_min <= emin
    # 跨日时间窗口（例如 22:00~06:00）
    return now_min >= smin or now_min <= emin


class TaskRunner:
    """高层调度器，按照设计执行购买任务。

    - 支持两种模式：轮询时长模式 与 时间窗口模式；
    - 基于统一购买流程与 OCR（货币价格区域）；
    - 提供开始/暂停/继续/停止控制与结构化日志。
    """

    def __init__(
        self,
        *,
        tasks_data: Dict[str, Any],
        cfg_path: str = "config.json",
        goods_path: str = "goods.json",
        output_dir: Optional[str | Path] = None,
        on_log: Optional[Callable[[str], None]] = None,
        on_task_update: Optional[Callable[[int, Dict[str, Any]], None]] = None,
        debug_overrides: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.on_log = on_log or (lambda s: None)
        self.on_task_update = on_task_update or (lambda i, it: None)
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # 配置快照
        self.cfg = load_config(cfg_path)
        # 合并调试覆盖（仅本次运行生效，不落盘）
        if isinstance(debug_overrides, dict):
            try:
                base_dbg = dict(self.cfg.get("debug", {}) or {}) if isinstance(self.cfg.get("debug"), dict) else {}
            except Exception:
                base_dbg = {}
            try:
                nd = dict(base_dbg)
                nd.update(debug_overrides)
                self.cfg["debug"] = nd
            except Exception:
                self.cfg["debug"] = debug_overrides
        self.tasks_data = json.loads(json.dumps(tasks_data or {"tasks": []}))
        self.goods_map: Dict[str, Goods] = self._load_goods(goods_path)
        paths_cfg = self.cfg.get("paths", {}) or {}
        if not isinstance(paths_cfg, dict):
            paths_cfg = {}
        out_dir = Path(output_dir) if output_dir is not None else Path(paths_cfg.get("output_dir", "output"))
        self.history_paths = _resolve_history_paths(out_dir)

        # 派生辅助对象
        # 高级配置：延时单位 ms，默认 15ms；兼容旧字段 step_delays.default（秒）
        adv = self.tasks_data.get("advanced") if isinstance(self.tasks_data.get("advanced"), dict) else {}
        try:
            delay_ms = float((adv or {}).get("delay_ms", 15))
            step_delay = max(0.0, delay_ms / 1000.0)
        except Exception:
            step_delay = 0.015
        # 兼容旧配置：当 advanced 未给出时回退到 step_delays.default（单位秒）
        if (adv or {}).get("delay_ms") is None:
            try:
                step_delay = float(((self.tasks_data.get("step_delays") or {}).get("default", step_delay)) or step_delay)
            except Exception:
                pass
        # 解析调试配置并尝试覆盖步进延时；规范化保存目录
        dbg = (self.cfg.get("debug", {}) or {}) if isinstance(self.cfg.get("debug"), dict) else {}
        try:
            self._debug_enabled = bool(dbg.get("enabled", False))
        except Exception:
            self._debug_enabled = False
        try:
            self._debug_overlay_sec = float(dbg.get("overlay_sec", 5.0) or 5.0)
        except Exception:
            self._debug_overlay_sec = 5.0
        try:
            self._debug_step_sleep = float(dbg.get("step_sleep", 0.0) or 0.0)
        except Exception:
            self._debug_step_sleep = 0.0
        try:
            self._debug_save_overlay_images = bool(dbg.get("save_overlay_images", False))
        except Exception:
            self._debug_save_overlay_images = False
        # 保存目录：默认 output/debug/single_tasks；若为 images/debug/* 则改写到 output/debug 下
        try:
            paths_cfg2 = (self.cfg.get("paths", {}) or {}) if isinstance(self.cfg.get("paths"), dict) else {}
        except Exception:
            paths_cfg2 = {}
        out_root = str(paths_cfg2.get("output_dir", "output") or "output")
        try:
            od = str(dbg.get("overlay_dir", ""))
        except Exception:
            od = ""
        if not od:
            od = os.path.join(out_root, "debug", "single_tasks")
        else:
            try:
                if not os.path.isabs(od):
                    p_norm = od.replace("\\", "/")
                    if p_norm.startswith("images/debug") or p_norm.startswith("images/\\debug"):
                        od = os.path.join(out_root, "debug", os.path.basename(od))
            except Exception:
                pass
        self._debug_overlay_dir = od
        # 覆盖步进延时（仅当启用调试）
        if self._debug_enabled:
            try:
                dd = float(self._debug_step_sleep or 0.0)
            except Exception:
                dd = 0.0
            base_min = 0.02
            if dd < max(step_delay, base_min):
                dd = max(step_delay, base_min)
            if dd > 0.2:
                dd = 0.2
            step_delay = dd
        # 可视化窗口状态
        self._ov_top = None
        self._ov_canvas = None
        self._ov_img_refs = []
        self._overlay_seq = 0
        # 单任务会话的调试叠加分组目录（在 _run 内按轮设置）
        self._loop_dir: Optional[str] = None
        self.screen = ScreenOps(self.cfg, step_delay=step_delay)
        self.buyer = Buyer(
            self.cfg,
            self.screen,
            self._relay_log,
            history_paths=self.history_paths,
        )

        # 将 Runner 的调试叠加能力注入 Buyer
        try:
            setattr(self.buyer, "_debug_show_overlay", self._debug_show_overlay)
        except Exception:
            pass

        # 处罚检测：OCR 连续未识别计数与参数（与多商品模式保持一致）
        self._ocr_miss_streak: int = 0
        try:
            tuning = (self.cfg.get("multi_snipe_tuning", {}) or {})
        except Exception:
            tuning = {}
        try:
            self._ocr_miss_threshold = int(tuning.get("ocr_miss_penalty_threshold", 10) or 10)
        except Exception:
            self._ocr_miss_threshold = 10
        try:
            self._penalty_confirm_delay_sec = float(tuning.get("penalty_confirm_delay_sec", 5.0) or 5.0)
        except Exception:
            self._penalty_confirm_delay_sec = 5.0
        try:
            self._penalty_wait_after_confirm_sec = float(tuning.get("penalty_wait_sec", 180.0) or 180.0)
        except Exception:
            self._penalty_wait_after_confirm_sec = 180.0
        # 购买结果等待时长（与多商品一致，可通过 multi_snipe_tuning.buy_result_timeout_sec 调整）
        try:
            self._buy_result_timeout_sec = float(tuning.get("buy_result_timeout_sec", 0.8) or 0.8)
        except Exception:
            self._buy_result_timeout_sec = 0.8
        # 成功时间戳：用于抑制“刚识别过又检查处罚”的抖动
        self._last_avg_ok_ts: float = 0.0

        # 模式、日志与重启
        self.mode = str(self.tasks_data.get("task_mode", "time"))
        # 日志等级（debug/info/error），默认 info
        self.log_level: str = _level_name(str(self.tasks_data.get("log_level", "info")))
        try:
            self.restart_every_min = int(
                self.tasks_data.get("restart_every_min", 0) or 0
            )
        except Exception:
            self.restart_every_min = 0
        self._next_restart_ts: Optional[float] = None
        # 降噪用：空闲提示的节流时间戳
        self._last_idle_log_ts: float = 0.0

    # ------------------------------ 调试可视化 ------------------------------
    def _debug_active(self) -> bool:
        try:
            return bool(getattr(self, "_debug_enabled", False))
        except Exception:
            return False

    def _debug_screenshot_full(self):
        try:
            return self.screen._pg.screenshot()  # type: ignore[attr-defined]
        except Exception:
            return None

    def _debug_build_annotated(self, base_img, overlays: List[Dict[str, Any]], *, stage: str, template_path: Optional[str] = None):
        try:
            from PIL import Image, ImageDraw  # type: ignore
        except Exception:
            return None
        if base_img is None:
            return None
        try:
            img = base_img.convert("RGBA")
        except Exception:
            return None
        W, H = img.size
        # 半透明全屏蒙版
        try:
            mask = Image.new("RGBA", (W, H), (0, 0, 0, 120))
            img = Image.alpha_composite(img, mask)
        except Exception:
            pass
        try:
            draw = ImageDraw.Draw(img)
        except Exception:
            return None
        # 辅助：rect(x,y,w,h) -> (x1,y1,x2,y2)
        def _xyxy(rect):
            x, y, w, h = rect
            return int(x), int(y), int(x + w), int(y + h)
        # 绘制 ROI
        for ov in overlays or []:
            try:
                rect = tuple(ov.get("rect", ()))
                if len(rect) != 4:
                    continue
                x1, y1, x2, y2 = _xyxy(rect)  # type: ignore[arg-type]
                fill = ov.get("fill", (255, 216, 77, 90))
                outline = ov.get("outline", (255, 216, 77))
                # 先以叠加层方式绘制填充，再画描边
                try:
                    roi_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                    roi_draw = ImageDraw.Draw(roi_layer)
                    roi_draw.rectangle([x1, y1, x2, y2], fill=tuple(fill))
                    img = Image.alpha_composite(img, roi_layer)
                    draw = ImageDraw.Draw(img)
                except Exception:
                    pass
                try:
                    draw.rectangle([x1, y1, x2, y2], outline=tuple(outline), width=2)
                except Exception:
                    pass
                # 标签背景 + 文本（中文字体）
                label = str(ov.get("label", "") or "")
                if label:
                    try:
                        tw = draw.textlength(label)
                        th = 16
                    except Exception:
                        tw, th = len(label) * 8, 16
                    pad = 4
                    bx1, by1 = x1 + 2, y1 + 2
                    bx2, by2 = bx1 + int(tw) + pad * 2, by1 + th + pad
                    try:
                        draw.rectangle([bx1, by1, bx2, by2], fill=(0, 0, 0, 160))
                    except Exception:
                        pass
                    try:
                        from super_buyer.services.font_loader import draw_text as _draw_text  # type: ignore
                        _draw_text(draw, (bx1 + pad, by1 + pad // 2), label, fill=(255, 255, 255, 255), size=14)
                    except Exception:
                        try:
                            draw.text((bx1 + pad, by1 + pad // 2), label, fill=(255, 255, 255, 255))
                        except Exception:
                            pass
            except Exception:
                continue
        # 阶段标题
        if stage:
            t = f"调试：{stage}"
            try:
                tw = draw.textlength(t)
                th = 18
            except Exception:
                tw, th = len(t) * 8, 18
            cx, top = W // 2, 20
            try:
                draw.rectangle([cx - tw // 2 - 8, top - 6, cx + tw // 2 + 8, top + th + 6], fill=(0, 0, 0, 170))
            except Exception:
                pass
            try:
                from super_buyer.services.font_loader import draw_text as _draw_text  # type: ignore
                _draw_text(draw, (cx - tw // 2, top), t, fill=(255, 255, 255, 255), size=16)
            except Exception:
                try:
                    draw.text((cx - tw // 2, top), t, fill=(255, 255, 255, 255))
                except Exception:
                    pass
        # 模板原图贴图
        try:
            import os
            if template_path and os.path.exists(template_path):
                from PIL import Image as _PILImage  # type: ignore
                # 选择目标 ROI：优先 label 含“模板”，否则第一个
                target_rect = None
                for ov in overlays or []:
                    lb = str(ov.get("label", ""))
                    if "模板" in lb:
                        target_rect = ov.get("rect")
                        break
                if target_rect is None and overlays:
                    target_rect = overlays[0].get("rect")
                if target_rect:
                    x1, y1, x2, y2 = _xyxy(target_rect)
                    tpl = _PILImage.open(template_path).convert("RGBA")
                    maxw = 200
                    ratio = min(1.0, maxw / max(1, tpl.width))
                    tw, th = max(1, int(tpl.width * ratio)), max(1, int(tpl.height * ratio))
                    try:
                        tpl = tpl.resize((tw, th))
                    except Exception:
                        pass
                    px = x2 + 10
                    py = y1
                    if px + tw > W - 8:
                        px = x1 - 10 - tw
                    if px < 8:
                        px = max(8, min(W - tw - 8, x1))
                    py = max(8, min(H - th - 8, py))
                    try:
                        draw.rectangle([px - 6, py - 24, px + tw + 6, py + th + 6], fill=(0, 0, 0, 180))
                    except Exception:
                        pass
                    try:
                        from super_buyer.services.font_loader import draw_text as _draw_text  # type: ignore
                        _draw_text(draw, (px, py - 18), "模板原图", fill=(255, 255, 255, 255), size=14)
                    except Exception:
                        try:
                            draw.text((px, py - 18), "模板原图", fill=(255, 255, 255, 255))
                        except Exception:
                            pass
                    try:
                        img.alpha_composite(tpl, dest=(px, py))
                    except Exception:
                        pass
        except Exception:
            pass
        try:
            return img.convert("RGB")
        except Exception:
            return None

    def _debug_show_overlay(self, overlays: List[Dict[str, Any]], *, stage: str, template_path: Optional[str] = None, save_name: Optional[str] = None) -> None:
        if not self._debug_active():
            return
        try:
            self._overlay_seq += 1
            seq = int(self._overlay_seq)
        except Exception:
            seq = 1
            self._overlay_seq = 1
        def _clean(s: str) -> str:
            s = str(s or "").strip()
            for ch in ("/", "\\", ":", "*", "?", "\"", "<", ">", "|", " "):
                s = s.replace(ch, "_")
            return s[:60] or "overlay"
        fname = save_name or f"{_clean(stage)}.png"
        fname = f"{seq:03d}_{fname}"
        delay_ms = int(max(0.0, float(getattr(self, "_debug_overlay_sec", 5.0))) * 1000)
        # 输出一次可视化摘要（DEBUG）
        try:
            self._relay_log(
                f"【DEBUG】[可视化] 阶段={stage} 叠加数={len(overlays) if overlays else 0} 持续={getattr(self,'_debug_overlay_sec',0)}s 保存={'on' if getattr(self,'_debug_save_overlay_images', False) else 'off'} seq={seq}"
            )
        except Exception:
            pass

        # 无 Tk：静态截图上绘制并可选保存
        try:
            import tkinter as tk  # type: ignore
            root = getattr(tk, "_default_root", None)
        except Exception:
            root = None
        if root is None:
            base = self._debug_screenshot_full()
            if base is not None and bool(getattr(self, "_debug_save_overlay_images", False)):
                annotated = self._debug_build_annotated(base, overlays, stage=stage, template_path=template_path)
                if annotated is not None:
                    try:
                        target_dir = getattr(self, "_loop_dir", None) or self._debug_overlay_dir
                        os.makedirs(target_dir, exist_ok=True)
                        annotated.save(os.path.join(target_dir, fname))
                    except Exception:
                        pass
            _sleep(float(delay_ms) / 1000.0)
            return

        # Tk 路径：创建/复用全屏蒙版窗口绘制
        done = threading.Event()
        def _spawn():
            try:
                W = int(root.winfo_screenwidth())
                H = int(root.winfo_screenheight())
            except Exception:
                W, H = 1920, 1080
            try:
                import tkinter as tk  # type: ignore
                top = getattr(self, "_ov_top", None)
                cv = getattr(self, "_ov_canvas", None)
                if top is None or cv is None:
                    t = tk.Toplevel(root)
                    try:
                        t.attributes("-topmost", True)
                    except Exception:
                        pass
                    t.overrideredirect(True)
                    try:
                        t.attributes("-alpha", 0.35)
                    except Exception:
                        pass
                    try:
                        t.geometry(f"{W}x{H}+0+0")
                    except Exception:
                        pass
                    c = tk.Canvas(t, width=W, height=H, highlightthickness=0, bg="#000000")
                    try:
                        c.pack(fill=tk.BOTH, expand=True)
                    except Exception:
                        c.pack()
                    self._ov_top = t
                    self._ov_canvas = c
                    top, cv = t, c
                else:
                    try:
                        top.deiconify()
                    except Exception:
                        pass
                    try:
                        top.geometry(f"{W}x{H}+0+0")
                    except Exception:
                        pass
                    try:
                        cv.delete("all")
                    except Exception:
                        pass
                # 顶部阶段标题
                try:
                    from super_buyer.services.font_loader import tk_font as _tk_font  # type: ignore
                except Exception:
                    _tk_font = None
                try:
                    title = f"调试：{stage}" if stage else "调试"
                    f = _tk_font(top, 14) if _tk_font else None
                    if f is not None:
                        cv.create_text(W // 2, 20, text=title, fill="#ffffff", font=f)
                    else:
                        cv.create_text(W // 2, 20, text=title, fill="#ffffff")
                except Exception:
                    pass
                for ov in overlays or []:
                    try:
                        rect = tuple(ov.get("rect", ()))
                        if len(rect) != 4:
                            continue
                        x, y, w, h = [int(v) for v in rect]
                        fill = ov.get("fill", (255, 216, 77, 90))
                        outline = ov.get("outline", (255, 216, 77))
                        label = str(ov.get("label", ""))
                        try:
                            cv.create_rectangle(x, y, x + w, y + h, outline="#%02x%02x%02x" % (outline[0], outline[1], outline[2]))
                        except Exception:
                            pass
                        try:
                            for yy in range(y, y + h, 3):
                                cv.create_line(x, yy, x + w, yy, fill="#%02x%02x%02x" % (fill[0], fill[1], fill[2]))
                        except Exception:
                            pass
                        if label:
                            try:
                                if _tk_font is not None:
                                    f2 = _tk_font(top, 12)
                                    cv.create_text(x + 6, y + 6, anchor="nw", text=label, fill="white", font=f2)
                                else:
                                    cv.create_text(x + 6, y + 6, anchor="nw", text=label, fill="white")
                            except Exception:
                                pass
                    except Exception:
                        continue
                # 若提供模板，贴图到 ROI 旁
                try:
                    import os
                    from PIL import Image, ImageTk  # type: ignore
                    target_rect = None
                    for ov in overlays or []:
                        if "模板" in str(ov.get("label", "")):
                            target_rect = ov.get("rect")
                            break
                    if target_rect is None and overlays:
                        target_rect = overlays[0].get("rect")
                    if template_path and os.path.exists(template_path) and target_rect:
                        rx, ry, rw, rh = [int(v) for v in target_rect]  # type: ignore
                        tpl = Image.open(template_path).convert("RGBA")
                        maxw = 200
                        ratio = min(1.0, maxw / max(1, tpl.width))
                        tw, th = max(1, int(tpl.width * ratio)), max(1, int(tpl.height * ratio))
                        try:
                            tpl = tpl.resize((tw, th))
                        except Exception:
                            pass
                        px = rx + rw + 10
                        py = ry
                        if px + tw > W - 8:
                            px = rx - 10 - tw
                        if px < 8:
                            px = max(8, min(W - tw - 8, rx))
                        py = max(8, min(H - th - 8, py))
                        try:
                            cv.create_rectangle(px - 6, py - 24, px + tw + 6, py + th + 6, fill="#000000", outline="")
                            title2 = "模板原图"
                            if _tk_font is not None:
                                f3 = _tk_font(top, 11)
                                cv.create_text(px, py - 12, text=title2, fill="#ffffff", anchor="w", font=f3)
                            else:
                                cv.create_text(px, py - 12, text=title2, fill="#ffffff", anchor="w")
                        except Exception:
                            pass
                        try:
                            ph = ImageTk.PhotoImage(tpl)
                            self._ov_img_refs.append(ph)
                            cv.create_image(px, py, image=ph, anchor="nw")
                        except Exception:
                            pass
                except Exception:
                    pass
                # 可选：保存真实屏幕截图（含叠加）
                if bool(getattr(self, "_debug_save_overlay_images", False)):
                    def _capture_and_save():
                        try:
                            import pyautogui as _pg  # type: ignore
                            img = _pg.screenshot()
                            loop_dir = getattr(self, "_loop_dir", None) or self._debug_overlay_dir
                            os.makedirs(loop_dir, exist_ok=True)
                            img.save(os.path.join(loop_dir, fname))
                        except Exception:
                            pass
                    try:
                        top.after(80, _capture_and_save)
                    except Exception:
                        pass
                try:
                    top.after(delay_ms, lambda: (top.withdraw(), done.set()))
                except Exception:
                    done.set()
            except Exception:
                done.set()
        try:
            import threading as _th
            if _th.current_thread() is _th.main_thread():
                _spawn()
            else:
                try:
                    root.after(0, _spawn)
                except Exception:
                    _spawn()
        except Exception:
            _spawn()
        try:
            done.wait(timeout=max(0.1, float(delay_ms) / 1000.0 + 0.1))
        except Exception:
            pass

    # ------------------------------ 对外 API ------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._pause.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def pause(self) -> None:
        self._pause.set()
        self._relay_log(f"【{_now_label()}】【全局】【-】：已暂停")
        # 使缓存的商品位置失效，确保恢复后重新匹配更安全
        try:
            self.buyer.clear_pos()
        except Exception:
            pass

    def resume(self) -> None:
        if self._pause.is_set():
            self._pause.clear()
            self._relay_log(f"【{_now_label()}】【全局】【-】：继续执行")

    def stop(self) -> None:
        self._stop.set()
        self._relay_log(f"【{_now_label()}】【全局】【-】：终止信号已发送")

    # ------------------------------ 内部辅助 ------------------------------
    def _relay_log(self, s: str) -> None:
        """统一日志出口：补齐等级标签并按等级过滤后再输出到 UI。"""
        try:
            lv = _extract_level_from_msg(s)
            msg = _ensure_level_tag(s, lv if lv else "info")
            if LOG_LEVELS.get(lv, 20) < LOG_LEVELS.get(self.log_level, 20):
                return
            self.on_log(msg)
        except Exception:
            pass

    def set_log_level(self, level: str) -> None:
        """动态设置日志等级（debug/info/error）。"""
        self.log_level = _level_name(level)

    def _load_goods(self, path: str) -> Dict[str, Goods]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                arr = json.load(f)
        except Exception:
            arr = []
        goods_map: Dict[str, Goods] = {}
        base_dir = Path(path).resolve().parent  # data/
        if isinstance(arr, list):
            for e in arr:
                if not isinstance(e, dict):
                    continue
                gid = str(e.get("id", ""))
                if not gid:
                    continue
                # 解析相对图片路径为 data/ 下的绝对路径；兼容反斜杠
                raw_img = str(e.get("image_path", ""))
                try:
                    pp = Path(raw_img)
                    if not pp.is_absolute():
                        norm = raw_img.replace("\\", "/")
                        img_abs = str((base_dir / norm).resolve()) if norm else ""
                    else:
                        img_abs = str(pp)
                except Exception:
                    img_abs = raw_img
                goods_map[gid] = Goods(
                    id=gid,
                    name=str(e.get("name", "")),
                    search_name=str(e.get("search_name", "")),
                    image_path=img_abs,
                    big_category=str(e.get("big_category", "")),
                    sub_category=str(e.get("sub_category", "")),
                    exchangeable=bool(e.get("exchangeable", False)),
                )
        return goods_map

    def _ensure_ready(self) -> bool:
        # 使用 Buyer 内部的更稳健的启动流程
        return self.buyer._ensure_ready_v2()

    def _should_restart_now(self) -> bool:
        if self.restart_every_min <= 0:
            return False
        if self._next_restart_ts is None:
            self._next_restart_ts = time.time() + self.restart_every_min * 60
            return False
        return time.time() >= self._next_restart_ts

    def _do_soft_restart(self, goods: Optional[Goods] = None) -> float:
        """严格按文档执行软重启步骤，返回本次重启耗时（秒）。"""
        t0 = time.time()
        self._relay_log(f"【{_now_label()}】【全局】【-】：到达重启周期，尝试重启游戏…")
        # 1) 首页 → 等待 ~5s
        h = self.screen.locate("btn_home", timeout=1.0)
        if h is not None:
            self.screen.click_center(h)
        # 大步延迟：等待返回首页动画与资源回收（约 5s）
        _sleep(5.0)
        # 2) 设置 → 等待 ~5s
        s = self.screen.locate("btn_settings", timeout=1.0)
        if s is not None:
            self.screen.click_center(s)
        # 大步延迟：设置页元素加载与状态持久化（约 5s）
        _sleep(5.0)
        # 3) 退出 → 等待 ~5s
        e = self.screen.locate("btn_exit", timeout=1.0)
        if e is not None:
            self.screen.click_center(e)
        # 大步延迟：退出流程出现确认弹窗/黑场过渡（约 5s）
        _sleep(5.0)
        # 4) 退出确认 → 等待 ~30s
        ec = self.screen.locate("btn_exit_confirm", timeout=1.0)
        if ec is not None:
            self.screen.click_center(ec)
        # 超大步延迟：完全退出到桌面并释放进程（约 30s）
        _sleep(30.0)
        # 5) 执行统一启动流程
        def _on_log(s: str) -> None:
            self._relay_log(f"【{_now_label()}】【全局】【-】：{s}")
        res = run_launch_flow(self.cfg, on_log=_on_log)
        if not res.ok:
            self._relay_log(f"【{_now_label()}】【全局】【-】：重启失败：{res.error or res.code}，终止任务")
            self._stop.set()
        # 6) 重建搜索上下文
        if goods is not None:
            try:
                self.buyer.clear_pos(goods.id)
                item_disp = goods.name or goods.search_name or "-"
                self.buyer.ensure_search_context(goods, item_disp=item_disp, purchased_str="-")
            except Exception:
                pass
        # 7) 重置重启计时点
        self._next_restart_ts = time.time() + max(1, self.restart_every_min) * 60
        return time.time() - t0

    # ------------------------------ 主循环 ------------------------------
    def _run(self) -> None:
        try:
            if not self._ensure_ready():
                return
            # 开始前重置调试序号，并准备本次会话的叠加保存目录
            try:
                self._overlay_seq = 0
                if bool(getattr(self, "_debug_save_overlay_images", False)):
                    loop_name = time.strftime("%Y%m%d-%H%M%S")
                    self._loop_dir = os.path.join(self._debug_overlay_dir, loop_name)
            except Exception:
                pass
            # 调试模式提示与可视化试运行
            try:
                if getattr(self, "_debug_enabled", False):
                    self._relay_log(
                        f"【{_now_label()}】【全局】【-】：调试模式已启用 overlay={getattr(self,'_debug_overlay_sec',5.0)}s step={getattr(self,'_debug_step_sleep',0.0)} 保存={'on' if getattr(self,'_debug_save_overlay_images', False) else 'off'} 目录={getattr(self,'_debug_overlay_dir','')}"
                    )
                    # 小块提示叠加（左上角 320x80），验证可视化链路
                    self._debug_show_overlay(
                        overlays=[{"rect": (20, 20, 320, 80), "label": "调试启用", "fill": (0, 128, 255, 90), "outline": (0, 128, 255)}],
                        stage="调试启用提示",
                        save_name="overlay_debug_start.png",
                    )
            except Exception:
                pass
            # 规范化任务列表
            tasks: List[Dict[str, Any]] = list(self.tasks_data.get("tasks", []) or [])
            # 稳定排序（按 order 升序）
            try:
                tasks.sort(key=lambda d: int(d.get("order", 0)))
            except Exception:
                pass
            # 校验任务：必须有 item_id → goods.search_name 非空且 image_path 存在
            self._validate_tasks(tasks)
            # 初始化进度字段
            for t in tasks:
                t.setdefault("purchased", 0)
                t.setdefault("executed_ms", 0)
                t.setdefault("status", "idle")

            # 执行前给出一次任务摘要，便于判断“卡住”的原因
            try:
                total = len(tasks)
                enabled = sum(1 for x in tasks if bool(x.get("enabled", True)))
                valid = sum(1 for x in tasks if bool(x.get("_valid", True)))
                runnable = 0
                for x in tasks:
                    if not bool(x.get("enabled", True)) or not bool(x.get("_valid", True)):
                        continue
                    tgt = int(x.get("target_total", 0) or 0)
                    pur = int(x.get("purchased", 0) or 0)
                    if tgt > 0 and pur >= tgt:
                        continue
                    runnable += 1
                self._relay_log(
                    f"【{_now_label()}】【全局】【-】：任务摘要 total={total} enabled={enabled} valid={valid} runnable={runnable}"
                )
            except Exception:
                pass

            self._next_restart_ts = None

            if str(self.mode or "time") == "round":
                self._run_round_robin(tasks)
            else:
                self._run_time_window(tasks)
        except Exception as e:
            try:
                self._relay_log(f"【{_now_label()}】【全局】【-】：运行异常：{e}")
            except Exception:
                pass

    def _run_round_robin(self, tasks: List[Dict[str, Any]]) -> None:
        # 按顺序循环执行已启用的任务
        idx = 0
        n = len(tasks)
        while not self._stop.is_set():
            if n == 0:
                # 任务列表为空：定期提示一次
                try:
                    now = time.time()
                    if now - self._last_idle_log_ts > 5.0:
                        self._relay_log(f"【{_now_label()}】【全局】【-】：任务列表为空，等待中…")
                        self._last_idle_log_ts = now
                except Exception:
                    pass
                # 空闲等待：列表为空时放慢轮询至 600ms，降低 CPU 与日志噪声
                _sleep(0.6)
                continue
            # 计算当前可执行的任务数量（启用+有效+未达目标）
            runnable = 0
            for _t in tasks:
                if not bool(_t.get("enabled", True)) or not bool(_t.get("_valid", True)):
                    continue
                tgt = int(_t.get("target_total", 0) or 0)
                pur = int(_t.get("purchased", 0) or 0)
                if tgt > 0 and pur >= tgt:
                    continue
                runnable += 1
            if runnable == 0:
                try:
                    now = time.time()
                    if now - self._last_idle_log_ts > 5.0:
                        self._relay_log(
                            f"【{_now_label()}】【全局】【-】：没有可执行的任务（未启用/无效/已达目标），等待中…"
                        )
                        self._last_idle_log_ts = now
                except Exception:
                    pass
                # 无可执行任务：1s 节奏避免忙等
                _sleep(1.0)
                continue
            t = tasks[idx % n]
            if not bool(t.get("enabled", True)):
                idx += 1
                continue
            if not bool(t.get("_valid", True)):
                idx += 1
                continue
            # 达到目标则跳过
            target = int(t.get("target_total", 0) or 0)
            purchased = int(t.get("purchased", 0) or 0)
            if target > 0 and purchased >= target:
                idx += 1
                continue

            # 开始片段执行
            duration_min = int(t.get("duration_min", 10) or 10)
            t["status"] = "running"
            seg_start = time.time()
            seg_end = seg_start + duration_min * 60
            item_disp = str(t.get("item_name", ""))
            self._relay_log(
                f"【{_now_label()}】【{item_disp}】【{purchased}/{target}】：开始片段，时长 {duration_min} 分钟"
            )
            # 片段开始时执行一次“进入搜索结果页”（模块一）
            gid = str(t.get("item_id", ""))
            goods = self.goods_map.get(gid)
            if not goods:
                idx += 1
                continue
            try:
                self.buyer.clear_pos(goods.id)
                item_disp = goods.name or goods.search_name or str(t.get("item_name", ""))
                if not self.buyer.ensure_search_context(goods, item_disp=item_disp, purchased_str=f"{purchased}/{target}"):
                    self._relay_log(f"【{_now_label()}】【{item_disp}】【{purchased}/{target}】：建立搜索上下文失败，跳过片段")
                    idx += 1
                    continue
            except FatalOcrError as e:
                self._relay_log(
                    f"【{_now_label()}】【全局】【-】：Umi OCR 失败（片段初始化），终止任务：{e}"
                )
                self._stop.set()
                break
            # 在片段内执行购买循环（不重复搜索）
            search_ready = True
            seg_paused_sec = 0.0
            while not self._stop.is_set() and time.time() < seg_end:
                # 暂停处理
                while self._pause.is_set() and not self._stop.is_set():
                    # 暂停中：200ms 轮询，保证响应并降低占用
                    _sleep(0.2)
                if self._stop.is_set():
                    break

                # 在每轮购买循环开始前检查重启（且暂停片段计时）
                if self._should_restart_now():
                    paused = self._do_soft_restart(goods)
                    seg_paused_sec += max(0.0, float(paused))
                    search_ready = False
                if not search_ready:
                    # 精确地再执行一次“进入搜索结果页”
                    self.buyer.clear_pos(goods.id)
                    _ = self.buyer.ensure_search_context(
                        goods,
                        item_disp=goods.name or goods.search_name or str(t.get("item_name", "")),
                        purchased_str=f"{purchased}/{target}",
                    )
                    search_ready = True

                # 执行一次购买尝试
                try:
                    got, _cont = self.buyer.purchase_cycle(goods, t, purchased)
                except FatalOcrError as e:
                    self._relay_log(
                        f"【{_now_label()}】【全局】【-】：Umi OCR 失败（循环中），终止任务：{e}"
                    )
                    self._stop.set()
                    break
                if got > 0:
                    purchased += int(got)
                    t["purchased"] = purchased
                    # 通知 UI 更新任务项
                    try:
                        self.on_task_update(tasks.index(t), dict(t))
                    except Exception:
                        pass
                if not _cont:
                    break
                # 统计 OCR 未识别连续次数并触发处罚检测
                try:
                    last_ok = bool(getattr(self.buyer, "_last_avg_ocr_ok", True))
                    if last_ok:
                        self._ocr_miss_streak = 0
                        self._last_avg_ok_ts = time.time()
                    else:
                        # 也参考 Buyer 内部的本地连败计数
                        buyer_streak = int(getattr(self.buyer, "_avg_ocr_streak", 0))
                        self._ocr_miss_streak = int(self._ocr_miss_streak) + 1
                        # 需满足：达到阈值 且 距离上次成功已超过 penalty_confirm_delay_sec
                        if (
                            int(self._ocr_miss_streak) >= max(1, int(self._ocr_miss_threshold))
                            and buyer_streak >= 1
                            and (time.time() - float(getattr(self, "_last_avg_ok_ts", 0.0))) >= float(max(2.0, self._penalty_confirm_delay_sec))
                        ):
                            self._check_and_handle_penalty()
                except Exception:
                    pass
                # 每轮尝试之间的微等待：避免连击触发节流/误判
                _sleep(0.02)

            # 片段收尾与统计
            elapsed = int((time.time() - seg_start - seg_paused_sec) * 1000)
            t["executed_ms"] = int(t.get("executed_ms", 0) or 0) + max(0, elapsed)
            t["status"] = "idle"
            self._relay_log(
                f"【{_now_label()}】【{item_disp}】【{purchased}/{target}】：片段结束，累计 {elapsed} ms"
            )
            idx += 1

    def _run_time_window(self, tasks: List[Dict[str, Any]]) -> None:
        # 当没有命中任务窗口时，间隔 1–3 秒轮询
        while not self._stop.is_set():
            # （全局）暂停处理
            while self._pause.is_set() and not self._stop.is_set():
                # 全局暂停：200ms 轮询等待恢复
                _sleep(0.2)
            if self._stop.is_set():
                break

            # 选择时间窗口包含当前时间的第一个任务
            now = time.time()
            chosen_idx = None
            for i, t in enumerate(tasks):
                if not bool(t.get("enabled", True)):
                    continue
                if not bool(t.get("_valid", True)):
                    continue
                if _time_in_window(
                    now, str(t.get("time_start", "")), str(t.get("time_end", ""))
                ):
                    chosen_idx = i
                    break
            if chosen_idx is None:
                # 当前时间无任务命中：定期提示一次
                try:
                    now = time.time()
                    if now - self._last_idle_log_ts > 5.0:
                        self._relay_log(f"【{_now_label()}】【全局】【-】：无任务命中当前时间窗口，等待中…")
                        self._last_idle_log_ts = now
                except Exception:
                    pass
                # 无窗口命中：1.2s 轮询频率，兼顾响应与低占用
                _sleep(1.2)
                continue

            t = tasks[chosen_idx]
            # 达到目标则跳过
            target = int(t.get("target_total", 0) or 0)
            purchased = int(t.get("purchased", 0) or 0)
            if target > 0 and purchased >= target:
                # 已达目标：短暂停顿 800ms，避免刷屏
                _sleep(0.8)
                continue

            # 计算窗口结束时间（用于显示）
            te = str(t.get("time_end", ""))
            # 仅用于显示；当窗口不再匹配时循环会自然退出
            self._relay_log(
                f"【{_now_label()}】【{t.get('item_name', '')}】【{purchased}/{target}】：进入时间窗口执行（结束 {te or '—:—'}）"
            )

            # 进入窗口时建立一次搜索上下文（模块一）
            gid = str(t.get("item_id", ""))
            goods = self.goods_map.get(gid)
            if not goods:
                # 缺少商品定义：1s 后重试
                _sleep(1.0)
                continue
            try:
                item_disp = goods.name or goods.search_name or str(t.get("item_name", ""))
                if not self.buyer.ensure_search_context(goods, item_disp=item_disp, purchased_str=f"{purchased}/{target}"):
                    # 无法建立上下文：1s 后再尝试，等待页面稳定
                    _sleep(1.0)
                    continue
            except FatalOcrError as e:
                self._relay_log(
                    f"【{_now_label()}】【全局】【-】：Umi OCR 失败（进入窗口），终止任务：{e}"
                )
                self._stop.set()
                break

            # 执行直至窗口结束或收到停止/暂停
            search_ready = True
            while not self._stop.is_set():
                # 时间窗口检查
                if not _time_in_window(
                    time.time(),
                    str(t.get("time_start", "")),
                    str(t.get("time_end", "")),
                ):
                    break
                while self._pause.is_set() and not self._stop.is_set():
                    # 窗口内暂停：200ms 轮询等待恢复
                    _sleep(0.2)
                if self._stop.is_set():
                    break
                # 重启检查（不涉及片段时长暂停）
                if self._should_restart_now():
                    self._do_soft_restart(goods)
                    search_ready = False
                if not search_ready:
                    # 重启后重新建立搜索上下文
                    _ = self.buyer.ensure_search_context(
                        goods,
                        item_disp=goods.name or goods.search_name or str(t.get("item_name", "")),
                        purchased_str=f"{purchased}/{target}",
                    )
                    search_ready = True
                # 执行一次购买尝试（不重复搜索）
                try:
                    got, _cont = self.buyer.purchase_cycle(goods, t, purchased)
                except FatalOcrError as e:
                    self._relay_log(
                        f"【{_now_label()}】【全局】【-】：Umi OCR 失败（窗口循环），终止任务：{e}"
                    )
                    self._stop.set()
                    break
                if got > 0:
                    purchased += int(got)
                    t["purchased"] = purchased
                    try:
                        self.on_task_update(tasks.index(t), dict(t))
                    except Exception:
                        pass
                if not _cont:
                    break
                # 统计 OCR 未识别连续次数并触发处罚检测（时间窗口模式）
                try:
                    last_ok = bool(getattr(self.buyer, "_last_avg_ocr_ok", True))
                    if last_ok:
                        self._ocr_miss_streak = 0
                        self._last_avg_ok_ts = time.time()
                    else:
                        buyer_streak = int(getattr(self.buyer, "_avg_ocr_streak", 0))
                        self._ocr_miss_streak = int(self._ocr_miss_streak) + 1
                        if (
                            int(self._ocr_miss_streak) >= max(1, int(self._ocr_miss_threshold))
                            and buyer_streak >= 1
                            and (time.time() - float(getattr(self, "_last_avg_ok_ts", 0.0))) >= float(max(2.0, self._penalty_confirm_delay_sec))
                        ):
                            self._check_and_handle_penalty()
                except Exception:
                    pass
                # 窗口循环的微等待：20ms，减少 UI 抖动影响
                _sleep(0.02)

            self._relay_log(
                f"【{_now_label()}】【{t.get('item_name', '')}】【{purchased}/{target}】：退出时间窗口"
            )

    def _validate_tasks(self, tasks: List[Dict[str, Any]]) -> None:
        """标记任务有效性并记录问题。

        合法任务需满足：
        - 具有 item_id 且可在 goods.json 中找到对应条目；
        - 对应 goods.search_name 非空；
        - goods.image_path 对应文件存在（用于模板匹配）。
        """
        for t in tasks:
            ok = True
            gid = str(t.get("item_id", "")).strip()
            if not gid:
                ok = False
                self._relay_log(
                    f"【{_now_label()}】【{t.get('item_name', '')}】【-】：缺少 item_id，任务无效"
                )
            g = self.goods_map.get(gid) if gid else None
            if not g:
                ok = False
                self._relay_log(
                    f"【{_now_label()}】【{t.get('item_name', '')}】【-】：goods.json 未找到该 item_id"
                )
            else:
                if not (g.search_name or "").strip():
                    ok = False
                    self._relay_log(
                        f"【{_now_label()}】【{t.get('item_name', '')}】【-】：goods.search_name 为空，任务无效"
                    )
                if not (g.image_path and os.path.exists(g.image_path)):
                    ok = False
                    self._relay_log(
                        f"【{_now_label()}】【{t.get('item_name', '')}】【-】：goods.image_path 不存在，任务无效"
                    )
            t["_valid"] = bool(ok)

    # ------------------------------ 处罚检测与处理 ------------------------------
    def _check_and_handle_penalty(self) -> None:
        """当 OCR 连续未识别次数达到阈值后，检测并处理处罚提示。

        - 检测 `penalty_warning` 模板是否存在；不存在则忽略。
        - 若存在，等待 `self._penalty_confirm_delay_sec` 秒后点击 `btn_penalty_confirm`。
        - 点击后等待 `self._penalty_wait_after_confirm_sec` 秒，再清零计数。
        """
        try:
            self._relay_log(
                f"【{_now_label()}】【全局】【-】：OCR 连续未识别 {int(self._ocr_miss_streak)} 次，检查处罚提示…"
            )
        except Exception:
            pass
        warn_box = self.screen.locate("penalty_warning", timeout=0.6)
        if warn_box is None:
            try:
                self._relay_log(f"【{_now_label()}】【全局】【-】：未发现处罚提示模板，稍后继续重试…")
            except Exception:
                pass
            # 未命中处罚提示：清零相关计数，避免持续重复触发
            try:
                self._ocr_miss_streak = 0
            except Exception:
                pass
            try:
                # 同步清理 Buyer 内部的本地计数，防止误触发
                if getattr(self, "buyer", None) is not None:
                    setattr(self.buyer, "_avg_ocr_streak", 0)
                    setattr(self.buyer, "_last_avg_ocr_ok", True)
            except Exception:
                pass
            return
        # 命中处罚提示时：可视化叠加提示区域
        try:
            if warn_box is not None:
                self._debug_show_overlay(
                    overlays=[{"rect": warn_box, "label": "处罚提示", "fill": (255, 193, 7, 80), "outline": (255, 193, 7)}],
                    stage="检测到处罚提示",
                    template_path=None,
                    save_name="overlay_penalty_warning.png",
                )
        except Exception:
            pass
        # 延迟后点击确认
        _sleep(max(0.0, float(getattr(self, "_penalty_confirm_delay_sec", 5.0))))
        btn_box = None
        end = time.time() + 2.0
        while time.time() < end and btn_box is None:
            btn_box = self.screen.locate("btn_penalty_confirm", timeout=0.2)
        if btn_box is not None:
            try:
                self._debug_show_overlay(
                    overlays=[{"rect": btn_box, "label": "处罚确认", "fill": (76, 175, 80, 80), "outline": (76, 175, 80)}],
                    stage="点击处罚确认",
                    template_path=None,
                    save_name="overlay_penalty_confirm.png",
                )
            except Exception:
                pass
            self.screen.click_center(btn_box)
            try:
                self._relay_log(
                    f"【{_now_label()}】【全局】【-】：已点击处罚确认，等待 {int(getattr(self, '_penalty_wait_after_confirm_sec', 180.0))} 秒后继续…"
                )
            except Exception:
                pass
            _sleep(max(0.0, float(getattr(self, "_penalty_wait_after_confirm_sec", 180.0))))
            self._ocr_miss_streak = 0
        else:
            try:
                self._relay_log(f"【{_now_label()}】【全局】【-】：未定位到处罚确认按钮，跳过点击。")
            except Exception:
                pass
__all__ = [
    "TaskRunner",
    "run_launch_flow",
]