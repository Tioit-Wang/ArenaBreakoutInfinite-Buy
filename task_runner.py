"""任务运行器（task_runner.py）

基于 purchase_flow.md 的严格实现与模块化重构。

核心流程（与文档一致）：
1. 统一启动流程（首页/市场标识即视为已启动；否则按规程启动，缺配置即失败停止）。
2. 读取并校验任务；选择执行模式（轮询/时间窗口）。
3. 购买流程分两部分：
   - 模块一：进入搜索结果页面（优先处理阻碍性事件；按“首页/市场标识”分支执行并缓存商品坐标）。
   - 模块二：购买循环（首次进入详情缓存按钮；读价→判断补货/普通→提交→结果处理→在同一详情内重复，直至价格不合适后关闭）。
4. 周期性软重启：严格按照步进与等待时长执行；顺序执行模式下暂停片段计时，重启耗时不计入片段时长；重启后重建搜索上下文。
5. 日志等级过滤与旧文案等级补齐；缓存与恢复策略按文档约定实现。
"""
from __future__ import annotations

import json
import os
import threading
import time
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

# 可选依赖按需延迟导入（使用到时再导入）

try:
    # 兼容性：即使缺少 OpenCV 也确保 PyAutoGUI 的 confidence 参数可用
    from compat import ensure_pyautogui_confidence_compat  # type: ignore

    ensure_pyautogui_confidence_compat()
except Exception:
    pass

from app_config import load_config  # type: ignore
from utils.ocr_utils import recognize_numbers, recognize_text  # type: ignore


class FatalOcrError(RuntimeError):
    """当 OCR 引擎（Umi）出现致命错误时抛出，用于指示任务应终止。"""

    pass


# ------------------------------ 日志等级 ------------------------------

LOG_LEVELS: Dict[str, int] = {"debug": 10, "info": 20, "error": 40}

def _level_name(lv: str) -> str:
    s = str(lv or "").lower()
    return s if s in LOG_LEVELS else "info"

def _extract_level_from_msg(msg: str) -> str:
    try:
        if "【ERROR】" in msg:
            return "error"
        if "【DEBUG】" in msg:
            return "debug"
        if "【INFO】" in msg:
            return "info"
        # 关键字启发：失败/错误/超时/未找到/缺少 → error
        kw_err = ("失败", "错误", "超时", "未找到", "缺少")
        if any(k in msg for k in kw_err):
            return "error"
        # 关键字启发：耗时/匹配/打开/cost/ms → debug
        kw_dbg = ("耗时", "匹配", "打开", "cost=", "match=", "open=", "ms")
        if any(k in msg for k in kw_dbg):
            return "debug"
    except Exception:
        pass
    return "info"

def _ensure_level_tag(msg: str, level: str) -> str:
    """若消息中缺少等级标签，则在第一个】后插入【LEVEL】。"""
    if "【DEBUG】" in msg or "【INFO】" in msg or "【ERROR】" in msg:
        return msg
    try:
        i = msg.find("】")
        if i >= 0:
            return msg[: i + 1] + f"【{level.upper()}】" + msg[i + 1 :]
    except Exception:
        pass
    # 回退：前置一个时间与等级
    return f"【{_now_label()}】【{level.upper()}】" + msg


# ------------------------------ 工具函数 ------------------------------


def _now_label() -> str:
    return time.strftime("%H:%M:%S")


def _safe_int(s: str, default: int = -1) -> int:
    try:
        return int(s)
    except Exception:
        return default


def _parse_price_text(txt: str) -> Optional[int]:
    """将 OCR 文本解析为整数价格。

    支持纯数字与可选后缀 K/M（如 2.1K -> 2100）。
    解析失败返回 None。
    """
    if not txt:
        return None
    s = txt.strip().upper().replace(",", "").replace(" ", "")
    # 仅保留数字、小数点与后缀 K/M
    import re

    m = re.search(r"([0-9]+(?:\.[0-9]+)?)([MK])?", s)
    if not m:
        # 尝试提取连续数字作为兜底
        d = "".join(ch for ch in s if ch.isdigit())
        if d:
            try:
                return int(d)
            except Exception:
                return None
        return None
    num_s = m.group(1)
    suffix = m.group(2)
    try:
        if "." in num_s:
            val = float(num_s)
        else:
            val = int(num_s)
    except Exception:
        return None
    if suffix == "K":
        val = float(val) * 1000.0
    elif suffix == "M":
        val = float(val) * 1_000_000.0
    try:
        return int(round(val))
    except Exception:
        return None


def _sleep(s: float) -> None:
    """统一的安全休眠封装。

    用途：
    - 给 UI/动画/输入法 留出“沉淀时间”，避免连贯操作导致误判；
    - 控制轮询频率，降低 CPU 占用，避免忙等；
    - 规避负数/类型异常带来的随机错误。

    调参建议：
    - ≤0.03s：点击/键入后的微等待；
    - 0.2~0.8s：轮询/暂停的节奏；
    - ≥5s：页面/进程级切换（如重启/加载）。
    """
    try:
        time.sleep(max(0.0, float(s)))
    except Exception:
        # 休眠失败不致命：忽略异常以保证主流程连续
        pass


# ------------------------------ 图像/点击辅助 ------------------------------


class ScreenOps:
    """基于 pyautogui 与 OpenCV 模板匹配的轻量封装。

    提供运行器所需的最小能力：查找/点击/截图。
    """

    def __init__(self, cfg: Dict[str, Any], step_delay: float = 0.01) -> None:
        self.cfg = cfg
        self.step_delay = float(step_delay or 0.01)

        try:  # 导入阶段可选引入 pyautogui（缺失时给出友好提示）
            import pyautogui  # type: ignore

            # 若不支持 confidence 参数则尽早失败
            _ = getattr(pyautogui, "locateOnScreen")
        except Exception as e:  # pragma: no cover - runtime guard
            raise RuntimeError(
                "缺少 pyautogui 或其依赖，请安装 pyautogui + opencv-python。"
            ) from e

    # 惰性属性，避免重复 getattr 开销
    @property
    def _pg(self):  # type: ignore
        import pyautogui  # type: ignore

        return pyautogui

    def _tpl(self, key: str) -> Tuple[str, float]:
        t = (self.cfg.get("templates", {}) or {}).get(key) or {}
        path = str(t.get("path", ""))
        conf = float(t.get("confidence", 0.85) or 0.85)
        return path, conf

    def locate(
        self,
        tpl_key: str,
        region: Optional[Tuple[int, int, int, int]] = None,
        timeout: float = 0.0,
    ) -> Optional[Tuple[int, int, int, int]]:
        """返回 (left, top, width, height)，找不到返回 None。"""
        path, conf = self._tpl(tpl_key)
        if not path or not os.path.exists(path):
            return None
        end = time.time() + max(0.0, float(timeout or 0.0))
        while True:
            try:
                box = self._pg.locateOnScreen(path, confidence=conf, region=region)
                if box is not None:
                    # pyautogui.Box -> tuple 元组
                    return (
                        int(box.left),
                        int(box.top),
                        int(box.width),
                        int(box.height),
                    )
            except Exception:
                pass
            if time.time() >= end:
                return None
            # 小步轮询：让屏幕刷新/模板匹配有时间完成，避免 CPU 忙等
            _sleep(self.step_delay)

    def click_center(
        self, box: Tuple[int, int, int, int], clicks: int = 1, interval: float = 0.02
    ) -> None:
        l, t, w, h = box
        x = int(l + w / 2)
        y = int(t + h / 2)
        try:
            self._pg.moveTo(x, y)
            for i in range(max(1, int(clicks))):
                self._pg.click(x, y)
                if i + 1 < clicks:
                    # 多击之间的间隔：避免系统将多次点击合并或丢失
                    _sleep(interval)
        except Exception:
            pass
        # 点击后的小步沉淀：等待焦点/状态变化生效
        _sleep(self.step_delay)

    def type_text(self, s: str, clear_first: bool = True) -> None:
        try:
            if clear_first:
                # 全选后删除（Ctrl+A -> Backspace）
                self._pg.hotkey("ctrl", "a")
                # 让选择态更新到位
                _sleep(0.02)
                self._pg.press("backspace")
                # 给删除动作一点处理时间
                _sleep(0.02)
            # 逐字输入，使用 step_delay 防止过快导致漏键
            self._pg.typewrite(str(s), interval=max(0.0, self.step_delay))
        except Exception:
            pass
        # 输入结束后的沉淀：等待文本渲染/光标稳定
        _sleep(self.step_delay)

    def screenshot_region(self, region: Tuple[int, int, int, int]):
        l, t, w, h = region
        try:
            img = self._pg.screenshot(region=(int(l), int(t), int(w), int(h)))
            return img
        except Exception:
            return None


# ------------------------------ 购买逻辑 ------------------------------


@dataclass
class Goods:
    id: str
    name: str
    search_name: str
    image_path: str
    big_category: str
    sub_category: str = ""
    exchangeable: bool = False


# ------------------------------ 统一启动流程 ------------------------------


@dataclass
class LaunchResult:
    ok: bool
    code: str
    error: Optional[str] = None
    details: Optional[Dict[str, Any]] = None


def run_launch_flow(
    cfg: Dict[str, Any],
    *,
    on_log: Optional[Callable[[str], None]] = None,
    click_delay_override: Optional[float] = None,
) -> LaunchResult:
    """统一启动流程（预览与任务共用）。

    步骤：
    0）快速路径：若屏幕已存在首页/市场标识，则跳过启动；
    1）配置与模板校验：exe_path、templates.home_indicator、templates.btn_launch；
    2）启动启动器进程；
    3）等待出现 'btn_launch'（launcher_timeout_sec）；
    4）按延迟（launch_click_delay_sec）后点击一次；
    5）等待进入首页/市场（startup_timeout_sec）。
    """

    def _log(s: str) -> None:
        try:
            if on_log:
                on_log(s)
        except Exception:
            pass

    g = cfg.get("game") or {}
    exe = str(g.get("exe_path", "")).strip()
    args = str(g.get("launch_args", "")).strip()
    try:
        launcher_to = float(g.get("launcher_timeout_sec", 60) or 60)
    except Exception:
        launcher_to = 60.0
    try:
        click_delay = float(click_delay_override if click_delay_override is not None else (g.get("launch_click_delay_sec", 20) or 20))
    except Exception:
        click_delay = 20.0
    try:
        startup_to = float(g.get("startup_timeout_sec", 180) or 180)
    except Exception:
        startup_to = 180.0

    # 模板与路径校验
    tpls = (cfg.get("templates", {}) or {})
    def _tpl_path(key: str) -> str:
        t = tpls.get(key) or {}
        return str(t.get("path", "")).strip()
    home_key = "home_indicator"
    home_path = _tpl_path(home_key)
    market_key = "market_indicator"
    market_path = _tpl_path(market_key)
    launch_path = _tpl_path("btn_launch")

    # 提前创建 ScreenOps 以支持快速路径检测
    screen = ScreenOps(cfg, step_delay=0.02)

    # 0）快速路径：检测到首页或市场即视为已启动
    try:
        if (home_path and os.path.exists(home_path) and screen.locate(home_key, timeout=0.4) is not None) or \
           (market_path and os.path.exists(market_path) and screen.locate(market_key, timeout=0.4) is not None):
            _log("[启动流程] 已检测到首页/市场标识，跳过启动。")
            return LaunchResult(True, code="ok", details={"skipped": True, "reason": "home_or_market_present"})
    except Exception:
        pass

    # 1）严格校验：启动器路径必须配置且存在
    if not exe:
        return LaunchResult(False, code="missing_config", error="未配置启动器路径")
    if not os.path.exists(exe):
        return LaunchResult(False, code="exe_missing", error="启动器路径不存在")
    # 监听“启动”按钮模板必须存在
    if not (launch_path and os.path.exists(launch_path)):
        return LaunchResult(False, code="missing_launch_template", error="未配置或找不到“启动”按钮模板文件")
    # 至少应提供一个首页/市场标识模板，用于进入完成判定
    if not ((home_path and os.path.exists(home_path)) or (market_path and os.path.exists(market_path))):
        return LaunchResult(False, code="missing_indicator_template", error="未配置首页/市场标识模板，无法判定启动完成")

    # 创建 ScreenOps 实例
    screen = ScreenOps(cfg, step_delay=0.02)

    # 启动启动器进程
    try:
        wd = os.path.dirname(exe)
        cmd: List[str]
        if args:
            try:
                import shlex as _shlex

                cmd = [exe] + _shlex.split(args, posix=False)
            except Exception:
                cmd = [exe] + args.split()
        else:
            cmd = [exe]
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        subprocess.Popen(cmd, cwd=wd or None, creationflags=creationflags)  # noqa: S603,S607
        _log(f"[启动流程] 已执行: {exe} {' '+args if args else ''}")
    except Exception as e:
        return LaunchResult(False, code="launch_error", error=str(e))

    # 等待出现 'btn_launch'
    end_launch = time.time() + max(1.0, float(launcher_to))
    launch_box: Optional[Tuple[int, int, int, int]] = None
    while time.time() < end_launch:
        box = screen.locate("btn_launch", timeout=0.2)
        if box is not None:
            launch_box = box
            break
        # 以 200ms 节奏轮询，避免忙等并兼顾响应
        _sleep(0.2)
    if launch_box is None:
        return LaunchResult(False, code="launch_button_timeout", error="等待启动按钮超时")

    # 等待点击延迟后执行点击
    if float(click_delay) > 0:
        t_end_click = time.time() + float(click_delay)
        while time.time() < t_end_click:
            # 按文档延迟点击“启动”，等待资源准备就绪
            _sleep(0.2)
    screen.click_center(launch_box)
    _log("[启动流程] 已点击启动按钮")

    # 等待出现 'home_indicator'（或市场标识）
    end_home = time.time() + max(1.0, float(startup_to))
    while time.time() < end_home:
        if (screen.locate(home_key, timeout=0.3) is not None) or (screen.locate(market_key, timeout=0.3) is not None):
            return LaunchResult(True, code="ok")
        # 进入游戏加载较慢：300ms 轮询，减少 CPU 占用
        _sleep(0.3)
    return LaunchResult(False, code="home_timeout", error="等待首页标识超时")


class Buyer:
    """单个商品的统一购买流程（严格对齐 purchase_flow.md）。

    模块职责：
    - 模块一：进入搜索结果页（处理阻碍性事件→按首页/市场标识分支→搜索→匹配并缓存商品坐标）。
    - 模块二：购买循环（首次缓存按钮→在详情内读价与购买→价格不合适后关闭）。
    - 失败与恢复：按文档进行恢复性搜索与本轮结束处理。
    """

    def __init__(
        self, cfg: Dict[str, Any], screen: ScreenOps, on_log: Callable[[str], None]
    ) -> None:
        self.cfg = cfg
        self.screen = screen
        self.on_log = on_log
        # 商品列表项坐标缓存（临时坐标缓存）
        self._pos_cache: Dict[str, Tuple[int, int, int, int]] = {}
        # 详情按钮首次缓存（跨详情会话复用）
        self._first_detail_cached: Dict[str, bool] = {}
        self._first_detail_buttons: Dict[str, Dict[str, Tuple[int, int, int, int]]] = {}

        # 当前详情页按钮缓存（本次进入详情周期内有效）
        self._detail_ui_cache: Dict[str, Tuple[int, int, int, int]] = {}

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
        _sleep(0.01)

    def _dismiss_success_overlay(self) -> None:
        # 将鼠标移至安全区并任意点击以关闭“成功遮罩”
        try:
            self._move_cursor_top_right()
            self._click_center_screen_once()
            self._move_cursor_top_right()
        except Exception:
            self._click_center_screen_once()

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
            _sleep(0.02)

    # ------------------------------ 价格读取 ------------------------------

    def _read_avg_unit_price(
        self,
        goods: Goods,
        item_disp: str,
        purchased_str: str,
        *,
        expected_floor: Optional[int] = None,
    ) -> Optional[int]:
        # 以购买按钮为锚点（与“平均单价预览”一致）
        t_btn = time.perf_counter()
        # 优先使用缓存的购买按钮；失败则短时匹配
        buy_box = self._detail_ui_cache.get("btn_buy") or self.screen.locate(
            "btn_buy", timeout=0.3
        )
        btn_ms = int((time.perf_counter() - t_btn) * 1000.0)
        if buy_box is None:
            self._log(
                item_disp,
                purchased_str,
                f"未匹配到“购买”按钮模板，无法定位平均价格区域 匹配耗时={btn_ms}ms",
            )
            return None
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
            return None
        roi = (x_left, y_top, width, height)
        img = self.screen.screenshot_region(roi)
        if img is None:
            self._log(item_disp, purchased_str, "平均单价 ROI 截屏失败")
            return None
        # 横向分割 ROI：上=平均价，下=合计
        try:
            w0, h0 = img.size
        except Exception:
            self._log(item_disp, purchased_str, "平均单价 ROI 尺寸无效")
            return None
        if h0 < 2:
            self._log(item_disp, purchased_str, "平均单价 ROI 高度过小，无法二分")
            return None
        mid_h = h0 // 2
        img_top = img.crop((0, 0, w0, mid_h))
        img_bot = img.crop((0, mid_h, w0, h0))
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
        except Exception:
            val = None
        if isinstance(val, int) and val > 0:
            try:
                floor = int(expected_floor or 0)
            except Exception:
                floor = 0
            if floor > 0 and int(val) < max(1, floor // 2):
                return None
            self._log(
                item_disp,
                purchased_str,
                f"平均价 OCR 成功 值={val} ROI=({x_left},{y_top},{x_left + width},{y_top + mid_h}) 缩放={sc:.2f} btn耗时={btn_ms}ms",
            )
            try:
                from history_store import append_price  # type: ignore
                append_price(
                    item_id=goods.id,
                    item_name=goods.name or goods.search_name or str(item_disp),
                    price=int(val),
                    category=(goods.big_category or "") or None,
                )
            except Exception:
                pass
            return int(val)
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
        val = _parse_price_text(txt or "")
        if val is None or val <= 0:
            self._log(
                item_disp,
                purchased_str,
                f"平均价 OCR 解析失败：'{(txt or '').strip()[:64]}' | ROI=({x_left},{y_top},{x_left + width},{y_top + mid_h}) 缩放={sc:.2f} btn耗时={btn_ms}ms OCR耗时={ocr_ms if ocr_ms >= 0 else '-'}ms",
            )
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
            return None
        self._log(
            item_disp,
            purchased_str,
            f"平均价 OCR 成功 值={val} ROI=({x_left},{y_top},{x_left + width},{y_top + mid_h}) 缩放={sc:.2f} btn耗时={btn_ms}ms OCR耗时={ocr_ms}ms",
        )
        # 记录价格历史（按物品），供历史价格与分钟聚合使用
        try:
            from history_store import append_price  # type: ignore
            append_price(
                item_id=goods.id,
                item_name=goods.name or goods.search_name or str(item_disp),
                price=int(val),
                category=(goods.big_category or "") or None,
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

            used_max = False
            if ok_restock and (goods.big_category or "").strip() == "弹药":
                mx = self._first_detail_buttons.get(goods.id, {}).get("btn_max") or self.screen.locate("btn_max", timeout=0.3)
                if mx is not None:
                    self.screen.click_center(mx)
                    _sleep(0.02)
                    used_max = True

            b = self._first_detail_buttons.get(goods.id, {}).get("btn_buy") or self.screen.locate("btn_buy", timeout=0.4)
            if b is None:
                # 未找到“购买”按钮：关闭详情（缓存优先）
                _ = self._close_detail(goods, timeout=0.3)
                self._log(item_disp, purchased_str, "未找到“购买”按钮，已关闭详情")
                return bought, True
            self.screen.click_center(b)

            t_end = time.time() + 0.5
            got_ok = False
            found_fail = False
            while time.time() < t_end:
                if self.screen.locate("buy_ok", timeout=0.0) is not None:
                    got_ok = True
                    break
                if self.screen.locate("buy_fail", timeout=0.0) is not None:
                    found_fail = True
                _sleep(0.02)

            if got_ok:
                # 根据商品类别与是否使用 Max 调整进度增量
                try:
                    is_ammo = (goods.big_category or "").strip() == "弹药"
                except Exception:
                    is_ammo = False
                if is_ammo:
                    inc = 120 if used_max else 10
                else:
                    inc = 5 if used_max else 1
                bought += int(inc)
                # 记录购买历史（关联任务与物品）
                try:
                    from history_store import append_purchase  # type: ignore
                    append_purchase(
                        item_id=goods.id,
                        item_name=goods.name or goods.search_name or str(task.get("item_name", "")),
                        price=int(unit_price or 0),
                        qty=int(inc),
                        task_id=str(task.get("id", "")) if task.get("id") else None,
                        task_name=str(task.get("item_name", "")) or None,
                        category=(goods.big_category or "") or None,
                        used_max=bool(used_max),
                    )
                except Exception:
                    pass
                self._dismiss_success_overlay()
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
        on_log: Optional[Callable[[str], None]] = None,
        on_task_update: Optional[Callable[[int, Dict[str, Any]], None]] = None,
    ) -> None:
        self.on_log = on_log or (lambda s: None)
        self.on_task_update = on_task_update or (lambda i, it: None)
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # 配置快照
        self.cfg = load_config(cfg_path)
        self.tasks_data = json.loads(json.dumps(tasks_data or {"tasks": []}))
        self.goods_map: Dict[str, Goods] = self._load_goods(goods_path)

        # 派生辅助对象
        step_delay = float(
            ((self.tasks_data.get("step_delays") or {}).get("default", 0.01)) or 0.01
        )
        self.screen = ScreenOps(self.cfg, step_delay=step_delay)
        self.buyer = Buyer(self.cfg, self.screen, self._relay_log)

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
        if isinstance(arr, list):
            for e in arr:
                if not isinstance(e, dict):
                    continue
                gid = str(e.get("id", ""))
                if not gid:
                    continue
                goods_map[gid] = Goods(
                    id=gid,
                    name=str(e.get("name", "")),
                    search_name=str(e.get("search_name", "")),
                    image_path=str(e.get("image_path", "")),
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


__all__ = [
    "TaskRunner",
    "run_launch_flow",
]
