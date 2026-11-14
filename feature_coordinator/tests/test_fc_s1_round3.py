"""Security S1, round 3 — the second independent review's findings F8–F12, in disposable repositories and state.

F8  accepting a baseline removed a staged secret's sandbox mask and hardened-native deny entry;
F9  a Worker that finished its preliminary checks still launched its agent after another process held the checkout;
F10 a resolution cleared a newer hold it had never inspected;
F11 a scan returned a clean result although the checkout changed under it, and a cached inventory left a new secret
    unmasked in a sandbox plan;
F12 a known tracked secret reached the reviewer through a commit subject (and other fields).

Security policy (what is masked, denied, read-only), workspace baseline (change detection) and workspace hold (no new
agent) are tested as three separate things. Concurrency is exercised with REAL processes: the Worker is a separate
Python process driving the fake agent CLI; holds and resolutions come from other processes."""

# pylint: disable=missing-function-docstring,redefined-outer-name,too-many-lines,too-many-arguments

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from feature_coordinator import fsguard, review_context
from feature_coordinator.config import ProjectConfig
from feature_coordinator.fsguard import InventoryError, InventoryUnstableError, scan
from feature_coordinator.integration_paths import name_problem
from feature_coordinator.intake import IntakeRecord
from feature_coordinator.launchers import ClaudeCodeLauncher, RealCliRunner
from feature_coordinator.model import Feature, TaskState, TaskStep
from feature_coordinator.native_backend import NativeCliRunner
from feature_coordinator.native_hardening import NativeHardening
from feature_coordinator.paths import PathPolicy, is_git_dir_name
from feature_coordinator.review_context import ChangeListError, build_context, changed_entries
from feature_coordinator.review_guard import WITHHELD, SecretGuard, compact, tokens_of
from feature_coordinator.sandbox import SandboxBuilder
from feature_coordinator.store import StateStore
from feature_coordinator.workflow import FeatureSpec, TaskSpec
from feature_coordinator.workspace_hold import (
    GUARD_FLAG,
    WorkspaceHoldError,
    WorkspaceUnstableError,
    executions_of,
    guard_before_run,
    hold_of,
    hold_ref,
    launch_gate,
    put_on_hold,
    resolve,
    trusted_of,
)

import test_fc_autonomy as autonomy
import test_fc_protected_descendants as s1
import test_fc_s1_review_findings as r2
from fc_helpers import (
    actions_prompt,
    child_env,
    config_with_agents,
    fake_broker_factory,
    git,
    install_fake_agent,
    run_py,
)

write, init = r2.write, r2.init
USER = ("-c", "user.name=T", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false")
EACCES, EROFS = s1.EACCES, s1.EROFS


def hardened(config: ProjectConfig) -> ProjectConfig:
    return dataclasses.replace(config, runtime=dataclasses.replace(
        config.runtime, native_hardening=NativeHardening(enabled=True)))


def masks(repo: Path, config: ProjectConfig, tmp: Path, role: str = "backend") -> set[str]:
    """What a NEW sandbox plan hides (masked or not mounted at all)."""
    plan = SandboxBuilder(repo, config).plan(run_dir=tmp / f"plan-{time.monotonic_ns()}", role=role)
    try:
        return set(plan.masked) | set(plan.hidden)
    finally:
        plan.discard_shadow()


def denied(repo: Path, config: ProjectConfig) -> set[str]:
    """What a NEW hardened native launcher denies."""
    cfg = hardened(config)
    launcher = ClaudeCodeLauncher(cfg.providers["claude"], cfg, "claude", native=True)  # type: ignore[arg-type]
    return set(launcher.secrets(repo))


def accept(store: StateStore, repo: Path, config: ProjectConfig, reason: str = "audited"):
    return resolve(store, repo, config, apply=True, accept=True, reason=reason)


# =====================================================================================================================
# F8 — security policy ≠ workspace baseline: acceptance never unmasks, undenies or unprotects anything
# =====================================================================================================================

SECRETS = ("backend/.env", "backend/.env.example/.env", "frontend/.env.development.local")


@pytest.fixture
def f8(tmp_path: Path, state_dir: Path, config: ProjectConfig):
    """A checkout with a TRACKED secret, an UNTRACKED one and one NESTED below an exempt directory."""
    repo = init(tmp_path / "f8")
    write(repo, "frontend/.env.development.local", "VITE_TRACKED_SECRET=tracked-0123456789\n")
    git(repo, "add", "-f", "frontend/.env.development.local")   # tracked AND committed, although .gitignore names it
    git(repo, *USER, "commit", "-q", "-m", "a tracked env file")
    write(repo, "backend/.env", "API_KEY=untracked-0123456789\n")
    write(repo, "backend/.env.example/.env", "API_KEY=nested-0123456789\n")
    return repo, StateStore(state_dir), config


def test_f8_tracked_untracked_and_nested_secrets_are_all_masked_and_denied(f8, tmp_path: Path) -> None:
    repo, _store, config = f8
    assert "frontend/.env.development.local" in git(repo, "ls-files").splitlines()   # in the history
    assert set(SECRETS) <= masks(repo, config, tmp_path) and set(SECRETS) <= denied(repo, config)
    assert "backend/.env.example/notes" not in masks(repo, config, tmp_path)


def test_f8_a_staged_uncommitted_secret_keeps_its_mask_and_cannot_be_accepted(f8, tmp_path: Path) -> None:
    repo, store, config = f8
    secret = "backend/new/.env/key"
    write(repo, secret, "STAGED=staged-not-committed-0123456789\n")
    git(repo, "add", "-f", secret)
    assert secret in git(repo, "ls-files").splitlines()
    assert subprocess.run(["git", "-C", str(repo), "cat-file", "-e", f"HEAD:{secret}"], capture_output=True,
                          check=False).returncode != 0    # the index is not history
    assert secret in masks(repo, config, tmp_path) and secret in denied(repo, config)
    with pytest.raises(WorkspaceHoldError, match="first inventory"):
        guard_before_run(store, repo, config)
    refused = accept(store, repo, config)
    assert not refused.applied and refused.staged == [secret] and hold_of(store, repo) is not None
    assert secret in masks(repo, config, tmp_path) and secret in denied(repo, config)
    git(repo, "restore", "--staged", "--", secret)   # the operator's remediation; nothing does it automatically
    assert accept(store, repo, config).applied
    assert secret in masks(repo, config, tmp_path) and secret in denied(repo, config)   # accepted, still masked


def test_f8_acceptance_changes_the_baseline_and_nothing_else(f8, tmp_path: Path) -> None:
    repo, store, config = f8
    policy = PathPolicy(repo, config)
    before = (masks(repo, config, tmp_path), denied(repo, config),
              {p: policy.check("backend", p).allowed for p in SECRETS})
    with pytest.raises(WorkspaceHoldError, match="first inventory"):   # unreviewed sensitive entries: held
        guard_before_run(store, repo, config)
    assert trusted_of(store, repo) is None
    assert accept(store, repo, config).applied
    assert {"backend/.env", "backend/.env.example/.env"} <= set(trusted_of(store, repo)["entries"])
    guard_before_run(store, repo, config)   # the baseline now explains them: no hold
    after = (masks(repo, config, tmp_path), denied(repo, config),
             {p: policy.check("backend", p).allowed for p in SECRETS})
    assert after == before and not any(after[2].values())
    # REPEATED acceptance (the operator edits the secret, accepts again) weakens nothing either
    for round_ in range(3):
        write(repo, "backend/.env", f"API_KEY=rotated-{round_}-0123456789\n")
        with pytest.raises(WorkspaceHoldError, match="outside an accepted run"):
            guard_before_run(store, repo, config)
        assert accept(store, repo, config, f"rotation {round_}").applied
        assert (masks(repo, config, tmp_path), denied(repo, config)) == before[:2]


def test_f8_masks_survive_a_restart_after_acceptance(f8) -> None:
    repo, store, config = f8
    with pytest.raises(WorkspaceHoldError):
        guard_before_run(store, repo, config)
    assert accept(store, repo, config).applied
    proc = run_py("""
        import dataclasses, json, sys
        from pathlib import Path
        from feature_coordinator.config import default_config
        from feature_coordinator.launchers import ClaudeCodeLauncher
        from feature_coordinator.native_hardening import NativeHardening
        from feature_coordinator.sandbox import SandboxBuilder
        from feature_coordinator.store import StateStore
        from feature_coordinator.workspace_hold import guard_before_run, trusted_of
        repo, state, run = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
        config = default_config()
        store = StateStore(state)                      # a new process: only the persisted state
        guard_before_run(store, repo, config)          # the accepted baseline is in force
        plan = SandboxBuilder(repo, config).plan(run_dir=run, role="backend")
        hard = dataclasses.replace(config, runtime=dataclasses.replace(
            config.runtime, native_hardening=NativeHardening(enabled=True)))
        launcher = ClaudeCodeLauncher(hard.providers["claude"], hard, "claude", native=True)
        print(json.dumps({"masked": sorted({*plan.masked, *plan.hidden}), "denied": launcher.secrets(repo),
                          "baseline": sorted(trusted_of(store, repo)["entries"])}))
        """, str(repo), str(store.root), str(repo.parent / "restart-plan"))
    assert proc.returncode == 0, proc.stderr
    seen = json.loads(proc.stdout)
    assert set(SECRETS) <= set(seen["masked"]) and set(SECRETS) <= set(seen["denied"])
    assert "backend/.env" in seen["baseline"]


@pytest.mark.usefixtures("needs_bwrap", "fake_home")
def test_f8_a_new_worker_task_cannot_read_an_accepted_secret(tmp_path: Path, state_dir: Path) -> None:
    repo = init(tmp_path / "rig")
    write(repo, "backend/.env", "API_KEY=accepted-but-still-secret-0123456789\n")
    cfg = config_with_agents(autonomy.autonomous(), install_fake_agent(tmp_path / "agents"))
    rig = autonomy.Rig(repo, state_dir, config=cfg)
    rig.coord.runners["backend"] = RealCliRunner(cfg.providers["claude"], cfg,  # type: ignore[arg-type]
                                                 broker_factory=fake_broker_factory())
    rig.activate(TaskSpec("one", "backend", actions_prompt([
        {"do": "try_read", "path": "backend/.env"}, {"do": "write", "path": "backend/src/ok.py", "text": "x = 1\n"}]),
        required_gates=("backend-tests",)))
    with pytest.raises(WorkspaceHoldError, match="first inventory"):
        rig.coord.run_task("w4.one")
    assert accept(rig.coord.store, repo, cfg).applied
    run = rig.coord.run_task("w4.one")   # a new Worker task after the acceptance
    assert run.runner.status == "completed", run.runner.error
    report = json.loads(run.runner.transcript[0])
    read = next(item for item in report["report"] if item["action"] == "read backend/.env")
    assert "accepted-but-still-secret" not in json.dumps(report)
    assert read.get("value", "") == "" or not read["ok"]   # masked: /dev/null content or EACCES, never the secret


def test_f8_residue_a_run_created_can_never_be_accepted(tmp_path: Path, state_dir: Path, fake_home: Path) -> None:
    del fake_home
    repo = init(tmp_path / "rig")
    cfg = config_with_agents(autonomy.autonomous(), install_fake_agent(tmp_path / "agents"))
    rig = autonomy.Rig(repo, state_dir, config=cfg)
    for role in ("backend", "cli"):
        rig.coord.runners[role] = NativeCliRunner(cfg.provider_for(role), cfg)  # type: ignore[arg-type]
    created = "backend/new/.git/config"
    rig.activate(TaskSpec("one", "backend", actions_prompt([{"do": "write", "path": created, "text": "[core]\n"}]),
                          required_gates=("backend-tests",)),
                 TaskSpec("two", "cli", actions_prompt([{"do": "write", "path": "cli/src/x.py", "text": "x = 1\n"}]),
                          required_gates=("cli-lint",)))
    assert rig.coord.run_task("w4.one").runner.status == "rejected"
    refused = accept(rig.coord.store, repo, cfg, "I want to keep it")
    assert not refused.applied and created in refused.forbidden and hold_of(rig.coord.store, repo) is not None
    with pytest.raises(WorkspaceHoldError):
        rig.coord.run_task("w4.two")   # so no later agent ever reads what the first one planted
    assert (repo / created).exists()    # and still nothing was removed automatically
    (repo / created).unlink()
    (repo / "backend/new/.git").rmdir()
    assert resolve(rig.coord.store, repo, cfg, apply=True).applied
    assert rig.coord.run_task("w4.two").runner.status == "completed"


# =====================================================================================================================
# F9 — the launch gate: no agent starts after a hold exists (real processes)
# =====================================================================================================================

WORKER = """
import json, sys, time
from pathlib import Path
from feature_coordinator.demo import build_coordinator
from feature_coordinator.native_backend import NativeCliRunner
from feature_coordinator.workspace_hold import executions_of, hold_of
from fc_helpers import config_with_agents
import test_fc_autonomy as auto
repo, state, agent, task, mode, ready, resume = sys.argv[1:8]
cfg = config_with_agents(auto.autonomous(), Path(agent))
coord = build_coordinator(Path(repo), Path(state), config=cfg)
for role in ("backend", "cli"):
    coord.runners[role] = NativeCliRunner(cfg.provider_for(role), cfg)

def pause():
    Path(ready).write_text("paused")
    until = time.monotonic() + 30
    while not Path(resume).exists():
        if time.monotonic() > until:
            raise RuntimeError("never resumed")
        time.sleep(0.01)

if mode == "approval":          # after the pre-run guard, before the claim
    coord._require_approval = lambda _task: pause()
elif mode == "prelaunch":       # after EVERY preliminary check: claimed, prepared, baseline snapshot taken
    for runner in coord.runners.values():
        if isinstance(runner, NativeCliRunner):
            original = runner._state
            def paused_state(root, ctx, attempts=None, _original=original, _seen=[]):
                state_ = _original(root, ctx, attempts)
                if not _seen:
                    _seen.append(1)
                    pause()
                return state_
            runner._state = paused_state
try:
    run = coord.run_task(task)
    out = {"status": run.runner.status, "error": run.runner.error, "state": run.state.value}
except Exception as exc:
    out = {"exception": type(exc).__name__, "error": str(exc)[:300]}
out["task_state"] = coord.store.load_task(task).state.value
out["claims"] = sorted(coord.store.list_claims())
out["executions"] = sorted(executions_of(coord.store, Path(repo)))
print(json.dumps(out), flush=True)
"""


def native_rig(tmp_path: Path, state_dir: Path, first: list, second: list | None = None):
    repo = init(tmp_path / "rig")
    cfg = config_with_agents(autonomy.autonomous(), install_fake_agent(tmp_path / "agents"))
    rig = autonomy.Rig(repo, state_dir, config=cfg)
    for role in ("backend", "cli"):
        rig.coord.runners[role] = NativeCliRunner(cfg.provider_for(role), cfg)  # type: ignore[arg-type]
    rig.activate(TaskSpec("one", "backend", actions_prompt(first), required_gates=("backend-tests",)),
                 TaskSpec("two", "cli", actions_prompt(second or [{"do": "write", "path": "cli/src/two.py"}]),
                          required_gates=("cli-lint",)))
    return rig


def start_worker(rig, tmp_path: Path, task: str, mode: str) -> tuple[subprocess.Popen, Path, Path]:
    ready, resume = tmp_path / f"ready-{task}", tmp_path / f"resume-{task}"
    proc = subprocess.Popen(  # pylint: disable=consider-using-with
        [sys.executable, "-c", WORKER, str(rig.coord.root), str(rig.coord.store.root),
         rig.config.provider_for("backend").executable, task, mode, str(ready), str(resume)],
        env=child_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    return proc, ready, resume


def wait_for(path: Path, proc: subprocess.Popen | None = None, timeout: float = 30) -> None:
    until = time.monotonic() + timeout
    while not path.exists():
        if proc is not None and proc.poll() is not None:
            raise AssertionError(f"the worker ended early: {proc.communicate()}")
        if time.monotonic() > until:
            raise AssertionError(f"{path} never appeared")
        time.sleep(0.01)


def finish(proc: subprocess.Popen) -> dict:
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 0 and out.strip(), (out, err)
    return json.loads(out.strip().splitlines()[-1])


@pytest.mark.parametrize("mode", ["approval", "prelaunch"])
def test_f9_a_worker_that_finished_its_checks_does_not_launch_after_another_process_holds(
        tmp_path: Path, state_dir: Path, fake_home: Path, mode: str) -> None:
    """Process A completes its preliminary checks and waits; process B (this one, its own StateStore) persists a
    hold; A goes on. No agent starts: its marker is never written."""
    del fake_home
    marker = "backend/src/agent_started.txt"
    rig = native_rig(tmp_path, state_dir, [{"do": "write", "path": marker, "text": "started\n"}])
    proc, ready, resume = start_worker(rig, tmp_path, "w4.one", mode)
    try:
        wait_for(ready, proc)
        put_on_hold(StateStore(state_dir), rig.coord.root, reason="another Worker found residue",
                    paths=["Git state: simulated"])
        resume.write_text("go")
        out = finish(proc)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert out.get("exception") == "WorkspaceHoldError" and "on hold" in out["error"], out
    assert not (rig.coord.root / marker).exists()                        # the agent never started
    assert out["task_state"] == "ready" and out["claims"] == [] and out["executions"] == []
    assert not list((state_dir / "runs").glob("w4.one.*/stdout.jsonl")) or not any(
        p.stat().st_size for p in (state_dir / "runs").glob("w4.one.*/stdout.jsonl"))
    assert hold_of(StateStore(state_dir), rig.coord.root) is not None    # and B's hold is intact


def test_f9_an_execution_already_admitted_is_recorded_in_the_later_hold(tmp_path: Path, state_dir: Path,
                                                                        fake_home: Path) -> None:
    """The one exception the protocol allows, made explicit: an agent that started BEFORE the hold existed is a
    registered execution; the hold names it, it may finish and is judged; nothing new starts."""
    del fake_home
    release = tmp_path / "release"
    rig = native_rig(tmp_path, state_dir, [
        {"do": "write", "path": "backend/src/started.txt", "text": "1\n"},
        {"do": "wait_for", "path": str(release), "timeout": 30, "required": True},
        {"do": "write", "path": "backend/src/finished.txt", "text": "2\n"}])
    proc, _ready, _resume = start_worker(rig, tmp_path, "w4.one", "none")
    store = StateStore(state_dir)
    try:
        wait_for(rig.coord.root / "backend/src/started.txt", proc)
        running = executions_of(store, rig.coord.root)
        assert len(running) == 1 and next(iter(running.values()))["agent"] is not None
        hold = put_on_hold(store, rig.coord.root, reason="found by another process", paths=["Git state: simulated"])
        assert hold["entries"][-1]["in_flight"] == sorted(running)       # the hold says who was already running
        blocked = resolve(store, rig.coord.root, rig.config, apply=True, accept=True, reason="too early")
        assert not blocked.applied and blocked.in_flight == sorted(running)   # never resolved under a running agent
        with pytest.raises(WorkspaceHoldError):
            rig.coord.run_task("w4.two")                                 # nothing NEW starts
        release.write_text("go")
        out = finish(proc)
    finally:
        release.write_text("go")
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    assert out.get("status") == "completed" and out["executions"] == [], out
    assert (rig.coord.root / "backend/src/finished.txt").exists() and hold_of(store, rig.coord.root) is not None


def test_f9_two_workers_and_a_hold_at_the_same_time(tmp_path: Path, state_dir: Path, fake_home: Path) -> None:
    """Two Worker processes launch different roles while a third process publishes a hold. Whatever the
    interleaving: every agent that ran is an execution admitted BEFORE the hold's generation; none after it."""
    del fake_home
    rig = native_rig(tmp_path, state_dir, [{"do": "write", "path": "backend/src/one.txt", "text": "1\n"}],
                     [{"do": "write", "path": "cli/src/two.txt", "text": "2\n"}])
    workers = [start_worker(rig, tmp_path, task, "none")[0] for task in ("w4.one", "w4.two")]
    holder = subprocess.Popen(  # pylint: disable=consider-using-with
        [sys.executable, "-c", "import sys\nfrom pathlib import Path\n"
         "from feature_coordinator.store import StateStore\n"
         "from feature_coordinator.workspace_hold import put_on_hold\n"
         "put_on_hold(StateStore(Path(sys.argv[1])), Path(sys.argv[2]), reason='third process',\n"
         "            paths=['Git state: x'])\n",
         str(state_dir), str(rig.coord.root)], env=child_env())
    results = [finish(proc) for proc in workers]
    assert holder.wait(timeout=30) == 0
    hold = hold_of(StateStore(state_dir), rig.coord.root)
    assert hold is not None
    admitted = [e for e in rig.events() if e["kind"] == "workspace-hold"]
    assert len(admitted) == 1
    ran = {"w4.one": (rig.coord.root / "backend/src/one.txt").exists(),
           "w4.two": (rig.coord.root / "cli/src/two.txt").exists()}
    for task, out in zip(("w4.one", "w4.two"), results):
        if out.get("exception"):
            assert out["exception"] == "WorkspaceHoldError" and not ran[task], (task, out)   # refused: never ran
        else:
            assert ran[task], (task, out)   # ran: then it was admitted before the hold existed
    assert executions_of(StateStore(state_dir), rig.coord.root) == {}


def test_f9_a_hold_blocks_every_backend_and_every_step(tmp_path: Path, state_dir: Path, config: ProjectConfig,
                                                       fake_home: Path, bwrap_ok: bool) -> None:
    del fake_home
    repo = init(tmp_path / "rig")
    cfg = config_with_agents(autonomy.autonomous(), install_fake_agent(tmp_path / "agents"))
    rig = autonomy.Rig(repo, state_dir, config=cfg)               # mock (scripted) runners by default
    rig.coord.runners["cli"] = NativeCliRunner(cfg.provider_for("cli"), cfg)  # type: ignore[arg-type]
    if bwrap_ok:
        rig.coord.runners["frontend"] = RealCliRunner(cfg.providers["codex"], cfg,  # type: ignore[arg-type]
                                                      broker_factory=fake_broker_factory())
    steps = (TaskStep("a", "first step"), TaskStep("b", "second step"))
    rig.coord.activate(rig.coord.plan(FeatureSpec("w4", "W4", (
        TaskSpec("mock", "backend", "API"),
        TaskSpec("native", "cli", actions_prompt([{"do": "write", "path": "cli/x"}])),
        TaskSpec("bwrap", "frontend", actions_prompt([{"do": "write", "path": "frontend/x"}])),
        TaskSpec("steps", "coordination", "Docs", steps=steps)))).id)
    guard_before_run(rig.coord.store, repo, config)
    put_on_hold(StateStore(state_dir), repo, reason="held", paths=["Git state: simulated"])
    before = len(rig.log)
    for task in ("w4.mock", "w4.native", "w4.bwrap", "w4.steps"):
        with pytest.raises(WorkspaceHoldError):
            rig.coord.run_task(task)
        assert rig.task(task).state is TaskState.READY, task
    assert len(rig.log) == before and not (repo / "cli/x").exists() and not (repo / "frontend/x").exists()
    assert rig.coord.store.list_claims() == {}


def test_f9_a_hold_published_between_two_steps_stops_the_second(tmp_path: Path, state_dir: Path,
                                                               config: ProjectConfig) -> None:
    repo = init(tmp_path / "rig")
    rig = autonomy.Rig(repo, state_dir)
    steps = (TaskStep("a", "first step"), TaskStep("b", "second step"))
    rig.coord.activate(rig.coord.plan(FeatureSpec("w4", "W4", (
        TaskSpec("steps", "backend", "API", steps=steps),))).id)
    real = rig.coord.runners["backend"].run

    def run_then_hold(request):
        result = real(request)
        if request.step == "a":   # another process, right after the first step's agent ended
            put_on_hold(StateStore(state_dir), repo, reason="between steps", paths=["Git state: simulated"])
        return result
    rig.coord.runners["backend"].run = run_then_hold
    with pytest.raises(WorkspaceHoldError):
        rig.coord.run_task("w4.steps")
    assert [key for key, _at in rig.log] == ["w4.steps#a"]    # step b's agent was never invoked
    del config


def test_f9_the_decision_is_made_from_persistent_state_not_from_memory(tmp_path: Path, state_dir: Path,
                                                                       config: ProjectConfig) -> None:
    repo = init(tmp_path / "repo")
    store = StateStore(state_dir)
    guard_before_run(store, repo, config)
    execution = {"id": "e1", "task": "t", "role": "backend", "runner": "x", "run_dir": ""}
    with launch_gate(store, repo, config, execution=execution) as started:
        started(os.getpid())
    assert "e1" in executions_of(StateStore(state_dir), repo)   # visible to any other process, on disk
    put_on_hold(StateStore(state_dir), repo, reason="held by a different store object")
    with pytest.raises(WorkspaceHoldError):                      # the same (stale) store object decides afresh
        with launch_gate(store, repo, config, execution={**execution, "id": "e2"}):
            raise AssertionError("an agent would have started")
    assert "e2" not in executions_of(store, repo)
    proc = run_py("""
        import sys
        from pathlib import Path
        from feature_coordinator.config import default_config
        from feature_coordinator.store import StateStore
        from feature_coordinator.workspace_hold import WorkspaceHoldError, launch_gate
        try:
            with launch_gate(StateStore(Path(sys.argv[1])), Path(sys.argv[2]), default_config(),
                             execution={"id": "e3", "task": "t", "role": "cli", "runner": "x", "run_dir": ""}):
                print("STARTED")
        except WorkspaceHoldError:
            print("REFUSED AFTER RESTART")
        """, str(state_dir), str(repo))
    assert proc.stdout.strip() == "REFUSED AFTER RESTART", (proc.stdout, proc.stderr)


def test_f9_a_crashed_workers_execution_counts_while_its_agent_lives_and_is_dropped_afterwards(
        tmp_path: Path, state_dir: Path, config: ProjectConfig) -> None:
    repo = init(tmp_path / "repo")
    store = StateStore(state_dir)
    guard_before_run(store, repo, config)
    proc = run_py("""
        import os, subprocess, sys
        from pathlib import Path
        from feature_coordinator.config import default_config
        from feature_coordinator.store import StateStore
        from feature_coordinator.workspace_hold import launch_gate
        store = StateStore(Path(sys.argv[1]))
        execution = {"id": "crashed", "task": "t", "role": "backend", "runner": "x", "run_dir": ""}
        with launch_gate(store, Path(sys.argv[2]), default_config(), execution=execution) as started:
            agent = subprocess.Popen(["/usr/bin/sleep", "120"], start_new_session=True, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            started(agent.pid)
        print(agent.pid, flush=True)
        os._exit(0)     # the Worker dies without a final verification; its "agent" survives
        """, str(state_dir), str(repo))
    agent_pid = int(proc.stdout.strip())
    try:
        assert "crashed" in executions_of(store, repo)            # Worker gone, agent alive: still in flight
        put_on_hold(store, repo, reason="operator noticed", paths=["Git state: simulated"])
        blocked = resolve(store, repo, config, apply=True, accept=True, reason="x")
        assert not blocked.applied and blocked.in_flight == ["crashed"]
    finally:
        os.kill(agent_pid, 9)
    until = time.monotonic() + 10
    while executions_of(store, repo) and time.monotonic() < until:
        time.sleep(0.05)
    assert executions_of(store, repo) == {}
    done = resolve(store, repo, config, apply=True, accept=True, reason="agent gone, checked")
    assert done.applied and not done.in_flight
    assert any(e["kind"] == "workspace-execution-abandoned" for e in store.events())


# =====================================================================================================================
# F10 — resolution is bound to the hold's generation
# =====================================================================================================================

@pytest.fixture
def held(tmp_path: Path, state_dir: Path, config: ProjectConfig):
    repo = init(tmp_path / "held")
    store = StateStore(state_dir)
    guard_before_run(store, repo, config)
    put_on_hold(store, repo, reason="old finding, already remediated", paths=["backend/src/no-longer-there"])
    return repo, store, config


def during_inspection(monkeypatch: pytest.MonkeyPatch, action) -> None:
    """Run *action* once, after resolve() has taken its inventory and before it writes."""
    import feature_coordinator.workspace_hold as hold_module  # pylint: disable=import-outside-toplevel
    real, done = hold_module.scan, []

    def scan_then(root, cfg, **kwargs):
        inventory = real(root, cfg, **kwargs)
        if not done:
            done.append(1)
            action()
        return inventory
    monkeypatch.setattr(hold_module, "scan", scan_then)


def test_f10_a_hold_augmented_during_resolution_survives(held, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, store, config = held
    first = hold_of(store, repo)

    def another_process() -> None:
        git(repo, "config", "audit.raced", "unauthorized")   # a Git-control change no inventory can rediscover
        put_on_hold(StateStore(store.root), repo, reason="new Git control violation",
                    paths=["Git control files (.git/config, hooks or info) changed"])
    during_inspection(monkeypatch, another_process)
    out = resolve(store, repo, config, apply=True)
    assert not out.applied and out.stale and "changed while it was inspected" in out.stale
    now = hold_of(store, repo)
    assert now is not None and now["generation"] == first["generation"] + 1
    assert [e["reason"] for e in now["entries"]] == ["old finding, already remediated", "new Git control violation"]
    with pytest.raises(WorkspaceHoldError):
        guard_before_run(StateStore(store.root), repo, config)


def test_f10_a_hold_replaced_during_resolution_survives(held, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, store, config = held

    def another_process() -> None:   # resolves the old hold and then finds something new
        other = StateStore(store.root)
        assert resolve(other, repo, config, apply=True).applied
        write(repo, "backend/new/.git/config", "[core]\n")
        put_on_hold(other, repo, reason="a different hold", paths=["backend/new/.git/config"])
    during_inspection(monkeypatch, another_process)
    out = resolve(store, repo, config, apply=True, accept=True, reason="I only looked at the old one")
    assert not out.applied and out.stale
    now = hold_of(store, repo)
    assert now is not None and [e["reason"] for e in now["entries"]] == ["a different hold"]
    assert now["id"] != out.ref.split(".", maxsplit=1)[0]


def test_f10_an_explicit_hold_reference_must_match(held) -> None:
    repo, store, config = held
    ref = hold_ref(hold_of(store, repo))
    put_on_hold(store, repo, reason="one more finding", paths=["Git state: simulated"])   # after the operator looked
    stale = resolve(store, repo, config, apply=True, accept=True, reason="x", expect=ref)
    assert not stale.applied and "look at it again" in stale.stale and len(hold_of(store, repo)["entries"]) == 2
    current = hold_ref(hold_of(store, repo))
    assert resolve(store, repo, config, apply=True, accept=True, reason="both reviewed", expect=current).applied
    # a REPEATED request for the hold that is already resolved changes nothing
    revision = trusted_of(store, repo)["revision"]
    again = resolve(store, repo, config, apply=True, accept=True, reason="again", expect=current)
    assert not again.applied and again.stale and trusted_of(store, repo)["revision"] == revision


def test_f10_two_simultaneous_resolutions_from_two_processes(held) -> None:
    repo, store, config = held
    ref = hold_ref(hold_of(store, repo))
    go = repo.parent / "go"
    code = ("import json, sys, time\nfrom pathlib import Path\nfrom feature_coordinator.config import default_config\n"
            "from feature_coordinator.store import StateStore\nfrom feature_coordinator.workspace_hold import resolve\n"
            "while not Path(sys.argv[4]).exists(): time.sleep(0.001)\n"
            "out = resolve(StateStore(Path(sys.argv[1])), Path(sys.argv[2]), default_config(), apply=True, "
            "expect=sys.argv[3])\nprint(json.dumps({'applied': out.applied, 'stale': bool(out.stale)}))\n")
    argv = [sys.executable, "-c", code, str(store.root), str(repo), ref, str(go)]
    procs = [subprocess.Popen(argv, env=child_env(), stdout=subprocess.PIPE,  # pylint: disable=consider-using-with
                              stderr=subprocess.PIPE, text=True) for _ in range(4)]
    go.write_text("go")
    outs = []
    for proc in procs:
        out, err = proc.communicate(timeout=60)
        assert proc.returncode == 0, err
        outs.append(json.loads(out))
    assert sum(o["applied"] for o in outs) == 1 and sum(o["stale"] for o in outs) == 3, outs
    assert hold_of(store, repo) is None
    assert len([e for e in store.events() if e["kind"] == "workspace-hold-resolved"]) == 1
    del config


def test_f10_resolution_after_a_restart_and_after_file_system_changes(held) -> None:
    repo, store, config = held
    restarted = StateStore(store.root)                       # a new process would see exactly this
    write(repo, "backend/late/.env", "LATE=appeared-after-the-hold\n")
    out = resolve(restarted, repo, config, apply=True)
    assert not out.applied and out.remaining == ["backend/late/.env"] and hold_of(restarted, repo) is not None
    (repo / "backend/late/.env").unlink()
    assert resolve(restarted, repo, config, apply=True).applied and hold_of(store, repo) is None


def test_f10_a_crash_before_the_write_leaves_the_hold_and_the_baseline_untouched(
        held, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, store, config = held
    before = json.loads((store.root / "flags" / f"{GUARD_FLAG}.json").read_text(encoding="utf-8"))
    real = StateStore.set_flag
    calls = []

    def crash_on_the_final_write(self, name, value):
        calls.append(name)
        if name == GUARD_FLAG and len(calls) >= 1 and value and not next(iter(value.values()))["hold"]:
            raise OSError("power loss")
        return real(self, name, value)
    monkeypatch.setattr(StateStore, "set_flag", crash_on_the_final_write)
    with pytest.raises(OSError, match="power loss"):
        resolve(store, repo, config, apply=True)
    monkeypatch.setattr(StateStore, "set_flag", real)
    assert json.loads((store.root / "flags" / f"{GUARD_FLAG}.json").read_text(encoding="utf-8")) == before
    assert hold_of(StateStore(store.root), repo) is not None
    assert resolve(StateStore(store.root), repo, config, apply=True).applied   # and the request can be repeated


def test_f10_a_launch_during_the_inspection_makes_an_acceptance_stale(tmp_path: Path, state_dir: Path,
                                                                      config: ProjectConfig,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    repo = init(tmp_path / "repo")
    store = StateStore(state_dir)
    guard_before_run(store, repo, config)

    def a_worker_launches() -> None:
        with launch_gate(StateStore(state_dir), repo, config,
                         execution={"id": "late", "task": "t", "role": "cli", "runner": "x", "run_dir": ""}):
            pass
    during_inspection(monkeypatch, a_worker_launches)
    out = resolve(store, repo, config, apply=True, accept=True, reason="baseline refresh")
    assert not out.applied and out.stale   # what was inspected is no longer what would be written


# =====================================================================================================================
# F11 — a scan is a result only when it was verified; nothing security-relevant comes from a cache
# =====================================================================================================================

def after_listing(monkeypatch: pytest.MonkeyPatch, repo: Path, directory: str, action, times: int = 1) -> list:
    """Call *action* right after the walk LISTED *directory* (before it processes that listing), *times* times."""
    real, fired = fsguard.os.scandir, []

    @contextlib.contextmanager
    def hooked(fd):
        with real(fd) as listing:
            entries = list(listing)
        if os.readlink(f"/proc/self/fd/{fd}") == str(repo / directory) and len(fired) < times:
            fired.append(action(len(fired)))
        yield iter(entries)
    monkeypatch.setattr(fsguard.os, "scandir", hooked)
    return fired


def test_f11_an_insertion_after_a_directory_was_listed_is_never_missed(tmp_path: Path, config: ProjectConfig,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    repo = init(tmp_path / "repo")
    fired = after_listing(monkeypatch, repo, "backend", lambda _n: write(repo, "backend/.env/key", "S=1\n"))
    inventory = scan(repo, config, sleep=lambda _s: None)
    assert fired and {"backend/.env", "backend/.env/key"} <= set(inventory.entries)


def test_f11_a_checkout_that_keeps_changing_never_yields_a_result(tmp_path: Path, config: ProjectConfig,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    repo = init(tmp_path / "repo")
    after_listing(monkeypatch, repo, "backend", lambda n: write(repo, f"backend/churn-{n}.txt", "x\n"), times=99)
    with pytest.raises(InventoryUnstableError, match="completeness cannot be established"):
        scan(repo, config, attempts=5, sleep=lambda _s: None)
    assert issubclass(InventoryUnstableError, InventoryError)   # every caller that fails closed still does


def test_f11_concurrent_deletion_directory_replacement_and_symlink_replacement(
        tmp_path: Path, config: ProjectConfig, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = init(tmp_path / "repo")
    outside = tmp_path / "outside"
    write(tmp_path, "outside/.env", "OUTSIDE=1\n")
    write(repo, "backend/.env", "S=1\n")
    write(repo, "backend/sub/plain.txt", "x\n")
    write(repo, "backend/swap/plain.txt", "x\n")

    def mutate(_n: int) -> str:
        (repo / "backend/.env").unlink()                                     # deletion
        (repo / "backend/sub").rename(repo / "backend/sub-old")              # directory replacement …
        write(repo, "backend/sub/.env", "REPLACED=1\n")                      # … by one holding a secret
        (repo / "backend/swap").rename(tmp_path / "swap-away")               # symlink replacement …
        os.symlink(outside, repo / "backend/swap")                           # … pointing out of the checkout
        return "mutated"
    fired = after_listing(monkeypatch, repo, "backend", mutate)
    inventory = scan(repo, config, sleep=lambda _s: None)
    assert fired == ["mutated"]
    assert "backend/.env" not in inventory.entries and "backend/sub/.env" in inventory.entries
    assert not any(p.startswith("backend/swap/") for p in inventory.entries)       # the link was never followed
    assert not any(e.kind == fsguard.VANISHED for e in inventory.entries.values())  # only a verified walk returns


def test_f11_repeated_scans_during_real_concurrent_mutation(tmp_path: Path, config: ProjectConfig) -> None:
    """A real writer thread churns the checkout. Every scan either raises InventoryUnstableError or returns a
    verified inventory containing every stable sensitive entry — never a silently incomplete one."""
    repo = init(tmp_path / "repo")
    stable = {write(repo, f"backend/keep{i}/.env", "S=1\n") for i in range(5)}
    stop = threading.Event()

    def churn() -> None:
        i = 0
        while not stop.is_set():
            path = repo / "backend" / f"churn{i % 7}" / "f.txt"
            path.parent.mkdir(exist_ok=True)
            path.write_text("x", encoding="utf-8")
            path.unlink()
            path.parent.rmdir()
            i += 1
    writer = threading.Thread(target=churn, daemon=True)
    writer.start()
    try:
        returned = unstable = 0
        for _ in range(40):
            try:
                inventory = scan(repo, config, attempts=2, sleep=lambda _s: None)
            except InventoryUnstableError:
                unstable += 1
                continue
            returned += 1
            assert stable <= set(inventory.entries)
    finally:
        stop.set()
        writer.join(timeout=10)
    assert returned + unstable == 40
    assert stable <= set(scan(repo, config).entries)   # quiescent again: a normal verified scan


def test_f11_time_and_entry_bounds_hold_inside_one_flat_directory(tmp_path: Path, config: ProjectConfig,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    flat = tmp_path / "flat"
    flat.mkdir()
    for i in range(100):
        (flat / f"f{i}").write_text("x", encoding="utf-8")
    ticks = [0.0]
    real_child = fsguard.Classifier.child

    def child(self, *args):
        ticks[0] += 1.0   # time passes while the entries of ONE directory are processed
        return real_child(self, *args)
    monkeypatch.setattr(fsguard.Classifier, "child", child)
    with pytest.raises(InventoryError, match="did not finish in time"):
        scan(flat, config, max_seconds=0.5, clock=lambda: ticks[0])
    monkeypatch.setattr(fsguard.Classifier, "child", real_child)

    class Endless:
        """An entry of a directory that never stops listing: the cap must hit WHILE listing (bounded memory)."""

        def __init__(self, number: int) -> None:
            self.name = f"n{number}"

        def is_dir(self, follow_symlinks: bool = True) -> bool:
            del follow_symlinks
            return False

        def is_symlink(self) -> bool:
            return False

    @contextlib.contextmanager
    def endless(_fd):
        yield (Endless(i) for i in range(10**12))
    monkeypatch.setattr(fsguard.os, "scandir", endless)
    with pytest.raises(InventoryError, match="more than 1000 entries"):
        scan(flat, config, max_entries=1000)


def test_f11_the_builder_never_reuses_an_inventory(tmp_path: Path, config: ProjectConfig) -> None:
    repo = init(tmp_path / "repo")
    builder = SandboxBuilder(repo, config)
    assert builder.secret_files() == [] and builder.inventory() is not builder.inventory()
    write(repo, "backend/.env", "ADDED=after-the-first-inventory\n")           # after inventory construction
    assert builder.secret_files() == ["backend/.env"]                           # no cached mask list
    plan = builder.plan(run_dir=tmp_path / "plan", role="backend")              # before plan generation: covered
    assert "backend/.env" in plan.masked and plan.inventory is not None and builder.stale(plan) == []
    write(repo, "backend/src/.env.local", "ADDED=after-the-plan\n")             # after plan generation
    assert builder.stale(plan) == ["backend/src/.env.local"]                    # the plan is known to be stale
    plan.discard_shadow()


@pytest.mark.usefixtures("needs_bwrap", "fake_home")
def test_f11_a_stale_sandbox_plan_is_rebuilt_or_the_run_is_refused(tmp_path: Path, agent_config: ProjectConfig,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    repo = init(tmp_path / "repo")
    real_plan, added = SandboxBuilder.plan, []

    def plan_then_a_secret_appears(self, **kwargs):
        plan = real_plan(self, **kwargs)
        if len(added) < limit[0]:
            added.append(write(repo, f"backend/late{len(added)}/.env", "LATE=1\n"))
        return plan
    monkeypatch.setattr(SandboxBuilder, "plan", plan_then_a_secret_appears)
    limit = [1]   # one secret appears while the first plan is built: the plan is rebuilt and masks it
    result = r2.bwrap_run(repo, agent_config, "backend", [{"do": "try_read", "path": "backend/late0/.env"},
                                                          {"do": "write", "path": "backend/src/ok.py"}], tmp_path)
    assert result.status == "completed", result.error
    sandbox = json.loads((Path(result.details["run_dir"]) / "sandbox.json").read_text(encoding="utf-8"))
    assert "backend/late0/.env" in sandbox["masked"] and "LATE=1" not in result.transcript[0]
    added.clear()
    limit[0] = 99   # the checkout keeps changing: no current plan exists — the agent is never started
    refused = r2.bwrap_run(repo, agent_config, "backend", [{"do": "write", "path": "backend/src/never.py"}], tmp_path)
    assert refused.status == "unavailable" and "was not started" in refused.error
    assert not (repo / "backend/src/never.py").exists()


def test_f11_hardened_native_never_launches_with_a_stale_deny_list(tmp_path: Path, fake_home: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    del fake_home
    repo = init(tmp_path / "repo")
    cfg = hardened(config_with_agents(autonomy.autonomous(), install_fake_agent(tmp_path / "agents")))
    real_prepare = ClaudeCodeLauncher.prepare

    def prepare_then_a_secret_appears(self, request, run_dir, writable):
        spec = real_prepare(self, request, run_dir, writable)   # the deny rules are generated here …
        write(repo, "backend/.env.local", "LATE=1\n")            # … and a secret appears afterwards
        return spec
    monkeypatch.setattr(ClaudeCodeLauncher, "prepare", prepare_then_a_secret_appears)
    runner = NativeCliRunner(cfg.provider_for("backend"), cfg)  # type: ignore[arg-type]
    result = runner.run(s1.request_for(repo, cfg, "backend", [{"do": "write", "path": "backend/src/x.py"}], tmp_path))
    assert result.status == "unavailable" and "backend/.env.local" in result.error, (result.status, result.error)
    assert "was not started" in result.error and not (repo / "backend/src/x.py").exists()


def test_f11_an_unfinished_scan_is_never_a_trusted_clean_result(tmp_path: Path, state_dir: Path,
                                                               config: ProjectConfig,
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    import feature_coordinator.workspace_hold as hold_module  # pylint: disable=import-outside-toplevel
    repo = init(tmp_path / "repo")
    store = StateStore(state_dir)

    def unstable(*_args, **_kwargs):
        raise InventoryUnstableError("the checkout kept changing during the inventory")
    monkeypatch.setattr(hold_module, "scan", unstable)
    with pytest.raises(WorkspaceUnstableError, match="no agent is started"):
        guard_before_run(store, repo, config)
    with pytest.raises(WorkspaceUnstableError):
        with launch_gate(store, repo, config, execution={"id": "x", "task": "t", "role": "cli", "runner": "r"}):
            raise AssertionError("an agent would have started")
    assert trusted_of(store, repo) is None and hold_of(store, repo) is None and executions_of(store, repo) == {}

    def limit(*_args, **_kwargs):
        raise InventoryError("the checkout has more than 1000000 entries")
    monkeypatch.setattr(hold_module, "scan", limit)
    with pytest.raises(WorkspaceHoldError, match="could not be completed"):
        guard_before_run(store, repo, config)
    monkeypatch.undo()
    restarted = StateStore(state_dir)                                     # after a restart
    assert trusted_of(restarted, repo) is None                            # nothing was trusted by the failed scans
    assert hold_of(restarted, repo) is not None                           # and the hard failure still needs an operator
    with pytest.raises(WorkspaceHoldError):
        guard_before_run(restarted, repo, config)


# =====================================================================================================================
# F12 — known protected content never reaches the reviewer, whichever field would carry it
# =====================================================================================================================

SECRET_VALUE = "s1-review-secret-value-0123456789"
SECRET_LINE = f"API_TOKEN={SECRET_VALUE}"
SECOND_VALUE = "second-secret-value-9876543210"


def history(tmp_path: Path, change, *, message: str = "head", base_files: dict | None = None) -> tuple[Path, str, str]:
    root = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "--initial-branch=dev", str(root)], check=True)
    write(root, "backend/src/.env/key.py", f"{SECRET_LINE}\r\nDB_PASSWORD: '{SECOND_VALUE}'\r\nOTHER=1\n")
    write(root, "backend/src/app.py", "ROUTES = []\n")
    write(root, ".env.example", "EXAMPLE_PUBLIC_VALUE=documented-example-value\n")
    for rel, text in (base_files or {}).items():
        write(root, rel, text)
    git(root, "add", "-A")
    git(root, *USER, "commit", "-q", "-m", "base")
    base = git(root, "rev-parse", "HEAD").strip()
    change(root)
    git(root, "add", "-A")
    git(root, *USER, "commit", "-q", "--allow-empty", "-m", message)
    return root, base, git(root, "rev-parse", "HEAD").strip()


def context(config: ProjectConfig, root: Path, base: str, head: str, *, title: str = "X", body: str = "Do it.",
            branch: str = "feature/issue-9"):
    feature = Feature(id="issue-9", slug="issue-9", title="X", base_branch="dev", branch=branch, base_sha=base)
    record = IntakeRecord(id="issue-9", issue_number=9, issue_id=909, node_id="I_9", url="mock://issues/9",
                          issue_author="someone", title=title, body=body, digest="d" * 64, role="backend",
                          gates=["backend-tests"], task_id="issue-9.backend", approved_by="spyderbg",
                          approval_comment_id=1, approval_comment_url="mock://c/1", approved_at="2026-11-13T10:00:00Z")
    binding = {"repository": "spyderbg/mx-admin-cloud", "pr": 9, "head_branch": branch, "base_branch": "dev",
               "head_sha": head, "merge_base": base}
    return build_context(config, root, feature=feature, record=record, tasks=[], binding=binding)


def ordinary(root: Path) -> None:
    write(root, "backend/src/app.py", "ROUTES = ['/health']\n")


def clean_of_secrets(ctx) -> bool:
    everything = "\n".join([ctx.prompt, ctx.problem, *ctx.notes])
    return SECRET_VALUE not in everything and SECOND_VALUE not in everything \
        and compact(SECRET_VALUE) not in compact(ctx.prompt) and compact(SECOND_VALUE) not in compact(ctx.prompt)


@pytest.mark.parametrize("message", [
    SECRET_LINE,                                                        # the reviewer's reproduction
    f"fix: use {SECRET_VALUE} as the token",                            # the bare value inside ordinary text
    f"rotate {SECRET_VALUE} and {SECOND_VALUE} together",               # several secrets in one field
    f"subject\n\nbody line\n{SECRET_LINE}\nmore body",                  # the commit body
    SECRET_VALUE[:10] + chr(0x200B) + SECRET_VALUE[10:],                   # a zero-width space inside it
    "".join(chr(ord(c) + 0xFEE0) if "!" <= c <= "~" else c for c in SECRET_VALUE),   # full-width forms (NFKC)
    f"API_TOKEN =  {SECRET_VALUE}   ",                                  # other spacing
], ids=["exact-line", "value-in-text", "two-secrets", "body", "zero-width", "fullwidth", "spacing"])
def test_f12_commit_metadata_never_carries_a_known_secret(tmp_path: Path, config: ProjectConfig,
                                                         message: str) -> None:
    root, base, head = history(tmp_path, ordinary, message=message)
    ctx = context(config, root, base, head)
    assert clean_of_secrets(ctx)
    assert not ctx.problem and "ROUTES = ['/health']" in ctx.prompt       # the review itself still happens
    if "\n" not in message:
        assert WITHHELD in ctx.prompt and any("commit subject" in note for note in ctx.notes), ctx.notes


@pytest.mark.parametrize("field", ["title", "body", "conventions", "plan-branch"])
def test_f12_every_other_untrusted_field_is_guarded(tmp_path: Path, config: ProjectConfig, field: str) -> None:
    base_files = {"CLAUDE.md": f"Conventions.\nNever log {SECRET_VALUE}.\n"} if field == "conventions" else None
    root, base, head = history(tmp_path, ordinary, base_files=base_files)
    kwargs = {"title": f"Use {SECRET_VALUE}"} if field == "title" else \
        {"body": f"Please keep\n{SECRET_LINE}\nworking."} if field == "body" else \
        {"branch": f"feature/{SECRET_VALUE}"} if field == "plan-branch" else {}
    ctx = context(config, root, base, head, **kwargs)
    assert clean_of_secrets(ctx)
    if field == "plan-branch":   # a field with no guard of its own: the final boundary refuses the whole prompt
        assert ctx.prompt == "" and "nothing was sent" in ctx.problem
    else:
        assert not ctx.problem and WITHHELD in ctx.prompt and ctx.notes


def test_f12_the_final_boundary_holds_even_if_a_field_guard_is_missing(tmp_path: Path, config: ProjectConfig,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    root, base, head = history(tmp_path, ordinary, message=SECRET_LINE)
    passthrough = getattr(review_context, "_Guarded")
    monkeypatch.setattr(review_context, "_guarded_fields", lambda _guard, fields: {
        name: passthrough(list(texts) or [""]) for name, texts in fields.items()})
    ctx = context(config, root, base, head)
    assert ctx.prompt == "" and ctx.problem.startswith("the reviewer context still repeated the content")
    assert "backend/src/.env/key.py" in ctx.problem and clean_of_secrets(ctx)   # names the path, never the content


@pytest.mark.parametrize("change,shown", [
    (lambda root: git(root, "mv", "backend/src/.env/key.py", "backend/src/public.py"), "backend/src/public.py"),
    (lambda root: write(root, "backend/src/copy.py", (root / "backend/src/.env/key.py").read_text("utf-8")),
     "backend/src/copy.py"),
    (lambda root: write(root, "backend/src/value.py", f"TOKEN = '{SECRET_VALUE}'\n"), "backend/src/value.py"),
    (lambda root: write(root, "backend/src/wrapped.py",
                        f"TOKEN = (\n    '{SECRET_VALUE[:12]}'\n    '{SECRET_VALUE[12:]}'\n)\n"), None),
    (lambda root: write(root, "backend/src/two.py", f"A = '{SECRET_VALUE}'\nB = '{SECOND_VALUE}'\n"),
     "backend/src/two.py"),
], ids=["renamed", "byte-identical", "value-only", "wrapped", "two-values"])
def test_f12_file_content_is_withheld_by_value_not_only_by_line(tmp_path: Path, config: ProjectConfig, change,
                                                               shown: str | None) -> None:
    root, base, head = history(tmp_path, change)
    ctx = context(config, root, base, head)
    assert clean_of_secrets(ctx)
    if shown:
        assert not ctx.problem and ctx.files[shown].lines is None and any("withheld" in n for n in ctx.notes)


def test_f12_a_secret_in_a_path_or_added_and_removed_inside_the_range(tmp_path: Path, config: ProjectConfig) -> None:
    root, base, head = history(tmp_path, lambda r: write(r, f"backend/src/{SECRET_VALUE}.py", "x = 1\n"))
    ctx = context(config, root, base, head)
    assert ctx.prompt == "" and "a changed path repeats the content of the protected file" in ctx.problem
    assert clean_of_secrets(ctx)
    # a secret that exists only in an INTERMEDIATE commit of the range is known too
    other = tmp_path / "second"
    other.mkdir()
    root, base, _head = history(other, lambda r: write(r, "backend/.env.local",
                                                       "RANGE_ONLY=only-inside-the-range-42\n"))
    (root / "backend/.env.local").unlink()
    ordinary(root)
    git(root, "add", "-A")
    git(root, *USER, "commit", "-q", "-m", "remove it again; it was only-inside-the-range-42")
    ctx = context(config, root, base, git(root, "rev-parse", "HEAD").strip())
    assert "only-inside-the-range-42" not in ctx.prompt and WITHHELD in ctx.prompt and not ctx.problem


def test_f12_a_range_only_secret_is_known_even_when_an_ordinary_path_holds_the_same_blob(
        tmp_path: Path, config: ProjectConfig) -> None:
    """The same content at a secret path and at an ordinary path (one blob), both only INSIDE the range: the guard
    learns it whichever path Git lists first (`aaa/…` sorts before `backend/.env.local`)."""
    text = "RANGE_ONLY=shared-blob-inside-the-range-42\n"

    def both(root: Path) -> None:
        write(root, "aaa/copy.txt", text)
        write(root, "backend/.env.local", text)
    root, base, _head = history(tmp_path, both)
    (root / "aaa/copy.txt").unlink()
    (root / "backend/.env.local").unlink()
    ordinary(root)
    git(root, "add", "-A")
    git(root, *USER, "commit", "-q", "-m", "remove both; it was shared-blob-inside-the-range-42")
    ctx = context(config, root, base, git(root, "rev-parse", "HEAD").strip())
    assert "shared-blob-inside-the-range-42" not in ctx.prompt and WITHHELD in ctx.prompt and not ctx.problem


def test_f12_exempt_and_ordinary_text_is_not_withheld_and_limits_are_documented(tmp_path: Path,
                                                                               config: ProjectConfig) -> None:
    root, base, head = history(tmp_path, ordinary,
                               message="docs: mention EXAMPLE_PUBLIC_VALUE=documented-example-value")
    ctx = context(config, root, base, head, body="An ordinary body with OTHER=1 in it.")
    assert not ctx.problem and WITHHELD not in ctx.prompt
    assert "documented-example-value" in ctx.prompt and "OTHER=1" in ctx.prompt   # exempt file; too short to match
    guard = SecretGuard()
    guard.add("x/.env", f"{SECRET_LINE}\nPIN=1234\n".encode())
    assert guard.find(f"see {SECRET_VALUE}!") == "x/.env" and guard.find("PIN=1234") is None   # documented limit
    assert not list(tokens_of("PIN=1234")) and compact(f"a {chr(0x200B)} b\r\n") == "ab"


@pytest.mark.parametrize("raw", [b"Rgarbage\0source\0destination\0", b"Mgarbage\0x\0", b"M\0../outside\0",
                                 b"M\0a//b\0", b"M\0./x\0", b"R100\0a\0b/../c\0", b"MM\0x\0", b"R\0a\0b\0"])
def test_f12_the_change_list_grammar_is_checked_completely_and_errors_carry_no_content(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: bytes) -> None:
    monkeypatch.setattr(review_context, "_git", lambda *_args, **_kwargs: raw)
    with pytest.raises(ChangeListError) as error:
        changed_entries(tmp_path, "a", "b")
    assert not any(part in str(error.value) for part in ("garbage", "outside", "source", "destination"))


# =====================================================================================================================
# Minor findings of the second review
# =====================================================================================================================

@pytest.mark.parametrize("alias", [".git:foo", ".git::$INDEX_ALLOCATION", ".git:$I30:$INDEX_ALLOCATION", "git~1:foo",
                                   ".GIT .:stream"])
def test_ntfs_stream_spellings_of_git_are_refused_like_git_refuses_them(tmp_path: Path, config: ProjectConfig,
                                                                       alias: str) -> None:
    repo = init(tmp_path / "repo")
    assert is_git_dir_name(alias) and name_problem(alias.encode()) is not None
    assert not PathPolicy(repo, config).check("backend", f"backend/{alias}/config").allowed
    assert not is_git_dir_name("notgit:foo") and not is_git_dir_name(".github:x")


def test_status_json_lists_the_paths_that_need_an_audit_before_any_baseline_exists(
        tmp_path: Path, state_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from feature_coordinator.cli import main  # pylint: disable=import-outside-toplevel
    repo = init(tmp_path / "repo")
    write(repo, "backend/hidden/.git/config", "[core]\n")
    assert main(["--repo", str(repo), "--state-dir", str(state_dir), "workspace", "status", "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out["baseline"] is False and out["needs_operator"] is True
    assert "backend/hidden/.git/config" in out["untrusted"]
    assert out["untrusted_reasons"]["backend/hidden/.git/config"] == ["protected:backend/hidden/.git"]
