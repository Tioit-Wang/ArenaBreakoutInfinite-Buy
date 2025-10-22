import os
import shutil
from typing import List, Optional, Tuple, Dict, Any

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


# OcrLite support removed


def _run_umi_ocr_on_pil(pil_img, cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """Call Umi-OCR (/api/ocr) with a PIL image and return list of texts.

    cfg: expects keys similar to DEFAULT_CONFIG['umi_ocr'].
    """
    import base64
    import io
    try:
        import requests  # type: ignore
    except Exception as e:
        raise RuntimeError(f"缺少 requests 依赖: {e}")
    ocfg = dict(cfg or {})
    base_url = str(ocfg.get("base_url", "http://127.0.0.1:1224")).rstrip("/")
    timeout = float(ocfg.get("timeout_sec", 2.5) or 2.5)
    options = dict(ocfg.get("options", {}) or {})
    buf = io.BytesIO()
    try:
        pil_img.save(buf, format="PNG")
    except Exception:
        pil_img.convert("RGB").save(buf, format="PNG")
    data_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    url = base_url + "/api/ocr"
    payload = {"base64": data_b64}
    if options:
        payload["options"] = options
    # Log basic request info for troubleshooting (stdout)
    try:
        import time as _time  # type: ignore
        _t0 = _time.perf_counter()
        _sz = len(payload.get("base64", ""))
        print(f"[UmiOCR] POST {url} timeout={timeout}s payload_b64_len={_sz} opts={list(options.keys())}")
    except Exception:
        _t0 = None
    resp = requests.post(url, json=payload, timeout=timeout)
    try:
        resp.raise_for_status()
    except Exception as e:
        print(f"[UmiOCR] HTTP error: {e}")
        raise
    j = resp.json()
    try:
        _elapsed = ((_time.perf_counter() - _t0) * 1000.0) if _t0 is not None else -1.0
        _n = 0
        if isinstance(j.get("data"), list):
            _n = len(j.get("data"))
        print(f"[UmiOCR] code={j.get('code')} elapsed_ms={int(_elapsed)} items={_n}")
    except Exception:
        pass
    code = int(j.get("code", 0) or 0)
    data = j.get("data")
    texts: List[str] = []
    if code == 100:
        if isinstance(data, list):
            for e in data:
                if isinstance(e, dict) and e.get("text"):
                    texts.append(str(e["text"]))
        elif isinstance(data, str):
            texts = [data]
    elif isinstance(data, str):
        texts = [data]
    return texts


def _parse_price_from_texts(texts: List[str]) -> Optional[int]:
    """Parse integer price supporting K/M suffix from a list of strings.

    Safety: Ignore known error/diagnostic strings (e.g. exceptions) to avoid
    mistakenly parsing numbers from messages like "line 358".
    """
    best: Optional[int] = None
    for raw in texts or []:
        try:
            t_raw = (raw or "").strip()
            # Skip obvious error or diagnostic messages
            tl = t_raw.lower()
            if any(s in tl for s in (
                "ocr失败",  # our own failure tag
                "invalid syntax",
                "traceback",
                "exception",
                "error:",
                " file ",
                ".py",
                " line ",
            )):
                continue

            t = t_raw.upper()
            # tokenize by spaces/commas
            for token in t.replace(",", " ").split():
                mult = 1
                if token.endswith("M"):
                    mult = 1_000_000
                    token = token[:-1]
                elif token.endswith("K"):
                    mult = 1_000
                    token = token[:-1]
                # Only accept tokens that are purely numeric after stripping
                # common punctuation; reject if they still contain letters
                token_stripped = token.strip("()[]{}<>:;.")
                if not token_stripped or any(ch.isalpha() for ch in token_stripped):
                    continue
                digits = "".join(ch for ch in token_stripped if ch.isdigit())
                if digits:
                    v = int(digits) * mult
                    best = v if best is None or v < best else best
        except Exception:
            continue
    return best


def read_lowest_price_from_roi(
    region: Tuple[int, int, int, int],
    price_min: int = 10,
    price_max: int = 10_000_000,
    debug_save: Optional[str] = None,
    *,
    engine: Optional[str] = None,
) -> Optional[int]:
    """Capture `region=(left, top, width, height)` and return min price.

    Tries multiple preprocessing variants to handle low-contrast text.
    """
    pil = pyautogui.screenshot(region=region)
    eng = (engine or "").lower().strip() if engine else ""
    # Prefer Umi-OCR when requested
    if eng in ("umi", "umi-ocr", "umiocr"):
        try:
            umi_cfg = (_load_app_config("config.json").get("umi_ocr", {}) or {})
        except Exception:
            umi_cfg = {}
        try:
            texts = _run_umi_ocr_on_pil(pil, umi_cfg)
            val = _parse_price_from_texts(texts)
            if debug_save:
                try:
                    os.makedirs(os.path.dirname(debug_save), exist_ok=True)
                    pil.save(debug_save)
                except Exception:
                    pass
            return int(val) if val is not None and price_min <= val <= price_max else None
        except Exception:
            pass
    if cv2 is None or np is None or pytesseract is None:
        print("[OCR] 缺少依赖: 请安装 opencv-python、pytesseract，并在系统安装 Tesseract。")
        return None
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


def _load_app_config(path: str = "config.json") -> Dict[str, Any]:
    try:
        from app_config import load_config  # type: ignore
        return load_config(path)
    except Exception:
        try:
            import json as _json  # type: ignore
            with open(path, "r", encoding="utf-8") as f:
                return _json.load(f)  # type: ignore
        except Exception:
            return {}


def read_lowest_price_from_config(*, config_path: str = "config.json", debug: bool = False) -> Optional[int]:
    """Read ROI from config.json (rects.price_region) and return min price.

    Backward compatible: also accepts legacy key '价格区域'.
    """
    cfg = _load_app_config(config_path)
    rects = (cfg.get("rects", {}) or {})
    rect = rects.get("price_region") or rects.get("价格区域")
    if not isinstance(rect, dict):
        print("[OCR] 配置缺少 'rects.price_region'，请先标定 ROI。")
        return None
    try:
        l, t = int(rect.get("x1", 0)), int(rect.get("y1", 0))
        r, b = int(rect.get("x2", 0)), int(rect.get("y2", 0))
        if r <= l or b <= t:
            print("[OCR] ROI 无效：右下坐标不应小于左上坐标。")
            return None
        region = (l, t, r - l, b - t)
    except Exception:
        print("[OCR] 解析 ROI 失败，请检查 config.json。")
        return None
    save = os.path.join("images", "_debug_price_proc.png") if debug else None
    avg = (cfg.get("avg_price_area", {}) or {})
    eng = str(avg.get("ocr_engine", "umi") or "umi").lower()
    return read_lowest_price_from_roi(region, debug_save=save, engine=eng)


def read_price_and_stock_from_config(*, config_path: str = "config.json", debug: bool = False) -> Tuple[int, int]:
    """Read ROI from config.json and return (price, quantity). 0 means not found.

    Backward compatible: 'rects.price_region' preferred; '价格区域' accepted.
    """
    cfg = _load_app_config(config_path)
    rects = (cfg.get("rects", {}) or {})
    rect = rects.get("price_region") or rects.get("价格区域")
    if not isinstance(rect, dict):
        print("[OCR] 配置缺少 'rects.price_region'，请先标定 ROI。")
        return 0, 0
    try:
        l, t = int(rect.get("x1", 0)), int(rect.get("y1", 0))
        r, b = int(rect.get("x2", 0)), int(rect.get("y2", 0))
        if r <= l or b <= t:
            print("[OCR] ROI 无效：右下坐标不应小于左上坐标。")
            return 0, 0
        region = (l, t, r - l, b - t)
    except Exception:
        print("[OCR] 解析 ROI 失败，请检查 config.json。")
        return 0, 0
    save = os.path.join("images", "_debug_price_proc.png") if debug else None
    return read_price_and_stock_from_roi(region, debug_save=save)


def read_currency_prices_from_config(
    *,
    config_path: str = "config.json",
    debug: bool = False,
) -> Tuple[Optional[int], Optional[int]]:
    """Locate a single currency icon template and read two prices to its right.

    - Matches the template across the screen and picks up to two matches.
    - Orders matches by Y (top=平均单价, bottom=合计价格).
    - Crops ROIs immediately to the right of each match, with width from config
      and height equal to the template height.
    - OCR engine follows avg_price_area.ocr_engine.

    Returns (avg_price, total_price) where each is Optional[int].
    """
    cfg = _load_app_config(config_path)
    cur = (cfg.get("currency_area", {}) or {})
    tmpl_path = str(cur.get("template", "") or "").strip()
    try:
        thr = float(cur.get("threshold", 0.8))
    except Exception:
        thr = 0.8
    try:
        pw = int(cur.get("price_width", 220))
    except Exception:
        pw = 220
    if not tmpl_path or not os.path.exists(tmpl_path):
        print("[OCR] 配置缺少 'currency_area.template' 或文件不存在。")
        return None, None
    # Engine: currency_area.ocr_engine overrides; else fall back to avg_price_area.ocr_engine
    cur_eng = str((cur.get("ocr_engine", "") or "")).strip().lower()
    if cur_eng:
        eng = cur_eng
    else:
        eng = str(((cfg.get("avg_price_area", {}) or {}).get("ocr_engine", "umi") or "umi")).lower()
    try:
        sc = float(cur.get("scale", 1.0))
    except Exception:
        sc = 1.0
    try:
        print(f"[CurrencyOCR] engine={eng} scale={sc}")
    except Exception:
        pass
    # OcrLite config removed
    try:
        import pyautogui  # type: ignore
    except Exception:
        print("[OCR] 缺少 pyautogui 依赖。")
        return None, None
    if cv2 is None or np is None:
        print("[OCR] 缺少 opencv-python/numpy 依赖。")
        return None, None
    try:
        pil = pyautogui.screenshot()
    except Exception as e:
        print(f"[OCR] 截图失败: {e}")
        return None, None
    bgr = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
    tmpl = cv2.imread(tmpl_path, cv2.IMREAD_COLOR)
    if tmpl is None:
        print("[OCR] 读取货币模板失败。")
        return None, None
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    tgray = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY) if tmpl.ndim == 3 else tmpl
    res = cv2.matchTemplate(gray, tgray, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(res >= thr)
    th, tw = int(tgray.shape[0]), int(tgray.shape[1])
    cand = [(int(y), int(x), float(res[y, x])) for y, x in zip(ys, xs)]
    cand.sort(key=lambda a: a[2], reverse=True)
    picks: list[tuple[int, int, float]] = []
    for y, x, s in cand:
        ok = True
        for py, px, _ in picks:
            if abs(py - y) < th // 2 and abs(px - x) < tw // 2:
                ok = False
                break
        if ok:
            picks.append((y, x, s))
        if len(picks) >= 2:
            break
    if not picks:
        return None, None
    picks.sort(key=lambda a: a[0])
    H, W = gray.shape[:2]
    rois: list[Tuple[str, Tuple[int, int, int, int]]] = []
    for idx, (y, x, s) in enumerate(picks[:2]):
        x1 = max(0, x + tw)
        y1 = max(0, y)
        x2 = min(W, x1 + max(1, pw))
        y2 = min(H, y1 + th)
        rois.append(("avg" if idx == 0 else "total", (x1, y1, max(1, x2 - x1), max(1, y2 - y1))))

    def _ocr_one(region: Tuple[int, int, int, int]) -> Optional[int]:
        # Prefer Umi-OCR if selected; otherwise fall back to Tesseract
        if eng in ("umi", "umi-ocr", "umiocr"):
            try:
                img = pyautogui.screenshot(region=region)
            except Exception:
                return None
            try:
                texts = _run_umi_ocr_on_pil(img, (cfg.get("umi_ocr", {}) or {}))
            except Exception:
                return None
            val = _parse_price_from_texts(texts)
            return int(val) if val is not None else None
        # Tesseract path
        if pytesseract is None:
            return None
        _maybe_init_tesseract()
        try:
            img = pyautogui.screenshot(region=region)
            if abs(sc - 1.0) > 1e-3:
                try:
                    w, h = img.size
                    from PIL import Image as _Image  # type: ignore
                    img = img.resize((max(1, int(w * sc)), max(1, int(h * sc))), resample=getattr(_Image, 'LANCZOS', 1))
                except Exception:
                    pass
        except Exception:
            return None
        try:
            txt = pytesseract.image_to_string(
                img, config="--oem 3 --psm 6 -c tessedit_char_whitelist=0123456789KM"
            )
        except Exception:
            return None
        return _parse_price_from_texts([txt or ""]) or None

    avg_val: Optional[int] = None
    tot_val: Optional[int] = None
    for tag, reg in rois:
        v = _ocr_one(reg)
        if tag == "avg":
            avg_val = v
        else:
            tot_val = v
        if debug:
            try:
                os.makedirs("images", exist_ok=True)
                dbg = os.path.join("images", f"_cur_{tag}_dbg.png")
                pil2 = pyautogui.screenshot(region=reg)
                pil2.save(dbg)
            except Exception:
                pass
    return avg_val, tot_val
