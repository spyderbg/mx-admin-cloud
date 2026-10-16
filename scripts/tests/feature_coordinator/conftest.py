"""Fixtures for the feature coordinator tests.

Every test runs against a TEMPORARY Git repository and a temporary state directory;
XDG_STATE_HOME is redirected so the real ~/.local/state is never touched, and the
remote-Qwen environment variables are removed so nothing can point at a real endpoint.
"""

# pylint: disable=redefined-outer-name,missing-function-docstring

from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from feature_coordinator.config import ProjectConfig, default_config
from feature_coordinator.demo import DEMO_SCRIPTS, build_coordinator, init_demo_repo
from feature_coordinator.statedir import ensure_state_dir
from feature_coordinator.store import StateStore
from feature_coordinator.workflow import FeatureCoordinator
from fc_helpers import Holder


@pytest.fixture(autouse=True)
def isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    for name in ("AGENT_QWEN_BASE_URL", "AGENT_QWEN_MODEL", "AGENT_QWEN_API_KEY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def config() -> ProjectConfig:
    return default_config()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    init_demo_repo(path)
    return path


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    return ensure_state_dir(tmp_path / "state")


@pytest.fixture
def store(state_dir: Path) -> StateStore:
    return StateStore(state_dir)


@pytest.fixture
def coord(repo: Path, state_dir: Path) -> FeatureCoordinator:
    return build_coordinator(repo, state_dir, scripts=DEMO_SCRIPTS)


@pytest.fixture
def holders() -> Iterator[list[Holder]]:
    started: list[Holder] = []
    yield started
    for holder in started:
        if holder.proc.poll() is None:
            holder.kill()
