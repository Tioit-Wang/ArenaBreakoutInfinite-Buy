"""
屏幕操作封装。
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, Tuple

from super_buyer.core.common import safe_sleep


class ScreenOps:
    """基于 PyAutoGUI/OpenCV 的屏幕操作工具。"""

    def __init__(self, cfg: Dict[str, Any], step_delay: float = 0.01) -> None:
        self.cfg = cfg
        self.step_delay = float(step_delay or 0.01)
        try:
            import pyautogui  # type: ignore

            _ = getattr(pyautogui, "locateOnScreen")
        except Exception as exc:
            raise RuntimeError(
                "缺少 pyautogui 或其依赖，请安装 pyautogui + opencv-python。"
            ) from exc

    @property
    def _pg(self):  # type: ignore
        import pyautogui  # type: ignore

        return pyautogui

    def _template(self, key: str) -> Tuple[str, float]:
        template = (self.cfg.get("templates", {}) or {}).get(key) or {}
        path = str(template.get("path", ""))
        confidence = float(template.get("confidence", 0.85) or 0.85)
        return path, confidence

    def locate(
        self,
        tpl_key: str,
        region: Optional[Tuple[int, int, int, int]] = None,
        timeout: float = 0.0,
    ) -> Optional[Tuple[int, int, int, int]]:
        path, confidence = self._template(tpl_key)
        if not path or not os.path.exists(path):
            return None
        end = time.time() + max(0.0, float(timeout or 0.0))
        while True:
            try:
                box = self._pg.locateOnScreen(path, confidence=confidence, region=region)
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
            safe_sleep(self.step_delay)

    def click_center(
        self,
        box: Tuple[int, int, int, int],
        clicks: int = 1,
        interval: float = 0.02,
    ) -> None:
        left, top, width, height = box
        x = int(left + width / 2)
        y = int(top + height / 2)
        try:
            self._pg.moveTo(x, y)
            for idx in range(max(1, int(clicks))):
                self._pg.click(x, y)
                if idx + 1 < clicks:
                    safe_sleep(interval)
        except Exception:
            pass
        safe_sleep(self.step_delay)

    def click_point(self, x: int, y: int, *, clicks: int = 1, interval: float = 0.02) -> None:
        try:
            self._pg.moveTo(int(x), int(y))
            for idx in range(max(1, int(clicks))):
                self._pg.click(int(x), int(y))
                if idx + 1 < clicks:
                    safe_sleep(interval)
        except Exception:
            pass
        safe_sleep(self.step_delay)

    def drag(
        self,
        start: Tuple[int, int],
        end: Tuple[int, int],
        *,
        duration: float = 0.2,
        button: str = "left",
    ) -> None:
        try:
            self._pg.moveTo(int(start[0]), int(start[1]))
            self._pg.dragTo(
                int(end[0]),
                int(end[1]),
                duration=max(0.0, float(duration)),
                button=str(button or "left"),
            )
        except Exception:
            pass
        safe_sleep(self.step_delay)

    def type_text(self, text: str, *, clear_first: bool = True) -> None:
        try:
            if clear_first:
                self._pg.hotkey("ctrl", "a")
                safe_sleep(0.02)
                self._pg.press("backspace")
                safe_sleep(0.02)
            self._pg.typewrite(str(text), interval=max(0.0, self.step_delay))
        except Exception:
            pass
        safe_sleep(self.step_delay)

    def screenshot_region(self, region: Tuple[int, int, int, int]):
        left, top, width, height = region
        try:
            return self._pg.screenshot(
                region=(int(left), int(top), int(width), int(height))
            )
        except Exception:
            return None


__all__ = ["ScreenOps"]

