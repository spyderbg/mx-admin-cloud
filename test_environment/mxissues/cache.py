"""Local fingerprint -> issue number cache (one per tracker), a fast path only.

The API is the source of truth (implementation_plan.md §11) — this cache may be absent
or stale on another machine (or after someone deletes an issue by hand), so a cache
miss — or a cache hit that turns out to be wrong — always falls through to a real
search, never straight to "create". See mxissues/sync.py.
"""

from __future__ import annotations

import json

from .paths import cache_file


def load(tracker_name: str) -> dict[str, int]:
    path = cache_file(tracker_name)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): int(v) for k, v in data.items()} if isinstance(data, dict) else {}


def save(tracker_name: str, cache: dict[str, int]) -> None:
    path = cache_file(tracker_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
