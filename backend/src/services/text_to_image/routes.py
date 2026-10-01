"""Internal image-generator configuration and generation endpoints.

Exposes image provider configs loaded from ``backend/config/text_to_image/``.
All endpoints require a JWT with the role ``internal`` or ``admin``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from fastapi import (  # pylint: disable=import-error
    APIRouter,
    Depends,
    HTTPException,
    Response,
    status,
)

from src.api.dependencies import get_registry, require_internal
from src.api.service_resolver import get_typed_service, require_active_providers
from src.core.log_client import emit_event
from src.core.logging_route import LoggingAPIRoute
from src.services import text_to_image
from src.services.logging.models import LogLevel
from src.services.provider_strategy import AllProvidersFailedError
from src.services.service_registry import ServiceRegistry
from src.services.text_to_image.models import ImageRequest, media_type_for_extension
from src.services.text_to_image.schemas import (
    GenerateImageBytesRequest,
    GenerateImageRequest,
    ImageGenerateResult,
    ImageGeneratorProviderInfo,
)
from src.services.text_to_image.service import (
    ImageProviderNotFoundError,
    NoActiveImageProvidersError,
    TextToImageService,
)

# Identifies this service in records collected by the logging service, which
# runs in its own process and possibly on its own host.
LOG_SOURCE = "text_to_image"

# Prompts are free-form and can be very long; keep log records readable.
_PROMPT_LOG_LIMIT = 200


def _prompt_preview(prompt: str) -> str:
    """Trim long prompt text down to a loggable excerpt."""
    text = prompt.strip()
    return text if len(text) <= _PROMPT_LOG_LIMIT else f"{text[:_PROMPT_LOG_LIMIT]}…"


async def _log_generation_result(*, endpoint: str, generated: Any) -> None:
    """Record a generation outcome.

    Provider failures are reported in-band on a 200 response, so they never
    reach the route-level failure hook and need their own record here.
    """
    fields: dict[str, Any] = {
        "endpoint": endpoint,
        "provider": generated.provider,
        "request_id": generated.request_id,
        "model": generated.model,
        "latency_seconds": generated.latency_seconds,
        "attempts": generated.attempts,
        "cost_estimate": generated.cost_estimate,
        "width": generated.width,
        "height": generated.height,
    }
    if generated.success:
        await emit_event(
            LOG_SOURCE,
            "image_generation_completed",
            LogLevel.INFO,
            **fields,
            raw_path=str(generated.raw_path),
        )
        return
    await emit_event(
        LOG_SOURCE,
        "image_generation_failed",
        LogLevel.ERROR,
        **fields,
        error=generated.error,
    )


router = APIRouter(
    prefix="/internal/image-generators",
    tags=["internal-image-generators"],
    route_class=LoggingAPIRoute.for_source(LOG_SOURCE, event_prefix="image_generator"),
)

IMAGE_GENERATORS_LIST = "/"
IMAGE_GENERATOR_SINGLE_GENERATE = "/{provider}/generate"
GENERATE_IMAGE = "/generate-image"

# Outer backstop only: per-attempt timeouts come from each provider's traffic
# policy. Local ComfyUI renders can legitimately run for minutes.
_CLIENT_TIMEOUT = httpx.Timeout(600.0, connect=15.0)


def _to_result(generated: Any) -> ImageGenerateResult:
    """Serialize a ``GeneratedImage`` into the wire response model."""
    return ImageGenerateResult(
        provider=generated.provider,
        request_id=generated.request_id,
        prompt=generated.prompt,
        filename_text=generated.filename_text,
        output_format=generated.output_format,
        raw_path=str(generated.raw_path),
        raw_extension=generated.raw_extension,
        latency_seconds=generated.latency_seconds,
        success=generated.success,
        model=generated.model,
        width=generated.width,
        height=generated.height,
        cost_estimate=generated.cost_estimate,
        attempts=generated.attempts,
        error=generated.error,
    )


@router.get(
    IMAGE_GENERATORS_LIST,
    response_model=list[ImageGeneratorProviderInfo],
    status_code=status.HTTP_200_OK,
    summary="List text to image service configurations",
    description=(
            "Return all configured image services with their activation state, config "
            "path, generation settings, traffic limits, and price table. "
            "The per-provider 'workflow' block is omitted to keep the payload compact."
    ),
)
async def list_image_generators(
        _user: dict = Depends(require_internal),
        registry: ServiceRegistry = Depends(get_registry),
) -> list[dict[str, Any]]:
    """Return configured image generators with activation states."""
    service = get_typed_service(registry, text_to_image.SERVICE_NAME, TextToImageService)
    configs = service.providers_snapshot()
    await emit_event(
        LOG_SOURCE,
        "image_generators_listed",
        LogLevel.DEBUG,
        total=len(configs),
        active=sum(1 for c in configs if c.is_active()),
    )
    return [
        {
            "name": c.name,
            "provider": c.provider,
            "enabled": c.is_active(),
            "priority": c.priority,
            "config_path": str(c.config_path),
            "generate": {k: v for k, v in c.generate_config.items() if k != "workflow"},
            "traffic": c.traffic_policy.as_dict(),
            "pricing": c.price_table.as_dict(),
        }
        for c in configs
    ]


@router.post(
    IMAGE_GENERATOR_SINGLE_GENERATE,
    response_model=ImageGenerateResult,
    status_code=status.HTTP_200_OK,
    summary="Generate an image for one request",
    description=(
            "Generate an image using the image service named by the 'provider' path "
            "parameter and return the result as JSON, including the output path, "
            "estimated cost, and attempt count. Provider failures are reported "
            "in-band with 'success: false' and an 'error' message. "
            "Responds with HTTP 404 when the provider is unknown and HTTP 503 when no "
            "active image providers are configured."
    ),
)
async def generate_single(
        provider: str,
        body: GenerateImageRequest,
        _user: dict = Depends(require_internal),
        registry: ServiceRegistry = Depends(get_registry),
) -> ImageGenerateResult:
    """Generate a single image via the named image service."""
    service = get_typed_service(registry, text_to_image.SERVICE_NAME, TextToImageService)

    if not body.prompt or not body.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt must be non-empty.")

    request_id = body.request_id or f"img-{uuid4().hex[:12]}"
    await emit_event(
        LOG_SOURCE,
        "image_generation_started",
        LogLevel.INFO,
        endpoint="generate_single",
        provider=provider,
        request_id=request_id,
        size=body.size,
        quality=body.quality,
        output_format=body.output_format,
        language_code=body.language_code,
        prompt=_prompt_preview(body.prompt),
    )
    image_request = ImageRequest(
        request_id=request_id,
        prompt=body.prompt,
        filename_text=body.filename_text or body.prompt,
        negative_prompt=body.negative_prompt,
        language_code=body.language_code,
        size=body.size,
        quality=body.quality,
        output_format=body.output_format,
        seed=body.seed,
        metadata=body.metadata,
    )

    async with httpx.AsyncClient(timeout=_CLIENT_TIMEOUT) as client:
        try:
            generated = await service.generate(
                image_request,
                client,
                provider_name=provider,
            )
        except NoActiveImageProvidersError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except ImageProviderNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    await _log_generation_result(endpoint="generate_single", generated=generated)
    return _to_result(generated)


@router.post(
    GENERATE_IMAGE,
    status_code=status.HTTP_200_OK,
    summary="Generate an image and return the raw bytes",
    description=(
            "Generate an image for a prompt using the highest-priority active image "
            "service and return the raw image bytes. "
            "Responds with HTTP 400 when the prompt is empty, HTTP 500 when generation "
            "fails, and HTTP 503 when no active image providers are configured."
    ),
    responses={
        200: {
            "content": {"image/png": {}},
            "description": "Raw image bytes",
        },
        400: {"description": "Prompt is empty"},
        500: {"description": "Image generation failed"},
        503: {"description": "No active image providers configured"},
    },
)
async def generate_image(
        body: GenerateImageBytesRequest,
        _user: dict = Depends(require_internal),
        registry: ServiceRegistry = Depends(get_registry),
):
    """Generate an image for a prompt and return the raw image bytes."""
    if not body.prompt or not body.prompt.strip():
        raise HTTPException(status_code=400, detail="Prompt must be non-empty.")

    service = get_typed_service(registry, text_to_image.SERVICE_NAME, TextToImageService)
    require_active_providers(
        service,
        service_name=text_to_image.SERVICE_NAME,
        detail="No active text to image services configured.",
    )

    prompt = body.prompt.strip()
    request_id = f"image-{uuid4().hex[:12]}"
    await emit_event(
        LOG_SOURCE,
        "image_generation_started",
        LogLevel.INFO,
        endpoint="generate_image",
        provider=None,
        request_id=request_id,
        prompt=_prompt_preview(prompt),
    )
    image_request = ImageRequest(
        request_id=request_id,
        prompt=prompt,
        filename_text=(body.filename_text or prompt).strip(),
    )

    async with httpx.AsyncClient(timeout=_CLIENT_TIMEOUT) as client:
        try:
            result = await service.generate(image_request, client)
        except NoActiveImageProvidersError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except AllProvidersFailedError as exc:
            # Every provider was tried (and logged its own call); keep the per-request summary record.
            await emit_event(
                LOG_SOURCE,
                "image_generation_failed",
                LogLevel.ERROR,
                endpoint="generate_image",
                request_id=request_id,
                errors=exc.errors,
            )
            raise HTTPException(status_code=500, detail=str(exc)) from exc
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    await _log_generation_result(endpoint="generate_image", generated=result)
    if not result.success:
        raise HTTPException(status_code=500, detail=result.error or "Image generation failed.")

    image_bytes = Path(result.raw_path).read_bytes()
    return Response(content=image_bytes, media_type=media_type_for_extension(result.raw_extension))
