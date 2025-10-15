import threading
import time
from typing import Callable, Optional, List, Dict, Any

from auto_clicker import MappingAutomator, ImageBasedAutomator
from price_reader import read_lowest_price_from_config


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

    def log(self, msg: str) -> None:
        self.on_log(msg)

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
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
        time.sleep(1.8)
        return True

    def _run(self) -> None:
        self._purchased = 0
        if not self._nav_to_search():
            return

        # Monitoring loop
        while not self._stop.is_set() and self._purchased < self.target_total:
            # Enter first item detail (required)
            if not self.automator._click_preferring_mapping(["第一个商品", "第一个商品位置", "第1个商品"], None, required=True):
                self.log("未定位到第一个商品，重试…")
                time.sleep(1.0)
                continue

            # Read price via ROI
            price = read_lowest_price_from_config(mapping_path="key_mapping.json", debug=False)
            if price is None:
                self.log("未能解析价格，关闭详情并刷新…")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                time.sleep(0.8)
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
                    time.sleep(0.8)
                    continue

                # Buy
                if not self.automator._click_preferring_mapping(["购买按钮", "买入按钮", "提交购买"], "btn_buy.png", required=True):
                    self.log("点击购买失败，关闭详情…")
                    self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                    time.sleep(0.8)
                    continue

                # Optional: verify success by template (if provided)
                # For MVP, assume success after a short wait
                time.sleep(1.2)
                self._purchased += qty
                self.log(f"购买成功，累计已购: {self._purchased}/{self.target_total}")

                # Close detail
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                time.sleep(0.6)
            else:
                # Not buying — close detail and refresh by re-entering next loop
                self.log("价格高于阈值，关闭详情刷新…")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                time.sleep(0.6)

        if self._purchased >= self.target_total:
            self.log("目标购买数已达成，停止。")
        else:
            self.log("已停止。")


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
        time.sleep(1.2)
        return True

    def _attempt_one(self, idx: int, it: Dict[str, Any]) -> None:
        # Enter first item
        if not self.automator._click_preferring_mapping(["第一个商品", "第一个商品位置", "第1个商品"], None, required=True):
            self.log(f"[{it['item_name']}] 未定位到第一个商品")
            return

        price = read_lowest_price_from_config(mapping_path="key_mapping.json", debug=False)
        if price is None:
            self.log(f"[{it['item_name']}] 未解析到价格")
            self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
            time.sleep(0.4)
            return

        self.log(f"[{it['item_name']}] 读取价格: {price}")
        if price <= int(it.get("price_threshold", 0)) and int(it.get("purchased", 0)) < int(it.get("target_total", 0)):
            remaining = int(it.get("target_total", 0)) - int(it.get("purchased", 0))
            qty = max(1, min(int(it.get("max_per_order", 120)), remaining))
            # Quantity input
            if self.automator._click_preferring_mapping(["数量输入框", "数量输入"], "input_quantity.png", required=True):
                self.automator.type_text(str(qty), clear_first=True)
            else:
                self.log(f"[{it['item_name']}] 定位数量输入框失败")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                return
            # Buy
            if not self.automator._click_preferring_mapping(["购买按钮", "买入按钮", "提交购买"], "btn_buy.png", required=True):
                self.log(f"[{it['item_name']}] 点击购买失败")
                self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
                return
            time.sleep(1.0)
            it["purchased"] = int(it.get("purchased", 0)) + qty
            self.log(f"[{it['item_name']}] 购买成功，累计 {it['purchased']}/{it['target_total']}")
            self.on_item_update(idx, dict(it))
            self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
        else:
            self.log(f"[{it['item_name']}] 高于阈值或已完成，刷新下一个")
            self.automator._click_preferring_mapping(["商品关闭位置", "关闭"], "btn_close.png", required=False)
            time.sleep(0.3)

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
