"""
Umi-OCR HTTP 工具封装（仅 Umi 模式）。

提供：
- 普通文本识别：返回每个文本块的包围矩形坐标与文本内容；
- 数字识别：返回每个文本块的包围矩形坐标、清洗后的数字字符串与解析出的整数值。

约定与说明：
- 仅调用 Umi-OCR 的 /api/ocr 接口；
- 强制设置 options["data.format"] = "dict"，以便拿到位置坐标信息；
- 数字识别不考虑小数点：遇到 '.' 将被直接清洗去除；不处理 1.5K 之类形式；
- 不在 options 中传递白名单；数字清洗在客户端完成；
- 返回的坐标为包围矩形 (x, y, w, h)，支持通过 offset 参数做整体平移（如从 ROI 坐标映射到全屏）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import base64 as _b64
import io as _io
import os


ImageLike = Union["Image.Image", "np.ndarray", str]


# 可选依赖：Pillow / numpy
try:
    from PIL import Image  # type: ignore
except Exception:  # pragma: no cover
    Image = None  # type: ignore

try:
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover
    np = None  # type: ignore


@dataclass
class OcrBox:
    """普通文本识别结果

    字段：
    - text: 识别文本
    - bbox: 包围矩形 (x, y, w, h)
    - score: 置信度（0~1，部分引擎/版本可能缺失）
    """

    text: str
    bbox: Tuple[int, int, int, int]
    score: Optional[float] = None


@dataclass
class NumberBox(OcrBox):
    """数字识别结果（在 OcrBox 基础上增加清洗值与数值）"""

    clean_text: str = ""
    value: Optional[int] = None


def _ensure_pil(img: ImageLike) -> "Image.Image":
    """将输入统一为 PIL.Image。支持：路径字符串、numpy 数组、PIL.Image。

    - 路径：必须存在；
    - numpy：灰度/彩色均可；彩色默认按 BGR->RGB 反转；
    - PIL：直接返回。
    """
    if Image is None:  # pragma: no cover
        raise RuntimeError("缺少 Pillow 依赖，请安装 pillow 库")

    if isinstance(img, str):
        if not os.path.exists(img):
            raise FileNotFoundError(f"图片文件不存在: {img}")
        return Image.open(img)

    if hasattr(img, "__class__") and img.__class__.__name__ == "Image":  # type: ignore[attr-defined]
        return img  # type: ignore[return-value]

    if np is not None and hasattr(img, "ndim"):
        arr = img  # type: ignore[assignment]
        if getattr(arr, "ndim", 0) == 2:
            return Image.fromarray(arr)
        if getattr(arr, "ndim", 0) == 3:
            h, w, c = getattr(arr, "shape", (0, 0, 0))
            if c == 3:
                try:
                    return Image.fromarray(arr[:, :, ::-1])  # BGR -> RGB
                except Exception:
                    return Image.fromarray(arr)
            return Image.fromarray(arr)

    raise TypeError("不支持的图片类型：请传入路径/PIL.Image/numpy.ndarray")


def _pil_to_base64(pil_img: "Image.Image") -> str:
    """PIL.Image 转 base64（PNG）。"""
    bio = _io.BytesIO()
    try:
        pil_img.save(bio, format="PNG")
    except Exception:
        pil_img.convert("RGB").save(bio, format="PNG")
    return _b64.b64encode(bio.getvalue()).decode("ascii")


def _quad_to_bbox(quad: Sequence[Sequence[float]]) -> Tuple[int, int, int, int]:
    """四点坐标 -> 包围矩形 (x, y, w, h)。

    quad 形如 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]；容错：点数/类型异常时抛错。
    """
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
    """调用 Umi-OCR /api/ocr，返回解析后的 JSON 字典。

    注意：本函数会强制设置 options["data.format"] = "dict"。
    抛出 RuntimeError 表示接口调用失败（非 100/101 等）。
    """
    try:
        import requests  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("缺少 requests 依赖，请安装 requests 库") from e

    url = str(base_url).rstrip("/") + "/api/ocr"
    data_b64 = _pil_to_base64(pil_img)
    opts = dict(options or {})
    # 强制拿到位置信息
    try:
        opts["data.format"] = "dict"
    except Exception:
        pass
    payload: Dict[str, Any] = {"base64": data_b64, "options": opts}

    resp = requests.post(url, json=payload, timeout=float(timeout or 2.5))
    resp.raise_for_status()
    j = resp.json()
    code = int(j.get("code", 0) or 0)
    # code: 100 成功；101 无文本；其余失败
    if code in (100, 101):
        return j
    # 失败：data 可能为字符串原因
    reason = j.get("data")
    raise RuntimeError(f"Umi-OCR 识别失败: code={code}, data={reason}")


def recognize_text(
    image: ImageLike,
    *,
    base_url: str = "http://127.0.0.1:1224",
    timeout: float = 2.5,
    options: Optional[Dict[str, Any]] = None,
    offset: Tuple[int, int] = (0, 0),
) -> List[OcrBox]:
    """普通文本识别：返回文本块与包围矩形。

    参数：
    - image: 路径/PIL/numpy
    - base_url/timeout/options: Umi-OCR HTTP 参数；会强制 options["data.format"] = "dict"
    - offset: (ox, oy) 输出坐标整体平移（用于将 ROI 相对坐标映射到全屏）
    返回：OcrBox 列表（可能为空）
    """
    pil = _ensure_pil(image)
    j = _post_umi_ocr(pil, base_url=base_url, timeout=timeout, options=options)
    code = int(j.get("code", 0) or 0)
    if code == 101:
        return []
    data = j.get("data")
    if not isinstance(data, list):
        return []
    ox, oy = int(offset[0]), int(offset[1])
    out: List[OcrBox] = []
    for blk in data:
        if not isinstance(blk, dict):
            continue
        text = str(blk.get("text", "") or "")
        box = blk.get("box")
        if not isinstance(box, (list, tuple)):
            continue
        try:
            x, y, w, h = _quad_to_bbox(box)
            x += ox
            y += oy
        except Exception:
            continue
        try:
            score = blk.get("score", None)
            score_f = float(score) if score is not None else None
        except Exception:
            score_f = None
        out.append(OcrBox(text=text, bbox=(int(x), int(y), int(w), int(h)), score=score_f))
    return out


def _clean_number_text(s: str) -> Tuple[str, Optional[int]]:
    """清洗数字字符串并解析为整数。

    规则：
    - 仅保留 0-9 与 KkMm；小数点 '.' 直接去除；
    - 结果形如："123"、"123K"、"2M"；
    - 值转换：K=×1000，M=×1_000_000；无法解析返回 (clean, None)。
    """
    if not s:
        return "", None
    # 只保留 0-9KkMm
    raw = [ch for ch in s if ch.isdigit() or ch in "KkMm"]
    if not raw:
        return "", None
    # 规范化：最多取一个后缀，优先第一个出现的 K/M
    digits: List[str] = []
    suffix: Optional[str] = None
    for ch in raw:
        if ch.isdigit():
            digits.append(ch)
        elif suffix is None and ch in "KkMm":
            suffix = ch.upper()
    if not digits:
        return "", None
    clean = "".join(digits) + (suffix or "")
    try:
        base = int("".join(digits))
    except Exception:
        return clean, None
    if suffix == "K":
        return clean, int(base * 1000)
    if suffix == "M":
        return clean, int(base * 1_000_000)
    return clean, int(base)


def recognize_numbers(
    image: ImageLike,
    *,
    base_url: str = "http://127.0.0.1:1224",
    timeout: float = 2.5,
    options: Optional[Dict[str, Any]] = None,
    offset: Tuple[int, int] = (0, 0),
) -> List[NumberBox]:
    """数字识别：返回包围矩形 + 清洗结果 + 数值。

    注意：不会处理小数点，遇到 '.' 将直接被清洗去除；不会出现 1.5K 这类形式。
    """
    boxes = recognize_text(
        image,
        base_url=base_url,
        timeout=timeout,
        options=options,
        offset=offset,
    )
    out: List[NumberBox] = []
    for b in boxes:
        clean, val = _clean_number_text(b.text)
        # 仅保留清洗后仍含有效数字的条目
        if not clean or not any(ch.isdigit() for ch in clean):
            continue
        out.append(
            NumberBox(
                text=b.text,
                bbox=b.bbox,
                score=b.score,
                clean_text=clean,
                value=val,
            )
        )
    return out


__all__ = [
    "OcrBox",
    "NumberBox",
    "recognize_text",
    "recognize_numbers",
]

