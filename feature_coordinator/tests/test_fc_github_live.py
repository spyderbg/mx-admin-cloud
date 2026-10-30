"""LIVE GitHub acceptance tests (Phase 4) — opt-in, they write to the real repository.

    FC_GITHUB_LIVE=1 [FC_GITHUB_LIVE_DIR=<dir>] .venv/bin/python -m pytest -p no:cacheprovider \
        feature_coordinator/tests/test_fc_github_live.py

One module-scoped run of the Phase 4 scenario (live_test.run_lifecycle) creates exactly four
Issues labeled `feature:test` in the configured repository, drives the dependency lifecycle,
restarts the coordinator in a new process, verifies that no unrelated Issue changed and no pull
request exists, then posts a final verification comment and closes the four Issues. The
individual tests below re-read GitHub (read-only) to confirm each property. Without
FC_GITHUB_LIVE=1 every test is skipped and nothing is sent to GitHub.
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
from feature_coordinator.issues import TEST_LABEL, VERIFIED_LABEL, feature_marker, parse_op, task_marker, \
    verify_ownership
from feature_coordinator.live_test import run_lifecycle

pytestmark = pytest.mark.skipif(os.environ.get("FC_GITHUB_LIVE") != "1",
                                reason="live GitHub tests are opt-in: set FC_GITHUB_LIVE=1")
CONFIG = default_config()


@pytest.fixture(scope="module")
def live() -> tuple[GitHubClient, dict[str, Any]]:
    workdir = Path(os.environ.get("FC_GITHUB_LIVE_DIR") or tempfile.mkdtemp(prefix="fc-github-live-"))
    client = GitHubClient.from_config(CONFIG.github)
    report = run_lifecycle(client, workdir, tracker_name="github", operator_comment=client.create_comment)
    return client, report


def check(report: dict[str, Any], prefix: str) -> list[dict[str, Any]]:
    found = [c for c in report["checks"] if c["check"].startswith(prefix)]
    assert found, f"no check {prefix!r} in the report"
    return found


def test_live_authentication_and_repository_access(live) -> None:
    client, report = live
    assert client.viewer_login() == report["viewer"]
    info = client.repository_info()
    assert info["full_name"] == CONFIG.github.repository and info["has_issues"]


def test_live_lifecycle_passed_every_check(live) -> None:
    _client, report = live
    failed = [c for c in report["checks"] if not c["passed"]]
    assert report["passed"] and not failed, failed


def test_live_issues_exist_are_owned_and_labeled(live) -> None:
    client, report = live
    feature_id, viewer = report["feature"], client.viewer_login()
    for record_id, info in report["issues"].items():
        issue = client.get_issue(info["number"])
        expected = feature_marker(CONFIG.github.coordinator_id, feature_id) if record_id == feature_id else \
            task_marker(CONFIG.github.coordinator_id, feature_id, record_id)
        verify_ownership(issue, expected, viewer=viewer, known_number=info["number"])
        assert TEST_LABEL in issue.labels and issue.url == info["url"]


def test_live_dependency_lifecycle_labels_and_comments(live) -> None:
    client, report = live
    for prefix in ("D/E", "F ", "G ", "H ", "I ", "J ", "K ", "K2", "K3", "T1", "T2", "T3"):
        assert all(c["passed"] for c in check(report, prefix)), prefix
    backend = report["issues"][f"{report['feature']}.contract-items-export"]["number"]
    assert VERIFIED_LABEL in client.get_issue(backend).labels
    comments = client.list_comments(backend)
    assert all(parse_op(c.body) for c in comments if c.author == client.viewer_login()
               and not c.body.startswith("/feature"))
    assert any("Verification evidence" in c.body for c in comments)


def test_live_idempotency_duplicates_restart_and_reconciliation(live) -> None:
    _client, report = live
    for prefix in ("A1", "A2", "A3", "A4", "L ", "M ", "N ", "O ", "P "):
        assert all(c["passed"] for c in check(report, prefix)), prefix


def test_live_no_pull_request_and_one_candidate(live) -> None:
    client, report = live
    assert all(c["passed"] for c in check(report, "R"))
    assert client.list_pull_requests(f"feature/{report['feature']}") == []


def test_live_unrelated_issues_untouched(live) -> None:
    _client, report = live
    for prefix in ("Q ", "Q2", "Q3", "Q4"):
        assert all(c["passed"] for c in check(report, prefix)), prefix


def test_live_test_issues_closed_with_a_final_comment(live) -> None:
    client, report = live
    assert report["cleanup"]["ok"] and report["cleanup"]["repeat_writes"] == []
    for info in report["issues"].values():
        assert client.get_issue(info["number"]).state == "closed"
        assert any((parse_op(c.body) or ("", "", ""))[2] == "final" for c in client.list_comments(info["number"]))
