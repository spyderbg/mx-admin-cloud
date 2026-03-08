"""
TTS service: generate speech for a word in a given language via OpenAI Audio API.
Ported from existing_scripts/generate.py logic.
"""
from openai import OpenAI

from src.core.config import settings

VOICE = "alloy"
MODEL = "tts-1-hd"


def generate_audio(word: str, lang: str) -> bytes:
    """
    Generate TTS audio for one word in the given language.
    Returns MP3 bytes.
    """
    if not word or not word.strip():
        raise ValueError("Word must be non-empty for TTS.")

    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set; cannot call TTS.")

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.audio.speech.create(
        model=MODEL,
        voice=VOICE,
        input=word.strip(),
    )
    return response.content
