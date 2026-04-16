"""Internal translator-configuration endpoints.

Exposes translation service configs loaded from "backend/config/text_translation/".
All endpoints require a JWT with the role "internal" or "admin".
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, UploadFile  # pylint: disable=import-error

from src.api.dependencies import (
    get_registry,
    require_internal
)
from src.api.internal.schemas.common import ErrorMessage
from src.api.internal.schemas.text_translation import (
    LangCodeText,
    TranslateFileResponse,
    TranslateRequest,
    TranslateResponse,
    TranslationFileSourceWord,
)
from src.api.service_resolver import get_typed_service, require_active_providers
from src.core.config import settings
from src.core.languages import normalize_language_code
from src.services import text_translation
from src.services.models import ServiceProviderInfo
from src.services.service_registry import ServiceRegistry
from src.services.text_translation import TextTranslationService

TRANSLATORS_LIST = "/translators"
TRANSLATE_TEXT = "/translate"
TRANSLATE_FILE = "/translate-file"

router = APIRouter(prefix="/internal", tags=["internal-translators"])


@router.get(
    TRANSLATORS_LIST,
    response_model=list[ServiceProviderInfo],
    summary="List text translation service configurations",
    description=(
            "Return all configured text translation services with their service type, "
            "activation state, priority, and config path."
    ),
    responses={
        200: {"model": list[ServiceProviderInfo],
              "description": "Configured translator providers sorted by priority"},
        401: {"model": ErrorMessage, "description": "Missing or invalid JWT token"},
        403: {"model": ErrorMessage,
              "description": "JWT is valid but does not carry the &#x60;internal&#x60; or &#x60;admin&#x60; role"},
    }
)
async def list_translators(
        _user: dict = Depends(require_internal),
        registry: ServiceRegistry = Depends(get_registry),
) -> list[dict[str, Any]]:
    """Return configured translators with service metadata."""
    service = get_typed_service(
        registry=registry,
        service_name=text_translation.SERVICE_NAME,
        expected_type=TextTranslationService,
    )
    configs = service.providers_snapshot()
    configs.sort(key=lambda c: c.priority)
    return [
        {
            "name": c.name,
            "service": c.service,
            "provider": c.provider,
            "enabled": c.enabled,
            "priority": c.priority,
            "config_path": str(c.config_path),
        }
        for c in configs
    ]


@router.post(
    TRANSLATE_TEXT,
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
    service = get_typed_service(
        registry=registry,
        service_name=text_translation.SERVICE_NAME,
        expected_type=TextTranslationService,
    )
    require_active_providers(
        service,
        service_name=text_translation.SERVICE_NAME,
        detail="No active translators are configured.",
    )
    result = await service.translate_text(
        text=body.text,
        input_lang_code=body.input_lang_code,
        target_lang_code=body.target_lang_code,
        category=body.category,
    )
    if result.success and result.lang_text is not None:
        lang_text = LangCodeText(
            lang_code=body.target_lang_code,
            text=result.lang_text.text,
        )
    else:
        lang_text = LangCodeText(
            lang_code=normalize_language_code(body.input_lang_code),
            text=body.text,
        )
    return TranslateResponse(
        success=result.success,
        lang_text=lang_text,
    )


@router.post(
    TRANSLATE_FILE,
    response_model=TranslateFileResponse,
    summary="Translate words in a file using active translators",
    description=(
            "Upload a file (TXT, CSV, or XLSX) and translate segments whose language differs from "
            "*target_lang_code* (per row/column language) using active translators in priority order."
    ),
)
async def translate_file(
        # pylint: disable=too-many-locals
        file: UploadFile = File(...),
        input_lang_code: str | None = None,
        target_lang_code: str = "en",
        category: str | None = None,
        _user: dict = Depends(require_internal),
        registry: ServiceRegistry = Depends(get_registry),
) -> TranslateFileResponse:
    """Translate words in an uploaded file via the registry's active translators."""
    from src.core.input_loader import load_words  # pylint: disable=import-outside-toplevel

    service = get_typed_service(
        registry=registry,
        service_name=text_translation.SERVICE_NAME,
        expected_type=TextTranslationService,
    )
    require_active_providers(
        service,
        service_name=text_translation.SERVICE_NAME,
        detail="No active translators are configured.",
    )

    # Temporarily save the file to parse it
    job_id = str(uuid.uuid4())
    suffix = os.path.splitext(file.filename or "input.txt")[1].lower()
    temp_path = settings.output_path / f"temp_translate_{job_id}{suffix}"
    settings.output_path.mkdir(parents=True, exist_ok=True)

    try:
        content = await file.read()
        with open(temp_path, "wb") as f:
            f.write(content)

        source_lang = input_lang_code or "en"
        loaded = load_words(
            temp_path,
            source_language=source_lang,
            target_language=target_lang_code,
        )
        results = []

        for word in loaded:
            payload = TranslationFileSourceWord.from_core(word)
            translated = await service.translate_text(
                text=payload.text,
                input_lang_code=payload.source_language_code,
                target_lang_code=target_lang_code,
                category=category,
            )
            if translated.success and translated.lang_text is not None:
                translated_text = translated.lang_text.text
                translated_lang = normalize_language_code(target_lang_code)
            else:
                translated_text = payload.text
                translated_lang = normalize_language_code(payload.source_language_code)
            results.append({
                "original": payload.text,
                "translated": translated_text,
                "language": translated_lang,
            })

        return TranslateFileResponse(results=results)

    finally:
        if temp_path.exists():
            temp_path.unlink()
