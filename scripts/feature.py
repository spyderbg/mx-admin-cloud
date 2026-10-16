#!/usr/bin/env python3
"""Feature coordinator CLI — see scripts/feature_coordinator/cli.py and
docs_agents/coordination/feature-workflow.md.

    python scripts/feature.py --help
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from feature_coordinator.cli import main  # noqa: E402  pylint: disable=wrong-import-position

if __name__ == "__main__":
    sys.exit(main())
