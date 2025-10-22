"""
Simple OCR abstraction that accepts an image and returns raw text.

Supports engines:
- "umi" (Umi-OCR HTTP API)
- "tesseract" (pytesseract)

Design goals:
- Input accepts file path, PIL.Image, or numpy array.
- Optional grayscale conversion only; no other preprocessing.
- Return raw text without postprocessing; callers decide how to parse.

Usage example:

    from PIL import Image
    from ocr_reader import read_text

    img = Image.open("images/sample.png")
    txt = read_text(img, engine="tesseract", grayscale=True)
    print(txt)

"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import os

ImageLike = Union["Image.Image", "np.ndarray", str]


# Lazy imports for optional deps
try:  # Pillow
    from PIL import Image
except Exception:  # pragma: no cover - optional at import time
    Image = None  # type: ignore

try:  # numpy
    import numpy as np  # type: ignore
except Exception:  # pragma: no cover - optional at import time
    np = None  # type: ignore


def _ensure_pil(img: ImageLike) -> "Image.Image":
    """Normalize input to a PIL.Image.

    Accepts: file path (str), numpy array (RGB/BGR/Gray), or PIL.Image.
    """
    if Image is None:  # pragma: no cover - import/runtime guard
        raise RuntimeError("缺少 Pillow 依赖，请安装 pillow 包。")

    if isinstance(img, str):
        if not os.path.exists(img):
            raise FileNotFoundError(f"图像文件不存在: {img}")
        return Image.open(img)

    # Already PIL
    if hasattr(img, "__class__") and img.__class__.__name__ == "Image":  # type: ignore[attr-defined]
        return img  # type: ignore[return-value]

    # numpy array path
    if np is not None and hasattr(img, "ndim"):
        arr = img  # type: ignore[assignment]
        if getattr(arr, "ndim", 0) == 2:  # gray
            return Image.fromarray(arr)
        if getattr(arr, "ndim", 0) == 3:  # color
            h, w, c = getattr(arr, "shape", (0, 0, 0))
            if c == 3:
                try:
                    # Assume BGR (OpenCV) and convert to RGB
                    return Image.fromarray(arr[:, :, ::-1])
                except Exception:
                    return Image.fromarray(arr)
            return Image.fromarray(arr)
    raise TypeError("不支持的图像类型：请传入路径、PIL.Image 或 numpy.ndarray")


# -------------------- Tesseract --------------------

def _maybe_init_tesseract() -> None:
    try:
        import pytesseract  # type: ignore
    except Exception as e:  # pragma: no cover - import/runtime guard
        raise RuntimeError("缺少 pytesseract 依赖，请安装 pytesseract 包。") from e

    ts_cmd = getattr(pytesseract.pytesseract, "tesseract_cmd", None)
    import shutil

    def _is_valid(cmd: str) -> bool:
        return os.path.isabs(cmd) and os.path.exists(cmd) or shutil.which(cmd) is not None

    if isinstance(ts_cmd, str) and _is_valid(ts_cmd):
        return
    for p in (
        r"C:\\Program Files\\Tesseract-OCR\\tesseract.exe",
        r"C:\\Program Files (x86)\\Tesseract-OCR\\tesseract.exe",
    ):
        if os.path.exists(p):
            pytesseract.pytesseract.tesseract_cmd = p
            return
    # otherwise rely on PATH


def _ocr_tesseract(pil_img: "Image.Image", config: Optional[str] = None) -> str:
    _maybe_init_tesseract()
    import pytesseract  # type: ignore

    cfg = config or "--oem 3 --psm 6"
    try:
        return pytesseract.image_to_string(pil_img, config=cfg) or ""
    except Exception as e:
        raise RuntimeError(f"Tesseract 识别失败: {e}") from e


# -------------------- Umi-OCR (HTTP) --------------------

@dataclass
class UmiConfig:
    base_url: str = "http://127.0.0.1:1224"
    timeout_sec: float = 5.0
    options: Dict[str, Any] | None = None  # forwarded to Umi-OCR


def _ocr_umi_http(pil_img: "Image.Image", cfg: Optional[UmiConfig] = None) -> str:
    import base64
    import io
    try:
        import requests  # type: ignore
    except Exception as e:  # pragma: no cover
        raise RuntimeError("缺少 requests 依赖，请安装 requests 包。") from e

    ocfg = cfg or UmiConfig()
    base_url = str(ocfg.base_url).rstrip("/")
    url = base_url + "/api/ocr"
    timeout = float(ocfg.timeout_sec or 5.0)
    opts = dict(ocfg.options or {})

    buf = io.BytesIO()
    try:
        pil_img.save(buf, format="PNG")
    except Exception:
        pil_img.convert("RGB").save(buf, format="PNG")
    data_b64 = base64.b64encode(buf.getvalue()).decode("ascii")

    payload: Dict[str, Any] = {"base64": data_b64}
    if opts:
        payload["options"] = opts

    resp = requests.post(url, json=payload, timeout=timeout)
    resp.raise_for_status()
    j = resp.json()
    # Umi-OCR: code==100 ok; code==101 partially ok; otherwise error
    if int(j.get("code", 0) or 0) not in (100, 101):
        raise RuntimeError(f"Umi-OCR HTTP 错误: {j}")

    data = j.get("data")
    if isinstance(data, list):
        texts = [str(e.get("text", "")).strip() for e in data if isinstance(e, dict)]
        return "\n".join(t for t in texts if t)
    if isinstance(data, str):
        return data
    # Some server versions put text under other wrappers; fall back to best-effort
    return str(data or "")


# -------------------- Public API --------------------

def read_text(
    image: ImageLike,
    *,
    engine: str = "umi",
    grayscale: bool = False,
    # Tesseract options
    tess_config: Optional[str] = None,
    # Umi options
    umi_base_url: Optional[str] = None,
    umi_timeout: float = 5.0,
    umi_options: Optional[Dict[str, Any]] = None,
) -> str:
    """Run OCR on `image` with the selected `engine` and return text.

    Parameters:
        image: str|PIL.Image|np.ndarray
        engine: one of {"umi", "tesseract"}
        grayscale: whether to convert to grayscale before OCR
        tess_config: optional passthrough config string for pytesseract
        umi_base_url: HTTP base URL for Umi-OCR service
        umi_timeout: HTTP timeout seconds
        umi_options: forward options to Umi-OCR (e.g., {"data.format": "text"})

    Returns:
        Raw OCR text (no additional parsing or normalization).
    """
    pil = _ensure_pil(image)
    if grayscale:
        try:
            pil = pil.convert("L")
        except Exception:
            pass

    eng = (engine or "tesseract").strip().lower()
    if eng in ("tesseract", "tess", "ts"):
        return _ocr_tesseract(pil, config=tess_config)
    if eng in ("umi", "umi-ocr", "umiocr"):
        cfg = UmiConfig(
            base_url=umi_base_url or "http://127.0.0.1:1224",
            timeout_sec=float(umi_timeout or 5.0),
            options=umi_options or {},
        )
        return _ocr_umi_http(pil, cfg)
    raise ValueError(f"未知的 OCR 引擎: {engine!r}，可选：umi / tesseract")


__all__ = [
    "read_text",
    "UmiConfig",
]
