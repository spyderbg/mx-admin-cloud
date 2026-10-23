"""W4.1: the autonomous Worker's watchdog with REAL hung, slow, killed and orphaned processes.

Real-process tests run `feature.py worker start` as a subprocess. Each launch is a real `feature.py
launch` process using the native-cli backend and its Supervisor; each agent is the fake agent CLI
(fc_fake_agent.py: no model, no subscription use, fake credentials under a fake HOME). Every
timeout is short and set in that test's own project configuration only: production defaults are
untouched. Synchronization uses marker and pid files, bounded polling and explicit release files;
no fixed sleep decides an outcome.

Faults are injected by a test-only `sitecustomize` (WATCHDOG_HOOK):
  * a launch process pauses ONCE before a given step, emitting no heartbeat (a slow phase, or —
    with no end — a hung launch whose SIGTERM handler only sets a flag);
  * a service process dies (os._exit) part-way through a watchdog termination.
In-process tests (mock agents, simulated clock) cover injected identity evidence, and a process
that cannot be confirmed terminated (os.kill made to drop signals to one test process).

Cleanup touches only this test's processes: the `reaper` fixture kills processes whose command line
names the test's temporary directory, and every test asserts that nothing of it is left."""

# pylint: disable=missing-function-docstring,redefined-outer-name,too-many-locals,too-many-statements
# pylint: disable=too-many-lines

from __future__ import annotations

import dataclasses
import json
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator.autonomy import (ServiceRecord, claim_view, diagnose, epoch, health_report, iso,
                                          process_start_epoch)
from feature_coordinator.checkpoints import inspect
from feature_coordinator.cli import main as cli_main
from feature_coordinator.config import load_config
from feature_coordinator.locks import FileLock, ProcessIdentity
from feature_coordinator.model import TaskState
from feature_coordinator.operations import build_coordinator
from feature_coordinator.supervisor import RUN_TAG_ENV
from feature_coordinator.workflow import TaskSpec

from test_fc_autonomy import Rig, autonomous, state_is
from test_fc_autonomy import rig  # noqa: F401  pylint: disable=unused-import
from test_fc_service_process import (Project, Services, activate, agent_recorded, make_project, pid_alive,
                                     test_processes)
from test_fc_service_process import services  # noqa: F401  pylint: disable=unused-import
from test_fc_worker_loop import actions, wait_until

WATCHDOG_HOOK = r'''"""TEST-ONLY fault injection (test_fc_watchdog.py). Never part of the coordinator.
* `feature.py launch` with FC_TEST_PAUSE_STEP / FC_TEST_PAUSE_MARKER / FC_TEST_PAUSE_SECONDS: before
  that step, ONCE (while the marker does not exist), write the marker and do nothing — no claim
  heartbeat — for that many seconds (0: until killed; SIGTERM only sets the stop flag).
* `feature.py worker start` with FC_TEST_SERVICE_CRASH: die (os._exit 70) part-way through a
  watchdog termination: "watchdog-terminate" (after SIGTERM to the hung launch) or
  "orphan-terminate" (after SIGTERM to the orphaned processes)."""
import importlib.machinery, json, os, signal, sys, time
_STEP = os.environ.get("FC_TEST_PAUSE_STEP", "")
_MARKER = os.environ.get("FC_TEST_PAUSE_MARKER", "")
_SECONDS = float(os.environ.get("FC_TEST_PAUSE_SECONDS", "0") or 0)
_CRASH = os.environ.get("FC_TEST_SERVICE_CRASH", "")

def _patch_workflow(module):
    original = module.FeatureCoordinator._run_step
    def pausing(self, feature, task, claim, mark, step, record, progress):
        if step.key == _STEP and not os.path.exists(_MARKER):
            with open(_MARKER + ".tmp", "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "step": step.key, "at": time.time()}, handle)
            os.replace(_MARKER + ".tmp", _MARKER)
            deadline = time.monotonic() + (_SECONDS or 3600)
            while time.monotonic() < deadline:
                time.sleep(0.05)
            if not _SECONDS:
                os._exit(99)
        return original(self, feature, task, claim, mark, step, record, progress)
    module.FeatureCoordinator._run_step = pausing

def _patch_service(module):
    if _CRASH == "watchdog-terminate":
        def crashing_terminate(self, handle):
            handle.send_signal(signal.SIGTERM)   # the termination has begun ...
            os._exit(70)                          # ... and the service is lost before it is confirmed
        module.WorkerService._terminate = crashing_terminate
    elif _CRASH == "orphan-terminate":
        def crashing_terminate_pids(pids, **kwargs):
            for pid in pids:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
            os._exit(70)
        module.terminate_pids = crashing_terminate_pids

_PATCHES = {}
if _STEP and _MARKER and "launch" in sys.argv:
    _PATCHES["feature_coordinator.workflow"] = _patch_workflow
if _CRASH and "worker" in sys.argv and "start" in sys.argv:
    _PATCHES["feature_coordinator.service"] = _patch_service

class _Finder:
    @staticmethod
    def find_spec(name, path=None, target=None):
        if name not in _PATCHES:
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is not None and spec.loader is not None:
            run = spec.loader.exec_module
            def exec_module(module):
                run(module)
                _PATCHES[name](module)
            spec.loader.exec_module = exec_module
        return spec

if _PATCHES:
    sys.meta_path.insert(0, _Finder)
'''

# hung_after_seconds = stale 2 + task timeout 5 + gate timeout 1 + grace 1 = 9 s (this test only)
FAST_WATCHDOG = {"heartbeat_interval_seconds": 1, "stale_heartbeat_seconds": 2, "task_timeout_seconds": 5,
                 "gate_timeout_seconds": 1, "terminate_grace_seconds": 1}
STEPS = ("models", "service", "endpoints")
SLEEPER = ("import signal, sys, time\n"
           "def note(signum, _frame):\n"
           "    with open(sys.argv[2], 'a', encoding='utf-8') as handle:\n"
           "        handle.write(f'{signum}\\n')\n"
           "if sys.argv[3] == 'bystander':\n"
           "    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGUSR1):\n"
           "        signal.signal(sig, note)\n"
           "time.sleep(600)\n")


# -- helpers ----------------------------------------------------------------------------------------------------------

@pytest.fixture
def reaper(tmp_path: Path):
    """After the test (even a failed one): kill whatever of THIS test is left — nothing else."""
    yield tmp_path
    for pid, _cmd in test_processes(tmp_path):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def read_identity(path: Path) -> ProcessIdentity:
    pid, ticks = path.read_text(encoding="utf-8").split()
    return ProcessIdentity(pid=int(pid), start_ticks=int(ticks), boot_id=ProcessIdentity.current().boot_id)


def lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def health(project: Project) -> dict[str, Any]:
    result = project.cli("worker", "health", "--json")
    assert "Traceback" not in result.stderr, result.stderr
    return json.loads(result.stdout)


def entry(report: dict[str, Any], task_id: str) -> dict[str, Any]:
    return next(t for t in report["tasks"] if t["task"] == task_id)


def history(project: Project, task_id: str) -> list[str]:
    return [h["category"] for h in project.record("scheduling", task_id)["history"]]


def hooks_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "hooks"
    directory.mkdir(exist_ok=True)
    (directory / "sitecustomize.py").write_text(WATCHDOG_HOOK, encoding="utf-8")
    return directory


def stepped(project: Project, slug: str, special: dict[str, list[dict]] | None = None) -> None:
    """One Backend task with three steps; each appends its key to the audit log and writes a file."""
    steps = []
    for key in STEPS:
        acts = [{"do": "append", "path": str(project.audit), "text": f"{key}\n"}, *(special or {}).get(key, []),
                {"do": "write", "path": f"backend/src/{key}.py", "text": f"{key.upper()} = 1\n"}]
        steps.append({"key": key, "title": actions(*acts)})
    activate(project, slug, [{"key": "api", "role": "backend", "title": "API in three steps", "steps": steps}])


def assert_clean(project: Project, *tasks: str) -> None:
    """Nothing of the test left: processes, claims, held locks, a live exclusive holder; every
    checkpoint record consistent with the branch."""
    assert wait_until(lambda: not test_processes(project.dir), 15), test_processes(project.dir)
    assert not list(project.state.glob("claims/*.json"))
    for lock in (project.state / "locks").glob("worker*.lock"):
        FileLock(lock).acquire().release()  # held by nobody
    flag = project.state / "flags" / "agent-exclusive.json"
    if flag.exists():
        assert not ProcessIdentity.from_dict(json.loads(flag.read_text(encoding="utf-8"))["identity"]).is_alive()
    coord = build_coordinator(load_config(project.config), project.repo, project.state)
    for task_id in tasks:
        assert not inspect(coord, task_id).conflicts, task_id


def sleeper(tmp_path: Path, role: str, *, tag: str | None = None, session: bool = False) -> subprocess.Popen:
    """A real test process (its command line names tmp_path, so the reaper finds it)."""
    log = tmp_path / f"{role}.signals"
    env = {**os.environ, **({RUN_TAG_ENV: tag} if tag else {})}
    return subprocess.Popen([sys.executable, "-c", SLEEPER, str(tmp_path), str(log), role],  # pylint: disable=consider-using-with
                            env=env, start_new_session=session)


def dead_identity() -> dict[str, Any]:
    """The identity of a process that has ended (a worker that is gone)."""
    proc = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"],  # pylint: disable=consider-using-with
                            stdin=subprocess.PIPE)
    identity = ProcessIdentity.of_pid(proc.pid)
    assert identity is not None
    proc.stdin.close()
    proc.wait(timeout=30)
    return identity.to_dict()


def write_claim(rig: Rig, task_id: str, identity: dict[str, Any], progress: dict[str, Any]) -> Path:
    path = rig.coord.store.root / "claims" / f"{task_id}.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"task_id": task_id, "role": rig.task(task_id).role, "branch": "feature/w4",
                                "head_sha": "0" * 40, "identity": identity, "claimed_at": "2026-01-01T00:00:00Z",
                                "heartbeat_at": "2026-01-01T00:00:00Z", "checkpoint": progress, "revision": 1}),
                    encoding="utf-8")
    return path


def fast_rig(repo: Path, state_dir: Path, **runtime: Any) -> Rig:
    config = autonomous()
    return Rig(repo, state_dir, config=dataclasses.replace(config, runtime=dataclasses.replace(config.runtime,
                                                                                               **runtime)))


# -- 4. a real hung agent ---------------------------------------------------------------------------------------------

def test_hung_agent_is_stopped_at_its_deadline_and_retried(tmp_path: Path, fake_home: Path, fake_agent: Path,
                                                           services: Services, reaper: Path) -> None:
    """The agent hangs (no output, SIGTERM ignored) and leaves a setsid'd child that also ignores
    SIGTERM. The launch's Supervisor stops both at the run deadline (SIGTERM, grace, SIGKILL; the
    child by its run tag); the service records a transient failure and retries after its backoff —
    and the retry starts only after both processes are gone."""
    del fake_home, reaper
    project = make_project(tmp_path, fake_agent, runtime={"task_timeout_seconds": 5, "terminate_grace_seconds": 1})
    agent1, child1, sigterms, beat = (tmp_path / n for n in ("agent1.id", "child1.id", "sigterm.log", "child.beat"))
    activate(project, "w41a", [{"key": "api", "role": "backend", "title": actions(
        {"do": "append", "path": str(project.audit), "text": "run\n"},
        {"do": "once", "path": str(tmp_path / "first-run"), "actions": [
            {"do": "pidfile", "path": str(agent1)},
            {"do": "trap_sigterm", "path": str(sigterms)},
            {"do": "daemon", "path": str(beat), "interval": 0.05, "pid_path": str(child1), "ignore_sigterm": True},
            {"do": "sleep", "seconds": 600}]},
        {"do": "check_gone", "pid_file": str(agent1), "path": str(project.audit), "label": "agent1"},
        {"do": "check_gone", "pid_file": str(child1), "path": str(project.audit), "label": "child1"},
        {"do": "write", "path": "backend/src/api.py", "text": "API = 1\n"})}])
    proc, log = services.start(project, "--exit-when-idle")
    assert wait_until(lambda: agent1.exists() and child1.exists(), 60), log.read_text(encoding="utf-8")
    first, child = read_identity(agent1), read_identity(child1)
    assert first.is_alive() and child.is_alive()
    claims = list(project.state.glob("claims/*.json"))  # the Worker owns the checkout: one claim, its launch
    assert len(claims) == 1 and ProcessIdentity.from_dict(json.loads(claims[0].read_text())["identity"]).is_alive()
    flag = json.loads((project.state / "flags" / "agent-exclusive.json").read_text(encoding="utf-8"))
    assert flag["identity"]["pid"] == proc.pid
    report = health(project)
    view = entry(report, "w41a.api")["claim"]["watchdog"]
    assert view["status"] == "running" and view["agent_pid"] == first.pid and view["deadline"]
    assert report["diagnosis"] == "agent-running" and report["current"]["task"] == "w41a.api"

    assert proc.wait(timeout=120) == 0, log.read_text(encoding="utf-8")
    assert not first.is_alive() and not child.is_alive()
    assert [line.split()[1] for line in lines(sigterms)] == [str(first.pid)]  # SIGTERM once (ignored) → SIGKILL
    assert lines(project.audit) == ["run", "run", "agent1 gone", "child1 gone"]  # no overlap with the retry
    rec = project.record("scheduling", "w41a.api")
    assert history(project, "w41a.api") == ["transient", "success"] and "timed out" in rec["history"][0]["reason"]
    assert rec["launches"] == 2 and rec["attempts"] == {"transient": 1} and rec["in_flight"] is None
    assert not project.events("worker-watchdog-hung")  # the agent's deadline sufficed: the launch stayed healthy
    assert project.task("w41a.api")["state"] == "verified" and project.task_commits("feature/w41a") == ["w41a.api"]
    assert_clean(project)


# -- 5. slow but healthy ----------------------------------------------------------------------------------------------

def test_silent_but_healthy_agent_is_not_stopped(tmp_path: Path, fake_home: Path, fake_agent: Path,
                                                 services: Services, reaper: Path) -> None:
    """An agent that writes nothing for several stale-heartbeat periods is NOT hung: its launch
    keeps heartbeating, health says "running", and nothing is signalled."""
    del fake_home, reaper
    project = make_project(tmp_path, fake_agent, runtime={"heartbeat_interval_seconds": 1, "stale_heartbeat_seconds": 4,
                                                          "task_timeout_seconds": 60, "terminate_grace_seconds": 1})
    agent, sigterms, proceed = tmp_path / "agent.id", tmp_path / "sigterm.log", tmp_path / "proceed"
    activate(project, "w41b", [{"key": "api", "role": "backend", "title": actions(
        {"do": "append", "path": str(project.audit), "text": "run\n"},
        {"do": "pidfile", "path": str(agent)},
        {"do": "trap_sigterm", "path": str(sigterms), "exit": True},
        {"do": "wait_for", "path": str(proceed), "timeout": 120, "required": True},  # silent until released
        {"do": "write", "path": "backend/src/api.py", "text": "API = 1\n"})}])
    proc, log = services.start(project, "--exit-when-idle")
    assert wait_until(agent.exists, 60), log.read_text(encoding="utf-8")
    silent_since = time.monotonic()
    stdout = next(project.state.glob("runs/w41b.api.*/stdout.jsonl"))
    observed = []
    while time.monotonic() - silent_since < 3 * 4:  # three stale-heartbeat periods of agent silence
        view = entry(health(project), "w41b.api")["claim"]["watchdog"]
        observed.append((view["status"], view["heartbeat_age_seconds"]))
        assert stdout.stat().st_size == 0  # the agent really is silent
    proceed.write_text("1", encoding="utf-8")
    assert proc.wait(timeout=120) == 0, log.read_text(encoding="utf-8")
    assert observed and {status for status, _age in observed} == {"running"}, observed
    assert not lines(sigterms) and not project.events("worker-watchdog-hung")
    assert not project.events("worker-watchdog-stale")
    assert history(project, "w41b.api") == ["success"] and project.record("scheduling", "w41b.api")["launches"] == 1
    assert lines(project.audit) == ["run"] and project.task("w41b.api")["state"] == "verified"
    assert_clean(project)


def test_slow_launch_phase_is_reported_not_stopped(tmp_path: Path, fake_home: Path, fake_agent: Path,
                                                   services: Services, reaper: Path) -> None:
    """The launch itself stops heartbeating for 5 s (like a long gate): beyond the stale threshold
    (2 s) but below the hung threshold (9 s). Health says "slow", one warning is recorded, nothing is
    stopped, every step runs once."""
    del fake_home, reaper
    project = make_project(tmp_path, fake_agent, runtime=FAST_WATCHDOG)
    stepped(project, "w41c")
    marker = tmp_path / "paused.json"
    proc, log = services.start(project, "--exit-when-idle", hooks=hooks_dir(tmp_path), env={
        "FC_TEST_PAUSE_STEP": "service", "FC_TEST_PAUSE_MARKER": str(marker), "FC_TEST_PAUSE_SECONDS": "5"})
    assert wait_until(marker.exists, 60), log.read_text(encoding="utf-8")
    slow: dict[str, Any] = {}

    def seen_slow() -> bool:
        report = health(project)
        if entry(report, "w41c.api").get("claim", {}).get("watchdog", {}).get("status") == "slow":
            slow.update(report)
            return True
        return False
    assert wait_until(seen_slow, 5), "health never reported the paused launch as slow"
    assert slow["diagnosis"] == "agent-slow"
    assert proc.wait(timeout=120) == 0, log.read_text(encoding="utf-8")
    assert len(project.events("worker-watchdog-stale")) >= 1 and not project.events("worker-watchdog-hung")
    assert lines(project.audit) == list(STEPS) and history(project, "w41c.api") == ["success"]
    assert project.task_commits("feature/w41c") == ["w41c.api"] * 3
    assert_clean(project, "w41c.api")


# -- the service's own watchdog: a hung launch -------------------------------------------------------------------------

def test_hung_launch_is_stopped_and_the_task_resumes_from_its_checkpoint(tmp_path: Path, fake_home: Path,
                                                                         fake_agent: Path, services: Services,
                                                                         reaper: Path) -> None:
    """The launch process hangs before step 2 (no heartbeat; SIGTERM only sets a flag). After 9 s
    the service records its decision, sends SIGTERM, then SIGKILL, confirms the end, records a
    transient failure; after the backoff the task resumes at step 2 (step 1 is not repeated)."""
    del fake_home, reaper
    project = make_project(tmp_path, fake_agent, runtime=FAST_WATCHDOG)
    stepped(project, "w41d")
    marker = tmp_path / "paused.json"
    proc, log = services.start(project, "--exit-when-idle", hooks=hooks_dir(tmp_path), env={
        "FC_TEST_PAUSE_STEP": "service", "FC_TEST_PAUSE_MARKER": str(marker)})
    assert wait_until(marker.exists, 60), log.read_text(encoding="utf-8")
    hung = json.loads(marker.read_text(encoding="utf-8"))["pid"]
    assert proc.wait(timeout=120) == 0, log.read_text(encoding="utf-8")
    assert not pid_alive(hung)
    stopped = project.events("worker-watchdog-terminated")
    assert len(project.events("worker-watchdog-hung")) == 1 and len(stopped) == 1  # one decision, one termination
    assert stopped[0]["pids"] == [hung] and stopped[0]["confirmed"] is True
    rec = project.record("scheduling", "w41d.api")
    assert history(project, "w41d.api") == ["transient", "success"] and "watchdog" in rec["history"][0]["reason"]
    assert rec["launches"] == 2 and rec["attempts"] == {"transient": 1}
    assert lines(project.audit) == list(STEPS)  # every step exactly once
    assert project.task_commits("feature/w41d") == ["w41d.api"] * 3
    assert_clean(project, "w41d.api")


# -- 9. a crash during the watchdog's cleanup --------------------------------------------------------------------------

def test_crash_during_hung_launch_termination_is_finished_by_the_next_service(tmp_path: Path, fake_home: Path,
                                                                              fake_agent: Path, services: Services,
                                                                              reaper: Path) -> None:
    """The service decides the launch is hung, records the decision, sends SIGTERM — and dies. The
    hung launch survives (it ignores SIGTERM) and still holds the claim. A new service finds the
    recorded decision, re-verifies the process (identity + launch tag), finishes the termination,
    and only then lets the task resume. Status and health stay read-only meanwhile."""
    del fake_home, reaper
    project = make_project(tmp_path, fake_agent, runtime=FAST_WATCHDOG)
    stepped(project, "w41e")
    marker = tmp_path / "paused.json"
    hooks = hooks_dir(tmp_path)
    code, out = services.run(project, hooks=hooks, timeout=120, env={
        "FC_TEST_PAUSE_STEP": "service", "FC_TEST_PAUSE_MARKER": str(marker),
        "FC_TEST_SERVICE_CRASH": "watchdog-terminate"})
    assert code == 70, out
    hung = json.loads(marker.read_text(encoding="utf-8"))["pid"]
    assert pid_alive(hung)  # SIGTERM was not enough, and the service is gone
    rec = project.record("scheduling", "w41e.api")
    assert rec["in_flight"]["watchdog"]["pid"] == hung and rec["launches"] == 1  # the decision was durable first
    assert project.record("claims", "w41e.api") is not None
    snapshot = {p: p.read_bytes() for p in project.state.rglob("*") if p.is_file()}
    report = health(project)
    for command in (("worker", "status"), ("worker", "waiting"), ("worker", "attention")):
        project.cli(*command)
    assert {p: p.read_bytes() for p in project.state.rglob("*") if p.is_file()} == snapshot  # read-only
    view = entry(report, "w41e.api")["claim"]["watchdog"]
    assert report["state"] == "OFFLINE" and view["status"] == "hung" and view["holder_pid"] == hung
    assert report["diagnosis"] == "agent-deadline-exceeded"

    code, out = services.run(project, "--exit-when-idle", timeout=120)
    assert code == 0, out
    assert not pid_alive(hung)
    resumed = [e for e in project.events("worker-watchdog-hung") if e.get("resumed")]
    assert len(project.events("worker-watchdog-hung")) == 2 and len(resumed) == 1
    terminated = project.events("worker-watchdog-terminated")
    assert len(terminated) == 1 and terminated[0]["pids"] == [hung] and not terminated[0]["survivors"]
    rec = project.record("scheduling", "w41e.api")
    assert history(project, "w41e.api") == ["transient", "success"] and rec["launches"] == 2
    assert lines(project.audit) == list(STEPS) and project.task_commits("feature/w41e") == ["w41e.api"] * 3
    assert_clean(project, "w41e.api")


def test_crash_during_orphan_termination_is_finished_by_the_next_service(tmp_path: Path, fake_home: Path,
                                                                         fake_agent: Path, services: Services,
                                                                         reaper: Path) -> None:
    """An orphaned agent ignores SIGTERM; the service sends it at the orphan's deadline and dies. A
    new service re-checks the orphan's identity, escalates to SIGKILL, recovers the claim, and the
    task runs again — after the orphan is gone, with its launch budget intact."""
    del fake_home, reaper
    project = make_project(tmp_path, fake_agent, runtime={"task_timeout_seconds": 5, "terminate_grace_seconds": 1})
    agent, sigterms = tmp_path / "agent.id", tmp_path / "sigterm.log"
    activate(project, "w41f", [{"key": "api", "role": "backend", "title": actions(
        {"do": "append", "path": str(project.audit), "text": "run\n"},
        {"do": "once", "path": str(tmp_path / "first-run"), "actions": [
            {"do": "pidfile", "path": str(agent)}, {"do": "trap_sigterm", "path": str(sigterms)},
            {"do": "sleep", "seconds": 600}]},
        {"do": "check_gone", "pid_file": str(agent), "path": str(project.audit), "label": "orphan"},
        {"do": "write", "path": "backend/src/api.py", "text": "API = 1\n"})}])
    proc, log = services.start(project, hooks=hooks_dir(tmp_path), env={"FC_TEST_SERVICE_CRASH": "orphan-terminate"})
    assert wait_until(agent.exists, 60), log.read_text(encoding="utf-8")
    orphan = read_identity(agent)
    holder = agent_recorded(project, "w41f.api", orphan.pid)["identity"]["pid"]
    os.kill(holder, signal.SIGKILL)  # the launch process dies; its agent lives on
    assert proc.wait(timeout=120) == 70, log.read_text(encoding="utf-8")  # it died sending SIGTERM to the orphan
    assert orphan.is_alive() and [line.split()[1] for line in lines(sigterms)] == [str(orphan.pid)]
    assert project.record("scheduling", "w41f.api")["launches"] == 1

    code, out = services.run(project, "--exit-when-idle", timeout=120)
    assert code == 0, out
    assert not orphan.is_alive() and len(lines(sigterms)) == 2  # once per instance, then SIGKILL
    assert lines(project.audit) == ["run", "run", "orphan gone"]
    rec = project.record("scheduling", "w41f.api")
    assert rec["launches"] == 2 and history(project, "w41f.api")[-1] == "success"
    assert project.task_commits("feature/w41f") == ["w41f.api"]
    assert_clean(project)


# -- 6. an orphan after the whole Worker died --------------------------------------------------------------------------

def test_orphan_after_the_worker_dies_is_settled_before_the_task_resumes(tmp_path: Path, fake_home: Path,
                                                                         fake_agent: Path, services: Services,
                                                                         reaper: Path) -> None:
    """Service and launch are SIGKILLed while the step-2 agent runs. A new service finds the orphan,
    starts nothing, waits until the orphan's run deadline (never because its parent died), stops it
    after re-checking its identity, recovers the claim, and resumes at step 2 from the checkpoint."""
    del fake_home, reaper
    project = make_project(tmp_path, fake_agent, runtime={"task_timeout_seconds": 6, "terminate_grace_seconds": 1})
    agent, sigterms = tmp_path / "agent.id", tmp_path / "sigterm.log"
    stepped(project, "w41g", {"service": [
        {"do": "once", "path": str(tmp_path / "first-run"), "actions": [
            {"do": "pidfile", "path": str(agent)}, {"do": "trap_sigterm", "path": str(sigterms), "exit": True},
            {"do": "sleep", "seconds": 600}]},
        {"do": "check_gone", "pid_file": str(agent), "path": str(project.audit), "label": "orphan"}]})
    first, log = services.start(project)
    assert wait_until(agent.exists, 60), log.read_text(encoding="utf-8")
    orphan = read_identity(agent)
    holder = agent_recorded(project, "w41g.api", orphan.pid)["identity"]["pid"]
    os.kill(first.pid, signal.SIGKILL)  # the whole Worker is lost ...
    os.kill(holder, signal.SIGKILL)
    first.wait(timeout=30)
    assert wait_until(lambda: not pid_alive(holder), 10) and orphan.is_alive()  # ... its agent lives on

    second, log2 = services.start(project, "--exit-when-idle")
    waiting: dict[str, Any] = {}

    def waits_for_orphan() -> bool:
        record = project.record("service", "worker") or {}
        if record.get("state") == "WAITING" and "still running" in record.get("reason", ""):
            waiting.update(record=record, alive=orphan.is_alive(), audit=lines(project.audit), health=health(project))
            return True
        return False
    assert wait_until(waits_for_orphan, 60), log2.read_text(encoding="utf-8")
    assert waiting["alive"] and waiting["audit"] == ["models", "service"]  # nothing new started meanwhile
    assert entry(waiting["health"], "w41g.api")["claim"]["watchdog"]["status"] == "orphaned"
    assert waiting["health"]["diagnosis"] == "orphan-detected"
    assert second.wait(timeout=120) == 0, log2.read_text(encoding="utf-8")

    stopped = project.events("worker-orphans-stopped")
    assert len(stopped) == 1 and orphan.pid in stopped[0]["pids"] and not stopped[0]["survivors"]
    assert epoch(stopped[0]["at"]) >= epoch(stopped[0]["deadline"])  # only after its run deadline
    assert [line.split()[1] for line in lines(sigterms)] == [str(orphan.pid)] and not orphan.is_alive()
    assert lines(project.audit) == ["models", "service", "service", "orphan gone", "endpoints"]
    rec = project.record("scheduling", "w41g.api")
    assert history(project, "w41g.api") == ["interrupted", "success"] and rec["launches"] == 2
    assert project.task_commits("feature/w41g") == ["w41g.api"] * 3  # one checkpoint commit per step, none twice
    assert_clean(project, "w41g.api")


# -- 7. a process that cannot be confirmed terminated (injected), in-process -------------------------------------------

def test_orphan_that_survives_termination_blocks_everything(repo: Path, state_dir: Path, tmp_path: Path,
                                                            monkeypatch: pytest.MonkeyPatch, reaper: Path) -> None:
    """A dead worker's agent and its setsid'd child (both carrying the run tag). Signals to the child
    are dropped (injected: it cannot be confirmed terminated). The service stops the agent, reports
    the child, keeps the claim — so not even an unrelated task starts — and does not signal again.
    A new service, once the child can be stopped, finishes the cleanup; only then do tasks run."""
    del reaper
    rig = fast_rig(repo, state_dir, task_timeout_seconds=1, terminate_grace_seconds=1)
    rig.activate(TaskSpec("api", "backend", "API"), TaskSpec("cmd", "cli", "Command"))
    tag = "w41-" + secrets.token_hex(6)
    agent, child = sleeper(tmp_path, "agent", tag=tag), sleeper(tmp_path, "child", tag=tag, session=True)
    bystander = sleeper(tmp_path, "bystander")
    identity = ProcessIdentity.of_pid(agent.pid)
    assert identity is not None
    claim_path = write_claim(rig, "w4.api", dead_identity(), {"run_tag": tag, "agent": identity.to_dict()})
    started = process_start_epoch(identity)
    assert started is not None
    deadline = started + rig.config.runtime.task_timeout_seconds + rig.config.runtime.terminate_grace_seconds
    assert wait_until(lambda: time.time() > deadline + 0.2, 15)  # the orphan's run deadline has passed
    real_kill, dropped = os.kill, []

    def kill(pid: int, sig: int) -> None:
        if pid == child.pid:
            dropped.append(sig)
            return
        real_kill(pid, sig)
    monkeypatch.setattr(os, "kill", kill)
    seen: dict[str, Any] = {}

    def until(service: Any) -> bool:
        if service.record.state == "NEEDS_ATTENTION" and not seen:
            seen.update(health_report(rig.coord.store, rig.config, time.time()))
        return service.cycles >= 3
    rig.service(until=until, max_cycles=5).run()
    try:
        assert agent.wait(timeout=10) is not None and child.poll() is None
        assert dropped == [signal.SIGTERM, signal.SIGKILL]  # one bounded attempt; not repeated every cycle
        assert not rig.log and claim_path.exists()  # nothing started, not even the independent task
        assert seen["diagnosis"] == "cleanup-uncertain" and str(child.pid) in seen["reason"]
        assert entry(seen, "w4.api")["claim"]["watchdog"]["status"] == "orphaned"
        monkeypatch.undo()  # the child can be stopped now
        rig.service(until=state_is("AVAILABLE"), max_cycles=30).run()
        assert child.wait(timeout=10) is not None and not claim_path.exists()
        assert sorted(k for k, _t in rig.log) == ["w4.api", "w4.cmd"]
        assert bystander.poll() is None and not (tmp_path / "bystander.signals").exists()  # never touched
    finally:
        for proc in (agent, child, bystander):
            if proc.poll() is None:
                real_kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
    assert not test_processes(tmp_path)


@pytest.mark.parametrize("killable", [True, False])
def test_processes_surviving_a_finished_launch_are_cleaned_or_block(repo: Path, state_dir: Path, tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch, reaper: Path,
                                                                    killable: bool) -> None:
    """The launch ended and released its claim, but a process carrying its run tag lives on. The
    service stops it (same policy as the launch's own leftover sweep) and holds the task for an
    operator (it may have written late). If it cannot be stopped, NO agent starts on the checkout
    until it is gone — across service restarts."""
    del reaper
    rig = fast_rig(repo, state_dir, terminate_grace_seconds=1)
    rig.activate(TaskSpec("api", "backend", "API"), TaskSpec("cmd", "cli", "Command"))
    tag, survivor = "w41-" + secrets.token_hex(6), []

    def leaves_a_survivor(request: Any) -> Any:
        survivor.append(sleeper(tmp_path, "survivor", tag=tag, session=True))
        request.context.heartbeat({"run_tag": tag})
        time.sleep(0.3)  # the service sees the run tag (real runs also record it in native.json)
        return rig.coord.runners["backend"].runner.run(request)
    rig.coord.runners["backend"].plan["w4.api"] = [leaves_a_survivor]
    real_kill = os.kill
    if not killable:
        monkeypatch.setattr(os, "kill", lambda pid, sig: None if survivor and pid == survivor[0].pid
                            else real_kill(pid, sig))
    try:
        rig.service(until=state_is("NEEDS_ATTENTION", "AVAILABLE"), max_cycles=20).run()
        attention = rig.sched("w4.api").attention
        assert attention["category"] == "critical" and "survived their launch" in attention["reason"]
        assert len(rig.events("worker-leftovers-stopped")) == 1
        if killable:
            assert survivor[0].wait(timeout=10) is not None and "blocking_run_tags" not in attention
            assert sorted(k for k, _t in rig.log) == ["w4.api", "w4.cmd"]  # the checkout was safe again
        else:
            assert survivor[0].poll() is None and attention["blocking_run_tags"] == [tag]
            assert [k for k, _t in rig.log] == ["w4.api"]  # the independent task did NOT start
            report = health_report(rig.coord.store, rig.config, time.time())
            assert entry(report, "w4.api")["blocking_processes"] == [survivor[0].pid]
            assert report["diagnosis"] == "cleanup-uncertain"
            rig.service(max_cycles=3).run()  # a restarted service: still blocked
            assert [k for k, _t in rig.log] == ["w4.api"]
            monkeypatch.undo()
            real_kill(survivor[0].pid, signal.SIGKILL)  # it ends (or an operator ends it)
            survivor[0].wait(timeout=10)
            rig.service(until=state_is("NEEDS_ATTENTION", "AVAILABLE"), max_cycles=20).run()
            assert sorted(k for k, _t in rig.log) == ["w4.api", "w4.cmd"]
        assert rig.task("w4.cmd").state is TaskState.VERIFIED and rig.sched("w4.api").attention
    finally:
        for proc in survivor:
            if proc.poll() is None:
                real_kill(proc.pid, signal.SIGKILL)
            proc.wait(timeout=10)
    assert not test_processes(tmp_path)


# -- 8. identity evidence that does not match --------------------------------------------------------------------------

def test_pid_reuse_never_kills_the_process_now_using_the_pid(rig: Rig, tmp_path: Path, reaper: Path) -> None:
    """The claim names live PIDs whose start times differ (injected: as if reused). The recorded
    worker and agent are provably gone — the existing identity policy recovers the claim — and the
    processes now using those PIDs are never signalled."""
    del reaper
    rig.activate(TaskSpec("api", "backend", "API"))
    holder, agent = sleeper(tmp_path, "bystander"), sleeper(tmp_path, "bystander")
    try:
        wrong = []
        for proc in (holder, agent):
            identity = ProcessIdentity.of_pid(proc.pid)
            assert identity is not None
            wrong.append({**identity.to_dict(), "start_ticks": identity.start_ticks + 1})
        write_claim(rig, "w4.api", wrong[0], {"run_tag": "w41-" + secrets.token_hex(6), "agent": wrong[1]})
        rig.service(until=state_is("AVAILABLE")).run()
        assert rig.events("recovered") and [k for k, _t in rig.log] == ["w4.api"]
        assert rig.task("w4.api").state is TaskState.VERIFIED and not rig.events("worker-orphans-stopped")
        assert holder.poll() is None and agent.poll() is None
        assert not (tmp_path / "bystander.signals").exists()  # no signal of any kind reached them
    finally:
        for proc in (holder, agent):
            proc.kill()
            proc.wait(timeout=10)


def test_unreadable_identity_fails_closed(rig: Rig, tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                          reaper: Path) -> None:
    """A claim whose holder identity cannot be read (no start time): nothing is recovered, stopped or
    started — the service does not crash, health and status work, and say why."""
    del reaper
    rig.activate(TaskSpec("api", "backend", "API"), TaskSpec("cmd", "cli", "Command"))
    process = sleeper(tmp_path, "bystander")
    try:
        claim = write_claim(rig, "w4.api", {"pid": process.pid}, {})
        before = claim.read_bytes()
        rig.service(max_cycles=3).run()
        assert not rig.log and claim.read_bytes() == before and process.poll() is None
        state = [e for e in rig.events("worker-state") if e["state"] == "NEEDS_ATTENTION"]
        assert state and "no readable holder identity" in state[-1]["reason"]
        snapshot = {p: p.read_bytes() for p in rig.coord.store.root.rglob("*.json")}
        base = ["--repo", str(rig.coord.root), "--state-dir", str(rig.coord.store.root)]
        assert cli_main([*base, "worker", "health", "--json"]) == 1
        report = json.loads(capsys.readouterr().out)
        assert entry(report, "w4.api")["status"] == "needs-attention"
        assert entry(report, "w4.api")["claim"]["watchdog"]["status"] == "ambiguous"
        assert report["diagnosis"] == "attention-required"
        assert cli_main([*base, "worker", "status"]) == 0
        assert {p: p.read_bytes() for p in rig.coord.store.root.rglob("*.json")} == snapshot
        assert not (tmp_path / "bystander.signals").exists()
    finally:
        process.kill()
        process.wait(timeout=10)


# -- 11. health: the watchdog view and the diagnosis -------------------------------------------------------------------

def test_claim_view_tells_running_slow_hung_deadline_orphaned_stale_ambiguous(tmp_path: Path, reaper: Path) -> None:
    del reaper
    runtime = dataclasses.replace(autonomous().runtime, stale_heartbeat_seconds=2, task_timeout_seconds=60,
                                  gate_timeout_seconds=1, terminate_grace_seconds=1)  # hung after 64 s
    agent = sleeper(tmp_path, "agent")
    try:
        me = ProcessIdentity.current().to_dict()
        identity = ProcessIdentity.of_pid(agent.pid)
        assert identity is not None

        def view(holder: Any, age: float, run: Any = runtime) -> str:
            claim = {"identity": holder, "heartbeat_at": iso(time.time() - age),
                     "checkpoint": {"agent": identity.to_dict(), "run_tag": "w41-none"}}
            return claim_view(claim, run)["status"]
        assert view(me, 0) == "running"
        assert view(me, 5) == "slow"  # beyond the stale threshold: reported, never a reason to stop
        assert view(me, 70) == "hung"
        assert view(me, 0, dataclasses.replace(runtime, task_timeout_seconds=0, terminate_grace_seconds=0)) == \
            "deadline-exceeded"  # the agent outlived its run deadline
        assert view(dead_identity(), 0) == "orphaned"  # the holder is gone, the agent is not
        agent.kill()
        agent.wait(timeout=10)
        assert view(dead_identity(), 0) == "stale"
        assert view({"pid": os.getpid()}, 0) == "ambiguous"
    finally:
        if agent.poll() is None:
            agent.kill()
            agent.wait(timeout=10)


def test_diagnosis_names_the_most_urgent_condition() -> None:
    record = ServiceRecord(worker_id="w", state="BUSY")

    def task(status: str, view: str | None = None, **extra: Any) -> dict[str, Any]:
        return {"status": status, **({"claim": {"watchdog": {"status": view}}} if view else {}), **extra}
    assert diagnose("AVAILABLE", record, [], []) == "idle"
    assert diagnose("BUSY", record, [task("running", "running")], []) == "agent-running"
    assert diagnose("BUSY", record, [task("running", "slow")], []) == "agent-slow"
    assert diagnose("BUSY", record, [task("running", "deadline-exceeded")], []) == "agent-deadline-exceeded"
    assert diagnose("UNRESPONSIVE", record, [task("running", "running")], []) == "service-heartbeat-stale"
    assert diagnose("WAITING", record, [task("orphaned", "orphaned")], []) == "orphan-detected"
    terminating = ServiceRecord(worker_id="w", state="BUSY", current={"watchdog": {"phase": "terminating"}})
    assert diagnose("BUSY", terminating, [task("running", "hung")], []) == "termination-in-progress"
    uncertain = ServiceRecord(worker_id="w", state="NEEDS_ATTENTION", last_error={"category": "cleanup-uncertain"})
    assert diagnose("NEEDS_ATTENTION", uncertain, [task("orphaned", "orphaned")], []) == "cleanup-uncertain"
    assert diagnose("OFFLINE", None, [task("needs-attention", blocking_processes=[1])], []) == "cleanup-uncertain"
    assert diagnose("NEEDS_ATTENTION", record, [task("needs-attention")], []) == "attention-required"
    assert diagnose("WAITING", record, [task("waiting-retry")], [{"task": "x"}]) == "waiting"
    assert diagnose("OFFLINE", None, [], []) == "offline"


# -- 10. stop, stop --after-current and pause while an agent hangs -----------------------------------------------------

def hanging_task(project: Project, slug: str) -> tuple[Path, Path]:
    agent, sigterms = project.dir / "agent.id", project.dir / "sigterm.log"
    activate(project, slug, [{"key": "api", "role": "backend", "title": actions(
        {"do": "append", "path": str(project.audit), "text": "run\n"},
        {"do": "once", "path": str(project.dir / "first-run"), "actions": [
            {"do": "pidfile", "path": str(agent)}, {"do": "trap_sigterm", "path": str(sigterms)},
            {"do": "sleep", "seconds": 600}]},
        {"do": "check_gone", "pid_file": str(agent), "path": str(project.audit), "label": "previous"},
        {"do": "write", "path": "backend/src/api.py", "text": "API = 1\n"})}])
    return agent, sigterms


def test_graceful_stop_during_a_hung_agent(tmp_path: Path, fake_home: Path, fake_agent: Path, services: Services,
                                           reaper: Path) -> None:
    """`worker stop` while the agent hangs (SIGTERM ignored), long before its deadline: the launch
    cancels it (SIGTERM, grace, SIGKILL), the claim is released only then, the outcome is
    "cancelled" (no budget used), the service exits 0; nothing is terminated twice."""
    del fake_home, reaper
    project = make_project(tmp_path, fake_agent, runtime={"task_timeout_seconds": 120, "terminate_grace_seconds": 1})
    agent, sigterms = hanging_task(project, "w41h")
    proc, log = services.start(project)
    assert wait_until(agent.exists, 60), log.read_text(encoding="utf-8")
    hung = read_identity(agent)
    stop = project.cli("worker", "stop")
    assert stop.returncode == 0, stop.stdout + stop.stderr
    assert proc.wait(timeout=60) == 0, log.read_text(encoding="utf-8")
    assert not hung.is_alive() and [line.split()[1] for line in lines(sigterms)] == [str(hung.pid)]
    assert not project.events("worker-watchdog-hung") and not project.events("worker-watchdog-terminated")
    rec = project.record("scheduling", "w41h.api")
    assert history(project, "w41h.api") == ["cancelled"] and rec["attempts"] == {} and rec["launches"] == 1
    assert project.task("w41h.api")["state"] == "ready" and not list(project.state.glob("claims/*.json"))
    code, out = services.run(project, "--exit-when-idle")
    assert code == 0, out
    assert lines(project.audit) == ["run", "run", "previous gone"]
    assert history(project, "w41h.api") == ["cancelled", "success"]
    assert_clean(project)


def test_stop_after_current_waits_for_the_deadline_then_does_not_retry(tmp_path: Path, fake_home: Path,
                                                                       fake_agent: Path, services: Services,
                                                                       reaper: Path) -> None:
    del fake_home, reaper
    project = make_project(tmp_path, fake_agent, runtime={"task_timeout_seconds": 4, "terminate_grace_seconds": 1})
    agent, _sigterms = hanging_task(project, "w41i")
    proc, log = services.start(project)
    assert wait_until(agent.exists, 60), log.read_text(encoding="utf-8")
    assert project.cli("worker", "stop", "--after-current").returncode == 0
    assert proc.wait(timeout=60) == 0, log.read_text(encoding="utf-8")
    rec = project.record("scheduling", "w41i.api")
    assert history(project, "w41i.api") == ["transient"] and rec["next_attempt_at"] and rec["launches"] == 1
    assert lines(project.audit) == ["run"] and not read_identity(agent).is_alive()  # no retry after the stop
    code, out = services.run(project, "--exit-when-idle")
    assert code == 0, out
    assert history(project, "w41i.api") == ["transient", "success"] and lines(project.audit)[-1] == "previous gone"
    assert_clean(project)


def test_pause_during_a_hung_agent_holds_the_retry_until_resume(tmp_path: Path, fake_home: Path, fake_agent: Path,
                                                                services: Services, reaper: Path) -> None:
    del fake_home, reaper
    project = make_project(tmp_path, fake_agent, runtime={"task_timeout_seconds": 4, "terminate_grace_seconds": 1})
    agent, _sigterms = hanging_task(project, "w41j")
    proc, log = services.start(project, "--exit-when-idle")
    assert wait_until(agent.exists, 60), log.read_text(encoding="utf-8")
    assert project.cli("worker", "pause").returncode == 0
    due: dict[str, float] = {}

    def retry_due_while_paused() -> bool:
        rec = project.record("scheduling", "w41j.api") or {}
        if rec.get("next_attempt_at") and "due" not in due:
            due["due"] = rec["next_attempt_at"]
        return bool(due) and time.time() > due["due"] + 3
    assert wait_until(retry_due_while_paused, 60), log.read_text(encoding="utf-8")
    assert (project.record("service", "worker") or {}).get("state") == "PAUSED"
    assert project.record("scheduling", "w41j.api")["launches"] == 1 and lines(project.audit) == ["run"]
    assert project.cli("worker", "resume").returncode == 0
    assert proc.wait(timeout=60) == 0, log.read_text(encoding="utf-8")
    assert history(project, "w41j.api") == ["transient", "success"]
    assert lines(project.audit) == ["run", "run", "previous gone"]
    assert_clean(project)
