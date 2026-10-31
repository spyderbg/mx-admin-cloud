"""Unit tests for the runtime environment helpers in kiddo_cli.utils."""

import pytest

from kiddo_cli import utils


@pytest.mark.parametrize(
    ("platform", "windows", "linux", "darwin"),
    [
        ("win32", True, False, False),
        ("linux", False, True, False),
        ("linux2", False, True, False),
        ("darwin", False, False, True),
        ("freebsd13", False, False, False),
    ],
)
def test_platform_detection(monkeypatch, platform, windows, linux, darwin):
    monkeypatch.setattr(utils.sys, "platform", platform)
    assert utils.is_windows_platform() is windows
    assert utils.is_linux_platform() is linux
    assert utils.is_darwin_platform() is darwin


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, False),
        ("true", True),
        ("TRUE", True),
        ("false", False),
        ("", False),
    ],
)
def test_debug_and_production_mode(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("KIDDO_DEBUG", raising=False)
    else:
        monkeypatch.setenv("KIDDO_DEBUG", value)
    assert utils.is_debug_mode() is expected
    assert utils.is_production() is (not expected)
