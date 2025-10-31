
from __future__ import annotations

from typing import TYPE_CHECKING

import tkinter as tk
from tkinter import ttk

from .base import BaseTab

if TYPE_CHECKING:
    from super_buyer.ui.app import App


class ProfitTab(BaseTab):
    """利润计算标签页，用于快速估算买卖收益。"""

    tab_text = "利润计算"

    def __init__(self, app: "App", notebook: ttk.Notebook) -> None:
        super().__init__(app, notebook)
        self.tab_profit = self
        self._build_tab_profit()

    def _build_tab_profit(self) -> None:
        outer = self.tab_profit
        pad = {"padx": 8, "pady": 8}

        # 顶部说明
        hint = ttk.Label(
            outer,
            text="输入买入价/数量/卖出价，按卖出价收取6%交易税，剩余为净收入；利润=净收入-成本。",
            foreground="#444",
        )
        hint.pack(anchor="w", **pad)

        body = ttk.Frame(outer)
        body.pack(fill=tk.BOTH, expand=True, **pad)

        # 左：输入
        lf_in = ttk.LabelFrame(body, text="输入")
        lf_in.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, **pad)

        v_buy = tk.DoubleVar(value=0.0)
        v_qty = tk.IntVar(value=1)
        v_sell = tk.DoubleVar(value=0.0)
        TAX = 0.06  # 固定税率 6%

        def _sv(entry: tk.Entry) -> None:
            try:
                entry.selection_range(0, tk.END)
                entry.icursor(tk.END)
            except Exception:
                pass

        row = 0
        ttk.Label(lf_in, text="买入价").grid(row=row, column=0, sticky="e", padx=6, pady=6)
        ent_buy = ttk.Entry(lf_in, width=12, textvariable=v_buy)
        ent_buy.grid(row=row, column=1, sticky="w", padx=6, pady=6)
        self._attach_tooltip(ent_buy, "每件的买入单价（整数或小数）")
        try:
            ent_buy.bind("<FocusIn>", lambda _e=None: _sv(ent_buy))
        except Exception:
            pass

        row += 1
        ttk.Label(lf_in, text="购买数量").grid(row=row, column=0, sticky="e", padx=6, pady=6)
        ent_qty = ttk.Entry(lf_in, width=12, textvariable=v_qty)
        ent_qty.grid(row=row, column=1, sticky="w", padx=6, pady=6)
        self._attach_tooltip(ent_qty, "购买的总数量（整数）")
        try:
            ent_qty.bind("<FocusIn>", lambda _e=None: _sv(ent_qty))
        except Exception:
            pass

        row += 1
        ttk.Label(lf_in, text="卖出价").grid(row=row, column=0, sticky="e", padx=6, pady=6)
        ent_sell = ttk.Entry(lf_in, width=12, textvariable=v_sell)
        ent_sell.grid(row=row, column=1, sticky="w", padx=6, pady=6)
        self._attach_tooltip(ent_sell, "每件的卖出单价（整数或小数）")
        try:
            ent_sell.bind("<FocusIn>", lambda _e=None: _sv(ent_sell))
        except Exception:
            pass

        row += 1
        ttk.Label(lf_in, text="卖出税率").grid(row=row, column=0, sticky="e", padx=6, pady=6)
        lbl_tax = ttk.Label(lf_in, text="6%（固定）")
        lbl_tax.grid(row=row, column=1, sticky="w", padx=6, pady=6)

        for c in range(0, 2):
            try:
                lf_in.columnconfigure(c, weight=0)
            except Exception:
                pass

        # 右：结果
        lf_out = ttk.LabelFrame(body, text="结果")
        lf_out.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, **pad)

        def fmt(n: float | int) -> str:
            try:
                v = float(n)
            except Exception:
                return "0"
            try:
                return f"{int(round(v)):,}"
            except Exception:
                return str(int(round(v)))

        out_cost = tk.StringVar(value="0")
        out_rev_g = tk.StringVar(value="0")
        out_tax = tk.StringVar(value="0")
        out_rev_n = tk.StringVar(value="0")
        out_profit_t = tk.StringVar(value="0")
        out_profit_u = tk.StringVar(value="0")
        out_margin = tk.StringVar(value="0%")
        out_breakeven = tk.StringVar(value="0")

        def recalc(*_):
            try:
                buy = max(0.0, float(v_buy.get() or 0.0))
            except Exception:
                buy = 0.0
            try:
                qty = max(0, int(v_qty.get() or 0))
            except Exception:
                qty = 0
            try:
                sell = max(0.0, float(v_sell.get() or 0.0))
            except Exception:
                sell = 0.0
            cost = buy * qty
            rev_g = sell * qty
            tax = rev_g * TAX
            rev_n = rev_g - tax
            profit_t = rev_n - cost
            profit_u = (profit_t / qty) if qty > 0 else 0.0
            margin = (profit_t / cost * 100.0) if cost > 0 else 0.0
            breakeven = buy / (1.0 - TAX) if buy > 0 else 0.0
            out_cost.set(fmt(cost))
            out_rev_g.set(fmt(rev_g))
            out_tax.set(fmt(tax))
            out_rev_n.set(fmt(rev_n))
            out_profit_t.set(fmt(profit_t))
            out_profit_u.set(fmt(profit_u))
            try:
                out_margin.set(f"{margin:.1f}%")
            except Exception:
                out_margin.set("0%")
            out_breakeven.set(fmt(breakeven))

        try:
            v_buy.trace_add("write", recalc)
            v_qty.trace_add("write", recalc)
            v_sell.trace_add("write", recalc)
        except Exception:
            pass

        r = 0
        ttk.Label(lf_out, text="总成本").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        ttk.Label(lf_out, textvariable=out_cost, foreground="#37474F").grid(row=r, column=1, sticky="w", padx=6, pady=6)
        r += 1
        ttk.Label(lf_out, text="卖出总额").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        ttk.Label(lf_out, textvariable=out_rev_g).grid(row=r, column=1, sticky="w", padx=6, pady=6)
        r += 1
        ttk.Label(lf_out, text="交易税(6%)").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        ttk.Label(lf_out, textvariable=out_tax, foreground="#C62828").grid(row=r, column=1, sticky="w", padx=6, pady=6)
        r += 1
        ttk.Label(lf_out, text="净收入").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        ttk.Label(lf_out, textvariable=out_rev_n).grid(row=r, column=1, sticky="w", padx=6, pady=6)
        r += 1
        sep = ttk.Separator(lf_out, orient=tk.HORIZONTAL)
        sep.grid(row=r, column=0, columnspan=2, sticky="ew", padx=6, pady=4)
        r += 1
        ttk.Label(lf_out, text="总利润").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        ttk.Label(lf_out, textvariable=out_profit_t, font=("", 10, "bold"), foreground="#2E7D32").grid(row=r, column=1, sticky="w", padx=6, pady=6)
        r += 1
        ttk.Label(lf_out, text="每件利润").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        ttk.Label(lf_out, textvariable=out_profit_u).grid(row=r, column=1, sticky="w", padx=6, pady=6)
        r += 1
        ttk.Label(lf_out, text="毛利率").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        ttk.Label(lf_out, textvariable=out_margin).grid(row=r, column=1, sticky="w", padx=6, pady=6)
        r += 1
        ttk.Label(lf_out, text="保本卖价(单件)").grid(row=r, column=0, sticky="e", padx=6, pady=6)
        ttk.Label(lf_out, textvariable=out_breakeven).grid(row=r, column=1, sticky="w", padx=6, pady=6)

        # 底部操作
        bar = ttk.Frame(outer)
        bar.pack(fill=tk.X, **pad)
        def _reset():
            try:
                v_buy.set(0.0)
                v_qty.set(1)
                v_sell.set(0.0)
            except Exception:
                pass
            recalc()
        ttk.Button(bar, text="清空", command=_reset).pack(side=tk.RIGHT)
        ttk.Label(bar, text="提示：保本卖价=买入价/0.94").pack(side=tk.LEFT)

        # 初始计算
        recalc()


