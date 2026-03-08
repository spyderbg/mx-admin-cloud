"""Shared HTTP client for backend API calls."""
import os

import requests

DEFAULT_BACKEND_URL = "http://localhost:8000"
API_V1 = "/api/v1"


def base_url() -> str:
    return os.getenv("BACKEND_URL", DEFAULT_BACKEND_URL).rstrip("/")


def headers(token: str | None = None) -> dict:
    h = {"Accept": "application/json"}
    t = token or os.getenv("KIDDO_TOKEN")
    if t:
        h["Authorization"] = f"Bearer {t}"
    return h


def get(
    path: str,
    token: str | None = None,
    params: dict | None = None,
    timeout: int = 30,
) -> dict:
    r = requests.get(
        f"{base_url()}{API_V1}{path}",
        headers=headers(token),
        params=params,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def delete(path: str, token: str | None = None, timeout: int = 30) -> dict:
    r = requests.delete(
        f"{base_url()}{API_V1}{path}",
        headers=headers(token),
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def patch(path: str, token: str | None = None, json: dict | None = None, timeout: int = 30) -> dict:
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
