"""Task helpers used by API routes."""

import os
import platform
from typing import Any

from src.core.config import settings

# Keys whose values must not be exposed in API responses
_SENSITIVE_KEYS = {"SECRET_KEY", "DEFAULT_ADMIN_PASSWORD", "OPENAI_API_KEY", "ELEVENLABS_API_KEY", "SPEAKTOR_API_KEY"}


def _env_from_settings() -> dict[str, Any]:
    """Build a dict of settings (env vars used in project) with sensitive values masked."""
    env = {}
    for name, value in settings.model_dump().items():
        if name in _SENSITIVE_KEYS or name.endswith("_API_KEY"):
            env[name] = "***" if value else "<unset>"
        else:
            env[name] = value
    return env

def ping() -> dict[str, str]:
    """Return a basic health response including runtime platform."""
    platform_info = platform.platform()
    return {"message": f"Pong from KiddoWords backend!\nPlatform: {platform_info}"}

def status() -> dict[str, Any]:
    """Return service status and non-sensitive configuration."""
    return {
        "status": "All services operational.",
        "env": _env_from_settings(),
    }

def operation(operation_name: str) -> dict[str, str]:
    """Return a placeholder operation result."""
    return {"operation": operation_name, "result": "success"}

def _collect_tree(directory: str, prefix: str, levels: int, out: list[str]) -> None:
    """Append *directory*'s entries to *out*, descending into sub-folders while *levels* > 1."""
    with os.scandir(directory) as entries:
        ordered = sorted(entries, key=lambda entry: entry.name)
    for entry in ordered:
        path = prefix + entry.name
        # Symlinks are never followed, so the walk cannot leave the content tree or loop.
        if entry.is_dir(follow_symlinks=False):
            out.append(path + "/")
            if levels > 1:
                _collect_tree(entry.path, path + "/", levels - 1, out)
        else:
            out.append(path)


def list_files(depth: int = 1) -> dict[str, Any]:
    """List the configured content directory down to *depth* folder levels.

    ``depth=1`` lists the top level only, ``depth=2`` adds each top-level folder's contents, and so on.
    ``files`` holds paths relative to the content directory in tree order (every folder is followed by
    its contents, names sorted), and folders end with ``/`` so they can be told apart from files.
    """
    directory = settings.content_path
    if not os.path.exists(directory):
        return {"error": f"Directory '{directory}' does not exist"}
    try:
        files: list[str] = []
        _collect_tree(str(directory), "", max(1, depth), files)
        return {"directory": directory, "files": files, "depth": depth}
    except OSError as err:
        return {"error": str(err)}
