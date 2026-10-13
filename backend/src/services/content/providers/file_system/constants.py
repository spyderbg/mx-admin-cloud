"""File names and shared constants for the file_system content provider."""

from __future__ import annotations

from enum import Enum

_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg")

# --- File name constants ---
_CATEGORIES_IDS = "categories_ids.csv"
_CATEGORIES_LANG_CSV = "categories_{}.csv"
_DESCRIPTIONS_LANG_CSV = "descriptions_{}.csv"
_ITEMS_IDS = "items_ids.csv"
_ITEMS_LANG_CSV = "items_{}.csv"
_CATEGORY_VOICE_MP3 = "{}_{}.mp3"

# Written into a category folder when it is archived, so restore_category() can rebuild
# the root CSV registry rows that archive_category() deletes.
_ARCHIVE_MANIFEST = "_category.json"

_METADATA_COLUMNS = ("creation_method", "provider", "parameters", "hash")


class CreationMethod(str, Enum):
    """How a voice or item image came to be, as recorded in its metadata sidecar."""

    UPLOADED = "uploaded"
    GENERATED = "generated"
    OPTIMIZED = "optimized"
    UNKNOWN = "unknown"
