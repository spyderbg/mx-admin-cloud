"""Auth API: CLI login via Unix socket only."""

import os

import httpx

from kiddo_cli.utils import is_windows_platform
from .client import API_V1, get_cli_socket_path  # pylint: disable=import-error

DEFAULT_API_BASE_URL = "http://localhost:8000"


def get_api_base_url() -> str:
    """Get the API base URL from the environment variable KIDDO_API_BASE_URL."""
    return os.getenv("KIDDO_API_BASE_URL", DEFAULT_API_BASE_URL)


def login(username: str, password: str) -> str:
    """Authenticate via HTTP request. Returns access_token. Raises on failure."""
    print("Authenticating via HTTP...")
    base_url = get_api_base_url()
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.post(
                f"{base_url}{API_V1}/login",
                data={"username": username, "password": password},
            )
            r.raise_for_status()
            return r.json()["access_token"]
    except httpx.HTTPError as e:
        raise RuntimeError(f"HTTP login failed: {e}") from e


def login_via_uds(username: str, password: str) -> str:
    """Authenticate via CLI-only Unix socket. Returns access_token. Raises on failure."""
    if is_windows_platform():
        raise RuntimeError("UDS authentication is not supported on Windows.")
    print("Authenticating via UDS...")
    socket_path = get_cli_socket_path()
    try:
        with httpx.Client(
                transport=httpx.HTTPTransport(uds=socket_path),
                timeout=5.0,
        ) as client:
            r = client.post(
                f"http://localhost{API_V1}/cli-login",
                data={"username": username, "password": password},
            )
            r.raise_for_status()
            return r.json()["access_token"]
    except httpx.HTTPError as e:
        raise RuntimeError(f"UDS login failed: {e}") from e
