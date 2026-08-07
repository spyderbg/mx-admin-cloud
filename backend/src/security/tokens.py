"""JWT token creation utilities."""

from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import jwt
from src.core.config import settings


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Encode *data* as a signed JWT; defaults to ACCESS_TOKEN_EXPIRE_MINUTES if no delta given."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def create_internal_token(service_name: str, expires_delta: timedelta) -> str:
    """Issue a short-lived JWT for internal service-to-service calls.

    The token carries role: "internal" which is accepted by "require_internal".
    A new token is generated each time the server starts; there is no static shared secret.
    """
    return create_access_token({"sub": service_name, "role": "internal"}, expires_delta)
