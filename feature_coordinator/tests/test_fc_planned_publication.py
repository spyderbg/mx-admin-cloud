"""W4.5: a label-approved, multi-role execution plan through the W4.3.3 publication lifecycle — integration with
the APPROVED integration gates, one Draft PR, human merge, completion, the Issue closed and its Project #2 card
Done — all addressed through the planned feature's anchor (the approved Issue belongs to the feature).

Disposable repositories: a bare `origin` (real pushes), the persisted mock tracker for Issues, label events,
Actions runs and pull requests, the local board for Project #2, mock runners for the agents, recorded gates, a
fake planner and the GitHub Action's real receipt script. No model, no real GitHub, no network."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator.intake import feature_id_for, load_intake
from feature_coordinator.model import FeatureState, TaskState
from feature_coordinator.workflow import DEFAULT_INTEGRATION_GATES

from test_fc_issue_planning import EDITS, MULTI, SINGLE, FakePlanner, PlanRig, planning_config
from test_fc_project_sync import values
from test_fc_publish import REPOSITORY, PubRig, github_origin, remote, remote_sha  # pylint: disable=unused-import


def planned_publish_config(*, without_gates: tuple[str, ...] = ()):
    config = planning_config()
    config = dataclasses.replace(config, github=dataclasses.replace(config.github, publish=dataclasses.replace(
        config.github.publish, enabled=True)))
    if without_gates:
        config = dataclasses.replace(config, gates={k: v for k, v in config.gates.items() if k not in without_gates})
    return config


class PlannedPubRig(PlanRig):
    """PlanRig (planner, labels, the Action) with PubRig's `origin` remote, pull requests and publisher."""

    def __init__(self, repo: Path, state: Path, bare: Path, *, planner: FakePlanner, config=None) -> None:
        self.bare = bare
        super().__init__(repo, state, config=config or planned_publish_config(), planner=planner)

    def make(self, faults: tuple[str, ...] = (), config=None):
        coord = super().make(faults, config)
        coord.tracker.repository = REPOSITORY  # type: ignore[attr-defined]
        coord.tracker.ref_resolver = lambda branch: remote_sha(self.bare, branch)  # type: ignore[attr-defined]
        return coord

    publisher = PubRig.publisher
    publish = PubRig.publish
    feature = PubRig.feature
    candidate = PubRig.candidate
    prs = PubRig.prs
    merge_on_github = PubRig.merge_on_github
    comments = PubRig.comments


def scripted(rig: PlannedPubRig, proposal: dict[str, Any]) -> PlannedPubRig:
    for role, edit in EDITS.items():
        rig.runners[role].script.update({f"issue-1.{t['id']}": [edit] for t in proposal["tasks"] if t["role"] == role})
    return rig


@pytest.fixture
def mrig(repo: Path, state_dir: Path, github_origin: Path) -> PlannedPubRig:
    return scripted(PlannedPubRig(repo, state_dir, github_origin, planner=FakePlanner(MULTI)), MULTI)


def approved_and_verified(rig: PlannedPubRig, *, title: str, body: str) -> tuple[int, str, dict[str, Any]]:
    """Issue → `worker:plan` → plan v1 → `worker:approve` → receipt → validated → every task verified."""
    number = rig.issue(title=title, body=body)
    assert number == 1
    rig.published(number)
    rig.approve_label(number)
    report = rig.poll()
    feature_id = feature_id_for(number)
    assert report.ok and report.planned == [feature_id] and report.activated == [feature_id], report.lines()
    plan = load_intake(rig.store, feature_id).plan["canonical"]  # type: ignore[union-attr]
    for planned in plan["tasks"]:  # plan order respects the dependencies
        assert rig.coord.run_task(f"{feature_id}.{planned['id']}").state is TaskState.VERIFIED
    return number, feature_id, plan


def test_a_multi_role_plan_is_published_as_one_draft_pr_and_completed(mrig: PlannedPubRig) -> None:  # pylint: disable=too-many-locals,too-many-statements
    number, feature_id, plan = approved_and_verified(mrig, title="Category export across the stack",
                                                     body="Backend API, UI, CLI and docs.")
    feature = mrig.feature(feature_id)
    task_ids = [f"{feature_id}.{t}" for t in ("api", "ui", "cli", "docs")]
    tasks = mrig.store.load_tasks(feature)
    assert feature.task_ids == task_ids and all(tasks[t].state is TaskState.VERIFIED for t in task_ids)
    assert tasks[f"{feature_id}.ui"].depends_on == (f"{feature_id}.api",)
    assert tasks[f"{feature_id}.docs"].depends_on == tuple(f"{feature_id}.{d}" for d in ("api", "cli", "ui"))
    # the approved Issue is the feature's (anchor): one card, no Issue or card for any task
    assert mrig.coord.sync.load_link(feature_id).issue_number == number
    assert all(mrig.coord.sync.load_link(t) is None for t in task_ids)
    assert values(mrig.coord, feature_id)["Status"] == "In Progress"
    record = load_intake(mrig.store, feature_id)
    assert record is not None and record.planned and record.anchor_id == feature_id
    # integration with the APPROVED integration gates
    seen: list[dict[str, Any]] = []
    integrate = mrig.coord.integrate

    def spy(fid: str, **kwargs: Any) -> Any:
        seen.append(kwargs)
        return integrate(fid, **kwargs)
    mrig.coord.integrate = spy  # type: ignore[method-assign]
    before = len(mrig.gates.calls)
    report = mrig.publish()
    assert report.ok and report.integrated == [feature_id] and report.pushed == [feature_id], report.lines()
    assert seen == [{"gates": tuple(plan["integration_gates"])}]
    assert mrig.gates.calls[before:] == list(plan["integration_gates"]) == ["contract-check", "docs-check"]
    feature, cand = mrig.feature(feature_id), mrig.candidate(feature_id)
    assert feature.state is FeatureState.REVIEW and report.created == {feature_id: cand.number}
    assert remote_sha(mrig.bare, feature.branch) == cand.head_sha == feature.integration_evidence.head_sha
    # exactly one Draft PR; its body shows the approved plan and every task with role, dependencies, gates
    (pr,) = mrig.prs()
    assert (pr.head, pr.base, pr.draft, pr.head_sha) == (feature.branch, "dev", True, cand.head_sha)
    approval = record.plan["approval_sha256"]
    receipt = record.approval_comment_url
    api, ui, cli = (f"`{feature_id}.{t}`" for t in ("api", "ui", "cli"))
    for text in (f"GitHub Issue #{number}", f"`{feature_id}`",
                 f"| Plan | v1, approval sha256 `{approval}` (label `worker:approve`, receipt {receipt}) |",
                 f"| Task | {api} (role `backend`, depends on none, gates `backend-tests`, `backend-lint`, "
                 "`docs-check`) |",
                 f"| Task | {ui} (role `frontend`, depends on {api}, gates "
                 "`frontend-type-check`, `frontend-build`, `docs-check`) |",
                 f"| Task | {cli} (role `cli`, depends on {api}, gates `cli-lint`, `docs-check`) |",
                 f"| Task | `{feature_id}.docs` (role `coordination`, depends on {api}, {cli}, {ui}, gates "
                 "`docs-check`) |",
                 *(f"`{edit.path}`" for edit in EDITS.values()), f"## Integration gates at `{cand.head_sha[:12]}`",
                 "contract-check", "never merges"):
        assert text in pr.body, text
    assert "closes #" not in pr.body.lower()
    assert mrig.remote(number).state == "open" and values(mrig.coord, feature_id)["Status"] == "Review"
    (link,) = mrig.comments(number, "Draft pull request opened")
    assert "Merging completes tasks " + ", ".join(f"`{t}`" for t in task_ids) in link
    again = mrig.publish()
    assert again.ok and not (again.created or again.adopted or again.pushed) and len(mrig.prs()) == 1
    # a person merges it: the feature completes, the approved Issue is closed, its card is Done
    merge = mrig.merge_on_github(pr.number)
    done = mrig.publish()
    assert done.ok and done.completed == {feature_id: merge} and done.closed == {feature_id: number}, done.lines()
    feature = mrig.feature(feature_id)
    assert feature.state is FeatureState.DONE and feature.merged_sha == merge
    issue = mrig.remote(number)
    assert issue.state == "closed" and issue.state_reason == "completed"
    (completed,) = mrig.comments(number, "**Completed.**")
    assert merge[:12] in completed and "of tasks " + ", ".join(f"`{t}`" for t in task_ids) in completed
    assert values(mrig.coord, feature_id)["Status"] == "Done"
    record = load_intake(mrig.store, feature_id)
    assert record is not None and record.status == "done" and record.completed["closed_by"] == "coordinator"
    assert all(mrig.store.load_task(t).state is TaskState.VERIFIED for t in task_ids)  # history kept
    assert len(mrig.tracker.list_issues()) == 1 and len(mrig.prs()) == 1               # nothing else was created
    kinds = [e["kind"] for e in mrig.store.events()]
    order = ["intake-approved", "feature-integrated", "publish-pushed", "pull-request-opened", "feature-done",
             "intake-issue-closed", "intake-completed"]
    assert [kinds.index(k) for k in order] == sorted(kinds.index(k) for k in order)


def test_integration_runs_the_approved_gates_not_the_defaults(repo: Path, state_dir: Path,
                                                              github_origin: Path) -> None:
    config = planned_publish_config(without_gates=("contract-check",))
    rig = scripted(PlannedPubRig(repo, state_dir, github_origin, planner=FakePlanner(SINGLE), config=config), SINGLE)
    _number, feature_id, plan = approved_and_verified(rig, title="Export category items as CSV",
                                                      body="Add `kiddo-cli content export-items`.")
    assert plan["integration_gates"] == ["docs-check"] != list(DEFAULT_INTEGRATION_GATES)
    before = len(rig.gates.calls)
    report = rig.publish()
    assert report.ok and report.created, report.lines()
    assert rig.gates.calls[before:] == ["docs-check"]     # the approved gates; the default contract-check never ran
    (pr,) = rig.prs()
    row = f"| Task | `{feature_id}.cli-export` (role `cli`, depends on none, gates `cli-lint`, `docs-check`) |"
    assert row in pr.body
