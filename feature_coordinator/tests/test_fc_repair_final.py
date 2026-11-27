"""W4.8.1: regressions for the final independent (Codex) review of PR #90, findings F1–F6.

F1 is about publication for EVERY feature (repair disabled too): a task the Worker holds for an operator — e.g. a
launch settled CRITICAL because an agent process survived it — stops integration, the push and a new Draft PR until an
operator resets it; following an existing PR and completing a verified merge continue. F2–F6 are about the repair
step: an unsettled launch never lets an attempt advance; a review replaced during the launch's pre-launch checks starts
nothing; the review's binding (merge base) is re-derived before every launch and push; an unconfirmed push is never
repeated; and the diagnostic for an unfinished revalidation names the real state. Same rigs as test_fc_repair.py: a
bare `origin`, the mock tracker, mock agents, recorded gates, the real WorkerService loop. No model, no network."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-import

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from feature_coordinator import service as service_module
from feature_coordinator import supervisor
from feature_coordinator.autonomy import CRITICAL, Outcome, load_scheduling
from feature_coordinator.cli import main as cli_main
from feature_coordinator.errors import RemoteUnavailableError
from feature_coordinator.issue_sync import SimulatedCrash
from feature_coordinator.model import FeatureState, TaskState
from feature_coordinator.repair import abandon
from feature_coordinator.repair_config import GitHubRepairConfig
from feature_coordinator.repair_record import load_repair
from feature_coordinator.revalidate import PullRequestRevalidator, load_revalidation
from feature_coordinator.runners import FileEdit

from fc_helpers import git
from test_fc_publish import PubRig, github_origin, remote, remote_sha, verified
from test_fc_repair import (
    CHANGED, agent, attention, counted, dead_launch, events, follows, last, no_reviewer_process, repair_once,
    requested, rig, step,
)
from test_fc_revalidate import local_head
from test_fc_review import FakeReviewer, finding, ok, review


def survivor(monkeypatch: pytest.MonkeyPatch, service: Any, live: list[bool]) -> None:
    """An agent process with the run tag `survivor` lives while live[0] (the service and blocking_survivors see it)."""
    def tagged(env: str, tag: str) -> list[int]:
        return [4242] if live[0] and env == service_module.RUN_TAG_ENV and tag == "survivor" else []
    monkeypatch.setattr(service, "_run_tags", lambda *_a: {"survivor"})
    monkeypatch.setattr(service_module, "tagged_processes", tagged)
    monkeypatch.setattr(supervisor, "tagged_processes", tagged)


def off(rig: PubRig) -> Any:
    return dataclasses.replace(rig.config, github=dataclasses.replace(rig.config.github, repair=GitHubRepairConfig()))


# -- F1: publication stops for a task the Worker holds (repair disabled) -------------------------------------------

def test_a_survivor_of_an_orphaned_launch_stops_publication_with_repair_off(rig: PubRig,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    _number, task_id, feature_id = verified(rig)  # an ordinary approved Issue's task, verified by its launch
    service = rig.service(config=off(rig), until=lambda s: s.cycles >= 8, max_cycles=8)
    with rig.store.transaction():  # that launch's supervision failed: its record stayed in flight (this instance)
        rec = load_scheduling(rig.store, rig.store.load_task(task_id))
        rig.store.save_record("scheduling", dataclasses.replace(rec, launches=1, in_flight={
            "launch": 1, "instance": service.instance, "started_at": "2026-01-01T00:00:00Z", "tag": "exited",
            "worker_id": "w"}))
    live = [True]
    survivor(monkeypatch, service, live)
    assert service.run() == 0 and not service.launched
    assert attention(rig, task_id)["category"] == CRITICAL and attention(rig, task_id)["blocking_run_tags"] == [
        "survivor"]
    assert rig.feature(feature_id).state is FeatureState.ACTIVE and not rig.prs(), rig.out[-10:]
    live[0] = False  # the process ended: the critical finding stays until an operator resets it
    stopped = rig.publish()
    assert "held for an operator" in stopped.attention[0] and "worker reset-retries" in stopped.attention[0]
    assert rig.feature(feature_id).state is FeatureState.ACTIVE and not rig.prs()
    assert cli_main(["--state-dir", str(rig.state), "worker", "reset-retries", task_id, "--apply"]) == 0
    assert rig.publish().created and rig.feature(feature_id).state is FeatureState.REVIEW


def test_a_verified_task_with_a_critical_launch_verdict_is_never_integrated(rig: PubRig,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from test_fc_intake import approved  # pylint: disable=import-outside-toplevel
    _number, task_id = approved(rig)
    service = rig.service(config=off(rig), until=lambda s: bool(attention(rig, task_id)), max_cycles=20)
    monkeypatch.setattr(service, "_judge", lambda *_a: Outcome(CRITICAL, "agent processes survived their launch",
                                                               evidence={"blocking_run_tags": ["t"]}))
    assert service.run() == 0 and rig.store.load_task(task_id).state is TaskState.VERIFIED
    report = rig.publish()  # the normal path (no error): the same rule
    assert "not integrated" in report.attention[0] and not report.integrated and not rig.prs()


def test_a_held_task_never_lets_a_feature_in_review_push_or_open_its_pr(rig: PubRig) -> None:
    _number, task_id, feature_id = verified(rig)
    with rig.store.transaction():  # integrated before; the push and the PR are still to come
        rig.coord.integrate(feature_id)
        rec = load_scheduling(rig.store, rig.store.load_task(task_id))
        rig.store.save_record("scheduling", dataclasses.replace(rec, attention={
            "category": CRITICAL, "reason": "foreign commit", "since": "2026-01-01T00:00:00Z", "action": "inspect"}))
    report = rig.publish()
    assert "not pushed, no Draft PR opened" in report.attention[0] and not rig.prs()
    assert remote_sha(rig.bare, f"feature/{feature_id}") is None


def test_a_verified_merge_still_completes_while_a_task_is_held(rig: PubRig) -> None:
    from test_fc_publish import draft_open  # pylint: disable=import-outside-toplevel
    number, task_id, feature_id, pr = draft_open(rig)
    with rig.store.transaction():
        rec = load_scheduling(rig.store, rig.store.load_task(task_id))
        rig.store.save_record("scheduling", dataclasses.replace(rec, attention={
            "category": CRITICAL, "reason": "late verdict", "since": "2026-01-01T00:00:00Z", "action": "inspect"}))
    merge = rig.merge_on_github(pr)
    done = rig.publish()
    assert done.completed == {feature_id: merge} and rig.remote(number).state == "closed"


# -- F2: an unsettled launch never lets an attempt advance -------------------------------------------------------

def verified_attempt(rig: PubRig, status: str = "verified") -> tuple[str, str, str, str]:
    """A synthetic attempt recorded `status` at the agent's verified head, its launch still in flight (dead)."""
    _number, task_id, feature_id, _pr, old = requested(rig)
    step(rig)
    agent(rig, task_id)
    assert rig.coord.run_task(task_id).state is TaskState.VERIFIED
    head = local_head(rig, feature_id)
    with rig.store.transaction():
        record = load_repair(rig.store, feature_id)
        record.open_attempt().update(status=status, verified_head=head, launches=1)
        rig.store.save_record("repair", record)
    dead_launch(rig, task_id, 1)
    return task_id, feature_id, old, head


@pytest.mark.parametrize("status", ["verified", "pushed"])
@pytest.mark.usefixtures("follows")
def test_an_unsettled_launch_holds_the_push_and_the_revalidation(rig: PubRig, status: str) -> None:
    task_id, feature_id, old, head = verified_attempt(rig, status)
    if status == "pushed":
        git(rig.repo, "push", "-q", str(rig.bare), f"{head}:refs/heads/feature/{feature_id}")
    report = step(rig)
    assert "not settled yet" in report.waiting[0] and last(rig, feature_id)["status"] == status, report.lines()
    assert rig.candidate(feature_id).head_sha == old and not load_revalidation(rig.store, feature_id)
    if status == "verified":
        assert remote_sha(rig.bare, f"feature/{feature_id}") == old
    with pytest.raises(Exception, match="stop it first"):  # abandon waits for the settlement too
        abandon(rig.store, feature_id, reason="r", actor="operator", apply=True)
    service = rig.service(until=lambda s: last(rig, feature_id)["status"] == "completed", max_cycles=20)
    assert service.run() == 0 and not service.launched, rig.out[-10:]  # settled first, then it advanced
    assert rig.candidate(feature_id).head_sha == head and not load_scheduling(
        rig.store, rig.store.load_task(task_id)).in_flight


@pytest.mark.usefixtures("follows")
def test_a_held_task_or_an_inconsistent_record_never_advances_the_attempt(rig: PubRig) -> None:
    task_id, feature_id, old, _head = verified_attempt(rig)
    with rig.store.transaction():
        rec = load_scheduling(rig.store, rig.store.load_task(task_id))
        rig.store.save_record("scheduling", dataclasses.replace(rec, in_flight=None, attention={
            "category": CRITICAL, "reason": "survivor", "since": "2026-01-01T00:00:00Z", "action": "inspect"}))
    report = step(rig)
    assert "held for an operator" in report.attention[0] and last(rig, feature_id)["status"] == "verified"
    assert remote_sha(rig.bare, f"feature/{feature_id}") == old
    with rig.store.transaction():  # an inconsistent record: verified, but its task is not
        rec = load_scheduling(rig.store, rig.store.load_task(task_id))
        rig.store.save_record("scheduling", dataclasses.replace(rec, attention=None))
        rig.coord.reopen_task(task_id, "an operator re-opened it by hand")
    report = step(rig)
    assert last(rig, feature_id)["status"] == "failed" and "inconsistent record" in last(rig, feature_id)["reason"]
    assert remote_sha(rig.bare, f"feature/{feature_id}") == old


# -- F3: a review replaced during the launch's pre-launch checks -------------------------------------------------

def test_a_review_run_during_the_pre_launch_checks_starts_no_agent(rig: PubRig,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    _number, task_id, feature_id, _pr, _old = requested(rig)
    step(rig)
    agent(rig, task_id)
    service = rig.service(until=lambda s: s.cycles >= 1, max_cycles=1)
    monkeypatch.setattr(service, "_preflight", lambda _task: review(  # an operator's retry, after the freshness check
        rig, feature_id, FakeReviewer(ok("pass")), retry={"by": "operator", "reason": "again"}) and None)
    assert service.run() == 0 and not service.launched
    assert last(rig, feature_id)["launches"] == 0 and not (rig.store.load_record("scheduling", task_id) or {}).get(
        "in_flight")
    assert "no longer the current" in step(rig).attention[0] and last(rig, feature_id)["status"] == "failed"


# -- F4: the review's binding (merge base) before each launch and push -------------------------------------------

@pytest.mark.parametrize("boundary", ["launch", "push"])
@pytest.mark.usefixtures("follows")
def test_a_changed_merge_base_stops_the_launch_and_the_push(rig: PubRig, boundary: str) -> None:
    _number, task_id, feature_id, pr, earlier = requested(rig)
    old = repair_once(rig, feature_id, task_id)
    review(rig, feature_id, FakeReviewer(ok("request_changes", finding("major"))))
    agent(rig, task_id, FileEdit(CHANGED, "COMMANDS = ['second fix']\n"))
    step(rig)
    if boundary == "push":
        assert rig.coord.run_task(task_id).state is TaskState.VERIFIED
    git(rig.bare, "update-ref", "refs/heads/dev", earlier)  # the base takes the earlier repair commit
    rig.tracker.update_pull_request(pr, base_sha=earlier)
    if boundary == "launch":
        service = rig.service(until=lambda s: s.cycles >= 2)
        assert service.run() == 0 and not service.launched, rig.out[-10:]
    else:
        step(rig)
    attempt = last(rig, feature_id)
    assert attempt["status"] == "failed" and "merge_base" in attempt["reason"] and "review retry" in attempt["reason"]
    assert remote_sha(rig.bare, f"feature/{feature_id}") == old and len(rig.prs()) == 1


# -- F5: an unconfirmed push is never repeated -------------------------------------------------------------------

@pytest.mark.parametrize("pr_reports", ["reviewed", "repaired"])
@pytest.mark.usefixtures("follows")
def test_a_rewind_after_an_unrecorded_push_is_never_undone(rig: PubRig, pr_reports: str,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    _number, task_id, feature_id, pr, old = requested(rig)
    step(rig)
    agent(rig, task_id)
    assert rig.coord.run_task(task_id).state is TaskState.VERIFIED
    with pytest.raises(SimulatedCrash):  # 1-2: pushed, the Worker crashed before recording it
        step(rig, (f"crash-after-push:{feature_id}",))
    new = local_head(rig, feature_id)
    assert last(rig, feature_id)["status"] == "verified" and last(rig, feature_id)["pushing"]["sha"] == new
    git(rig.bare, "update-ref", f"refs/heads/feature/{feature_id}", old, new)  # 3: an operator rewinds the branch
    shown = rig.tracker.get_pull_request(pr)  # 4: GitHub's PR answer (either side of the lag)
    shown = dataclasses.replace(shown, head_sha=old if pr_reports == "reviewed" else new)
    monkeypatch.setattr(type(rig.tracker), "get_pull_request", lambda _self, _number: shown)
    report = step(rig, coord=rig.make())  # 5: the Worker resumes
    attempt = last(rig, feature_id)
    assert attempt["status"] == "failed" and "cannot be told apart" in attempt["reason"], report.lines()
    assert remote_sha(rig.bare, f"feature/{feature_id}") == old and not events(rig, "repair-pushed")


@pytest.mark.usefixtures("follows")
def test_a_push_whose_answer_was_lost_is_never_repeated(rig: PubRig, monkeypatch: pytest.MonkeyPatch) -> None:
    _number, task_id, feature_id, _pr, old = requested(rig)
    step(rig)
    agent(rig, task_id)
    assert rig.coord.run_task(task_id).state is TaskState.VERIFIED
    network = rig.coord.git._network

    def lost(*args: Any, **kwargs: Any) -> Any:  # the push itself fails without a clear refusal
        if args[0] == "push":
            raise RemoteUnavailableError("the connection dropped during the push")
        return network(*args, **kwargs)
    with monkeypatch.context() as patched:
        patched.setattr(rig.coord.git, "_network", lost)
        report = step(rig)
    assert report.deferred and last(rig, feature_id)["pushing"] and last(rig, feature_id)["status"] == "verified"
    again = step(rig)  # origin still has the reviewed head: lost, or rewound since — never pushed again
    assert last(rig, feature_id)["status"] == "failed" and "never confirmed" in last(rig, feature_id)["reason"], \
        again.lines()
    assert remote_sha(rig.bare, f"feature/{feature_id}") == old


# -- F6: the diagnostic for an unfinished revalidation -----------------------------------------------------------

@pytest.mark.usefixtures("follows")
def test_an_unfinished_revalidation_is_diagnosed_without_a_futile_command(rig: PubRig,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    _number, task_id, feature_id, _pr, old = requested(rig)
    step(rig)
    agent(rig, task_id)
    assert rig.coord.run_task(task_id).state is TaskState.VERIFIED
    real = rig.store.save_feature

    def save_then_crash(value: Any) -> None:
        real(value)
        if value.pull_request is not None and value.pull_request.head_sha != old:
            raise SimulatedCrash("after the candidate moved, before the revalidation record was finalized")
    with monkeypatch.context() as patched:
        patched.setattr(rig.store, "save_feature", save_then_crash)
        with pytest.raises(SimulatedCrash):
            step(rig)
    step(rig, coord=rig.make())
    reason, new = last(rig, feature_id)["reason"], local_head(rig, feature_id)
    (entry,) = load_revalidation(rig.store, feature_id).entries
    assert last(rig, feature_id)["status"] == "failed" and entry["status"] == "running"
    assert f"revalidation {entry['op']} was cut short" in reason and "cannot finish it" in reason
    assert "every gate passed" in reason and "github publish status" in reason
    # what the diagnostic says: rerunning revalidation does not finish the record
    assert PullRequestRevalidator(rig.make()).run(feature_id, apply=True, expected_head=new).status == "unchanged"
    assert load_revalidation(rig.store, feature_id).entries[0]["status"] == "running"


# -- gaps in the corrections themselves (found by the focused review's partial run) --------------------------------

@pytest.mark.usefixtures("follows")
def test_a_rewind_between_the_reconciling_read_and_the_push_is_never_undone(rig: PubRig,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    _number, task_id, feature_id, _pr, old = requested(rig)
    step(rig)
    agent(rig, task_id)
    assert rig.coord.run_task(task_id).state is TaskState.VERIFIED
    with pytest.raises(SimulatedCrash):
        step(rig, (f"crash-after-push:{feature_id}",))
    new, branch = local_head(rig, feature_id), f"feature/{feature_id}"
    coord = rig.make()
    remote_head, reads = coord.git.remote_head, []

    def read_then_rewind(name: str, **kwargs: Any) -> Any:  # the operator rewinds right after the first read
        value = remote_head(name, **kwargs)
        if name == branch:
            reads.append(value)
            if len(reads) == 1:
                git(rig.bare, "update-ref", f"refs/heads/{branch}", old, new)
        return value
    monkeypatch.setattr(coord.git, "remote_head", read_then_rewind)
    report = step(rig, coord=coord)
    assert reads[:2] == [new, old] and remote_sha(rig.bare, branch) == old, (reads, report.lines())
    assert last(rig, feature_id)["status"] == "failed" and not events(rig, "repair-pushed")


@pytest.mark.usefixtures("follows")
def test_a_merge_ends_a_recorded_verified_attempt_that_is_held_for_an_operator(rig: PubRig,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    task_id, feature_id, old, _head = verified_attempt(rig)
    pr = rig.candidate(feature_id).number
    service = rig.service(until=lambda s: s.cycles >= 2, max_cycles=2)
    survivor(monkeypatch, service, [True])  # its dead launch is settled CRITICAL: an agent process survived it
    assert service.run() == 0 and not service.launched
    assert attention(rig, task_id)["blocking_run_tags"] == ["survivor"]
    assert last(rig, feature_id)["status"] == "verified"
    merge = rig.merge_on_github(pr)  # a person merges the reviewed head meanwhile
    report = step(rig)
    assert last(rig, feature_id)["status"] == "failed" and "merged by a person" in report.attention[0]
    assert rig.publish().completed == {feature_id: merge} and remote_sha(rig.bare, f"feature/{feature_id}") == old


def test_a_launch_record_nothing_settles_never_blocks_a_feature_in_review(rig: PubRig,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    from test_fc_intake import approved  # pylint: disable=import-outside-toplevel
    service = rig.service(config=off(rig), until=lambda s: s.cycles >= 1, max_cycles=1)
    _number, task_id = approved(rig)

    def failed_verdict(*_args: Any) -> Outcome:  # the child verified; the Worker failed before judging the launch
        raise OSError("interrupted after the verified child exited")
    monkeypatch.setattr(service, "_judge", failed_verdict)
    assert service.run() == 0 and len(service.launched) == 1
    feature_id = rig.store.load_task(task_id).feature_id
    assert load_scheduling(rig.store, rig.store.load_task(task_id)).in_flight
    stopped = rig.publish()  # an active feature: integration still waits for the launch to be settled
    assert not stopped.integrated and rig.feature(feature_id).state is FeatureState.ACTIVE
    assert rig.make(config=off(rig)).integrate(feature_id).state is FeatureState.REVIEW  # an operator, by hand
    report = rig.publish()  # in review, nothing could settle that record: it does not block the push for ever
    assert report.created and remote_sha(rig.bare, f"feature/{feature_id}") == rig.candidate(feature_id).head_sha
