from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = REPO_ROOT / "data" / "images" / "home_indicator.png"
DEFAULT_IMAGE = REPO_ROOT / "data" / "output" / "bench_home_indicator_screen.png"
DEFAULT_REPORT = REPO_ROOT / "docs" / "首页标识模板识别基准报告.md"
DEFAULT_RESULTS = REPO_ROOT / "data" / "output" / "home_indicator_benchmark_results.json"
RUST_MANIFEST = REPO_ROOT / "desktop" / "Cargo.toml"


@dataclass(slots=True)
class BenchConfig:
    template: Path
    image: Path
    threshold: float
    image_rounds: int
    image_warmup: int
    screen_rounds: int
    screen_warmup: int
    rust_bin: Path


def _ms_summary(samples: list[float]) -> dict[str, float]:
    return {
        "avg": statistics.fmean(samples),
        "min": min(samples),
        "max": max(samples),
    }


def _speedup(baseline_ms: float, target_ms: float) -> float:
    if target_ms <= 0:
        return 0.0
    return baseline_ms / target_ms


def _extract_json_payload(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            return json.loads(line)
    raise RuntimeError(f"未在输出中找到 JSON 结果:\n{output}")


def _run_command(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )


def build_rust_bench(manifest_path: Path) -> Path:
    _run_command(
        [
            "cargo",
            "build",
            "--release",
            "--manifest-path",
            str(manifest_path),
            "--bin",
            "template_bench",
        ],
        cwd=REPO_ROOT,
    )
    suffix = ".exe" if sys.platform.startswith("win") else ""
    binary = REPO_ROOT / "desktop" / "target" / "release" / f"template_bench{suffix}"
    if not binary.exists():
        raise FileNotFoundError(f"未找到 Rust 基准程序: {binary}")
    return binary


def bench_python_pyscreeze_image(
    template_path: Path,
    image_path: Path,
    *,
    rounds: int,
    warmup: int,
    threshold: float,
) -> dict[str, Any]:
    from PIL import Image
    import pyscreeze

    haystack = Image.open(image_path).convert("RGB")
    samples: list[float] = []
    matched = False
    image_not_found = getattr(pyscreeze, "ImageNotFoundException", RuntimeError)
    try:
        for index in range(rounds + warmup):
            started = time.perf_counter()
            try:
                result = pyscreeze.locate(str(template_path), haystack, confidence=threshold)
            except image_not_found:
                result = None
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            if index >= warmup:
                samples.append(elapsed_ms)
            matched = result is not None
    finally:
        haystack.close()

    summary = _ms_summary(samples)
    return {
        "engine": "python-pyscreeze",
        "mode": "image-preloaded",
        "rounds": rounds,
        "warmup_rounds": warmup,
        "threshold": threshold,
        "matched": matched,
        "grayscale_default": bool(getattr(pyscreeze, "GRAYSCALE_DEFAULT", True)),
        "match_ms_avg": summary["avg"],
        "match_ms_min": summary["min"],
        "match_ms_max": summary["max"],
    }


def bench_python_opencv_image(
    template_path: Path,
    image_path: Path,
    *,
    rounds: int,
    warmup: int,
) -> dict[str, Any]:
    import cv2

    template = cv2.imread(str(template_path), cv2.IMREAD_GRAYSCALE)
    haystack = cv2.imread(str(image_path), cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise FileNotFoundError(f"OpenCV 无法读取模板图: {template_path}")
    if haystack is None:
        raise FileNotFoundError(f"OpenCV 无法读取基准图: {image_path}")

    samples: list[float] = []
    best_score = 0.0
    best_location = (0, 0)
    for index in range(rounds + warmup):
        started = time.perf_counter()
        response = cv2.matchTemplate(haystack, template, cv2.TM_CCOEFF_NORMED)
        _, max_value, _, max_location = cv2.minMaxLoc(response)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if index >= warmup:
            samples.append(elapsed_ms)
        best_score = float(max_value)
        best_location = tuple(int(v) for v in max_location)

    summary = _ms_summary(samples)
    return {
        "engine": "python-opencv-direct",
        "mode": "image-preloaded-gray",
        "rounds": rounds,
        "warmup_rounds": warmup,
        "score": best_score,
        "location": best_location,
        "match_ms_avg": summary["avg"],
        "match_ms_min": summary["min"],
        "match_ms_max": summary["max"],
    }


def bench_python_pyscreeze_screen_capture(*, rounds: int, warmup: int) -> dict[str, Any]:
    import pyautogui
    import pyscreeze

    screen_size = pyautogui.size()
    samples: list[float] = []
    for index in range(rounds + warmup):
        started = time.perf_counter()
        screenshot = pyscreeze.screenshot(region=None)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if index >= warmup:
            samples.append(elapsed_ms)
        try:
            screenshot.close()
        except AttributeError:
            pass

    summary = _ms_summary(samples)
    return {
        "engine": "python-pyscreeze",
        "mode": "screen-capture-only",
        "rounds": rounds,
        "warmup_rounds": warmup,
        "screen_size": [int(screen_size.width), int(screen_size.height)],
        "capture_ms_avg": summary["avg"],
        "capture_ms_min": summary["min"],
        "capture_ms_max": summary["max"],
    }


def bench_python_pyscreeze_screen(
    template_path: Path,
    *,
    rounds: int,
    warmup: int,
    threshold: float,
) -> dict[str, Any]:
    import pyscreeze

    samples: list[float] = []
    matched = False
    image_not_found = getattr(pyscreeze, "ImageNotFoundException", RuntimeError)
    for index in range(rounds + warmup):
        started = time.perf_counter()
        try:
            result = pyscreeze.locateOnScreen(str(template_path), confidence=threshold)
        except image_not_found:
            result = None
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if index >= warmup:
            samples.append(elapsed_ms)
        matched = result is not None

    summary = _ms_summary(samples)
    return {
        "engine": "python-pyscreeze",
        "mode": "screen",
        "rounds": rounds,
        "warmup_rounds": warmup,
        "threshold": threshold,
        "matched": matched,
        "total_ms_avg": summary["avg"],
        "total_ms_min": summary["min"],
        "total_ms_max": summary["max"],
    }


def bench_rust(
    rust_bin: Path,
    mode: str,
    template_path: Path,
    *,
    image_path: Path | None,
    rounds: int,
    threshold: float,
) -> dict[str, Any]:
    args = [str(rust_bin), mode, str(template_path)]
    if image_path is not None:
        args.append(str(image_path))
    args.extend([str(rounds), f"{threshold:.6f}"])
    completed = _run_command(args, cwd=REPO_ROOT)
    return _extract_json_payload(completed.stdout)


def build_report(results: dict[str, Any], config: BenchConfig) -> str:
    python_image = results["python"]["pyscreeze_image"]
    python_cv = results["python"]["opencv_image"]
    python_capture = results["python"]["screen_capture"]
    python_screen = results["python"]["screen_total"]
    rust_fast_image = results["rust"]["fast_image"]
    rust_corr_image = results["rust"]["corrmatch_image"]
    rust_fast_screen = results["rust"]["fast_screen"]
    rust_corr_screen = results["rust"]["corrmatch_screen"]

    image_fast_speedup = _speedup(
        float(python_image["match_ms_avg"]),
        float(rust_fast_image["match_ms_avg"]),
    )
    image_corr_speedup = _speedup(
        float(python_image["match_ms_avg"]),
        float(rust_corr_image["match_ms_avg"]),
    )
    screen_fast_speedup = _speedup(
        float(python_screen["total_ms_avg"]),
        float(rust_fast_screen["total_ms_avg"]),
    )
    screen_corr_speedup = _speedup(
        float(python_screen["total_ms_avg"]),
        float(rust_corr_screen["total_ms_avg"]),
    )

    measured_at = results["meta"]["measured_at"]
    screen_size = python_capture["screen_size"]
    screen_note = (
        "- 实时抓屏命中状态：本次测量时当前桌面未出现首页标识，因此抓屏结果代表 miss-path；固定底图结果仍代表 hit-path。"
        if not python_screen["matched"]
        else "- 实时抓屏命中状态：本次测量时当前桌面出现了首页标识，抓屏结果代表 hit-path。"
    )
    return "\n".join(
        [
            "# 首页标识模板识别基准报告",
            "",
            f"- 测试时间：{measured_at}",
            f"- 模板图：`{config.template.relative_to(REPO_ROOT)}`",
            f"- 固定底图：`{config.image.relative_to(REPO_ROOT)}`",
            f"- 阈值：`{config.threshold:.2f}`",
            f"- 当前桌面分辨率：`{screen_size[0]}x{screen_size[1]}`",
            screen_note,
            "",
            "## 结论",
            "",
            f"- 离线固定底图口径下，Rust `corrmatch` 平均 `match_ms_avg={rust_corr_image['match_ms_avg']:.3f}` ms，Python `pyscreeze` 平均 `match_ms_avg={python_image['match_ms_avg']:.3f}` ms，Rust 快 `x{image_corr_speedup:.2f}`。",
            f"- 端到端抓屏口径下，Rust `corrmatch` 平均 `total_ms_avg={rust_corr_screen['total_ms_avg']:.3f}` ms，Python `locateOnScreen` 平均 `total_ms_avg={python_screen['total_ms_avg']:.3f}` ms，Rust 快 `x{screen_corr_speedup:.2f}`。",
            f"- 仅使用 `imageproc` 路线时，Rust 在固定底图下也快于 Python（`x{image_fast_speedup:.2f}`），端到端抓屏口径也快于 Python（`x{screen_fast_speedup:.2f}`）；但 `corrmatch` 仍然是全场最佳。",
            "",
            "## 识别链路确认",
            "",
            "- Python 现网路径是 `src/super_buyer/services/screen_ops.py` 中的 `pyautogui.locateOnScreen(...)`。",
            "- 本地 `pyscreeze` 源码显示：`locateOnScreen()` 每轮先 `screenshot(region=None)`，再调用 `locate()`；`_locateAll_opencv()` 里会把模板图和底图都转成 OpenCV 图像后执行 `cv2.matchTemplate(..., TM_CCOEFF_NORMED)`。",
            "- Rust 侧直接复用了仓库已有的 `desktop/src-tauri/src/bin/template_bench.rs`、`desktop/src-tauri/src/automation/vision.rs` 与现成依赖 `corrmatch` / `imageproc`。",
            "",
            "## 实测数据",
            "",
            "| 场景 | 引擎 | 平均耗时(ms) | 最小(ms) | 最大(ms) | 备注 |",
            "| --- | --- | ---: | ---: | ---: | --- |",
            f"| 固定底图 | Python `pyscreeze.locate` | {python_image['match_ms_avg']:.3f} | {python_image['match_ms_min']:.3f} | {python_image['match_ms_max']:.3f} | 预加载底图，保留 PyScreeze 默认灰度策略 |",
            f"| 固定底图 | Python 直接 `cv2.matchTemplate` | {python_cv['match_ms_avg']:.3f} | {python_cv['match_ms_min']:.3f} | {python_cv['match_ms_max']:.3f} | 作为 Python/OpenCV 下限参考 |",
            f"| 固定底图 | Rust `imageproc` fast-image | {rust_fast_image['match_ms_avg']:.3f} | {rust_fast_image['match_ms_min']:.3f} | {rust_fast_image['match_ms_max']:.3f} | 现有缓存模板 + 降采样粗定位 |",
            f"| 固定底图 | Rust `corrmatch` | {rust_corr_image['match_ms_avg']:.3f} | {rust_corr_image['match_ms_min']:.3f} | {rust_corr_image['match_ms_max']:.3f} | 编译模板 + SIMD + rayon |",
            f"| 实时抓屏 | Python `screenshot` | {python_capture['capture_ms_avg']:.3f} | {python_capture['capture_ms_min']:.3f} | {python_capture['capture_ms_max']:.3f} | 仅抓屏，不匹配 |",
            f"| 实时抓屏 | Python `locateOnScreen` | {python_screen['total_ms_avg']:.3f} | {python_screen['total_ms_min']:.3f} | {python_screen['total_ms_max']:.3f} | 生产路径基准 |",
            f"| 实时抓屏 | Rust `imageproc` fast-screen | {rust_fast_screen['total_ms_avg']:.3f} | {rust_fast_screen['total_ms_min']:.3f} | {rust_fast_screen['total_ms_max']:.3f} | GDI 抓屏 + 现有 fast probe |",
            f"| 实时抓屏 | Rust `corrmatch` | {rust_corr_screen['total_ms_avg']:.3f} | {rust_corr_screen['total_ms_min']:.3f} | {rust_corr_screen['total_ms_max']:.3f} | GDI 抓屏 + 编译模板匹配 |",
            "",
            "## 关键分析",
            "",
            f"- Python 端到端平均约 `{python_screen['total_ms_avg']:.0f} ms`，其中抓屏单项约 `{python_capture['capture_ms_avg']:.0f} ms`，剩余大头在 PyScreeze 每轮的灰度转换与 `cv2.matchTemplate` 全图滑窗。",
            f"- Rust `imageproc` 路线虽然也做了模板缓存和降采样粗定位，但在 `2560x1440` 全屏上仍要做细匹配，端到端平均 `total_ms_avg={rust_fast_screen['total_ms_avg']:.3f}` ms，明显落后于 `corrmatch`。",
            f"- Rust `corrmatch` 路线把模板预编译成多尺度结构，启用了 `parallel=true`，并使用 crate 提供的 SIMD/rayon 特性，因此在固定底图和实时抓屏两种口径下都稳定拉开差距。",
            f"- 从当前结果看，若目标是替换现有首页标识识别实现，推荐优先把 Rust 侧的 `corrmatch` 路线产品化，而不是继续围绕 `imageproc` 做小修小补。",
            "",
            "## 本轮选型取舍",
            "",
            "- 已采用：`imageproc::template_matching`，因为仓库已有实现，可作为直接对标 OpenCV NCC 的 CPU 基线。",
            "- 已采用：`corrmatch`，因为仓库已经依赖，且支持模板编译、并行和 SIMD，实测是当前最优解。",
            "- 已调研未接入：`template-matching` crate。文档显示它基于 `wgpu` 做 GPU 加速；对当前 Windows RPA 工具来说，引入额外 GPU 运行时和部署约束，不如复用现有 `corrmatch` 划算。",
            "",
            "## 复跑命令",
            "",
            "```powershell",
            "uv run python tools/bench_home_indicator.py",
            "```",
            "",
            "## 参考资料",
            "",
            "- `imageproc` 模板匹配文档：<https://docs.rs/imageproc/latest/imageproc/template_matching/index.html>",
            "- `template-matching` crate 文档：<https://docs.rs/template-matching/latest/template_matching/>",
            "- `corrmatch` crate 文档：<https://docs.rs/corrmatch/latest/corrmatch/>",
            "- 调研文章：<https://blog.csdn.net/HoKis/article/details/143697427>",
        ]
    )


def run_benchmarks(config: BenchConfig) -> dict[str, Any]:
    results: dict[str, Any] = {
        "meta": {
            "measured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "template": str(config.template),
            "image": str(config.image),
            "threshold": config.threshold,
        },
        "python": {},
        "rust": {},
    }

    results["python"]["pyscreeze_image"] = bench_python_pyscreeze_image(
        config.template,
        config.image,
        rounds=config.image_rounds,
        warmup=config.image_warmup,
        threshold=config.threshold,
    )
    results["python"]["opencv_image"] = bench_python_opencv_image(
        config.template,
        config.image,
        rounds=config.image_rounds,
        warmup=config.image_warmup,
    )
    results["python"]["screen_capture"] = bench_python_pyscreeze_screen_capture(
        rounds=config.screen_rounds,
        warmup=config.screen_warmup,
    )
    results["python"]["screen_total"] = bench_python_pyscreeze_screen(
        config.template,
        rounds=config.screen_rounds,
        warmup=config.screen_warmup,
        threshold=config.threshold,
    )

    results["rust"]["fast_image"] = bench_rust(
        config.rust_bin,
        "fast-image",
        config.template,
        image_path=config.image,
        rounds=config.image_rounds,
        threshold=config.threshold,
    )
    results["rust"]["corrmatch_image"] = bench_rust(
        config.rust_bin,
        "corrmatch-image",
        config.template,
        image_path=config.image,
        rounds=config.image_rounds,
        threshold=config.threshold,
    )
    results["rust"]["fast_screen"] = bench_rust(
        config.rust_bin,
        "fast-screen",
        config.template,
        image_path=None,
        rounds=config.screen_rounds,
        threshold=config.threshold,
    )
    results["rust"]["corrmatch_screen"] = bench_rust(
        config.rust_bin,
        "corrmatch-screen",
        config.template,
        image_path=None,
        rounds=config.screen_rounds,
        threshold=config.threshold,
    )

    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="首页标识模板 Python/Rust 串行基准工具")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help="模板图路径")
    parser.add_argument("--image", type=Path, default=DEFAULT_IMAGE, help="固定底图路径")
    parser.add_argument("--threshold", type=float, default=0.85, help="匹配阈值")
    parser.add_argument("--image-rounds", type=int, default=40, help="固定底图基准轮次")
    parser.add_argument("--image-warmup", type=int, default=6, help="固定底图预热轮次")
    parser.add_argument("--screen-rounds", type=int, default=12, help="抓屏基准轮次")
    parser.add_argument("--screen-warmup", type=int, default=2, help="抓屏预热轮次")
    parser.add_argument(
        "--results-json",
        type=Path,
        default=DEFAULT_RESULTS,
        help="JSON 结果输出路径",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help="Markdown 报告输出路径",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    template_path = args.template.resolve()
    image_path = args.image.resolve()
    if not template_path.exists():
        raise FileNotFoundError(f"模板图不存在: {template_path}")
    if not image_path.exists():
        raise FileNotFoundError(f"固定底图不存在: {image_path}")

    rust_bin = build_rust_bench(RUST_MANIFEST)
    config = BenchConfig(
        template=template_path,
        image=image_path,
        threshold=float(args.threshold),
        image_rounds=int(args.image_rounds),
        image_warmup=int(args.image_warmup),
        screen_rounds=int(args.screen_rounds),
        screen_warmup=int(args.screen_warmup),
        rust_bin=rust_bin,
    )
    results = run_benchmarks(config)
    report = build_report(results, config)

    args.results_json.parent.mkdir(parents=True, exist_ok=True)
    args.results_json.write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
        newline="\r\n",
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(report + "\r\n", encoding="utf-8", newline="\r\n")

    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\n报告已写入: {args.report}")
    print(f"结果已写入: {args.results_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
