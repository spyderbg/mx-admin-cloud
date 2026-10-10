"""Authentication API routes."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status  # pylint: disable=import-error
from fastapi.security import OAuth2PasswordRequestForm  # pylint: disable=import-error

from src.api.dependencies import is_unix_socket_request
from src.api.v1.schemas.auth import AuthConfigResponse, GoogleLoginRequest, TokenResponse
from src.core.config import settings
from src.core.log_client import emit_event_nowait
from src.db.database import UsersDb
from src.security.google import (
    GoogleCredentialError,
    GoogleVerificationUnavailableError,
    verify_google_credential,
)
from src.security.tokens import create_access_token
from src.services.logging.models import LogLevel

LOG_SOURCE = "auth"
# Role of the users that logging in with Google registers automatically.
GOOGLE_REGISTERED_ROLE = "viewer"

router = APIRouter()


@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """Login for web interface: returns a long-lived JWT."""
    user = UsersDb.authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        data={"sub": user["username"], "role": user["role"]},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/auth/config", response_model=AuthConfigResponse)
def auth_config() -> AuthConfigResponse:
    """Sign-in options for the login page; public, since it is read before logging in."""
    allowed = settings.google_auth_allowed
    return AuthConfigResponse(
        google_enabled=allowed,
        google_client_id=(settings.GOOGLE_CLIENT_ID or "").strip() if allowed else None,
    )


@router.post("/login/google", response_model=TokenResponse)
def login_google(body: GoogleLoginRequest) -> TokenResponse:
    """Login for web interface with a Google account: returns a long-lived JWT.

    The Google account's verified email is matched with a username (ignoring case), and the
    token carries that user's role. An email with no user yet is registered as a viewer.
    """
    if not settings.google_auth_allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Google sign-in is not enabled.")
    try:
        email = verify_google_credential(body.credential, (settings.GOOGLE_CLIENT_ID or "").strip())
    except GoogleVerificationUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    except GoogleCredentialError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user, registered = UsersDb.get_or_register_email_user(email, GOOGLE_REGISTERED_ROLE)
    if registered:
        emit_event_nowait(
            LOG_SOURCE, "google_user_registered", LogLevel.INFO, username=user["username"], role=user["role"],
        )

    access_token = create_access_token(
        data={"sub": user["username"], "role": user["role"]},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return TokenResponse(access_token=access_token)


@router.post("/cli-login")
def cli_login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    """Login for CLI only: allowed only over the Unix domain socket. Returns a short-lived JWT."""
    if not is_unix_socket_request(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CLI login is only allowed via the Unix domain socket.",
        )

    user = UsersDb.authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        data={"sub": user["username"], "role": user["role"]},
        expires_delta=timedelta(seconds=settings.CLI_TOKEN_EXPIRE_SECONDS),
    )
    return {"access_token": access_token, "token_type": "bearer"}
