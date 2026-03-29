"""
Content service: list categories, category details, create object, archive and restore category.
Orchestrates filesystem, CSVs, translation, TTS, and icon generation.
"""
import base64
import csv
import re
import shutil
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI

from src.core.config import settings
from src.core.content_naming import english_word_to_folder_name, english_word_to_image_id
from src.security.tokens import create_internal_token


def _content_root() -> Path:
    return settings.content_path


def _category_path(category: str) -> Path:
    return _content_root() / category


def _archive_root() -> Path:
    return settings.archive_path


def translate_word(
    text: str,
    target_lang_code: str | None = None,
    category: str | None = None,
    input_lang_code: str | None = None,
) -> str:
    """Translate text by calling the internal /internal/translate endpoint."""
    if not text or not text.strip():
        return text.strip()

    token = create_internal_token("translation-service", timedelta(seconds=30))
    response = httpx.post(
        f"{settings.API_BASE_URL}/internal/translate",
        json={
            "text": text.strip(),
            "target_lang_code": target_lang_code,
            "input_lang_code": input_lang_code,
            "category": category,
        },
        headers={"Authorization": f"Bearer {token}"},
        timeout=30.0,
    )
    response.raise_for_status()
    return response.json()["translated_text"] or text.strip()


def generate_audio(word: str, lang: str) -> bytes:
    """Generate TTS audio for one word using the first active provider."""
    if not word or not word.strip():
        raise ValueError("Word must be non-empty for TTS.")

    token = create_internal_token("tts-service", timedelta(seconds=30))
    response = httpx.post(
        f"{settings.API_BASE_URL}/internal/generators/generate-word",
        json={"word": word.strip(), "lang": lang},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
    response.raise_for_status()
    return response.content


def generate_icon(prompt: str) -> bytes:
    """Generate a simple, child-friendly icon from a text prompt."""
    if not prompt or not prompt.strip():
        raise ValueError("Prompt must be non-empty for icon generation.")

    if not settings.OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY is not set; cannot call Images API.")

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    response = client.images.generate(
        model="dall-e-2",
        prompt=prompt.strip(),
        size="256x256",
        response_format="b64_json",
        n=1,
    )
    b64_data = response.data[0].b64_json
    if not b64_data:
        raise ValueError("No image data returned from Images API.")
    return base64.b64decode(b64_data)


def _list_archived_entries() -> list[dict[str, Any]]:
    """Scan archive root for folders matching {base_name}_{unix_timestamp}; return list of dicts."""
    if not settings.ARCHIVE_DIR or not settings.ARCHIVE_DIR.strip():
        return []
    archive_root = _archive_root()
    if not archive_root.exists() or not archive_root.is_dir():
        return []
    entries: list[dict[str, Any]] = []
    for p in archive_root.iterdir():
        if p.is_dir() and not p.name.startswith("."):
            m = re.match(r"^(.+)_(\d+)$", p.name)
            if m:
                base_name, ts_str = m.group(1), m.group(2)
                entries.append({
                    "base_name": base_name,
                    "archive_folder": p.name,
                    "archived_at": int(ts_str),
                })
    return sorted(entries, key=lambda x: x["archived_at"], reverse=True)


def list_categories(
    include_archived: bool = False,
    archived_only: bool = False,
) -> dict[str, Any]:
    """List immediate subdirs of content root (exclude hidden). Optionally include or list only archived categories."""
    archived = _list_archived_entries() if (include_archived or archived_only) else []

    if archived_only:
        return {"categories": [], "archived": archived}

    root = _content_root()
    if not root.exists() or not root.is_dir():
        result: dict[str, Any] = {"categories": [], "error": f"Content root does not exist: {root}"}
        if include_archived:
            result["archived"] = archived
        return result
    names = [
        p.name
        for p in root.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    ]
    result = {"categories": sorted(names)}
    if include_archived:
        result["archived"] = archived
    return result


def category_details(category_name: str) -> dict[str, Any]:
    """Return object folders and optionally CSV-derived data for a category."""
    if not _is_safe_category_name(category_name):
        return {"error": "Invalid category name", "name": category_name}
    cat_path = _category_path(category_name)
    if not cat_path.exists() or not cat_path.is_dir():
        return {"error": "Category not found", "name": category_name}

    objects = [
        p.name
        for p in cat_path.iterdir()
        if p.is_dir() and not p.name.startswith(".")
    ]
    result: dict[str, Any] = {"name": category_name, "objects": sorted(objects)}

    for lang in settings.SUPPORTED_LANGUAGES:
        csv_path = cat_path / f"images_{lang}.csv"
        if csv_path.exists():
            try:
                with open(csv_path, newline="", encoding="utf-8") as f:
                    rows = list(csv.reader(f))
                    words = [r[0].strip() for r in rows if r and r[0].strip()]
                    result[f"images_{lang}"] = words
            except Exception:
                result[f"images_{lang}"] = []
    names_path = cat_path / "images_names.csv"
    if names_path.exists():
        try:
            with open(names_path, newline="", encoding="utf-8") as f:
                rows = list(csv.reader(f))
                result["images_names"] = [r[0].strip() for r in rows if r and r[0].strip()]
        except Exception:
            result["images_names"] = []
    return result


def _is_safe_category_name(name: str) -> bool:
    """Allow alphanumeric and underscore only."""
    return bool(name) and bool(re.match(r"^[a-zA-Z0-9_]+$", name))


def _category_name_to_icon_prompt(category_name: str) -> str:
    """Derive a short, child-friendly icon prompt from category name (underscores to spaces, title-case)."""
    label = category_name.replace("_", " ").strip()
    if not label:
        return category_name
    label = label.title()
    return f"Simple, friendly icon for children: {label}"


def create_category(category_name: str) -> dict[str, Any]:
    """
    Create a new category: folder under content root, voice files per language, and a category icon.
    Uses category_name for all labels (no display_name).
    """
    if not _is_safe_category_name(category_name):
        return {"error": "Invalid category name", "name": category_name}
    cat_path = _category_path(category_name)
    if cat_path.exists() and cat_path.is_dir():
        return {"error": f"Category already exists: {category_name}", "name": category_name}

    cat_path.mkdir(parents=True, exist_ok=True)

    # Voices: one file per language (category label in that language)
    for lang in settings.SUPPORTED_LANGUAGES:
        if lang == "en":
            label = category_name.replace("_", " ").strip() or category_name
        else:
            try:
                label = translate_word(category_name.replace("_", " ").strip() or category_name, lang, category_name)
            except Exception as e:
                return {"error": f"Translation failed for {lang}: {e}", "name": category_name}
        try:
            audio_bytes = generate_audio(label, lang)
            voice_path = cat_path / f"{category_name}_{lang}.mp3"
            with open(voice_path, "wb") as f:
                f.write(audio_bytes)
        except Exception as e:
            return {"error": f"TTS failed for {lang}: {e}", "name": category_name}

    # Icon: prompt derived from category_name
    try:
        prompt = _category_name_to_icon_prompt(category_name)
        icon_bytes = generate_icon(prompt)
        icon_path = cat_path / f"{category_name}.png"
        with open(icon_path, "wb") as f:
            f.write(icon_bytes)
    except Exception as e:
        return {"error": f"Icon generation failed: {e}", "name": category_name}

    return {"category": category_name, "message": f"Category '{category_name}' created."}


def _append_csv_line(file_path: Path, line: str) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "a", newline="", encoding="utf-8") as f:
        f.write(line.strip() + "\n")


def _first_available_image_index(object_folder: Path) -> int:
    """First index (1-based) such that no 01.png, 01.jpg, 02.png, ... exists yet."""
    for i in range(1, 1000):
        if not any(
            (object_folder / f"{i:02d}{ext}").exists()
            for ext in (".png", ".jpg", ".jpeg")
        ):
            return i
    return 1


def create_object(
    category_name: str,
    english_word: str,
    image_data: bytes,
    image_extension: str = ".png",
) -> dict[str, Any]:
    """
    Create object folder, save image, update CSVs, translate, generate voices.
    Returns result dict with category, object, folder, image_id or error.
    """
    if not _is_safe_category_name(category_name):
        return {"error": "Invalid category name", "category": category_name}
    if not english_word or not english_word.strip():
        return {"error": "Object word is required"}
    english_word = english_word.strip()

    folder_name = english_word_to_folder_name(english_word)
    image_id = english_word_to_image_id(english_word)
    if not folder_name:
        return {"error": "Could not derive folder name from object word"}

    root = _content_root()
    cat_path = root / category_name
    object_path = cat_path / folder_name

    if object_path.exists():
        return {"error": f"Object folder already exists: {folder_name}", "category": category_name}

    cat_path.mkdir(parents=True, exist_ok=True)
    object_path.mkdir(parents=True, exist_ok=True)

    idx = _first_available_image_index(object_path)
    ext = image_extension if image_extension.startswith(".") else f".{image_extension}"
    if ext.lower() not in (".png", ".jpg", ".jpeg"):
        ext = ".png"
    image_filename = f"{idx:02d}{ext}"
    image_path = object_path / image_filename
    with open(image_path, "wb") as f:
        f.write(image_data)

    _append_csv_line(cat_path / "images_en.csv", english_word)
    _append_csv_line(cat_path / "images_names.csv", image_id)

    words_by_lang: dict[str, str] = {"en": english_word}
    for lang in settings.SUPPORTED_LANGUAGES:
        if lang == "en":
            continue
        try:
            translated = translate_word(english_word, lang, category_name)
            words_by_lang[lang] = translated
            _append_csv_line(cat_path / f"images_{lang}.csv", translated)
        except Exception as e:
            return {
                "error": f"Translation failed for {lang}: {e}",
                "category": category_name,
                "object": english_word,
                "folder": folder_name,
                "image_id": image_id,
            }

    for lang in settings.SUPPORTED_LANGUAGES:
        word = words_by_lang.get(lang) or english_word
        try:
            audio_bytes = generate_audio(word, lang)
            voice_path = object_path / f"{folder_name}_{lang}_01.mp3"
            with open(voice_path, "wb") as f:
                f.write(audio_bytes)
        except Exception as e:
            return {
                "error": f"TTS failed for {lang}: {e}",
                "category": category_name,
                "object": english_word,
                "folder": folder_name,
                "image_id": image_id,
            }

    return {
        "category": category_name,
        "object": english_word,
        "folder": folder_name,
        "image_id": image_id,
    }


def archive_category(category_name: str) -> dict[str, Any]:
    """
    Archive a category: move its directory from content root to archive root
    with name {category_name}_{unix_timestamp}, then remove it from content.
    """
    if not _is_safe_category_name(category_name):
        return {
            "error": "Invalid category name",
            "name": category_name,
            "archived": False,
            "message": "Category name must be non-empty and contain only letters, numbers, and underscores.",
        }
    cat_path = _category_path(category_name)
    if not cat_path.exists() or not cat_path.is_dir():
        return {
            "error": "Category not found",
            "name": category_name,
            "archived": False,
            "message": f"Category '{category_name}' does not exist in content directory.",
        }
    archive_root = _archive_root()
    archive_root.mkdir(parents=True, exist_ok=True)
    unix_time = int(time.time())
    archive_folder_name = f"{category_name}_{unix_time}"
    dest = archive_root / archive_folder_name
    try:
        shutil.move(str(cat_path), str(dest))
        return {
            "archived": True,
            "name": category_name,
            "archive_folder": archive_folder_name,
            "destination": str(dest),
            "message": f"Category '{category_name}' has been archived to {archive_folder_name} and removed from content.",
        }
    except Exception as e:
        return {
            "error": str(e),
            "name": category_name,
            "archived": False,
            "message": f"Failed to archive category: {e}",
        }


def rename_category(old_name: str, new_name: str) -> dict[str, Any]:
    """
    Rename a category: rename the content folder and regenerate category voice files (no icon regeneration).
    """
    if not _is_safe_category_name(old_name):
        return {"error": "Invalid category name", "name": old_name}
    if not _is_safe_category_name(new_name):
        return {"error": "Invalid new category name", "name": new_name}
    old_path = _category_path(old_name)
    new_path = _category_path(new_name)
    if not old_path.exists() or not old_path.is_dir():
        return {"error": "Category not found", "name": old_name}
    if new_path.exists() and new_path.is_dir():
        return {"error": f"Target category already exists: {new_name}", "name": new_name}
    try:
        shutil.move(str(old_path), str(new_path))
    except Exception as e:
        return {"error": str(e), "name": old_name, "message": f"Failed to rename: {e}"}

    # Delete previously generated voice files (old category name)
    for lang in settings.SUPPORTED_LANGUAGES:
        old_voice = new_path / f"{old_name}_{lang}.mp3"
        if old_voice.exists() and old_voice.is_file():
            old_voice.unlink()
    # Also remove legacy category_{lang}.mp3 if present
    for lang in settings.SUPPORTED_LANGUAGES:
        legacy_voice = new_path / f"category_{lang}.mp3"
        if legacy_voice.exists() and legacy_voice.is_file():
            legacy_voice.unlink()

    # Regenerate category voice files for the new name (same as create_category)
    for lang in settings.SUPPORTED_LANGUAGES:
        if lang == "en":
            label = new_name.replace("_", " ").strip() or new_name
        else:
            try:
                label = translate_word(new_name.replace("_", " ").strip() or new_name, lang, new_name)
            except Exception as e:
                return {"error": f"Translation failed for {lang}: {e}", "name": new_name}
        try:
            audio_bytes = generate_audio(label, lang)
            voice_path = new_path / f"{new_name}_{lang}.mp3"
            with open(voice_path, "wb") as f:
                f.write(audio_bytes)
        except Exception as e:
            return {"error": f"TTS failed for {lang}: {e}", "name": new_name}

    # Rename icon file to match new category name (no regeneration)
    new_icon_path = new_path / f"{new_name}.png"
    for old_icon_name in (f"{old_name}.png", "category_icon.png"):
        old_icon_path = new_path / old_icon_name
        if old_icon_path.exists() and old_icon_path.is_file():
            if old_icon_path != new_icon_path:
                old_icon_path.rename(new_icon_path)
            break

    return {
        "category": new_name,
        "message": f"Category renamed from '{old_name}' to '{new_name}'.",
    }


def remove_category(category_name: str) -> dict[str, Any]:
    """
    Remove a category: if ARCHIVE_DIR is set, archive it (via archive_category); otherwise delete permanently.
    Does not change archive_category logic.
    """
    if not _is_safe_category_name(category_name):
        return {
            "error": "Invalid category name",
            "name": category_name,
            "message": "Category name must be non-empty and contain only letters, numbers, and underscores.",
        }
    cat_path = _category_path(category_name)
    if not cat_path.exists() or not cat_path.is_dir():
        return {
            "error": "Category not found",
            "name": category_name,
            "message": f"Category '{category_name}' does not exist in content directory.",
        }
    if settings.ARCHIVE_DIR and settings.ARCHIVE_DIR.strip():
        return archive_category(category_name)
    try:
        shutil.rmtree(cat_path)
        return {
            "removed": True,
            "name": category_name,
            "message": f"Category '{category_name}' was permanently deleted (ARCHIVE_DIR not set).",
        }
    except Exception as e:
        return {
            "error": str(e),
            "name": category_name,
            "removed": False,
            "message": f"Failed to delete category: {e}",
        }


def _archive_folders_for_base(archive_root: Path, base_name: str) -> list[tuple[str, int]]:
    """Return list of (archive_folder_name, unix_timestamp) for folders matching base_name_<digits>."""
    candidates: list[tuple[str, int]] = []
    for p in archive_root.iterdir():
        if p.is_dir() and not p.name.startswith("."):
            m = re.match(r"^" + re.escape(base_name) + r"_(\d+)$", p.name)
            if m:
                candidates.append((p.name, int(m.group(1))))
    return sorted(candidates, key=lambda x: x[1], reverse=True)


def restore_category(category_name: str) -> dict[str, Any]:
    """
    Restore a category from archive to content directory.
    - Empty name: do nothing.
    - Name only (e.g. 'foods'): restore the latest archive folder for that category.
    - Name with suffix (e.g. 'foods_1738456789'): restore that exact archive if it exists.
    Does not overwrite: if the category already exists in content, returns an error.
    """
    name = (category_name or "").strip()
    if not name:
        return {
            "restored": False,
            "message": "No category name provided.",
        }
    content_root = _content_root()
    archive_root = _archive_root()
    if not archive_root.exists() or not archive_root.is_dir():
        return {
            "restored": False,
            "error": "Archive directory does not exist",
            "message": "Archive directory is not available.",
        }

    # Determine base name and which archive folder to use
    if re.match(r"^.+_\d+$", name):
        base_name = re.sub(r"_\d+$", "", name)
        archive_folder = name
    else:
        base_name = name
        candidates = _archive_folders_for_base(archive_root, base_name)
        if not candidates:
            return {
                "restored": False,
                "error": f"No archive found for category '{base_name}'",
                "name": base_name,
                "message": f"No archived version of '{base_name}' found in archive.",
            }
        archive_folder = candidates[0][0]

    archive_path = archive_root / archive_folder
    if not archive_path.exists() or not archive_path.is_dir():
        return {
            "restored": False,
            "error": f"Archive folder not found: {archive_folder}",
            "name": base_name,
            "message": f"Archive folder '{archive_folder}' does not exist.",
        }

    content_dest = content_root / base_name
    if content_dest.exists() and content_dest.is_dir():
        return {
            "restored": False,
            "error": f"Category already exists in content directory: {base_name}",
            "name": base_name,
            "message": f"Category '{base_name}' already exists in content; not overwriting.",
        }

    try:
        shutil.copytree(archive_path, content_dest)
        return {
            "restored": True,
            "name": base_name,
            "from_archive": archive_folder,
            "message": f"Restored category '{base_name}' from archive '{archive_folder}'.",
        }
    except Exception as e:
        return {
            "restored": False,
            "error": str(e),
            "name": base_name,
            "message": f"Failed to restore category: {e}",
        }
