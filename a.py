"""
预处理并保存每一步效果图（含参数注释与耗时）。

使用方法（示例）:
  uv run python a.py --image images/price_area_roi.png

可调参数请查看 `--help`。
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import numpy as np

try:  # OpenCV 是必需的
    import cv2
except Exception as exc:  # pragma: no cover - 运行时错误提示
    raise SystemExit("需要依赖: opencv-python-headless 或 opencv-python") from exc

# Tesseract 可选，默认不强制使用
try:  # pragma: no cover
    import pytesseract  # type: ignore
except Exception:  # pragma: no cover
    pytesseract = None  # noqa: N816


@dataclass
class OCRPreprocessParams:
    """可调参数集合。若通过命令行传参，会覆盖默认值。"""

    # 输入/输出
    image: str = "images/price_area_roi.png"
    outdir: str | None = None
    overlay: bool = False  # 是否在图片上叠加说明
    overlay_params: bool = False  # 是否叠加参数（默认不叠加）

    # 尺度与锐化
    scale: float = 3.0
    enable_unsharp: bool = False
    unsharp_sigma: float = 1.0
    unsharp_amount: float = 1.0

    # 对比增强（CLAHE）
    clahe_clip_limit: float = 2.0
    clahe_tile_grid: int = 8

    # 形态学/阈值
    tophat_ksize: int = 15
    hline_len: int = 60
    adaptive_block_size: int = 31  # 必须为奇数
    adaptive_C: int = 2
    invert: bool = True
    close_ksize: int = 3
    min_area: int = 0  # >0 时会去除更小连通域

    # OCR（可选）
    do_ocr: bool = False
    ocr_psm: int = 6
    ocr_whitelist: str = "0123456789K,"
    extract: bool = False  # 从整图按行提取 price/number
    row_count: int = 5
    row_min_sep_ratio: float = 0.08  # 行间最小间距相对高度
    left_right_split: float = 0.4  # 小于该比例视为左侧价格

    def as_readable_lines(self) -> List[str]:
        """将关键参数转为适合叠加绘制的注释行。"""
        return [
            f"scale={self.scale:.2f}",
            (
                f"unsharp(sigma={self.unsharp_sigma},amount={self.unsharp_amount})"
                if self.enable_unsharp
                else "unsharp=off"
            ),
            f"CLAHE(clip={self.clahe_clip_limit},tile={self.clahe_tile_grid})",
            f"tophat={self.tophat_ksize}x{self.tophat_ksize}",
            f"hline_len={self.hline_len}",
            f"adapt(block={self.adaptive_block_size},C={self.adaptive_C})",
            f"close={self.close_ksize}",
            f"invert={'1' if self.invert else '0'}",
            f"min_area={self.min_area}",
        ]


def _ensure_odd(n: int) -> int:
    return n if n % 2 == 1 else max(1, n - 1)


def _to_bgr(img: np.ndarray) -> np.ndarray:
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    return img


def _annotate(img: np.ndarray, lines: Iterable[str]) -> np.ndarray:
    """在图像左上角叠加注释文本与半透明背景。"""
    bgr = _to_bgr(img).copy()
    lines = [str(x) for x in lines]
    if not lines:
        return bgr
    # 文本排版
    margin, pad, lh = 6, 8, 18
    font = cv2.FONT_HERSHEY_SIMPLEX
    # 计算背景框
    maxw = 0
    for ln in lines:
        ((w, _h), _baseline) = cv2.getTextSize(ln, font, 0.5, 1)
        maxw = max(maxw, w)
    box_w = maxw + pad * 2
    box_h = lh * len(lines) + pad * 2
    overlay = bgr.copy()
    cv2.rectangle(overlay, (margin, margin), (margin + box_w, margin + box_h), (0, 0, 0), -1)
    bgr = cv2.addWeighted(overlay, 0.45, bgr, 0.55, 0)
    # 逐行写字（白字黑描边）
    x0, y0 = margin + pad, margin + pad + 12
    for i, ln in enumerate(lines):
        y = y0 + i * lh
        cv2.putText(bgr, ln, (x0, y), font, 0.5, (0, 0, 0), 2, cv2.LINE_AA)
        cv2.putText(bgr, ln, (x0, y), font, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
    return bgr


def _save_step(
    out_dir: Path,
    step_idx: int,
    name: str,
    img: np.ndarray,
    params: OCRPreprocessParams,
    elapsed_ms: float,
    extra_lines: Iterable[str] | None = None,
) -> Path:
    """保存步骤图像；如启用 overlay，则叠加简要说明。"""
    if params.overlay:
        lines = [f"{step_idx:02d}. {name}", f"time={elapsed_ms:.1f} ms"]
        if params.overlay_params:
            lines += params.as_readable_lines()
        if extra_lines:
            lines += list(extra_lines)
        out_img = _annotate(img, lines)
    else:
        out_img = img
    out_path = out_dir / f"{step_idx:02d}_{name}.png"
    cv2.imwrite(str(out_path), out_img)
    return out_path


def _timer() -> Tuple[float, callable]:
    start = time.perf_counter()

    def end() -> float:
        return (time.perf_counter() - start) * 1000.0

    return start, end


def _smooth_1d(arr: np.ndarray, k: int) -> np.ndarray:
    k = max(1, int(k))
    if k % 2 == 0:
        k += 1
    kernel = np.ones(k, dtype=np.float32) / k
    return np.convolve(arr.astype(np.float32), kernel, mode="same")


def _find_rows_from_lines(lines_img: np.ndarray, expected: int, min_sep: int) -> List[int]:
    """在 `lines` 图上通过投影与NMS寻找水平行中心y。"""
    if lines_img.ndim == 3:
        lines_img = cv2.cvtColor(lines_img, cv2.COLOR_BGR2GRAY)
    proj = lines_img.mean(axis=1)
    proj = _smooth_1d(proj, max(5, min_sep // 6))
    # 贪心选峰 + NMS
    ys: List[int] = []
    used = np.zeros_like(proj, dtype=bool)
    for _ in range(max(1, expected or 10)):
        # 在未屏蔽区域找最大
        masked = np.where(~used, proj, -1)
        idx = int(np.argmax(masked))
        if masked[idx] <= 0:
            break
        ys.append(idx)
        lo = max(0, idx - min_sep // 2)
        hi = min(len(proj), idx + min_sep // 2 + 1)
        used[lo:hi] = True
    ys = sorted(ys)
    # 如果期望行数为0，直接返回找到的全部；否则取最靠近均匀分布的前 expected 个
    if expected and len(ys) > expected:
        # 简单下采样：按值从大到小再取前 expected
        candidates = sorted(ys, key=lambda y: proj[y], reverse=True)[:expected]
        ys = sorted(candidates)
    return ys


def _extract_rows_from_image(
    bin_for_ocr: np.ndarray,
    size_wh: Tuple[int, int],
    lines_img: np.ndarray,
    params: OCRPreprocessParams,
) -> List[Dict[str, str]]:
    """使用 Tesseract 的 data 输出结合行 y 值对文本进行配对。"""
    assert pytesseract is not None

    h, w = bin_for_ocr.shape[:2]
    min_sep = max(10, int(params.row_min_sep_ratio * h))
    row_ys = _find_rows_from_lines(lines_img, params.row_count, min_sep)
    if not row_ys:
        # 退化：等间距猜测 expected 行
        rc = params.row_count or 5
        step = h // (rc + 1)
        row_ys = [step * (i + 1) for i in range(rc)]

    band = max(10, int(min_sep * 0.45))
    split_x = int(params.left_right_split * w)

    cfg = f"--oem 3 --psm {params.ocr_psm} -c tessedit_char_whitelist={params.ocr_whitelist}"
    try:
        from pytesseract import Output  # type: ignore
        data = pytesseract.image_to_data(bin_for_ocr, config=cfg, lang="eng", output_type=Output.DICT)  # type: ignore
    except Exception:
        # 回退到简单字符串（效果较差）
        text = pytesseract.image_to_string(bin_for_ocr, config=cfg, lang="eng")  # type: ignore
        (Path(".") / "tesseract_raw.txt").write_text(text, encoding="utf-8")
        return []

    import re

    tokens: List[Dict[str, object]] = []
    n = len(data.get("text", []))
    for i in range(n):
        txt = (data["text"][i] or "").strip()
        conf = float(data.get("conf", ["-1"][0])[i]) if "conf" in data else 0.0
        if not txt or conf < 40:  # 过滤低置信度/空白
            continue
        if not re.fullmatch(r"[0-9K,]+", txt, re.IGNORECASE):
            # 只保留数字与K、逗号
            continue
        l, t, wi, hi = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        xc, yc = l + wi / 2.0, t + hi / 2.0
        tokens.append({"text": txt, "conf": conf, "xc": xc, "yc": yc, "box": (l, t, wi, hi)})

    # 按行聚合
    rows: List[Dict[str, List[Dict[str, object]]]] = []
    for y in row_ys:
        rows.append({"y": y, "left": [], "right": []})

    def nearest_row(yc: float) -> int | None:
        idx = int(np.argmin([abs(yc - y) for y in row_ys]))
        if abs(yc - row_ys[idx]) <= band:
            return idx
        return None

    for tk in tokens:
        idx = nearest_row(float(tk["yc"]))
        if idx is None:
            continue
        if float(tk["xc"]) < split_x:
            rows[idx]["left"].append(tk)
        else:
            rows[idx]["right"].append(tk)

    def join_tokens(tks: List[Dict[str, object]]) -> str:
        tks = sorted(tks, key=lambda z: z["xc"])  # left-to-right
        s = "".join(str(z["text"]) for z in tks)
        s = s.upper()
        s = re.sub(r"[^0-9K,]", "", s)
        s = re.sub(r",{2,}", ",", s)
        # 规范形态：3,271K
        s = s.replace("KK", "K")
        return s

    def clean_number(s: str) -> str:
        return re.sub(r"[^0-9]", "", s)

    out: List[Dict[str, str]] = []
    for r in rows:
        price = join_tokens(r["left"]) if r["left"] else ""
        number = join_tokens(r["right"]) if r["right"] else ""
        price = price.replace("K", "K")
        number = clean_number(number)
        if price or number:
            out.append({"price": price, "number": number})

    # 只返回非空，并按从上到下顺序
    out = [x for x in out if x.get("price") or x.get("number")]
    return out


def run_pipeline(params: OCRPreprocessParams) -> None:
    """执行整套预处理流程，导出每一步的效果图与计时。"""
    src_path = Path(params.image)
    if not src_path.exists():
        raise SystemExit(f"找不到输入图像: {src_path}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = Path(params.outdir) if params.outdir else Path("images") / f"ocr_steps_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 保存参数
    (out_dir / "params.json").write_text(json.dumps(asdict(params), ensure_ascii=False, indent=2), encoding="utf-8")

    steps_log: List[Dict[str, object]] = []

    # 0) 读取
    t0, end = _timer()
    bgr = cv2.imread(str(src_path), cv2.IMREAD_COLOR)
    elapsed = end()
    p = _save_step(out_dir, 0, "load", bgr, params, elapsed, [f"src={src_path}"])
    steps_log.append({"step": 0, "name": "load", "ms": elapsed, "path": str(p)})

    # 1) 放大
    t0, end = _timer()
    scaled = cv2.resize(bgr, None, fx=params.scale, fy=params.scale, interpolation=cv2.INTER_CUBIC)
    elapsed = end()
    p = _save_step(out_dir, 1, "resize", scaled, params, elapsed)
    steps_log.append({"step": 1, "name": "resize", "ms": elapsed, "path": str(p)})

    # 2) 反锐化（可选）
    sharpened = scaled
    if params.enable_unsharp:
        t0, end = _timer()
        blur = cv2.GaussianBlur(scaled, (0, 0), params.unsharp_sigma)
        sharpened = cv2.addWeighted(scaled, 1 + params.unsharp_amount, blur, -params.unsharp_amount, 0)
        elapsed = end()
        p = _save_step(out_dir, 2, "unsharp", sharpened, params, elapsed)
        steps_log.append({"step": 2, "name": "unsharp", "ms": elapsed, "path": str(p)})
    else:
        # 若未启用，将步骤编号继续推进但不另存
        pass

    # 3) CLAHE（L 通道）
    t0, end = _timer()
    lab = cv2.cvtColor(sharpened, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=float(params.clahe_clip_limit), tileGridSize=(params.clahe_tile_grid, params.clahe_tile_grid))
    cl = clahe.apply(l)
    clahe_bgr = cv2.cvtColor(cv2.merge([cl, a, b]), cv2.COLOR_LAB2BGR)
    elapsed = end()
    p = _save_step(out_dir, 3, "clahe", clahe_bgr, params, elapsed)
    steps_log.append({"step": 3, "name": "clahe", "ms": elapsed, "path": str(p)})

    # 4) 灰度
    t0, end = _timer()
    gray = cv2.cvtColor(clahe_bgr, cv2.COLOR_BGR2GRAY)
    elapsed = end()
    p = _save_step(out_dir, 4, "gray", gray, params, elapsed)
    steps_log.append({"step": 4, "name": "gray", "ms": elapsed, "path": str(p)})

    # 5) 顶帽（突出亮字符）
    t0, end = _timer()
    k = params.tophat_ksize
    tophat = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    elapsed = end()
    p = _save_step(out_dir, 5, "tophat", tophat, params, elapsed)
    steps_log.append({"step": 5, "name": "tophat", "ms": elapsed, "path": str(p)})

    # 6) 提取/去除水平线
    t0, end = _timer()
    hker = cv2.getStructuringElement(cv2.MORPH_RECT, (params.hline_len, 1))
    lines = cv2.morphologyEx(tophat, cv2.MORPH_OPEN, hker)
    no_lines = cv2.subtract(tophat, lines)
    elapsed = end()
    p1 = _save_step(out_dir, 6, "lines", lines, params, elapsed)
    p2 = _save_step(out_dir, 7, "nolines", no_lines, params, 0.0)
    steps_log.append({"step": 6, "name": "lines", "ms": elapsed, "path": str(p1)})
    steps_log.append({"step": 7, "name": "nolines", "ms": 0.0, "path": str(p2)})

    # 7) 自适应阈值
    t0, end = _timer()
    blk = _ensure_odd(params.adaptive_block_size)
    binimg = cv2.adaptiveThreshold(
        no_lines,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blk,
        params.adaptive_C,
    )
    elapsed = end()
    p = _save_step(out_dir, 8, "adaptive", binimg, params, elapsed, [f"block={blk}"])
    steps_log.append({"step": 8, "name": "adaptive", "ms": elapsed, "path": str(p)})

    # 8) 取反（如需要）
    if params.invert:
        t0, end = _timer()
        inv = 255 - binimg
        elapsed = end()
        p = _save_step(out_dir, 9, "invert", inv, params, elapsed)
        steps_log.append({"step": 9, "name": "invert", "ms": elapsed, "path": str(p)})
    else:
        inv = binimg

    # 9) 闭运算（连接断裂笔画）
    t0, end = _timer()
    ck = params.close_ksize
    closed = cv2.morphologyEx(inv, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (ck, ck)), 1)
    elapsed = end()
    p = _save_step(out_dir, 10, "close", closed, params, elapsed)
    steps_log.append({"step": 10, "name": "close", "ms": elapsed, "path": str(p)})

    # 10) 小连通域去噪（可选）
    denoised = closed
    if params.min_area and params.min_area > 0:
        t0, end = _timer()
        comp_src = 255 - closed if params.invert else closed  # 连通域检测需前景为白
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(comp_src, connectivity=8)
        mask = np.zeros_like(comp_src)
        for i in range(1, num_labels):
            if stats[i, cv2.CC_STAT_AREA] >= params.min_area:
                mask[labels == i] = 255
        # 将方向恢复为与 closed 一致：invert=True 时原图为黑字白底
        denoised = 255 - mask if params.invert else mask
        elapsed = end()
        p = _save_step(out_dir, 11, "denoise", denoised, params, elapsed)
        steps_log.append({"step": 11, "name": "denoise", "ms": elapsed, "path": str(p)})

    # 11) 可选 OCR
    if params.do_ocr:
        if pytesseract is None:
            (out_dir / "ocr.txt").write_text("pytesseract 未安装，跳过 OCR。\n", encoding="utf-8")
        else:
            t0, end = _timer()
            cfg = f"--oem 3 --psm {params.ocr_psm} -c tessedit_char_whitelist={params.ocr_whitelist}"
            text = pytesseract.image_to_string(denoised, config=cfg, lang="eng")  # type: ignore
            elapsed = end()
            (out_dir / "ocr.txt").write_text(text, encoding="utf-8")
            steps_log.append({"step": 12, "name": "ocr", "ms": elapsed, "path": str(out_dir / 'ocr.txt')})

    # 12) 提取结构化结果（price/number）
    if params.extract and pytesseract is not None:
        t0, end = _timer()
        result = _extract_rows_from_image(
            denoised,
            (bgr.shape[1], bgr.shape[0]),
            lines,
            params,
        )
        elapsed = end()
        (out_dir / "extracted.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        steps_log.append({"step": 13, "name": "extract", "ms": elapsed, "path": str(out_dir / 'extracted.json')})

    # 导出步骤日志
    (out_dir / "steps.json").write_text(json.dumps(steps_log, ensure_ascii=False, indent=2), encoding="utf-8")
    with (out_dir / "steps.csv").open("w", encoding="utf-8") as f:
        f.write("step,name,ms,path\n")
        for s in steps_log:
            f.write(f"{s['step']},{s['name']},{s['ms']:.1f},{s['path']}\n")

    print(f"已输出到: {out_dir}")


def build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="保存 OCR 预处理每一步效果图与耗时")
    p.add_argument("--image", type=str, default=OCRPreprocessParams.image, help="输入图片路径")
    p.add_argument("--outdir", type=str, default=None, help="输出目录（默认 images/ocr_steps_时间戳）")
    p.add_argument("--overlay", action="store_true", help="在输出图像上叠加步骤与耗时（默认关闭）")
    p.add_argument("--overlay-params", action="store_true", help="叠加参数文本（默认关闭）")
    p.add_argument("--scale", type=float, default=OCRPreprocessParams.scale, help="缩放倍数")
    p.add_argument("--enable-unsharp", action="store_true", help="启用反锐化增强")
    p.add_argument("--unsharp-sigma", type=float, default=OCRPreprocessParams.unsharp_sigma, help="反锐化高斯σ")
    p.add_argument("--unsharp-amount", type=float, default=OCRPreprocessParams.unsharp_amount, help="反锐化强度")
    p.add_argument("--clahe-clip-limit", type=float, default=OCRPreprocessParams.clahe_clip_limit, help="CLAHE clip limit")
    p.add_argument("--clahe-tile-grid", type=int, default=OCRPreprocessParams.clahe_tile_grid, help="CLAHE tile grid 大小")
    p.add_argument("--tophat-ksize", type=int, default=OCRPreprocessParams.tophat_ksize, help="顶帽核大小（正方形）")
    p.add_argument("--hline-len", type=int, default=OCRPreprocessParams.hline_len, help="水平线结构元长度")
    p.add_argument("--adaptive-block-size", type=int, default=OCRPreprocessParams.adaptive_block_size, help="自适应阈值窗口（奇数）")
    p.add_argument("--adaptive-C", type=int, default=OCRPreprocessParams.adaptive_C, help="自适应阈值常数C")
    p.add_argument("--invert", action="store_true", help="二值图取反（默认开启）")
    p.add_argument("--no-invert", dest="invert", action="store_false", help="禁用取反")
    p.set_defaults(invert=OCRPreprocessParams.invert)
    p.add_argument("--close-ksize", type=int, default=OCRPreprocessParams.close_ksize, help="闭运算核大小")
    p.add_argument("--min-area", type=int, default=OCRPreprocessParams.min_area, help="去噪最小连通域面积（0 关闭）")
    p.add_argument("--do-ocr", action="store_true", help="执行 OCR 并输出文本")
    p.add_argument("--ocr-psm", type=int, default=OCRPreprocessParams.ocr_psm, help="Tesseract --psm")
    p.add_argument("--ocr-whitelist", type=str, default=OCRPreprocessParams.ocr_whitelist, help="Tesseract 白名单")
    p.add_argument("--extract", action="store_true", help="提取结构化结果（price/number）")
    p.add_argument("--row-count", type=int, default=OCRPreprocessParams.row_count, help="预期行数；0 表示自动")
    p.add_argument("--row-min-sep-ratio", type=float, default=OCRPreprocessParams.row_min_sep_ratio, help="相对高度的最小行间距，用于非极大值抑制")
    p.add_argument("--split", dest="left_right_split", type=float, default=OCRPreprocessParams.left_right_split, help="左右划分的相对 x（0-1）")
    return p


def main(argv: List[str] | None = None) -> None:
    args = vars(build_argparser().parse_args(argv))
    params = OCRPreprocessParams(**args)
    # 适配奇数窗口
    params.adaptive_block_size = _ensure_odd(params.adaptive_block_size)
    run_pipeline(params)


if __name__ == "__main__":
    main()
