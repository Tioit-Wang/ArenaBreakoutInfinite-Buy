"""
价格与库存读取工具。
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Tuple

import pyautogui

from super_buyer.config.loader import load_config
from super_buyer.services.ocr import NumberBox, recognize_numbers, recognize_text


def _load_ocr_config(config_path: str) -> Dict[str, any]:
    try:
        cfg = load_config(config_path)
        return dict(cfg.get("umi_ocr", {}) or {})
    except Exception:
        return {}


def read_texts_from_roi(
    region: Tuple[int, int, int, int],
    *,
    config_path: str = "config.json",
) -> List[str]:
    """截图并返回识别出的文本列表。"""
    pil = pyautogui.screenshot(region=region)
    ocr_cfg = _load_ocr_config(config_path)
    boxes = recognize_text(
        pil,
        base_url=str(ocr_cfg.get("base_url", "http://127.0.0.1:1224")),
        timeout=float(ocr_cfg.get("timeout_sec", 2.5) or 2.5),
        options=dict(ocr_cfg.get("options", {}) or {}),
    )
    return [(box.text or "").strip() for box in boxes if (box.text or "").strip()]


def read_lowest_price_from_roi(
    region: Tuple[int, int, int, int],
    *,
    price_min: int = 10,
    price_max: int = 10_000_000,
    config_path: str = "config.json",
    debug_save: Optional[str] = None,
) -> Optional[int]:
    pil = pyautogui.screenshot(region=region)
    ocr_cfg = _load_ocr_config(config_path)
    try:
        boxes: List[NumberBox] = recognize_numbers(
            pil,
            base_url=str(ocr_cfg.get("base_url", "http://127.0.0.1:1224")),
            timeout=float(ocr_cfg.get("timeout_sec", 2.5) or 2.5),
            options=dict(ocr_cfg.get("options", {}) or {}),
        )
    except Exception:
        return None
    values = [
        int(box.value)
        for box in boxes
        if getattr(box, "value", None) is not None
        and price_min <= int(box.value) <= price_max
    ]
    if debug_save:
        try:
            os.makedirs(os.path.dirname(debug_save), exist_ok=True)
            pil.save(debug_save)
        except Exception:
            pass
    return min(values) if values else None


def read_price_and_stock_from_roi(
    region: Tuple[int, int, int, int],
    *,
    price_min: int = 10,
    price_max: int = 10_000_000,
    qty_min: int = 0,
    qty_max: int = 1_000_000,
    config_path: str = "config.json",
    debug_save: Optional[str] = None,
) -> Tuple[int, int]:
    pil = pyautogui.screenshot(region=region)
    ocr_cfg = _load_ocr_config(config_path)
    try:
        boxes = recognize_numbers(
            pil,
            base_url=str(ocr_cfg.get("base_url", "http://127.0.0.1:1224")),
            timeout=float(ocr_cfg.get("timeout_sec", 2.5) or 2.5),
            options=dict(ocr_cfg.get("options", {}) or {}),
        )
    except Exception:
        return 0, 0
    width = max(1, int(region[2]))
    left_vals: List[int] = []
    right_vals: List[int] = []
    for box in boxes:
        value = getattr(box, "value", None)
        if value is None:
            continue
        x, _, w, _ = box.bbox
        center = int(x + w / 2)
        if center <= width // 2:
            if price_min <= int(value) <= price_max:
                left_vals.append(int(value))
        else:
            if qty_min <= int(value) <= qty_max:
                right_vals.append(int(value))
    if debug_save:
        try:
            os.makedirs(os.path.dirname(debug_save), exist_ok=True)
            pil.save(debug_save)
        except Exception:
            pass
    price = min(left_vals) if left_vals else 0
    qty = max(right_vals) if right_vals else 0
    return price, qty


def preprocess_variants_for_digits(image) -> List["numpy.ndarray"]:
    """生成若干二值化候选图，辅助手动 OCR 调试。"""
    variants: List["numpy.ndarray"] = []
    try:
        import cv2  # type: ignore
        import numpy as np  # type: ignore
    except Exception:
        return variants
    try:
        arr = np.array(image)
    except Exception:
        return variants
    if arr.ndim == 3:
        try:
            gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
        except Exception:
            gray = arr[:, :, 0]
    else:
        gray = arr
    variants.append(gray)
    try:
        _, otsu = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(otsu)
    except Exception:
        pass
    try:
        blur = cv2.GaussianBlur(gray, (3, 3), 0)
        _, thr = cv2.threshold(blur, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        variants.append(thr)
    except Exception:
        pass
    return variants


def _preprocess_variants_for_digits(image):
    return preprocess_variants_for_digits(image)


__all__ = [
    "preprocess_variants_for_digits",
    "_preprocess_variants_for_digits",
    "read_lowest_price_from_roi",
    "read_price_and_stock_from_roi",
    "read_texts_from_roi",
]
