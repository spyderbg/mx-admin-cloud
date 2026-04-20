"""Shared Pydantic response schemas used across service routes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ErrorMessage(BaseModel):
    """Error message returned by the API"""

    message: str = Field(..., description="Human-readable error message")
    error: Any | None = None


class ErrorHttpCode(BaseModel):
    """Error message returned by the API, with HTTP status code."""

    http_code: int = Field(..., description="HTTP status code")
    message: str = Field(..., description="Human-readable error message")
    error: Any | None = None
