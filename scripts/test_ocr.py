"""
OCR engine quick benchmark on sample images.

Images tested by default:
- images/test/avg_price.png
- images/test/current_number.png
- images/test/price_and_number.png

It measures elapsed time and prints raw OCR text for each engine/setting.

Run:
  uv run python scripts/test_ocr.py

Options (see -h):
  --repeats N         number of runs per case (default: 3)
  --engines ...       comma list: tesseract,easyocr,umi (default: all)
  --umi-url URL       Umi-OCR HTTP base URL (default: http://127.0.0.1:1224)
  --umi-timeout SEC   Umi-OCR HTTP timeout seconds (default: 2.5)
  --easyocr-langs ... comma list, e.g. en,ch_sim (default: en)
  --print-full        print full text (default: truncate)
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Iterable, List, Tuple

import sys
from pathlib import Path as _Path

# Ensure project root on sys.path so we can import ocr_reader when running from scripts/
_ROOT = _Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from ocr_reader import read_text


DEFAULT_IMAGES = [
    Path("images") / "test" / "avg_price.png",
    Path("images") / "test" / "current_number.png",
    Path("images") / "test" / "price_and_number.png",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="OCR engines quick test")
    p.add_argument(
        "--repeats", type=int, default=3, help="Runs per engine/setting/image (default: 3)",
    )
    p.add_argument(
        "--engines",
        default="tesseract,easyocr,umi",
        help="Comma list of engines to test: tesseract,easyocr,umi (default: all)",
    )
    p.add_argument(
        "--images",
        nargs="*",
        default=[str(p) for p in DEFAULT_IMAGES],
        help="Image files to test (default: predefined 3 files)",
    )
    p.add_argument(
        "--umi-url", default="http://127.0.0.1:1224", help="Umi-OCR HTTP base URL (default: http://127.0.0.1:1224)",
    )
    p.add_argument(
        "--umi-timeout", type=float, default=2.5, help="Umi-OCR HTTP timeout seconds (default: 2.5)",
    )
    p.add_argument(
        "--easyocr-langs", default="en", help="EasyOCR languages, comma list (default: en)",
    )
    p.add_argument(
        "--print-full", action="store_true", help="Print full OCR text instead of truncating",
    )
    return p.parse_args()


def engine_list(s: str) -> List[str]:
    items = [x.strip().lower() for x in s.split(",") if x.strip()]
    valid = {"tesseract", "easyocr", "umi"}
    return [x for x in items if x in valid]


def short(text: str, n: int = 120) -> str:
    t = (text or "").replace("\n", " ")
    return t if len(t) <= n else t[: n - 3] + "..."


def ensure_exists(paths: Iterable[Path]) -> List[Path]:
    out: List[Path] = []
    for p in paths:
        if p.is_file():
            out.append(p)
        else:
            print(f"[skip] Missing image: {p}")
    return out


def warm_up(engine: str, *, umi_url: str, umi_timeout: float, easyocr_langs: List[str]) -> None:
    """A tiny warm-up call per engine to initialize models/binaries."""
    from PIL import Image

    tiny = Image.new("L", (2, 2), color=0)
    try:
        if engine == "tesseract":
            _ = read_text(tiny, engine="tesseract", grayscale=False, tess_config="--oem 3 --psm 6")
        elif engine == "easyocr":
            _ = read_text(tiny, engine="easyocr", grayscale=False, easyocr_langs=easyocr_langs)
        elif engine == "umi":
            _ = read_text(tiny, engine="umi", grayscale=False, umi_base_url=umi_url, umi_timeout=umi_timeout)
    except Exception:
        # warm-up failures are non-fatal; actual run will report errors
        pass


def run_case(
    img_path: Path,
    engine: str,
    grayscale: bool,
    *,
    umi_url: str,
    umi_timeout: float,
    easyocr_langs: List[str],
) -> Tuple[float, str, str | None]:
    """Returns (elapsed_sec, text, error)."""
    t0 = time.perf_counter()
    try:
        if engine == "tesseract":
            text = read_text(
                str(img_path),
                engine="tesseract",
                grayscale=grayscale,
                tess_config="--oem 3 --psm 6",
            )
        elif engine == "easyocr":
            text = read_text(
                str(img_path),
                engine="easyocr",
                grayscale=grayscale,
                easyocr_langs=easyocr_langs,
            )
        elif engine == "umi":
            text = read_text(
                str(img_path),
                engine="umi",
                grayscale=grayscale,
                umi_base_url=umi_url,
                umi_timeout=umi_timeout,
                umi_options={"data.format": "text"},
            )
        else:
            raise ValueError(engine)
        dt = time.perf_counter() - t0
        return dt, text, None
    except Exception as e:  # noqa: BLE001
        dt = time.perf_counter() - t0
        return dt, "", str(e)


def main() -> None:
    args = parse_args()
    engines = engine_list(args.engines)
    if not engines:
        print("No valid engines selected; choose from: tesseract,easyocr,umi")
        return
    imgs = ensure_exists(Path(p) for p in args.images)
    if not imgs:
        print("No images to test.")
        return

    easy_langs = [x.strip() for x in args.easyocr_langs.split(",") if x.strip()]

    print("Engines:", ", ".join(engines))
    print("Repeats per case:", args.repeats)
    print("")

    # Warm up each engine once to reduce one-time overhead
    for eng in engines:
        warm_up(eng, umi_url=args.umi_url, umi_timeout=args.umi_timeout, easyocr_langs=easy_langs)

    for img in imgs:
        print(f"Image: {img}")
        for eng in engines:
            for gray in (False, True):
                label = f"{eng} | gray={gray}"
                times: List[float] = []
                last_text = ""
                last_err: str | None = None
                for _ in range(max(1, int(args.repeats))):
                    dt, text, err = run_case(
                        img,
                        eng,
                        gray,
                        umi_url=args.umi_url,
                        umi_timeout=args.umi_timeout,
                        easyocr_langs=easy_langs,
                    )
                    times.append(dt)
                    last_text = text
                    last_err = err
                avg_ms = 1000.0 * (sum(times) / max(len(times), 1))
                best_ms = 1000.0 * min(times) if times else 0.0
                if last_err:
                    print(f"- {label}: avg={avg_ms:.1f} ms, best={best_ms:.1f} ms, ERROR: {last_err}")
                else:
                    text_out = last_text if args.print_full else short(last_text)
                    print(f"- {label}: avg={avg_ms:.1f} ms, best={best_ms:.1f} ms, text: {text_out}")
        print("")


if __name__ == "__main__":
    main()
