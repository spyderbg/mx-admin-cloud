"""Unit tests for FileSystemContentProvider — item voice variants, the main voice, and voice optimization."""

# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from src.services.content.models import (
    CategoryNotFoundError,
    ContentOperationError,
)
from src.services.content.providers.file_system import (
    FileSystemContentProvider,
    VoiceCreationMethod,
)
from src.services.content.__tests__.content_fs_helpers import (
    patch_fs_settings,
    make_fs_provider,
    voice_metadata_normalized,
    write_category_registry,
    write_item_registry,
)


# ---------------------------------------------------------------------------
# item voice variants and set_main_item_voice
# ---------------------------------------------------------------------------

def _prepare_item_with_voices(tmp_path: Path, voices: dict[str, bytes]) -> tuple[Path, Path]:
    """Write a Foods/Apple registry and *voices* ({stem: bytes}) as MP3s into the item folder."""
    content_root = tmp_path / "content"
    category_path = content_root / "foods"
    write_category_registry(content_root, [("C_Foods", "Foods")])
    write_item_registry(category_path, [("I_Apple", "apple")])
    item_path = category_path / "apple"
    for stem, data in voices.items():
        (item_path / f"{stem}.mp3").write_bytes(data)
    return content_root, item_path


def test_item_voices_lists_variants_and_the_selected_main_per_language(tmp_path: Path) -> None:
    content_root, _ = _prepare_item_with_voices(tmp_path, {
        "apple_en": b"second-en",  # the English main voice is a copy of apple_en_02
        "apple_en_01": b"first-en",
        "apple_en_02": b"second-en",
        "apple_fr": b"custom-fr",  # matches no variant
        "apple_fr_01": b"first-fr",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr", "bg"]
        result = provider.item_voices("C_Foods", "I_Apple")

    assert [(v.id, v.file_uri) for v in result.voices] == [
        ("apple-v-en", "/content/foods/apple/apple_en.mp3"),
        ("apple-v-fr", "/content/foods/apple/apple_fr.mp3"),
        ("apple-v-bg", None),
    ]
    assert [(v.id, v.language) for v in result.variants] == [
        ("apple_en_01", "en"), ("apple_en_02", "en"), ("apple_fr_01", "fr"),
    ]
    assert result.main_voice_ids == {"en": "apple_en_02", "fr": None, "bg": None}


def test_item_voices_without_main_uses_first_variant(tmp_path: Path) -> None:
    content_root, _ = _prepare_item_with_voices(tmp_path, {"apple_en_01": b"first-en", "apple_en_02": b"second-en"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        result = provider.item_voices("C_Foods", "I_Apple")

    assert result.voices[0].file_uri == "/content/foods/apple/apple_en_01.mp3"
    assert result.main_voice_ids == {"en": "apple_en_01"}


def test_set_main_item_voice_copies_the_variant_and_its_metadata_over_the_main_voice(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {
        "apple_en": b"main-en",
        "apple_en_01": b"first-en",
        "apple_en_02": b"second-en",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider._write_voice_metadata(item_path / "apple_en.mp3", VoiceCreationMethod.UPLOADED, "custom")
        provider._write_voice_metadata(
            item_path / "apple_en_02.mp3", VoiceCreationMethod.GENERATED, "openai", {"actor": "alloy"},
        )
        result = provider.set_main_item_voice("C_Foods", "I_Apple", "apple_en_02", "en")

    assert (item_path / "apple_en.mp3").read_bytes() == b"second-en"
    # The variant is copied, never moved: every recording stays in the item.
    assert (item_path / "apple_en_01.mp3").read_bytes() == b"first-en"
    assert (item_path / "apple_en_02.mp3").read_bytes() == b"second-en"
    assert (item_path / "apple_en.csv").read_text() == (item_path / "apple_en_02.csv").read_text()
    assert result.main_voice_ids == {"en": "apple_en_02"}
    assert [v.id for v in result.variants] == ["apple_en_01", "apple_en_02"]
    assert result.voices[0].provider == "openai"


def test_set_main_item_voice_drops_stale_metadata_when_the_variant_has_none(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"main-en", "apple_en_01": b"first-en"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider._write_voice_metadata(item_path / "apple_en.mp3", VoiceCreationMethod.UPLOADED, "custom")
        provider.set_main_item_voice("C_Foods", "I_Apple", "apple_en_01", "en")

    assert (item_path / "apple_en.mp3").read_bytes() == b"first-en"
    assert not (item_path / "apple_en.csv").exists()


def test_set_main_item_voice_with_no_existing_main_creates_it(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {
        "apple_en_01": b"first-en",
        "apple_en_02": b"second-en",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        result = provider.set_main_item_voice("C_Foods", "I_Apple", "apple_en_02", "en")

    assert (item_path / "apple_en.mp3").read_bytes() == b"second-en"
    assert result.voices[0].file_uri == "/content/foods/apple/apple_en.mp3"
    assert result.main_voice_ids == {"en": "apple_en_02"}


def test_set_main_item_voice_leaves_other_languages_untouched(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {
        "apple_en": b"main-en",
        "apple_en_01": b"first-en",
        "apple_fr": b"main-fr",
        "apple_fr_01": b"first-fr",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        provider.set_main_item_voice("C_Foods", "I_Apple", "apple_fr_01", "fr")

    assert (item_path / "apple_en.mp3").read_bytes() == b"main-en"
    assert (item_path / "apple_fr.mp3").read_bytes() == b"first-fr"


@pytest.mark.parametrize("voice_id", ["apple-v-en", "apple_en"])
def test_set_main_item_voice_already_in_use_is_noop(tmp_path: Path, voice_id: str) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"main-en", "apple_en_01": b"first-en"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider.set_main_item_voice("C_Foods", "I_Apple", voice_id, "en")

    assert (item_path / "apple_en.mp3").read_bytes() == b"main-en"


def test_set_main_item_voice_variant_of_another_language_is_not_found(tmp_path: Path) -> None:
    content_root, _ = _prepare_item_with_voices(tmp_path, {"apple_en": b"main-en", "apple_fr_01": b"first-fr"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        with pytest.raises(CategoryNotFoundError, match="language 'en'"):
            provider.set_main_item_voice("C_Foods", "I_Apple", "apple_fr_01", "en")


@pytest.mark.parametrize("voice_id", ["apple_en_99", "../apple_en_01", "apple-v-en"])
def test_set_main_item_voice_not_found(tmp_path: Path, voice_id: str) -> None:
    # "apple-v-en" is only a no-op when the language has a voice; here it has none.
    content_root, _ = _prepare_item_with_voices(tmp_path, {})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        with pytest.raises(CategoryNotFoundError):
            provider.set_main_item_voice("C_Foods", "I_Apple", voice_id, "en")


def test_set_main_item_voice_unsupported_language(tmp_path: Path) -> None:
    content_root, _ = _prepare_item_with_voices(tmp_path, {"apple_en_01": b"first-en"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        with pytest.raises(ContentOperationError, match="Unsupported language"):
            provider.set_main_item_voice("C_Foods", "I_Apple", "apple_en_01", "de")


# ---------------------------------------------------------------------------
# optimize_item_voice
# ---------------------------------------------------------------------------

def _fake_optimizer(failing: frozenset[str] = frozenset()):
    """Stand-in for _optimize_audio_file: writes "<optimizer>:<raw bytes>" to the output path."""
    calls: list[tuple[str, Path, Path]] = []

    def optimize(optimizer: str, raw_path: Path, output_path: Path) -> None:
        calls.append((optimizer, raw_path, output_path))
        output_path.write_bytes(b"partial")
        if optimizer in failing:
            raise RuntimeError(f"{optimizer} exploded")
        output_path.write_bytes(optimizer.encode() + b":" + raw_path.read_bytes())

    return optimize, calls


def _optimize(
        content_root: Path,
        voice_id: str,
        optimizers: list[str],
        failing: frozenset[str] = frozenset(),
        languages: tuple[str, ...] = ("en",),
):
    """Run optimize_item_voice for I_Apple with fake optimizers; return (result, optimizer calls)."""
    provider = make_fs_provider()
    optimize, calls = _fake_optimizer(failing)
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_active_voice_optimizers", return_value=optimizers),
        patch.object(FileSystemContentProvider, "_optimize_audio_file", side_effect=optimize),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = list(languages)
        return provider.optimize_item_voice("C_Foods", "I_Apple", voice_id), calls


def test_optimize_item_voice_keeps_the_original_and_adds_one_variant_per_optimizer(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})
    provider = make_fs_provider()
    with patch_fs_settings():
        provider._write_voice_metadata(
            item_path / "apple_en.mp3", VoiceCreationMethod.GENERATED, "openai", {"_text": "apple"},
        )

    result, calls = _optimize(content_root, "apple-v-en", ["ffmpeg_low", "pydub_optimized"])

    # The main voice was no copy of any variant, so it is kept as a variant first.
    assert (item_path / "apple_en_01.mp3").read_bytes() == b"original"
    assert (item_path / "apple_en_02.mp3").read_bytes() == b"ffmpeg_low:original"
    assert (item_path / "apple_en_03.mp3").read_bytes() == b"pydub_optimized:original"
    # Optimizing never changes the main voice.
    assert (item_path / "apple_en.mp3").read_bytes() == b"original"
    assert [c[1] for c in calls] == [(item_path / "apple_en_01.mp3").resolve()] * 2
    assert result.source_voice_id == "apple_en_01"
    assert result.optimized_voice_ids == ["apple_en_02", "apple_en_03"]
    assert result.warnings == []
    assert [v.id for v in result.variants] == ["apple_en_01", "apple_en_02", "apple_en_03"]
    assert result.main_voice_ids == {"en": "apple_en_01"}
    assert voice_metadata_normalized(item_path / "apple_en_02.csv") == {
        "creation_method": "optimized",
        "provider": "openai",
        "parameters": {"_text": "apple", "_optimizer": "ffmpeg_low", "_source": "apple_en_01"},
    }
    assert voice_metadata_normalized(item_path / "apple_en_01.csv")["creation_method"] == "generated"
    assert not list(item_path.glob(".*"))


def test_optimize_item_voice_again_replaces_the_previous_output(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})
    _optimize(content_root, "apple-v-en", ["ffmpeg_low"])
    (item_path / "apple_en_02.mp3").write_bytes(b"stale")

    result, _ = _optimize(content_root, "apple-v-en", ["ffmpeg_low"])

    assert result.source_voice_id == "apple_en_01"
    assert result.optimized_voice_ids == ["apple_en_02"]
    assert (item_path / "apple_en_02.mp3").read_bytes() == b"ffmpeg_low:original"
    assert sorted(f.name for f in item_path.glob("apple_en*.mp3")) == [
        "apple_en.mp3", "apple_en_01.mp3", "apple_en_02.mp3",
    ]


def test_optimize_item_voice_uses_the_variant_the_main_voice_was_copied_from(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {
        "apple_en": b"second",
        "apple_en_01": b"first",
        "apple_en_02": b"second",
    })

    result, _ = _optimize(content_root, "apple-v-en", ["ffmpeg_low"])

    assert result.source_voice_id == "apple_en_02"
    assert (item_path / "apple_en_03.mp3").read_bytes() == b"ffmpeg_low:second"
    assert not (item_path / "apple_en_04.mp3").exists()


def test_optimize_item_voice_of_an_optimized_version_starts_from_its_original(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})
    _optimize(content_root, "apple-v-en", ["ffmpeg_low"])

    result, calls = _optimize(content_root, "apple_en_02", ["ffmpeg_low", "ffmpeg_high"])

    assert result.source_voice_id == "apple_en_01"
    assert {c[1] for c in calls} == {(item_path / "apple_en_01.mp3").resolve()}
    assert result.optimized_voice_ids == ["apple_en_02", "apple_en_03"]
    assert (item_path / "apple_en_03.mp3").read_bytes() == b"ffmpeg_high:original"


def test_optimize_item_voice_after_selecting_an_optimized_main_keeps_one_original(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})
    _optimize(content_root, "apple-v-en", ["ffmpeg_low"])
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        selected = provider.set_main_item_voice("C_Foods", "I_Apple", "apple_en_02", "en")
    assert selected.main_voice_ids == {"en": "apple_en_02"}

    result, _ = _optimize(content_root, "apple-v-en", ["ffmpeg_low"])

    # The main is now a copy of the optimized variant: no second "original" is made from it.
    assert result.source_voice_id == "apple_en_01"
    assert sorted(f.name for f in item_path.glob("apple_en_*.mp3")) == ["apple_en_01.mp3", "apple_en_02.mp3"]


def test_optimize_item_voice_without_main_uses_the_first_variant(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en_01": b"first", "apple_en_02": b"second"})

    result, _ = _optimize(content_root, "apple-v-en", ["ffmpeg_low"])

    assert result.source_voice_id == "apple_en_01"
    assert (item_path / "apple_en_03.mp3").read_bytes() == b"ffmpeg_low:first"
    assert not (item_path / "apple_en.mp3").exists()


def test_optimize_item_voice_reports_a_failing_optimizer_and_keeps_the_others(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})

    result, _ = _optimize(content_root, "apple-v-en", ["ffmpeg_low", "pydub_optimized"], frozenset({"ffmpeg_low"}))

    assert result.optimized_voice_ids == ["apple_en_02"]
    assert (item_path / "apple_en_02.mp3").read_bytes() == b"pydub_optimized:original"
    assert result.warnings == ["Voice optimization failed for ffmpeg_low: ffmpeg_low exploded"]
    assert not list(item_path.glob(".*"))


def test_optimize_item_voice_fails_when_every_optimizer_fails(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})

    with pytest.raises(ContentOperationError, match="ffmpeg_low exploded"):
        _optimize(content_root, "apple-v-en", ["ffmpeg_low"], frozenset({"ffmpeg_low"}))

    assert not list(item_path.glob(".*"))
    assert not (item_path / "apple_en_02.mp3").exists()


def test_optimize_item_voice_without_active_optimizers(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})

    with pytest.raises(ContentOperationError, match="No active voice optimizers"):
        _optimize(content_root, "apple-v-en", [])

    # Nothing is touched when there is nothing to run.
    assert not (item_path / "apple_en_01.mp3").exists()


def test_optimize_item_voice_when_the_optimizer_listing_fails(tmp_path: Path) -> None:
    content_root, _ = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})
    provider = make_fs_provider()
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_active_voice_optimizers", side_effect=RuntimeError("down")),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        with pytest.raises(ContentOperationError, match="unavailable: down"):
            provider.optimize_item_voice("C_Foods", "I_Apple", "apple-v-en")


@pytest.mark.parametrize("voice_id", ["apple-v-de", "apple_en_99", "banana-v-en"])
def test_optimize_item_voice_unknown_voice(tmp_path: Path, voice_id: str) -> None:
    content_root, _ = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})
    with pytest.raises(CategoryNotFoundError):
        _optimize(content_root, voice_id, ["ffmpeg_low"])


def test_optimize_item_voice_language_without_audio(tmp_path: Path) -> None:
    content_root, _ = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})
    with pytest.raises(CategoryNotFoundError, match="no audio"):
        _optimize(content_root, "apple-v-fr", ["ffmpeg_low"], languages=("en", "fr"))


def test_active_voice_optimizers_lists_only_enabled_configs() -> None:
    response = httpx.Response(
        200,
        json=[
            {"name": "ffmpeg_low", "enabled": True, "priority": 1},
            {"name": "ffmpeg_high", "enabled": False, "priority": 1},
            {"name": "pydub_optimized", "enabled": True, "priority": 2},
        ],
        request=httpx.Request("GET", "http://test/internal/optimizers/"),
    )
    with (
        patch_fs_settings(),
        patch("src.services.content.providers.file_system.internal_api.httpx.get", return_value=response),
        patch("src.services.content.providers.file_system.internal_api.create_internal_token", return_value="t"),
    ):
        assert FileSystemContentProvider._active_voice_optimizers() == ["ffmpeg_low", "pydub_optimized"]


def test_optimize_audio_file_raises_the_endpoint_detail(tmp_path: Path) -> None:
    response = httpx.Response(
        404,
        json={"detail": "Optimizer 'ffmpeg_low' not found or not active."},
        request=httpx.Request("POST", "http://test/internal/optimizers/ffmpeg_low/optimize"),
    )
    with (
        patch_fs_settings(),
        patch("src.services.content.providers.file_system.internal_api.httpx.post", return_value=response) as post,
        patch("src.services.content.providers.file_system.internal_api.create_internal_token", return_value="t"),
    ):
        with pytest.raises(RuntimeError, match="not found or not active"):
            FileSystemContentProvider._optimize_audio_file("ffmpeg_low", tmp_path / "a.mp3", tmp_path / "b.mp3")
    assert post.call_args.kwargs["json"] == {
        "raw_path": str(tmp_path / "a.mp3"),
        "output_path": str(tmp_path / "b.mp3"),
    }
