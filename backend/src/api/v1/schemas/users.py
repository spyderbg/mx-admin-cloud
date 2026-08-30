"""Pydantic request/response schemas for v1 user routes."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateUserRequest(BaseModel):
    """Request body for creating a new user."""

    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1)
    role: str = Field(default="viewer", pattern=r"^(admin|viewer)$")


class ChangePasswordRequest(BaseModel):
    """Request body for changing a user's password."""

    password: str = Field(min_length=1)


class ChangeRoleRequest(BaseModel):
    """Request body for changing a user's role."""

    role: str = Field(pattern=r"^(admin|viewer)$")


class UserOut(BaseModel):
    """Response body for a user."""

    id: int
    username: str
    role: str
