"""Offline text-to-speech provider backed by the pyttsx3 library."""

from __future__ import annotations

import asyncio
import logging
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from src.core.log_client import log_provider_request
from src.services.text_to_speech.models import GeneratedAudio
from src.services.logging import LogProviderRequest
from src.services.text_to_speech.models import TTSProvider, TTSRequest
from src.services.text_to_speech.service import TextToSpeechProviderFactory

logger = logging.getLogger(__name__)

# pyttsx3 drives a single system speech engine, which is not safe to use from several threads.
_ENGINE_LOCK = threading.Lock()
_LANGUAGE_PREFIX = "\x00\x01\x02\x03\x04\x05"  # eSpeak prefixes each language code with a control byte
_DEFAULT_RATE = 200  # words per minute pyttsx3 starts from


class Pyttsx3TTSProvider(TTSProvider):
    """Synthesize speech locally with the system speech engine (SAPI5, NSSpeechSynthesizer, eSpeak).

    Per-language generate keys: ``actor`` (a voice id or part of a voice name or language code),
    ``rate`` (words per minute) and ``volume`` (0.0-1.0). A request's ``speed`` scales ``rate``.
    The engine writes WAV; other output formats are converted with ffmpeg through pydub.
    """

    @classmethod
    def register(cls) -> None:
        """Register this provider under the key "pyttsx3" in the ProviderFactory."""
        TextToSpeechProviderFactory.register("pyttsx3", cls)

    def api_key_env_name(self) -> str:
        """The provider runs locally and needs no API key."""
        return ""

    @staticmethod
    def _pick_voice(voices: list[Any], actor: str, language_code: str) -> str | None:
        """Return the id of the voice matching *actor*, else one speaking *language_code*, else None."""
        wanted = actor.strip().lower()
        if wanted:
            for voice in voices:
                if wanted in (str(voice.id).lower(), str(voice.name).lower()):
                    return voice.id
            for voice in voices:
                if wanted in str(voice.id).lower() or wanted in str(voice.name).lower():
                    return voice.id
        language = language_code.strip().lower()
        for voice in voices:
            codes = [
                (code.decode("utf-8", "ignore") if isinstance(code, bytes) else str(code))
                .lower().lstrip(_LANGUAGE_PREFIX)
                for code in getattr(voice, "languages", None) or []
            ]
            if any(code == language or code.startswith(f"{language}-") or code.startswith(f"{language}_")
                   for code in codes) or str(voice.id).lower().endswith(f"/{language}"):
                return voice.id
        return None

    def _synthesize(self, text: str, gen: dict[str, Any], language_code: str, wav_path: Path) -> str:
        """Write *text* to *wav_path* with the system engine and return the voice id used."""
        import pyttsx3  # pylint: disable=import-outside-toplevel

        with _ENGINE_LOCK:
            engine = pyttsx3.init()
            try:
                voice_id = self._pick_voice(engine.getProperty("voices"), str(gen.get("actor") or ""), language_code)
                if voice_id:
                    engine.setProperty("voice", voice_id)
                rate = float(gen.get("rate") or _DEFAULT_RATE) * float(gen.get("speed") or 1.0)
                engine.setProperty("rate", int(round(rate)))
                if gen.get("volume") is not None:
                    engine.setProperty("volume", float(gen["volume"]))
                engine.save_to_file(text, str(wav_path))
                engine.runAndWait()
            finally:
                engine.stop()
        if not wav_path.is_file() or wav_path.stat().st_size == 0:
            raise RuntimeError("pyttsx3 produced no audio; check that a system speech engine is installed.")
        return str(voice_id or "")

    async def generate(self, request: TTSRequest, client: Any) -> GeneratedAudio:
        """Synthesize the request text locally and write the audio to disk."""
        output_format = request.output_format
        gen = self.resolve_request_generate(request)
        output_path = self.build_output_path(request, output_format)
        text = self.build_prompt_text(request)

        started_at = time.perf_counter()
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "speech.wav"
            voice_id = await asyncio.to_thread(self._synthesize, text, gen, request.language_code, wav_path)
            if output_format.lstrip(".").lower() == "wav":
                output_path.write_bytes(wav_path.read_bytes())
            else:
                from pydub import AudioSegment  # pylint: disable=import-outside-toplevel

                await asyncio.to_thread(
                    lambda: AudioSegment.from_wav(wav_path).export(output_path, format=output_format.lstrip(".")),
                )
        latency = time.perf_counter() - started_at

        await log_provider_request(
            LogProviderRequest(
                provider=self.name,
                tts_model="pyttsx3",
                actor=str(gen.get("actor") or ""),
                voice_id=voice_id,
                language=str(request.language_code),
                input_data=text,
                response_duration=latency,
            )
        )
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


Pyttsx3TTSProvider.register()
