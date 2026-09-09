"""OpenAI Images provider using raw HTTP instead of the SDK.

Same API and payload as the SDK provider, but it reuses the caller's shared
``httpx`` client. That keeps connection handling, timeouts, and traffic
accounting in one place, which matters when image spend is being controlled.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from src.services.text_to_image.governor import NonRetryableError
from src.services.text_to_image.models import ImagePayload, ImageRequest, TextToImageProvider
from src.services.text_to_image.service import TextToImageProviderFactory
from ._openai_payload import ENDPOINT, build_openai_kwargs, mime_for_kwargs

logger = logging.getLogger(__name__)


class OpenAIHttpImageProvider(TextToImageProvider):
    """Image provider calling the OpenAI Images endpoint over plain HTTP."""

    @classmethod
    def register(cls) -> None:
        """Register this provider under the key ``"openai_http"``."""
        TextToImageProviderFactory.register("openai_http", cls)

    def api_key_env_name(self) -> str:
        """Use the shared OpenAI credential regardless of the config file name."""
        return "OPENAI_API_KEY"

    async def fetch_image(self, request: ImageRequest, client: Any) -> ImagePayload:
        """Post one image request to the OpenAI Images endpoint."""
        api_key = self.resolve_api_key()
        if not api_key:
            raise NonRetryableError("OPENAI_API_KEY is not set.")

        payload = build_openai_kwargs(self, request)
        endpoint = str(self.generate_config.get("endpoint") or ENDPOINT)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        response = await client.post(
            endpoint,
            headers=headers,
            json=payload,
            timeout=self.traffic_policy.timeout_seconds,
        )
        response.raise_for_status()

        body = response.json()
        entries = body.get("data") or []
        if not entries:
            raise NonRetryableError("OpenAI Images API returned no image data.")

        encoded = entries[0].get("b64_json")
        if not encoded:
            raise NonRetryableError("OpenAI Images API returned no base64 image payload.")

        return ImagePayload(data=base64.b64decode(encoded), mime_type=mime_for_kwargs(payload))


OpenAIHttpImageProvider.register()
