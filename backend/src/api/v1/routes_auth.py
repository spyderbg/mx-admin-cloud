"""Authentication API routes."""

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status  # pylint: disable=import-error
from fastapi.security import OAuth2PasswordRequestForm  # pylint: disable=import-error

from src.api.dependencies import is_unix_socket_request
from src.core.config import settings
from src.db.database import UsersDb
from src.security.tokens import create_access_token

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
