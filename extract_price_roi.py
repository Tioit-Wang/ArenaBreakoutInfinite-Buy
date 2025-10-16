"""
Standalone script to extract the middle price distribution area from a panel screenshot.

Usage:
  py -3.13 extract_price_roi.py images/proc_20251016_093453/step_01_raw.png

Outputs (saved next to the input image):
  - price_area_roi.png    Cropped ROI
  - price_area_debug.png  Debug visualization with detected lines/box

This script is self-contained and does not depend on project modules.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import cv2
import numpy as np


@dataclass
class HLine:
    y: int
    x: int
    w: int
    h: int


def _debug_draw_line(img: np.ndarray, y: int, color: Tuple[int, int, int], label: str) -> None:
    h, w = img.shape[:2]
    cv2.line(img, (0, int(y)), (w - 1, int(y)), color, 2, cv2.LINE_AA)
    cv2.putText(
        img,
        label,
        (10, int(y) - 6),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        color,
        1,
        cv2.LINE_AA,
    )


def _threshold_for_lines(gray: np.ndarray) -> np.ndarray:
    # Otsu on bright-on-dark UI makes lines/text white.
    _, bin_img = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return bin_img


def detect_horizontal_lines(bin_img: np.ndarray, min_rel_len: float = 0.5) -> List[HLine]:
    h, w = bin_img.shape[:2]
    # Suppress small text with a long horizontal kernel.
    k = max(15, int(w * 0.35))
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (k, 3))
    opened = cv2.morphologyEx(bin_img, cv2.MORPH_OPEN, kernel, iterations=1)

    contours, _ = cv2.findContours(opened, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    lines: List[HLine] = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if cw >= int(w * min_rel_len) and ch <= 10:
            lines.append(HLine(y=y + ch // 2, x=x, w=cw, h=ch))

    # Deduplicate lines close in Y.
    lines.sort(key=lambda l: l.y)
    dedup: List[HLine] = []
    for ln in lines:
        if not dedup or abs(ln.y - dedup[-1].y) > 6:
            dedup.append(ln)
        else:
            # Keep the wider one when nearly overlapping.
            if ln.w > dedup[-1].w:
                dedup[-1] = ln
    return dedup


def find_buy_button_top(hsv: np.ndarray) -> Optional[int]:
    h, w = hsv.shape[:2]
    # Orange button (购买) – allow some hue tolerance.
    lower1 = np.array([5, 80, 120], dtype=np.uint8)
    upper1 = np.array([25, 255, 255], dtype=np.uint8)
    mask = cv2.inRange(hsv, lower1, upper1)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 15), np.uint8), iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cand = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        area = cw * ch
        ar = cw / max(1.0, float(ch))
        if y > int(h * 0.6) and area > (w * h) * 0.002 and ar > 2.0:
            cand.append((area, y))
    if not cand:
        return None
    cand.sort(reverse=True)
    return int(cand[0][1])


def _match_template(
    gray: np.ndarray,
    tmpl_gray: np.ndarray,
    search_roi: Optional[Tuple[int, int, int, int]] = None,
    method: int = cv2.TM_CCOEFF_NORMED,
) -> Tuple[Tuple[int, int], float]:
    """Return best top-left and score in the full image or a sub ROI.

    search_roi: (x, y, w, h) restricting where to search.
    """
    if search_roi is not None:
        x, y, w, h = search_roi
        region = gray[y : y + h, x : x + w]
    else:
        x = y = 0
        region = gray
    res = cv2.matchTemplate(region, tmpl_gray, method)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    top_left = (max_loc[0] + x, max_loc[1] + y)
    return top_left, float(max_val)


def locate_top_by_template(gray: np.ndarray, bin_img: np.ndarray, tmpl: np.ndarray) -> Optional[int]:
    h, w = gray.shape[:2]
    tmpl_gray = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY) if tmpl.ndim == 3 else tmpl
    # Search near the top third
    roi = (0, 0, w, int(h * 0.35))
    (tx, ty), score = _match_template(gray, tmpl_gray, roi)
    if score < 0.55:
        return None
    y_after_template = ty + tmpl_gray.shape[0]
    band_top = min(h - 1, y_after_template + 1)
    band_bot = min(h, y_after_template + max(12, int(h * 0.03)))
    if band_bot <= band_top:
        return None
    sub = bin_img[band_top:band_bot, :]
    lines = detect_horizontal_lines(sub, min_rel_len=0.5)
    if not lines:
        # fallback: pick the row in the band with max horizontal density
        proj = sub.sum(axis=1)
        y_local = int(np.argmax(proj))
        return band_top + y_local
    # choose the first long line in this band
    y_local = min(l.y for l in lines)
    return band_top + y_local


def locate_bottom_by_template(gray: np.ndarray, tmpl: np.ndarray) -> Optional[int]:
    h, w = gray.shape[:2]
    tmpl_gray = cv2.cvtColor(tmpl, cv2.COLOR_BGR2GRAY) if tmpl.ndim == 3 else tmpl
    # Search in the middle-to-lower half
    roi = (0, int(h * 0.35), w, int(h * 0.55))
    (bx, by), score = _match_template(gray, tmpl_gray, roi)
    if score < 0.55:
        return None
    return int(by + tmpl_gray.shape[0] // 2 - 20)


def choose_roi_y(lines: List[HLine], y_btn_top: Optional[int], h: int) -> Optional[Tuple[int, int]]:
    if not lines:
        return None
    ys = [l.y for l in lines]
    # Restrict to lines above the buy button if available, otherwise up to 90% height.
    limit = int(y_btn_top - 8) if y_btn_top is not None else int(h * 0.9)
    ys = [y for y in ys if y < limit]
    if len(ys) < 2:
        return None

    # Heuristic: choose the pair with a plausible height range.
    best_pair = None
    best_score = -1.0
    for i in range(1, len(ys)):
        top, bot = ys[i - 1], ys[i]
        height = bot - top
        if height < int(h * 0.18) or height > int(h * 0.7):
            continue
        # Prefer larger content area but not too close to borders.
        score = height - abs(0.5 * h - (top + bot) / 2) * 0.1
        if score > best_score:
            best_score = score
            best_pair = (top, bot)
    if best_pair:
        return best_pair

    # Fallback: take last two before limit
    return (ys[-2], ys[-1])


def refine_x_bounds(bin_img: np.ndarray, y_top: int, y_bot: int) -> Tuple[int, int]:
    h, w = bin_img.shape[:2]
    roi = bin_img[y_top:y_bot, :]

    # Strong vertical lines first
    vk = max(15, int((y_bot - y_top) * 0.3))
    vkernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, vk))
    vopen = cv2.morphologyEx(roi, cv2.MORPH_OPEN, vkernel, iterations=1)
    contours, _ = cv2.findContours(vopen, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    tall: List[Tuple[int, int, int, int]] = []
    for c in contours:
        x, y, cw, ch = cv2.boundingRect(c)
        if ch > int((y_bot - y_top) * 0.55):
            tall.append((x, y, cw, ch))

    x_left: Optional[int] = None
    x_right: Optional[int] = None
    if tall:
        xs = sorted([x for x, _, _, _ in tall])
        # Prefer a left axis around 5-50% width
        xs_left = [x for x in xs if 0.05 * w <= x <= 0.5 * w]
        x_left = int(xs_left[0]) if xs_left else int(xs[0])

        xs_right = [x for x in xs if x > (x_left or 0) + int(0.2 * w)]
        if xs_right:
            x_right = int(xs_right[-1])

    # Projection-based refinement / fallback
    col_sum = roi.sum(axis=0).astype(np.float64)
    # Normalize to 0..1 for thresholding
    if col_sum.max() > 0:
        col_sum /= col_sum.max()
    thr = 0.12
    # Find leftmost significant column after some margin
    margin = int(w * 0.03)
    for i in range(margin, w - margin):
        if col_sum[i] > thr:
            if x_left is None or i < x_left:
                x_left = i
            break
    # Find rightmost significant column
    for j in range(w - margin - 1, margin, -1):
        if col_sum[j] > thr:
            if x_right is None or j > x_right:
                x_right = j
            break

    if x_left is None:
        x_left = int(w * 0.12)
    if x_right is None or x_right - x_left < int(w * 0.3):
        x_right = max(x_right or int(w * 0.92), int(w * 0.92))

    # Clamp and pad a little
    pad = 2
    x_left = max(0, x_left - pad)
    x_right = min(w - 1, x_right + pad)
    return x_left, x_right


def extract_price_roi(image_path: Path, out_dir: Optional[Path] = None) -> Tuple[Path, Path]:
    img = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Cannot read image: {image_path}")
    h, w = img.shape[:2]

    out_dir = out_dir or image_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    bin_img = _threshold_for_lines(gray)

    y_btn_top = find_buy_button_top(hsv)
    hlines = detect_horizontal_lines(bin_img)

    # First try template-based boundaries if templates exist
    y_top_tmpl: Optional[int] = None
    y_bot_tmpl: Optional[int] = None
    top_tpl_path = Path("buy_data_top.png")
    btm_tpl_path = Path("buy_data_btm.png")
    if top_tpl_path.exists():
        top_tpl = cv2.imread(str(top_tpl_path), cv2.IMREAD_COLOR)
        if top_tpl is not None:
            y_top_tmpl = locate_top_by_template(gray, bin_img, top_tpl)
    if btm_tpl_path.exists():
        btm_tpl = cv2.imread(str(btm_tpl_path), cv2.IMREAD_COLOR)
        if btm_tpl is not None:
            y_bot_tmpl = locate_bottom_by_template(gray, btm_tpl)

    if y_top_tmpl is not None and y_bot_tmpl is not None and y_bot_tmpl - y_top_tmpl > 10:
        y_top, y_bot = y_top_tmpl, y_bot_tmpl
    else:
        y_pair = choose_roi_y(hlines, y_btn_top, h)
        if y_pair is None:
            # Ratio fallback
            y_top = int(h * 0.18)
            y_bot = int(h * 0.66)
        else:
            y_top, y_bot = y_pair

    x_left, x_right = refine_x_bounds(bin_img, y_top, y_bot)

    # Final crop (safe bounds)
    y_top = max(0, min(h - 2, y_top))
    y_bot = max(y_top + 2, min(h - 1, y_bot))
    x_left = max(0, min(w - 2, x_left))
    x_right = max(x_left + 2, min(w - 1, x_right))
    crop = img[y_top:y_bot, x_left:x_right]

    # Save outputs
    crop_path = out_dir / "price_area_roi.png"
    dbg_path = out_dir / "price_area_debug.png"
    cv2.imwrite(str(crop_path), crop)

    dbg = img.copy()
    _debug_draw_line(dbg, y_top, (0, 255, 0), "top")
    _debug_draw_line(dbg, y_bot, (0, 255, 0), "bottom")
    if y_top_tmpl is not None:
        _debug_draw_line(dbg, y_top_tmpl, (0, 200, 255), "top(tmpl)")
    if y_bot_tmpl is not None:
        _debug_draw_line(dbg, y_bot_tmpl, (0, 200, 255), "bottom(tmpl)")
    cv2.rectangle(dbg, (x_left, y_top), (x_right, y_bot), (0, 165, 255), 2)
    if y_btn_top is not None:
        _debug_draw_line(dbg, y_btn_top, (0, 140, 255), "btn-top")
    cv2.imwrite(str(dbg_path), dbg)

    return crop_path, dbg_path


def main() -> None:
    ap = argparse.ArgumentParser(description="Extract middle price area from screenshot")
    ap.add_argument("image", type=Path, help="Input image path")
    ap.add_argument("--out", type=Path, default=None, help="Output directory (default: same as input)")
    args = ap.parse_args()

    crop_path, dbg_path = extract_price_roi(args.image, args.out)
    print(f"Saved ROI to: {crop_path}")
    print(f"Saved debug to: {dbg_path}")


if __name__ == "__main__":
    main()
