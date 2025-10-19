import json
import os
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple


_LOCK = threading.Lock()
_BASE_DIR = os.path.join("output")
_PRICE_FILE = os.path.join(_BASE_DIR, "price_history.jsonl")
_PURCHASE_FILE = os.path.join(_BASE_DIR, "purchase_history.jsonl")

# Simple in-process dedup cache: item_id -> (last_price, ts)
_LAST_PRICE_CACHE: Dict[str, Tuple[int, float]] = {}


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


def append_price(item_id: str, item_name: str, price: int, *, ts: Optional[float] = None) -> None:
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
    _ensure_dir()
    with _LOCK:
        try:
            with open(_PRICE_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            _LAST_PRICE_CACHE[item_id] = (p, t)
        except Exception:
            pass


def append_purchase(
    item_id: str, item_name: str, price: int, qty: int, *, ts: Optional[float] = None
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


def clear_purchase_history(item_id: str) -> int:
    """Remove all purchase records for given item_id. Returns removed count."""
    if not item_id:
        return 0
    return _rewrite_filtered(_PURCHASE_FILE, lambda o: o.get("item_id") != item_id)

