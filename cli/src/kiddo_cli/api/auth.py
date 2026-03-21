"""Auth API: CLI login via Unix socket only."""
import os

import httpx

from .client import API_V1

DEFAULT_CLI_SOCKET = "/tmp/kiddo-backend.sock"


def cli_socket_path() -> str:
    """Get the CLI socket path from the environment variable KIDDO_CLI_SOCKET."""
    print(f"KIDDO_CLI_SOCKET: {os.getenv('KIDDO_CLI_SOCKET')}")
    return os.getenv("KIDDO_CLI_SOCKET", DEFAULT_CLI_SOCKET)


def login_via_uds(username: str, password: str) -> str:
    """Authenticate via CLI-only Unix socket. Returns access_token. Raises on failure."""
    socket_path = cli_socket_path()
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
