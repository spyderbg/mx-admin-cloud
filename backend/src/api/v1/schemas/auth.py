"""Pydantic request/response schemas for v1 authentication routes."""

from __future__ import annotations

from pydantic import BaseModel, Field


class AuthConfigResponse(BaseModel):
    """Sign-in options the login page offers."""

    # "Log in with Google" is allowed.
    google_enabled: bool
    # OAuth web client id the Google sign-in button is rendered for; None when not allowed.
    google_client_id: str | None = None


class GoogleLoginRequest(BaseModel):
    """Request body for logging in with Google."""

    # ID token returned to the browser by Google Identity Services.
    credential: str = Field(min_length=1)


class TokenResponse(BaseModel):
    """An access token for the web interface."""

    access_token: str
    token_type: str = "bearer"
