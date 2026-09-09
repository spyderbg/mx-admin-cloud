"""Speaktor (Transkriptor) TTS provider.

Uses the ``https://api.tor.app/developer/text_to_speech`` endpoint.
The API returns a JSON body with an ``audioUrl`` pre-signed URL that
must be downloaded in a second GET request.

See: https://developer.transkriptor.com/docs/text-to-speech-1
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

from src.core.languages import to_bcp47
from src.core.log_client import log_provider_request
from src.core.models import GeneratedAudio
from src.services.logging.schemas import LogProviderRequest
from src.services.text_to_speech.models import TTSProvider, TTSRequest
from src.services.text_to_speech.service import TextToSpeechProviderFactory

logger = logging.getLogger(__name__)


def _requires_api_key(*templates: Any) -> bool:
    """Return whether any template substitutes the ``{api_key}`` placeholder.

    Endpoint, headers, and body are all config-driven, so a Speaktor-compatible
    endpoint may need no credentials at all. Only demand a key when the config
    actually asks for one.
    """
    return any("{api_key}" in json.dumps(template, default=str) for template in templates)


def _render_template(value: Any, context: dict[str, str]) -> Any:
    """Recursively apply str.format(**context) to strings, lists, and dicts."""
    if isinstance(value, str):
        return value.format(**context)
    if isinstance(value, list):
        return [_render_template(item, context) for item in value]
    if isinstance(value, dict):
        return {key: _render_template(item, context) for key, item in value.items()}
    return value


class SpeaktorTTSProvider(TTSProvider):
    """TTS provider backed by the Speaktor (Transkriptor) developer API."""

    @classmethod
    def register(cls) -> None:
        """Register this provider under the key ``"speaktor"`` in the ProviderFactory."""
        TextToSpeechProviderFactory.register("speaktor", cls)

    def _resolve_credentials(self, *templates: Any) -> str:
        """Return the API key, failing with the configuration cause when it is required but unset.

        An empty key would otherwise render as a header like ``"Bearer "`` whose
        trailing space the HTTP layer rejects as an illegal header value, hiding
        the real cause behind a protocol error.
        """
        api_key = self.resolve_api_key()
        if not api_key and _requires_api_key(*templates):
            raise RuntimeError(f"{self.api_key_env_name()} is not set.")
        return api_key

    async def generate(self, request: TTSRequest, client: Any) -> GeneratedAudio:
        """Call the Speaktor TTS endpoint, download the audio URL, and write it to disk."""
        output_format = request.output_format
        gen = self.resolve_language_generate(request.language_code)

        voice_name = str(gen.get("voice_name") or gen.get("actor") or "").strip()
        if not voice_name:
            logger.warning(
                "No voice_name configured for provider=%s language=%s -- skipping API request.",
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
                error=f"No voice_name configured for language '{request.language_code}'",
            )

        endpoint = str(gen.get("endpoint", "")).strip()
        if not endpoint:
            raise RuntimeError("Speaktor config requires `endpoint` in the generate section.")

        method = str(gen.get("method", "POST")).upper()
        headers_template = gen.get("headers", {})
        payload_template = gen.get("payload_template", {})

        api_key = self._resolve_credentials(endpoint, headers_template, payload_template)
        response_mode = str(gen.get("response_mode", "json_url")).lower()
        audio_field = str(gen.get("audio_field", "audioUrl"))

        lang_override = gen.get("language")
        resolved_language = lang_override if lang_override else to_bcp47(request.language_code)

        prompt_text = self.build_prompt_text(request)
        context = {
            "text": prompt_text,
            "raw_text": request.text,
            "language": resolved_language,
            "language_name": request.language_name,
            "voice_name": voice_name,
            "actor": voice_name,
            "format": output_format,
            "api_key": api_key,
        }
        headers = _render_template(headers_template, context)
        payload = _render_template(payload_template, context)
        output_path = self.build_output_path(request, output_format)
        model_name = str(gen.get("model") or voice_name or "default")

        payload["voice_name"] = voice_name

        speed_rate = gen.get("speed_rate")
        if speed_rate is not None and str(speed_rate).strip() != "":
            payload["speed_rate"] = float(speed_rate)
        emotion = gen.get("emotion")
        if emotion is not None and str(emotion).strip() != "":
            payload["emotion"] = str(emotion).strip()

        started_at = time.perf_counter()
        response = await client.request(method, endpoint, headers=headers, json=payload)
        latency = time.perf_counter() - started_at
        if response.is_error:
            body = response.text
            raise RuntimeError(
                f"Speaktor API {response.status_code}: {body}"
            )
        await log_provider_request(
            LogProviderRequest(
                provider=self.name,
                tts_model=model_name,
                actor=voice_name,
                voice_id=str(gen.get("voice_id", "")),
                language=str(request.language_code),
                input_data=prompt_text,
                response_duration=latency,
            )
        )

        if response_mode == "json_url":
            data = response.json()
            audio_url = data[audio_field]
            audio_response = await client.get(audio_url)
            audio_response.raise_for_status()
            output_path.write_bytes(audio_response.content)
        elif response_mode == "json_base64":
            data = response.json()
            encoded_audio = str(data[audio_field])
            output_path.write_bytes(base64.b64decode(encoded_audio))
        else:
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
            raw_extension=output_format,
            latency_seconds=latency,
            success=True,
        )


SpeaktorTTSProvider.register()
