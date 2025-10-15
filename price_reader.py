import os
import shutil
import json
from typing import List, Optional, Tuple

import pyautogui

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
    import pytesseract  # type: ignore
except Exception:
    cv2 = None  # type: ignore
    np = None  # type: ignore
    pytesseract = None  # type: ignore


def _maybe_init_tesseract() -> None:
    if pytesseract is None:
        return
    ts_cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", None)

    def _is_valid(cmd: str) -> bool:
        # If a full path is provided, check it exists; otherwise rely on PATH
        return os.path.isabs(cmd) and os.path.exists(cmd) or shutil.which(cmd) is not None

    if isinstance(ts_cmd, str) and _is_valid(ts_cmd):
        return

    # Try common Windows install locations
    for p in (
        r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe",
        r"C:\\Program Files (x86)\\Tesseract-OCR\\tesseract.exe",
    ):
        if os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            return

    # As a last resort, leave default and let pytesseract raise a helpful error
    # (Callers may handle/log this.)


def _preprocess_for_digits(pil_img):
    if cv2 is None or np is None:
        return None
    img = np.array(pil_img)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
    gray = cv2.normalize(gray, None, 0, 255, cv2.NORM_MINMAX)
    _, th = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = np.ones((2, 2), np.uint8)
    th = cv2.morphologyEx(th, cv2.MORPH_CLOSE, kernel, iterations=1)
    return th


def _ocr_digits(bin_img) -> List[int]:
    if pytesseract is None:
        return []
    _maybe_init_tesseract()
    config = "--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789,"
    try:
        data = pytesseract.image_to_data(
            bin_img, config=config, output_type=pytesseract.Output.DICT
        )
    except Exception:
        return []
    vals: List[int] = []
    for txt in data.get("text", []):
        if not txt:
            continue
        digits = "".join(ch for ch in txt if ch.isdigit())
        if not digits:
            continue
        try:
            vals.append(int(digits))
        except Exception:
            pass
    return vals


def _ocr_digit_boxes(bin_img) -> List[Tuple[int, int]]:
    """Return list of (value, x_center) for each detected numeric token.

    Falls back to empty list if OCR is unavailable.
    """
    if pytesseract is None:
        return []
    _maybe_init_tesseract()
    config = "--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789,"
    try:
        data = pytesseract.image_to_data(
            bin_img, config=config, output_type=pytesseract.Output.DICT
        )
    except Exception:
        return []
    out: List[Tuple[int, int]] = []
    n = len(data.get("text", []))
    for i in range(n):
        txt = data.get("text", [""])[i] or ""
        digits = "".join(ch for ch in txt if ch.isdigit())
        if not digits:
            continue
        try:
            val = int(digits)
        except Exception:
            continue
        try:
            x = int(data.get("left", [0])[i]) + int(data.get("width", [0])[i]) // 2
        except Exception:
            x = int(data.get("left", [0])[i])
        out.append((val, x))
    return out


def read_lowest_price_from_roi(
    region: Tuple[int, int, int, int],
    price_min: int = 10,
    price_max: int = 10_000_000,
    debug_save: Optional[str] = None,
) -> Optional[int]:
    """Capture `region=(left, top, width, height)` and return min price."""
    if cv2 is None or np is None or pytesseract is None:
        print("[OCR] 缺少依赖: 请安装 opencv-python、pytesseract，并在系统安装 Tesseract。")
        return None
    pil = pyautogui.screenshot(region=region)
    proc = _preprocess_for_digits(pil)
    if proc is None:
        return None
    if debug_save:
        try:
            cv2.imwrite(debug_save, proc)
        except Exception:
            pass
    vals = _ocr_digits(proc)
    cand = [v for v in vals if price_min <= v <= price_max]
    if not cand:
        return None
    return min(cand)


def read_price_and_stock_from_roi(
    region: Tuple[int, int, int, int],
    price_min: int = 10,
    price_max: int = 10_000_000,
    qty_min: int = 0,
    qty_max: int = 1_000_000,
    debug_save: Optional[str] = None,
) -> Tuple[int, int]:
    """Capture region and return (price, quantity). Missing values -> 0.

    Heuristic: split by the region mid-X; take price from left side and
    quantity from right side. Choose a reasonable candidate within range.
    """
    if cv2 is None or np is None or pytesseract is None:
        print("[OCR] ȱ������: �밲װ opencv-python��pytesseract������ϵͳ��װ Tesseract��")
        return 0, 0
    pil = pyautogui.screenshot(region=region)
    proc = _preprocess_for_digits(pil)
    if proc is None:
        return 0, 0
    if debug_save:
        try:
            cv2.imwrite(debug_save, proc)
        except Exception:
            pass
    boxes = _ocr_digit_boxes(proc)
    if not boxes:
        return 0, 0
    # midpoint along X to split left (price) vs right (qty)
    mid_x = region[2] // 2
    left_vals = [v for v, x in boxes if x <= mid_x and price_min <= v <= price_max]
    right_vals = [v for v, x in boxes if x > mid_x and qty_min <= v <= qty_max]
    price = 0
    qty = 0
    if left_vals:
        # choose the minimum as the displayed level is usually the target price
        price = min(left_vals)
    if right_vals:
        # choose the maximum quantity observed on the right
        qty = max(right_vals)
    return int(price or 0), int(qty or 0)


def _load_key_mapping(path: str = "key_mapping.json") -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def read_lowest_price_from_config(
    mapping_path: str = "key_mapping.json", debug: bool = False
) -> Optional[int]:
    """Read ROI from mapping file and return min price.

    Expects keys:
      - 价格区域左上: {x, y}
      - 价格区域右下: {x, y}
    """
    mapping = _load_key_mapping(mapping_path)
    tl = mapping.get("价格区域左上")
    br = mapping.get("价格区域右下")
    if not (isinstance(tl, dict) and isinstance(br, dict)):
        print("[OCR] key_mapping.json 缺少 '价格区域左上/右下'，请先标定 ROI。")
        return None
    try:
        l, t = int(tl["x"]), int(tl["y"])
        r, b = int(br["x"]), int(br["y"])
        if r <= l or b <= t:
            print("[OCR] ROI 无效：右下坐标不应小于左上坐标。")
            return None
        region = (l, t, r - l, b - t)
    except Exception:
        print("[OCR] 解析 ROI 失败，请检查 key_mapping.json。")
        return None
    save = os.path.join("images", "_debug_price_roi.png") if debug else None
    return read_lowest_price_from_roi(region, debug_save=save)


def read_price_and_stock_from_config(
    mapping_path: str = "key_mapping.json", debug: bool = False
) -> Tuple[int, int]:
    """Read ROI from mapping and return (price, quantity). 0 means not found."""
    mapping = _load_key_mapping(mapping_path)
    tl = mapping.get("�۸���������")
    br = mapping.get("�۸���������")
    if not (isinstance(tl, dict) and isinstance(br, dict)):
        print("[OCR] key_mapping.json ȱ�� '�۸���������/����'�����ȱ궨 ROI��")
        return 0, 0
    try:
        l, t = int(tl["x"]), int(tl["y"])
        r, b = int(br["x"]), int(br["y"])
        if r <= l or b <= t:
            print("[OCR] ROI ��Ч���������겻ӦС���������ꡣ")
            return 0, 0
        region = (l, t, r - l, b - t)
    except Exception:
        print("[OCR] ���� ROI ʧ�ܣ����� key_mapping.json��")
        return 0, 0
    save = os.path.join("images", "_debug_price_roi.png") if debug else None
    return read_price_and_stock_from_roi(region, debug_save=save)
