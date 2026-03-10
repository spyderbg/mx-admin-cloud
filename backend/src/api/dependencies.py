from fastapi import Depends, HTTPException, Request, status

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
