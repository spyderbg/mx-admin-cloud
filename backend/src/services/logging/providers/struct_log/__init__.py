"""Structlog-based logging provider (provider package).

Importing this package registers ``StructLogProvider`` with ``LoggingProviderFactory``
(provider.py calls register() at import time).
"""

from src.services.logging.providers.struct_log.provider import StructLogProvider

__all__ = ["StructLogProvider"]
