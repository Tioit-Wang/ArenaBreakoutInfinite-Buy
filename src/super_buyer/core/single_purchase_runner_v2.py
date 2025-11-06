"""单商品购买模式 v2（按《单商品购买流程设计指导方案》重构版）。

模块目标：
- 将单商品购买流程按“步骤 1–8”进行明确的模块化拆分；
- 统一强制等待与轮询步进的时序参数；
- 不对进入详情叠加固定等待，OCR 与结果识别均采用“识别轮”推进（窗口+步进）；
- 非新界面小操作（如 Max/数量）优先采用“快速点击并复位”，无需固定等待，由识别轮自然收敛；
- 收敛日志输出：info 记录关键状态迁移与核心数据，debug 仅输出排障关键点（阶段开始/结束、匹配来源、ROI 尺寸、OCR/匹配耗时等）。

说明：
- 保持与现有配置/服务的兼容：ScreenOps/launcher/ocr/history 等；
- 不替换旧版 `task_runner.py`，仅新增 v2 版本以便按需切换。
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from super_buyer.config.loader import load_config
from super_buyer.core.common import now_label, safe_sleep
from super_buyer.core.exceptions import FatalOcrError
from super_buyer.core.launcher import run_launch_flow
from super_buyer.core.logging import (
    LOG_LEVELS,
    ensure_level_tag,
    extract_level_from_msg,
    level_name,
)
from super_buyer.core.models import Goods
from super_buyer.services.history import (
    HistoryPaths,
    append_price,
    append_purchase,
    resolve_paths as _resolve_history_paths,
)
from super_buyer.services.ocr import recognize_numbers, recognize_text
from super_buyer.services.screen_ops import ScreenOps


# ------------------------------ 时序/策略 ------------------------------


@dataclass
class Timings:
    """流程关键时序参数（单位：秒）。

    - post_close_detail: 关闭详情后强制等待（规范：100ms）
    - post_success_click: 关闭购买成功遮罩后强制等待（规范：≥300ms）
    - post_nav: 导航（首页/市场）点击后的强制等待（规范：100ms）
    - buy_result_timeout: 购买结果识别窗口（规范：0.8s，可配）
    - buy_result_poll_step: 购买结果识别轮询步进（规范：20ms，可配）
    - poll_step: 通用轮询步进（规范：20ms），若未专门配置则可共用
    - ocr_min_wait: 非新界面小操作后的最小等待（保留兼容，v2 主流程不依赖固定等待）
    - step_delay: 微步进（来自 ScreenOps，默认 15ms）
    - ocr_round_window: OCR 识别轮窗口（规范：0.5s，可配）
    - ocr_round_step: OCR 识别轮步进（规范：20ms，可配）
    - ocr_round_fail_limit: OCR 识别轮连续失败上限（规范：10 次，可配）
    """

    post_close_detail: float = 0.1
    post_success_click: float = 0.3
    post_nav: float = 0.1
    buy_result_timeout: float = 0.8
    buy_result_poll_step: float = 0.02
    poll_step: float = 0.02
    ocr_min_wait: float = 0.05
    step_delay: float = 0.015
    ocr_round_window: float = 0.5
    ocr_round_step: float = 0.02
    ocr_round_fail_limit: int = 10


class StageTimer:
    """阶段计时器（用于 debug 输出流程耗时）。"""

    def __init__(self, emit: Callable[[str, str], None], stage: str) -> None:
        self._emit = emit
        self._stage = stage
        self._t0 = 0.0

    def __enter__(self):
        self._t0 = time.perf_counter()
        self._emit("debug", f"阶段开始: {self._stage}")
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed_ms = int((time.perf_counter() - self._t0) * 1000.0)
        self._emit("debug", f"阶段结束: {self._stage} | 耗时={elapsed_ms}ms")


# ------------------------------ 单商品 Buyer v2 ------------------------------


class SinglePurchaseBuyerV2:
    """单商品购买流程（v2）。

    职责划分：
    - 步骤 3：障碍清理（关闭遗留详情/成功遮罩）
    - 步骤 4：建立搜索上下文（首页/市场 → 搜索 → 模板匹配并缓存）
    - 步骤 5：进入详情并缓存按钮（btn_buy/btn_close/btn_max）
    - 步骤 6：读取均价（以 btn_buy 为锚点的 ROI + OCR）
    - 步骤 7：执行购买（普通/补货，含快速点击与结果识别）
    """

    def __init__(
        self,
        cfg: Dict[str, Any],
        screen: ScreenOps,
        on_log: Callable[[str], None],
        *,
        history_paths: HistoryPaths,
        timings: Timings,
    ) -> None:
        self.cfg = cfg
        self.screen = screen
        self.on_log = on_log
        self.history_paths = history_paths
        self.timings = timings

        # 临时/跨会话缓存
        self._pos_cache: Dict[str, Tuple[int, int, int, int]] = {}  # 商品卡片矩形
        self._first_detail_cached: Dict[str, bool] = {}
        self._first_detail_buttons: Dict[str, Dict[str, Tuple[int, int, int, int]]] = {}
        self._detail_ui_cache: Dict[str, Tuple[int, int, int, int]] = {}

        # OCR 连败标记（供外层统计参考）
        self._last_avg_ocr_ok: bool = True
        self._avg_ocr_streak: int = 0
        # 最近一次 OCR 使用的 ROI 与二值图（用于最终失败时落盘）
        self._last_roi_debug: Dict[str, Any] = {}

    # -------------------- 基础：日志/工具 --------------------
    def _emit(self, level: str, msg: str) -> None:
        try:
            # 统一前缀在上层 Runner 处理，这里只透传文本
            self.on_log(f"[{level.upper()}] {msg}")
        except Exception:
            pass

    def _log_info(self, item: str, purchased: str, msg: str) -> None:
        self.on_log(f"【{now_label()}】【{item}】【{purchased}】：{msg}")

    def _log_debug(self, item: str, purchased: str, msg: str) -> None:
        self.on_log(f"【DEBUG】【{item}】【{purchased}】：{msg}")

    def _safe_name(self, s: str) -> str:
        try:
            name = str(s)
        except Exception:
            name = "obj"
        keep = []
        for ch in name:
            if ch.isalnum() or ch in ("_", "-", "×", "·", " "):
                keep.append(ch)
        return ("".join(keep) or "obj").strip().replace(" ", "_")[:60]

    def _stash_roi_debug(self, *,
                         item_disp: str,
                         roi: Tuple[int, int, int, int],
                         img, img_top, img_bot,
                         bin_top, bin_bot) -> None:
        """缓存最近一次 ROI/二值图，供最终失败时保存。"""
        try:
            self._last_roi_debug = {
                "item": item_disp,
                "roi": tuple(int(v) for v in roi),
                "img": img,
                "img_top": img_top,
                "img_bot": img_bot,
                "bin_top": bin_top,
                "bin_bot": bin_bot,
                "ts": time.time(),
            }
        except Exception:
            self._last_roi_debug = {}

    def _dump_last_roi_debug(self, item_disp: str, purchased_str: str) -> None:
        """若开启了 debug.save_roi_on_fail，则将最近一次 ROI/二值图落盘。"""
        try:
            dbg = (self.cfg.get("debug", {}) or {})
            if not bool(dbg.get("save_roi_on_fail", False)):
                return
        except Exception:
            return
        data = self._last_roi_debug or {}
        if not data:
            return
        try:
            out_root = str(((self.cfg.get("paths", {}) or {}).get("output_dir", "output")) or "output")
        except Exception:
            out_root = "output"
        out_dir = os.path.join(out_root, "roi_debug")
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception:
            return
        try:
            ts = time.strftime("%Y%m%d-%H%M%S", time.localtime(float(data.get("ts", time.time()))))
        except Exception:
            ts = time.strftime("%Y%m%d-%H%M%S")
        base = f"{ts}_{self._safe_name(item_disp)}"
        # 保存文件
        saved = []
        for key in ("img", "img_top", "img_bot", "bin_top", "bin_bot"):
            im = data.get(key)
            if im is None:
                continue
            fn = os.path.join(out_dir, f"{base}_{key}.png")
            try:
                im.save(fn)
                saved.append(fn)
            except Exception:
                pass
        if saved:
            self._log_debug(item_disp, purchased_str, f"已保存 ROI 调试图：{len(saved)} 张 -> {out_dir}")

    @property
    def _pg(self):  # type: ignore
        import pyautogui  # type: ignore

        return pyautogui

    def _center_of(self, box: Tuple[int, int, int, int]) -> Tuple[int, int]:
        x, y, w, h = box
        return int(x + w / 2), int(y + h / 2)

    def _fast_click_and_restore(self, box: Tuple[int, int, int, int]) -> None:
        """快速点击并复位：不涉及新界面场景（如 Max/数量输入）优先使用。

        - 记录当前位置 → 移动至目标中心 → 点击 → 立即移回原位置；
        - 不做固定等待，若后续需要触发 OCR，应由“识别轮”推进收敛。
        """

        try:
            pg = self._pg
            cur_x, cur_y = pg.position()
            tx, ty = self._center_of(box)
            pg.moveTo(tx, ty)
            pg.click(tx, ty)
            pg.moveTo(cur_x, cur_y)
        except Exception:
            # 回退：用 ScreenOps 普通点击（不复位）
            try:
                self.screen.click_center(box)
            except Exception:
                pass

    # -------------------- 公共：遮罩/关闭/导航 --------------------
    def _dismiss_success_overlay_with_wait(self, item: str, purchased: str, *, goods: Optional[Goods]) -> None:
        """关闭购买成功遮罩。

        - 若已缓存 buy 坐标：当前位置快速单击；否则保守“右上→中间→右上”；
        - 关闭后强制等待 post_success_click（≥300ms）。
        """

        # 快速单击当前位置（若已缓存按钮视为坐标可见）
        try:
            has_cache = False
            if goods is not None:
                has_cache = (self._first_detail_buttons.get(goods.id, {}) or {}).get("btn_buy") is not None
            if has_cache:
                try:
                    self.screen._pg.click()  # type: ignore[attr-defined]
                except Exception:
                    # 回退：中部点击一次
                    try:
                        sw, sh = self.screen._pg.size()  # type: ignore[attr-defined]
                        self.screen._pg.click(int(sw // 2), int(sh // 2))  # type: ignore[attr-defined]
                    except Exception:
                        pass
                safe_sleep(self.timings.post_success_click)
                return
        except Exception:
            pass

        # 兜底：安全移动
        try:
            pg = self.screen._pg  # type: ignore[attr-defined]
            sw, sh = pg.size()
            pg.moveTo(max(0, int(sw) - 5), max(0, 5))
            pg.click(int(sw // 2), int(sh // 2))
            pg.moveTo(max(0, int(sw) - 5), max(0, 5))
        except Exception:
            try:
                sw, sh = self.screen._pg.size()  # type: ignore[attr-defined]
                self.screen._pg.click(int(sw // 2), int(sh // 2))  # type: ignore[attr-defined]
            except Exception:
                pass
        safe_sleep(self.timings.post_success_click)

    def _get_btn_box(self, goods: Goods, key: str, timeout: float = 0.35) -> Optional[Tuple[int, int, int, int]]:
        # 1) 首次缓存
        try:
            box = (self._first_detail_buttons.get(goods.id, {}) or {}).get(key)
            if box is not None:
                return box
        except Exception:
            pass
        # 2) 会话缓存
        try:
            box = self._detail_ui_cache.get(key)
            if box is not None:
                return box
        except Exception:
            pass
        # 3) 模板匹配
        return self.screen.locate(key, timeout=timeout)

    def _close_detail_with_wait(self, goods: Goods) -> bool:
        c = self._get_btn_box(goods, "btn_close", timeout=0.35)
        if c is not None:
            self.screen.click_center(c)
            safe_sleep(self.timings.post_close_detail)
            return True
        return False

    def _navigate_and_wait(self, key: str) -> bool:
        box = self.screen.locate(key, timeout=2.0)
        if box is None:
            return False
        self.screen.click_center(box)
        safe_sleep(self.timings.post_nav)
        return True

    # -------------------- 步骤 3：障碍清理 --------------------
    def step3_clear_obstacles(self) -> None:
        # 同时命中 购买/关闭 → 关闭详情
        b = self.screen.locate("btn_buy", timeout=0.1)
        c = self.screen.locate("btn_close", timeout=0.1)
        if (b is not None) and (c is not None):
            self.screen.click_center(c)
            safe_sleep(self.timings.post_close_detail)
            return
        # 命中购买成功遮罩 → 关闭遮罩 → 再尝试关闭详情
        ok = self.screen.locate("buy_ok", timeout=0.1)
        if ok is not None:
            self._dismiss_success_overlay_with_wait("全局", "-", goods=None)
            c2 = self.screen.locate("btn_close", timeout=0.5)
            if c2 is not None:
                self.screen.click_center(c2)
                safe_sleep(self.timings.post_close_detail)

    # -------------------- 步骤 4：搜索与列表定位 --------------------
    def _type_and_search(self, query: str) -> bool:
        sbox = self.screen.locate("input_search", timeout=2.0)
        if sbox is None:
            return False
        self.screen.click_center(sbox)
        safe_sleep(0.03)
        self.screen.type_text(query or "", clear_first=True)
        safe_sleep(0.03)
        btn = self.screen.locate("btn_search", timeout=1.0)
        if btn is None:
            return False
        self.screen.click_center(btn)
        safe_sleep(0.02)
        return True

    def _pg_locate_image(self, path: str, confidence: float, timeout: float = 2.5) -> Optional[Tuple[int, int, int, int]]:
        end = time.time() + max(0.0, timeout)
        while time.time() < end:
            try:
                box = self._pg.locateOnScreen(path, confidence=float(confidence))
                if box is not None:
                    return (int(box.left), int(box.top), int(box.width), int(box.height))
            except Exception:
                pass
            safe_sleep(self.timings.step_delay)
        return None

    def _match_and_cache_goods(self, goods: Goods) -> bool:
        if goods.image_path and os.path.exists(goods.image_path):
            box = self._pg_locate_image(goods.image_path, confidence=0.80, timeout=2.5)
            if box is not None:
                self._pos_cache[goods.id] = box
                return True
        return False

    def step4_build_search_context(self, goods: Goods, *, item_disp: str, purchased_str: str) -> bool:
        self.step3_clear_obstacles()
        in_home = self.screen.locate("home_indicator", timeout=0.4) is not None
        in_market = self.screen.locate("market_indicator", timeout=0.4) is not None
        query = (goods.search_name or "").strip()
        if not query:
            self._log_info(item_disp, purchased_str, "缺少检索词，无法建立搜索上下文")
            return False
        if in_home:
            if not self._navigate_and_wait("btn_market"):
                self._log_info(item_disp, purchased_str, "未找到市场按钮")
                return False
            if not self._type_and_search(query):
                self._log_info(item_disp, purchased_str, "未能输入并点击搜索")
                return False
            if not self._match_and_cache_goods(goods):
                self._log_info(item_disp, purchased_str, "未匹配到商品模板，无法缓存坐标")
                return False
            self._log_debug(item_disp, purchased_str, "已建立搜索上下文（首页分支）")
            return True
        if in_market:
            if not self._navigate_and_wait("btn_home"):
                self._log_info(item_disp, purchased_str, "未找到首页按钮用于重置")
                return False
            if not self._navigate_and_wait("btn_market"):
                self._log_info(item_disp, purchased_str, "未找到市场按钮（重置阶段）")
                return False
            if not self._type_and_search(query):
                self._log_info(item_disp, purchased_str, "未能输入并点击搜索（重置阶段）")
                return False
            if not self._match_and_cache_goods(goods):
                self._log_info(item_disp, purchased_str, "未匹配到商品模板（重置阶段）")
                return False
            self._log_debug(item_disp, purchased_str, "已建立搜索上下文（市场重置分支）")
            return True
        self._log_info(item_disp, purchased_str, "缺少首页/市场标识，无法判定页面")
        return False

    # -------------------- 步骤 5：进入详情与按钮缓存 --------------------
    def _open_detail_from_cache_or_match(self, goods: Goods) -> bool:
        """点击商品卡片进入详情并校验关键按钮存在。

        - 优先使用列表阶段缓存的卡片矩形；失败再回退模板匹配；
        - 点击后不叠加固定等待，直接以模板验证 `btn_buy` 与 `btn_close`；
        - 成功后返回 True；失败清除位置缓存并返回 False。
        """
        # 1) 缓存坐标
        if goods.id in self._pos_cache:
            self.screen.click_center(self._pos_cache[goods.id])
            b = self.screen.locate("btn_buy", timeout=0.35)
            c = self.screen.locate("btn_close", timeout=0.35)
            if (b is not None) and (c is not None):
                return True
            self._pos_cache.pop(goods.id, None)
        # 2) 模板匹配
        if goods.image_path and os.path.exists(goods.image_path):
            box = self._pg_locate_image(goods.image_path, confidence=0.80, timeout=2.5)
            if box is not None:
                self._pos_cache[goods.id] = box
                self.screen.click_center(box)
                b = self.screen.locate("btn_buy", timeout=0.35)
                c = self.screen.locate("btn_close", timeout=0.35)
                if (b is not None) and (c is not None):
                    return True
        return False

    def _read_avg_price_with_rounds(
        self,
        goods: Goods,
        item_disp: str,
        purchased_str: str,
        *,
        expected_floor: Optional[int],
        allow_bottom_fallback: bool,
    ) -> Optional[int]:
        """基于“识别轮”的均价读取（仅平均价，上半 ROI）。

        - 窗口：`timings.ocr_round_window`（默认 0.5s）；步进：`timings.ocr_round_step`（默认 20ms）；
        - 每轮内循环尝试 `_read_avg_unit_price(..., fast_anchor_only=True)`；
        - 成功条件：读到任意正整数即返回；超时记一次失败；
        - 失败达到 `timings.ocr_round_fail_limit` 由调用方触发障碍清理并退出。
        """

        fails = 0
        step = max(0.0, float(getattr(self.timings, "ocr_round_step", 0.02)))
        ocr_window = max(0.0, float(getattr(self.timings, "ocr_round_window", 0.5)))
        fail_limit = int(getattr(self.timings, "ocr_round_fail_limit", 10))
        # 步骤级可观测性
        self._log_debug(item_disp, purchased_str, "正在执行【步骤6-均价读取】")
        self._log_debug(
            item_disp,
            purchased_str,
            f"【步骤6-均价读取】- 参数 窗口={int(ocr_window * 1000)}ms 步进={int(step * 1000)}ms 失败上限={fail_limit}",
        )
        while fails < fail_limit:
            self._log_debug(item_disp, purchased_str, f"【步骤6-均价读取】- 识别轮#{fails + 1} 开始")
            t_end = time.time() + ocr_window
            unit_price: Optional[int] = None
            iter_idx = 0
            while time.time() < t_end:
                up = self._read_avg_unit_price(
                    goods,
                    item_disp,
                    purchased_str,
                    expected_floor=expected_floor,
                    allow_bottom_fallback=allow_bottom_fallback,
                    fast_anchor_only=True,
                )
                if isinstance(up, int) and up > 0:
                    unit_price = int(up)
                    self._log_debug(
                        item_disp,
                        purchased_str,
                        f"OCR识别轮#{fails + 1} 成功 | 步进={int(step * 1000)}ms",
                    )
                    self._log_debug(
                        item_disp,
                        purchased_str,
                        f"【步骤6-均价读取】- 本轮迭代={iter_idx} 值={unit_price}",
                    )
                    return unit_price
                iter_idx += 1
                safe_sleep(step)
            fails += 1
            self._log_debug(
                item_disp,
                purchased_str,
                f"OCR识别轮#{fails} 超时 | 窗口={int(ocr_window * 1000)}ms 步进={int(step * 1000)}ms",
            )
        # 最终失败：按需落盘 ROI 调试图
        self._dump_last_roi_debug(item_disp, purchased_str)
        return None

    def _ensure_first_detail_buttons(self, goods: Goods) -> None:
        """缓存详情页关键按钮坐标（修复首次缓存作用域问题）。"""
        if self._first_detail_cached.get(goods.id):
            # 预热到会话缓存
            self._detail_ui_cache.update(self._first_detail_buttons.get(goods.id) or {})
            return
        cache: Dict[str, Tuple[int, int, int, int]] = {}
        b = self.screen.locate("btn_buy", timeout=0.4)
        c = self.screen.locate("btn_close", timeout=0.4)
        if (b is not None) and (c is not None):
            cache = {"btn_buy": b, "btn_close": c}
            if (goods.big_category or "").strip() == "弹药":
                m = self.screen.locate("btn_max", timeout=0.35)
                if m is not None:
                    cache["btn_max"] = m
            self._first_detail_buttons[goods.id] = cache
            self._first_detail_cached[goods.id] = True
        if cache:
            self._detail_ui_cache.update(cache)

    # -------------------- 步骤 6：价格读取（ROI + OCR） --------------------
    def _wait_buy_result_window(self, item_disp: str, purchased_str: str) -> str:
        """购买结果识别轮：在窗口内轮询模板，返回 ok/fail/unknown。

        - 窗口：timings.buy_result_timeout
        - 步进：timings.buy_result_poll_step（若无则回退为 timings.poll_step）
        - 日志：仅输出“识别结果=成功/失败/未知”汇总，不打印迭代级耗时
        """

        t_end = time.time() + float(self.timings.buy_result_timeout)
        got_ok = False
        found_fail = False
        step = float(getattr(self.timings, "buy_result_poll_step", self.timings.poll_step))
        while time.time() < t_end:
            ok_hit = self.screen.locate("buy_ok", timeout=0.0) is not None
            if ok_hit:
                got_ok = True
                break
            fail_hit = self.screen.locate("buy_fail", timeout=0.0) is not None
            if fail_hit:
                found_fail = True
            safe_sleep(step)
        if got_ok:
            self._log_debug(item_disp, purchased_str, "识别结果=成功")
            return "ok"
        if found_fail:
            self._log_debug(item_disp, purchased_str, "识别结果=失败")
            return "fail"
        self._log_debug(item_disp, purchased_str, "识别结果=未知")
        return "unknown"

    def precache_detail_once(self, goods: Goods, item_disp: str, purchased_str: str) -> bool:
        """预热：独立执行一次“进入详情→缓存关键信息→轻量OCR→退出”。

        约定：
        - 每个关键步骤后固定等待 2s（只限预热流程）；
        - 关键信息：btn_buy/btn_close/(btn_max)、数量输入锚点（qty_minus/qty_plus 中点）；
        - 轻量 OCR：使用识别轮（fast_anchor_only），成功条件=读到任意正整数；
        - 失败：触发障碍清理并返回 False。
        """

        # 1) 进入详情
        if not self._open_detail_from_cache_or_match(goods):
            self._log_info(item_disp, purchased_str, "预缓存：打开详情失败")
            self.step3_clear_obstacles()
            return False
        safe_sleep(2.0)

        # 2) 缓存关键按钮
        self._ensure_first_detail_buttons(goods)
        safe_sleep(2.0)
        b = self._get_btn_box(goods, "btn_buy", timeout=0.5)
        c = self._get_btn_box(goods, "btn_close", timeout=0.5)
        if (b is None) or (c is None):
            self._log_info(item_disp, purchased_str, "预缓存：关键按钮未命中")
            self.step3_clear_obstacles()
            return False

        # 3) 缓存数量输入锚点（可选）
        mid = self._find_qty_midpoint()
        if isinstance(mid, tuple):
            try:
                self._detail_ui_cache["qty_mid"] = (int(mid[0]) - 2, int(mid[1]) - 2, 4, 4)
            except Exception:
                pass
        safe_sleep(2.0)

        # 4) 轻量 OCR 验证（不写历史，仅验证链路）
        _ = self._read_avg_price_with_rounds(
            goods,
            item_disp,
            purchased_str,
            expected_floor=None,
            allow_bottom_fallback=True,
        )
        # 识别轮内部会更新 buyer 的 OCR 连败统计标记；不强制依赖结果成功
        safe_sleep(2.0)

        # 5) 关闭详情
        _ = self._close_detail_with_wait(goods)
        safe_sleep(2.0)
        return True

    def _read_avg_unit_price(
        self,
        goods: Goods,
        item_disp: str,
        purchased_str: str,
        *,
        expected_floor: Optional[int] = None,
        allow_bottom_fallback: bool = True,
        fast_anchor_only: bool = False,
    ) -> Optional[int]:
        """以 btn_buy 为锚点计算 ROI，并识别“平均单价”。

        算法要点：
        - 锚点来源优先级：会话缓存 → 缓存附近小区域快速重定位 → 全局匹配；
        - ROI 公式：distance_from_buy_top=5（兑换+30）、height=45、scale∈[0.6,2.5]；
        - 分割与二值化：上下半分割；优先 Otsu，回退灰度固定阈值；
        - 识别策略：仅使用上半（平均单价）→ 数字识别优先，失败再文本解析；不使用下半兜底；
        - 不在此阶段做阈值过滤：任何正整数即视为识别成功，是否合格交由外层决策；
        - 产物：成功写入价格历史，可选保存 ROI。
        """

        # 购买按钮锚点（带邻域重定位）
        self._log_debug(item_disp, purchased_str, "正在执行【步骤6-均价读取-ROI裁剪与OCR】")
        t_btn = time.perf_counter()
        prev = self._detail_ui_cache.get("btn_buy")
        buy_box = None
        btn_source = "cache"
        try:
            if prev is not None:
                px, py, pw, ph = prev
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
                cand = self.screen.locate("btn_buy", region=region, timeout=max(0.0, self.timings.step_delay))
                if cand is not None:
                    buy_box = cand
                    self._detail_ui_cache["btn_buy"] = cand
                    btn_source = "region"
        except Exception:
            pass
        if buy_box is None and prev is not None:
            buy_box = prev
        if buy_box is None and not bool(fast_anchor_only):
            cand_global = self.screen.locate("btn_buy", timeout=0.35)
            if cand_global is not None:
                buy_box = cand_global
                self._detail_ui_cache["btn_buy"] = cand_global
                btn_source = "global"
        btn_ms = int((time.perf_counter() - t_btn) * 1000.0)
        if buy_box is None:
            self._log_debug(item_disp, purchased_str, f"未找到购买按钮，无法计算 ROI | 匹配耗时={btn_ms}ms")
            self._last_avg_ocr_ok = False
            self._avg_ocr_streak = self._avg_ocr_streak + 1
            return None
        self._log_debug(item_disp, purchased_str, f"按钮来源={btn_source} | 坐标={buy_box} | 匹配耗时={btn_ms}ms")

        b_left, b_top, b_w, b_h = buy_box
        avg_cfg = self.cfg.get("avg_price_area") or {}
        try:
            dist = int(avg_cfg.get("distance_from_buy_top", 5) or 5)
            hei = int(avg_cfg.get("height", 45) or 45)
        except Exception:
            dist, hei = 5, 45
        try:
            if bool(getattr(goods, "exchangeable", False)):
                dist += 30
        except Exception:
            pass
        y_bottom = int(b_top - dist)
        y_top = int(y_bottom - hei)
        x_left = int(b_left)
        width = int(max(1, b_w))
        try:
            sw, sh = self._pg.size()  # type: ignore[attr-defined]
        except Exception:
            sw, sh = 1920, 1080
        y_top = max(0, min(sh - 2, y_top))
        y_bottom = max(y_top + 1, min(sh - 1, y_bottom))
        x_left = max(0, min(sw - 2, x_left))
        width = max(1, min(width, sw - x_left))
        height = max(1, y_bottom - y_top)
        if height <= 0 or width <= 0:
            self._last_avg_ocr_ok = False
            self._avg_ocr_streak += 1
            self._log_debug(item_disp, purchased_str, "ROI 尺寸无效")
            return None
        roi = (x_left, y_top, width, height)
        img = self.screen.screenshot_region(roi)
        if img is None:
            self._last_avg_ocr_ok = False
            self._avg_ocr_streak += 1
            self._log_debug(item_disp, purchased_str, "ROI 截屏失败")
            return None
        # 分割与缩放
        try:
            w0, h0 = img.size
        except Exception:
            self._last_avg_ocr_ok = False
            self._avg_ocr_streak += 1
            self._log_debug(item_disp, purchased_str, "ROI 尺寸无法获取")
            return None
        if h0 < 2:
            self._last_avg_ocr_ok = False
            self._avg_ocr_streak += 1
            self._log_debug(item_disp, purchased_str, "ROI 高度过小")
            return None
        mid_h = h0 // 2
        img_top = img.crop((0, 0, w0, mid_h))
        img_bot = img.crop((0, mid_h, w0, h0))
        self._log_debug(item_disp, purchased_str, f"ROI=({x_left},{y_top},{width},{height}) | 上下=({img_top.width}x{img_top.height})/({img_bot.width}x{img_bot.height})")
        try:
            sc = float((avg_cfg.get("scale", 1.0) or 1.0))
        except Exception:
            sc = 1.0
        if sc < 0.6:
            sc = 0.6
        if sc > 2.5:
            sc = 2.5
        if abs(sc - 1.0) > 1e-3:
            try:
                img_top = img_top.resize((max(1, int(img_top.width * sc)), max(1, int(img_top.height * sc))))
                img_bot = img_bot.resize((max(1, int(img_bot.width * sc)), max(1, int(img_bot.height * sc))))
            except Exception:
                pass
        # 输出 ROI 参数
        self._log_debug(item_disp, purchased_str, f"【步骤6-均价读取-ROI参数】- dist={dist} height={hei} scale={sc}")

        # 二值化：优先 Otsu
        bin_top = None
        bin_bot = None
        try:
            import numpy as _np  # type: ignore
            import cv2 as _cv2  # type: ignore
            from PIL import Image as _PIL  # type: ignore
            for src, name in ((img_top, "top"), (img_bot, "bot")):
                arr = _np.array(src)
                bgr = _cv2.cvtColor(arr, _cv2.COLOR_RGB2BGR)
                gray = _cv2.cvtColor(bgr, _cv2.COLOR_BGR2GRAY)
                _thr, th = _cv2.threshold(gray, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU)
                if name == "top":
                    bin_top = _PIL.fromarray(th)
                else:
                    bin_bot = _PIL.fromarray(th)
        except Exception:
            try:
                bin_top = img_top.convert("L").point(lambda p: 255 if p > 128 else 0)
            except Exception:
                bin_top = img_top
            try:
                bin_bot = img_bot.convert("L").point(lambda p: 255 if p > 128 else 0)
            except Exception:
                bin_bot = img_bot

        # 识别：仅上半（平均单价）数字 → 文本解析；不使用下半兜底
        # 先缓存 ROI/二值图用于可能的最终失败落盘
        try:
            self._stash_roi_debug(
                item_disp=item_disp,
                roi=(x_left, y_top, width, height),
                img=img,
                img_top=img_top,
                img_bot=img_bot,
                bin_top=bin_top,
                bin_bot=bin_bot,
            )
        except Exception:
            pass
        try:
            ocfg = self.cfg.get("umi_ocr") or {}
            t_ocr = time.perf_counter()
            cands = recognize_numbers(
                bin_top,
                base_url=str(ocfg.get("base_url", "http://127.0.0.1:1224")),
                timeout=float(ocfg.get("timeout_sec", 2.5) or 2.5),
                options=dict(ocfg.get("options", {}) or {}),
                offset=(x_left, y_top),
            ) if bin_top is not None else []
            cand = max([c for c in cands if getattr(c, "value", None) is not None], key=lambda c: int(c.value)) if cands else None  # type: ignore[arg-type]
            val = int(getattr(cand, "value", 0)) if cand is not None and getattr(cand, "value", None) is not None else None
            ocr_ms = int((time.perf_counter() - t_ocr) * 1000.0)
            try:
                cand_vals = [int(getattr(c, "value", 0)) for c in (cands or []) if getattr(c, "value", None) is not None]
            except Exception:
                cand_vals = []
            self._log_debug(item_disp, purchased_str, f"OCR(数字) 候选={cand_vals} 选={getattr(cand, 'value', None)} 耗时={ocr_ms}ms")
        except Exception:
            val = None
            ocr_ms = -1

        def _accept_and_record(v: int) -> Optional[int]:
            """任何正整数即视为识别成功（阈值合格性在外层判断）。"""
            if not isinstance(v, int) or v <= 0:
                self._last_avg_ocr_ok = False
                self._avg_ocr_streak += 1
                return None
            try:
                append_price(
                    item_id=goods.id,
                    item_name=goods.name or goods.search_name or item_disp,
                    price=int(v),
                    category=(goods.big_category or "") or None,
                    paths=self.history_paths,
                )
            except Exception:
                pass
            self._last_avg_ocr_ok = True
            self._avg_ocr_streak = 0
            self._log_info(item_disp, purchased_str, f"平均价 OCR 成功 值={v} 阈下限={expected_floor or 0}")
            return int(v)

        if isinstance(val, int) and val > 0:
            r = _accept_and_record(int(val))
            if r is not None:
                return r
        # 上半文本解析
        txt = ""
        try:
            ocfg = self.cfg.get("umi_ocr") or {}
            t_ocr = time.perf_counter()
            if bin_top is not None:
                boxes = recognize_text(
                    bin_top,
                    base_url=str(ocfg.get("base_url", "http://127.0.0.1:1224")),
                    timeout=float(ocfg.get("timeout_sec", 2.5) or 2.5),
                    options=dict(ocfg.get("options", {}) or {}),
                )
                txt = " ".join((b.text or "").strip() for b in boxes if (b.text or "").strip())
            ocr_ms = int((time.perf_counter() - t_ocr) * 1000.0)
        except Exception as e:
            self._log_info(item_disp, purchased_str, f"Umi OCR 失败：{e}")
            raise FatalOcrError(str(e))
        # 简单解析数字
        import re as _re

        def _parse_num(s: str) -> Optional[int]:
            m = _re.search(r"(\d{1,9})", s.replace(",", " ").replace(".", " "))
            if not m:
                return None
            try:
                return int(m.group(1))
            except Exception:
                return None

        val2 = _parse_num(txt or "")
        self._log_debug(item_disp, purchased_str, f"OCR(文本) 原文='{txt}' 解析={val2} 耗时={ocr_ms if 'ocr_ms' in locals() else -1}ms")
        if isinstance(val2, int) and val2 > 0:
            r2 = _accept_and_record(int(val2))
            if r2 is not None:
                return r2

        # 拒绝下半兜底：未识别平均单价则记为失败，由识别轮继续

        self._last_avg_ocr_ok = False
        self._avg_ocr_streak += 1
        return None

    # -------------------- 步骤 7：执行购买（普通/补货） --------------------
    def _find_qty_midpoint(self) -> Optional[Tuple[int, int]]:
        m = self.screen.locate("qty_minus", timeout=0.2)
        p = self.screen.locate("qty_plus", timeout=0.2)
        if m is None or p is None:
            return None
        mx, my = int(m[0] + m[2] / 2), int(m[1] + m[3] / 2)
        px, py = int(p[0] + p[2] / 2), int(p[1] + p[3] / 2)
        return int((mx + px) / 2), int((my + py) / 2)

    def _focus_and_type_quantity_fast(self, qty: int) -> bool:
        """优先使用预缓存的数量输入锚点进行聚焦与输入。

        策略：
        - 若 `_detail_ui_cache['qty_mid']` 存在，直接使用该矩形进行快速点击并复位；
        - 否则回退到模板匹配 `qty_minus/qty_plus` 计算中点，并将矩形写入 `_detail_ui_cache['qty_mid']`；
        - 成功后输入指定数量（清空后输入）。
        """
        # 1) 使用会话缓存的数量输入中点
        cached = self._detail_ui_cache.get("qty_mid")
        box = None
        if isinstance(cached, tuple) and len(cached) == 4:
            box = tuple(int(v) for v in cached)  # type: ignore[assignment]
        else:
            # 2) 回退：计算数量输入中点，并写入会话缓存
            mid = self._find_qty_midpoint()
            if mid is None:
                return False
            box = (int(mid[0]) - 2, int(mid[1]) - 2, 4, 4)
            try:
                self._detail_ui_cache["qty_mid"] = box
            except Exception:
                pass
        try:
            # 快速点击并复位到输入框中心
            self._fast_click_and_restore(box)  # type: ignore[arg-type]
            # 输入数量
            self.screen.type_text(str(int(qty)), clear_first=True)
            return True
        except Exception:
            return False

    def _restock_fast_loop(
        self,
        goods: Goods,
        task: Dict[str, Any],
        purchased_so_far: int,
    ) -> Tuple[int, bool]:
        item_disp = goods.name or goods.search_name or str(task.get("item_name", ""))
        target_total = int(task.get("target_total", 0) or 0)
        bought = 0
        # 上限计算
        try:
            restock = int(task.get("restock_price", 0) or 0)
        except Exception:
            restock = 0
        try:
            r_prem = float(task.get("restock_premium_pct", 0.0) or 0.0)
        except Exception:
            r_prem = 0.0
        restock_limit = restock + int(round(restock * max(0.0, r_prem) / 100.0)) if restock > 0 else 0
        # 会话准备：弹药 Max / 非弹药数量
        is_ammo = (goods.big_category or "").strip() == "弹药"
        used_max = False
        typed_qty = 0
        if is_ammo:
            mx = (self._first_detail_buttons.get(goods.id, {}) or {}).get("btn_max") or self.screen.locate("btn_max", timeout=0.35)
            if mx is not None:
                self._fast_click_and_restore(mx)
                used_max = True
        else:
            if self._focus_and_type_quantity_fast(5):
                typed_qty = 5
            else:
                typed_qty = 1

        # 循环直到退出
        while True:
            purchased_str = f"{purchased_so_far + bought}/{target_total}"
            try:
                thr_base = int(task.get("price_threshold", 0) or 0)
            except Exception:
                thr_base = 0
            base = thr_base if thr_base > 0 else restock
            unit_price = self._read_avg_price_with_rounds(
                goods,
                item_disp,
                purchased_str,
                expected_floor=base if base > 0 else None,
                allow_bottom_fallback=False,
            )
            if unit_price is None or unit_price <= 0:
                # 识别轮连续失败（达到阈值），执行障碍清理逻辑并退出本次详情
                self.step3_clear_obstacles()
                self._log_info(item_disp, purchased_str, "平均价识别失败（补货，识别轮超时），已执行障碍清理")
                return bought, True
            ok_restock = (restock > 0) and (unit_price <= restock_limit)
            if not ok_restock:
                _ = self._close_detail_with_wait(goods)
                return bought, True
            b = (self._first_detail_buttons.get(goods.id, {}) or {}).get("btn_buy") or self.screen.locate("btn_buy", timeout=0.4)
            if b is None:
                _ = self._close_detail_with_wait(goods)
                self._log_info(item_disp, purchased_str, "未找到购买按钮（补货），已关闭详情")
                return bought, True
            # 点击购买（调试记录）
            self._log_debug(item_disp, purchased_str, f"已点击购买(补货) 按钮框={b}")
            self.screen.click_center(b)
            # 结果识别轮（汇总级日志）
            _res = self._wait_buy_result_window(item_disp, purchased_str)
            if _res == "ok":
                inc = (120 if used_max else 10) if is_ammo else max(1, int(typed_qty or 5))
                bought += int(inc)
                try:
                    append_purchase(
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
                self._dismiss_success_overlay_with_wait(item_disp, purchased_str, goods=goods)
                if target_total > 0 and (purchased_so_far + bought) >= target_total:
                    _ = self._close_detail_with_wait(goods)
                    h = self.screen.locate("btn_home", timeout=2.0)
                    if h is not None:
                        self.screen.click_center(h)
                        safe_sleep(self.timings.post_nav)
                    return bought, False
                continue
            if _res == "fail":
                _ = self._close_detail_with_wait(goods)
                self._log_info(item_disp, purchased_str, "购买失败（补货），已关闭详情")
                return bought, True
            # 未知
            _ = self._close_detail_with_wait(goods)
            self._log_info(item_disp, purchased_str, "结果未知（补货），已关闭详情")
            return bought, True

    def purchase_cycle(
        self,
        goods: Goods,
        task: Dict[str, Any],
        purchased_so_far: int,
    ) -> Tuple[int, bool]:
        """一次完整的购买循环：
        - 进入详情并缓存按钮
        - 读均价 → 阈值/补货判定 → 购买 → 结果识别
        返回 (新增购买量, 是否继续外层循环)。
        """

        item_disp = goods.name or goods.search_name or str(task.get("item_name", ""))
        target_total = int(task.get("target_total", 0) or 0)
        purchased_str = f"{purchased_so_far}/{target_total}"
        with StageTimer(lambda lv, m: self._log_debug(item_disp, purchased_str, m), "进入详情与购买循环"):
            used_cache = goods.id in self._pos_cache
            if not self._open_detail_from_cache_or_match(goods):
                if not used_cache:
                    ok_ctx = self.step4_build_search_context(goods, item_disp=item_disp, purchased_str=purchased_str)
                    if ok_ctx and self._open_detail_from_cache_or_match(goods):
                        pass
                    else:
                        if used_cache:
                            self._log_info(item_disp, purchased_str, "缓存坐标无效，打开详情失败")
                        else:
                            self._log_info(item_disp, purchased_str, "未匹配到商品模板，打开详情失败")
                        return 0, True
            # 首次缓存按钮
            self._ensure_first_detail_buttons(goods)

        bought = 0
        while True:
            try:
                thr_base = int(task.get("price_threshold", 0) or 0)
            except Exception:
                thr_base = 0
            try:
                rest_base = int(task.get("restock_price", 0) or 0)
            except Exception:
                rest_base = 0
            base = thr_base if thr_base > 0 else rest_base
            unit_price = self._read_avg_price_with_rounds(
                goods,
                item_disp,
                purchased_str,
                expected_floor=base if base > 0 else None,
                allow_bottom_fallback=False,
            )
            if unit_price is None or unit_price <= 0:
                # 识别轮连续失败（达到阈值），执行障碍清理逻辑并退出本次详情
                self.step3_clear_obstacles()
                self._log_info(item_disp, purchased_str, "平均单价识别失败（识别轮超时），已执行障碍清理")
                return bought, True

            # 价格阈值/补货上限解析与判定
            # 约定：任何阈值为 0 均表示“不购买/禁用”。当同时配置补货与普通阈值时，优先补货；
            # 若补货不满足，再回退普通阈值判定。
            thr = int(task.get("price_threshold", 0) or 0)
            prem = float(task.get("price_premium_pct", 0.0) or 0.0)
            limit = thr + int(round(thr * max(0.0, prem) / 100.0)) if thr > 0 else 0
            restock = int(task.get("restock_price", 0) or 0)
            r_prem = float(task.get("restock_premium_pct", 0.0) or 0.0)
            rest_limit = restock + int(round(restock * max(0.0, r_prem) / 100.0)) if restock > 0 else 0
            ok_restock = (restock > 0) and (unit_price <= rest_limit)
            # 修正：阈值为 0 表示禁用，因此普通判定仅在 limit>0 时生效
            ok_normal = (limit > 0) and (unit_price <= limit)

            # 信息日志：输出两条路径的阈值线，便于人工核对
            if restock > 0:
                self._log_info(item_disp, purchased_str, f"均价={unit_price} 阈≤{limit}(+{int(prem)}%) 补≤{rest_limit}(+{int(r_prem)}%)")
            else:
                self._log_info(item_disp, purchased_str, f"均价={unit_price} 阈≤{limit}(+{int(prem)}%)")

            # 决策点（仅一条 debug，收敛但足够排障）
            if ok_restock:
                self._log_debug(
                    item_disp,
                    purchased_str,
                    f"决策=补货 | unit={unit_price} limit={limit} rest_limit={rest_limit} thr={thr} prem={int(prem)}% restock={restock} r_prem={int(r_prem)}%",
                )
                got_more, cont = self._restock_fast_loop(goods, task, purchased_so_far + bought)
                bought += int(got_more)
                return bought, cont
            if ok_normal:
                self._log_debug(
                    item_disp,
                    purchased_str,
                    f"决策=普通 | unit={unit_price} limit={limit} rest_limit={rest_limit} thr={thr} prem={int(prem)}% restock={restock} r_prem={int(r_prem)}%",
                )
            else:
                self._log_debug(
                    item_disp,
                    purchased_str,
                    f"决策=放弃 | unit={unit_price} limit={limit} rest_limit={rest_limit} thr={thr} prem={int(prem)}% restock={restock} r_prem={int(r_prem)}%",
                )
                _ = self._close_detail_with_wait(goods)
                return bought, True

            b = self._first_detail_buttons.get(goods.id, {}).get("btn_buy") or self.screen.locate("btn_buy", timeout=0.4)
            if b is None:
                _ = self._close_detail_with_wait(goods)
                self._log_info(item_disp, purchased_str, "未找到购买按钮，已关闭详情")
                return bought, True
            # 点击购买（调试记录）
            self._log_debug(item_disp, purchased_str, f"已点击购买 按钮框={b}")
            self.screen.click_center(b)
            # 结果识别轮（汇总级日志）
            _res = self._wait_buy_result_window(item_disp, purchased_str)
            if _res == "ok":
                is_ammo = (goods.big_category or "").strip() == "弹药"
                inc = 10 if is_ammo else 1
                bought += int(inc)
                try:
                    append_purchase(
                        item_id=goods.id,
                        item_name=goods.name or goods.search_name or str(task.get("item_name", "")),
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
                self._dismiss_success_overlay_with_wait(item_disp, purchased_str, goods=goods)
                continue
            if _res == "fail":
                _ = self._close_detail_with_wait(goods)
                self._log_info(item_disp, purchased_str, "购买失败，已关闭详情")
                return bought, True
            # 未知
            _ = self._close_detail_with_wait(goods)
            self._log_info(item_disp, purchased_str, "结果未知，已关闭详情")
            return bought, True

    # 工具：清理临时卡片坐标缓存
    def clear_pos(self, goods_id: Optional[str] = None) -> None:
        if goods_id is None:
            self._pos_cache.clear()
        else:
            self._pos_cache.pop(goods_id, None)

    def clear_all_caches(self, goods_id: Optional[str] = None) -> None:
        """清理与缓存相关的所有数据结构。

        - 若提供 goods_id：仅清理该商品的卡片坐标与首次按钮缓存；
        - 始终清空会话级 `_detail_ui_cache`，确保下次进入详情重新识别。
        """
        try:
            if goods_id is None:
                self._pos_cache.clear()
                self._first_detail_cached.clear()
                self._first_detail_buttons.clear()
            else:
                self._pos_cache.pop(goods_id, None)
                self._first_detail_cached.pop(goods_id, None)
                self._first_detail_buttons.pop(goods_id, None)
        except Exception:
            pass
        try:
            self._detail_ui_cache.clear()
        except Exception:
            pass


# ------------------------------ 单商品 Runner v2 ------------------------------


class SinglePurchaseTaskRunnerV2:
    """单商品购买调度器（v2）。

    - 支持 round/ time 两种模式；
    - v2 采用模块化步骤与统一时序策略；
    - 处罚与软重启符合文档描述；
    - 对外仅暴露“启动(start) / 停止(stop)”控制。
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
    ) -> None:
        self.on_log = on_log or (lambda s: None)
        self.on_task_update = on_task_update or (lambda i, it: None)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # 配置
        self.cfg = load_config(cfg_path)
        self.tasks_data = json.loads(json.dumps(tasks_data or {"tasks": []}))
        self.goods_map: Dict[str, Goods] = self._load_goods(goods_path)
        paths_cfg = self.cfg.get("paths", {}) or {}
        if not isinstance(paths_cfg, dict):
            paths_cfg = {}
        out_dir = Path(output_dir) if output_dir is not None else Path(paths_cfg.get("output_dir", "output"))
        self.history_paths = _resolve_history_paths(out_dir)

        # 步进延时（不再读取/应用 cfg.debug.*）
        adv = self.tasks_data.get("advanced") if isinstance(self.tasks_data.get("advanced"), dict) else {}
        try:
            delay_ms = float((adv or {}).get("delay_ms", 15))
            step_delay = max(0.0, delay_ms / 1000.0)
        except Exception:
            step_delay = 0.015

        # 统一时序
        try:
            tuning = (self.cfg.get("multi_snipe_tuning", {}) or {})
        except Exception:
            tuning = {}
        timings = Timings(
            step_delay=step_delay,
            buy_result_timeout=float(tuning.get("buy_result_timeout_sec", 0.8) or 0.8),
            buy_result_poll_step=float(tuning.get("buy_result_poll_step_sec", 0.02) or 0.02),
            poll_step=float(tuning.get("poll_step_sec", 0.02) or 0.02),
            ocr_round_window=float(tuning.get("ocr_round_window_sec", 0.5) or 0.5),
            ocr_round_step=float(tuning.get("ocr_round_step_sec", 0.02) or 0.02),
            ocr_round_fail_limit=int(tuning.get("ocr_round_fail_limit", 10) or 10),
        )

        # 服务对象
        self.screen = ScreenOps(self.cfg, step_delay=step_delay)
        self.buyer = SinglePurchaseBuyerV2(
            self.cfg,
            self.screen,
            self._relay_log,
            history_paths=self.history_paths,
            timings=timings,
        )

        # 处罚链路参数
        self._ocr_miss_streak: int = 0
        self._ocr_miss_threshold = int(tuning.get("ocr_miss_penalty_threshold", 10) or 10)
        self._penalty_confirm_delay_sec = float(tuning.get("penalty_confirm_delay_sec", 5.0) or 5.0)
        self._penalty_wait_after_confirm_sec = float(tuning.get("penalty_wait_sec", 180.0) or 180.0)
        self._last_avg_ok_ts: float = 0.0

        # 模式/日志/重启
        self.mode = str(self.tasks_data.get("task_mode", "time"))
        self.log_level: str = level_name(str(self.tasks_data.get("log_level", "info")))
        try:
            self.restart_every_min = int(self.tasks_data.get("restart_every_min", 0) or 0)
        except Exception:
            self.restart_every_min = 0
        self._next_restart_ts: Optional[float] = None
        self._last_idle_log_ts: float = 0.0

    # -------------------- 对外 API --------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._relay_log(f"【{now_label()}】【全局】【-】：终止信号已发送")

    def set_log_level(self, level: str) -> None:
        self.log_level = level_name(level)

    # -------------------- 内部：日志/商品/启动/重启 --------------------
    def _relay_log(self, s: str) -> None:
        try:
            lv = extract_level_from_msg(s)
            msg = ensure_level_tag(s, lv if lv else "info")
            if LOG_LEVELS.get(lv, 20) < LOG_LEVELS.get(self.log_level, 20):
                return
            self.on_log(msg)
        except Exception:
            pass

    def _load_goods(self, path: str) -> Dict[str, Goods]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                arr = json.load(f)
        except Exception:
            arr = []
        goods_map: Dict[str, Goods] = {}
        base_dir = Path(path).resolve().parent
        if isinstance(arr, list):
            for e in arr:
                if not isinstance(e, dict):
                    continue
                gid = str(e.get("id", ""))
                if not gid:
                    continue
                raw_img = str(e.get("image_path", ""))
                try:
                    pp = Path(raw_img)
                    img_abs = str(pp) if pp.is_absolute() else str((base_dir / raw_img.replace("\\", "/")).resolve())
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
        def _on(s: str) -> None:
            self._relay_log(f"【{now_label()}】【全局】【-】：{s}")

        res = run_launch_flow(self.cfg, on_log=_on)
        if not res.ok:
            self._relay_log(f"【{now_label()}】【全局】【-】：启动失败：{res.error or res.code}")
        return bool(res.ok)

    def _should_restart_now(self) -> bool:
        if self.restart_every_min <= 0:
            return False
        if self._next_restart_ts is None:
            self._next_restart_ts = time.time() + self.restart_every_min * 60
            return False
        return time.time() >= self._next_restart_ts

    def _do_soft_restart(self, goods: Optional[Goods] = None) -> float:
        t0 = time.time()
        self._relay_log(f"【{now_label()}】【全局】【-】：到达重启周期，尝试重启…")
        # 1) 首页 → 等 5s
        h = self.screen.locate("btn_home", timeout=1.0)
        if h is not None:
            self.screen.click_center(h)
        safe_sleep(5.0)
        # 2) 设置 → 等 5s
        s = self.screen.locate("btn_settings", timeout=1.0)
        if s is not None:
            self.screen.click_center(s)
        safe_sleep(5.0)
        # 3) 退出 → 等 5s
        e = self.screen.locate("btn_exit", timeout=1.0)
        if e is not None:
            self.screen.click_center(e)
        safe_sleep(5.0)
        # 4) 确认 → 等 30s
        ec = self.screen.locate("btn_exit_confirm", timeout=1.0)
        if ec is not None:
            self.screen.click_center(ec)
        safe_sleep(30.0)
        # 5) 启动流程
        def _on(s: str) -> None:
            self._relay_log(f"【{now_label()}】【全局】【-】：{s}")

        res = run_launch_flow(self.cfg, on_log=_on)
        if not res.ok:
            self._relay_log(f"【{now_label()}】【全局】【-】：重启失败：{res.error or res.code}")
            self._stop.set()
        # 6) 重建上下文
        if goods is not None:
            try:
                self.buyer.clear_all_caches(goods.id)
                item_disp = goods.name or goods.search_name or "-"
                self.buyer.step4_build_search_context(goods, item_disp=item_disp, purchased_str="-")
            except Exception:
                pass
        # 7) 重置周期
        self._next_restart_ts = time.time() + max(1, self.restart_every_min) * 60
        return time.time() - t0

    # -------------------- 主循环 --------------------
    def _validate_tasks(self, tasks: List[Dict[str, Any]]) -> None:
        for t in tasks:
            ok = True
            gid = str(t.get("item_id", "")).strip()
            if not gid:
                ok = False
                self._relay_log(f"【{now_label()}】【{t.get('item_name', '')}】【-】：缺少 item_id，任务无效")
            g = self.goods_map.get(gid) if gid else None
            if not g:
                ok = False
                self._relay_log(f"【{now_label()}】【{t.get('item_name', '')}】【-】：goods.json 未找到该 item_id")
            else:
                if not (g.search_name or "").strip():
                    ok = False
                    self._relay_log(f"【{now_label()}】【{t.get('item_name', '')}】【-】：goods.search_name 为空，任务无效")
                if not (g.image_path and os.path.exists(g.image_path)):
                    ok = False
                    self._relay_log(f"【{now_label()}】【{t.get('item_name', '')}】【-】：goods.image_path 不存在，任务无效")
            t["_valid"] = bool(ok)

    def _run(self) -> None:
        try:
            if not self._ensure_ready():
                return
            tasks: List[Dict[str, Any]] = list(self.tasks_data.get("tasks", []) or [])
            try:
                tasks.sort(key=lambda d: int(d.get("order", 0)))
            except Exception:
                pass
            self._validate_tasks(tasks)
            for t in tasks:
                t.setdefault("purchased", 0)
                t.setdefault("executed_ms", 0)
                t.setdefault("status", "idle")

            self._next_restart_ts = None
            if str(self.mode or "time") == "round":
                self._run_round_robin(tasks)
            else:
                self._run_time_window(tasks)
        except Exception as e:
            self._relay_log(f"【{now_label()}】【全局】【-】：运行异常：{e}")

    def _precache_with_retries(self, goods: Goods, item_disp: str, purchased_str: str) -> bool:
        """预缓存重试：最多 3 次，指数退避（1s→2s→4s），失败触发清理并可触发处罚逻辑。

        返回 True 表示预缓存成功；False 表示失败（调用处应终止本次任务/片段）。
        """
        backoff = 1.0
        for attempt in range(1, 4):
            self._relay_log(f"【{now_label()}】【{item_disp}】【{purchased_str}】：预缓存尝试#{attempt}")
            ok = False
            try:
                ok = bool(self.buyer.precache_detail_once(goods, item_disp, purchased_str))
            except FatalOcrError as e:
                self._relay_log(f"【{now_label()}】【全局】【-】：Umi OCR 失败（预缓存）：{e}")
                ok = False
            if ok:
                # 重置处罚统计
                self._ocr_miss_streak = 0
                self._last_avg_ok_ts = time.time()
                return True
            # 失败：清理 + 处罚判定 + 退避
            try:
                self.buyer.step3_clear_obstacles()
            except Exception:
                pass
            # 处罚统计更新
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
            safe_sleep(max(0.0, float(backoff)))
            backoff *= 2.0
        return False

    def _run_round_robin(self, tasks: List[Dict[str, Any]]) -> None:
        idx = 0
        n = len(tasks)
        while not self._stop.is_set():
            if n == 0:
                now = time.time()
                if now - self._last_idle_log_ts > 5.0:
                    self._relay_log(f"【{now_label()}】【全局】【-】：任务列表为空，等待中…")
                    self._last_idle_log_ts = now
                safe_sleep(0.6)
                continue
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
                now = time.time()
                if now - self._last_idle_log_ts > 5.0:
                    self._relay_log(f"【{now_label()}】【全局】【-】：没有可执行的任务，等待中…")
                    self._last_idle_log_ts = now
                safe_sleep(1.0)
                continue
            t = tasks[idx % n]
            if not bool(t.get("enabled", True)) or not bool(t.get("_valid", True)):
                idx += 1
                continue
            target = int(t.get("target_total", 0) or 0)
            purchased = int(t.get("purchased", 0) or 0)
            if target > 0 and purchased >= target:
                idx += 1
                continue
            duration_min = int(t.get("duration_min", 10) or 10)
            t["status"] = "running"
            seg_start = time.time()
            seg_end = seg_start + duration_min * 60
            gid = str(t.get("item_id", ""))
            goods = self.goods_map.get(gid)
            if not goods:
                idx += 1
                continue
            item_disp = goods.name or goods.search_name or str(t.get("item_name", ""))
            self._relay_log(f"【{now_label()}】【{item_disp}】【{purchased}/{target}】：开始片段 {duration_min}min")
            try:
                self.buyer.clear_all_caches(goods.id)
                if not self.buyer.step4_build_search_context(goods, item_disp=item_disp, purchased_str=f"{purchased}/{target}"):
                    self._relay_log(f"【{now_label()}】【{item_disp}】【{purchased}/{target}】：建立搜索上下文失败，跳过片段")
                    idx += 1
                    continue
                # 预热：进入详情→缓存→轻量OCR→退出（失败清理，最多重试3次，指数退避）
                if not self._precache_with_retries(goods, item_disp, f"{purchased}/{target}"):
                    self._relay_log(f"【{now_label()}】【{item_disp}】【{purchased}/{target}】：预缓存失败，跳过片段")
                    idx += 1
                    continue
            except FatalOcrError as e:
                self._relay_log(f"【{now_label()}】【全局】【-】：Umi OCR 失败（片段初始化）：{e}")
                self._stop.set()
                break
            seg_paused_sec = 0.0
            while not self._stop.is_set() and time.time() < seg_end:
                if self._stop.is_set():
                    break
                if self._should_restart_now():
                    paused = self._do_soft_restart(goods)
                    seg_paused_sec += max(0.0, float(paused))
                    try:
                        self.buyer.clear_all_caches(goods.id)
                        _ = self.buyer.step4_build_search_context(
                            goods,
                            item_disp=item_disp,
                            purchased_str=f"{purchased}/{target}",
                        )
                    except Exception:
                        pass
                try:
                    got, cont = self.buyer.purchase_cycle(goods, t, purchased)
                except FatalOcrError as e:
                    self._relay_log(f"【{now_label()}】【全局】【-】：Umi OCR 失败（循环中）：{e}")
                    self._stop.set()
                    break
                if got > 0:
                    purchased += int(got)
                    t["purchased"] = purchased
                    try:
                        self.on_task_update(tasks.index(t), dict(t))
                    except Exception:
                        pass
                if not cont:
                    break
                # 处罚统计
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
                safe_sleep(0.02)
            elapsed = int((time.time() - seg_start - seg_paused_sec) * 1000)
            t["executed_ms"] = int(t.get("executed_ms", 0) or 0) + max(0, elapsed)
            t["status"] = "idle"
            self._relay_log(f"【{now_label()}】【{item_disp}】【{purchased}/{target}】：片段结束 累计{elapsed}ms")
            idx += 1

    def _run_time_window(self, tasks: List[Dict[str, Any]]) -> None:
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
            if not start and not end:
                return True
            lt = time.localtime(now_ts)
            now_min = lt.tm_hour * 60 + lt.tm_min
            sp = _parse_hhmm(start or "") or (0, 0)
            ep = _parse_hhmm(end or "") or (23, 59)
            smin = sp[0] * 60 + sp[1]
            emin = ep[0] * 60 + ep[1]
            if emin >= smin:
                return smin <= now_min <= emin
            return now_min >= smin or now_min <= emin

        while not self._stop.is_set():
            if self._stop.is_set():
                break
            now = time.time()
            chosen_idx = None
            for i, t in enumerate(tasks):
                if not bool(t.get("enabled", True)):
                    continue
                if not bool(t.get("_valid", True)):
                    continue
                if _time_in_window(now, str(t.get("time_start", "")), str(t.get("time_end", ""))):
                    chosen_idx = i
                    break
            if chosen_idx is None:
                now = time.time()
                if now - self._last_idle_log_ts > 5.0:
                    self._relay_log(f"【{now_label()}】【全局】【-】：无任务命中当前时间窗口，等待中…")
                    self._last_idle_log_ts = now
                safe_sleep(1.2)
                continue
            t = tasks[chosen_idx]
            target = int(t.get("target_total", 0) or 0)
            purchased = int(t.get("purchased", 0) or 0)
            if target > 0 and purchased >= target:
                safe_sleep(0.8)
                continue
            gid = str(t.get("item_id", ""))
            goods = self.goods_map.get(gid)
            if not goods:
                safe_sleep(1.0)
                continue
            item_disp = goods.name or goods.search_name or str(t.get("item_name", ""))
            self._relay_log(f"【{now_label()}】【{item_disp}】【{purchased}/{target}】：进入时间窗口")
            try:
                self.buyer.clear_all_caches(goods.id)
                if not self.buyer.step4_build_search_context(goods, item_disp=item_disp, purchased_str=f"{purchased}/{target}"):
                    safe_sleep(1.0)
                    continue
                # 预热：进入详情→缓存→轻量OCR→退出（失败清理，最多重试3次，指数退避）
                if not self._precache_with_retries(goods, item_disp, f"{purchased}/{target}"):
                    self._relay_log(f"【{now_label()}】【{item_disp}】【{purchased}/{target}】：预缓存失败，终止本次任务（时间窗口）")
                    break
            except FatalOcrError as e:
                self._relay_log(f"【{now_label()}】【全局】【-】：Umi OCR 失败（进入窗口）：{e}")
                self._stop.set()
                break
            while not self._stop.is_set():
                if not _time_in_window(time.time(), str(t.get("time_start", "")), str(t.get("time_end", ""))):
                    break
                if self._stop.is_set():
                    break
                if self._should_restart_now():
                    self._do_soft_restart(goods)
                    try:
                        _ = self.buyer.step4_build_search_context(
                            goods,
                            item_disp=item_disp,
                            purchased_str=f"{purchased}/{target}",
                        )
                    except Exception:
                        pass
                try:
                    got, cont = self.buyer.purchase_cycle(goods, t, purchased)
                except FatalOcrError as e:
                    self._relay_log(f"【{now_label()}】【全局】【-】：Umi OCR 失败（窗口循环）：{e}")
                    self._stop.set()
                    break
                if got > 0:
                    purchased += int(got)
                    t["purchased"] = purchased
                    try:
                        self.on_task_update(tasks.index(t), dict(t))
                    except Exception:
                        pass
                if not cont:
                    break
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
                safe_sleep(0.02)
            self._relay_log(f"【{now_label()}】【{item_disp}】【{purchased}/{target}】：退出时间窗口")

    # -------------------- 处罚处理 --------------------
    def _check_and_handle_penalty(self) -> None:
        self._relay_log(f"【{now_label()}】【全局】【-】：OCR 连续未识别 {int(self._ocr_miss_streak)} 次，检查处罚提示…")
        warn_box = self.screen.locate("penalty_warning", timeout=0.6)
        if warn_box is None:
            self._relay_log(f"【{now_label()}】【全局】【-】：未发现处罚提示模板，稍后继续重试…")
            self._ocr_miss_streak = 0
            try:
                setattr(self.buyer, "_avg_ocr_streak", 0)
                setattr(self.buyer, "_last_avg_ocr_ok", True)
            except Exception:
                pass
            return
        # 延迟后点击确认
        safe_sleep(max(0.0, float(getattr(self, "_penalty_confirm_delay_sec", 5.0))))
        btn_box = None
        end = time.time() + 2.0
        while time.time() < end and btn_box is None:
            btn_box = self.screen.locate("btn_penalty_confirm", timeout=0.2)
        if btn_box is not None:
            self.screen.click_center(btn_box)
            self._relay_log(
                f"【{now_label()}】【全局】【-】：已点击处罚确认，等待 {int(getattr(self, '_penalty_wait_after_confirm_sec', 180.0))} 秒…"
            )
            safe_sleep(max(0.0, float(getattr(self, "_penalty_wait_after_confirm_sec", 180.0))))
            self._ocr_miss_streak = 0
        else:
            self._relay_log(f"【{now_label()}】【全局】【-】：未定位到处罚确认按钮，跳过点击。")


__all__ = [
    "Timings",
    "SinglePurchaseBuyerV2",
    "SinglePurchaseTaskRunnerV2",
]
