"""Internal translator-configuration endpoints.

Exposes translation service configs loaded from ``backend/config/translators/``.
All endpoints require a JWT with role ``internal`` or ``admin``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile  # pylint: disable=import-error
from pydantic import BaseModel  # pylint: disable=import-error

from src.api.dependencies import get_registry, require_internal
from src.core.config import settings
from src.core.registry import ServiceRegistry


router = APIRouter(prefix="/internal", tags=["internal-translators"])


class TranslateRequest(BaseModel):
    """Request body for the translation endpoint."""
    text: str
    input_lang_code: str | None = None
    target_lang_code: str
    category: str | None = None


class TranslateResponse(BaseModel):
    """Response body for the translate endpoint."""
    translated_text: str


class TranslateFileResponse(BaseModel):
    """Response body for the translate file endpoint."""
    results: list[dict[str, str]]


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
            "name": c.name,
            "category": c.category,
            "enabled": c.enabled,
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


@router.post(
    "/translate-file",
    response_model=TranslateFileResponse,
    summary="Translate words in a file using active translators",
    description=(
        "Upload a file (TXT, CSV, or XLSX) and translate all words from *input_lang_code* "
        "to *target_lang_code* using all active translators in priority order."
    ),
)
async def translate_file(
    file: UploadFile = File(...),
    input_lang_code: str | None = None,
    target_lang_code: str = "en",
    category: str | None = None,
    _user: dict = Depends(require_internal),
    registry: ServiceRegistry = Depends(get_registry),
) -> TranslateFileResponse:
    """Translate words in an uploaded file via the registry's active translators."""
    from src.core.input_loader import load_requests  # pylint: disable=import-outside-toplevel
    from src.services.translation import translate_with_all_active  # pylint: disable=import-outside-toplevel
    import uuid
    import os

    if not registry.translators:
        raise HTTPException(status_code=503, detail="No active translators are configured.")

    # Temporarily save the file to parse it
    job_id = str(uuid.uuid4())
    suffix = os.path.splitext(file.filename or "input.txt")[1].lower()
    temp_path = settings.output_path / f"temp_translate_{job_id}{suffix}"
    settings.output_path.mkdir(parents=True, exist_ok=True)

    try:
        content = await file.read()
        with open(temp_path, "wb") as f:
            f.write(content)

        requests = load_requests(temp_path, input_lang_code or "en")
        results = []

        for req in requests:
            translated = await translate_with_all_active(
                registry.translators,
                text=req.text,
                input_lang_code=req.language_code,
                target_lang_code=target_lang_code,
                category=category,
            )
            results.append({
                "original": req.text,
                "translated": translated,
                "language": req.language_code,
            })

        return TranslateFileResponse(results=results)

    finally:
        if temp_path.exists():
            temp_path.unlink()
