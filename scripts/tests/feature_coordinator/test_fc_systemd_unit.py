"""W4.5.2: `feature.py worker systemd-unit` keeps the explicitly selected global options in ExecStart.

The state directory, the project configuration (`systemd-unit --config`, else the global
`--project-config`) and the tracker reach ExecStart as global options before `worker start`;
without them the unit is unchanged. The unit is only rendered (printed) and checked with
`systemd-analyze --user verify` where the host has a user manager; nothing is installed, enabled
or started. The rendered ExecStart is parsed with the real CLI parser and run through
`worker start` with the service replaced by a recorder, so no agent, model or GitHub connection
is involved."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator.cli import EXAMPLE_CONFIG, build_parser, main as cli_main

CONFIG = EXAMPLE_CONFIG.resolve()
NOTE = "note: without --config (or the global --project-config)"

# id: (global options given with `systemd-unit`, its --config, global options expected in ExecStart).
# Run from tmp_path: "state" is relative there; {state} is its absolute path, {config_rel} the
# example configuration spelled relative to tmp_path.
CASES: dict[str, tuple[tuple[str, ...], str | None, tuple[str, ...]]] = {
    "default": ((), "{config}", ("--project-config", "{config}")),
    "local": (("--tracker", "local"), "{config}", ("--project-config", "{config}", "--tracker", "local")),
    "github": (("--tracker", "github"), "{config}", ("--project-config", "{config}", "--tracker", "github")),
    "inherited-config": (("--project-config", "{config}"), None, ("--project-config", "{config}")),
    "same-config-twice": (("--project-config", "{config}"), "{config_rel}", ("--project-config", "{config}")),
    "state-dir": (("--state-dir", "state"), "{config}", ("--state-dir", "{state}", "--project-config", "{config}")),
    "no-config": (("--tracker", "github", "--state-dir", "state"), None,
                  ("--state-dir", "{state}", "--tracker", "github")),
    "everything": (("--tracker", "github", "--project-config", "{config}", "--state-dir", "state"), None,
                   ("--state-dir", "{state}", "--project-config", "{config}", "--tracker", "github")),
}


def render(capsys: pytest.CaptureFixture[str], repo: Path, *global_args: str,
           config: str | None = str(CONFIG)) -> tuple[str, str]:
    unit_args = ["--config", config] if config else []
    assert cli_main(["--repo", str(repo), *global_args, "worker", "systemd-unit", *unit_args]) == 0
    captured = capsys.readouterr()
    return captured.out, captured.err


def exec_start(unit: str) -> list[str]:
    lines = [line for line in unit.splitlines() if line.startswith("ExecStart=")]
    assert len(lines) == 1
    return shlex.split(lines[0].removeprefix("ExecStart="))


@pytest.fixture
def recorded_service(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """`worker start` builds this recorder instead of the real service (no handlers installed)."""
    seen: dict[str, Any] = {}

    class Recorder:  # pylint: disable=too-few-public-methods
        """Records the service's arguments; runs nothing."""

        def __init__(self, _config: Any, repo: Path, state_dir: Path, **kwargs: Any) -> None:
            seen.update(kwargs, repo=repo, state_dir=state_dir)

        def run(self) -> int:
            return 0
    monkeypatch.setattr("feature_coordinator.service.WorkerService", Recorder)
    monkeypatch.setattr("feature_coordinator.supervisor.install_termination_handlers", lambda: None)
    monkeypatch.setattr("feature_coordinator.supervisor.install_wake_handler", lambda: None)
    return seen


@pytest.mark.parametrize("given, config, expected", CASES.values(), ids=CASES.keys())
def test_exec_start_keeps_the_selected_global_options(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
        recorded_service: dict[str, Any], given: tuple[str, ...], config: str | None,
        expected: tuple[str, ...]) -> None:
    monkeypatch.chdir(tmp_path)
    repo, state = tmp_path / "repo", (tmp_path / "state").resolve()
    values = {"config": str(CONFIG), "config_rel": os.path.relpath(CONFIG, tmp_path), "state": str(state)}
    unit, err = render(capsys, repo, *(arg.format(**values) for arg in given),
                       config=config.format(**values) if config else None)
    expected = tuple(arg.format(**values) for arg in expected)
    want = dict(zip(expected[::2], expected[1::2]))
    assert not re.search(r"@[A-Z_]+@", unit)  # every placeholder was filled
    assert (NOTE in err) is ("--project-config" not in want)
    argv = exec_start(unit)
    assert argv[:2] == [os.path.abspath(sys.executable), f"{repo.resolve()}/scripts/feature.py"]
    options = argv[2:]
    # Global options first, in the parser's order, then the subcommand: argparse rejects them after `worker start`.
    assert options == ["--repo", str(repo.resolve()), *expected, "worker", "start"]
    parsed = build_parser().parse_args(options)
    assert (parsed.command, parsed.worker_command) == ("worker", "start")
    assert (parsed.state_dir, parsed.project_config, parsed.tracker) == (
        want.get("--state-dir"), want.get("--project-config"), want.get("--tracker"))
    assert cli_main(options) == 0
    assert recorded_service["repo"] == repo.resolve()
    assert recorded_service["tracker"] == want.get("--tracker", "local")
    assert recorded_service["project_config"] == want.get("--project-config")
    if "--state-dir" in want:
        assert recorded_service["state_dir"] == state
    else:  # the default (D-12) location, under the XDG_STATE_HOME the test redirected
        assert recorded_service["state_dir"].is_relative_to(tmp_path / "xdg-state")


def test_exec_start_is_unchanged_without_options(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo, python = tmp_path / "repo", os.path.abspath(sys.executable)
    script = f"{python} {repo.resolve()}/scripts/feature.py --repo {repo.resolve()}"
    unit, err = render(capsys, repo)
    assert f"\nExecStart={script} --project-config {CONFIG} worker start\n" in unit and not err
    unit, err = render(capsys, repo, config=None)
    assert f"\nExecStart={script} worker start\n" in unit and err.startswith(NOTE)


def test_conflicting_project_configurations_are_refused(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    other = tmp_path / "project.yaml"
    shutil.copyfile(CONFIG, other)  # the same content, but another file: still a conflict
    code = cli_main(["--repo", str(tmp_path / "repo"), "--project-config", str(other),
                     "worker", "systemd-unit", "--config", str(CONFIG)])
    captured = capsys.readouterr()
    assert code == 1 and captured.out == ""  # no unit is printed
    assert captured.err == (f"error: conflicting project configurations: `systemd-unit --config {CONFIG}` and the "
                            f"global `--project-config {other.resolve()}`; give only one of them, or the same "
                            "file to both\n")


@pytest.mark.skipif(not shutil.which("systemd-analyze") or not os.environ.get("XDG_RUNTIME_DIR"),
                    reason="systemd-analyze --user needs systemd and a user manager")
@pytest.mark.parametrize("given, config", [((), None), CASES["everything"][:2]], ids=["bare", "everything"])
def test_systemd_analyze_verifies_the_rendered_unit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                    capsys: pytest.CaptureFixture[str], given: tuple[str, ...],
                                                    config: str | None) -> None:
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    unit, _err = render(capsys, repo, *(arg.format(config=CONFIG) for arg in given), config=config)
    path = tmp_path / "mx-admin-worker.service"  # verified where it is: never copied into a unit directory
    path.write_text(unit + "\n", encoding="utf-8")
    result = subprocess.run(["systemd-analyze", "--user", "verify", str(path)], capture_output=True, text=True,
                            timeout=60, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
