"""
Umi-OCR HTTP 封装。
"""

from __future__ import annotations

import base64
import io
import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

ImageLike = Union["Image.Image", "numpy.ndarray", str]

try:
    from PIL import Image  # type: ignore
except Exception:
    Image = None  # type: ignore

try:
    import numpy as np  # type: ignore
except Exception:
    np = None  # type: ignore


@dataclass
class OcrBox:
    text: str
    bbox: Tuple[int, int, int, int]
    score: Optional[float] = None


@dataclass
class NumberBox(OcrBox):
    clean_text: str = ""
    value: Optional[int] = None


def _ensure_pil(img: ImageLike) -> "Image.Image":
    if Image is None:
        raise RuntimeError("缺少 Pillow 依赖，请安装 pillow 库")
    if isinstance(img, str):
        if not os.path.exists(img):
            raise FileNotFoundError(f"图片文件不存在: {img}")
        return Image.open(img)
    if hasattr(img, "__class__") and img.__class__.__name__ == "Image":
        return img  # type: ignore[return-value]
    if np is not None and hasattr(img, "ndim"):
        arr = img  # type: ignore[assignment]
        if getattr(arr, "ndim", 0) == 2:
            return Image.fromarray(arr)
        if getattr(arr, "ndim", 0) == 3:
            if getattr(arr, "shape", (0, 0, 0))[2] == 3:
                try:
                    return Image.fromarray(arr[:, :, ::-1])
                except Exception:
                    return Image.fromarray(arr)
            return Image.fromarray(arr)
    raise TypeError("不支持的图片类型：请传入路径/PIL.Image/numpy.ndarray")


def _pil_to_base64(pil_img: "Image.Image") -> str:
    buf = io.BytesIO()
    try:
        pil_img.save(buf, format="PNG")
    except Exception:
        pil_img.convert("RGB").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _quad_to_bbox(quad: Sequence[Sequence[float]]) -> Tuple[int, int, int, int]:
    xs: List[float] = []
    ys: List[float] = []
    for pt in quad:
        if not isinstance(pt, (list, tuple)) or len(pt) < 2:
            continue
        xs.append(float(pt[0]))
        ys.append(float(pt[1]))
    if not xs or not ys:
        raise ValueError("无效的 box 四点坐标")
    x1, y1 = min(xs), min(ys)
    x2, y2 = max(xs), max(ys)
    return int(x1), int(y1), int(max(1, x2 - x1)), int(max(1, y2 - y1))


def _post_umi_ocr(
    pil_img: "Image.Image",
    *,
    base_url: str = "http://127.0.0.1:1224",
    timeout: float = 2.5,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    try:
        import requests  # type: ignore
    except Exception as exc:
        raise RuntimeError("缺少 requests 依赖，请安装 requests 库") from exc
    url = str(base_url).rstrip("/") + "/api/ocr"
    payload: Dict[str, Any] = {
        "base64": _pil_to_base64(pil_img),
        "options": dict(options or {}),
    }
    payload["options"]["data.format"] = "dict"
    resp = requests.post(url, json=payload, timeout=float(timeout or 2.5))
    resp.raise_for_status()
    data = resp.json()
    code = int(data.get("code", 0) or 0)
    if code in (100, 101):
        return data
    raise RuntimeError(f"Umi-OCR 识别失败: code={code}, data={data.get('data')}")


def recognize_text(
    image: ImageLike,
    *,
    base_url: str = "http://127.0.0.1:1224",
    timeout: float = 2.5,
    options: Optional[Dict[str, Any]] = None,
    offset: Tuple[int, int] = (0, 0),
) -> List[OcrBox]:
    pil = _ensure_pil(image)
    payload = _post_umi_ocr(pil, base_url=base_url, timeout=timeout, options=options)
    if int(payload.get("code", 0) or 0) == 101:
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    ox, oy = int(offset[0]), int(offset[1])
    result: List[OcrBox] = []
    for block in data:
        if not isinstance(block, dict):
            continue
        text = str(block.get("text", "") or "")
        box = block.get("box")
        if not isinstance(box, (list, tuple)):
            continue
        try:
            x, y, w, h = _quad_to_bbox(box)
        except Exception:
            continue
        score = block.get("score")
        result.append(
            OcrBox(
                text=text,
                bbox=(x + ox, y + oy, w, h),
                score=float(score) if score is not None else None,
            )
        )
    return result


def recognize_numbers(
    image: ImageLike,
    *,
    base_url: str = "http://127.0.0.1:1224",
    timeout: float = 2.5,
    options: Optional[Dict[str, Any]] = None,
    offset: Tuple[int, int] = (0, 0),
    allowlist: Iterable[str] | None = None,
) -> List[NumberBox]:
    boxes = recognize_text(
        image, base_url=base_url, timeout=timeout, options=options, offset=offset
    )
    allow = set(allowlist or ())
    result: List[NumberBox] = []
    for box in boxes:
        raw = box.text or ""
        clean = "".join(ch for ch in raw if ch.isdigit() or ch in "KkMm.")
        if allow and not set(clean).issubset(allow):
            continue
        value = None
        digits = "".join(ch for ch in clean if ch.isdigit())
        if digits:
            try:
                value = int(digits)
            except Exception:
                value = None
        result.append(
            NumberBox(
                text=box.text,
                bbox=box.bbox,
                score=box.score,
                clean_text=clean,
                value=value,
            )
        )
    return result


__all__ = ["OcrBox", "NumberBox", "recognize_numbers", "recognize_text"]
