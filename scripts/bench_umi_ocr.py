"""
Benchmark Umi-OCR performance: CLI vs HTTP

Requirements:
- Umi-OCR must be running with HTTP service enabled (default: 127.0.0.1:1224).
- The `umi-ocr` CLI must be available on PATH.

Run:
- uv run python scripts/bench_umi_ocr.py
- Options: see `-h`

Notes:
- The CLI internally communicates via Umi-OCR's HTTP interface, so results may be close.
- This script avoids third-party dependencies (uses urllib for HTTP).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Iterable, List, Tuple


def read_image_b64(path: Path) -> str:
    with path.open("rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def http_get(url: str, timeout: float = 10.0) -> Tuple[int, str]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")
            return status, body
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def http_post_json(url: str, payload: dict, timeout: float = 30.0) -> Tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
            body = resp.read().decode("utf-8", errors="replace")
            return status, body
    except Exception as e:  # noqa: BLE001
        return 0, str(e)


def ensure_http_alive(base_url: str) -> None:
    status, body = http_get(f"{base_url}/api/ocr/get_options")
    if status != 200:
        raise RuntimeError(
            "Umi-OCR HTTP server not reachable. "
            f"GET {base_url}/api/ocr/get_options -> status={status}, body={body[:200]}\n"
            "Please ensure Umi-OCR is running and HTTP service is enabled (host=Local)."
        )


def bench_http(images: Iterable[Path], repeats: int, base_url: str) -> List[float]:
    times: List[float] = []
    # Warm-up a tiny POST to load engine
    dummy_payload = {"base64": base64.b64encode(b"\x89PNG\r\n\x1a\n").decode("ascii"), "options": {"data.format": "text"}}
    http_post_json(f"{base_url}/api/ocr", dummy_payload, timeout=5.0)

    for i in range(repeats):
        for img in images:
            b64 = read_image_b64(img)
            payload = {
                "base64": b64,
                "options": {"data.format": "text"},
            }
            t0 = time.perf_counter()
            status, body = http_post_json(f"{base_url}/api/ocr", payload)
            dt = time.perf_counter() - t0
            if status != 200:
                raise RuntimeError(f"HTTP OCR failed for {img.name}: status={status}, body={body[:200]}")
            try:
                resp = json.loads(body)
            except json.JSONDecodeError as e:  # noqa: PERF203
                raise RuntimeError(f"Invalid JSON for {img.name}: {e}\nBody head: {body[:200]}") from e
            if resp.get("code") not in (100, 101):
                raise RuntimeError(f"HTTP OCR error for {img.name}: {resp}")
            times.append(dt)
    return times


def which(cmd: str) -> str | None:
    # Simple PATH lookup, cross-platform
    paths = os.environ.get("PATH", "").split(os.pathsep)
    exts = [""]
    if sys.platform == "win32":
        pathext = os.environ.get("PATHEXT", ".EXE;.BAT;.CMD").split(";")
        exts = pathext
    for p in paths:
        full = Path(p) / cmd
        if full.is_file():
            return str(full)
        # try with extensions
        for ext in exts:
            full_ext = Path(p) / (cmd + ext)
            if full_ext.is_file():
                return str(full_ext)
    return None


def bench_cli(images: Iterable[Path], repeats: int, cli_name: str) -> List[float]:
    cli_path = which(cli_name)
    if not cli_path:
        raise RuntimeError(
            f"CLI '{cli_name}' not found on PATH. Install Umi-OCR CLI or add it to PATH."
        )

    times: List[float] = []
    # Attempt a quick help warm-up and hide UI (non-fatal)
    try:
        cli_dir = str(Path(cli_path).resolve().parent)
        subprocess.run([cli_path, "--help"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, cwd=cli_dir)
        subprocess.run([cli_path, "--hide"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False, cwd=cli_dir)
    except Exception:  # noqa: BLE001
        pass

    output_dir = Path("output").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    cli_cwd = Path(cli_path).resolve().parent

    def run_once(img_path: Path, out_path: Path) -> float:
        # Prefer POSIX-style paths to avoid backslash-escape quirks in some parsers
        img_arg = img_path.as_posix()
        out_arg = out_path.as_posix()

        # Try two forms: --output and "-->" (arrow alias)
        candidates = [
            [cli_path, "--hide", "--path", img_arg, "--output", out_arg],
            [cli_path, "--hide", "--output", out_arg, "--path", img_arg],
            [cli_path, "--hide", "--path", img_arg, "-->", out_arg],
            [cli_path, "--hide", "-->", out_arg, "--path", img_arg],
        ]

        last_err: str | None = None
        for cmd in candidates:
            t0 = time.perf_counter()
            proc = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(cli_cwd),
            )
            dt = time.perf_counter() - t0

            # Validate: rc==0 AND output file exists and is non-empty
            if proc.returncode == 0 and out_path.exists():
                try:
                    if out_path.stat().st_size > 0:
                        return dt
                except OSError:
                    pass
            last_err = f"rc={proc.returncode}, stderr_head={proc.stderr[:200]!r}"
            # Small backoff and try next form
            time.sleep(0.1)

        raise RuntimeError(
            f"CLI OCR failed for {img_path.name}: {last_err or 'unknown error'}\n"
            f"Tried commands: {candidates}"
        )

    def run_batched(imgs: List[Path]) -> float:
        # Single CLI invocation with multiple paths, one output file
        out_path = output_dir / f"bench_cli_batch_{int(time.time())}.txt"
        if out_path.exists():
            try:
                out_path.unlink()
            except OSError:
                pass
        img_args = [p.resolve().as_posix() for p in imgs]
        candidates = [
            [cli_path, "--hide", "--path", *img_args, "--output", out_path.as_posix()],
            [cli_path, "--hide", "--output", out_path.as_posix(), "--path", *img_args],
            [cli_path, "--hide", "--path", *img_args, "-->", out_path.as_posix()],
            [cli_path, "--hide", "-->", out_path.as_posix(), "--path", *img_args],
        ]
        last_err: str | None = None
        for cmd in candidates:
            t0 = time.perf_counter()
            proc = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                cwd=str(cli_cwd),
            )
            dt = time.perf_counter() - t0
            if proc.returncode == 0 and out_path.exists():
                try:
                    if out_path.stat().st_size > 0:
                        return dt
                except OSError:
                    pass
            last_err = f"rc={proc.returncode}, stderr_head={proc.stderr[:200]!r}"
            time.sleep(0.1)
        raise RuntimeError(
            f"CLI batch failed: {last_err or 'unknown error'}\nTried commands: {candidates}"
        )

    # Try per-image invocations first
    idx = 0
    try:
        for _ in range(repeats):
            for img in images:
                idx += 1
                out_path = output_dir / f"bench_cli_{idx}_{img.stem}.txt"
                if out_path.exists():
                    try:
                        out_path.unlink()
                    except OSError:
                        pass
                dt = run_once(img.resolve(), out_path)
                times.append(dt)
                time.sleep(0.05)
        return times
    except Exception:
        # Fallback: one batch invocation with all images repeated
        imgs: List[Path] = []
        for _ in range(repeats):
            imgs.extend([Path(p) for p in images])
        dt_total = run_batched(imgs)
        # Approximate per-image durations evenly
        per = dt_total / max(len(imgs), 1)
        return [per for _ in range(len(imgs))]


def summarize(name: str, samples: List[float]) -> str:
    if not samples:
        return f"{name}: no samples"
    avg = statistics.mean(samples)
    p50 = statistics.median(samples)
    p90 = statistics.quantiles(samples, n=10)[8] if len(samples) >= 10 else max(samples)
    return (
        f"{name}: count={len(samples)}, avg={avg*1000:.1f} ms, "
        f"p50={p50*1000:.1f} ms, p90={p90*1000:.1f} ms"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark Umi-OCR CLI vs HTTP")
    parser.add_argument(
        "--repeats", type=int, default=5, help="Number of repeats per image per method (default: 5)",
    )
    parser.add_argument(
        "--http-url", default="http://127.0.0.1:1224", help="Umi-OCR HTTP base URL (default: http://127.0.0.1:1224)",
    )
    parser.add_argument(
        "--umi-cli", default="umi-ocr", help="Umi-OCR CLI executable name (default: umi-ocr)",
    )
    parser.add_argument(
        "--images",
        nargs="*",
        default=[
            str(Path("images") / "_currency_total_roi.png"),
            str(Path("images") / "_currency_avg_roi.png"),
        ],
        help="Image paths to test (default: repository currency ROI images)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    images = [Path(p).resolve() for p in args.images]
    missing = [str(p) for p in images if not p.is_file()]
    if missing:
        print("Missing images:", *missing, sep="\n- ")
        sys.exit(2)

    # Ensure HTTP server alive before timing
    try:
        ensure_http_alive(args.http_url)
    except Exception as e:  # noqa: BLE001
        print(str(e))
        sys.exit(2)

    print("Benchmarking Umi-OCR (", ", ".join(p.name for p in images), ")", sep="")
    print(f"Repeats per image per method: {args.repeats}")

    # HTTP
    print("\n[1/2] HTTP …")
    http_times = bench_http(images, args.repeats, args.http_url)
    print(summarize("HTTP", http_times))

    # CLI
    print("\n[2/2] CLI …")
    cli_times = bench_cli(images, args.repeats, args.umi_cli)
    print(summarize("CLI", cli_times))

    # Comparison
    if http_times and cli_times:
        http_avg = statistics.mean(http_times)
        cli_avg = statistics.mean(cli_times)
        faster = "HTTP" if http_avg < cli_avg else "CLI"
        ratio = (max(http_avg, cli_avg) / max(min(http_avg, cli_avg), 1e-9))
        print(
            f"\nWinner: {faster} (≈{ratio:.2f}x vs slower method on average)"
        )


if __name__ == "__main__":
    main()
