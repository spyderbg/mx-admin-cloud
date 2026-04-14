"""Schemas for internal text-to-speech endpoints."""

from typing import Any

from pydantic import BaseModel


class GeneratorProviderInfo(BaseModel):
    """Metadata for one configured TTS provider (GET /internal/generators)."""

    name: str
    enabled: bool
    config_path: str
    generate: dict[str, Any]
    evaluate_enabled: bool


class GenerateRequest(BaseModel):
    """Body for a single TTS generation request."""

    text: str
    language_code: str
    language_name: str
    filename_text: str
    expected_text: str
    source_file: str
    source_type: str
    output_format: str = "mp3"
    row_index: int
    column_index: int | None = None
    request_id: str | None = None


class GenerateWordRequest(BaseModel):
    """Body for single-word TTS generation."""

    word: str
    lang: str


class TtsGenerateResult(BaseModel):
    """Serialized generated-audio payload returned by the generate endpoint."""

    provider: str
    request_id: str
    language_code: str
    language_name: str
    expected_text: str
    filename_text: str
    output_format: str
    raw_path: str
    raw_extension: str
    latency_seconds: float
    success: bool
    error: str | None = None
    normalized_path: str | None = None
    normalization_error: str | None = None
