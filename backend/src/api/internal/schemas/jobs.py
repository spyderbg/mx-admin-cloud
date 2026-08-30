"""Schemas for internal job endpoints."""

from typing import Any

from pydantic import BaseModel


class GenerateJobResponse(BaseModel):
    """Response body for a voice generation job."""

    job_id: str
    status: str
    message: str


class JobStatusResponse(BaseModel):
    """Response body for a voice generation job status."""

    job_id: str
    status: str
    result: dict[str, Any] | None = None
    error: str | None = None
