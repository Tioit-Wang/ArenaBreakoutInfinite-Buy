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
import random
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
    build_context_message,
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
    detail_open_settle: float = 0.05
    detail_cache_verify_timeout: float = 0.18
    anchor_stabilize: float = 0.05


class StageTimer:
    """阶段计时器（用于 debug 输出流程耗时）。"""

    def __init__(self, emit: Callable[[str, str], None], step_name: str, phase: str) -> None:
        self._emit = emit
        self._step_name = step_name
        self._phase = phase
        self._t0 = 0.0

    def __enter__(self):
        self._t0 = time.perf_counter()
        self._emit(
            "debug",
            build_context_message(self._step_name, phase=self._phase, state="开始"),
        )
        return self

    def __exit__(self, exc_type, exc, tb):
        elapsed_ms = int((time.perf_counter() - self._t0) * 1000.0)
        self._emit(
            "debug",
            build_context_message(
                self._step_name,
                phase=self._phase,
                state="结束",
                message=f"耗时={elapsed_ms}ms",
            ),
        )


STEP_1_NAME = "步骤1-全局启动与准备"
STEP_2_NAME = "步骤2-任务选择与会话进入"
STEP_3_NAME = "步骤3-障碍清理与初始化检查"
STEP_4_NAME = "步骤4-搜索与列表定位"
STEP_5_NAME = "步骤5-预缓存（预热）"
STEP_6_NAME = "步骤6-价格读取与阈值判定"
STEP_7_NAME = "步骤7-执行购买"
STEP_8_NAME = "步骤8-会话内循环与退出条件"


def _format_step_value(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        try:
            if value.is_integer():
                return str(int(value))
        except Exception:
            pass
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, (list, tuple, set)):
        return ",".join(_format_step_value(v) for v in value)
    if isinstance(value, dict):
        return ",".join(f"{k}:{_format_step_value(v)}" for k, v in value.items())
    return str(value)


def _format_step_params(params: Optional[Dict[str, Any]] = None) -> str:
    parts: List[str] = []
    for key, value in (params or {}).items():
        if value is None or value == "":
            continue
        parts.append(f"{key}={_format_step_value(value)}")
    return " ".join(parts)


def _build_step_log(
    item: str,
    purchased: str,
    step_name: str,
    elapsed_ms: int,
    result: str,
    params: Optional[Dict[str, Any]] = None,
) -> str:
    prefix = f"【{now_label()}】【{item}】【{purchased}】【{step_name}】：耗时={max(0, int(elapsed_ms))}ms"
    payload = _format_step_params(params)
    if payload:
        return f"{prefix} | {payload} | 结果={result}"
    return f"{prefix} | 结果={result}"


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
        self._global_ui_cache: Dict[str, Tuple[int, int, int, int]] = {}
        self._goods_list_region_cache: Optional[Tuple[int, int, int, int]] = None

        # OCR 连败标记（供外层统计参考）
        self._last_avg_ocr_ok: bool = True
        self._avg_ocr_streak: int = 0
        # 最近一次 OCR 使用的 ROI 与二值图（用于最终失败时落盘）
        self._last_roi_debug: Dict[str, Any] = {}
        self._last_open_detail_source: str = "-"
        self._last_btn_source: str = "-"
        self._last_btn_match_ms: int = 0
        self._last_avg_read_meta: Dict[str, Any] = {}
        self._last_cycle_meta: Dict[str, Any] = {}
        self._anchor_revalidate_needed: bool = False
        self._pending_anchor_settle_goods_id: Optional[str] = None
        self.should_stop: Callable[[], bool] = lambda: False

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

    def _log_step_info_text(
        self,
        item: str,
        purchased: str,
        step_name: str,
        *,
        phase: str | None = None,
        state: str | None = None,
        message: str | None = None,
    ) -> None:
        self._log_info(
            item,
            purchased,
            build_context_message(step_name, phase=phase, state=state, message=message),
        )

    def _log_step_debug_text(
        self,
        item: str,
        purchased: str,
        step_name: str,
        *,
        phase: str | None = None,
        state: str | None = None,
        message: str | None = None,
    ) -> None:
        self._log_debug(
            item,
            purchased,
            build_context_message(step_name, phase=phase, state=state, message=message),
        )

    def _log_step(
        self,
        item: str,
        purchased: str,
        step_name: str,
        elapsed_ms: int,
        result: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            self.on_log(_build_step_log(item, purchased, step_name, elapsed_ms, result, params))
        except Exception:
            pass

    def _set_cycle_meta(self, **kwargs: Any) -> None:
        try:
            self._last_cycle_meta = dict(kwargs)
        except Exception:
            self._last_cycle_meta = {}

    def get_last_cycle_meta(self) -> Dict[str, Any]:
        try:
            return dict(self._last_cycle_meta or {})
        except Exception:
            return {}

    def _stop_requested(self) -> bool:
        try:
            return bool(self.should_stop())
        except Exception:
            return False

    def _mark_detail_opened(self, goods: Goods) -> None:
        self._anchor_revalidate_needed = False
        self._pending_anchor_settle_goods_id = goods.id

    def _consume_anchor_settle(self, goods: Goods) -> None:
        if self._pending_anchor_settle_goods_id != goods.id:
            return
        self._pending_anchor_settle_goods_id = None
        safe_sleep(max(0.0, float(getattr(self.timings, "anchor_stabilize", 0.05) or 0.05)))

    def _cached_detail_btn_box(
        self,
        goods: Goods,
        key: str,
    ) -> Optional[Tuple[int, int, int, int]]:
        try:
            box = (self._first_detail_buttons.get(goods.id, {}) or {}).get(key)
            if box is not None:
                return tuple(int(v) for v in box)
        except Exception:
            pass
        try:
            box = self._detail_ui_cache.get(key)
            if box is not None:
                return tuple(int(v) for v in box)
        except Exception:
            pass
        return None

    def _has_cached_detail_buttons(self, goods: Goods) -> bool:
        return (
            self._cached_detail_btn_box(goods, "btn_buy") is not None
            and self._cached_detail_btn_box(goods, "btn_close") is not None
        )

    def _detect_scene(self, timeout: float = 0.1) -> str:
        try:
            if self._get_global_ui_box("buy_ok", timeout=max(0.0, float(timeout))) is not None:
                return "success_overlay"
        except Exception:
            pass
        try:
            b = self.screen.locate("btn_buy", timeout=max(0.0, float(timeout)))
            c = self.screen.locate("btn_close", timeout=max(0.0, float(timeout)))
            if (b is not None) and (c is not None):
                return "detail"
        except Exception:
            pass
        try:
            if self._get_global_ui_box("home_indicator", timeout=max(0.0, float(timeout))) is not None:
                return "home"
        except Exception:
            pass
        try:
            if self._get_global_ui_box("market_indicator", timeout=max(0.0, float(timeout))) is not None:
                return "market"
        except Exception:
            pass
        return "unknown"

    def _log_step6(
        self,
        item: str,
        purchased: str,
        elapsed_ms: int,
        result: str,
        *,
        unit_price: Optional[int],
        normal_limit: int,
        restock_limit: int,
        reason: Optional[str] = None,
    ) -> None:
        meta = dict(self._last_avg_read_meta or {})
        params: Dict[str, Any] = {
            "unit_price": unit_price if unit_price is not None else "-",
            "normal_limit": int(normal_limit or 0),
            "restock_limit": int(restock_limit or 0),
            "ocr_round": int(meta.get("rounds", 0) or 0),
            "btn_source": str(meta.get("btn_source", "-") or "-"),
        }
        if reason:
            params["reason"] = reason
        self._log_step(item, purchased, STEP_6_NAME, elapsed_ms, result, params)

    def _log_step7(
        self,
        item: str,
        purchased: str,
        elapsed_ms: int,
        result: str,
        *,
        purchase_mode: str,
        unit_price: Optional[int],
        qty: Optional[int] = None,
        bought: Optional[int] = None,
        chain_count: Optional[int] = None,
        max_chain: Optional[int] = None,
        used_max: Optional[bool] = None,
        reason: Optional[str] = None,
    ) -> None:
        params: Dict[str, Any] = {
            "purchase_mode": purchase_mode,
            "click_target": "btn_buy",
            "result_window_ms": int(float(self.timings.buy_result_timeout) * 1000.0),
        }
        if unit_price is not None:
            params["unit_price"] = int(unit_price)
        if qty is not None:
            params["qty"] = int(qty)
        if bought is not None:
            params["bought"] = int(bought)
        if chain_count is not None:
            params["chain_count"] = int(chain_count)
        if max_chain is not None:
            params["max_chain"] = int(max_chain)
        if used_max is not None:
            params["used_max"] = bool(used_max)
        if reason:
            params["reason"] = reason
        self._log_step(item, purchased, STEP_7_NAME, elapsed_ms, result, params)

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
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_6_NAME,
                phase="调试落盘",
                message=f"已保存 ROI 调试图：{len(saved)} 张 -> {out_dir}",
            )

    # -------------------- 工具：价格合法性校验（半阈下限） --------------------
    def _is_price_legal_for_history(self, unit_price: int, task: Dict[str, Any]) -> Tuple[bool, int, int]:
        """判断 OCR 读数是否“价格合理”，用于决定是否写入价格历史。

        规则：
        - 使用“有效阈值基准”作为 B：若配置了 `restock_price>0` 则优先使用补货上限；否则使用 `price_threshold`。
          说明：补货与普通阈值同时存在时，补货优先，避免高普通阈值误伤补货分支。
        - 合理性条件：`unit_price > B/2`（严格大于二分之一）。

        返回 (是否合理, 基准阈值B, 下限 floor=B//2)。
        """
        try:
            thr = int(task.get("price_threshold", 0) or 0)
        except Exception:
            thr = 0
        try:
            restock = int(task.get("restock_price", 0) or 0)
        except Exception:
            restock = 0
        # 补货优先：当配置补货上限时，用补货阈值作为“异常过滤”的基准。
        # 否则回退到普通阈值。
        base = int(restock) if int(restock) > 0 else int(thr)
        if base <= 0:
            return True, 0, 0
        # 严格大于二分之一：unit_price * 2 > base
        ok = int(unit_price) * 2 > int(base)
        return ok, int(base), int(base // 2)

    def _resolve_price_limits(self, task: Dict[str, Any]) -> Tuple[int, int, float, int, int, float]:
        """解析普通/补货价格与溢价后的有效上限。"""

        try:
            thr = int(task.get("price_threshold", 0) or 0)
        except Exception:
            thr = 0
        try:
            prem = float(task.get("price_premium_pct", 0.0) or 0.0)
        except Exception:
            prem = 0.0
        try:
            restock = int(task.get("restock_price", 0) or 0)
        except Exception:
            restock = 0
        try:
            restock_prem = float(task.get("restock_premium_pct", 0.0) or 0.0)
        except Exception:
            restock_prem = 0.0

        limit = thr + int(round(thr * max(0.0, prem) / 100.0)) if thr > 0 else 0
        restock_limit = (
            restock + int(round(restock * max(0.0, restock_prem) / 100.0))
            if restock > 0
            else 0
        )
        return thr, limit, prem, restock, restock_limit, restock_prem

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

    def _fast_close_success_overlay(
        self,
        btn_box: Tuple[int, int, int, int],
        interval_sec: float,
    ) -> None:
        """快速关闭购买成功遮罩。

        - 假定当前已出现购买成功遮罩，且点击购买按钮位置可以关闭遮罩；
        - 在按钮中心点击一次并等待短暂渲染时间；
        - 若失败则回退到通用遮罩关闭逻辑。
        """

        try:
            cx, cy = self._center_of(btn_box)
            self.screen.click_point(cx, cy, clicks=1)
            safe_sleep(max(interval_sec, 0.03))
        except Exception:
            try:
                self._dismiss_success_overlay_with_wait("全局", "-", goods=None)
            except Exception:
                pass

    def _fast_close_and_rebuy(
        self,
        btn_box: Tuple[int, int, int, int],
        interval_sec: float,
    ) -> None:
        """关闭成功遮罩并在同一位置快速再次点击一次以发起下一次购买。

        - 第一次点击：关闭成功遮罩；
        - 等待 interval_sec（≥30ms），确保界面渲染；
        - 第二次点击：在同一位置再次点击，作为下一次购买操作；
        - 若过程中异常，则回退为“关闭遮罩 + 普通点击购买”。
        """

        try:
            cx, cy = self._center_of(btn_box)
            self.screen.click_point(cx, cy, clicks=1)
            safe_sleep(max(interval_sec, 0.03))
            self.screen.click_point(cx, cy, clicks=1)
        except Exception:
            try:
                self._dismiss_success_overlay_with_wait("全局", "-", goods=None)
            except Exception:
                pass
            try:
                self.screen.click_center(btn_box)
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

    def _remember_detail_btn_box(
        self,
        goods: Goods,
        key: str,
        box: Tuple[int, int, int, int],
    ) -> Tuple[int, int, int, int]:
        norm = tuple(int(v) for v in box)
        self._detail_ui_cache[key] = norm
        try:
            cache = self._first_detail_buttons.get(goods.id)
            if cache is not None:
                cache[key] = norm
        except Exception:
            pass
        return norm

    def _remember_global_ui_box(
        self,
        key: str,
        box: Tuple[int, int, int, int],
    ) -> Tuple[int, int, int, int]:
        norm = tuple(int(v) for v in box)
        self._global_ui_cache[key] = norm
        return norm

    def _expand_region(
        self,
        box: Tuple[int, int, int, int],
        margin: int,
    ) -> Tuple[int, int, int, int]:
        left, top, width, height = [int(v) for v in box]
        try:
            sw, sh = self._pg.size()  # type: ignore[attr-defined]
        except Exception:
            sw, sh = 1920, 1080
        x0 = max(0, left - int(margin))
        y0 = max(0, top - int(margin))
        x1 = min(int(sw), left + width + int(margin))
        y1 = min(int(sh), top + height + int(margin))
        return (int(x0), int(y0), max(1, int(x1 - x0)), max(1, int(y1 - y0)))

    def _merge_regions(
        self,
        region_a: Optional[Tuple[int, int, int, int]],
        region_b: Optional[Tuple[int, int, int, int]],
    ) -> Optional[Tuple[int, int, int, int]]:
        if region_a is None:
            return tuple(int(v) for v in region_b) if region_b is not None else None
        if region_b is None:
            return tuple(int(v) for v in region_a)
        ax, ay, aw, ah = [int(v) for v in region_a]
        bx, by, bw, bh = [int(v) for v in region_b]
        x0 = min(ax, bx)
        y0 = min(ay, by)
        x1 = max(ax + aw, bx + bw)
        y1 = max(ay + ah, by + bh)
        return (x0, y0, max(1, x1 - x0), max(1, y1 - y0))

    def _locate_global_ui_near_cache(
        self,
        key: str,
        *,
        timeout: float = 0.0,
    ) -> Optional[Tuple[int, int, int, int]]:
        box = self._global_ui_cache.get(key)
        if box is None:
            return None
        region = self._expand_region(
            box,
            margin=max(20, min(160, max(int(box[2]), int(box[3]), 40))),
        )
        hit = self.screen.locate(key, region=region, timeout=timeout)
        if hit is not None:
            return self._remember_global_ui_box(key, hit)
        return None

    def _get_global_ui_box(
        self,
        key: str,
        *,
        timeout: float = 0.2,
        allow_global: bool = True,
    ) -> Optional[Tuple[int, int, int, int]]:
        hit = self._locate_global_ui_near_cache(key, timeout=0.0)
        if hit is not None:
            return hit
        if not allow_global:
            return None
        hit = self.screen.locate(key, timeout=timeout)
        if hit is not None:
            return self._remember_global_ui_box(key, hit)
        return None

    def _locate_detail_btn_near_cache(
        self,
        goods: Goods,
        key: str,
        *,
        timeout: float = 0.0,
    ) -> Optional[Tuple[int, int, int, int]]:
        candidates: List[Tuple[int, int, int, int]] = []
        try:
            box = (self._first_detail_buttons.get(goods.id, {}) or {}).get(key)
            if box is not None:
                candidates.append(tuple(int(v) for v in box))
        except Exception:
            pass
        try:
            box = self._detail_ui_cache.get(key)
            if box is not None:
                norm = tuple(int(v) for v in box)
                if norm not in candidates:
                    candidates.append(norm)
        except Exception:
            pass
        for cand in candidates:
            region = self._expand_region(
                cand,
                margin=max(16, min(72, max(int(cand[2]), int(cand[3]), 32))),
            )
            hit = self.screen.locate(key, region=region, timeout=timeout)
            if hit is not None:
                return self._remember_detail_btn_box(goods, key, hit)
        return None

    def _verify_detail_ready(
        self,
        goods: Goods,
        *,
        timeout: float = 0.35,
        allow_global: bool = True,
    ) -> bool:
        """在总窗口内循环验证详情页按钮，避免串行匹配造成“已打开但误判失败”。

        关键点：
        - 优先使用详情按钮缓存的小区域探测；
        - 缺失按钮再做短时全局探测，而不是一次性吃满整个 timeout；
        - 在总窗口内反复补齐 btn_buy / btn_close，任意时刻两者齐全即视为进入详情成功。
        """
        if self._stop_requested():
            return False
        deadline = time.time() + max(0.0, float(timeout or 0.0))
        cached_buy = self._cached_detail_btn_box(goods, "btn_buy")
        cached_close = self._cached_detail_btn_box(goods, "btn_close")
        has_cached = (cached_buy is not None) or (cached_close is not None)
        buy_box = cached_buy or self._locate_detail_btn_near_cache(goods, "btn_buy", timeout=0.0)
        close_box = self._locate_detail_btn_near_cache(goods, "btn_close", timeout=0.0)
        if close_box is not None and cached_buy is not None:
            self._remember_detail_btn_box(goods, "btn_buy", cached_buy)
            return True
        if (buy_box is not None) and (close_box is not None):
            return True

        while time.time() < deadline:
            if self._stop_requested():
                return False
            remaining = max(0.0, deadline - time.time())
            if remaining <= 0:
                break

            if close_box is None:
                close_box = self._locate_detail_btn_near_cache(goods, "btn_close", timeout=0.0)
                if close_box is None and allow_global:
                    probe_timeout = min(0.05 if has_cached else 0.08, remaining)
                    if probe_timeout > 0:
                        hit = self.screen.locate("btn_close", timeout=probe_timeout)
                        if hit is not None:
                            close_box = self._remember_detail_btn_box(goods, "btn_close", hit)
            if close_box is not None and buy_box is not None:
                return True
            if close_box is not None and cached_buy is not None:
                self._remember_detail_btn_box(goods, "btn_buy", cached_buy)
                return True

            remaining = max(0.0, deadline - time.time())
            if remaining <= 0:
                break

            if buy_box is None:
                buy_box = self._locate_detail_btn_near_cache(goods, "btn_buy", timeout=0.0)
                if buy_box is None and allow_global:
                    probe_timeout = min(0.05 if has_cached else 0.08, remaining)
                    if probe_timeout > 0:
                        hit = self.screen.locate("btn_buy", timeout=probe_timeout)
                        if hit is not None:
                            buy_box = self._remember_detail_btn_box(goods, "btn_buy", hit)

            if (buy_box is not None) and (close_box is not None):
                return True

            remaining = max(0.0, deadline - time.time())
            if remaining <= 0:
                break
            safe_sleep(min(max(0.005, self.timings.step_delay), remaining))
        return False

    def _close_detail_with_wait(self, goods: Goods) -> bool:
        if self._stop_requested():
            return False

        def _detail_still_open() -> bool:
            b = self._locate_detail_btn_near_cache(goods, "btn_buy", timeout=0.0)
            c_now = self._locate_detail_btn_near_cache(goods, "btn_close", timeout=0.0)
            if (b is not None) and (c_now is not None):
                return True
            b = self.screen.locate("btn_buy", timeout=0.03)
            c_now = self.screen.locate("btn_close", timeout=0.03)
            return (b is not None) and (c_now is not None)

        c = self._get_btn_box(goods, "btn_close", timeout=0.12)
        if c is not None:
            self.screen.click_center(c)
            self._pending_anchor_settle_goods_id = None
            safe_sleep(self.timings.post_close_detail)
            if not _detail_still_open():
                return True
            # 第一击未生效时，刷新按钮位置后再补点一次，避免停留在详情里误当成下一轮已进入。
            c_retry = self._locate_detail_btn_near_cache(goods, "btn_close", timeout=0.0) or self.screen.locate("btn_close", timeout=0.08)
            if c_retry is not None:
                self.screen.click_center(c_retry)
                safe_sleep(self.timings.post_close_detail)
            return not _detail_still_open()
        return False

    def _navigate_and_wait(self, key: str) -> bool:
        box = self._get_global_ui_box(key, timeout=2.0)
        if box is None:
            return False
        self.screen.click_center(box)
        safe_sleep(self.timings.post_nav)
        return True

    # -------------------- 步骤 3：障碍清理 --------------------
    def step3_clear_obstacles(self, item: str = "全局", purchased: str = "-") -> None:
        t0 = time.perf_counter()
        scene_before = self._detect_scene(timeout=0.03)
        b = None
        c = None
        ok = None
        action = "noop"
        if scene_before == "detail":
            b = self.screen.locate("btn_buy", timeout=0.0)
            c = self.screen.locate("btn_close", timeout=0.0)
        if scene_before == "detail" and (c is not None):
            self.screen.click_center(c)
            safe_sleep(self.timings.post_close_detail)
            action = "close_detail"
        elif scene_before == "success_overlay":
            # 命中购买成功遮罩 → 关闭遮罩 → 再尝试关闭详情
            ok = self._get_global_ui_box("buy_ok", timeout=0.0)
            self._dismiss_success_overlay_with_wait(item, purchased, goods=None)
            action = "dismiss_overlay"
            c2 = self.screen.locate("btn_close", timeout=0.08)
            if c2 is not None:
                self.screen.click_center(c2)
                safe_sleep(self.timings.post_close_detail)
                action = "dismiss_overlay_close_detail"
        scene_after = self._detect_scene(timeout=0.05)
        self._log_step(
            item,
            purchased,
            STEP_3_NAME,
            int((time.perf_counter() - t0) * 1000.0),
            "ready",
            {
                "scene_before": scene_before,
                "scene_after": scene_after,
                "detail_open": scene_before == "detail",
                "buy_ok": ok is not None,
                "action": action,
            },
        )

    # -------------------- 步骤 4：搜索与列表定位 --------------------
    def _type_and_search(self, query: str) -> bool:
        sbox = self._get_global_ui_box("input_search", timeout=2.0)
        if sbox is None:
            return False
        self.screen.click_center(sbox)
        safe_sleep(0.03)
        self.screen.type_text(query or "", clear_first=True)
        safe_sleep(0.03)
        btn = self._get_global_ui_box("btn_search", timeout=1.0)
        if btn is None:
            return False
        self.screen.click_center(btn)
        safe_sleep(0.02)
        return True

    def _pg_locate_image(
        self,
        path: str,
        confidence: float,
        timeout: float = 2.5,
        *,
        region: Optional[Tuple[int, int, int, int]] = None,
    ) -> Optional[Tuple[int, int, int, int]]:
        end = time.time() + max(0.0, timeout)
        while time.time() < end:
            if self._stop_requested():
                return None
            try:
                box = self._pg.locateOnScreen(path, confidence=float(confidence), region=region)
                if box is not None:
                    return (int(box.left), int(box.top), int(box.width), int(box.height))
            except Exception:
                pass
            safe_sleep(self.timings.step_delay)
        return None

    def _remember_goods_hit(
        self,
        goods: Goods,
        box: Tuple[int, int, int, int],
    ) -> None:
        norm = tuple(int(v) for v in box)
        self._pos_cache[goods.id] = norm
        list_region = self._expand_region(norm, margin=420)
        self._goods_list_region_cache = self._merge_regions(self._goods_list_region_cache, list_region)

    def _match_and_cache_goods(self, goods: Goods) -> bool:
        if goods.image_path and os.path.exists(goods.image_path):
            if self._goods_list_region_cache is not None:
                box = self._pg_locate_image(
                    goods.image_path,
                    confidence=0.80,
                    timeout=0.6,
                    region=self._goods_list_region_cache,
                )
                if box is not None:
                    self._remember_goods_hit(goods, box)
                    return True
            box = self._pg_locate_image(goods.image_path, confidence=0.80, timeout=2.5)
            if box is not None:
                self._remember_goods_hit(goods, box)
                return True
        return False

    def step4_build_search_context(self, goods: Goods, *, item_disp: str, purchased_str: str) -> bool:
        self.step3_clear_obstacles(item_disp, purchased_str)
        t0 = time.perf_counter()
        in_home = self._get_global_ui_box("home_indicator", timeout=0.2) is not None
        in_market = self._get_global_ui_box("market_indicator", timeout=0.2) is not None
        scene_before = "home" if in_home else ("market" if in_market else "unknown")
        cache_before = "hit" if goods.id in self._pos_cache else "miss"
        query = (goods.search_name or "").strip()

        def _finish(ok: bool, result: str, **extra: Any) -> bool:
            params: Dict[str, Any] = {
                "scene_before": scene_before,
                "branch": extra.pop("branch", "-"),
                "query": query or "-",
                "cache_before": cache_before,
                "cache_after": ("hit" if goods.id in self._pos_cache else "miss"),
                "match_timeout_ms": 2500,
            }
            params.update(extra)
            self._log_step(
                item_disp,
                purchased_str,
                STEP_4_NAME,
                int((time.perf_counter() - t0) * 1000.0),
                result,
                params,
            )
            return ok

        if not query:
            self._log_step_info_text(
                item_disp,
                purchased_str,
                STEP_4_NAME,
                message="缺少检索词，无法建立搜索上下文",
            )
            return _finish(False, "fail", branch="unknown", reason="missing_query")
        if in_home:
            if not self._navigate_and_wait("btn_market"):
                self._log_step_info_text(
                    item_disp,
                    purchased_str,
                    STEP_4_NAME,
                    phase="首页进入市场",
                    message="未找到市场按钮",
                )
                return _finish(False, "fail", branch="home_to_market", reason="missing_btn_market")
            if not self._type_and_search(query):
                self._log_step_info_text(
                    item_disp,
                    purchased_str,
                    STEP_4_NAME,
                    phase="首页进入市场",
                    message="未能输入并点击搜索",
                )
                return _finish(False, "fail", branch="home_to_market", reason="search_submit_failed")
            if not self._match_and_cache_goods(goods):
                self._log_step_info_text(
                    item_disp,
                    purchased_str,
                    STEP_4_NAME,
                    phase="首页进入市场",
                    message="未匹配到商品模板，无法缓存坐标",
                )
                return _finish(False, "fail", branch="home_to_market", reason="goods_template_not_found")
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_4_NAME,
                phase="首页进入市场",
                state="完成",
                message="已建立搜索上下文",
            )
            return _finish(True, "success", branch="home_to_market", scene_after=self._detect_scene(timeout=0.05))
        if in_market:
            if not self._navigate_and_wait("btn_home"):
                self._log_step_info_text(
                    item_disp,
                    purchased_str,
                    STEP_4_NAME,
                    phase="市场重置",
                    message="未找到首页按钮用于重置",
                )
                return _finish(False, "fail", branch="market_reset", reason="missing_btn_home")
            if not self._navigate_and_wait("btn_market"):
                self._log_step_info_text(
                    item_disp,
                    purchased_str,
                    STEP_4_NAME,
                    phase="市场重置",
                    message="未找到市场按钮",
                )
                return _finish(False, "fail", branch="market_reset", reason="missing_btn_market")
            if not self._type_and_search(query):
                self._log_step_info_text(
                    item_disp,
                    purchased_str,
                    STEP_4_NAME,
                    phase="市场重置",
                    message="未能输入并点击搜索",
                )
                return _finish(False, "fail", branch="market_reset", reason="search_submit_failed")
            if not self._match_and_cache_goods(goods):
                self._log_step_info_text(
                    item_disp,
                    purchased_str,
                    STEP_4_NAME,
                    phase="市场重置",
                    message="未匹配到商品模板，无法缓存坐标",
                )
                return _finish(False, "fail", branch="market_reset", reason="goods_template_not_found")
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_4_NAME,
                phase="市场重置",
                state="完成",
                message="已建立搜索上下文",
            )
            return _finish(True, "success", branch="market_reset", scene_after=self._detect_scene(timeout=0.05))
        self._log_step_info_text(
            item_disp,
            purchased_str,
            STEP_4_NAME,
            message="缺少首页/市场标识，无法判定页面",
        )
        return _finish(False, "fail", branch="unknown", reason="scene_unknown")

    # -------------------- 步骤 5：进入详情与按钮缓存 --------------------
    def _open_detail_from_cache_or_match(self, goods: Goods, *, verify_timeout: float = 0.35) -> bool:
        """点击商品卡片进入详情并校验关键按钮存在。

        - 优先使用列表阶段缓存的卡片矩形；失败再回退模板匹配；
        - 点击后不叠加固定等待，优先在缓存按钮邻域验证，再回退模板匹配；
        - 成功后返回 True；失败清除位置缓存并返回 False。
        """
        self._last_open_detail_source = "none"
        # 1) 缓存坐标
        if goods.id in self._pos_cache:
            self.screen.click_center(self._pos_cache[goods.id])
            safe_sleep(max(0.0, float(getattr(self.timings, "detail_open_settle", 0.05) or 0.05)))
            if self._has_cached_detail_buttons(goods):
                quick_timeout = min(
                    max(0.0, float(getattr(self.timings, "detail_cache_verify_timeout", 0.18) or 0.18)),
                    max(0.0, float(verify_timeout)),
                )
                if quick_timeout > 0 and self._verify_detail_ready(goods, timeout=quick_timeout, allow_global=False):
                    self._mark_detail_opened(goods)
                    self._last_open_detail_source = "cache_fast"
                    return True
            if self._verify_detail_ready(goods, timeout=verify_timeout):
                self._mark_detail_opened(goods)
                self._last_open_detail_source = "cache"
                return True
            if self._detect_scene(timeout=min(0.15, max(0.05, float(verify_timeout)))) == "detail":
                self._mark_detail_opened(goods)
                self._last_open_detail_source = "cache_late"
                return True
            self._pos_cache.pop(goods.id, None)
        # 2) 模板匹配
        if goods.image_path and os.path.exists(goods.image_path):
            box = None
            if self._goods_list_region_cache is not None:
                box = self._pg_locate_image(
                    goods.image_path,
                    confidence=0.80,
                    timeout=0.6,
                    region=self._goods_list_region_cache,
                )
            if box is None:
                box = self._pg_locate_image(goods.image_path, confidence=0.80, timeout=2.5)
            if box is not None:
                self._remember_goods_hit(goods, box)
                self.screen.click_center(box)
                safe_sleep(max(0.0, float(getattr(self.timings, "detail_open_settle", 0.05) or 0.05)))
                if self._verify_detail_ready(goods, timeout=verify_timeout):
                    self._mark_detail_opened(goods)
                    self._last_open_detail_source = "template"
                    return True
                if self._detect_scene(timeout=min(0.15, max(0.05, float(verify_timeout)))) == "detail":
                    self._mark_detail_opened(goods)
                    self._last_open_detail_source = "template_late"
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
        self._last_avg_read_meta = {
            "window_ms": int(ocr_window * 1000.0),
            "step_ms": int(step * 1000.0),
            "fail_limit": int(fail_limit),
            "rounds": 0,
            "btn_source": str(getattr(self, "_last_btn_source", "-") or "-"),
            "unit_price": None,
        }
        self._log_step_debug_text(
            item_disp,
            purchased_str,
            STEP_6_NAME,
            phase="均价读取",
            state="开始",
            message=(
                f"窗口={int(ocr_window * 1000)}ms 步进={int(step * 1000)}ms "
                f"失败上限={fail_limit}"
            ),
        )
        while fails < fail_limit:
            if self._stop_requested():
                self._last_avg_read_meta.update(
                    {
                        "rounds": int(fails),
                        "btn_source": str(getattr(self, "_last_btn_source", "-") or "-"),
                        "unit_price": None,
                        "result": "stopped",
                    }
                )
                return None
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_6_NAME,
                phase="均价读取",
                message=f"识别轮#{fails + 1} 开始",
            )
            t_end = time.time() + ocr_window
            unit_price: Optional[int] = None
            iter_idx = 0
            while time.time() < t_end:
                if self._stop_requested():
                    self._last_avg_read_meta.update(
                        {
                            "rounds": int(fails),
                            "btn_source": str(getattr(self, "_last_btn_source", "-") or "-"),
                            "unit_price": None,
                            "result": "stopped",
                        }
                    )
                    return None
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
                    self._log_step_debug_text(
                        item_disp,
                        purchased_str,
                        STEP_6_NAME,
                        phase="均价读取",
                        message=f"识别轮#{fails + 1} 成功 | 步进={int(step * 1000)}ms",
                    )
                    self._log_step_debug_text(
                        item_disp,
                        purchased_str,
                        STEP_6_NAME,
                        phase="均价读取",
                        message=f"本轮迭代={iter_idx} 值={unit_price}",
                    )
                    self._last_avg_read_meta.update(
                        {
                            "rounds": int(fails + 1),
                            "btn_source": str(getattr(self, "_last_btn_source", "-") or "-"),
                            "unit_price": int(unit_price),
                            "result": "success",
                        }
                    )
                    return unit_price
                iter_idx += 1
                safe_sleep(step)
            fails += 1
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_6_NAME,
                phase="均价读取",
                message=(
                    f"识别轮#{fails} 超时 | 窗口={int(ocr_window * 1000)}ms "
                    f"步进={int(step * 1000)}ms"
                ),
            )
        # 最终失败：按需落盘 ROI 调试图
        self._dump_last_roi_debug(item_disp, purchased_str)
        self._last_avg_read_meta.update(
            {
                "rounds": int(fails),
                "btn_source": str(getattr(self, "_last_btn_source", "-") or "-"),
                "unit_price": None,
                "result": "ocr_failed",
            }
        )
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
            if self._stop_requested():
                break
            ok_hit = self._get_global_ui_box("buy_ok", timeout=0.0) is not None
            if ok_hit:
                got_ok = True
                break
            fail_hit = self._get_global_ui_box("buy_fail", timeout=0.0) is not None
            if fail_hit:
                found_fail = True
            safe_sleep(step)
        if got_ok:
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_7_NAME,
                phase="结果识别",
                message="识别结果=成功",
            )
            return "ok"
        if found_fail:
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_7_NAME,
                phase="结果识别",
                message="识别结果=失败",
            )
            return "fail"
        self._log_step_debug_text(
            item_disp,
            purchased_str,
            STEP_7_NAME,
            phase="结果识别",
            message="识别结果=未知",
        )
        return "unknown"

    def _wait_qty_anchor_ready(self, timeout: float = 0.18) -> Optional[Tuple[int, int]]:
        deadline = time.time() + max(0.0, float(timeout or 0.0))
        mid = self._find_qty_midpoint()
        while mid is None and time.time() < deadline:
            if self._stop_requested():
                return None
            safe_sleep(min(0.02, max(0.005, self.timings.step_delay)))
            mid = self._find_qty_midpoint()
        return mid

    def precache_detail_once(self, goods: Goods, item_disp: str, purchased_str: str) -> bool:
        """预热：独立执行一次“进入详情→缓存关键信息→轻量OCR→退出”。

        约定：
        - 使用事件式推进，不再为每个步骤追加固定 2s 等待；
        - 关键信息：btn_buy/btn_close/(btn_max)、数量输入锚点（qty_minus/qty_plus 中点）；
        - 轻量 OCR：使用识别轮（fast_anchor_only），成功条件=读到任意正整数；
        - 失败：触发障碍清理并返回 False。
        """

        t0 = time.perf_counter()
        open_detail_source = "-"
        btn_buy_state = "miss"
        btn_close_state = "miss"
        btn_max_state = "na"
        qty_anchor = "miss"
        ocr_verify = "not_run"
        close_detail_ok = False

        def _finish(ok: bool, result: str, **extra: Any) -> bool:
            params: Dict[str, Any] = {
                "open_detail": open_detail_source,
                "btn_buy": btn_buy_state,
                "btn_close": btn_close_state,
                "btn_max": btn_max_state,
                "qty_anchor": qty_anchor,
                "ocr_verify": ocr_verify,
                "close_detail": close_detail_ok,
            }
            params.update(extra)
            self._log_step(
                item_disp,
                purchased_str,
                STEP_5_NAME,
                int((time.perf_counter() - t0) * 1000.0),
                result,
                params,
            )
            return ok

        # 1) 进入详情
        if not self._open_detail_from_cache_or_match(goods, verify_timeout=0.75):
            open_detail_source = str(getattr(self, "_last_open_detail_source", "-") or "-")
            self._log_step_info_text(
                item_disp,
                purchased_str,
                STEP_5_NAME,
                phase="进入详情",
                message="打开详情失败",
            )
            self.step3_clear_obstacles(item_disp, purchased_str)
            return _finish(False, "fail", reason="open_detail_failed")
        open_detail_source = str(getattr(self, "_last_open_detail_source", "-") or "-")

        # 2) 缓存关键按钮
        self._ensure_first_detail_buttons(goods)
        b = self._get_btn_box(goods, "btn_buy", timeout=0.12)
        c = self._get_btn_box(goods, "btn_close", timeout=0.12)
        btn_buy_state = "hit" if b is not None else "miss"
        btn_close_state = "hit" if c is not None else "miss"
        if (goods.big_category or "").strip() == "弹药":
            btn_max_state = "hit" if self._get_btn_box(goods, "btn_max", timeout=0.12) is not None else "miss"
        if (b is None) or (c is None):
            self._log_step_info_text(
                item_disp,
                purchased_str,
                STEP_5_NAME,
                phase="按钮缓存",
                message="关键按钮未命中",
            )
            self.step3_clear_obstacles(item_disp, purchased_str)
            return _finish(False, "fail", reason="missing_detail_buttons")

        # 3) 缓存数量输入锚点（可选）
        mid = self._wait_qty_anchor_ready(timeout=0.18)
        if isinstance(mid, tuple):
            qty_anchor = "hit"
            try:
                self._detail_ui_cache["qty_mid"] = (int(mid[0]) - 2, int(mid[1]) - 2, 4, 4)
            except Exception:
                pass

        # 4) 轻量 OCR 验证（不写历史，仅验证链路）
        _ = self._read_avg_price_with_rounds(
            goods,
            item_disp,
            purchased_str,
            expected_floor=None,
            allow_bottom_fallback=True,
        )
        ocr_verify = "success" if bool(getattr(self, "_last_avg_ocr_ok", False)) else "failed"
        # 识别轮内部会更新 buyer 的 OCR 连败统计标记；不强制依赖结果成功

        # 5) 关闭详情
        close_detail_ok = bool(self._close_detail_with_wait(goods))
        return _finish(True, "success")

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
        - 不在此阶段做阈值过滤与历史写入：任何正整数即视为 OCR 成功并返回，历史写入与阈值合法性校验交由调用方处理；
        - 产物：返回识别到的平均单价，可选保存 ROI（调试）。
        """

        # 购买按钮锚点（优先直接使用缓存坐标，失败后再做邻域/全局重定位）
        t_btn = time.perf_counter()
        prev = self._cached_detail_btn_box(goods, "btn_buy")
        buy_box = None
        btn_source = "missing"
        self._last_btn_source = "missing"
        self._last_btn_match_ms = 0
        if prev is not None and not bool(self._anchor_revalidate_needed):
            self._consume_anchor_settle(goods)
            buy_box = tuple(int(v) for v in prev)
            btn_source = "cache"
        elif prev is not None:
            try:
                px, py, pw, ph = prev
                try:
                    sw, sh = self._pg.size()  # type: ignore[attr-defined]
                except Exception:
                    sw, sh = 1920, 1080
                margin = int(max(8, min(56, max(int(pw), int(ph), 24))))
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
                    self._anchor_revalidate_needed = False
            except Exception:
                pass
        if buy_box is None and not bool(fast_anchor_only):
            cand_global = self.screen.locate("btn_buy", timeout=0.35)
            if cand_global is not None:
                buy_box = cand_global
                self._detail_ui_cache["btn_buy"] = cand_global
                btn_source = "global"
                self._anchor_revalidate_needed = False
        btn_ms = int((time.perf_counter() - t_btn) * 1000.0)
        self._last_btn_match_ms = int(btn_ms)
        if buy_box is None:
            self._last_btn_source = "missing"
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_6_NAME,
                phase="ROI裁剪与OCR",
                message=f"未找到购买按钮，无法计算 ROI | 匹配耗时={btn_ms}ms",
            )
            self._last_avg_ocr_ok = False
            self._avg_ocr_streak = self._avg_ocr_streak + 1
            self._anchor_revalidate_needed = True
            return None
        self._last_btn_source = str(btn_source)
        self._log_step_debug_text(
            item_disp,
            purchased_str,
            STEP_6_NAME,
            phase="ROI裁剪与OCR",
            message=f"按钮来源={btn_source} | 坐标={buy_box} | 匹配耗时={btn_ms}ms",
        )

        def _mark_failed() -> None:
            self._last_avg_ocr_ok = False
            self._avg_ocr_streak += 1
            if btn_source == "cache":
                self._anchor_revalidate_needed = True

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
            _mark_failed()
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_6_NAME,
                phase="ROI裁剪与OCR",
                message="ROI 尺寸无效",
            )
            return None
        roi = (x_left, y_top, width, height)
        img = self.screen.screenshot_region(roi)
        if img is None:
            _mark_failed()
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_6_NAME,
                phase="ROI裁剪与OCR",
                message="ROI 截屏失败",
            )
            return None
        # 分割与缩放
        try:
            w0, h0 = img.size
        except Exception:
            _mark_failed()
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_6_NAME,
                phase="ROI裁剪与OCR",
                message="ROI 尺寸无法获取",
            )
            return None
        if h0 < 2:
            _mark_failed()
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_6_NAME,
                phase="ROI裁剪与OCR",
                message="ROI 高度过小",
            )
            return None
        mid_h = h0 // 2
        img_top = img.crop((0, 0, w0, mid_h))
        img_bot = img.crop((0, mid_h, w0, h0))
        self._log_step_debug_text(
            item_disp,
            purchased_str,
            STEP_6_NAME,
            phase="ROI裁剪与OCR",
            message=(
                f"ROI=({x_left},{y_top},{width},{height}) | "
                f"上下=({img_top.width}x{img_top.height})/({img_bot.width}x{img_bot.height})"
            ),
        )
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
        self._log_step_debug_text(
            item_disp,
            purchased_str,
            STEP_6_NAME,
            phase="ROI参数",
            message=f"dist={dist} height={hei} scale={sc}",
        )

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
                _lut = [255 if i > 128 else 0 for i in range(256)]
                bin_top = img_top.convert("L").point(_lut)
            except Exception:
                bin_top = img_top
            try:
                _lut = [255 if i > 128 else 0 for i in range(256)]
                bin_bot = img_bot.convert("L").point(_lut)
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
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_6_NAME,
                phase="数字OCR",
                message=f"候选={cand_vals} 选={getattr(cand, 'value', None)} 耗时={ocr_ms}ms",
            )
        except Exception:
            val = None
            ocr_ms = -1

        try:
            expected_floor_val = int(expected_floor or 0)
        except Exception:
            expected_floor_val = 0

        def _accept_and_record(v: int) -> Optional[int]:
            """接受 OCR 结果，并在异常低值时丢弃后继续识别轮。"""
            if not isinstance(v, int) or v <= 0:
                _mark_failed()
                return None
            if expected_floor_val > 0 and int(v) * 2 < int(expected_floor_val):
                _mark_failed()
                self._log_step_debug_text(
                    item_disp,
                    purchased_str,
                    STEP_6_NAME,
                    phase="数字OCR",
                    message=f"值={int(v)} 低于异常下限<{int(expected_floor_val)}/2，丢弃并继续识别",
                )
                return None
            self._last_avg_ocr_ok = True
            self._avg_ocr_streak = 0
            self._anchor_revalidate_needed = False
            self._log_step_info_text(
                item_disp,
                purchased_str,
                STEP_6_NAME,
                phase="数字OCR",
                state="成功",
                message=f"平均价={v}",
            )
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
            self._log_step_info_text(
                item_disp,
                purchased_str,
                STEP_6_NAME,
                phase="文本OCR",
                message=f"Umi OCR 失败：{e}",
            )
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
        self._log_step_debug_text(
            item_disp,
            purchased_str,
            STEP_6_NAME,
            phase="文本OCR",
            message=(
                f"原文='{txt}' 解析={val2} "
                f"耗时={ocr_ms if 'ocr_ms' in locals() else -1}ms"
            ),
        )
        if isinstance(val2, int) and val2 > 0:
            r2 = _accept_and_record(int(val2))
            if r2 is not None:
                return r2

        # 拒绝下半兜底：未识别平均单价则记为失败，由识别轮继续

        _mark_failed()
        return None

    # -------------------- 步骤 7：执行购买（普通/补货） --------------------
    def _find_qty_midpoint(self) -> Optional[Tuple[int, int]]:
        m = self._detail_ui_cache.get("qty_minus") or self.screen.locate("qty_minus", timeout=0.2)
        p = self._detail_ui_cache.get("qty_plus") or self.screen.locate("qty_plus", timeout=0.2)
        if m is None or p is None:
            return None
        try:
            self._detail_ui_cache["qty_minus"] = tuple(int(v) for v in m)
            self._detail_ui_cache["qty_plus"] = tuple(int(v) for v in p)
        except Exception:
            pass
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

    def _calc_qty_input_roi(self) -> Optional[Tuple[int, int, int, int]]:
        minus = self._detail_ui_cache.get("qty_minus")
        plus = self._detail_ui_cache.get("qty_plus")
        if minus is None or plus is None:
            _ = self._find_qty_midpoint()
            minus = self._detail_ui_cache.get("qty_minus")
            plus = self._detail_ui_cache.get("qty_plus")
        try:
            if minus is not None and plus is not None:
                left_box, right_box = sorted(
                    [tuple(int(v) for v in minus), tuple(int(v) for v in plus)],
                    key=lambda b: int(b[0]),
                )
                x0 = int(left_box[0] + left_box[2] + 2)
                x1 = int(right_box[0] - 2)
                cy = int(
                    (
                        left_box[1] + left_box[3] / 2
                        + right_box[1] + right_box[3] / 2
                    )
                    / 2
                )
                h = int(max(left_box[3], right_box[3]) + 10)
                if x1 > x0:
                    roi = (x0, cy - h // 2, x1 - x0, h)
                else:
                    roi = None
            else:
                roi = None
        except Exception:
            roi = None
        if roi is None:
            mid_box = self._detail_ui_cache.get("qty_mid")
            if mid_box is None:
                mid = self._find_qty_midpoint()
                if mid is None:
                    return None
                mid_box = (int(mid[0]) - 2, int(mid[1]) - 2, 4, 4)
            try:
                cx = int(mid_box[0] + mid_box[2] / 2)
                cy = int(mid_box[1] + mid_box[3] / 2)
                roi = (cx - 40, cy - 18, 80, 36)
            except Exception:
                return None
        try:
            sw, sh = self._pg.size()  # type: ignore[attr-defined]
        except Exception:
            sw, sh = 1920, 1080
        x, y, w, h = [int(v) for v in roi]
        x = max(0, min(sw - 2, x))
        y = max(0, min(sh - 2, y))
        w = max(1, min(w, sw - x))
        h = max(1, min(h, sh - y))
        return (x, y, w, h)

    def _read_quantity_value(
        self,
        item_disp: str,
        purchased_str: str,
    ) -> Optional[int]:
        roi = self._calc_qty_input_roi()
        if roi is None:
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_7_NAME,
                phase="数量校验",
                message="未能推导数量输入区域",
            )
            return None
        img = self.screen.screenshot_region(roi)
        if img is None:
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_7_NAME,
                phase="数量校验",
                message=f"数量截图失败 ROI={roi}",
            )
            return None
        try:
            img = img.resize((max(1, int(img.width * 2.0)), max(1, int(img.height * 2.0))))
        except Exception:
            pass
        try:
            import numpy as _np  # type: ignore
            import cv2 as _cv2  # type: ignore
            from PIL import Image as _PIL  # type: ignore

            arr = _np.array(img)
            gray = _cv2.cvtColor(_cv2.cvtColor(arr, _cv2.COLOR_RGB2BGR), _cv2.COLOR_BGR2GRAY)
            _thr, th = _cv2.threshold(gray, 0, 255, _cv2.THRESH_BINARY + _cv2.THRESH_OTSU)
            bin_img = _PIL.fromarray(th)
        except Exception:
            try:
                bin_img = img.convert("L").point(lambda p: 255 if p > 128 else 0)  # type: ignore[arg-type]
            except Exception:
                bin_img = img
        try:
            ocfg = self.cfg.get("umi_ocr") or {}
            cands = recognize_numbers(
                bin_img,
                base_url=str(ocfg.get("base_url", "http://127.0.0.1:1224")),
                timeout=float(ocfg.get("timeout_sec", 2.5) or 2.5),
                options=dict(ocfg.get("options", {}) or {}),
                offset=(int(roi[0]), int(roi[1])),
            )
            vals = [int(getattr(c, "value", 0)) for c in (cands or []) if getattr(c, "value", None) is not None]
        except Exception:
            vals = []
        val = max(vals) if vals else None
        self._log_step_debug_text(
            item_disp,
            purchased_str,
            STEP_7_NAME,
            phase="数量校验",
            message=f"ROI={roi} 候选={vals} 选={val}",
        )
        return int(val) if isinstance(val, int) and val > 0 else None

    def _random_overlay_dismiss_point(self) -> Tuple[int, int]:
        try:
            sw, sh = self._pg.size()  # type: ignore[attr-defined]
        except Exception:
            sw, sh = 1920, 1080
        x = random.randint(max(20, int(sw * 0.30)), max(21, int(sw * 0.70)))
        y = random.randint(max(20, int(sh * 0.25)), max(21, int(sh * 0.65)))
        return int(x), int(y)

    def _fast_random_dismiss_success_overlay(
        self,
        btn_box: Tuple[int, int, int, int],
        interval_sec: float,
    ) -> None:
        try:
            rx, ry = self._random_overlay_dismiss_point()
            self.screen.click_point(rx, ry, clicks=1)
            safe_sleep(max(interval_sec, 0.03))
        except Exception:
            try:
                self._fast_close_success_overlay(btn_box, interval_sec)
            except Exception:
                pass

    def _fast_random_dismiss_and_rebuy(
        self,
        btn_box: Tuple[int, int, int, int],
        interval_sec: float,
    ) -> None:
        try:
            rx, ry = self._random_overlay_dismiss_point()
            self.screen.click_point(rx, ry, clicks=1)
            safe_sleep(max(interval_sec, 0.03))
            self.screen.click_center(btn_box)
        except Exception:
            try:
                self._fast_close_and_rebuy(btn_box, interval_sec)
            except Exception:
                pass

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
        thr, limit, prem, restock, restock_limit, restock_prem = self._resolve_price_limits(task)
        # 会话准备：弹药 Max / 非弹药数量
        is_ammo = (goods.big_category or "").strip() == "弹药"
        used_max = False
        typed_qty = 0
        prepared_qty = 0
        prep_progress = f"{purchased_so_far}/{target_total}"
        if is_ammo:
            mx = self._get_btn_box(goods, "btn_max", timeout=0.35)
            qty_after_max = None
            if mx is not None:
                self._log_step_debug_text(
                    item_disp,
                    prep_progress,
                    STEP_7_NAME,
                    phase="执行补货",
                    message=f"数量准备=优先点击Max 按钮框={mx}",
                )
                self.screen.click_center(mx)
                safe_sleep(max(0.05, float(getattr(self.timings, "ocr_min_wait", 0.05) or 0.05)))
                qty_after_max = self._read_quantity_value(item_disp, prep_progress)
                if qty_after_max == 120:
                    prepared_qty = 120
                    used_max = True
                    self._log_step_debug_text(
                        item_disp,
                        prep_progress,
                        STEP_7_NAME,
                        phase="执行补货",
                        message="数量准备=Max校验成功 值=120",
                    )
                else:
                    self._log_step_debug_text(
                        item_disp,
                        prep_progress,
                        STEP_7_NAME,
                        phase="执行补货",
                        message=f"数量准备=Max校验未通过 值={qty_after_max}，回退手动输入120",
                    )
            if prepared_qty <= 0:
                if self._focus_and_type_quantity_fast(120):
                    typed_qty = 120
                    qty_after_type = self._read_quantity_value(item_disp, prep_progress)
                    if qty_after_type is None or qty_after_type == 120:
                        prepared_qty = 120
                        self._log_step_debug_text(
                            item_disp,
                            prep_progress,
                            STEP_7_NAME,
                            phase="执行补货",
                            message=f"数量准备=输入120{'(校验成功)' if qty_after_type == 120 else '(未校验到明确值)'}",
                        )
                    else:
                        prepared_qty = max(1, int(qty_after_type))
                        self._log_step_debug_text(
                            item_disp,
                            prep_progress,
                            STEP_7_NAME,
                            phase="执行补货",
                            message=f"数量准备=输入120后校验值={qty_after_type}，按当前值继续",
                        )
                else:
                    prepared_qty = 10
                    self._log_step_debug_text(
                        item_disp,
                        prep_progress,
                        STEP_7_NAME,
                        phase="执行补货",
                        message="数量准备失败：Max与手动输入均未成功，将按界面当前数量尝试",
                    )
        else:
            if self._focus_and_type_quantity_fast(5):
                typed_qty = 5
                prepared_qty = 5
                self._log_step_debug_text(
                    item_disp,
                    prep_progress,
                    STEP_7_NAME,
                    phase="执行补货",
                    message="数量准备=输入5",
                )
            else:
                typed_qty = 1
                prepared_qty = 1
                self._log_step_debug_text(
                    item_disp,
                    prep_progress,
                    STEP_7_NAME,
                    phase="执行补货",
                    message="数量准备失败：未命中数量输入锚点，将按界面当前数量尝试",
                )

        def _effective_restock_qty() -> int:
            if prepared_qty > 0:
                return int(prepared_qty)
            if is_ammo:
                return 120 if used_max else 10
            return max(1, int(typed_qty or 5))

        # 循环直到退出
        while True:
            purchased_str = f"{purchased_so_far + bought}/{target_total}"
            try:
                thr_base = int(task.get("price_threshold", 0) or 0)
            except Exception:
                thr_base = 0
            # 补货优先：补货循环以 restock 为基准，避免普通阈值过高导致识别过滤过严。
            base = restock if restock > 0 else thr_base
            step6_t0 = time.perf_counter()
            unit_price = self._read_avg_price_with_rounds(
                goods,
                item_disp,
                purchased_str,
                expected_floor=base if base > 0 else None,
                allow_bottom_fallback=False,
            )
            step6_ms = int((time.perf_counter() - step6_t0) * 1000.0)
            if unit_price is None or unit_price <= 0:
                # 识别轮连续失败（达到阈值），执行障碍清理逻辑并退出本次详情
                self._log_step6(
                    item_disp,
                    purchased_str,
                    step6_ms,
                    "ocr_failed",
                    unit_price=None,
                    normal_limit=limit,
                    restock_limit=restock_limit,
                    reason="ocr_round_timeout",
                )
                self.step3_clear_obstacles(item_disp, purchased_str)
                self._log_step_info_text(
                    item_disp,
                    purchased_str,
                    STEP_6_NAME,
                    phase="均价读取",
                    message="平均价识别失败（补货，识别轮超时），已执行障碍清理",
                )
                self._set_cycle_meta(
                    reason="ocr_round_timeout",
                    step6_result="ocr_failed",
                    step7_result="not_run",
                    purchase_mode="restock",
                    continue_loop=True,
                    bought=bought,
                )
                return bought, True
            # 价格合理性（半阈下限）校验 → 决定是否写入历史
            legal, base_thr, floor_half = self._is_price_legal_for_history(int(unit_price), task)
            if not legal:
                self._log_step6(
                    item_disp,
                    purchased_str,
                    step6_ms,
                    "skip",
                    unit_price=int(unit_price),
                    normal_limit=limit,
                    restock_limit=restock_limit,
                    reason="below_sanity_floor",
                )
                _ = self._close_detail_with_wait(goods)
                self._log_info(
                    item_disp,
                    purchased_str,
                    build_context_message(
                        STEP_6_NAME,
                        phase="阈值判定",
                        message=(
                            f"均价={int(unit_price)} 不满足下限>基准/2"
                            f"（B={base_thr} 下限={floor_half}），以不符合价格阈值处理"
                        ),
                    ),
                )
                self._set_cycle_meta(
                    reason="below_sanity_floor",
                    step6_result="skip",
                    step7_result="not_run",
                    purchase_mode="restock",
                    continue_loop=True,
                    bought=bought,
                )
                return bought, True
            # 合理：写入价格历史
            try:
                append_price(
                    item_id=goods.id,
                    item_name=goods.name or goods.search_name or item_disp,
                    price=int(unit_price),
                    category=(goods.big_category or "") or None,
                    paths=self.history_paths,
                )
            except Exception:
                pass
            ok_restock = (restock > 0) and (unit_price <= restock_limit)
            if not ok_restock:
                self._log_step6(
                    item_disp,
                    purchased_str,
                    step6_ms,
                    "skip",
                    unit_price=int(unit_price),
                    normal_limit=limit,
                    restock_limit=restock_limit,
                    reason="price_over_limit",
                )
                _ = self._close_detail_with_wait(goods)
                self._log_info(
                    item_disp,
                    purchased_str,
                    build_context_message(
                        STEP_6_NAME,
                        phase="阈值判定",
                        message=f"均价={unit_price} 超过补货上限≤{restock_limit}(+{int(restock_prem)}%)，结束补货",
                    ),
                )
                self._set_cycle_meta(
                    reason="price_over_limit",
                    step6_result="skip",
                    step7_result="not_run",
                    purchase_mode="restock",
                    continue_loop=True,
                    bought=bought,
                )
                return bought, True
            self._log_step6(
                item_disp,
                purchased_str,
                step6_ms,
                "restock_buy",
                unit_price=int(unit_price),
                normal_limit=limit,
                restock_limit=restock_limit,
            )
            fast_mode = bool(getattr(self, "_fast_chain_mode", False))
            try:
                fast_max = int(getattr(self, "_fast_chain_max", 10) or 10)
            except Exception:
                fast_max = 10
            try:
                fast_interval_sec = float(getattr(self, "_fast_chain_interval_ms", 35.0) or 35.0) / 1000.0
            except Exception:
                fast_interval_sec = 0.035
            fast_interval_sec = max(0.03, fast_interval_sec)
            step7_t0 = time.perf_counter()
            b = (self._first_detail_buttons.get(goods.id, {}) or {}).get("btn_buy") or self.screen.locate("btn_buy", timeout=0.4)
            if b is None:
                self._log_step7(
                    item_disp,
                    purchased_str,
                    int((time.perf_counter() - step7_t0) * 1000.0),
                    "fail",
                    purchase_mode="restock",
                    unit_price=int(unit_price),
                    qty=_effective_restock_qty(),
                    used_max=used_max,
                    reason="missing_btn_buy",
                )
                _ = self._close_detail_with_wait(goods)
                self._log_step_info_text(
                    item_disp,
                    purchased_str,
                    STEP_7_NAME,
                    phase="执行补货",
                    message="未找到购买按钮，已关闭详情",
                )
                self._set_cycle_meta(
                    reason="missing_btn_buy",
                    step6_result="restock_buy",
                    step7_result="fail",
                    purchase_mode="restock",
                    continue_loop=True,
                    bought=bought,
                )
                return bought, True
            if (not fast_mode) or fast_max <= 1:
                self._log_step_debug_text(
                    item_disp,
                    purchased_str,
                    STEP_7_NAME,
                    phase="执行补货",
                    message="决策=补货(单次) | 未启用快速连击模式",
                )
                self._log_step_debug_text(
                    item_disp,
                    purchased_str,
                    STEP_7_NAME,
                    phase="执行补货",
                    message=f"已点击购买 按钮框={b}",
                )
                self.screen.click_center(b)
                _res = self._wait_buy_result_window(item_disp, purchased_str)
                if _res == "ok":
                    inc = _effective_restock_qty()
                    bought += int(inc)
                    self._log_step7(
                        item_disp,
                        purchased_str,
                        int((time.perf_counter() - step7_t0) * 1000.0),
                        "success",
                        purchase_mode="restock",
                        unit_price=int(unit_price),
                        qty=int(inc),
                        bought=int(inc),
                        used_max=used_max,
                    )
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
                        self._set_cycle_meta(
                            reason="target_reached",
                            step6_result="restock_buy",
                            step7_result="success",
                            purchase_mode="restock",
                            continue_loop=False,
                            bought=bought,
                        )
                        return bought, False
                    continue
                if _res == "fail":
                    self._log_step7(
                        item_disp,
                        purchased_str,
                        int((time.perf_counter() - step7_t0) * 1000.0),
                        "fail",
                        purchase_mode="restock",
                        unit_price=int(unit_price),
                        qty=_effective_restock_qty(),
                        used_max=used_max,
                        reason="buy_fail",
                    )
                    _ = self._close_detail_with_wait(goods)
                    self._log_step_info_text(
                        item_disp,
                        purchased_str,
                        STEP_7_NAME,
                        phase="执行补货",
                        message="购买失败，已关闭详情",
                    )
                    self._set_cycle_meta(
                        reason="buy_fail",
                        step6_result="restock_buy",
                        step7_result="fail",
                        purchase_mode="restock",
                        continue_loop=True,
                        bought=bought,
                    )
                    return bought, True
                self._log_step7(
                    item_disp,
                    purchased_str,
                    int((time.perf_counter() - step7_t0) * 1000.0),
                    "unknown",
                    purchase_mode="restock",
                    unit_price=int(unit_price),
                    qty=_effective_restock_qty(),
                    used_max=used_max,
                    reason="buy_result_unknown",
                )
                _ = self._close_detail_with_wait(goods)
                self._log_step_info_text(
                    item_disp,
                    purchased_str,
                    STEP_7_NAME,
                    phase="执行补货",
                    message="结果未知，已关闭详情",
                )
                self._set_cycle_meta(
                    reason="buy_result_unknown",
                    step6_result="restock_buy",
                    step7_result="unknown",
                    purchase_mode="restock",
                    continue_loop=True,
                    bought=bought,
                )
                return bought, True

            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_7_NAME,
                phase="补货连击",
                message=(
                    f"决策=补货(快速连击) | unit={unit_price} "
                    f"restock_limit={restock_limit} qty={_effective_restock_qty()} max_chain={fast_max}"
                ),
            )
            chain_count = 0
            bought_before_chain = int(bought)
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_7_NAME,
                phase="补货连击",
                message=f"首次点击购买 按钮框={b}",
            )
            self.screen.click_center(b)
            while chain_count < fast_max:
                _res = self._wait_buy_result_window(item_disp, purchased_str)
                if _res != "ok":
                    step7_result = "fail" if _res == "fail" else ("partial_success" if bought > bought_before_chain else "unknown")
                    reason = "buy_fail" if _res == "fail" else "buy_result_unknown"
                    self._log_step7(
                        item_disp,
                        purchased_str,
                        int((time.perf_counter() - step7_t0) * 1000.0),
                        step7_result,
                        purchase_mode="restock_fast",
                        unit_price=int(unit_price),
                        qty=_effective_restock_qty(),
                        bought=int(bought - bought_before_chain),
                        chain_count=int(chain_count),
                        max_chain=int(fast_max),
                        used_max=used_max,
                        reason=reason,
                    )
                    _ = self._close_detail_with_wait(goods)
                    self._log_step_info_text(
                        item_disp,
                        purchased_str,
                        STEP_7_NAME,
                        phase="补货连击",
                        message="未识别到购买成功，已关闭详情并准备重新识别价格",
                    )
                    self._set_cycle_meta(
                        reason=reason,
                        step6_result="restock_buy",
                        step7_result=step7_result,
                        purchase_mode="restock_fast",
                        continue_loop=True,
                        bought=bought,
                    )
                    return bought, True

                inc = _effective_restock_qty()
                bought += int(inc)
                chain_count += 1
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

                if target_total > 0 and (purchased_so_far + bought) >= target_total:
                    self._fast_random_dismiss_success_overlay(b, fast_interval_sec)
                    _ = self._close_detail_with_wait(goods)
                    h = self.screen.locate("btn_home", timeout=2.0)
                    if h is not None:
                        self.screen.click_center(h)
                        safe_sleep(self.timings.post_nav)
                    self._log_step7(
                        item_disp,
                        purchased_str,
                        int((time.perf_counter() - step7_t0) * 1000.0),
                        "success",
                        purchase_mode="restock_fast",
                        unit_price=int(unit_price),
                        qty=int(inc),
                        bought=int(bought - bought_before_chain),
                        chain_count=int(chain_count),
                        max_chain=int(fast_max),
                        used_max=used_max,
                        reason="target_reached",
                    )
                    self._set_cycle_meta(
                        reason="target_reached",
                        step6_result="restock_buy",
                        step7_result="success",
                        purchase_mode="restock_fast",
                        continue_loop=False,
                        bought=bought,
                    )
                    return bought, False

                self._log_step_debug_text(
                    item_disp,
                    purchased_str,
                    STEP_7_NAME,
                    phase="补货连击",
                    message=f"第{chain_count}次购买成功(本轮最多 {fast_max} 次)",
                )

                if chain_count >= fast_max:
                    self._fast_random_dismiss_success_overlay(b, fast_interval_sec)
                    self._log_step7(
                        item_disp,
                        purchased_str,
                        int((time.perf_counter() - step7_t0) * 1000.0),
                        "success",
                        purchase_mode="restock_fast",
                        unit_price=int(unit_price),
                        qty=int(inc),
                        bought=int(bought - bought_before_chain),
                        chain_count=int(chain_count),
                        max_chain=int(fast_max),
                        used_max=used_max,
                        reason="chain_limit_reached",
                    )
                    break

                self._fast_random_dismiss_and_rebuy(b, fast_interval_sec)

            self._set_cycle_meta(
                reason="chain_limit_reached",
                step6_result="restock_buy",
                step7_result="success",
                purchase_mode="restock_fast",
                continue_loop=True,
                bought=bought,
            )
            continue

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
        self._set_cycle_meta(
            reason="init",
            step6_result="not_run",
            step7_result="not_run",
            purchase_mode="none",
            continue_loop=True,
            bought=0,
        )
        with StageTimer(
            lambda lv, m: self._log_debug(item_disp, purchased_str, m),
            STEP_2_NAME,
            "进入详情与购买循环",
        ):
            used_cache = goods.id in self._pos_cache
            if not self._open_detail_from_cache_or_match(goods):
                if not used_cache:
                    ok_ctx = self.step4_build_search_context(goods, item_disp=item_disp, purchased_str=purchased_str)
                    if ok_ctx and self._open_detail_from_cache_or_match(goods):
                        pass
                    else:
                        if used_cache:
                            self._log_step_info_text(
                                item_disp,
                                purchased_str,
                                STEP_5_NAME,
                                phase="进入详情",
                                message="缓存坐标无效，打开详情失败",
                            )
                        else:
                            self._log_step_info_text(
                                item_disp,
                                purchased_str,
                                STEP_5_NAME,
                                phase="进入详情",
                                message="未匹配到商品模板，打开详情失败",
                            )
                        self._set_cycle_meta(
                            reason="open_detail_failed",
                            step6_result="not_run",
                            step7_result="not_run",
                            purchase_mode="none",
                            continue_loop=True,
                            bought=0,
                        )
                        return 0, True
            # 首次缓存按钮
            self._ensure_first_detail_buttons(goods)

        if self._stop_requested():
            self._set_cycle_meta(
                reason="stop_requested",
                step6_result="not_run",
                step7_result="not_run",
                purchase_mode="none",
                continue_loop=False,
                bought=0,
            )
            return 0, False

        bought = 0
        while True:
            if self._stop_requested():
                self._set_cycle_meta(
                    reason="stop_requested",
                    step6_result="not_run",
                    step7_result="not_run",
                    purchase_mode="none",
                    continue_loop=False,
                    bought=bought,
                )
                return bought, False
            purchased_str = f"{purchased_so_far + bought}/{target_total}"
            try:
                thr_base = int(task.get("price_threshold", 0) or 0)
            except Exception:
                thr_base = 0
            try:
                rest_base = int(task.get("restock_price", 0) or 0)
            except Exception:
                rest_base = 0
            # 补货优先：当同时配置补货上限与普通阈值时，优先以补货为识别过滤基准。
            base = rest_base if rest_base > 0 else thr_base
            step6_t0 = time.perf_counter()
            unit_price = self._read_avg_price_with_rounds(
                goods,
                item_disp,
                purchased_str,
                expected_floor=base if base > 0 else None,
                allow_bottom_fallback=False,
            )
            step6_ms = int((time.perf_counter() - step6_t0) * 1000.0)
            thr, limit, prem, restock, restock_limit, restock_prem = self._resolve_price_limits(task)
            if self._stop_requested():
                self._set_cycle_meta(
                    reason="stop_requested",
                    step6_result="not_run",
                    step7_result="not_run",
                    purchase_mode="none",
                    continue_loop=False,
                    bought=bought,
                )
                return bought, False
            if unit_price is None or unit_price <= 0:
                # 识别轮连续失败（达到阈值），执行障碍清理逻辑并退出本次详情
                self._log_step6(
                    item_disp,
                    purchased_str,
                    step6_ms,
                    "ocr_failed",
                    unit_price=None,
                    normal_limit=limit,
                    restock_limit=restock_limit,
                    reason="ocr_round_timeout",
                )
                self.step3_clear_obstacles(item_disp, purchased_str)
                self._log_step_info_text(
                    item_disp,
                    purchased_str,
                    STEP_6_NAME,
                    phase="均价读取",
                    message="平均单价识别失败（识别轮超时），已执行障碍清理",
                )
                self._set_cycle_meta(
                    reason="ocr_round_timeout",
                    step6_result="ocr_failed",
                    step7_result="not_run",
                    purchase_mode="none",
                    continue_loop=True,
                    bought=bought,
                )
                return bought, True

            # 价格合理性（半阈下限）校验 → 决定是否写入历史
            legal, base_thr, floor_half = self._is_price_legal_for_history(int(unit_price), task)
            if not legal:
                self._log_step6(
                    item_disp,
                    purchased_str,
                    step6_ms,
                    "skip",
                    unit_price=int(unit_price),
                    normal_limit=limit,
                    restock_limit=restock_limit,
                    reason="below_sanity_floor",
                )
                self._log_info(
                    item_disp,
                    purchased_str,
                    build_context_message(
                        STEP_6_NAME,
                        phase="阈值判定",
                        message=(
                            f"均价={int(unit_price)} 不满足下限>基准/2"
                            f"（B={base_thr} 下限={floor_half}），以不符合价格阈值处理"
                        ),
                    ),
                )
                _ = self._close_detail_with_wait(goods)
                self._set_cycle_meta(
                    reason="below_sanity_floor",
                    step6_result="skip",
                    step7_result="not_run",
                    purchase_mode="none",
                    continue_loop=True,
                    bought=bought,
                )
                return bought, True
            # 合理：写入价格历史
            try:
                append_price(
                    item_id=goods.id,
                    item_name=goods.name or goods.search_name or item_disp,
                    price=int(unit_price),
                    category=(goods.big_category or "") or None,
                    paths=self.history_paths,
                )
            except Exception:
                pass

            # 价格阈值/补货上限解析与判定（支持溢价百分比）
            # 约定：任何阈值为 0 表示“不购买/禁用”。当同时配置补货与普通阈值时，优先补货；
            ok_restock = (restock > 0) and (unit_price <= restock_limit)
            ok_normal = (thr > 0) and (unit_price <= limit)

            # 信息日志：输出两条路径的阈值线，便于人工核对
            if restock > 0:
                self._log_info(
                    item_disp,
                    purchased_str,
                    build_context_message(
                        STEP_6_NAME,
                        phase="阈值判定",
                        message=(
                            f"均价={unit_price} 普≤{limit if limit>0 else 0}(+{int(prem)}%) "
                            f"补≤{restock_limit}(+{int(restock_prem)}%) "
                            f"(下限>{(restock//2) if restock>0 else (thr//2 if thr>0 else 0)})"
                        ),
                    ),
                )
            else:
                self._log_info(
                    item_disp,
                    purchased_str,
                    build_context_message(
                        STEP_6_NAME,
                        phase="阈值判定",
                        message=f"均价={unit_price} 阈≤{limit if limit>0 else 0}(+{int(prem)}%) (下限>{thr//2 if thr>0 else 0})",
                    ),
                )

            # 决策点（仅一条 debug，收敛但足够排障）
            if ok_restock:
                self._log_step6(
                    item_disp,
                    purchased_str,
                    step6_ms,
                    "restock_buy",
                    unit_price=int(unit_price),
                    normal_limit=limit,
                    restock_limit=restock_limit,
                )
                self._log_step_debug_text(
                    item_disp,
                    purchased_str,
                    STEP_6_NAME,
                    phase="阈值判定",
                    message=f"决策=补货 | unit={unit_price} limit={limit} restock_limit={restock_limit}",
                )
                got_more, cont = self._restock_fast_loop(goods, task, purchased_so_far + bought)
                bought += int(got_more)
                return bought, cont
            if ok_normal:
                self._log_step6(
                    item_disp,
                    purchased_str,
                    step6_ms,
                    "normal_buy",
                    unit_price=int(unit_price),
                    normal_limit=limit,
                    restock_limit=restock_limit,
                )
                self._log_step_debug_text(
                    item_disp,
                    purchased_str,
                    STEP_6_NAME,
                    phase="阈值判定",
                    message=f"决策=普通 | unit={unit_price} limit={limit} restock_limit={restock_limit}",
                )
            else:
                self._log_step6(
                    item_disp,
                    purchased_str,
                    step6_ms,
                    "skip",
                    unit_price=int(unit_price),
                    normal_limit=limit,
                    restock_limit=restock_limit,
                    reason="price_over_limit",
                )
                self._log_step_debug_text(
                    item_disp,
                    purchased_str,
                    STEP_6_NAME,
                    phase="阈值判定",
                    message=f"决策=放弃 | unit={unit_price} limit={limit} restock_limit={restock_limit}",
                )
                _ = self._close_detail_with_wait(goods)
                self._set_cycle_meta(
                    reason="price_over_limit",
                    step6_result="skip",
                    step7_result="not_run",
                    purchase_mode="none",
                    continue_loop=True,
                    bought=bought,
                )
                return bought, True

            # 统一读取快速连击参数（按任务高级配置注入）
            fast_mode = bool(getattr(self, "_fast_chain_mode", False))
            try:
                fast_max = int(getattr(self, "_fast_chain_max", 10) or 10)
            except Exception:
                fast_max = 10
            try:
                fast_interval_sec = float(getattr(self, "_fast_chain_interval_ms", 35.0) or 35.0) / 1000.0
            except Exception:
                fast_interval_sec = 0.035
            fast_interval_sec = max(0.03, fast_interval_sec)

            step7_t0 = time.perf_counter()
            b = self._first_detail_buttons.get(goods.id, {}).get("btn_buy") or self.screen.locate("btn_buy", timeout=0.4)
            if b is None:
                self._log_step7(
                    item_disp,
                    purchased_str,
                    int((time.perf_counter() - step7_t0) * 1000.0),
                    "fail",
                    purchase_mode="normal",
                    unit_price=int(unit_price),
                    qty=10 if (goods.big_category or "").strip() == "弹药" else 1,
                    reason="missing_btn_buy",
                )
                _ = self._close_detail_with_wait(goods)
                self._log_step_info_text(
                    item_disp,
                    purchased_str,
                    STEP_7_NAME,
                    phase="执行购买",
                    message="未找到购买按钮，已关闭详情",
                )
                self._set_cycle_meta(
                    reason="missing_btn_buy",
                    step6_result="normal_buy",
                    step7_result="fail",
                    purchase_mode="normal",
                    continue_loop=True,
                    bought=bought,
                )
                return bought, True

            # 非快速连击模式：保持原有“一次 OCR 一次购买”的节奏
            if (not fast_mode) or fast_max <= 1:
                self._log_step_debug_text(
                    item_disp,
                    purchased_str,
                    STEP_7_NAME,
                    phase="执行购买",
                    message="决策=普通(单次) | 未启用快速连击模式",
                )
                self._log_step_debug_text(
                    item_disp,
                    purchased_str,
                    STEP_7_NAME,
                    phase="执行购买",
                    message=f"已点击购买 按钮框={b}",
                )
                self.screen.click_center(b)
                _res = self._wait_buy_result_window(item_disp, purchased_str)
                if _res == "ok":
                    is_ammo = (goods.big_category or "").strip() == "弹药"
                    inc = 10 if is_ammo else 1
                    bought += int(inc)
                    self._log_step7(
                        item_disp,
                        purchased_str,
                        int((time.perf_counter() - step7_t0) * 1000.0),
                        "success",
                        purchase_mode="normal",
                        unit_price=int(unit_price),
                        qty=int(inc),
                        bought=int(inc),
                    )
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
                    if target_total > 0 and (purchased_so_far + bought) >= target_total:
                        _ = self._close_detail_with_wait(goods)
                        self._set_cycle_meta(
                            reason="target_reached",
                            step6_result="normal_buy",
                            step7_result="success",
                            purchase_mode="normal",
                            continue_loop=False,
                            bought=bought,
                        )
                        return bought, False
                    self._set_cycle_meta(
                        reason="buy_success",
                        step6_result="normal_buy",
                        step7_result="success",
                        purchase_mode="normal",
                        continue_loop=True,
                        bought=bought,
                    )
                    continue
                if _res == "fail":
                    self._log_step7(
                        item_disp,
                        purchased_str,
                        int((time.perf_counter() - step7_t0) * 1000.0),
                        "fail",
                        purchase_mode="normal",
                        unit_price=int(unit_price),
                        qty=10 if (goods.big_category or "").strip() == "弹药" else 1,
                        reason="buy_fail",
                    )
                    _ = self._close_detail_with_wait(goods)
                    self._log_step_info_text(
                        item_disp,
                        purchased_str,
                        STEP_7_NAME,
                        phase="执行购买",
                        message="购买失败，已关闭详情",
                    )
                    self._set_cycle_meta(
                        reason="buy_fail",
                        step6_result="normal_buy",
                        step7_result="fail",
                        purchase_mode="normal",
                        continue_loop=True,
                        bought=bought,
                    )
                    return bought, True
                self._log_step7(
                    item_disp,
                    purchased_str,
                    int((time.perf_counter() - step7_t0) * 1000.0),
                    "unknown",
                    purchase_mode="normal",
                    unit_price=int(unit_price),
                    qty=10 if (goods.big_category or "").strip() == "弹药" else 1,
                    reason="buy_result_unknown",
                )
                _ = self._close_detail_with_wait(goods)
                self._log_step_info_text(
                    item_disp,
                    purchased_str,
                    STEP_7_NAME,
                    phase="执行购买",
                    message="结果未知，已关闭详情",
                )
                self._set_cycle_meta(
                    reason="buy_result_unknown",
                    step6_result="normal_buy",
                    step7_result="unknown",
                    purchase_mode="normal",
                    continue_loop=True,
                    bought=bought,
                )
                return bought, True

            # 快速连击模式：一次 OCR 后连续多次购买，达到上限或失败后重新 OCR
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_7_NAME,
                phase="快速连击",
                message=(
                    f"决策=普通(快速连击) | unit={unit_price} "
                    f"limit={limit} restock_limit={restock_limit} max_chain={fast_max}"
                ),
            )

            chain_count = 0
            bought_before_chain = int(bought)
            # 首次点击：发起第一笔购买
            self._log_step_debug_text(
                item_disp,
                purchased_str,
                STEP_7_NAME,
                phase="快速连击",
                message=f"首次点击购买 按钮框={b}",
            )
            self.screen.click_center(b)

            while chain_count < fast_max:
                _res = self._wait_buy_result_window(item_disp, purchased_str)
                if _res != "ok":
                    # 未识别到购买成功视为失败：关闭详情并交由外层重新 OCR 判断
                    step7_result = "fail" if _res == "fail" else ("partial_success" if bought > bought_before_chain else "unknown")
                    reason = "buy_fail" if _res == "fail" else "buy_result_unknown"
                    self._log_step7(
                        item_disp,
                        purchased_str,
                        int((time.perf_counter() - step7_t0) * 1000.0),
                        step7_result,
                        purchase_mode="normal_fast",
                        unit_price=int(unit_price),
                        qty=10 if (goods.big_category or "").strip() == "弹药" else 1,
                        bought=int(bought - bought_before_chain),
                        chain_count=int(chain_count),
                        max_chain=int(fast_max),
                        reason=reason,
                    )
                    _ = self._close_detail_with_wait(goods)
                    self._log_step_info_text(
                        item_disp,
                        purchased_str,
                        STEP_7_NAME,
                        phase="快速连击",
                        message="未识别到购买成功，已关闭详情并准备重新识别价格",
                    )
                    self._set_cycle_meta(
                        reason=reason,
                        step6_result="normal_buy",
                        step7_result=step7_result,
                        purchase_mode="normal_fast",
                        continue_loop=True,
                        bought=bought,
                    )
                    return bought, True

                # 识别到一次成功购买
                is_ammo = (goods.big_category or "").strip() == "弹药"
                inc = 10 if is_ammo else 1
                bought += int(inc)
                chain_count += 1
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

                if target_total > 0 and (purchased_so_far + bought) >= target_total:
                    self._dismiss_success_overlay_with_wait(item_disp, purchased_str, goods=goods)
                    _ = self._close_detail_with_wait(goods)
                    self._log_step7(
                        item_disp,
                        purchased_str,
                        int((time.perf_counter() - step7_t0) * 1000.0),
                        "success",
                        purchase_mode="normal_fast",
                        unit_price=int(unit_price),
                        qty=int(inc),
                        bought=int(bought - bought_before_chain),
                        chain_count=int(chain_count),
                        max_chain=int(fast_max),
                        reason="target_reached",
                    )
                    self._set_cycle_meta(
                        reason="target_reached",
                        step6_result="normal_buy",
                        step7_result="success",
                        purchase_mode="normal_fast",
                        continue_loop=False,
                        bought=bought,
                    )
                    return bought, False

                self._log_step_debug_text(
                    item_disp,
                    purchased_str,
                    STEP_7_NAME,
                    phase="快速连击",
                    message=f"第{chain_count}次购买成功(本轮最多 {fast_max} 次)",
                )

                # 连击达到上限：只关闭遮罩，跳出本轮，回到 OCR 流程
                if chain_count >= fast_max:
                    self._fast_close_success_overlay(b, fast_interval_sec)
                    self._log_step7(
                        item_disp,
                        purchased_str,
                        int((time.perf_counter() - step7_t0) * 1000.0),
                        "success",
                        purchase_mode="normal_fast",
                        unit_price=int(unit_price),
                        qty=int(inc),
                        bought=int(bought - bought_before_chain),
                        chain_count=int(chain_count),
                        max_chain=int(fast_max),
                        reason="chain_limit_reached",
                    )
                    break

                # 未达到上限：关闭遮罩并在同一位置再次点击一次，发起下一次购买
                self._fast_close_and_rebuy(b, fast_interval_sec)

            # 跳出快速连击循环后，继续最外层 while True，进入下一轮 OCR
            self._set_cycle_meta(
                reason="chain_limit_reached",
                step6_result="normal_buy",
                step7_result="success",
                purchase_mode="normal_fast",
                continue_loop=True,
                bought=bought,
            )
            continue

    # 工具：清理临时卡片坐标缓存
    def clear_pos(self, goods_id: Optional[str] = None) -> None:
        if goods_id is None:
            self._pos_cache.clear()
        else:
            self._pos_cache.pop(goods_id, None)

    def clear_all_caches(self, goods_id: Optional[str] = None) -> None:
        """清理与缓存相关的所有数据结构。

        - 若提供 goods_id：仅清理该商品的卡片坐标与首次按钮缓存；
        - 若不提供 goods_id：同时清理全局 UI 缓存与列表区域缓存；
        - 始终清空会话级 `_detail_ui_cache`，确保下次进入详情重新识别。
        """
        try:
            if goods_id is None:
                self._pos_cache.clear()
                self._first_detail_cached.clear()
                self._first_detail_buttons.clear()
                self._global_ui_cache.clear()
                self._goods_list_region_cache = None
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
            post_close_detail=float(tuning.get("post_close_detail_sec", 0.05) or 0.05),
            post_success_click=float(tuning.get("post_success_click_sec", 0.05) or 0.05),
            post_nav=float(tuning.get("post_nav_sec", 0.05) or 0.05),
            buy_result_timeout=float(tuning.get("buy_result_timeout_sec", 0.35) or 0.35),
            buy_result_poll_step=float(tuning.get("buy_result_poll_step_sec", 0.02) or 0.02),
            poll_step=float(tuning.get("poll_step_sec", 0.02) or 0.02),
            ocr_min_wait=float(tuning.get("ocr_min_wait_sec", 0.05) or 0.05),
            step_delay=step_delay,
            ocr_round_window=float(tuning.get("ocr_round_window_sec", 0.25) or 0.25),
            ocr_round_step=float(tuning.get("ocr_round_step_sec", 0.02) or 0.02),
            ocr_round_fail_limit=int(tuning.get("ocr_round_fail_limit", 6) or 6),
            detail_open_settle=float(tuning.get("detail_open_settle_sec", 0.05) or 0.05),
            detail_cache_verify_timeout=float(tuning.get("detail_cache_verify_timeout_sec", 0.18) or 0.18),
            anchor_stabilize=float(tuning.get("anchor_stabilize_sec", 0.05) or 0.05),
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
        self.buyer.should_stop = self._stop.is_set

        # 处罚链路参数
        self._ocr_miss_streak: int = 0
        self._ocr_miss_threshold = int(tuning.get("ocr_miss_penalty_threshold", 10) or 10)
        self._penalty_confirm_delay_sec = float(tuning.get("penalty_confirm_delay_sec", 5.0) or 5.0)
        self._penalty_wait_after_confirm_sec = float(tuning.get("penalty_wait_sec", 180.0) or 180.0)
        self._last_avg_ok_ts: float = 0.0

        # 快速连击参数：优先使用 cfg.multi_snipe_tuning，其次回退旧 tasks_data.advanced
        try:
            adv_global = self.tasks_data.get("advanced") if isinstance(self.tasks_data.get("advanced"), dict) else {}
        except Exception:
            adv_global = {}
        fast_mode_raw = tuning.get("fast_chain_mode", (adv_global or {}).get("fast_chain_mode", True))
        fast_mode = bool(fast_mode_raw)
        try:
            fast_max = int(tuning.get("fast_chain_max", (adv_global or {}).get("fast_chain_max", 10)) or 10)
        except Exception:
            fast_max = 10
        try:
            fast_interval_ms = float(tuning.get("fast_chain_interval_ms", (adv_global or {}).get("fast_chain_interval_ms", 35.0)) or 35.0)
        except Exception:
            fast_interval_ms = 35.0
        # 写入 buyer 实例供内部读取
        try:
            setattr(self.buyer, "_fast_chain_mode", fast_mode)
            setattr(self.buyer, "_fast_chain_max", max(1, fast_max))
            # 确保间隔不少于 30ms
            setattr(self.buyer, "_fast_chain_interval_ms", max(30.0, fast_interval_ms))
        except Exception:
            pass

        # 模式/日志
        self.mode = str(self.tasks_data.get("task_mode", "time"))
        self.log_level: str = level_name(str(self.tasks_data.get("log_level", "info")))
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

    def _log_step(
        self,
        item: str,
        purchased: str,
        step_name: str,
        elapsed_ms: int,
        result: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._relay_log(_build_step_log(item, purchased, step_name, elapsed_ms, result, params))

    def _progress_text(self, purchased: int, target: int) -> str:
        return f"{int(purchased)}/{int(target)}"

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
        t0 = time.perf_counter()
        def _on(s: str) -> None:
            self._relay_log(f"【{now_label()}】【全局】【-】：{s}")

        res = run_launch_flow(self.cfg, on_log=_on)
        try:
            umi_cfg = self.cfg.get("umi_ocr") or {}
        except Exception:
            umi_cfg = {}
        params: Dict[str, Any] = {
            "step_delay_ms": int(float(self.buyer.timings.step_delay) * 1000.0),
            "ocr_timeout_ms": int(float(umi_cfg.get("timeout_sec", 2.5) or 2.5) * 1000.0),
            "code": str(getattr(res, "code", "") or "-"),
            "launch_mode": ("skip" if bool((res.details or {}).get("skipped")) else "launch"),
        }
        if not res.ok:
            self._relay_log(f"【{now_label()}】【全局】【-】：启动失败：{res.error or res.code}")
            params["error"] = str(res.error or res.code or "unknown")
            self._log_step(
                "全局",
                "-",
                STEP_1_NAME,
                int((time.perf_counter() - t0) * 1000.0),
                "fail",
                params,
            )
        else:
            self._log_step(
                "全局",
                "-",
                STEP_1_NAME,
                int((time.perf_counter() - t0) * 1000.0),
                "success",
                params,
            )
        return bool(res.ok)

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
                self.buyer.step3_clear_obstacles(item_disp, purchased_str)
            except Exception:
                pass
            # 尝试立即检测处罚提示，预缓存阶段也要处理
            try:
                warn_box = self.screen.locate("penalty_warning", timeout=0.3)
                if warn_box is not None:
                    self._check_and_handle_penalty()
                    # 处罚处理中，直接进入下一轮，不再叠加退避
                    backoff = max(1.0, backoff)
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
            progress_text = self._progress_text(purchased, target)
            self._log_step(
                item_disp,
                progress_text,
                STEP_2_NAME,
                0,
                "selected",
                {
                    "mode": "round",
                    "duration_min": int(duration_min),
                    "order": int(t.get("order", idx % n) or 0) + 1,
                    "target_total": int(target),
                    "purchased": int(purchased),
                },
            )
            self._relay_log(f"【{now_label()}】【{item_disp}】【{purchased}/{target}】：开始片段 {duration_min}min")
            try:
                self.buyer.clear_all_caches(goods.id)
                if not self.buyer.step4_build_search_context(goods, item_disp=item_disp, purchased_str=progress_text):
                    self._relay_log(f"【{now_label()}】【{item_disp}】【{purchased}/{target}】：建立搜索上下文失败，跳过片段")
                    idx += 1
                    continue
                if not self._precache_with_retries(goods, item_disp, progress_text):
                    self._relay_log(f"【{now_label()}】【{item_disp}】【{purchased}/{target}】：预缓存失败，跳过片段")
                    idx += 1
                    continue
            except FatalOcrError as e:
                self._relay_log(f"【{now_label()}】【全局】【-】：Umi OCR 失败（片段初始化）：{e}")
                self._stop.set()
                break
            seg_paused_sec = 0.0
            loop_exit_logged = False
            while not self._stop.is_set() and time.time() < seg_end:
                if self._stop.is_set():
                    break
                cycle_tp0 = time.perf_counter()
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
                cycle_meta = self.buyer.get_last_cycle_meta()
                session_elapsed_ms = int((time.time() - seg_start - seg_paused_sec) * 1000.0)
                self._log_step(
                    item_disp,
                    self._progress_text(purchased, target),
                    STEP_8_NAME,
                    int((time.perf_counter() - cycle_tp0) * 1000.0),
                    ("next_cycle" if cont else "break"),
                    {
                        "continue": bool(cont),
                        "reason": str(cycle_meta.get("reason", "cycle_return") or "cycle_return"),
                        "bought": int(got),
                        "ocr_miss_streak": int(self._ocr_miss_streak),
                        "executed_ms": max(0, session_elapsed_ms),
                    },
                )
                if not cont:
                    loop_exit_logged = True
                    break
                safe_sleep(0.02)
            elapsed = int((time.time() - seg_start - seg_paused_sec) * 1000)
            t["executed_ms"] = int(t.get("executed_ms", 0) or 0) + max(0, elapsed)
            t["status"] = "idle"
            if not loop_exit_logged:
                self._log_step(
                    item_disp,
                    self._progress_text(purchased, target),
                    STEP_8_NAME,
                    0,
                    "break",
                    {
                        "continue": False,
                        "reason": ("stop_requested" if self._stop.is_set() else "segment_timeout"),
                        "ocr_miss_streak": int(self._ocr_miss_streak),
                        "executed_ms": max(0, elapsed),
                    },
                )
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

        blocked_in_current_window: set[int] = set()

        while not self._stop.is_set():
            if self._stop.is_set():
                break
            now = time.time()
            for idx in list(blocked_in_current_window):
                if idx < 0 or idx >= len(tasks):
                    blocked_in_current_window.discard(idx)
                    continue
                task = tasks[idx]
                if not _time_in_window(now, str(task.get("time_start", "")), str(task.get("time_end", ""))):
                    blocked_in_current_window.discard(idx)
            chosen_idx = None
            for i, t in enumerate(tasks):
                if not bool(t.get("enabled", True)):
                    continue
                if not bool(t.get("_valid", True)):
                    continue
                target = int(t.get("target_total", 0) or 0)
                purchased = int(t.get("purchased", 0) or 0)
                if target > 0 and purchased >= target:
                    continue
                if i in blocked_in_current_window:
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
            progress_text = self._progress_text(purchased, target)
            self._log_step(
                item_disp,
                progress_text,
                STEP_2_NAME,
                0,
                "selected",
                {
                    "mode": "time",
                    "window": f"{str(t.get('time_start', '') or '-')}-{str(t.get('time_end', '') or '-')}",
                    "target_total": int(target),
                    "purchased": int(purchased),
                },
            )
            self._relay_log(f"【{now_label()}】【{item_disp}】【{purchased}/{target}】：进入时间窗口")
            try:
                self.buyer.clear_all_caches(goods.id)
                if not self.buyer.step4_build_search_context(goods, item_disp=item_disp, purchased_str=progress_text):
                    safe_sleep(1.0)
                    continue
                if not self._precache_with_retries(goods, item_disp, progress_text):
                    self._relay_log(f"【{now_label()}】【{item_disp}】【{purchased}/{target}】：预缓存失败，终止本次任务（时间窗口）")
                    blocked_in_current_window.add(chosen_idx)
                    safe_sleep(1.0)
                    continue
            except FatalOcrError as e:
                self._relay_log(f"【{now_label()}】【全局】【-】：Umi OCR 失败（进入窗口）：{e}")
                self._stop.set()
                break
            window_start = time.time()
            window_paused_sec = 0.0
            loop_exit_logged = False
            while not self._stop.is_set():
                if not _time_in_window(time.time(), str(t.get("time_start", "")), str(t.get("time_end", ""))):
                    break
                if self._stop.is_set():
                    break
                cycle_tp0 = time.perf_counter()
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
                session_elapsed_ms = int((time.time() - window_start - window_paused_sec) * 1000.0)
                cycle_meta = self.buyer.get_last_cycle_meta()
                self._log_step(
                    item_disp,
                    self._progress_text(purchased, target),
                    STEP_8_NAME,
                    int((time.perf_counter() - cycle_tp0) * 1000.0),
                    ("next_cycle" if cont else "break"),
                    {
                        "continue": bool(cont),
                        "reason": str(cycle_meta.get("reason", "cycle_return") or "cycle_return"),
                        "bought": int(got),
                        "ocr_miss_streak": int(self._ocr_miss_streak),
                        "executed_ms": max(0, session_elapsed_ms),
                    },
                )
                if not cont:
                    loop_exit_logged = True
                    break
                safe_sleep(0.02)
            if not loop_exit_logged:
                final_elapsed = int((time.time() - window_start - window_paused_sec) * 1000.0)
                self._log_step(
                    item_disp,
                    self._progress_text(purchased, target),
                    STEP_8_NAME,
                    0,
                    "break",
                    {
                        "continue": False,
                        "reason": ("stop_requested" if self._stop.is_set() else "window_closed"),
                        "ocr_miss_streak": int(self._ocr_miss_streak),
                        "executed_ms": max(0, final_elapsed),
                    },
                )
            self._relay_log(f"【{now_label()}】【{item_disp}】【{purchased}/{target}】：退出时间窗口")

    # -------------------- 处罚处理 --------------------
    def _check_and_handle_penalty(self) -> None:
        t0 = time.perf_counter()
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
            self._log_step(
                "全局",
                "-",
                STEP_8_NAME,
                int((time.perf_counter() - t0) * 1000.0),
                "next_cycle",
                {
                    "continue": True,
                    "reason": "penalty_warning_not_found",
                    "ocr_miss_streak": int(self._ocr_miss_streak),
                },
            )
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
            self._log_step(
                "全局",
                "-",
                STEP_8_NAME,
                int((time.perf_counter() - t0) * 1000.0),
                "next_cycle",
                {
                    "continue": True,
                    "reason": "penalty_handled",
                    "ocr_miss_streak": int(self._ocr_miss_streak),
                    "wait_sec": float(getattr(self, "_penalty_wait_after_confirm_sec", 180.0)),
                },
            )
        else:
            self._relay_log(f"【{now_label()}】【全局】【-】：未定位到处罚确认按钮，跳过点击。")
            self._log_step(
                "全局",
                "-",
                STEP_8_NAME,
                int((time.perf_counter() - t0) * 1000.0),
                "next_cycle",
                {
                    "continue": True,
                    "reason": "penalty_confirm_missing",
                    "ocr_miss_streak": int(self._ocr_miss_streak),
                },
            )


__all__ = [
    "Timings",
    "SinglePurchaseBuyerV2",
    "SinglePurchaseTaskRunnerV2",
]
