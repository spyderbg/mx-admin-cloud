"""
Translation service: translate English words to target languages via OpenAI.
Ported from existing_scripts/translate_csv.py logic.
"""
from openai import OpenAI

from src.core.config import settings

LANG_MAP = {
    "en": "English",
    "de": "German",
    "fr": "French",
    "it": "Italian",
    "es": "Spanish",
    "pl": "Polish",
    "uk": "Ukrainian",
    "ro": "Romanian",
    "nl": "Dutch",
    "bg": "Bulgarian",
}


def translate_word(text: str, target_lang_code: str, category: str | None = None) -> str:
    """
    Translate a single English word to the target language.
    Uses same prompt as original script: noun, singular, no articles.
    """
    if not text or not text.strip():
        return text.strip()

    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set; cannot call translation.")

    target_lang_full = LANG_MAP.get(target_lang_code, target_lang_code)
    system_content = (
        "You are a professional translator. "
        f"Translate the given English word into {target_lang_full}. "
        "ALWAYS treat the input as a NOUN (object name). "
        "Return ONLY the translated noun, singular form, "
        "no articles, no explanations."
    )
    if category:
        system_content += f" The category of the translated word is {category}."

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": system_content},
            {"role": "user", "content": text.strip()},
        ],
        temperature=0,
    )
    return response.choices[0].message.content.strip()
