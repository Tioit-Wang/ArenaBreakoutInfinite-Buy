"""任务运行器（task_runner.py）

执行流程概要：
1. 初始化：读取配置与商品数据，构建 ScreenOps 与 Buyer。
2. 启动就绪：通过 run_launch_flow 统一启动/就绪（含快速路径与模板校验）。
3. 主循环：按模式（轮询/时间窗口）选择任务，管理暂停/继续/终止与软重启。
4. 单次购买：Buyer.execute_once 负责搜索→打开详情→OCR 价格→阈值判断→提交与结果处理。
5. 任务进度：累计 purchased/executed_ms，通过回调 on_task_update 更新 UI。
6. 软重启：定周期尝试退出/回到市场并重建搜索上下文。
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

from app_config import load_config, save_config  # type: ignore
from path_finder import search_wegame_launchers
from ocr_reader import read_text  # type: ignore


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
    try:
        time.sleep(max(0.0, float(s)))
    except Exception:
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
            _sleep(self.step_delay)

    def click_center(
        self, box: Tuple[int, int, int, int], clicks: int = 1, interval: float = 0.05
    ) -> None:
        l, t, w, h = box
        x = int(l + w / 2)
        y = int(t + h / 2)
        try:
            self._pg.moveTo(x, y)
            for i in range(max(1, int(clicks))):
                self._pg.click(x, y)
                if i + 1 < clicks:
                    _sleep(interval)
        except Exception:
            pass
        _sleep(self.step_delay)

    def type_text(self, s: str, clear_first: bool = True) -> None:
        try:
            if clear_first:
                # 全选后删除（Ctrl+A -> Backspace）
                self._pg.hotkey("ctrl", "a")
                _sleep(0.02)
                self._pg.press("backspace")
                _sleep(0.02)
            self._pg.typewrite(str(s), interval=max(0.0, self.step_delay))
        except Exception:
            pass
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
    launch_path = _tpl_path("btn_launch")
    market_key = "market_indicator"
    market_path = _tpl_path(market_key)
    # 若仅配置了市场模板，则将其别名为首页模板以兼容旧校验
    try:
        if (not home_path or not os.path.exists(home_path)) and (market_path and os.path.exists(market_path)):
            home_path = market_path
    except Exception:
        pass
    market_key = "market_indicator"
    market_path = _tpl_path(market_key)

    # 提前创建 ScreenOps 以支持快速路径检测
    screen = ScreenOps(cfg, step_delay=0.05)

    # 0a) 扩展快速路径：检测到首页或市场即视为已启动
    try:
        if (home_path and os.path.exists(home_path)) or (market_path and os.path.exists(market_path)):
            if (home_path and os.path.exists(home_path) and screen.locate(home_key, timeout=0.4) is not None) or \
               (market_path and os.path.exists(market_path) and screen.locate(market_key, timeout=0.4) is not None):
                _log("[启动流程] 已检测到首页/市场标识，跳过启动。")
                return LaunchResult(True, code="ok", details={"skipped": True, "reason": "home_or_market_present"})
    except Exception:
        pass

    # 0) 快速路径：若首页标识可见则跳过启动
    try:
        if home_path and os.path.exists(home_path):
            if screen.locate(home_key, timeout=0.8) is not None:
                _log("[启动流程] 已检测到首页标识，跳过启动。")
                return LaunchResult(True, code="ok", details={"skipped": True, "reason": "home_present"})
    except Exception:
        # 忽略快速路径错误，继续执行常规校验/启动
        pass

    # 自动发现：在未配置或路径无效时尝试搜索启动器
    if not exe or not os.path.exists(exe):
        try:
            found = search_wegame_launchers()
        except Exception:
            found = []
        if found:
            exe = found[0]
            try:
                cfg.setdefault("game", {})["exe_path"] = exe
                # 尽力持久化到 config.json（忽略错误）
                save_config(cfg, "config.json")
            except Exception:
                pass
            _log(f"[启动流程] 未配置或路径无效，已自动搜索并使用：{exe}")
        else:
            if not exe:
                return LaunchResult(False, code="missing_config", error="未配置启动器路径")
            return LaunchResult(False, code="exe_missing", error="启动器路径不存在")
    if not launch_path or not os.path.exists(launch_path):
        return LaunchResult(False, code="missing_launch_template", error="未配置或找不到“启动”按钮模板文件")
    if not home_path or not os.path.exists(home_path):
        return LaunchResult(False, code="missing_home_template", error="未配置或找不到“首页”标识模板文件")

    # 创建 ScreenOps 实例
    screen = ScreenOps(cfg, step_delay=0.05)

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
        _sleep(0.2)
    if launch_box is None:
        return LaunchResult(False, code="launch_button_timeout", error="等待启动按钮超时")

    # 等待点击延迟后执行点击
    if float(click_delay) > 0:
        t_end_click = time.time() + float(click_delay)
        while time.time() < t_end_click:
            _sleep(0.2)
    screen.click_center(launch_box)
    _log("[启动流程] 已点击启动按钮")

    # 等待出现 'home_indicator'（或市场标识）
    end_home = time.time() + max(1.0, float(startup_to))
    while time.time() < end_home:
        if (screen.locate(home_key, timeout=0.3) is not None) or (screen.locate(market_key, timeout=0.3) is not None):
            return LaunchResult(True, code="ok")
        _sleep(0.3)
    return LaunchResult(False, code="home_timeout", error="等待首页标识超时")


class Buyer:
    """单个商品的统一购买流程。

    封装导航/搜索/进入详情/读取价格（OCR）/阈值判断/提交等步骤。
    """

    def __init__(
        self, cfg: Dict[str, Any], screen: ScreenOps, on_log: Callable[[str], None]
    ) -> None:
        self.cfg = cfg
        self.screen = screen
        self.on_log = on_log
        # 每个 goods.id 对应的列表项位置缓存，避免重复模板匹配
        self._pos_cache: Dict[str, Tuple[int, int, int, int]] = {}
        # 记录连续打开失败次数，用于触发恢复策略
        self._open_fail_counts: Dict[str, int] = {}

        ca = cfg.get("currency_area") or {}
        self._cur_tpl = str(ca.get("template", ""))
        self._cur_thr = float(ca.get("threshold", 0.8) or 0.8)
        self._cur_width = int(ca.get("price_width", 180) or 180)
        self._cur_engine = str(
            ca.get("ocr_engine")
            or cfg.get("avg_price_area", {}).get("ocr_engine")
            or "umi"
        )
        self._cur_scale = float(ca.get("scale", 1.0) or 1.0)

        umi = cfg.get("umi_ocr") or {}
        self._umi_base = str(umi.get("base_url", "http://127.0.0.1:1224"))
        self._umi_timeout = float(umi.get("timeout_sec", 5.0) or 5.0)
        self._umi_options = dict((umi.get("options") or {}))
        # 已打开详情页内的按钮缓存
        # 键：'btn_buy'、'btn_close'、'btn_max'
        self._detail_ui_cache: Dict[str, Tuple[int, int, int, int]] = {}

    # ------------------------------ 辅助方法 ------------------------------
    def _log(self, item_disp: str, purchased: str, msg: str) -> None:
        self.on_log(f"【{_now_label()}】【{item_disp}】【{purchased}】：{msg}")

    def _move_cursor_top_right(self) -> None:
        """将鼠标移动到右上角安全区域，避免遮挡 UI。"""
        try:
            pg = self.screen._pg  # type: ignore[attr-defined]
            sw, sh = pg.size()
            x = max(0, int(sw) - 5)
            y = max(0, 5)
            try:
                pg.moveTo(x, y)
            except Exception:
                try:
                    pg.moveRel(10, -10)
                except Exception:
                    pass
        except Exception:
            pass

    def _click_center_screen_once(self) -> None:
        """尽力在屏幕中心点击一次。"""
        try:
            pg = self.screen._pg  # type: ignore[attr-defined]
            sw, sh = pg.size()
            pg.click(int(sw // 2), int(sh // 2))
        except Exception:
            pass
        _sleep(0.02)

    def _dismiss_success_overlay(self) -> None:
        """关闭成功弹层：移开 → 中心点击 → 再移开。"""
        try:
            self._move_cursor_top_right()
            self._click_center_screen_once()
            self._move_cursor_top_right()
        except Exception:
            # 备用兜底
            self._click_center_screen_once()

    def _ensure_ready(self, startup_timeout_sec: float) -> bool:
        # 若市场按钮可见 → 视为已就绪
        if self.screen.locate("btn_market", timeout=0.2) is not None:
            return True
        # 若配置了启动器则尝试启动游戏
        g = self.cfg.get("game") or {}
        exe = str(g.get("exe_path", "")).strip()
        args = str(g.get("launch_args", "")).strip()
        if exe:
            try:
                wd = os.path.dirname(exe)
                if os.path.exists(exe):
                    if args:
                        subprocess.Popen([exe, *args.split()], cwd=wd or None)
                    else:
                        subprocess.Popen([exe], cwd=wd or None)
            except Exception:
                pass
        end = time.time() + float(max(10.0, startup_timeout_sec))
        self.on_log(
            f"【{_now_label()}】【全局】【-】：开始启动，超时点 {time.strftime('%H:%M:%S', time.localtime(end))}"
        )
        while time.time() < end:
            # 若出现启动按钮则点击
            box = self.screen.locate("btn_launch", timeout=0.0)
            if box is not None:
                self.screen.click_center(box)
                self.on_log(f"【{_now_label()}】【全局】【-】：已点击启动按钮")
            # 市场按钮可见 → 视为已就绪
            if self.screen.locate("btn_market", timeout=0.2) is not None:
                return True
            _sleep(0.2)
        self.on_log(
            f"【{_now_label()}】【全局】【-】：启动失败，未见市场按钮，任务终止。"
        )
        return False

    def _ensure_ready_v2(self) -> bool:
        """基于 run_launch_flow 的统一启动封装。"""
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

    # ------------------------------ 导航/搜索 ------------------------------
    def _nav_to_search(self) -> bool:
        # 若已在市场页，直接聚焦搜索框
        sbox = self.screen.locate("input_search", timeout=0.5)
        if sbox is not None:
            self.screen.click_center(sbox)
            # 短暂等待（约 30ms）
            _sleep(0.03)
            return True
        # 否则点击“市场”进入
        box = self.screen.locate("btn_market", timeout=2.0)
        if box is None:
            return False
        self.screen.click_center(box)
        # 短暂等待（约 30ms）
        _sleep(0.03)
        # 聚焦搜索框
        sbox = self.screen.locate("input_search", timeout=2.0)
        if sbox is None:
            return False
        self.screen.click_center(sbox)
        # 短暂等待（约 30ms）
        _sleep(0.03)
        return True

    def _search(self, text: str) -> bool:
        self.screen.type_text(text or "")
        btn = self.screen.locate("btn_search", timeout=1.0)
        if btn is None:
            return False
        self.screen.click_center(btn)
        # 短暂等待（约 30ms）
        _sleep(0.03)
        return True

    # 严格导航：首页 → 市场 → 聚焦搜索 → 输入 → 搜索
    def _nav_home_market_search(self, item_disp: str, purchased_str: str, query: str) -> bool:
        # 步骤 A：若在详情页（btn_close 可见），先关闭并短暂等待
        t0 = time.perf_counter()
        closed_once = False
        c = self.screen.locate("btn_close", timeout=0.2)
        if c is not None:
            self.screen.click_center(c)
            # 非严格属于“搜索阶段”的恢复动作，保持较短等待
            _sleep(0.05)
            closed_once = True
        ms = int((time.perf_counter() - t0) * 1000.0)
        if closed_once:
            self._log(item_disp, purchased_str, f"检测到详情页，执行关闭 耗时={ms}ms")

        # 步骤 B：若存在首页按钮则主动点击
        t1 = time.perf_counter()
        h = self.screen.locate("btn_home", timeout=0.3)
        if h is not None:
            self.screen.click_center(h)
            # 短暂等待（约 30ms）
            _sleep(0.03)
            ms = int((time.perf_counter() - t1) * 1000.0)
            self._log(item_disp, purchased_str, f"点击首页按钮 耗时={ms}ms")
        else:
            ms = int((time.perf_counter() - t1) * 1000.0)
            self._log(item_disp, purchased_str, f"未找到首页按钮（跳过），耗时={ms}ms")

        # 步骤 C：点击“市场”
        t2 = time.perf_counter()
        m = self.screen.locate("btn_market", timeout=1.0)
        if m is None:
            ms = int((time.perf_counter() - t2) * 1000.0)
            self._log(item_disp, purchased_str, f"未找到市场按钮，耗时={ms}ms")
            return False
        self.screen.click_center(m)
        # 短暂等待（约 30ms）
        _sleep(0.03)
        ms = int((time.perf_counter() - t2) * 1000.0)
        self._log(item_disp, purchased_str, f"点击市场按钮 耗时={ms}ms")

        # 步骤 D：聚焦搜索框
        t3 = time.perf_counter()
        sbox = self.screen.locate("input_search", timeout=1.0)
        if sbox is None:
            ms = int((time.perf_counter() - t3) * 1000.0)
            self._log(item_disp, purchased_str, f"未找到搜索框，耗时={ms}ms")
            return False
        self.screen.click_center(sbox)
        # 短暂等待（约 30ms）
        _sleep(0.03)
        ms = int((time.perf_counter() - t3) * 1000.0)
        self._log(item_disp, purchased_str, f"聚焦搜索框 耗时={ms}ms")

        # 步骤 E：输入搜索词
        t4 = time.perf_counter()
        self.screen.type_text(query or "", clear_first=True)
        # 短暂等待（约 30ms）
        _sleep(0.03)
        ms = int((time.perf_counter() - t4) * 1000.0)
        self._log(item_disp, purchased_str, f"输入搜索内容 耗时={ms}ms")

        # 步骤 F：点击“搜索”
        t5 = time.perf_counter()
        btn = self.screen.locate("btn_search", timeout=1.0)
        if btn is None:
            ms = int((time.perf_counter() - t5) * 1000.0)
            self._log(item_disp, purchased_str, f"未找到搜索按钮，耗时={ms}ms")
            return False
        self.screen.click_center(btn)
        # 短暂等待（约 30ms）
        _sleep(0.03)
        ms = int((time.perf_counter() - t5) * 1000.0)
        self._log(item_disp, purchased_str, f"点击搜索 耗时={ms}ms")
        return True

    def clear_pos(self, goods_id: Optional[str] = None) -> None:
        if goods_id is None:
            self._pos_cache.clear()
        else:
            try:
                self._pos_cache.pop(goods_id, None)
            except Exception:
                pass

    def _open_goods_detail(
        self, goods: Goods, *, prefer_cached: bool = False
    ) -> Tuple[bool, int, str]:
        """打开指定商品的详情页。

        返回三元组 (ok, cost_ms, src)，src ∈ {"cached", "match", "none"}。
        当 prefer_cached=True 且存在缓存坐标时，优先尝试点击并通过 btn_buy 快速校验；
        若校验失败，回退到模板匹配并刷新缓存。
        """
        # 优先走缓存坐标路径（可选）
        if prefer_cached and goods.id and goods.id in self._pos_cache:
            try:
                box = self._pos_cache[goods.id]
                t0 = time.perf_counter()
                self.screen.click_center(box)
                # 通过 btn_buy 快速校验详情是否已打开
                # 缩短中间等待以加快校验
                buy_box = self.screen.locate("btn_buy", timeout=0.3)
                ok = buy_box is not None
                ms = int((time.perf_counter() - t0) * 1000.0)
                if ok:
                    # 重置并预热该详情页的控件缓存
                    try:
                        self._detail_ui_cache.clear()
                    except Exception:
                        self._detail_ui_cache = {}
                    self._detail_ui_cache["btn_buy"] = buy_box  # type: ignore[assignment]
                    # 以较短超时顺便缓存 “关闭/最大” 按钮
                    c = self.screen.locate("btn_close", timeout=0.2)
                    if c is not None:
                        self._detail_ui_cache["btn_close"] = c
                    m = self.screen.locate("btn_max", timeout=0.2)
                    if m is not None:
                        self._detail_ui_cache["btn_max"] = m
                    return True, ms, "cached"
                # 失效缓存并继续执行模板匹配分支
                self._pos_cache.pop(goods.id, None)
            except Exception:
                try:
                    self._pos_cache.pop(goods.id, None)
                except Exception:
                    pass
        # 模板匹配路径（统一：包含匹配与校验在内的打开耗时）
        t_begin = time.perf_counter()
        src = "none"
        if goods.image_path and os.path.exists(goods.image_path):
            try:
                template_box: Optional[Tuple[int, int, int, int]] = self._pg_locate_image(
                    goods.image_path, confidence=0.80, timeout=2.0
                )
                if template_box is not None:
                    self._pos_cache[goods.id] = template_box
                    self.screen.click_center(template_box)
                    # 校验详情是否已打开；等待时间与缓存路径保持一致（减半）
                    buy_box = self.screen.locate("btn_buy", timeout=0.3)
                    ok = buy_box is not None
                    ms = int((time.perf_counter() - t_begin) * 1000.0)
                    src = "match"
                    if ok:
                        # 重置并预热该详情页的控件缓存
                        try:
                            self._detail_ui_cache.clear()
                        except Exception:
                            self._detail_ui_cache = {}
                        self._detail_ui_cache["btn_buy"] = buy_box  # type: ignore[assignment]
                        c = self.screen.locate("btn_close", timeout=0.2)
                        if c is not None:
                            self._detail_ui_cache["btn_close"] = c
                        m = self.screen.locate("btn_max", timeout=0.2)
                        if m is not None:
                            self._detail_ui_cache["btn_max"] = m
                        return True, ms, src
                    return False, ms, src
                # 在查找超时内未找到
                ms = int((time.perf_counter() - t_begin) * 1000.0)
                src = "match"
                return False, ms, src
            except Exception:
                pass
        ms = int((time.perf_counter() - t_begin) * 1000.0)
        return False, ms, src

    @property
    def _pg(self):  # type: ignore
        import pyautogui  # type: ignore

        return pyautogui

    def _pg_locate_image(
        self, path: str, confidence: float, timeout: float = 0.0
    ) -> Optional[Tuple[int, int, int, int]]:
        end = time.time() + max(0.0, float(timeout or 0.0))
        while True:
            try:
                box = self._pg.locateOnScreen(path, confidence=float(confidence))
                if box is not None:
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
            _sleep(0.05)

    # ------------------------------ 价格读取 ------------------------------
    def _read_currency_avg_price(
        self, item_disp: str, purchased_str: str
    ) -> Optional[int]:
        # 与 GUI「货币价格区域-模板预览」一致：
        # - 在全屏匹配货币图标；
        # - 取最靠上的命中作为“平均单价”所在行；
        # - 在其右侧按配置宽度截取 ROI 并进行 OCR；
        ca = self.cfg.get("currency_area") or {}
        tpl = str(ca.get("template", ""))
        if not tpl or not os.path.exists(tpl):
            self._log(item_disp, purchased_str, "货币模板未配置或文件不存在")
            return None
        try:
            import pyautogui  # type: ignore
            import numpy as np  # type: ignore
            import cv2 as cv2  # type: ignore
            from PIL import Image as _PIL  # type: ignore
        except Exception as e:
            self._log(item_disp, purchased_str, f"缺少依赖: {e}")
            return None
        try:
            scr_img = pyautogui.screenshot()
        except Exception as e:
            self._log(item_disp, purchased_str, f"截屏失败: {e}")
            return None
        try:
            scr = np.array(scr_img)[:, :, ::-1].copy()  # BGR 通道顺序
        except Exception as e:
            self._log(item_disp, purchased_str, f"图像转换失败: {e}")
            return None
        tmpl = cv2.imread(tpl, cv2.IMREAD_COLOR)
        if tmpl is None:
            self._log(item_disp, purchased_str, "无法读取货币模板图片")
            return None
        try:
            thr = float(ca.get("threshold", 0.8) or 0.8)
        except Exception:
            thr = 0.8
        th, tw = int(tmpl.shape[0]), int(tmpl.shape[1])
        try:
            gray = cv2.cvtColor(scr, cv2.COLOR_BGR2GRAY)
            tgray = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY) if tmpl.ndim == 3 else tmpl
            res = cv2.matchTemplate(gray, tgray, cv2.TM_CCOEFF_NORMED)
            ys, xs = np.where(res >= thr)
        except Exception as e:
            self._log(item_disp, purchased_str, f"模板匹配失败: {e}")
            return None
        cand = [(int(y), int(x), float(res[y, x])) for y, x in zip(ys, xs)]
        cand.sort(key=lambda a: a[2], reverse=True)
        picks: list[tuple[int, int, float]] = []
        for y, x, s in cand:
            ok = True
            for py, px, _ in picks:
                if abs(py - y) < th // 2 and abs(px - x) < tw // 2:
                    ok = False
                    break
            if ok:
                picks.append((y, x, s))
            if len(picks) >= 2:
                break
        if not picks:
            self._log(item_disp, purchased_str, "未找到货币图标匹配位置")
            return None
        # Y 最小的候选视为“平均单价”所在行
        picks.sort(key=lambda a: a[0])
        y, x, _s = picks[0]
        try:
            pw = int(ca.get("price_width", 220) or 220)
        except Exception:
            pw = 220
        h, w = gray.shape[:2]
        x1 = max(0, int(x + tw))
        y1 = max(0, int(y))
        x2 = min(w, x1 + max(1, pw))
        y2 = min(h, y1 + th)
        if x2 <= x1 or y2 <= y1:
            self._log(item_disp, purchased_str, "平均单价 ROI 尺寸无效")
            return None
        crop = scr[y1:y2, x1:x2]
        # 缩放
        try:
            sc = float(ca.get("scale", 1.0) or 1.0)
        except Exception:
            sc = 1.0
        if abs(sc - 1.0) > 1e-3:
            try:
                ch, cw = crop.shape[:2]
                crop = cv2.resize(
                    crop,
                    (max(1, int(cw * sc)), max(1, int(ch * sc))),
                    interpolation=cv2.INTER_CUBIC,
                )
            except Exception:
                pass
        # 二值化（尽力而为）
        oimg: Optional[_PIL.Image] = None
        try:
            g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            _thr, thb = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            oimg = _PIL.fromarray(thb)
        except Exception:
            try:
                oimg = _PIL.fromarray(crop[:, :, ::-1])
            except Exception:
                pass

        # 根据 currency_area 选择 OCR 引擎，回退到 avg_price_area 的配置
        eng = str(ca.get("ocr_engine", "") or "").strip().lower()
        if not eng:
            eng = str(
                (self.cfg.get("avg_price_area", {}) or {}).get("ocr_engine", "umi")
                or "umi"
            ).lower()
        txt = ""
        try:
            if eng in ("tesseract", "tess", "ts"):
                allow = str(
                    (self.cfg.get("avg_price_area", {}) or {}).get(
                        "ocr_allowlist", "0123456789KM"
                    )
                )
                need = "KMkm"
                allow_ex = allow + "".join(ch for ch in need if ch not in allow)
                cfg = f"--oem 3 --psm 6 -c tessedit_char_whitelist={allow_ex}"
                if oimg is not None:
                    txt = read_text(
                        oimg, engine="tesseract", grayscale=True, tess_config=cfg
                    )
            else:
                ocfg = self.cfg.get("umi_ocr") or {}
                if oimg is not None:
                    txt = read_text(
                        oimg,
                        engine="umi",
                        grayscale=True,
                        umi_base_url=str(ocfg.get("base_url", "http://127.0.0.1:1224")),
                        umi_timeout=float(ocfg.get("timeout_sec", 2.5) or 2.5),
                        umi_options=dict(ocfg.get("options", {}) or {}),
                    )
        except Exception as e:
            # 若 Umi 失败：抛出致命错误以终止任务
            if eng in ("umi", "umi-ocr", "umiocr"):
                self._log(
                    item_disp,
                    purchased_str,
                    f"Umi OCR 失败: {e} | 引擎={eng} ROI=({x1},{y1},{x2},{y2}) 缩放={sc:.2f} 置信度={_s:.2f}",
                )
                raise FatalOcrError(str(e))
            else:
                self._log(
                    item_disp,
                    purchased_str,
                    f"货币 OCR 失败: {e} | 引擎={eng} ROI=({x1},{y1},{x2},{y2}) 缩放={sc:.2f} 置信度={_s:.2f}",
                )
                return None
        val = _parse_price_text(txt or "")
        if val is None or val <= 0:
            self._log(
                item_disp,
                purchased_str,
                f"货币 OCR 解析失败：'{(txt or '').strip()[:64]}' | 引擎={eng} ROI=({x1},{y1},{x2},{y2}) 缩放={sc:.2f} 置信度={_s:.2f}",
            )
            return None
        # 记录关键参数用于调试
        self._log(
            item_disp,
            purchased_str,
            f"货币 OCR 成功 值={val} 引擎={eng} ROI=({x1},{y1},{x2},{y2}) 缩放={sc:.2f} 置信度={_s:.2f}",
        )
        return int(val)

    def _read_avg_unit_price(
        self, goods: Goods, item_disp: str, purchased_str: str
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
        # 仅对上半部分做 OCR（平均单价）
        eng = str(avg_cfg.get("ocr_engine", "umi") or "umi").lower()
        txt = ""
        ocr_ms = -1
        try:
            if eng in ("tesseract", "tess", "ts"):
                allow = str(avg_cfg.get("ocr_allowlist", "0123456789KM"))
                need = "KMkm"
                allow_ex = allow + "".join(ch for ch in need if ch not in allow)
                cfg = f"--oem 3 --psm 6 -c tessedit_char_whitelist={allow_ex}"
                t_ocr = time.perf_counter()
                if bin_top is not None:
                    txt = read_text(
                        bin_top,
                        engine="tesseract",
                        grayscale=True,
                        tess_config=cfg,
                    )
                ocr_ms = int((time.perf_counter() - t_ocr) * 1000.0)
            else:
                ocfg = self.cfg.get("umi_ocr") or {}
                t_ocr = time.perf_counter()
                if bin_top is not None:
                    txt = read_text(
                        bin_top,
                        engine="umi",
                        grayscale=True,
                        umi_base_url=str(ocfg.get("base_url", "http://127.0.0.1:1224")),
                        umi_timeout=float(ocfg.get("timeout_sec", 2.5) or 2.5),
                        umi_options=dict(ocfg.get("options", {}) or {}),
                    )
                ocr_ms = int((time.perf_counter() - t_ocr) * 1000.0)
        except Exception as e:
            if eng in ("umi", "umi-ocr", "umiocr"):
                self._log(
                    item_disp,
                    purchased_str,
                    f"Umi OCR 失败: {e} | 引擎={eng} ROI=({x_left},{y_top},{x_left + width},{y_top + mid_h}) 缩放={sc:.2f} btn耗时={btn_ms}ms OCR耗时={ocr_ms if ocr_ms >= 0 else '-'}ms",
                )
                raise FatalOcrError(str(e))
            else:
                self._log(
                    item_disp,
                    purchased_str,
                    f"平均价 OCR 失败: {e} | 引擎={eng} ROI=({x_left},{y_top},{x_left + width},{y_top + mid_h}) 缩放={sc:.2f} btn耗时={btn_ms}ms OCR耗时={ocr_ms if ocr_ms >= 0 else '-'}ms",
                )
                return None
        val = _parse_price_text(txt or "")
        if val is None or val <= 0:
            self._log(
                item_disp,
                purchased_str,
                f"平均价 OCR 解析失败：'{(txt or '').strip()[:64]}' | 引擎={eng} ROI=({x_left},{y_top},{x_left + width},{y_top + mid_h}) 缩放={sc:.2f} btn耗时={btn_ms}ms OCR耗时={ocr_ms if ocr_ms >= 0 else '-'}ms",
            )
            return None
        self._log(
            item_disp,
            purchased_str,
            f"平均价 OCR 成功 值={val} 引擎={eng} ROI=({x_left},{y_top},{x_left + width},{y_top + mid_h}) 缩放={sc:.2f} btn耗时={btn_ms}ms OCR耗时={ocr_ms}ms",
        )
        return int(val)

    def _continue_from_detail(
        self,
        goods: Goods,
        task: Dict[str, Any],
        purchased_so_far: int,
        item_disp: str,
        purchased_str: str,
    ) -> Tuple[int, bool]:
        # 从货币区域读取价格
        unit_price = self._read_avg_unit_price(goods, item_disp, purchased_str)
        if unit_price is None or unit_price <= 0:
            # 如可关闭详情则执行关闭
            c = self._detail_ui_cache.get("btn_close") or self.screen.locate(
                "btn_close", timeout=0.3
            )
            if c is not None:
                self.screen.click_center(c)
            try:
                self._detail_ui_cache.clear()
            except Exception:
                self._detail_ui_cache = {}
            try:
                self._detail_ui_cache.clear()
            except Exception:
                self._detail_ui_cache = {}
            try:
                self._detail_ui_cache.clear()
            except Exception:
                self._detail_ui_cache = {}
                try:
                    self._detail_ui_cache.clear()
                except Exception:
                    self._detail_ui_cache = {}
                try:
                    self._detail_ui_cache.clear()
                except Exception:
                    self._detail_ui_cache = {}
            self._log(item_disp, purchased_str, "ƽ������ʶ��ʧ�ܣ��ѹر�����")
            return 0, True

        thr = int(task.get("price_threshold", 0) or 0)
        prem = float(task.get("price_premium_pct", 0.0) or 0.0)
        limit = thr + int(round(thr * max(0.0, prem) / 100.0)) if thr > 0 else 0
        restock = int(task.get("restock_price", 0) or 0)
        # 记录价格与阈值
        self._log(
            item_disp,
            purchased_str,
            f"ƽ������={unit_price}����ֵ��{limit}(+{int(prem)}%)",
        )
        # 计算购买数量
        target_total = int(task.get("target_total", 0) or 0)
        max_per_order = max(1, int(task.get("max_per_order", 120) or 120))
        remain = (
            max(0, target_total - purchased_so_far)
            if target_total > 0
            else max_per_order
        )

        # 基础数量规则
        q = 1
        # 补货路径
        if restock > 0 and unit_price <= restock:
            if (goods.big_category or "").strip() == "��ҩ":
                # 若存在“最大”按钮则点击
                b = self._detail_ui_cache.get("btn_max") or self.screen.locate(
                    "btn_max", timeout=0.3
                )
                if b is not None:
                    self.screen.click_center(b)
                    q = min(120, max_per_order, max(1, remain))
                else:
                    q = min(120, max_per_order, max(1, remain))
            else:
                # 非弹药：若配置了数量输入点，则输入 5
                pt = (self.cfg.get("points") or {}).get("quantity_input") or {}
                try:
                    x, y = int(pt.get("x", 0)), int(pt.get("y", 0))
                    if x > 0 and y > 0:
                        self._pg.click(x, y)  # type: ignore[attr-defined]
                        _sleep(0.02)
                        self.screen.type_text("5", clear_first=True)
                        q = min(5, max_per_order, max(1, remain))
                except Exception:
                    q = min(5, max_per_order, max(1, remain))
        else:
            # 默认或 1（默认不改动数量）
            q = min(1, max_per_order, max(1, remain))

        # 阈值检查（thr=0 时总是购买）
        ok_to_buy = (limit <= 0) or (unit_price <= limit)
        if not ok_to_buy:
            # 关闭详情并稍后重试
            c = self._detail_ui_cache.get("btn_close") or self.screen.locate(
                "btn_close", timeout=0.3
            )
            if c is not None:
                self.screen.click_center(c)
                try:
                    self._detail_ui_cache.clear()
                except Exception:
                    self._detail_ui_cache = {}
            return 0, True

        # 提交
        # 同样缩短等待以减少中间延时
        b = self._detail_ui_cache.get("btn_buy") or self.screen.locate(
            "btn_buy", timeout=0.3
        )
        if b is None:
            c = self._detail_ui_cache.get("btn_close") or self.screen.locate(
                "btn_close", timeout=0.3
            )
            if c is not None:
                self.screen.click_center(c)
            self._log(item_disp, purchased_str, "δ�ҵ��������ύ��ť")
            return 0, True
        self.screen.click_center(b)

        # 结果轮询（约100ms 间隔，最长 ~1.2s）
        t_end = time.time() + 1.2
        got_ok = False
        found_fail = False
        while time.time() < t_end:
            if self.screen.locate("buy_ok", timeout=0.0) is not None:
                got_ok = True
                break
            if self.screen.locate("buy_fail", timeout=0.0) is not None:
                found_fail = True
            _sleep(0.1)

        if got_ok:
            # 先关闭成功弹层（再继续后续关闭/保留详情）
            self._dismiss_success_overlay()
            if restock > 0 and unit_price <= restock:
                self._log(
                    item_disp,
                    f"{purchased_so_far + q}/{target_total}",
                    f"购买成功(+{q})，补货模式：保留详情，继续",
                )
                return q, True
            # 关闭详情
            c = self._detail_ui_cache.get("btn_close") or self.screen.locate(
                "btn_close", timeout=0.5
            )
            if c is not None:
                self.screen.click_center(c)
            self._log(
                item_disp,
                f"{purchased_so_far + q}/{target_total}",
                f"����ɹ�(+{q})���ѹر�����",
            )
            return q, True
        elif found_fail:
            c = self._detail_ui_cache.get("btn_close") or self.screen.locate(
                "btn_close", timeout=0.3
            )
            if c is not None:
                self.screen.click_center(c)
            self._log(item_disp, purchased_str, "����ʧ�ܣ��ѹر�����")
            return 0, True
        else:
            # 未知结果：关闭详情并继续
            c = self._detail_ui_cache.get("btn_close") or self.screen.locate(
                "btn_close", timeout=0.3
            )
            if c is not None:
                self.screen.click_center(c)
            self._log(item_disp, purchased_str, "���δ֪���ѹر�����")
            return 0, True

    # ------------------------------ 购买流程 ------------------------------

    def _open_detail_with_recovery(
        self,
        goods: Goods,
        *,
        skip_search: bool,
        item_disp: str,
        purchased_str: str,
    ) -> Tuple[bool, int, str]:
        # 若已在详情页：直接返回“resume”，并预热按钮缓存
        b0 = self.screen.locate("btn_buy", timeout=0.2)
        c0 = self.screen.locate("btn_close", timeout=0.2)
        if (b0 is not None) and (c0 is not None):
            try:
                self._detail_ui_cache["btn_buy"] = b0  # type: ignore[index]
            except Exception:
                pass
            try:
                self._detail_ui_cache["btn_close"] = c0  # type: ignore[index]
            except Exception:
                pass
            return True, 0, "resume"

        ok_open, cost_ms, src = self._open_goods_detail(
            goods, prefer_cached=bool(skip_search)
        )
        if ok_open:
            return ok_open, cost_ms, src
        # 当模板匹配失败时，尝试进行一次恢复性搜索
        query = (goods.search_name or "").strip()
        try:
            if query and self._nav_to_search() and self._search(query):
                ok2, cost_ms2, src2 = self._open_goods_detail(
                    goods, prefer_cached=False
                )
                return ok2, cost_ms2, src2
        except Exception:
            pass
        return ok_open, cost_ms, src
    def execute_once(
        self,
        goods: Goods,
        task: Dict[str, Any],
        purchased_so_far: int,
        *,
        skip_search: bool = False,
    ) -> Tuple[int, bool]:
        """对单个商品执行一次购买尝试。

        返回 (本次购买数量, 是否继续外层循环)。
        """
        # 准备日志上下文
        item_disp = goods.name or goods.search_name or str(task.get("item_name", ""))
        target_total = int(task.get("target_total", 0) or 0)
        purchased_str = f"{purchased_so_far}/{target_total}"
        # 恢复快速路径：若仍停留在详情页，则直接从详情页继续
        try:
            # 同时检测到购买与关闭按钮才认为在详情页，避免误判
            b0 = self.screen.locate("btn_buy", timeout=0.2)
            c0 = self.screen.locate("btn_close", timeout=0.2)
            if (b0 is not None) and (c0 is not None):
                # 预热缓存（购买/关闭）后从详情页继续
                try:
                    self._detail_ui_cache["btn_buy"] = b0  # type: ignore[index]
                except Exception:
                    pass
                try:
                    self._detail_ui_cache["btn_close"] = c0  # type: ignore[index]
                except Exception:
                    pass
                self._log(item_disp, purchased_str, "检测到仍在详情页，直接继续")
                return self._continue_from_detail(
                    goods, task, purchased_so_far, item_disp, purchased_str
                )
        except Exception:
            pass
        # 如需，则执行导航与搜索
        if not skip_search:
            if not self._nav_to_search():
                self._log(item_disp, purchased_str, "未能进入市场或定位搜索框")
                return 0, True
            query = (goods.search_name or "").strip()
            if not query:
                self._log(item_disp, purchased_str, "无有效 search_name，跳过本次")
                return 0, False
            if not self._search(query):
                self._log(item_disp, purchased_str, "未能点击“搜索”按钮")
                return 0, True
            self._log(
                item_disp,
                purchased_str,
                f"定位 市场→搜索框→搜索 成功（关键词={query}）",
            )
        # 打开详情（带恢复）：可复用详情或在失败时重搜一次
        ok_open, cost_ms, src = self._open_detail_with_recovery(
            goods, skip_search=bool(skip_search), item_disp=item_disp, purchased_str=purchased_str
        )
        if not ok_open:
            if src == "cached":
                self._log(
                    item_disp,
                    purchased_str,
                    f"缓存坐标无效，未进入详情 打开耗时={cost_ms}ms",
                )
            else:
                # 模板匹配失败仍保留匹配耗时语义
                self._log(
                    item_disp, purchased_str, f"未匹配到商品模板 匹配耗时={cost_ms}ms"
                )
            return 0, True
        if src == "cached":
            self._log(
                item_disp, purchased_str, f"使用缓存坐标进入详情 打开耗时={cost_ms}ms"
            )
        else:
            # 统一指标：模板路径成功也输出打开总耗时（包含匹配+验证）
            self._log(
                item_disp,
                purchased_str,
                f"匹配商品模板并进入详情 打开耗时={cost_ms}ms",
            )

        # 从货币区域读取价格
        unit_price = self._read_avg_unit_price(goods, item_disp, purchased_str)
        if unit_price is None or unit_price <= 0:
            # 如可关闭详情则执行关闭
            c = self._detail_ui_cache.get("btn_close") or self.screen.locate(
                "btn_close", timeout=0.3
            )
            if c is not None:
                self.screen.click_center(c)
            try:
                self._detail_ui_cache.clear()
            except Exception:
                self._detail_ui_cache = {}
            try:
                self._detail_ui_cache.clear()
            except Exception:
                self._detail_ui_cache = {}
            try:
                self._detail_ui_cache.clear()
            except Exception:
                self._detail_ui_cache = {}
                try:
                    self._detail_ui_cache.clear()
                except Exception:
                    self._detail_ui_cache = {}
                try:
                    self._detail_ui_cache.clear()
                except Exception:
                    self._detail_ui_cache = {}
            self._log(item_disp, purchased_str, "平均单价识别失败，已关闭详情")
            return 0, True

        thr = int(task.get("price_threshold", 0) or 0)
        prem = float(task.get("price_premium_pct", 0.0) or 0.0)
        limit = thr + int(round(thr * max(0.0, prem) / 100.0)) if thr > 0 else 0
        restock = int(task.get("restock_price", 0) or 0)
        # 记录价格与阈值
        self._log(
            item_disp,
            purchased_str,
            f"平均单价={unit_price}，阈值≤{limit}(+{int(prem)}%)",
        )
        # 计算购买数量
        target_total = int(task.get("target_total", 0) or 0)
        max_per_order = max(1, int(task.get("max_per_order", 120) or 120))
        remain = (
            max(0, target_total - purchased_so_far)
            if target_total > 0
            else max_per_order
        )

        # 基础数量规则
        q = 1
        # 补货路径
        if restock > 0 and unit_price <= restock:
            if (goods.big_category or "").strip() == "弹药":
                # 若存在“最大”按钮则点击
                b = self._detail_ui_cache.get("btn_max") or self.screen.locate(
                    "btn_max", timeout=0.3
                )
                if b is not None:
                    self.screen.click_center(b)
                    q = min(120, max_per_order, max(1, remain))
                else:
                    q = min(120, max_per_order, max(1, remain))
            else:
                # 非弹药：若配置了数量输入点，则输入 5
                pt = (self.cfg.get("points") or {}).get("quantity_input") or {}
                try:
                    x, y = int(pt.get("x", 0)), int(pt.get("y", 0))
                    if x > 0 and y > 0:
                        self._pg.click(x, y)  # type: ignore[attr-defined]
                        _sleep(0.02)
                        self.screen.type_text("5", clear_first=True)
                        q = min(5, max_per_order, max(1, remain))
                except Exception:
                    q = min(5, max_per_order, max(1, remain))
        else:
            # 默认或 1（默认不改动数量）
            q = min(1, max_per_order, max(1, remain))

        # 阈值检查（thr=0 时总是购买）
        ok_to_buy = (limit <= 0) or (unit_price <= limit)
        if not ok_to_buy:
            # 关闭详情并稍后重试
            c = self._detail_ui_cache.get("btn_close") or self.screen.locate(
                "btn_close", timeout=0.3
            )
            if c is not None:
                self.screen.click_center(c)
                try:
                    self._detail_ui_cache.clear()
                except Exception:
                    self._detail_ui_cache = {}
            return 0, True

        # 提交
        # 同样缩短等待以减少中间延时
        b = self._detail_ui_cache.get("btn_buy") or self.screen.locate(
            "btn_buy", timeout=0.3
        )
        if b is None:
            c = self._detail_ui_cache.get("btn_close") or self.screen.locate(
                "btn_close", timeout=0.3
            )
            if c is not None:
                self.screen.click_center(c)
            self._log(item_disp, purchased_str, "未找到“购买”提交按钮")
            return 0, True
        self.screen.click_center(b)

        # 结果轮询（约100ms 间隔，最长 ~1.2s）
        t_end = time.time() + 1.2
        got_ok = False
        found_fail = False
        while time.time() < t_end:
            if self.screen.locate("buy_ok", timeout=0.0) is not None:
                got_ok = True
                break
            if self.screen.locate("buy_fail", timeout=0.0) is not None:
                found_fail = True
            _sleep(0.1)

        if got_ok:
            # 先关闭成功弹层（再继续后续关闭/保留详情）
            self._dismiss_success_overlay()
            if restock > 0 and unit_price <= restock:
                self._log(
                    item_disp,
                    f"{purchased_so_far + q}/{target_total}",
                    f"购买成功(+{q})，补货模式：保留详情，继续",
                )
                return q, True
            # 关闭详情
            c = self._detail_ui_cache.get("btn_close") or self.screen.locate(
                "btn_close", timeout=0.5
            )
            if c is not None:
                self.screen.click_center(c)
            self._log(
                item_disp,
                f"{purchased_so_far + q}/{target_total}",
                f"购买成功(+{q})，已关闭详情",
            )
            return q, True
        elif found_fail:
            c = self._detail_ui_cache.get("btn_close") or self.screen.locate(
                "btn_close", timeout=0.3
            )
            if c is not None:
                self.screen.click_center(c)
            self._log(item_disp, purchased_str, "购买失败，已关闭详情")
            return 0, True
        else:
            # 未知结果：关闭详情并继续
            c = self._detail_ui_cache.get("btn_close") or self.screen.locate(
                "btn_close", timeout=0.3
            )
            if c is not None:
                self.screen.click_center(c)
            self._log(item_disp, purchased_str, "结果未知，已关闭详情")
            return 0, True


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

    def _do_soft_restart(self) -> None:
        # 尝试通过按钮优雅退出；否则通过重启进程方式恢复
        self._relay_log(f"【{_now_label()}】【全局】【-】：到达重启周期，尝试重启游戏…")
        # 重启前清空位置缓存
        try:
            self.buyer.clear_pos()
        except Exception:
            pass
        # 若提供了模板，则点击返回/设置/退出/确认等
        for key in ("btn_back", "btn_settings", "btn_exit", "btn_exit_confirm"):
            box = self.screen.locate(key, timeout=0.8)
            if box is not None:
                self.screen.click_center(box)
                _sleep(0.3)
        # 重新进行就绪检查
        ok = self._ensure_ready()
        if ok:
            self._relay_log(f"【{_now_label()}】【全局】【-】：已重启并回到市场")
        else:
            self._relay_log(f"【{_now_label()}】【全局】【-】：重启失败，未回到市场")
        # 重置计时窗口（重启耗时不计入任务时长）
        self._next_restart_ts = time.time() + max(1, self.restart_every_min) * 60

    # ------------------------------ 主循环 ------------------------------
    def _run(self) -> None:
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

        self._next_restart_ts = None

        if str(self.mode or "time") == "round":
            self._run_round_robin(tasks)
        else:
            self._run_time_window(tasks)

    def _run_round_robin(self, tasks: List[Dict[str, Any]]) -> None:
        # 按顺序循环执行已启用的任务
        idx = 0
        n = len(tasks)
        while not self._stop.is_set():
            if n == 0:
                _sleep(0.3)
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
            # 片段开始时执行一次导航与搜索
            gid = str(t.get("item_id", ""))
            goods = self.goods_map.get(gid)
            if not goods:
                idx += 1
                continue
            # 确认搜索上下文；片段起始清理缓存坐标以确保重新匹配
            try:
                self.buyer.clear_pos(goods.id)
                _ = self.buyer.execute_once(goods, t, purchased, skip_search=False)
            except FatalOcrError as e:
                self._relay_log(
                    f"【{_now_label()}】【全局】【-】：Umi OCR 失败（片段初始化），终止任务：{e}"
                )
                self._stop.set()
                break
            # 在片段内执行购买循环（不重复搜索）
            search_ready = True
            while not self._stop.is_set() and time.time() < seg_end:
                # 暂停处理
                while self._pause.is_set() and not self._stop.is_set():
                    _sleep(0.2)
                if self._stop.is_set():
                    break

                # 在安全点检查重启（详情关闭的间隙）
                if self._should_restart_now():
                    self._do_soft_restart()
                    search_ready = False
                    # 继续执行以重建搜索上下文
                if not search_ready:
                    # 精确地再执行一次导航与搜索
                    self.buyer.clear_pos(goods.id)
                    _ = self.buyer.execute_once(goods, t, purchased, skip_search=False)
                    search_ready = True

                # 执行一次购买尝试
                try:
                    got, _cont = self.buyer.execute_once(
                        goods, t, purchased, skip_search=True
                    )
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
                _sleep(0.05)

            # 片段收尾与统计
            elapsed = int((time.time() - seg_start) * 1000)
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
                _sleep(1.2)
                continue

            t = tasks[chosen_idx]
            # 达到目标则跳过
            target = int(t.get("target_total", 0) or 0)
            purchased = int(t.get("purchased", 0) or 0)
            if target > 0 and purchased >= target:
                _sleep(0.8)
                continue

            # 计算窗口结束时间（用于显示）
            te = str(t.get("time_end", ""))
            # 仅用于显示；当窗口不再匹配时循环会自然退出
            self._relay_log(
                f"【{_now_label()}】【{t.get('item_name', '')}】【{purchased}/{target}】：进入时间窗口执行（结束 {te or '—:—'}）"
            )

            # 进入窗口时建立一次搜索上下文
            gid = str(t.get("item_id", ""))
            goods = self.goods_map.get(gid)
            if not goods:
                _sleep(1.0)
                continue
            try:
                _ = self.buyer.execute_once(goods, t, purchased, skip_search=False)
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
                    _sleep(0.2)
                if self._stop.is_set():
                    break
                # 重启检查
                if self._should_restart_now():
                    self._do_soft_restart()
                    search_ready = False
                if not search_ready:
                    # 重启后重新建立搜索上下文
                    _ = self.buyer.execute_once(goods, t, purchased, skip_search=False)
                    search_ready = True
                # 执行一次购买尝试（不重复搜索）
                try:
                    got, _cont = self.buyer.execute_once(
                        goods, t, purchased, skip_search=True
                    )
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
                _sleep(0.05)

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
]
