"""
Runtime compatibility helpers.

This module provides a small shim to make PyAutoGUI image search calls
(`locateOnScreen`/`locateCenterOnScreen`) tolerate environments where
OpenCV is not installed. PyAutoGUI (via pyscreeze) only accepts the
`confidence` keyword when OpenCV is available; otherwise it raises:

    "The confidence keyword argument is only available if OpenCV is installed."

We patch those functions at runtime to silently drop the `confidence`
keyword so users can still perform exact (pixel-perfect) matching without
crashing. A one-time console warning is emitted to encourage installing
dependencies via `uv sync`.

Note: Without OpenCV the search becomes strict equality and may fail if
the template does not match the screen pixels exactly. Installing OpenCV
is still recommended for robust matching.
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
    """Patch PyAutoGUI to ignore `confidence` when OpenCV is missing.

    Returns True if OpenCV is available (patch not needed), otherwise False.
    Safe to call multiple times.
    """

    if _has_opencv():
        return True

    try:
        import pyautogui  # type: ignore
    except Exception:
        # PyAutoGUI not present; nothing to patch.
        return False

    # Idempotency: only patch once per process
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
        # Best-effort; ignore failures silently
        pass

    return False

