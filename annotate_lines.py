"""Detect and box long straight lines in an image.

Examples
--------
- Basic:
    uv run python annotate_lines.py images/proc_20251016_093453/step_02_projection.png
- Tune length and merging:
    uv run python annotate_lines.py INPUT.png --min-length 140 --max-gap 8 --hough-threshold 80
- Only horizontal/vertical lines:
    uv run python annotate_lines.py INPUT.png --orient hv

Output is saved next to the input as `<name>_lines_annotated.png` by default.
"""

from __future__ import annotations

import argparse
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import cv2 as cv
import numpy as np


@dataclass
class LineSeg:
    x1: int
    y1: int
    x2: int
    y2: int
    length: float
    angle_deg: float  # [0, 180)


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    angle_deg: float


def _auto_canny(gray: np.ndarray) -> Tuple[int, int]:
    v = float(np.median(gray))
    # heuristics: lower ~0.66*v, upper ~1.33*v, clamped to [0,255]
    lower = max(0, min(255, int(0.66 * v)))
    upper = max(0, min(255, int(1.33 * v)))
    if lower >= upper:
        lower = max(0, upper - 1)
    return lower, upper


def _angle_deg(x1: int, y1: int, x2: int, y2: int) -> float:
    ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
    ang = ang % 180.0
    if ang < 0:
        ang += 180.0
    return ang


def _angle_diff(a: float, b: float) -> float:
    # minimal difference on circular domain [0,180)
    d = abs((a - b) % 180.0)
    return min(d, 180.0 - d)


def _filter_orient(seg: LineSeg, orient: str) -> bool:
    # Normalize to nearest canonical orientation
    a = seg.angle_deg
    if orient == "all":
        return True
    if orient == "h":  # near 0 or 180
        return min(abs(a - 0), abs(a - 180)) <= 15 or abs(a - 180) <= 15 or abs(a - 0) <= 15
    if orient == "v":  # near 90
        return abs(a - 90) <= 15
    if orient == "hv":
        return abs(a - 90) <= 15 or min(abs(a - 0), abs(a - 180)) <= 15
    if orient == "diag":  # near 45 or 135
        return min(abs(a - 45), abs(a - 135)) <= 15
    return True


def _merge_boxes(boxes: List[Box], iou_thresh: float, angle_tol: float) -> List[Box]:
    # Greedy union merge for overlapping boxes with similar orientation
    changed = True
    bxs = boxes[:]
    while changed and len(bxs) > 1:
        changed = False
        out: List[Box] = []
        used = [False] * len(bxs)
        for i in range(len(bxs)):
            if used[i]:
                continue
            a = bxs[i]
            ax2, ay2 = a.x + a.w, a.y + a.h
            merged = a
            for j in range(i + 1, len(bxs)):
                if used[j]:
                    continue
                b = bxs[j]
                if _angle_diff(a.angle_deg, b.angle_deg) > angle_tol:
                    continue
                bx2, by2 = b.x + b.w, b.y + b.h
                inter_x1 = max(a.x, b.x)
                inter_y1 = max(a.y, b.y)
                inter_x2 = min(ax2, bx2)
                inter_y2 = min(ay2, by2)
                inter_w = max(0, inter_x2 - inter_x1)
                inter_h = max(0, inter_y2 - inter_y1)
                inter_area = inter_w * inter_h
                union_area = a.w * a.h + b.w * b.h - inter_area
                iou = inter_area / union_area if union_area > 0 else 0.0
                if iou >= iou_thresh:
                    # merge via union rect and averaged angle
                    nx1 = min(a.x, b.x)
                    ny1 = min(a.y, b.y)
                    nx2 = max(ax2, bx2)
                    ny2 = max(ay2, by2)
                    merged = Box(nx1, ny1, nx2 - nx1, ny2 - ny1, (a.angle_deg + b.angle_deg) / 2.0)
                    a = merged
                    ax2, ay2 = nx2, ny2
                    used[j] = True
                    changed = True
            used[i] = True
            out.append(merged)
        bxs = out
    return bxs


def detect_lines(
    img: np.ndarray,
    min_length: int = 120,
    max_gap: int = 10,
    hough_thresh: int = 80,
    canny_low: int | None = None,
    canny_high: int | None = None,
    kernel: int = 1,
    orient: str = "all",
    pad: int = 3,
    merge_iou: float = 0.2,
    merge_angle_tol: float = 8.0,
) -> Tuple[np.ndarray, List[LineSeg], List[Box]]:
    """Detect long straight lines and return annotated image, segments, and boxes."""
    gray = cv.cvtColor(img, cv.COLOR_BGR2GRAY)
    blur = cv.GaussianBlur(gray, (3, 3), 0)
    if canny_low is None or canny_high is None:
        canny_low, canny_high = _auto_canny(blur)
    edges = cv.Canny(blur, canny_low, canny_high)
    if kernel and kernel > 1:
        k = cv.getStructuringElement(cv.MORPH_RECT, (kernel, kernel))
        edges = cv.morphologyEx(edges, cv.MORPH_CLOSE, k, iterations=1)

    lines_p = cv.HoughLinesP(
        edges,
        rho=1,
        theta=np.pi / 180.0,
        threshold=hough_thresh,
        minLineLength=min_length,
        maxLineGap=max_gap,
    )
    segs: List[LineSeg] = []
    if lines_p is not None:
        for l in lines_p.reshape(-1, 4):
            x1, y1, x2, y2 = map(int, l.tolist())
            length = float(math.hypot(x2 - x1, y2 - y1))
            angle = _angle_deg(x1, y1, x2, y2)
            seg = LineSeg(x1, y1, x2, y2, length, angle)
            if _filter_orient(seg, orient):
                segs.append(seg)

    boxes: List[Box] = []
    for s in segs:
        x1, y1, x2, y2 = s.x1, s.y1, s.x2, s.y2
        x = min(x1, x2)
        y = min(y1, y2)
        w = abs(x2 - x1)
        h = abs(y2 - y1)
        if w == 0:
            # vertical line: make a thin box with min height
            bx = x - pad
            by = y
            bw = 2 * pad
            bh = h if h > 0 else 1
        elif h == 0:
            # horizontal line
            bx = x
            by = y - pad
            bw = w if w > 0 else 1
            bh = 2 * pad
        else:
            # diagonal: create oriented-agnostic bounding rectangle with padding
            bx = x - pad
            by = y - pad
            bw = w + 2 * pad
            bh = h + 2 * pad
        boxes.append(Box(int(bx), int(by), int(bw), int(bh), s.angle_deg))

    if boxes:
        boxes = _merge_boxes(boxes, iou_thresh=float(merge_iou), angle_tol=float(merge_angle_tol))

    # Annotate visualization
    out = img.copy()
    # draw lines
    for i, s in enumerate(segs, start=1):
        cv.line(out, (s.x1, s.y1), (s.x2, s.y2), (0, 255, 0), 2)
    # draw boxes
    for i, b in enumerate(boxes, start=1):
        cv.rectangle(out, (b.x, b.y), (b.x + b.w, b.y + b.h), (0, 128, 255), 2)
        cv.putText(out, f"L{i}", (b.x + 2, max(0, b.y - 6)), cv.FONT_HERSHEY_SIMPLEX, 0.5, (0, 128, 255), 1, cv.LINE_AA)

    return out, segs, boxes


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Detect and box long straight lines in a UI/screenshot image.")
    ap.add_argument("input", help="Path to input image")
    ap.add_argument("--output", default=None, help="Output path (default: <input>_lines_annotated.png)")
    ap.add_argument("--min-length", type=int, default=120, help="Minimum line length in pixels")
    ap.add_argument("--max-gap", type=int, default=10, help="Max gap to link line segments (HoughLinesP)")
    ap.add_argument("--hough-threshold", type=int, default=80, help="Accumulator threshold for HoughLinesP")
    ap.add_argument("--canny-low", type=int, default=None, help="Canny lower threshold (auto if omitted)")
    ap.add_argument("--canny-high", type=int, default=None, help="Canny upper threshold (auto if omitted)")
    ap.add_argument("--kernel", type=int, default=1, help="Morphological close kernel size (1 disables)")
    ap.add_argument("--orient", choices=["all", "h", "v", "hv", "diag"], default="all", help="Orientation filter")
    ap.add_argument("--pad", type=int, default=3, help="Padding (px) for each line's bounding box")
    ap.add_argument("--merge-iou", type=float, default=0.2, help="IoU threshold to merge overlapping boxes")
    ap.add_argument("--merge-angle-tol", type=float, default=8.0, help="Angle tolerance (deg) for merging boxes")
    return ap.parse_args()


def main() -> None:
    args = _parse_args()
    in_path = Path(os.path.normpath(args.input))
    img = cv.imread(str(in_path), cv.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Failed to read image: {in_path}")
    out_img, segs, boxes = detect_lines(
        img,
        min_length=int(args.min_length),
        max_gap=int(args.max_gap),
        hough_thresh=int(args.hough_threshold),
        canny_low=args.canny_low,
        canny_high=args.canny_high,
        kernel=max(1, int(args.kernel)),
        orient=args.orient,
        pad=max(0, int(args.pad)),
        merge_iou=float(args.merge_iou),
        merge_angle_tol=float(args.merge_angle_tol),
    )
    if args.output:
        out_path = Path(os.path.normpath(args.output))
    else:
        out_path = in_path.with_name(f"{in_path.stem}_lines_annotated{in_path.suffix}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv.imwrite(str(out_path), out_img)
    # Simple summary
    print(f"Found {len(segs)} line segments -> {len(boxes)} boxes.")
    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()

