import sys
from src.core.config import settings

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
