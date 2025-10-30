import os
from typing import List, Optional, Tuple, Dict, Any

import pyautogui

try:
    import cv2  # type: ignore
    import numpy as np  # type: ignore
except Exception:
    cv2 = None  # type: ignore
    np = None  # type: ignore


"""价格与数量读取工具：统一使用 Umi-OCR（utils/ocr_utils）。"""


# OcrLite support removed


def _run_umi_ocr_on_pil(pil_img, cfg: Optional[Dict[str, Any]] = None) -> List[str]:
    """使用 utils/ocr_utils 调用 Umi-OCR 并返回文本列表。"""
    from utils.ocr_utils import recognize_text  # type: ignore
    ocfg = dict(cfg or {})
    base_url = str(ocfg.get("base_url", "http://127.0.0.1:1224"))
    timeout = float(ocfg.get("timeout_sec", 2.5) or 2.5)
    options = dict(ocfg.get("options", {}) or {})
    boxes = recognize_text(pil_img, base_url=base_url, timeout=timeout, options=options)
    return [(b.text or "").strip() for b in boxes if (b.text or "").strip()]


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
    """截取 `region=(left, top, width, height)` 并返回识别到的最低价格（Umi-OCR）。"""
    pil = pyautogui.screenshot(region=region)
    try:
        cfg = _load_app_config("config.json")
        umi_cfg = (cfg.get("umi_ocr", {}) or {})
    except Exception:
        umi_cfg = {}
    try:
        from utils.ocr_utils import recognize_numbers  # type: ignore
        boxes = recognize_numbers(
            pil,
            base_url=str(umi_cfg.get("base_url", "http://127.0.0.1:1224")),
            timeout=float(umi_cfg.get("timeout_sec", 2.5) or 2.5),
            options=dict(umi_cfg.get("options", {}) or {}),
        )
        vals = [int(b.value) for b in boxes if getattr(b, "value", None) is not None]
        cand = [v for v in vals if price_min <= v <= price_max]
        if debug_save:
            try:
                os.makedirs(os.path.dirname(debug_save), exist_ok=True)
                pil.save(debug_save)
            except Exception:
                pass
        return min(cand) if cand else None
    except Exception:
        return None


def read_price_and_stock_from_roi(
    region: Tuple[int, int, int, int],
    price_min: int = 10,
    price_max: int = 10_000_000,
    qty_min: int = 0,
    qty_max: int = 1_000_000,
    debug_save: Optional[str] = None,
) -> Tuple[int, int]:
    """截取区域并返回 (价格, 数量)。缺失为 0。统一使用 Umi-OCR。"""
    pil = pyautogui.screenshot(region=region)
    try:
        cfg = _load_app_config("config.json")
        umi_cfg = (cfg.get("umi_ocr", {}) or {})
    except Exception:
        umi_cfg = {}
    try:
        from utils.ocr_utils import recognize_numbers  # type: ignore
        boxes = recognize_numbers(
            pil,
            base_url=str(umi_cfg.get("base_url", "http://127.0.0.1:1224")),
            timeout=float(umi_cfg.get("timeout_sec", 2.5) or 2.5),
            options=dict(umi_cfg.get("options", {}) or {}),
        )
        w = max(1, int(region[2]))
        # 根据 bbox 中心 X 将候选分入左右两侧
        left_vals: List[int] = []
        right_vals: List[int] = []
        for b in boxes:
            v = getattr(b, "value", None)
            if v is None:
                continue
            bx, by, bw, bh = b.bbox
            cx = int(bx + bw / 2)
            if cx <= w // 2:
                if price_min <= int(v) <= price_max:
                    left_vals.append(int(v))
            else:
                if qty_min <= int(v) <= qty_max:
                    right_vals.append(int(v))
        price = min(left_vals) if left_vals else 0
        qty = max(right_vals) if right_vals else 0
        if debug_save:
            try:
                os.makedirs(os.path.dirname(debug_save), exist_ok=True)
                pil.save(debug_save)
            except Exception:
                pass
        return int(price or 0), int(qty or 0)
    except Exception:
        return 0, 0


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
    return read_lowest_price_from_roi(region, debug_save=save)


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
    - OCR 统一使用 Umi-OCR。

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
    # 统一使用 Umi-OCR
    try:
        sc = float(cur.get("scale", 1.0))
    except Exception:
        sc = 1.0
    try:
        print(f"[CurrencyOCR] scale={sc}")
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
