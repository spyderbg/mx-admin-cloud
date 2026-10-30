"""The local Worker (W2): one process that drives a whole feature through its specialized agents.

`python -m feature_coordinator work FEATURE --apply` (or FeatureWorker.run) on an ACTIVE feature:

  1. takes the feature's worker lock (one Worker per feature) and verifies the workspace
     (workspace.py: identity, branch, base revision, no unfinished Git operation);
  2. repeatedly starts every READY task whose role is enabled and not already running, up to
     `max_parallel` at a time — each as its own `python -m feature_coordinator launch TASK --apply` process, the
     existing, tested single-task path (claim → agent → validation → serialized commit → gates →
     release). Independent tasks of different roles therefore run concurrently; two tasks of
     one role never do (the role lock would refuse the second anyway);
  3. after each task finishes, re-reads the state: dependents of a verified task become READY
     (model.refresh_readiness) and are started in a later round;
  W3: at start (after 1.) it reclaims the claims of this feature's tasks whose worker died — but
     never one whose interrupted run still has a live agent process (reported instead: the
     operator decides, `python -m feature_coordinator recover TASK`). A reclaimed multi-step task resumes from its
     last checkpoint when it is launched again; completed steps are never run again;
  4. stops when nothing runs and nothing can start. Each task is launched at most
     `max_attempts` times per Worker run (default 1), so a deferred task — provider
     unavailable, operator stop, commit deferred — is reported instead of being retried
     indefinitely (no unbounded model calls);
  5. on SIGTERM/SIGINT it asks every running task to stop gracefully (each launch process
     then stops its agent: SIGTERM → grace → SIGKILL) and waits for them.

The Worker never switches branches, never resets or cleans, and never runs Git itself. When
every task is verified the feature is ready for integration (`python -m feature_coordinator integrate`, or
`work --integrate`).
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from . import CLI, CODE_ROOT
from .checkpoints import progress_line
from .errors import CoordinatorError, LockBusyError
from .model import FeatureState, Task, TaskState
from .supervisor import stop_requested
from .workspace import require_workspace



class Handle(Protocol):
    """A started task: a subprocess.Popen or a test double."""

    def poll(self) -> int | None:
        """The exit code, or None while running."""

    def send_signal(self, sig: int) -> None:
        """Deliver a signal."""

    def wait(self, timeout: float | None = None) -> int:
        """Wait for the exit code."""


Spawner = Callable[[str, int], Handle]


@dataclass
class TaskOutcome:
    """One launch of one task by the Worker."""

    task_id: str
    role: str
    attempt: int
    exit_code: int | None
    state: str
    seconds: float


@dataclass
class WorkReport:
    """What a Worker run did."""

    feature_id: str
    launches: list[TaskOutcome] = field(default_factory=list)
    states: dict[str, str] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)
    max_concurrency: int = 0  # most tasks in progress at the same time
    stopped: bool = False
    recovered: list[str] = field(default_factory=list)          # W3: stale claims reclaimed at start
    orphaned: dict[str, list[int]] = field(default_factory=dict)  # W3: dead worker, agent still alive

    @property
    def all_verified(self) -> bool:
        """True when every task of the feature is verified (ready for integration)."""
        return bool(self.states) and all(s == TaskState.VERIFIED.value for s in self.states.values())


def cli_spawner(repo: Path, state_dir: Path, *, tracker: str = "local", project_config: str | None = None,
                log_dir: Path | None = None) -> Spawner:
    """Start `python -m feature_coordinator launch TASK --apply` for this checkout and state directory. *env*: extra
    variables for the launch process (W4: the service's launch tag)."""
    def spawn(task_id: str, attempt: int, env: dict[str, str] | None = None) -> Handle:
        argv = [*CLI, "--repo", str(repo), "--state-dir", str(state_dir), "--tracker", tracker]
        if project_config:
            argv += ["--project-config", str(project_config)]
        argv += ["launch", task_id, "--apply"]
        directory = log_dir or (state_dir / "logs")
        directory.mkdir(parents=True, exist_ok=True)
        with open(directory / f"work-{task_id}.{attempt}.log", "ab") as log:
            return subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,  # pylint: disable=consider-using-with
                                    start_new_session=True, env={**os.environ, **env} if env else None, cwd=CODE_ROOT)
    return spawn


class FeatureWorker:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """Drives one feature's tasks through their agents. See the module docstring."""

    def __init__(self, coord: Any, spawn: Spawner, *, max_parallel: int = 4, max_attempts: int = 1,  # pylint: disable=too-many-arguments
                 poll_interval: float = 0.2, stop: Callable[[], bool] = stop_requested,
                 echo: Callable[[str], None] = lambda _line: None) -> None:
        if max_parallel < 1 or max_attempts < 1:
            raise CoordinatorError("max_parallel and max_attempts must be at least 1")
        self.coord = coord
        self.spawn = spawn
        self.max_parallel = max_parallel
        self.max_attempts = max_attempts
        self.poll_interval = poll_interval
        self.stop = stop
        self.echo = echo

    def _eligible(self, tasks: Mapping[str, Task], running: Mapping[str, Any], attempts: Mapping[str, int],
                  report: WorkReport) -> list[Task]:
        busy = {self.coord.store.load_task(t).role for t in running}
        busy |= {c.get("role") for c in self.coord.store.list_claims().values()}  # launched by someone else
        chosen: list[Task] = []
        for task in tasks.values():
            if task.state is not TaskState.READY or task.id in running:
                continue
            if not self.coord.config.role(task.role).enabled:
                report.skipped[task.id] = f"role {task.role!r} is disabled"
            elif attempts.get(task.id, 0) >= self.max_attempts:
                report.skipped[task.id] = f"not relaunched: {attempts[task.id]} attempt(s) in this run"
            elif task.role not in busy and len(running) + len(chosen) < self.max_parallel:
                chosen.append(task)
                busy.add(task.role)
        return chosen

    def run(self, feature_id: str) -> WorkReport:
        """Drive *feature_id* until nothing runs and nothing can start."""
        lock = self.coord.store.locks.worker(feature_id)
        try:
            lock.acquire()
        except LockBusyError as exc:
            raise CoordinatorError(f"another Worker is already driving {feature_id!r}") from exc
        try:
            return self._run(feature_id)
        finally:
            lock.release()

    def _recover(self, task_ids: Sequence[str], report: WorkReport) -> None:
        """W3: reclaim this feature's claims whose worker died — never one whose run still has a
        live agent process (reported as orphaned instead)."""
        recovery = self.coord.claims.recover(task_ids=list(task_ids))
        report.recovered = [r["task_id"] for r in recovery.recovered]
        report.orphaned = {r["task_id"]: list(r["orphans"]) for r in recovery.orphaned}
        for task_id in report.recovered:
            self.echo(f"recovered {task_id}: its worker is gone; it continues from its last checkpoint")
        for task_id, pids in report.orphaned.items():
            self.echo(f"NOT recovered {task_id}: agent process(es) {pids} of its interrupted run are still running")

    def _run(self, feature_id: str) -> WorkReport:  # pylint: disable=too-many-locals
        feature = self.coord.store.load_feature(feature_id)
        if feature.state is not FeatureState.ACTIVE:
            raise CoordinatorError(f"feature {feature_id!r} is {feature.state.value}, not active")
        require_workspace(self.coord.root, feature)
        report = WorkReport(feature_id)
        self._recover(feature.task_ids, report)
        running: dict[str, tuple[Handle, int, float]] = {}
        attempts: dict[str, int] = {}
        while True:
            if self.stop() and not report.stopped:
                report.stopped = True
                for handle, _attempt, _started in running.values():
                    handle.send_signal(signal.SIGTERM)
                self.echo(f"stop requested: asked {len(running)} running task(s) to stop")
            for task_id, (handle, attempt, began) in list(running.items()):
                code = handle.poll()
                if code is None:
                    continue
                del running[task_id]
                task = self.coord.store.load_task(task_id)
                report.launches.append(TaskOutcome(task_id, task.role, attempt, code, task.state.value,
                                                   round(time.monotonic() - began, 1)))
                self.echo(f"done  {task_id}: {task.state.value} (exit {code})")
            tasks = self.coord.store.load_tasks(self.coord.store.load_feature(feature_id))
            started = [] if report.stopped else self._eligible(tasks, running, attempts, report)
            for task in started:
                attempts[task.id] = attempts.get(task.id, 0) + 1
                steps = progress_line(self.coord.store, task)
                self.echo(f"start {task.id} ({task.role} → {self.coord.config.provider_for(task.role).name}, "
                          f"model {self.coord.config.model_for(task.role) or 'CLI default'})"
                          + (f"; {steps}" if steps else ""))
                running[task.id] = (self.spawn(task.id, attempts[task.id]), attempts[task.id], time.monotonic())
            # Tasks actually being worked on: a launch process may still be finishing its bookkeeping
            # (the Issue sync) after its task was verified and its role released.
            active = [t for t in running if t in {s.id for s in started} or tasks[t].state is TaskState.IN_PROGRESS]
            report.max_concurrency = max(report.max_concurrency, len(active))
            if not running:
                break
            time.sleep(self.poll_interval)
        final = self.coord.store.load_tasks(self.coord.store.load_feature(feature_id))
        report.states = {t: task.state.value for t, task in final.items()}
        for task_id, task in final.items():
            if task.state is TaskState.BLOCKED and task_id not in report.skipped:
                report.skipped[task_id] = task.blocked_reason
            elif task_id in report.orphaned and task.state is TaskState.IN_PROGRESS:
                report.skipped[task_id] = (f"its interrupted run's agent process(es) {report.orphaned[task_id]} are "
                                           f"still running (`python -m feature_coordinator recover {task_id}`)")
        return report


def default_parallelism(config: Any, roles: Sequence[str]) -> int:
    """One slot per distinct enabled role in the feature (at least 1)."""
    return max(1, len({r for r in roles if config.role(r).enabled}))
