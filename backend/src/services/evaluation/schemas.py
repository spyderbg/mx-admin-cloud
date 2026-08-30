"""Pydantic request/response schemas for internal evaluation endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class EvaluateRequest(BaseModel):
    """Body for a single evaluation request."""

    provider: str
    request_id: str
    expected_text: str
    language_code: str
    language_name: str
    filename_text: str
    output_format: str
    raw_path: str
    raw_extension: str
    latency_seconds: float
    normalized_path: str | None = None
    normalization_error: str | None = None
    evaluate_config: dict[str, Any]
    output_dir: str
