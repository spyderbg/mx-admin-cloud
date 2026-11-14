"""File-system backed configuration provider (provider package).

Importing this package registers ``FileSystemConfigurationProvider`` with ``ConfigurationProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.configuration.providers.file_system.provider import FileSystemConfigurationProvider

__all__ = ["FileSystemConfigurationProvider"]
