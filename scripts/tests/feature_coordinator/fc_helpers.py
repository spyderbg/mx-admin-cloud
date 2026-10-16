"""Process and Git helpers shared by the feature coordinator tests (temporary repos only)."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[2]


def git(repo: Path, *args: str) -> str:
    """Run git in *repo* and return its stdout (raises on failure)."""
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


def child_env() -> dict[str, str]:
    """The environment for worker processes: the coordinator package is importable."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SCRIPTS_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    return env


def run_py(code: str, *args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
    """Run *code* in a separate Python process (a separate worker) and wait for it."""
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code), *args], capture_output=True, text=True,
                          env=child_env(), timeout=timeout, check=False)


class Holder:
    """A separate process that runs *code*, prints 'ready' and then waits for stdin to close."""

    def __init__(self, code: str, *args: str) -> None:
        body = textwrap.dedent(code) + "\nimport sys\nprint('ready', flush=True)\nsys.stdin.read()\n"
        self.proc = subprocess.Popen(  # pylint: disable=consider-using-with
            [sys.executable, "-c", body, *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=child_env())
        line = self.proc.stdout.readline().strip() if self.proc.stdout else ""
        if line != "ready":
            err = self.proc.stderr.read() if self.proc.stderr else ""
            self.proc.kill()
            raise RuntimeError(f"holder failed to start: {line!r} {err}")

    @property
    def pid(self) -> int:
        """The holder process ID."""
        return self.proc.pid

    def stop(self) -> None:
        """Let the process exit normally."""
        if self.proc.poll() is None and self.proc.stdin:
            self.proc.stdin.close()
            self.proc.wait(timeout=30)

    def kill(self) -> None:
        """Crash the process (SIGKILL) and reap it."""
        self.proc.kill()
        self.proc.wait(timeout=30)
