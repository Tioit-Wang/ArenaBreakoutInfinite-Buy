from __future__ import annotations

import time
import os
import uuid
from pathlib import Path
from collections.abc import Callable

import tkinter as tk
from tkinter import messagebox, ttk

from super_buyer.services.font_loader import pil_font, setup_matplotlib_chinese, tk_font
from super_buyer.ui.widgets.selectors import RegionSelector


class _CardSelector:
    """固定 165x212 卡片样式选择器，复刻历史截图蒙版效果。"""

    def __init__(
        self,
        root: tk.Tk,
        on_done: Callable[[tuple[int, int, int, int] | None], None],
        *,
        w: int = 165,
        h: int = 212,
        top_h: int = 20,
        bottom_h: int = 30,
        margin_lr: int = 30,
        margin_tb: int = 30,
    ) -> None:
        self.root = root
        self.on_done = on_done
        self.w = int(max(1, w))
        self.h = int(max(1, h))
        self.top_h = int(max(0, top_h))
        self.bottom_h = int(max(0, bottom_h))
        self.margin_lr = int(max(0, margin_lr))
        self.margin_tb = int(max(0, margin_tb))
        self.top: tk.Toplevel | None = None
        self.canvas: tk.Canvas | None = None
        self._x = 0
        self._y = 0
        self.item_top: int | None = None
        self.item_mid: int | None = None
        self.item_bot: int | None = None
        self.item_outline: int | None = None
        self.item_img_rect: int | None = None

    def show(self) -> None:
        top = tk.Toplevel(self.root)
        self.top = top
        W = self.root.winfo_screenwidth()
        H = self.root.winfo_screenheight()
        top.geometry(f"{W}x{H}+0+0")
        for attr, val in (("-alpha", 0.25), ("-topmost", True)):
            try:
                top.attributes(attr, val)
            except Exception:
                pass
        top.configure(bg="black")
        top.overrideredirect(True)
        cv = tk.Canvas(top, bg="black", highlightthickness=0)
        cv.pack(fill=tk.BOTH, expand=True)
        self.canvas = cv
        try:
            font = tk_font(self.root, 12)
        except Exception:
            font = None
        try:
            text = f"移动鼠标定位，左键确认（{self.w}x{self.h}），右键/ESC取消"
            if font is not None:
                cv.create_text(W // 2, 30, text=text, fill="white", font=font)
            else:
                cv.create_text(W // 2, 30, text=text, fill="white")
        except Exception:
            pass
        self.item_top = cv.create_rectangle(0, 0, 1, 1, fill="#2d7cff", outline="")
        self.item_mid = cv.create_rectangle(0, 0, 1, 1, fill="#ffd84d", outline="")
        self.item_bot = cv.create_rectangle(0, 0, 1, 1, fill="#2ea043", outline="")
        try:
            self.item_outline = cv.create_rectangle(0, 0, 1, 1, outline="#cccccc", width=0.5)
        except Exception:
            self.item_outline = cv.create_rectangle(0, 0, 1, 1, outline="#cccccc", width=1)
        self.item_img_rect = cv.create_rectangle(0, 0, 1, 1, outline="#333333", dash=(4, 2))

        cv.bind("<Motion>", self._on_motion)
        cv.bind("<Button-1>", self._on_confirm)
        cv.bind("<Button-3>", self._on_cancel)
        cv.bind("<Escape>", self._on_cancel)
        try:
            cv.focus_force()
            top.grab_set()
        except Exception:
            pass

    def _on_motion(self, event: tk.Event) -> None:
        self._x = int(getattr(event, "x_root", 0))
        self._y = int(getattr(event, "y_root", 0))
        self._redraw()

    def _redraw(self) -> None:
        if not self.canvas:
            return
        x1 = self._x - self.w // 2
        y1 = self._y - self.h // 2
        x2 = x1 + self.w
        y2 = y1 + self.h
        mid_top = y1 + self.top_h
        mid_bot = y2 - self.bottom_h
        if self.item_top is not None:
            self.canvas.coords(self.item_top, x1, y1, x2, mid_top)
        if self.item_mid is not None:
            self.canvas.coords(self.item_mid, x1, mid_top, x2, mid_bot)
        if self.item_bot is not None:
            self.canvas.coords(self.item_bot, x1, mid_bot, x2, y2)
        if self.item_outline is not None:
            self.canvas.coords(self.item_outline, x1 + 1, y1 + 1, x2 - 1, y2 - 1)
        ix1 = x1 + self.margin_lr
        ix2 = x2 - self.margin_lr
        iy1 = mid_top + self.margin_tb
        iy2 = mid_bot - self.margin_tb
        if iy2 < iy1:
            iy2 = iy1
        if self.item_img_rect is not None:
            self.canvas.coords(self.item_img_rect, ix1, iy1, ix2, iy2)

    def _on_confirm(self, _event: tk.Event | None) -> None:
        if self.top is None:
            return
        x1 = self._x - self.w // 2
        y1 = self._y - self.h // 2
        x2 = x1 + self.w
        y2 = y1 + self.h
        try:
            self.top.grab_release()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass
        self.on_done((x1, y1, x2, y2))

    def _on_cancel(self, _event: tk.Event | None) -> None:
        if self.top is not None:
            try:
                self.top.grab_release()
            except Exception:
                pass
            try:
                self.top.destroy()
            except Exception:
                pass
        self.on_done(None)


class GoodsMarketUI(ttk.Frame):
    """物品市场子系统

    - 管理视图（表格 + 表单 + 截图存图）
    - 浏览视图（左侧类目树 + 右侧卡片网格），布局样式参考提供的截图，仅采用布局不限定配色

    数据：`goods.json`
    图片：`images/goods/<category_en>/<uuid>.png`
    """

    def __init__(self, master, *, images_dir: Path, goods_path: Path) -> None:
        super().__init__(master)
        self.pack(fill=tk.BOTH, expand=True)

        self.images_dir = Path(images_dir)
        self.images_dir.mkdir(parents=True, exist_ok=True)
        self.goods_path = Path(goods_path)
        self.goods: list[dict[str, object]] = []

        # 显示与存储的分类映射
        self.cat_map_en: dict[str, str] = {
            "装备": "equipment",
            "武器配件": "weapon_parts",
            "武器枪机": "firearms",
            "弹药": "ammo",
            "医疗用品": "medical",
            "战术道具": "tactical",
            "钥匙": "keys",
            "杂物": "misc",
            "饮食": "food",
        }
        self.sub_map: dict[str, list[str]] = {
            "装备": [
                "头盔",
                "面罩",
                "防弹衣",
                "无甲单挂",
                "有甲弹挂",
                "背包",
                "耳机 -防毒面具",
            ],
            "武器配件": [
                "瞄具",
                "弹匣",
                "前握把",
                "后握把",
                "枪托",
                "枪口",
                "镭指器",
                "枪管",
                "护木",
                "机匣&防尘盖",
                "导轨",
                "导气箍",
                "枪栓",
                "手电",
            ],
            "武器枪机": [
                "突击步枪",
                "冲锋枪",
                "霰弹枪",
                "轻机枪",
                "栓动步枪",
                "射手步枪",
                "卡宾枪",
                "手枪",
            ],
            "弹药": [
                "5.45×39毫米子弹",
                "5.56×45毫米子弹",
                "5.7×28毫米子弹",
                "5.8×42毫米子弹",
                "7.62×25毫米子弹",
                "7.62×39毫米子弹",
                "7.62×51毫米子弹",
                "7.62×54毫米子弹",
                "9×19毫米子弹",
                "9×39毫米子弹",
                "12×70毫米子弹",
                ".44口径子弹",
                ".45口径子弹",
                ".338口径子弹",
            ],
            "医疗用品": ["药物", "伤害救治", "医疗包", "药剂"],
            "战术道具": ["投掷物"],
            "钥匙": ["农场钥匙", "北山钥匙", "山谷钥匙", "前线要塞钥匙", "电视台钥匙"],
            "杂物": [
                "易燃物品",
                "建筑材料",
                "电脑配件",
                "能源物品",
                "工具",
                "生活用品",
                "医疗杂物",
                "收藏品",
                "纸制品",
                "仪器仪表",
                "军用杂物",
                "首领信物",
                "电子产品",
            ],
            "饮食": ["饮料", "食品"],
        }

        # 浏览视图相关状态
        self._thumb_cache: dict[str, tk.PhotoImage] = {}
        # 共享路径级缓存：默认占位图等可被多物品复用，减少重复解码
        self._img_cache_by_path: dict[str, tk.PhotoImage] = {}
        self._current_big_cat: str | None = None
        self._current_sub_cat: str | None = None
        self._card_width = 220  # 单卡近似宽度（含边距）
        self._img_preview_photo: tk.PhotoImage | None = None
        self._preview_modal_photo: tk.PhotoImage | None = None

        # 画廊刷新与分批构建的调度控制，降低频繁重建导致的卡顿
        self._gallery_refresh_after: str | None = None
        self._gallery_build_after: str | None = None
        self._gallery_build_token: int = 0
        self._last_cols: int = 0

        self._load_goods()
        self._build_views()

    # ---------- Path helpers ----------
    def _resolve_image_path(self, p: str) -> str:
        """将 goods.image_path 等相对路径解析为绝对路径。

        - 相对路径以 `data/` 根目录为基准（即 `self.images_dir.parent`）。
        - 同时兼容 Windows 反斜杠分隔的相对路径（统一替换为 `/`）。
        """
        p = (p or "").strip()
        if not p:
            return ""
        try:
            pp = Path(p)
            if pp.is_absolute():
                return str(pp)
        except Exception:
            pass
        try:
            norm = p.replace("\\", "/")
        except Exception:
            norm = p
        base = self.images_dir.parent  # data/
        return str((base / norm).resolve())

    # ---------- Storage ----------
    def _load_goods(self) -> None:
        try:
            import json
            if self.goods_path.exists():
                with self.goods_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    self.goods = data
                elif isinstance(data, dict) and isinstance(data.get("items"), list):
                    self.goods = list(data.get("items") or [])
                else:
                    self.goods = []
            else:
                self.goods = []
        except Exception:
            self.goods = []

    def _save_goods(self) -> None:
        try:
            import json
            with self.goods_path.open("w", encoding="utf-8") as f:
                json.dump(self.goods, f, ensure_ascii=False, indent=4)
        except Exception:
            pass

    # ---------- Utils ----------
    def _ensure_default_img(self) -> str:
        path = self.images_dir / "goods" / "_default.png"
        try:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                from PIL import Image, ImageDraw  # type: ignore

                img = Image.new("RGBA", (160, 120), (240, 240, 240, 255))
                dr = ImageDraw.Draw(img)
                dr.rectangle([(0, 0), (159, 119)], outline=(200, 200, 200), width=2)
                try:
                    f = pil_font(16)
                except Exception:
                    f = None
                if f is not None:
                    dr.text((20, 48), "No Image", fill=(120, 120, 120), font=f)
                else:
                    dr.text((20, 48), "No Image", fill=(120, 120, 120))
                img.save(path)
        except Exception:
            pass
        return str(path)

    def _category_dir(self, big_cat: str) -> str:
        slug = self.cat_map_en.get(big_cat) or "misc"
        path = self.images_dir / "goods" / slug
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    def _capture_image(self) -> str | None:
        root = self.winfo_toplevel()
        result_path: str | None = None

        def _done(bounds):
            nonlocal result_path
            if not bounds:
                return
            x1, y1, x2, y2 = bounds
            w, h = max(1, x2 - x1), max(1, y2 - y1)
            try:
                import pyautogui  # type: ignore

                img = pyautogui.screenshot(region=(x1, y1, w, h))
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return

            # decide save dir by big category
            big_cat = self.var_big_cat.get().strip() or "杂物"
            base_dir = self._category_dir(big_cat)
            os.makedirs(base_dir, exist_ok=True)
            fname = f"{uuid.uuid4().hex}.png"
            path = os.path.join(base_dir, fname)
            try:
                img.save(path)
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return
            result_path = path

        sel = RegionSelector(root, _done)
        try:
            sel.show()
        except Exception:
            pass
        # modal-like; actual selection returns via _done then overlay destroys itself
        # We cannot block here; result_path will be set in callback.
        # Provide a small polling to wait until overlay closes
        # but keep UI responsive.
        root.wait_window(sel.top) if getattr(sel, "top", None) else None
        if result_path:
            try:
                result_path = Path(result_path).resolve().relative_to(self.images_dir.parent).as_posix()
            except Exception:
                result_path = os.path.abspath(result_path)
        return result_path

    def _bind_mousewheel(self, area: tk.Widget, target: tk.Widget | None = None) -> None:
        """绑定滚轮事件到指定目标控件，兼容 Windows/macOS/Linux。"""
        if target is None:
            target = area

        def _y_scroll(units: int) -> None:
            try:
                target.yview_scroll(int(units), "units")
            except Exception:
                pass

        def _x_scroll(units: int) -> None:
            try:
                target.xview_scroll(int(units), "units")
            except Exception:
                pass

        def _on_mousewheel(e):
            try:
                delta = int(e.delta)
            except Exception:
                delta = 0
            if delta == 0:
                return
            step = -1 if delta > 0 else 1
            _y_scroll(step)

        def _on_shift_mousewheel(e):
            try:
                delta = int(getattr(e, "delta", 0))
            except Exception:
                delta = 0
            if delta == 0:
                return
            step = -1 if delta > 0 else 1
            _x_scroll(step)

        def _on_linux_up(_e):
            _y_scroll(-1)

        def _on_linux_down(_e):
            _y_scroll(1)

        def _bind_all(_e=None):
            try:
                area.bind_all("<MouseWheel>", _on_mousewheel)
                area.bind_all("<Shift-MouseWheel>", _on_shift_mousewheel)
                area.bind_all("<Button-4>", _on_linux_up)
                area.bind_all("<Button-5>", _on_linux_down)
            except Exception:
                pass

        def _unbind_all(_e=None):
            try:
                area.unbind_all("<MouseWheel>")
                area.unbind_all("<Shift-MouseWheel>")
                area.unbind_all("<Button-4>")
                area.unbind_all("<Button-5>")
            except Exception:
                pass

        try:
            area.bind("<Enter>", _bind_all)
            area.bind("<Leave>", _unbind_all)
        except Exception:
            pass

    # ---------- UI: views ----------
    def _build_views(self) -> None:
        # 采用单视图：浏览 + 卡片管理（编辑/删除在模态框中完成）
        self._build_browse_tab(self)

    # ---------- UI: 浏览（侧栏 + 卡片网格） ----------
    def _build_browse_tab(self, parent) -> None:
        outer = parent
        # 左侧：搜索 + 类目树
        left = ttk.Frame(outer)
        left.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 4), pady=8)

        srow = ttk.Frame(left)
        srow.pack(side=tk.TOP, fill=tk.X)
        ttk.Label(srow, text="搜索").pack(side=tk.LEFT)
        self.var_browse_q = tk.StringVar(value="")
        ent = ttk.Entry(srow, textvariable=self.var_browse_q, width=22)
        ent.pack(side=tk.LEFT, padx=6)
        ent.bind("<Return>", lambda _e: self._schedule_refresh_gallery(0))
        ttk.Button(srow, text="查询", command=lambda: self._schedule_refresh_gallery(0)).pack(side=tk.LEFT)

        # 类目树
        self.cat_tree = ttk.Treeview(left, show="tree", height=24)
        self.cat_tree.pack(side=tk.TOP, fill=tk.Y, expand=True, pady=(8, 0))
        # 根节点：全部
        self.cat_tree.insert("", tk.END, iid="all", text="全部")
        # 填充大类/子类
        for big, subs in self.sub_map.items():
            self.cat_tree.insert("", tk.END, iid=f"b:{big}", text=big)
            for s in subs:
                self.cat_tree.insert(f"b:{big}", tk.END, iid=f"s:{big}:{s}", text=s)
        self.cat_tree.bind("<<TreeviewSelect>>", lambda _e: self._on_cat_select())
        try:
            self.cat_tree.selection_set("all")
        except Exception:
            pass
        # Enable wheel scroll on the category tree
        try:
            self._bind_mousewheel(self.cat_tree, self.cat_tree)
        except Exception:
            pass

        # 右侧：顶部工具条 + 滚动卡片区
        right = ttk.Frame(outer)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(4, 8), pady=8)

        topbar = ttk.Frame(right)
        topbar.pack(side=tk.TOP, fill=tk.X)
        self.lbl_cat_title = ttk.Label(topbar, text="全部")
        self.lbl_cat_title.pack(side=tk.LEFT)
        ttk.Button(topbar, text="新增物品", command=lambda: self._open_item_modal(None)).pack(side=tk.LEFT, padx=(10, 0))
        ttk.Label(topbar, text="排序").pack(side=tk.RIGHT)
        self.var_sort = tk.StringVar(value="默认")
        self.cmb_sort = ttk.Combobox(topbar, width=12, state="readonly",
                                     values=["默认", "按名称"] ,
                                     textvariable=self.var_sort)
        self.cmb_sort.pack(side=tk.RIGHT, padx=(0, 6))
        self.cmb_sort.bind("<<ComboboxSelected>>", lambda _e: self._schedule_refresh_gallery(50))

        # Canvas + Scrollbar 包裹网格卡片
        wrapper = ttk.Frame(right)
        wrapper.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(6, 0))
        self.gallery_canvas = tk.Canvas(wrapper, highlightthickness=0)
        vsb = ttk.Scrollbar(wrapper, orient=tk.VERTICAL, command=self.gallery_canvas.yview)
        self.gallery_canvas.configure(yscrollcommand=vsb.set)
        self.gallery_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        self.gallery_inner = ttk.Frame(self.gallery_canvas)
        self.gallery_window = self.gallery_canvas.create_window(0, 0, anchor=tk.NW, window=self.gallery_inner)

        def _on_inner_config(_e=None):
            # 更新滚动区域
            try:
                self.gallery_canvas.configure(scrollregion=self.gallery_canvas.bbox("all"))
            except Exception:
                pass

        def _on_canvas_config(_e=None):
            # 撑满宽度并重排
            try:
                w = self.gallery_canvas.winfo_width()
                self.gallery_canvas.itemconfigure(self.gallery_window, width=w)
            except Exception:
                pass
            # 仅当列数发生变化时刷新，避免频繁重建
            try:
                cols_now = max(1, int(max(1, w) // self._card_width))
            except Exception:
                cols_now = 1
            if cols_now != self._last_cols:
                self._last_cols = cols_now
                self._schedule_refresh_gallery(50)

        self.gallery_inner.bind("<Configure>", _on_inner_config)
        self.gallery_canvas.bind("<Configure>", _on_canvas_config)
        # Enable wheel scroll over the gallery area
        try:
            self._bind_mousewheel(self.gallery_inner, self.gallery_canvas)
        except Exception:
            pass

        # 初次渲染
        self.after(50, lambda: self._schedule_refresh_gallery(0))

    # ---------- 浏览事件 & 渲染 ----------
    def _on_cat_select(self) -> None:
        sel = self.cat_tree.selection()
        if not sel:
            return
        iid = sel[0]
        if iid == "all":
            self._current_big_cat = None
            self._current_sub_cat = None
            self.lbl_cat_title.configure(text="全部")
        elif iid.startswith("s:"):
            _p, big, sub = iid.split(":", 2)
            self._current_big_cat = big
            self._current_sub_cat = sub
            self.lbl_cat_title.configure(text=f"{big} / {sub}")
        elif iid.startswith("b:"):
            _p, big = iid.split(":", 1)
            self._current_big_cat = big
            self._current_sub_cat = None
            self.lbl_cat_title.configure(text=big)
        self._schedule_refresh_gallery(0)

    def _filtered_goods_for_gallery(self) -> list[dict]:
        q = (self.var_browse_q.get() or "").strip().lower()
        items = [it for it in (self.goods or []) if isinstance(it, dict)]
        res: list[dict] = []
        for it in items:
            if self._current_big_cat and str(it.get("big_category", "")) != self._current_big_cat:
                continue
            if self._current_sub_cat and str(it.get("sub_category", "")) != self._current_sub_cat:
                continue
            if q:
                name = str(it.get("name", "")).lower()
                sname = str(it.get("search_name", "")).lower()
                if q not in name and q not in sname:
                    continue
            res.append(it)

        sort = (self.var_sort.get() or "默认").strip()
        if sort == "按名称":
            res.sort(key=lambda x: str(x.get("name", "")))
        return res

    def _schedule_refresh_gallery(self, delay_ms: int = 0) -> None:
        """延迟刷新画廊，合并短时间内的多次触发，降低卡顿。"""
        try:
            if self._gallery_refresh_after:
                self.after_cancel(self._gallery_refresh_after)
        except Exception:
            pass
        self._gallery_refresh_after = self.after(max(0, int(delay_ms)), self._refresh_gallery)

    def _refresh_gallery(self) -> None:
        # 取消在途分批构建任务
        try:
            if self._gallery_build_after:
                self.after_cancel(self._gallery_build_after)
        except Exception:
            pass
        self._gallery_build_after = None

        # 清空后分批重建卡片网格，避免主线程长时间阻塞
        for w in self.gallery_inner.winfo_children():
            w.destroy()

        items = self._filtered_goods_for_gallery()
        # 估算列数
        try:
            cw = max(1, self.gallery_canvas.winfo_width())
        except Exception:
            cw = 800
        col_w = max(1, self._card_width)
        cols = max(1, cw // col_w)
        self._last_cols = cols
        # 让每列等宽
        for c in range(cols):
            try:
                self.gallery_inner.grid_columnconfigure(c, weight=1)
            except Exception:
                pass

        batch = 24
        total = len(items)
        token = self._gallery_build_token = (self._gallery_build_token + 1) % 1_000_000

        def _build(i: int) -> None:
            # 若已启动新一轮刷新，停止旧批次
            if token != self._gallery_build_token:
                return
            end = min(i + batch, total)
            for idx in range(i, end):
                it = items[idx]
                r, c = divmod(idx, cols)
                card = self._build_card(self.gallery_inner, it)
                card.grid(row=r, column=c, padx=6, pady=6, sticky="nsew")
            if end < total:
                self._gallery_build_after = self.after(0, lambda: _build(end))
            else:
                self._gallery_build_after = None

        _build(0)

    def _build_card(self, parent, it: dict) -> ttk.Frame:
        if not isinstance(it, dict):
            return ttk.Frame(parent)

        frm = ttk.Frame(parent, relief=tk.SOLID, borderwidth=1)

        # 头部：操作按钮（编辑/删除）
        head = ttk.Frame(frm)
        head.pack(side=tk.TOP, fill=tk.X)
        # 右上：快速截图（仅默认图时显示）+ 编辑/删除
        try:
            img_path_raw = str(it.get("image_path", "")).strip()
            # 视为“默认图”的条件：未配置 或 解析后等于默认占位图
            img_abs = self._resolve_image_path(img_path_raw) if img_path_raw else ""
            is_default_img = (not img_path_raw) or (img_abs == self._ensure_default_img())
        except Exception:
            is_default_img = False
        if is_default_img:
            btn_cap = ttk.Button(
                head,
                text="📷",
                width=2,
                command=lambda it_=dict(it): self._quick_capture_item_image(it_),
            )
            btn_cap.pack(side=tk.RIGHT, padx=(2, 2), pady=2)
        # 历史价格入口（右上角）
        try:
            ttk.Button(head, text="📈", width=2, command=lambda it_=dict(it): self._open_price_history_for_goods(it_)).pack(side=tk.RIGHT, padx=(2, 2), pady=2)
        except Exception:
            pass
        btn_edit = ttk.Button(head, text="✎", width=2,
                              command=lambda it_=it: self._open_item_modal(dict(it_)))
        btn_edit.pack(side=tk.RIGHT, padx=(2, 2), pady=2)
        # 按需保留“删除”仅在管理界面/编辑对话框中提供，浏览卡片不再提供删除按钮

        # 图片
        cnv = tk.Canvas(frm, width=180, height=130, bg="#f0f0f0", highlightthickness=0)
        cnv.pack(side=tk.TOP, padx=8, pady=(0, 4))
        tkimg = self._thumb_for_item(it)
        if tkimg:
            cnv.create_image(90, 65, image=tkimg)
            cnv.image = tkimg

        # 名称 + 分类
        name = str(it.get("name", ""))
        ttk.Label(frm, text=name, wraplength=200, justify=tk.LEFT).pack(side=tk.TOP, padx=8)
        cat_txt = f"{it.get('big_category','')}/{it.get('sub_category','')}".strip("/")
        ttk.Label(frm, text=cat_txt, foreground="#666").pack(side=tk.TOP, anchor="w", padx=8)

        # 底栏：最近1天统计 + 当前价（可选）
        hi, lo, avg = self._price_stats_1d(str(it.get("id", "")))
        stats = f"1d 高:{hi if hi>0 else '—'} 低:{lo if lo>0 else '—'} 均:{avg if avg>0 else '—'}"
        footer = ttk.Frame(frm)
        footer.pack(side=tk.TOP, fill=tk.X, pady=(2, 6))
        ttk.Label(footer, text=stats).pack(side=tk.LEFT, padx=8)
        
        return frm

    def _open_price_history_for_goods(self, it: dict) -> None:
        try:
            from history_store import query_price, query_price_minutely  # type: ignore
        except Exception:
            messagebox.showwarning("历史价格", "历史模块不可用。")
            return
        name = str(it.get("name", ""))
        item_id = str(it.get("id", ""))
        if not item_id:
            return
        top = tk.Toplevel(self)
        top.title(f"历史价格 - {name}")
        top.geometry("720x420")
        top.transient(self)
        try:
            top.grab_set()
        except Exception:
            pass
        # Controls
        ctrl = ttk.Frame(top)
        ctrl.pack(fill=tk.X, padx=8, pady=6)
        ttk.Label(ctrl, text="时间范围").pack(side=tk.LEFT)
        rng_var = tk.StringVar(value="近1天")
        cmb = ttk.Combobox(ctrl, textvariable=rng_var, state="readonly",
                           values=["近1小时", "近1天", "近7天", "近1月"], width=10)
        cmb.pack(side=tk.LEFT, padx=6)
        lbl_stats = ttk.Label(ctrl, text="")
        lbl_stats.pack(side=tk.RIGHT)
        # Figure area
        figf = ttk.Frame(top)
        figf.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        def _sec_for_label(s: str) -> int:
            return {
                "近1小时": 3600,
                "近1天": 86400,
                "近7天": 7 * 86400,
                "近1月": 30 * 86400,
            }.get(s, 86400)
        def _render():
            # Lazy import
            try:
                import matplotlib
                matplotlib.use("TkAgg")
                import matplotlib.pyplot as plt  # type: ignore
                from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # type: ignore
                import matplotlib.dates as mdates  # type: ignore
                import matplotlib.ticker as mtick  # type: ignore
                from datetime import datetime
                import time as _time
                try:
                    setup_matplotlib_chinese()
                except Exception:
                    pass
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return
            for w in figf.winfo_children():
                try:
                    w.destroy()
                except Exception:
                    pass
            sec = _sec_for_label(rng_var.get())
            since = _time.time() - sec
            # Prefer minutely aggregate
            x = []
            y_avg = []
            y_min = []
            y_max = []
            try:
                recs_m = query_price_minutely(item_id, since)
            except Exception:
                recs_m = []
            if recs_m:
                for r in recs_m:
                    try:
                        ts = float(r.get("ts_min", 0.0))
                        vmin = int(r.get("min", 0))
                        vmax = int(r.get("max", 0))
                        vavg = int(r.get("avg", 0))
                    except Exception:
                        continue
                    x.append(datetime.fromtimestamp(ts))
                    y_min.append(vmin)
                    y_max.append(vmax)
                    y_avg.append(vavg)
            else:
                recs = query_price(item_id, since)
                for r in recs:
                    try:
                        ts = float(r.get("ts", 0.0))
                        pr = int(r.get("price", 0))
                    except Exception:
                        continue
                    x.append(datetime.fromtimestamp(ts))
                    y_avg.append(pr)
                    y_min.append(pr)
                    y_max.append(pr)
            fig = plt.Figure(figsize=(6.4, 3.4), dpi=100)
            ax = fig.add_subplot(111)
            if x and y_avg:
                ax.plot_date(x, y_avg, "-", linewidth=1.5, label="平均价")
                try:
                    ax.fill_between(x, y_min, y_max, color="#90CAF9", alpha=0.25, label="区间[最低,最高]")
                except Exception:
                    pass
                ax.set_title(name)
                ax.set_ylabel("价格")
                ax.grid(True, linestyle=":", alpha=0.4)
                ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
                def _fmt_tick(v, _p):
                    try:
                        v = float(v)
                    except Exception:
                        return str(v)
                    if abs(v) >= 1_000_000:
                        return f"{v/1_000_000:.1f}M"
                    if abs(v) >= 1_000:
                        return f"{v/1_000:.1f}K"
                    try:
                        return f"{int(v):,}"
                    except Exception:
                        return str(v)
                ax.yaxis.set_major_formatter(mtick.FuncFormatter(_fmt_tick))
                fig.autofmt_xdate()
                mn = min(y_min) if y_min else 0
                mx = max(y_max) if y_max else 0
                try:
                    lbl_stats.configure(text=f"最高价: {mx:,}    最低价: {mn:,}")
                except Exception:
                    lbl_stats.configure(text=f"最高价: {mx}    最低价: {mn}")
                try:
                    ax.legend(loc="upper right")
                except Exception:
                    pass
            else:
                ax.set_title("暂无数据")
                lbl_stats.configure(text="")
            canvas = FigureCanvasTkAgg(fig, master=figf)
            canvas.draw()
            canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        cmb.bind("<<ComboboxSelected>>", lambda _e: _render())
        _render()
        btnf = ttk.Frame(top)
        btnf.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(btnf, text="关闭", command=top.destroy).pack(side=tk.RIGHT)

    def _thumb_for_item(self, it: dict) -> tk.PhotoImage | None:
        iid = str(it.get("id", ""))
        path_raw = str(it.get("image_path", "")) or self._ensure_default_img()
        path = self._resolve_image_path(path_raw)
        if not iid:
            return None
        if iid in self._thumb_cache:
            return self._thumb_cache[iid]
        if path in self._img_cache_by_path:
            tkimg = self._img_cache_by_path[path]
            self._thumb_cache[iid] = tkimg
            return tkimg
        try:
            from PIL import Image, ImageTk  # type: ignore

            im = Image.open(path)
            im.thumbnail((180, 130))
            tkimg = ImageTk.PhotoImage(im)
        except Exception:
            # 回退默认图（确保有缩略图可显示）
            try:
                im = Image.open(self._ensure_default_img())
                im.thumbnail((180, 130))
                tkimg = ImageTk.PhotoImage(im)
            except Exception:
                return None
        self._thumb_cache[iid] = tkimg
        self._img_cache_by_path[path] = tkimg
        return tkimg

    def _quick_capture_item_image(self, item: dict) -> None:
        """在卡片上执行快速截图（与“测试”页截取样式一致）。

        - 使用卡片样式固定框 165x212（上 20 / 下 30），中间图片区域左右 30、上下 20。
        - 仅截取中间图片区域并保存到对应大类目录，更新该物品的 `image_path`。
        - 刷新画廊并清理该物品缩略图缓存。
        """
        iid = str(item.get("id", ""))
        if not iid:
            return

        root = self.winfo_toplevel()
        result_path: str | None = None

        def _done(bounds):
            nonlocal result_path
            if not bounds:
                return
            # 卡片整体坐标（根据卡片样式 165x212 推导中间图片区域）
            x1, y1, x2, y2 = bounds
            # 固定样式：若用户改变了外框大小，仍按 165x212 的比例与边距推导中间区域
            CARD_W, CARD_H = 165, 212
            TOP_H, BTM_H = 20, 30
            MID_H = CARD_H - TOP_H - BTM_H  # 162
            MARG_LR, MARG_TB = 30, 20

            # 以左上角为基准，推导中间图片区域（不因拖拽尺寸变化而改变）
            ix = int(x1 + MARG_LR)
            iy = int(y1 + TOP_H + MARG_TB)
            iw = int(CARD_W - 2 * MARG_LR)
            ih = int(MID_H - 2 * MARG_TB)

            # 屏幕裁剪
            try:
                W, H = root.winfo_screenwidth(), root.winfo_screenheight()
            except Exception:
                W = H = 10**6
            ix = max(0, min(ix, max(0, W - 1)))
            iy = max(0, min(iy, max(0, H - 1)))
            iw = max(1, min(iw, W - ix))
            ih = max(1, min(ih, H - iy))

            try:
                import pyautogui  # type: ignore
                img = pyautogui.screenshot(region=(ix, iy, iw, ih))
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return

            # 保存到对应大类目录
            big_cat = str(item.get("big_category", "") or "杂物").strip()
            base_dir = self._category_dir(big_cat)
            try:
                os.makedirs(base_dir, exist_ok=True)
            except Exception:
                pass
            fname = f"{uuid.uuid4().hex}.png"
            path = os.path.join(base_dir, fname)
            try:
                img.save(path)
            except Exception as e:
                messagebox.showerror("选图片", f"失败: {e}")
                return
            result_path = path

        # 使用与“测试”页一致的卡片选择器样式
        sel = _CardSelector(root, _done, w=165, h=212, top_h=20, bottom_h=30, margin_lr=30, margin_tb=20)
        try:
            sel.show()
        except Exception:
            pass
        root.wait_window(sel.top) if getattr(sel, "top", None) else None

        if not result_path:
            return

        # 更新该物品的图片路径
        for i, g in enumerate(self.goods):
            if str(g.get("id", "")) == iid:
                g = dict(g)
                g["image_path"] = result_path
                self.goods[i] = g
                break
        else:
            return

        # 持久化与刷新
        self._save_goods()
        try:
            self._thumb_cache.pop(iid, None)
        except Exception:
            pass
        self._schedule_refresh_gallery(0)

    # 收藏功能已移除

    # ---------- 数据：价格统计（最近1天） ----------
    def _price_stats_1d(self, iid: str) -> tuple[int, int, int]:
        if not iid:
            return 0, 0, 0
        # 简易缓存，避免频繁 I/O
        now = time.time()
        cache = getattr(self, "_price_cache", None)
        if cache is None:
            cache = {}
            self._price_cache = cache  # type: ignore
        ent = cache.get(iid) if isinstance(cache, dict) else None
        if isinstance(ent, tuple) and len(ent) == 2:
            ts, val = ent
            if now - float(ts) <= 10.0:
                return tuple(val)  # type: ignore
        try:
            from history_store import query_price  # type: ignore
        except Exception:
            return 0, 0, 0
        since = now - 24 * 3600
        try:
            recs = query_price(iid, since)
        except Exception:
            recs = []
        prices = [int(r.get("price", 0)) for r in (recs or []) if int(r.get("price", 0)) > 0]
        if not prices:
            hi = lo = avg = 0
        else:
            hi = max(prices)
            lo = min(prices)
            avg = int(round(sum(prices) / len(prices)))
        cache[iid] = (now, (hi, lo, avg))  # type: ignore
        return hi, lo, avg

    # ---------- 管理模态框 ----------
    def _open_item_modal(self, item: dict | None) -> None:
        top = tk.Toplevel(self)
        top.title("物品管理")
        top.geometry("560x420")
        top.transient(self)
        try:
            top.grab_set()
        except Exception:
            pass

        # 变量（局部，不污染主界面变量）
        var_id = tk.StringVar(value=str(item.get("id", "")) if item else "")
        var_name = tk.StringVar(value=str(item.get("name", "")) if item else "")
        var_sname = tk.StringVar(value=str(item.get("search_name", "")) if item else "")
        var_big = tk.StringVar(value=str(item.get("big_category", "")) if item else "弹药")
        var_sub = tk.StringVar(value=str(item.get("sub_category", "")) if item else "")
        var_ex = tk.BooleanVar(value=bool(item.get("exchangeable", False)) if item else False)
        var_cf = tk.BooleanVar(value=bool(item.get("craftable", False)) if item else False)
        var_img = tk.StringVar(value=str(item.get("image_path", "")) if item and item.get("image_path") else self._ensure_default_img())

        # 表单布局
        frm = ttk.Frame(top)
        frm.pack(fill=tk.BOTH, expand=True, padx=10, pady=8)

        # 图片
        row0 = ttk.Frame(frm)
        row0.grid(row=0, column=0, columnspan=4, sticky="we")
        ttk.Label(row0, text="图片").pack(side=tk.LEFT)
        cnv = tk.Canvas(row0, width=140, height=100, bg="#f0f0f0")
        cnv.pack(side=tk.LEFT, padx=8)

        def _update_preview():
            cnv.delete("all")
            p_raw = (var_img.get() or "").strip()
            p = self._resolve_image_path(p_raw)
            if not p or not os.path.exists(p):
                # 统一回退默认占位图
                p = self._ensure_default_img()
            try:
                from PIL import Image, ImageTk  # type: ignore

                im = Image.open(p)
                im.thumbnail((140, 100))
                tkimg = ImageTk.PhotoImage(im)
            except Exception:
                return
            self._preview_modal_photo = tkimg
            cnv.create_image(0, 0, anchor=tk.NW, image=tkimg)

        def _capture_to_cat():
            # 卡片样式截取（165x212；上 20 / 下 30；中间图片区域左右 30、上下 20）
            root = self.winfo_toplevel()
            result_path: str | None = None

            def _done(bounds):
                nonlocal result_path
                if not bounds:
                    return
                # 按“测试”页样式从卡片整体推导中间图片区域
                x1, y1, x2, y2 = bounds
                CARD_W, CARD_H = 165, 212
                TOP_H, BTM_H = 20, 30
                MID_H = CARD_H - TOP_H - BTM_H
                MARG_LR, MARG_TB = 30, 20

                ix = int(x1 + MARG_LR)
                iy = int(y1 + TOP_H + MARG_TB)
                iw = int(CARD_W - 2 * MARG_LR)
                ih = int(MID_H - 2 * MARG_TB)

                # 屏幕裁剪
                try:
                    W, H = root.winfo_screenwidth(), root.winfo_screenheight()
                except Exception:
                    W = H = 10**6
                ix = max(0, min(ix, max(0, W - 1)))
                iy = max(0, min(iy, max(0, H - 1)))
                iw = max(1, min(iw, W - ix))
                ih = max(1, min(ih, H - iy))

                try:
                    import pyautogui  # type: ignore
                    img = pyautogui.screenshot(region=(ix, iy, int(iw), int(ih)))
                except Exception as e:
                    messagebox.showerror("选图片", f"失败: {e}")
                    return

                # 保存到对应大类
                big_cat = var_big.get().strip() or "misc"
                base_dir = self._category_dir(big_cat)
                try:
                    os.makedirs(base_dir, exist_ok=True)
                except Exception:
                    pass
                fname = f"{uuid.uuid4().hex}.png"
                path = os.path.join(base_dir, fname)
                try:
                    img.save(path)
                except Exception as e:
                    messagebox.showerror("选图片", f"失败: {e}")
                    return
                result_path = path

            sel = _CardSelector(root, _done, w=165, h=212, top_h=20, bottom_h=30, margin_lr=30, margin_tb=20)
            try:
                sel.show()
            except Exception:
                pass
            root.wait_window(sel.top) if getattr(sel, "top", None) else None
            if result_path:
                try:
                    rel = Path(result_path).resolve().relative_to(self.images_dir.parent)
                    var_img.set(rel.as_posix())
                except Exception:
                    var_img.set(result_path)
                _update_preview()

        ttk.Button(row0, text="截图", command=_capture_to_cat).pack(side=tk.LEFT, padx=6)

        ttk.Label(frm, text="名称").grid(row=1, column=0, sticky="e", padx=4, pady=6)
        ttk.Entry(frm, textvariable=var_name, width=28).grid(row=1, column=1, sticky="w")
        ttk.Label(frm, text="搜索名").grid(row=1, column=2, sticky="e", padx=4)
        ttk.Entry(frm, textvariable=var_sname, width=18).grid(row=1, column=3, sticky="w")

        ttk.Label(frm, text="大分类").grid(row=2, column=0, sticky="e", padx=4, pady=6)
        cmb_big = ttk.Combobox(frm, textvariable=var_big, state="readonly", width=14,
                               values=list(self.cat_map_en.keys()))
        cmb_big.grid(row=2, column=1, sticky="w")
        ttk.Label(frm, text="子分类").grid(row=2, column=2, sticky="e", padx=4)
        cmb_sub = ttk.Combobox(frm, textvariable=var_sub, state="readonly", width=18)
        cmb_sub.grid(row=2, column=3, sticky="w")

        def _fill_sub():
            try:
                cmb_sub.configure(values=self.sub_map.get(var_big.get().strip(), []) or [])
            except Exception:
                pass

        cmb_big.bind("<<ComboboxSelected>>", lambda _e: _fill_sub())
        _fill_sub()

        ttk.Checkbutton(frm, text="当前赛季可兑换", variable=var_ex).grid(row=3, column=0, columnspan=2, sticky="w", padx=4)
        ttk.Checkbutton(frm, text="当前赛季可制造", variable=var_cf).grid(row=3, column=2, columnspan=2, sticky="w", padx=4)

        # 操作按钮
        btns = ttk.Frame(frm)
        btns.grid(row=4, column=0, columnspan=4, sticky="we", pady=10)

        def _do_save():
            name = (var_name.get() or "").strip()
            if not name:
                messagebox.showwarning("保存", "名称不能为空。")
                return
            iid = (var_id.get() or "").strip() or uuid.uuid4().hex
            item2 = {
                "id": iid,
                "name": name,
                "search_name": (var_sname.get() or "").strip(),
                "big_category": var_big.get().strip(),
                "sub_category": var_sub.get().strip(),
                "exchangeable": bool(var_ex.get()),
                "craftable": bool(var_cf.get()),
                "image_path": (var_img.get() or "").strip(),
                # 可选字段：价格（若已有则保留）
                "price": item.get("price") if item and "price" in item else None,
            }
            found = False
            for i, g in enumerate(self.goods):
                if str(g.get("id", "")) == iid:
                    self.goods[i] = item2
                    found = True
                    break
            if not found:
                self.goods.append(item2)
            self._save_goods()
            self._refresh_gallery()
            try:
                top.grab_release()
            except Exception:
                pass
            top.destroy()

        def _do_delete():
            iid = (var_id.get() or "").strip()
            if not iid:
                return
            self._delete_item(iid)
            try:
                top.grab_release()
            except Exception:
                pass
            top.destroy()

        ttk.Button(btns, text="保存", command=_do_save).pack(side=tk.RIGHT)
        ttk.Button(btns, text="取消", command=lambda: top.destroy()).pack(side=tk.RIGHT, padx=6)
        if item and item.get("id"):
            ttk.Button(btns, text="删除", command=_do_delete).pack(side=tk.LEFT)

        # 首次预览
        _update_preview()

    def _delete_item(self, iid: str) -> None:
        if not iid:
            return
        it = next((x for x in self.goods if str(x.get("id")) == iid), None)
        if not it:
            return
        if not messagebox.askokcancel("删除", f"确认删除 [{it.get('name','')}]？"):
            return
        img_path = self._resolve_image_path(str(it.get("image_path", "")))
        self.goods = [x for x in self.goods if str(x.get("id")) != iid]
        self._save_goods()
        self._refresh_gallery()
        if img_path and os.path.exists(img_path):
            if messagebox.askyesno("删除图片", "同时删除对应图片文件？"):
                try:
                    os.remove(img_path)
                except Exception:
                    pass

    # ---------- UI: 管理（原有表格 + 表单） ----------
    def _build_manage_tab(self, parent) -> None:
        outer = parent
        # top: search + actions
        top = ttk.Frame(outer)
        top.pack(fill=tk.X, padx=8, pady=(8, 4))
        ttk.Label(top, text="搜索").pack(side=tk.LEFT)
        self.var_q = tk.StringVar(value="")
        ent = ttk.Entry(top, textvariable=self.var_q, width=24)
        ent.pack(side=tk.LEFT, padx=6)
        ttk.Button(top, text="查询", command=self._refresh_list).pack(side=tk.LEFT)
        ttk.Button(top, text="重置", command=self._reset_search).pack(side=tk.LEFT, padx=(6, 0))

        # center: list + form
        center = ttk.Frame(outer)
        center.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        # list
        lf = ttk.Frame(center)
        lf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        cols = ("name", "sname", "bcat", "scat", "exch", "craft")
        self.tree = ttk.Treeview(lf, columns=cols, show="headings", height=18)
        self.tree.heading("name", text="名称")
        self.tree.heading("sname", text="搜索名")
        self.tree.heading("bcat", text="大分类")
        self.tree.heading("scat", text="子分类")
        self.tree.heading("exch", text="可兑换")
        self.tree.heading("craft", text="可制造")
        self.tree.column("name", width=200)
        self.tree.column("sname", width=90)
        self.tree.column("bcat", width=90)
        self.tree.column("scat", width=140)
        self.tree.column("exch", width=70, anchor="center")
        self.tree.column("craft", width=70, anchor="center")
        self.tree.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._on_select())

        btns = ttk.Frame(lf)
        btns.pack(side=tk.TOP, fill=tk.X, pady=6)
        ttk.Button(btns, text="新增", command=self._new_item).pack(side=tk.LEFT)
        ttk.Button(btns, text="保存", command=self._save_current).pack(side=tk.LEFT, padx=6)
        ttk.Button(btns, text="删除", command=self._delete_selected).pack(side=tk.LEFT)

        # form
        rf = ttk.LabelFrame(center, text="物品信息")
        rf.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 0))
        # variables
        self.var_id = tk.StringVar(value="")
        self.var_name = tk.StringVar(value="")
        self.var_sname = tk.StringVar(value="")
        self.var_big_cat = tk.StringVar(value="弹药")
        self.var_sub_cat = tk.StringVar(value="")
        self.var_exch = tk.BooleanVar(value=False)
        self.var_craft = tk.BooleanVar(value=False)
        self.var_img = tk.StringVar(value=self._ensure_default_img())

        # row 0: image preview + capture
        img_row = ttk.Frame(rf)
        img_row.grid(row=0, column=0, columnspan=4, sticky="we", pady=(6, 2))
        ttk.Label(img_row, text="图片").pack(side=tk.LEFT)
        self.img_preview_canvas = tk.Canvas(img_row, width=120, height=90, bg="#f0f0f0")
        self.img_preview_canvas.pack(side=tk.LEFT, padx=8)
        ttk.Button(img_row, text="截图", command=self._on_capture_img).pack(side=tk.LEFT)
        ttk.Button(img_row, text="预览", command=lambda: self._preview_image(self.var_img.get(), "预览 - 物品图片")).pack(side=tk.LEFT, padx=6)

        ttk.Label(rf, text="名称").grid(row=1, column=0, sticky="e", padx=4, pady=4)
        ttk.Entry(rf, textvariable=self.var_name, width=30).grid(row=1, column=1, sticky="w")
        ttk.Label(rf, text="搜索名").grid(row=1, column=2, sticky="e", padx=4)
        ttk.Entry(rf, textvariable=self.var_sname, width=16).grid(row=1, column=3, sticky="w")

        ttk.Label(rf, text="大分类").grid(row=2, column=0, sticky="e", padx=4, pady=4)
        self.cmb_big = ttk.Combobox(rf, textvariable=self.var_big_cat, state="readonly", width=14,
                                    values=list(self.cat_map_en.keys()))
        self.cmb_big.grid(row=2, column=1, sticky="w")
        self.cmb_big.bind("<<ComboboxSelected>>", lambda _e: self._fill_subcats())
        ttk.Label(rf, text="子分类").grid(row=2, column=2, sticky="e", padx=4)
        self.cmb_sub = ttk.Combobox(rf, textvariable=self.var_sub_cat, state="readonly", width=18)
        self.cmb_sub.grid(row=2, column=3, sticky="w")

        ttk.Checkbutton(rf, text="当前赛季可兑换", variable=self.var_exch).grid(row=3, column=0, columnspan=2, sticky="w", padx=4, pady=4)
        ttk.Checkbutton(rf, text="当前赛季可制造", variable=self.var_craft).grid(row=3, column=2, columnspan=2, sticky="w", padx=4)

        for i in range(4):
            rf.columnconfigure(i, weight=0)

        self._fill_subcats()
        self._update_img_preview()

    # ---------- Events ----------
    def _reset_search(self) -> None:
        self.var_q.set("")
        self._refresh_list()

    def _filter_goods(self) -> list[dict]:
        q = (self.var_q.get() or "").strip().lower()
        items = self.goods or []
        if not q:
            return items
        res = []
        for it in items:
            name = str(it.get("name", "")).lower()
            sname = str(it.get("search_name", "")).lower()
            if q in name or q in sname:
                res.append(it)
        return res

    def _refresh_list(self) -> None:
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for it in self._filter_goods():
            vals = (
                str(it.get("name", "")),
                str(it.get("search_name", "")),
                str(it.get("big_category", "")),
                str(it.get("sub_category", "")),
                "是" if bool(it.get("exchangeable", False)) else "否",
                "是" if bool(it.get("craftable", False)) else "否",
            )
            self.tree.insert("", tk.END, iid=str(it.get("id", "")), values=vals)

    def _on_select(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        it = next((x for x in self.goods if str(x.get("id")) == iid), None)
        if not it:
            return
        self.var_id.set(str(it.get("id", "")))
        self.var_name.set(str(it.get("name", "")))
        self.var_sname.set(str(it.get("search_name", "")))
        self.var_big_cat.set(str(it.get("big_category", "")) or "杂物")
        self._fill_subcats()
        self.var_sub_cat.set(str(it.get("sub_category", "")))
        self.var_exch.set(bool(it.get("exchangeable", False)))
        self.var_craft.set(bool(it.get("craftable", False)))
        self.var_img.set(str(it.get("image_path", "")) or self._ensure_default_img())
        self._update_img_preview()

    def _new_item(self) -> None:
        self.var_id.set("")
        self.var_name.set("")
        self.var_sname.set("")
        self.var_big_cat.set("弹药")
        self._fill_subcats()
        self.var_sub_cat.set("")
        self.var_exch.set(False)
        self.var_craft.set(False)
        self.var_img.set(self._ensure_default_img())
        self._update_img_preview()

    def _save_current(self) -> None:
        name = (self.var_name.get() or "").strip()
        if not name:
            messagebox.showwarning("保存", "名称不能为空。")
            return
        iid = (self.var_id.get() or "").strip()
        item = {
            "id": iid or uuid.uuid4().hex,
            "name": name,
            "search_name": (self.var_sname.get() or "").strip(),
            "big_category": self.var_big_cat.get().strip(),
            "sub_category": self.var_sub_cat.get().strip(),
            "exchangeable": bool(self.var_exch.get()),
            "craftable": bool(self.var_craft.get()),
            "image_path": (self.var_img.get() or "").strip(),
        }
        existed = False
        for i, g in enumerate(self.goods):
            if str(g.get("id", "")) == item["id"]:
                self.goods[i] = item
                existed = True
                break
        if not existed:
            self.goods.append(item)
        self.var_id.set(str(item["id"]))
        self._save_goods()
        self._refresh_list()
        messagebox.showinfo("保存", "已保存。")

    def _delete_selected(self) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        iid = sel[0]
        it = next((x for x in self.goods if str(x.get("id")) == iid), None)
        if not it:
            return
        if not messagebox.askokcancel("删除", f"确认删除 [{it.get('name','')}]？"):
            return
        img_path = self._resolve_image_path(str(it.get("image_path", "")))
        self.goods = [x for x in self.goods if str(x.get("id")) != iid]
        self._save_goods()
        self._refresh_list()
        self._new_item()
        if img_path and os.path.exists(img_path):
            # ask whether to delete image file
            if messagebox.askyesno("删除图片", "同时删除对应图片文件？"):
                try:
                    os.remove(img_path)
                except Exception:
                    pass

    def _fill_subcats(self) -> None:
        b = self.var_big_cat.get().strip()
        vals = self.sub_map.get(b) or []
        try:
            self.cmb_sub.configure(values=vals)
        except Exception:
            pass

    def _update_img_preview(self) -> None:
        self.img_preview_canvas.delete("all")
        path = self._resolve_image_path((self.var_img.get() or "").strip())
        if not path or not os.path.exists(path):
            # 统一回退默认占位图
            path = self._ensure_default_img()
        try:
            from PIL import Image, ImageTk  # type: ignore

            im = Image.open(path)
            im.thumbnail((120, 90))
            tkimg = ImageTk.PhotoImage(im)
        except Exception:
            return
        self._img_preview_photo = tkimg
        self.img_preview_canvas.create_image(0, 0, anchor=tk.NW, image=tkimg)

    def _on_capture_img(self) -> None:
        p = self._capture_image()
        if p:
            self.var_img.set(p)
            self._update_img_preview()

    # ---------- Local image preview ----------
    def _preview_image(self, path: str, title: str = "预览") -> None:
        p = self._resolve_image_path((path or "").strip())
        if not p or not os.path.exists(p):
            # 统一回退默认占位图，而不是直接报错
            p = self._ensure_default_img()
        top = tk.Toplevel(self)
        top.title(title)
        top.geometry("560x420")
        top.transient(self)
        try:
            top.grab_set()
        except Exception:
            pass
        cv = tk.Canvas(top, bg="#222", highlightthickness=0)
        cv.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        try:
            from PIL import Image, ImageTk  # type: ignore

            img = Image.open(p)
            img.thumbnail((520, 380))
            tkimg = ImageTk.PhotoImage(img)
        except Exception as e:
            top.destroy()
            messagebox.showerror("预览", f"失败: {e}")
            return
        self._preview_modal_photo = tkimg
        img_w, img_h = tkimg.width(), tkimg.height()
        cv.configure(scrollregion=(0, 0, img_w, img_h))
        cv.create_image(0, 0, anchor=tk.NW, image=tkimg)
        ttk.Button(top, text="关闭", command=top.destroy).pack(pady=(0, 8))
