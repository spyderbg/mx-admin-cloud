"""Pydantic request/response schemas for v1 common routes."""

from __future__ import annotations

from pydantic import BaseModel


class ListFilesRequest(BaseModel):
    """Request body for listing files."""

    depth: int
