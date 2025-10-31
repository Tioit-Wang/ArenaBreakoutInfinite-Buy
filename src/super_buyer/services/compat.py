"""
运行时兼容性补丁。
"""

from __future__ import annotations

from typing import Any, Callable

_warned_once = False


def _has_opencv() -> bool:
    try:
        import cv2  # type: ignore  # noqa: F401

        return True
    except Exception:
        return False


def ensure_pyautogui_confidence_compat() -> bool:
    """保证在缺少 OpenCV 时 PyAutoGUI 忽略 confidence 参数。"""
    if _has_opencv():
        return True
    try:
        import pyautogui  # type: ignore
    except Exception:
        return False
    if getattr(pyautogui, "_wg1_conf_patch", False):
        return False

    def _wrap(fn: Callable[..., Any]) -> Callable[..., Any]:
        def _inner(*args: Any, **kwargs: Any) -> Any:
            global _warned_once
            if "confidence" in kwargs:
                kwargs.pop("confidence", None)
                if not _warned_once:
                    try:
                        print(
                            "[wg1] OpenCV 未安装，已忽略 confidence 参数并降级为像素级精确匹配。"
                            "建议先执行 `uv sync` 安装依赖以启用阈值匹配。"
                        )
                    except Exception:
                        pass
                    _warned_once = True
            return fn(*args, **kwargs)

        return _inner

    try:
        pyautogui.locateOnScreen = _wrap(pyautogui.locateOnScreen)  # type: ignore[attr-defined]
        pyautogui.locateCenterOnScreen = _wrap(pyautogui.locateCenterOnScreen)  # type: ignore[attr-defined]
        setattr(pyautogui, "_wg1_conf_patch", True)
    except Exception:
        pass
    return False


__all__ = ["ensure_pyautogui_confidence_compat"]

