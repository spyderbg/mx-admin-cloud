"""Make the scripts/ modules importable the way they import each other when run directly.

Run from the repository root:  python -m pytest -p no:cacheprovider scripts/tests
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
