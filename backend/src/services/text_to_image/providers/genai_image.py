"""Google GenAI image provider backed by the ``google-genai`` SDK."""

from __future__ import annotations

import logging
from typing import Any

from src.services.text_to_image.governor import NonRetryableError
from src.services.text_to_image.models import ImagePayload, ImageRequest, TextToImageProvider
from src.services.text_to_image.service import TextToImageProviderFactory
from ._genai_common import (
    API_IMAGEN,
    DEFAULT_MIME_TYPE,
    optional_str,
    resolve_api,
    resolve_aspect_ratio,
    resolve_output_mime,
)

logger = logging.getLogger(__name__)


class GenAIImageProvider(TextToImageProvider):
    """Image provider backed by Google Imagen or Gemini image models."""

    @classmethod
    def register(cls) -> None:
        """Register this provider under the key ``"genai"``."""
        TextToImageProviderFactory.register("genai", cls)

    def api_key_env_name(self) -> str:
        """Use the shared Gemini credential regardless of the config file name."""
        return "GEMINI_API_KEY"

    async def fetch_image(self, request: ImageRequest, client: Any) -> ImagePayload:
        """Request one image from the configured Google image API."""
        del client  # the SDK manages its own transport

        # Imported lazily so a missing optional dependency degrades to a provider
        # error instead of breaking the whole service package at import time.
        try:
            from google import genai  # pylint: disable=import-outside-toplevel
            from google.genai import types  # pylint: disable=import-outside-toplevel
        except ImportError as exc:
            raise NonRetryableError(
                "google-genai is not installed; run 'pip install -r requirements.txt'.",
            ) from exc

        api_key = self.resolve_api_key()
        if not api_key:
            raise NonRetryableError("GEMINI_API_KEY is not set.")

        api = resolve_api(self)
        sdk = genai.Client(
            api_key=api_key,
            # HttpOptions.timeout is milliseconds.
            http_options=types.HttpOptions(timeout=int(self.traffic_policy.timeout_seconds * 1000)),
        )

        if api == API_IMAGEN:
            return await self._generate_imagen(sdk, types, request)
        return await self._generate_gemini(sdk, types, request)

    async def _generate_imagen(self, sdk: Any, types: Any, request: ImageRequest) -> ImagePayload:
        gen = self.generate_config
        config_kwargs: dict[str, Any] = {
            "number_of_images": 1,
            "aspect_ratio": resolve_aspect_ratio(self, request),
            "output_mime_type": resolve_output_mime(self, request),
        }
        image_size = optional_str(gen.get("image_size"))
        if image_size:
            config_kwargs["image_size"] = image_size
        negative_prompt = self.resolve_negative_prompt(request)
        if negative_prompt:
            config_kwargs["negative_prompt"] = negative_prompt
        if request.seed is not None:
            # Imagen rejects a seed while watermarking is enabled.
            config_kwargs["seed"] = int(request.seed)
            config_kwargs["add_watermark"] = False

        response = await sdk.aio.models.generate_images(
            model=self.resolve_model(request) or "imagen-4.0-generate-001",
            prompt=self.build_prompt(request),
            config=types.GenerateImagesConfig(**config_kwargs),
        )

        generated = getattr(response, "generated_images", None) or []
        if not generated:
            raise NonRetryableError("Imagen returned no images (prompt may have been filtered).")

        image = getattr(generated[0], "image", None)
        data = getattr(image, "image_bytes", None) if image is not None else None
        if not data:
            raise NonRetryableError("Imagen returned an image without bytes.")

        return ImagePayload(
            data=data,
            mime_type=getattr(image, "mime_type", None) or resolve_output_mime(self, request),
        )

    async def _generate_gemini(self, sdk: Any, types: Any, request: ImageRequest) -> ImagePayload:
        gen = self.generate_config
        image_kwargs: dict[str, Any] = {"aspect_ratio": resolve_aspect_ratio(self, request)}
        image_size = optional_str(gen.get("image_size"))
        if image_size:
            image_kwargs["image_size"] = image_size

        response = await sdk.aio.models.generate_content(
            model=self.resolve_model(request) or "gemini-2.5-flash-image",
            contents=self.build_prompt(request),
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(**image_kwargs),
            ),
        )

        for part in self._iter_parts(response):
            blob = getattr(part, "inline_data", None)
            data = getattr(blob, "data", None) if blob is not None else None
            if data:
                return ImagePayload(
                    data=data,
                    mime_type=getattr(blob, "mime_type", None) or DEFAULT_MIME_TYPE,
                )

        raise NonRetryableError("Gemini returned no inline image data.")

    @staticmethod
    def _iter_parts(response: Any) -> list[Any]:
        for candidate in getattr(response, "candidates", None) or []:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None) if content is not None else None
            if parts:
                return list(parts)
        return []


GenAIImageProvider.register()
