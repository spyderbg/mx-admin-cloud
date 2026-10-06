"""Unit tests for the pyttsx3 provider and its shipped configuration."""

# pylint: disable=missing-function-docstring,too-few-public-methods,import-outside-toplevel

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from src.services.text_to_speech.providers.pyttsx3_tts import Pyttsx3TTSProvider

_CONFIG_PATH = Path(__file__).resolve().parents[4] / "config" / "text_to_speech" / "pyttsx3.json"

_VOICES = [
    SimpleNamespace(id="english", name="English (Great Britain)", languages=[b"\x05en-gb"]),
    SimpleNamespace(id="bulgarian", name="Bulgarian", languages=[b"\x02bg"]),
]


def test_shipped_configuration_is_disabled() -> None:
    document = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))["service_provider"]
    assert document["provider"] == "pyttsx3"
    assert document["enabled"] is False


def test_pick_voice_matches_actor_by_id_or_name() -> None:
    assert Pyttsx3TTSProvider._pick_voice(_VOICES, "Bulgarian", "en") == "bulgarian"
    assert Pyttsx3TTSProvider._pick_voice(_VOICES, "great britain", "bg") == "english"


def test_pick_voice_falls_back_to_the_language() -> None:
    assert Pyttsx3TTSProvider._pick_voice(_VOICES, "", "bg") == "bulgarian"
    assert Pyttsx3TTSProvider._pick_voice(_VOICES, "", "en") == "english"
    assert Pyttsx3TTSProvider._pick_voice(_VOICES, "", "fr") is None
