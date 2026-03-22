"""Core dataclasses used across the TTS pipeline."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class TTSRequest:
    request_id: str
    text: str
    language_code: str
    language_name: str
    filename_text: str
    expected_text: str
    source_file: str
    source_type: str
    row_index: int
    column_index: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProviderConfig:
    name: str
    generate: dict[str, Any]
    evaluate: dict[str, Any]
    config_path: Path


@dataclass(slots=True)
class TranslatorConfig:
    type: str
    name: str
    active: bool
    priority: int
    config: dict[str, Any]
    config_path: Path


@dataclass(slots=True)
class OptimizerConfig:
    type: str
    name: str
    active: bool
    priority: int
    config: dict[str, Any]
    config_path: Path


@dataclass(slots=True)
class GeneratedAudio:
    provider: str
    request_id: str
    language_code: str
    language_name: str
    expected_text: str
    filename_text: str
    output_format: str
    raw_path: Path
    raw_extension: str
    latency_seconds: float
    success: bool
    error: str | None = None
    normalized_path: Path | None = None
    normalization_error: str | None = None


@dataclass(slots=True)
class EvaluationResult:
    provider: str
    request_id: str
    expected_text: str
    transcription: str
    levenshtein_distance: int
    char_accuracy: float
    word_accuracy: float
    pronunciation_accuracy: float
    latency_seconds: float
    success: bool
    error: str | None = None
    rolling_average: float | None = None


@dataclass(slots=True)
class ProviderAggregate:
    provider: str
    evaluation_count: int
    total_score: float
    average_score: float


@dataclass(slots=True)
class ProviderRunRecord:
    request_id: str
    provider: str
    source_file: str
    source_type: str
    language_code: str
    language_name: str
    expected_text: str
    filename_text: str
    output_format: str
    raw_path: str
    normalized_path: str | None
    generation_latency_seconds: float
    evaluation_latency_seconds: float | None
    evaluation_score: float | None
    rolling_average_score: float | None
    transcription: str | None
    success: bool
    error: str | None
    optimized_paths: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RunSummary:
    run_id: str
    input_file: str
    output_format: str
    provider_records: list[ProviderRunRecord]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "input_file": self.input_file,
            "output_format": self.output_format,
            "provider_records": [record.to_dict() for record in self.provider_records],
        }
