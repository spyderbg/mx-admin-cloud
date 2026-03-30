"""Internal generator-configuration and generation endpoints.

Exposes TTS provider (generator) configs loaded from ``backend/config/text_to_speech/``.
All endpoints require a JWT with role ``internal`` or ``admin``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException  # pylint: disable=import-error
from pydantic import BaseModel  # pylint: disable=import-error

from src.api.dependencies import get_registry, require_internal
from src.core.config import settings
from src.core.models import TTSRequest
from src.core.registry import ServiceRegistry
from src.services.voice_optimization.normalizer import normalize_audio

router = APIRouter(prefix="/internal", tags=["internal-generators"])


class GenerateRequest(BaseModel):
    """Body for a single TTS generation request."""
    text: str
    language_code: str
    language_name: str
    filename_text: str
    expected_text: str
    source_file: str
    source_type: str
    output_format: str
    row_index: int
    column_index: int | None = None


class GenerateWordRequest(BaseModel):
    """Body for single-word TTS generation."""
    word: str
    lang: str


@router.get(
    "/generators",
    summary="List TTS generator configurations",
    description=(
        "Return all configured TTS providers (generators) with their activation state, "
        "config path, and generation settings. Language lists are omitted from the response."
    ),
)
async def list_generators(
    _user: dict = Depends(require_internal),
) -> list[dict[str, Any]]:
    """Return configured generators with activation states."""
    from src.services.config_loader import load_service_configs  # pylint: disable=import-outside-toplevel
    from src.services.text_to_speech.models import ProviderConfig  # pylint: disable=import-outside-toplevel
    configs = load_service_configs(settings.config_path / "text_to_speech", ProviderConfig)
    return [
        {
            "name": c.name,
            "enabled": c.generate.get("enabled", False),
            "config_path": str(c.config_path),
            "generate": {k: v for k, v in c.generate.items() if k != "languages"},
            "evaluate_enabled": c.evaluate.get("enabled", False),
        }
        for c in configs
    ]


@router.post(
    "/generators/{provider}/generate",
    summary="Generate TTS audio for one request",
    description="Run provider.generate() + normalize_audio() and return the result as JSON.",
)
async def generate_single(
    provider: str,
    body: GenerateRequest,
    _user: dict = Depends(require_internal),
    registry: ServiceRegistry = Depends(get_registry),
) -> dict[str, Any]:
    """Generate audio for a single TTS request via the named provider."""
    matched = [p for p in registry.get("providers") if p.name == provider]
    if not matched:
        raise HTTPException(status_code=404, detail=f"Provider '{provider}' not found or not active.")
    prov = matched[0]

    request_id = f"api-{body.row_index}-{body.column_index or 0}-{body.language_code}"
    tts_request = TTSRequest(
        request_id=request_id,
        text=body.text,
        language_code=body.language_code,
        language_name=body.language_name,
        filename_text=body.filename_text,
        expected_text=body.expected_text,
        source_file=body.source_file,
        source_type=body.source_type,
        row_index=body.row_index,
        column_index=body.column_index,
    )

    async with httpx.AsyncClient(timeout=180.0) as client:
        generated = await prov.generate(tts_request, body.output_format, client)

    generated = await normalize_audio(prov.base_dir, generated)

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
    summary="Generate TTS audio for a single word",
    description="Pick the first active provider, generate audio, and return raw bytes.",
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

    providers = registry.get("providers")
    if not providers:
        raise HTTPException(status_code=503, detail="No active TTS providers configured.")

    prov = providers[0]

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
        row_index=0,
    )

    async with httpx.AsyncClient(timeout=60.0) as client:
        result = await prov.generate(tts_request, "mp3", client)

    if not result.success:
        raise HTTPException(status_code=500, detail=result.error or "TTS generation failed.")

    audio_bytes = Path(result.raw_path).read_bytes()
    return Response(content=audio_bytes, media_type="audio/mpeg")
