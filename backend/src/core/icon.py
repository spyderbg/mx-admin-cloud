"""
Icon service: generate a simple icon image via OpenAI Images API.
Same structure as existing_scripts/generate.py and backend tts.py.
"""
import base64

from openai import OpenAI

from src.core.config import settings

MODEL = "dall-e-2"
SIZE = "256x256"


def generate_icon(prompt: str) -> bytes:
    """
    Generate a simple, child-friendly icon from a text prompt.
    Returns PNG image bytes.
    """
    if not prompt or not prompt.strip():
        raise ValueError("Prompt must be non-empty for icon generation.")

    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set; cannot call Images API.")

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.images.generate(
        model=MODEL,
        prompt=prompt.strip(),
        size=SIZE,
        response_format="b64_json",
        n=1,
    )
    b64_data = response.data[0].b64_json
    if not b64_data:
        raise ValueError("No image data returned from Images API.")
    return base64.b64decode(b64_data)
