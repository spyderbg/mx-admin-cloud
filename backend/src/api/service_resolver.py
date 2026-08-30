"""Shared helpers for resolving services and readiness checks."""

from __future__ import annotations

from typing import TypeVar

from fastapi import HTTPException

from src.services.service_registry import ServiceRegistry

ServiceT = TypeVar("ServiceT")


def get_typed_service(
        registry: ServiceRegistry,
        service_name: str,
        expected_type: type[ServiceT],
) -> ServiceT:
    """Fetch *service_name* and validate it matches *expected_type*."""
    try:
        return registry.get_typed_service(service_name, expected_type)
    except KeyError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Service '{service_name}' is not registered.",
        ) from exc
    except TypeError as exc:
        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


def require_active_providers(service: object, *, service_name: str, detail: str) -> None:
    """Raise an HTTP error when *service* has no active providers."""
    get_active_providers = getattr(service, "get_active_providers", None)
    if get_active_providers is None or not callable(get_active_providers):
        raise HTTPException(
            status_code=500,
            detail=f"Service '{service_name}' does not expose active providers.",
        )
    if not get_active_providers():
        raise HTTPException(status_code=503, detail=detail)
