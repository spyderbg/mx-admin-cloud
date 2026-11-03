"""`python -m feature_coordinator` — the feature coordinator's CLI (see cli.py).

Run it from the repository root, the directory that contains this package."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
