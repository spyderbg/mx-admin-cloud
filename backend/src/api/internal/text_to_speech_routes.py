"""Internal generator-configuration and generation endpoints.

Exposes TTS provider (generator) configs loaded from "backend/config/text_to_speech/".
All endpoints require a JWT with the role "internal" or "admin".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException  # pylint: disable=import-error

from src.api.dependencies import get_registry, require_internal
from src.api.internal.schemas.text_to_speech import GenerateRequest, GenerateWordRequest, TtsGenerateResult
from src.core.config import settings
from src.services.service_registry import ServiceRegistry
from src.services.text_to_speech.models import TTSRequest
from src.services.text_to_speech.service import (
    NoActiveTTSProvidersError,
    TTSProviderNotFoundError,
    TextToSpeechService,
)

router = APIRouter(prefix="/internal", tags=["internal-generators"])


@router.get(
    "/generators",
    summary="List text to speech service configurations",
    description=(
        "Return all configured text to speech services with their activation state, "
        "config path, and generation settings. Language lists are omitted from the response."
    ),
)
async def list_generators(
    _user: dict = Depends(require_internal),
    registry: ServiceRegistry = Depends(get_registry),
) -> list[dict[str, Any]]:
    """Return configured generators with activation states."""
    service = registry.get_service("text_to_speech")
    if not isinstance(service, TextToSpeechService):
        raise HTTPException(status_code=500, detail="Service registry mismatch for text_to_speech.")
    configs = service.providers_snapshot()
    return [
        {
            "name": c.name,
            "enabled": c.config.get("generate", {}).get("enabled", False),
            "config_path": str(c.config_path),
            "generate": {k: v for k, v in c.config.get("generate", {}).items() if k != "languages"},
            "evaluate_enabled": c.config.get("evaluate", {}).get("enabled", False),
        }
        for c in configs
    ]


@router.post(
    "/generators/{provider}/generate",
    response_model=TtsGenerateResult,
    summary="Generate text to speech audio for one request",
    description="Generate raw audio using the named text to speech service and return the result as JSON.",
)
async def generate_single(
    provider: str,
    body: GenerateRequest,
    _user: dict = Depends(require_internal),
    registry: ServiceRegistry = Depends(get_registry),
) -> dict[str, Any]:
    """Generate raw audio for a single text to speech request via the named text to speech service."""
    service = registry.get_service("text_to_speech")
    if not isinstance(service, TextToSpeechService):
        raise HTTPException(status_code=500, detail="Service registry mismatch for text_to_speech.")

    request_id = body.request_id or f"api-{body.row_index}-{body.column_index or 0}-{body.language_code}"
    tts_request = TTSRequest(
        request_id=request_id,
        text=body.text,
        language_code=body.language_code,
        language_name=body.language_name,
        filename_text=body.filename_text,
        expected_text=body.expected_text,
        source_file=body.source_file,
        source_type=body.source_type,
        output_format=body.output_format,
        row_index=body.row_index,
        column_index=body.column_index,
    )

    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            generated = await service.generate(
                tts_request,
                client,
                provider_name=provider,
            )
        except NoActiveTTSProvidersError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except TTSProviderNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "provider": generated.provider,
        "request_id": generated.request_id,
        "language_code": generated.language_code,
        "language_name": generated.language_name,
        "expected_text": generated.expected_text,
        "filename_text": generated.filename_text,
        "output_format": generated.output_format,
        "raw_path": str(generated.raw_path),
        "raw_extension": generated.raw_extension,
        "latency_seconds": generated.latency_seconds,
        "success": generated.success,
        "error": generated.error,
        "normalized_path": str(generated.normalized_path) if generated.normalized_path else None,
        "normalization_error": generated.normalization_error,
    }


@router.post(
    "/generators/generate-word",
    summary="Generate text to speech audio for a single word",
    description="Generate raw audio using the highest-priority active text to speech service and return raw bytes.",
)
async def generate_word(
    body: GenerateWordRequest,
    _user: dict = Depends(require_internal),
    registry: ServiceRegistry = Depends(get_registry),
):
    """Generate audio for a single word and return raw audio bytes."""
    from src.core.languages import language_name, normalize_language_code  # pylint: disable=import-outside-toplevel
    from fastapi.responses import Response  # pylint: disable=import-outside-toplevel

    if not body.word or not body.word.strip():
        raise HTTPException(status_code=400, detail="Word must be non-empty.")

    service = registry.get_service("text_to_speech")
    if not isinstance(service, TextToSpeechService):
        raise HTTPException(status_code=500, detail="Service registry mismatch for text_to_speech.")
    if not service.active_providers:
        raise HTTPException(status_code=503, detail="No active text to speech services configured.")

    lang_code = normalize_language_code(body.lang)
    lang_full = language_name(lang_code)
    safe_text = settings.slugify_filename(body.word.strip(), "item")
    tts_request = TTSRequest(
        request_id=f"word-{safe_text}-{lang_code}",
        text=body.word.strip(),
        language_code=lang_code,
        language_name=lang_full,
        filename_text=body.word.strip(),
        expected_text=body.word.strip(),
        source_file="generate-word-endpoint",
        source_type="inline",
        output_format="mp3",
        row_index=0,
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            result = await service.generate(tts_request, client)
        except NoActiveTTSProvidersError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    if not result.success:
        raise HTTPException(status_code=500, detail=result.error or "Text to speech generation failed.")

    audio_bytes = Path(result.raw_path).read_bytes()
    return Response(content=audio_bytes, media_type="audio/mpeg")
