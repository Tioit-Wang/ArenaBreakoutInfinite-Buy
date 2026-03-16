"""运行日志按日落库与按需回放。"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Deque, Dict, List

from super_buyer.core.logging import ensure_level_tag

MAX_VISIBLE_LOG_LINES = 5000

_LOCK = threading.Lock()


def _safe_channel_name(channel: str) -> str:
    raw = str(channel or "").strip().lower()
    if not raw:
        return "runtime"
    return "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in raw)


def _logs_root(output_dir: str | Path) -> Path:
    root = Path(output_dir).resolve() / "logs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _day_dir(output_dir: str | Path, *, ts: float | None = None) -> Path:
    now_ts = time.time() if ts is None else float(ts)
    day = time.strftime("%Y-%m-%d", time.localtime(now_ts))
    path = _logs_root(output_dir) / day
    path.mkdir(parents=True, exist_ok=True)
    return path


def _log_file(output_dir: str | Path, channel: str, *, ts: float | None = None) -> Path:
    return _day_dir(output_dir, ts=ts) / f"{_safe_channel_name(channel)}.jsonl"


def append_runtime_log(
    output_dir: str | Path,
    channel: str,
    message: str,
    *,
    ts: float | None = None,
) -> str:
    """写入一条运行日志，并返回标准化后的展示文本。"""
    now_ts = time.time() if ts is None else float(ts)
    normalized = ensure_level_tag(message, "detail", ts=now_ts)
    record: Dict[str, Any] = {
        "ts": now_ts,
        "iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now_ts)),
        "channel": _safe_channel_name(channel),
        "message": normalized,
    }
    path = _log_file(output_dir, channel, ts=now_ts)
    with _LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return normalized


def _read_last_messages(path: Path, *, limit: int) -> List[str]:
    if limit <= 0 or (not path.exists()):
        return []
    tail: Deque[str] = deque(maxlen=limit)
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    tail.append(line)
    except Exception:
        return []

    out: List[str] = []
    for line in tail:
        try:
            record = json.loads(line)
        except Exception:
            record = {"message": line}
        text = str(record.get("message", "") or "").strip()
        if text:
            out.append(text)
    return out


def read_latest_runtime_logs(
    output_dir: str | Path,
    channel: str,
    *,
    limit: int = MAX_VISIBLE_LOG_LINES,
) -> List[str]:
    """按日期倒序回放指定频道的最新若干条日志。"""
    if limit <= 0:
        return []
    root = _logs_root(output_dir)
    day_dirs = sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name)
    if not day_dirs:
        return []

    safe_channel = _safe_channel_name(channel)
    remaining = int(limit)
    batches: List[List[str]] = []
    for day_dir in reversed(day_dirs):
        path = day_dir / f"{safe_channel}.jsonl"
        batch = _read_last_messages(path, limit=remaining)
        if not batch:
            continue
        batches.append(batch)
        remaining -= len(batch)
        if remaining <= 0:
            break

    out: List[str] = []
    for batch in reversed(batches):
        out.extend(batch)
    return out[-limit:]


__all__ = [
    "MAX_VISIBLE_LOG_LINES",
    "append_runtime_log",
    "read_latest_runtime_logs",
]
