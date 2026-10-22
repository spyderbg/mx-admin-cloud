"""Process supervision for sandboxed runners and gates (Phase 3).

`supervise()` starts a command in its own session (process group), feeds it stdin from a
file, writes stdout/stderr to log files and then polls it:

  * every `heartbeat_interval` seconds it calls `heartbeat()` (the claim heartbeat);
  * it stops the command when `cancelled()` becomes true (operator `stop`, or SIGTERM/SIGINT
    delivered to the supervising worker) or when the deadline passes;
  * stopping is safe and complete: SIGTERM to the whole process group, a grace period,
    then SIGKILL. Under bubblewrap the sandbox's PID 1 dies with it, and the kernel kills
    every process left in that PID namespace, so no agent process survives its worker.

If the supervising worker itself is killed (even with SIGKILL), `bwrap --die-with-parent`
ends the sandbox, and the kernel releases the worker's flocks; recovery then reclaims the
task (claims.recover()).
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence

_STOP = threading.Event()
_SIGNAL_NAMES: list[str] = []


def install_termination_handlers() -> None:
    """Make SIGTERM/SIGINT request a graceful stop of the supervised command (worker processes)."""

    def _handler(signum: int, _frame: object) -> None:
        _SIGNAL_NAMES.append(signal.Signals(signum).name)
        _STOP.set()

    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)


def stop_requested() -> bool:
    """True after the worker received SIGTERM or SIGINT."""
    return _STOP.is_set()


@dataclass(frozen=True)
class Supervision:
    """The outcome of one supervised command."""

    status: str  # exited | timeout | cancelled
    exit_code: int | None
    duration_seconds: float
    pid: int
    escalated_to_kill: bool = False
    heartbeats: int = 0

    @property
    def ok(self) -> bool:
        """True when the command exited by itself with status 0."""
        return self.status == "exited" and self.exit_code == 0


def terminate_group(proc: subprocess.Popen, grace_seconds: float) -> bool:
    """SIGTERM the process group, then SIGKILL after *grace_seconds*. Returns True if SIGKILL was needed."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    try:
        proc.wait(timeout=grace_seconds)
        return False
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()
    return True


def tagged_processes(name: str, value: str) -> list[int]:
    """PIDs of this user's processes whose initial environment holds NAME=value (W2, native runs).
    Best effort: a process that cleared its environment before exec is not found."""
    needle = f"{name}={value}".encode()
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit() or int(entry) == os.getpid():
            continue
        try:
            with open(f"/proc/{entry}/environ", "rb") as handle:
                if needle in handle.read().split(b"\0"):
                    found.append(int(entry))
        except OSError:
            continue
    return found


def kill_tagged(name: str, value: str) -> list[int]:
    """SIGKILL every process tagged NAME=value (descendants that left the process group, e.g. a
    daemon that called setsid). Returns the PIDs signalled."""
    killed = []
    for pid in tagged_processes(name, value):
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(pid)
        except (ProcessLookupError, PermissionError):
            pass
    return killed


def supervise(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str] | None, stdout_path: Path,  # pylint: disable=too-many-arguments,too-many-locals
              stderr_path: Path, stdin_path: Path | None = None, timeout_seconds: float,
              heartbeat: Callable[[], None] | None = None, heartbeat_interval: float = 10.0,
              cancelled: Callable[[], bool] | None = None, grace_seconds: float = 10.0,
              poll_interval: float = 0.1, on_start: Callable[[int], None] | None = None) -> Supervision:
    """Run *argv* under supervision. See the module docstring."""
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err, \
            (open(stdin_path, "rb") if stdin_path else open(os.devnull, "rb")) as stdin:
        start = time.monotonic()
        proc = subprocess.Popen(list(argv), cwd=cwd, env=dict(env) if env is not None else None,  # pylint: disable=consider-using-with
                                stdin=stdin, stdout=out, stderr=err, start_new_session=True)
        if on_start:
            on_start(proc.pid)
        deadline = start + timeout_seconds
        next_beat = start + heartbeat_interval
        beats = 0
        status = "exited"
        while proc.poll() is None:
            now = time.monotonic()
            if heartbeat and now >= next_beat:
                heartbeat()
                beats += 1
                next_beat = now + heartbeat_interval
            if (cancelled and cancelled()) or stop_requested():
                status = "cancelled"
                break
            if now >= deadline:
                status = "timeout"
                break
            time.sleep(poll_interval)
        escalated = False
        if proc.poll() is None:
            escalated = terminate_group(proc, grace_seconds)
        else:
            # The leader exited; make sure nothing it left behind in its group keeps running.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        return Supervision(status=status, exit_code=proc.returncode, duration_seconds=time.monotonic() - start,
                           pid=proc.pid, escalated_to_kill=escalated, heartbeats=beats)
