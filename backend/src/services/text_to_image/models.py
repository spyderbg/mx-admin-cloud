"""Base image-provider abstraction and image request/response value types.

Every provider implements a single-attempt :meth:`TextToImageProvider.fetch_image`
and inherits :meth:`TextToImageProvider.generate`, which applies the traffic
policy, writes the file, estimates cost, and logs the call. Centralising that
here is deliberate: a provider cannot forget to log, and failures are logged
exactly like successes.
"""

from __future__ import annotations

import logging
import math
import struct
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.log_client import log_provider_request
from src.services.logging import LogProviderRequest
from src.services.models import ServiceProvider
from src.services.text_to_image.governor import RequestGovernor, TrafficPolicy
from src.services.text_to_image.pricing import PriceTable

logger = logging.getLogger(__name__)

_MIME_TO_EXTENSION = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/webp": "webp",
}

_EXTENSION_TO_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
}

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# ``generate`` keys left out of the parameters recorded for a generated image.
_UNRECORDED_GENERATE_KEYS = frozenset({"workflow", "headers"})


@dataclass(slots=True)
class ImageRequest:  # pylint: disable=too-many-instance-attributes
    """One image to generate."""

    request_id: str  # unique id for this request
    prompt: str  # text prompt describing the image
    filename_text: str  # text used in the output file name
    negative_prompt: str | None = None  # what to avoid (providers that support it)
    language_code: str | None = None  # optional language context, logged only
    size: str | None = None  # e.g. "1024x1024"; falls back to provider config
    quality: str | None = None  # provider-specific quality tier
    output_format: str = "png"  # requested image format
    seed: int | None = None  # deterministic seed where supported
    metadata: dict[str, Any] = field(default_factory=dict)  # extra pass-through data


@dataclass(slots=True)
class GeneratedImage:  # pylint: disable=too-many-instance-attributes
    """Result of a single image generation attempt by one provider."""

    provider: str  # provider that produced this image
    request_id: str  # matching ImageRequest.request_id
    prompt: str  # prompt sent to the provider
    filename_text: str  # sanitized text for the output filename
    output_format: str  # requested image format, e.g. "png"
    raw_path: Path  # path to the generated image file
    raw_extension: str  # actual file extension written (png/jpg/webp)
    latency_seconds: float  # wall-clock generation time
    success: bool  # whether generation succeeded
    model: str | None = None  # provider model identifier
    width: int | None = None  # image width in pixels (when known)
    height: int | None = None  # image height in pixels (when known)
    cost_estimate: float | None = None  # estimated USD cost, None when unpriced
    attempts: int = 1  # number of provider attempts made (retries included)
    error: str | None = None  # error message on failure
    language_code: str | None = None  # optional language context for logging


@dataclass(slots=True)
class ImagePayload:
    """Raw image bytes returned by one provider attempt."""

    data: bytes
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None


def parse_size(size: str | None) -> tuple[int, int] | None:
    """Parse ``"1024x768"`` into ``(1024, 768)``; return ``None`` when unparsable."""
    if not size:
        return None
    parts = str(size).lower().split("x")
    if len(parts) != 2:
        return None
    try:
        width, height = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if width <= 0 or height <= 0:
        return None
    return width, height


def size_to_aspect_ratio(size: str | None, default: str = "1:1") -> str:
    """Convert a pixel size into a reduced ``"W:H"`` aspect ratio."""
    parsed = parse_size(size)
    if parsed is None:
        return default
    width, height = parsed
    divisor = math.gcd(width, height) or 1
    return f"{width // divisor}:{height // divisor}"


def png_dimensions(data: bytes) -> tuple[int, int] | None:
    """Read width/height from a PNG IHDR chunk without an imaging library."""
    if len(data) < 24 or not data.startswith(_PNG_SIGNATURE):
        return None
    try:
        width, height = struct.unpack(">II", data[16:24])
    except struct.error:  # pragma: no cover - guarded by the length check
        return None
    if width <= 0 or height <= 0:
        return None
    return int(width), int(height)


def extension_for_mime(mime: str | None, fallback: str) -> str:
    """Map a response content type to a file extension."""
    if mime:
        key = str(mime).split(";", 1)[0].strip().lower()
        extension = _MIME_TO_EXTENSION.get(key)
        if extension:
            return extension
    cleaned = (fallback or "png").lstrip(".").lower()
    return cleaned or "png"


def media_type_for_extension(extension: str) -> str:
    """Map a file extension back to a response media type."""
    return _EXTENSION_TO_MIME.get((extension or "").lstrip(".").lower(), "application/octet-stream")


@dataclass(slots=True)
class _GenerationOutcome:
    """Internal result of one governed generation attempt."""

    output_path: Path
    extension: str
    payload: ImagePayload | None = None
    attempts: int = 1
    error: str | None = None


class TextToImageProvider(ServiceProvider, ABC):
    """Common interface and shared machinery for image providers.

    Provider ``config`` uses three sub-dicts: ``generate`` (model and prompt
    settings), ``traffic`` (concurrency/retry limits) and ``pricing`` (cost
    table). Subclasses only implement :meth:`fetch_image`.
    """

    #: Provider-SDK exception types that should be treated as transient.
    retryable_exception_types: tuple[type[BaseException], ...] = ()

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._policy = TrafficPolicy.from_config(self.config.get("traffic"))
        self._governor = RequestGovernor(
            self._policy,
            extra_retryable=self.retryable_exception_types,
        )
        self._pricing = PriceTable.from_config(
            self.config.get("pricing"),
            self.config.get("cost_per_image"),
        )

    # ------------------------------------------------------------------
    # Config accessors
    # ------------------------------------------------------------------

    @property
    def generate_config(self) -> dict[str, Any]:
        """Return a copy of the ``generate`` sub-dict from ``self.config``."""
        generate = self.config.get("generate", {})
        return dict(generate) if isinstance(generate, dict) else {}

    @property
    def traffic_policy(self) -> TrafficPolicy:
        """Return the traffic policy applied to this provider's calls."""
        return self._policy

    @property
    def price_table(self) -> PriceTable:
        """Return the configured price table."""
        return self._pricing

    def resolve_model(self, request: ImageRequest) -> str:
        """Return the model id for *request* (request metadata overrides config)."""
        override = str(request.metadata.get("model", "") or "").strip()
        if override:
            return override
        return str(self.generate_config.get("model", "") or "").strip()

    def resolve_size(self, request: ImageRequest) -> str | None:
        """Return the requested size, falling back to the configured default."""
        size = request.size or self.generate_config.get("size")
        return str(size).strip() if size else None

    def resolve_quality(self, request: ImageRequest) -> str | None:
        """Return the requested quality tier, falling back to config."""
        quality = request.quality or self.generate_config.get("quality")
        return str(quality).strip() if quality else None

    def resolve_negative_prompt(self, request: ImageRequest) -> str | None:
        """Return the negative prompt from the request or config."""
        negative = request.negative_prompt or self.generate_config.get("negative_prompt")
        text = str(negative).strip() if negative else ""
        return text or None

    def build_prompt(self, request: ImageRequest) -> str:
        """Wrap the request prompt in the configured prefix/suffix."""
        gen = self.generate_config
        prefix = str(gen.get("prompt_prefix", "") or "").strip()
        suffix = str(gen.get("prompt_suffix", "") or "").strip()
        parts = [part for part in (prefix, request.prompt.strip(), suffix) if part]
        return " ".join(parts)

    def build_output_path(self, request: ImageRequest, extension: str) -> Path:
        """Resolve the full output path for the generated image."""
        from src.core.config import settings  # pylint: disable=import-outside-toplevel

        # The provider name reaches the filesystem here, and runtime-applied
        # configs carry synthetic names like "<runtime-upload>".
        output_dir = settings.images_output_dir(settings.slugify_filename(self.name, "provider"))
        output_dir.mkdir(parents=True, exist_ok=True)
        stem_source = request.filename_text.strip() or request.prompt.strip()[:60]
        stem = settings.slugify_filename(stem_source, fallback=request.request_id)
        return output_dir / f"{stem}.{extension.lstrip('.')}"

    def generation_parameters(self, request: ImageRequest) -> dict[str, Any]:
        """Return the settings this provider generates *request* with, for recording next to the image.

        That is its ``generate`` config with the request's own values resolved over it, minus
        the inline ComfyUI ``workflow`` (too large to record; the list endpoint omits it too)
        and ``headers`` (they can carry credentials).
        """
        parameters = {k: v for k, v in self.generate_config.items() if k not in _UNRECORDED_GENERATE_KEYS}
        resolved = {
            "model": self.resolve_model(request),
            "size": self.resolve_size(request),
            "quality": self.resolve_quality(request),
            "negative_prompt": self.resolve_negative_prompt(request),
            "seed": request.seed,
        }
        parameters.update({key: value for key, value in resolved.items() if value not in (None, "")})
        return parameters

    def estimate_cost(self, request: ImageRequest) -> float | None:
        """Estimate the USD cost of one image for *request*."""
        return self._pricing.estimate(
            self.resolve_model(request),
            self.resolve_size(request),
            self.resolve_quality(request),
        )

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    @abstractmethod
    async def fetch_image(self, request: ImageRequest, client: Any) -> ImagePayload:
        """Perform exactly one attempt against the external service.

        Raise on any failure; the governor classifies the exception and decides
        whether to retry.
        """

    async def generate(self, request: ImageRequest, client: Any) -> GeneratedImage:
        """Generate one image under the traffic policy and log the attempt.

        Provider failures are returned as ``success=False`` results rather than
        raised, so a single bad provider never becomes an HTTP 500.
        """
        started_at = time.perf_counter()
        size = self.resolve_size(request)
        outcome = await self._attempt_generation(request, client)
        latency = time.perf_counter() - started_at

        width, height = self._resolve_dimensions(outcome.payload, size)
        result = GeneratedImage(
            provider=self.name,
            request_id=request.request_id,
            prompt=request.prompt,
            filename_text=request.filename_text,
            output_format=request.output_format,
            raw_path=outcome.output_path,
            raw_extension=outcome.extension,
            latency_seconds=latency,
            success=outcome.error is None,
            model=self.resolve_model(request) or None,
            width=width,
            height=height,
            cost_estimate=self.estimate_cost(request) if outcome.error is None else 0.0,
            attempts=outcome.attempts,
            error=outcome.error,
            language_code=request.language_code,
        )
        await self._log_request(request, result, size=size, quality=self.resolve_quality(request))
        return result

    async def _attempt_generation(self, request: ImageRequest, client: Any) -> "_GenerationOutcome":
        """Run the governed provider call and write the bytes, capturing failures."""
        outcome = _GenerationOutcome(
            output_path=self.build_output_path(request, request.output_format),
            extension=request.output_format.lstrip(".").lower() or "png",
        )
        try:
            governed = await self._governor.run(lambda: self.fetch_image(request, client))
            payload = governed.value
            outcome.attempts = governed.attempts
            if not payload.data:
                raise ValueError("Provider returned an empty image.")
            outcome.extension = extension_for_mime(payload.mime_type, request.output_format)
            outcome.output_path = self.build_output_path(request, outcome.extension)
            outcome.output_path.write_bytes(payload.data)
            outcome.payload = payload
        except Exception as exc:  # pylint: disable=broad-exception-caught
            outcome.attempts = getattr(exc, "attempts", 0) or outcome.attempts
            outcome.error = str(exc)
            outcome.payload = None
            logger.warning(
                "Image generation failed provider=%s request_id=%s error=%s",
                self.name, request.request_id, outcome.error,
            )
        return outcome

    @staticmethod
    def _resolve_dimensions(
            payload: ImagePayload | None,
            size: str | None,
    ) -> tuple[int | None, int | None]:
        if payload is not None:
            if payload.width and payload.height:
                return payload.width, payload.height
            measured = png_dimensions(payload.data)
            if measured is not None:
                return measured
        parsed = parse_size(size)
        if parsed is not None:
            return parsed
        return None, None

    async def _log_request(
            self,
            request: ImageRequest,
            result: GeneratedImage,
            *,
            size: str | None,
            quality: str | None,
    ) -> None:
        """Record the call in the request log; never let logging fail a request."""
        try:
            await log_provider_request(
                LogProviderRequest(
                    provider=self.name,
                    tts_model=result.model or "",
                    language=request.language_code,
                    input_data=self.build_prompt(request),
                    response_duration=result.latency_seconds,
                    request_kind="image",
                    size=size,
                    quality=quality,
                    cost_estimate=result.cost_estimate,
                    status="success" if result.success else "error",
                    error=result.error,
                    attempts=result.attempts,
                )
            )
        except Exception:  # pylint: disable=broad-exception-caught
            logger.debug("Provider request logging failed", exc_info=True)
