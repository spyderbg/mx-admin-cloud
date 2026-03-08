from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from src.core.config import settings
from src.security.tokens import create_access_token

router = APIRouter()


def _is_unix_socket_request(request: Request) -> bool:
    """True if the request was received over a Unix domain socket."""
    server = request.scope.get("server")
    if not isinstance(server, (list, tuple)) or len(server) < 1:
        return False
    first = server[0]
    return isinstance(first, str) and first.startswith("/")


@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    # For a minimal backend, using hardcoded credentials
    if form_data.username == "admin" and form_data.password == "admin":
        access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        access_token = create_access_token(
            data={"sub": form_data.username}, expires_delta=access_token_expires
        )
        return {"access_token": access_token, "token_type": "bearer"}

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/cli-login")
def cli_login(request: Request, form_data: OAuth2PasswordRequestForm = Depends()):
    """Login for CLI only: allowed only over the Unix domain socket. Returns a short-lived JWT."""
    if not _is_unix_socket_request(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CLI login is only allowed via the Unix domain socket.",
        )
    if form_data.username != "admin" or form_data.password != "admin":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(seconds=settings.CLI_TOKEN_EXPIRE_SECONDS)
    access_token = create_access_token(
        data={"sub": form_data.username}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer"}
