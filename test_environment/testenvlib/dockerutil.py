"""Docker/Compose binary resolution.

Docker Desktop on macOS installs the `docker` CLI at ~/.docker/bin/docker, symlinked
from the app bundle, and only reaches PATH once a *new* shell picks up the profile
change Docker Desktop's first run makes — so immediately after installing or starting
Docker Desktop, `docker` is routinely missing from PATH in the current session even
though the daemon is already up. Fall back to the well-known install locations before
concluding Docker isn't available.
"""

from __future__ import annotations

import shutil
from pathlib import Path

_FALLBACK_LOCATIONS = (
    Path.home() / ".docker" / "bin" / "docker",
    Path("/Applications/Docker.app/Contents/Resources/bin/docker"),
    Path("/usr/local/bin/docker"),
    Path("/opt/homebrew/bin/docker"),
)


def find_docker() -> tuple[str | None, bool]:
    """Return (path-to-docker-or-None, found_via_path_fallback)."""
    on_path = shutil.which("docker")
    if on_path:
        return on_path, False
    for candidate in _FALLBACK_LOCATIONS:
        if candidate.is_file():
            return str(candidate), True
    return None, False
