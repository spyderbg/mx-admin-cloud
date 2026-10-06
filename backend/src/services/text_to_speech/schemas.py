"""Schemas for internal text-to-speech endpoints."""

from typing import Any

from pydantic import BaseModel, Field


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


class VoiceSettingsOverride(BaseModel):
    """Voice settings for one generation, layered over the configuration's own."""

    speed: float | None = Field(default=None, gt=0)
    stability: float | None = Field(default=None, ge=0, le=1)
    similarity: float | None = Field(default=None, ge=0, le=1)


class GenerateWordRequest(BaseModel):
    """Body for single-word TTS generation."""

    word: str
    lang: str
    # Configuration to generate with, enabled or not. None tries the active ones by priority.
    configuration_name: str | None = None
    settings: VoiceSettingsOverride | None = None


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
