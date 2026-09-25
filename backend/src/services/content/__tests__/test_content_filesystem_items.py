"""Unit tests for FileSystemContentProvider — item creation, item names, voices and voice metadata, and item removal."""

# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

import base64
import csv
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from src.services.content.models import (
    CategoryConflictError,
    CategoryNotFoundError,
    ContentOperationError,
)
from src.core.localization import LocalizedString
from src.services.content.providers.file_system import (
    FileSystemContentProvider,
    VoiceCreationMethod,
    _extension_for_content_type,
)
from src.services.content.__tests__.content_fs_helpers import (
    patch_fs_settings,
    make_fs_provider,
    write_category_registry,
    write_item_registry,
)


# ---------------------------------------------------------------------------
# create_item
# ---------------------------------------------------------------------------

def _prepare_category(tmp_path: Path) -> tuple[Path, Path]:
    """Create a registered, empty 'foods' category and return (content_root, category_path)."""
    content_root = tmp_path / "content"
    write_category_registry(content_root, [("C_Foods", "foods")])
    category_path = content_root / "foods"
    category_path.mkdir(parents=True, exist_ok=True)
    return content_root, category_path


def _fake_translate(
        text: str,
        target_lang_code: str | None = None,
        _category: str | None = None,
        _input_lang_code: str | None = None,
        *,
        require_success: bool = False,  # pylint: disable=unused-argument
) -> str:
    return f"{text}-{target_lang_code}"


def _csv_rows(path: Path) -> list[str]:
    return FileSystemContentProvider._read_csv_column(path)


def _voice_metadata(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8", newline="") as file_handle:
        return next(csv.DictReader(file_handle))


def _without_date(parameters: dict[str, object]) -> dict[str, object]:
    """Assert a 'date' stamp is present and ISO-formatted, then strip it for comparison."""
    assert "date" in parameters
    datetime.fromisoformat(str(parameters["date"]))
    return {k: v for k, v in parameters.items() if k != "date"}


def _voice_metadata_normalized(path: Path) -> dict[str, object]:
    """Like _voice_metadata(), but with 'parameters' parsed and its 'date' stamp
    replaced by a presence check, so callers can assert the rest exactly."""
    row = _voice_metadata(path)
    parameters = json.loads(row["parameters"])
    return {**row, "parameters": _without_date(parameters)}


def test_create_item_success_with_image(tmp_path: Path) -> None:
    content_root, category_path = _prepare_category(tmp_path)
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", side_effect=_fake_translate),
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"audio"),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.create_item("C_Foods", "  apple ", b"\x89PNG", ".png")

    assert result.item_id == "I_Apple"
    assert result.image_id == "I_Apple"
    assert result.folder == "apple"
    assert result.voices_generated == ["en", "fr"]
    assert not result.voice_warnings
    assert result.message == "Item 'apple' created."
    item_path = category_path / "apple"
    assert (item_path / "apple.png").read_bytes() == b"\x89PNG"
    assert (item_path / "apple_01.png").read_bytes() == b"\x89PNG"
    assert (item_path / "apple_en.mp3").exists()
    assert (item_path / "apple_fr.mp3").exists()
    assert _voice_metadata_normalized(item_path / "apple_en.csv") == {
        "creation_method": "generated",
        "provider": "unknown",
        "parameters": {"_text": "apple"},
    }
    assert _csv_rows(category_path / "items_ids.csv") == ["I_Apple"]
    assert _csv_rows(category_path / "items_en.csv") == ["apple"]
    assert _csv_rows(category_path / "items_fr.csv") == ["apple-fr"]


def test_create_item_success_without_image(tmp_path: Path) -> None:
    content_root, category_path = _prepare_category(tmp_path)
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", side_effect=_fake_translate),
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"audio"),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.create_item("C_Foods", "apple")
        details = provider.category_details("C_Foods")

    item_path = category_path / "apple"
    assert item_path.is_dir()
    assert not [f for f in item_path.iterdir() if f.suffix in (".png", ".jpg", ".jpeg")]
    assert result.voices_generated == ["en", "fr"]
    item = next(entry for entry in details.items if entry.id == "I_Apple")
    assert item.images_count == 0
    assert item.thumbnail_uri == ""
    assert item.name.to_dict()["fr"] == "apple-fr"


def test_create_item_translation_failure_leaves_no_trace(tmp_path: Path) -> None:
    content_root, category_path = _prepare_category(tmp_path)
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", side_effect=RuntimeError("translator down")),
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"audio") as audio_mock,
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        with pytest.raises(ContentOperationError, match="Translation failed for fr: translator down"):
            provider.create_item("C_Foods", "apple", b"\x89PNG")

    assert not (category_path / "apple").exists()
    assert not (category_path / "items_ids.csv").exists()
    assert not (category_path / "items_en.csv").exists()
    assert not (category_path / "items_fr.csv").exists()
    assert audio_mock.call_count == 0


def test_translate_word_strict_rejects_unsuccessful_response() -> None:
    class _Response:  # pylint: disable=too-few-public-methods
        is_error = False

        @staticmethod
        def json() -> dict:
            return {"success": False, "lang_text": {"lang_code": "en", "text": "apple"}}

    with (
        patch("src.services.content.providers.file_system.internal_api.httpx.post", return_value=_Response()),
        patch("src.services.content.providers.file_system.internal_api.create_internal_token", return_value="token"),
        patch_fs_settings() as mock_settings,
    ):
        mock_settings.API_BASE_URL = "http://test"
        # Lenient mode (used by create_category) keeps echoing the source text.
        assert FileSystemContentProvider._translate_word("apple", "fr") == "apple"
        with pytest.raises(RuntimeError, match="No active translator"):
            FileSystemContentProvider._translate_word("apple", "fr", require_success=True)


def test_create_item_tts_failure_still_creates_item(tmp_path: Path) -> None:
    content_root, category_path = _prepare_category(tmp_path)
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", side_effect=_fake_translate),
        patch.object(FileSystemContentProvider, "_generate_audio", side_effect=RuntimeError("boom")),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.create_item("C_Foods", "apple", b"\x89PNG")

    assert result.item_id == "I_Apple"
    assert not result.voices_generated
    assert result.voice_warnings == [
        "Voice generation failed for en: boom",
        "Voice generation failed for fr: boom",
    ]
    assert "Voices were not generated" in result.message
    assert (category_path / "apple" / "apple.png").exists()
    assert (category_path / "apple" / "apple_01.png").exists()
    assert not list((category_path / "apple").glob("*.mp3"))
    assert _csv_rows(category_path / "items_ids.csv") == ["I_Apple"]


def test_create_item_partial_tts_failure(tmp_path: Path) -> None:
    content_root, category_path = _prepare_category(tmp_path)

    def fake_audio(lang: str) -> bytes:
        if lang == "fr":
            raise RuntimeError("fr voice unavailable")
        return b"audio"

    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", side_effect=_fake_translate),
        patch.object(FileSystemContentProvider, "_generate_audio", side_effect=fake_audio),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.create_item("C_Foods", "apple")

    assert result.voices_generated == ["en"]
    assert result.voice_warnings == ["Voice generation failed for fr: fr voice unavailable"]
    assert (category_path / "apple" / "apple_en.mp3").exists()
    assert not (category_path / "apple" / "apple_fr.mp3").exists()


def test_create_item_stops_after_no_active_tts_providers(tmp_path: Path) -> None:
    content_root, _ = _prepare_category(tmp_path)
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", side_effect=_fake_translate),
        patch.object(
            FileSystemContentProvider,
            "_generate_audio",
            side_effect=RuntimeError("No active text to speech services configured."),
        ) as audio_mock,
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.create_item("C_Foods", "apple")

    assert audio_mock.call_count == 1
    assert not result.voices_generated
    assert len(result.voice_warnings) == 1


def test_create_item_generate_voices_false_skips_tts(tmp_path: Path) -> None:
    content_root, category_path = _prepare_category(tmp_path)
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", side_effect=_fake_translate),
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"audio") as audio_mock,
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.create_item("C_Foods", "apple", generate_voices=False)

    assert audio_mock.call_count == 0
    assert not result.voices_generated
    assert not result.voice_warnings
    assert result.message == "Item 'apple' created."
    assert not list((category_path / "apple").glob("*.mp3"))


def test_create_item_conflict_existing_folder(tmp_path: Path) -> None:
    content_root, category_path = _prepare_category(tmp_path)
    (category_path / "apple").mkdir()
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", side_effect=_fake_translate) as translate_mock,
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        with pytest.raises(CategoryConflictError):
            provider.create_item("C_Foods", "apple")

    assert translate_mock.call_count == 0
    assert not (category_path / "items_ids.csv").exists()


def test_create_item_conflict_existing_id_row(tmp_path: Path) -> None:
    content_root, category_path = _prepare_category(tmp_path)
    (category_path / "items_ids.csv").write_text("I_Apple\n", encoding="utf-8")
    (category_path / "items_en.csv").write_text("apple\n", encoding="utf-8")
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", side_effect=_fake_translate),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        with pytest.raises(CategoryConflictError):
            provider.create_item("C_Foods", "Apple")

    assert _csv_rows(category_path / "items_ids.csv") == ["I_Apple"]
    assert not (category_path / "items_fr.csv").exists()


def test_create_item_rolls_back_on_disk_failure(tmp_path: Path) -> None:
    content_root, category_path = _prepare_category(tmp_path)
    (category_path / "items_ids.csv").write_text("I_Pear\n", encoding="utf-8")
    (category_path / "items_en.csv").write_text("pear\n", encoding="utf-8")
    # A directory where items_fr.csv should be makes that append fail after ids/en were written.
    (category_path / "items_fr.csv").mkdir()
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", side_effect=_fake_translate),
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"audio") as audio_mock,
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        with pytest.raises(ContentOperationError, match="Failed to write item files"):
            provider.create_item("C_Foods", "apple", b"\x89PNG")

    assert not (category_path / "apple").exists()
    assert _csv_rows(category_path / "items_ids.csv") == ["I_Pear"]
    assert _csv_rows(category_path / "items_en.csv") == ["pear"]
    assert audio_mock.call_count == 0


def test_create_item_unknown_category(tmp_path: Path) -> None:
    content_root, _ = _prepare_category(tmp_path)
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        with pytest.raises(CategoryNotFoundError):
            provider.create_item("C_Missing", "apple")


def test_create_item_missing_category_folder(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    write_category_registry(content_root, [("C_Foods", "foods")])  # registered, but no folder on disk
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", side_effect=_fake_translate),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        with pytest.raises(CategoryNotFoundError):
            provider.create_item("C_Foods", "apple")
    assert not (content_root / "foods").exists()


def test_append_csv_line_quotes_and_terminates_rows(tmp_path: Path) -> None:
    path = tmp_path / "items_en.csv"
    path.write_text("apple", encoding="utf-8")  # no trailing newline
    FileSystemContentProvider._append_csv_line(path, "hot, chocolate")
    FileSystemContentProvider._append_csv_line(path, "pear")
    assert path.read_text(encoding="utf-8") == 'apple\n"hot, chocolate"\npear\n'
    assert FileSystemContentProvider._read_csv_column(path) == ["apple", "hot, chocolate", "pear"]


# ---------------------------------------------------------------------------
# update_item_name
# ---------------------------------------------------------------------------

def test_update_item_name_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    write_item_registry(category_path, [("I_Apple", "apple")])

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        with pytest.raises(CategoryNotFoundError):
            provider.update_item_name("C_Foods", "I_Missing", LocalizedString({"en": "banana"}))


def test_update_item_name_writes_localized_name(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    write_item_registry(category_path, [("I_Apple", "apple"), ("I_Banana", "banana")])
    (category_path / "items_fr.csv").write_text("pomme\nbanane\n", encoding="utf-8")

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.update_item_name(
            "C_Foods", "I_Apple", LocalizedString({"en": "green apple", "fr": "pomme verte"}),
        )

    assert result.id == "I_Apple"
    assert result.name.en == "green apple"
    assert result.name.fr == "pomme verte"
    assert (category_path / "items_en.csv").read_text(encoding="utf-8") == "green apple\nbanana\n"
    assert (category_path / "items_fr.csv").read_text(encoding="utf-8") == "pomme verte\nbanane\n"


# ---------------------------------------------------------------------------
# regenerate_item_voice
# ---------------------------------------------------------------------------

def test_regenerate_item_voice_unknown_voice_id(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    write_item_registry(category_path, [("I_Apple", "apple")])

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        with pytest.raises(CategoryNotFoundError):
            provider.regenerate_item_voice("C_Foods", "I_Apple", "apple-v-de")


def test_regenerate_item_voice_uses_current_text_and_overwrites_file(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    write_item_registry(category_path, [("I_Apple", "apple"), ("I_Banana", "banana")])
    (category_path / "items_fr.csv").write_text("pomme verte\nbanane\n", encoding="utf-8")
    (category_path / "apple" / "apple_fr.mp3").write_bytes(b"stale")

    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"fresh") as audio_mock,
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.regenerate_item_voice("C_Foods", "I_Apple", "apple-v-fr")

    audio_mock.assert_called_once_with("pomme verte", "fr")
    assert result.id == "apple-v-fr"
    assert result.language == "fr"
    assert result.file_uri == "/content/foods/apple/apple_fr.mp3"
    assert (category_path / "apple" / "apple_fr.mp3").read_bytes() == b"fresh"


def test_regenerate_item_voice_creates_missing_file_and_falls_back_to_english(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    write_item_registry(category_path, [("I_Apple", "apple")])

    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"fresh") as audio_mock,
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.regenerate_item_voice("C_Foods", "I_Apple", "apple-v-fr")

    audio_mock.assert_called_once_with("apple", "fr")
    assert result.file_uri == "/content/foods/apple/apple_fr.mp3"
    assert (category_path / "apple" / "apple_fr.mp3").read_bytes() == b"fresh"


def test_regenerate_item_voice_wraps_tts_failure(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    write_item_registry(category_path, [("I_Apple", "apple")])

    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_generate_audio", side_effect=RuntimeError("boom")),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        with pytest.raises(ContentOperationError, match="Voice generation failed for en: boom"):
            provider.regenerate_item_voice("C_Foods", "I_Apple", "apple-v-en")


def test_item_voice_sidecar_tracks_upload_generation_provider_and_deletion(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    write_item_registry(category_path, [("I_Apple", "apple")])
    provider = make_fs_provider()

    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider.add_item_voice("C_Foods", "I_Apple", "en", b"uploaded audio")

        metadata_path = category_path / "apple" / "apple_en.csv"
        assert _voice_metadata_normalized(metadata_path) == {
            "creation_method": "uploaded",
            "provider": "custom",
            "parameters": {},
        }

        parameters = {"actor": "alloy", "model": "tts-1-hd", "output_format": "mp3"}
        response = httpx.Response(
            200,
            content=b"generated audio",
            headers={
                "X-TTS-Provider": "openai",
                "X-TTS-Parameters": base64.urlsafe_b64encode(
                    json.dumps(parameters).encode("utf-8"),
                ).decode("ascii"),
            },
            request=httpx.Request("POST", "http://test/internal/generators/generate-word"),
        )
        with (
            patch("src.services.content.providers.file_system.internal_api.httpx.post", return_value=response),
            patch(
                "src.services.content.providers.file_system.internal_api.create_internal_token",
                return_value="token",
            ),
        ):
            provider.regenerate_item_voice("C_Foods", "I_Apple", "apple-v-en")

        assert _voice_metadata_normalized(metadata_path) == {
            "creation_method": "generated",
            "provider": "openai",
            "parameters": {**parameters, "_text": "apple"},
        }
        provider.delete_item_voice("C_Foods", "I_Apple", "apple-v-en")

    assert not metadata_path.exists()


# ---------------------------------------------------------------------------
# regenerate_category_voice
# ---------------------------------------------------------------------------

def test_regenerate_category_voice_unknown_voice_id(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    category_path.mkdir(parents=True)

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        with pytest.raises(CategoryNotFoundError):
            provider.regenerate_category_voice("C_Foods", "foods-v-de")


def test_regenerate_category_voice_uses_current_name_and_overwrites_file(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    (content_root / "categories_fr.csv").write_text("Aliments\n", encoding="utf-8")
    category_path.mkdir(parents=True)
    (category_path / "foods_fr.mp3").write_bytes(b"stale")

    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"fresh") as audio_mock,
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.regenerate_category_voice("C_Foods", "foods-v-fr")

    audio_mock.assert_called_once_with("Aliments", "fr")
    assert result.id == "foods-v-fr"
    assert result.language == "fr"
    assert result.file_uri == "/content/foods/foods_fr.mp3"
    assert (category_path / "foods_fr.mp3").read_bytes() == b"fresh"
    # _generate_audio is mocked to return plain bytes, so no provider/parameters attrs are carried.
    assert result.provider == "unknown"
    assert _without_date(result.parameters) == {"_text": "Aliments"}


def test_regenerate_category_voice_creates_missing_file_and_falls_back_to_english(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    category_path.mkdir(parents=True)

    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_generate_audio", return_value=b"fresh") as audio_mock,
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.regenerate_category_voice("C_Foods", "foods-v-fr")

    audio_mock.assert_called_once_with("Foods", "fr")
    assert result.file_uri == "/content/foods/foods_fr.mp3"
    assert (category_path / "foods_fr.mp3").read_bytes() == b"fresh"


def test_regenerate_category_voice_wraps_tts_failure(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    category_path.mkdir(parents=True)

    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_generate_audio", side_effect=RuntimeError("boom")),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        with pytest.raises(ContentOperationError, match="Voice generation failed for en: boom"):
            provider.regenerate_category_voice("C_Foods", "foods-v-en")


def test_regenerate_category_voice_missing_category_folder(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    write_category_registry(content_root, [("C_Foods", "foods")])  # registered, but no folder on disk

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        with pytest.raises(CategoryNotFoundError):
            provider.regenerate_category_voice("C_Foods", "foods-v-en")


# ---------------------------------------------------------------------------
# voice metadata (provider / parameters)
# ---------------------------------------------------------------------------

def test_read_voice_metadata_missing_sidecar_returns_unknown(tmp_path: Path) -> None:
    provider = make_fs_provider()
    assert provider._read_voice_metadata(tmp_path / "foods_en.mp3") == ("unknown", {})


def test_read_voice_metadata_reads_provider_and_parameters(tmp_path: Path) -> None:
    voice_path = tmp_path / "foods_en.mp3"
    voice_path.write_bytes(b"audio")
    FileSystemContentProvider._write_voice_metadata(
        voice_path, VoiceCreationMethod.GENERATED, "openai", {"actor": "alloy"},
    )

    provider = make_fs_provider()
    read_provider, parameters = provider._read_voice_metadata(voice_path)
    assert read_provider == "openai"
    assert _without_date(parameters) == {"actor": "alloy"}


def test_read_voice_metadata_blank_provider_and_invalid_json_fall_back_to_defaults(tmp_path: Path) -> None:
    voice_path = tmp_path / "foods_en.mp3"
    voice_path.write_bytes(b"audio")
    metadata_path = FileSystemContentProvider._voice_metadata_path(voice_path)
    metadata_path.write_text("creation_method,provider,parameters\nuploaded,,not-json\n", encoding="utf-8")

    provider = make_fs_provider()
    assert provider._read_voice_metadata(voice_path) == ("unknown", {})


def test_collect_voices_surfaces_provider_and_parameters(tmp_path: Path) -> None:
    content_root, category_path = _prepare_category(tmp_path)
    voice_path = category_path / "foods_en.mp3"
    voice_path.write_bytes(b"audio")
    FileSystemContentProvider._write_voice_metadata(
        voice_path, VoiceCreationMethod.GENERATED, "speaktor", {"voice": "en-US-1"},
    )

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        voices = provider._collect_voices(category_path, "foods")

    en_voice = next(v for v in voices if v.language == "en")
    fr_voice = next(v for v in voices if v.language == "fr")
    assert en_voice.provider == "speaktor"
    assert _without_date(en_voice.parameters) == {"voice": "en-US-1"}
    # No file for 'fr' at all, let alone a metadata sidecar.
    assert fr_voice.provider == "unknown"
    assert fr_voice.parameters == {}


def test_add_category_voice_uploaded_has_custom_provider(tmp_path: Path) -> None:
    content_root, _ = _prepare_category(tmp_path)

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        result = provider.add_category_voice("C_Foods", "en", b"uploaded audio")

    assert result.provider == "custom"
    assert _without_date(result.parameters) == {}


# ---------------------------------------------------------------------------
# remove_item
# ---------------------------------------------------------------------------

def test_remove_item_not_found(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    write_item_registry(category_path, [("I_Apple", "apple")])

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        with pytest.raises(CategoryNotFoundError):
            provider.remove_item("C_Foods", "I_Missing")


def test_remove_item_deletes_folder_and_csv_rows(tmp_path: Path) -> None:
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    write_item_registry(category_path, [("I_Apple", "apple"), ("I_Banana", "banana")])
    (category_path / "items_fr.csv").write_text("pomme\nbanane\n", encoding="utf-8")

    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.remove_item("C_Foods", "I_Apple")

    assert result.category == "foods"
    assert result.item == "I_Apple"
    assert not (category_path / "apple").exists()
    assert (category_path / "banana").is_dir()
    assert (category_path / "items_ids.csv").read_text(encoding="utf-8") == "I_Banana\n"
    assert (category_path / "items_en.csv").read_text(encoding="utf-8") == "banana\n"
    assert (category_path / "items_fr.csv").read_text(encoding="utf-8") == "banane\n"


def test_extension_for_content_type_maps_supported_types() -> None:
    """Only extensions the icon lookup understands may be produced."""
    assert _extension_for_content_type("image/png") == ".png"
    assert _extension_for_content_type("image/jpeg") == ".jpg"
    assert _extension_for_content_type("image/jpeg; charset=binary") == ".jpg"
    assert _extension_for_content_type("image/webp") == ".png"
    assert _extension_for_content_type(None) == ".png"
