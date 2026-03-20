"""ElevenLabs text-to-speech provider."""

from __future__ import annotations

import logging
import time
from typing import Any

from ..models import GeneratedAudio, TTSRequest
from ..logging import log_provider_request
from .base import TTSProvider

logger = logging.getLogger(__name__)

ELEVENLABS_OUTPUT_MAP = {
    "mp3": "mp3_44100_128",
    "wav": "wav_22050",
    "pcm": "pcm_22050",
    "opus": "opus_48000_128",
}


def _to_float(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Invalid `{field_name}` value in ElevenLabs config: {value!r}") from exc


def _to_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "0", "no", "n", "off"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    raise RuntimeError(f"Invalid `{field_name}` value in ElevenLabs config: {value!r}")


class ElevenLabsTTSProvider(TTSProvider):
    API_BASE = "https://api.elevenlabs.io/v1"

    async def generate(self, request: TTSRequest, output_format: str, client: Any) -> GeneratedAudio:
        gen = self.resolve_language_generate(request.language_code)

        actor = gen.get("actor") or ""
        if not actor:
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
            raise RuntimeError("ELEVENLABS_API_KEY is not set.")

        voice_id = gen.get("voice_id")
        if not voice_id:
            raise RuntimeError("ElevenLabs config requires `voice_id` in the generate/languages section.")

        model_id = gen.get("model", "eleven_multilingual_v2")
        language_code = gen.get("language") or request.language_code
        payload = {
            "text": self.build_prompt_text(request),
            "model_id": model_id,
            "language_code": language_code,
        }

        voice_settings_raw = gen.get("voice_settings")
        voice_settings: dict[str, Any] = {}
        if isinstance(voice_settings_raw, dict):
            voice_settings = dict(voice_settings_raw)

        speed = gen.get("speed")
        if speed is not None:
            voice_settings["speed"] = _to_float(speed, "speed")
        elif "speed" in voice_settings:
            voice_settings["speed"] = _to_float(voice_settings["speed"], "voice_settings.speed")

        speaker_boost = (
            gen.get("speaker_boost")
            if gen.get("speaker_boost") is not None
            else gen.get("speder_boost")
        )
        if speaker_boost is None:
            speaker_boost = gen.get("use_speaker_boost")

        if speaker_boost is not None:
            voice_settings["use_speaker_boost"] = _to_bool(speaker_boost, "speaker_boost")
        elif "speaker_boost" in voice_settings:
            voice_settings["use_speaker_boost"] = _to_bool(
                voice_settings.pop("speaker_boost"), "voice_settings.speaker_boost",
            )
        elif "use_speaker_boost" in voice_settings:
            voice_settings["use_speaker_boost"] = _to_bool(
                voice_settings["use_speaker_boost"], "voice_settings.use_speaker_boost",
            )

        if voice_settings:
            payload["voice_settings"] = voice_settings

        elevenlabs_format = ELEVENLABS_OUTPUT_MAP.get(
            output_format, gen.get("output_format", "mp3_44100_128"),
        )
        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json",
        }
        output_path = self.build_output_path(request, output_format)
        endpoint = f"{self.API_BASE}/text-to-speech/{voice_id}"

        started_at = time.perf_counter()
        request_params = {"output_format": elevenlabs_format}
        response = await client.post(
            endpoint,
            headers=headers,
            params=request_params,
            json=payload,
        )
        latency = time.perf_counter() - started_at
        response.raise_for_status()
        log_provider_request(
            base_dir=self.base_dir,
            provider=self.name,
            model=str(model_id),
            actor=str(actor),
            voice_id=str(voice_id),
            language=str(language_code),
            input=payload.get("text", ""),
            response_duration=latency,
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
            raw_extension=output_format,
            latency_seconds=latency,
            success=True,
        )
