"""Local `flock(2)` locks and process identity (D-3, v5 §3.2).

Lock files live in `<state dir>/locks/`, outside Git. The kernel releases a flock when
the holding open file description is closed — including when the process dies — so a
crashed worker never leaves a lock behind. Lock order (always acquired in this order,
never the reverse, so waits cannot deadlock):

    coordinator.lock  (short critical sections: claims and state changes)
      → project.lock  (SHARED for every scoped writer, EXCLUSIVE for full-project work and
                       working-tree-changing Git operations)
        → role-<name>.lock  (EXCLUSIVE: one active writer per specialization)
          → git.lock  (EXCLUSIVE: every Git mutation)

A process identity is (pid, kernel start time, boot id): a recycled PID or a reboot can
never make a dead worker look alive.
"""

from __future__ import annotations

import fcntl
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, IO

from .errors import LockBusyError

_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")


def _boot_id() -> str:
    try:
        return _BOOT_ID_PATH.read_text(encoding="ascii").strip()
    except OSError:
        return "unknown"


def _proc_stat(pid: int) -> tuple[str, int] | None:
    """(state, start_ticks) from /proc/<pid>/stat, or None if the process does not exist."""
    try:
        raw = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace")
    except OSError:
        return None
    # The command name (field 2) may contain spaces and parentheses; fields resume after the last ')'.
    rest = raw[raw.rfind(")") + 2:].split()
    if len(rest) < 20:
        return None
    return rest[0], int(rest[19])


@dataclass(frozen=True)
class ProcessIdentity:
    """A process, robust against PID reuse and reboots."""

    pid: int
    start_ticks: int
    boot_id: str

    @classmethod
    def current(cls) -> "ProcessIdentity":
        """The identity of the calling process."""
        identity = cls.of_pid(os.getpid())
        if identity is None:  # pragma: no cover - /proc always has the current process on Linux
            raise LockBusyError("cannot read /proc for the current process")
        return identity

    @classmethod
    def of_pid(cls, pid: int) -> "ProcessIdentity | None":
        """The identity of a running *pid*, or None if it is not running."""
        stat_info = _proc_stat(pid)
        if stat_info is None:
            return None
        return cls(pid=pid, start_ticks=stat_info[1], boot_id=_boot_id())

    def is_alive(self) -> bool:
        """True only if the same process (not a recycled PID) is still running and not a zombie."""
        if self.boot_id != _boot_id():
            return False
        stat_info = _proc_stat(self.pid)
        if stat_info is None:
            return False
        state, start_ticks = stat_info
        return start_ticks == self.start_ticks and state not in ("Z", "X", "x")

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable form."""
        return {"pid": self.pid, "start_ticks": self.start_ticks, "boot_id": self.boot_id}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProcessIdentity":
        """Inverse of to_dict()."""
        return cls(pid=int(data["pid"]), start_ticks=int(data["start_ticks"]), boot_id=str(data["boot_id"]))


class FileLock:
    """One flock on one lock file. Each instance opens its own file description, so two
    instances conflict even inside one process — exactly like two processes."""

    def __init__(self, path: Path, *, shared: bool = False, busy_error: type[LockBusyError] = LockBusyError) -> None:
        self.path = path
        self.shared = shared
        self.busy_error = busy_error
        self._handle: IO[str] | None = None

    @property
    def held(self) -> bool:
        """True while this instance holds the lock."""
        return self._handle is not None

    def acquire(self, timeout: float = 0.0, poll_interval: float = 0.02) -> "FileLock":
        """Acquire within *timeout* seconds (0 = try once). Raises busy_error when busy."""
        if self._handle is not None:
            raise LockBusyError(f"{self.path.name} is already held by this lock object")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+", encoding="utf-8")  # pylint: disable=consider-using-with
        mode = (fcntl.LOCK_SH if self.shared else fcntl.LOCK_EX) | fcntl.LOCK_NB
        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            try:
                fcntl.flock(handle, mode)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    handle.close()
                    kind = "shared" if self.shared else "exclusive"
                    raise self.busy_error(f"{self.path.name} is busy (could not take it {kind})") from None
                time.sleep(poll_interval)
        self._handle = handle
        return self

    def release(self) -> None:
        """Release the lock (idempotent)."""
        if self._handle is not None:
            try:
                fcntl.flock(self._handle, fcntl.LOCK_UN)
            finally:
                self._handle.close()
                self._handle = None

    def __enter__(self) -> "FileLock":
        if not self.held:
            self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class LockSet:
    """Factory for the coordinator's named locks inside one locks directory."""

    def __init__(self, locks_dir: Path) -> None:
        self.dir = locks_dir

    def coordinator(self) -> FileLock:
        """Short critical sections around claims and state changes."""
        return FileLock(self.dir / "coordinator.lock")

    def project(self, *, shared: bool, busy_error: type[LockBusyError] = LockBusyError) -> FileLock:
        """Shared by scoped writers; exclusive for full-project and tree-changing Git operations."""
        return FileLock(self.dir / "project.lock", shared=shared, busy_error=busy_error)

    def role(self, role: str, busy_error: type[LockBusyError] = LockBusyError) -> FileLock:
        """One active writer per role."""
        return FileLock(self.dir / f"role-{role}.lock", busy_error=busy_error)

    def git(self) -> FileLock:
        """Every Git mutation."""
        return FileLock(self.dir / "git.lock")

    def worker(self, feature_id: str) -> FileLock:
        """One Worker per feature (W2, worker.py). Held for the whole Worker run; it is NOT part
        of the lock order above — the Worker takes no other lock itself."""
        return FileLock(self.dir / f"worker-{feature_id}.lock")

    def service(self) -> FileLock:
        """W4: one autonomous Worker service per state directory (worker-service.lock), held for the
        service's whole lifetime. Like worker(), NOT part of the lock order above."""
        return FileLock(self.dir / "worker-service.lock")

    def github_sync(self) -> FileLock:
        """One GitHub synchronization at a time (prevents racing Issue creation). It is NOT
        part of the lock order above: no worker holds it, and a sync holds no other lock
        while it waits on the network (state transactions inside a sync are short)."""
        return FileLock(self.dir / "github-sync.lock")

    def intake(self) -> FileLock:
        """W4.3.1: one GitHub intake poll at a time (intake.py). Like github_sync(), NOT part of the
        lock order above: nobody takes it while holding another lock."""
        return FileLock(self.dir / "github-intake.lock")
