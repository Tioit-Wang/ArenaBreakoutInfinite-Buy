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

import base64
import io
import os
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

from task_runner import ScreenOps, _parse_price_text  # type: ignore


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
        try:
            self._debug_save_dir = str(dbg.get("save_dir", "debug"))
        except Exception:
            self._debug_save_dir = "debug"

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
        except Exception:
            self.screen = ScreenOps(cfg, step_delay=0.02)

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
        t = (self.cfg.get("templates", {}) or {}).get(key) or {}
        return str(t.get("path", "")), float(t.get("confidence", 0.85) or 0.85)

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
                try:
                    draw.text((bx1 + pad, by1 + pad // 2), label, fill=(255, 255, 255, 255))
                except Exception:
                    pass
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
                draw.text((cx - tw // 2, top), t, fill=(255, 255, 255, 255))
            except Exception:
                pass
        # 在左上角贴上模板图片缩略图
        if template_path and os.path.exists(template_path):
            try:
                tpl = Image.open(template_path).convert("RGBA")
                maxw = 240
                ratio = min(1.0, maxw / max(1, tpl.width))
                tpl = tpl.resize((max(1, int(tpl.width * ratio)), max(1, int(tpl.height * ratio))))
                # 放在 16, 60 处
                x_off, y_off = 16, 60
                # 边框底板
                draw.rectangle([x_off - 6, y_off - 24, x_off + tpl.width + 6, y_off + tpl.height + 6], fill=(0, 0, 0, 180))
                draw.text((x_off, y_off - 18), "模板预览", fill=(255, 255, 255, 255))
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
        """显示 5s 叠加蒙版；若 Tk 不可用，则落盘图片并等待 5s。

        overlays: 列表，每项含 rect=(l,t,w,h), label, 颜色
        """
        if not self._debug_active():
            return
        base = self._debug_screenshot_full()
        if base is None:
            time.sleep(max(0.0, float(self._debug_overlay_sec)))
            return
        annotated = self._debug_build_annotated(base, overlays, stage=stage, template_path=template_path)
        if annotated is None:
            time.sleep(max(0.0, float(self._debug_overlay_sec)))
            return
        # 保存调试图片
        try:
            os.makedirs(self._debug_save_dir, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            name = save_name or f"overlay_{ts}.png"
            annotated.save(os.path.join(self._debug_save_dir, name))
        except Exception:
            pass
        # 在屏幕中央展示 5s（尽量走 Tk 主线程）
        try:
            import tkinter as tk  # type: ignore
            root = getattr(tk, "_default_root", None)
        except Exception:
            root = None
        delay_ms = int(max(0.0, float(self._debug_overlay_sec)) * 1000)
        if root is None:
            time.sleep(max(0.0, float(self._debug_overlay_sec)))
            return
        done = threading.Event()

        def _spawn():
            try:
                top = tk.Toplevel(root)
                try:
                    top.attributes("-topmost", True)
                except Exception:
                    pass
                top.overrideredirect(True)
                sw, sh = int(root.winfo_screenwidth()), int(root.winfo_screenheight())
                iw, ih = int(annotated.width), int(annotated.height)
                # 缩放以适配屏幕
                scale = min(1.0, (sw - 80) / max(1, iw), (sh - 80) / max(1, ih))
                disp = annotated
                if scale < 0.999:
                    try:
                        disp = annotated.resize((max(1, int(iw * scale)), max(1, int(ih * scale))))
                    except Exception:
                        pass
                iw2, ih2 = disp.width, disp.height
                x = max(0, (sw - iw2) // 2)
                y = max(0, (sh - ih2) // 2)
                try:
                    top.geometry(f"{iw2}x{ih2}+{x}+{y}")
                except Exception:
                    pass
                # 显示图片
                try:
                    from PIL import ImageTk as _ImageTk  # type: ignore
                except Exception:
                    _ImageTk = None  # type: ignore
                if _ImageTk is None:
                    try:
                        top.after(delay_ms, lambda: (top.destroy(), done.set()))
                        return
                    except Exception:
                        done.set()
                        return
                try:
                    photo = _ImageTk.PhotoImage(disp)
                except Exception:
                    top.after(delay_ms, lambda: (top.destroy(), done.set()))
                    return
                import tkinter as tk2  # type: ignore
                lbl = tk2.Label(top, image=photo, borderwidth=0, highlightthickness=0)
                lbl.image = photo  # Prevent GC
                lbl.pack(fill=tk2.BOTH, expand=True)
                try:
                    top.after(delay_ms, lambda: (top.destroy(), done.set()))
                except Exception:
                    done.set()
            except Exception:
                done.set()

        try:
            root.after(0, _spawn)
        except Exception:
            # Fallback：阻塞等待
            time.sleep(max(0.0, float(self._debug_overlay_sec)))
            return
        # 阻塞等待窗口关闭
        done.wait(timeout=(max(5.0, float(self._debug_overlay_sec) + 1.0)))

    # ---------- 刷新（最近购买 -> 我的收藏） ----------
    def refresh_favorites(self) -> bool:
        rp_path, rp_conf = self._tpl("recent_purchases_tab")
        fav_path, fav_conf = self._tpl("favorites_tab")
        if not rp_path or not os.path.exists(rp_path) or not fav_path or not os.path.exists(fav_path):
            self._log("[刷新] 模板缺失：请在‘多商品抢购模式’内配置‘最近购买’与‘我的收藏’模板。")
            return False
        # 第一步：点击最近购买（使其进入选中态）
        try:
            box = self.screen._pg.locateOnScreen(rp_path, confidence=rp_conf)
            if box is not None:
                rect = (int(box.left), int(box.top), int(box.width), int(box.height))
                self.screen.click_center(rect)
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
                # Debug 可视化：我的收藏模板与位置
                self._debug_show_overlay([
                    {"rect": rect, "label": "我的收藏", "fill": (46, 160, 67, 90), "outline": (46, 160, 67)},
                ], stage="刷新：我的收藏", template_path=fav_path, save_name="overlay_refresh_fav.png")
                time.sleep(0.05)
                self._log("[刷新] 已执行：最近购买 -> 我的收藏")
                return True
        except Exception:
            pass
        self._log("[刷新] 未能完成：请检查标签模板是否清晰、阈值是否合适。")
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
            self._log(f"[定位][{item.name}] 中间模板缺失：{item.template}")
            return False
        end = time.time() + max(0.0, timeout)
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
            self._log(f"[定位][{item.name}] 匹配失败，建议降低阈值或重截模板。")
            return False
        # 同时缓存中间模板框与推导出的卡片框
        mid = box
        card = self._infer_card_from_mid(box)
        self._mid_cache[item.id] = mid
        self._card_cache[item.id] = card
        self._log_debug(f"[定位][{item.name}] mid={mid} card={card}")
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
        jobs: List[Dict[str, Any]] = []
        for it in self.items:
            # 跳过已达目标数量的任务
            try:
                if int(getattr(it, "target_total", 0) or 0) > 0 and int(getattr(it, "purchased", 0) or 0) >= int(getattr(it, "target_total", 0) or 0):
                    continue
            except Exception:
                pass
            if not self._ensure_card_cached(it):
                continue
            card = self._card_cache.get(it.id)
            if not card:
                continue
            top_rect, btm_rect = self._rois_from_card(card)
            name_img = self._screenshot(top_rect)
            price_img = self._screenshot(btm_rect)
            if name_img is None or price_img is None:
                self._log(f"[截图][{it.name}] 失败：ROI 越界或截屏异常。")
                continue
            # Debug 可视化：即将 OCR 的两个 ROI
            try:
                self._debug_show_overlay([
                    {"rect": top_rect, "label": f"名称ROI[{it.name}]", "fill": (45, 124, 255, 90), "outline": (45, 124, 255)},
                    {"rect": btm_rect, "label": f"价格ROI[{it.name}]", "fill": (46, 160, 67, 90), "outline": (46, 160, 67)},
                ], stage="列表OCR区域", template_path=it.template, save_name=f"overlay_rois_{it.id}.png")
            except Exception:
                pass
            jobs.append({
                "item": it,
                "name_img": name_img,
                "price_img": price_img,
                "top_rect": top_rect,
                "btm_rect": btm_rect,
            })
        return jobs

    # ---------- 并发 OCR ----------
    def _umi_ocr_one(self, pil_image) -> str:
        # 直接调用 ocr_reader.read_text 以统一行为
        try:
            from ocr_reader import read_text  # type: ignore
        except Exception:
            read_text = None  # type: ignore
        if read_text is None:
            return ""
        umi = (self.cfg.get("umi_ocr", {}) or {})
        base_url = str(umi.get("base_url", "http://127.0.0.1:1224"))
        timeout = float(umi.get("timeout_sec", 5.0) or 5.0)
        options = umi.get("options", {}) or {}
        try:
            # 启用灰度，有助于提升数字识别稳定性（与测试/详情复核保持一致）
            return read_text(
                pil_image,
                engine="umi",
                grayscale=True,
                umi_base_url=base_url,
                umi_timeout=timeout,
                umi_options=options,
            ) or ""
        except Exception:
            return ""

    def ocr_batch(self, imgs: List[Tuple[str, Any]], *, max_workers: int = 6) -> Dict[str, str]:
        """并发 OCR。

        imgs: [(key, PIL.Image), ...]
        返回：key -> text
        """
        results: Dict[str, str] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(self._umi_ocr_one, im): key for key, im in imgs}
            for fu in as_completed(futs):
                key = futs[fu]
                try:
                    results[key] = fu.result() or ""
                except Exception:
                    results[key] = ""
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
        txt = self._umi_ocr_one(img_top) or ""
        val = _parse_price_text(txt or "")
        if val is None or val <= 0:
            return None
        try:
            floor = int(expected_floor or 0)
        except Exception:
            floor = 0
        if floor > 0 and int(val) < max(1, floor // 2):
            return None
        return int(val)

    # ---------- 单轮扫描 ----------
    def scan_once(self) -> List[Dict[str, Any]]:
        """执行一次刷新+批量识别，返回识别结果列表。

        返回项：{item, name_text, price_text, price_value}
        """
        self.refresh_favorites()
        jobs = self.collect_batch_rois()
        pairs: List[Tuple[str, Any]] = []
        for j in jobs:
            it: SnipeItem = j["item"]
            # 外层价格识别：将价格 ROI 放大 1.5 倍后再 OCR
            price_img_scaled = j.get("price_img")
            try:
                if price_img_scaled is not None:
                    w0, h0 = price_img_scaled.size  # type: ignore[attr-defined]
                    sw = max(1, int(w0 * 1.5))
                    sh = max(1, int(h0 * 1.5))
                    price_img_scaled = price_img_scaled.resize((sw, sh))
            except Exception:
                pass
            j["price_img_scaled"] = price_img_scaled
            pairs.append((f"name:{it.id}", j["name_img"]))
            pairs.append((f"price:{it.id}", price_img_scaled))
        texts = self.ocr_batch(pairs)
        out: List[Dict[str, Any]] = []
        for j in jobs:
            it: SnipeItem = j["item"]
            name_txt = (texts.get(f"name:{it.id}") or "").strip()
            price_txt = (texts.get(f"price:{it.id}") or "").strip()
            val = _parse_price_text(price_txt or "")
            # 保存调试用的价格 ROI 图片（文件名包含结果与时间）
            try:
                dbg_dir = os.path.join("debug")
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
        return out

    # ---------- 数量调整 ----------
    def _adjust_qty(self, target: int) -> None:
        target = max(1, int(target or 1))
        if target <= 1:
            return
        plus = self.screen.locate("qty_plus", timeout=0.2)
        if plus is None:
            return
        for _ in range(target - 1):
            self.screen.click_center(plus)
            time.sleep(0.01)

    # ---------- 购买（进入详情→复核详情价→购买） ----------
    def _purchase_once(self, it: SnipeItem, *, price_limit: int) -> Tuple[bool, int]:
        # 优先用中间图中心进入详情，兜底卡片中心
        mid = self._mid_cache.get(it.id)
        card = self._card_cache.get(it.id)
        target_box = mid or card
        if not target_box:
            return False, 0
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
        self._log_debug(f"[复核][{it.name}] 详情平均价={unit if unit is not None else '-'} 上限={price_limit}")
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
        t_end = time.time() + 0.6
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
            return False, 0
        # 购买增量：参考 task_runner 的逻辑
        try:
            is_ammo = (getattr(it, "big_category", "") or "").strip() == "弹药"
        except Exception:
            is_ammo = False
        inc = (120 if used_max else 10) if is_ammo else (5 if used_max else 1)
        return True, int(inc)

    # ---------- 跑一轮并购买 ----------
    def run_once(self) -> Dict[str, Any]:
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
        return {"recognized": results, "bought": bought}
