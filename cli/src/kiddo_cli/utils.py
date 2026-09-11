"""Runtime environment helper functions (platform detection and debug/production mode checks)."""

import sys
import os

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
    """Check if the application is in debug mode (based on environment variable)."""
    return os.getenv("KIDDO_DEBUG", "false").lower() == "true"

def is_production() -> bool:
    """Check if the application is in production mode."""
    return not is_debug_mode()
