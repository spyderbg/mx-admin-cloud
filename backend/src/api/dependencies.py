"""Dependencies for API routes."""

from fastapi import Depends, HTTPException, Request, status  # pylint: disable=import-error

from src.core.job_store import JobStore
from src.services.service_registry import ServiceRegistry
from src.core.utils import is_windows_platform
from src.security.auth import verify_token


def is_unix_socket_request(request: Request) -> bool:
    """True when the request arrived over a Unix domain socket."""
    if is_windows_platform():
        return False
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
    """Get the current user."""
    return user


def require_admin(current_user: dict = Depends(get_current_user)) -> dict:
    """Dependency that ensures the caller has the admin role."""
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )
    return current_user


def require_internal(current_user: dict = Depends(get_current_user)) -> dict:
    """Dependency for internal endpoints — allows role 'internal' or 'admin'."""
    if current_user.get("role") not in ("internal", "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Internal access required.",
        )
    return current_user


def get_registry(request: Request) -> ServiceRegistry:
    """Inject the shared ServiceRegistry from the app state."""
    return request.app.state.registry


def get_job_store(request: Request) -> JobStore:
    """Inject the shared JobStore from the app state."""
    return request.app.state.job_store


def get_internal_token(request: Request) -> str:
    """Inject the long-lived internal service JWT from the app state."""
    return request.app.state.internal_token
