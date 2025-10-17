import threading
import time
from typing import Callable, Optional, List, Dict, Any, Tuple

from auto_clicker import MappingAutomator, ImageBasedAutomator
from price_reader import read_lowest_price_from_config
from app_config import load_config  # type: ignore


class AutoBuyer:
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
        wait_time: float = 1.0,
        on_log: Optional[Callable[[str], None]] = None,
        allow_image_fallback: bool = True,
    ) -> None:
        self.item_name = item_name
        self.price_threshold = int(price_threshold)
        self.target_total = int(target_total)
        self.max_per_order = max(1, int(max_per_order))
        self.on_log = on_log or (lambda s: None)

        img_auto = ImageBasedAutomator(image_dir="images", confidence=0.85, wait_time=wait_time)
        self.automator = MappingAutomator(
            mapping_path="key_mapping.json",
            wait_time=wait_time,
            image_automator=img_auto,
            allow_image_fallback=allow_image_fallback,
        )

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._purchased = 0

        # Runtime config for templates and ROI settings
        try:
            self.cfg: Dict[str, Any] = load_config("config.json")
        except Exception:
            self.cfg = {}

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
        if self._stop.wait(1.8):
            return False
        return True

    def _run(self) -> None:
        # New monitoring loop that uses 平均价格区域设置 + 模板确认成功
        self._run_avg_roi_loop()
        return

        # Monitoring loop
        while not self._stop.is_set() and self._purchased < self.target_total:
            # Enter first item detail (required)
            if not self.automator._click_preferring_mapping(["第一个商品", "第一个商品位置", "第1个商品"], None, required=True):
                self.log("未定位到第一个商品，重试…")
                if self._stop.wait(1.0):
                    break
                continue

            # Read price via ROI
            price = read_lowest_price_from_config(mapping_path="key_mapping.json", debug=False)
            if price is None:
                self.log("未能解析价格，关闭详情后重试…")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                if self._stop.wait(0.8):
                    break
                continue

            self.log(f"读取价格: {price}")
            if price <= self.price_threshold and self._purchased < self.target_total:
                remaining = self.target_total - self._purchased
                qty = max(1, min(self.max_per_order, remaining))
                # Quantity input
                if self.automator._click_preferring_mapping(["数量输入框", "数量输入"], "input_quantity.png", required=True):
                    self.automator.type_text(str(qty), clear_first=True)
                else:
                    self.log("定位数量输入框失败，放弃本次购买…")
                    self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    if self._stop.wait(0.8):
                        break
                    continue

                # Buy
                if not self.automator._click_preferring_mapping(["购买按钮", "买入按钮", "提交购买"], "btn_buy.png", required=True):
                    self.log("点击购买失败，关闭详情…")
                    self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    if self._stop.wait(0.8):
                        break
                    continue

                # Optional: verify success by template (if provided)
                # For MVP, assume success after a short wait
                if self._stop.wait(1.2):
                    break
                self._purchased += qty
                self.log(f"购买成功，累计已购: {self._purchased}/{self.target_total}")

                # Close detail
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                time.sleep(0.6)
            else:
                # Not buying — close detail and retry by re-entering next loop
                self.log("价格高于阈值，关闭详情后重试…")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                time.sleep(0.6)

        if self._purchased >= self.target_total:
            self.log("目标购买数已达成，停止。")
        else:
            self.log("已停止。")


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
                    if self._stop.wait(0.8):
                        break
                    continue
                opened = True

            region = self._avg_price_roi_region()
            if region is None:
                self.log("未定位到‘购买’按钮或 ROI，关闭详情后重试。")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.6):
                    break
                continue

            unit_price = self._ocr_unit_price_from_region(region)
            if unit_price is None:
                self.log("未能识别到单价，尝试刷新后重试。")
                self._press_refresh()
                if self._stop.wait(0.3):
                    break
                continue

            self.log(f"单价: {unit_price}")
            if unit_price <= self.price_threshold and self._purchased < self.target_total:
                remaining = self.target_total - self._purchased
                init_qty = min(self.max_per_order, remaining)
                # 输入后立刻复核单价，不通过则数量/2，再/2（均需>1）
                ok = self._attempt_purchase_with_rechecks(init_qty)
                # 完成一次尝试后关闭详情并进入下一轮
                self.automator._click_preferring_mapping(["��Ʒ�ر�λ��", "�ر�"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.4):
                    break
                if not ok:
                    # 未成功则刷新以便获取新报价
                    self._press_refresh()
                continue

                if self.automator._click_preferring_mapping(["数量输入框", "购买数量"], "input_quantity.png", required=True):
                    self.automator.type_text(str(qty), clear_first=True)
                else:
                    self.log("未能定位数量输入框，关闭详情后重试。")
                    self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    opened = False
                    if self._stop.wait(0.5):
                        break
                    continue

                if not self.automator._click_preferring_mapping(["购买按钮", "确认按钮", "提交订单"], "btn_buy.png", required=True):
                    self.log("点击购买失败，关闭详情后重试。")
                    self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    opened = False
                    if self._stop.wait(0.5):
                        break
                    continue

                if self._stop.wait(0.2):
                    break
                if self._check_purchase_success():
                    self._purchased += qty
                    self.log(f"购买成功，累计已购: {self._purchased}/{self.target_total}")
                else:
                    self.log("未检测到成功模板，疑似失败或网络延迟。")

                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                opened = False
                if self._stop.wait(0.4):
                    break
            else:
                self._press_refresh()
                if self._stop.wait(0.3):
                    break

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

        avg_cfg = self.cfg.get("avg_price_area", {}) if isinstance(self.cfg.get("avg_price_area"), dict) else {}
        allow = str(avg_cfg.get("ocr_allowlist", "0123456789KM"))
        need = "KMkm"
        allow_ex = allow + "".join(ch for ch in need if ch not in allow)
        cfg = f"--oem 3 --psm 6 -c tessedit_char_whitelist={allow_ex}"
        try:
            raw_text = pytesseract.image_to_string(img, config=cfg) or ""
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

    def _press_refresh(self) -> None:
        """Best-effort refresh on the detail page (optional)."""
        try:
            self.automator._click_preferring_mapping(["商品刷新位置", "刷新按钮"], "btn_refresh.png", required=False)
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
        # 每次数量需为整数且 >1
        if q < 2:
            self.log("数量小于2，放弃本轮。")
            return False

        # 3 次尝试：q, q//2, q//4（均需 >=2）
        attempts = []
        cur = q
        for _ in range(3):
            if cur >= 2:
                attempts.append(int(cur))
            cur = cur // 2
        # 使用与现有逻辑一致的候选标签（坐标优先，失败回退图片）
        qty_labels = ["���������", "��������"]
        for qty in attempts:
            if self._stop.is_set():
                return False
            # 点击数量并输入
            if self.automator._click_preferring_mapping(qty_labels, "input_quantity.png", required=True):
                self.automator.type_text(str(int(qty)), clear_first=True)
            else:
                self.log("未能定位数量输入框，放弃本轮。")
                return False

            # 重新计算 ROI 并复核单价
            region = self._avg_price_roi_region()
            if region is None:
                self.log("复核 ROI 失败，尝试下一个数量。")
                continue
            chk_price = self._ocr_unit_price_from_region(region)
            if chk_price is None:
                self.log("复核价格失败，尝试下一个数量。")
                continue
            self.log(f"复核单价: {chk_price}")
            if chk_price > self.price_threshold:
                # 价格不符合，尝试更小数量
                continue

            # 价格符合，点击购买
            if not self.automator._click_preferring_mapping(["����ť", "ȷ�ϰ�ť", "�ύ����"], "btn_buy.png", required=True):
                self.log("点击购买失败。")
                continue
            if self._stop.wait(0.2):
                return False
            if self._check_purchase_success():
                self._purchased += int(qty)
                self.log(f"购买成功，累计已购: {self._purchased}/{self.target_total}")
                return True
            else:
                self.log("未检测到成功模板，疑似失败或延迟。")
                # 本轮尝试失败，继续下一个更小数量
                continue

        # 全部尝试失败
        self.log("两次/2复核均未通过，放弃本轮。")
        return False

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
        wait_time: float = 1.0,
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
            self.items.append(d)
        self.on_log = on_log or (lambda s: None)
        self.on_item_update = on_item_update or (lambda i, d: None)

        img_auto = ImageBasedAutomator(image_dir="images", confidence=0.85, wait_time=wait_time)
        self.automator = MappingAutomator(
            mapping_path="key_mapping.json",
            wait_time=wait_time,
            image_automator=img_auto,
            allow_image_fallback=allow_image_fallback,
        )
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

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
        if self._stop.wait(1.2):
            return True
        return True

    def _attempt_one(self, idx: int, it: Dict[str, Any]) -> None:
        """After search results are visible, loop open/close details.

        If condition not met, close detail and immediately reopen details
        instead of navigating back home and re-searching.
        Bounded retry loop to avoid deadlocks.
        """
        name = str(it.get("item_name", ""))
        try:
            threshold = int(it.get("price_threshold", 0))
            target_total = int(it.get("target_total", 0))
            max_per_order = int(it.get("max_per_order", 120))
        except Exception:
            self.log(f"[{name}] 配置无效（阈值/目标/每单），跳过。")
            return

        # Safety: don't spin forever if UI is abnormal
        max_loops = 20
        loops = 0

        while not self._stop.is_set():
            loops += 1
            if loops > max_loops:
                self.log(f"[{name}] 连续尝试超过 {max_loops} 次，暂停该商品。")
                break

            # Already done for this item?
            try:
                purchased_now = int(it.get("purchased", 0))
            except Exception:
                purchased_now = 0
            if purchased_now >= target_total:
                self.log(f"[{name}] 已达目标 {purchased_now}/{target_total}，结束该商品循环。")
                break

            # Enter first item detail
            if not self.automator._click_preferring_mapping([
                "第一个商品", "第一个商品位置", "第1个商品"
            ], None, required=True):
                self.log(f"[{name}] 未定位到第一个商品，稍后重试。")
                if self._stop.wait(0.8):
                    break
                # 无法进入详情时，退出本轮，由上层重新导航
                break

            # Read price
            price = read_lowest_price_from_config(mapping_path="key_mapping.json", debug=False)
            if price is None:
                self.log(f"[{name}] 未能解析价格，关闭详情后直接重试…")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                if self._stop.wait(0.5):
                    break
                # 不刷新，直接下一轮打开详情
                continue

            self.log(f"[{name}] 读取价格: {price}")
            if price <= threshold and purchased_now < target_total:
                # Decide quantity for this order
                remaining = target_total - purchased_now
                qty = max(1, min(max_per_order, remaining))

                # Quantity input
                if self.automator._click_preferring_mapping(["数量输入框", "数量输入"], "input_quantity.png", required=True):
                    self.automator.type_text(str(qty), clear_first=True)
                else:
                    self.log(f"[{name}] 定位数量输入框失败，关闭详情重试…")
                    self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    if self._stop.wait(0.4):
                        break
                    continue

                # Buy
                if not self.automator._click_preferring_mapping(["购买按钮", "买入按钮", "提交购买"], "btn_buy.png", required=True):
                    self.log(f"[{name}] 点击购买失败，关闭详情…")
                    self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    if self._stop.wait(0.4):
                        break
                    continue

                if self._stop.wait(1.0):
                    break

                it["purchased"] = int(it.get("purchased", 0)) + qty
                self.log(f"[{name}] 购买成功，累计 {it['purchased']}/{target_total}")
                self.on_item_update(idx, dict(it))

                # Close detail and continue loop if still below target
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                if self._stop.wait(0.5):
                    break
                # 若未达目标，继续开启详情，避免回到首页
                continue
            else:
                # Not buying this round: close and directly reopen later (no refresh, no home)
                self.log(f"[{name}] 价格高于阈值或已完成，关闭详情后直接重试…")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                if self._stop.wait(0.5):
                    break
                # 继续 while 循环，直接再次打开详情
                continue

    def _run(self) -> None:
        while not self._stop.is_set():
            all_done = True
            for idx, it in enumerate(self.items):
                if self._stop.is_set():
                    break
                if not it.get("enabled", True):
                    continue
                if int(it.get("purchased", 0)) >= int(it.get("target_total", 0)):
                    continue
                all_done = False
                if not self._nav_to_search(str(it.get("item_name", ""))):
                    continue
                self._attempt_one(idx, it)
                time.sleep(0.4)
            if all_done:
                self.log("所有任务均已完成")
                break
        self.log("多任务已停止")
