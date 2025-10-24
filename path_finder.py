import os
import glob
from typing import List, Optional, Tuple


def _existing_drives() -> List[str]:
    """Return existing Windows drive letters (e.g., ['C', 'D']).

    On non-Windows platforms, returns an empty list.
    """
    if os.name != "nt":
        return []
    drives: List[str] = []
    for code in range(ord("C"), ord("Z") + 1):
        d = f"{chr(code)}:\\"
        try:
            if os.path.exists(d):
                drives.append(chr(code))
        except Exception:
            pass
    # Heuristic: prioritize common game drives
    prio = ["D", "E", "F", "C"]
    drives = sorted(drives, key=lambda x: (prio.index(x) if x in prio else 99, x))
    return drives


def _score_candidate(path: str, *, hint: Optional[str]) -> Tuple[int, float]:
    """Score a candidate path for ordering.

    Lower score is better. We use:
    - hint_match: 0 if matches hint, else 1
    - negative mtime (newer first)
    """
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        mtime = 0.0
    hint_match = 1
    if hint:
        try:
            if hint.lower() in path.lower():
                hint_match = 0
        except Exception:
            pass
    return (hint_match, -mtime)


def search_wegame_launchers(*, game_dir_hint: Optional[str] = None) -> List[str]:
    """Search likely WeGame launcher paths across available drives.

    Typical pattern:
      <Drive>:\\WeGameApps\\rail_apps\\*\\WeGameLauncher\\launcher.exe

    Args:
        game_dir_hint: optional substring to prioritize, e.g. "(2001688)" or "暗区突围".

    Returns:
        List of absolute paths (sorted by priority). Empty when none found or non-Windows.
    """
    if os.name != "nt":
        return []

    patterns = []
    for drv in _existing_drives():
        base = f"{drv}:\\WeGameApps\\rail_apps"
        patterns.append(os.path.join(base, "*", "WeGameLauncher", "launcher.exe"))

    candidates: List[str] = []
    for pat in patterns:
        try:
            for p in glob.iglob(pat):
                try:
                    if os.path.isfile(p):
                        candidates.append(os.path.normpath(p))
                except Exception:
                    pass
        except Exception:
            pass

    # De-dup and sort
    uniq: List[str] = []
    seen = set()
    for p in candidates:
        if p not in seen:
            seen.add(p)
            uniq.append(p)

    uniq.sort(key=lambda p: _score_candidate(p, hint=game_dir_hint))
    return uniq

