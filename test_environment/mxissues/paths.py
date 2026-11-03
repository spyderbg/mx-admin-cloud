"""Path constants for the issue tracking (Workstream 2) tooling."""

from __future__ import annotations

from pathlib import Path

from testenvlib.paths import REPO_ROOT

MXISSUES_DIR = REPO_ROOT / ".mxissues"


def cache_file(tracker_name: str) -> Path:
    """One fingerprint cache per tracker — a GitLab iid means nothing on GitHub."""
    return MXISSUES_DIR / f"index-{tracker_name}.json"
