"""Feature/task state machines, evidence and the dependency DAG."""

# pylint: disable=missing-function-docstring

from __future__ import annotations

import pytest

from feature_coordinator.errors import DependencyError, EvidenceError, IllegalTransitionError
from feature_coordinator.model import (
    Evidence,
    Feature,
    FeatureState,
    GateResult,
    PullRequestCandidate,
    Task,
    TaskState,
    check_id,
    invalidate_dependents,
    refresh_readiness,
    transition_feature,
    transition_task,
    validate_dag,
)

HEAD = "a" * 40
OTHER = "b" * 40


def task(tid: str, role: str = "backend", deps: tuple[str, ...] = (), gates: tuple[str, ...] = ("t",),
         state: TaskState = TaskState.PROPOSED) -> Task:
    return Task(id=tid, feature_id="f", role=role, title=tid, depends_on=deps, required_gates=gates, state=state)


def good(head: str = HEAD, gates: tuple[str, ...] = ("t",)) -> Evidence:
    return Evidence(head_sha=head, gates=tuple(GateResult(g, 0) for g in gates))


def verify(t: Task) -> None:
    for state in (TaskState.READY, TaskState.IN_PROGRESS):
        transition_task(t, state, "test")
    transition_task(t, TaskState.VERIFIED, "test", evidence=good(), expected_head=HEAD)


def test_task_lifecycle_and_idempotency() -> None:
    t = task("f.a")
    assert transition_task(t, TaskState.READY, "go")
    assert not transition_task(t, TaskState.READY, "again")  # idempotent no-op
    assert len(t.history) == 1
    transition_task(t, TaskState.IN_PROGRESS, "claimed")
    transition_task(t, TaskState.VERIFIED, "done", evidence=good(), expected_head=HEAD)
    assert t.state is TaskState.VERIFIED and t.evidence is not None


@pytest.mark.parametrize("start,target", [
    (TaskState.PROPOSED, TaskState.VERIFIED),
    (TaskState.PROPOSED, TaskState.IN_PROGRESS),
    (TaskState.READY, TaskState.VERIFIED),
    (TaskState.BLOCKED, TaskState.IN_PROGRESS),
    (TaskState.VERIFIED, TaskState.IN_PROGRESS),
])
def test_illegal_task_transitions(start: TaskState, target: TaskState) -> None:
    with pytest.raises(IllegalTransitionError):
        transition_task(task("f.a", state=start), target, "nope", evidence=good(), expected_head=HEAD)


@pytest.mark.parametrize("evidence,match", [
    (None, "requires evidence"),
    (good(head=OTHER), "bound to"),
    (Evidence(HEAD, (GateResult("t", 1),)), "failing gates"),
    (Evidence(HEAD, ()), "missing gates"),
    (Evidence("short", (GateResult("t", 0),)), "40-hex"),
])
def test_verified_needs_passing_evidence_on_the_branch_head(evidence: Evidence | None, match: str) -> None:
    t = task("f.a", state=TaskState.IN_PROGRESS)
    with pytest.raises(EvidenceError, match=match):
        transition_task(t, TaskState.VERIFIED, "x", evidence=evidence, expected_head=HEAD)
    assert t.state is TaskState.IN_PROGRESS


def test_reopening_a_verified_task_drops_its_evidence() -> None:
    t = task("f.a")
    verify(t)
    transition_task(t, TaskState.READY, "contract changed")
    assert t.evidence is None


def test_feature_done_needs_a_merged_pr_not_just_pr_creation() -> None:
    f = Feature(id="f", slug="f", title="F", base_branch="dev", branch="feature/f")
    for state in (FeatureState.ACTIVE, FeatureState.INTEGRATION, FeatureState.REVIEW):
        transition_feature(f, state, "step")
    with pytest.raises(EvidenceError, match="merged PR"):
        transition_feature(f, FeatureState.DONE, "pr created")
    f.pull_request = PullRequestCandidate("feature/f", "dev", "F", "body", HEAD, url="pr#1")
    with pytest.raises(EvidenceError):
        transition_feature(f, FeatureState.DONE, "no merge sha")
    transition_feature(f, FeatureState.DONE, "merged", merged_sha=OTHER)
    assert f.state is FeatureState.DONE
    with pytest.raises(IllegalTransitionError):
        transition_feature(f, FeatureState.ACTIVE, "terminal")


def test_feature_cannot_skip_integration() -> None:
    f = Feature(id="f", slug="f", title="F", base_branch="dev", branch="feature/f", state=FeatureState.ACTIVE)
    with pytest.raises(IllegalTransitionError):
        transition_feature(f, FeatureState.REVIEW, "skip")


def test_dag_rejects_unknown_self_and_cycles() -> None:
    with pytest.raises(DependencyError, match="unknown"):
        validate_dag({"f.a": task("f.a", deps=("f.zz",))})
    with pytest.raises(DependencyError, match="itself"):
        task("f.a", deps=("f.a",))
    tasks = {"f.a": task("f.a", deps=("f.c",)), "f.b": task("f.b", deps=("f.a",)), "f.c": task("f.c", deps=("f.b",))}
    with pytest.raises(DependencyError, match="cycle"):
        validate_dag(tasks)


def test_topological_order() -> None:
    tasks = {"f.cli": task("f.cli", deps=("f.api",)), "f.api": task("f.api"), "f.e2e": task("f.e2e", deps=("f.cli",))}
    order = validate_dag(tasks)
    assert order.index("f.api") < order.index("f.cli") < order.index("f.e2e")


def test_blocking_and_resuming_only_touches_the_dependent_task() -> None:
    api, cli, ui = task("f.api"), task("f.cli", "cli", ("f.api",)), task("f.ui", "frontend")
    tasks = {t.id: t for t in (api, cli, ui)}
    for t in tasks.values():
        transition_task(t, TaskState.READY, "activated")
    assert refresh_readiness(tasks) == ["f.cli"]
    assert cli.state is TaskState.BLOCKED and "f.api" in cli.blocked_reason
    assert ui.state is TaskState.READY  # unrelated work continues
    verify(api)
    assert refresh_readiness(tasks) == ["f.cli"]
    assert cli.state is TaskState.READY


def test_human_block_is_not_auto_resumed() -> None:
    t = task("f.a", state=TaskState.READY)
    transition_task(t, TaskState.BLOCKED, "needs an operator decision")
    assert not refresh_readiness({"f.a": t})
    assert t.state is TaskState.BLOCKED


def test_invalidate_dependents_after_contract_change() -> None:
    api, cli, e2e = task("f.api"), task("f.cli", "cli", ("f.api",)), task("f.e2e", "automation", ("f.cli",))
    tasks = {t.id: t for t in (api, cli, e2e)}
    for t in (api, cli, e2e):
        verify(t)
    transition_task(api, TaskState.READY, "contract change")
    assert sorted(invalidate_dependents("f.api", tasks)) == ["f.cli", "f.e2e"]
    assert cli.state is TaskState.BLOCKED and e2e.state is TaskState.BLOCKED
    verify(api)
    refresh_readiness(tasks)
    assert cli.state is TaskState.READY and e2e.state is TaskState.BLOCKED


def test_records_round_trip() -> None:
    t = task("f.a")
    verify(t)
    assert Task.from_dict(t.to_dict()) == t
    f = Feature(id="f", slug="f", title="F", base_branch="dev", branch="feature/f", task_ids=["f.a"])
    assert Feature.from_dict(f.to_dict()) == f


def test_schema_version_is_checked() -> None:
    data = task("f.a").to_dict()
    data["schema_version"] = 99
    with pytest.raises(ValueError, match="schema_version"):
        Task.from_dict(data)


@pytest.mark.parametrize("bad", ["", "../x", "A", "a/b", "x" * 81, ".hidden"])
def test_ids_cannot_traverse(bad: str) -> None:
    with pytest.raises(ValueError):
        check_id(bad)
