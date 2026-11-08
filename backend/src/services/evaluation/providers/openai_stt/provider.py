"""OpenAI speech-to-text backend (``evaluate.stt_provider: "openai"``).

Config keys read from the ``evaluate`` block: ``model`` (default ``whisper-1``) and ``prompt``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from src.services.evaluation.models import STTProvider
from src.services.evaluation.service import SpeechToTextProviderFactory


class OpenAISTTProvider(STTProvider):
    """Transcribes through the OpenAI audio transcription API."""

    @classmethod
    def register(cls) -> None:
        """Register this provider under the key ``"openai"`` in SpeechToTextProviderFactory."""
        SpeechToTextProviderFactory.register("openai", cls)

    async def transcribe(
            self,
            audio_path: Path,
            language: str,
            eval_cfg: dict[str, Any],
            api_key: str,
            client: Any,
    ) -> str:
        """Transcribe the audio file using the OpenAI API."""
        model = eval_cfg.get("model", "whisper-1")
        prompt = str(eval_cfg.get("prompt", "")).strip()

        headers = {"Authorization": f"Bearer {api_key}"}
        files: dict[str, Any] = {
            "file": (audio_path.name, audio_path.read_bytes(), "audio/wav"),
            "model": (None, model),
            "response_format": (None, "json"),
            "language": (None, language),
        }
        if prompt:
            files["prompt"] = (None, prompt)

        endpoint = "https://api.openai.com/v1/audio/transcriptions"
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
            provider="openai",
            model=str(model),
            language=language,
            request_input=f"audio:{audio_path.name}",
            response_duration=response_duration,
        )
        return str(response.json().get("text", "")).strip()


OpenAISTTProvider.register()
