"""Google GenAI image provider using raw HTTP instead of the SDK.

Calls the Generative Language REST API directly with the caller's shared
``httpx`` client, so no Google SDK (and no extra transport stack) is involved.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from src.services.text_to_image.governor import NonRetryableError
from src.services.text_to_image.models import ImagePayload, ImageRequest, TextToImageProvider
from src.services.text_to_image.service import TextToImageProviderFactory
from src.services.text_to_image.providers._genai_common import (
    API_IMAGEN,
    DEFAULT_MIME_TYPE,
    optional_str,
    resolve_api,
    resolve_aspect_ratio,
    resolve_output_mime,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"


class GenAIHttpImageProvider(TextToImageProvider):
    """Image provider calling the Google Generative Language API over plain HTTP."""

    @classmethod
    def register(cls) -> None:
        """Register this provider under the key ``"genai_http"``."""
        TextToImageProviderFactory.register("genai_http", cls)

    def api_key_env_name(self) -> str:
        """Use the shared Gemini credential regardless of the config file name."""
        return "GEMINI_API_KEY"

    def _base_url(self) -> str:
        return str(self.generate_config.get("base_url") or BASE_URL).rstrip("/")

    async def fetch_image(self, request: ImageRequest, client: Any) -> ImagePayload:
        """Post one image request to the Generative Language REST API."""
        api_key = self.resolve_api_key()
        if not api_key:
            raise NonRetryableError("GEMINI_API_KEY is not set.")

        api = resolve_api(self)
        headers = {"x-goog-api-key": api_key, "Content-Type": "application/json"}

        if api == API_IMAGEN:
            model = self.resolve_model(request) or "imagen-4.0-generate-001"
            url = f"{self._base_url()}/{model}:predict"
            payload = self._imagen_payload(request)
        else:
            model = self.resolve_model(request) or "gemini-2.5-flash-image"
            url = f"{self._base_url()}/{model}:generateContent"
            payload = self._gemini_payload(request)

        response = await client.post(
            url,
            headers=headers,
            json=payload,
            timeout=self.traffic_policy.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()

        if api == API_IMAGEN:
            return self._payload_from_predict(body, request)
        return self._payload_from_generate_content(body)

    def _imagen_payload(self, request: ImageRequest) -> dict[str, Any]:
        parameters: dict[str, Any] = {
            "sampleCount": 1,
            "aspectRatio": resolve_aspect_ratio(self, request),
            "outputOptions": {"mimeType": resolve_output_mime(self, request)},
        }
        image_size = optional_str(self.generate_config.get("image_size"))
        if image_size:
            parameters["imageSize"] = image_size
        negative_prompt = self.resolve_negative_prompt(request)
        if negative_prompt:
            parameters["negativePrompt"] = negative_prompt
        if request.seed is not None:
            parameters["seed"] = int(request.seed)
            parameters["addWatermark"] = False
        return {
            "instances": [{"prompt": self.build_prompt(request)}],
            "parameters": parameters,
        }

    def _gemini_payload(self, request: ImageRequest) -> dict[str, Any]:
        image_config: dict[str, Any] = {"aspectRatio": resolve_aspect_ratio(self, request)}
        image_size = optional_str(self.generate_config.get("image_size"))
        if image_size:
            image_config["imageSize"] = image_size
        return {
            "contents": [{"parts": [{"text": self.build_prompt(request)}]}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                "imageConfig": image_config,
            },
        }

    def _payload_from_predict(self, body: dict[str, Any], request: ImageRequest) -> ImagePayload:
        predictions = body.get("predictions") or []
        if not predictions:
            raise NonRetryableError("Imagen returned no images (prompt may have been filtered).")
        encoded = predictions[0].get("bytesBase64Encoded")
        if not encoded:
            raise NonRetryableError("Imagen returned a prediction without image bytes.")
        return ImagePayload(
            data=base64.b64decode(encoded),
            mime_type=predictions[0].get("mimeType") or resolve_output_mime(self, request),
        )

    @staticmethod
    def _payload_from_generate_content(body: dict[str, Any]) -> ImagePayload:
        for candidate in body.get("candidates") or []:
            parts = (candidate.get("content") or {}).get("parts") or []
            for part in parts:
                blob = part.get("inlineData") or part.get("inline_data") or {}
                encoded = blob.get("data")
                if encoded:
                    return ImagePayload(
                        data=base64.b64decode(encoded),
                        mime_type=blob.get("mimeType") or blob.get("mime_type") or DEFAULT_MIME_TYPE,
                    )
        raise NonRetryableError("Gemini returned no inline image data.")


GenAIHttpImageProvider.register()
