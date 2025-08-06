import pyautogui
from ahk import AHK
import time
import os
import random



class ImageBasedAutomator:
    """
    一个基于图像识别的自动化操作类。
    通过在屏幕上查找指定的模板图片，来执行点击、输入等操作。
    """

    def __init__(self, image_dir="images", confidence=0.8, wait_time=1.0):
        """
        初始化自动化工具。
        :param image_dir: 存放模板图片的文件夹路径。
        :param confidence: 图像匹配的置信度，值越高匹配越精确。
        :param wait_time: 每次操作后的默认等待时间（秒）。
        """
        self.image_dir = image_dir
        self.confidence = confidence
        self.wait_time = wait_time
        self.ahk = AHK()

        if not os.path.isdir(self.image_dir):
            raise FileNotFoundError(
                f"错误：图片资源文件夹 '{self.image_dir}' 不存在。请先创建并放入模板图片。"
            )

    def _get_image_path(self, image_name):
        """获取图片的完整路径。"""
        return os.path.join(self.image_dir, image_name)

    def find_and_click(
        self, image_name, clicks=1, interval=0.1, button="left", timeout=10
    ):
        """
        在屏幕上查找指定图片并点击。
        :param image_name: 要查找的图片文件名 (例如 'btn_buy.png')。
        :param clicks: 点击次数。
        :param interval: 每次点击之间的间隔。
        :param button: 鼠标按键 ('left', 'right', 'middle')。
        :param timeout: 查找图片的超时时间（秒）。
        :return: 如果成功点击，返回True；否则返回False。
        """
        image_path = self._get_image_path(image_name)
        print(f"正在查找 '{image_name}'... (超时: {timeout}s)")

        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                # 保存截图为调试用
                screenshot = pyautogui.screenshot()
                screenshot.save(os.path.join(self.image_dir, "screenshot.png"))
                location = pyautogui.locateCenterOnScreen(
                    image_path, confidence=self.confidence
                )
                if location:
                    print(f"成功找到 '{image_name}' 在坐标: {location}，准备点击。")

                    # --- 使用 AHK 点击 ---
                    # AHK's click is blocking, so we handle multi-clicks in a loop for custom intervals.
                    for i in range(clicks):
                        self.ahk.click(location.x, location.y, button=button, coord_mode="Screen")
                        if i < clicks - 1:  # Don't sleep after the last click
                            time.sleep(random.uniform(interval * 0.8, interval * 1.2))

                    # 4. 操作后加入随机延迟
                    time.sleep(
                        random.uniform(self.wait_time * 0.8, self.wait_time * 1.2)
                    )
                    return True
            except pyautogui.PyAutoGUIException as e:
                # pyautogui.locateOnScreen 在某些系统上找不到图片会抛出异常
                print(f"查找图片 '{image_name}' 时发生错误: {e}")
                pass
            time.sleep(0.5)  # 短暂休眠，避免CPU占用过高

        print(f"错误：在 {timeout} 秒内未能找到图片 '{image_name}'。")
        return False

    def type_text(self, text, clear_first=False):
        """
        在当前焦点位置输入文本。
        :param text: 要输入的字符串。
        :param clear_first: 是否在输入前先清空输入框 (模拟 Ctrl+A, Delete)。
        """
        print(f"准备输入文本: '{text}'")
        if clear_first:
            print("正在清空输入框...")
            self.ahk.send("^a")  # Ctrl+A
            self.ahk.send("{Delete}")
            time.sleep(0.5)

        self.ahk.send(text)
        time.sleep(random.uniform(self.wait_time * 0.8, self.wait_time * 1.2))

    def run_purchase_workflow(self, item_image_name, item_search_keyword, quantity=1):
        """
        执行完整的自动化购买流程。
        :param item_image_name: 目标商品的图片文件名。
        :param item_search_keyword: 在搜索框中要输入的商品名称。
        :param quantity: 购买数量。
        """
        print("--- 开始执行自动化购买流程 ---")

        # 1. (可选) 点击首页按钮，返回主界面
        if self.find_and_click("btn_home.png"):
            print("已返回首页。")

        # 2. 点击市场按钮，进入市场
        if not self.find_and_click("btn_market.png"):
            print("流程中止：无法进入市场。")
            return

        # 3. 点击搜索框并输入商品名
        if self.find_and_click("input_search.png"):
            self.type_text(item_search_keyword)
        else:
            print("流程中止：找不到搜索框。")
            return

        # 4. 点击搜索按钮
        if not self.find_and_click("btn_search.png"):
            print("流程中止：找不到搜索按钮。")
            return

        print("等待搜索结果...")
        time.sleep(random.uniform(1.8, 2.5))  # 等待搜索结果加载

        # 5. 点击目标商品
        if not self.find_and_click(item_image_name):
            print(f"流程中止：找不到目标商品 '{item_image_name}'。")
            return

        # 6. 点击刷新按钮
        if not self.find_and_click("btn_refresh.png"):
            print("警告：未找到刷新按钮，继续执行。")

        # 7. 输入购买数量
        if self.find_and_click("input_quantity.png"):
            self.type_text(str(quantity), clear_first=True)
        else:
            print("流程中止：找不到购买数量输入框。")
            return

        # 8. 点击购买按钮
        if not self.find_and_click("btn_buy.png"):
            print("流程中止：找不到购买按钮。")
            return

        print("购买操作已提交。")

        # 9. 关闭当前界面
        if self.find_and_click("btn_close.png"):
            print("已关闭购买界面。")

        print("--- 自动化购买流程执行完毕 ---")


if __name__ == "__main__":
    # --- 使用说明 ---
    # 1. 确保已安装所需库:
    #    pip install pyautogui opencv-python ahk
    #
    # 2. 确保在脚本同目录下已创建 'images' 文件夹，并放入所有模板图片。
    #    图片命名请参考 ImageBasedAutomator 类中的调用。
    #
    # 3. 修改下面的参数以符合您的需求。

    # 要搜索和购买的商品信息
    TARGET_ITEM_IMAGE = "item_target_goods.png"  # 目标商品的截图文件名
    TARGET_ITEM_KEYWORD = "BH步兵胸挂"  # 要在搜索框输入的文字
    PURCHASE_QUANTITY = 1  # 购买数量

    try:
        automator = ImageBasedAutomator(confidence=0.8, wait_time=2)
        automator.run_purchase_workflow(
            item_image_name=TARGET_ITEM_IMAGE,
            item_search_keyword=TARGET_ITEM_KEYWORD,
            quantity=PURCHASE_QUANTITY,
        )
    except FileNotFoundError as e:
        print(e)
    except Exception as e:
        print(f"发生未知错误: {e}")
