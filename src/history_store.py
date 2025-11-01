"""
历史读写工具（UI 查询/清理/汇总的轻量实现）。

- 写入：透传至 services.history（append_price/append_purchase）。
- 读取：基于 data/output 下 JSONL 文件进行查询与汇总。

默认输出目录解析顺序：
1) 环境变量 ARENA_BUYER_OUTPUT_DIR；
2) 当前工作目录下 data/output；
3) 当前工作目录下 output（若不存在则创建）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import json
import os
import time

from super_buyer.services.history import (
    HistoryPaths,
    append_price as _append_price,
    append_purchase as _append_purchase,
    resolve_paths as _resolve_paths,
)


def _base_dir() -> Path:
    env = os.environ.get("ARENA_BUYER_OUTPUT_DIR")
    if env:
        p = Path(env)
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return p
    d1 = Path.cwd() / "data" / "output"
    if d1.exists() or (Path.cwd() / "data").exists():
        # 如果存在 data/ 或 data/output，优先使用 data/output
        try:
            d1.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        return d1
    d2 = Path.cwd() / "output"
    try:
        d2.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d2


def _paths() -> HistoryPaths:
    return _resolve_paths(_base_dir())


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return out


# ---------- 写入（供 MultiSnipe/Runner 复用） ----------

def append_price(*, item_id: str, item_name: str, price: int, category: Optional[str] = None) -> None:
    """写入价格历史。"""
    try:
        _append_price(item_id=item_id, item_name=item_name, price=int(price), paths=_paths(), category=category)
    except Exception:
        pass


def append_purchase(*, item_id: str, item_name: str, price: int, qty: int, task_id: Optional[str] = None, task_name: Optional[str] = None, category: Optional[str] = None, used_max: Optional[bool] = None) -> None:
    """写入购买历史。"""
    try:
        _append_purchase(
            item_id=item_id,
            item_name=item_name,
            price=int(price),
            qty=int(qty),
            paths=_paths(),
            task_id=task_id,
            task_name=task_name,
            category=category,
            used_max=used_max,
        )
    except Exception:
        pass


# ---------- 读取（UI 查询） ----------

def query_price(item_id: str, since_ts: float) -> List[Dict[str, Any]]:
    p = _paths().price_file
    arr = _read_jsonl(p)
    try:
        since = float(since_ts)
    except Exception:
        since = 0.0
    out = [r for r in arr if str(r.get("item_id", "")) == str(item_id) and float(r.get("ts", 0.0)) >= since]
    out.sort(key=lambda r: float(r.get("ts", 0.0)))
    return out


def query_price_minutely(item_id: str, since_ts: float) -> List[Dict[str, Any]]:
    p = _paths().price_minutely_file
    arr = _read_jsonl(p)
    try:
        since = float(since_ts)
    except Exception:
        since = 0.0
    out = [r for r in arr if str(r.get("item_id", "")) == str(item_id) and float(r.get("ts", 0.0)) >= since]
    out.sort(key=lambda r: float(r.get("ts", 0.0)))
    return out


def query_purchase(item_id: str, since_ts: float) -> List[Dict[str, Any]]:
    p = _paths().purchase_file
    arr = _read_jsonl(p)
    try:
        since = float(since_ts)
    except Exception:
        since = 0.0
    out = [r for r in arr if str(r.get("item_id", "")) == str(item_id) and float(r.get("ts", 0.0)) >= since]
    out.sort(key=lambda r: float(r.get("ts", 0.0)))
    return out


def summarize_purchases(recs: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    total_qty = 0
    total_amt = 0
    for r in recs:
        try:
            q = int(r.get("qty", 0))
            p = int(r.get("price", 0))
        except Exception:
            continue
        if q <= 0 or p <= 0:
            continue
        total_qty += q
        total_amt += p * q
    avg = int(round(total_amt / max(1, total_qty))) if total_qty > 0 else 0
    return {"quantity": total_qty, "avg_price": avg}


# ---------- 清理（UI 操作） ----------

def _rewrite_jsonl(path: Path, keep: List[Dict[str, Any]]) -> int:
    try:
        with path.open("w", encoding="utf-8") as f:
            for r in keep:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        return 0
    except Exception:
        return -1


def clear_price_history(item_id: str) -> int:
    p = _paths().price_file
    arr = _read_jsonl(p)
    keep = [r for r in arr if str(r.get("item_id", "")) != str(item_id)]
    removed = len(arr) - len(keep)
    _rewrite_jsonl(p, keep)
    # 同时清理聚合文件中对应 item_id 的记录
    pm = _paths().price_minutely_file
    marr = _read_jsonl(pm)
    mkeep = [r for r in marr if str(r.get("item_id", "")) != str(item_id)]
    _rewrite_jsonl(pm, mkeep)
    return removed


def clear_purchase_history(item_id: str) -> int:
    p = _paths().purchase_file
    arr = _read_jsonl(p)
    keep = [r for r in arr if str(r.get("item_id", "")) != str(item_id)]
    removed = len(arr) - len(keep)
    _rewrite_jsonl(p, keep)
    return removed

