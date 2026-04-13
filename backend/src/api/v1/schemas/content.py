"""Pydantic request/response schemas for v1 content routes."""

from __future__ import annotations

from pydantic import BaseModel


class CreateCategoryBody(BaseModel):
    """Request body for creating a new category."""

    name: str


class RenameCategoryBody(BaseModel):
    """Request body for renaming a category."""

    name: str


class RestoreCategoryBody(BaseModel):
    """Request body for restoring a category."""

    name: str
