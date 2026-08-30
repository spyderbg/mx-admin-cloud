"""Pydantic schemas for internal API routes.

These models define the internal HTTP contract and are used for OpenAPI output.
"""

from src.api.internal.schemas.jobs import GenerateJobResponse, JobStatusResponse

__all__ = [
    "GenerateJobResponse",
    "JobStatusResponse",
]
