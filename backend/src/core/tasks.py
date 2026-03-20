"""Task helpers used by API routes."""

import os
import platform
from typing import Any

from src.core.config import settings

# Keys whose values must not be exposed in API responses
_SENSITIVE_KEYS = {"SECRET_KEY", "OPENAI_API_KEY"}


def _env_from_settings() -> dict[str, Any]:
    """Build a dict of settings (env vars used in project) with sensitive values masked."""
    env = {}
    for name, value in settings.model_dump().items():
        if name in _SENSITIVE_KEYS:
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

def list_files(depth: int = 1) -> dict[str, Any]:
    """List files in the configured content directory."""
    directory = settings.CONTENT_DIR
    if not os.path.exists(directory):
        return {"error": f"Directory '{directory}' does not exist"}
    try:
        files = os.listdir(directory)
        return {"directory": directory, "files": files, "depth": depth}
    except OSError as err:
        return {"error": str(err)}
