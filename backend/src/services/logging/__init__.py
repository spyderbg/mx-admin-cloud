"""Logging subsystem: service class, factory, and provider implementations.

Public interface: the service/provider classes plus the **wire contract** (``LogLevel`` and the
``Log*Request`` bodies) that other services and ``core.log_client`` use to emit records over HTTP.
"""

from pathlib import Path

import src.services.logging.providers  # noqa: F401 — triggers provider auto-registration

from src.services.acceptance import ServiceAcceptCriteria
from src.services.models import ServiceProvider
from src.services.logging.models import LoggingServiceProvider, LogLevel
from src.services.logging.schemas import LogApiRequest, LogProviderRequest, LogRequest, LogServiceRequest
from src.services.logging.service import (
    LoggingProviderFactory,
    LoggingService,
)

__all__ = [
    "LoggingService",
    "LoggingServiceProvider",
    "LoggingProviderFactory",
    # Wire contract (see backend/CLAUDE.md "Logging — always out-of-process").
    "LogLevel",
    "LogRequest",
    "LogApiRequest",
    "LogProviderRequest",
    "LogServiceRequest",
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
    available_providers=LoggingProviderFactory.available_names,
)
