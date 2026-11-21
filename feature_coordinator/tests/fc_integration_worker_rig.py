"""Shared rig for the W4.7 M6 tests (Worker scheduling of integration jobs, crash recovery, concurrency).

The Worker's scheduling step starts each stage as a child process and judges it by its process TAG and by the job
record. `Stages` is the step's `start` for the tests: it checks that the command is exactly a schedulable stage, runs
the stage IN THIS PROCESS through the M2–M5 rig (the real inspector, validator, reviewer and merger over the rig's
disposable repositories, fake gates, fake reviewer process and scripted GitHub), writes the answer the real command
would print with --json into the stage log, and hands back a REAL tagged process that stands for the stage process:

  * by default one that has already ended with the command's exit code (the next step settles it);
  * for a stage in `hold`, one that keeps running until the test lets the stage `release` (it then does its work), or
    `kill`s it — with nothing done, or with a fault that stops the work at one of the M3–M5 fault points, like a
    process killed there.

Everything is disposable; no network, no real GitHub, no model, and no real merge."""

# pylint: disable=missing-function-docstring,too-many-instance-attributes,too-many-arguments

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from feature_coordinator.errors import CoordinatorError
from feature_coordinator.integration_config import IntegrationWorkerConfig
from feature_coordinator.integration_merge import IntegrationMerger
from feature_coordinator.integration_schedule import ACTOR, STAGE_TAG_ENV, STAGES, load_schedule
from feature_coordinator.integration_slot import confirm_launch
from feature_coordinator.integration_worker import IntegrationStep
from feature_coordinator.issue_sync import SimulatedCrash
from feature_coordinator.supervisor import tagged_processes

from fc_integration_rig import Rig, live_client

REPO_ROOT = Path(__file__).resolve().parents[2]
ALL_STAGES = ("inspect", "validate", "review", "recover")
T0 = 1_900_000_000.0


class Clock:
    """The step's clock and wall time for a test: it only moves when the test moves it."""

    def __init__(self, now: float = T0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> float:
        self.now += seconds
        return self.now


def scheduling(rig: Rig, **changes: Any) -> Rig:
    """The rig with the Worker's integration scheduling ON (*changes*: fields of github.integration.worker)."""
    settings = {"enabled": True, "stages": ALL_STAGES, "retry_seconds": 60, "max_retry_seconds": 600, **changes}
    github = rig.config.github
    rig.config = dataclasses.replace(rig.config, github=dataclasses.replace(
        github, integration=dataclasses.replace(github.integration, worker=IntegrationWorkerConfig(**settings))))
    return rig


def coord_of(rig: Rig) -> Any:
    """What the step needs of a coordinator: the store and the configuration (as they are now)."""
    return SimpleNamespace(store=rig.store, config=rig.config)


class Stages:
    """The scheduling step's `start` (module docstring)."""

    def __init__(self, rig: Rig, *, gates: Any = None, launcher: Any = None, fake: Any = None,
                 hold: tuple[str, ...] = ()) -> None:
        self.rig, self.gates, self.launcher, self.fake = rig, gates, launcher, fake
        self.hold = set(hold)
        self.calls: list[dict[str, Any]] = []
        self.handles: list[subprocess.Popen] = []
        self.pending: dict[str, tuple[str, str, subprocess.Popen]] = {}
        self.scripted: dict[str, Any] = {}      # stage → (outcome, reason) | callable(job) -> (outcome, reason)
        self.faults: dict[str, tuple[str, ...]] = {}  # stage → the fault points of its NEXT run
        self.results: list[tuple[str, str, str]] = []  # (stage, job, outcome) of every stage that did its work
        self.fail_start: OSError | None = None
        self.tags: dict[str, str] = {}          # job → the tag of its newest launch
        self.refused: list[tuple[str, str, str]] = []   # (stage, job, why): launches the stage did not confirm

    # -- the starter ---------------------------------------------------------------------------------------------

    def __call__(self, tail: list[str], log_name: str, env: dict[str, str]) -> subprocess.Popen:
        tail = list(tail)
        assert tail[0] == "integration" and tail[1] in STAGES and tail[2] == "--job" \
            and tail[4:] == ["--apply", "--actor", ACTOR, "--json"], tail   # a schedulable stage, nothing else
        if self.fail_start is not None:
            raise self.fail_start
        stage, job = tail[1], tail[3]
        self.calls.append({"stage": stage, "job": job, "tail": tail, "log": log_name, "env": dict(env),
                           "tag": env[STAGE_TAG_ENV]})
        self.tags[job] = env[STAGE_TAG_ENV]
        if stage in self.hold:
            proc = self._process(env, "import time; time.sleep(600)")
            self.pending[job] = (stage, log_name, proc)
            return proc
        proc = self._process(env, f"import sys; sys.exit({self.work(stage, job, log_name)})")
        proc.wait(timeout=30)
        return proc

    def _process(self, env: dict[str, str], code: str) -> subprocess.Popen:
        proc = subprocess.Popen([sys.executable, "-c", code], env=dict(env), stdin=subprocess.DEVNULL,  # pylint: disable=consider-using-with
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.handles.append(proc)
        return proc

    @property
    def stages(self) -> list[str]:
        return [call["stage"] for call in self.calls]

    def started(self, job: str | None = None) -> list[str]:
        return [call["stage"] for call in self.calls if job is None or call["job"] == job]

    # -- the stage's work ----------------------------------------------------------------------------------------

    def work(self, stage: str, job: str, log_name: str) -> int:
        """Do what the stage command does and write its --json answer; the exit code it would end with. A fault
        (SimulatedCrash) leaves the work where it stopped and no answer, like a killed process (-9)."""
        try:
            # as the stage commands do first (integration_commands._confirm_scheduled): confirm the launch, or do
            # nothing at all. (The stage's work is a test double here, so no process is recorded for it.)
            problem = confirm_launch(self.rig.store, job, self.tags[job], None)
            if problem:
                self.refused.append((stage, job, problem))
                raise CoordinatorError(f"this scheduled stage does not run: {problem}; nothing was done")
            scripted = self.scripted.get(stage)
            if scripted is not None:
                outcome, reason = scripted(job) if callable(scripted) else scripted
                answer, ok = {"outcome": outcome, "reasons": [reason] if reason else []}, False
            else:
                report = self._run(stage, job, self.faults.pop(stage, ()))
                ok = report.outcome in ("completed", "settled", "nothing-to-settle") if stage == "recover" \
                    else bool(report.ok)   # as the command exits
                answer = {"outcome": report.outcome, "reasons": list(report.reasons)}
        except SimulatedCrash:
            return 137
        except CoordinatorError as exc:
            answer, ok = {"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}}, False
        self.results.append((stage, job, answer.get("outcome") or "error"))
        logs = self.rig.state / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / log_name).write_text(json.dumps(answer, indent=2) + "\n", encoding="utf-8")
        return 0 if ok else 1

    def _run(self, stage: str, job: str, faults: tuple[str, ...]) -> Any:
        from test_fc_integration_review import reviewer  # pylint: disable=import-outside-toplevel
        rig = self.rig
        if stage == "inspect":
            return rig.inspect(job, actor=ACTOR, faults=faults)
        if stage == "validate":
            return rig.validate(job, gates=self.gates, actor=ACTOR, faults=faults)
        if stage == "review":
            return reviewer(rig, self.launcher, faults=faults).run(job, apply=True, actor=ACTOR)
        return self.recoverer(faults=faults).settle(job, apply=True, actor=ACTOR)

    def reader(self) -> Any:
        """What `integration recover` gets from `_tracker`: a NEW GitHub client over the scripted GitHub that carries
        NO integration anchors (only `integration merge` binds them) — it can send no integration write."""
        assert self.fake is not None, "this test's Stages has no scripted GitHub (fake=)"
        return live_client(self.fake, integration=self.rig.config.github.integration)

    def recoverer(self, *, faults: tuple[str, ...] = (), clock: Any = None,
                  window: float = 120.0) -> IntegrationMerger:
        kwargs = {"clock": clock} if clock is not None else {}
        return IntegrationMerger(self.rig.store, self.rig.config, self.rig.git, verifier=self.rig.verifier,
                                 tracker=self.reader, faults=faults, sleep=lambda _seconds: None,
                                 settle_seconds=window, **kwargs)

    # -- a held stage ---------------------------------------------------------------------------------------------

    def release(self, job: str, *, faults: tuple[str, ...] = ()) -> None:
        """The held stage of *job* does its work now and its process ends (with *faults*: killed at that point)."""
        stage, log_name, proc = self.pending.pop(job)
        if faults:
            self.faults[stage] = faults
        self.work(stage, job, log_name)
        self.kill_process(proc)

    def kill(self, job: str) -> None:
        """The held stage process of *job* is killed before it did anything."""
        _stage, _log, proc = self.pending.pop(job)
        self.kill_process(proc)

    @staticmethod
    def kill_process(proc: subprocess.Popen) -> None:
        with contextlib.suppress(OSError, subprocess.TimeoutExpired):
            proc.kill()
            proc.wait(timeout=30)

    def alive(self) -> list[int]:
        """The stage processes of this rig that still run (by their tags)."""
        return sorted(pid for call in self.calls for pid in tagged_processes(STAGE_TAG_ENV, call["tag"]))

    def cleanup(self) -> None:
        for call in self.calls:
            for pid in tagged_processes(STAGE_TAG_ENV, call["tag"]):
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
        for proc in self.handles:
            self.kill_process(proc)


class ForkedStages(Stages):
    """The stage's work runs in a REAL other process — a fork of the test process, so it has the rig's test doubles
    (fake gates, the fake reviewer launcher, the scripted GitHub) — exactly as the stage command would: it takes the
    job's real `flock`, writes the real records, starts a real reviewer process. The tagged stand-in the Worker
    watches is tied to it by a pipe and lives exactly as long as it does.

    `checkpoint[stage] = NAME`: on reaching the M3–M5 fault point NAME the stage process does not raise — it marks the
    checkpoint and stops there, holding whatever it holds, until the test SIGKILLs it (`sigkill`): no handler, no
    `finally`, no lock release but the kernel's. Whatever the stage process sends to GitHub that is not a read is
    appended to `writes.log` in the state directory (the parent's scripted GitHub does not see a child's calls)."""

    def __init__(self, rig: Rig, **options: Any) -> None:
        super().__init__(rig, **options)
        self.checkpoint: dict[str, str] = {}
        self.children: dict[str, int] = {}      # job → its newest stage process
        self.forked: list[int] = []             # stage processes not reaped yet: each is, in the end (no zombie)
        self.ever: list[int] = []               # every stage process this rig forked
        self.marks = rig.state.parent / "checkpoints"
        self.marks.mkdir(exist_ok=True)

    def __call__(self, tail: list[str], log_name: str, env: dict[str, str]) -> subprocess.Popen:
        tail = list(tail)
        assert tail[0] == "integration" and tail[1] in STAGES and tail[2] == "--job" \
            and tail[4:] == ["--apply", "--actor", ACTOR, "--json"], tail
        stage, job = tail[1], tail[3]
        self.calls.append({"stage": stage, "job": job, "tail": tail, "log": log_name, "env": dict(env),
                           "tag": env[STAGE_TAG_ENV]})
        self.tags[job] = env[STAGE_TAG_ENV]
        read_fd, write_fd = os.pipe()
        pid = os.fork()
        if pid == 0:   # the stage process
            code = 70
            try:
                os.close(read_fd)
                self._arm(stage, job)
                code = self.work(stage, job, log_name)
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                (self.marks / f"error-{job}-{os.getpid()}").write_text(repr(exc), encoding="utf-8")
            finally:
                os._exit(code)  # pylint: disable=protected-access  # never back into the test runner
        os.close(write_fd)
        proc = subprocess.Popen([sys.executable, "-c", "import os, sys; os.read(int(sys.argv[1]), 1)", str(read_fd)],  # pylint: disable=consider-using-with
                                env=dict(env), pass_fds=[read_fd], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        os.close(read_fd)
        self.handles.append(proc)
        self.reap()
        self.children[job] = pid
        self.forked.append(pid)
        self.ever.append(pid)
        return proc

    def reap(self) -> None:
        """Collect every stage process that has ended (a finished child that nobody waited for would stay a zombie
        of the test process)."""
        for pid in list(self.forked):
            try:
                done, _status = os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                done = pid
            if done:
                self.forked.remove(pid)

    def _arm(self, stage: str, job: str) -> None:
        """In the stage process: the checkpoint blocks instead of raising; GitHub writes are logged to a file."""
        from feature_coordinator.integration_inspect import IntegrationInspector  # pylint: disable=import-outside-toplevel
        from feature_coordinator.integration_review import IntegrationReviewer  # pylint: disable=import-outside-toplevel
        from feature_coordinator.integration_validate import IntegrationValidator  # pylint: disable=import-outside-toplevel
        wanted, mark = self.checkpoint.get(stage), self.marks / f"at-{job}"

        def stop_here(_self: Any, name: str) -> None:
            if name == wanted:
                mark.write_text(name, encoding="utf-8")
                time.sleep(600)
        for cls in (IntegrationInspector, IntegrationValidator, IntegrationReviewer):
            cls._fault = stop_here  # type: ignore[method-assign]  # pylint: disable=protected-access
        if self.fake is not None:
            real, log = self.fake.request, self.rig.state / "writes.log"

            def request(method: str, path: str, body: Any = None) -> Any:
                if method != "GET" and path != "graphql":
                    with open(log, "a", encoding="utf-8") as handle:
                        handle.write(f"{method} {path}\n")
                return real(method, path, body)
            self.fake.request = request  # type: ignore[method-assign]

    def at_checkpoint(self, job: str, timeout: float = 60.0) -> str:
        """Wait until the stage process of *job* stands at its checkpoint; the checkpoint's name."""
        mark = self.marks / f"at-{job}"
        wait_for(mark.exists, timeout)
        return mark.read_text(encoding="utf-8")

    def sigkill(self, job: str) -> None:
        """SIGKILL the stage process of *job* (and reap it): the kernel releases what it held."""
        pid = self.children.pop(job)
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        self.forked.remove(pid)
        (self.marks / f"at-{job}").unlink(missing_ok=True)

    def join(self, job: str, timeout: float = 120.0) -> int:
        """Wait for the stage process of *job* to end by itself; its exit code."""
        pid = self.children.pop(job)
        deadline = time.monotonic() + timeout
        while True:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                self.forked.remove(pid)
                return os.waitstatus_to_exitcode(status)
            assert time.monotonic() < deadline, f"the stage process of {job} did not end"
            time.sleep(0.02)

    def errors(self) -> list[str]:
        return [path.read_text(encoding="utf-8") for path in sorted(self.marks.glob("error-*"))]

    def github_writes(self) -> list[str]:
        log = self.rig.state / "writes.log"
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    def cleanup(self) -> None:
        for pid in list(self.forked):           # every one of them: ended or not, it is killed and REAPED
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)
            with contextlib.suppress(OSError):
                os.waitpid(pid, 0)
        self.forked.clear()
        self.children.clear()
        super().cleanup()


def step(rig: Rig, stages: Any, clock: Clock | None = None, *, faults: tuple[str, ...] = (),
         instance: str = "test") -> IntegrationStep:
    """One scheduling step object over the rig as it is NOW (a fresh one stands for a restarted service)."""
    clock = clock or Clock()
    return IntegrationStep(coord_of(rig), start=stages, clock=clock, wall=clock, faults=faults, instance=instance,
                           environ={k: v for k, v in os.environ.items() if k != STAGE_TAG_ENV})


def poll(rig: Rig, stages: Any, clock: Clock | None = None, **options: Any) -> Any:
    return step(rig, stages, clock, **options).poll()


def run_until_quiet(rig: Rig, stages: Stages, clock: Clock, *, limit: int = 40) -> list[Any]:
    """Poll until a step starts nothing and nothing runs (the Worker has done what it may by itself). Each poll is a
    fresh step object; the clock does not move, so a delayed stage stays delayed."""
    reports = []
    for _ in range(limit):
        report = poll(rig, stages, clock)
        reports.append(report)
        if not report.started and not report.busy and not any("ended" in line for line in report.events):
            return reports
    raise AssertionError(f"the Worker did not come to rest within {limit} steps: {stages.stages}")


def settle(rig: Rig, stages: Any, clock: Clock, job: str, timeout: float = 60.0) -> None:
    """Poll until the Worker has settled the job's stage (its launch is closed) — real time may have to pass: a
    reviewer process a killed review stage left behind keeps the slot until it has ended."""
    deadline = time.monotonic() + timeout
    while True:
        poll(rig, stages, clock)
        if load_schedule(rig.store, job).running is None:
            return
        assert time.monotonic() < deadline, f"the stage of {job} was not settled in time"
        time.sleep(0.05)


def schedule(rig: Rig, job: str) -> Any:
    return load_schedule(rig.store, job)


def events_of(rig: Rig, kind: str, job: str | None = None) -> list[dict[str, Any]]:
    return [e for e in rig.store.events() if e["kind"] == kind and (job is None or e.get("job") == job)]


def hold_job_lock(rig: Rig, job: str) -> subprocess.Popen:
    """A REAL other process that owns the job's lock exactly as a stage command does (FileLock, non-blocking) — an
    operator's `integration validate --apply` in another terminal. It holds it until killed."""
    lock = rig.state / "locks" / f"integration-{job}.lock"
    code = ("import sys, time\n"
            "from pathlib import Path\n"
            "from feature_coordinator.locks import FileLock\n"
            "held = FileLock(Path(sys.argv[1])).acquire(timeout=0)\n"
            "print('locked', flush=True)\n"
            "time.sleep(600)\n")
    proc = subprocess.Popen([sys.executable, "-c", code, str(lock)], stdout=subprocess.PIPE, text=True,  # pylint: disable=consider-using-with
                            cwd=REPO_ROOT, env={**os.environ, "PYTHONPATH": str(REPO_ROOT)})
    assert proc.stdout is not None and proc.stdout.readline().strip() == "locked"
    return proc


def wait_for(condition: Callable[[], Any], timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "condition not reached in time"
        time.sleep(0.02)


__all__ = ["ALL_STAGES", "Clock", "ForkedStages", "Stages", "coord_of", "events_of", "hold_job_lock", "poll",
           "run_until_quiet", "schedule", "scheduling", "settle", "step", "wait_for"]
