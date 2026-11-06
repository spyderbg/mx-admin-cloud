"""W4.6.1 (R2): the Desktop Worker reviews each verified Draft PR by itself, asynchronously (review_worker.py, the
reviewer process of review_process.py, the service hook in service.py), on the W4.3.3 publication rig.

Reviewer processes are REAL OS processes (process identity, tags in /proc, signals, orphans) running the real
`run_exec` with a scripted reviewer instead of Claude Code (fc_fake_reviewer_process.py); the end-to-end test of
`python -m feature_coordinator github review exec` uses a fake `claude` executable. The configured reviewer CLI of
every other test does not exist, so nothing here can reach a real CLI. No model, no real GitHub, no network."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,too-many-lines

from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import signal
import stat
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

from feature_coordinator import review_worker
from feature_coordinator.autonomy import health_report, iso, new_service_record, write_control
from feature_coordinator.cli import EXAMPLE_CONFIG
from feature_coordinator.cli import main as cli_main
from feature_coordinator.config import default_config
from feature_coordinator.errors import CoordinatorError, GitHubUnavailableError
from feature_coordinator.issue_sync import SimulatedCrash
from feature_coordinator.locks import ProcessIdentity
from feature_coordinator.model import FeatureState
from feature_coordinator.review import ReviewRunner, load_review, review_evidence
from feature_coordinator.review_context import ReviewContext
from feature_coordinator.review_process import (
    REVIEW_TAG_ENV,
    attempt_processes,
    load_result,
    process_environment,
    result_path,
    run_exec,
    stop_attempt,
    tagged_run,
    write_request,
)
from feature_coordinator.review_result import FileFacts, review_schema
from feature_coordinator.review_worker import (
    REVIEW_SLACK_SECONDS,
    ReviewStep,
    github_steps,
    idle_state,
    review_state,
    review_view,
    stop_reviews,
)
from feature_coordinator.reviewer import ReviewRequest, ReviewResult
from feature_coordinator.service import WorkerService
from feature_coordinator.supervisor import tagged_processes

from test_fc_autonomy import Clock
from test_fc_intake import BODY, outside
from test_fc_publish import (  # pylint: disable=unused-import
    PubRig,
    draft_open,
    github_origin,
    remote,
    verified,
)
from test_fc_review import FakeReviewer, ok, pr_comments, review, review_config

REPO_ROOT = Path(__file__).resolve().parents[2]
FAKE_PROCESS = Path(__file__).resolve().parent / "fc_fake_reviewer_process.py"
NO_CLI = "fc-no-such-claude"  # the configured reviewer CLI: it does not exist, so no test can reach a real one


def worker_config(**review_settings: Any):
    config = review_config(**review_settings)
    providers = {**config.providers, "claude": dataclasses.replace(config.providers["claude"], executable=NO_CLI)}
    runtime = dataclasses.replace(config.runtime, terminate_grace_seconds=2)
    return dataclasses.replace(config, providers=providers, runtime=runtime)


class Launcher:
    """`spawn.start` for the tests: `github review exec RUN_DIR` → the FAKE reviewer process (a real process)."""

    def __init__(self, state: Path, *modes: str) -> None:
        self.state = state
        self.modes = list(modes) or ["pass"]
        self.calls: list[dict[str, Any]] = []
        self.handles: list[subprocess.Popen] = []

    @property
    def tags(self) -> list[str]:
        return [c["env"][REVIEW_TAG_ENV] for c in self.calls]

    def __call__(self, tail: list[str], log_name: str, env: dict[str, str]) -> subprocess.Popen:
        assert list(tail[:3]) == ["github", "review", "exec"] and len(tail) == 4, tail
        mode = self.modes.pop(0) if len(self.modes) > 1 else self.modes[0]
        self.calls.append({"tail": list(tail), "log": log_name, "env": dict(env), "mode": mode})
        full = {**env, "PYTHONPATH": str(REPO_ROOT), "FAKE_REVIEW_MODE": mode, "FAKE_STATE_DIR": str(self.state)}
        (self.state / "logs").mkdir(exist_ok=True)
        with open(self.state / "logs" / log_name, "ab") as log:
            handle = subprocess.Popen([sys.executable, str(FAKE_PROCESS), tail[3]], env=full, stdout=log,  # pylint: disable=consider-using-with
                                      stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
                                      cwd=REPO_ROOT)
        self.handles.append(handle)
        return handle

    def wait(self, timeout: float = 60.0) -> None:
        for handle in self.handles:
            handle.wait(timeout=timeout)

    def cleanup(self) -> None:
        for tag in self.tags:
            for pid in tagged_processes(REVIEW_TAG_ENV, tag):
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
        for handle in self.handles:
            with contextlib.suppress(OSError, subprocess.TimeoutExpired):
                handle.kill()
                handle.wait(timeout=10)


class Spawn:  # pylint: disable=too-few-public-methods
    """The service's spawner: tasks never start here (the rig's features are verified); `.start` = a Launcher."""

    def __init__(self, launcher: Launcher) -> None:
        self.start = launcher

    def __call__(self, task_id: str, attempt: int, env: Any = None) -> Any:
        raise AssertionError(f"no task launch expected ({task_id}, {attempt})")


@pytest.fixture
def wrig(repo: Path, state_dir: Path, github_origin: Path) -> PubRig:
    return PubRig(repo, state_dir, github_origin, config=worker_config())


@pytest.fixture
def launcher(state_dir: Path) -> Iterator[Launcher]:
    found = Launcher(state_dir)
    yield found
    found.cleanup()
    review_worker._CHILDREN.clear()


def step(rig: PubRig, launcher: Launcher | None = None, *, preflight: str = "", config=None,
         faults: tuple[str, ...] = (), clock=lambda: 1000.0, wall=time.time) -> ReviewStep:
    coord = rig.coord if config is None else rig.make(config=config)
    return ReviewStep(coord, start=launcher, preflight=lambda: preflight, faults=faults, clock=clock, wall=wall)


def current(rig: PubRig, feature_id: str) -> dict[str, Any]:
    record = load_review(rig.store, feature_id)
    assert record is not None
    entry = record.entry(record.current)
    assert entry is not None
    return entry


def kinds(rig: PubRig, prefix: str = "review-") -> list[str]:
    return [e["kind"] for e in rig.store.events() if e["kind"].startswith(prefix)]


@contextlib.contextmanager
def github_requests(rig: PubRig) -> Iterator[list[str]]:
    """Every request the review code makes to (mock) GitHub inside the block."""
    calls: list[str] = []
    client = rig.coord.sync.client
    names = ("get_pull_request", "list_pull_requests", "list_comments", "create_comment", "get_issue",
             "list_issue_comments")
    saved = {n: client.__dict__.get(n) for n in names}
    for name in names:
        original = getattr(client, name, None)
        if original is not None:
            setattr(client, name, lambda *a, _o=original, _n=name, **k: (calls.append(_n), _o(*a, **k))[1])
    try:
        yield calls
    finally:
        for name, value in saved.items():
            if value is None:
                client.__dict__.pop(name, None)
            else:
                setattr(client, name, value)


def alive(pid: int) -> bool:
    identity = ProcessIdentity.of_pid(pid)
    return identity is not None and identity.is_alive()


def untagged_sleeper() -> subprocess.Popen:
    return subprocess.Popen(["sleep", "60"], start_new_session=True)  # pylint: disable=consider-using-with


def running_until(rig: PubRig, feature_id: str, predicate, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate(current(rig, feature_id)):
            return
        time.sleep(0.05)
    raise AssertionError("timed out")


def fake_started(entry: dict[str, Any]) -> bool:
    return (Path(entry["running"]["run_dir"]) / "fake-pid").exists()


# -- the happy path: started, watched, collected, published once ----------------------------------------------------

def test_a_verified_draft_pr_gets_one_reviewer_process_and_one_comment(wrig: PubRig, launcher: Launcher) -> None:  # pylint: disable=too-many-locals
    _number, _task_id, feature_id, pr = draft_open(wrig)
    head = wrig.candidate(feature_id).head_sha
    launcher.modes = ["slow:1.5"]
    report = step(wrig, launcher).poll()
    assert report.started == [feature_id] and len(launcher.calls) == 1, report.lines()
    entry = current(wrig, feature_id)
    running = entry["running"]
    assert (entry["status"], entry["attempts"], entry["outcome"]) == ("running", 1, None)  # counted BEFORE it starts
    assert running["tag"] == launcher.tags[0] and running["pid"] == launcher.handles[0].pid
    assert running["process"] == ProcessIdentity.of_pid(launcher.handles[0].pid).to_dict()  # type: ignore[union-attr]
    assert running["deadline_epoch"] >= time.time() + 600 + REVIEW_SLACK_SECONDS - 5
    assert launcher.calls[0]["tail"][3] == running["run_dir"] and launcher.calls[0]["log"] == \
        f"review-{entry['key']}.{Path(running['run_dir']).name}.log"
    request = Path(running["run_dir"]) / "request.json"
    assert stat.S_IMODE(request.stat().st_mode) == 0o600
    data = json.loads(request.read_text(encoding="utf-8"))
    assert data["tag"] == running["tag"] and f"Reviewed head: {head}" in data["prompt"] and data["files"]
    env = launcher.calls[0]["env"]  # the native allowlist and the tag: no GitHub token, no API key
    assert env[REVIEW_TAG_ENV] == running["tag"] and not {"GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY"} & set(env)
    assert report.service_state() is not None and "is running" in report.service_state()[1]  # type: ignore[index]
    # while it runs: nothing else happens — no second process, not one GitHub request
    with github_requests(wrig) as calls:
        again = step(wrig, launcher).poll()
    assert not again.started and len(launcher.calls) == 1 and not calls and not again.lines()
    launcher.wait()
    done = step(wrig, launcher).poll()
    entry = current(wrig, feature_id)
    assert (entry["status"], entry["outcome"], entry["running"], entry["attempts"]) == ("complete", "PASS", None, 1)
    (comment,) = pr_comments(wrig, pr)
    assert "Independent AI review: PASS" in comment and head in comment
    assert review_evidence(wrig.store, feature_id, pr, head) == entry
    assert any("PASS" in line for line in done.lines()) and done.service_state() is None
    # complete for the verified head: not looked at again — no GitHub request, no process, no comment
    with github_requests(wrig) as calls:
        assert not step(wrig, launcher).poll().lines()
    assert not calls and len(launcher.calls) == 1 and len(pr_comments(wrig, pr)) == 1
    assert kinds(wrig) == ["review-started", "review-process-starting", "review-process-started", "review-attempt",
                           "review-completed", "review-comment-posted"]
    assert wrig.feature(feature_id).state is FeatureState.REVIEW and wrig.tracker.get_pull_request(pr).draft


@pytest.mark.parametrize(("mode", "outcome", "state"), [
    ("notes", "PASS_WITH_NOTES", None),
    ("changes", "REQUEST_CHANGES", ("WAITING", "requests changes (1 blocking finding(s)); it waits for a person")),
])
def test_findings_decide_the_outcome_and_the_wait(wrig: PubRig, launcher: Launcher, mode: str, outcome: str,
                                                  state: tuple[str, str] | None) -> None:
    _number, _task_id, feature_id, pr = draft_open(wrig)
    launcher.modes = [mode]
    step(wrig, launcher).poll()
    launcher.wait()
    report = step(wrig, launcher).poll()
    assert current(wrig, feature_id)["outcome"] == outcome and f"Independent AI review: {outcome}" in \
        pr_comments(wrig, pr)[0]
    if state is None:
        assert report.service_state() is None
    else:
        kind, text = report.service_state()  # type: ignore[misc]
        assert kind == state[0] and state[1] in text and f"PR #{pr}" in text


# -- the service: after publication, without blocking --------------------------------------------------------------

def service(rig: PubRig, launcher: Launcher, clock: Clock, *, until, config=None, out: list[str] | None = None,
            max_cycles: int = 3000) -> WorkerService:
    config = config or rig.config
    coord = rig.coord if config is rig.config else rig.make(config=config)
    return WorkerService(config, rig.repo, rig.state, coord=coord, spawn=Spawn(launcher), clock=clock,
                         idle_sleep=lambda s: (clock.sleep(s), time.sleep(0.005)), poll_sleep=lambda _s: None,
                         echo=(out if out is not None else []).append, max_cycles=max_cycles, stop_when=until)


def test_the_service_reviews_a_new_draft_pr_and_keeps_its_heartbeat(wrig: PubRig, launcher: Launcher,  # pylint: disable=too-many-locals
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review_worker, "reviewer_available", lambda _config: "")
    _number, _task_id, feature_id = verified(wrig)  # not yet published: the service publishes it
    launcher.modes = ["slow:2"]
    clock: Clock = Clock()
    seen: list[tuple[Any, ...]] = []
    out: list[str] = []

    def until(svc: WorkerService) -> bool:
        record = load_review(wrig.store, feature_id)
        entry = record.entry(record.current) if record else None
        assert svc.record is not None
        seen.append(((entry or {}).get("status"), svc.record.heartbeat_at, svc.record.state, svc.record.reason))
        return bool(entry and (entry.get("comment") or {}).get("status") == "posted")
    svc = service(wrig, launcher, clock, until=until, out=out)
    assert svc.run() == 0
    pr = wrig.candidate(feature_id).number
    assert pr and len(launcher.calls) == 1 and current(wrig, feature_id)["outcome"] == "PASS"
    (comment,) = pr_comments(wrig, pr)
    assert "Independent AI review: PASS" in comment
    while_running = [s for s in seen if s[0] == "running"]
    assert len(while_running) >= 5, len(while_running)  # the loop went on while the model "ran"
    assert len({s[1] for s in while_running}) >= 3  # … and so did the heartbeat
    assert all(s[2] == "WAITING" and "is running" in s[3] and f"Draft PR #{pr} waits for a person" in s[3]
               for s in while_running), while_running[0]
    assert any("review: " in line and "runs in reviewer process" in line for line in out)
    # the order of the GitHub steps: publication, then the review, then intake
    assert [name for name, _cfg, _make in github_steps(wrig.config, wrig.coord, {}, None)] == \
        ["publish", "review", "intake"]


def test_a_graceful_service_stop_cancels_the_reviewer_and_gives_the_attempt_back(  # pylint: disable=too-many-locals
        wrig: PubRig, launcher: Launcher, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(review_worker, "reviewer_available", lambda _config: "")
    _number, _task_id, feature_id, pr = draft_open(wrig)
    launcher.modes = ["hang", "pass"]
    clock: Clock = Clock()
    out: list[str] = []

    def until(_svc: WorkerService) -> bool:
        record = load_review(wrig.store, feature_id)
        entry = record.entry(record.current) if record else None
        return bool(entry and entry["status"] == "running" and fake_started(entry))
    assert service(wrig, launcher, clock, until=until, out=out).run() == 0
    # the service waited for its reviewer process, which recorded its cancellation before the service exited
    assert launcher.handles[0].poll() is not None and not tagged_processes(REVIEW_TAG_ENV, launcher.tags[0])
    entry = current(wrig, feature_id)
    result = load_result(entry["key"], entry["running"])
    assert result is not None and result.status == "cancelled"
    assert any("stopped the reviewer process" in line and "service stop" in line for line in out)
    # the next start collects it (the attempt is given back, R1's rule) and reviews again
    report = step(wrig, launcher).poll()
    entry = current(wrig, feature_id)
    assert entry["attempts"] == 1 and len(launcher.calls) == 2 and report.started == [feature_id]
    assert [a["status"] for a in entry["attempt_log"]] == ["cancelled"]
    launcher.wait()
    step(wrig, launcher).poll()
    assert current(wrig, feature_id)["outcome"] == "PASS" and len(pr_comments(wrig, pr)) == 1


def test_pause_cancels_the_reviewer_and_resume_starts_it_again(wrig: PubRig, launcher: Launcher) -> None:
    _number, _task_id, feature_id, pr = draft_open(wrig)
    launcher.modes = ["hang", "pass"]
    step(wrig, launcher).poll()
    running_until(wrig, feature_id, fake_started)
    write_control(wrig.store, pause=True, pause_reason="deploy")  # the reviewer reads the control file itself
    launcher.handles[0].wait(timeout=30)
    entry = current(wrig, feature_id)
    assert load_result(entry["key"], entry["running"]).status == "cancelled"  # type: ignore[union-attr]
    paused = clock_service_cycle(wrig, launcher)  # a paused service runs no GitHub step: nothing collected
    assert paused == "PAUSED" and current(wrig, feature_id)["status"] == "running" and len(launcher.calls) == 1
    write_control(wrig.store, pause=False, pause_reason=None)
    step(wrig, launcher).poll()  # collected (given back) and started again in the same step
    entry = current(wrig, feature_id)
    assert (entry["attempts"], entry["status"], len(launcher.calls)) == (1, "running", 2)
    assert entry["attempt_log"][0]["status"] == "cancelled"
    launcher.wait()
    step(wrig, launcher).poll()
    assert current(wrig, feature_id)["outcome"] == "PASS" and len(pr_comments(wrig, pr)) == 1


def clock_service_cycle(rig: PubRig, launcher: Launcher) -> str:
    states: list[str] = []
    service(rig, launcher, Clock(), until=lambda s: bool(states.append(s.record.state)), max_cycles=1).run()  # type: ignore[func-returns-value]
    return states[-1]


def test_with_the_review_disabled_nothing_starts_and_nothing_is_written(repo: Path, state_dir: Path,
                                                                       github_origin: Path,
                                                                       launcher: Launcher) -> None:
    rig = PubRig(repo, state_dir, github_origin,
                 config=dataclasses.replace(worker_config(), github=dataclasses.replace(
                     worker_config().github, review=dataclasses.replace(worker_config().github.review,
                                                                        enabled=False))))
    _number, _task_id, feature_id = verified(rig)
    out: list[str] = []
    states: list[tuple[str, str]] = []
    svc = service(rig, launcher, Clock(), out=out, max_cycles=12,
                  until=lambda s: bool(states.append((s.record.state, s.record.reason))))  # type: ignore[func-returns-value]
    assert svc.run() == 0
    pr = rig.candidate(feature_id).number
    assert pr and not launcher.calls and rig.store.list_records("review") == []
    assert not (rig.store.root / "runs" / "review").exists() and pr_comments(rig, pr) == []
    assert not [e for e in rig.store.events() if e["kind"].startswith("review-")]
    state, reason = states[-1]  # exactly publication's wait, as before R2
    assert state == "WAITING" and reason.startswith(f"{feature_id}: Draft PR #{pr} waits for a person") and \
        "AI review" not in reason and ";" not in reason
    assert not any(": review: " in line for line in out)
    assert ReviewStep(rig.coord, start=launcher).poll().lines() == [] and not launcher.calls  # the step itself


# -- restart, identity, orphans, deadlines ---------------------------------------------------------------------------

def test_a_restarted_service_waits_for_the_running_reviewer_and_collects_it(wrig: PubRig,
                                                                           launcher: Launcher) -> None:
    _number, _task_id, feature_id, pr = draft_open(wrig)
    launcher.modes = ["slow:1.5"]
    step(wrig, launcher).poll()
    review_worker._CHILDREN.clear()  # a new service instance: not the reviewer's parent
    later = step(wrig, launcher)
    assert not later.poll().started and len(launcher.calls) == 1 and current(wrig, feature_id)["status"] == "running"
    launcher.wait()
    later.poll()
    assert current(wrig, feature_id)["outcome"] == "PASS" and len(pr_comments(wrig, pr)) == 1


def test_a_crash_after_the_reviewer_started_finds_it_by_its_tag(wrig: PubRig, launcher: Launcher) -> None:
    _number, _task_id, feature_id, pr = draft_open(wrig)
    launcher.modes = ["slow:1.5"]
    with pytest.raises(SimulatedCrash):
        step(wrig, launcher, faults=(f"crash-after-reviewer-started:{feature_id}",)).poll()
    entry = current(wrig, feature_id)
    assert entry["running"]["process"] is None and entry["running"]["tag"] == launcher.tags[0]  # not recorded yet
    assert attempt_processes(entry["running"])[0] == "running"
    assert not step(wrig, launcher).poll().started and len(launcher.calls) == 1  # no second reviewer
    launcher.wait()
    step(wrig, launcher).poll()
    assert current(wrig, feature_id)["outcome"] == "PASS" and len(pr_comments(wrig, pr)) == 1


@pytest.mark.parametrize("max_attempts", [1, 2])
def test_a_crash_before_the_reviewer_started_counts_the_attempt(repo: Path, state_dir: Path, github_origin: Path,
                                                                launcher: Launcher, max_attempts: int) -> None:
    rig = PubRig(repo, state_dir, github_origin, config=worker_config(max_attempts=max_attempts))
    _number, _task_id, feature_id, pr = draft_open(rig)
    with pytest.raises(SimulatedCrash):
        step(rig, launcher, faults=(f"crash-after-attempt-counted:{feature_id}",)).poll()
    assert current(rig, feature_id)["attempts"] == 1 and not launcher.calls
    step(rig, launcher).poll()
    entry = current(rig, feature_id)
    assert entry["attempt_log"][0]["status"] == "interrupted"  # write-ahead: it stays counted (R1)
    if max_attempts == 1:
        assert entry["outcome"] == "ERROR" and "ended without a result" in entry["reason"] and not launcher.calls
        assert "Independent AI review: ERROR" in pr_comments(rig, pr)[0]
    else:
        assert entry["status"] == "running" and entry["attempts"] == 2 and len(launcher.calls) == 1


def test_only_the_tag_makes_a_process_ours(wrig: PubRig, launcher: Launcher) -> None:
    _number, _task_id, feature_id, _pr = draft_open(wrig)
    launcher.modes = ["hang"]
    step(wrig, launcher).poll()
    running_until(wrig, feature_id, fake_started)
    other = untagged_sleeper()
    try:
        tampered = {**current(wrig, feature_id)["running"],
                    "process": ProcessIdentity.of_pid(other.pid).to_dict()}  # type: ignore[union-attr]
        assert attempt_processes(tampered)[0] == "orphaned"  # the recorded process is not tagged: not ours
        assert stop_attempt(tampered, 1.0) == []
        assert alive(other.pid) and launcher.handles[0].wait(timeout=10) is not None  # only the tagged one stopped
        assert attempt_processes(tampered)[0] == "gone"  # an untagged live identity never blocks
        stale = {**tampered, "process": {**tampered["process"], "start_ticks": tampered["process"]["start_ticks"] + 1}}
        assert attempt_processes(stale) == ("gone", [])  # a reused PID: the start time differs
        assert attempt_processes({**tampered, "tag": ""}) == ("gone", []) and stop_attempt({"tag": ""}, 1.0) == []
    finally:
        other.kill()
        other.wait()


def test_the_service_stop_signals_only_the_tagged_reviewer(wrig: PubRig, launcher: Launcher) -> None:
    _number, _task_id, feature_id, _pr = draft_open(wrig)
    launcher.modes = ["hang"]
    step(wrig, launcher).poll()
    running_until(wrig, feature_id, fake_started)
    other = untagged_sleeper()
    try:
        with wrig.store.transaction():  # a tampered (or reused) record names an unrelated live process
            record = load_review(wrig.store, feature_id)
            assert record is not None
            record.entries[0]["running"]["process"] = ProcessIdentity.of_pid(other.pid).to_dict()  # type: ignore[union-attr]
            wrig.store.save_record("review", record)
        logs: list[str] = []
        stop_reviews(wrig.coord, logs.append)
        assert alive(other.pid) and launcher.handles[0].wait(timeout=30) is not None
        assert len(logs) == 1 and "stopped the reviewer process" in logs[0]
        stop_reviews(wrig.coord, logs.append)  # nothing left to stop: nothing happens, nothing raises
        assert len(logs) == 1
    finally:
        other.kill()
        other.wait()


def test_the_worker_state_names_are_autonomys() -> None:
    from feature_coordinator import autonomy  # pylint: disable=import-outside-toplevel
    assert (review_worker.WAITING, review_worker.NEEDS_ATTENTION) == (autonomy.WAITING, autonomy.NEEDS_ATTENTION)
    assert review_worker.iso(0) == autonomy.iso(0)


def test_an_orphaned_cli_is_stopped_and_the_attempt_counts(wrig: PubRig, launcher: Launcher) -> None:
    _number, _task_id, feature_id, pr = draft_open(wrig)
    launcher.modes = ["orphan"]
    other = untagged_sleeper()
    try:
        step(wrig, launcher).poll()
        launcher.wait()
        orphans = tagged_processes(REVIEW_TAG_ENV, launcher.tags[0])
        assert orphans and attempt_processes(current(wrig, feature_id)["running"])[0] == "orphaned"
        report = step(wrig, launcher).poll()
        assert not tagged_processes(REVIEW_TAG_ENV, launcher.tags[0]) and alive(other.pid)
        entry = current(wrig, feature_id)
        assert entry["outcome"] == "ERROR" and entry["attempt_log"][0]["status"] == "interrupted"
        assert any("left processes of its CLI" in line for line in report.lines()) and len(launcher.calls) == 1
        assert "Independent AI review: ERROR" in pr_comments(wrig, pr)[0]
    finally:
        other.kill()
        other.wait()


@pytest.mark.parametrize(("mode", "status"), [("hang", "timeout"), ("hang-hard", "interrupted")])
def test_a_reviewer_past_its_deadline_is_stopped_and_its_attempt_used(wrig: PubRig, launcher: Launcher, mode: str,
                                                                     status: str) -> None:
    _number, _task_id, feature_id, pr = draft_open(wrig)
    launcher.modes = [mode]
    step(wrig, launcher).poll()
    running_until(wrig, feature_id, fake_started)
    assert not step(wrig, launcher).poll().lines()  # within its deadline: left alone
    late = step(wrig, launcher, wall=lambda: time.time() + 10_000).poll()
    assert launcher.handles[0].poll() is not None and not tagged_processes(REVIEW_TAG_ENV, launcher.tags[0])
    entry = current(wrig, feature_id)
    log = entry["attempt_log"][0]
    assert log["status"] == status and ("stopped at its deadline" in log["error"] if status == "timeout" else
                                        log["watchdog"]["kind"] == "deadline")
    assert entry["outcome"] == "ERROR" and entry["attempts"] == 1 and len(launcher.calls) == 1  # never given back
    assert kinds(wrig).index("review-process-stopping") < kinds(wrig).index("review-process-stopped")  # write-ahead
    assert any("passed its deadline" in line for line in late.lines())
    assert "Independent AI review: ERROR" in pr_comments(wrig, pr)[0]


# -- results: crash, invalid, refused, unavailable ------------------------------------------------------------------

@pytest.mark.parametrize(("modes", "max_attempts", "outcome", "reason", "processes"), [
    (["crash"], 1, "ERROR", "ended without a result", 1),
    (["crash", "pass"], 2, "PASS", "", 2),
    (["invalid", "invalid"], 2, "ERROR", "no valid review in 2 attempt(s)", 2),
    (["timeout"], 1, "ERROR", "no valid review in 1 attempt(s)", 1),
    (["refused", "pass"], 2, "ERROR", "the reviewer run was refused", 1),
])
def test_attempts_follow_r1s_rules(repo: Path, state_dir: Path, github_origin: Path, launcher: Launcher,  # pylint: disable=too-many-arguments
                                   modes: list[str], max_attempts: int, outcome: str, reason: str,
                                   processes: int) -> None:
    rig = PubRig(repo, state_dir, github_origin, config=worker_config(max_attempts=max_attempts))
    _number, _task_id, feature_id, pr = draft_open(rig)
    launcher.modes = list(modes)
    for _ in range(6):
        step(rig, launcher).poll()
        launcher.wait()
        if current(rig, feature_id)["status"] == "complete":
            break
    entry = current(rig, feature_id)
    assert entry["outcome"] == outcome and reason in entry["reason"] and len(launcher.calls) == processes
    assert entry["attempts"] == processes and len(pr_comments(rig, pr)) == 1


def test_an_unavailable_reviewer_starts_no_process_then_errors(wrig: PubRig, launcher: Launcher) -> None:
    _number, _task_id, feature_id, pr = draft_open(wrig)
    first = step(wrig, launcher, preflight="Claude Code is not logged in", clock=lambda: 1000.0).poll()
    entry = current(wrig, feature_id)
    assert (entry["status"], entry["attempts"], entry["unavailable_since"]["epoch"]) == ("pending", 0, 1000.0)
    assert len(first.deferred) == 1 and "not logged in" in first.deferred[0] and not launcher.calls
    assert first.service_state()[0] == "WAITING" and "waits for the reviewer" in first.service_state()[1]  # type: ignore[index]
    with github_requests(wrig) as calls:
        later = step(wrig, launcher, preflight="Claude Code is not logged in", clock=lambda: 2000.0).poll()
    assert not later.deferred and not later.lines() and not calls  # no repeated message, no GitHub request
    assert kinds(wrig).count("review-deferred") == 1
    final = step(wrig, launcher, preflight="Claude Code is not logged in", clock=lambda: 1000.0 + 3600).poll()
    entry = current(wrig, feature_id)
    assert entry["outcome"] == "ERROR" and "unavailable since" in entry["reason"] and not launcher.calls
    assert entry["attempts"] == 0 and "Independent AI review: ERROR" in pr_comments(wrig, pr)[0]
    assert final.service_state()[0] == "NEEDS_ATTENTION"  # type: ignore[index]


def test_a_reviewer_reported_unavailable_gives_its_attempt_back(wrig: PubRig, launcher: Launcher) -> None:
    _number, _task_id, feature_id, _pr = draft_open(wrig)
    launcher.modes = ["unavailable"]
    step(wrig, launcher).poll()
    launcher.wait()
    step(wrig, launcher, preflight="still not logged in").poll()
    entry = current(wrig, feature_id)
    assert (entry["attempts"], entry["status"]) == (0, "pending") and entry["unavailable_since"] is not None
    assert [a["status"] for a in entry["attempt_log"]] == ["unavailable"] and len(launcher.calls) == 1


def test_a_result_of_another_attempt_is_never_accepted(wrig: PubRig, launcher: Launcher) -> None:
    _number, _task_id, feature_id, _pr = draft_open(wrig)
    step(wrig, launcher).poll()
    launcher.wait()
    entry = current(wrig, feature_id)
    path = result_path(entry["running"]["run_dir"])
    data = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**data, "tag": "review-0000"}), encoding="utf-8")  # not this attempt's
    step(wrig, launcher).poll()
    entry = current(wrig, feature_id)
    assert entry["outcome"] == "ERROR" and entry["attempt_log"][0]["status"] == "interrupted"


# -- GitHub, the PR and the approval change ------------------------------------------------------------------------

def test_github_unavailable_defers_a_start_and_holds_a_finished_answer(wrig: PubRig, launcher: Launcher,
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    _number, _task_id, feature_id, pr = draft_open(wrig)

    def down(_number: int) -> Any:
        raise GitHubUnavailableError("GitHub returned 502")
    with monkeypatch.context() as patch:
        patch.setattr(wrig.coord.sync.client, "get_pull_request", down)
        report = step(wrig, launcher).poll()
        assert "GitHubUnavailableError" in report.deferred[0] and not launcher.calls
        assert load_review(wrig.store, feature_id) is None  # nothing counted, nothing started
    launcher.modes = ["slow:1"]
    step(wrig, launcher).poll()
    launcher.wait()
    with monkeypatch.context() as patch:
        patch.setattr(wrig.coord.sync.client, "get_pull_request", down)
        step(wrig, launcher).poll()  # the answer arrives; its binding cannot be re-read: HELD, nothing current
    entry = current(wrig, feature_id)
    assert entry["status"] == "pending" and entry["held"]["outcome"] == "PASS" and not pr_comments(wrig, pr)
    assert review_view(wrig.store)[0]["category"] == "held"
    step(wrig, launcher).poll()  # GitHub is back: confirmed by this step's own binding check, no new process
    entry = current(wrig, feature_id)
    assert entry["outcome"] == "PASS" and len(launcher.calls) == 1 and len(pr_comments(wrig, pr)) == 1


CHANGES = {  # what happens on GitHub while the reviewer runs → why its answer is history only
    "pr-head": (lambda rig, _n, pr: rig.tracker.update_pull_request(pr, head_sha="f" * 40), "is not the verified"),
    "pr-closed": (lambda rig, _n, pr: rig.tracker.update_pull_request(pr, state="closed"), "closed"),
    "issue-edited": (lambda rig, number, _p: outside(rig, number, body=BODY + "\nAlso export images."),
                     "the approval no longer holds"),
}


@pytest.mark.parametrize("change", sorted(CHANGES))
def test_a_change_during_the_review_is_history_only_and_nothing_new_starts(wrig: PubRig, launcher: Launcher,
                                                                           change: str) -> None:
    number, _task_id, feature_id, pr = draft_open(wrig)
    act, why = CHANGES[change]
    launcher.modes = ["slow:1.5"]
    step(wrig, launcher).poll()
    act(wrig, number, pr)
    wrig.publish()  # publication sees it first (a moved head or a closed PR stops publication)
    assert current(wrig, feature_id)["status"] == "running" and not step(wrig, launcher).poll().started
    launcher.wait()
    step(wrig, launcher).poll()
    entry = current(wrig, feature_id)
    assert (entry["status"], entry["outcome"]) == ("invalidated", "PASS") and why in entry["invalidated"]["reason"]
    assert "stale when the review finished" in entry["invalidated"]["reason"] and not pr_comments(wrig, pr)
    assert review_evidence(wrig.store, feature_id, pr, entry["binding"]["head_sha"]) is None
    for _ in range(2):  # an unverified head, a closed PR or a withdrawn approval: nothing new starts
        step(wrig, launcher).poll()
    assert len(launcher.calls) == 1 and len(load_review(wrig.store, feature_id).entries) == 1  # type: ignore[union-attr]


def test_a_merged_pr_stops_its_reviewer_and_discards_the_review(wrig: PubRig, launcher: Launcher) -> None:
    _number, _task_id, feature_id, pr = draft_open(wrig)
    launcher.modes = ["hang"]
    step(wrig, launcher).poll()
    running_until(wrig, feature_id, fake_started)
    wrig.merge_on_github(pr)
    wrig.publish()  # completed: the feature is done
    assert wrig.feature(feature_id).state is FeatureState.DONE
    report = step(wrig, launcher).poll()
    assert launcher.handles[0].poll() is not None and not tagged_processes(REVIEW_TAG_ENV, launcher.tags[0])
    entry = current(wrig, feature_id)
    assert entry["status"] == "invalidated" and "no longer in review" in entry["invalidated"]["reason"]
    assert entry["attempt_log"][-1]["status"] == "discarded" and entry["outcome"] is None and not pr_comments(wrig, pr)
    assert any("no longer in review" in line for line in report.lines())
    assert not step(wrig, launcher).poll().lines() and len(launcher.calls) == 1


# -- duplicates, locks, claims, idempotent comments, R1's commands ------------------------------------------------

def test_a_manual_run_waits_for_the_worker_reviewer_then_collects_it(wrig: PubRig, launcher: Launcher,
                                                                     capsys: pytest.CaptureFixture[str]) -> None:
    _number, _task_id, feature_id, pr = draft_open(wrig)
    launcher.modes = ["slow:1.5"]
    step(wrig, launcher).poll()
    fake = FakeReviewer(ok("pass"))
    report = review(wrig, feature_id, fake)  # R1's `github review run --apply`
    assert report.status == "running" and not report.ok and not fake.requests  # no second reviewer
    assert "runs in a reviewer process the Worker service started" in report.deferred[0]
    assert cli_main(["--state-dir", str(wrig.store.root), "github", "review", "show", feature_id]) == 0
    assert "in reviewer process" in capsys.readouterr().out
    launcher.wait()
    report = review(wrig, feature_id, fake)  # the process is gone: the manual run collects it, no reviewer run
    assert report.outcome == "PASS" and report.reviewer_runs == 0 and not fake.requests
    assert len(pr_comments(wrig, pr)) == 1 and not step(wrig, launcher).poll().lines()


def test_the_step_defers_while_a_manual_run_holds_the_lock_or_an_agent_works(wrig: PubRig,
                                                                             launcher: Launcher) -> None:
    _number, task_id, feature_id, _pr = draft_open(wrig)
    lock = wrig.store.locks.review().acquire()
    try:
        report = step(wrig, launcher).poll()
    finally:
        lock.release()
    assert "github-review.lock is held" in report.deferred[0] and not launcher.calls
    with wrig.store.transaction():
        wrig.store.save_claim(task_id, {"task_id": task_id, "role": "cli"})
    assert not step(wrig, launcher).poll().started and not launcher.calls  # an agent works: no reviewer starts
    with wrig.store.transaction():
        wrig.store.delete_claim(task_id)
    assert step(wrig, launcher).poll().started == [feature_id]


def test_a_crash_after_the_comment_never_duplicates_it(wrig: PubRig, launcher: Launcher) -> None:
    _number, _task_id, feature_id, pr = draft_open(wrig)
    step(wrig, launcher).poll()
    launcher.wait()
    with pytest.raises(SimulatedCrash):
        step(wrig, launcher, faults=(f"crash-after-review-comment:{feature_id}",)).poll()
    assert len(pr_comments(wrig, pr)) == 1 and current(wrig, feature_id)["comment"]["status"] == "pending"
    step(wrig, launcher).poll()
    assert len(pr_comments(wrig, pr)) == 1 and current(wrig, feature_id)["comment"]["status"] == "posted"
    assert len(launcher.calls) == 1


# -- reporting ----------------------------------------------------------------------------------------------------

def test_status_health_waiting_and_attention_report_the_reviews(wrig: PubRig, launcher: Launcher,  # pylint: disable=too-many-locals
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    _number, _task_id, feature_id, pr = draft_open(wrig)
    service_record = new_service_record()  # a live, responsive service (this process stands in for it)
    service_record.identity = ProcessIdentity.current().to_dict()
    service_record.state, service_record.reason = "WAITING", "Draft PR waits for a person"
    service_record.heartbeat_at = iso(time.time())
    with wrig.store.transaction():
        wrig.store.save_record("service", service_record)
    before = health_report(wrig.store, wrig.config, time.time())
    assert not before["reviews"] and before["healthy"] is True
    launcher.modes = ["slow:1"]
    step(wrig, launcher).poll()
    assert health_report(wrig.store, wrig.config, time.time())["healthy"] is True  # a running review waits
    rows = review_view(wrig.store)
    assert len(rows) == 1
    row = rows[0]
    assert (row["category"], row["pr"], row["process"]["state"], row["process"]["pid"]) == \
        ("running", pr, "running", launcher.handles[0].pid)
    state = ["--state-dir", str(wrig.store.root)]
    assert cli_main([*state, "worker", "waiting"]) == 0
    assert f"review {feature_id}: running" in capsys.readouterr().out
    launcher.wait()
    with wrig.store.transaction():  # the reviewer answered ERROR (e.g. its diff was too large)
        record = load_review(wrig.store, feature_id)
        record.entries[0].update(status="complete", outcome="ERROR", reason="the diff is too large", running=None)  # type: ignore[union-attr]
        wrig.store.save_record("review", record)  # type: ignore[arg-type]
    report = health_report(wrig.store, wrig.config, time.time())
    assert report["reviews"][0]["category"] == "error" and report["healthy"] is False
    assert cli_main([*state, "worker", "attention"]) == 1
    out = capsys.readouterr().out
    assert f"review {feature_id}: error ERROR" in out and f"github review retry {feature_id}" in out
    assert cli_main([*state, "worker", "status"]) == 0
    assert "the diff is too large" in capsys.readouterr().out
    kind, reason = review_state(review_view(wrig.store))  # type: ignore[misc]
    assert kind == "NEEDS_ATTENTION" and "is ERROR: the diff is too large" in reason
    assert idle_state({"publish": ("WAITING", "Draft PR waits"), "review": (kind, reason)}) == (kind, reason)
    assert idle_state({"publish": ("WAITING", "a"), "review": ("WAITING", "b")}) == ("WAITING", "a; b")
    assert idle_state({"publish": None, "review": None}) is None
    wrig.merge_on_github(pr)
    wrig.publish()  # a done feature's review needs nobody
    assert review_state(review_view(wrig.store)) is None
    assert health_report(wrig.store, wrig.config, time.time())["healthy"] is True


# -- the reviewer process itself -------------------------------------------------------------------------------------

def request_dir(state: Path, *, tag: str = "review-abc") -> Path:
    run_dir = state / "runs" / "review" / "pr2-aaaaaaaaaaaa-g1" / "attempt-1"
    write_request(run_dir, key="pr2-aaaaaaaaaaaa-g1", attempt=1, tag=tag, feature="issue-1",
                  context=ReviewContext(prompt=f"Reviewed head: {'a' * 40}", schema=review_schema(30),
                                        files={"cli/src/commands.py": FileFacts("M", 3)}, notes=[]),
                  timeout_seconds=60)
    return run_dir


class Scripted:  # pylint: disable=too-few-public-methods
    """A reviewer with one scripted result (in-process)."""

    def __init__(self, result: ReviewResult) -> None:
        self.result, self.requests = result, []

    def review(self, request: ReviewRequest) -> ReviewResult:
        self.requests.append(request)
        return self.result


def enabled(on: bool = True):
    github = default_config().github
    return dataclasses.replace(default_config(), github=dataclasses.replace(
        github, intake=dataclasses.replace(github.intake, enabled=True),
        publish=dataclasses.replace(github.publish, enabled=True),
        review=dataclasses.replace(github.review, enabled=on)))


def test_the_reviewer_process_refuses_unless_the_worker_started_it(state_dir: Path, tmp_path: Path) -> None:
    run_dir = request_dir(state_dir)
    fake = Scripted(ReviewResult("ok", output={}))
    for where, env, message in ((tmp_path, {REVIEW_TAG_ENV: "review-abc"}, "not a review attempt directory"),
                                (run_dir, {}, "does not carry the attempt's tag"),
                                (run_dir, {REVIEW_TAG_ENV: "review-other"}, "does not carry the attempt's tag")):
        with pytest.raises(CoordinatorError, match=message):
            run_exec(enabled(), state_dir, where, reviewer=fake, environ=env, cancelled=lambda: False,
                     echo=lambda _l: None)
    assert not fake.requests and not result_path(run_dir).exists()
    assert run_exec(enabled(), state_dir, run_dir, reviewer=fake, environ={REVIEW_TAG_ENV: "review-abc"},
                    cancelled=lambda: False, echo=lambda _l: None) == 0
    assert len(fake.requests) == 1 and fake.requests[0].run_dir == run_dir.resolve()
    with pytest.raises(CoordinatorError, match="already has a result"):  # an attempt runs once
        run_exec(enabled(), state_dir, run_dir, reviewer=fake, environ={REVIEW_TAG_ENV: "review-abc"},
                 cancelled=lambda: False, echo=lambda _l: None)
    assert len(fake.requests) == 1


@pytest.mark.parametrize(("status", "stopped", "recorded"), [
    ("ok", True, "ok"), ("refused", True, "refused"), ("failed", True, "cancelled"), ("timeout", True, "cancelled"),
    ("failed", False, "failed"),
])
def test_a_cancelled_run_is_recorded_as_cancelled(state_dir: Path, status: str, stopped: bool, recorded: str) -> None:
    run_dir = request_dir(state_dir)
    run_exec(enabled(), state_dir, run_dir, reviewer=Scripted(ReviewResult(status, output={"x": 1})),
             environ={REVIEW_TAG_ENV: "review-abc"}, cancelled=lambda: stopped, echo=lambda _l: None)
    data = json.loads(result_path(run_dir).read_text(encoding="utf-8"))
    assert data["status"] == recorded and data["tag"] == "review-abc" and data["attempt"] == 1


def test_a_disabled_review_runs_no_reviewer(state_dir: Path) -> None:
    run_dir = request_dir(state_dir)
    fake = Scripted(ReviewResult("ok", output={}))
    run_exec(enabled(False), state_dir, run_dir, reviewer=fake, environ={REVIEW_TAG_ENV: "review-abc"},
             cancelled=lambda: False, echo=lambda _l: None)
    assert not fake.requests and json.loads(result_path(run_dir).read_text(encoding="utf-8"))["status"] == "cancelled"


def test_the_tag_reaches_the_cli_and_no_credential_does() -> None:
    seen: dict[str, Any] = {}
    tagged_run(lambda argv, **kwargs: seen.update(argv=argv, **kwargs), "review-xyz")(["claude"], env={"A": "1"},
                                                                                      cwd="/")
    assert seen["env"] == {"A": "1", REVIEW_TAG_ENV: "review-xyz"} and seen["cwd"] == "/"
    env = process_environment("review-xyz", {"PATH": "/usr/bin", "HOME": "/home/op", "GH_TOKEN": "ghp_x",
                                             "GITHUB_TOKEN": "x", "ANTHROPIC_API_KEY": "sk-ant-x", "AWS_SECRET": "x"})
    assert env[REVIEW_TAG_ENV] == "review-xyz" and env["HOME"] == "/home/op"
    assert not {"GH_TOKEN", "GITHUB_TOKEN", "ANTHROPIC_API_KEY", "AWS_SECRET"} & set(env)


FAKE_CLAUDE = r'''#!{python}
import json, os, re, sys
args = sys.argv[1:]
if args == ["--version"]:
    print("2.1.318 (Claude Code)")
elif args == ["--help"]:
    print(" ".join({flags!r}))
elif args[:2] == ["auth", "status"]:
    print(json.dumps({{"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
                      "subscriptionType": "pro"}}))
else:
    prompt = sys.stdin.read()
    with open({record!r}, "w") as handle:
        json.dump({{"argv": args, "env": dict(os.environ), "cwd": os.getcwd()}}, handle)
    head = re.search(r"Reviewed head: ([0-9a-f]{{40}})", prompt).group(1)
    answer = {{"verdict": "pass", "summary": "fine", "head_sha": head, "findings": [], "limitations": []}}
    print(json.dumps({{"type": "system", "subtype": "init", "model": "claude-opus-5-5", "apiKeySource": "none",
                      "tools": [], "mcp_servers": [], "permissionMode": "dontAsk"}}))
    print(json.dumps({{"type": "result", "subtype": "success", "is_error": False, "result": "done",
                      "structured_output": answer}}))
'''


def test_the_exec_command_runs_r1s_reviewer_with_a_fake_cli(state_dir: Path, tmp_path: Path) -> None:
    from feature_coordinator.launchers import REQUIRED_FLAGS  # pylint: disable=import-outside-toplevel
    from feature_coordinator.providers import ProviderKind  # pylint: disable=import-outside-toplevel
    record = tmp_path / "fake-claude-run.json"
    cli = tmp_path / "bin" / "claude-fake"
    cli.parent.mkdir()
    cli.write_text(FAKE_CLAUDE.format(python=sys.executable, flags=list(REQUIRED_FLAGS[ProviderKind.CLAUDE_CODE]),
                                      record=str(record)), encoding="utf-8")
    cli.chmod(0o755)
    data = yaml.safe_load(EXAMPLE_CONFIG.read_text(encoding="utf-8"))
    data["github"]["intake"]["enabled"] = data["github"]["publish"]["enabled"] = True
    data["github"]["review"]["enabled"] = True
    data["providers"]["claude"]["executable"] = str(cli)
    config = tmp_path / "project.yaml"
    config.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    run_dir = request_dir(state_dir, tag="review-e2e")
    env = {**process_environment("review-e2e"), "GH_TOKEN": "ghp_never", "PYTHONPATH": str(REPO_ROOT)}
    proc = subprocess.run([sys.executable, "-m", "feature_coordinator", "--repo", str(REPO_ROOT), "--state-dir",
                           str(state_dir), "--project-config", str(config), "github", "review", "exec", str(run_dir)],
                          env=env, cwd=REPO_ROOT, capture_output=True, text=True, timeout=120, check=False)
    assert proc.returncode == 0, proc.stderr
    assert "Reviewed head" not in proc.stdout + proc.stderr  # the prompt is never printed
    result = json.loads(result_path(run_dir).read_text(encoding="utf-8"))
    assert result["status"] == "ok" and result["output"]["head_sha"] == "a" * 40, result
    seen = json.loads(record.read_text(encoding="utf-8"))
    assert seen["env"][REVIEW_TAG_ENV] == "review-e2e" and "GH_TOKEN" not in seen["env"]  # tagged, no token
    assert seen["argv"][seen["argv"].index("--tools") + 1] == "" and "--strict-mcp-config" in seen["argv"]
    assert Path(seen["cwd"]) == (run_dir / "empty").resolve() and not any((run_dir / "empty").iterdir())
    refused = subprocess.run([sys.executable, "-m", "feature_coordinator", "--repo", str(REPO_ROOT),
                              "--state-dir", str(state_dir),
                              "--project-config", str(config), "github", "review", "exec", str(run_dir)],
                             env={k: v for k, v in env.items() if k != REVIEW_TAG_ENV}, cwd=REPO_ROOT,
                             capture_output=True, text=True, timeout=60, check=False)
    assert refused.returncode == 1 and "does not carry the attempt's tag" in refused.stderr


def test_the_r1_runner_never_starts_a_background_process() -> None:
    assert ReviewRunner.background is False and review_worker.WorkerReviewRunner.background is True
