"""
多商品抢购运行器（与主任务隔离）。

特性：
- 刷新逻辑：点击“最近购买”→ 点击“我的收藏”（因默认进入收藏时收藏标签为选中态，模板不匹配）。
- 首次定位后缓存卡片坐标；后续直接按缓存坐标截图，无需重复模板匹配。
- 批量 OCR：汇总所有 ROI 截图后并发请求 Umi-OCR，降低整体耗时。
- 购买逻辑：与 task_runner 的按钮模板与处理风格保持一致（点击购买、等待 buy_ok/buy_fail、关闭详情）。

依赖：
- 使用 task_runner.ScreenOps 进行 locate/click/screenshot。
- 使用 task_runner._parse_price_text 清洗价格。

任务数据（示例）：
items: [
  {
    "id": "uuid",
    "name": "物品名",
    "template": "images/goods/.../middle.png",  # 中间区域模板（卡片中间图）
    "price_threshold": 12345
  },
  ...
]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from pathlib import Path

import base64
import io
import os
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

try:
    from PIL import Image  # type: ignore
    from PIL import ImageDraw  # type: ignore
    from PIL import ImageFont  # type: ignore
    from PIL import ImageTk  # type: ignore
except Exception:
    Image = None  # type: ignore
    ImageDraw = None  # type: ignore
    ImageFont = None  # type: ignore
    ImageTk = None  # type: ignore

from super_buyer.core.common import parse_price_text as _parse_price_text
from super_buyer.services.font_loader import draw_text, pil_font, tk_font
from super_buyer.services.ocr import recognize_numbers
from super_buyer.services.screen_ops import ScreenOps

# 卡片与 ROI 固定参数（与“测试”页逻辑一致）
CARD_W = 165
CART_H = 212  # typo preserved? keep name consistent
CARD_H = 212
TOP_H = 20
BTM_H = 30
MARG_LR = 30
MARG_TB = 20


@dataclass
class SnipeItem:
    id: str
    name: str
    template: str  # 中间区域模板（用于首次定位）
    price_threshold: int = 0  # 兼容旧字段
    # 新字段：统一的价格与浮动、模式与数量/目标
    price: int = 0
    premium_pct: float = 0.0
    purchase_mode: str = "normal"  # normal | restock
    target_total: int = 0
    purchased: int = 0
    item_id: str = ""
    big_category: str = ""
    image_path: str = ""
    enabled: bool = True


class MultiSnipeRunner:
    """多商品抢购运行器。

    - cfg: 完整配置 dict（含 templates/umi_ocr 等）。
    - items: 抢购项列表（SnipeItem 或兼容 dict）。
    - on_log: 日志回调。
    """

    def __init__(self, cfg: Dict[str, Any], items: List[Dict[str, Any]] | List[SnipeItem], on_log: Callable[[str], None]) -> None:
        self.cfg = cfg
        self.on_log = on_log
        self.items: List[SnipeItem] = []
        for it in items:
            if isinstance(it, SnipeItem):
                self.items.append(it)
            elif isinstance(it, dict):
                self.items.append(
                    SnipeItem(
                        id=str(it.get("id", "")) or os.urandom(4).hex(),
                        name=str(it.get("name", "")),
                        template=str(it.get("template", it.get("image_path", ""))),
                        price_threshold=int(it.get("price_threshold", it.get("price", 0)) or 0),
                        price=int(it.get("price", 0) or 0),
                        premium_pct=float(it.get("premium_pct", 0.0) or 0.0),
                        purchase_mode=str(it.get("purchase_mode", it.get("mode", "normal")) or "normal").lower(),
                        target_total=int(it.get("target_total", it.get("buy_qty", 0)) or 0),
                        purchased=int(it.get("purchased", 0) or 0),
                        item_id=str(it.get("item_id", "") or ""),
                        big_category=str(it.get("big_category", "") or ""),
                        image_path=str(it.get("image_path", "") or ""),
                        enabled=bool(it.get("enabled", True)),
                    )
                )
        # 运行控制
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.clear()

        # 解析相对路径为 data 根（由 paths.output_dir 的上级）下的绝对路径
        def _resolve_rel(p: str) -> str:
            p = (p or "").strip()
            if not p:
                return ""
            try:
                pp = os.path.abspath(p) if os.path.isabs(p) else None
            except Exception:
                pp = None
            if pp:
                return pp
            try:
                _paths = (self.cfg.get("paths") or {}) if isinstance(self.cfg.get("paths"), dict) else {}
            except Exception:
                _paths = {}
            out_root = str(_paths.get("output_dir", "output") or "output")
            base = Path(out_root).resolve().parent  # data/
            try:
                norm = p.replace("\\", "/")
            except Exception:
                norm = p
            return str((base / norm).resolve())

        for i, it in enumerate(self.items):
            try:
                if getattr(it, "image_path", None):
                    it.image_path = _resolve_rel(str(it.image_path))
            except Exception:
                pass
            try:
                if getattr(it, "template", None):
                    it.template = _resolve_rel(str(it.template))
            except Exception:
                pass

        # 坐标缓存：item.id -> card_rect (x,y,w,h)
        self._card_cache: Dict[str, Tuple[int, int, int, int]] = {}
        # 中间模板框缓存：item.id -> mid_rect (x,y,w,h)，用于更稳的点击进入详情
        self._mid_cache: Dict[str, Tuple[int, int, int, int]] = {}

        # Debug 配置（可视化蒙版 + 额外节流）
        dbg = (self.cfg.get("debug", {}) or {})
        try:
            self._debug_enabled = bool(dbg.get("enabled", False))
        except Exception:
            self._debug_enabled = False
        try:
            self._debug_overlay_sec = float(dbg.get("overlay_sec", 5.0) or 5.0)
        except Exception:
            self._debug_overlay_sec = 5.0
        try:
            self._debug_step_sleep = float(dbg.get("step_sleep", 0.0) or 0.0)
        except Exception:
            self._debug_step_sleep = 0.0
        # 保存叠加截图（新配置）
        try:
            self._debug_save_overlay_images = bool(dbg.get("save_overlay_images", False))
        except Exception:
            self._debug_save_overlay_images = False
        try:
            self._debug_overlay_dir = str(
                dbg.get("overlay_dir", os.path.join("images", "debug", "可视化调试"))
            )
        except Exception:
            # 兼容旧字段 save_dir
            self._debug_overlay_dir = str(dbg.get("save_dir", os.path.join("images", "debug", "可视化调试")))
        # 运行轮次与叠加序号（便于命名/分组）
        self._loop_no: int = 0
        self._loop_dir: Optional[str] = None
        self._overlay_seq: int = 0
        # 复用型蒙版窗口（降低创建销毁开销）
        self._ov_top = None
        self._ov_canvas = None

        # ScreenOps：若开启调试，则把 step_delay 抬高（优先使用 debug.step_sleep）
        try:
            base_delay = 0.02
            if self._debug_enabled:
                # clamp to [0.02, 0.2]
                dd = float(self._debug_step_sleep or 0.0)
                if dd < base_delay:
                    dd = base_delay
                if dd > 0.2:
                    dd = 0.2
                base_delay = dd
            self.screen = ScreenOps(cfg, step_delay=float(base_delay))
            # 将调试叠加图目录规范为 output_dir/debug，避免写入 images/
            try:
                _paths = (self.cfg.get("paths") or {})
            except Exception:
                _paths = {}
            _out_root = str((_paths.get("output_dir") if isinstance(_paths, dict) else None) or "output")
            try:
                if not os.path.isabs(self._debug_overlay_dir):
                    p_norm = str(self._debug_overlay_dir or "").replace("\\", "/")
                    if p_norm.startswith("images/debug") or p_norm.startswith("images/\\debug"):
                        self._debug_overlay_dir = os.path.join(_out_root, "debug", os.path.basename(self._debug_overlay_dir))
            except Exception:
                pass
        except Exception:
            self.screen = ScreenOps(cfg, step_delay=0.02)

        # 运行时调优（等待/并发/CPU 降压），通过 cfg['multi_snipe_tuning'] 可覆盖
        # - probe_step_sec: 收藏就绪探测的单次 sleep（默认 0.06）
        # - post_click_wait_sec: 点击标签后的额外等待（非 debug，默认 0.2）
        # - roi_pre_capture_wait_sec: 截 ROI 前的短等待（非 debug，默认 0.05）
        # - ocr_max_workers: OCR 并发度（默认 4，原为 6 以降低 CPU 压力）
        # - buy_result_timeout_sec: 等待购买结果的时长（默认 0.8s）
        # - relocate_after_fail: 同一物品连续失败 N 次后清空缓存强制重定位（默认 3）
        tuning = (self.cfg.get("multi_snipe_tuning", {}) or {})
        try:
            self._probe_step_sec = float(tuning.get("probe_step_sec", 0.06) or 0.06)
        except Exception:
            self._probe_step_sec = 0.06
        try:
            self._post_click_wait_sec = float(tuning.get("post_click_wait_sec", 0.2) or 0.2)
        except Exception:
            self._post_click_wait_sec = 0.2
        try:
            self._roi_pre_capture_wait_sec = float(tuning.get("roi_pre_capture_wait_sec", 0.05) or 0.05)
        except Exception:
            self._roi_pre_capture_wait_sec = 0.05
        try:
            self._ocr_max_workers = int(tuning.get("ocr_max_workers", 4) or 4)
        except Exception:
            self._ocr_max_workers = 4
        try:
            self._buy_result_timeout_sec = float(tuning.get("buy_result_timeout_sec", 0.8) or 0.8)
        except Exception:
            self._buy_result_timeout_sec = 0.8
        try:
            self._relocate_after_fail = int(tuning.get("relocate_after_fail", 3) or 3)
        except Exception:
            self._relocate_after_fail = 3
        # 处罚检测相关参数
        try:
            self._ocr_miss_threshold = int(tuning.get("ocr_miss_penalty_threshold", 10) or 10)
        except Exception:
            self._ocr_miss_threshold = 10
        try:
            self._penalty_confirm_delay_sec = float(tuning.get("penalty_confirm_delay_sec", 5.0) or 5.0)
        except Exception:
            self._penalty_confirm_delay_sec = 5.0
        try:
            self._penalty_wait_after_confirm_sec = float(tuning.get("penalty_wait_sec", 180.0) or 180.0)
        except Exception:
            self._penalty_wait_after_confirm_sec = 180.0

        # 连续失败计数器：item.id -> count
        self._fail_counts: Dict[str, int] = {}
        # OCR 连续未识别计数器（整轮计数）
        self._ocr_miss_streak: int = 0
        # 最近一次扫描存在有效价格识别的时间戳（用于抑制处罚误报）
        self._last_ocr_ok_ts: float = 0.0

    # ---------- Utils ----------
    def _log(self, s: str) -> None:
        try:
            self.on_log(s)
        except Exception:
            pass

    def _log_debug(self, s: str) -> None:
        self._log(f"【DEBUG】{s}")

    def _log_info(self, s: str) -> None:
        self._log(f"【INFO】{s}")

    def _log_error(self, s: str) -> None:
        self._log(f"【ERROR】{s}")

    def _tpl(self, key: str) -> Tuple[str, float]:
        """读取模板配置并解析相对路径为可用的绝对路径。

        优先级：
        1) 已是绝对路径（POSIX）→ 直接返回；
        2) Windows 盘符形式（如 C:\\path）→ 原样返回；
        3) 相对路径：按以下 base 依次拼接并选择第一个存在的路径：
           - data 根（paths.output_dir 的上级）
           - cwd/data
           - cwd
        若都不存在，返回 data 根拼接的路径（用于后续 exists 判断与日志）。
        """
        t = (self.cfg.get("templates", {}) or {}).get(key) or {}
        raw = str(t.get("path", "")).strip()
        conf = float(t.get("confidence", 0.85) or 0.85)
        if not raw:
            return "", conf
        # 已是 POSIX 绝对路径
        try:
            if os.path.isabs(raw):
                return os.path.abspath(raw), conf
        except Exception:
            pass
        # Windows 盘符（在 POSIX 下 os.path.isabs 可能为 False）
        try:
            import re as _re
            if _re.match(r"^[a-zA-Z]:[\\/]", raw):
                return raw, conf
        except Exception:
            pass
        # 相对路径：统一分隔符
        try:
            norm = raw.replace("\\", "/")
        except Exception:
            norm = raw
        # base1: data 根（由 output_dir 推导）
        try:
            _paths = (self.cfg.get("paths") or {}) if isinstance(self.cfg.get("paths"), dict) else {}
        except Exception:
            _paths = {}
        out_root = str(_paths.get("output_dir", "output") or "output")
        bases = []
        try:
            bases.append(Path(out_root).resolve().parent)
        except Exception:
            pass
        # base2: cwd/data
        try:
            bases.append((Path.cwd() / "data").resolve())
        except Exception:
            pass
        # base3: cwd
        try:
            bases.append(Path.cwd().resolve())
        except Exception:
            pass
        for b in bases:
            try:
                cand = (b / norm).resolve()
                if cand.exists():
                    return str(cand), conf
            except Exception:
                continue
        # 回退：返回 data 根拼接的路径（即便不存在也保留可读路径）
        try:
            return str((bases[0] / norm).resolve()), conf if bases else (norm, conf)
        except Exception:
            return norm, conf

    # ---------- Debug helpers ----------
    def _debug_active(self) -> bool:
        return bool(getattr(self, "_debug_enabled", False))

    def _debug_pause(self, label: str = "") -> None:
        if not self._debug_active():
            return
        try:
            d = float(getattr(self, "_debug_step_sleep", 0.0) or 0.0)
        except Exception:
            d = 0.0
        if d > 0:
            time.sleep(d)

    def _debug_screenshot_full(self):
        try:
            return self.screen._pg.screenshot()
        except Exception:
            return None

    @staticmethod
    def _to_xyxy(rect: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        x, y, w, h = rect
        return int(x), int(y), int(x + w), int(y + h)

    def _debug_build_annotated(self,
                               base_img,
                               overlays: List[Dict[str, Any]],
                               *,
                               stage: str = "",
                               template_path: Optional[str] = None) -> Optional[Any]:
        """在整屏截图基础上绘制半透明蒙版与 ROI 结构。

        overlays: [{"rect": (l,t,w,h), "label": str, "fill": (r,g,b,alpha), "outline": (r,g,b)}]
        返回：PIL.Image 或 None
        """
        if Image is None:
            return None
        try:
            img = base_img.convert("RGBA")
        except Exception:
            return None
        W, H = img.size
        # 全屏半透明蒙版
        try:
            mask = Image.new("RGBA", (W, H), (0, 0, 0, 120))
            img = Image.alpha_composite(img, mask)
        except Exception:
            pass
        try:
            draw = ImageDraw.Draw(img)
        except Exception:
            return None
        # 绘制每个 ROI
        for ov in overlays:
            rect = ov.get("rect")
            if not rect:
                continue
            x1, y1, x2, y2 = self._to_xyxy(rect)
            fill = ov.get("fill") or (255, 255, 0, 80)
            outline = ov.get("outline") or (255, 255, 0)
            # 填充（较低透明度）
            try:
                roi_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
                roi_draw = ImageDraw.Draw(roi_layer)
                roi_draw.rectangle([x1, y1, x2, y2], fill=fill)
                img = Image.alpha_composite(img, roi_layer)
                draw = ImageDraw.Draw(img)
            except Exception:
                try:
                    draw.rectangle([x1, y1, x2, y2], outline=outline, width=2)
                except Exception:
                    pass
            # 外轮廓
            try:
                draw.rectangle([x1, y1, x2, y2], outline=outline, width=2)
            except Exception:
                pass
            # 标签
            label = str(ov.get("label") or "")
            if label:
                try:
                    tw, th = draw.textlength(label), 14
                except Exception:
                    tw, th = len(label) * 8, 14
                pad = 4
                bx1, by1 = x1 + 2, y1 + 2
                bx2, by2 = bx1 + int(tw) + pad * 2, by1 + th + pad
                try:
                    draw.rectangle([bx1, by1, bx2, by2], fill=(0, 0, 0, 160))
                except Exception:
                    pass
                # 使用中文字体绘制标签
                draw_text(draw, (bx1 + pad, by1 + pad // 2), label, fill=(255, 255, 255, 255), size=14)
        # 阶段标题
        if stage:
            try:
                t = f"调试：{stage}"
                tw, th = draw.textlength(t), 16
            except Exception:
                t, tw, th = f"调试：{stage}", len(stage) * 8 + 32, 16
            try:
                cx, top = W // 2, 20
                draw.rectangle([cx - tw // 2 - 8, top - 6, cx + tw // 2 + 8, top + th + 6], fill=(0, 0, 0, 170))
            except Exception:
                pass
            # 使用中文字体绘制标题
            draw_text(draw, (cx - tw // 2, top), t, fill=(255, 255, 255, 255), size=16)
        # 将模板原图贴在“目标ROI”旁边（右侧优先，不够则左侧）
        if template_path and os.path.exists(template_path):
            try:
                # 选择目标 ROI：优先 label 含“模板”，否则第一个
                target_rect = None
                for ov in overlays:
                    lb = str(ov.get("label") or "")
                    if "模板" in lb:
                        target_rect = ov.get("rect")
                        break
                if target_rect is None and overlays:
                    target_rect = overlays[0].get("rect")
                if target_rect:
                    rx1, ry1, rx2, ry2 = self._to_xyxy(target_rect)
                    tpl = Image.open(template_path).convert("RGBA")
                    maxw = 200
                    ratio = min(1.0, maxw / max(1, tpl.width))
                    tw, th = max(1, int(tpl.width * ratio)), max(1, int(tpl.height * ratio))
                    try:
                        tpl = tpl.resize((tw, th))
                    except Exception:
                        pass
                    # 计算相邻位置：右侧优先
                    px = rx2 + 10
                    py = ry1
                    if px + tw > W - 8:
                        px = rx1 - 10 - tw
                    if px < 8:
                        px = max(8, min(W - tw - 8, rx1))
                    py = max(8, min(H - th - 8, py))
                    # 背景与标题
                    try:
                        draw.rectangle([px - 6, py - 24, px + tw + 6, py + th + 6], fill=(0, 0, 0, 180))
                    except Exception:
                        pass
                    draw_text(draw, (px, py - 18), "模板原图", fill=(255, 255, 255, 255), size=14)
                    img.alpha_composite(tpl, dest=(px, py))
                else:
                    # 回退：仍贴左上角
                    tpl = Image.open(template_path).convert("RGBA")
                    maxw = 200
                    ratio = min(1.0, maxw / max(1, tpl.width))
                    tw, th = max(1, int(tpl.width * ratio)), max(1, int(tpl.height * ratio))
                    tpl = tpl.resize((tw, th))
                    x_off, y_off = 16, 60
                    draw.rectangle([x_off - 6, y_off - 24, x_off + tw + 6, y_off + th + 6], fill=(0, 0, 0, 180))
                    draw_text(draw, (x_off, y_off - 18), "模板原图", fill=(255, 255, 255, 255), size=14)
                    img.alpha_composite(tpl, dest=(x_off, y_off))
            except Exception:
                pass
        return img.convert("RGB")

    def _debug_show_overlay(self,
                            overlays: List[Dict[str, Any]],
                            *,
                            stage: str,
                            template_path: Optional[str] = None,
                            save_name: Optional[str] = None) -> None:
        """基于全屏蒙版的实时可视化叠加。

        - 直接在顶层半透明窗口的 Canvas 上绘制矩形与标签，保持 overlay_sec 秒。
        - 若配置开启保存，则在绘制后抓取整屏截图（含叠加）保存到按轮次分组的目录。
        - 回退：若 Tk 不可用，按旧逻辑在静态截图上绘制并保存（若启用保存），然后仅 sleep overlay_sec。
        """
        if not self._debug_active():
            return
        # 统一生成当前轮次的序号与保存名
        try:
            self._overlay_seq += 1
            seq = int(self._overlay_seq)
        except Exception:
            seq = 1
            self._overlay_seq = 1
        ts = time.strftime("%H%M%S")
        def _clean(s: str) -> str:
            s = str(s or "").strip()
            for ch in ("/", "\\", ":", "*", "?", "\"", "<", ">", "|", " "):
                s = s.replace(ch, "_")
            return s[:60] or "overlay"
        fname = save_name or f"{_clean(stage)}.png"
        fname = f"{seq:03d}_{fname}"
        delay_ms = int(max(0.0, float(self._debug_overlay_sec)) * 1000)

        # 记录一次叠加的概要日志
        self._log_debug(f"[可视化] 阶段={stage} 叠加数={len(overlays)} 持续={self._debug_overlay_sec}s 保存={( 'on' if getattr(self,'_debug_save_overlay_images', False) else 'off')} seq={seq}")
        # Tk 路径：在主线程创建/复用蒙版窗口并绘制
        try:
            import tkinter as tk  # type: ignore
            root = getattr(tk, "_default_root", None)
        except Exception:
            root = None

        if root is None:
            # 回退：无法显示蒙版，改为在静态截图上绘制；若开启保存则落盘
            base = self._debug_screenshot_full()
            if base is not None and bool(getattr(self, "_debug_save_overlay_images", False)):
                annotated = self._debug_build_annotated(base, overlays, stage=stage, template_path=template_path)
                try:
                    loop_dir = self._loop_dir or self._debug_overlay_dir
                    os.makedirs(loop_dir, exist_ok=True)
                    annotated.save(os.path.join(loop_dir, fname))
                    self._log_debug(f"[可视化] 已保存(静态) {os.path.join(loop_dir, fname)}")
                except Exception:
                    pass
            time.sleep(max(0.0, float(self._debug_overlay_sec)))
            return

        done = threading.Event()

        def _spawn():
            try:
                W = int(root.winfo_screenwidth())
                H = int(root.winfo_screenheight())
                # 创建或复用顶层窗口
                top = getattr(self, "_ov_top", None)
                cv = getattr(self, "_ov_canvas", None)
                if top is None or cv is None:
                    t = tk.Toplevel(root)
                    try:
                        t.attributes("-topmost", True)
                    except Exception:
                        pass
                    t.overrideredirect(True)
                    try:
                        t.attributes("-alpha", 0.35)
                    except Exception:
                        pass
                    try:
                        t.geometry(f"{W}x{H}+0+0")
                    except Exception:
                        pass
                    c = tk.Canvas(t, width=W, height=H, highlightthickness=0, bg="#000000")
                    try:
                        c.pack(fill=tk.BOTH, expand=True)
                    except Exception:
                        c.pack()
                    self._ov_top = t
                    self._ov_canvas = c
                    top, cv = t, c
                else:
                    try:
                        top.deiconify()
                    except Exception:
                        pass
                    try:
                        top.geometry(f"{W}x{H}+0+0")
                    except Exception:
                        pass
                    try:
                        cv.delete("all")
                    except Exception:
                        pass
                # 清空上一轮图片引用以避免泄漏
                try:
                    self._ov_img_refs = []
                except Exception:
                    self._ov_img_refs = []

                # 绘制标题（优先使用中文字体）
                try:
                    ttxt = f"调试：{stage}" if stage else ""
                    if ttxt:
                        tw = max(120, len(ttxt) * 10)
                        x1 = max(8, W // 2 - tw // 2)
                        x2 = min(W - 8, x1 + tw)
                        y1, y2 = 20, 42
                        cv.create_rectangle(x1, y1, x2, y2, fill="#000000", outline="")
                        try:
                            f = tk_font(root, 12)
                        except Exception:
                            f = None
                        if f is not None:
                            cv.create_text((x1 + x2) // 2, (y1 + y2) // 2, text=ttxt, fill="#ffffff", font=f)
                        else:
                            cv.create_text((x1 + x2) // 2, (y1 + y2) // 2, text=ttxt, fill="#ffffff")
                except Exception:
                    pass

                # 绘制每个 ROI（边框 + 标签）
                for ov in overlays:
                    rect = ov.get("rect")
                    if not rect:
                        continue
                    x1, y1, x2, y2 = self._to_xyxy(rect)
                    outline = ov.get("outline") or (255, 255, 0)
                    try:
                        color = "#%02x%02x%02x" % (int(outline[0]), int(outline[1]), int(outline[2]))
                    except Exception:
                        color = "#ffff00"
                    try:
                        cv.create_rectangle(x1, y1, x2, y2, outline=color, width=2)
                    except Exception:
                        pass
                    label = str(ov.get("label") or "")
                    if label:
                        try:
                            lw = max(60, len(label) * 9 + 12)
                            lx1, ly1 = x1 + 2, y1 + 2
                            lx2, ly2 = lx1 + lw, ly1 + 18
                            cv.create_rectangle(lx1, ly1, lx2, ly2, fill="#000000", outline="")
                            try:
                                f = tk_font(root, 11)
                            except Exception:
                                f = None
                            if f is not None:
                                cv.create_text(lx1 + 6, (ly1 + ly2) // 2, text=label, fill="#ffffff", anchor="w", font=f)
                            else:
                                cv.create_text(lx1 + 6, (ly1 + ly2) // 2, text=label, fill="#ffffff", anchor="w")
                        except Exception:
                            pass

                # 在“目标ROI”旁展示模板原图（若提供了 template_path）
                if template_path and os.path.exists(template_path):
                    try:
                        # 选择目标 ROI：优先 label 含“模板”，否则第一个
                        target_rect = None
                        for ov in overlays:
                            lb = str(ov.get("label") or "")
                            if "模板" in lb:
                                target_rect = ov.get("rect")
                                break
                        if target_rect is None and overlays:
                            target_rect = overlays[0].get("rect")
                        if target_rect:
                            rx1, ry1, rx2, ry2 = self._to_xyxy(target_rect)
                            # 读取与缩放
                            if Image is not None and ImageTk is not None:
                                tpl = Image.open(template_path).convert("RGB")
                                maxw = 200
                                ratio = min(1.0, maxw / max(1, tpl.width))
                                tw, th = max(1, int(tpl.width * ratio)), max(1, int(tpl.height * ratio))
                                try:
                                    tpl = tpl.resize((tw, th))
                                except Exception:
                                    pass
                                # 计算位置：右侧优先，不够则左侧
                                px = rx2 + 10
                                py = ry1
                                if px + tw > W - 8:
                                    px = rx1 - 10 - tw
                                if px < 8:
                                    px = max(8, min(W - tw - 8, rx1))
                                py = max(8, min(H - th - 8, py))
                                # 背景板与标题
                                try:
                                    cv.create_rectangle(px - 6, py - 24, px + tw + 6, py + th + 6, fill="#000000", outline="")
                                    try:
                                        f = tk_font(root, 11)
                                    except Exception:
                                        f = None
                                    title = "模板原图"
                                    if f is not None:
                                        cv.create_text(px, py - 12, text=title, fill="#ffffff", anchor="w", font=f)
                                    else:
                                        cv.create_text(px, py - 12, text=title, fill="#ffffff", anchor="w")
                                except Exception:
                                    pass
                                # 贴图
                                try:
                                    ph = ImageTk.PhotoImage(tpl)
                                    self._ov_img_refs.append(ph)  # 避免被回收
                                    cv.create_image(px, py, image=ph, anchor="nw")
                                except Exception:
                                    pass
                    except Exception:
                        pass

                # 可选：保存真实屏幕截图（含叠加）
                if bool(getattr(self, "_debug_save_overlay_images", False)):
                    def _capture_and_save():
                        try:
                            import pyautogui as _pg  # type: ignore
                            img = _pg.screenshot()
                            loop_dir = self._loop_dir or self._debug_overlay_dir
                            os.makedirs(loop_dir, exist_ok=True)
                            img.save(os.path.join(loop_dir, fname))
                            try:
                                self._log_debug(f"[可视化] 已保存 {os.path.join(loop_dir, fname)}")
                            except Exception:
                                pass
                        except Exception:
                            pass
                    try:
                        root.after(80, _capture_and_save)
                    except Exception:
                        pass

                # 定时隐藏窗口并结束
                try:
                    top.after(delay_ms, lambda: (top.withdraw(), done.set()))
                except Exception:
                    done.set()
            except Exception:
                done.set()

        try:
            root.after(0, _spawn)
        except Exception:
            time.sleep(max(0.0, float(self._debug_overlay_sec)))
            return
        done.wait(timeout=(max(5.0, float(self._debug_overlay_sec) + 1.0)))

    # ---------- 刷新（最近购买 -> 我的收藏） ----------
    def refresh_favorites(self) -> bool:
        rp_path, rp_conf = self._tpl("recent_purchases_tab")
        fav_path, fav_conf = self._tpl("favorites_tab")
        if not rp_path or not os.path.exists(rp_path) or not fav_path or not os.path.exists(fav_path):
            # 刷新失败归为 Info：提示用户关键缺失
            self._log_info("[刷新] 模板缺失：请在‘多商品抢购模式’内配置‘最近购买’与‘我的收藏’模板。")
            # 追加调试信息：输出解析后的路径，便于定位路径解析问题
            try:
                self._log_debug(f"[刷新] 路径检查 rp='{rp_path}' exists={os.path.exists(rp_path) if rp_path else False} | fav='{fav_path}' exists={os.path.exists(fav_path) if fav_path else False}")
            except Exception:
                pass
            return False
        self._log_debug(f"[刷新] 开始：最近购买→我的收藏 rp_conf={rp_conf:.2f} fav_conf={fav_conf:.2f}")
        def _do_once() -> bool:
            # 第一步：点击最近购买（使其进入选中态）
            try:
                box = self.screen._pg.locateOnScreen(rp_path, confidence=rp_conf)
                if box is not None:
                    rect = (int(box.left), int(box.top), int(box.width), int(box.height))
                    self.screen.click_center(rect)
                    self._log_debug(f"[刷新] 点击最近购买 rect={rect}")
                    # Debug 可视化：最近购买模板与位置
                    self._debug_show_overlay([
                        {"rect": rect, "label": "最近购买", "fill": (45, 124, 255, 90), "outline": (45, 124, 255)},
                    ], stage="刷新：最近购买", template_path=rp_path, save_name="overlay_refresh_rp.png")
                    time.sleep(0.05)
                    self._debug_pause("after_click_rp")
            except Exception:
                pass
            # 第二步：点击我的收藏（此时应为未选中态，可匹配）
            try:
                box = self.screen._pg.locateOnScreen(fav_path, confidence=fav_conf)
                if box is not None:
                    rect = (int(box.left), int(box.top), int(box.width), int(box.height))
                    self.screen.click_center(rect)
                    self._log_debug(f"[刷新] 点击我的收藏 rect={rect}")
                    # Debug 可视化：我的收藏模板与位置
                    self._debug_show_overlay([
                        {"rect": rect, "label": "我的收藏", "fill": (46, 160, 67, 90), "outline": (46, 160, 67)},
                    ], stage="刷新：我的收藏", template_path=fav_path, save_name="overlay_refresh_fav.png")
                    time.sleep(0.05)
                    # 计算收藏网格区域（限定商品模板搜索范围）
                    try:
                        sw, sh = self.screen._pg.size()
                    except Exception:
                        try:
                            import pyautogui as _pg  # type: ignore
                            _sz = _pg.size()
                            sw, sh = int(_sz[0]), int(_sz[1])
                        except Exception:
                            sw, sh = 0, 0
                    try:
                        left = max(8, int(self._grid_left_margin))
                        right = max(left + 1, sw - max(8, int(self._grid_right_margin)))
                        top = int(rect[1] + rect[3] + max(0, int(self._grid_top_margin)))
                        top = max(8, min(top, sh - 8))
                        bottom = max(top + 1, sh - max(8, int(self._grid_bottom_margin)))
                        w = max(1, right - left)
                        h = max(1, bottom - top)
                        region = (left, top, w, h)
                        # 粗校验尺寸
                        if sw > 0 and sh > 0 and (w >= 40 and h >= 40):
                            self._grid_region = region
                            self._log_debug(f"[区域] 收藏网格区域={region} 屏幕=({sw},{sh})")
                    except Exception:
                        pass
                    # 进入收藏后，等待内容就绪
                    try:
                        got = self._wait_favorites_content_ready(probe_step=float(getattr(self, "_probe_step_sec", 0.06)))
                        self._log_debug(f"[刷新] 收藏内容就绪={got}")
                    except Exception:
                        pass
                    self._log_info("[刷新] 已执行：最近购买 -> 我的收藏")
                    return True
            except Exception:
                pass
            return False

        if _do_once():
            return True
        # 兜底：短暂停后重试一次
        try:
            time.sleep(0.3)
        except Exception:
            pass
        if _do_once():
            return True
        self._log_info("[刷新] 未能完成：请检查标签模板是否清晰、阈值是否合适。")
        return False

    def _wait_favorites_content_ready(self, *, timeout: float = 1.8, confidence: float = 0.83, probe_step: float = 0.06, max_probe_items: int = 6) -> bool:
        """在收藏页等待任意一个任务的中间模板出现在屏幕上。

        目的：避免刚进入收藏时数据尚未加载就开始 ROI 截图与 OCR，导致误判。

        策略：
        - 从启用的任务中收集有可用模板的条目，随机打乱；
        - 在 timeout 窗口内，循环按序尝试 locateOnScreen，命中即返回 True；
        - 命中后展示一次叠加并小暂停；
        - 若候选为空或超时未命中，返回 False（外层仍会继续，但这一步已尽力等待）。
        """
        try:
            items = [
                it for it in self.items
                if bool(getattr(it, "enabled", True)) and (getattr(it, "template", None) and os.path.exists(getattr(it, "template")))
            ]
        except Exception:
            items = []
        if not items:
            try:
                self._log_debug("[就绪等待] 候选为空：无启用任务或模板缺失")
            except Exception:
                pass
            return False
        try:
            random.shuffle(items)
        except Exception:
            pass
        # 限制本轮探测的任务数量，降低全屏匹配的开销
        items = items[:max(1, int(max_probe_items))]
        # 轮询直到超时
        end = time.time() + max(0.2, float(timeout))
        idx = 0
        names = ", ".join([str(getattr(it, 'name', '') or '') for it in items])
        self._log_debug(
            f"[就绪等待] 计划探测 {len(items)} 项 timeout={timeout}s step={probe_step}s conf={confidence} 列表=[{names}]"
        )
        while time.time() < end:
            it = items[idx % len(items)]
            idx += 1
            # 每轮探测加入间隔，避免忙等并给 UI 留出渲染时间
            try:
                time.sleep(max(0.02, float(probe_step)))
            except Exception:
                time.sleep(0.02)
            path = getattr(it, "template", "") or ""
            try:
                box = self.screen._pg.locateOnScreen(path, confidence=float(confidence))
            except Exception:
                box = None
            if box is not None:
                try:
                    rect = (int(box[0]), int(box[1]), int(box[2]), int(box[3]))
                except Exception:
                    rect = (int(getattr(box, 'left', 0)), int(getattr(box, 'top', 0)), int(getattr(box, 'width', 0)), int(getattr(box, 'height', 0)))
                self._log_debug(f"[就绪等待] 命中 {it.name} rect={rect}")
                # 可视化：命中的那个中间模板
                try:
                    self._debug_show_overlay([
                        {"rect": rect, "label": f"就绪[{it.name}]", "fill": (255, 216, 77, 90), "outline": (255, 216, 77)},
                    ], stage="收藏加载就绪", template_path=path, save_name="overlay_fav_ready.png")
                except Exception:
                    pass
                # 稳定一下再返回
                time.sleep(0.05)
                return True
            else:
                self._log_debug(f"[就绪等待] 未命中 {it.name}")
        time.sleep(max(0.02, float(probe_step)))
        self._log_debug("[就绪等待] 超时，继续后续流程")
        return False

    # ---------- 坐标推断与缓存 ----------
    @staticmethod
    def _infer_card_from_mid(mid: Tuple[int, int, int, int]) -> Tuple[int, int, int, int]:
        ml, mt, mw, mh = mid
        x1 = ml - MARG_LR
        y1 = mt - (TOP_H + MARG_TB)
        w = mw + 2 * MARG_LR
        h = (TOP_H + MARG_TB) + mh + (MARG_TB + BTM_H)
        return int(x1), int(y1), int(w), int(h)

    @staticmethod
    def _rois_from_card(card: Tuple[int, int, int, int]) -> Tuple[Tuple[int, int, int, int], Tuple[int, int, int, int]]:
        cl, ct, cw, ch = card
        top_rect = (cl, ct, cw, TOP_H)
        btm_rect = (cl, ct + ch - BTM_H, cw, BTM_H)
        return top_rect, btm_rect

    def _ensure_card_cached(self, item: SnipeItem, *, timeout: float = 1.2, confidence: float = 0.83) -> bool:
        if item.id in self._card_cache:
            return True
        if not item.template or not os.path.exists(item.template):
            # 单条任务配置问题，降级为 Debug，避免 Info 污染
            self._log_debug(f"[定位][{item.name}] 中间模板缺失：{item.template}")
            return False
        try:
            self._log_debug(f"[定位][{item.name}] 开始 timeout={timeout}s conf={confidence} tpl={item.template}")
        except Exception:
            pass
        t0 = time.time()
        end = t0 + max(0.0, timeout)
        box = None
        while time.time() < end and box is None:
            try:
                b = self.screen._pg.locateOnScreen(item.template, confidence=float(confidence))
                if b is not None:
                    box = (int(b.left), int(b.top), int(b.width), int(b.height))
                    break
            except Exception:
                pass
            time.sleep(0.02)
        if box is None:
            try:
                self._log_debug(f"[定位][{item.name}] 结束 未命中 耗时={int((time.time()-t0)*1000)}ms")
            except Exception:
                pass
            # 定位失败细节保持 Debug
            self._log_debug(f"[定位][{item.name}] 匹配失败，建议降低阈值或重截模板。")
            return False
        # 同时缓存中间模板框与推导出的卡片框
        mid = box
        card = self._infer_card_from_mid(box)
        self._mid_cache[item.id] = mid
        self._card_cache[item.id] = card
        try:
            self._log_debug(f"[定位][{item.name}] 结束 命中 mid={mid} card={card} 耗时={int((time.time()-t0)*1000)}ms")
        except Exception:
            pass
        # Debug 可视化：卡片结构与 ROI 区域
        try:
            top_rect, btm_rect = self._rois_from_card(card)
            self._debug_show_overlay([
                {"rect": card, "label": f"卡片[{item.name}]", "fill": (204, 204, 204, 40), "outline": (204, 204, 204)},
                {"rect": mid, "label": "中间模板", "fill": (255, 216, 77, 90), "outline": (255, 216, 77)},
                {"rect": top_rect, "label": "名称区(Top)", "fill": (45, 124, 255, 90), "outline": (45, 124, 255)},
                {"rect": btm_rect, "label": "价格区(Bottom)", "fill": (46, 160, 67, 90), "outline": (46, 160, 67)},
            ], stage="卡片定位与ROI", template_path=item.template, save_name=f"overlay_card_{item.id}.png")
        except Exception:
            pass
        return True

    # ---------- 批量截图 ----------
    def _screenshot(self, region: Tuple[int, int, int, int]):
        try:
            return self.screen._pg.screenshot(region=(int(region[0]), int(region[1]), int(region[2]), int(region[3])))
        except Exception:
            return None

    def collect_batch_rois(self) -> List[Dict[str, Any]]:
        t0 = time.time()
        jobs: List[Dict[str, Any]] = []
        total = 0
        for it in self.items:
            # 跳过禁用任务，禁用任务不应进入截图/OCR阶段
            try:
                if not bool(getattr(it, "enabled", True)):
                    continue
            except Exception:
                pass
            total += 1
            # 跳过已达目标数量的任务
            try:
                if int(getattr(it, "target_total", 0) or 0) > 0 and int(getattr(it, "purchased", 0) or 0) >= int(getattr(it, "target_total", 0) or 0):
                    continue
            except Exception:
                pass
            if not self._ensure_card_cached(it):
                try:
                    self._log_debug(f"[截图][{it.name}] 跳过：未定位到中间模板（未缓存 mid/card）。")
                except Exception:
                    pass
                continue
            card = self._card_cache.get(it.id)
            if not card:
                continue
            # 非调试：在定位命中后，截取 ROI 前短暂等待，提升文字稳定性
            try:
                if not self._debug_active():
                    time.sleep(max(0.0, float(getattr(self, "_roi_pre_capture_wait_sec", 0.05))))
            except Exception:
                pass
            top_rect, btm_rect = self._rois_from_card(card)
            name_img = self._screenshot(top_rect)
            price_img = self._screenshot(btm_rect)
            if name_img is None or price_img is None:
                # 截图失败为 Debug 细节
                self._log_debug(f"[截图][{it.name}] 失败：ROI 越界或截屏异常。")
                continue
            # 可视化移至 OCR 后展示，以便底部标注实际“清洗后价格”
            jobs.append({
                "item": it,
                "name_img": name_img,
                "price_img": price_img,
                "top_rect": top_rect,
                "btm_rect": btm_rect,
            })
        try:
            self._log_debug(f"[截图] 批量完成 目标={total} 有效={len(jobs)} 耗时={int((time.time()-t0)*1000)}ms")
        except Exception:
            pass
        return jobs

    # ---------- 并发 OCR ----------
    def _umi_ocr_one(self, pil_image, *, allowlist: Optional[str] = None) -> str:
        # 优先使用 super_buyer.services.ocr 作为统一识别实现
        from super_buyer.services.ocr import recognize_text  # type: ignore
        umi = (self.cfg.get("umi_ocr", {}) or {})
        base_url = str(umi.get("base_url", "http://127.0.0.1:1224"))
        timeout = float(umi.get("timeout_sec", 5.0) or 5.0)
        options = umi.get("options", {}) or {}
        # 统一使用 Umi-OCR：若提供 allowlist，则透传至 options 的常见键位（由 Umi 端决定是否采纳）
        if allowlist:
            try:
                opts = dict(options)
                opts.setdefault("rec_char_type", "custom")
                opts.setdefault("custom_chars", allowlist)
                opts.setdefault("use_space_char", False)
                options = opts
            except Exception:
                pass
        boxes = recognize_text(pil_image, base_url=base_url, timeout=timeout, options=options)
        txt = " ".join((b.text or "").strip() for b in boxes if (b.text or "").strip())
        return txt

    def ocr_batch(self, imgs: List[Tuple[str, Any]], *, max_workers: Optional[int] = None) -> Dict[str, str]:
        """并发 OCR。

        imgs: [(key, PIL.Image), ...]
        返回：key -> text
        """
        t0 = time.time()
        results: Dict[str, str] = {}
        # 动态并发：支持 CPU 降压（可用 cfg['multi_snipe_tuning'].ocr_max_workers 覆盖）
        try:
            if not isinstance(max_workers, int) or max_workers <= 0:
                max_workers = int(getattr(self, "_ocr_max_workers", 4) or 4)
        except Exception:
            max_workers = 4
        try:
            self._log_debug(f"[OCR] 开始 并发={max_workers} 数量={len(imgs)} 引擎=umi")
        except Exception:
            pass
        # Determine allowlist for price images (key startswith 'price:')
        try:
            price_allow = str(self.cfg.get("ocr_allowlist", "0123456789KkMm"))
        except Exception:
            price_allow = "0123456789KkMm"
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {}
            for key, im in imgs:
                if isinstance(key, str) and key.startswith("price:"):
                    fut = ex.submit(self._umi_ocr_one, im, allowlist=price_allow)
                else:
                    fut = ex.submit(self._umi_ocr_one, im)
                futs[fut] = key
            for fu in as_completed(futs):
                key = futs[fu]
                try:
                    results[key] = fu.result() or ""
                except Exception:
                    results[key] = ""
        try:
            self._log_debug(f"[OCR] 结束 耗时={int((time.time()-t0)*1000)}ms")
        except Exception:
            pass
        return results

    # ---------- 详情页平均价读取（锚定“购买”按钮） ----------
    def _read_detail_avg_price(self, *, expected_floor: Optional[int] = None) -> Optional[int]:
        b = self.screen.locate("btn_buy", timeout=0.4)
        if b is None:
            return None
        b_left, b_top, b_w, _b_h = b
        avg_cfg = self.cfg.get("avg_price_area") or {}
        try:
            dist = int(avg_cfg.get("distance_from_buy_top", 5) or 5)
            hei = int(avg_cfg.get("height", 45) or 45)
        except Exception:
            dist, hei = 5, 45
        t0 = time.time()
        y_bottom = int(b_top - dist)
        y_top = int(y_bottom - hei)
        x_left = int(b_left)
        width = int(max(1, b_w))
        try:
            sw, sh = self.screen._pg.size()  # type: ignore[attr-defined]
        except Exception:
            sw, sh = 1920, 1080
        y_top = max(0, min(sh - 2, y_top))
        y_bottom = max(y_top + 1, min(sh - 1, y_bottom))
        x_left = max(0, min(sw - 2, x_left))
        width = max(1, min(width, sw - x_left))
        height = max(1, y_bottom - y_top)
        if height <= 0 or width <= 0:
            return None
        roi = (x_left, y_top, width, height)
        # Debug 可视化：详情页平均价OCR区域（以购买按钮为锚）
        try:
            self._debug_show_overlay([
                {"rect": (b_left, b_top, b_w, _b_h), "label": "按钮-购买", "fill": (255, 99, 71, 70), "outline": (255, 99, 71)},
                {"rect": roi, "label": "详情均价OCR区域", "fill": (255, 216, 77, 90), "outline": (255, 216, 77)},
            ], stage="详情价复核区域", template_path=None, save_name="overlay_detail_avg.png")
        except Exception:
            pass
        self._log_debug(f"[详情均价] ROI x={x_left} y={y_top} w={width} h={height} dist={dist} height={hei}")
        img = self._screenshot(roi)
        if img is None:
            return None
        try:
            w0, h0 = img.size
        except Exception:
            return None
        if h0 < 2:
            return None
        mid_h = h0 // 2
        img_top = img.crop((0, 0, w0, mid_h))
        try:
            sc = float((avg_cfg.get("scale", 1.0) or 1.0))
        except Exception:
            sc = 1.0
        if sc < 0.6:
            sc = 0.6
        if sc > 2.5:
            sc = 2.5
        if abs(sc - 1.0) > 1e-3:
            try:
                img_top = img_top.resize((max(1, int(img_top.width * sc)), max(1, int(img_top.height * sc))))
            except Exception:
                pass
        # Downstream allowlist hint to OCR service
        try:
            avg_allow = str(self.cfg.get("ocr_allowlist", "0123456789KkMm"))
        except Exception:
            avg_allow = "0123456789KkMm"
        txt = self._umi_ocr_one(img_top, allowlist=avg_allow) or ""
        # 白名单清洗：仅保留 0-9 与 KkMm
        try:
            allowlist = str(self.cfg.get("ocr_allowlist", "0123456789KkMm"))
        except Exception:
            allowlist = "0123456789KkMm"
        txt_clean = "".join(ch for ch in (txt or "") if ch in allowlist)
        val = _parse_price_text(txt_clean or "")
        if val is None or val <= 0:
            self._log_debug(
                f"[详情均价] 识别失败 文本='{(txt_clean[:20] + '...') if len(txt_clean)>20 else txt_clean}' 耗时={int((time.time()-t0)*1000)}ms"
            )
            return None
        try:
            floor = int(expected_floor or 0)
        except Exception:
            floor = 0
        if floor > 0 and int(val) < max(1, floor // 2):
            self._log_debug(f"[详情均价] 低于地板/2 val={val} floor={floor}")
            return None
        self._log_debug(f"[详情均价] 结果={int(val)} scale={sc} 耗时={int((time.time()-t0)*1000)}ms")
        return int(val)

    # ---------- 单轮扫描 ----------
    def scan_once(self) -> List[Dict[str, Any]]:
        """执行一次刷新+批量识别，返回识别结果列表。

        返回项：{item, name_text, price_text, price_value}
        """
        t0 = time.time()
        self._log_debug("[扫描] 开始：刷新与批量截图")
        self.refresh_favorites()
        jobs = self.collect_batch_rois()
        self._log_debug(f"[扫描] 截图完成 jobs={len(jobs)} 耗时={int((time.time()-t0)*1000)}ms")
        pairs: List[Tuple[str, Any]] = []
        for j in jobs:
            it: SnipeItem = j["item"]
            # 外层价格识别：将价格 ROI 放大 2.5 倍后再 OCR
            price_img_scaled = j.get("price_img")
            try:
                if price_img_scaled is not None:
                    w0, h0 = price_img_scaled.size  # type: ignore[attr-defined]
                    sw = max(1, int(w0 * 2.5))
                    sh = max(1, int(h0 * 2.5))
                    price_img_scaled = price_img_scaled.resize((sw, sh))
            except Exception:
                pass
            j["price_img_scaled"] = price_img_scaled
            pairs.append((f"name:{it.id}", j["name_img"]))
            # 列表价改为 utils/ocr_utils 识别，批量 OCR 不再包含 price
        t1 = time.time()
        texts = self.ocr_batch(pairs)
        self._log_debug(f"[扫描] OCR完成 耗时={int((time.time()-t1)*1000)}ms 总耗时={int((time.time()-t0)*1000)}ms")
        out: List[Dict[str, Any]] = []
        for j in jobs:
            it: SnipeItem = j["item"]
            name_txt = (texts.get(f"name:{it.id}") or "").strip()
            # 使用 utils/ocr_utils 进行列表价数字识别
            price_txt = ""
            cand_val: Optional[int] = None
            try:
                btm_rect = j.get("btm_rect")
                offset = (int(btm_rect[0]), int(btm_rect[1])) if btm_rect else (0, 0)
            except Exception:
                offset = (0, 0)
            img_for_num = j.get("price_img_scaled") or j.get("price_img")
            try:
                umi = (self.cfg.get("umi_ocr", {}) or {})
                _umi_base = str(umi.get("base_url", "http://127.0.0.1:1224"))
                _umi_timeout = float(umi.get("timeout_sec", 2.5) or 2.5)
                _umi_opts = dict(umi.get("options", {}) or {})
            except Exception:
                _umi_base, _umi_timeout, _umi_opts = "http://127.0.0.1:1224", 2.5, {}
            try:
                cands = recognize_numbers(
                    img_for_num,
                    base_url=_umi_base,
                    timeout=_umi_timeout,
                    options=_umi_opts,
                    offset=offset,
                )
            except Exception:
                cands = []
            # 记录数字候选统计日志（仅 Debug）
            try:
                vals = []
                for c in (cands or []):
                    v = getattr(c, 'value', None)
                    t = getattr(c, 'clean_text', None) or getattr(c, 'text', None) or ''
                    if v is not None:
                        vals.append(f"{int(v)}:{str(t)[:8]}")
                self._log_debug(f"[数字OCR][{it.name}] 候选={len(cands or [])} 列表=[{', '.join(vals[:5])}]")
            except Exception:
                pass
            try:
                cand = max([c for c in cands if getattr(c, "value", None) is not None], key=lambda c: int(c.value)) if cands else None  # type: ignore[arg-type]
            except Exception:
                cand = None
            if cand is not None:
                try:
                    price_txt = str(getattr(cand, "clean_text", "") or "")
                except Exception:
                    price_txt = str(getattr(cand, "text", "") or "")
                try:
                    cand_val = int(getattr(cand, "value", None)) if getattr(cand, "value", None) is not None else None
                except Exception:
                    cand_val = None
            # 关键词判定：若为错误/提示文本，直接视为无效
            try:
                _t = (price_txt or "").strip().lower()
                if _t and any(k in _t for k in (
                    "no text found", "no text", "未识别", "识别失败", "请求失败", "error", "exception", "path", "base64"
                )):
                    price_txt = ""
            except Exception:
                pass
            # 白名单清洗（列表价格）：仅保留 0-9 与 KkMm
            try:
                price_cfg = self.cfg.get("price_roi", {}) or {}
            except Exception:
                price_cfg = {}
            allowlist = str(self.cfg.get("ocr_allowlist", "0123456789KkMm"))
            price_txt_clean = "".join(ch for ch in price_txt if ch in allowlist)
            val = _parse_price_text(price_txt_clean or "")
            if cand_val is not None:
                try:
                    val = int(cand_val)
                except Exception:
                    pass
            # 调试叠加：在列表上直接标注识别文本（顶部=名称；底部=清洗后价格或原始价格）
            try:
                if self._debug_active():
                    top_rect = j.get("top_rect")
                    btm_rect = j.get("btm_rect")
                    price_disp = (str(int(val)) if isinstance(val, int) else (price_txt_clean or "NA"))
                    overlays = []
                    if top_rect:
                        overlays.append({"rect": top_rect, "label": f"{(name_txt or it.name)[:16]}", "fill": (45,124,255,90), "outline": (45,124,255)})
                    if btm_rect:
                        overlays.append({"rect": btm_rect, "label": f"{price_disp}", "fill": (46,160,67,90), "outline": (46,160,67)})
                    # 高亮 utils 数字候选 bbox（若存在）
                    try:
                        if 'cand' in locals() and cand is not None and getattr(cand, 'bbox', None):
                            bx, by, bw, bh = getattr(cand, 'bbox')
                            overlays.append({
                                "rect": (int(bx), int(by), int(bw), int(bh)),
                                "label": f"候选{cand_val if isinstance(cand_val, int) else ''}",
                                "fill": (255, 99, 71, 50),
                                "outline": (255, 99, 71)
                            })
                    except Exception:
                        pass
                    if overlays:
                        self._debug_show_overlay(overlays, stage="列表OCR结果", template_path=getattr(it, "template", None) or None, save_name=f"overlay_rois_{it.id}.png")
            except Exception:
                pass
            # 保存调试用的价格 ROI 图片（文件名包含结果与时间）
            try:
                _paths = (self.cfg.get("paths") or {})
            except Exception:
                _paths = {}
            _out_root = str((_paths.get("output_dir") if isinstance(_paths, dict) else None) or "output")
            try:
                dbg_dir = os.path.join(_out_root, "debug")
                os.makedirs(dbg_dir, exist_ok=True)
                ts = time.strftime("%Y%m%d_%H%M%S")
                vdisp = (str(int(val)) if isinstance(val, int) else (price_txt or "NA").replace("/", "-").replace("\\", "-"))
                # 简单清理名称中的不可用字符
                def _clean(s: str) -> str:
                    for ch in ("/", "\\", ":", "*", "?", "\"", "<", ">", "|"):
                        s = s.replace(ch, "-")
                    return s
                name_clean = _clean(it.name or "item")
                fname = f"price_{ts}_{name_clean}_{vdisp}.png"
                path = os.path.join(dbg_dir, fname)
                img_to_save = j.get("price_img_scaled") or j.get("price_img")
                if img_to_save is not None:
                    try:
                        img_to_save.save(path)
                    except Exception:
                        pass
            except Exception:
                pass
            # 历史价格记录
            try:
                if isinstance(val, int) and getattr(it, "item_id", None):
                    from history_store import append_price  # type: ignore
                    append_price(
                        item_id=str(getattr(it, "item_id", "") or ""),
                        item_name=(it.name or name_txt or ""),
                        price=int(val),
                        category=((it.big_category or "") or None),
                    )
            except Exception:
                pass
            out.append({
                "item": it,
                "name_text": name_txt,
                "price_text": price_txt,
                "price_value": (int(val) if isinstance(val, int) else None),
                "top_rect": j.get("top_rect"),
                "btm_rect": j.get("btm_rect"),
            })
        self._log_debug(f"[扫描] 结束 结果条数={len(out)}")
        return out

    # ---------- 数量调整 ----------
    def _adjust_qty(self, target: int) -> None:
        target = max(1, int(target or 1))
        try:
            self._log_debug(f"[数量] 目标数量={target}")
        except Exception:
            pass
        if target <= 1:
            return
        plus = self.screen.locate("qty_plus", timeout=0.2)
        if plus is None:
            try:
                self._log_debug("[数量] 未找到数量+ 按钮")
            except Exception:
                pass
            return
        for _ in range(target - 1):
            self.screen.click_center(plus)
            time.sleep(0.01)

    # ---------- 购买（进入详情→复核详情价→购买） ----------
    def _purchase_once(self, it: SnipeItem, *, price_limit: int) -> Tuple[bool, int]:
        t0 = time.time()
        # 优先用中间图中心进入详情，兜底卡片中心
        mid = self._mid_cache.get(it.id)
        card = self._card_cache.get(it.id)
        target_box = mid or card
        if not target_box:
            return False, 0
        try:
            self._log_debug(f"[购买][{it.name}] 步骤1: 进入详情 target={target_box}")
        except Exception:
            pass
        # Debug：进入详情前可视化点击区域
        try:
            self._debug_show_overlay([
                {"rect": target_box, "label": f"进入详情[{it.name}]", "fill": (45, 124, 255, 90), "outline": (45, 124, 255)},
            ], stage="进入详情点击", template_path=getattr(it, "template", None) or None, save_name=f"overlay_enter_{it.id}.png")
        except Exception:
            pass
        self.screen.click_center(target_box)
        time.sleep(0.05)
        # 详情价复核：以“购买”按钮为锚点
        base = int(getattr(it, "price", 0) or getattr(it, "price_threshold", 0) or 0)
        unit = self._read_detail_avg_price(expected_floor=(base if base > 0 else None))
        try:
            self._log_debug(f"[复核][{it.name}] 步骤2: 详情平均价={unit if unit is not None else '-'} 上限={price_limit}")
        except Exception:
            pass
        if unit is None or int(unit) > int(price_limit):
            c = self.screen.locate("btn_close", timeout=0.3)
            if c is not None:
                # Debug：复核失败时，标注关闭按钮
                try:
                    self._debug_show_overlay([
                        {"rect": c, "label": "关闭详情", "fill": (255, 99, 71, 70), "outline": (255, 99, 71)},
                    ], stage="复核未通过-关闭详情", template_path=None, save_name="overlay_close_on_reject.png")
                except Exception:
                    pass
                self.screen.click_center(c)
            try:
                self._log_debug(f"[购买][{it.name}] 复核未通过 用时={int((time.time()-t0)*1000)}ms")
            except Exception:
                pass
            # 连续失败计数 + 强制重定位（超过阈值）
            try:
                _id = str(it.id)
                self._fail_counts[_id] = int(self._fail_counts.get(_id, 0)) + 1
                if int(self._fail_counts[_id]) >= max(1, int(getattr(self, "_relocate_after_fail", 3))):
                    try:
                        self._card_cache.pop(_id, None)
                        self._mid_cache.pop(_id, None)
                    except Exception:
                        pass
                    self._fail_counts[_id] = 0
                    self._log_debug(f"[恢复][{it.name}] 连续复核失败，已清空缓存强制重定位")
            except Exception:
                pass
            return False, 0
        # 购买数量/Max 逻辑
        mode = str(getattr(it, "purchase_mode", "normal") or "normal").lower()
        used_max = False
        if mode == "restock":
            mx = self.screen.locate("btn_max", timeout=0.25)
            if mx is not None:
                self.screen.click_center(mx)
                time.sleep(0.02)
                used_max = True
                try:
                    self._log_debug(f"[数量][{it.name}] 使用最大按钮")
                except Exception:
                    pass
            else:
                self._adjust_qty(5)
        else:
            qty = int(getattr(it, "buy_qty", 1) or 1)
            if qty > 1:
                self._adjust_qty(qty)
        # 定位并点击购买
        b = self.screen.locate("btn_buy", timeout=0.5)
        if b is None:
            # 关闭详情
            c = self.screen.locate("btn_close", timeout=0.3)
            if c is not None:
                self.screen.click_center(c)
            try:
                self._log_debug(f"[购买][{it.name}] 未找到购买按钮 用时={int((time.time()-t0)*1000)}ms")
            except Exception:
                pass
            # 连续失败计数 + 强制重定位
            try:
                _id = str(it.id)
                self._fail_counts[_id] = int(self._fail_counts.get(_id, 0)) + 1
                if int(self._fail_counts[_id]) >= max(1, int(getattr(self, "_relocate_after_fail", 3))):
                    try:
                        self._card_cache.pop(_id, None)
                        self._mid_cache.pop(_id, None)
                    except Exception:
                        pass
                    self._fail_counts[_id] = 0
                    self._log_debug(f"[恢复][{it.name}] 未见购买按钮，已清空缓存强制重定位")
            except Exception:
                pass
            return False, 0
        # Debug：购买按钮点击
        try:
            buy_tpl, _ = self._tpl("btn_buy")
            self._debug_show_overlay([
                {"rect": b, "label": "购买按钮", "fill": (46, 160, 67, 90), "outline": (46, 160, 67)},
            ], stage="点击购买", template_path=(buy_tpl if buy_tpl and os.path.exists(buy_tpl) else None), save_name="overlay_click_buy.png")
        except Exception:
            pass
        self.screen.click_center(b)
        t_end = time.time() + max(0.2, float(getattr(self, "_buy_result_timeout_sec", 0.8)))
        got_ok = False
        found_fail = False
        while time.time() < t_end:
            ok_box = self.screen.locate("buy_ok", timeout=0.0)
            if ok_box is not None:
                got_ok = True
                # Debug：购买成功标识
                try:
                    ok_tpl, _ = self._tpl("buy_ok")
                    self._debug_show_overlay([
                        {"rect": ok_box, "label": "购买成功", "fill": (46, 160, 67, 90), "outline": (46, 160, 67)},
                    ], stage="购买结果-成功", template_path=(ok_tpl if ok_tpl and os.path.exists(ok_tpl) else None), save_name="overlay_buy_ok.png")
                except Exception:
                    pass
                break
            fail_box = self.screen.locate("buy_fail", timeout=0.0)
            if fail_box is not None:
                found_fail = True
                # Debug：购买失败标识
                try:
                    fail_tpl, _ = self._tpl("buy_fail")
                    self._debug_show_overlay([
                        {"rect": fail_box, "label": "购买失败", "fill": (255, 99, 71, 90), "outline": (255, 99, 71)},
                    ], stage="购买结果-失败", template_path=(fail_tpl if fail_tpl and os.path.exists(fail_tpl) else None), save_name="overlay_buy_fail.png")
                except Exception:
                    pass
            time.sleep(0.02)
        # 关闭详情
        c = self.screen.locate("btn_close", timeout=0.4)
        if c is not None:
            try:
                self._debug_show_overlay([
                    {"rect": c, "label": "关闭详情", "fill": (45, 124, 255, 70), "outline": (45, 124, 255)},
                ], stage="关闭详情", template_path=None, save_name="overlay_close_after_buy.png")
            except Exception:
                pass
            self.screen.click_center(c)
        if not (got_ok and not found_fail):
            try:
                self._log_debug(f"[购买][{it.name}] 失败/放弃 用时={int((time.time()-t0)*1000)}ms")
            except Exception:
                pass
            # 连续失败计数 + 强制重定位（超过阈值）
            try:
                _id = str(it.id)
                self._fail_counts[_id] = int(self._fail_counts.get(_id, 0)) + 1
                if int(self._fail_counts[_id]) >= max(1, int(getattr(self, "_relocate_after_fail", 3))):
                    # 清理缓存强制下次重定位
                    try:
                        self._card_cache.pop(_id, None)
                        self._mid_cache.pop(_id, None)
                    except Exception:
                        pass
                    self._fail_counts[_id] = 0
                    self._log_debug(f"[恢复][{it.name}] 连续失败，已清空缓存强制重定位")
            except Exception:
                pass
            return False, 0
        # 购买增量：参考 task_runner 的逻辑
        try:
            is_ammo = (getattr(it, "big_category", "") or "").strip() == "弹药"
        except Exception:
            is_ammo = False
        inc = (120 if used_max else 10) if is_ammo else (5 if used_max else 1)
        try:
            self._log_debug(f"[购买][{it.name}] 成功 数量+={int(inc)} 总用时={int((time.time()-t0)*1000)}ms")
        except Exception:
            pass
        # 成功：清零连续失败计数
        try:
            self._fail_counts[str(it.id)] = 0
        except Exception:
            pass
        return True, int(inc)

    # ---------- 跑一轮并购买 ----------
    def run_once(self) -> Dict[str, Any]:
        # 标记一轮开始：更新轮次与叠加序号、建立本轮输出目录（若开启保存）
        try:
            self._loop_no = int(getattr(self, "_loop_no", 0)) + 1
        except Exception:
            self._loop_no = 1
        self._overlay_seq = 0
        try:
            ts = time.strftime("%Y%m%d_%H%M%S")
        except Exception:
            ts = str(int(time.time()))
        try:
            if bool(getattr(self, "_debug_save_overlay_images", False)):
                loop_name = f"{ts}_loop-{self._loop_no:04d}"
                self._loop_dir = os.path.join(self._debug_overlay_dir, loop_name)
                os.makedirs(self._loop_dir, exist_ok=True)
        except Exception:
            pass
        t0 = time.time()
        self._log_debug(f"[一轮] 开始 轮次={self._loop_no}")
        results = self.scan_once()
        bought: List[Dict[str, Any]] = []
        for r in results:
            it: SnipeItem = r["item"]
            if not bool(getattr(it, "enabled", True)):
                continue
            base = int(getattr(it, "price", 0) or getattr(it, "price_threshold", 0) or 0)
            prem = float(getattr(it, "premium_pct", 0.0) or 0.0)
            limit = base + int(round(base * max(0.0, prem) / 100.0)) if base > 0 else 0
            val = r.get("price_value")
            # 严守不买：未配置阈值（base<=0）不进详情
            if base <= 0:
                self._log_debug(f"[判断][{it.name}] 未配置阈值（基准价<=0），严守不买")
                continue
            if val is None:
                self._log_debug(f"[识别][{it.name}] 价格解析失败：{r.get('price_text','')}")
                continue
            # 低于设置价格的50% → 视为本次识别无效（丢弃）
            try:
                floor = max(1, int(base // 2))
            except Exception:
                floor = 1
            if int(val) < floor:
                self._log_debug(f"[识别][{it.name}] 列表价 OCR 异常：值={int(val)} 低于设置价格50%({floor})，本次丢弃")
                continue
            if int(val) > int(limit):
                self._log_debug(f"[判断][{it.name}] 列表价 {val} 高于上限 {limit}(基准 {base} +{int(prem)}%)，跳过")
                continue
            ok, inc = self._purchase_once(it, price_limit=int(limit))
            if ok:
                try:
                    it.purchased = int(getattr(it, 'purchased', 0) or 0) + int(inc or 0)
                except Exception:
                    pass
                bought.append({"id": it.id, "name": it.name, "price": int(val), "inc": int(inc)})
                self._log_info(f"[购买][{it.name}] 成功，列表价={val}")
            else:
                self._log_info(f"[购买][{it.name}] 放弃或失败（详情复核未通过/未知）")
        try:
            self._log_debug(f"[一轮] 结束 识别={len(results)} 购买={len(bought)} 用时={int((time.time()-t0)*1000)}ms")
        except Exception:
            pass
        # 一轮摘要：Info 输出识别/购买/耗时（精简且可读）
        # 统计本轮价格识别是否全部失败（用于处罚检测阈值累计）
        try:
            valid_count = 0
            for r in results:
                if r.get("price_value") is not None:
                    valid_count += 1
            if len(results) == 0 or valid_count == 0:
                self._ocr_miss_streak = int(getattr(self, "_ocr_miss_streak", 0)) + 1
            else:
                self._ocr_miss_streak = 0
                self._last_ocr_ok_ts = time.time()
        except Exception:
            pass

        # 达到阈值后，且距离上次成功识别已超过 penalty_confirm_delay_sec，再检测处罚
        try:
            if (
                int(getattr(self, "_ocr_miss_streak", 0)) >= max(1, int(getattr(self, "_ocr_miss_threshold", 10)))
                and (time.time() - float(getattr(self, "_last_ocr_ok_ts", 0.0))) >= float(max(2.0, getattr(self, "_penalty_confirm_delay_sec", 5.0)))
            ):
                self._check_and_handle_penalty()
        except Exception:
            pass

        try:
            self._log_info(f"[一轮] 完成 识别={len(results)} 购买={len(bought)} 用时={int((time.time()-t0)*1000)}ms")
        except Exception:
            pass
        return {"recognized": results, "bought": bought}

    # ---------- 处罚检测与处理 ----------
    def _check_and_handle_penalty(self) -> None:
        """当连续 OCR 未识别次数达到阈值后，检测并处理处罚提示。

        流程：
        1) 检测 `penalty_warning` 模板是否存在；若不存在则返回。
        2) 日志提示并等待 `self._penalty_confirm_delay_sec` 秒。
        3) 尝试定位 `btn_penalty_confirm` 并点击一次；若未定位到，记录日志后返回。
        4) 点击后等待 `self._penalty_wait_after_confirm_sec` 秒（处罚流程结束）。
        5) 清零计数，继续后续轮次。
        """
        try:
            self._log_info(
                f"[风控] OCR 连续未识别 {int(self._ocr_miss_streak)} 次，检查处罚提示…"
            )
        except Exception:
            pass
        # 检查处罚提示模板
        warn_box = None
        try:
            warn_box = self.screen.locate("penalty_warning", timeout=0.6)
        except Exception:
            warn_box = None
        if warn_box is None:
            try:
                self._log_debug("[风控] 未发现处罚提示模板，稍后继续重试。")
            except Exception:
                pass
            # 未命中处罚提示：清零计数，符合“未命中即清零”的策略
            try:
                self._ocr_miss_streak = 0
            except Exception:
                pass
            return

        # 可视化叠加（若开启）
        try:
            wtpl, _ = self._tpl("penalty_warning")
            self._debug_show_overlay(
                [
                    {"rect": warn_box, "label": "处罚提示", "fill": (255, 193, 7, 80), "outline": (255, 193, 7)},
                ],
                stage="检测到处罚提示",
                template_path=(wtpl if wtpl and os.path.exists(wtpl) else None),
                save_name="overlay_penalty_warning.png",
            )
        except Exception:
            pass

        # 延迟后点击确认
        try:
            self._log_info(
                f"[风控] 5 秒后点击处罚确认（等待 {int(self._penalty_confirm_delay_sec)}s）"
            )
        except Exception:
            pass
        try:
            time.sleep(max(0.0, float(self._penalty_confirm_delay_sec)))
        except Exception:
            pass

        # 定位确认按钮并点击
        btn_box = None
        t_end = time.time() + 2.0
        while time.time() < t_end and btn_box is None:
            try:
                btn_box = self.screen.locate("btn_penalty_confirm", timeout=0.2)
            except Exception:
                btn_box = None
        if btn_box is not None:
            try:
                btpl, _ = self._tpl("btn_penalty_confirm")
                self._debug_show_overlay(
                    [
                        {"rect": btn_box, "label": "处罚确认", "fill": (76, 175, 80, 80), "outline": (76, 175, 80)},
                    ],
                    stage="点击处罚确认",
                    template_path=(btpl if btpl and os.path.exists(btpl) else None),
                    save_name="overlay_penalty_confirm.png",
                )
            except Exception:
                pass
            try:
                self.screen.click_center(btn_box)
            except Exception:
                pass
            try:
                self._log_info(
                    f"[风控] 已点击处罚确认，等待 {int(self._penalty_wait_after_confirm_sec)}s 后继续…"
                )
            except Exception:
                pass
            try:
                time.sleep(max(0.0, float(self._penalty_wait_after_confirm_sec)))
            except Exception:
                pass
            # 处罚流程结束，清零计数
            try:
                self._ocr_miss_streak = 0
            except Exception:
                pass
        else:
            try:
                self._log_error("[风控] 未定位到处罚确认按钮，跳过点击。")
            except Exception:
                pass
