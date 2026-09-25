"""File-system backed content provider (package).

Importing this package registers FileSystemContentProvider with the content provider
factory (provider.py calls register() at import time).
"""

from src.services.content.providers.file_system.constants import VoiceCreationMethod
from src.services.content.providers.file_system.internal_api import _extension_for_content_type
from src.services.content.providers.file_system.provider import FileSystemContentProvider

__all__ = ["FileSystemContentProvider", "VoiceCreationMethod", "_extension_for_content_type"]
