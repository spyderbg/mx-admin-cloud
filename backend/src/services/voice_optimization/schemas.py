"""Pydantic request/response schemas for internal voice-optimization endpoints."""

from pydantic import BaseModel


class OptimizeRequest(BaseModel):
    """Body for a single optimization request."""

    raw_path: str
    output_path: str
