"""Audio evaluation with transcription and Levenshtein-based scoring.

Each provider's evaluated config specifies which STT backend to use via
"stt_provider". Currently supported: "openai", "elevenlabs", and
"speaktor" (Transkriptor local-file transcription workflow).
API keys are resolved by convention from "{PROVIDER_NAME}_API_KEY"
environment variables.
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Any

from src.services.logging.schemas import LogProviderRequest
from src.core.languages import to_bcp47
from src.core.log_client import log_provider_request
from src.core.models import EvaluationResult, GeneratedAudio


def levenshtein_distance(left: str, right: str) -> int:
    """Calculate the Levenshtein distance between two strings."""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for left_index, left_char in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_char in enumerate(right, start=1):
            insert_cost = current[right_index - 1] + 1
            delete_cost = previous[right_index] + 1
            replace_cost = previous[right_index - 1] + (left_char != right_char)
            current.append(min(insert_cost, delete_cost, replace_cost))
        previous = current
    return previous[-1]


def normalized_accuracy(left: str, right: str) -> float:
    """Calculate normalized accuracy between two strings."""
    length = max(len(left), len(right), 1)
    return max(0.0, 1.0 - (levenshtein_distance(left, right) / length)) * 100.0


def _resolve_stt_api_key(stt_provider: str) -> str:
    """Resolve the API key from the environment variables."""
    env_name = f"{stt_provider.upper()}_API_KEY"
    return os.getenv(env_name, "")


class EvaluationEngine:
    """Engine for evaluating generated audio against expected text."""

    def __init__(self, timeout_seconds: float = 120.0, base_dir: Path | None = None) -> None:
        self.timeout_seconds = timeout_seconds
        self.base_dir = base_dir

    async def evaluate(
            self,
            generated_audio: GeneratedAudio,
            evaluate_config: dict[str, Any],
            client: Any,
            provider_name: str = "",
    ) -> EvaluationResult:
        """Evaluate the generated audio against the expected text.

        Args:
            generated_audio: The audio to evaluate.
            evaluate_config: The "evaluate" sub-dict from the provider config.
            client: An async HTTP client.
            provider_name: Fallback STT provider name when not set in *evaluate_config*.
        """
        normalized_path = generated_audio.normalized_path
        if not normalized_path:
            return self._fail(
                generated_audio,
                generated_audio.normalization_error or "Normalized audio file was not created.",
            )

        stt_provider = str(evaluate_config.get("stt_provider", provider_name or generated_audio.provider)).lower()
        api_key = _resolve_stt_api_key(stt_provider)
        if not api_key:
            return self._fail(
                generated_audio,
                f"Evaluation skipped: {stt_provider.upper()}_API_KEY is not set.",
            )

        started_at = time.perf_counter()
        transcription = await self._transcribe(
            stt_provider, normalized_path, evaluate_config, api_key, generated_audio, client
        )
        if transcription is None:
            return self._fail(generated_audio, f"Unsupported stt_provider: {stt_provider}")
        latency = time.perf_counter() - started_at

        return self._score(generated_audio, transcription, latency)

    async def _transcribe(
            self,
            stt_provider: str,
            audio_path: Path,
            eval_cfg: dict[str, Any],
            api_key: str,
            generated_audio: GeneratedAudio,
            client: Any,
    ) -> str | None:
        """Dispatch to the STT backend for *stt_provider*, or None if unsupported."""
        if stt_provider == "openai":
            return await self._transcribe_openai(audio_path, eval_cfg, api_key, generated_audio, client)
        if stt_provider == "elevenlabs":
            return await self._transcribe_elevenlabs(audio_path, eval_cfg, api_key, generated_audio, client)
        if stt_provider == "speaktor":
            return await self._transcribe_speaktor(audio_path, eval_cfg, api_key, generated_audio, client)
        return None

    @staticmethod
    def _score(generated_audio: GeneratedAudio, transcription: str, latency: float) -> EvaluationResult:
        """Score a transcription against the expected text and build the EvaluationResult."""
        expected_text = generated_audio.expected_text.strip()
        actual_text = transcription.strip()
        expected_folded = expected_text.casefold()
        actual_folded = actual_text.casefold()
        expected_words = " ".join(expected_folded.split())
        actual_words = " ".join(actual_folded.split())

        char_distance = levenshtein_distance(expected_folded, actual_folded)
        word_distance = levenshtein_distance(expected_words, actual_words)
        char_accuracy = normalized_accuracy(expected_folded, actual_folded)
        word_accuracy = normalized_accuracy(expected_words, actual_words)

        return EvaluationResult(
            provider=generated_audio.provider,
            request_id=generated_audio.request_id,
            expected_text=expected_text,
            transcription=actual_text,
            levenshtein_distance=max(char_distance, word_distance),
            char_accuracy=round(char_accuracy, 2),
            word_accuracy=round(word_accuracy, 2),
            pronunciation_accuracy=round((char_accuracy + word_accuracy) / 2.0, 2),
            latency_seconds=latency,
            success=True,
        )

    async def _transcribe_openai(
            self,
            audio_path: Path,
            eval_cfg: dict[str, Any],
            api_key: str,
            generated_audio: GeneratedAudio,
            client: Any,
    ) -> str:
        """Transcribe the audio file using the OpenAI API."""
        model = eval_cfg.get("model", "whisper-1")
        prompt = str(eval_cfg.get("prompt", "")).strip()
        language = str(eval_cfg.get("language") or generated_audio.language_code)

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
            actor="",
            voice_id="",
            language=language,
            request_input=f"audio:{audio_path.name}",
            response_duration=response_duration,
        )
        return str(response.json().get("text", "")).strip()

    async def _transcribe_elevenlabs(
            self,
            audio_path: Path,
            eval_cfg: dict[str, Any],
            api_key: str,
            generated_audio: GeneratedAudio,
            client: Any,
    ) -> str:
        """Transcribe the audio file using the ElevenLabs API."""
        model = eval_cfg.get("model", "scribe_v1")
        language = str(eval_cfg.get("language") or generated_audio.language_code)

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
            actor="",
            voice_id="",
            language=language,
            request_input=f"audio:{audio_path.name}",
            response_duration=response_duration,
        )
        return str(response.json().get("text", "")).strip()

    async def _transcribe_speaktor(
            self,
            audio_path: Path,
            eval_cfg: dict[str, Any],
            api_key: str,
            generated_audio: GeneratedAudio,
            client: Any,
    ) -> str:
        """Transcribe via the Transkriptor 3-step local-file workflow.

        1. POST /local_file/get_upload_url -> upload_url, public_url
        2. PUT upload_url -> upload raw audio bytes
        3. POST /local_file/initiate_transcription -> order_id
        4. Poll GET /files/{order_id}/content until status == "Completed"
        """
        base_url = "https://api.tor.app/developer"
        language = to_bcp47(
            str(eval_cfg.get("language") or generated_audio.language_code),
        )
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        started_at = time.perf_counter()
        upload_url, public_url = await self._speaktor_get_upload_url(client, base_url, headers, audio_path)
        await self._speaktor_upload_audio(client, upload_url, audio_path)
        order_id = await self._speaktor_initiate_transcription(client, base_url, headers, public_url, language)
        transcription_text = await self._speaktor_poll_transcription(client, base_url, headers, eval_cfg, order_id)
        response_duration = time.perf_counter() - started_at

        await self._log_request(
            provider="speaktor",
            model="transkriptor-stt",
            actor="",
            voice_id="",
            language=language,
            request_input=f"audio:{audio_path.name}",
            response_duration=response_duration,
        )
        return transcription_text

    async def _speaktor_get_upload_url(
            self, client: Any, base_url: str, headers: dict[str, str], audio_path: Path
    ) -> tuple[str, str]:
        """Step 1 -- get a pre-signed upload URL. Returns (upload_url, public_url)."""
        response = await client.post(
            f"{base_url}/transcription/local_file/get_upload_url",
            headers=headers,
            json={"file_name": audio_path.name},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        upload_meta = response.json()
        return upload_meta["upload_url"], upload_meta["public_url"]

    async def _speaktor_upload_audio(self, client: Any, upload_url: str, audio_path: Path) -> None:
        """Step 2 -- upload the audio file."""
        response = await client.put(upload_url, content=audio_path.read_bytes(), timeout=self.timeout_seconds)
        response.raise_for_status()

    async def _speaktor_initiate_transcription(
            self, client: Any, base_url: str, headers: dict[str, str], public_url: str, language: str
    ) -> str:
        """Step 3 -- initiate transcription. Returns the order_id."""
        response = await client.post(
            f"{base_url}/transcription/local_file/initiate_transcription",
            headers=headers,
            json={
                "url": public_url,
                "language": language,
                "service": "Standard",
            },
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()["order_id"]

    async def _speaktor_poll_transcription(
            self, client: Any, base_url: str, headers: dict[str, str], eval_cfg: dict[str, Any], order_id: str
    ) -> str:
        """Step 4 -- poll until the transcription completes."""
        poll_interval = float(eval_cfg.get("poll_interval_seconds", 3))
        poll_max = int(eval_cfg.get("poll_max_attempts", 40))
        for _ in range(poll_max):
            await asyncio.sleep(poll_interval)
            response = await client.get(
                f"{base_url}/files/{order_id}/content",
                headers=headers,
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            content_data = response.json()
            body = content_data.get("body", content_data)
            if str(body.get("status", "")).strip() == "Completed":
                segments = body.get("content", [])
                return " ".join(
                    str(seg.get("text", "")).strip()
                    for seg in segments
                    if str(seg.get("text", "")).strip()
                )
        raise RuntimeError(
            f"Transkriptor transcription did not complete within "
            f"{poll_max * poll_interval:.0f}s (order_id={order_id})."
        )

    @staticmethod
    def _fail(generated_audio: GeneratedAudio, error: str) -> EvaluationResult:
        return EvaluationResult(
            provider=generated_audio.provider,
            request_id=generated_audio.request_id,
            expected_text=generated_audio.expected_text,
            transcription="",
            levenshtein_distance=0,
            char_accuracy=0.0,
            word_accuracy=0.0,
            pronunciation_accuracy=0.0,
            latency_seconds=0.0,
            success=False,
            error=error,
        )

    async def _log_request(
            self,
            *,
            provider: str,
            model: str,
            actor: str,
            voice_id: str,
            language: str,
            request_input: Any,
            response_duration: float,
    ) -> None:
        await log_provider_request(
            LogProviderRequest(
                provider=provider,
                tts_model=model,
                actor=actor,
                voice_id=voice_id,
                language=language,
                input_data=request_input,
                response_duration=response_duration,
            )
        )
