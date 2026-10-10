"""Unit tests for FileSystemContentProvider — item voice variants, the main voice, and voice optimization."""

# pylint: disable=missing-function-docstring,protected-access

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from src.services.content.models import (
    CategoryNotFoundError,
    ContentOperationError,
    GenerateItemVoice,
)
from src.services.content.providers.file_system import (
    FileSystemContentProvider,
    VoiceCreationMethod,
)
from src.services.content.providers.file_system.internal_api import _GeneratedAudioBytes
from src.services.provider_strategy import ProviderSelectionStrategy
from src.services.content.__tests__.content_fs_helpers import (
    patch_fs_settings,
    make_fs_provider,
    voice_metadata,
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
        result = provider.set_main_item_voice("C_Foods", "I_Apple", "apple_en_02")

    assert (item_path / "apple_en.mp3").read_bytes() == b"second-en"
    # The variant is copied, never moved: every recording stays in the item.
    assert (item_path / "apple_en_01.mp3").read_bytes() == b"first-en"
    assert (item_path / "apple_en_02.mp3").read_bytes() == b"second-en"
    assert (item_path / "apple_en.csv").read_text() == (item_path / "apple_en_02.csv").read_text()
    assert result.main_voice_ids == {"en": "apple_en_02"}
    # The replaced main matched no variant, so it was saved as one instead of being lost.
    assert (item_path / "apple_en_03.mp3").read_bytes() == b"main-en"
    assert voice_metadata_normalized(item_path / "apple_en_03.csv")["creation_method"] == "uploaded"
    assert [v.id for v in result.variants] == ["apple_en_01", "apple_en_02", "apple_en_03"]
    assert result.voices[0].provider == "openai"


def test_set_main_item_voice_drops_stale_metadata_when_the_variant_has_none(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"main-en", "apple_en_01": b"first-en"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider._write_voice_metadata(item_path / "apple_en.mp3", VoiceCreationMethod.UPLOADED, "custom")
        provider.set_main_item_voice("C_Foods", "I_Apple", "apple_en_01")

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
        result = provider.set_main_item_voice("C_Foods", "I_Apple", "apple_en_02")

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
        provider.set_main_item_voice("C_Foods", "I_Apple", "apple_fr_01")

    assert (item_path / "apple_en.mp3").read_bytes() == b"main-en"
    assert (item_path / "apple_fr.mp3").read_bytes() == b"first-fr"


@pytest.mark.parametrize("voice_id", ["apple-v-en", "apple_en"])
def test_set_main_item_voice_already_in_use_is_noop(tmp_path: Path, voice_id: str) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"main-en", "apple_en_01": b"first-en"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider.set_main_item_voice("C_Foods", "I_Apple", voice_id)

    assert (item_path / "apple_en.mp3").read_bytes() == b"main-en"


def test_set_main_item_voice_selects_the_correct_language_from_the_voice_id(tmp_path: Path) -> None:
    # Language is derived from voice_id alone, so selecting an "fr" variant can never touch "en".
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"main-en", "apple_fr_01": b"first-fr"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en", "fr"]
        result = provider.set_main_item_voice("C_Foods", "I_Apple", "apple_fr_01")

    assert (item_path / "apple_en.mp3").read_bytes() == b"main-en"
    assert (item_path / "apple_fr.mp3").read_bytes() == b"first-fr"
    assert result.main_voice_ids == {"en": None, "fr": "apple_fr_01"}


@pytest.mark.parametrize("voice_id", ["apple_en_99", "../apple_en_01", "apple-v-en", "does_not_exist"])
def test_set_main_item_voice_not_found(tmp_path: Path, voice_id: str) -> None:
    # "apple-v-en" is only a no-op when the language has a voice; here it has none.
    content_root, _ = _prepare_item_with_voices(tmp_path, {})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        with pytest.raises(CategoryNotFoundError):
            provider.set_main_item_voice("C_Foods", "I_Apple", voice_id)


# ---------------------------------------------------------------------------
# regenerate_item_voice: takes are added, never overwritten
# ---------------------------------------------------------------------------

def _regenerate(provider: FileSystemContentProvider, content_root: Path, voice_id: str, audio: bytes | Exception):
    """Run regenerate_item_voice for the Apple item with the TTS call stubbed to return *audio*."""
    stub = {"side_effect": audio} if isinstance(audio, Exception) else {"return_value": audio}
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_generate_audio", **stub),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        return provider.regenerate_item_voice("C_Foods", "I_Apple", voice_id)


def _mp3s(item_path: Path) -> dict[str, bytes]:
    return {f.stem: f.read_bytes() for f in sorted(item_path.glob("*.mp3"))}


def test_regenerate_item_voice_saves_the_original_main_as_a_variant_then_adds_the_new_take(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})
    provider = make_fs_provider()

    result = _regenerate(provider, content_root, "apple-v-en", b"take-two")

    assert result.id == "apple_en_02"
    assert _mp3s(item_path) == {"apple_en": b"original", "apple_en_01": b"original", "apple_en_02": b"take-two"}
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        voices = provider.item_voices("C_Foods", "I_Apple")
    # The main voice is still the original recording; the new take waits to be chosen.
    assert voices.main_voice_ids == {"en": "apple_en_01"}
    assert [v.id for v in voices.variants] == ["apple_en_01", "apple_en_02"]


def test_regenerate_item_voice_again_keeps_adding_takes(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})
    provider = make_fs_provider()

    _regenerate(provider, content_root, "apple-v-en", b"take-two")
    result = _regenerate(provider, content_root, "apple-v-en", b"take-three")

    assert result.id == "apple_en_03"
    assert _mp3s(item_path) == {
        "apple_en": b"original", "apple_en_01": b"original", "apple_en_02": b"take-two", "apple_en_03": b"take-three",
    }


def test_regenerate_item_voice_does_not_duplicate_a_main_that_is_already_a_variant(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"same", "apple_en_01": b"same"})
    provider = make_fs_provider()

    result = _regenerate(provider, content_root, "apple-v-en", b"new")

    assert result.id == "apple_en_02"
    assert _mp3s(item_path) == {"apple_en": b"same", "apple_en_01": b"same", "apple_en_02": b"new"}


@pytest.mark.parametrize("voice_id", ["apple-v-en", "apple_en", "apple_en_01"])
def test_regenerate_item_voice_accepts_the_main_and_variant_references(tmp_path: Path, voice_id: str) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"same", "apple_en_01": b"same"})
    provider = make_fs_provider()

    result = _regenerate(provider, content_root, voice_id, b"new")

    assert result.id == "apple_en_02"
    assert _mp3s(item_path)["apple_en_02"] == b"new"


def test_regenerate_item_voice_writes_generation_metadata_for_the_new_take(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})
    provider = make_fs_provider()

    result = _regenerate(provider, content_root, "apple-v-en", b"take-two")

    assert result.provider == "unknown"
    assert voice_metadata_normalized(item_path / "apple_en_02.csv") == {
        "creation_method": "generated", "provider": "unknown", "parameters": {"_text": "apple"},
    }


def test_regenerate_item_voice_with_only_variants_adds_a_variant_and_no_main(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en_01": b"first"})
    provider = make_fs_provider()

    result = _regenerate(provider, content_root, "apple-v-en", b"new")

    assert result.id == "apple_en_02"
    assert _mp3s(item_path) == {"apple_en_01": b"first", "apple_en_02": b"new"}


def test_regenerate_item_voice_failure_changes_nothing(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})
    provider = make_fs_provider()

    with pytest.raises(ContentOperationError, match="Voice generation failed for en: boom"):
        _regenerate(provider, content_root, "apple-v-en", RuntimeError("boom"))

    assert _mp3s(item_path) == {"apple_en": b"original"}


def test_a_regenerated_take_can_be_chosen_and_the_original_chosen_back(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})
    provider = make_fs_provider()
    new_take = _regenerate(provider, content_root, "apple-v-en", b"take-two")

    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        chosen = provider.set_main_item_voice("C_Foods", "I_Apple", new_take.id)
        assert (item_path / "apple_en.mp3").read_bytes() == b"take-two"
        assert chosen.main_voice_ids == {"en": "apple_en_02"}

        provider.set_main_item_voice("C_Foods", "I_Apple", "apple_en_01")
    assert _mp3s(item_path) == {"apple_en": b"original", "apple_en_01": b"original", "apple_en_02": b"take-two"}


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


def _optimize(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        content_root: Path,
        voice_id: str,
        optimizers: list[str],
        failing: frozenset[str] = frozenset(),
        languages: tuple[str, ...] = ("en",),
        strategy: ProviderSelectionStrategy = ProviderSelectionStrategy.ALL_PROVIDERS,
):
    """Run optimize_item_voice for I_Apple with fake optimizers; return (result, optimizer calls)."""
    provider = make_fs_provider()
    optimize, calls = _fake_optimizer(failing)
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_active_voice_optimizers", return_value=optimizers),
        patch.object(FileSystemContentProvider, "_optimize_audio_file", side_effect=optimize),
        patch.object(FileSystemContentProvider, "_voice_optimization_strategy", return_value=strategy),
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
    # Optimized files are named after their source and the optimizer configuration.
    assert (item_path / "apple_en_01_ffmpeg_low.mp3").read_bytes() == b"ffmpeg_low:original"
    assert (item_path / "apple_en_01_pydub_optimized.mp3").read_bytes() == b"pydub_optimized:original"
    # Optimizing never changes the main voice.
    assert (item_path / "apple_en.mp3").read_bytes() == b"original"
    assert [c[1] for c in calls] == [(item_path / "apple_en_01.mp3").resolve()] * 2
    assert result.source_voice_id == "apple_en_01"
    assert result.optimized_voice_ids == ["apple_en_01_ffmpeg_low", "apple_en_01_pydub_optimized"]
    assert result.warnings == []
    assert [v.id for v in result.variants] == ["apple_en_01", "apple_en_01_ffmpeg_low", "apple_en_01_pydub_optimized"]
    assert result.main_voice_ids == {"en": "apple_en_01"}
    assert voice_metadata_normalized(item_path / "apple_en_01_ffmpeg_low.csv") == {
        "creation_method": "optimized",
        "provider": "openai",
        "parameters": {"_text": "apple", "_optimizer": "ffmpeg_low", "_source": "apple_en_01"},
    }
    assert voice_metadata_normalized(item_path / "apple_en_01.csv")["creation_method"] == "generated"
    assert not list(item_path.glob(".*"))


def test_optimize_item_voice_again_replaces_the_previous_output(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})
    _optimize(content_root, "apple-v-en", ["ffmpeg_low"])
    (item_path / "apple_en_01_ffmpeg_low.mp3").write_bytes(b"stale")

    result, _ = _optimize(content_root, "apple-v-en", ["ffmpeg_low"])

    assert result.source_voice_id == "apple_en_01"
    assert result.optimized_voice_ids == ["apple_en_01_ffmpeg_low"]
    assert (item_path / "apple_en_01_ffmpeg_low.mp3").read_bytes() == b"ffmpeg_low:original"
    assert sorted(f.name for f in item_path.glob("apple_en*.mp3")) == [
        "apple_en.mp3", "apple_en_01.mp3", "apple_en_01_ffmpeg_low.mp3",
    ]


def test_optimize_item_voice_renames_output_stored_under_a_take_number(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original", "apple_en_01": b"original"})
    provider = make_fs_provider()
    (item_path / "apple_en_02.mp3").write_bytes(b"old")
    with patch_fs_settings():
        provider._write_voice_metadata(
            item_path / "apple_en_02.mp3", VoiceCreationMethod.OPTIMIZED, "openai",
            {"_optimizer": "ffmpeg_low", "_source": "apple_en_01"},
        )

    result, _ = _optimize(content_root, "apple-v-en", ["ffmpeg_low"])

    assert result.optimized_voice_ids == ["apple_en_01_ffmpeg_low"]
    assert (item_path / "apple_en_01_ffmpeg_low.mp3").read_bytes() == b"ffmpeg_low:original"
    assert sorted(f.name for f in item_path.glob("apple_en*.mp3")) == [
        "apple_en.mp3", "apple_en_01.mp3", "apple_en_01_ffmpeg_low.mp3",
    ]
    assert not (item_path / "apple_en_02.csv").exists()


def test_optimize_item_voice_uses_the_variant_the_main_voice_was_copied_from(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {
        "apple_en": b"second",
        "apple_en_01": b"first",
        "apple_en_02": b"second",
    })

    result, _ = _optimize(content_root, "apple-v-en", ["ffmpeg_low"])

    assert result.source_voice_id == "apple_en_02"
    assert (item_path / "apple_en_02_ffmpeg_low.mp3").read_bytes() == b"ffmpeg_low:second"
    assert not (item_path / "apple_en_03.mp3").exists()


def test_optimize_item_voice_of_an_optimized_version_starts_from_its_original(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})
    _optimize(content_root, "apple-v-en", ["ffmpeg_low"])

    result, calls = _optimize(content_root, "apple_en_01_ffmpeg_low", ["ffmpeg_low", "ffmpeg_high"])

    assert result.source_voice_id == "apple_en_01"
    assert {c[1] for c in calls} == {(item_path / "apple_en_01.mp3").resolve()}
    assert result.optimized_voice_ids == ["apple_en_01_ffmpeg_low", "apple_en_01_ffmpeg_high"]
    assert (item_path / "apple_en_01_ffmpeg_high.mp3").read_bytes() == b"ffmpeg_high:original"


def test_optimize_item_voice_after_selecting_an_optimized_main_keeps_one_original(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})
    _optimize(content_root, "apple-v-en", ["ffmpeg_low"])
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        selected = provider.set_main_item_voice("C_Foods", "I_Apple", "apple_en_01_ffmpeg_low")
    assert selected.main_voice_ids == {"en": "apple_en_01_ffmpeg_low"}

    result, _ = _optimize(content_root, "apple-v-en", ["ffmpeg_low"])

    # The main is now a copy of the optimized variant: no second "original" is made from it.
    assert result.source_voice_id == "apple_en_01"
    assert sorted(f.name for f in item_path.glob("apple_en_*.mp3")) == ["apple_en_01.mp3", "apple_en_01_ffmpeg_low.mp3"]


def test_optimize_item_voice_without_main_uses_the_first_variant(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en_01": b"first", "apple_en_02": b"second"})

    result, _ = _optimize(content_root, "apple-v-en", ["ffmpeg_low"])

    assert result.source_voice_id == "apple_en_01"
    assert (item_path / "apple_en_01_ffmpeg_low.mp3").read_bytes() == b"ffmpeg_low:first"
    assert not (item_path / "apple_en.mp3").exists()


def test_optimize_item_voice_reports_a_failing_optimizer_and_keeps_the_others(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})

    result, _ = _optimize(content_root, "apple-v-en", ["ffmpeg_low", "pydub_optimized"], frozenset({"ffmpeg_low"}))

    assert result.optimized_voice_ids == ["apple_en_01_pydub_optimized"]
    assert (item_path / "apple_en_01_pydub_optimized.mp3").read_bytes() == b"pydub_optimized:original"
    assert result.warnings == ["Voice optimization failed for ffmpeg_low: ffmpeg_low exploded"]
    assert not list(item_path.glob(".*"))


def test_optimize_item_voice_first_success_stops_after_the_first_optimizer_that_succeeds(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})

    result, calls = _optimize(
        content_root, "apple-v-en", ["ffmpeg_low", "pydub_optimized"],
        strategy=ProviderSelectionStrategy.FIRST_SUCCESS,
    )

    assert [optimizer for optimizer, _, _ in calls] == ["ffmpeg_low"]
    assert result.optimized_voice_ids == ["apple_en_01_ffmpeg_low"]
    assert (item_path / "apple_en_01_ffmpeg_low.mp3").read_bytes() == b"ffmpeg_low:original"
    assert not (item_path / "apple_en_01_pydub_optimized.mp3").exists()


def test_optimize_item_voice_first_success_falls_back_past_a_failing_optimizer(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})

    result, calls = _optimize(
        content_root, "apple-v-en", ["ffmpeg_low", "pydub_optimized", "ffmpeg_high"], frozenset({"ffmpeg_low"}),
        strategy=ProviderSelectionStrategy.FIRST_SUCCESS,
    )

    assert [optimizer for optimizer, _, _ in calls] == ["ffmpeg_low", "pydub_optimized"]
    assert result.optimized_voice_ids == ["apple_en_01_pydub_optimized"]
    assert (item_path / "apple_en_01_pydub_optimized.mp3").read_bytes() == b"pydub_optimized:original"
    assert result.warnings == ["Voice optimization failed for ffmpeg_low: ffmpeg_low exploded"]


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


def _strategy_lookup(response: httpx.Response | Exception) -> ProviderSelectionStrategy:
    stub = {"side_effect": response} if isinstance(response, Exception) else {"return_value": response}
    with (
        patch_fs_settings(),
        patch("src.services.content.providers.file_system.internal_api.httpx.get", **stub) as get,
        patch("src.services.content.providers.file_system.internal_api.create_internal_token", return_value="t"),
    ):
        strategy = FileSystemContentProvider._voice_optimization_strategy()
    assert get.call_args.args[0].endswith("/internal/configurations/voice_optimization/strategy")
    return strategy


_STRATEGY_REQUEST = httpx.Request("GET", "http://test/internal/configurations/voice_optimization/strategy")


def test_voice_optimization_strategy_reads_the_configured_strategy() -> None:
    response = httpx.Response(
        200, json={"service_name": "voice_optimization", "strategy": "FIRST_SUCCESS"}, request=_STRATEGY_REQUEST,
    )
    assert _strategy_lookup(response) is ProviderSelectionStrategy.FIRST_SUCCESS


@pytest.mark.parametrize("response", [
    httpx.Response(500, json={"detail": "boom"}, request=_STRATEGY_REQUEST),
    httpx.Response(200, json={"strategy": "SOMETHING_ELSE"}, request=_STRATEGY_REQUEST),
    httpx.Response(200, json=["not", "an", "object"], request=_STRATEGY_REQUEST),
    httpx.ConnectError("refused"),
])
def test_voice_optimization_strategy_falls_back_to_all_providers(response: httpx.Response | Exception) -> None:
    assert _strategy_lookup(response) is ProviderSelectionStrategy.ALL_PROVIDERS


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


# ---------------------------------------------------------------------------
# generate_item_voice: a new take with a chosen configuration and settings
# ---------------------------------------------------------------------------

def _generate(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        content_root: Path,
        request: GenerateItemVoice,
        audio: bytes | Exception = b"generated",
        optimizers: list[str] | None = None,
        failing: frozenset[str] = frozenset(),
        languages: tuple[str, ...] = ("en",),
):
    """Run generate_item_voice for I_Apple with TTS and optimizers stubbed; return (result, TTS mock)."""
    provider = make_fs_provider()
    stub = {"side_effect": audio} if isinstance(audio, Exception) else {"return_value": audio}
    optimize, _ = _fake_optimizer(failing)
    with (
        patch_fs_settings() as mock_settings,
        patch.object(FileSystemContentProvider, "_generate_audio", **stub) as generate_audio,
        patch.object(FileSystemContentProvider, "_active_voice_optimizers", return_value=optimizers or []),
        patch.object(FileSystemContentProvider, "_optimize_audio_file", side_effect=optimize),
        patch.object(
            FileSystemContentProvider, "_voice_optimization_strategy",
            return_value=ProviderSelectionStrategy.ALL_PROVIDERS,
        ),
    ):
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = list(languages)
        return provider.generate_item_voice("C_Foods", "I_Apple", request), generate_audio


def _request(**overrides) -> GenerateItemVoice:
    return GenerateItemVoice(**{"language": "en", "text": "apple", "configuration_name": "elevenlabs", **overrides})


def test_generate_item_voice_adds_a_variant_and_keeps_the_main_voice(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})

    result, _ = _generate(content_root, _request())

    assert result.voice_id == "apple_en_02"
    # The original main voice is first saved as a variant so it stays selectable, and stays in use.
    assert _mp3s(item_path) == {"apple_en": b"original", "apple_en_01": b"original", "apple_en_02": b"generated"}
    assert result.main_voice_ids == {"en": "apple_en_01"}
    assert [v.id for v in result.variants] == ["apple_en_01", "apple_en_02"]
    assert result.optimized_voice_ids == []
    assert result.warnings == []


def test_generate_item_voice_sends_the_text_configuration_and_settings(tmp_path: Path) -> None:
    content_root, _ = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})

    _, generate_audio = _generate(content_root, _request(
        text="  red apple ", configuration_name="elevenlabs_slow", speed=0.8, stability=0.3,
    ))

    # Only the settings that were given override the configuration's own.
    generate_audio.assert_called_once_with("red apple", "en", "elevenlabs_slow", {"speed": 0.8, "stability": 0.3})


def test_generate_item_voice_records_the_configuration_and_text(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})
    audio = _GeneratedAudioBytes(b"generated", "elevenlabs", {"actor": "Milena", "speed": 0.8})

    result, _ = _generate(content_root, _request(text="red apple", speed=0.8), audio=audio)

    assert voice_metadata_normalized(item_path / "apple_en_02.csv") == {
        "creation_method": "generated",
        "provider": "elevenlabs",
        "parameters": {"actor": "Milena", "speed": 0.8, "_text": "red apple", "configuration_name": "elevenlabs"},
    }
    assert result.variants[1].provider == "elevenlabs"


def test_generate_item_voice_again_keeps_adding_takes(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})

    _generate(content_root, _request(), audio=b"take-two")
    result, _ = _generate(content_root, _request(), audio=b"take-three")

    assert result.voice_id == "apple_en_03"
    assert _mp3s(item_path) == {
        "apple_en": b"original", "apple_en_01": b"original", "apple_en_02": b"take-two", "apple_en_03": b"take-three",
    }
    assert result.main_voice_ids == {"en": "apple_en_01"}


def test_generate_item_voice_for_a_language_without_recordings_becomes_its_voice(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})

    result, _ = _generate(content_root, _request(language="fr"), languages=("en", "fr"))

    # Like a regenerated first take: with nothing to keep in use, it is the language's voice.
    assert result.voice_id == "apple-v-fr"
    assert _mp3s(item_path) == {"apple_en": b"original", "apple_fr": b"generated"}


def test_generate_item_voice_with_optimize_adds_optimized_versions_of_the_new_take(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})

    result, _ = _generate(content_root, _request(optimize=True), optimizers=["ffmpeg_low", "pydub_optimized"])

    assert result.voice_id == "apple_en_02"
    assert result.optimized_voice_ids == ["apple_en_02_ffmpeg_low", "apple_en_02_pydub_optimized"]
    assert _mp3s(item_path) == {
        "apple_en": b"original",
        "apple_en_01": b"original",
        "apple_en_02": b"generated",
        "apple_en_02_ffmpeg_low": b"ffmpeg_low:generated",
        "apple_en_02_pydub_optimized": b"pydub_optimized:generated",
    }
    # Neither generating nor optimizing selects anything.
    assert result.main_voice_ids == {"en": "apple_en_01"}
    assert [v.id for v in result.variants] == [
        "apple_en_01",
        "apple_en_02",
        "apple_en_02_ffmpeg_low",
        "apple_en_02_pydub_optimized",
    ]
    assert result.warnings == []


def test_generate_item_voice_reports_a_failing_optimizer_and_keeps_the_others(tmp_path: Path) -> None:
    content_root, _ = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})

    result, _ = _generate(
        content_root, _request(optimize=True),
        optimizers=["ffmpeg_low", "pydub_optimized"], failing=frozenset({"ffmpeg_low"}),
    )

    assert result.optimized_voice_ids == ["apple_en_02_pydub_optimized"]
    assert result.warnings == ["Voice optimization failed for ffmpeg_low: ffmpeg_low exploded"]


def test_generate_item_voice_keeps_the_take_when_optimization_cannot_run(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})

    result, _ = _generate(content_root, _request(optimize=True), optimizers=[])

    assert result.voice_id == "apple_en_02"
    assert result.optimized_voice_ids == []
    assert result.warnings == ["No active voice optimizers configured."]
    assert _mp3s(item_path)["apple_en_02"] == b"generated"


def test_generate_item_voice_without_optimize_never_runs_optimizers(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})

    result, _ = _generate(content_root, _request(optimize=False), optimizers=["ffmpeg_low"])

    assert result.optimized_voice_ids == []
    assert set(_mp3s(item_path)) == {"apple_en", "apple_en_01", "apple_en_02"}


def test_generate_item_voice_failure_changes_nothing(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})

    with pytest.raises(ContentOperationError, match="Voice generation failed for en: not found"):
        _generate(content_root, _request(), audio=RuntimeError("not found"))

    assert _mp3s(item_path) == {"apple_en": b"original"}


@pytest.mark.parametrize("overrides,message", [
    ({"language": "de"}, "Unsupported language"),
    ({"text": "   "}, "Text to synthesize is required"),
    ({"configuration_name": " "}, "configuration is required"),
])
def test_generate_item_voice_rejects_invalid_requests(tmp_path: Path, overrides: dict, message: str) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"original"})

    with pytest.raises(ContentOperationError, match=message):
        _generate(content_root, _request(**overrides))

    assert _mp3s(item_path) == {"apple_en": b"original"}


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_voice_sidecar_records_the_hash_of_the_voice_file(tmp_path: Path) -> None:
    _, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"main-en"})
    make_fs_provider()._write_voice_metadata(item_path / "apple_en.mp3", VoiceCreationMethod.UPLOADED, "custom")

    assert voice_metadata(item_path / "apple_en.csv")["hash"] == _sha256(b"main-en")


def test_main_voice_matches_the_variant_with_the_same_hash(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {
        "apple_en": b"same", "apple_en_01": b"other", "apple_en_02": b"same",
    })
    provider = make_fs_provider()
    for name in ("apple_en", "apple_en_01", "apple_en_02"):
        provider._write_voice_metadata(item_path / f"{name}.mp3", VoiceCreationMethod.UPLOADED, "custom")
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        result = provider.item_voices("C_Foods", "I_Apple")

    assert result.main_voice_ids == {"en": "apple_en_02"}


def test_changed_voice_file_resets_its_sidecar_and_is_no_longer_the_main_copy(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"same", "apple_en_01": b"same"})
    provider = make_fs_provider()
    for name in ("apple_en", "apple_en_01"):
        provider._write_voice_metadata(
            item_path / f"{name}.mp3", VoiceCreationMethod.GENERATED, "openai", {"actor": "alloy"},
        )
    # The variant's recorded hash still equals the main's, but its MP3 was replaced outside the app.
    (item_path / "apple_en_01.mp3").write_bytes(b"replaced")
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        result = provider.item_voices("C_Foods", "I_Apple")

    assert result.main_voice_ids == {"en": None}
    assert voice_metadata(item_path / "apple_en_01.csv") == {
        "creation_method": "unknown", "provider": "custom", "parameters": "{}", "hash": _sha256(b"replaced"),
    }
    assert voice_metadata(item_path / "apple_en.csv")["creation_method"] == "generated"


def test_sidecar_without_a_hash_gains_it_and_keeps_its_fields(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {"apple_en": b"same", "apple_en_01": b"same"})
    for name in ("apple_en", "apple_en_01"):
        (item_path / f"{name}.csv").write_text(
            'creation_method,provider,parameters\nuploaded,custom,"{""a"": 1}"\n', encoding="utf-8",
        )
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        result = provider.item_voices("C_Foods", "I_Apple")

    assert result.main_voice_ids == {"en": "apple_en_01"}
    assert voice_metadata(item_path / "apple_en.csv") == {
        "creation_method": "uploaded", "provider": "custom", "parameters": '{"a": 1}', "hash": _sha256(b"same"),
    }


# ---------------------------------------------------------------------------
# delete_item_voice
# ---------------------------------------------------------------------------

def test_delete_item_voice_removes_the_named_variant_and_its_sidecar(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {
        "apple_en": b"main",
        "apple_en_01": b"take-one",
        "apple_en_02": b"main",
    })
    (item_path / "apple_en_01.csv").write_text(
        "creation_method,provider,parameters,hash\nuploaded,custom,{},\n", encoding="utf-8",
    )
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        deleted = provider.delete_item_voice("C_Foods", "I_Apple", "apple_en_01")

    assert deleted.id == "apple_en_01"
    assert deleted.language == "en"
    assert not (item_path / "apple_en_01.mp3").exists()
    assert not (item_path / "apple_en_01.csv").exists()
    assert (item_path / "apple_en.mp3").read_bytes() == b"main"
    assert (item_path / "apple_en_02.mp3").read_bytes() == b"main"


def test_delete_item_voice_removes_the_main_copy_of_the_deleted_take(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {
        "apple_en": b"chosen",
        "apple_en_01": b"other",
        "apple_en_02": b"chosen",
    })
    (item_path / "apple_en.csv").write_text("keep\n", encoding="utf-8")
    (item_path / "apple_en_02.csv").write_text("keep\n", encoding="utf-8")
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider.delete_item_voice("C_Foods", "I_Apple", "apple_en_02")

    assert not (item_path / "apple_en_02.mp3").exists()
    assert not (item_path / "apple_en_02.csv").exists()
    assert not (item_path / "apple_en.mp3").exists()
    assert not (item_path / "apple_en.csv").exists()
    assert (item_path / "apple_en_01.mp3").read_bytes() == b"other"


@pytest.mark.parametrize("voice_id", ["apple-v-en", "apple_en"])
def test_delete_item_voice_of_the_language_removes_its_identical_variant(tmp_path: Path, voice_id: str) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {
        "apple_en": b"chosen",
        "apple_en_01": b"chosen",
        "apple_en_02": b"other",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        provider.delete_item_voice("C_Foods", "I_Apple", voice_id)

    assert not (item_path / "apple_en.mp3").exists()
    assert not (item_path / "apple_en_01.mp3").exists()
    assert (item_path / "apple_en_02.mp3").read_bytes() == b"other"


def test_delete_item_voice_removes_its_optimized_files_and_keeps_other_takes(tmp_path: Path) -> None:
    content_root, item_path = _prepare_item_with_voices(tmp_path, {
        "apple_en": b"ffmpeg_low:one",  # the main voice is a copy of an optimized file
        "apple_en_01": b"one",
        "apple_en_01_ffmpeg_low": b"ffmpeg_low:one",
        "apple_en_01_pydub_optimized": b"pydub_optimized:one",
        "apple_en_02": b"two",
        "apple_en_02_ffmpeg_low": b"ffmpeg_low:two",
    })
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        for stem, source, optimizer in (
                ("apple_en_01_ffmpeg_low", "apple_en_01", "ffmpeg_low"),
                ("apple_en_01_pydub_optimized", "apple_en_01", "pydub_optimized"),
                ("apple_en_02_ffmpeg_low", "apple_en_02", "ffmpeg_low"),
        ):
            provider._write_voice_metadata(
                item_path / f"{stem}.mp3", VoiceCreationMethod.OPTIMIZED, "openai",
                {"_optimizer": optimizer, "_source": source},
            )
        provider.delete_item_voice("C_Foods", "I_Apple", "apple_en_01")

    assert _mp3s(item_path) == {"apple_en_02": b"two", "apple_en_02_ffmpeg_low": b"ffmpeg_low:two"}


def test_delete_item_voice_unknown_id(tmp_path: Path) -> None:
    content_root, _ = _prepare_item_with_voices(tmp_path, {"apple_en": b"main", "apple_en_01": b"take"})
    provider = make_fs_provider()
    with patch_fs_settings() as mock_settings:
        mock_settings.content_path = content_root
        mock_settings.SUPPORTED_LANGUAGES = ["en"]
        with pytest.raises(CategoryNotFoundError):
            provider.delete_item_voice("C_Foods", "I_Apple", "apple_en_99")
