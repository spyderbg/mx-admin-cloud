"""Unit tests for FileSystemContentProvider.create_item — generating the image of a new item."""

# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from src.services.content.providers.file_system import FileSystemContentProvider
from src.services.content.__tests__.content_fs_helpers import (
    make_fs_provider,
    patch_fs_settings,
    write_category_registry,
)


def _prepare_category(tmp_path: Path) -> tuple[Path, Path]:
    """Create a registered, empty 'foods' category and return (content_root, category_path)."""
    content_root = tmp_path / "content"
    write_category_registry(content_root, [("C_Foods", "foods")])
    category_path = content_root / "foods"
    category_path.mkdir(parents=True, exist_ok=True)
    return content_root, category_path


def _fake_translate(text: str, target_lang_code: str | None = None, *_args: object, **_kwargs: object) -> str:  # pylint: disable=keyword-arg-before-vararg
    return f"{text}-{target_lang_code}"


def test_create_item_generates_an_image_when_asked(tmp_path: Path) -> None:
    content_root, category_path = _prepare_category(tmp_path)
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", side_effect=_fake_translate),
        patch.object(FileSystemContentProvider, "_generate_image", return_value=(b"generated", ".png")) as generate,
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.create_item("C_Foods", "apple", generate_voices=False, generate_image=True)

    assert generate.call_count == 1
    assert "apple" in generate.call_args.args[0]
    assert result.image_generated is True
    assert not result.image_warnings
    assert (category_path / "apple" / "apple.png").read_bytes() == b"generated"


def test_create_item_image_failure_is_a_warning_not_an_error(tmp_path: Path) -> None:
    content_root, category_path = _prepare_category(tmp_path)
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", side_effect=_fake_translate),
        patch.object(FileSystemContentProvider, "_generate_image", side_effect=RuntimeError("no GPU")),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.create_item("C_Foods", "apple", generate_voices=False, generate_image=True)

    assert result.image_generated is False
    assert any("no GPU" in warning for warning in result.image_warnings)
    assert "The image was not generated." in result.message
    assert (category_path / "apple").is_dir()


def test_create_item_uploaded_image_wins_over_generation(tmp_path: Path) -> None:
    content_root, _ = _prepare_category(tmp_path)
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_translate_word", side_effect=_fake_translate),
        patch.object(FileSystemContentProvider, "_generate_image", return_value=(b"generated", ".png")) as generate,
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.create_item(
            "C_Foods", "apple", b"\x89PNG", ".png", generate_voices=False, generate_image=True,
        )

    assert generate.call_count == 0
    assert result.image_generated is False
