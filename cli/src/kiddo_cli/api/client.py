"""Shared HTTP client for backend API calls."""

import os

import requests

DEFAULT_BACKEND_URL = "http://localhost:8000"
API_V1 = "/api/v1"
INTERNAL_PREFIX = "/internal"


def base_url() -> str:
    """Get the base URL from the environment variable BACKEND_URL."""
    backend_url = os.getenv("BACKEND_URL")
    if backend_url:
        print(f"BACKEND_URL: {os.getenv('BACKEND_URL')}")
    return os.getenv("BACKEND_URL", DEFAULT_BACKEND_URL).rstrip("/")


def headers(token: str | None = None) -> dict:
    """Get the headers for the API request."""
    kiddo_token = token
    env_token = os.getenv("KIDDO_TOKEN")
    if env_token:
        print(f"KIDDO_TOKEN: {env_token}")
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
    print(f"GET {API_V1}{path}")
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
    print(f"DELETE {API_V1}{path}")
    r = requests.delete(
        f"{base_url()}{API_V1}{path}",
        headers=headers(token),
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def patch(path: str, token: str | None = None, json: dict | None = None, timeout: int = 30) -> dict:
    """Patch the data from the API."""
    print(f"PATCH {API_V1}{path}")
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
    print(f"POST {API_V1}{path}")
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
    print(f"GET {INTERNAL_PREFIX}{path}")
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
    print(f"POST {INTERNAL_PREFIX}{path}")
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
