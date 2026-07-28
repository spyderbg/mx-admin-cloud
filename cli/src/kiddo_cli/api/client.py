"""Shared HTTP client for backend API calls."""

import os
import stat

import httpx
import requests

from kiddo_cli.utils import is_windows_platform

DEFAULT_BACKEND_URL = "http://localhost:8000"
DEFAULT_CLI_SOCKET = "/tmp/kiddo-backend.sock"
API_V1 = "/api/v1"
INTERNAL_PREFIX = "/internal"


def get_cli_socket_path() -> str:
    """Return the CLI Unix socket path (env ``KIDDO_CLI_SOCKET`` or default)."""
    return os.getenv("KIDDO_CLI_SOCKET", DEFAULT_CLI_SOCKET)


def _user_mgmt_use_uds() -> bool:
    """True when user-management calls must go over the CLI socket (Linux/macOS)."""
    if is_windows_platform():
        return False
    sock = get_cli_socket_path()
    try:
        mode = os.stat(sock).st_mode
    except FileNotFoundError:
        return False
    return stat.S_ISSOCK(mode)


def _uds_v1_request(
        method: str,
        path: str,
        token: str | None,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        timeout: float = 30.0,
) -> httpx.Response:
    """Issue an HTTP request to API v1 over the Unix domain socket."""
    _vprint(f"{method} (UDS) {API_V1}{path}")
    url = f"http://localhost{API_V1}{path}"
    transport = httpx.HTTPTransport(uds=get_cli_socket_path())
    with httpx.Client(transport=transport, timeout=timeout) as client:
        return client.request(
            method,
            url,
            headers=headers(token),
            params=params,
            json=json_body,
        )


def _raise_if_user_mgmt_needs_uds(response: requests.Response) -> None:
    """Raise a clear error when TCP hits UDS-only user routes."""
    if response.status_code != 403:
        return
    try:
        detail = response.json().get("detail", "")
    except (ValueError, TypeError, AttributeError):
        detail = ""
    if "Unix domain socket" not in str(detail):
        return
    sock = get_cli_socket_path()
    raise RuntimeError(
        "User management only accepts requests via the CLI Unix socket on this platform. "
        f"Start the backend from the backend directory with ./uvicorn_start.sh so {sock} exists, "
        "or remove a stale socket file if the listener is not running."
    ) from None


def _json_or_empty(response: httpx.Response) -> dict:
    if not response.content:
        return {}
    return response.json()


def user_mgmt_get(
        path: str,
        token: str | None = None,
        params: dict | None = None,
        timeout: int = 30,
) -> dict:
    """GET /api/v1/users* — uses the CLI socket on Linux/macOS when available."""
    if _user_mgmt_use_uds():
        r = _uds_v1_request("GET", path, token, params=params, timeout=float(timeout))
        r.raise_for_status()
        return r.json()
    _vprint(f"GET {API_V1}{path}")
    r = requests.get(
        f"{base_url()}{API_V1}{path}",
        headers=headers(token),
        params=params,
        timeout=timeout,
    )
    _raise_if_user_mgmt_needs_uds(r)
    r.raise_for_status()
    return r.json()


def user_mgmt_post(
        path: str,
        token: str | None = None,
        params: dict | None = None,
        data: dict | None = None,
        json: dict | None = None,
        files: dict | None = None,
        timeout: int = 30,
) -> dict:
    """POST /api/v1/users* — uses the CLI socket on Linux/macOS when available."""
    if _user_mgmt_use_uds():
        if files is not None or data is not None:
            raise RuntimeError("Multipart form user-mgmt calls over UDS are not supported.")
        r = _uds_v1_request(
            "POST",
            path,
            token,
            params=params,
            json_body=json,
            timeout=float(timeout),
        )
        r.raise_for_status()
        return r.json()
    _vprint(f"POST {API_V1}{path}")
    r = requests.post(
        f"{base_url()}{API_V1}{path}",
        headers=headers(token),
        params=params,
        data=data,
        json=json,
        files=files,
        timeout=timeout,
    )
    _raise_if_user_mgmt_needs_uds(r)
    r.raise_for_status()
    return r.json()


def user_mgmt_delete(path: str, token: str | None = None, timeout: int = 30) -> None:
    """DELETE /api/v1/users* — uses the CLI socket on Linux/macOS when available."""
    if _user_mgmt_use_uds():
        r = _uds_v1_request("DELETE", path, token, timeout=float(timeout))
        r.raise_for_status()
        return
    _vprint(f"DELETE {API_V1}{path}")
    r = requests.delete(
        f"{base_url()}{API_V1}{path}",
        headers=headers(token),
        timeout=timeout,
    )
    _raise_if_user_mgmt_needs_uds(r)
    r.raise_for_status()


def user_mgmt_patch(
        path: str,
        token: str | None = None,
        json: dict | None = None,
        timeout: int = 30,
) -> dict:
    """PATCH /api/v1/users* — uses the CLI socket on Linux/macOS when available."""
    if _user_mgmt_use_uds():
        r = _uds_v1_request("PATCH", path, token, json_body=json, timeout=float(timeout))
        r.raise_for_status()
        return _json_or_empty(r)
    _vprint(f"PATCH {API_V1}{path}")
    r = requests.patch(
        f"{base_url()}{API_V1}{path}",
        headers=headers(token),
        json=json,
        timeout=timeout,
    )
    _raise_if_user_mgmt_needs_uds(r)
    r.raise_for_status()
    if not r.content:
        return {}
    return r.json()


def _is_verbose() -> bool:
    """Return whether verbose output is enabled."""
    return os.getenv("KIDDO_CLI_VERBOSE", "0").lower() in {"1", "true", "yes", "on"}


def _vprint(message: str) -> None:
    """Print message only when verbose mode is enabled."""
    if _is_verbose():
        print(message)


def base_url() -> str:
    """Get the base URL from the environment variable BACKEND_URL."""
    backend_url = os.getenv("BACKEND_URL")
    if backend_url:
        _vprint(f"BACKEND_URL: {os.getenv('BACKEND_URL')}")
    return os.getenv("BACKEND_URL", DEFAULT_BACKEND_URL).rstrip("/")


def headers(token: str | None = None) -> dict:
    """Get the headers for the API request."""
    kiddo_token = token
    env_token = os.getenv("KIDDO_TOKEN")
    if env_token:
        _vprint(f"KIDDO_TOKEN: {env_token}")
        kiddo_token = env_token

    h = {"Accept": "application/json"}
    if kiddo_token:
        h["Authorization"] = f"Bearer {kiddo_token}"
    return h


def get(
        path: str,
        token: str | None = None,
        params: dict | None = None,
        timeout: int = 30,
) -> dict:
    """Get the data from the API."""
    _vprint(f"GET {API_V1}{path}")
    r = requests.get(
        f"{base_url()}{API_V1}{path}",
        headers=headers(token),
        params=params,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def delete(path: str, token: str | None = None, timeout: int = 30) -> dict:
    """Delete the data from the API."""
    _vprint(f"DELETE {API_V1}{path}")
    r = requests.delete(
        f"{base_url()}{API_V1}{path}",
        headers=headers(token),
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def patch(path: str, token: str | None = None, json: dict | None = None, timeout: int = 30) -> dict:
    """Patch the data from the API."""
    _vprint(f"PATCH {API_V1}{path}")
    r = requests.patch(
        f"{base_url()}{API_V1}{path}",
        headers=headers(token),
        json=json,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def post(
        path: str,
        token: str | None = None,
        params: dict | None = None,
        data: dict | None = None,
        json: dict | None = None,
        files: dict | None = None,
        timeout: int = 30,
) -> dict:
    """Post the data to the API."""
    _vprint(f"POST {API_V1}{path}")
    r = requests.post(
        f"{base_url()}{API_V1}{path}",
        headers=headers(token),
        params=params,
        data=data,
        json=json,
        files=files,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def internal_get(
        path: str,
        token: str | None = None,
        params: dict | None = None,
        timeout: int = 30,
) -> dict:
    """Get the data from the internal API."""
    _vprint(f"GET {INTERNAL_PREFIX}{path}")
    r = requests.get(
        f"{base_url()}{INTERNAL_PREFIX}{path}",
        headers=headers(token),
        params=params,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def internal_post(
        path: str,
        token: str | None = None,
        json: dict | None = None,
        data: dict | None = None,
        files: dict | None = None,
        timeout: int = 300,
) -> dict:
    """Post the data to the internal API."""
    _vprint(f"POST {INTERNAL_PREFIX}{path}")
    r = requests.post(
        f"{base_url()}{INTERNAL_PREFIX}{path}",
        headers=headers(token),
        json=json,
        data=data,
        files=files,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def internal_post_bytes(
        path: str,
        token: str | None = None,
        json: dict | None = None,
        data: dict | None = None,
        files: dict | None = None,
        timeout: int = 300,
) -> bytes:
    """Post the data to the internal API and return the raw response bytes."""
    _vprint(f"POST {INTERNAL_PREFIX}{path}")
    r = requests.post(
        f"{base_url()}{INTERNAL_PREFIX}{path}",
        headers=headers(token),
        json=json,
        data=data,
        files=files,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.content


def internal_patch(
        path: str,
        token: str | None = None,
        json: dict | None = None,
        timeout: int = 30,
) -> dict:
    """Patch the data on the internal API."""
    _vprint(f"PATCH {INTERNAL_PREFIX}{path}")
    r = requests.patch(
        f"{base_url()}{INTERNAL_PREFIX}{path}",
        headers=headers(token),
        json=json,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def internal_delete(path: str, token: str | None = None, timeout: int = 30) -> dict:
    """Delete the data from the internal API."""
    _vprint(f"DELETE {INTERNAL_PREFIX}{path}")
    r = requests.delete(
        f"{base_url()}{INTERNAL_PREFIX}{path}",
        headers=headers(token),
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()
