"""Core dataclasses for the TTS pipeline and file-derived structures (e.g. translation sources)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TranslationSourceWord:  # pylint: disable=too-many-instance-attributes
    """One text segment from an input file intended for translation.

    Holds file-derived fields only (no TTS "request_id" or HTTP request metadata).
    """

    text: str
    source_language_code: str
    filename_text: str
    expected_text: str
    row_index: int
    column_index: int | None
    source_file: str
    source_type: str


@dataclass(slots=True)
class GeneratedAudio:  # pylint: disable=too-many-instance-attributes
    """Result of a single TTS generation attempt by one provider."""

    provider: str  # provider produced this audio
    request_id: str  # matching TTSRequest.request_id
    language_code: str  # ISO language code
    language_name: str  # human-readable language name
    expected_text: str  # reference text for evaluation
    filename_text: str  # sanitized text for the output filename
    output_format: str  # audio format, e.g. "mp3"
    raw_path: Path  # path to the raw generated audio file
    raw_extension: str  # file extension of the raw audio
    latency_seconds: float  # wall-clock generation time
    success: bool  # whether generation succeeded
    error: str | None = None  # error message on failure
    normalized_path: Path | None = None  # path after normalization (if any)
    normalization_error: str | None = None  # normalization error (if any)


@dataclass(slots=True)
class GeneratedImage:  # pylint: disable=too-many-instance-attributes
    """Result of a single image generation attempt by one provider."""

    provider: str  # provider that produced this image
    request_id: str  # matching ImageRequest.request_id
    prompt: str  # prompt sent to the provider
    filename_text: str  # sanitized text for the output filename
    output_format: str  # requested image format, e.g. "png"
    raw_path: Path  # path to the generated image file
    raw_extension: str  # actual file extension written (png/jpg/webp)
    latency_seconds: float  # wall-clock generation time
    success: bool  # whether generation succeeded
    model: str | None = None  # provider model identifier
    width: int | None = None  # image width in pixels (when known)
    height: int | None = None  # image height in pixels (when known)
    cost_estimate: float | None = None  # estimated USD cost, None when unpriced
    attempts: int = 1  # number of provider attempts made (retries included)
    error: str | None = None  # error message on failure
    language_code: str | None = None  # optional language context for logging


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
class ProviderRunRecord:  # pylint: disable=too-many-instance-attributes
    """Flat record combining generation, evaluation, and optimization results."""

    request_id: str  # unique request identifier
    provider: str  # provider that handled the request
    source_file: str  # originating input file
    source_type: str  # input format
    language_code: str  # ISO language code
    language_name: str  # human-readable language
    expected_text: str  # reference text
    filename_text: str  # text used in filenames
    output_format: str  # audio format
    raw_path: str  # path to the raw audio
    normalized_path: str | None  # path after normalisation
    generation_latency_seconds: float  # generation wall-clock time
    evaluation_latency_seconds: float | None  # evaluation wall-clock time
    evaluation_score: float | None  # pronunciation accuracy score
    rolling_average_score: float | None  # running average across evaluations
    transcription: str | None  # transcribed text from audio
    success: bool  # overall success flag
    error: str | None  # combined error message (if any)
    optimized_paths: dict[str, str] = field(default_factory=dict)  # optimizer_name -> path

    def to_dict(self) -> dict[str, Any]:
        """Return this record as a plain dict, suitable for JSON/CSV serialization."""
        return asdict(self)


@dataclass(slots=True)
class RunSummary:
    """Top-level summary for a complete pipeline run."""

    run_id: str  # timestamp-based run identifier
    input_file: str  # path to the input file
    output_format: str  # target audio format
    provider_records: list[ProviderRunRecord]  # per-request results

    def to_dict(self) -> dict[str, Any]:
        """Return this summary as a plain dict, suitable for JSON/CSV serialization."""
        return {
            "run_id": self.run_id,
            "input_file": self.input_file,
            "output_format": self.output_format,
            "provider_records": [record.to_dict() for record in self.provider_records],
        }
