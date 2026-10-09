"""Schemas for internal text-to-image endpoints."""

from typing import Any

from pydantic import BaseModel, Field


class ImageGeneratorProviderInfo(BaseModel):
    """Metadata for one configured image provider (GET /internal/image-generators)."""

    name: str
    provider: str
    enabled: bool
    priority: int
    config_path: str
    generate: dict[str, Any]
    traffic: dict[str, Any]
    pricing: dict[str, Any]


class GenerateImageRequest(BaseModel):
    """Body for a single image generation request."""

    prompt: str
    filename_text: str | None = None
    negative_prompt: str | None = None
    language_code: str | None = None
    size: str | None = None
    quality: str | None = None
    output_format: str = "png"
    seed: int | None = None
    request_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class GenerateImageBytesRequest(BaseModel):
    """Body for single-image generation returning raw bytes."""

    prompt: str
    filename_text: str | None = None
    # Name of the (active) image configuration to generate with; omitted: first active one that succeeds.
    provider: str | None = None


class ImageGenerateResult(BaseModel):
    """Serialized generated-image payload returned by the generate endpoint."""

    provider: str
    request_id: str
    prompt: str
    filename_text: str
    output_format: str
    raw_path: str
    raw_extension: str
    latency_seconds: float
    success: bool
    model: str | None = None
    width: int | None = None
    height: int | None = None
    cost_estimate: float | None = None
    attempts: int = 1
    error: str | None = None
