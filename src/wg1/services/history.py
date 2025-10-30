"""
价格与购买历史记录写入。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

_LOCK = threading.Lock()


@dataclass
class HistoryPaths:
    base_dir: Path
    price_file: Path
    price_minutely_file: Path
    purchase_file: Path


def resolve_paths(base_dir: Path | str) -> HistoryPaths:
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    return HistoryPaths(
        base_dir=base,
        price_file=base / "price_history.jsonl",
        price_minutely_file=base / "price_history_minutely.jsonl",
        purchase_file=base / "purchase_history.jsonl",
    )


_LAST_PRICE_CACHE: Dict[str, Tuple[int, float]] = {}
_LAST_RAW_WRITE: Dict[str, Tuple[int, float]] = {}
_MIN_AGG: Dict[str, Dict[str, Any]] = {}


def _now_iso(ts: Optional[float] = None) -> Tuple[float, str]:
    t = time.time() if ts is None else float(ts)
    try:
        label = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
    except Exception:
        label = str(t)
    return t, label


def append_price(
    item_id: str,
    item_name: str,
    price: int,
    *,
    paths: HistoryPaths,
    ts: Optional[float] = None,
    category: Optional[str] = None,
) -> None:
    if not item_id:
        return
    try:
        price_val = int(price)
    except Exception:
        return
    if price_val <= 0:
        return
    now_ts, iso = _now_iso(ts)
    last = _LAST_PRICE_CACHE.get(item_id)
    if last is not None:
        last_price, last_ts = last
        if last_price == price_val and (now_ts - last_ts) <= 2.0:
            return
    record = {
        "ts": now_ts,
        "iso": iso,
        "item_id": str(item_id),
        "item_name": str(item_name or ""),
        "price": price_val,
    }
    if category:
        record["category"] = str(category)
    with _LOCK:
        allow_raw = True
        prev_raw = _LAST_RAW_WRITE.get(item_id)
        if prev_raw is not None:
            prev_price, prev_ts = prev_raw
            time_ok = (now_ts - prev_ts) >= 10.0
            abs_ok = abs(price_val - prev_price) >= 100
            rel_ok = False
            try:
                rel_ok = abs(price_val - prev_price) >= int(round(0.02 * max(1, prev_price)))
            except Exception:
                rel_ok = False
            allow_raw = bool(time_ok or abs_ok or rel_ok)
        if allow_raw:
            with paths.price_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
            _LAST_RAW_WRITE[item_id] = (price_val, now_ts)
        _LAST_PRICE_CACHE[item_id] = (price_val, now_ts)
    _agg_minutely(item_id, record, paths, category=category)


def _agg_minutely(
    item_id: str,
    record: Dict[str, Any],
    paths: HistoryPaths,
    *,
    category: Optional[str] = None,
) -> None:
    ts = float(record.get("ts", time.time()))
    minute = int(ts // 60)
    state = _MIN_AGG.get(item_id)
    if state is None or int(state.get("minute", -1)) != minute:
        if state is not None:
            _flush_minutely(item_id, state, paths, category=category)
        _MIN_AGG[item_id] = {
            "minute": minute,
            "min": int(record.get("price", 0)),
            "max": int(record.get("price", 0)),
            "sum": int(record.get("price", 0)),
            "cnt": 1,
            "name": record.get("item_name", ""),
        }
    else:
        state["min"] = min(int(state.get("min", record["price"])), int(record["price"]))
        state["max"] = max(int(state.get("max", record["price"])), int(record["price"]))
        state["sum"] = int(state.get("sum", 0)) + int(record["price"])
        state["cnt"] = int(state.get("cnt", 0)) + 1
        state["name"] = record.get("item_name", state.get("name", ""))


def _flush_minutely(
    item_id: str,
    state: Dict[str, Any],
    paths: HistoryPaths,
    *,
    category: Optional[str] = None,
) -> None:
    try:
        minute = int(state.get("minute", 0))
        total = int(state.get("cnt", 1) or 1)
        avg = int(round(int(state.get("sum", 0)) / max(1, total)))
        rec = {
            "ts": minute * 60,
            "iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(minute * 60)),
            "item_id": item_id,
            "item_name": state.get("name", ""),
            "min": int(state.get("min", avg)),
            "max": int(state.get("max", avg)),
            "avg": avg,
            "count": total,
        }
        if category:
            rec["category"] = str(category)
        with paths.price_minutely_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def append_purchase(
    item_id: str,
    item_name: str,
    price: int,
    qty: int,
    *,
    paths: HistoryPaths,
    ts: Optional[float] = None,
    task_id: Optional[str] = None,
    task_name: Optional[str] = None,
    category: Optional[str] = None,
    used_max: Optional[bool] = None,
) -> None:
    if not item_id:
        return
    try:
        price_val = int(price)
        qty_val = int(qty)
    except Exception:
        return
    if price_val <= 0 or qty_val <= 0:
        return
    now_ts, iso = _now_iso(ts)
    record = {
        "ts": now_ts,
        "iso": iso,
        "item_id": str(item_id),
        "item_name": str(item_name or ""),
        "price": price_val,
        "qty": qty_val,
        "amount": price_val * qty_val,
    }
    if task_id:
        record["task_id"] = str(task_id)
    if task_name:
        record["task_name"] = str(task_name)
    if category:
        record["category"] = str(category)
    if used_max is not None:
        record["used_max"] = bool(used_max)
    with _LOCK:
        with paths.purchase_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


__all__ = ["HistoryPaths", "append_price", "append_purchase", "resolve_paths"]

