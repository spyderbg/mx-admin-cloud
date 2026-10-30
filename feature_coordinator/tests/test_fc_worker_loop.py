"""W2: ONE Worker drives a whole feature through several specialized agents.

Scheduling is tested in-process (mock agents, `run_task` in threads). The complete local
workflow is tested end to end with real processes: `python -m feature_coordinator plan / activate / work
--integrate` on a disposable repository, the native-cli backend and the fake agent standing in
for Claude Code (Backend, CLI) and Codex (Frontend). No model, no GitHub, no AWS, no W1 parts."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest
import yaml

from feature_coordinator import CLI
from feature_coordinator.cli import EXAMPLE_CONFIG
from feature_coordinator.cli import main as cli_main
from feature_coordinator.demo import RecordingGates, build_coordinator, init_demo_repo
from feature_coordinator.errors import CoordinatorError
from feature_coordinator.model import TaskState
from feature_coordinator.operations import SELF_TEST_FILES
from feature_coordinator.runners import FileEdit, RunResult
from feature_coordinator.worker import FeatureWorker, cli_spawner
from feature_coordinator.workflow import FeatureSpec, TaskSpec
from feature_coordinator.workspace import WorkspaceError

from fc_helpers import child_env, git



class ThreadHandle:
    """A started task for the in-process tests (a thread running coord.run_task)."""

    def __init__(self, func) -> None:
        self.code: int | None = None
        self.signals: list[int] = []
        self.thread = threading.Thread(target=self._run, args=(func,), daemon=True)
        self.thread.start()

    def _run(self, func) -> None:
        try:
            func()
            self.code = 0
        except Exception:  # pylint: disable=broad-except
            self.code = 1

    def poll(self) -> int | None:
        return None if self.thread.is_alive() else self.code

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)

    def wait(self, timeout: float | None = None) -> int:
        self.thread.join(timeout)
        return self.code or 0


class Recorder:  # pylint: disable=too-few-public-methods
    """Wraps a runner: records start/end order and can make two runs meet at a barrier."""

    def __init__(self, runner, log: list, barrier: threading.Barrier | None = None, tasks=()) -> None:
        self.runner, self.log, self.barrier, self.tasks = runner, log, barrier, set(tasks)
        self.name, self.kind = runner.name, runner.kind

    def run(self, request):
        self.log.append(("start", request.task_id))
        if self.barrier is not None and request.task_id in self.tasks:
            self.barrier.wait(timeout=20)  # only passes if both tasks are running at the same time
        result = self.runner.run(request)
        self.log.append(("end", request.task_id))
        return result


def spec(*extra: TaskSpec) -> FeatureSpec:
    return FeatureSpec("w2", "Worker", (TaskSpec("api", "backend", "API"),
                                        TaskSpec("cmd", "cli", "Command", depends_on=("api",)),
                                        TaskSpec("view", "frontend", "View", depends_on=("api",)), *extra))


SCRIPTS = {"backend": {"w2.api": [FileEdit("backend/src/app.py", "ROUTES = ['/x']\n")],
                       "w2.api2": [FileEdit("backend/src/second.py", "SECOND = 1\n")]},
           "cli": {"w2.cmd": [FileEdit("cli/src/commands.py", "COMMANDS = ['x']\n")]},
           "frontend": {"w2.view": [FileEdit("frontend/src/view.ts", "export const views = ['x']\n")]}}


@pytest.fixture
def mock_coord(repo: Path, state_dir: Path):
    return build_coordinator(repo, state_dir, scripts=SCRIPTS, gates=RecordingGates())


def thread_spawner(coord):
    """Each task runs on its OWN coordinator over the same checkout and state directory, like a
    separate `python -m feature_coordinator launch` process (a StateStore instance is not shared between threads)."""
    def spawn(task_id, attempt):
        del attempt
        own = build_coordinator(coord.root, coord.store.root, config=coord.config, gates=coord.gate_runner)
        own.runners = coord.runners
        return ThreadHandle(lambda: own.run_task(task_id))
    return spawn


# -- scheduling (in-process) --------------------------------------------------------------------------

def test_dependencies_first_then_independent_tasks_concurrently(mock_coord) -> None:
    log: list = []
    barrier = threading.Barrier(2)
    for role in ("backend", "cli", "frontend"):
        mock_coord.runners[role] = Recorder(mock_coord.runners[role], log, barrier, ("w2.cmd", "w2.view"))
    feature = mock_coord.activate(mock_coord.plan(spec()).id)
    report = FeatureWorker(mock_coord, thread_spawner(mock_coord), poll_interval=0.02).run(feature.id)
    assert report.all_verified and report.max_concurrency == 2
    assert log.index(("end", "w2.api")) < min(log.index(("start", "w2.cmd")), log.index(("start", "w2.view")))
    assert {o.task_id for o in report.launches} == {"w2.api", "w2.cmd", "w2.view"}
    roles = [git(mock_coord.root, "log", "-1", "--format=%(trailers:key=Feature-Role,valueonly)", sha).strip()
             for sha in git(mock_coord.root, "rev-list", f"{feature.base_sha}..{feature.branch}").split()]
    assert sorted(roles) == ["backend", "cli", "frontend"]  # one serialized commit per role
    assert mock_coord.integrate(feature.id, gates=()).state.value == "review"


def test_one_writer_per_role_and_max_parallel(mock_coord) -> None:
    active, peak = [0], [0]
    lock = threading.Lock()

    class Counting:  # pylint: disable=too-few-public-methods
        """Counts Backend runs in flight."""

        def __init__(self, runner):
            self.runner, self.name, self.kind = runner, runner.name, runner.kind

        def run(self, request):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.3)
            try:
                return self.runner.run(request)
            finally:
                with lock:
                    active[0] -= 1
    mock_coord.runners["backend"] = Counting(mock_coord.runners["backend"])
    feature = mock_coord.activate(mock_coord.plan(FeatureSpec("w2", "Two backend tasks", (
        TaskSpec("api", "backend", "A"), TaskSpec("api2", "backend", "B")))).id)
    report = FeatureWorker(mock_coord, thread_spawner(mock_coord), max_parallel=4, poll_interval=0.02).run(feature.id)
    assert report.all_verified and peak[0] == 1 and report.max_concurrency == 1  # never two Backend writers


def test_a_failed_task_keeps_its_dependents_blocked(repo: Path, state_dir: Path) -> None:
    coord = build_coordinator(repo, state_dir, scripts=SCRIPTS, gates=RecordingGates(failing=["backend-tests"]))
    feature = coord.activate(coord.plan(FeatureSpec("w2", "Fails", (
        TaskSpec("api", "backend", "API", required_gates=("backend-tests",)),
        TaskSpec("cmd", "cli", "Command", depends_on=("api",))))).id)
    report = FeatureWorker(coord, thread_spawner(coord), poll_interval=0.02).run(feature.id)
    assert report.states == {"w2.api": "failed", "w2.cmd": "blocked"} and not report.all_verified
    assert [o.task_id for o in report.launches] == ["w2.api"] and "waiting for" in report.skipped["w2.cmd"]


def test_deferred_tasks_are_not_retried_and_disabled_roles_never_run(mock_coord) -> None:
    class Unavailable:  # pylint: disable=too-few-public-methods
        """A provider that is down."""

        name, kind = "claude", None

        def run(self, request):
            del request
            return RunResult("claude", "unavailable", error="provider down")
    mock_coord.runners["cli"] = Unavailable()
    feature = mock_coord.activate(mock_coord.plan(spec(TaskSpec("e2e", "automation", "E2E"))).id)
    report = FeatureWorker(mock_coord, thread_spawner(mock_coord), max_attempts=1, poll_interval=0.02).run(feature.id)
    assert report.states["w2.cmd"] == "ready" and "not relaunched" in report.skipped["w2.cmd"]
    assert [o.task_id for o in report.launches].count("w2.cmd") == 1  # no unbounded retries
    assert report.states["w2.e2e"] == "ready" and "disabled" in report.skipped["w2.e2e"]
    assert "w2.e2e" not in [o.task_id for o in report.launches] and not report.all_verified


def test_one_worker_per_feature_and_workspace_checks(mock_coord) -> None:
    feature = mock_coord.activate(mock_coord.plan(spec()).id)
    held = mock_coord.store.locks.worker(feature.id).acquire()
    try:
        with pytest.raises(CoordinatorError, match="another Worker"):
            FeatureWorker(mock_coord, thread_spawner(mock_coord)).run(feature.id)
    finally:
        held.release()
    with mock_coord.store.transaction():
        record = mock_coord.store.load_feature(feature.id)
        record.workspace = {"root_commits": ["0" * 40], "origin": ""}
        mock_coord.store.save_feature(record)
    with pytest.raises(WorkspaceError, match="identity mismatch"):
        FeatureWorker(mock_coord, thread_spawner(mock_coord)).run(feature.id)
    with pytest.raises(WorkspaceError, match="identity mismatch"):
        mock_coord.run_task("w2.api")  # every run checks it, not only the Worker


def test_stop_request_reaches_every_running_task(mock_coord) -> None:
    gate = threading.Event()
    handles: list[ThreadHandle] = []

    def spawn(task_id, attempt):
        del attempt
        handles.append(ThreadHandle(lambda: gate.wait(5) and mock_coord.store.load_task(task_id)))
        return handles[-1]
    feature = mock_coord.activate(mock_coord.plan(spec()).id)
    report = FeatureWorker(mock_coord, spawn, stop=lambda: bool(handles), poll_interval=0.02).run(feature.id)
    gate.set()
    assert report.stopped and handles and all(h.signals == [signal.SIGTERM] for h in handles)


# -- the complete local workflow (real processes, native-cli, fake agents) --------------------------------

def write_config(path: Path, agent: Path) -> Path:
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    for name in ("claude", "codex"):
        data["providers"][name]["executable"] = str(agent)
    data["gates"]["unit-check"] = ["python3", "-c", "print('unit ok')"]
    data["runtime"].update(task_timeout_seconds=120, terminate_grace_seconds=2)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def feature_cli(repo: Path, state: Path, config: Path, *args: str, timeout: float = 240):
    return subprocess.run([*CLI, "--repo", str(repo), "--state-dir", str(state),
                           "--project-config", str(config), *args], capture_output=True, text=True,
                          env=child_env(), timeout=timeout, check=False)


def actions(*steps) -> str:
    return "FAKE-ACTIONS:" + json.dumps(list(steps))


@pytest.mark.usefixtures("needs_bwrap")  # the verification gate runs in the existing gate sandbox
def test_complete_local_worker_workflow(tmp_path: Path, fake_home: Path, fake_agent: Path) -> None:  # pylint: disable=too-many-locals
    del fake_home
    repo, state = tmp_path / "repo", tmp_path / "state"
    init_demo_repo(repo, SELF_TEST_FILES)
    config = write_config(tmp_path / "config.yaml", fake_agent)
    def meet(me: str, other: str, path: str, other_path: str) -> str:  # CLI and Frontend must overlap to pass
        return actions({"do": "write", "path": f"{me}/.started", "text": "1"},
                       {"do": "wait_for", "path": f"{other}/.started", "timeout": 60},
                       {"do": "write", "path": path, "text": f"# written by the {me} agent\n"},
                       {"do": "wait_for", "path": other_path, "timeout": 60},
                       {"do": "delete", "path": f"{me}/.started"})
    spec_file = tmp_path / "feature.yaml"
    spec_file.write_text(yaml.safe_dump({"slug": "w2-demo", "title": "Test Feature", "tasks": [
        {"key": "api", "role": "backend", "required_gates": ["unit-check"],
         "title": actions({"do": "write", "path": "backend/src/api.py", "text": "API = 1\n"})},
        {"key": "cmd", "role": "cli", "depends_on": ["api"],
         "title": meet("cli", "frontend", "cli/src/cmd.py", "frontend/src/new_view.ts")},
        {"key": "view", "role": "frontend", "depends_on": ["api"],
         "title": meet("frontend", "cli", "frontend/src/new_view.ts", "cli/src/cmd.py")}]}), encoding="utf-8")
    for step in (("plan", str(spec_file), "--apply"), ("activate", "w2-demo", "--apply")):
        result = feature_cli(repo, state, config, *step)
        assert result.returncode == 0, result.stdout + result.stderr
    dry = feature_cli(repo, state, config, "work", "w2-demo")
    assert dry.returncode == 0 and "native-cli (NOT a security sandbox)" in dry.stdout and "model opus" in dry.stdout
    work = feature_cli(repo, state, config, "work", "w2-demo", "--apply", "--max-parallel", "3", "--integrate",
                       "--integration-gate", "unit-check")  # W4: parallelism is explicit (the default is 1)
    assert work.returncode == 0, work.stdout + work.stderr
    assert "3/3 task(s) verified" in work.stdout and "up to 2 at once" in work.stdout and "review" in work.stdout

    events = [json.loads(line) for line in (state / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    at = {(e["kind"], e.get("task")): i for i, e in enumerate(events) if e["kind"] in ("claim", "release")}
    verified = next(i for i, e in enumerate(events) if e["kind"] == "task-finished" and e["task"] == "w2-demo.api")
    assert events[verified]["state"] == "verified"
    assert verified < at[("claim", "w2-demo.cmd")] and verified < at[("claim", "w2-demo.view")]  # dependency first
    assert at[("claim", "w2-demo.cmd")] < at[("release", "w2-demo.view")]   # ... then CLI and Frontend overlap
    assert at[("claim", "w2-demo.view")] < at[("release", "w2-demo.cmd")]

    branch, base = "feature/w2-demo", git(repo, "merge-base", "dev", "feature/w2-demo").strip()
    commits = git(repo, "rev-list", "--reverse", f"{base}..{branch}").split()
    owned = {"backend": "backend/", "cli": "cli/", "frontend": "frontend/"}
    roles = []
    for sha in commits:  # serialized, one per role, each inside its role
        role = git(repo, "log", "-1", "--format=%(trailers:key=Feature-Role,valueonly)", sha).strip()
        files = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()
        assert files and all(f.startswith(owned[role]) for f in files), (role, files)
        roles.append(role)
    assert roles[0] == "backend" and sorted(roles) == ["backend", "cli", "frontend"]
    assert git(repo, "status", "--porcelain").strip() == ""
    status = feature_cli(repo, state, config, "status")
    assert "review" in status.stdout and not list(state.glob("**/pull-request*.json"))
    runs = sorted(state.glob("runs/*/native.json"))
    models = {json.loads(p.read_text(encoding="utf-8"))["model"] for p in runs}
    assert len(runs) == 3 and models == {"opus", "sonnet", "CLI default"}


def test_worker_cancellation_stops_the_agents(tmp_path: Path, fake_home: Path, fake_agent: Path) -> None:
    del fake_home
    repo, state = tmp_path / "repo", tmp_path / "state"
    init_demo_repo(repo, SELF_TEST_FILES)
    config = write_config(tmp_path / "config.yaml", fake_agent)
    spec_file = tmp_path / "feature.yaml"
    spec_file.write_text(yaml.safe_dump({"slug": "w2-stop", "title": "Stop", "tasks": [
        {"key": "api", "role": "backend", "title": actions(
            {"do": "write", "path": "backend/src/partial.py", "text": "P = 1\n"},
            {"do": "daemon", "path": "backend/beat.log", "interval": 0.05}, {"do": "sleep", "seconds": 60})}]}),
        encoding="utf-8")
    for step in (("plan", str(spec_file), "--apply"), ("activate", "w2-stop", "--apply")):
        assert feature_cli(repo, state, config, *step).returncode == 0
    proc = subprocess.Popen([*CLI, "--repo", str(repo), "--state-dir", str(state),  # pylint: disable=consider-using-with
                             "--project-config", str(config), "work", "w2-stop", "--apply"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=child_env())
    deadline = time.monotonic() + 60
    while not (repo / "backend/beat.log").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    os.kill(proc.pid, signal.SIGTERM)
    out, err = proc.communicate(timeout=120)
    assert proc.returncode == 1 and "STOPPED" in out, out + err
    beat = repo / "backend/beat.log"
    size = beat.stat().st_size
    time.sleep(0.5)
    assert beat.stat().st_size == size  # the detached daemon is gone too
    task = json.loads(next(state.glob("tasks/w2-stop.api.json")).read_text(encoding="utf-8"))
    assert task["state"] == TaskState.READY.value and not list(state.glob("claims/*.json"))
    assert (repo / "backend/src/partial.py").exists()  # uncommitted work stays for the next run


# -- the subscription smoke test's --apply path (fake agents: no model, no subscription use) -------------------

def smoke_cli(tmp_path: Path, config: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*CLI, "--state-dir", str(tmp_path / "state"),
                           "--project-config", str(config), "subscription-smoke", *args], capture_output=True,
                          text=True, env=child_env(), cwd=tmp_path, timeout=240, check=False)


def smoke_result(stdout: str) -> dict:
    start = stdout.rindex("\n{\n  \"repository\"")  # the result JSON printed after the run
    return json.loads(stdout[start:stdout.index("\n}\n", start) + 2])


def wait_until(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def rendezvous(sync: Path, me: str, peer: str) -> dict:
    """Fake-agent actions around writing the smoke file: arrive, wait for the peer agent, then wait
    for the test's release. Neither agent can finish before both are running."""
    return {"before": [{"do": "write", "path": str(sync / f"{me}.arrived"), "text": "1"},
                       {"do": "wait_for", "path": str(sync / f"{peer}.arrived"), "timeout": 120, "required": True},
                       {"do": "wait_for", "path": str(sync / "release"), "timeout": 120, "required": True}],
            "after": [{"do": "write", "path": str(sync / f"{me}.done"), "text": "1"}]}


def live_processes_mentioning(text: str) -> list[str]:
    """Command lines of running processes that mention *text*."""
    found = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/cmdline", "rb") as handle:
                cmdline = handle.read()
        except OSError:
            continue
        if text.encode() in cmdline:
            found.append(cmdline.replace(b"\0", b" ").decode(errors="replace"))
    return found


def test_subscription_smoke_apply_runs_the_bounded_feature(tmp_path: Path, fake_home: Path, fake_agent: Path,  # pylint: disable=too-many-locals,too-many-statements
                                                           capsys: pytest.CaptureFixture[str],
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    del fake_home
    sync = tmp_path / "sync"
    sync.mkdir()
    Path(f"{fake_agent}.json").write_text(json.dumps({"follow_prompt": True, "around": {
        "cli/smoke/cli.txt": rendezvous(sync, "cli", "frontend"),
        "frontend/smoke/frontend.txt": rendezvous(sync, "frontend", "cli")}}), encoding="utf-8")
    config, workdir = write_config(tmp_path / "config.yaml", fake_agent), tmp_path / "smoke"
    dry = smoke_cli(tmp_path, config, "--workdir", str(workdir))
    assert dry.returncode == 0 and "no model request was made" in dry.stdout, dry.stdout + dry.stderr
    assert not workdir.exists()

    # The Worker samples its running tasks once per polling iteration (FeatureWorker._run). A
    # pass-through spy on _eligible (called once per iteration, before that iteration's sample)
    # counts the iterations, so the agents are released only after the Worker has provably
    # sampled while both were running. The --apply run is therefore in-process (a thread).
    polls: list[float] = []
    eligible = FeatureWorker._eligible  # pylint: disable=protected-access

    def counting(self, *args):
        chosen = eligible(self, *args)
        polls.append(time.monotonic())
        return chosen
    monkeypatch.setattr(FeatureWorker, "_eligible", counting)
    outcome: dict = {}

    def apply() -> None:
        try:
            outcome["code"] = cli_main(["--state-dir", str(tmp_path / "state"), "--project-config", str(config),
                                        "subscription-smoke", "--apply", "--workdir", str(workdir)])
        except BaseException as exc:  # pylint: disable=broad-except
            outcome["error"] = exc
    runner = threading.Thread(target=apply, daemon=True)
    runner.start()
    try:
        assert wait_until(lambda: (sync / "cli.arrived").exists() and (sync / "frontend.arrived").exists(), 120), \
            f"the CLI and Frontend agents were never running at the same time: {sorted(p.name for p in sync.iterdir())}"
        # Both agents are running, so both tasks are in progress until the release. Iteration n may
        # have taken its snapshot before this point, but iteration n+1 took it after; its sample is
        # complete once iteration n+2 calls _eligible.
        seen = len(polls)
        assert wait_until(lambda: len(polls) >= seen + 3, 30), "the Worker stopped polling while both agents ran"
    finally:
        (sync / "release").write_text("go", encoding="utf-8")  # always: never leave agents waiting
        runner.join(240)
    assert not runner.is_alive() and "error" not in outcome, outcome
    out = capsys.readouterr().out
    assert outcome["code"] == 0 and "subscription smoke test: PASSED" in out, out
    result = smoke_result(out)
    assert set(result["states"].values()) == {"verified"} and len(result["states"]) == 3
    assert result["max_concurrency"] == 2 and not result["skipped"] and len(result["launches"]) == 3
    assert all(f["on_branch"] and f["content_ok"] for f in result["files"].values())
    assert {r["model"] for r in result["runs"].values()} == {"opus", "sonnet", "CLI default"}
    assert result["integration"] == {"state": "review", "pr_opened": False, "head": result["integration"]["head"]}
    repo, state = workdir / "repo", workdir / "state"
    assert (state.stat().st_mode & 0o777) == 0o700 and not list(state.glob("**/pull-request*.json"))

    # Dependency order, then true overlap (from the coordinator's own event log).
    events = [json.loads(line) for line in (state / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    at = {(e["kind"], e.get("task")): i for i, e in enumerate(events) if e["kind"] in ("claim", "release")}
    verified = next(i for i, e in enumerate(events) if e["kind"] == "task-finished"
                    and e["task"] == "subscription-smoke.backend")
    assert events[verified]["state"] == "verified"
    cli, frontend = "subscription-smoke.cli", "subscription-smoke.frontend"
    assert verified < at[("claim", cli)] and verified < at[("claim", frontend)]  # Backend first
    assert at[("claim", cli)] < at[("release", frontend)] and at[("claim", frontend)] < at[("release", cli)]
    # Neither agent finished before both had started (markers outside the repository).
    arrived = [(sync / f"{r}.arrived").stat().st_mtime_ns for r in ("cli", "frontend")]
    done = [(sync / f"{r}.done").stat().st_mtime_ns for r in ("cli", "frontend")]
    assert max(arrived) <= (sync / "release").stat().st_mtime_ns <= min(done)

    branch = "feature/subscription-smoke"
    base = git(repo, "merge-base", "dev", branch).strip()
    roles, subjects = {}, {}
    for sha in git(repo, "rev-list", f"{base}..{branch}").split():  # one commit per role, inside its role
        role = git(repo, "log", "-1", "--format=%(trailers:key=Feature-Role,valueonly)", sha).strip()
        roles[role] = git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", sha).split()
        subjects[role] = git(repo, "log", "-1", "--format=%s", sha).strip()
    assert roles == {"backend": ["backend/smoke/backend.txt"], "cli": ["cli/smoke/cli.txt"],
                     "frontend": ["frontend/smoke/frontend.txt"]}
    assert subjects == {r: f"{r}: task subscription-smoke.{r}" for r in roles}  # the prompts are not subjects
    assert git(repo, "status", "--porcelain", "--untracked-files=all").strip() == ""  # nothing else changed
    assert sorted(git(repo, "ls-tree", "-r", "--name-only", branch).split()) == sorted(
        ["README.md", "backend/README.md", "cli/README.md", "frontend/README.md", "backend/smoke/backend.txt",
         "cli/smoke/cli.txt", "frontend/smoke/frontend.txt"])
    # No agent and no launch process outlives the run (the Worker waits for launches, launches for agents).
    leftovers = live_processes_mentioning(str(workdir)) + live_processes_mentioning(str(fake_agent))
    assert not [c for c in leftovers if " github sync " not in c], leftovers
    # The launches' board publishers (operations.DetachedPublisher: detached, never waited for, by design)
    # finish on their own shortly after; none may linger.
    assert wait_until(lambda: not live_processes_mentioning(str(workdir)), 60), live_processes_mentioning(str(workdir))


def test_subscription_smoke_refuses_without_subscription(tmp_path: Path, fake_home: Path, fake_agent: Path) -> None:
    del fake_home
    Path(f"{fake_agent}.json").write_text(json.dumps({"auth": "api-key", "follow_prompt": True}), encoding="utf-8")
    config, workdir = write_config(tmp_path / "config.yaml", fake_agent), tmp_path / "smoke"
    run = smoke_cli(tmp_path, config, "--apply", "--workdir", str(workdir))
    assert run.returncode == 1 and "BLOCKER: backend" in run.stdout and "BLOCKER: frontend" in run.stdout
    assert not workdir.exists()  # nothing created, no agent started


def test_a_spawned_launch_imports_this_package_from_any_working_directory(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`python -m feature_coordinator` children start in the package's root, so a caller in an unrelated
    directory, without PYTHONPATH (like the systemd service's own children), still runs this code."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    proc = cli_spawner(tmp_path / "no-repo", tmp_path / "state", log_dir=tmp_path / "logs")("x.y", 1)
    assert proc.wait(timeout=60) == 1
    log = (tmp_path / "logs" / "work-x.y.1.log").read_text(encoding="utf-8")
    assert "No module named" not in log and "unknown task 'x.y'" in log, log  # it ran this package's code
