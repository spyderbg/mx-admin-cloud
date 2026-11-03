"""Fixtures for the feature coordinator tests.

Every test runs against a TEMPORARY Git repository and a temporary state directory;
XDG_STATE_HOME is redirected so the real ~/.local/state is never touched, and the
remote-Qwen environment variables are removed so nothing can point at a real endpoint.
"""

# pylint: disable=redefined-outer-name,missing-function-docstring,wrong-import-position

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:  # `feature_coordinator` is importable whatever the working directory
    sys.path.insert(0, str(REPO_ROOT))

from feature_coordinator.config import ProjectConfig, default_config
from feature_coordinator.demo import DEMO_SCRIPTS, build_coordinator, init_demo_repo
from feature_coordinator.statedir import ensure_state_dir
from feature_coordinator.store import StateStore
from feature_coordinator.workflow import FeatureCoordinator
from feature_coordinator.sandbox import SandboxUnavailableError, check_bwrap
from fc_helpers import FAKE_HOME_FILES, FAKE_HOST_LOGIN, Holder, config_with_agents, install_fake_agent


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


@pytest.fixture(scope="session")
def bwrap_ok() -> bool:
    try:
        check_bwrap()
        return True
    except SandboxUnavailableError:
        return False


@pytest.fixture
def needs_bwrap(bwrap_ok: bool) -> None:
    if not bwrap_ok:
        pytest.skip("bubblewrap is not usable on this host")


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """HOME with FAKE login, SSH, GitHub, browser and keyring files (each holding FAKE_HOST_LOGIN)
    — tests never read the real ones."""
    home = tmp_path / "home"
    for rel in FAKE_HOME_FILES:
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text(f'{{"token": "{FAKE_HOST_LOGIN}"}}', encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture
def fake_agent(tmp_path: Path) -> Path:
    return install_fake_agent(tmp_path / "agents")


@pytest.fixture
def agent_config(config: ProjectConfig, fake_agent: Path) -> ProjectConfig:
    return config_with_agents(config, fake_agent)
