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
    """Backward-compatible single variant preprocessor (kept for reference).

    New code uses `_preprocess_variants_for_digits` to try multiple pipelines
    for low-contrast text. This function returns a single image for legacy
    callers.
    """
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


def _preprocess_variants_for_digits(pil_img):
    """Generate multiple binarized variants to cope with low contrast.

    Returns a list of binary OpenCV images. Variants include:
    - Channels: gray, green channel, HSV-V
    - CLAHE contrast boosting
    - Otsu threshold and adaptive Gaussian threshold
    - Morphological open/close to reduce noise or bridge thin strokes
    - Inverted versions for robustness
    - Top-hat/Black-hat enhancement for light/dark text on background
    """
    if cv2 is None or np is None:
        return []

    src = np.array(pil_img)
    bgr = cv2.cvtColor(src, cv2.COLOR_RGB2BGR)

    # Prepare channels
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gchan = bgr[:, :, 1]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    vchan = hsv[:, :, 2]

    chans = [gray, gchan, vchan]

    # Resize for OCR
    chans = [cv2.resize(c, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC) for c in chans]

    # CLAHE to boost contrast locally
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    chans = [clahe.apply(c) for c in chans]

    # Structuring elements
    k2 = np.ones((2, 2), np.uint8)
    k3 = np.ones((3, 3), np.uint8)

    variants = []

    def add_variant(img_bin):
        if img_bin is None:
            return
        # Ensure binary uint8
        v = (img_bin > 0).astype(np.uint8) * 255
        variants.append(v)
        # Inverted
        variants.append(255 - v)

    for c in chans:
        # Otsu
        _, otsu = cv2.threshold(c, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        add_variant(otsu)
        # Adaptive Gaussian
        try:
            adp = cv2.adaptiveThreshold(c, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                        cv2.THRESH_BINARY, 31, 2)
            add_variant(adp)
        except Exception:
            pass
        # Top-hat (light text on dark background)
        try:
            top = cv2.morphologyEx(c, cv2.MORPH_TOPHAT, k3, iterations=1)
            _, th_top = cv2.threshold(top, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            add_variant(th_top)
        except Exception:
            pass
        # Black-hat (dark text on light background)
        try:
            blk = cv2.morphologyEx(c, cv2.MORPH_BLACKHAT, k3, iterations=1)
            _, th_blk = cv2.threshold(blk, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            add_variant(th_blk)
        except Exception:
            pass

    # --- Color-aware variants tailored for near (#070708 bg, #606867 text) ---
    try:
        # Colors in RGB: bg=(7,7,8), txt=(96,104,103); convert to BGR since `bgr` is in BGR
        bg_bgr = np.array([8, 7, 7], dtype=np.float32)
        txt_bgr = np.array([103, 104, 96], dtype=np.float32)
        V = txt_bgr - bg_bgr
        V_norm2 = float(np.dot(V, V)) if float(np.dot(V, V)) != 0 else 1.0

        # 1) Closer-to-text-than-background mask
        diff_bg = bgr.astype(np.float32) - bg_bgr[None, None, :]
        diff_txt = bgr.astype(np.float32) - txt_bgr[None, None, :]
        d_bg = np.sqrt(np.maximum(0.0, np.sum(diff_bg * diff_bg, axis=2)))
        d_txt = np.sqrt(np.maximum(0.0, np.sum(diff_txt * diff_txt, axis=2)))
        m_close = (d_txt + 5.0 < d_bg)  # margin to be safely closer to text color
        m_close &= (d_txt < 220.0)  # clamp outliers
        m1 = (m_close.astype(np.uint8)) * 255
        add_variant(m1)

        # 2) Linear color projection along (txt - bg)
        #    s ~ 0 for bg, ~1 for text; clamp to [0,1], then Otsu
        proj = np.sum((bgr.astype(np.float32) - bg_bgr[None, None, :]) * V[None, None, :], axis=2) / V_norm2
        proj = np.clip(proj, 0.0, 1.0)
        proj8 = (proj * 255.0).astype(np.uint8)
        _, th_proj = cv2.threshold(proj8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        add_variant(th_proj)
    except Exception:
        pass

    # Post-process: light open/close and light dilation to thicken strokes
    processed = []
    H, W = bgr.shape[:2]
    for v in variants:
        try:
            # Remove salt-pepper noise
            x = cv2.morphologyEx(v, cv2.MORPH_OPEN, k2, iterations=1)
            # Bridge thin gaps
            x = cv2.morphologyEx(x, cv2.MORPH_CLOSE, k2, iterations=1)
            # Slightly thicken
            x = cv2.dilate(x, k2, iterations=1)

            # Remove wide, thin horizontal bars (likely progress bars)
            try:
                cnts, _ = cv2.findContours((x > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            except ValueError:
                _tmp, cnts, _ = cv2.findContours((x > 0).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)  # type: ignore
            for c in cnts:
                rx, ry, rw, rh = cv2.boundingRect(c)
                if rw >= max(60, W // 6) and rh > 0 and (rw / max(1, rh)) >= 8.0:
                    cv2.rectangle(x, (rx, ry), (rx + rw, ry + rh), color=0, thickness=-1)

            processed.append(x)
        except Exception:
            processed.append(v)

    # Deduplicate variants by size+hash to keep list small
    seen = set()
    unique = []
    for img in processed:
        try:
            key = (img.shape[0], img.shape[1], int(img.sum()) % 1_000_000)
        except Exception:
            key = None
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(img)

    return unique


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


def _ocr_digit_boxes(bin_img) -> List[Tuple[int, float]]:
    """Return list of (value, x_center_norm) for each detected numeric token.

    x_center_norm is normalized to [0, 1] within `bin_img` width.
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
    out: List[Tuple[int, float]] = []
    try:
        width = float(getattr(bin_img, "shape", [0, 0])[1] or 1)
    except Exception:
        width = 1.0
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
            xc = (int(data.get("left", [0])[i]) + int(data.get("width", [0])[i]) // 2) / max(1.0, width)
        except Exception:
            try:
                xc = int(data.get("left", [0])[i]) / max(1.0, width)
            except Exception:
                xc = 0.0
        out.append((val, float(max(0.0, min(1.0, xc)))))
    return out


def read_lowest_price_from_roi(
    region: Tuple[int, int, int, int],
    price_min: int = 10,
    price_max: int = 10_000_000,
    debug_save: Optional[str] = None,
) -> Optional[int]:
    """Capture `region=(left, top, width, height)` and return min price.

    Tries multiple preprocessing variants to handle low-contrast text.
    """
    if cv2 is None or np is None or pytesseract is None:
        print("[OCR] 缺少依赖: 请安装 opencv-python、pytesseract，并在系统安装 Tesseract。")
        return None
    pil = pyautogui.screenshot(region=region)
    variants = _preprocess_variants_for_digits(pil)
    if not variants:
        return None
    best_img = None
    best_vals: List[int] = []
    for img in variants:
        vals = _ocr_digits(img)
        cand = [v for v in vals if price_min <= v <= price_max]
        if cand and (not best_vals or min(cand) < min(best_vals)):
            best_vals = cand
            best_img = img
    if debug_save:
        try:
            os.makedirs(os.path.dirname(debug_save), exist_ok=True)
            base, ext = os.path.splitext(debug_save)
            # Save raw ROI
            try:
                raw = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
                cv2.imwrite(base + "_raw.png", raw)
            except Exception:
                pass
            # Save a handful of variant images
            for i, v in enumerate(variants[:12]):
                try:
                    cv2.imwrite(f"{base}_v{i:02d}.png", v)
                except Exception:
                    pass
            # Save best image to the target path
            if best_img is not None:
                try:
                    cv2.imwrite(debug_save, best_img)
                except Exception:
                    pass
        except Exception:
            pass
    if not best_vals:
        return None
    return min(best_vals)


def read_price_and_stock_from_roi(
    region: Tuple[int, int, int, int],
    price_min: int = 10,
    price_max: int = 10_000_000,
    qty_min: int = 0,
    qty_max: int = 1_000_000,
    debug_save: Optional[str] = None,
) -> Tuple[int, int]:
    """Capture region and return (price, quantity). Missing values -> 0.

    Uses multiple preprocessing variants and normalized X positions to
    robustly separate price (left) vs quantity (right).
    """
    if cv2 is None or np is None or pytesseract is None:
        print("[OCR] 缺少依赖: 请安装 opencv-python、pytesseract，并在系统安装 Tesseract。")
        return 0, 0
    pil = pyautogui.screenshot(region=region)
    variants = _preprocess_variants_for_digits(pil)
    if not variants:
        return 0, 0
    best_img = None
    best_price, best_qty = 0, 0

    for img in variants:
        boxes = _ocr_digit_boxes(img)  # (val, x_norm)
        if not boxes:
            continue
        left_vals = [v for v, xn in boxes if xn <= 0.5 and price_min <= v <= price_max]
        right_vals = [v for v, xn in boxes if xn > 0.5 and qty_min <= v <= qty_max]
        cand_price = min(left_vals) if left_vals else 0
        cand_qty = max(right_vals) if right_vals else 0
        if cand_price or cand_qty:
            # Prefer image with a valid price; tie-break by larger qty
            if (cand_price and not best_price) or \
               (cand_price and best_price and cand_price <= best_price) or \
               (not cand_price and not best_price and cand_qty > best_qty):
                best_price, best_qty = int(cand_price or 0), int(cand_qty or 0)
                best_img = img

    if debug_save:
        try:
            os.makedirs(os.path.dirname(debug_save), exist_ok=True)
            base, ext = os.path.splitext(debug_save)
            # Save raw ROI
            try:
                raw = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
                cv2.imwrite(base + "_raw.png", raw)
            except Exception:
                pass
            # Save a handful of variant images
            for i, v in enumerate(variants[:12]):
                try:
                    cv2.imwrite(f"{base}_v{i:02d}.png", v)
                except Exception:
                    pass
            # Save best image to the target path
            if best_img is not None:
                try:
                    cv2.imwrite(debug_save, best_img)
                except Exception:
                    pass
        except Exception:
            pass

    return int(best_price or 0), int(best_qty or 0)


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
    save = os.path.join("images", "_debug_price_proc.png") if debug else None
    return read_lowest_price_from_roi(region, debug_save=save)


def read_price_and_stock_from_config(
    mapping_path: str = "key_mapping.json", debug: bool = False
) -> Tuple[int, int]:
    """Read ROI from mapping and return (price, quantity). 0 means not found."""
    mapping = _load_key_mapping(mapping_path)
    tl = mapping.get("价格区域左上")
    br = mapping.get("价格区域右下")
    if not (isinstance(tl, dict) and isinstance(br, dict)):
        print("[OCR] key_mapping.json 缺少 '价格区域左上/右下'，请先标定 ROI。")
        return 0, 0
    try:
        l, t = int(tl["x"]), int(tl["y"])
        r, b = int(br["x"]), int(br["y"])
        if r <= l or b <= t:
            print("[OCR] ROI 无效：右下坐标不应小于左上坐标。")
            return 0, 0
        region = (l, t, r - l, b - t)
    except Exception:
        print("[OCR] 解析 ROI 失败，请检查 key_mapping.json。")
        return 0, 0
    save = os.path.join("images", "_debug_price_proc.png") if debug else None
    return read_price_and_stock_from_roi(region, debug_save=save)
