from __future__ import annotations

import json
import os
import threading
import time
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

# Optional dependencies are imported lazily where used

try:
    # Ensure PyAutoGUI confidence param exists even when OpenCV missing
    from compat import ensure_pyautogui_confidence_compat  # type: ignore

    ensure_pyautogui_confidence_compat()
except Exception:
    pass

from app_config import load_config  # type: ignore
from ocr_reader import read_text  # type: ignore


class FatalOcrError(RuntimeError):
    """Raised when OCR engine (Umi) fatally fails and the task should stop."""

    pass


# ------------------------------ Utilities ------------------------------


def _now_label() -> str:
    return time.strftime("%H:%M:%S")


def _safe_int(s: str, default: int = -1) -> int:
    try:
        return int(s)
    except Exception:
        return default


def _parse_price_text(txt: str) -> Optional[int]:
    """Parse OCR text into integer price.

    Accepts digits and optional 'K' suffix (2.1K -> 2100).
    Returns None when parsing fails.
    """
    if not txt:
        return None
    s = txt.strip().upper().replace(",", "").replace(" ", "")
    # Keep digits, dot and K
    import re

    m = re.search(r"([0-9]+(?:\.[0-9]+)?)([MK])?", s)
    if not m:
        # Try to extract continuous digits
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


# ------------------------------ Image/Click helpers ------------------------------


class ScreenOps:
    """Thin wrappers around pyautogui and OpenCV template matching.

    Implement minimal find/click/screenshot used by the runner.
    """

    def __init__(self, cfg: Dict[str, Any], step_delay: float = 0.01) -> None:
        self.cfg = cfg
        self.step_delay = float(step_delay or 0.01)

        try:  # pyautogui is optional at import time
            import pyautogui  # type: ignore

            # Fail faster when confidence not supported
            _ = getattr(pyautogui, "locateOnScreen")
        except Exception as e:  # pragma: no cover - runtime guard
            raise RuntimeError(
                "缺少 pyautogui 或其依赖，请安装 pyautogui + opencv-python。"
            ) from e

    # Lazy properties to avoid repeated getattr
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
        """Return (left, top, width, height) or None."""
        path, conf = self._tpl(tpl_key)
        if not path or not os.path.exists(path):
            return None
        end = time.time() + max(0.0, float(timeout or 0.0))
        while True:
            try:
                box = self._pg.locateOnScreen(path, confidence=conf, region=region)
                if box is not None:
                    # pyautogui.Box -> tuple
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
                # Ctrl+A, Backspace
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


# ------------------------------ Buyer logic ------------------------------


@dataclass
class Goods:
    id: str
    name: str
    search_name: str
    image_path: str
    big_category: str
    sub_category: str = ""
    exchangeable: bool = False


# ------------------------------ Unified Launch Flow ------------------------------


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
    """Unified launcher flow used by both preview and task execution.

    Steps:
    1) Validate config + template files (exe_path, templates.home_indicator, templates.btn_launch)
    2) Start launcher process, catching errors
    3) Wait up to launcher_timeout_sec for 'btn_launch'
    4) Wait click_delay, then click once
    5) Wait up to startup_timeout_sec for 'home_indicator'
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

    # Validate templates
    tpls = (cfg.get("templates", {}) or {})
    def _tpl_path(key: str) -> str:
        t = tpls.get(key) or {}
        return str(t.get("path", "")).strip()
    home_key = "home_indicator"
    home_path = _tpl_path(home_key)
    launch_path = _tpl_path("btn_launch")

    if not exe:
        return LaunchResult(False, code="missing_config", error="未配置启动器路径")
    if not os.path.exists(exe):
        return LaunchResult(False, code="exe_missing", error="启动器路径不存在")
    if not launch_path or not os.path.exists(launch_path):
        return LaunchResult(False, code="missing_launch_template", error="未配置或找不到‘启动按钮’模板文件")
    if not home_path or not os.path.exists(home_path):
        return LaunchResult(False, code="missing_home_template", error="未配置或找不到‘首页标识’模板文件")

    # Create screen ops
    screen = ScreenOps(cfg, step_delay=0.05)

    # Start launcher
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

    # Wait for 'btn_launch'
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

    # Wait click delay then click
    if float(click_delay) > 0:
        t_end_click = time.time() + float(click_delay)
        while time.time() < t_end_click:
            _sleep(0.2)
    screen.click_center(launch_box)
    _log("[启动流程] 已点击启动按钮")

    # Wait for 'home_indicator'
    end_home = time.time() + max(1.0, float(startup_to))
    while time.time() < end_home:
        if screen.locate(home_key, timeout=0.5) is not None:
            return LaunchResult(True, code="ok")
        _sleep(0.3)
    return LaunchResult(False, code="home_timeout", error="等待首页标识超时")


class Buyer:
    """Unified purchase flow for a single goods item.

    This class encapsulates the navigation/search/detail/price/read/submit steps
    described in the TASK_EXECUTION_DESIGN_FINAL.md design.
    """

    def __init__(
        self, cfg: Dict[str, Any], screen: ScreenOps, on_log: Callable[[str], None]
    ) -> None:
        self.cfg = cfg
        self.screen = screen
        self.on_log = on_log
        # Cache of list-item bounding boxes per goods.id to avoid repeated template matching
        self._pos_cache: Dict[str, Tuple[int, int, int, int]] = {}

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
        # Cache for buttons inside an opened detail panel
        # Keys: 'btn_buy', 'btn_close', 'btn_max'
        self._detail_ui_cache: Dict[str, Tuple[int, int, int, int]] = {}

    # ----- helpers -----
    def _log(self, item_disp: str, purchased: str, msg: str) -> None:
        self.on_log(f"【{_now_label()}】【{item_disp}】【{purchased}】：{msg}")

    def _ensure_ready(self, startup_timeout_sec: float) -> bool:
        # If market present -> ready
        if self.screen.locate("btn_market", timeout=0.2) is not None:
            return True
        # Launch game if configured
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
            # Click launch if present
            box = self.screen.locate("btn_launch", timeout=0.0)
            if box is not None:
                self.screen.click_center(box)
                self.on_log(f"【{_now_label()}】【全局】【-】：已点击启动按钮")
            # Market visible -> ready
            if self.screen.locate("btn_market", timeout=0.2) is not None:
                return True
            _sleep(0.2)
        self.on_log(
            f"【{_now_label()}】【全局】【-】：启动失败，未见市场按钮，任务终止。"
        )
        return False

    def _ensure_ready_v2(self) -> bool:
        """Unified launch flow wrapper using run_launch_flow()."""
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

    # ----- navigation/search -----
    def _nav_to_search(self) -> bool:
        # Try clicking Market (if visible)
        box = self.screen.locate("btn_market", timeout=2.0)
        if box is None:
            return False
        self.screen.click_center(box)
        _sleep(0.1)
        # Focus search box
        sbox = self.screen.locate("input_search", timeout=2.0)
        if sbox is None:
            return False
        self.screen.click_center(sbox)
        return True

    def _search(self, text: str) -> bool:
        self.screen.type_text(text or "")
        btn = self.screen.locate("btn_search", timeout=1.0)
        if btn is None:
            return False
        self.screen.click_center(btn)
        # Explicit settle time for results rendering
        _sleep(2.0)
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
        """Open detail for goods.

        Returns (ok, cost_ms, src) where src in {"cached", "match", "none"}.
        When prefer_cached=True and a cached box is available, tries clicking it first.
        If cached click fails to reveal a detail context (via a quick btn_buy check),
        falls back to template matching and refreshes the cache.
        """
        # Cached path first (optional)
        if prefer_cached and goods.id and goods.id in self._pos_cache:
            try:
                box = self._pos_cache[goods.id]
                t0 = time.perf_counter()
                self.screen.click_center(box)
                # Quick verify that detail opened (btn_buy present)
                # Halve intermediate wait to speed up verification
                buy_box = self.screen.locate("btn_buy", timeout=0.3)
                ok = buy_box is not None
                ms = int((time.perf_counter() - t0) * 1000.0)
                if ok:
                    # Reset and warm-fill UI cache for this detail view
                    try:
                        self._detail_ui_cache.clear()
                    except Exception:
                        self._detail_ui_cache = {}
                    self._detail_ui_cache["btn_buy"] = buy_box  # type: ignore[assignment]
                    # Opportunistically cache close/max with small timeouts
                    c = self.screen.locate("btn_close", timeout=0.2)
                    if c is not None:
                        self._detail_ui_cache["btn_close"] = c
                    m = self.screen.locate("btn_max", timeout=0.2)
                    if m is not None:
                        self._detail_ui_cache["btn_max"] = m
                    return True, ms, "cached"
                # Invalidate and fall through
                self._pos_cache.pop(goods.id, None)
            except Exception:
                try:
                    self._pos_cache.pop(goods.id, None)
                except Exception:
                    pass
        # Template matching path (unified: measure full open time including verify)
        t_begin = time.perf_counter()
        src = "none"
        if goods.image_path and os.path.exists(goods.image_path):
            try:
                box = self._pg_locate_image(
                    goods.image_path, confidence=0.80, timeout=2.0
                )
                if box is not None:
                    self._pos_cache[goods.id] = box
                    self.screen.click_center(box)
                    # Verify detail opened; halve wait similar to cached path
                    buy_box = self.screen.locate("btn_buy", timeout=0.3)
                    ok = buy_box is not None
                    ms = int((time.perf_counter() - t_begin) * 1000.0)
                    src = "match"
                    if ok:
                        # Reset and warm-fill UI cache for this detail view
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
                # Not found within locate timeout
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

    # ----- price read -----
    def _read_currency_avg_price(
        self, item_disp: str, purchased_str: str
    ) -> Optional[int]:
        # Follow the same approach as GUI "货币价格区域-模版预览":
        # - Match currency icon on full screen
        # - Take the top match as average price row
        # - OCR right-side ROI with configured width
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
            scr = np.array(scr_img)[:, :, ::-1].copy()  # BGR
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
        # Top match is the average price row
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
            self._log(item_disp, purchased_str, "平均单价ROI尺寸无效")
            return None
        crop = scr[y1:y2, x1:x2]
        # Scale
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
        # Binarize (best-effort)
        try:
            g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
            _thr, thb = cv2.threshold(g, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            oimg = _PIL.fromarray(thb)
        except Exception:
            try:
                oimg = _PIL.fromarray(crop[:, :, ::-1])
            except Exception:
                oimg = None

        # Debug save ROI (raw and bin) to images/debug
        try:
            import os as _os

            _os.makedirs("images/debug", exist_ok=True)
            ts = (
                time.strftime("%Y%m%d_%H%M%S") + f"_{int((time.time() % 1) * 1000):03d}"
            )
            safe_item = (item_disp or "item").replace("/", "-").replace("\\", "-")
            raw_path = f"images/debug/currency_avg_raw_{safe_item}_{ts}.png"
            bin_path = f"images/debug/currency_avg_bin_{safe_item}_{ts}.png"
            try:
                cv2.imwrite(raw_path, crop)
            except Exception:
                pass
            if oimg is not None:
                try:
                    oimg.save(bin_path)
                except Exception:
                    pass
        except Exception:
            pass
        # OCR with engine preference from currency_area, fallback to avg_price_area
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
                txt = read_text(
                    oimg, engine="tesseract", grayscale=True, tess_config=cfg
                )
            else:
                ocfg = self.cfg.get("umi_ocr") or {}
                txt = read_text(
                    oimg,
                    engine="umi",
                    grayscale=True,
                    umi_base_url=str(ocfg.get("base_url", "http://127.0.0.1:1224")),
                    umi_timeout=float(ocfg.get("timeout_sec", 2.5) or 2.5),
                    umi_options=dict(ocfg.get("options", {}) or {}),
                )
        except Exception as e:
            # On Umi failure: terminate by raising a fatal error
            if eng in ("umi", "umi-ocr", "umiocr"):
                self._log(
                    item_disp,
                    purchased_str,
                    f"Umi-OCR 失败: {e} | eng={eng} roi=({x1},{y1},{x2},{y2}) scale={sc:.2f} score={_s:.2f}",
                )
                raise FatalOcrError(str(e))
            else:
                self._log(
                    item_disp,
                    purchased_str,
                    f"CurrencyOCR失败: {e} | eng={eng} roi=({x1},{y1},{x2},{y2}) scale={sc:.2f} score={_s:.2f}",
                )
                return None
        val = _parse_price_text(txt or "")
        if val is None or val <= 0:
            self._log(
                item_disp,
                purchased_str,
                f"CurrencyOCR解析失败：'{(txt or '').strip()[:64]}' | eng={eng} roi=({x1},{y1},{x2},{y2}) scale={sc:.2f} score={_s:.2f}",
            )
            return None
        # Log key parameters for debugging
        self._log(
            item_disp,
            purchased_str,
            f"CurrencyOCR成功 val={val} eng={eng} roi=({x1},{y1},{x2},{y2}) scale={sc:.2f} score={_s:.2f}",
        )
        return int(val)

    def _read_avg_unit_price(
        self, goods: Goods, item_disp: str, purchased_str: str
    ) -> Optional[int]:
        # Anchor at Buy button (same as 平均单价预览)
        t_btn = time.perf_counter()
        # Prefer cached buy button; fallback to a short locate
        buy_box = self._detail_ui_cache.get("btn_buy") or self.screen.locate(
            "btn_buy", timeout=0.3
        )
        btn_ms = int((time.perf_counter() - t_btn) * 1000.0)
        if buy_box is None:
            self._log(
                item_disp,
                purchased_str,
                f"未匹配到‘购买按钮’模板，无法定位平均价格区域 match={btn_ms}ms",
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
        # Clamp to screen bounds
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
            self._log(item_disp, purchased_str, "平均单价ROI计算失败（尺寸无效）")
            return None
        roi = (x_left, y_top, width, height)
        img = self.screen.screenshot_region(roi)
        if img is None:
            self._log(item_disp, purchased_str, "平均单价ROI截屏失败")
            return None
        # Split ROI horizontally: top=平均价, bottom=合计
        try:
            w0, h0 = img.size
        except Exception:
            self._log(item_disp, purchased_str, "平均单价ROI尺寸无效")
            return None
        if h0 < 2:
            self._log(item_disp, purchased_str, "平均单价ROI高度过小，无法二分")
            return None
        mid_h = h0 // 2
        img_top = img.crop((0, 0, w0, mid_h))
        img_bot = img.crop((0, mid_h, w0, h0))
        # Optional scale (clamped similar to preview)
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
        # Binarize both halves (prefer cv2; fallback PIL)
        bin_top = None
        bin_bot = None
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
                bin_top = img_top.convert("L").point(lambda p: 255 if p > 128 else 0)
            except Exception:
                bin_top = img_top
            try:
                bin_bot = img_bot.convert("L").point(lambda p: 255 if p > 128 else 0)
            except Exception:
                bin_bot = img_bot
        # Debug save ROI images (top/bottom)
        try:
            import os as _os

            _os.makedirs("images/debug", exist_ok=True)
            ts = (
                time.strftime("%Y%m%d_%H%M%S") + f"_{int((time.time() % 1) * 1000):03d}"
            )
            safe_item = (item_disp or "item").replace("/", "-").replace("\\", "-")
            raw_top = f"images/debug/avg_top_raw_{safe_item}_{ts}.png"
            bin_top_path = f"images/debug/avg_top_bin_{safe_item}_{ts}.png"
            raw_bot = f"images/debug/avg_bot_raw_{safe_item}_{ts}.png"
            bin_bot_path = f"images/debug/avg_bot_bin_{safe_item}_{ts}.png"
            try:
                img_top.save(raw_top)
                img_bot.save(raw_bot)
            except Exception:
                pass
            try:
                (bin_top or img_top).save(bin_top_path)
                (bin_bot or img_bot).save(bin_bot_path)
            except Exception:
                pass
        except Exception:
            pass
        # OCR on top half only (average price)
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
                txt = read_text(
                    (bin_top or img_top),
                    engine="tesseract",
                    grayscale=True,
                    tess_config=cfg,
                )
                ocr_ms = int((time.perf_counter() - t_ocr) * 1000.0)
            else:
                ocfg = self.cfg.get("umi_ocr") or {}
                t_ocr = time.perf_counter()
                txt = read_text(
                    (bin_top or img_top),
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
                    f"Umi-OCR 失败: {e} | eng={eng} roi=({x_left},{y_top},{x_left + width},{y_top + mid_h}) scale={sc:.2f} btn_ms={btn_ms} ocr_ms={ocr_ms if ocr_ms >= 0 else '-'}",
                )
                raise FatalOcrError(str(e))
            else:
                self._log(
                    item_disp,
                    purchased_str,
                    f"AvgOCR失败: {e} | eng={eng} roi=({x_left},{y_top},{x_left + width},{y_top + mid_h}) scale={sc:.2f} btn_ms={btn_ms} ocr_ms={ocr_ms if ocr_ms >= 0 else '-'}",
                )
                return None
        val = _parse_price_text(txt or "")
        if val is None or val <= 0:
            self._log(
                item_disp,
                purchased_str,
                f"AvgOCR解析失败：'{(txt or '').strip()[:64]}' | eng={eng} roi=({x_left},{y_top},{x_left + width},{y_top + mid_h}) scale={sc:.2f} btn_ms={btn_ms} ocr_ms={ocr_ms if ocr_ms >= 0 else '-'}",
            )
            return None
        self._log(
            item_disp,
            purchased_str,
            f"AvgOCR成功 val={val} eng={eng} roi=({x_left},{y_top},{x_left + width},{y_top + mid_h}) scale={sc:.2f} btn_ms={btn_ms} ocr_ms={ocr_ms}",
        )
        return int(val)

    # ----- buy flow -----
    def execute_once(
        self,
        goods: Goods,
        task: Dict[str, Any],
        purchased_so_far: int,
        *,
        skip_search: bool = False,
    ) -> Tuple[int, bool]:
        """Run one purchase attempt on a single goods entry.

        Returns (purchased_in_this_attempt, should_continue_loop).
        """
        # Prepare context for logging
        item_disp = goods.name or goods.search_name or str(task.get("item_name", ""))
        target_total = int(task.get("target_total", 0) or 0)
        purchased_str = f"{purchased_so_far}/{target_total}"
        # Optionally perform navigation + search if requested
        if not skip_search:
            if not self._nav_to_search():
                self._log(item_disp, purchased_str, "未能进入市场或定位搜索框")
                return 0, True
            query = (goods.search_name or "").strip()
            if not query:
                self._log(item_disp, purchased_str, "无有效 search_name，跳过本次")
                return 0, False
            if not self._search(query):
                self._log(item_disp, purchased_str, "未能点击‘搜索’按钮")
                return 0, True
            self._log(
                item_disp,
                purchased_str,
                f"匹配 btn_market → input_search({query}) → btn_search 成功",
            )
        # Open detail by goods image (prefer cached pos when skipping search)
        ok_open, cost_ms, src = self._open_goods_detail(
            goods, prefer_cached=bool(skip_search)
        )
        if not ok_open:
            if src == "cached":
                self._log(
                    item_disp,
                    purchased_str,
                    f"缓存坐标无效，未进入详情 open={cost_ms}ms",
                )
            else:
                # Template路径失败仍保留匹配耗时语义
                self._log(
                    item_disp, purchased_str, f"未匹配到商品图片模板 match={cost_ms}ms"
                )
            return 0, True
        if src == "cached":
            self._log(
                item_disp, purchased_str, f"使用缓存坐标进入详情 open={cost_ms}ms"
            )
        else:
            # 统一指标：模板路径成功也输出 open=...（包含匹配+验证）
            self._log(
                item_disp,
                purchased_str,
                f"匹配 goods.image_path 模板并进入详情 open={cost_ms}ms",
            )

        # Read price from currency area
        unit_price = self._read_avg_unit_price(goods, item_disp, purchased_str)
        if unit_price is None or unit_price <= 0:
            # Close detail if possible
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
        # Log price and thresholds
        self._log(
            item_disp,
            purchased_str,
            f"平均单价={unit_price}，阈值≤{limit}(+{int(prem)}%)",
        )
        # Determine quantity
        target_total = int(task.get("target_total", 0) or 0)
        max_per_order = max(1, int(task.get("max_per_order", 120) or 120))
        remain = (
            max(0, target_total - purchased_so_far)
            if target_total > 0
            else max_per_order
        )

        # Base quantity rules
        q = 1
        # Restock path
        if restock > 0 and unit_price <= restock:
            if (goods.big_category or "").strip() == "弹药":
                # Use Max button if present
                b = self._detail_ui_cache.get("btn_max") or self.screen.locate(
                    "btn_max", timeout=0.3
                )
                if b is not None:
                    self.screen.click_center(b)
                    q = min(120, max_per_order, max(1, remain))
                else:
                    q = min(120, max_per_order, max(1, remain))
            else:
                # Non-ammo: set 5 via quantity input if configured
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
            # Use default or 1 (we don't change quantity by default)
            q = min(1, max_per_order, max(1, remain))

        # Threshold check (if thr=0 -> always buy)
        ok_to_buy = (limit <= 0) or (unit_price <= limit)
        if not ok_to_buy:
            # Close detail and retry later
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

        # Submit
        # Halve wait here as well to reduce intermediate delays
        b = self._detail_ui_cache.get("btn_buy") or self.screen.locate(
            "btn_buy", timeout=0.3
        )
        if b is None:
            c = self._detail_ui_cache.get("btn_close") or self.screen.locate(
                "btn_close", timeout=0.3
            )
            if c is not None:
                self.screen.click_center(c)
            self._log(item_disp, purchased_str, "未找到‘购买’提交按钮")
            return 0, True
        self.screen.click_center(b)

        # Result polling (300–600ms loop, up to ~1.2s)
        t_end = time.time() + 1.2
        got = "unknown"
        while time.time() < t_end:
            if self.screen.locate("buy_ok", timeout=0.0) is not None:
                got = "ok"
                break
            if self.screen.locate("buy_fail", timeout=0.0) is not None:
                got = "fail"
                break
            _sleep(0.1)

        if got == "ok":
            # Close detail
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
        elif got == "fail":
            c = self._detail_ui_cache.get("btn_close") or self.screen.locate(
                "btn_close", timeout=0.3
            )
            if c is not None:
                self.screen.click_center(c)
            self._log(item_disp, purchased_str, "购买失败，已关闭详情")
            return 0, True
        else:
            # Unknown: close and continue
            c = self._detail_ui_cache.get("btn_close") or self.screen.locate(
                "btn_close", timeout=0.3
            )
            if c is not None:
                self.screen.click_center(c)
            self._log(item_disp, purchased_str, "结果未知，已关闭详情")
            return 0, True


# ------------------------------ Scheduler/Runner ------------------------------


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
    """Whether now (local time) is within [start,end], supporting cross-day windows.

    Empty start/end -> always true.
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
    # Cross-day (e.g., 22:00~06:00)
    return now_min >= smin or now_min <= emin


class TaskRunner:
    """High-level scheduler that executes buying tasks per final design doc.

    - Supports two modes: round-robin duration and time-window selection.
    - Implements unified purchase flow with OCR (currency area).
    - Provides start/pause/stop controls and structured logging.
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

        # Snapshot configs
        self.cfg = load_config(cfg_path)
        self.tasks_data = json.loads(json.dumps(tasks_data or {"tasks": []}))
        self.goods_map: Dict[str, Goods] = self._load_goods(goods_path)

        # Derived helpers
        step_delay = float(
            ((self.tasks_data.get("step_delays") or {}).get("default", 0.01)) or 0.01
        )
        self.screen = ScreenOps(self.cfg, step_delay=step_delay)
        self.buyer = Buyer(self.cfg, self.screen, self._relay_log)

        # Mode and restart
        self.mode = str(self.tasks_data.get("task_mode", "time"))
        try:
            self.restart_every_min = int(
                self.tasks_data.get("restart_every_min", 0) or 0
            )
        except Exception:
            self.restart_every_min = 0
        self._next_restart_ts: Optional[float] = None

    # ----- public API -----
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
        # Invalidate cached item positions so resume will re-match safely
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

    # ----- internal helpers -----
    def _relay_log(self, s: str) -> None:
        try:
            self.on_log(s)
        except Exception:
            pass

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
        # Use new tolerant launch flow inside Buyer
        return self.buyer._ensure_ready_v2()

    def _should_restart_now(self) -> bool:
        if self.restart_every_min <= 0:
            return False
        if self._next_restart_ts is None:
            self._next_restart_ts = time.time() + self.restart_every_min * 60
            return False
        return time.time() >= self._next_restart_ts

    def _do_soft_restart(self) -> None:
        # Try to exit gracefully via buttons; otherwise attempt process restart by relaunch
        self._relay_log(f"【{_now_label()}】【全局】【-】：到达重启周期，尝试重启游戏…")
        # Clear cached positions before restart
        try:
            self.buyer.clear_pos()
        except Exception:
            pass
        # Click back/settings/exit if templates provided
        for key in ("btn_back", "btn_settings", "btn_exit", "btn_exit_confirm"):
            box = self.screen.locate(key, timeout=0.8)
            if box is not None:
                self.screen.click_center(box)
                _sleep(0.3)
        # Re-run readiness
        ok = self._ensure_ready()
        if ok:
            self._relay_log(f"【{_now_label()}】【全局】【-】：已重启并回到市场")
        else:
            self._relay_log(f"【{_now_label()}】【全局】【-】：重启失败，未回到市场")
        # Reset timer window (do not count restart time towards task duration)
        self._next_restart_ts = time.time() + max(1, self.restart_every_min) * 60

    # ----- main loop -----
    def _run(self) -> None:
        if not self._ensure_ready():
            return
        # Normalize tasks
        tasks: List[Dict[str, Any]] = list(self.tasks_data.get("tasks", []) or [])
        # Stable order for round-robin and time-window selection (order asc)
        try:
            tasks.sort(key=lambda d: int(d.get("order", 0)))
        except Exception:
            pass
        # Validate tasks: require item_id -> goods with non-empty search_name and image_path file
        self._validate_tasks(tasks)
        # Initialize progress fields
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
        # Cycle through enabled tasks by order
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
            # Skip if target reached
            target = int(t.get("target_total", 0) or 0)
            purchased = int(t.get("purchased", 0) or 0)
            if target > 0 and purchased >= target:
                idx += 1
                continue

            # Start segment
            duration_min = int(t.get("duration_min", 10) or 10)
            t["status"] = "running"
            seg_start = time.time()
            seg_end = seg_start + duration_min * 60
            item_disp = str(t.get("item_name", ""))
            self._relay_log(
                f"【{_now_label()}】【{item_disp}】【{purchased}/{target}】：开始片段，时长 {duration_min} 分钟"
            )
            # Perform navigation + search once per segment
            gid = str(t.get("item_id", ""))
            goods = self.goods_map.get(gid)
            if not goods:
                idx += 1
                continue
            # Ensure search context; clear cached pos for fresh match at segment start
            try:
                self.buyer.clear_pos(goods.id)
                _ = self.buyer.execute_once(goods, t, purchased, skip_search=False)
            except FatalOcrError as e:
                self._relay_log(
                    f"【{_now_label()}】【全局】【-】：Umi-OCR 失败（片段初始化），终止任务：{e}"
                )
                self._stop.set()
                break
            # Execute purchase loop within segment (skip re-search)
            search_ready = True
            while not self._stop.is_set() and time.time() < seg_end:
                # Pause handling
                while self._pause.is_set() and not self._stop.is_set():
                    _sleep(0.2)
                if self._stop.is_set():
                    break

                # Restart check at safe points (between detail closes)
                if self._should_restart_now():
                    self._do_soft_restart()
                    search_ready = False
                    # fall through to re-ensure search context
                if not search_ready:
                    # Re-do navigation + search exactly once
                    self.buyer.clear_pos(goods.id)
                    _ = self.buyer.execute_once(goods, t, purchased, skip_search=False)
                    search_ready = True

                # Execute one attempt
                try:
                    got, _cont = self.buyer.execute_once(
                        goods, t, purchased, skip_search=True
                    )
                except FatalOcrError as e:
                    self._relay_log(
                        f"【{_now_label()}】【全局】【-】：Umi-OCR 失败（循环中），终止任务：{e}"
                    )
                    self._stop.set()
                    break
                if got > 0:
                    purchased += int(got)
                    t["purchased"] = purchased
                    # Notify UI
                    try:
                        self.on_task_update(tasks.index(t), dict(t))
                    except Exception:
                        pass
                if not _cont:
                    break
                _sleep(0.05)

            # End segment bookkeeping
            elapsed = int((time.time() - seg_start) * 1000)
            t["executed_ms"] = int(t.get("executed_ms", 0) or 0) + max(0, elapsed)
            t["status"] = "idle"
            self._relay_log(
                f"【{_now_label()}】【{item_disp}】【{purchased}/{target}】：片段结束，累计 {elapsed} ms"
            )
            idx += 1

    def _run_time_window(self, tasks: List[Dict[str, Any]]) -> None:
        # Poll every 1–3s when no active window
        while not self._stop.is_set():
            # Pause handling (global)
            while self._pause.is_set() and not self._stop.is_set():
                _sleep(0.2)
            if self._stop.is_set():
                break

            # Choose first task whose time window contains now
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
            # Skip reached target
            target = int(t.get("target_total", 0) or 0)
            purchased = int(t.get("purchased", 0) or 0)
            if target > 0 and purchased >= target:
                _sleep(0.8)
                continue

            # Compute window end ts
            te = str(t.get("time_end", ""))
            # For display purposes only; loop will exit when window no longer matches
            self._relay_log(
                f"【{_now_label()}】【{t.get('item_name', '')}】【{purchased}/{target}】：进入时间窗口执行（结束 {te or '—:—'}）"
            )

            # Ensure we have search context once when entering window
            gid = str(t.get("item_id", ""))
            goods = self.goods_map.get(gid)
            if not goods:
                _sleep(1.0)
                continue
            try:
                _ = self.buyer.execute_once(goods, t, purchased, skip_search=False)
            except FatalOcrError as e:
                self._relay_log(
                    f"【{_now_label()}】【全局】【-】：Umi-OCR 失败（进入窗口），终止任务：{e}"
                )
                self._stop.set()
                break

            # Execute until window ends or stop/pause
            search_ready = True
            while not self._stop.is_set():
                # Window check
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
                # Restart check
                if self._should_restart_now():
                    self._do_soft_restart()
                    search_ready = False
                if not search_ready:
                    # Re-establish search context after restart
                    _ = self.buyer.execute_once(goods, t, purchased, skip_search=False)
                    search_ready = True
                # Attempt one purchase (do not re-search)
                try:
                    got, _cont = self.buyer.execute_once(
                        goods, t, purchased, skip_search=True
                    )
                except FatalOcrError as e:
                    self._relay_log(
                        f"【{_now_label()}】【全局】【-】：Umi-OCR 失败（窗口循环），终止任务：{e}"
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
        """Mark tasks as valid/invalid and log issues.

        A valid task must:
        - Have item_id mapping to goods.json entry
        - That goods entry must have non-empty search_name
        - goods.image_path must exist on disk (for template matching)
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
