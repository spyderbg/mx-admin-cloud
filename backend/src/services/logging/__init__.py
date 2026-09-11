"""Logging subsystem: service class, factory, and provider implementations."""

from pathlib import Path

import src.services.logging.providers  # noqa: F401 — triggers provider auto-registration

from src.services.acceptance import ServiceAcceptCriteria
from src.services.models import ServiceProvider
from src.services.logging.models import LoggingServiceProvider
from src.services.logging.service import (
    LoggingProviderFactory,
    LoggingService,
)

__all__ = [
    "LoggingService",
    "LoggingServiceProvider",
    "LoggingProviderFactory",
]


def _validate_logging_provider(service_provider: ServiceProvider) -> None:
    provider_name = service_provider.provider
    if not LoggingProviderFactory.supports(provider_name):
        raise ValueError(
            f"Unsupported logging provider: '{provider_name}' "
            f"(file='{service_provider.config_path}')"
        )


def _build_logging_provider(
        provider: ServiceProvider,
        base_dir: Path,
) -> LoggingServiceProvider:
    return LoggingProviderFactory.create(provider, base_dir)


SERVICE_CLASS = LoggingService  # pylint: disable=invalid-name
SERVICE_NAME = "logging"
SERVICE_ACCEPT_CRITERIA = ServiceAcceptCriteria(
    service_name=SERVICE_NAME,
    config_subdir="logging",
    validate_provider=_validate_logging_provider,
    build_provider=_build_logging_provider,
)
