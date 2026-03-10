from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from src.core.config import settings
from src.db.database import get_db, verify_password
from src.security.tokens import create_access_token

router = APIRouter()


def _authenticate_user(username: str, password: str) -> dict | None:
    """Look up the user in SQLite and verify the password. Returns the row or None."""
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT username, role, hashed_password FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None
    if not verify_password(password, row["hashed_password"]):
        return None
    return {"username": row["username"], "role": row["role"]}


@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = _authenticate_user(form_data.username, form_data.password)
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
    from src.api.dependencies import is_unix_socket_request

    if not is_unix_socket_request(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CLI login is only allowed via the Unix domain socket.",
        )

    user = _authenticate_user(form_data.username, form_data.password)
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
