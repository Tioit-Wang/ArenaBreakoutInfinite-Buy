"""Annotate all connected blocks in an image by drawing bounding boxes.

Usage examples:
    uv run python annotate_blocks.py images/proc_20251016_093453/step_02_projection.png
    uv run python annotate_blocks.py INPUT.png --min-area 120 --kernel 3 --export-roi

The script automatically chooses between binary and inverted-binary Otsu thresholding
to maximize the number of detected components. It optionally applies a small
morphological close to connect nearby pixels and filters out tiny noise by area.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import cv2 as cv
import numpy as np


@dataclass
class Component:
    label: int
    x: int
    y: int
    w: int
    h: int
    area: int


def _connected_components(mask: np.ndarray, min_area: int) -> List[Component]:
    num_labels, labels, stats, _centroids = cv.connectedComponentsWithStats(mask, connectivity=8)
    comps: List[Component] = []
    for label in range(1, num_labels):  # skip background
        x, y, w, h, area = stats[label]
        if area >= min_area and w > 0 and h > 0:
            comps.append(Component(label=label, x=int(x), y=int(y), w=int(w), h=int(h), area=int(area)))
    return comps


def _prepare_binary(gray: np.ndarray, kernel: int) -> Tuple[np.ndarray, np.ndarray]:
    # Otsu threshold (both direct and inverted). Blur slightly to reduce noise.
    blur = cv.GaussianBlur(gray, (3, 3), 0)
    _, thr = cv.threshold(blur, 0, 255, cv.THRESH_BINARY + cv.THRESH_OTSU)
    _, thri = cv.threshold(blur, 0, 255, cv.THRESH_BINARY_INV + cv.THRESH_OTSU)
    if kernel > 1:
        k = cv.getStructuringElement(cv.MORPH_RECT, (kernel, kernel))
        thr = cv.morphologyEx(thr, cv.MORPH_CLOSE, k, iterations=1)
        thri = cv.morphologyEx(thri, cv.MORPH_CLOSE, k, iterations=1)
    return thr, thri


def annotate_blocks(
    input_path: Path,
    output_path: Path | None = None,
    min_area: int = 100,
    kernel: int = 3,
    export_roi: bool = False,
) -> Path:
    # Read image
    img = cv.imread(str(input_path), cv.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Failed to read image: {input_path}")
    gray = cv.cvtColor(img, cv.COLOR_BGR2GRAY)

    # Prepare binary masks and choose the one that gives more components
    thr, thri = _prepare_binary(gray, kernel=max(1, kernel))
    comps_thr = _connected_components(thr, min_area=min_area)
    comps_thri = _connected_components(thri, min_area=min_area)
    if len(comps_thri) > len(comps_thr):
        mask = thri
        comps = comps_thri
        inverted = True
    else:
        mask = thr
        comps = comps_thr
        inverted = False

    # Annotate
    annotated = img.copy()
    # Distinct colors (cycled)
    palette = [
        (255, 88, 88),
        (88, 200, 255),
        (120, 255, 120),
        (255, 220, 120),
        (200, 160, 255),
        (255, 140, 220),
        (120, 220, 255),
    ]
    for i, c in enumerate(comps):
        color = palette[i % len(palette)]
        pt1 = (c.x, c.y)
        pt2 = (c.x + c.w, c.y + c.h)
        cv.rectangle(annotated, pt1, pt2, color, thickness=2)
        label_text = f"{i+1}({c.w}x{c.h},{c.area})"
        cv.putText(annotated, label_text, (c.x + 2, max(0, c.y - 6)), cv.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv.LINE_AA)

    # Define output path
    if output_path is None:
        output_path = input_path.with_name(f"{input_path.stem}_annotated{input_path.suffix}")

    # Save annotated image
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ok = cv.imwrite(str(output_path), annotated)
    if not ok:
        raise RuntimeError(f"Failed to write annotated image: {output_path}")

    # Optionally export each ROI
    if export_roi:
        roi_dir = output_path.with_suffix("").with_name(f"{output_path.stem}_rois")
        roi_dir.mkdir(parents=True, exist_ok=True)
        for i, c in enumerate(comps):
            crop = img[c.y : c.y + c.h, c.x : c.x + c.w]
            cv.imwrite(str(roi_dir / f"roi_{i+1:03d}_x{c.x}_y{c.y}_w{c.w}_h{c.h}.png"), crop)

    # Print a brief summary for convenience
    total = len(comps)
    fg_ratio = float(np.count_nonzero(mask)) / mask.size
    mode = "binary_inv" if inverted else "binary"
    print(f"Detected {total} blocks (min_area={min_area}, kernel={kernel}, mode={mode}, fg_ratio={fg_ratio:.2f}).")
    print(f"Saved: {output_path}")
    return output_path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Annotate all blocks in an image with bounding boxes.")
    p.add_argument("input", help="Path to input image (e.g., images/proc_*/step_02_projection.png)")
    p.add_argument("--output", help="Optional output path. Defaults to <input>_annotated.png", default=None)
    p.add_argument("--min-area", type=int, default=100, help="Minimum area in pixels to keep a component")
    p.add_argument("--kernel", type=int, default=3, help="Morphological close kernel size (>=1; 1 disables)")
    p.add_argument("--export-roi", action="store_true", help="Export each detected ROI as a separate image")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    in_path = Path(os.path.normpath(args.input))
    out_path = Path(os.path.normpath(args.output)) if args.output else None
    annotate_blocks(
        input_path=in_path,
        output_path=out_path,
        min_area=max(1, int(args.min_area)),
        kernel=max(1, int(args.kernel)),
        export_roi=bool(args.export_roi),
    )


if __name__ == "__main__":
    main()

