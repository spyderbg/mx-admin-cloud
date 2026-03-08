"""
Naming helpers for content: folder names and image IDs.
Ported from names_export logic (PascalCase + I_ prefix for image id).
"""
import re


def _to_pascal(s: str) -> str:
    """Split on non-alphanumeric and convert to PascalCase."""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", s) if p]
    return "".join(p[:1].upper() + p[1:].lower() if p else "" for p in parts)


def english_word_to_folder_name(word: str) -> str:
    """
    Convert English object word to folder name (snake_case).
    e.g. "apple pie" -> "apple_pie"
    """
    if not word or not word.strip():
        return ""
    normalized = word.strip().replace(" ", "_")
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized.lower()


def english_word_to_image_id(word: str) -> str:
    """
    Convert English object word to image ID (I_ + PascalCase).
    e.g. "apple pie" -> "I_ApplePie"
    """
    if not word or not word.strip():
        return ""
    return "I_" + _to_pascal(word.strip())
