"""Make the scripts/ modules importable the way they import each other when run directly, and the
feature_coordinator package (at the repository root) importable as well.

Run from the repository root, together with the coordinator's own tests:
    python -m pytest -p no:cacheprovider scripts/tests feature_coordinator/tests
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
for path in (SCRIPTS_DIR, SCRIPTS_DIR.parent):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
