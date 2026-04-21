"""Unified /internal/services endpoint — aggregates all registered service providers.

All endpoints require a JWT with the role "internal" or "admin".
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from src.api.dependencies import get_registry, require_internal
from src.api.service_resolver import get_typed_service
from src.services import text_to_speech, text_translation, voice_optimization
from src.services.service_registry import ServiceRegistry
from src.services.text_to_speech.service import TextToSpeechService
from src.services.text_translation import TextTranslationService
from src.services.voice_optimization.service import VoiceOptimizationService

router = APIRouter(prefix="/internal", tags=["internal-services"])


class ServiceSummary(BaseModel):
    """One service provider entry in the aggregated /services response."""

    category: str
    name: str
    enabled: bool
    priority: int
    provider: str | None = None
    config_path: str | None = None


@router.get(
    "/services",
    response_model=list[ServiceSummary],
    summary="List all configured service providers",
    description=(
        "Aggregate translators, generators, optimizers, and evaluators into one response. "
        "Each entry is tagged with its category so the caller needs only one request."
    ),
)
async def list_all_services(
        _user: dict = Depends(require_internal),
        registry: ServiceRegistry = Depends(get_registry),
) -> list[dict[str, Any]]:
    """Return all configured service providers across every category."""
    results: list[dict[str, Any]] = []

    tl_service = get_typed_service(registry, text_translation.SERVICE_NAME, TextTranslationService)
    for c in sorted(tl_service.providers_snapshot(), key=lambda c: c.priority):
        results.append({
            "category": "translators",
            "name": c.name,
            "enabled": c.enabled,
            "priority": c.priority,
            "provider": c.provider,
            "config_path": str(c.config_path),
        })

    tts_service = get_typed_service(registry, text_to_speech.SERVICE_NAME, TextToSpeechService)
    tts_configs = sorted(tts_service.providers_snapshot(), key=lambda c: c.priority)
    for c in tts_configs:
        results.append({
            "category": "generators",
            "name": c.name,
            "enabled": c.config.get("generate", {}).get("enabled", False),
            "priority": c.priority,
            "provider": c.provider,
            "config_path": str(c.config_path),
        })

    opt_service = get_typed_service(registry, voice_optimization.SERVICE_NAME, VoiceOptimizationService)
    for c in sorted(opt_service.providers_snapshot(), key=lambda c: c.priority):
        results.append({
            "category": "optimizers",
            "name": c.name,
            "enabled": c.enabled,
            "priority": c.priority,
            "provider": c.provider,
            "config_path": str(c.config_path),
        })

    for c in tts_configs:
        results.append({
            "category": "evaluators",
            "name": c.name,
            "enabled": c.config.get("evaluate", {}).get("enabled", False),
            "priority": c.priority,
            "provider": c.provider,
            "config_path": str(c.config_path),
        })

    return results
