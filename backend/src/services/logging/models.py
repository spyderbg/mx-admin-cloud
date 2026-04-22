"""Logging service provider base class and data models."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from src.services.models import ServiceProvider


class LoggingServiceProvider(ServiceProvider, ABC):
    """Base class for logging service providers.

    Subclasses configure logging subsystems (stdlib, structlog, request logging)
    from the "config" dict stored by "ServiceProvider" and expose runtime
    helpers for structured log emission.
    """

    @abstractmethod
    def configure(self, *, output_dir: Path, profile: str) -> None:
        """Apply logging configuration using this provider's config dict."""

    def apply_levels(self) -> None:
        """Patch handler/logger levels from the current config without recreating handlers.

        The default implementation is a no-op; providers that support live level
        updates should override this.
        """

    @abstractmethod
    def get_logger(self, name: str) -> Any:
        """Return a logger instance for *name*."""

    @abstractmethod
    def emit(self, logger_name: str, level: str, event: str, **fields: Any) -> None:
        """Emit a structured log message through the named logger."""

    @abstractmethod
    def bind_request_id(self, request_id: str) -> None:
        """Bind a request ID to the logging context."""

    @abstractmethod
    def clear_request_context(self) -> None:
        """Clear the logging request context."""

    @abstractmethod
    def log_to_all_active_loggers(
            self, message: str, level: int = logging.INFO,
    ) -> None:
        """Broadcast *message* to root and all standalone active loggers."""
