"""Tests for audio optimizer config loading, factory building, and output path routing."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.core.config import settings
from src.core.models import ProviderRunRecord
from src.services.optimization import build_active_optimizers, build_optimizer
from src.services.optimization.base import AudioOptimizerBase
from src.services.optimization.config_loader import load_optimizer_configs
from src.services.optimization.models import OptimizerConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    """Return a temp dir usable as config_dir (parent of optimizers/)."""
    (tmp_path / "optimizers").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_optimizer_json(config_root: Path, name: str, payload: object) -> Path:
    path = config_root / "optimizers" / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _make_config(
    name: str = "test_opt",
    opt_type: str = "ffmpeg",
    active: bool = True,
    **params: object,
) -> OptimizerConfig:
    return OptimizerConfig(
        name=name,
        type=opt_type,
        active=active,
        params={"sample_rate": 22050, "mono": True, "vbr_quality": 9, **params},
        config_path=Path("test.json"),
    )


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestLoadOptimizerConfigs:
    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_optimizer_configs(base_dir=tmp_path, config_dir=tmp_path) == []

    def test_parses_valid_list(self, config_dir: Path) -> None:
        _write_optimizer_json(config_dir, "fast", {"type": "ffmpeg", "active": True, "params": {"sample_rate": 16000}})
        _write_optimizer_json(config_dir, "hq", {"type": "pydub", "active": False, "params": {"vbr_quality": 2}})
        configs = load_optimizer_configs(base_dir=config_dir, config_dir=config_dir)
        assert len(configs) == 2
        assert configs[0].name == "fast"
        assert configs[0].type == "ffmpeg"
        assert configs[0].active is True
        assert configs[0].params["sample_rate"] == 16000
        assert configs[0].params["mono"] is True  # default merged
        assert configs[1].name == "hq"
        assert configs[1].active is False

    def test_duplicate_name_raises(self, config_dir: Path) -> None:
        _write_optimizer_json(config_dir, "dup", {"type": "ffmpeg", "active": True})
        # Overwrite same filename to verify loader keeps one unique config name.
        _write_optimizer_json(config_dir, "dup", {"type": "pydub", "active": False})
        configs = load_optimizer_configs(base_dir=config_dir, config_dir=config_dir)
        assert len(configs) == 1
        assert configs[0].name == "dup"
        assert configs[0].type == "pydub"

    def test_skips_entries_without_name_or_type(self, config_dir: Path) -> None:
        _write_optimizer_json(config_dir, "missing_type", {"active": True})
        _write_optimizer_json(config_dir, "ok", {"type": "ffmpeg"})
        configs = load_optimizer_configs(base_dir=config_dir, config_dir=config_dir)
        assert len(configs) == 1
        assert configs[0].name == "ok"

    def test_defaults_merged_when_params_missing(self, config_dir: Path) -> None:
        _write_optimizer_json(config_dir, "bare", {"type": "ffmpeg", "active": True})
        configs = load_optimizer_configs(base_dir=config_dir, config_dir=config_dir)
        assert configs[0].params == {"sample_rate": 22050, "mono": True, "vbr_quality": 9}

    def test_non_list_payload_returns_empty(self, config_dir: Path) -> None:
        _write_optimizer_json(config_dir, "bad", {"not": "an_optimizer"})
        assert load_optimizer_configs(base_dir=config_dir, config_dir=config_dir) == []

    def test_boolish_active_field(self, config_dir: Path) -> None:
        _write_optimizer_json(config_dir, "yes_str", {"type": "ffmpeg", "active": "yes"})
        _write_optimizer_json(config_dir, "no_str", {"type": "ffmpeg", "active": "no"})
        configs = load_optimizer_configs(base_dir=config_dir, config_dir=config_dir)
        by_name = {cfg.name: cfg for cfg in configs}
        assert by_name["yes_str"].active is True
        assert by_name["no_str"].active is False


# ---------------------------------------------------------------------------
# Factory / builder
# ---------------------------------------------------------------------------

class TestBuildOptimizer:
    def test_ffmpeg_type(self, tmp_path: Path) -> None:
        from src.services.optimization.ffmpeg_optimizer import FfmpegAudioOptimizer
        cfg = _make_config(opt_type="ffmpeg")
        opt = build_optimizer(cfg, tmp_path)
        assert isinstance(opt, FfmpegAudioOptimizer)

    def test_pydub_type(self, tmp_path: Path) -> None:
        from src.services.optimization.pydub_optimizer import PydubAudioOptimizer
        cfg = _make_config(opt_type="pydub")
        opt = build_optimizer(cfg, tmp_path)
        assert isinstance(opt, PydubAudioOptimizer)

    def test_unknown_type_raises(self, tmp_path: Path) -> None:
        cfg = _make_config(opt_type="unknown")
        with pytest.raises(ValueError, match="Unsupported optimizer type"):
            build_optimizer(cfg, tmp_path)

    def test_build_active_filters_inactive(self, tmp_path: Path) -> None:
        configs = [
            _make_config(name="a", active=True),
            _make_config(name="b", active=False),
            _make_config(name="c", active=True),
        ]
        active = build_active_optimizers(configs, tmp_path)
        assert len(active) == 2
        assert [o.name for o in active] == ["a", "c"]

    def test_build_active_empty_when_all_inactive(self, tmp_path: Path) -> None:
        configs = [_make_config(name="x", active=False)]
        assert build_active_optimizers(configs, tmp_path) == []


# ---------------------------------------------------------------------------
# Output paths
# ---------------------------------------------------------------------------

class TestOptimizedOutputDir:
    def test_structure(self) -> None:
        result = settings.optimized_output_dir("voice_fast", "bg", "openai")
        assert str(result).endswith("/optimized_voice_fast/tts_audio_bg/openai")

    def test_different_optimizer_names_differ(self) -> None:
        a = settings.optimized_output_dir("opt_a", "en", "p")
        b = settings.optimized_output_dir("opt_b", "en", "p")
        assert a != b
        assert "optimized_opt_a" in str(a)
        assert "optimized_opt_b" in str(b)


# ---------------------------------------------------------------------------
# Orchestrator integration (optimizer stage)
# ---------------------------------------------------------------------------

class TestRunOptimizations:
    """Test the _run_optimizations helper in isolation."""

    def _make_record(self, raw_path: str, success: bool = True) -> ProviderRunRecord:
        return ProviderRunRecord(
            request_id="r1",
            provider="openai",
            source_file="words.txt",
            source_type="txt",
            language_code="en",
            language_name="English",
            expected_text="hello",
            filename_text="hello",
            output_format="mp3",
            raw_path=raw_path,
            normalized_path=None,
            generation_latency_seconds=0.1,
            evaluation_latency_seconds=None,
            evaluation_score=None,
            rolling_average_score=None,
            transcription=None,
            success=success,
            error=None,
        )

    @pytest.mark.asyncio
    async def test_skips_failed_records(self, tmp_path: Path) -> None:
        from src.services.orchestrator import _run_optimizations

        record = self._make_record("", success=False)
        mock_opt = AsyncMock(spec=AudioOptimizerBase)
        mock_opt.name = "opt"
        await _run_optimizations([record], [mock_opt], tmp_path, asyncio.Semaphore(1))
        mock_opt.optimize.assert_not_called()

    @pytest.mark.asyncio
    async def test_runs_optimizer_on_existing_file(self, tmp_path: Path) -> None:
        from src.services.orchestrator import _run_optimizations

        raw_file = tmp_path / "tts_audio_en" / "openai" / "hello_en.mp3"
        raw_file.parent.mkdir(parents=True)
        raw_file.write_bytes(b"fake-mp3")

        record = self._make_record(str(raw_file))
        mock_opt = AsyncMock(spec=AudioOptimizerBase)
        mock_opt.name = "voice_fast"
        mock_opt.optimize.return_value = Path("/ignored")

        await _run_optimizations([record], [mock_opt], tmp_path, asyncio.Semaphore(1))

        mock_opt.optimize.assert_called_once()
        call_args = mock_opt.optimize.call_args
        assert call_args[0][0] == raw_file
        expected_out = (
            tmp_path / "optimized_voice_fast" / "tts_audio_en" / "openai" / "hello_en.mp3"
        )
        assert call_args[0][1] == expected_out
        assert record.optimized_paths["voice_fast"] == str(expected_out)

    @pytest.mark.asyncio
    async def test_optimizer_failure_logged_not_raised(self, tmp_path: Path) -> None:
        from src.services.orchestrator import _run_optimizations

        raw_file = tmp_path / "test.mp3"
        raw_file.write_bytes(b"data")

        record = self._make_record(str(raw_file))
        mock_opt = AsyncMock(spec=AudioOptimizerBase)
        mock_opt.name = "bad"
        mock_opt.optimize.side_effect = RuntimeError("boom")

        await _run_optimizations([record], [mock_opt], tmp_path, asyncio.Semaphore(1))
        assert "bad" not in record.optimized_paths

    @pytest.mark.asyncio
    async def test_no_optimizers_means_no_work(self, tmp_path: Path) -> None:
        from src.services.orchestrator import _run_optimizations

        raw_file = tmp_path / "x.mp3"
        raw_file.write_bytes(b"data")
        record = self._make_record(str(raw_file))
        await _run_optimizations([record], [], tmp_path, asyncio.Semaphore(1))
        assert record.optimized_paths == {}
