"""OpenAI text-to-speech provider."""

from __future__ import annotations

import logging
import time
from typing import Any

from src.api.internal.schemas.logging import LogProviderRequest
from src.core.log_client import log_provider_request
from src.core.models import GeneratedAudio
from ..models import TTSProvider, TTSRequest
from ..service import TextToSpeechProviderFactory

logger = logging.getLogger(__name__)


class OpenAITTSProvider(TTSProvider):
    """TTS provider backed by the OpenAI audio/speech API."""

    ENDPOINT = "https://api.openai.com/v1/audio/speech"

    @classmethod
    def register(cls) -> None:
        """Register this provider under the key ``"openai"`` in the ProviderFactory."""
        TextToSpeechProviderFactory.register("openai", cls)

    async def generate(self, request: TTSRequest, client: Any) -> GeneratedAudio:
        """Call the OpenAI TTS endpoint and write the audio to disk."""
        output_format = request.output_format
        gen = self.resolve_language_generate(request.language_code)

        voice = gen.get("actor") or gen.get("voice") or ""
        if not voice:
            logger.warning(
                "No actor configured for provider=%s language=%s -- skipping API request.",
                self.name, request.language_code,
            )
            return GeneratedAudio(
                provider=self.name,
                request_id=request.request_id,
                language_code=request.language_code,
                language_name=request.language_name,
                expected_text=request.expected_text,
                filename_text=request.filename_text,
                output_format=output_format,
                raw_path=self.build_output_path(request, output_format),
                raw_extension=output_format,
                latency_seconds=0.0,
                success=False,
                error=f"No actor configured for language '{request.language_code}'",
            )

        api_key = self.resolve_api_key()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set.")

        model = gen.get("model", "tts-1-hd")
        prompt = str(gen.get("prompt", "")).strip()

        payload = {
            "model": model,
            "voice": voice,
            "input": self.build_prompt_text(request),
            "response_format": output_format,
        }
        if prompt:
            payload["instructions"] = prompt

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        extension = output_format
        output_path = self.build_output_path(request, extension)

        started_at = time.perf_counter()
        response = await client.post(self.ENDPOINT, headers=headers, json=payload)
        latency = time.perf_counter() - started_at
        response.raise_for_status()
        await log_provider_request(
            LogProviderRequest(
                provider=self.name,
                tts_model=str(model),
                actor=str(voice),
                voice_id="",
                language=str(request.language_code),
                input_data=payload.get("input", ""),
                response_duration=latency,
            )
        )
        output_path.write_bytes(response.content)

        return GeneratedAudio(
            provider=self.name,
            request_id=request.request_id,
            language_code=request.language_code,
            language_name=request.language_name,
            expected_text=request.expected_text,
            filename_text=request.filename_text,
            output_format=output_format,
            raw_path=output_path,
            raw_extension=extension,
            latency_seconds=latency,
            success=True,
        )

OpenAITTSProvider.register()
