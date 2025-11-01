"""
通用“选择商品”弹窗组件。

- 优先使用“物品市场”页的内存数据；否则回落读取 `paths.root/goods.json`。
- 列表展示三列：名称/大类/子类。
- on_pick 回调统一输出字段：name、id、image_path、big_category。
"""

from __future__ import annotations

from typing import Callable, Iterable, List, Optional, Dict, Any
from pathlib import Path

import tkinter as tk
from tkinter import ttk

try:
    from tkinter import messagebox  # noqa: F401  # 便于后续扩展错误提示
except Exception:  # pragma: no cover - 运行环境可能无 messagebox
    messagebox = None  # type: ignore

from super_buyer.config.loader import ConfigPaths


def _normalize_goods(records: Iterable[dict]) -> list[dict]:
    """规范化 goods 记录为字典列表（跳过非字典值）。"""
    arr: list[dict] = []
    for it in records:
        if isinstance(it, dict):
            arr.append(it)
    return arr


def load_goods(paths: Optional[ConfigPaths], *, goods: Optional[Iterable[dict]] = None) -> list[dict]:
    """加载商品数据。

    优先使用传入的内存数据 `goods`；否则若提供了 `paths`，则读取 `paths.root/goods.json`。
    失败时返回空列表。
    """
    if goods is not None:
        try:
            return _normalize_goods(goods)
        except Exception:
            pass
    # 回退：从文件加载
    if paths is None:
        return []
    try:
        p = Path(paths.root) / "goods.json"
        if p.exists():
            import json

            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return _normalize_goods(data)
    except Exception:
        pass
    return []


def open_goods_picker(
    root: tk.Misc,
    paths: ConfigPaths,
    on_pick: Callable[[dict], None],
    *,
    goods: Optional[Iterable[dict]] = None,
) -> None:
    """弹出“选择商品”对话框。

    - root: Tk/Toplevel/Frame 任意 Tk 容器，用于挂载弹窗与 grab。
    - paths: Config 路径对象，用于文件回退加载。
    - on_pick: 选中回调；保证输出包含 name、id、image_path、big_category 字段。
    - goods: 可选的内存数据源；若提供则优先使用。
    """
    goods_list: list[dict] = load_goods(paths, goods=goods)

    # 构建弹窗
    top = tk.Toplevel(root)
    top.title("选择物品")
    try:
        top.geometry("720x480")
    except Exception:
        pass
    try:
        # 将弹窗与 root 进行亲缘绑定，避免焦点问题
        top.transient(root.winfo_toplevel())
    except Exception:
        try:
            top.transient(root)
        except Exception:
            pass
    try:
        top.grab_set()
    except Exception:
        pass

    # 过滤条：搜索 + 大类/子类
    ctrl = ttk.Frame(top)
    ctrl.pack(fill=tk.X, padx=8, pady=6)
    ttk.Label(ctrl, text="搜索").pack(side=tk.LEFT)
    var_q = tk.StringVar(value="")
    ent = ttk.Entry(ctrl, textvariable=var_q, width=24)
    ent.pack(side=tk.LEFT, padx=6)
    try:
        ent.focus_set()
    except Exception:
        pass
    var_big = tk.StringVar(value="全部")
    var_sub = tk.StringVar(value="全部")

    def _derive_bigs() -> list[str]:
        return ["全部"] + sorted({str(g.get("big_category", "")) for g in goods_list if g.get("big_category")})

    def _derive_subs(sel_big: str) -> list[str]:
        subs = sorted({
            str(g.get("sub_category", ""))
            for g in goods_list
            if sel_big in ("全部", str(g.get("big_category", "")))
        })
        return ["全部"] + [s for s in subs if s]

    cmb_big = ttk.Combobox(ctrl, values=_derive_bigs(), state="readonly", width=12, textvariable=var_big)
    cmb_big.pack(side=tk.LEFT, padx=6)
    cmb_sub = ttk.Combobox(ctrl, values=["全部"], state="readonly", width=16, textvariable=var_sub)
    cmb_sub.pack(side=tk.LEFT, padx=6)

    def _refresh_sub(_e: object | None = None) -> None:
        vals = _derive_subs(var_big.get())
        try:
            cmb_sub.configure(values=vals)
        except Exception:
            pass
        if var_sub.get() not in vals:
            var_sub.set("全部")

    try:
        cmb_big.bind("<<ComboboxSelected>>", _refresh_sub)
    except Exception:
        pass
    _refresh_sub()

    # 列表：三列（名称/大类/子类）
    body = ttk.Frame(top)
    body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
    cols = ("name", "big", "sub")
    tree = ttk.Treeview(body, columns=cols, show="headings")
    tree.heading("name", text="名称")
    tree.heading("big", text="大类")
    tree.heading("sub", text="子类")
    tree.column("name", width=280)
    tree.column("big", width=120)
    tree.column("sub", width=180)
    sb = ttk.Scrollbar(body, orient=tk.VERTICAL, command=tree.yview)
    tree.configure(yscrollcommand=sb.set)
    tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    sb.pack(side=tk.LEFT, fill=tk.Y)

    # 鼠标滚轮（若 root 提供了绑定助手，则复用；否则忽略）
    try:
        bind_wheel = getattr(root, "_bind_mousewheel", None)
        if callable(bind_wheel):
            bind_wheel(tree, tree)
    except Exception:
        pass

    def _apply_filter(_e: object | None = None) -> None:
        q = (var_q.get() or "").strip().lower()
        b = var_big.get()
        s = var_sub.get()
        for iid in tree.get_children():
            tree.delete(iid)
        seen: set[str] = set()
        for g in goods_list:
            name = str(g.get("name", ""))
            big = str(g.get("big_category", ""))
            sub = str(g.get("sub_category", ""))
            if b not in ("全部", big):
                continue
            if s not in ("全部", sub):
                continue
            if q and (q not in name.lower() and q not in str(g.get("search_name", "")).lower()):
                continue
            iid = str(g.get("id", name))
            if iid in seen or tree.exists(iid):
                continue
            seen.add(iid)
            tree.insert("", tk.END, iid=iid, values=(name, big, sub))

    try:
        ent.bind("<KeyRelease>", _apply_filter)
        cmb_big.bind("<<ComboboxSelected>>", _apply_filter)
        cmb_sub.bind("<<ComboboxSelected>>", _apply_filter)
    except Exception:
        pass
    _apply_filter()

    def _select_current() -> None:
        sel = tree.selection()
        if not sel:
            top.destroy()
            return
        iid = sel[0]
        # 在当前 goods_list 中按 id 或 name 匹配
        item = next((g for g in goods_list if str(g.get("id", "")) == iid or str(g.get("name", "")) == iid), None)
        if item is None:
            top.destroy()
            return
        # 统一输出字段
        payload: Dict[str, Any] = {
            "name": str(item.get("name", "")),
            "id": str(item.get("id", "")),
            "image_path": str(item.get("image_path", "")),
            "big_category": str(item.get("big_category", "")),
        }
        try:
            on_pick(payload)
        finally:
            try:
                top.destroy()
            except Exception:
                pass

    btns = ttk.Frame(top)
    btns.pack(fill=tk.X, padx=8, pady=6)
    ttk.Button(btns, text="确定", command=_select_current).pack(side=tk.RIGHT)
    ttk.Button(btns, text="取消", command=top.destroy).pack(side=tk.RIGHT, padx=(0, 6))

    def _on_dbl(_e: object | None = None) -> None:
        _select_current()

    try:
        tree.bind("<Double-1>", _on_dbl)
    except Exception:
        pass

    # 窗口关闭清理：释放 grab，避免影响主界面交互
    def _on_close(_e: object | None = None) -> None:
        try:
            top.grab_release()
        except Exception:
            pass
        try:
            top.destroy()
        except Exception:
            pass

    try:
        top.protocol("WM_DELETE_WINDOW", _on_close)
    except Exception:
        pass


__all__ = ["open_goods_picker", "load_goods"]

