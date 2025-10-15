import json
import os
import random
import time
from typing import Dict, List, Optional

import pyautogui
from ahk import AHK

class ImageBasedAutomator:
    """基于模板图片的自动化点击器。保留以便必要时回落使用。"""

    def __init__(
        self, image_dir: str = "images", confidence: float = 0.8, wait_time: float = 1.0
    ):
        self.image_dir = image_dir
        self.confidence = confidence
        self.wait_time = wait_time
        self.ahk = AHK()

        if not os.path.isdir(self.image_dir):
            raise FileNotFoundError(
                f"图片资源目录 '{self.image_dir}' 不存在。请先创建并放入模板图片。"
            )

    def _get_image_path(self, image_name: str) -> str:
        return os.path.join(self.image_dir, image_name)

    def find_and_click(
        self,
        image_name: str,
        clicks: int = 1,
        interval: float = 0.1,
        button: str = "left",
        timeout: float = 10.0,
    ) -> bool:
        """在屏幕查找图片并点击中心点。"""
        image_path = self._get_image_path(image_name)
        print(f"尝试模板匹配 '{image_name}'... (超时: {timeout}s)")

        start = time.time()
        while time.time() - start < timeout:
            try:
                location = pyautogui.locateCenterOnScreen(
                    image_path, confidence=self.confidence
                )
                if location:
                    print(f"找到 '{image_name}' 坐标: {location}，准备点击")
                    for i in range(clicks):
                        self.ahk.click(
                            location.x, location.y, button=button, coord_mode="Screen"
                        )
                        if i < clicks - 1:
                            time.sleep(random.uniform(interval * 0.8, interval * 1.2))
                    time.sleep(
                        random.uniform(self.wait_time * 0.8, self.wait_time * 1.2)
                    )
                    return True
            except pyautogui.PyAutoGUIException as e:
                print(f"匹配 '{image_name}' 时发生异常: {e}")
            time.sleep(0.5)

        print(f"在 {timeout} 秒内未找到模板 '{image_name}'。")
        return False

    def type_text(self, text: str, clear_first: bool = False) -> None:
        if clear_first:
            self.ahk.send("^a")
            self.ahk.send("{Delete}")
            time.sleep(0.3)
        self.ahk.send(text)
        time.sleep(random.uniform(self.wait_time * 0.8, self.wait_time * 1.2))


class MappingAutomator:
    """基于 key_mapping.json 的坐标点击自动化，优先使用坐标，必要时回落图片模板。"""

    def __init__(
        self,
        mapping_path: str = "key_mapping.json",
        wait_time: float = 1.0,
        image_automator: Optional[ImageBasedAutomator] = None,
        allow_image_fallback: bool = True,
    ) -> None:
        self.wait_time = wait_time
        self.mapping_path = mapping_path
        self.allow_image_fallback = allow_image_fallback
        self.image_automator = image_automator
        self.ahk = AHK()
        self.key_mapping: Dict[str, Dict[str, int]] = {}
        self._load_mapping()

    def _load_mapping(self) -> None:
        try:
            with open(self.mapping_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {}
        pts: Dict[str, Dict[str, int]] = {}
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict) and "x" in v and "y" in v:
                    try:
                        pts[k] = {"x": int(v["x"]), "y": int(v["y"])}
                    except Exception:
                        pass
        self.key_mapping = pts

    # ---- helpers ----
    def _get_point(self, label_candidates: List[str]) -> Optional[Dict[str, int]]:
        for key in label_candidates:
            if key in self.key_mapping:
                return self.key_mapping[key]
        return None

    def click_point(
        self,
        x: int,
        y: int,
        clicks: int = 1,
        interval: float = 0.1,
        button: str = "left",
    ) -> None:
        for i in range(max(1, clicks)):
            self.ahk.click(int(x), int(y), button=button, coord_mode="Screen")
            if i < clicks - 1:
                time.sleep(random.uniform(interval * 0.8, interval * 1.2))

    def click_by_labels(
        self,
        label_candidates: List[str],
        *,
        clicks: int = 1,
        interval: float = 0.1,
        button: str = "left",
    ) -> bool:
        pt = self._get_point(label_candidates)
        if not pt:
            return False
        x, y = pt.get("x"), pt.get("y")
        print(f"使用坐标点击: {label_candidates[0]} -> ({x}, {y})")
        self.click_point(x, y, clicks=clicks, interval=interval, button=button)
        time.sleep(random.uniform(self.wait_time * 0.8, self.wait_time * 1.2))
        return True

    def _click_preferring_mapping(
        self,
        label_candidates: List[str],
        image_name: Optional[str] = None,
        *,
        required: bool = False,
        clicks: int = 1,
        interval: float = 0.1,
        button: str = "left",
    ) -> bool:
        # 1) 坐标
        if self.click_by_labels(
            label_candidates, clicks=clicks, interval=interval, button=button
        ):
            return True
        # 2) 模板回落
        if (
            self.allow_image_fallback
            and image_name
            and self.image_automator is not None
        ):
            ok = self.image_automator.find_and_click(
                image_name, clicks=clicks, interval=interval, button=button
            )
            if ok:
                return True
        if required:
            print(f"未找到坐标且模板失败：{label_candidates} / {image_name}")
        return False

    def type_text(self, text: str, clear_first: bool = False) -> None:
        if clear_first:
            self.ahk.send("^a")
            self.ahk.send("{Delete}")
            time.sleep(0.2)
        self.ahk.send(text)
        time.sleep(random.uniform(self.wait_time * 0.8, self.wait_time * 1.2))

    def run_purchase_workflow(
        self, item_image_name, item_search_keyword: str, quantity: int = 1
    ) -> None:
        print("--- 使用坐标驱动的自动化流程 ---")

        # 1) 首页（可选）
        self._click_preferring_mapping(["首页按钮"], "btn_home.png", required=False)

        # 2) 市场（必需）
        if not self._click_preferring_mapping(
            ["市场按钮", "市场入口", "商城按钮", "市场"],
            "btn_market.png",
            required=True,
        ):
            return

        # 3) 搜索栏（必需）
        if self._click_preferring_mapping(
            ["市场搜索栏", "搜索框", "搜索输入"], "input_search.png", required=True
        ):
            self.type_text(item_search_keyword, clear_first=True)
        else:
            return

        # 4) 搜索按钮（必需）
        if not self._click_preferring_mapping(
            ["市场搜索按钮", "搜索按钮"], "btn_search.png", required=True
        ):
            return

        print("等待搜索结果...")
        time.sleep(random.uniform(1.8, 2.5))

        # 5) 第一个商品（必需）
        if not self._click_preferring_mapping(
            ["第一个商品", "第一个商品位置", "第1个商品"],
            item_image_name or None,
            required=True,
        ):
            return

        # 6) 刷新（可选）
        self._click_preferring_mapping(
            ["商品刷新位置", "刷新按钮"], "btn_refresh.png", required=False
        )

        # 7) 数量输入（必需）
        if self._click_preferring_mapping(
            ["数量输入框", "数量输入"], "input_quantity.png", required=True
        ):
            self.type_text(str(quantity), clear_first=True)
        else:
            return

        # 8) 购买（必需，若无坐标则回落图片）
        if not self._click_preferring_mapping(
            ["购买按钮", "买入按钮", "提交购买"], "btn_buy.png", required=True
        ):
            return

        print("已提交购买")

        # 9) 关闭（可选）
        self._click_preferring_mapping(
            ["商品关闭位置", "关闭按钮", "关闭"], "btn_close.png", required=False
        )


if __name__ == "__main__":
    # 目标物品信息
    TARGET_ITEM_IMAGE = "item_target_goods.png"  # 若 key_mapping 有“第一个商品”可留空
    TARGET_ITEM_KEYWORD = "BH燃料电池"  # 示例关键字，可按需调整
    PURCHASE_QUANTITY = 1

    try:
        image_automator = ImageBasedAutomator(confidence=0.8, wait_time=2)
        automator = MappingAutomator(
            mapping_path="key_mapping.json",
            wait_time=2,
            image_automator=image_automator,
            allow_image_fallback=True,
        )
        automator.run_purchase_workflow(
            item_image_name=TARGET_ITEM_IMAGE,
            item_search_keyword=TARGET_ITEM_KEYWORD,
            quantity=PURCHASE_QUANTITY,
        )
    except FileNotFoundError as e:
        print(e)
    except Exception as e:
        print(f"发生未知错误: {e}")
