"""W4.2 offline rehearsal: the real-agent Worker smoke scenario with fake agents, and the audit wrapper.

The rehearsal runs the same harness as the real-agent run (fc_worker_smoke.py): a real temporary
Git repository, the real service entry point (`python -m feature_coordinator worker start --exit-when-idle`) twice
with a controlled graceful stop at the boundary between Backend's steps, the native-cli backend,
real checkpoints and sandboxed gates. Only the agent behind the audit wrapper is the fake CLI
(it follows the very same prompts). No model, no GitHub.

The wrapper tests show that it fails closed: every check that does not pass refuses the session
(exit 97) and the CLI never runs."""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator import CLI
from feature_coordinator.launchers import ClaudeCodeLauncher, CodexLauncher
from feature_coordinator.store import StateStore

from fc_helpers import install_fake_agent
from fc_worker_smoke import AUDIT_WRAPPER, MAX_SESSIONS, REPO_ROOT, Harness
from test_fc_service_process import reap_test_processes  # noqa: F401  pylint: disable=unused-import
from test_fc_service_process import test_processes

HERE = Path(__file__).resolve().parent

PROMPT = ("You are the {role} agent working on feature task {task} in this repository.\n"
          "Task:\n...\n\nCURRENT STEP 1 of 2 — `{step}` (do ONLY this step now):\n"
          "Create the file backend/w4/x.py with exactly the content below and nothing else in it.\n"
          "```python\nX = 1\n```\nDo not create, change or delete any other file. Do not run any command.\n")
CLAUDE_ARGS = ["-p", "--output-format", "stream-json", "--verbose", "--restricted", "--no-session-persistence",
               "--model", "opus"]


@pytest.mark.usefixtures("needs_bwrap")  # the tasks' gates run in the existing gate sandbox
def test_rehearsal_runs_the_real_service_path_sequentially_with_a_restart(tmp_path: Path, fake_home: Path) -> None:
    del fake_home
    if not shutil.which("node"):
        pytest.skip("the Frontend gate runs the TypeScript utility with node")
    agents = {p: install_fake_agent(tmp_path / "fake-agents", p) for p in ("claude", "codex")}
    for agent in agents.values():
        Path(f"{agent}.json").write_text(json.dumps({"auth": "subscription", "follow_prompt": True, "around": {
            p: {"before": [{"do": "sleep", "seconds": 4}]} for p in (
                "backend/w4smoke/scoring.py", "backend/w4smoke/test_scoring.py", "cli/w4smoke/greet.py",
                "frontend/w4smoke/slugify.ts")}}), encoding="utf-8")
    report = Harness(tmp_path / "w4-smoke", agents, fake=True, echo=lambda _line: None, session_timeout=120,
                     total_seconds=900, health_interval=0.5).run()
    failed = [c for c in report["checks"] if not c["ok"]]
    assert report["passed"] and not failed, failed
    steps = [(s["step"], s["provider"], s["model"]) for s in report["invocations"]]
    assert steps == [("module", "claude", "opus"), ("validation-test", "claude", "opus"),
                     ("cli-utility", "claude", "sonnet"), ("ts-utility", "codex", "CLI default")]
    assert len(steps) == MAX_SESSIONS
    backend = report["scheduling"]["backend"]
    assert (backend["launches"], backend["attempts"], backend["history"], backend["attention"]) == (
        2, {}, ["cancelled", "success"], None)
    assert backend["reasons"][0] == "stopped before step 'validation-test'"  # the production cancel check
    assert report["monitor"]["max_concurrent_run_tags"] == 1 and not report["monitor"]["overlaps"]
    assert report["final_health"]["state"] == "OFFLINE" and report["final_health"]["alive"] is False
    assert report["verdict"] == "VERIFIED" and report["primary_failure"] is None and not report["safety_violations"]
    assert [(s["exit_kind"], s["final_state"]) for s in report["services"]][1] == ("NATURAL_IDLE_EXIT", "AVAILABLE")
    assert report["services"][0]["exit_kind"] == "OPERATOR_STOP"
    assert all(g["status"] == "passed" for g in report["gate_probe"].values())  # the gate sandbox works here


@pytest.mark.usefixtures("needs_bwrap")
def test_rehearsal_usage_limit_waits_and_starts_no_further_session(tmp_path: Path, fake_home: Path) -> None:
    """Claude reports a usage limit on Backend's first step: classified as a subscription wait
    (WAITING, no retry), the harness stops the service after the active task, nothing is left."""
    del fake_home
    agents = {p: install_fake_agent(tmp_path / "fake-agents", p) for p in ("claude", "codex")}
    limited = {"before": [{"do": "exit", "code": 1, "message": "Claude AI usage limit reached; try again later"}]}
    Path(f"{agents['claude']}.json").write_text(json.dumps({"auth": "subscription", "follow_prompt": True, "around": {
        "backend/w4smoke/scoring.py": limited}}), encoding="utf-8")
    Path(f"{agents['codex']}.json").write_text(json.dumps({"auth": "subscription", "follow_prompt": True}),
                                               encoding="utf-8")
    report = Harness(tmp_path / "w4-smoke", agents, fake=True, echo=lambda _line: None, session_timeout=120,
                     total_seconds=900, health_interval=0.5).run()
    assert not report["passed"]
    steps = [s["step"] for s in report["invocations"]]
    assert steps[0] == "module" and steps.count("module") == 1 and set(steps) <= {"module", "ts-utility"}, steps
    backend = report["scheduling"]["backend"]
    assert backend["history"] == ["subscription"] and backend["waiting"]["category"] == "subscription"
    assert backend["launches"] == 1 and backend["attempts"] == {"subscription": 1}
    assert any("waiting" in why for why in report["unexpected"]) and report["stops"][-1]["graceful"] is False
    assert [s.get("exit") for s in report["services"]] == [0]  # one service, stopped after the active task
    cleanup = [c for c in report["checks"] if c["check"].startswith("J.")]
    assert cleanup and all(c["ok"] for c in cleanup), cleanup


def slow_agents(root: Path, seconds: int) -> dict[str, Path]:
    """Fake CLIs that follow the prompts, each step taking *seconds*."""
    agents = {p: install_fake_agent(root / "fake-agents", p) for p in ("claude", "codex")}
    for agent in agents.values():
        Path(f"{agent}.json").write_text(json.dumps({"auth": "subscription", "follow_prompt": True, "around": {
            p: {"before": [{"do": "sleep", "seconds": seconds}]} for p in (
                "backend/w4smoke/scoring.py", "backend/w4smoke/test_scoring.py", "cli/w4smoke/greet.py",
                "frontend/w4smoke/slugify.ts")}}), encoding="utf-8")
    return agents


def test_a_harness_error_stops_the_worker_and_still_writes_the_report(tmp_path: Path, fake_home: Path) -> None:
    del fake_home

    class Crashing(Harness):
        """A harness that fails while service 1's first agent runs."""

        def service1(self) -> bool:
            self.start_service(1)
            assert self.wait(lambda: any(e["decision"] == "started" for e in self.invocations()), 120)
            raise RuntimeError("simulated harness bug while an agent runs")

    harness = Crashing(tmp_path / "w4-smoke", slow_agents(tmp_path, 8), fake=True, echo=lambda _line: None,
                       session_timeout=120, total_seconds=900, health_interval=0.5)
    report = harness.run()
    assert json.loads((tmp_path / "w4-smoke" / "reports" / "report.json").read_text(encoding="utf-8"))["checks"]
    failed = {c["check"]: c["detail"] for c in report["checks"] if not c["ok"]}
    assert "simulated harness bug" in failed["the scenario ran without a harness error"]
    assert report["stops"][-1]["why"] == "the harness stopped early" and report["stops"][-1]["graceful"] is True
    assert [s.get("exit") for s in report["services"]] == [0]
    assert [s["step"] for s in report["invocations"]] == ["module"]  # cancelled; nothing started after
    assert report["scheduling"]["backend"]["history"] == ["cancelled"]
    cleanup = [c for c in report["checks"] if c["check"].startswith("J.")]
    assert cleanup and all(c["ok"] for c in cleanup), cleanup


def test_a_dead_service_leaves_its_launch_no_further_session(tmp_path: Path, fake_home: Path) -> None:
    """The service is SIGKILLed while its agent runs (the harness lives): the harness stops supervising
    and closes the session gate, so the orphaned launch cannot start the task's next step."""
    del fake_home

    class ServiceDies(Harness):
        """Service 1 dies (SIGKILL) during Backend's first session; no hook, no restart."""

        def service1(self) -> bool:
            proc = self.start_service(1)
            assert self.wait(lambda: any(e["decision"] == "started" for e in self.invocations()), 120)
            os.kill(proc.pid, signal.SIGKILL)
            self.services[-1]["exit"] = proc.wait(timeout=30)
            return False

    report = ServiceDies(tmp_path / "w4-smoke", slow_agents(tmp_path, 4), fake=True, echo=lambda _line: None,
                         session_timeout=120, total_seconds=900, health_interval=0.5).run()
    decisions = [(e["decision"], e["step"]) for e in report["audit"] if e["decision"] in ("started", "refused")]
    assert decisions == [("started", "module"), ("refused", "validation-test")], decisions
    refused = next(e for e in report["audit"] if e["decision"] == "refused")
    assert any("the harness no longer accepts sessions" in p for p in refused["problems"]), refused["problems"]
    processes = [c for c in report["checks"] if c["check"].startswith(("J. no Claude", "J. no other process"))]
    assert len(processes) == 2 and all(c["ok"] for c in processes), processes


def bystanders() -> list[subprocess.Popen[bytes]]:
    """Processes that are NOT part of the test — named like agents, carrying an unrelated run tag, or
    looking like a Worker. Every scenario must leave them running."""
    # pylint: disable=consider-using-with
    code = "import time; time.sleep(600)"
    return [subprocess.Popen([sys.executable, "-c", code, "claude", "codex"]),
            subprocess.Popen([sys.executable, "-c", code],
                             env={**os.environ, "FC_RUN_TAG": secrets.token_hex(8), "FC_WORKER_LAUNCH": "other.1"}),
            subprocess.Popen([sys.executable, "-c", code, "-m", "feature_coordinator", "worker", "start"])]


def stop(processes: list[subprocess.Popen[bytes]]) -> None:
    for proc in processes:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


DRIVER = """import sys
from pathlib import Path
sys.path[:0] = [{here!r}, {scripts!r}]
from fc_worker_smoke import Harness

class NoHook(Harness):
    def service1(self):
        self.run_service(self.start_service(1), boundary=False)
        return False

agents = {{"claude": Path({claude!r}), "codex": Path({codex!r})}}
NoHook(Path({workdir!r}), agents, fake=True, die_with_harness=False, session_timeout=120, total_seconds=900,
       health_interval=0.5).run()
"""


def test_scenario_1_harness_dies_worker_survives_no_new_session(tmp_path: Path, fake_home: Path) -> None:
    """The harness is SIGKILLed while an agent runs; its Worker and launch SURVIVE (the parent-death
    stop is switched off here, so the wrapper's harness check is proven on its own). The running
    session finishes and is checkpointed; every later session is refused; the Worker then stops by
    itself (nothing it can do); unrelated processes are untouched."""
    del fake_home
    others = bystanders()
    agents = slow_agents(tmp_path, 4)
    workdir = tmp_path / "w4-smoke"
    driver = tmp_path / "driver.py"
    driver.write_text(DRIVER.format(here=str(HERE), scripts=str(REPO_ROOT), claude=str(agents["claude"]),
                                    codex=str(agents["codex"]), workdir=str(workdir)), encoding="utf-8")
    harness = subprocess.Popen(  # pylint: disable=consider-using-with
        [sys.executable, str(driver)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    audit = workdir / "audit" / "agent-invocations.jsonl"

    def audited(decision: str) -> list[dict[str, Any]]:
        lines = audit.read_text(encoding="utf-8").splitlines() if audit.exists() else []
        return [e for e in map(json.loads, lines) if e["decision"] == decision]
    try:
        assert Harness.wait(lambda: bool(audited("started")), 120), "no session started"
        harness.kill()  # the harness dies; the Worker (own session, no parent-death signal) lives on
        harness.wait(timeout=30)
        assert Path(f"/proc/{audited('started')[0]['service_pid']}").exists()  # the Worker survived the harness
        assert Harness.wait(lambda: not test_processes(tmp_path), 180), test_processes(tmp_path)
        assert all(p.poll() is None for p in others), "an unrelated process was terminated"
    finally:
        stop([harness, *others])
    assert [e["step"] for e in audited("started")] == ["module"]  # no session after the harness died
    refused = audited("refused")
    assert [e["step"] for e in refused] == ["validation-test", "ts-utility"], refused
    assert all("the harness that supervises this test is not running" in e["problems"] for e in refused)
    ended = audited("ended")
    assert [(e["step"], e["exit"]) for e in ended] == [("module", 0)]  # the running session was not cut off
    record = json.loads(next((workdir / "state" / "checkpoints").glob("*.backend.json")).read_text(encoding="utf-8"))
    assert [(c["step"], c["status"]) for c in record["checkpoints"]] == [("module", "committed")]
    commits = subprocess.run(["git", "-C", str(workdir / "repo"), "rev-list", "dev..feature/autonomous-worker-smoke"],
                             capture_output=True, text=True, check=True).stdout.split()
    assert commits == [record["checkpoints"][0]["commit"]]  # the completed checkpoint survived
    log = (workdir / "audit" / "service-1.log").read_text(encoding="utf-8")
    assert "NEEDS_ATTENTION" in log and "stopped (stop condition reached)" in log, log[-1500:]
    assert not StateStore(workdir / "state").list_claims()


def test_scenario_2_worker_dies_harness_lives_recovery_needs_authorization(tmp_path: Path, fake_home: Path) -> None:
    """The Worker is SIGKILLed while its agent runs; the harness and the launch SURVIVE. Without any
    revocation by the harness, the wrapper refuses the orphaned launch's next step (its Worker is not
    the authorized, living one). A Worker the harness did not start cannot spend a session either;
    only a NEW authorized Worker recovers — from the surviving checkpoint, within the 4-session budget."""
    del fake_home
    others = bystanders()

    class WorkerDies(Harness):
        """Service 1 dies mid-session; a rogue Worker tries; an authorized service 2 recovers."""

        def checkpoints(self) -> list[tuple[str, str, str]]:
            from feature_coordinator.checkpoints import load_record  # pylint: disable=import-outside-toplevel
            return [(c.id, c.step, c.commit) for c in load_record(self.store(), self.task_id("backend")).checkpoints]

        def service1(self) -> bool:
            proc = self.start_service(1)
            assert self.wait(lambda: self.started_steps() == ["module"], 120)
            os.kill(proc.pid, signal.SIGKILL)  # the Worker dies; the harness and its launch live on
            self.services[-1]["exit"] = proc.wait(timeout=30)
            # no revocation here: the wrapper's identity check alone must stop the orphaned launch
            assert self.wait(lambda: not self.monitor.sample()[1], 180), "the orphaned launch did not end"
            self.report["after_orphan"] = self.checkpoints()
            self.feature("worker", "reset-retries", self.task_id("backend"), "--apply")  # an operator decision
            rogue = subprocess.run([*CLI, "--repo", str(self.repo), "--state-dir",
                                    str(self.state), "--project-config", str(self.config), "worker", "start",
                                    "--exit-when-idle"], env=self.env(), capture_output=True, text=True, timeout=600,
                                   check=False, cwd=REPO_ROOT)  # recovery by a Worker the harness did not authorize
            self.report["rogue"] = {"exit": rogue.returncode, "after": self.checkpoints()}
            for key in ("backend", "frontend"):
                self.feature("worker", "reset-retries", self.task_id(key), "--apply")
            proc = self.start_service(2)  # recovery by a NEW authorized Worker
            self.services[-1]["exit"] = proc.wait(timeout=600)
            self.revoke("service 2 exited")
            return True

    try:
        report = WorkerDies(tmp_path / "w4-smoke", slow_agents(tmp_path, 3), fake=True, echo=lambda _line: None,
                            session_timeout=120, total_seconds=900, health_interval=0.5).run()
        assert Harness.wait(lambda: not test_processes(tmp_path), 60), test_processes(tmp_path)
        assert all(p.poll() is None for p in others), "an unrelated process was terminated"
    finally:
        stop(others)
    s1, s2 = (s["pid"] for s in report["services"])
    grants = {g["service"]: g["id"] for g in report["grants"]}
    started = [(e["step"], e["service_pid"], e["authorization"]) for e in report["audit"] if e["decision"] == "started"]
    assert started == [("module", s1, grants[1]), ("validation-test", s2, grants[2]), ("cli-utility", s2, grants[2]),
                       ("ts-utility", s2, grants[2])], started
    assert len(started) == MAX_SESSIONS  # the budget held across the death and both recoveries
    refused = [(e["step"], e["service_pid"]) for e in report["audit"] if e["decision"] == "refused"]
    assert [step for step, _pid in refused] == ["validation-test", "validation-test", "ts-utility"], refused
    assert refused[0][1] != s1 and refused[1][1] not in (s1, s2)  # the orphan's new parent; the rogue Worker
    orphan, rogue = (e for e in report["audit"] if e["decision"] == "refused" and e["step"] == "validation-test")
    assert any("the authorized Worker" in p for p in orphan["problems"]), orphan["problems"]
    assert not any("harness" in p for p in orphan["problems"])  # the harness was alive and still open
    assert any("Worker" in p for p in rogue["problems"]), rogue["problems"]
    first = report["after_orphan"]
    assert [step for _id, step, _commit in first] == ["module"]
    assert report["rogue"]["after"] == first  # the completed checkpoint survived, unchanged
    final = [(c["id"], c["step"], c["commit"]) for c in report["checkpoints"] if c["task"] == "backend"]
    assert final[0] == first[0] and [step for _id, step, _commit in final] == ["module", "validation-test"]
    assert report["tasks"]["states"] == {"backend": "verified", "cli": "verified", "frontend": "verified"}


def test_a_killed_harness_never_leaves_a_worker_running(tmp_path: Path, fake_home: Path) -> None:
    """SIGKILL of the harness process while an agent runs: the parent-death signal gives the service
    its graceful stop, the agent is cancelled, no further session starts, nothing is left."""
    del fake_home
    workdir = tmp_path / "w4-smoke"
    harness = subprocess.Popen(  # pylint: disable=consider-using-with
        [sys.executable, str(HERE / "fc_worker_smoke.py"), "--agent", "fake", "--apply", "--workdir", str(workdir)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    audit = workdir / "audit" / "agent-invocations.jsonl"

    def started() -> list[dict[str, Any]]:
        lines = audit.read_text(encoding="utf-8").splitlines() if audit.exists() else []
        return [e for e in map(json.loads, lines) if e["decision"] == "started"]
    try:
        assert Harness.wait(lambda: bool(started()), 120), "no session started"
        harness.kill()
        harness.wait(timeout=30)
        assert Harness.wait(lambda: not test_processes(tmp_path), 120), test_processes(tmp_path)
    finally:
        if harness.poll() is None:
            harness.kill()
            harness.wait()
    assert [e["step"] for e in started()] == ["module"]  # no session after the harness died
    log = (workdir / "audit" / "service-1.log").read_text(encoding="utf-8")
    assert "STOPPING" in log and "stopped (stop signal)" in log, log
    store = StateStore(workdir / "state")
    assert not store.list_claims()
    sched = json.loads(next((workdir / "state" / "scheduling").glob("*.backend.json")).read_text(encoding="utf-8"))
    assert [h["category"] for h in sched["history"]] == ["cancelled"] and sched["launches"] == 1


def test_harness_helpers_are_not_test_processes_but_services_are(tmp_path: Path) -> None:
    """Regression (a rehearsal failed once): the harness's own `python -m feature_coordinator worker health` sampler,
    whose command line names the run directory, was counted as a leftover process of the test."""
    harness = Harness(tmp_path / "w4-smoke", {}, fake=True, echo=lambda _line: None)
    argv = [sys.executable, "-c", "import time; time.sleep(60)", str(harness.state)]  # like `worker health`
    helper = subprocess.Popen(argv)  # pylint: disable=consider-using-with
    service = subprocess.Popen(argv)  # pylint: disable=consider-using-with
    try:
        assert Harness.wait(lambda: all(Path(f"/proc/{p.pid}/cmdline").read_bytes().count(b"sleep(60)")
                                        for p in (helper, service)), 10)
        harness.services.append({"number": 1, "pid": service.pid})
        found = harness.processes()
        assert [line.split(":", 1)[0] for line in found] == [str(service.pid)], found
    finally:
        stop([helper, service])


def fake_bwrap(directory: Path, flag: Path | None = None) -> Path:
    """`bwrap` as it behaved in the W4.2 Phase B run: started from an unconfined terminal under Ubuntu's
    AppArmor restriction of unprivileged user namespaces, it cannot set up its namespace. With *flag*,
    it fails only once that file exists (a sandbox that breaks after the preflight)."""
    directory.mkdir(parents=True, exist_ok=True)
    fake = directory / "bwrap"
    broken = "echo 'bwrap: setting up uid map: Permission denied' >&2; exit 1"
    body = f"if [ -e '{flag}' ]; then {broken}; fi\nexec {shutil.which('bwrap')} \"$@\"\n" if flag else broken + "\n"
    fake.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    fake.chmod(0o755)
    return fake


class BrokenSandbox(Harness):
    """The services (and the preflight's gate probe) find the fake `bwrap` first on PATH."""

    fake: Path = Path()
    flag: Path | None = None

    def env(self, **extra: str) -> dict[str, str]:
        env = super().env(**extra)
        env["PATH"] = os.pathsep.join([str(self.fake.parent), env["PATH"]])
        return env

    def service1(self) -> bool:
        if self.flag is not None:
            self.flag.touch()  # the sandbox breaks only now, after the preflight passed
        return super().service1()


@pytest.mark.usefixtures("needs_bwrap")
def test_preflight_refuses_when_the_gate_sandbox_cannot_run_and_starts_no_session(tmp_path: Path,
                                                                                    fake_home: Path) -> None:
    del fake_home
    harness = BrokenSandbox(tmp_path / "w4-smoke", slow_agents(tmp_path, 1), fake=True, echo=lambda _line: None,
                            session_timeout=120, total_seconds=900, health_interval=0.5)
    harness.fake = fake_bwrap(tmp_path / "fakebin")
    report = harness.run()
    assert report["verdict"] == "BLOCKED" and report["preflight_failures"], report["preflight_failures"]
    assert report["preflight_failures"][-1].startswith("the gate sandbox works in the services' environment")
    assert all(g["status"] == "blocked" and "Permission denied" in g["summary"] for g in report["gate_probe"].values())
    assert report["services"] == [] and [e for e in report["audit"] if e["decision"] != "passthrough"] == []
    assert not test_processes(tmp_path)


@pytest.mark.usefixtures("needs_bwrap")
def test_reproduction_gate_sandbox_breaks_after_preflight(tmp_path: Path, fake_home: Path) -> None:
    """The W4.2 Phase B failure path, offline: Backend's two sessions run and are checkpointed, then
    its gate cannot run (bwrap cannot set up its namespace). Recorded with its cause, CRITICAL (no AI
    redo), the harness stops the Worker, Frontend gets no session (or a visible refusal), CLI never
    starts; the report names the primary failure and finds NO safety violation."""
    del fake_home
    harness = BrokenSandbox(tmp_path / "w4-smoke", slow_agents(tmp_path, 3), fake=True, echo=lambda _line: None,
                            session_timeout=120, total_seconds=900, health_interval=0.5)
    harness.fake, harness.flag = fake_bwrap(tmp_path / "fakebin", tmp_path / "broken"), tmp_path / "broken"
    report = harness.run()
    started = [e["step"] for e in report["audit"] if e["decision"] == "started"]
    assert started == ["module", "validation-test"], started  # no other session
    backend = report["scheduling"]["backend"]
    assert backend["history"] == ["cancelled", "critical"] and backend["attention"]["category"] == "critical"
    assert backend["reasons"][-1].startswith("gates could not run: ['w4s-backend']")
    assert "bwrap cannot create namespaces here" in backend["reasons"][-1]
    assert "Permission denied" in backend["reasons"][-1]
    frontend = report["scheduling"]["frontend"]
    refusal = "refused by the W4.2 audit wrapper (no model session)"
    assert frontend["launches"] == 0 or refusal in frontend["reasons"][-1], frontend
    assert report["scheduling"]["cli"]["launches"] == 0
    assert report["verdict"] == "PARTIALLY VERIFIED" and report["safety_violations"] == [], report["safety_violations"]
    primary = report["primary_failure"]
    assert primary["subject"] == "autonomous-worker-smoke.backend" and primary["cause"].startswith("environment:")
    assert "A. exactly four sessions, one per step, none refused, every one ended with exit 0" in \
        report["secondary_consequences"]
    assert report["other_findings"] == [], report["other_findings"]  # every failed check is explained
    assert [s["exit_kind"] for s in report["services"]] == ["OPERATOR_STOP", "OPERATOR_STOP"]  # never "idle"
    final = [(c["task"], c["step"], c["status"]) for c in report["checkpoints"]]
    assert final == [("backend", "module", "committed"), ("backend", "validation-test", "committed")]
    assert not test_processes(tmp_path)


# -- the audit wrapper ---------------------------------------------------------------------------------


class Rig:  # pylint: disable=too-few-public-methods
    """A repository, a state directory with run records, the audit log, a fake CLI and its wrapper."""

    def __init__(self, root: Path, provider: str = "claude", auth: str = "subscription") -> None:
        self.root, self.provider = root, provider
        self.repo, self.state, self.audit = root / "repo", root / "state", root / "audit" / "agent-invocations.jsonl"
        for directory in (self.repo, self.state / "runs" / "earlier", self.state / "runs" / "now", self.audit.parent):
            directory.mkdir(parents=True, exist_ok=True)
        self.tag, self.earlier_tag = secrets.token_hex(8), secrets.token_hex(8)
        (self.state / "runs" / "earlier" / "native.json").write_text(json.dumps({"run_tag": self.earlier_tag}))
        (self.state / "runs" / "now" / "native.json").write_text(json.dumps({"run_tag": self.tag}))
        self.real = install_fake_agent(root / "agents", provider)
        Path(f"{self.real}.json").write_text(json.dumps({"auth": auth, "follow_prompt": True}), encoding="utf-8")
        self.control = root / "audit" / "control.json"
        self.write_control()
        self.wrapper = root / "bin" / f"{provider}-audit"
        self.wrapper.parent.mkdir()
        self.wrapper.write_text(AUDIT_WRAPPER, encoding="utf-8")
        self.wrapper.chmod(0o755)
        Path(f"{self.wrapper}.json").write_text(json.dumps({
            "provider": provider, "real": str(self.real), "control": str(self.control),
            "sha256": hashlib.sha256(self.real.read_bytes()).hexdigest()}), encoding="utf-8")

    def write_control(self, **changes: Any) -> None:
        control = {"repo": os.path.realpath(self.repo), "state": str(self.state), "audit": str(self.audit),
                   "max_sessions": 4, "session_timeout": 60, "deadline": time.time() + 3600,
                   "harness": {"pid": os.getpid(), "start_ticks": start_ticks(os.getpid())},  # this test: alive
                   # the wrapper runs as pytest's child: pytest is its "launch", pytest's parent its Worker
                   "authorization": {"id": "rig", "worker": {"pid": os.getppid(),
                                                             "start_ticks": start_ticks(os.getppid())}},
                   "budget": {"backend": 2, "cli": 1, "frontend": 1},
                   "steps": {"module": {"task": "f.backend", "role": "backend", "provider": "claude", "model": "opus"},
                             "validation-test": {"task": "f.backend", "role": "backend", "provider": "claude",
                                                 "model": "opus"},
                             "ts-utility": {"task": "f.frontend", "role": "frontend", "provider": "codex",
                                            "model": ""}}}
        control.update(changes)
        self.control.write_text(json.dumps(control), encoding="utf-8")

    def earlier(self, **entry: Any) -> None:
        """An audit record of an earlier session (dead by default: an impossible start time)."""
        record = {"decision": "started", "pid": os.getpid(), "start_ticks": -1, "run_tag": self.earlier_tag,
                  "role": "backend", "step": "validation-test", **entry}
        with open(self.audit, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def run(self, args: list[str], *, prompt: str | None = None, cwd: Path | None = None,
            env: dict[str, str] | None = None, role: str = "backend", task: str = "f.backend",
            step: str = "module") -> subprocess.CompletedProcess[str]:
        base = {"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "FC_RUN_TAG": self.tag}
        return subprocess.run([str(self.wrapper), *args], input=prompt if prompt is not None else PROMPT.format(
            role=role, task=task, step=step), capture_output=True, text=True, cwd=cwd or self.repo,
                              env={**base, **(env or {})}, timeout=60, check=False)

    def ran(self) -> bool:
        return (self.repo / "backend" / "w4" / "x.py").exists()

    def log(self) -> list[dict[str, Any]]:
        if not self.audit.exists():
            return []
        return [json.loads(line) for line in self.audit.read_text(encoding="utf-8").splitlines() if line.strip()]


def sleeper(root: Path, env: dict[str, str] | None = None) -> subprocess.Popen[bytes]:
    argv = [sys.executable, "-c", "import time; time.sleep(120)", str(root)]
    return subprocess.Popen(argv, env={**os.environ, **(env or {})})  # pylint: disable=consider-using-with


def start_ticks(pid: int) -> int:
    with open(f"/proc/{pid}/stat", encoding="ascii") as handle:
        return int(handle.read().rsplit(")", 1)[1].split()[19])


def test_wrapper_starts_a_verified_session_and_logs_its_end(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    result = rig.run(CLAUDE_ARGS)
    assert result.returncode == 0, result.stderr
    assert rig.ran()
    log = rig.log()
    assert [e["decision"] for e in log] == ["started", "spawned", "ended"]
    started, spawned, ended = log
    assert (started["step"], started["role"], started["task"], started["model"]) == ("module", "backend", "f.backend",
                                                                                   "opus")
    assert started["login"].startswith("claude.ai subscription") and "email" not in json.dumps(log)
    assert started["earlier_sessions_alive"] == [] and started["earlier_tag_processes"] == []
    assert spawned["child_pid"] != started["pid"] and ended["exit"] == 0 and ended["run_tag"] == rig.tag


def test_wrapper_passes_known_status_calls_through_and_refuses_other_calls(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    assert rig.run(["--version"], prompt="").stdout.strip() == "fake-agent 1.0"
    assert json.loads(rig.run(["auth", "status", "--json"], prompt="").stdout)["authMethod"] == "claude.ai"
    refused = rig.run(["mcp", "list"], prompt="")
    assert refused.returncode == 97 and "not a known non-session call" in refused.stderr
    assert [e["decision"] for e in rig.log()] == ["passthrough", "passthrough", "refused"]


@pytest.mark.parametrize("case, expected", [
    ("api-key variable", "API-key/token/provider variables present"),
    ("api-key login", "does not report the subscription login"),
    ("unknown login", "does not report the subscription login"),
    ("not logged in", "does not report the subscription login"),
    ("other model", "is not the routed"),
    ("no --restricted", "subscription-only flags missing"),
    ("outside the repository", "is not the disposable repository"),
    ("unknown step", "unknown step"),
    ("another role", "is not backend/f.backend"),
    ("unrecorded run tag", "no run tag recorded by the coordinator"),
    ("total budget used", "session budget exhausted"),
    ("role budget used", "the backend session budget is exhausted"),
    ("step already ran", "already had a session"),
    ("earlier session alive", "a process of an earlier session is still alive"),
    ("earlier run tag alive", "processes with an earlier run tag of this test are still alive"),
    ("binary changed", "the CLI binary changed since preparation"),
    ("past the deadline", "could not end before the test's deadline"),
    ("harness gone", "the harness that supervises this test is not running"),
    ("harness closed", "the harness no longer accepts sessions"),
    ("no Worker authorized", "no Worker is authorized to start sessions"),
    ("authorization revoked", "the Worker authorization rig was revoked"),
    ("authorized Worker gone", "the authorized Worker"),
    ("another Worker authorized", "is not the authorized Worker"),
])
def test_wrapper_refuses_a_session_unless_every_check_passes(tmp_path: Path, case: str, expected: str) -> None:
    # pylint: disable=too-many-branches,too-many-statements
    rig = Rig(tmp_path, auth={"api-key login": "api-key", "unknown login": "unknown",
                              "not logged in": "none"}.get(case, "subscription"))
    args, kwargs, live = list(CLAUDE_ARGS), {}, None
    if case == "api-key variable":
        kwargs["env"] = {"ANTHROPIC_API_KEY": secrets.token_hex(12)}  # a synthetic value, generated at runtime
    elif case == "other model":
        args[args.index("opus")] = "sonnet"
    elif case == "no --restricted":
        args.remove("--restricted")
    elif case == "outside the repository":
        kwargs["cwd"] = tmp_path
    elif case == "unknown step":
        kwargs["step"] = "something-else"
    elif case == "another role":
        kwargs["role"] = "cli"
    elif case == "unrecorded run tag":
        kwargs["env"] = {"FC_RUN_TAG": secrets.token_hex(8)}
    elif case == "total budget used":
        for step in ("a", "b", "c", "d"):
            rig.earlier(step=step, role="frontend")
    elif case == "role budget used":
        rig.earlier(step="x")
        rig.earlier(step="y")
    elif case == "step already ran":
        rig.earlier(step="module")
    elif case == "earlier session alive":
        live = sleeper(tmp_path)
        rig.earlier(pid=live.pid, start_ticks=start_ticks(live.pid))
    elif case == "earlier run tag alive":
        live = sleeper(tmp_path, {"FC_RUN_TAG": rig.earlier_tag})
        Harness.wait(lambda: Path(f"/proc/{live.pid}/environ").read_bytes().count(b"FC_RUN_TAG") == 1, 10)
    elif case == "binary changed":
        with open(rig.real, "a", encoding="utf-8") as handle:
            handle.write("# changed after preparation\n")
    elif case == "past the deadline":
        rig.write_control(deadline=time.time() + 30)  # a 60 s session could not end in time
    elif case == "harness gone":
        rig.write_control(harness={"pid": os.getpid(), "start_ticks": -1})  # that identity no longer runs
    elif case == "harness closed":
        rig.write_control(closed="the harness stopped supervising")
    elif case == "no Worker authorized":
        rig.write_control(authorization=None)
    elif case == "authorization revoked":
        rig.write_control(authorization={"id": "rig", "revoked": "service 1 exited", "worker": {
            "pid": os.getppid(), "start_ticks": start_ticks(os.getppid())}})
    elif case == "authorized Worker gone":
        rig.write_control(authorization={"id": "rig", "worker": {"pid": os.getppid(), "start_ticks": -1}})
    elif case == "another Worker authorized":
        live = sleeper(tmp_path)  # alive and authorized, but not the Worker that started this launch
        rig.write_control(authorization={"id": "rig", "worker": {"pid": live.pid,
                                                                 "start_ticks": start_ticks(live.pid)}})
    try:
        result = rig.run(args, **kwargs)
    finally:
        if live is not None:
            live.kill()
            live.wait()
    assert result.returncode == 97, (result.stdout, result.stderr)
    assert expected in result.stderr
    assert not rig.ran()  # the CLI never ran
    assert rig.log()[-1]["decision"] == "refused" and "CURRENT STEP" not in rig.audit.read_text(encoding="utf-8")


@pytest.mark.parametrize("provider", ["claude", "codex"])
def test_a_refusal_is_recorded_with_its_reason_not_as_a_bare_exit_97(tmp_path: Path, provider: str) -> None:
    """W4.2 Phase B: Frontend's run was recorded as `runner exit 97:` with no reason. The refusal now
    also speaks the CLI's own output format, so the production launchers record why."""
    rig = Rig(tmp_path, provider=provider)
    rig.write_control(authorization={"id": "rig", "revoked": "the service reports NEEDS_ATTENTION", "worker": {
        "pid": os.getppid(), "start_ticks": start_ticks(os.getppid())}})
    if provider == "claude":
        result = rig.run(CLAUDE_ARGS)
    else:
        result = rig.run(["exec", "--json", "--strict-config", "--ignore-user-config", "-C", str(rig.repo), "-c",
                          'forced_login_method="chatgpt"', "-"],
                         prompt=PROMPT.format(role="frontend", task="f.frontend", step="ts-utility"))
    assert result.returncode == 97 and not rig.ran()
    stdout = tmp_path / "stdout.jsonl"
    stdout.write_text(result.stdout, encoding="utf-8")
    ok, summary, _details = (ClaudeCodeLauncher if provider == "claude" else CodexLauncher).parse(stdout)
    assert not ok and "refused by the W4.2 audit wrapper (no model session)" in summary
    assert "the Worker authorization rig was revoked (the service reports NEEDS_ATTENTION)" in summary


def test_wrapper_checks_codex_subscription_flags_and_routing(tmp_path: Path) -> None:
    rig = Rig(tmp_path, provider="codex")
    args = ["exec", "--json", "--strict-config", "--sandbox", "workspace-write", "--ephemeral", "--ignore-user-config",
            "-C", str(rig.repo), "-c", 'forced_login_method="chatgpt"', "-"]
    prompt = PROMPT.format(role="frontend", task="f.frontend", step="ts-utility")
    missing = rig.run([a for a in args if a not in ("-c", 'forced_login_method="chatgpt"')], prompt=prompt)
    assert missing.returncode == 97 and "subscription-only flags missing" in missing.stderr
    claude_step = rig.run(args, prompt=PROMPT.format(role="backend", task="f.backend", step="module"))
    assert claude_step.returncode == 97 and "belongs to claude, not codex" in claude_step.stderr
    with_model = rig.run([*args[:-1], "-m", "gpt-x", "-"], prompt=prompt)
    assert with_model.returncode == 97 and "is not the routed 'CLI default'" in with_model.stderr
    assert not rig.ran()
    ok = rig.run(args, prompt=prompt)
    assert ok.returncode == 0, ok.stderr
    started = [e for e in rig.log() if e["decision"] == "started"]
    assert len(started) == 1 and started[0]["login"] == "ChatGPT subscription" and started[0]["model"] == "CLI default"
