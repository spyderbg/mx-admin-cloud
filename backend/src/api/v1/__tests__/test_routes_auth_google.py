"""Tests for "Log in with Google": the sign-in options, the login route and credential verification."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.auth import exceptions as google_exceptions
from jose import jwt

from src.api.v1 import routes_auth
from src.core.config import settings
from src.db.database import UsersDb
from src.security import google as google_auth
from src.security.google import (
    GoogleCredentialError,
    GoogleVerificationUnavailableError,
    verify_google_credential,
)

CLIENT_ID = "client-123.apps.googleusercontent.com"


@pytest.fixture
def users_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh user store holding the seeded admin plus a viewer whose username is an email."""
    monkeypatch.setattr(settings, "DB_PATH", str(tmp_path / "users.db"))
    UsersDb.init_db()
    UsersDb.create_user("Ana@Example.com", "viewer", UsersDb.hash_password("unused"))


@pytest.fixture(autouse=True)
def emitted(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Records the log events the auth routes emit instead of posting them."""
    events: list[tuple] = []
    monkeypatch.setattr(
        routes_auth, "emit_event_nowait",
        lambda source, event, level, **fields: events.append((source, event, fields)),
    )
    return events


@pytest.fixture
def client(users_db: None) -> TestClient:  # pylint: disable=unused-argument
    app = FastAPI()
    app.include_router(routes_auth.router, prefix=settings.API_V1_STR)
    return TestClient(app)


def _allow_google(monkeypatch: pytest.MonkeyPatch, enabled: bool = True, client_id: str | None = CLIENT_ID) -> None:
    monkeypatch.setattr(settings, "GOOGLE_AUTH_ENABLED", enabled)
    monkeypatch.setattr(settings, "GOOGLE_CLIENT_ID", client_id)


def _google_returns(monkeypatch: pytest.MonkeyPatch, outcome: str | Exception) -> list[tuple[str, str]]:
    """Stub credential verification with *outcome* (an email or an error); return the calls made."""
    calls: list[tuple[str, str]] = []

    def verify(credential: str, client_id: str) -> str:
        calls.append((credential, client_id))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(routes_auth, "verify_google_credential", verify)
    return calls


# ---------------------------------------------------------------------------
# GET /api/v1/auth/config
# ---------------------------------------------------------------------------

def test_auth_config_offers_google_when_enabled_with_a_client_id(
        client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_google(monkeypatch, client_id=f"  {CLIENT_ID} ")
    response = client.get("/api/v1/auth/config")
    assert response.status_code == 200
    assert response.json() == {"google_enabled": True, "google_client_id": CLIENT_ID}


@pytest.mark.parametrize("enabled,client_id", [(False, CLIENT_ID), (True, None), (True, "  ")])
def test_auth_config_hides_google_unless_enabled_and_configured(
        client: TestClient, monkeypatch: pytest.MonkeyPatch, enabled: bool, client_id: str | None,
) -> None:
    _allow_google(monkeypatch, enabled=enabled, client_id=client_id)
    response = client.get("/api/v1/auth/config")
    assert response.json() == {"google_enabled": False, "google_client_id": None}


def test_auth_config_needs_no_token(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_DISABLED", False)
    assert client.get("/api/v1/auth/config").status_code == 200


# ---------------------------------------------------------------------------
# POST /api/v1/login/google
# ---------------------------------------------------------------------------

def test_login_google_issues_a_token_for_the_matching_user(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_google(monkeypatch)
    calls = _google_returns(monkeypatch, "ana@example.com")

    response = client.post("/api/v1/login/google", json={"credential": "google-id-token"})

    assert response.status_code == 200
    assert calls == [("google-id-token", CLIENT_ID)]
    body = response.json()
    assert body["token_type"] == "bearer"
    claims = jwt.decode(body["access_token"], settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    # The email matches the username ignoring case; the token carries the stored username and role.
    assert (claims["sub"], claims["role"]) == ("Ana@Example.com", "viewer")


@pytest.mark.parametrize("enabled,client_id", [(False, CLIENT_ID), (True, None)])
def test_login_google_is_refused_unless_allowed(
        client: TestClient, monkeypatch: pytest.MonkeyPatch, enabled: bool, client_id: str | None,
) -> None:
    _allow_google(monkeypatch, enabled=enabled, client_id=client_id)
    calls = _google_returns(monkeypatch, "ana@example.com")

    response = client.post("/api/v1/login/google", json={"credential": "google-id-token"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Google sign-in is not enabled."
    assert not calls


def test_login_google_rejects_an_invalid_credential(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_google(monkeypatch)
    _google_returns(monkeypatch, GoogleCredentialError("Invalid Google sign-in."))

    response = client.post("/api/v1/login/google", json={"credential": "forged"})

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid Google sign-in."


def _token_claims(response) -> dict:
    return jwt.decode(response.json()["access_token"], settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


def test_login_google_registers_an_unknown_account_as_a_viewer(
        client: TestClient, monkeypatch: pytest.MonkeyPatch, emitted: list[tuple],
) -> None:
    _allow_google(monkeypatch)
    _google_returns(monkeypatch, "Stranger@Example.com")

    response = client.post("/api/v1/login/google", json={"credential": "google-id-token"})

    assert response.status_code == 200
    claims = _token_claims(response)
    assert (claims["sub"], claims["role"]) == ("stranger@example.com", "viewer")
    assert UsersDb.get_user_by_email("stranger@example.com") == {"username": "stranger@example.com", "role": "viewer"}
    assert emitted == [("auth", "google_user_registered", {"username": "stranger@example.com", "role": "viewer"})]


def test_login_google_registers_an_account_only_once(
        client: TestClient, monkeypatch: pytest.MonkeyPatch, emitted: list[tuple],
) -> None:
    _allow_google(monkeypatch)
    _google_returns(monkeypatch, "stranger@example.com")

    first = client.post("/api/v1/login/google", json={"credential": "google-id-token"})
    second = client.post("/api/v1/login/google", json={"credential": "google-id-token"})

    assert (first.status_code, second.status_code) == (200, 200)
    assert [row["username"] for row in UsersDb.list_users()].count("stranger@example.com") == 1
    assert len(emitted) == 1


def test_login_google_keeps_the_role_of_an_existing_user(
        client: TestClient, monkeypatch: pytest.MonkeyPatch, emitted: list[tuple],
) -> None:
    UsersDb.create_user("boss@example.com", "admin", UsersDb.hash_password("unused"))
    _allow_google(monkeypatch)
    _google_returns(monkeypatch, "BOSS@example.com")

    response = client.post("/api/v1/login/google", json={"credential": "google-id-token"})

    assert _token_claims(response)["role"] == "admin"
    assert not emitted


def test_a_registered_google_user_cannot_log_in_with_a_password(
        client: TestClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_google(monkeypatch)
    _google_returns(monkeypatch, "stranger@example.com")
    client.post("/api/v1/login/google", json={"credential": "google-id-token"})

    for password in ("", "stranger@example.com", settings.DEFAULT_ADMIN_PASSWORD):
        assert UsersDb.authenticate_user("stranger@example.com", password) is None


def test_get_or_register_email_user_tolerates_a_concurrent_registration(
        users_db: None, monkeypatch: pytest.MonkeyPatch,  # pylint: disable=unused-argument
) -> None:
    lookup = UsersDb.get_user_by_email
    seen: list[str] = []

    def racing_lookup(email: str):
        # The first lookup misses; another sign-in registers the email before this one inserts it.
        if not seen:
            seen.append(email)
            UsersDb.create_user("racer@example.com", "viewer", UsersDb.hash_password("unused"))
            return None
        return lookup(email)

    monkeypatch.setattr(UsersDb, "get_user_by_email", staticmethod(racing_lookup))

    user, registered = UsersDb.get_or_register_email_user("racer@example.com", "viewer")

    assert user == {"username": "racer@example.com", "role": "viewer"}
    assert registered is False
    conn = sqlite3.connect(settings.DB_PATH)
    try:
        count = conn.execute("SELECT COUNT(*) FROM users WHERE username = 'racer@example.com'").fetchone()[0]
    finally:
        conn.close()
    assert count == 1


def test_login_google_reports_google_being_unreachable(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_google(monkeypatch)
    _google_returns(monkeypatch, GoogleVerificationUnavailableError("Could not reach Google to verify the sign-in."))

    response = client.post("/api/v1/login/google", json={"credential": "google-id-token"})

    assert response.status_code == 503


def test_login_google_requires_a_credential(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    _allow_google(monkeypatch)
    assert client.post("/api/v1/login/google", json={"credential": ""}).status_code == 422
    assert client.post("/api/v1/login/google", json={}).status_code == 422


# ---------------------------------------------------------------------------
# verify_google_credential
# ---------------------------------------------------------------------------

def _google_library_returns(monkeypatch: pytest.MonkeyPatch, outcome: dict | Exception) -> list[dict]:
    calls: list[dict] = []

    def verify_oauth2_token(credential, request, audience=None, clock_skew_in_seconds=0):  # pylint: disable=unused-argument
        calls.append({"credential": credential, "audience": audience})
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(google_auth.id_token, "verify_oauth2_token", verify_oauth2_token)
    return calls


def test_verify_google_credential_returns_the_verified_email(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _google_library_returns(monkeypatch, {"email": "ana@example.com", "email_verified": True})

    assert verify_google_credential("token", CLIENT_ID) == "ana@example.com"
    # The token must have been issued for this application's client id.
    assert calls == [{"credential": "token", "audience": CLIENT_ID}]


@pytest.mark.parametrize("claims", [
    {"email": "ana@example.com", "email_verified": False},
    {"email": "ana@example.com"},
    {"email_verified": True},
])
def test_verify_google_credential_requires_a_verified_email(monkeypatch: pytest.MonkeyPatch, claims: dict) -> None:
    _google_library_returns(monkeypatch, claims)
    with pytest.raises(GoogleCredentialError, match="verified email"):
        verify_google_credential("token", CLIENT_ID)


@pytest.mark.parametrize("error", [
    ValueError("Token has wrong audience"),
    google_exceptions.GoogleAuthError("Wrong issuer"),
])
def test_verify_google_credential_rejects_invalid_tokens(monkeypatch: pytest.MonkeyPatch, error: Exception) -> None:
    _google_library_returns(monkeypatch, error)
    with pytest.raises(GoogleCredentialError, match="Invalid Google sign-in"):
        verify_google_credential("token", CLIENT_ID)


def test_verify_google_credential_when_google_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    _google_library_returns(monkeypatch, google_exceptions.TransportError("Could not fetch certificates"))
    with pytest.raises(GoogleVerificationUnavailableError):
        verify_google_credential("token", CLIENT_ID)
