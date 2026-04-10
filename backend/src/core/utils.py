"""Runtime environment helper functions (platform detection, debug/production mode, config parsing)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from src.core.config import settings


class JsonObjectReadError(ValueError):
    """Raised when a JSON payload cannot be read as an object."""


def read_json_object(source: Path | bytes | str, require_object: bool = True) -> Any:
    """Read JSON from a file path or raw JSON bytes/string."""
    if isinstance(source, Path):
        if not source.exists():
            raise JsonObjectReadError("File is missing")
        try:
            payload_text = source.read_text(encoding="utf-8")
        except OSError as exc:
            raise JsonObjectReadError("Invalid JSON file") from exc
    elif isinstance(source, bytes):
        try:
            payload_text = source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise JsonObjectReadError("Invalid JSON file") from exc
    else:
        payload_text = source

    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise JsonObjectReadError(f"JSON parser error at line: {exc.lineno}") from exc

    if require_object and not isinstance(payload, dict):
        raise JsonObjectReadError("JSON root must be an object")
    return payload


def boolish(value: object) -> bool:
    """Coerce a config value to bool; accepts True/False, None, and common string truths."""
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

def is_windows_platform() -> bool:
    """Check if the current platform is Windows."""
    return sys.platform == "win32"

def is_linux_platform() -> bool:
    """Check if the current platform is Linux."""
    return sys.platform.startswith("linux")

def is_darwin_platform() -> bool:
    """Check if the current platform is macOS."""
    return sys.platform == "darwin"

def is_debug_mode() -> bool:
    """Check if the application is in debug mode."""
    return settings.DEBUG_MODE

def is_production() -> bool:
    """Check if the application is in production mode."""
    return not settings.DEBUG_MODE
