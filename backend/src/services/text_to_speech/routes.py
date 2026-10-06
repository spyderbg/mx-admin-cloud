"""Internal generator-configuration and generation endpoints.

Exposes TTS provider (generator) configs loaded from "backend/config/text_to_speech/".
All endpoints require a JWT with the role "internal" or "admin".
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status  # pylint: disable=import-error

from src.api.dependencies import get_registry, require_internal
from src.api.service_resolver import get_typed_service, require_active_providers
from src.core.config import settings
from src.services import text_to_speech
from src.services.service_registry import ServiceRegistry
from src.services.text_to_speech.models import TTSRequest
from src.services.text_to_speech.schemas import (
    GenerateRequest,
    GenerateWordRequest,
    GeneratorProviderInfo,
    TtsGenerateResult,
)
from src.services.text_to_speech.service import (
    NoActiveTTSProvidersError,
    TTSProviderNotFoundError,
    TextToSpeechService,
)

router = APIRouter(prefix="/internal/generators", tags=["internal-generators"])

GENERATORS_LIST = "/"
GENERATOR_SINGLE_GENERATE = "/{provider}/generate"
GENERATE_WORD = "/generate-word"


@router.get(
    GENERATORS_LIST,
    response_model=list[GeneratorProviderInfo],
    status_code=status.HTTP_200_OK,
    summary="List text to speech service configurations",
    description=(
            "Return all configured text to speech services with their activation state, "
            "config path, and generation settings. "
            "The per-provider 'languages' list is omitted from the response to keep the "
            "payload compact."
    ),
)
async def list_generators(
        _user: dict = Depends(require_internal),
        registry: ServiceRegistry = Depends(get_registry),
) -> list[dict[str, Any]]:
    """Return configured generators with activation states."""
    service = get_typed_service(registry, text_to_speech.SERVICE_NAME, TextToSpeechService)
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
    GENERATOR_SINGLE_GENERATE,
    response_model=TtsGenerateResult,
    status_code=status.HTTP_200_OK,
    summary="Generate text to speech audio for one request",
    description=(
            "Generate raw audio using the text to speech service named by the 'provider' "
            "path parameter and return the result as JSON, including the raw audio path "
            "and optional normalisation output. "
            "Responds with HTTP 404 when the provider is unknown and HTTP 503 when no "
            "active text to speech providers are configured."
    ),
)
async def generate_single(
        provider: str,
        body: GenerateRequest,
        _user: dict = Depends(require_internal),
        registry: ServiceRegistry = Depends(get_registry),
) -> TtsGenerateResult:
    """Generate raw audio for a single text to speech request via the named text to speech service."""
    service = get_typed_service(registry, text_to_speech.SERVICE_NAME, TextToSpeechService)

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

    return TtsGenerateResult(
        provider=generated.provider,
        request_id=generated.request_id,
        language_code=generated.language_code,
        language_name=generated.language_name,
        expected_text=generated.expected_text,
        filename_text=generated.filename_text,
        output_format=generated.output_format,
        raw_path=str(generated.raw_path),
        raw_extension=generated.raw_extension,
        latency_seconds=generated.latency_seconds,
        success=generated.success,
        error=generated.error,
        normalized_path=str(generated.normalized_path) if generated.normalized_path else None,
        normalization_error=generated.normalization_error,
    )


@router.post(
    GENERATE_WORD,
    status_code=status.HTTP_200_OK,
    summary="Generate text to speech audio for a single word",
    description=(
            "Generate raw MP3 audio for a single word using the highest-priority active "
            "text to speech service and return the raw audio bytes. "
            "With 'configuration_name' that configuration generates instead, even when it is "
            "disabled. 'settings' (speed, stability, similarity) override the configuration's "
            "own voice settings for this request only. "
            "Responds with HTTP 400 when the word is empty, HTTP 404 when the named "
            "configuration does not exist, HTTP 500 when generation fails, and HTTP 503 when "
            "no configuration is named and no active text to speech providers are configured."
    ),
    responses={
        200: {
            "content": {"audio/mpeg": {}},
            "description": "Raw MP3 audio bytes",
        },
        400: {"description": "Word is empty"},
        404: {"description": "Named configuration not found"},
        500: {"description": "TTS generation failed"},
        503: {"description": "No active TTS providers configured"},
    },
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

    service = get_typed_service(registry, text_to_speech.SERVICE_NAME, TextToSpeechService)
    if body.configuration_name is None:
        require_active_providers(
            service,
            service_name=text_to_speech.SERVICE_NAME,
            detail="No active text to speech services configured.",
        )

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
        overrides=body.settings.model_dump(exclude_none=True) if body.settings else {},
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            if body.configuration_name is None:
                result = await service.generate(tts_request, client)
            else:
                result = await service.generate_with_configuration(tts_request, client, body.configuration_name)
        except NoActiveTTSProvidersError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except TTSProviderNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    if not result.success:
        raise HTTPException(status_code=500, detail=result.error or "Text to speech generation failed.")

    audio_bytes = Path(result.raw_path).read_bytes()
    generation_parameters = service.generation_parameters(result.provider, tts_request)
    generation_parameters["output_format"] = tts_request.output_format
    encoded_parameters = base64.urlsafe_b64encode(
        json.dumps(generation_parameters, ensure_ascii=True, sort_keys=True).encode("utf-8"),
    ).decode("ascii")
    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={
            "X-TTS-Provider": result.provider,
            "X-TTS-Parameters": encoded_parameters,
        },
    )
