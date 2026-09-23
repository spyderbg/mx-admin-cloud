"""Path constants for the GitLab issue tracking (Workstream 2) tooling."""

from __future__ import annotations

from testenvlib.paths import REPO_ROOT

MXISSUES_DIR = REPO_ROOT / ".mxissues"
CACHE_FILE = MXISSUES_DIR / "index.json"
