"""LIVE GitHub Projects acceptance tests (Phase 4 extension) — opt-in, they write to the real
repository and the configured project board.

    FC_GITHUB_PROJECTS_LIVE=1 [FC_GITHUB_LIVE_DIR=<dir>] .venv/bin/python -m pytest -p no:cacheprovider \
        scripts/tests/feature_coordinator/test_fc_projects_live.py

One module-scoped run of live_projects.run_projects_test creates exactly TWO Issues labeled
`feature:test` (a parent and one Backend task), adds them to the configured project (Project #2
for mx-admin-cloud), drives them through the board lifecycle, restarts the coordinator in a new
process, verifies that the protected project (#1), every unrelated Issue and PR and the labels
are unchanged, then closes both Issues with a final comment. The tests below re-read GitHub
(read-only) to confirm each property. Without FC_GITHUB_PROJECTS_LIVE=1 every test is skipped
and nothing is sent to GitHub. (FC_GITHUB_LIVE=1 runs only the Phase 4 Issue scenario.)
"""

# pylint: disable=missing-function-docstring,redefined-outer-name

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import pytest

from feature_coordinator.config import default_config
from feature_coordinator.github_api import GitHubClient
from feature_coordinator.github_projects import ProjectsClient
from feature_coordinator.issues import TEST_LABEL, feature_marker, parse_op, task_marker, verify_ownership
from feature_coordinator.live_projects import run_projects_test

pytestmark = pytest.mark.skipif(os.environ.get("FC_GITHUB_PROJECTS_LIVE") != "1",
                                reason="live GitHub Projects tests are opt-in: set FC_GITHUB_PROJECTS_LIVE=1")
CONFIG = default_config()
CFG = CONFIG.github.project


@pytest.fixture(scope="module")
def live() -> tuple[GitHubClient, ProjectsClient, dict[str, Any]]:
    workdir = Path(os.environ.get("FC_GITHUB_LIVE_DIR") or tempfile.mkdtemp(prefix="fc-projects-live-"))
    report = run_projects_test(CONFIG, workdir, tracker_name="github")
    client = GitHubClient.from_config(CONFIG.github)
    return client, ProjectsClient(client), report


def check(report: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    found = [c for c in report["checks"] if c["check"].startswith(prefix)]
    assert found, f"no check {prefix!r} in the report"
    return found


def test_live_projects_scenario_passed_every_check(live) -> None:
    _client, _board, report = live
    failed = [c for c in report["checks"] if not c["passed"]]
    assert report["passed"] and not failed, failed


def test_live_project_identity_scope_and_setup(live) -> None:
    client, board, report = live
    scopes = client.token_scopes()
    assert scopes is None or "project" in scopes
    project = board.project(CFG.owner, CFG.owner_type, CFG.number)
    assert project is not None and project.id == CFG.node_id == report["project"]["id"]
    status = project.field(CFG.status_field)
    assert status is not None and [o.name for o in status.options][:6] == list(CFG.status_names.values())
    assert project.field(CFG.agent_field) and project.field(CFG.feature_id_field) and project.field(
        CFG.blocked_reason_field)
    assert project.view(CFG.board_view) and project.view(CFG.active_view)
    for prefix in ("B0",):
        assert all(c["passed"] for c in check(report, prefix))


def test_live_exactly_two_owned_issues_each_on_the_board_once(live) -> None:
    client, board, report = live
    feature_id, viewer = report["feature"], client.viewer_login()
    assert len(report["issues"]) == 2
    for record_id, info in report["issues"].items():
        issue = client.get_issue(info["number"])
        expected = feature_marker(CONFIG.github.coordinator_id, feature_id) if record_id == feature_id else \
            task_marker(CONFIG.github.coordinator_id, feature_id, record_id)
        verify_ownership(issue, expected, viewer=viewer, known_number=info["number"])
        assert TEST_LABEL in issue.labels
        refs = board.issue_items(CONFIG.github.repository, info["number"])
        assert [r.id for r in refs if r.project_number == CFG.number] == [info["item"]]
        assert all(r.project_number not in CFG.protected_numbers for r in refs)


def test_live_status_values_after_cleanup(live) -> None:
    client, board, report = live
    feature_id = report["feature"]
    for record_id, info in report["issues"].items():
        item = board.get_item(info["item"])
        assert item is not None and item.values.get(CFG.feature_id_field) == feature_id
        expected = ("Review", "Coordination") if record_id == feature_id else ("Done", "Backend")
        assert (item.values.get(CFG.status_field), item.values.get(CFG.agent_field)) == expected
        assert client.get_issue(info["number"]).state == "closed"
        assert any((parse_op(c.body) or ("", "", ""))[2] == "final" for c in client.list_comments(info["number"]))


def test_live_transitions_recovery_and_manual_moves(live) -> None:
    _client, _board, report = live
    for prefix in ("B1", "B2", "B3", "B4", "B5", "B6", "B7", "B8", "B9", "B10", "B11", "B12", "B13"):
        assert all(c["passed"] for c in check(report, prefix)), prefix
    assert report["observations"]["running"]["status"] == "In Progress"
    assert report["observations"]["verifying"]["status"] == "Review"


def test_live_protected_project_and_unrelated_issues_untouched(live) -> None:
    client, _board, report = live
    assert all(c["passed"] for c in check(report, "B14"))
    assert report["cleanup"]["ok"] and report["cleanup"]["protected_unchanged"] and \
        report["cleanup"]["repeat_writes"] == []
    branch = f"feature/{report['feature']}"
    assert client.list_pull_requests(branch) == []
