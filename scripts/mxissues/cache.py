"""Local fingerprint -> issue iid cache, a fast path only.

The API is the source of truth (implementation_plan.md §11) — this cache may be absent
or stale on another machine (or after someone deletes an issue by hand), so a cache
miss — or a cache hit that turns out to be wrong — always falls through to a real
search, never straight to "create". See mxissues/sync.py.
"""

from __future__ import annotations

import json

from .paths import CACHE_FILE


def load() -> dict[str, int]:
    if not CACHE_FILE.is_file():
        return {}
    try:
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}


def save(cache: dict[str, int]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
