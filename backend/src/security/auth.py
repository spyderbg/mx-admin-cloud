"""FastAPI dependency for JWT bearer token verification."""

from fastapi import HTTPException, Depends, status
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from src.core.config import settings


UNAUTHORIZED_HEADERS = {"WWW-Authenticate": "Bearer"}
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login",
    auto_error=False,
)


def _raise_unauthorized(detail: str) -> None:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers=UNAUTHORIZED_HEADERS,
    )


def verify_token(token: str | None = Depends(oauth2_scheme)) -> dict:
    """Validate the Bearer JWT and return the decoded payload dict (username + role)."""
    if settings.AUTH_DISABLED:
        return {"username": "dev-user", "role": "admin"}

    if not token:
        _raise_unauthorized("Not authenticated")

    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: str | None = payload.get("sub")
        role: str | None = payload.get("role")
        if username is None or role is None:
            _raise_unauthorized("Could not validate credentials")
    except JWTError:
        _raise_unauthorized("Could not validate credentials")
    return {"username": username, "role": role}
