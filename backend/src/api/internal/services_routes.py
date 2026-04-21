"""Unified /internal/services endpoint — aggregates all registered service providers.

All endpoints require a JWT with the role "internal" or "admin".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.dependencies import get_registry, require_internal
from src.services.models import ServiceProviderInfo
from src.services.service_registry import ServiceRegistry

router = APIRouter(prefix="/internal", tags=["internal-services"])


@router.get(
    "/services",
    response_model=list[ServiceProviderInfo],
    summary="List all configured service providers",
    description=(
            "Aggregate translators, generators, optimizers, and evaluators into one response. "
            "Each entry is tagged with its category so the caller needs only one request."
    ),
)
async def list_all_services(
        _user: dict = Depends(require_internal),
        registry: ServiceRegistry = Depends(get_registry),
) -> list[ServiceProviderInfo]:
    """Return all configured service providers across every category."""
    results: list[ServiceProviderInfo] = []

    registered_services = registry.get_registered_services()

    for service in registered_services.values():
        providers = service.providers_snapshot()
        sorted_providers = sorted(providers, key=lambda x: (not x.enabled, x.priority))
        results.extend(p.get_info() for p in sorted_providers)

    return results
