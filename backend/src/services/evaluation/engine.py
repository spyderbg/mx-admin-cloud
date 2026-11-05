"""Audio evaluation with transcription and Levenshtein-based scoring.

Each provider's evaluated config specifies which STT backend to use via
"stt_provider": the id of a provider registered with SpeechToTextProviderFactory
(one package each under providers/: "openai", "elevenlabs", and "speaktor").
API keys are resolved by convention from "{PROVIDER_NAME}_API_KEY"
environment variables.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from src.services.text_to_speech import GeneratedAudio
from src.services.evaluation.models import EvaluationResult, STTProvider
from src.services.evaluation.service import SpeechToTextProviderFactory


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

        stt = self._stt_provider(stt_provider)
        if stt is None:
            return self._fail(generated_audio, f"Unsupported stt_provider: {stt_provider}")

        started_at = time.perf_counter()
        transcription = await self._transcribe(stt, normalized_path, evaluate_config, api_key, generated_audio, client)
        latency = time.perf_counter() - started_at

        return self._score(generated_audio, transcription, latency)

    async def compare(
            self,
            generated_audio_a: GeneratedAudio,
            generated_audio_b: GeneratedAudio,
            evaluate_config: dict[str, Any],
            client: Any,
            provider_name: str = "",
    ) -> EvaluationResult:
        """Compare two generated audio clips against each other.

        Transcribes *generated_audio_a* and *generated_audio_b* with the same STT
        backend, then scores *generated_audio_b*'s transcription against
        *generated_audio_a*'s transcription (used as the reference text), with the
        same Levenshtein-based metrics as evaluate().

        Args:
            generated_audio_a: The audio providing the reference transcription.
            generated_audio_b: The audio scored against generated_audio_a's transcription.
            evaluate_config: The "evaluate" sub-dict from the provider config.
            client: An async HTTP client.
            provider_name: Fallback STT provider name when not set in *evaluate_config*.
        """
        for generated_audio in (generated_audio_a, generated_audio_b):
            if not generated_audio.normalized_path:
                return self._fail(
                    generated_audio_b,
                    generated_audio.normalization_error or "Normalized audio file was not created.",
                )

        stt_provider = str(
            evaluate_config.get("stt_provider", provider_name or generated_audio_a.provider)
        ).lower()
        api_key = _resolve_stt_api_key(stt_provider)
        if not api_key:
            return self._fail(
                generated_audio_b,
                f"Evaluation skipped: {stt_provider.upper()}_API_KEY is not set.",
            )

        stt = self._stt_provider(stt_provider)
        if stt is None:
            return self._fail(generated_audio_b, f"Unsupported stt_provider: {stt_provider}")

        started_at = time.perf_counter()
        transcription_a = await self._transcribe(
            stt, generated_audio_a.normalized_path, evaluate_config, api_key, generated_audio_a, client
        )
        transcription_b = await self._transcribe(
            stt, generated_audio_b.normalized_path, evaluate_config, api_key, generated_audio_b, client
        )
        latency = time.perf_counter() - started_at

        return self._score_transcriptions(
            provider=generated_audio_b.provider,
            request_id=generated_audio_b.request_id,
            expected_text=transcription_a,
            transcription=transcription_b,
            latency=latency,
        )

    def _stt_provider(self, stt_provider: str) -> STTProvider | None:
        """The STT backend registered for *stt_provider* (a package under providers/), or None if there is none."""
        if not SpeechToTextProviderFactory.supports(stt_provider):
            return None
        return SpeechToTextProviderFactory.create(stt_provider, timeout_seconds=self.timeout_seconds)

    @staticmethod
    async def _transcribe(
            stt: STTProvider,
            audio_path: Path,
            eval_cfg: dict[str, Any],
            api_key: str,
            generated_audio: GeneratedAudio,
            client: Any,
    ) -> str:
        """Transcribe *audio_path* with *stt*, in the configured language or else the audio's own."""
        language = str(eval_cfg.get("language") or generated_audio.language_code)
        return await stt.transcribe(audio_path, language, eval_cfg, api_key, client)

    @staticmethod
    def _score(generated_audio: GeneratedAudio, transcription: str, latency: float) -> EvaluationResult:
        """Score a transcription against the expected text and build the EvaluationResult."""
        return EvaluationEngine._score_transcriptions(
            provider=generated_audio.provider,
            request_id=generated_audio.request_id,
            expected_text=generated_audio.expected_text,
            transcription=transcription,
            latency=latency,
        )

    @staticmethod
    def _score_transcriptions(
            *,
            provider: str,
            request_id: str,
            expected_text: str,
            transcription: str,
            latency: float,
    ) -> EvaluationResult:
        """Score *transcription* against *expected_text* and build the EvaluationResult."""
        expected_text = expected_text.strip()
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
            provider=provider,
            request_id=request_id,
            expected_text=expected_text,
            transcription=actual_text,
            levenshtein_distance=max(char_distance, word_distance),
            char_accuracy=round(char_accuracy, 2),
            word_accuracy=round(word_accuracy, 2),
            pronunciation_accuracy=round((char_accuracy + word_accuracy) / 2.0, 2),
            latency_seconds=latency,
            success=True,
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
