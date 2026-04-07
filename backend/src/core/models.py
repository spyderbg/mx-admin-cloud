"""Core dataclasses used across the TTS pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

@dataclass(slots=True)
class GeneratedAudio:
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
class EvaluationResult:
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
class ProviderAggregate:
    """Cumulative score statistics for a single provider."""

    provider: str  # provider identifier
    evaluation_count: int  # number of evaluations included
    total_score: float  # sum of individual scores
    average_score: float  # mean score (total / count)


@dataclass(slots=True)
class ProviderRunRecord:
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
        return asdict(self)


@dataclass(slots=True)
class RunSummary:
    """Top-level summary for a complete pipeline run."""

    run_id: str  # timestamp-based run identifier
    input_file: str  # path to the input file
    output_format: str  # target audio format
    provider_records: list[ProviderRunRecord]  # per-request results

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "input_file": self.input_file,
            "output_format": self.output_format,
            "provider_records": [record.to_dict() for record in self.provider_records],
        }
