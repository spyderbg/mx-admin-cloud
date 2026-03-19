from fastapi import Depends, Header, HTTPException, Request, status
from jose import jwt, JWTError

from src.core.config import settings
from src.security.auth import verify_token


def is_unix_socket_request(request: Request) -> bool:
    """True when the request arrived over a Unix domain socket."""
    server = request.scope.get("server")
    if not isinstance(server, (list, tuple)) or len(server) < 1:
        return False
    first = server[0]
    return isinstance(first, str) and first.startswith("/")


def require_uds(request: Request) -> None:
    """Dependency that rejects requests not coming through the UDS."""
    if not is_unix_socket_request(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This endpoint is only available via the Unix domain socket.",
        )


def get_current_user(user: dict = Depends(verify_token)) -> dict:
    return user


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Dependency that ensures the caller has the admin role."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )
    return current_user


def verify_internal_access(
    request: Request,
    x_internal_token: str | None = Header(None),
) -> dict:
    """Dual-auth: accepts EITHER X-Internal-Token header OR JWT with admin role."""
    if x_internal_token and x_internal_token == settings.INTERNAL_SERVICE_TOKEN:
        return {"sub": "internal_service", "role": "internal_service"}

    if settings.AUTH_DISABLED:
        return {"username": "dev-user", "role": "admin"}

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            username = payload.get("sub")
            role = payload.get("role")
            if username and role == "admin":
                return {"username": username, "role": role}
        except JWTError:
            pass

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Internal access requires a valid service token or an admin JWT.",
    )
