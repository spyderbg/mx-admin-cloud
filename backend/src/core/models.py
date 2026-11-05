"""Core dataclasses: TTS pipeline run records (used by ``result_writer``).

Service value types live in their service's ``models.py`` (e.g. ``GeneratedAudio`` in ``text_to_speech``).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


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
