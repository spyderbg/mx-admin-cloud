"""Voice evaluation service: provider base classes and the evaluation/score value types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.languages import LANG_MAP
from src.core.log_client import log_provider_request
from src.services.logging import LogProviderRequest
from src.services.models import ServiceProvider


@dataclass(slots=True)
class EvaluationResult:  # pylint: disable=too-many-instance-attributes
    """Scoring result from comparing generated audio against expected text."""

    provider: str  # provider that produced the audio
    request_id: str  # matching TTSRequest.request_id
    expected_text: str  # ground-truth reference text
    transcription: str  # text transcribed from the generated audio
    levenshtein_distance: int  # edit distance between expected and transcription
    char_accuracy: float  # character-level accuracy (0.0 - 1.0)
    word_accuracy: float  # word-level accuracy (0.0 - 1.0)
    pronunciation_accuracy: float  # composite pronunciation score (0.0 - 1.0)
    latency_seconds: float  # wall-clock evaluation time
    success: bool  # whether evaluation succeeded
    error: str | None = None  # error message on failure
    rolling_average: float | None = None  # running average score across evaluations


@dataclass(slots=True)
class ProviderAggregate:  # pylint: disable=too-many-instance-attributes
    """Cumulative score statistics for a single provider."""

    provider: str  # provider identifier
    evaluation_count: int  # number of evaluations included
    total_score: float  # sum of individual scores
    average_score: float  # mean score (total / count)


@dataclass(slots=True)
class ScoreRecord:  # pylint: disable=too-many-instance-attributes
    """One persisted evaluation entry, as returned by ScoreStore lookups."""

    provider: str  # provider that produced the audio
    request_id: str  # matching TTSRequest.request_id
    language_code: str  # ISO language code
    expected_text: str  # ground-truth reference text
    transcription: str  # text transcribed from the generated audio
    pronunciation_accuracy: float  # composite pronunciation score (0.0 - 1.0)
    evaluation_latency_seconds: float  # wall-clock evaluation time
    raw_path: str  # path to the raw generated audio file
    normalized_path: str | None  # path after normalization (if any)
    success: bool  # whether evaluation succeeded
    error: str | None = None  # error message on failure


class EvaluationServiceProvider(ServiceProvider):
    """Base class for translator service providers."""

    @staticmethod
    def language_name(lang_code: str) -> str:
        """Return the display name for a BCP-47 language code."""
        return LANG_MAP.get(lang_code.lower(), lang_code)

    @abstractmethod
    async def evaluate_text(
            self,
            text: str,
            input_lang_code: str,
            target_lang_code: str,
            category: str | None = None,
    ) -> str | None:
        """Translate a single word/phrase.

        Returns the translated string, or "None" when this translator cannot
        handle the request (signals fallback to the next translator).
        """


class STTProvider(ABC):  # pylint: disable=too-few-public-methods
    """Contract for one speech-to-text (STT) backend that ``EvaluationEngine`` transcribes with.

    Each concrete backend is its own package under ``providers/`` and registers its class with
    ``SpeechToTextProviderFactory`` under the id that a text-to-speech configuration names in
    ``evaluate.stt_provider`` (``providers/openai_stt/`` registers ``"openai"``). Unlike a
    ``ServiceProvider`` it has no configuration file: the engine creates one instance per evaluation
    and passes the ``evaluate`` block to every call.
    """

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds  # for each HTTP request

    @abstractmethod
    async def transcribe(
            self,
            audio_path: Path,
            language: str,
            eval_cfg: dict[str, Any],
            api_key: str,
            client: Any,
    ) -> str:
        """Return the text spoken in *audio_path*. HTTP and polling errors propagate to the caller.

        Args:
            audio_path: The normalized audio file.
            language: ``eval_cfg["language"]`` when set, else the generated audio's language code.
            eval_cfg: The ``evaluate`` block; the backend reads its own keys (``model``, ``prompt``, polling).
            api_key: The value of ``<STT_PROVIDER>_API_KEY`` (the engine skips evaluation when it is empty).
            client: The async HTTP client (an ``httpx.AsyncClient``) of the evaluation request.
        """

    @staticmethod
    async def _log_request(
            *,
            provider: str,
            model: str,
            language: str,
            request_input: Any,
            response_duration: float,
    ) -> None:
        """Record one STT call in the provider request log."""
        await log_provider_request(
            LogProviderRequest(
                provider=provider,
                tts_model=model,
                actor="",
                voice_id="",
                language=language,
                input_data=request_input,
                response_duration=response_duration,
            )
        )
