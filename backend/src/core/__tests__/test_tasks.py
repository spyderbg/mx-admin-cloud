"""Tests for the /status payload built by src.core.tasks."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import pytest

from src.core import tasks
from src.core.config import settings


def test_status_masks_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    # Any signed-in user, including an automatically registered viewer, can read /status.
    monkeypatch.setattr(settings, "DEFAULT_ADMIN_PASSWORD", "s3cret-admin")
    monkeypatch.setattr(settings, "SECRET_KEY", "s3cret-jwt")
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "s3cret-key")

    env = tasks.status()["env"]

    assert env["DEFAULT_ADMIN_PASSWORD"] == "***"
    assert env["SECRET_KEY"] == "***"
    assert env["GEMINI_API_KEY"] == "***"
    assert "s3cret" not in repr(env)
