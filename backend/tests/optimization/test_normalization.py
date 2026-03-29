"""Unit tests for audio normalization (optimization service)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.models import GeneratedAudio
from src.services.optimization.normalizer import normalize_audio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_generated_audio(
    tmp_path: Path,
    provider: str = "openai",
    language_code: str = "en",
    success: bool = True,
    raw_path: Path | None = None,
) -> GeneratedAudio:
    if raw_path is None:
        raw_path = tmp_path / "hello_en.mp3"
        raw_path.write_bytes(b"fake-audio")
    return GeneratedAudio(
        provider=provider,
        request_id="test-001",
        language_code=language_code,
        language_name="English",
        expected_text="hello",
        filename_text="hello",
        output_format="mp3",
        raw_path=raw_path,
        raw_extension="mp3",
        latency_seconds=0.1,
        success=success,
    )


def _mock_process(returncode: int = 0, stderr: bytes = b"") -> MagicMock:
    """Build a mock subprocess that returns *returncode* and *stderr*."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", stderr))
    return proc


# ---------------------------------------------------------------------------
# normalize_audio — ffmpeg not found
# ---------------------------------------------------------------------------

class TestNormalizeAudioNoFfmpeg:
    @pytest.mark.asyncio
    async def test_sets_normalization_error(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        with patch("shutil.which", return_value=None):
            result = await normalize_audio(tmp_path, audio)
        assert result.normalization_error == "ffmpeg not found in PATH."

    @pytest.mark.asyncio
    async def test_normalized_path_remains_none(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        with patch("shutil.which", return_value=None):
            result = await normalize_audio(tmp_path, audio)
        assert result.normalized_path is None

    @pytest.mark.asyncio
    async def test_returns_same_object(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        with patch("shutil.which", return_value=None):
            result = await normalize_audio(tmp_path, audio)
        assert result is audio


# ---------------------------------------------------------------------------
# normalize_audio — ffmpeg succeeds (returncode == 0)
# ---------------------------------------------------------------------------

class TestNormalizeAudioSuccess:
    @pytest.mark.asyncio
    async def test_sets_normalized_path(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_mock_process(0))),
        ):
            result = await normalize_audio(tmp_path, audio)
        assert result.normalized_path is not None

    @pytest.mark.asyncio
    async def test_normalized_path_is_wav(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_mock_process(0))),
        ):
            result = await normalize_audio(tmp_path, audio)
        assert result.normalized_path.suffix == ".wav"

    @pytest.mark.asyncio
    async def test_normalization_error_is_none(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_mock_process(0))),
        ):
            result = await normalize_audio(tmp_path, audio)
        assert result.normalization_error is None

    @pytest.mark.asyncio
    async def test_output_dir_created(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_mock_process(0))),
        ):
            result = await normalize_audio(tmp_path, audio)
        assert result.normalized_path.parent.exists()

    @pytest.mark.asyncio
    async def test_output_path_contains_language_and_provider(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path, provider="elevenlabs", language_code="fr")
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_mock_process(0))),
        ):
            result = await normalize_audio(tmp_path, audio)
        path_str = str(result.normalized_path)
        assert "fr" in path_str
        assert "elevenlabs" in path_str

    @pytest.mark.asyncio
    async def test_returns_same_object(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_mock_process(0))),
        ):
            result = await normalize_audio(tmp_path, audio)
        assert result is audio


# ---------------------------------------------------------------------------
# normalize_audio — ffmpeg fails (returncode != 0)
# ---------------------------------------------------------------------------

class TestNormalizeAudioFailure:
    @pytest.mark.asyncio
    async def test_sets_normalization_error_from_stderr(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch(
                "asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=_mock_process(1, b"Error: invalid input")),
            ),
        ):
            result = await normalize_audio(tmp_path, audio)
        assert result.normalization_error == "Error: invalid input"

    @pytest.mark.asyncio
    async def test_normalized_path_remains_none(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_mock_process(1, b"err"))),
        ):
            result = await normalize_audio(tmp_path, audio)
        assert result.normalized_path is None

    @pytest.mark.asyncio
    async def test_returns_same_object(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_mock_process(1, b"err"))),
        ):
            result = await normalize_audio(tmp_path, audio)
        assert result is audio

    @pytest.mark.asyncio
    async def test_stderr_whitespace_stripped(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch(
                "asyncio.create_subprocess_exec",
                new=AsyncMock(return_value=_mock_process(1, b"  stderr line  \n")),
            ),
        ):
            result = await normalize_audio(tmp_path, audio)
        assert result.normalization_error == "stderr line"


# ---------------------------------------------------------------------------
# normalize_audio — ffmpeg invocation args
# ---------------------------------------------------------------------------

class TestNormalizeAudioFfmpegArgs:
    @pytest.mark.asyncio
    async def test_called_with_overwrite_flag(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        mock_exec = AsyncMock(return_value=_mock_process(0))
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", new=mock_exec),
        ):
            await normalize_audio(tmp_path, audio)
        args = mock_exec.call_args[0]
        assert "-y" in args

    @pytest.mark.asyncio
    async def test_called_with_input_path(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        mock_exec = AsyncMock(return_value=_mock_process(0))
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", new=mock_exec),
        ):
            await normalize_audio(tmp_path, audio)
        args = mock_exec.call_args[0]
        assert str(audio.raw_path) in args

    @pytest.mark.asyncio
    async def test_called_with_mono_channel(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        mock_exec = AsyncMock(return_value=_mock_process(0))
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", new=mock_exec),
        ):
            await normalize_audio(tmp_path, audio)
        args = mock_exec.call_args[0]
        idx = list(args).index("-ac")
        assert args[idx + 1] == "1"

    @pytest.mark.asyncio
    async def test_called_with_22050_sample_rate(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        mock_exec = AsyncMock(return_value=_mock_process(0))
        with (
            patch("shutil.which", return_value="/usr/bin/ffmpeg"),
            patch("asyncio.create_subprocess_exec", new=mock_exec),
        ):
            await normalize_audio(tmp_path, audio)
        args = mock_exec.call_args[0]
        idx = list(args).index("-ar")
        assert args[idx + 1] == "22050"

    @pytest.mark.asyncio
    async def test_uses_resolved_ffmpeg_path(self, tmp_path: Path) -> None:
        audio = _make_generated_audio(tmp_path)
        custom_ffmpeg = "/opt/ffmpeg/bin/ffmpeg"
        mock_exec = AsyncMock(return_value=_mock_process(0))
        with (
            patch("shutil.which", return_value=custom_ffmpeg),
            patch("asyncio.create_subprocess_exec", new=mock_exec),
        ):
            await normalize_audio(tmp_path, audio)
        args = mock_exec.call_args[0]
        assert args[0] == custom_ffmpeg
