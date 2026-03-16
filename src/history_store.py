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

_JSONL_CACHE: Dict[str, tuple[int, int, List[Dict[str, Any]]]] = {}


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


def _cache_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except Exception:
        return str(path)


def _invalidate_jsonl_cache(path: Path) -> None:
    _JSONL_CACHE.pop(_cache_key(path), None)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _empty_price_summary() -> Dict[str, int]:
    return {
        "count": 0,
        "min_price": 0,
        "max_price": 0,
        "avg_price": 0,
        "latest_price": 0,
    }


def _empty_purchase_summary() -> Dict[str, int]:
    return {
        "count": 0,
        "quantity": 0,
        "total_amount": 0,
        "avg_price": 0,
        "max_price": 0,
        "min_price": 0,
    }


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        _invalidate_jsonl_cache(path)
        return []
    try:
        stat = path.stat()
        sig = (int(stat.st_mtime_ns), int(stat.st_size))
    except Exception:
        sig = None
    cache_key = _cache_key(path)
    cached = _JSONL_CACHE.get(cache_key)
    if sig is not None and cached is not None and cached[:2] == sig:
        return list(cached[2])
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
    if sig is not None:
        _JSONL_CACHE[cache_key] = (sig[0], sig[1], out)
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
    since = _to_float(since_ts)
    out = [r for r in arr if str(r.get("item_id", "")) == str(item_id) and _to_float(r.get("ts", 0.0)) >= since]
    out.sort(key=lambda r: float(r.get("ts", 0.0)))
    return out


def query_price_minutely(item_id: str, since_ts: float) -> List[Dict[str, Any]]:
    paths = _paths()
    arr = _read_jsonl(paths.price_minutely_file)
    since = _to_float(since_ts)

    def _normalize_minutely_record(record: Dict[str, Any]) -> Dict[str, Any] | None:
        """统一分钟聚合记录的时间字段，兼容旧的 ts/ts_min 读法。"""
        try:
            ts_val = float(record.get("ts", record.get("ts_min", 0.0)) or 0.0)
        except Exception:
            return None
        if ts_val < since:
            return None
        item_val = str(record.get("item_id", ""))
        if item_val != str(item_id):
            return None
        normalized = dict(record)
        normalized["ts"] = ts_val
        normalized["ts_min"] = ts_val
        return normalized

    out_by_minute: Dict[int, Dict[str, Any]] = {}
    for record in arr:
        normalized = _normalize_minutely_record(record)
        if normalized is None:
            continue
        out_by_minute[int(float(normalized.get("ts", 0.0)))] = normalized

    # 用原始价格记录补齐“当前分钟尚未 flush”或旧数据缺少分钟聚合的情况。
    raw_arr = _read_jsonl(paths.price_file)
    raw_buckets: Dict[int, Dict[str, Any]] = {}
    for record in raw_arr:
        try:
            ts_val = float(record.get("ts", 0.0) or 0.0)
        except Exception:
            continue
        if ts_val < since or str(record.get("item_id", "")) != str(item_id):
            continue
        try:
            price_val = int(record.get("price", 0) or 0)
        except Exception:
            continue
        if price_val <= 0:
            continue
        minute_ts = int(ts_val // 60) * 60
        bucket = raw_buckets.get(minute_ts)
        if bucket is None:
            raw_buckets[minute_ts] = {
                "ts": float(minute_ts),
                "ts_min": float(minute_ts),
                "iso": record.get("iso", ""),
                "item_id": str(item_id),
                "item_name": str(record.get("item_name", "") or ""),
                "min": price_val,
                "max": price_val,
                "sum": price_val,
                "count": 1,
            }
            continue
        bucket["min"] = min(int(bucket.get("min", price_val)), price_val)
        bucket["max"] = max(int(bucket.get("max", price_val)), price_val)
        bucket["sum"] = int(bucket.get("sum", 0)) + price_val
        bucket["count"] = int(bucket.get("count", 0)) + 1
        if not bucket.get("item_name"):
            bucket["item_name"] = str(record.get("item_name", "") or "")

    for minute_ts, bucket in raw_buckets.items():
        if minute_ts in out_by_minute:
            continue
        count = max(1, int(bucket.get("count", 1) or 1))
        avg_val = int(round(int(bucket.get("sum", 0)) / count))
        out_by_minute[minute_ts] = {
            "ts": float(minute_ts),
            "ts_min": float(minute_ts),
            "iso": bucket.get("iso", ""),
            "item_id": str(item_id),
            "item_name": str(bucket.get("item_name", "") or ""),
            "min": int(bucket.get("min", avg_val)),
            "max": int(bucket.get("max", avg_val)),
            "avg": avg_val,
            "count": count,
        }

    out = list(out_by_minute.values())
    out.sort(key=lambda r: float(r.get("ts", 0.0)))
    return out


def query_purchase(item_id: str, since_ts: float) -> List[Dict[str, Any]]:
    p = _paths().purchase_file
    arr = _read_jsonl(p)
    since = _to_float(since_ts)
    out = [r for r in arr if str(r.get("item_id", "")) == str(item_id) and _to_float(r.get("ts", 0.0)) >= since]
    out.sort(key=lambda r: float(r.get("ts", 0.0)))
    return out


def summarize_prices(recs: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    summary = _empty_price_summary()
    total = 0
    latest_ts = 0.0
    for r in recs:
        price = _to_int(r.get("price", 0))
        if price <= 0:
            continue
        ts = _to_float(r.get("ts", 0.0))
        summary["count"] += 1
        total += price
        if summary["min_price"] <= 0 or price < summary["min_price"]:
            summary["min_price"] = price
        if price > summary["max_price"]:
            summary["max_price"] = price
        if summary["latest_price"] <= 0 or ts >= latest_ts:
            latest_ts = ts
            summary["latest_price"] = price
    if summary["count"] > 0:
        summary["avg_price"] = int(round(total / summary["count"]))
    return summary


def summarize_prices_by_item(item_ids: Iterable[str], since_ts: float) -> Dict[str, Dict[str, int]]:
    ids = {str(item_id) for item_id in item_ids if str(item_id)}
    summaries = {item_id: _empty_price_summary() for item_id in ids}
    if not ids:
        return summaries

    totals = {item_id: 0 for item_id in ids}
    latest_ts = {item_id: 0.0 for item_id in ids}
    since = _to_float(since_ts)

    for r in _read_jsonl(_paths().price_file):
        item_id = str(r.get("item_id", ""))
        if item_id not in ids:
            continue
        ts = _to_float(r.get("ts", 0.0))
        if ts < since:
            continue
        price = _to_int(r.get("price", 0))
        if price <= 0:
            continue
        summary = summaries[item_id]
        summary["count"] += 1
        totals[item_id] += price
        if summary["min_price"] <= 0 or price < summary["min_price"]:
            summary["min_price"] = price
        if price > summary["max_price"]:
            summary["max_price"] = price
        if summary["latest_price"] <= 0 or ts >= latest_ts[item_id]:
            latest_ts[item_id] = ts
            summary["latest_price"] = price

    for item_id, summary in summaries.items():
        if summary["count"] > 0:
            summary["avg_price"] = int(round(totals[item_id] / summary["count"]))
    return summaries


def summarize_purchases(recs: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    summary = _empty_purchase_summary()
    for r in recs:
        q = _to_int(r.get("qty", 0))
        p = _to_int(r.get("price", 0))
        if q <= 0 or p <= 0:
            continue
        amount = _to_int(r.get("amount", p * q))
        if amount <= 0:
            amount = p * q
        summary["count"] += 1
        summary["quantity"] += q
        summary["total_amount"] += amount
        if summary["min_price"] <= 0 or p < summary["min_price"]:
            summary["min_price"] = p
        if p > summary["max_price"]:
            summary["max_price"] = p
    if summary["quantity"] > 0:
        summary["avg_price"] = int(round(summary["total_amount"] / summary["quantity"]))
    return summary


# ---------- 清理（UI 操作） ----------

def _rewrite_jsonl(path: Path, keep: List[Dict[str, Any]]) -> int:
    try:
        with path.open("w", encoding="utf-8") as f:
            for r in keep:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        _invalidate_jsonl_cache(path)
        return 0
    except Exception:
        _invalidate_jsonl_cache(path)
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
