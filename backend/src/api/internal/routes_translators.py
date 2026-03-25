"""Internal translator-configuration endpoints.

Exposes translation service configs loaded from ``backend/config/translators/``.
All endpoints require a JWT with role ``internal`` or ``admin``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException  # pylint: disable=import-error
from pydantic import BaseModel  # pylint: disable=import-error

from src.api.dependencies import get_registry, require_internal
from src.core.config import settings
from src.core.registry import ServiceRegistry

router = APIRouter(prefix="/internal", tags=["internal-translators"])


class TranslateRequest(BaseModel):
    """Request body for the translate endpoint."""
    text: str
    target_lang_code: str
    input_lang_code: str | None = None
    category: str | None = None


class TranslateResponse(BaseModel):
    """Response body for the translate endpoint."""
    translated_text: str


@router.get(
    "/translators",
    summary="List translator configurations",
    description=(
        "Return all configured translation services with their service type, "
        "activation state, priority, and config path."
    ),
)
async def list_translators(
    _user: dict = Depends(require_internal),
) -> list[dict[str, Any]]:
    """Return configured translators with service metadata."""
    from src.core.config_loader import load_translator_configs  # pylint: disable=import-outside-toplevel
    configs = load_translator_configs(
        base_dir=settings.BASE_DIR,
        config_dir=settings.config_path,
    )
    return [
        {
            "service_type": c.type,
            "name": c.name,
            "active": c.active,
            "priority": c.priority,
            "config_path": str(c.config_path),
        }
        for c in configs
    ]


@router.post(
    "/translate",
    response_model=TranslateResponse,
    summary="Translate text using active translators",
    description=(
        "Translate *text* from *input_lang_code* to *target_lang_code* using all "
        "active translators in priority order. Returns the last successful translation."
    ),
)
async def translate_text(
    body: TranslateRequest,
    _user: dict = Depends(require_internal),
    registry: ServiceRegistry = Depends(get_registry),
) -> TranslateResponse:
    """Translate text via the registry's active translators."""
    from src.services.translation import translate_with_all_active  # pylint: disable=import-outside-toplevel
    if not registry.translators:
        raise HTTPException(status_code=503, detail="No active translators are configured.")
    result = await translate_with_all_active(
        registry.translators,
        text=body.text,
        input_lang_code=body.input_lang_code,
        target_lang_code=body.target_lang_code,
        category=body.category,
    )
    return TranslateResponse(translated_text=result)
