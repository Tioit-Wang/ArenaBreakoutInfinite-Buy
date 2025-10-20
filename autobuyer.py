import threading
import time
from typing import Callable, Optional, List, Dict, Any, Tuple
import os
import subprocess
import shlex
import uuid

from auto_clicker import MappingAutomator, ImageBasedAutomator
from app_config import load_config  # type: ignore

# NOTE: Single-item AutoBuyer has been removed from public API. The legacy
# implementation is kept under a private name for reference but is unused.
AutoBuyer = None  # type: ignore

class _RemovedAutoBuyer:
    """Background worker that navigates, monitors price and buys when below threshold.

    Minimal MVP wiring to existing MappingAutomator and price_reader.
    """

    def __init__(
        self,
        *,
        item_name: str,
        price_threshold: int,
        target_total: int,
        max_per_order: int = 120,
        wait_time: float = 0.1,
        on_log: Optional[Callable[[str], None]] = None,
        allow_image_fallback: bool = True,
    ) -> None:
        self.item_name = item_name
        self.price_threshold = int(price_threshold)
        self.target_total = int(target_total)
        self.max_per_order = max(1, int(max_per_order))
        self.on_log = on_log or (lambda s: None)
        # History identity (single-item mode has no config id)
        try:
            self._hist_item_id = f"AUTO::{str(item_name)}"
        except Exception:
            self._hist_item_id = f"AUTO::{time.time()}"

        img_auto = ImageBasedAutomator(image_dir="images", confidence=0.85, wait_time=wait_time)
        # Load runtime config first to pass into automator
        try:
            self.cfg: Dict[str, Any] = load_config("config.json")
        except Exception:
            self.cfg = {}
        self.automator = MappingAutomator(
            config=self.cfg,
            wait_time=wait_time,
            image_automator=img_auto,
            allow_image_fallback=allow_image_fallback,
        )

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._purchased = 0
        # default quantity per successful buy (single-item mode)
        try:
            self.default_buy_qty = int((self.cfg.get("purchase", {}) or {}).get("default_buy_qty", 1))
        except Exception:
            self.default_buy_qty = 1

        # Runtime config for templates and ROI settings already loaded above
        self._easyocr_reader = None  # lazy init when using EasyOCR
        self._paddleocr_reader = None  # lazy init when using PaddleOCR

    def log(self, msg: str) -> None:
        self.on_log(msg)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        # propagate stop event to automators for immediate cancellation
        try:
            self.automator._stop_event = self._stop
            if getattr(self.automator, "image_automator", None) is not None:
                self.automator.image_automator._stop_event = self._stop
        except Exception:
            pass
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def purchased(self) -> int:
        return self._purchased

    def _nav_to_search(self) -> bool:
        # Home (optional)
        self.automator._click_preferring_mapping(["首页按钮"], "btn_home.png", required=False)
        # Market
        if not self.automator._click_preferring_mapping(["市场按钮", "市场入口", "市场"], "btn_market.png", required=True):
            self.log("未能进入【市场】，终止。")
            return False
        # Search box
        if self.automator._click_preferring_mapping(["市场搜索栏", "搜索框", "搜索输入"], "input_search.png", required=True):
            self.automator.type_text(self.item_name, clear_first=True)
        else:
            self.log("未能定位【搜索框】，终止。")
            return False
        # Search button
        if not self.automator._click_preferring_mapping(["市场搜索按钮", "搜索按钮"], "btn_search.png", required=True):
            self.log("未能点击【搜索按钮】，终止。")
            return False
        self.log("已完成搜索，等待结果…")
        if self._stop.wait(0.02):
            return False
        return True

    def _run(self) -> None:
        # 采用新的 ROI+模板 监控循环
        self._run_avg_roi_loop()
        return


    def _run_avg_roi_loop(self) -> None:
        """New monitoring loop using Avg Price ROI + template success check."""
        self._purchased = 0
        if not self._nav_to_search():
            return
        opened = False
        while not self._stop.is_set() and self._purchased < self.target_total:
            if not opened:
                if not self.automator._click_preferring_mapping(["第一个商品", "第一个商品位置", "第1个商品"], None, required=True):
                    self.log("未定位到第一个商品，稍后重试。")
                    # 上层将重新导航
                    break
                opened = True

            region = self._avg_price_roi_region()
            if region is None:
                self.log("未定位到‘购买’按钮或 ROI，关闭详情后立即重试。")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.01):
                    break
                continue

            unit_price = self._ocr_unit_price_from_region(region)
            if unit_price is None:
                self.log("未能识别到单价，关闭详情后立即重试。")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.01):
                    break
                continue

            self.log(f"单价: {unit_price}")
            # Suspiciously low price (<50% of threshold): treat as OCR error, skip logging and buying
            suspicious = False
            try:
                if int(self.price_threshold) > 0 and int(unit_price) < 0.5 * int(self.price_threshold):
                    suspicious = True
            except Exception:
                suspicious = False
            if not suspicious:
                # Append price history (best-effort)
                try:
                    from history_store import append_price  # type: ignore
                    append_price(self._hist_item_id, self.item_name, int(unit_price))
                except Exception:
                    pass
            else:
                # Consider as not suitable, close and retry
                self.log("价格异常(低于阈值50%)，视为识别错误，本轮放弃。")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.01):
                    break
                continue

            # Allow premium percentage if configured (default 0)
            try:
                premium_pct = float(getattr(self, 'price_premium_pct', 0) or 0)
            except Exception:
                premium_pct = 0.0
            allowed_max = int(round(self.price_threshold * (1.0 + max(0.0, premium_pct) / 100.0)))

            if unit_price <= allowed_max and self._purchased < self.target_total:
                # 直接购买
                if not self.automator._click_preferring_mapping(["购买按钮", "确认按钮", "提交订单"], "btn_buy.png", required=True):
                    self.log("点击购买失败，关闭详情后重试。")
                    self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    opened = False
                    if self._stop.wait(0.01):
                        break
                    continue
                if self._stop.wait(0.1):
                    break
                res = self._check_purchase_result(timeout=0.3, poll=0.01)
                if res is None:
                    # Fallback recheck to avoid missing slightly delayed success
                    res = self._check_purchase_result(timeout=0.6)
                if res is None:
                    # Fallback recheck to avoid missing slightly delayed success
                    res = self._check_purchase_result(timeout=0.6)
                if res == "success":
                    self._purchased += int(getattr(self, 'default_buy_qty', 1) or 1)
                    self.log(f"购买成功，累计已购: {self._purchased}/{self.target_total}")
                    # Append purchase history (best-effort)
                    try:
                        from history_store import append_purchase  # type: ignore
                        append_purchase(self._hist_item_id, self.item_name, int(unit_price), int(getattr(self, 'default_buy_qty', 1) or 1))
                    except Exception:
                        pass
                    # 移动鼠标至右上角以避免遮挡，再进行后续模板/ROI操作
                    self._move_cursor_to_top_right()
                    # 软点击关闭成功弹窗
                    self._close_success_overlay()
                    # 等待 ROI 刷新，继续在详情内循环
                    self._wait_roi_refresh(region, base_delay=0.02, timeout=0.05, poll=0.01)
                    continue
                else:
                    self.log(f"购买未成功({res})，关闭详情后重试。")
                    self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    opened = False
                    if self._stop.wait(0.01):
                        break
                    continue
            else:
                # 不合适：关闭并在10ms后重试
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.01):
                    break
                continue

        if self._purchased >= self.target_total:
            self.log("目标购买数已达成，停止。")
        else:
            self.log("已停止。")

    # ---------- Helpers: ROI / OCR / Template ----------
    def _tpl_path_conf(self, basename_hint: str) -> Tuple[str, float]:
        """Return (path, confidence) for a template whose file name contains `basename_hint`.

        Falls back to images/<basename_hint>.png with default confidence.
        """
        try:
            tpls = self.cfg.get("templates", {}) if isinstance(self.cfg.get("templates"), dict) else {}
            for _name, _d in tpls.items():
                p = str((_d or {}).get("path", ""))
                if not p:
                    continue
                base = p.replace("\\", "/").split("/")[-1].lower()
                if basename_hint.lower() in base:
                    try:
                        conf = float((_d or {}).get("confidence", 0.85))
                    except Exception:
                        conf = 0.85
                    return p, conf
        except Exception:
            pass
        return (f"images/{basename_hint}.png", 0.85)

    def _avg_price_roi_region(self) -> Optional[Tuple[int, int, int, int]]:
        """Compute ROI (left, top, width, height) from Avg Price settings anchored to Buy button."""
        try:
            import pyautogui  # type: ignore
            from PIL import Image  # type: ignore
        except Exception:
            return None

        avg_cfg = self.cfg.get("avg_price_area", {}) if isinstance(self.cfg.get("avg_price_area"), dict) else {}
        try:
            dist = int(avg_cfg.get("distance_from_buy_top", 5))
            hei = max(1, int(avg_cfg.get("height", 45)))
        except Exception:
            dist, hei = 5, 45

        buy_path, buy_conf = self._tpl_path_conf("btn_buy")

        center = None
        box = None
        try:
            center = pyautogui.locateCenterOnScreen(buy_path, confidence=float(buy_conf))
        except Exception:
            pass
        if center is None:
            try:
                box = pyautogui.locateOnScreen(buy_path, confidence=float(buy_conf))
            except Exception:
                box = None

        if center is not None:
            try:
                tpl_w, tpl_h = Image.open(buy_path).size
            except Exception:
                tpl_w, tpl_h = 120, 40
            cx, cy = int(getattr(center, "x", 0)), int(getattr(center, "y", 0))
            b_left = int(cx - tpl_w // 2)
            b_top = int(cy - tpl_h // 2)
            b_w, b_h = int(tpl_w), int(tpl_h)
        elif box is not None:
            try:
                b_left, b_top = int(getattr(box, "left", 0)), int(getattr(box, "top", 0))
                b_w, b_h = int(getattr(box, "width", 0)), int(getattr(box, "height", 0))
            except Exception:
                return None
        else:
            return None

        if b_w <= 1 or b_h <= 1:
            return None

        y_bottom = b_top - dist
        y_top = y_bottom - hei
        x_left = b_left
        width = b_w

        try:
            scr_w, scr_h = pyautogui.size()
        except Exception:
            scr_w, scr_h = 2560, 1440
        y_top = max(0, min(scr_h - 2, int(y_top)))
        y_bottom = max(y_top + 1, min(scr_h - 1, int(y_bottom)))
        x_left = max(0, min(scr_w - 2, int(x_left)))
        width = max(1, min(int(width), scr_w - x_left))
        height = max(1, int(y_bottom - y_top))
        return (x_left, y_top, width, height)

    def _ensure_easyocr(self):
        if getattr(self, "_easyocr_reader", None) is not None:
            return self._easyocr_reader
        try:
            import easyocr  # type: ignore
            self._easyocr_reader = easyocr.Reader(['en'], gpu=False)
        except Exception:
            self._easyocr_reader = None
        return self._easyocr_reader

    def _ensure_paddleocr(self):
        if getattr(self, "_paddleocr_reader", None) is not None:
            return self._paddleocr_reader
        try:
            from paddleocr import PaddleOCR  # type: ignore
            self._paddleocr_reader = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)
        except Exception:
            self._paddleocr_reader = None
        return self._paddleocr_reader

    def _ocr_unit_price_from_region(self, region: Tuple[int, int, int, int]) -> Optional[int]:
        """Screenshot the region and OCR a single unit price (supports K/M)."""
        try:
            import pyautogui  # type: ignore
            import pytesseract  # type: ignore
            from price_reader import _maybe_init_tesseract  # type: ignore
        except Exception:
            return None

        try:
            _maybe_init_tesseract()
        except Exception:
            pass
        try:
            img = pyautogui.screenshot(region=region)
        except Exception:
            return None

        # Apply scale factor (0.6~2.5) before binarization
        avg_cfg = self.cfg.get("avg_price_area", {}) if isinstance(self.cfg.get("avg_price_area"), dict) else {}
        try:
            scale = float(avg_cfg.get("scale", 1.0))
        except Exception:
            scale = 1.0
        if not (0.6 <= scale <= 2.5):
            scale = 1.0
        if abs(scale - 1.0) > 1e-3:
            try:
                w, h = img.size
                from PIL import Image as _Image  # type: ignore
                img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), resample=getattr(_Image, 'LANCZOS', 1))
            except Exception:
                pass

        # Binarize (contrast boost) — keep preprocessing minimal
        bin_img = None
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
            arr = np.array(img)
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            # Otsu thresholding
            _thr, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            from PIL import Image as _Image  # type: ignore
            bin_img = _Image.fromarray(th)
        except Exception:
            try:
                # Fallback: PIL grayscale + fixed threshold
                from PIL import ImageOps as _ImageOps  # type: ignore
                g = img.convert("L")
                th = g.point(lambda p: 255 if p > 128 else 0)
                bin_img = th
            except Exception:
                bin_img = img

        # Choose OCR engine
        engine = str(avg_cfg.get("ocr_engine", "tesseract")).lower()
        raw_text = ""
        if engine == "easyocr":
            reader = self._ensure_easyocr()
            if reader is not None:
                try:
                    import numpy as _np  # type: ignore
                    arr = _np.array(bin_img or img)
                    texts = reader.readtext(arr, detail=0)
                    raw_text = "\n".join(map(str, texts))
                except Exception:
                    raw_text = ""
            else:
                engine = "tesseract"
        elif engine in ("paddle", "paddleocr"):
            reader = self._ensure_paddleocr()
            if reader is not None:
                try:
                    import numpy as _np  # type: ignore
                    arr = _np.array(bin_img or img)
                    res = reader.ocr(arr, cls=True)
                    texts: list[str] = []
                    try:
                        if isinstance(res, list) and len(res) > 0 and isinstance(res[0], list) and len(res[0]) > 0 and isinstance(res[0][0], list):
                            for e in res[0]:
                                if isinstance(e, list) and len(e) >= 2 and isinstance(e[1], (list, tuple)):
                                    t = e[1][0]
                                    if t:
                                        texts.append(str(t))
                        else:
                            for e in (res or []):
                                if isinstance(e, list) and len(e) >= 2 and isinstance(e[1], (list, tuple)):
                                    t = e[1][0]
                                    if t:
                                        texts.append(str(t))
                    except Exception:
                        pass
                    raw_text = "\n".join(texts)
                except Exception:
                    raw_text = ""
            else:
                engine = "tesseract"
        if not raw_text:
            allow = str(avg_cfg.get("ocr_allowlist", "0123456789KM"))
            need = "KMkm"
            allow_ex = allow + "".join(ch for ch in need if ch not in allow)
            cfg = f"--oem 3 --psm 6 -c tessedit_char_whitelist={allow_ex}"
            try:
                raw_text = pytesseract.image_to_string(bin_img or img, config=cfg) or ""
            except Exception:
                return None

        lines = [ln.strip() for ln in str(raw_text).splitlines() if ln.strip()]

        def parse_num(s: str) -> Optional[int]:
            up = str(s).upper()
            mult = 1_000_000 if ("M" in up) else (1000 if ("K" in up) else 1)
            digits = "".join(ch for ch in up if ch.isdigit())
            if not digits:
                return None
            try:
                return int(digits) * mult
            except Exception:
                return None

        for ln in lines:
            val = parse_num(ln)
            if val is not None:
                return val

        import re
        toks = re.findall(r"[0-9]+[KMkm]?", str(raw_text))
        vals: List[int] = []
        for tk in toks:
            v = parse_num(tk)
            if v is not None:
                vals.append(v)
        if vals:
            return min(vals)
        return None

    def _sample_avg_price(
        self,
        region: Tuple[int, int, int, int],
        n: int,
        restock_price: int,
    ) -> tuple[Optional[int], bool, Optional[int]]:
        """Collect up to n valid OCR prices and return (avg, hit_restock, hit_price).

        Used by MultiBuyer normal path when price_mode == 'average'. If any sampled
        price <= restock_price (and restock_price > 0), immediately signal restock.
        """
        try:
            n = int(n)
        except Exception:
            n = 100
        n = max(1, min(n, 500))
        try:
            rp = int(restock_price)
        except Exception:
            rp = 0
        vals: list[int] = []
        attempts = 0
        max_attempts = max(n, int(n * 2))
        last_hit: Optional[int] = None
        while not self._stop.is_set() and len(vals) < n and attempts < max_attempts:
            attempts += 1
            v = self._ocr_unit_price_from_region(region)
            if v is None:
                if self._stop.wait(0.005):
                    break
                continue
            if rp > 0 and v <= rp:
                last_hit = int(v)
                return (None, True, last_hit)
            vals.append(int(v))
            if self._stop.wait(0.003):
                break
        if not vals:
            return (None, False, None)
        try:
            avg = int(round(sum(vals) / float(len(vals))))
        except Exception:
            avg = None
        return (avg, False, None)

    def _prescan_average_threshold(
        self,
        idx: int,
        it: Dict[str, Any],
        avg_samples: int,
        avg_subtract: int,
        restock_price: int,
    ) -> Optional[int]:
        """Run a monitoring phase to collect avg_samples via normal open→read→record→close cycles.

        - Does NOT perform normal purchases during prescan.
        - If any read price <= restock_price (and restock_price > 0), executes the restock flow immediately.
        - Appends each read price to history.
        - Returns a computed threshold: max(0, round(avg) - avg_subtract) from history records since start.
        """
        name = str(it.get("item_name", ""))
        try:
            item_id = str(it.get("id", ""))
        except Exception:
            item_id = ""
        try:
            restock_v = int(restock_price)
        except Exception:
            restock_v = 0
        try:
            avg_n = max(1, min(int(avg_samples), 500))
        except Exception:
            avg_n = 100
        start_ts = time.time()
        collected = 0
        opened = False
        self.log(f"[{name}] 开始平均值监控：目标 {avg_n} 次（仅记录不购买）…")
        while not self._stop.is_set() and collected < avg_n:
            # Already done for this item?
            try:
                purchased_now = int(it.get("purchased", 0))
            except Exception:
                purchased_now = 0
            try:
                target_total = int(it.get("target_total", 0))
            except Exception:
                target_total = 0
            if purchased_now >= target_total:
                self.log(f"[{name}] 已达目标 {purchased_now}/{target_total}，终止平均值监控。")
                break
            # Ensure detail is opened
            if not opened:
                if not self.automator._click_preferring_mapping([
                    "第一个商品", "第一个商品位置", "第1个商品"
                ], None, required=True):
                    self.log(f"[{name}] 未定位到第一个商品（平均监控），稍后重试…")
                    break
                opened = True
            # Read ROI
            region = self._avg_price_roi_region()
            if region is None:
                self.log(f"[{name}] 平均监控：未定位到‘购买’按钮或 ROI，关闭详情后重试…")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.01):
                    break
                continue
            price = self._ocr_unit_price_from_region(region)
            if price is None:
                self.log(f"[{name}] 平均监控：未能解析价格，关闭详情后重试…")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.01):
                    break
                continue
            self.log(f"[{name}] 平均监控：读取价格 {price}")
            # Restock hit during prescan
            if restock_v > 0 and int(price) <= restock_v and purchased_now < target_total:
                self.log(f"[{name}] 平均监控命中补货：尝试最大数量直接购买…")
                clicked_max = False
                try:
                    clicked_max = self.automator._click_preferring_mapping([
                        "数量最大按钮", "最大", "MAX", "Max", "最大数量"
                    ], "btn_max.png", required=False)
                except Exception:
                    clicked_max = False
                if not clicked_max:
                    try:
                        if self.automator._click_preferring_mapping(["数量输入框", "数量输入"], "input_quantity.png", required=True):
                            self.automator.type_text(str(int(it.get("max_button_qty", 120) or 120)), clear_first=True)
                            clicked_max = True
                        else:
                            clicked_max = False
                    except Exception:
                        clicked_max = False
                if clicked_max and self.automator._click_preferring_mapping(["购买按钮", "确认按钮", "提交订单"], "btn_buy.png", required=True):
                    if self._stop.wait(0.01):
                        return None
                    res = self._check_purchase_result(timeout=0.6, poll=0.01)
                    if res == "success":
                        # Update purchased and record purchase history
                        try:
                            remaining = max(0, int(target_total) - int(it.get("purchased", 0)))
                        except Exception:
                            remaining = 0
                        inc = max(1, int(it.get("max_button_qty", 120) or 120))
                        if remaining > 0:
                            inc = min(inc, remaining)
                        it["purchased"] = int(it.get("purchased", 0)) + inc
                        self.log(f"[{name}] 补货购买成功(+{inc})，累计 {it['purchased']}/{int(it.get('target_total', 0))}")
                        try:
                            from history_store import append_purchase, append_price  # type: ignore
                            append_purchase(str(item_id), name, int(price), int(inc))
                            append_price(str(item_id), name, int(price))
                        except Exception:
                            pass
                        try:
                            self.on_item_update(idx, dict(it))
                        except Exception:
                            pass
                        # Close detail to align with monitoring pattern
                        self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                        opened = False
                        if self._stop.wait(0.01):
                            return None
                        continue
                    else:
                        self.log(f"[{name}] 补货购买未成功({res})，关闭详情后重试。")
                        self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                        opened = False
                        if self._stop.wait(0.01):
                            return None
                        continue
                else:
                    self.log(f"[{name}] 补货：未能设置最大数量或点击购买，关闭后重试。")
                    self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    opened = False
                    if self._stop.wait(0.01):
                        return None
                    continue
            # Append to price history (best-effort) and close without purchasing
            try:
                from history_store import append_price  # type: ignore
                append_price(str(item_id), name, int(price))
            except Exception:
                pass
            # Close detail then proceed to next read
            self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
            opened = False
            collected += 1
            if self._stop.wait(0.01):
                break

        # Compute average from history since start
        try:
            from history_store import query_price  # type: ignore
            recs = query_price(str(item_id), float(start_ts))
            if recs:
                last = recs[-avg_n:]
                vals = [int(r.get("price", 0)) for r in last if int(r.get("price", 0)) > 0]
                if vals:
                    avg_val = int(round(sum(vals) / float(len(vals))))
                    thr = max(0, int(avg_val) - int(avg_subtract))
                    self.log(f"[{name}] 平均监控完成：{len(vals)} 条，均值 {avg_val}，阈值=均值-{int(avg_subtract)}→{thr}")
                    return int(thr)
        except Exception:
            pass
        self.log(f"[{name}] 平均监控完成：无有效样本，回退至配置阈值 {int(it.get('price_threshold', 0))}")
        try:
            return int(it.get("price_threshold", 0))
        except Exception:
            return None

    def _prescan_average_threshold(
        self,
        idx: int,
        it: Dict[str, Any],
        avg_samples: int,
        avg_subtract: int,
        restock_price: int,
    ) -> Optional[int]:
        """Run a monitoring phase to collect avg_samples via normal open→read→record→close cycles.

        - Does NOT perform normal purchases during prescan.
        - If any read price <= restock_price (and restock_price > 0), executes the restock flow immediately.
        - Appends each read price to history.
        - Returns a computed threshold: max(0, round(avg) - avg_subtract) from history records since start.
        """
        name = str(it.get("item_name", ""))
        try:
            item_id = str(it.get("id", ""))
        except Exception:
            item_id = ""
        try:
            restock_v = int(restock_price)
        except Exception:
            restock_v = 0
        try:
            avg_n = max(1, min(int(avg_samples), 500))
        except Exception:
            avg_n = 100
        start_ts = time.time()
        collected = 0
        opened = False
        self.log(f"[{name}] 开始平均值监控：目标 {avg_n} 次（仅记录不购买）…")
        while not self._stop.is_set() and collected < avg_n:
            # Already done for this item?
            try:
                purchased_now = int(it.get("purchased", 0))
            except Exception:
                purchased_now = 0
            try:
                target_total = int(it.get("target_total", 0))
            except Exception:
                target_total = 0
            if purchased_now >= target_total:
                self.log(f"[{name}] 已达目标 {purchased_now}/{target_total}，终止平均值监控。")
                break
            # Ensure detail is opened
            if not opened:
                if not self.automator._click_preferring_mapping([
                    "第一个商品", "第一个商品位置", "第1个商品"
                ], None, required=True):
                    self.log(f"[{name}] 未定位到第一个商品（平均监控），稍后重试…")
                    break
                opened = True
            # Read ROI
            region = self._avg_price_roi_region()
            if region is None:
                self.log(f"[{name}] 平均监控：未定位到‘购买’按钮或 ROI，关闭详情后重试…")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.01):
                    break
                continue
            price = self._ocr_unit_price_from_region(region)
            if price is None:
                self.log(f"[{name}] 平均监控：未能解析价格，关闭详情后重试…")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.01):
                    break
                continue
            self.log(f"[{name}] 平均监控：读取价格 {price}")
            # Restock hit during prescan
            if restock_v > 0 and int(price) <= restock_v and purchased_now < target_total:
                self.log(f"[{name}] 平均监控命中补货：尝试最大数量直接购买…")
                clicked_max = False
                try:
                    clicked_max = self.automator._click_preferring_mapping([
                        "数量最大按钮", "最大", "MAX", "Max", "最大数量"
                    ], "btn_max.png", required=False)
                except Exception:
                    clicked_max = False
                if not clicked_max:
                    try:
                        if self.automator._click_preferring_mapping(["数量输入框", "数量输入"], "input_quantity.png", required=True):
                            self.automator.type_text(str(int(it.get("max_button_qty", 120) or 120)), clear_first=True)
                            clicked_max = True
                        else:
                            clicked_max = False
                    except Exception:
                        clicked_max = False
                if clicked_max and self.automator._click_preferring_mapping(["购买按钮", "确认按钮", "提交订单"], "btn_buy.png", required=True):
                    if self._stop.wait(0.01):
                        return None
                    res = self._check_purchase_result(timeout=0.6, poll=0.01)
                    if res == "success":
                        # Update purchased and record purchase history
                        try:
                            remaining = max(0, int(target_total) - int(it.get("purchased", 0)))
                        except Exception:
                            remaining = 0
                        inc = max(1, int(it.get("max_button_qty", 120) or 120))
                        if remaining > 0:
                            inc = min(inc, remaining)
                        it["purchased"] = int(it.get("purchased", 0)) + inc
                        self.log(f"[{name}] 补货购买成功(+{inc})，累计 {it['purchased']}/{int(it.get('target_total', 0))}")
                        try:
                            from history_store import append_purchase, append_price  # type: ignore
                            append_purchase(str(item_id), name, int(price), int(inc))
                            append_price(str(item_id), name, int(price))
                        except Exception:
                            pass
                        try:
                            self.on_item_update(idx, dict(it))
                        except Exception:
                            pass
                        # Close detail to align with monitoring pattern
                        self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                        opened = False
                        if self._stop.wait(0.01):
                            return None
                        continue
                    else:
                        self.log(f"[{name}] 补货购买未成功({res})，关闭详情后重试。")
                        self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                        opened = False
                        if self._stop.wait(0.01):
                            return None
                        continue
                else:
                    self.log(f"[{name}] 补货：未能设置最大数量或点击购买，关闭后重试。")
                    self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    opened = False
                    if self._stop.wait(0.01):
                        return None
                    continue
            # Append to price history (best-effort) and close without purchasing
            try:
                from history_store import append_price  # type: ignore
                append_price(str(item_id), name, int(price))
            except Exception:
                pass
            # Close detail then proceed to next read
            self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
            opened = False
            collected += 1
            if self._stop.wait(0.01):
                break

        # Compute average from history since start
        try:
            from history_store import query_price  # type: ignore
            recs = query_price(str(item_id), float(start_ts))
            if recs:
                last = recs[-avg_n:]
                vals = [int(r.get("price", 0)) for r in last if int(r.get("price", 0)) > 0]
                if vals:
                    avg_val = int(round(sum(vals) / float(len(vals))))
                    thr = max(0, int(avg_val) - int(avg_subtract))
                    self.log(f"[{name}] 平均监控完成：{len(vals)} 条，均值 {avg_val}，阈值=均值-{int(avg_subtract)}→{thr}")
                    return int(thr)
        except Exception:
            pass
        self.log(f"[{name}] 平均监控完成：无有效样本，回退至配置阈值 {int(it.get('price_threshold', 0))}")
        try:
            return int(it.get("price_threshold", 0))
        except Exception:
            return None

    def _check_purchase_success(self) -> bool:
        """Check screen for the 'buy_ok' template to confirm success."""
        try:
            import pyautogui  # type: ignore
        except Exception:
            return False
        ok_path, ok_conf = self._tpl_path_conf("buy_ok")
        try:
            box = pyautogui.locateOnScreen(ok_path, confidence=float(ok_conf))
            return bool(box)
        except Exception:
            return False

    def _check_purchase_result(self, timeout: float = 1.2, poll: float = 0.02) -> str | None:
        """Return 'success' if buy_ok appears, 'fail' if buy_fail appears, None otherwise.

        Success-first, fail-delayed: prefer success immediately; record fail
        and only return it after timeout if success never appears.
        """
        try:
            import time as _time
            import pyautogui  # type: ignore
        except Exception:
            return None
        ok_path, ok_conf = self._tpl_path_conf("buy_ok")
        fail_path, fail_conf = self._tpl_path_conf("buy_fail")
        t_end = _time.time() + max(0.1, float(timeout))
        found_fail = False
        while _time.time() < t_end:
            try:
                if pyautogui.locateOnScreen(ok_path, confidence=float(ok_conf)):
                    return "success"
            except Exception:
                pass
            try:
                if pyautogui.locateOnScreen(fail_path, confidence=float(fail_conf)):
                    found_fail = True
            except Exception:
                pass
            _time.sleep(max(0.001, float(poll)))
        return "fail" if found_fail else None

    def _press_refresh(self) -> None:
        """Best-effort refresh on the detail page (optional)."""
        try:
            self.automator._click_preferring_mapping(["商品刷新位置", "刷新按钮"], "btn_refresh.png", required=False)
        except Exception:
            pass

    def _soft_click_anywhere(self) -> None:
        """Single left click at current cursor position to gently dismiss overlays."""
        try:
            import pyautogui  # type: ignore
            pos = pyautogui.position()
            self.automator.click_point(int(getattr(pos, 'x', 0)), int(getattr(pos, 'y', 0)), clicks=1)
        except Exception:
            try:
                # Fallback: click near center of screen
                import pyautogui  # type: ignore
                sw, sh = pyautogui.size()
                self.automator.click_point(int(sw // 2), int(sh // 2), clicks=1)
            except Exception:
                pass

    def _move_cursor_to_top_right(self) -> None:
        """Move the mouse cursor to the top-right corner to avoid covering buttons.

        Best-effort; ignores any failures. Uses a small margin from the edge.
        """
        try:
            import pyautogui  # type: ignore
            sw, sh = pyautogui.size()
            x = max(0, int(sw) - 5)
            y = max(0, 5)
            try:
                pyautogui.moveTo(x, y, duration=0)
            except Exception:
                # Fallback: a minimal relative move away from current pos
                try:
                    pyautogui.moveRel(10, -10, duration=0)
                except Exception:
                    pass
        except Exception:
            pass

    def _close_success_overlay(self) -> None:
        """Dismiss success dialog safely: move away, click center, move away again."""
        try:
            import pyautogui  # type: ignore
            # move away first
            self._move_cursor_to_top_right()
            sw, sh = pyautogui.size()
            # single click near center of screen (generic safe area)
            self.automator.click_point(int(sw // 2), int(sh // 2), clicks=1)
            # move away again before further template operations
            self._move_cursor_to_top_right()
        except Exception:
            # best-effort fallback
            try:
                self._soft_click_anywhere()
            except Exception:
                pass

    def _press_back(self) -> None:
        """Click the back button if present (price changed failure screen)."""
        try:
            self.automator._click_preferring_mapping(["返回按钮", "返回", "后退"], "btn_back.png", required=False)
        except Exception:
            pass

    def _attempt_purchase_with_rechecks(self, init_qty: int) -> bool:
        """Type quantity, re-OCR price; if not OK, halve qty (>=2) and recheck up to 2 times.

        Returns True if a buy operation was performed and success template was detected.
        """
        # Ensure integer and cap by remaining
        try:
            remaining = max(0, int(self.target_total) - int(self._purchased))
        except Exception:
            remaining = max(0, self.target_total - self._purchased)
        q = int(init_qty or 0)
        q = min(q, max(0, remaining))
        # 数量需为整数且 >=1（允许单件下单场景）
        if q < 1:
            self.log("数量无效(<1)，放弃本轮。")
            return False

        # 最多 3 次尝试：q, q//2, q//4（均需 >=1 且去重）
        cand = [int(q)]
        if q > 1:
            cand.append(max(1, q // 2))
        if q > 2:
            cand.append(max(1, q // 4))
        attempts: List[int] = []
        for v in cand:
            if v >= 1 and v not in attempts:
                attempts.append(v)
        # 使用与现有逻辑一致的候选标签（坐标优先，失败回退图片）
        # 在购买段将 automator 的等待时间降至 10ms 级别
        prev_wait = getattr(self.automator, 'wait_time', 0.0)
        prev_img_wait = getattr(getattr(self.automator, 'image_automator', None), 'wait_time', None)
        try:
            try:
                self.automator.wait_time = 0.01
            except Exception:
                pass
            try:
                if self.automator.image_automator is not None:
                    self.automator.image_automator.wait_time = 0.01
            except Exception:
                pass
            qty_labels = ["数量输入框", "数量输入"]
            for qty in attempts:
                if self._stop.is_set():
                    return False
                # 点击数量并输入
                if self.automator._click_preferring_mapping(qty_labels, "input_quantity.png", required=True):
                    self.automator.type_text(str(int(qty)), clear_first=True)
                else:
                    self.log("未能定位数量输入框，放弃本轮。")
                    return False

                # 重新计算 ROI 并等待区域刷新，再复核单价
                region = self._avg_price_roi_region()
                if region is None:
                    self.log("复核 ROI 失败，尝试下一个数量。")
                    continue
                self._wait_roi_refresh(region, base_delay=0.12, timeout=0.6, poll=0.03)
                chk_price = self._ocr_unit_price_from_region(region)
                if chk_price is None:
                    self.log("复核价格失败，尝试下一个数量。")
                    continue
                self.log(f"复核单价: {chk_price}")
                # Suspiciously low price on recheck: skip logging and try next
                suspicious = False
                try:
                    if int(self.price_threshold) > 0 and int(chk_price) < 0.5 * int(self.price_threshold):
                        suspicious = True
                except Exception:
                    suspicious = False
                if not suspicious:
                    # Append price history on recheck (best-effort)
                    try:
                        from history_store import append_price  # type: ignore
                        append_price(self._hist_item_id, self.item_name, int(chk_price))
                    except Exception:
                        pass
                # Compare with allowed premium
                try:
                    premium_pct = float(getattr(self, 'price_premium_pct', 0) or 0)
                except Exception:
                    premium_pct = 0.0
                allowed_max = int(round(self.price_threshold * (1.0 + max(0.0, premium_pct) / 100.0)))
                if suspicious or chk_price > allowed_max:
                    # 价格不符合，尝试更小数量
                    continue

                # 价格符合，点击购买
                if not self.automator._click_preferring_mapping(["购买按钮", "确认按钮", "提交订单"], "btn_buy.png", required=True):
                    self.log("点击购买失败。")
                    continue
                if self._stop.wait(0.02):
                    return False
                res = self._check_purchase_result(timeout=1.2)
                if res == "success":
                    # Move mouse to safe corner and click once to dismiss success dialog
                    self._move_cursor_to_top_right()
                    try:
                        self._soft_click_anywhere()
                    except Exception:
                        pass
                    self._purchased += int(qty)
                    self.log(f"购买成功，累计已购: {self._purchased}/{self.target_total}")
                    # Append purchase history (best-effort)
                    try:
                        from history_store import append_purchase  # type: ignore
                        append_purchase(self._hist_item_id, self.item_name, int(chk_price), int(qty))
                    except Exception:
                        pass
                    return True
                elif res == "fail":
                    self.log("检测到购买失败（价格变动等），点击关闭并返回。")
                    try:
                        self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    except Exception:
                        pass
                    return False
                else:
                    self.log("未检测到成功/失败模板，疑似异常或延迟。")
                    continue

            # 全部尝试失败
            self.log("两次/2复核均未通过，放弃本轮。")
            return False
        finally:
            try:
                self.automator.wait_time = prev_wait
            except Exception:
                pass
            try:
                if prev_img_wait is not None and self.automator.image_automator is not None:
                    self.automator.image_automator.wait_time = prev_img_wait
            except Exception:
                pass

    def _wait_roi_refresh(self, region: Tuple[int, int, int, int], *, base_delay: float = 0.02, timeout: float = 0.05, poll: float = 0.01) -> None:
        """Best-effort wait for ROI content to update after input.

        Takes a quick baseline and waits either a minimum delay or until pixel
        difference is observed, up to `timeout`. This reduces读到“上一次识别结果”的概率。
        """
        try:
            import pyautogui  # type: ignore
            from PIL import ImageChops as _IC, ImageStat as _IS  # type: ignore
        except Exception:
            # 无依赖时，至少等待最小延迟
            self._stop.wait(max(0.0, float(base_delay)))
            return
        # baseline
        try:
            img0 = pyautogui.screenshot(region=region).convert("L")
        except Exception:
            self._stop.wait(max(0.0, float(base_delay)))
            return
        end_t = time.time() + max(0.0, float(timeout))
        # 先等一个基础延迟，给 UI 一个刷新窗口
        if self._stop.wait(max(0.0, float(base_delay))):
            return
        while time.time() < end_t and not self._stop.is_set():
            try:
                img1 = pyautogui.screenshot(region=region).convert("L")
                diff = _IC.difference(img0, img1)
                stat = _IS.Stat(diff)
                mean = float(stat.mean[0]) if stat.mean else 0.0
                # 小阈值：一旦像素平均差异>0.5，就认为发生更新
                if mean > 0.5:
                    return
            except Exception:
                pass
            if self._stop.wait(max(0.0, float(poll))):
                return

class MultiBuyer:
    """Round-robin multiple items. Each iteration navigates and attempts a single purchase per item.

    items: list of dicts with keys:
      - enabled: bool
      - item_name: str
      - price_threshold: int
      - target_total: int
      - max_per_order: int
      - purchased: int (runtime)
    """

    def __init__(
        self,
        items: List[Dict[str, Any]],
        *,
        wait_time: float = 0.1,
        allow_image_fallback: bool = True,
        on_log: Optional[Callable[[str], None]] = None,
        on_item_update: Optional[Callable[[int, Dict[str, Any]], None]] = None,
    ) -> None:
        self.items = []
        for it in items:
            d = dict(it)
            d.setdefault("enabled", True)
            d.setdefault("purchased", 0)
            d.setdefault("max_per_order", 120)
            d.setdefault("max_button_qty", 120)
            d.setdefault("default_buy_qty", 1)
            d.setdefault("price_premium_pct", 0)
            d.setdefault("restock_price", 0)
            # Price mode defaults
            d.setdefault("price_mode", "fixed")  # 'fixed' | 'average'
            d.setdefault("avg_samples", 100)
            d.setdefault("avg_subtract", 0)
            if not d.get("id"):
                d["id"] = str(uuid.uuid4())
            self.items.append(d)
        self.on_log = on_log or (lambda s: None)
        self.on_item_update = on_item_update or (lambda i, d: None)

        img_auto = ImageBasedAutomator(image_dir="images", confidence=0.85, wait_time=wait_time)
        self.automator = MappingAutomator(
            config_path="config.json",
            wait_time=wait_time,
            image_automator=img_auto,
            allow_image_fallback=allow_image_fallback,
        )
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Load runtime config for templates and avg ROI settings
        try:
            self.cfg: Dict[str, Any] = load_config("config.json")
        except Exception:
            self.cfg = {}
        self._easyocr_reader = None  # type: ignore
        self._paddleocr_reader = None  # type: ignore
        # Cache template paths
        try:
            self._btn_max_tpl = self._tpl_path_conf("btn_max")[0]
        except Exception:
            self._btn_max_tpl = "images/btn_max.png"
        # Rate-limit for launch warnings
        self._last_launch_log_t: float = 0.0

    # ---------- Scheduling helpers ----------
    @staticmethod
    def _now_minutes() -> int:
        try:
            lt = time.localtime()
            return int(lt.tm_hour) * 60 + int(lt.tm_min)
        except Exception:
            return int(time.time() // 60 % (24 * 60))

    @staticmethod
    def _parse_hhmm(s: str) -> Optional[int]:
        s2 = (s or "").strip()
        if not s2:
            return None
        try:
            hh, mm = s2.split(":")
            h = int(hh); m = int(mm)
            if 0 <= h <= 23 and 0 <= m <= 59:
                return h * 60 + m
        except Exception:
            return None
        return None

    def _in_time_window(self, it: Dict[str, Any]) -> bool:
        ts = self._parse_hhmm(str(it.get("time_start", "")))
        te = self._parse_hhmm(str(it.get("time_end", "")))
        # If either missing -> always allowed
        if ts is None or te is None:
            return True
        now = self._now_minutes()
        if ts == te:
            # Zero-length -> treat as always allowed
            return True
        if ts < te:
            return ts <= now <= te
        # Cross-day window
        return now >= ts or now <= te

    def _market_present(self) -> bool:
        try:
            import pyautogui  # type: ignore
        except Exception:
            return False
        path, conf = self._tpl_path_conf("btn_market")
        try:
            if pyautogui.locateOnScreen(path, confidence=float(conf)):
                return True
        except Exception:
            pass
        return False

    def _click_launch_button_if_present(self) -> bool:
        """If the launcher '启动按钮' template is visible, click it once.

        Returns True if a click was attempted, False otherwise.
        """
        try:
            import pyautogui  # type: ignore
        except Exception:
            return False
        path, conf = self._tpl_path_conf("btn_launch")
        try:
            center = pyautogui.locateCenterOnScreen(path, confidence=float(conf))
        except Exception:
            center = None
        if center is None:
            try:
                box = pyautogui.locateOnScreen(path, confidence=float(conf))
            except Exception:
                box = None
            if not box:
                return False
            try:
                x = int(getattr(box, 'left', 0) + getattr(box, 'width', 0) // 2)
                y = int(getattr(box, 'top', 0) + getattr(box, 'height', 0) // 2)
            except Exception:
                return False
        else:
            try:
                x = int(getattr(center, 'x', 0))
                y = int(getattr(center, 'y', 0))
            except Exception:
                return False
        try:
            self.log("检测到启动按钮，正在点击…")
        except Exception:
            pass
        try:
            self.automator.click_point(x, y, clicks=1)
            return True
        except Exception:
            return False

    def _ensure_game_launched(self) -> bool:
        # If market button detected, assume game ready
        if self._market_present():
            return True
        # If launcher is already on screen, click launch button before/without starting exe
        try:
            if self._click_launch_button_if_present():
                # Give it a brief moment to react
                time.sleep(1.0)
                if self._market_present():
                    return True
        except Exception:
            pass
        game = self.cfg.get("game", {}) if isinstance(self.cfg.get("game"), dict) else {}
        exe_path = str(game.get("exe_path", "") or "").strip()
        if not exe_path or not os.path.exists(exe_path):
            # No path configured; still return False so caller can retry later
            t = time.time()
            if t - self._last_launch_log_t > 30.0:
                self._last_launch_log_t = t
                self.log("未配置游戏启动路径或路径不存在，无法自动启动游戏。")
            return False
        # Launch process best-effort
        try:
            args = str(game.get("launch_args", "") or "").strip()
            cmd: List[str]
            if args:
                # Try to split in Windows-friendly manner
                try:
                    cmd = [exe_path] + shlex.split(args, posix=False)
                except Exception:
                    cmd = [exe_path] + args.split()
            else:
                cmd = [exe_path]
            cwd = os.path.dirname(exe_path) or None
            # Start detached where possible; ignore failures
            creationflags = 0
            if os.name == "nt":
                creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            subprocess.Popen(cmd, cwd=cwd, creationflags=creationflags)  # noqa: S603,S607
            self.log(f"已启动游戏进程: {exe_path}")
        except Exception as e:
            t = time.time()
            if t - self._last_launch_log_t > 30.0:
                self._last_launch_log_t = t
                self.log(f"启动游戏失败: {e}")
            return False
        # Wait for market template up to timeout
        try:
            timeout = int(game.get("startup_timeout_sec", 120) or 120)
        except Exception:
            timeout = 120
        end_t = time.time() + max(5, timeout)
        while time.time() < end_t and not self._stop.is_set():
            if self._market_present():
                self.log("检测到市场按钮，开始执行任务…")
                return True
            # If launcher appears during wait, try clicking the launch button
            try:
                self._click_launch_button_if_present()
            except Exception:
                pass
            time.sleep(1.0)
        self.log("等待市场按钮超时，稍后将重试检测。")
        return False

    def log(self, s: str) -> None:
        self.on_log(s)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        # propagate stop event to automators for immediate cancellation
        try:
            self.automator._stop_event = self._stop
            if getattr(self.automator, "image_automator", None) is not None:
                self.automator.image_automator._stop_event = self._stop
        except Exception:
            pass
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _nav_to_search(self, item_name: str) -> bool:
        # Home (optional)
        self.automator._click_preferring_mapping(["首页按钮"], "btn_home.png", required=False)
        # Market
        if not self.automator._click_preferring_mapping(["市场按钮", "市场入口", "市场"], "btn_market.png", required=True):
            self.log("未能进入【市场】")
            return False
        # Search box
        if self.automator._click_preferring_mapping(["市场搜索栏", "搜索框", "搜索输入"], "input_search.png", required=True):
            self.automator.type_text(item_name, clear_first=True)
        else:
            self.log("未能定位【搜索框】")
            return False
        # Search button
        if not self.automator._click_preferring_mapping(["市场搜索按钮", "搜索按钮"], "btn_search.png", required=True):
            self.log("未能点击【搜索按钮】")
            return False
        if self._stop.wait(0.02):
            return False
        return True

    def _attempt_one(self, idx: int, it: Dict[str, Any]) -> None:
        """简化购买流程：进入详情→识别单价→合适则直接购买。

        - 不填写数量、不二次复核、不做数量退避；
        - 成功后按 default_buy_qty 累计并软点击关闭成功弹窗；
        - 成功后不点关闭，留在详情页，等待极短时间并继续识别；
        - 不合适或失败则点击关闭并在10ms后进行下一轮。
        """
        name = str(it.get("item_name", ""))
        try:
            threshold = int(it.get("price_threshold", 0))
            target_total = int(it.get("target_total", 0))
            max_per_order = int(it.get("max_per_order", 120))
            premium_pct = float(it.get("price_premium_pct", 0) or 0)
            restock_price = int(it.get("restock_price", 0) or 0)
            max_button_qty = int(it.get("max_button_qty", 120) or 120)
        except Exception:
            self.log(f"[{name}] 配置无效（阈值/目标/每单），跳过。")
            return

        # Determine effective threshold. If average mode, run a pre-scan phase that
        # collects N samples by following the normal open→read→record→close cycle
        # (no purchase), optionally performing restock if price ≤ restock_price.
        price_mode = str(it.get("price_mode", "fixed")).lower()
        try:
            avg_samples = int(it.get("avg_samples", 100))
        except Exception:
            avg_samples = 100
        try:
            avg_subtract = int(it.get("avg_subtract", 0))
        except Exception:
            avg_subtract = 0
        eff_thr = int(threshold)
        if price_mode == "average" and not self._stop.is_set():
            val = self._prescan_average_threshold(idx, it, avg_samples, avg_subtract, restock_price)
            if val is not None:
                eff_thr = int(val)

        opened = False
        while not self._stop.is_set():
            # Already done for this item?
            try:
                purchased_now = int(it.get("purchased", 0))
            except Exception:
                purchased_now = 0
            if purchased_now >= target_total:
                self.log(f"[{name}] 已达目标 {purchased_now}/{target_total}，结束该商品循环。")
                break
            # Ensure detail is opened
            if not opened:
                if not self.automator._click_preferring_mapping([
                    "第一个商品", "第一个商品位置", "第1个商品"
                ], None, required=True):
                    self.log(f"[{name}] 未定位到第一个商品，稍后重试。")
                    # 无法进入详情时，退出本轮，由上层重新导航
                    break
                opened = True

            # Read price using Avg Price ROI
            region = self._avg_price_roi_region()
            if region is None:
                self.log(f"[{name}] 未定位到‘购买’按钮或 ROI，关闭详情后重试…")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                # 进入下一轮
                if self._stop.wait(0.01):
                    break
                continue
            price = self._ocr_unit_price_from_region(region)
            if price is None:
                self.log(f"[{name}] 未能解析价格，关闭详情后立即重试…")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.01):
                    break
                continue

            self.log(f"[{name}] 读取价格: {price}")
            # Decide path: restock if restock_price>0 and price<=restock_price (immediate)
            do_restock = False
            try:
                do_restock = (restock_price > 0) and (int(price) <= int(restock_price))
            except Exception:
                do_restock = False

            # eff_thr was computed once before the loop (for average mode) or set to threshold

            # Suspicious low price based on effective threshold (restock or normal)
            suspicious = False
            try:
                base_thr = int(restock_price) if do_restock else int(eff_thr)
            except Exception:
                base_thr = int(eff_thr)
            try:
                if base_thr > 0 and int(price) < 0.5 * base_thr:
                    suspicious = True
            except Exception:
                suspicious = False
            if not suspicious:
                # Append price history (best-effort)
                try:
                    from history_store import append_price  # type: ignore
                    append_price(str(it.get("id", "")), name, int(price))
                except Exception:
                    pass
            else:
                self.log(f"[{name}] 价格异常(低于阈值50%)，视为识别错误，本轮放弃。")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.01):
                    break
                continue

            if do_restock and purchased_now < target_total:
                # Restock path: click MAX then buy, no recheck
                self.log(f"[{name}] 触发补货：点击最大数量并直接购买…")
                # Try click MAX button first
                clicked_max = False
                try:
                    clicked_max = self.automator._click_preferring_mapping([
                        "数量最大按钮", "最大", "MAX", "Max", "最大数量"
                    ], "btn_max.png", required=False)
                except Exception:
                    clicked_max = False
                if not clicked_max:
                    # Fallback: click quantity input and type max_button_qty
                    try:
                        if self.automator._click_preferring_mapping(["数量输入框", "数量输入"], "input_quantity.png", required=True):
                            self.automator.type_text(str(int(max_button_qty)), clear_first=True)
                            clicked_max = True
                        else:
                            clicked_max = False
                    except Exception:
                        clicked_max = False
                if not clicked_max:
                    self.log(f"[{name}] 未能设置最大数量，放弃本轮。")
                    # Close and retry next
                    self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    opened = False
                    if self._stop.wait(0.01):
                        break
                    continue

                # Direct buy
                if not self.automator._click_preferring_mapping(["购买按钮", "确认按钮", "提交订单"], "btn_buy.png", required=True):
                    self.log(f"[{name}] 点击购买失败，关闭详情后重试。")
                    self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    opened = False
                    if self._stop.wait(0.01):
                        break
                    continue
                if self._stop.wait(0.01):
                    break
                res = self._check_purchase_result(timeout=0.6, poll=0.01)
                if res == "success":
                    self._close_success_overlay()
                    # Inc by max_button_qty, clamp to remaining target for UI progress
                    try:
                        remaining = max(0, int(target_total) - int(it.get("purchased", 0)))
                    except Exception:
                        remaining = 0
                    inc = max(1, int(max_button_qty))
                    if remaining > 0:
                        inc = min(inc, remaining)
                    it["purchased"] = int(it.get("purchased", 0)) + inc
                    self.log(f"[{name}] 补货购买成功(+{inc})，累计 {it['purchased']}/{int(it.get('target_total', 0))}")
                    # History purchase (use same inc for consistency with UI progress)
                    try:
                        from history_store import append_purchase  # type: ignore
                        append_purchase(str(it.get("id", "")), name, int(price), int(inc))
                    except Exception:
                        pass
                    try:
                        self.on_item_update(idx, dict(it))
                    except Exception:
                        pass
                    # Move mouse and continue inside detail
                    self._move_cursor_to_top_right()
                    self._wait_roi_refresh(region, base_delay=0.02, timeout=0.05, poll=0.01)
                    continue
                else:
                    self.log(f"[{name}] 补货购买未成功({res})，关闭详情后重试。")
                    self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    opened = False
                    if self._stop.wait(0.01):
                        break
                    continue

            # Normal path with premium tolerance (based on effective threshold)
            allowed_max = int(round(int(eff_thr) * (1.0 + max(0.0, premium_pct) / 100.0)))
            if price <= allowed_max and purchased_now < target_total:
                # 直接购买，不填写数量
                if not self.automator._click_preferring_mapping(["购买按钮", "确认按钮", "提交订单"], "btn_buy.png", required=True):
                    self.log(f"[{name}] 点击购买失败，关闭详情后重试。")
                    self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    opened = False
                    if self._stop.wait(0.01):
                        break
                    continue
                # 极短等待 + 模板判断
                if self._stop.wait(0.01):
                    break
                res = self._check_purchase_result(timeout=0.3, poll=0.01)
                if res is None:
                    # Fallback recheck to avoid missing slightly delayed success
                    res = self._check_purchase_result(timeout=0.6)
                if res == "success":
                    self._close_success_overlay()
                    try:
                        inc = int(it.get("default_buy_qty", 1))
                    except Exception:
                        inc = 1
                    it["purchased"] = int(it.get("purchased", 0)) + inc
                    self.log(f"[{name}] 购买成功，累计 {it['purchased']}/{int(it.get('target_total', 0))}")
                    # Append purchase history (best-effort)
                    try:
                        from history_store import append_purchase  # type: ignore
                        append_purchase(str(it.get("id", "")), name, int(price), int(inc))
                    except Exception:
                        pass
                    try:
                        self.on_item_update(idx, dict(it))
                    except Exception:
                        pass
                    # 移动鼠标至右上角以避免遮挡，再进行后续模板/ROI操作
                    self._move_cursor_to_top_right()
                    # 软点击关闭成功弹窗
                    try:
                        import pyautogui  # type: ignore
                        self._move_cursor_to_top_right()
                        pos = pyautogui.position()
                        self.automator.click_point(int(getattr(pos, 'x', 0)), int(getattr(pos, 'y', 0)), clicks=1)
                    except Exception:
                        try:
                            import pyautogui  # type: ignore
                            sw, sh = pyautogui.size()
                            self.automator.click_point(int(sw // 2), int(sh // 2), clicks=1)
                        except Exception:
                            pass
                    # 等待 ROI 刷新，继续在详情内循环
                    self._wait_roi_refresh(region, base_delay=0.02, timeout=0.05, poll=0.01)
                    continue
                else:
                    # 失败或未检测到，关闭并在10ms后进入下一轮
                    self.log(f"[{name}] 购买未成功({res}), 关闭详情后重试。")
                    self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    opened = False
                    if self._stop.wait(0.01):
                        break
                    continue
            else:
                # 不合适：关闭并在10ms后进入下一轮
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.01):
                    break
                continue

    def _run(self) -> None:
        while not self._stop.is_set():
            any_remaining = False   # 仍有未完成的任务（未来某时可执行）
            any_runnable = False    # 当前时间段内可执行
            for idx, it in enumerate(self.items):
                if self._stop.is_set():
                    break
                if not it.get("enabled", True):
                    continue
                if int(it.get("purchased", 0)) >= int(it.get("target_total", 0)):
                    continue
                any_remaining = True
                # 时间段检查：不在时间段内则跳过（保持静默）
                if not self._in_time_window(it):
                    continue
                any_runnable = True
                # 进入时间段：确保游戏已启动并就绪
                if not self._ensure_game_launched():
                    # 下一轮继续尝试
                    continue
                if not self._nav_to_search(str(it.get("item_name", ""))):
                    continue
                self._attempt_one(idx, it)
                # 执行路径下维持短周期
                time.sleep(0.01)
            if self._stop.is_set():
                break
            if not any_remaining:
                self.log("所有任务均已完成")
                break
            if not any_runnable:
                # 静默等待：每分钟检查一次是否进入可执行时间
                for _ in range(60):
                    if self._stop.wait(1.0):
                        break
                continue
            # 有可执行任务时保持快速循环
            time.sleep(0.01)
        self.log("多任务已停止")

    # ---------- Helpers: ROI / OCR / Buy for MultiBuyer ----------
    def _mb_attempt_purchase_with_rechecks(self, init_qty: int, threshold: int, idx: int, it: Dict[str, Any]) -> bool:
        try:
            remaining = max(0, int(it.get("target_total", 0)) - int(it.get("purchased", 0)))
        except Exception:
            remaining = 0
        q = int(init_qty or 0)
        q = min(q, max(0, remaining))
        if q < 1:
            self.log("数量无效(<1)，放弃本轮。")
            return False
        # 生成尝试序列：q, q//2, q//4（>=1，去重）
        cand = [int(q)]
        if q > 1:
            cand.append(max(1, q // 2))
        if q > 2:
            cand.append(max(1, q // 4))
        attempts: List[int] = []
        for v in cand:
            if v >= 1 and v not in attempts:
                attempts.append(v)
        # 降低购买段的等待时间（~10ms）以减少操作间停顿
        prev_wait = getattr(self.automator, 'wait_time', 0.0)
        prev_img_wait = getattr(getattr(self.automator, 'image_automator', None), 'wait_time', None)
        try:
            try:
                self.automator.wait_time = 0.01
            except Exception:
                pass
            try:
                if self.automator.image_automator is not None:
                    self.automator.image_automator.wait_time = 0.01
            except Exception:
                pass
            qty_labels = ["数量输入框", "数量输入"]
            for qty in attempts:
                if self._stop.is_set():
                    return False
                if self.automator._click_preferring_mapping(qty_labels, "input_quantity.png", required=True):
                    self.automator.type_text(str(int(qty)), clear_first=True)
                else:
                    self.log("未能定位数量输入框，放弃本轮。")
                    return False
                region = self._avg_price_roi_region()
                if region is None:
                    self.log("复核 ROI 失败，尝试下一个数量。")
                    continue
                # 等待 ROI 内容刷新，避免读到上一次识别结果
                self._wait_roi_refresh(region, base_delay=0.12, timeout=0.6, poll=0.03)
                chk_price = self._ocr_unit_price_from_region(region)
                if chk_price is None:
                    self.log("复核价格失败，尝试下一个数量。")
                    continue
                self.log(f"复核单价: {chk_price}")
                # Suspicious low on recheck -> skip logging
                suspicious = False
                try:
                    if int(threshold) > 0 and int(chk_price) < 0.5 * int(threshold):
                        suspicious = True
                except Exception:
                    suspicious = False
                if not suspicious:
                    # Append price history on recheck (best-effort). Note: restock path skips recheck.
                    try:
                        from history_store import append_price  # type: ignore
                        name = str(it.get("item_name", ""))
                        append_price(str(it.get("id", "")), name, int(chk_price))
                    except Exception:
                        pass
                allowed_max = int(round(threshold * (1.0 + max(0.0, float(it.get("price_premium_pct", 0) or 0)) / 100.0)))
                if suspicious or chk_price > allowed_max:
                    continue
                if not self.automator._click_preferring_mapping(["购买按钮", "确认按钮", "提交订单"], "btn_buy.png", required=True):
                    self.log("点击购买失败。")
                    continue
                if self._stop.wait(0.02):
                    return False
                res = self._check_purchase_result(timeout=1.2)
                if res == "success":
                    self._close_success_overlay()
                    it["purchased"] = int(it.get("purchased", 0)) + int(qty)
                    name = str(it.get("item_name", ""))
                    self.log(f"[{name}] 购买成功，累计 {it['purchased']}/{int(it.get('target_total', 0))}")
                    # Append purchase history (best-effort)
                    try:
                        from history_store import append_purchase  # type: ignore
                        append_purchase(str(it.get("id", "")), name, int(chk_price), int(qty))
                    except Exception:
                        pass
                    try:
                        self.on_item_update(idx, dict(it))
                    except Exception:
                        pass
                    # 成功不点关闭，软点击
                    try:
                        import pyautogui  # type: ignore
                        pos = pyautogui.position()
                        self.automator.click_point(int(getattr(pos, 'x', 0)), int(getattr(pos, 'y', 0)), clicks=1)
                    except Exception:
                        try:
                            import pyautogui  # type: ignore
                            sw, sh = pyautogui.size()
                            self.automator.click_point(int(sw // 2), int(sh // 2), clicks=1)
                        except Exception:
                            pass
                    return True
                elif res == "fail":
                    self.log("检测到购买失败（价格变动等），点击关闭并返回。")
                    try:
                        self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    except Exception:
                        pass
                    return False
                else:
                    self.log("未检测到成功/失败模板，疑似失败或延迟。")
                    continue
            self.log("两次/2复核均未通过，放弃本轮。")
            return False
        finally:
            try:
                self.automator.wait_time = prev_wait
            except Exception:
                pass
            try:
                if prev_img_wait is not None and self.automator.image_automator is not None:
                    self.automator.image_automator.wait_time = prev_img_wait
            except Exception:
                pass

    def _tpl_path_conf(self, basename_hint: str) -> Tuple[str, float]:
        try:
            tpls = self.cfg.get("templates", {}) if isinstance(self.cfg.get("templates"), dict) else {}
            for _name, _d in tpls.items():
                p = str((_d or {}).get("path", ""))
                if not p:
                    continue
                base = p.replace("\\", "/").split("/")[-1].lower()
                if basename_hint.lower() in base:
                    try:
                        conf = float((_d or {}).get("confidence", 0.85))
                    except Exception:
                        conf = 0.85
                    return p, conf
        except Exception:
            pass
        return (f"images/{basename_hint}.png", 0.85)

    def _avg_price_roi_region(self) -> Optional[Tuple[int, int, int, int]]:
        try:
            import pyautogui  # type: ignore
            from PIL import Image  # type: ignore
        except Exception:
            return None
        avg_cfg = self.cfg.get("avg_price_area", {}) if isinstance(self.cfg.get("avg_price_area"), dict) else {}
        try:
            dist = int(avg_cfg.get("distance_from_buy_top", 5))
            hei = max(1, int(avg_cfg.get("height", 45)))
        except Exception:
            dist, hei = 5, 45
        buy_path, buy_conf = self._tpl_path_conf("btn_buy")
        center = None
        box = None
        try:
            center = pyautogui.locateCenterOnScreen(buy_path, confidence=float(buy_conf))
        except Exception:
            pass
        if center is None:
            try:
                box = pyautogui.locateOnScreen(buy_path, confidence=float(buy_conf))
            except Exception:
                box = None
        if center is not None:
            try:
                tpl_w, tpl_h = Image.open(buy_path).size
            except Exception:
                tpl_w, tpl_h = 120, 40
            cx, cy = int(getattr(center, "x", 0)), int(getattr(center, "y", 0))
            b_left = int(cx - tpl_w // 2)
            b_top = int(cy - tpl_h // 2)
            b_w, b_h = int(tpl_w), int(tpl_h)
        elif box is not None:
            try:
                b_left, b_top = int(getattr(box, "left", 0)), int(getattr(box, "top", 0))
                b_w, b_h = int(getattr(box, "width", 0)), int(getattr(box, "height", 0))
            except Exception:
                return None
        else:
            return None
        if b_w <= 1 or b_h <= 1:
            return None
        y_bottom = b_top - dist
        y_top = y_bottom - hei
        x_left = b_left
        width = b_w
        try:
            scr_w, scr_h = pyautogui.size()
        except Exception:
            scr_w, scr_h = 2560, 1440
        y_top = max(0, min(scr_h - 2, int(y_top)))
        y_bottom = max(y_top + 1, min(scr_h - 1, int(y_bottom)))
        x_left = max(0, min(scr_w - 2, int(x_left)))
        width = max(1, min(int(width), scr_w - x_left))
        height = max(1, int(y_bottom - y_top))
        return (x_left, y_top, width, height)

    def _ensure_easyocr(self):
        if getattr(self, "_easyocr_reader", None) is not None:
            return self._easyocr_reader
        try:
            import easyocr  # type: ignore
            self._easyocr_reader = easyocr.Reader(['en'], gpu=False)
        except Exception:
            self._easyocr_reader = None
        return self._easyocr_reader

    def _ensure_paddleocr(self):
        if getattr(self, "_paddleocr_reader", None) is not None:
            return self._paddleocr_reader
        try:
            from paddleocr import PaddleOCR  # type: ignore
            self._paddleocr_reader = PaddleOCR(use_angle_cls=True, lang='en', show_log=False)
        except Exception:
            self._paddleocr_reader = None
        return self._paddleocr_reader

    def _ocr_unit_price_from_region(self, region: Tuple[int, int, int, int]) -> Optional[int]:
        try:
            import pyautogui  # type: ignore
            import pytesseract  # type: ignore
            from price_reader import _maybe_init_tesseract  # type: ignore
        except Exception:
            return None
        try:
            _maybe_init_tesseract()
        except Exception:
            pass
        try:
            img = pyautogui.screenshot(region=region)
        except Exception:
            return None
        avg_cfg = self.cfg.get("avg_price_area", {}) if isinstance(self.cfg.get("avg_price_area"), dict) else {}
        try:
            scale = float(avg_cfg.get("scale", 1.0))
        except Exception:
            scale = 1.0
        if not (0.6 <= scale <= 2.5):
            scale = 1.0
        if abs(scale - 1.0) > 1e-3:
            try:
                w, h = img.size
                from PIL import Image as _Image  # type: ignore
                img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), resample=getattr(_Image, 'LANCZOS', 1))
            except Exception:
                pass
        bin_img = None
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
            arr = np.array(img)
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            _thr, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            from PIL import Image as _Image  # type: ignore
            bin_img = _Image.fromarray(th)
        except Exception:
            try:
                g = img.convert("L")
                th = g.point(lambda p: 255 if p > 128 else 0)
                bin_img = th
            except Exception:
                bin_img = img
        engine = str(avg_cfg.get("ocr_engine", "tesseract")).lower()
        raw_text = ""
        if engine == "easyocr":
            reader = self._ensure_easyocr()
            if reader is not None:
                try:
                    import numpy as _np  # type: ignore
                    arr = _np.array(bin_img or img)
                    texts = reader.readtext(arr, detail=0)
                    raw_text = "\n".join(map(str, texts))
                except Exception:
                    raw_text = ""
            else:
                engine = "tesseract"
        elif engine in ("paddle", "paddleocr"):
            reader = self._ensure_paddleocr()
            if reader is not None:
                try:
                    import numpy as _np  # type: ignore
                    arr = _np.array(bin_img or img)
                    res = reader.ocr(arr, cls=True)
                    texts: list[str] = []
                    try:
                        if isinstance(res, list) and len(res) > 0 and isinstance(res[0], list) and len(res[0]) > 0 and isinstance(res[0][0], list):
                            for e in res[0]:
                                if isinstance(e, list) and len(e) >= 2 and isinstance(e[1], (list, tuple)):
                                    t = e[1][0]
                                    if t:
                                        texts.append(str(t))
                        else:
                            for e in (res or []):
                                if isinstance(e, list) and len(e) >= 2 and isinstance(e[1], (list, tuple)):
                                    t = e[1][0]
                                    if t:
                                        texts.append(str(t))
                    except Exception:
                        pass
                    raw_text = "\n".join(texts)
                except Exception:
                    raw_text = ""
            else:
                engine = "tesseract"
        if not raw_text:
            allow = str(avg_cfg.get("ocr_allowlist", "0123456789KM"))
            need = "KMkm"
            allow_ex = allow + "".join(ch for ch in need if ch not in allow)
            cfg = f"--oem 3 --psm 6 -c tessedit_char_whitelist={allow_ex}"
            try:
                raw_text = pytesseract.image_to_string(bin_img or img, config=cfg) or ""
            except Exception:
                return None
        lines = [ln.strip() for ln in str(raw_text).splitlines() if ln.strip()]
        def parse_num(s: str) -> Optional[int]:
            up = str(s).upper()
            mult = 1_000_000 if ("M" in up) else (1000 if ("K" in up) else 1)
            digits = "".join(ch for ch in up if ch.isdigit())
            if not digits:
                return None
            try:
                return int(digits) * mult
            except Exception:
                return None
        for ln in lines:
            val = parse_num(ln)
            if val is not None:
                return val
        import re
        toks = re.findall(r"[0-9]+[KMkm]?", str(raw_text))
        vals: List[int] = []
        for tk in toks:
            v = parse_num(tk)
            if v is not None:
                vals.append(v)
        if vals:
            return min(vals)
        return None

    def _prescan_average_threshold(
        self,
        idx: int,
        it: Dict[str, Any],
        avg_samples: int,
        avg_subtract: int,
        restock_price: int,
    ) -> Optional[int]:
        """Monitoring phase for average mode: open→read→record→close, no purchase.

        - If any read price <= restock_price (>0), perform restock immediately (MAX→buy).
        - Append each observed price to history.
        - Compute threshold as max(0, round(mean(last N prices)) - avg_subtract) from records since start.
        """
        name = str(it.get("item_name", ""))
        item_id = str(it.get("id", ""))
        try:
            restock_v = int(restock_price)
        except Exception:
            restock_v = 0
        try:
            avg_n = max(1, min(int(avg_samples), 500))
        except Exception:
            avg_n = 100
        start_ts = time.time()
        collected = 0
        opened = False
        self.log(f"[{name}] 开始平均值监控：目标 {avg_n} 次（仅记录不购买）…")
        while not self._stop.is_set() and collected < avg_n:
            try:
                purchased_now = int(it.get("purchased", 0))
                target_total = int(it.get("target_total", 0))
            except Exception:
                purchased_now, target_total = 0, 0
            if purchased_now >= target_total:
                self.log(f"[{name}] 已达目标 {purchased_now}/{target_total}，终止平均值监控。")
                break
            if not opened:
                if not self.automator._click_preferring_mapping(["第一个商品", "第一个商品位置", "第1个商品"], None, required=True):
                    self.log(f"[{name}] 未定位到第一个商品（平均监控），稍后重试…")
                    break
                opened = True
            region = self._avg_price_roi_region()
            if region is None:
                self.log(f"[{name}] 平均监控：未定位到‘购买’按钮或 ROI，关闭详情后重试…")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.01):
                    break
                continue
            price = self._ocr_unit_price_from_region(region)
            if price is None:
                self.log(f"[{name}] 平均监控：未能解析价格，关闭详情后重试…")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.01):
                    break
                continue
            self.log(f"[{name}] 平均监控：读取价格 {price}")
            # Restock during prescan
            if restock_v > 0 and int(price) <= restock_v and purchased_now < target_total:
                self.log(f"[{name}] 平均监控命中补货：尝试最大数量直接购买…")
                clicked_max = False
                try:
                    clicked_max = self.automator._click_preferring_mapping(["数量最大按钮", "最大", "MAX", "Max", "最大数量"], "btn_max.png", required=False)
                except Exception:
                    clicked_max = False
                if not clicked_max:
                    try:
                        if self.automator._click_preferring_mapping(["数量输入框", "数量输入"], "input_quantity.png", required=True):
                            self.automator.type_text(str(int(it.get("max_button_qty", 120) or 120)), clear_first=True)
                            clicked_max = True
                    except Exception:
                        clicked_max = False
                if clicked_max and self.automator._click_preferring_mapping(["购买按钮", "确认按钮", "提交订单"], "btn_buy.png", required=True):
                    if self._stop.wait(0.01):
                        return None
                    res = self._check_purchase_result(timeout=0.6, poll=0.01)
                    if res == "success":
                        try:
                            remaining = max(0, int(target_total) - int(it.get("purchased", 0)))
                        except Exception:
                            remaining = 0
                        inc = max(1, int(it.get("max_button_qty", 120) or 120))
                        if remaining > 0:
                            inc = min(inc, remaining)
                        it["purchased"] = int(it.get("purchased", 0)) + inc
                        self.log(f"[{name}] 补货购买成功(+{inc})，累计 {it['purchased']}/{int(it.get('target_total', 0))}")
                        try:
                            from history_store import append_purchase, append_price  # type: ignore
                            append_purchase(str(item_id), name, int(price), int(inc))
                            append_price(str(item_id), name, int(price))
                        except Exception:
                            pass
                        try:
                            self.on_item_update(idx, dict(it))
                        except Exception:
                            pass
                        self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                        opened = False
                        if self._stop.wait(0.01):
                            return None
                        continue
                # fallthrough: if not successful, close and continue
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.01):
                    break
                continue
            # Record observed price, then close without purchase
            try:
                from history_store import append_price  # type: ignore
                append_price(str(item_id), name, int(price))
            except Exception:
                pass
            self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
            opened = False
            collected += 1
            if self._stop.wait(0.01):
                break

        # Compute average threshold from history since start
        try:
            from history_store import query_price  # type: ignore
            recs = query_price(str(item_id), float(start_ts))
            if recs:
                last = recs[-avg_n:]
                vals = [int(r.get("price", 0)) for r in last if int(r.get("price", 0)) > 0]
                if vals:
                    avg_val = int(round(sum(vals) / float(len(vals))))
                    thr = max(0, int(avg_val) - int(avg_subtract))
                    self.log(f"[{name}] 平均监控完成：{len(vals)} 条，均值 {avg_val}，阈值=均值-{int(avg_subtract)}→{thr}")
                    return int(thr)
        except Exception:
            pass
        self.log(f"[{name}] 平均监控完成：无有效样本，回退至配置阈值 {int(it.get('price_threshold', 0))}")
        try:
            return int(it.get("price_threshold", 0))
        except Exception:
            return None

    def _sample_avg_price(
        self,
        region: Tuple[int, int, int, int],
        n: int,
        restock_price: int,
    ) -> tuple[Optional[int], bool, Optional[int]]:
        """Collect up to n valid OCR prices and return (avg, hit_restock, hit_price).

        - Respects self._stop for quick abort.
        - If any sample <= restock_price (and restock_price > 0), returns (None, True, that_price).
        - If no valid samples collected, returns (None, False, None).
        """
        try:
            n = int(n)
        except Exception:
            n = 100
        n = max(1, min(n, 500))
        try:
            rp = int(restock_price)
        except Exception:
            rp = 0
        vals: list[int] = []
        attempts = 0
        max_attempts = max(n, int(n * 2))
        last_hit: Optional[int] = None
        while not self._stop.is_set() and len(vals) < n and attempts < max_attempts:
            attempts += 1
            v = self._ocr_unit_price_from_region(region)
            if v is None:
                # tiny wait to avoid busy loop
                if self._stop.wait(0.005):
                    break
                continue
            # Restock hit during sampling
            if rp > 0 and v <= rp:
                last_hit = int(v)
                return (None, True, last_hit)
            vals.append(int(v))
            # short wait between samples
            if self._stop.wait(0.003):
                break
        if not vals:
            return (None, False, None)
        try:
            avg = int(round(sum(vals) / float(len(vals))))
        except Exception:
            avg = None
        return (avg, False, None)

    def _check_purchase_result(self, timeout: float = 1.2, poll: float = 0.02) -> str | None:
        """Return 'success' if buy_ok appears, 'fail' if buy_fail appears, None otherwise.

        Success-first, fail-delayed: prefer success immediately; record fail
        and only return it after timeout if success never appears.
        """
        try:
            import time as _time
            import pyautogui  # type: ignore
        except Exception:
            return None
        ok_path, ok_conf = self._tpl_path_conf("buy_ok")
        fail_path, fail_conf = self._tpl_path_conf("buy_fail")
        t_end = _time.time() + max(0.1, float(timeout))
        found_fail = False
        while _time.time() < t_end:
            try:
                if pyautogui.locateOnScreen(ok_path, confidence=float(ok_conf)):
                    return "success"
            except Exception:
                pass
            try:
                if pyautogui.locateOnScreen(fail_path, confidence=float(fail_conf)):
                    found_fail = True
            except Exception:
                pass
            _time.sleep(max(0.001, float(poll)))
        return "fail" if found_fail else None

    def _press_back(self) -> None:
        """Click the back button if present (price changed failure screen)."""
        try:
            self.automator._click_preferring_mapping(["返回按钮", "返回", "后退"], "btn_back.png", required=False)
        except Exception:
            pass

    def _wait_roi_refresh(self, region: Tuple[int, int, int, int], *, base_delay: float = 0.02, timeout: float = 0.05, poll: float = 0.01) -> None:
        """Best-effort wait for ROI content to update after input.

        Takes a quick baseline and waits either a minimum delay or until pixel
        difference is observed, up to `timeout`.
        """
        try:
            import pyautogui  # type: ignore
            from PIL import ImageChops as _IC, ImageStat as _IS  # type: ignore
        except Exception:
            self._stop.wait(max(0.0, float(base_delay)))
            return
        try:
            img0 = pyautogui.screenshot(region=region).convert("L")
        except Exception:
            self._stop.wait(max(0.0, float(base_delay)))
            return
        end_t = time.time() + max(0.0, float(timeout))
        if self._stop.wait(max(0.0, float(base_delay))):
            return
        while time.time() < end_t and not self._stop.is_set():
            try:
                img1 = pyautogui.screenshot(region=region).convert("L")
                diff = _IC.difference(img0, img1)
                stat = _IS.Stat(diff)
                mean = float(stat.mean[0]) if stat.mean else 0.0
                if mean > 0.5:
                    return
            except Exception:
                pass
            if self._stop.wait(max(0.0, float(poll))):
                return

    def _move_cursor_to_top_right(self) -> None:
        """Move the mouse cursor to the top-right corner to avoid covering buttons.

        Best-effort; ignores any failures. Uses a small margin from the edge.
        """
        try:
            import pyautogui  # type: ignore
            sw, sh = pyautogui.size()
            x = max(0, int(sw) - 5)
            y = max(0, 5)
            try:
                pyautogui.moveTo(x, y, duration=0)
            except Exception:
                try:
                    pyautogui.moveRel(10, -10, duration=0)
                except Exception:
                    pass
        except Exception:
            pass

    def _close_success_overlay(self) -> None:
        """Dismiss success dialog safely: move away, click center, move away again."""
        try:
            import pyautogui  # type: ignore
            self._move_cursor_to_top_right()
            sw, sh = pyautogui.size()
            self.automator.click_point(int(sw // 2), int(sh // 2), clicks=1)
            self._move_cursor_to_top_right()
        except Exception:
            try:
                # last resort: click center only
                import pyautogui  # type: ignore
                sw, sh = pyautogui.size()
                self.automator.click_point(int(sw // 2), int(sh // 2), clicks=1)
            except Exception:
                pass

    # ---------- Helpers: ROI / OCR for MultiBuyer ----------
    def _tpl_path_conf(self, basename_hint: str) -> Tuple[str, float]:
        try:
            tpls = self.cfg.get("templates", {}) if isinstance(self.cfg.get("templates"), dict) else {}
            for _name, _d in tpls.items():
                p = str((_d or {}).get("path", ""))
                if not p:
                    continue
                base = p.replace("\\", "/").split("/")[-1].lower()
                if basename_hint.lower() in base:
                    try:
                        conf = float((_d or {}).get("confidence", 0.85))
                    except Exception:
                        conf = 0.85
                    return p, conf
        except Exception:
            pass
        return (f"images/{basename_hint}.png", 0.85)

    def _avg_price_roi_region(self) -> Optional[Tuple[int, int, int, int]]:
        try:
            import pyautogui  # type: ignore
            from PIL import Image  # type: ignore
        except Exception:
            return None
        avg_cfg = self.cfg.get("avg_price_area", {}) if isinstance(self.cfg.get("avg_price_area"), dict) else {}
        try:
            dist = int(avg_cfg.get("distance_from_buy_top", 5))
            hei = max(1, int(avg_cfg.get("height", 45)))
        except Exception:
            dist, hei = 5, 45
        buy_path, buy_conf = self._tpl_path_conf("btn_buy")
        center = None
        box = None
        try:
            center = pyautogui.locateCenterOnScreen(buy_path, confidence=float(buy_conf))
        except Exception:
            pass
        if center is None:
            try:
                box = pyautogui.locateOnScreen(buy_path, confidence=float(buy_conf))
            except Exception:
                box = None
        if center is not None:
            try:
                tpl_w, tpl_h = Image.open(buy_path).size
            except Exception:
                tpl_w, tpl_h = 120, 40
            cx, cy = int(getattr(center, "x", 0)), int(getattr(center, "y", 0))
            b_left = int(cx - tpl_w // 2)
            b_top = int(cy - tpl_h // 2)
            b_w, b_h = int(tpl_w), int(tpl_h)
        elif box is not None:
            try:
                b_left, b_top = int(getattr(box, "left", 0)), int(getattr(box, "top", 0))
                b_w, b_h = int(getattr(box, "width", 0)), int(getattr(box, "height", 0))
            except Exception:
                return None
        else:
            return None
        if b_w <= 1 or b_h <= 1:
            return None
        y_bottom = b_top - dist
        y_top = y_bottom - hei
        x_left = b_left
        width = b_w
        try:
            scr_w, scr_h = pyautogui.size()
        except Exception:
            scr_w, scr_h = 2560, 1440
        y_top = max(0, min(scr_h - 2, int(y_top)))
        y_bottom = max(y_top + 1, min(scr_h - 1, int(y_bottom)))
        x_left = max(0, min(scr_w - 2, int(x_left)))
        width = max(1, min(int(width), scr_w - x_left))
        height = max(1, int(y_bottom - y_top))
        return (x_left, y_top, width, height)

    def _ensure_easyocr(self):
        if getattr(self, "_easyocr_reader", None) is not None:
            return self._easyocr_reader
        try:
            import easyocr  # type: ignore
            self._easyocr_reader = easyocr.Reader(['en'], gpu=False)
        except Exception:
            self._easyocr_reader = None
        return self._easyocr_reader

    def _ocr_unit_price_from_region(self, region: Tuple[int, int, int, int]) -> Optional[int]:
        try:
            import pyautogui  # type: ignore
            import pytesseract  # type: ignore
            from price_reader import _maybe_init_tesseract  # type: ignore
        except Exception:
            return None
        try:
            _maybe_init_tesseract()
        except Exception:
            pass
        try:
            img = pyautogui.screenshot(region=region)
        except Exception:
            return None
        # Scale
        avg_cfg = self.cfg.get("avg_price_area", {}) if isinstance(self.cfg.get("avg_price_area"), dict) else {}
        try:
            scale = float(avg_cfg.get("scale", 1.0))
        except Exception:
            scale = 1.0
        if not (0.6 <= scale <= 2.5):
            scale = 1.0
        if abs(scale - 1.0) > 1e-3:
            try:
                w, h = img.size
                from PIL import Image as _Image  # type: ignore
                img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), resample=getattr(_Image, 'LANCZOS', 1))
            except Exception:
                pass
        # Binarize
        bin_img = None
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
            arr = np.array(img)
            bgr = cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            _thr, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            from PIL import Image as _Image  # type: ignore
            bin_img = _Image.fromarray(th)
        except Exception:
            try:
                g = img.convert("L")
                th = g.point(lambda p: 255 if p > 128 else 0)
                bin_img = th
            except Exception:
                bin_img = img
        # OCR by engine
        engine = str(avg_cfg.get("ocr_engine", "tesseract")).lower()
        raw_text = ""
        if engine == "easyocr":
            reader = self._ensure_easyocr()
            if reader is not None:
                try:
                    import numpy as _np  # type: ignore
                    arr = _np.array(bin_img or img)
                    texts = reader.readtext(arr, detail=0)
                    raw_text = "\n".join(map(str, texts))
                except Exception:
                    raw_text = ""
            else:
                engine = "tesseract"
        elif engine in ("paddle", "paddleocr"):
            reader = self._ensure_paddleocr()
            if reader is not None:
                try:
                    import numpy as _np  # type: ignore
                    arr = _np.array(bin_img or img)
                    res = reader.ocr(arr, cls=True)
                    texts: list[str] = []
                    try:
                        if isinstance(res, list) and len(res) > 0 and isinstance(res[0], list) and len(res[0]) > 0 and isinstance(res[0][0], list):
                            for e in res[0]:
                                if isinstance(e, list) and len(e) >= 2 and isinstance(e[1], (list, tuple)):
                                    t = e[1][0]
                                    if t:
                                        texts.append(str(t))
                        else:
                            for e in (res or []):
                                if isinstance(e, list) and len(e) >= 2 and isinstance(e[1], (list, tuple)):
                                    t = e[1][0]
                                    if t:
                                        texts.append(str(t))
                    except Exception:
                        pass
                    raw_text = "\n".join(texts)
                except Exception:
                    raw_text = ""
            else:
                engine = "tesseract"
        if not raw_text:
            allow = str(avg_cfg.get("ocr_allowlist", "0123456789KM"))
            need = "KMkm"
            allow_ex = allow + "".join(ch for ch in need if ch not in allow)
            cfg = f"--oem 3 --psm 6 -c tessedit_char_whitelist={allow_ex}"
            try:
                raw_text = pytesseract.image_to_string(bin_img or img, config=cfg) or ""
            except Exception:
                return None
        # Parse
        lines = [ln.strip() for ln in str(raw_text).splitlines() if ln.strip()]
        def parse_num(s: str) -> Optional[int]:
            up = str(s).upper()
            mult = 1_000_000 if ("M" in up) else (1000 if ("K" in up) else 1)
            digits = "".join(ch for ch in up if ch.isdigit())
            if not digits:
                return None
            try:
                return int(digits) * mult
            except Exception:
                return None
        for ln in lines:
            val = parse_num(ln)
            if val is not None:
                return val
        import re
        toks = re.findall(r"[0-9]+[KMkm]?", str(raw_text))
        vals: List[int] = []
        for tk in toks:
            v = parse_num(tk)
            if v is not None:
                vals.append(v)
        if vals:
            return min(vals)
        return None
