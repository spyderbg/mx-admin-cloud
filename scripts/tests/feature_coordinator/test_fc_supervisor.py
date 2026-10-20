"""Heartbeat, timeout, cancellation and safe termination of sandboxed processes."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from feature_coordinator.config import ProjectConfig
from feature_coordinator.demo import init_demo_repo
from feature_coordinator.sandbox import SandboxBuilder
from feature_coordinator.supervisor import supervise

from fc_helpers import child_env


def sh(tmp: Path, script: str, **kwargs):
    return supervise(["/bin/sh", "-c", script], cwd=tmp, env=None, stdout_path=tmp / "out", stderr_path=tmp / "err",
                     **kwargs)


def grows(path: Path, wait: float = 0.6) -> bool:
    before = path.stat().st_size if path.exists() else -1
    time.sleep(wait)
    return (path.stat().st_size if path.exists() else -1) != before


def test_exit_status_and_logs(tmp_path: Path) -> None:
    sup = sh(tmp_path, "echo out; echo err >&2; exit 4", timeout_seconds=10)
    assert (sup.status, sup.exit_code, sup.ok) == ("exited", 4, False)
    assert (tmp_path / "out").read_text() == "out\n" and (tmp_path / "err").read_text() == "err\n"


def test_heartbeats_are_sent_while_running(tmp_path: Path) -> None:
    beats: list[float] = []
    sup = sh(tmp_path, "sleep 1", timeout_seconds=10, heartbeat=lambda: beats.append(time.monotonic()),
             heartbeat_interval=0.2)
    assert sup.ok and sup.heartbeats >= 3 and len(beats) == sup.heartbeats


def test_timeout_kills_the_process_group(tmp_path: Path) -> None:
    start = time.monotonic()
    sup = sh(tmp_path, "sleep 30 & sleep 30; wait", timeout_seconds=0.5, grace_seconds=1)
    assert sup.status == "timeout" and time.monotonic() - start < 5


def test_cancellation_is_honored_quickly(tmp_path: Path) -> None:
    deadline = time.monotonic() + 0.4
    sup = sh(tmp_path, "sleep 30", timeout_seconds=30, cancelled=lambda: time.monotonic() > deadline)
    assert sup.status == "cancelled" and sup.duration_seconds < 3 and not sup.escalated_to_kill


def test_sigterm_ignoring_process_is_killed_after_the_grace_period(tmp_path: Path) -> None:
    sup = sh(tmp_path, "trap '' TERM; while true; do sleep 0.05; done", timeout_seconds=0.3, grace_seconds=0.5)
    assert sup.status == "timeout" and sup.escalated_to_kill


@pytest.fixture
def sandbox_repo(tmp_path: Path) -> Path:
    path = tmp_path / "r"
    init_demo_repo(path)
    return path


DAEMON = ("import os, sys, time\n"
          "if os.fork() == 0:\n"
          "    os.setsid()\n"  # escapes the process group: only the PID namespace can stop it
          "    while True:\n"
          "        open('backend/beat.txt', 'a').write('.')\n"
          "        time.sleep(0.05)\n"
          "import signal\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
          "while True:\n    time.sleep(1)\n")


@pytest.mark.usefixtures("needs_bwrap")
def test_cancel_stops_every_process_in_the_sandbox(sandbox_repo: Path, config: ProjectConfig, tmp_path: Path) -> None:
    plan = SandboxBuilder(sandbox_repo, config).plan(run_dir=tmp_path / "run", role="backend")
    started = time.monotonic()
    sup = supervise(plan.argv(["/usr/bin/python3", "-c", DAEMON]), cwd=sandbox_repo, env={"PATH": os.environ["PATH"]},
                    stdout_path=tmp_path / "o", stderr_path=tmp_path / "e", timeout_seconds=30, grace_seconds=0.5,
                    cancelled=lambda: time.monotonic() - started > 1.0)
    beat = sandbox_repo / "backend/beat.txt"
    assert sup.status == "cancelled" and beat.exists()
    assert not grows(beat), "a detached grandchild survived the sandbox"


WORKER = """
import os, sys
from pathlib import Path
from feature_coordinator.config import default_config
from feature_coordinator.sandbox import SandboxBuilder
from feature_coordinator.supervisor import supervise
repo, run = Path(sys.argv[1]), Path(sys.argv[2])
plan = SandboxBuilder(repo, default_config()).plan(run_dir=run, role="backend")
print("ready", flush=True)
supervise(plan.argv(["/usr/bin/python3", "-c", sys.argv[3]]), cwd=repo, env={"PATH": os.environ["PATH"]},
          stdout_path=run / "o", stderr_path=run / "e", timeout_seconds=60)
"""


@pytest.mark.usefixtures("needs_bwrap")
def test_killing_the_worker_kills_its_sandbox(sandbox_repo: Path, tmp_path: Path) -> None:
    """A SIGKILLed worker cannot clean up — bwrap --die-with-parent and the PID namespace do."""
    proc = subprocess.Popen([sys.executable, "-c", WORKER, str(sandbox_repo), str(tmp_path / "run"), DAEMON],  # pylint: disable=consider-using-with
                            stdout=subprocess.PIPE, text=True, env=child_env())
    assert proc.stdout.readline().strip() == "ready"
    beat = sandbox_repo / "backend/beat.txt"
    for _ in range(100):
        if beat.exists():
            break
        time.sleep(0.05)
    assert grows(beat, 0.3)
    os.kill(proc.pid, signal.SIGKILL)
    proc.wait(timeout=10)
    time.sleep(0.3)
    assert not grows(beat), "the sandbox outlived its killed worker"
