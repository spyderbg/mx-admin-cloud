"""W4: the autonomous Worker service as REAL processes (`feature.py worker start`).

Real temporary Git repositories, the real launch path (`feature.py launch TASK --apply` →
native-cli backend → agent), the fake agent CLI (fc_fake_agent.py: no model, no subscription
use, fake credentials under a fake HOME), real signals and SIGKILLs. Synchronization is by marker
files the agents write and wait for — no fixed sleeps decide an outcome. Faults inside the
service are injected by a test-only `sitecustomize` (SERVICE_HOOK); a frozen launch reuses the
W3.2 boundary hook (fc_recovery_smoke.HOOK). Scenarios: C restart, H orphaned agent, I crash
loop, K graceful stop, P a second service and a manual launch, Q a crash during bookkeeping."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml

from feature_coordinator.autonomy import read_control
from feature_coordinator.cli import EXAMPLE_CONFIG
from feature_coordinator.demo import init_demo_repo
from feature_coordinator.locks import ProcessIdentity
from feature_coordinator.store import StateStore

from fc_helpers import child_env, git
from fc_recovery_smoke import HOOK as HALT_HOOK
from test_fc_worker_loop import FEATURE_PY, actions, wait_until

SERVICE_HOOK = r'''"""TEST-ONLY fault injection (test_fc_service_process.py): with FC_TEST_SERVICE_CRASH set, a
`feature.py worker start` process dies at once (os._exit, like losing the service alone) at one
bookkeeping point. Launch processes are not affected. Never part of the coordinator."""
import importlib.machinery, os, sys
_POINT = os.environ.get("FC_TEST_SERVICE_CRASH", "")

def _patch(module):
    cls = module.WorkerService
    init, judge = cls.__init__, cls._judge
    def patched_init(self, *args, **kwargs):
        init(self, *args, **kwargs)
        spawn = self.spawn
        def crashing_spawn(task_id, launch, **kwargs):
            if _POINT == "before-spawn":   # the launch is counted (write-ahead), not started
                os._exit(70)
            handle = spawn(task_id, launch, **kwargs)
            if _POINT == "after-spawn":    # the launch runs on without its service
                os._exit(70)
            return handle
        self.spawn = crashing_spawn
    def crashing_judge(self, *args, **kwargs):
        outcome = judge(self, *args, **kwargs)
        if _POINT == "before-outcome":     # the launch ended; its outcome is not recorded
            os._exit(70)
        return outcome
    cls.__init__, cls._judge = patched_init, crashing_judge

class _Finder:
    @staticmethod
    def find_spec(name, path=None, target=None):
        if name != "feature_coordinator.service":
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is not None and spec.loader is not None:
            run = spec.loader.exec_module
            def exec_module(module):
                run(module)
                _patch(module)
            spec.loader.exec_module = exec_module
        return spec

if _POINT and "worker" in sys.argv and "start" in sys.argv:
    sys.meta_path.insert(0, _Finder)
'''


@dataclass
class Project:
    """One disposable repository, state directory and configuration."""
    dir: Path
    repo: Path
    state: Path
    config: Path
    audit: Path

    def cli(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        return subprocess.run([sys.executable, str(FEATURE_PY), "--repo", str(self.repo), "--state-dir",
                               str(self.state), "--project-config", str(self.config), *args], capture_output=True,
                              text=True, env=child_env(), timeout=timeout, check=False)

    def service_argv(self, *extra: str) -> list[str]:
        return [sys.executable, str(FEATURE_PY), "--repo", str(self.repo), "--state-dir", str(self.state),
                "--project-config", str(self.config), "worker", "start", *extra]

    def record(self, kind: str, record_id: str) -> dict[str, Any] | None:
        path = self.state / kind / f"{record_id}.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def task(self, task_id: str) -> dict[str, Any]:
        return self.record("tasks", task_id) or {}

    def audit_lines(self) -> list[str]:
        return self.audit.read_text(encoding="utf-8").split() if self.audit.exists() else []

    def events(self, kind: str) -> list[dict[str, Any]]:
        lines = (self.state / "events.jsonl").read_text(encoding="utf-8").splitlines()
        return [e for e in map(json.loads, lines) if e["kind"] == kind]

    def task_commits(self, branch: str) -> list[str]:
        base = git(self.repo, "merge-base", "dev", branch).strip()
        return [line for line in git(self.repo, "log", "--format=%(trailers:key=Feature-Task,valueonly)",
                                     f"{base}..{branch}").split("\n") if line.strip()]


class Services:
    """Started service processes; any still running at the end are stopped (process group)."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp, self.procs, self.count = tmp_path, [], 0

    def start(self, project: Project, *extra: str, env: dict[str, str] | None = None,
              hooks: Path | None = None) -> tuple[subprocess.Popen, Path]:
        self.count += 1
        log = self.tmp / f"service-{self.count}.log"
        environment = child_env()
        environment.update(env or {})
        if hooks is not None:
            environment["PYTHONPATH"] = f"{hooks}{os.pathsep}{environment['PYTHONPATH']}"
        with open(log, "wb") as out:
            proc = subprocess.Popen(project.service_argv(*extra), stdout=out, stderr=subprocess.STDOUT,  # pylint: disable=consider-using-with
                                    env=environment, start_new_session=True)
        self.procs.append(proc)
        return proc, log

    def run(self, project: Project, *extra: str, timeout: float = 180, **kwargs: Any) -> tuple[int, str]:
        proc, log = self.start(project, *extra, **kwargs)
        code = proc.wait(timeout=timeout)
        return code, log.read_text(encoding="utf-8", errors="replace")

    def cleanup(self) -> None:
        for proc in self.procs:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=30)


@pytest.fixture
def services(tmp_path: Path):
    started = Services(tmp_path)
    yield started
    started.cleanup()


def make_project(tmp_path: Path, agent: Path, *, runtime: dict | None = None, worker: dict | None = None) -> Project:
    repo, state = tmp_path / "repo", tmp_path / "state"
    init_demo_repo(repo)
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    for name in ("claude", "codex"):
        data["providers"][name]["executable"] = str(agent)
    data["runtime"].update({"task_timeout_seconds": 90, "terminate_grace_seconds": 2, **(runtime or {})})
    data["worker"]["autonomous"]["enabled"] = True
    data["worker"].update(idle_poll_seconds=1, heartbeat_seconds=1)
    data["worker"]["retry"]["transient"].update(initial_delay_seconds=1, max_delay_seconds=2)
    for key, value in (worker or {}).items():
        data["worker"][key] = value
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return Project(tmp_path, repo, state, config, tmp_path / "audit.log")


def activate(project: Project, slug: str, tasks: list[dict[str, Any]]) -> None:
    spec = project.dir / f"{slug}.yaml"
    spec.write_text(yaml.safe_dump({"slug": slug, "title": f"W4 {slug}", "tasks": tasks}), encoding="utf-8")
    for step in (("plan", str(spec), "--apply"), ("activate", slug, "--apply")):
        result = project.cli(*step)
        assert result.returncode == 0, result.stdout + result.stderr


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass  # another user's process: it exists
    stat = Path(f"/proc/{pid}/stat")
    return stat.exists() and stat.read_text(encoding="utf-8").split(") ")[-1][:1] != "Z"


def agent_recorded(project: Project, task_id: str, agent_pid: int, timeout: float = 30) -> dict[str, Any]:
    """Wait until the claim records the agent's identity (the launch writes it in a heartbeat just
    after starting the agent; an agent may write its own marker first). Before that, an orphan has
    only its run tag and is held for the operator, never stopped — a different scenario."""
    def recorded() -> bool:
        claim = project.record("claims", task_id) or {}
        return ((claim.get("checkpoint") or {}).get("agent") or {}).get("pid") == agent_pid
    assert wait_until(recorded, timeout), project.record("claims", task_id)
    return project.record("claims", task_id) or {}


def test_processes(root: Path) -> list[tuple[int, str]]:
    """Live processes of THIS test: their command line names its temporary directory."""
    found = []
    for entry in os.listdir("/proc"):
        if entry.isdigit() and int(entry) != os.getpid():
            cmd = _cmdline(int(entry))
            if str(root) in cmd and pid_alive(int(entry)):
                found.append((int(entry), cmd[:160]))
    return found


test_processes.__test__ = False  # a helper, not a test


@pytest.fixture(autouse=True)
def reap_test_processes(tmp_path: Path):
    """After every test here (even a failed one): kill what is left of THIS test — nothing else."""
    yield
    for pid, _cmd in test_processes(tmp_path):
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def simple_task(project: Project, key: str, role: str, path: str, **extra: Any) -> dict[str, Any]:
    return {"key": key, "role": role, "title": actions({"do": "append", "path": str(project.audit), "text": f"{key}\n"},
                                                       {"do": "write", "path": path, "text": f"# {key}\n"}), **extra}


# -- C: restart after losing the service AND its launch (a crash or reboot) -----------------------------------------

def test_c_restart_continues_from_the_last_checkpoint(tmp_path: Path, fake_home: Path, fake_agent: Path,
                                                      services: Services) -> None:
    del fake_home
    project = make_project(tmp_path, fake_agent)
    steps = [{"key": s, "title": actions({"do": "append", "path": str(project.audit), "text": f"{s}\n"},
                                          {"do": "write", "path": f"backend/src/{s}.py", "text": f"{s.upper()} = 1\n"})}
             for s in ("models", "service", "endpoints")]
    activate(project, "w4c", [{"key": "api", "role": "backend", "title": "API in three steps", "steps": steps}])
    hooks, marker = tmp_path / "halt-hooks", tmp_path / "halted.json"
    hooks.mkdir()
    (hooks / "sitecustomize.py").write_text(HALT_HOOK, encoding="utf-8")
    first, _log = services.start(project, env={"FC_TEST_HALT_BEFORE_STEP": "endpoints",
                                               "FC_TEST_HALT_MARKER": str(marker)}, hooks=hooks)
    assert wait_until(marker.exists, 120), "the launch never reached the boundary before step 3"
    halted = json.loads(marker.read_text(encoding="utf-8"))
    os.kill(halted["pid"], signal.SIGKILL)   # the launch process (it holds the claim) ...
    os.kill(first.pid, signal.SIGKILL)       # ... and the service: nothing of the run is left
    first.wait(timeout=30)
    assert wait_until(lambda: not pid_alive(halted["pid"]), 30)
    before = project.record("scheduling", "w4c.api")
    assert before["launches"] == 1 and before["in_flight"]["launch"] == 1  # the write-ahead record survived
    assert project.audit_lines() == ["models", "service"]

    code, out = services.run(project, "--exit-when-idle")
    assert code == 0, out
    assert project.audit_lines() == ["models", "service", "endpoints"]  # nothing redone, nothing skipped
    assert project.task("w4c.api")["state"] == "verified"
    after = project.record("scheduling", "w4c.api")
    assert after["launches"] == 2 and after["in_flight"] is None
    assert [h["category"] for h in after["history"]] == ["interrupted", "success"]
    assert project.task_commits("feature/w4c") == ["w4c.api"] * 3  # one commit per step, no duplicate
    assert "recovered w4c.api" in out and not list(project.state.glob("claims/*.json"))


# -- E: no subscription login, no launch -------------------------------------------------------------------------------

@pytest.mark.parametrize("auth", ["api-key", "unknown"])
def test_e_without_a_subscription_login_no_agent_starts(tmp_path: Path, fake_home: Path, fake_agent: Path,
                                                         services: Services, auth: str) -> None:
    """The pre-launch check finds an API-key (or unidentifiable) login: CRITICAL for the task, no
    launch, no agent session, nothing counted; never another provider or billing method."""
    del fake_home
    project = make_project(tmp_path, fake_agent)
    Path(f"{fake_agent}.json").write_text(json.dumps({"auth": auth}), encoding="utf-8")  # the fake CLI's login
    activate(project, "w4s", [simple_task(project, "api", "backend", "backend/src/api.py")])
    code, out = services.run(project, "--exit-when-idle")
    assert code == 0, out
    rec = project.record("scheduling", "w4s.api")
    assert rec["launches"] == 0 and rec["attention"]["category"] == "critical"
    assert "subscription authentication" in rec["attention"]["reason"] and "NEEDS_ATTENTION" in out
    assert not project.audit_lines() and not list(project.state.glob("runs/*")) and not project.events("claim")
    assert project.task("w4s.api")["state"] == "ready"


# -- H: an orphaned agent (its launch process died; the agent runs on) ------------------------------------------------

def test_h_orphaned_agent_is_stopped_only_after_its_deadline(tmp_path: Path, fake_home: Path, fake_agent: Path,
                                                             services: Services) -> None:
    del fake_home
    project = make_project(tmp_path, fake_agent, runtime={"task_timeout_seconds": 6, "terminate_grace_seconds": 1})
    started = tmp_path / "agent.pid"
    activate(project, "w4h", [{"key": "api", "role": "backend", "title": actions(
        {"do": "append", "path": str(project.audit), "text": "run\n"},
        {"do": "once", "path": str(started), "actions": [{"do": "sleep", "seconds": 600}]},
        {"do": "write", "path": "backend/src/api.py", "text": "API = 1\n"})}])
    proc, log = services.start(project, "--exit-when-idle")
    assert wait_until(started.exists, 60)
    agent = int(started.read_text(encoding="utf-8"))
    claim = agent_recorded(project, "w4h.api", agent)  # the scenario: an orphan whose identity is known
    os.kill(claim["identity"]["pid"], signal.SIGKILL)  # the launch process dies; its agent lives on
    assert pid_alive(agent)
    assert proc.wait(timeout=120) == 0, log.read_text(encoding="utf-8")
    out = log.read_text(encoding="utf-8")
    assert not pid_alive(agent)
    stopped = project.events("worker-orphans-stopped")
    assert len(stopped) == 1 and agent in stopped[0]["pids"] and not stopped[0]["survivors"]
    assert "still running" in out  # it waited for the agent first (deadline policy), never a second writer
    assert project.audit_lines() == ["run", "run"] and project.task("w4h.api")["state"] == "verified"


def test_h_unidentified_processes_are_never_killed(tmp_path: Path, fake_home: Path, fake_agent: Path) -> None:
    """A dead worker's claim whose run tag still has a live process, but no recorded agent
    identity: NEEDS_ATTENTION, the process is left alone."""
    del fake_home
    from feature_coordinator.config import load_config  # pylint: disable=import-outside-toplevel
    from feature_coordinator.service import WorkerService  # pylint: disable=import-outside-toplevel
    from feature_coordinator.supervisor import RUN_TAG_ENV  # pylint: disable=import-outside-toplevel
    project = make_project(tmp_path, fake_agent)
    activate(project, "w4u", [simple_task(project, "api", "backend", "backend/src/api.py")])
    stray = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"],  # pylint: disable=consider-using-with
                             env={**os.environ, RUN_TAG_ENV: "w4u-unknown-run"})
    dead = subprocess.Popen([sys.executable, "-c", "pass"])  # pylint: disable=consider-using-with
    dead.wait()
    try:
        identity = ProcessIdentity.current().to_dict()
        identity.update(pid=dead.pid, start_ticks=1)  # a worker that is gone
        claim = {"task_id": "w4u.api", "role": "backend", "branch": "feature/w4u", "head_sha": "0" * 40,
                 "identity": identity, "claimed_at": "2026-01-01T00:00:00Z", "heartbeat_at": "2026-01-01T00:00:00Z",
                 "checkpoint": {"run_tag": "w4u-unknown-run"}, "revision": 1}
        (project.state / "claims" / "w4u.api.json").write_text(json.dumps(claim), encoding="utf-8")
        lines: list[str] = []
        service = WorkerService(load_config(project.config), project.repo, project.state,
                                spawn=lambda *_a: pytest.fail("nothing may launch"), echo=lines.append,
                                idle_sleep=lambda _s: None, max_cycles=3)
        assert service.run() == 0
        assert stray.poll() is None  # alive: never signalled
        assert any("identity is unknown" in line and "NEEDS_ATTENTION" in line for line in lines), lines
        assert project.record("claims", "w4u.api") is not None  # held for the operator
    finally:
        stray.kill()
        stray.wait()


# -- I: a crash loop is bounded --------------------------------------------------------------------------------------

def test_i_identical_startup_failures_stop_the_restart_loop(tmp_path: Path, fake_home: Path, fake_agent: Path,
                                                            services: Services) -> None:
    del fake_home
    project = make_project(tmp_path, fake_agent)
    activate(project, "w4i", [simple_task(project, "api", "backend", "backend/src/api.py")])
    corrupt = project.state / "service" / "worker.json"  # the service's own record: it cannot run safely
    corrupt.parent.mkdir(exist_ok=True)
    corrupt.write_text("{not json", encoding="utf-8")
    codes, outputs = [], []
    for _ in range(6):  # what systemd does: Restart=on-failure, RestartPreventExitStatus=73 78
        code, out = services.run(project, timeout=60)
        codes.append(code)
        outputs.append(out)
        if code in (0, 73, 78):
            break
    assert codes == [1, 1, 78], outputs
    assert "do not restart" in outputs[-1] and not project.audit_lines()
    assert json.loads((project.state / "worker-startup.json").read_text(encoding="utf-8"))["consecutive"] == 3
    corrupt.unlink()  # the operator repairs the cause
    code, out = services.run(project, "--exit-when-idle")
    assert code == 0 and project.task("w4i.api")["state"] == "verified", out
    assert json.loads((project.state / "worker-startup.json").read_text(encoding="utf-8"))["consecutive"] == 0
    disabled = yaml.safe_load(project.config.read_text(encoding="utf-8"))
    disabled["worker"]["autonomous"]["enabled"] = False
    project.config.write_text(yaml.safe_dump(disabled, sort_keys=False), encoding="utf-8")
    code, out = services.run(project, timeout=60)
    assert code == 78 and "autonomous.enabled is false" in out  # refused at once: restarting cannot help


# -- K: graceful stop ------------------------------------------------------------------------------------------------

def test_k_graceful_stop_and_stop_after_current(tmp_path: Path, fake_home: Path, fake_agent: Path,
                                                services: Services) -> None:
    del fake_home
    project = make_project(tmp_path, fake_agent)
    started, release = tmp_path / "api.started", tmp_path / "api.release"
    activate(project, "w4k", [
        {"key": "api", "role": "backend", "title": actions(
            {"do": "append", "path": str(project.audit), "text": "api\n"},
            {"do": "write", "path": "backend/src/partial.py", "text": "P = 1\n"},
            {"do": "write", "path": str(started), "text": "1"},
            {"do": "wait_for", "path": str(release), "timeout": 120, "required": True},
            {"do": "write", "path": "backend/src/api.py", "text": "API = 1\n"})},
        simple_task(project, "cmd", "cli", "cli/src/cmd.py")])
    proc, log = services.start(project)
    assert wait_until(started.exists, 60)
    stop = project.cli("worker", "stop")
    assert stop.returncode == 0 and "signalled the service" in stop.stdout, stop.stdout + stop.stderr
    assert proc.wait(timeout=90) == 0, log.read_text(encoding="utf-8")
    assert project.task("w4k.api")["state"] == "ready" and project.task("w4k.cmd")["state"] == "ready"
    assert project.audit_lines() == ["api"] and not list(project.state.glob("claims/*.json"))
    assert (project.repo / "backend/src/partial.py").exists()  # uncommitted work stays
    rec = project.record("scheduling", "w4k.api")
    assert rec["in_flight"] is None and rec["history"][-1]["category"] == "cancelled" and not rec["attempts"]
    service = project.record("service", "worker")
    assert service["state"] == "OFFLINE" and "stop signal" in service["reason"]
    assert not read_control(StateStore(project.state)).get("stop")  # consumed by the stop
    health = project.cli("worker", "health", "--json")
    assert health.returncode == 1 and json.loads(health.stdout)["state"] == "OFFLINE"

    started.unlink()
    proc, log = services.start(project)
    assert wait_until(started.exists, 60)
    after = project.cli("worker", "stop", "--after-current")
    assert after.returncode == 0, after.stdout + after.stderr
    release.write_text("1", encoding="utf-8")
    assert proc.wait(timeout=90) == 0, log.read_text(encoding="utf-8")
    assert project.task("w4k.api")["state"] == "verified" and project.task("w4k.cmd")["state"] == "ready"
    assert project.audit_lines() == ["api", "api"]  # the active task finished; nothing new started


# -- P: one service per checkout; no manual agent beside it ------------------------------------------------------------

def test_p_a_second_service_and_a_manual_launch_are_refused(tmp_path: Path, fake_home: Path, fake_agent: Path,
                                                            services: Services) -> None:
    del fake_home
    project = make_project(tmp_path, fake_agent)
    started, release = tmp_path / "api.started", tmp_path / "api.release"
    activate(project, "w4p", [
        {"key": "api", "role": "backend", "title": actions(
            {"do": "append", "path": str(project.audit), "text": "api\n"},
            {"do": "write", "path": str(started), "text": "1"},
            {"do": "wait_for", "path": str(release), "timeout": 120, "required": True},
            {"do": "write", "path": "backend/src/api.py", "text": "API = 1\n"})},
        simple_task(project, "cmd", "cli", "cli/src/cmd.py")])  # independent: W2 would run it in parallel
    proc, log = services.start(project, "--exit-when-idle")
    assert wait_until(started.exists, 60)
    code, out = services.run(project, timeout=60)
    assert code == 73 and "another Worker service already runs" in out
    manual = project.cli("launch", "w4p.cmd", "--apply")
    assert manual.returncode != 0 and "sequential mode" in manual.stdout + manual.stderr
    work = project.cli("work", "w4p", "--apply", "--max-parallel", "2")
    assert work.returncode != 0  # the service owns the feature's Worker lock
    assert sorted(p.stem for p in project.state.glob("claims/*.json")) == ["w4p.api"]
    assert project.audit_lines() == ["api"]
    release.write_text("1", encoding="utf-8")
    assert proc.wait(timeout=120) == 0, log.read_text(encoding="utf-8")
    assert project.audit_lines() == ["api", "cmd"] and project.task("w4p.cmd")["state"] == "verified"
    claims = [e["task"] for e in project.events("claim")]
    assert claims == ["w4p.api", "w4p.cmd"]  # the refused manual attempt never claimed


# -- Q: a crash during the service's own bookkeeping ------------------------------------------------------------------

@pytest.mark.parametrize("point,launches,history", [("before-spawn", 2, ["interrupted", "success"]),
                                                    ("after-spawn", 1, ["success"]),
                                                    ("before-outcome", 1, ["success"])])
def test_q_crash_during_bookkeeping(tmp_path: Path, fake_home: Path, fake_agent: Path, services: Services,
                                    point: str, launches: int, history: list[str]) -> None:
    del fake_home
    project = make_project(tmp_path, fake_agent)
    activate(project, "w4q", [simple_task(project, "api", "backend", "backend/src/api.py")])
    hooks = tmp_path / "service-hooks"
    hooks.mkdir()
    (hooks / "sitecustomize.py").write_text(SERVICE_HOOK, encoding="utf-8")
    code, out = services.run(project, env={"FC_TEST_SERVICE_CRASH": point}, hooks=hooks, timeout=90)
    assert code == 70, out
    rec = project.record("scheduling", "w4q.api")
    assert rec["launches"] == 1 and rec["in_flight"]["launch"] == 1  # counted before anything started
    code, out = services.run(project, "--exit-when-idle")
    assert code == 0, out
    assert project.audit_lines() == ["api"]  # the agent ran exactly once
    assert project.task("w4q.api")["state"] == "verified"
    rec = project.record("scheduling", "w4q.api")
    assert rec["launches"] == launches and rec["in_flight"] is None
    assert [h["category"] for h in rec["history"]] == history
    assert project.task_commits("feature/w4q") == ["w4q.api"] and not list(project.state.glob("claims/*.json"))
    alive = [pid for pid in (int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit())
             if str(project.repo) in _cmdline(pid) and pid_alive(pid)]
    assert not alive  # no launch or agent of this project is left


def _cmdline(pid: int) -> str:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
    except OSError:
        return ""
