"""Finding fingerprints — the make-or-break part of dedup (implementation_plan.md §11).

A detector run nightly, or a suite that fails the same way twice, must not open
duplicate issues. Each finding's `fingerprint_key` (built per-source — see
sources/*.py) gets hashed here into a short, stable id embedded in the issue body as a
fenced ```mx-meta block, so both humans and this tool can read it back later.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

FINGERPRINT_LENGTH = 12

_META_BLOCK_RE = re.compile(r"```mx-meta\s*\n(.*?)\n```", re.DOTALL)


def compute(source: str, key: str) -> str:
    """Return a 12-hex-char fingerprint for *key*, namespaced by *source*.

    The source prefix guards against two different sources (say, a pylint symbol name
    and an e2e spec title) ever producing the same raw key string and colliding into one
    fingerprint — fingerprints are looked up globally across all issues, not scoped by
    source label.
    """
    digest = hashlib.sha1(f"{source}:{key}".encode("utf-8")).hexdigest()
    return digest[:FINGERPRINT_LENGTH]


def build_meta_block(*, fingerprint: str, source: str, key: str, first_seen: str, occurrences: int) -> str:
    """Render the fenced ```mx-meta block embedded in an issue's description."""
    payload = {
        "fp": fingerprint,
        "source": source,
        "key": key,
        "first_seen": first_seen,
        "occurrences": occurrences,
    }
    return "```mx-meta\n" + json.dumps(payload, indent=2, sort_keys=True) + "\n```"


def parse_meta_block(description: str) -> dict[str, Any] | None:
    """Extract and parse the ```mx-meta block from an issue description, if present."""
    match = _META_BLOCK_RE.search(description or "")
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except (ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def replace_meta_block(description: str, new_block: str) -> str:
    """Replace an existing ```mx-meta block with *new_block*, or append it if absent."""
    if _META_BLOCK_RE.search(description or ""):
        return _META_BLOCK_RE.sub(new_block, description, count=1)
    separator = "\n\n" if description and not description.endswith("\n") else "\n" if description else ""
    return f"{description}{separator}{new_block}"
