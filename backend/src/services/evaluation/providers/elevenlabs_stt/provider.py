"""ElevenLabs speech-to-text backend (``evaluate.stt_provider: "elevenlabs"``).

Config keys read from the ``evaluate`` block: ``model`` (default ``scribe_v1``).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from src.services.evaluation.models import STTProvider
from src.services.evaluation.service import SpeechToTextProviderFactory


class ElevenLabsSTTProvider(STTProvider):
    """Transcribes through the ElevenLabs speech-to-text API."""

    @classmethod
    def register(cls) -> None:
        """Register this provider under the key ``"elevenlabs"`` in SpeechToTextProviderFactory."""
        SpeechToTextProviderFactory.register("elevenlabs", cls)

    async def transcribe(
            self,
            audio_path: Path,
            language: str,
            eval_cfg: dict[str, Any],
            api_key: str,
            client: Any,
    ) -> str:
        """Transcribe the audio file using the ElevenLabs API."""
        model = eval_cfg.get("model", "scribe_v1")

        headers = {"xi-api-key": api_key}
        files: dict[str, Any] = {
            "file": (audio_path.name, audio_path.read_bytes(), "audio/wav"),
            "model_id": (None, model),
            "language_code": (None, language),
        }

        endpoint = "https://api.elevenlabs.io/v1/speech-to-text"
        started_at = time.perf_counter()
        response = await client.post(
            endpoint,
            headers=headers,
            files=files,
            timeout=self.timeout_seconds,
        )
        response_duration = time.perf_counter() - started_at
        response.raise_for_status()
        await self._log_request(
            provider="elevenlabs",
            model=str(model),
            language=language,
            request_input=f"audio:{audio_path.name}",
            response_duration=response_duration,
        )
        return str(response.json().get("text", "")).strip()


ElevenLabsSTTProvider.register()
