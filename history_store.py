import json
import os
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple


_LOCK = threading.Lock()
_BASE_DIR = os.path.join("output")
_PRICE_FILE = os.path.join(_BASE_DIR, "price_history.jsonl")
_PRICE_MINUTELY_FILE = os.path.join(_BASE_DIR, "price_history_minutely.jsonl")
_PURCHASE_FILE = os.path.join(_BASE_DIR, "purchase_history.jsonl")

# Simple in-process dedup cache: item_id -> (last_price, ts)
_LAST_PRICE_CACHE: Dict[str, Tuple[int, float]] = {}
# Last raw record write to price_history per item: item_id -> (price, ts)
_LAST_RAW_WRITE: Dict[str, Tuple[int, float]] = {}

# Raw tick throttling policy (env overridable)
def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except Exception:
        return default

def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except Exception:
        return default

# Minimal interval between two raw records for same item (seconds)
_RAW_MIN_INTERVAL_SEC = _float_env("PRICE_RAW_MIN_INTERVAL_SEC", 10.0)
# Minimal relative change vs last raw write (e.g., 0.02 = 2%)
_RAW_MIN_REL_CHANGE = _float_env("PRICE_RAW_MIN_REL_CHANGE", 0.02)
# Minimal absolute change vs last raw write (price units)
_RAW_MIN_ABS_CHANGE = _int_env("PRICE_RAW_MIN_ABS_CHANGE", 100)

# Minutely aggregation state: item_id -> {minute:int, min:int, max:int, sum:int, cnt:int, name:str}
_MIN_AGG: Dict[str, Dict[str, Any]] = {}


def _ensure_dir() -> None:
    try:
        os.makedirs(_BASE_DIR, exist_ok=True)
    except Exception:
        pass


def _now_ts_iso() -> Tuple[float, str]:
    ts = time.time()
    try:
        iso = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    except Exception:
        iso = str(ts)
    return ts, iso


def append_price(
    item_id: str,
    item_name: str,
    price: int,
    *,
    ts: Optional[float] = None,
    category: Optional[str] = None,
) -> None:
    """Append a price tick for an item (JSONL).

    Light dedup: if same price within 2 seconds for same item_id, skip.
    """
    if not item_id:
        return
    try:
        p = int(price)
    except Exception:
        return
    if p <= 0:
        return
    t = float(ts) if ts is not None else time.time()
    # Dedup check
    try:
        last = _LAST_PRICE_CACHE.get(item_id)
        if last is not None:
            lp, lt = last
            if lp == p and (t - lt) <= 2.0:
                return
    except Exception:
        pass
    rec = {
        "ts": t,
        "iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)),
        "item_id": str(item_id),
        "item_name": str(item_name or ""),
        "price": int(p),
    }
    if category:
        rec["category"] = str(category)
    _ensure_dir()
    # Decide whether to write a raw record (throttled by time/change)
    allow_raw = True
    try:
        lr = _LAST_RAW_WRITE.get(item_id)
        if lr is not None:
            lp, lt = lr
            time_ok = (t - lt) >= max(0.0, float(_RAW_MIN_INTERVAL_SEC))
            abs_ok = abs(p - lp) >= max(0, int(_RAW_MIN_ABS_CHANGE))
            try:
                rel_ok = abs(p - lp) >= int(round(max(0.0, _RAW_MIN_REL_CHANGE) * max(1, lp)))
            except Exception:
                rel_ok = False
            allow_raw = bool(time_ok or abs_ok or rel_ok)
    except Exception:
        allow_raw = True
    with _LOCK:
        try:
            if allow_raw:
                with open(_PRICE_FILE, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                _LAST_RAW_WRITE[item_id] = (p, t)
            # Always update last seen cache for dedup and rate decisions
            _LAST_PRICE_CACHE[item_id] = (p, t)
        except Exception:
            pass

    # --- Minutely aggregator (per item) ---
    try:
        minute = int(t // 60)
        st = _MIN_AGG.get(item_id)
        if (st is None) or (int(st.get("minute", -1)) != minute):
            # flush previous bucket
            if st is not None:
                try:
                    _append_minutely_record(
                        item_id,
                        st.get("name", ""),
                        int(st.get("minute", minute) * 60),
                        int(st.get("min", p)),
                        int(st.get("max", p)),
                        int(round((st.get("sum", p) or 0) / max(1, int(st.get("cnt", 1) or 1)))),
                        category=category,
                        count=int(st.get("cnt", 1) or 1),
                    )
                except Exception:
                    pass
            # start new bucket
            _MIN_AGG[item_id] = {
                "minute": minute,
                "min": p,
                "max": p,
                "sum": p,
                "cnt": 1,
                "name": item_name,
            }
        else:
            # update current bucket
            st["min"] = min(int(st.get("min", p)), p)
            st["max"] = max(int(st.get("max", p)), p)
            st["sum"] = int(st.get("sum", 0)) + p
            st["cnt"] = int(st.get("cnt", 0)) + 1
            st["name"] = item_name or st.get("name", "")
    except Exception:
        pass


def append_purchase(
    item_id: str,
    item_name: str,
    price: int,
    qty: int,
    *,
    ts: Optional[float] = None,
    task_id: Optional[str] = None,
    task_name: Optional[str] = None,
    category: Optional[str] = None,
    used_max: Optional[bool] = None,
) -> None:
    """Append a purchase record for an item (JSONL)."""
    if not item_id:
        return
    try:
        p = int(price)
    except Exception:
        return
    try:
        q = int(qty)
    except Exception:
        return
    if q <= 0 or p <= 0:
        return
    t = float(ts) if ts is not None else time.time()
    rec = {
        "ts": t,
        "iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t)),
        "item_id": str(item_id),
        "item_name": str(item_name or ""),
        "price": int(p),
        "qty": int(q),
        "amount": int(p) * int(q),
    }
    if task_id:
        rec["task_id"] = str(task_id)
    if task_name:
        rec["task_name"] = str(task_name)
    if category:
        rec["category"] = str(category)
    if used_max is not None:
        rec["used_max"] = bool(used_max)
    _ensure_dir()
    with _LOCK:
        try:
            with open(_PURCHASE_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass


def _iter_jsonl(path: str) -> Iterable[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except Exception:
        return []


def query_price(item_id: str, since_ts: float) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rec in _iter_jsonl(_PRICE_FILE):
        try:
            if rec.get("item_id") != item_id:
                continue
            if float(rec.get("ts", 0.0)) >= float(since_ts):
                out.append(rec)
        except Exception:
            continue
    out.sort(key=lambda r: float(r.get("ts", 0.0)))
    return out


def query_price_minutely(item_id: str, since_ts: float) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for rec in _iter_jsonl(_PRICE_MINUTELY_FILE):
        try:
            if rec.get("item_id") != item_id:
                continue
            if float(rec.get("ts_min", 0.0)) >= float(since_ts):
                out.append(rec)
        except Exception:
            continue
    out.sort(key=lambda r: float(r.get("ts_min", 0.0)))
    return out


def query_purchase(
    item_id: str,
    since_ts: float,
    *,
    price_min: Optional[int] = None,
    price_max: Optional[int] = None,
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    pmin = int(price_min) if price_min is not None else None
    pmax = int(price_max) if price_max is not None else None
    for rec in _iter_jsonl(_PURCHASE_FILE):
        try:
            if rec.get("item_id") != item_id:
                continue
            if float(rec.get("ts", 0.0)) < float(since_ts):
                continue
            pr = int(rec.get("price", 0))
            if pmin is not None and pr < pmin:
                continue
            if pmax is not None and pr > pmax:
                continue
            out.append(rec)
        except Exception:
            continue
    out.sort(key=lambda r: float(r.get("ts", 0.0)))
    return out


def summarize_purchases(recs: List[Dict[str, Any]]) -> Dict[str, Any]:
    orders = len(recs)
    qty = 0
    amount = 0
    for r in recs:
        try:
            q = int(r.get("qty", 0))
            p = int(r.get("price", 0))
        except Exception:
            q = 0
            p = 0
        qty += q
        amount += p * q
    avg_price = int(round(amount / qty)) if qty > 0 else 0
    return {
        "orders": orders,
        "quantity": qty,
        "amount": amount,
        "avg_price": avg_price,
    }


def _rewrite_filtered(path: str, keep_pred) -> int:
    """Rewrite JSONL at `path` keeping lines where keep_pred(dict)->True.

    Returns number of removed records. Safe in-process under _LOCK.
    """
    if not os.path.exists(path):
        return 0
    removed = 0
    tmp = path + ".tmp"
    with _LOCK:
        try:
            with open(path, "r", encoding="utf-8") as fin, open(tmp, "w", encoding="utf-8") as fout:
                for line in fin:
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        obj = json.loads(s)
                    except Exception:
                        # Keep malformed lines to avoid data loss
                        fout.write(line)
                        continue
                    try:
                        if keep_pred(obj):
                            fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
                        else:
                            removed += 1
                    except Exception:
                        # On predicate error, keep the line
                        fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
            os.replace(tmp, path)
        except Exception:
            # Best-effort cleanup
            try:
                if os.path.exists(tmp):
                    os.remove(tmp)
            except Exception:
                pass
    return removed


def clear_price_history(item_id: str) -> int:
    """Remove all price records for given item_id. Returns removed count."""
    if not item_id:
        return 0
    return _rewrite_filtered(_PRICE_FILE, lambda o: o.get("item_id") != item_id)


def _append_minutely_record(
    item_id: str,
    item_name: str,
    ts_min: int,
    vmin: int,
    vmax: int,
    vavg: int,
    *,
    category: Optional[str] = None,
    count: int = 1,
) -> None:
    rec = {
        "ts_min": int(ts_min),
        "iso_min": time.strftime("%Y-%m-%d %H:%M:00", time.localtime(ts_min)),
        "item_id": str(item_id),
        "item_name": str(item_name or ""),
        "min": int(vmin),
        "max": int(vmax),
        "avg": int(vavg),
        "count": int(count),
    }
    if category:
        rec["category"] = str(category)
    _ensure_dir()
    with _LOCK:
        try:
            with open(_PRICE_MINUTELY_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:
            pass


def flush_price_minutely() -> None:
    """Flush current in-memory minute buckets to output file.

    Writes partial aggregates for the current minute.
    """
    with _LOCK:
        try:
            for item_id, st in list(_MIN_AGG.items()):
                try:
                    ts_min = int(st.get("minute", int(time.time() // 60)) * 60)
                    vmin = int(st.get("min", 0))
                    vmax = int(st.get("max", 0))
                    cnt = int(st.get("cnt", 0))
                    sm = int(st.get("sum", 0))
                    vavg = int(round(sm / max(1, cnt))) if cnt > 0 else 0
                    _append_minutely_record(item_id, st.get("name", ""), ts_min, vmin, vmax, vavg, count=cnt)
                except Exception:
                    continue
        except Exception:
            pass


def clear_purchase_history(item_id: str) -> int:
    """Remove all purchase records for given item_id. Returns removed count."""
    if not item_id:
        return 0
    return _rewrite_filtered(_PURCHASE_FILE, lambda o: o.get("item_id") != item_id)
