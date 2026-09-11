"""OpenAI Images provider backed by the official SDK."""

from __future__ import annotations

import base64
import logging
from typing import Any

import openai
from openai import AsyncOpenAI

from src.services.text_to_image.governor import NonRetryableError
from src.services.text_to_image.models import ImagePayload, ImageRequest, TextToImageProvider
from src.services.text_to_image.service import TextToImageProviderFactory
from ._openai_payload import build_openai_kwargs, mime_for_kwargs

logger = logging.getLogger(__name__)


class OpenAIImageProvider(TextToImageProvider):
    """Image provider backed by the OpenAI Images API (SDK transport)."""

    # Connection/timeout failures are transient; APITimeoutError subclasses this.
    retryable_exception_types = (openai.APIConnectionError,)

    @classmethod
    def register(cls) -> None:
        """Register this provider under the key ``"openai"`` in the ProviderFactory."""
        TextToImageProviderFactory.register("openai", cls)

    def api_key_env_name(self) -> str:
        """Use the shared OpenAI credential regardless of the config file name."""
        return "OPENAI_API_KEY"

    async def fetch_image(self, request: ImageRequest, client: Any) -> ImagePayload:
        """Request one image from the OpenAI Images API."""
        del client  # the SDK manages its own transport

        api_key = self.resolve_api_key()
        if not api_key:
            raise NonRetryableError("OPENAI_API_KEY is not set.")

        kwargs = build_openai_kwargs(self, request)
        # max_retries=0: the shared governor is the only retry layer.
        async with AsyncOpenAI(
                api_key=api_key,
                max_retries=0,
                timeout=self.traffic_policy.timeout_seconds,
        ) as sdk:
            response = await sdk.images.generate(**kwargs)

        data = getattr(response, "data", None) or []
        if not data:
            raise NonRetryableError("OpenAI Images API returned no image data.")

        encoded = getattr(data[0], "b64_json", None)
        if not encoded:
            raise NonRetryableError("OpenAI Images API returned no base64 image payload.")

        return ImagePayload(data=base64.b64decode(encoded), mime_type=mime_for_kwargs(kwargs))


OpenAIImageProvider.register()
