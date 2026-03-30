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
from src.services.config_loader import load_service_configs
from src.services.optimization.models import OptimizerConfig


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture()
def config_dir(tmp_path: Path) -> Path:
    """Return a temp dir whose ``optimizers/`` subdirectory is pre-created."""
    (tmp_path / "optimizers").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _write_optimizer_json(
    config_root: Path,
    filename: str,
    provider: str,
    *,
    enabled: bool = True,
    priority: int = 1,
    config: dict | None = None,
) -> Path:
    """Write a correctly-formatted optimizer JSON file."""
    path = config_root / "optimizers" / f"{filename}.json"
    payload = {
        "service_provider": {
            "service": "optimizer",
            "provider": provider,
            "enabled": enabled,
            "priority": priority,
            "config": config or {},
        }
    }
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _make_config(
    name: str = "ffmpeg",
    service_type: str = "optimizer",
    enabled: bool = True,
    priority: int = 1,
    **config_overrides: object,
) -> OptimizerConfig:
    return OptimizerConfig(
        service_type=service_type,
        name=name,
        enabled=enabled,
        priority=priority,
        config={"sample_rate": 22050, "mono": True, "vbr_quality": 9, **config_overrides},
        config_path=Path("test.json"),
    )


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestLoadOptimizerConfigs:
    def test_missing_directory_returns_empty(self, tmp_path: Path) -> None:
        assert load_service_configs(tmp_path / "optimizers", OptimizerConfig) == []

    def test_parses_valid_files(self, config_dir: Path) -> None:
        _write_optimizer_json(config_dir, "fast", "ffmpeg", enabled=True, priority=1,
                              config={"sample_rate": 16000})
        _write_optimizer_json(config_dir, "hq", "pydub", enabled=False, priority=2,
                              config={"vbr_quality": 2})
        configs = load_service_configs(config_dir / "optimizers", OptimizerConfig)
        assert len(configs) == 2

        fast = next(c for c in configs if c.name == "ffmpeg")
        assert fast.service_type == "optimizer"
        assert fast.enabled is True
        assert fast.config["sample_rate"] == 16000
        assert fast.config["mono"] is True       # default merged

        hq = next(c for c in configs if c.name == "pydub")
        assert hq.enabled is False
        assert hq.config["vbr_quality"] == 2

    def test_sorted_by_priority(self, config_dir: Path) -> None:
        _write_optimizer_json(config_dir, "b", "pydub", priority=2)
        _write_optimizer_json(config_dir, "a", "ffmpeg", priority=1)
        configs = load_service_configs(config_dir / "optimizers", OptimizerConfig)
        configs.sort(key=lambda c: c.priority)
        assert configs[0].priority == 1
        assert configs[1].priority == 2

    def test_defaults_merged_when_config_absent(self, config_dir: Path) -> None:
        _write_optimizer_json(config_dir, "bare", "ffmpeg")
        configs = load_service_configs(config_dir / "optimizers", OptimizerConfig)
        assert configs[0].config == {"sample_rate": 22050, "mono": True, "vbr_quality": 9}

    def test_skips_entries_without_provider_or_service(self, config_dir: Path) -> None:
        bad_path = config_dir / "optimizers" / "bad.json"
        bad_path.write_text(json.dumps({"service_provider": {"provider": ""}}))
        _write_optimizer_json(config_dir, "ok", "ffmpeg")
        configs = load_service_configs(config_dir / "optimizers", OptimizerConfig)
        assert len(configs) == 1
        assert configs[0].name == "ffmpeg"

    def test_skips_non_dict_payload(self, config_dir: Path) -> None:
        bad_path = config_dir / "optimizers" / "list.json"
        bad_path.write_text(json.dumps([1, 2, 3]))
        configs = load_service_configs(config_dir / "optimizers", OptimizerConfig)
        assert configs == []

    def test_boolish_enabled_field(self, config_dir: Path) -> None:
        _write_optimizer_json(config_dir, "yes_str", "ffmpeg", enabled="yes")  # type: ignore[arg-type]
        _write_optimizer_json(config_dir, "no_str", "pydub", enabled="no")  # type: ignore[arg-type]
        configs = load_service_configs(config_dir / "optimizers", OptimizerConfig)
        by_name = {c.name: c for c in configs}
        assert by_name["ffmpeg"].enabled is True
        assert by_name["pydub"].enabled is False


# ---------------------------------------------------------------------------
# Factory / builder
# ---------------------------------------------------------------------------

class TestBuildOptimizer:
    def test_ffmpeg_type(self, tmp_path: Path) -> None:
        from src.services.optimization.providers.ffmpeg_optimizer import FfmpegAudioOptimizer
        cfg = _make_config(name="ffmpeg")
        opt = build_optimizer(cfg, tmp_path)
        assert isinstance(opt, FfmpegAudioOptimizer)

    def test_pydub_type(self, tmp_path: Path) -> None:
        from src.services.optimization.providers.pydub_optimizer import PydubAudioOptimizer
        cfg = _make_config(name="pydub")
        opt = build_optimizer(cfg, tmp_path)
        assert isinstance(opt, PydubAudioOptimizer)

    def test_unknown_name_raises(self, tmp_path: Path) -> None:
        cfg = _make_config(name="unknown_optimizer")
        with pytest.raises(ValueError, match="Unsupported optimizer service name"):
            build_optimizer(cfg, tmp_path)

    def test_build_active_filters_inactive(self, tmp_path: Path) -> None:
        configs = [
            _make_config(name="ffmpeg", enabled=True),
            _make_config(name="ffmpeg", enabled=False),
            _make_config(name="ffmpeg", enabled=True),
        ]
        active = build_active_optimizers(configs, tmp_path)
        assert len(active) == 2

    def test_build_active_empty_when_all_inactive(self, tmp_path: Path) -> None:
        configs = [_make_config(name="ffmpeg", enabled=False)]
        assert build_active_optimizers(configs, tmp_path) == []

    def test_optimizer_type_property(self, tmp_path: Path) -> None:
        cfg = _make_config(name="ffmpeg", service_type="optimizer")
        opt = build_optimizer(cfg, tmp_path)
        assert opt.optimizer_type == "optimizer"


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
