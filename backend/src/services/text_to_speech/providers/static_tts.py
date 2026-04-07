"""Static-voice TTS provider using ElevenLabs with English voice settings."""

from __future__ import annotations

import logging
import time
from typing import Any

from src.core.models import GeneratedAudio
from ...logging import log_provider_request
from .elevenlabs_tts import API_BASE, ELEVENLABS_OUTPUT_MAP, _to_bool, _to_float
from ..models import TTSProvider, TTSRequest
from ..service import TextToSpeechProviderFactory

logger = logging.getLogger(__name__)


class StaticTTSProvider(TTSProvider):
    """Generate audio with a fixed English voice configuration for all languages."""

    @classmethod
    def register(cls) -> None:
        """Register this provider under the key ``"static"`` in the ProviderFactory."""
        TextToSpeechProviderFactory.register("static", cls)

    def api_key_env_name(self) -> str:
        """Reuse ElevenLabs credentials for the static provider backend."""
        return "ELEVENLABS_API_KEY"

    def _build_failure(self, request: TTSRequest, error: str) -> GeneratedAudio:
        """Build a failed generation response with common request metadata."""
        output_format = request.output_format
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
            error=error,
        )

    @staticmethod
    def _resolve_voice_settings(gen: dict[str, Any]) -> dict[str, Any]:
        """Normalize and merge voice settings from top-level and nested config keys."""
        voice_settings_raw = gen.get("voice_settings")
        voice_settings: dict[str, Any] = {}
        if isinstance(voice_settings_raw, dict):
            voice_settings = dict(voice_settings_raw)

        speed = gen.get("speed")
        if speed is not None:
            voice_settings["speed"] = _to_float(speed, "speed")
        elif "speed" in voice_settings:
            voice_settings["speed"] = _to_float(voice_settings["speed"], "voice_settings.speed")

        speaker_boost = gen.get("speaker_boost")
        if speaker_boost is None:
            speaker_boost = gen.get("speder_boost")
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

        return voice_settings

    async def _generate_with_english_config(
        self,
        request: TTSRequest,
        client: Any,
        gen: dict[str, Any],
    ) -> GeneratedAudio:
        """Call ElevenLabs with resolved English voice config and persist audio."""
        output_format = request.output_format
        api_key = self.resolve_api_key()
        if not api_key:
            raise RuntimeError("ELEVENLABS_API_KEY is not set.")

        voice_id = gen.get("voice_id")
        if not voice_id:
            raise RuntimeError("Static provider requires `voice_id` in generate/languages.en.")

        model_id = gen.get("model", "eleven_multilingual_v2")
        payload = {
            "text": self.build_prompt_text(request),
            "model_id": model_id,
            "language_code": request.language_code,
        }
        voice_settings = self._resolve_voice_settings(gen)
        if voice_settings:
            payload["voice_settings"] = voice_settings

        output_path = self.build_output_path(request, output_format)
        started_at = time.perf_counter()
        response = await client.post(
            f"{API_BASE}/text-to-speech/{voice_id}",
            headers={"xi-api-key": api_key, "Content-Type": "application/json"},
            params={
                "output_format": ELEVENLABS_OUTPUT_MAP.get(
                    output_format, gen.get("output_format", "mp3_44100_128"),
                ),
            },
            json=payload,
        )
        latency = time.perf_counter() - started_at
        response.raise_for_status()

        log_provider_request(
            base_dir=self.base_dir,
            provider=self.name,
            model=str(model_id),
            actor=str(gen.get("actor") or ""),
            voice_id=str(voice_id),
            language=str(request.language_code),
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

    async def generate(self, request: TTSRequest, client: Any) -> GeneratedAudio:
        """Generate audio while always resolving voice settings from English config."""
        gen = self.resolve_language_generate("en")

        actor = gen.get("actor") or ""
        if not actor:
            logger.warning(
                "No English actor configured for provider=%s -- skipping API request.",
                self.name,
            )
            return self._build_failure(request, "No English actor configured for static provider")
        return await self._generate_with_english_config(request, client, gen)

    async def evaluate(self, *_: Any, **__: Any) -> None:
        """Static provider has no provider-specific evaluation hook."""
        return None


StaticTTSProvider.register()
