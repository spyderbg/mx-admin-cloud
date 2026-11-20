"""W4.7 (M5), Codex F5 and F6.

F5 — GitHub's check observations are untrusted data (integration_merge_github.check_observations): an identity that is
listed twice (contradictory or identical, within a page or across pages) makes the listing unreliable, so it blocks the
merge and the verification instead of letting an arbitrary first-or-last entry decide; distinct identities with the same
name (re-runs) stay legitimate.
F6 — COMPLETED needs the complete verification evidence (integration_merge_schema.completion_problems): every core check
exactly once and passing, and the check policy evaluated; `all([])` is not a verification.

Scripted GitHub behind the real client; real merges on the disposable origin only. No network, no real GitHub."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,too-many-lines

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Callable

import pytest

from feature_coordinator.integration import IntegrationJob, JobState, transition_job
from feature_coordinator.integration_merge_schema import CORE_CHECKS, completion_problems
from feature_coordinator.errors import GitHubResponseError
from feature_coordinator.integration_merge_github import CheckObservation, check_observations, evaluate_checks
from feature_coordinator.issue_sync import SimulatedCrash

from fc_integration_merge_rig import merge, ready_job
from fc_integration_rig import Rig, github_origin, origin  # pylint: disable=unused-import
from test_fc_integration_merge import check_run, existing_pr, required
from test_fc_integration_merge_approval import Doc, completed, merged  # pylint: disable=unused-import
from test_fc_integration_review import launchers, of_kind, rrig  # pylint: disable=unused-import
from test_fc_integration_validate import key, rig, seeded, tracker, with_integration  # pylint: disable=unused-import


def status_entry(ident: int, context: str, state: str) -> dict[str, Any]:
    return {"id": ident, "context": context, "state": state}


# -- F5 through the whole merge ---------------------------------------------------------------------------------------

@pytest.mark.parametrize(("name", "runs"), [
    ("same-id-success-then-failure", [check_run("ci", "success", ident=7), check_run("ci", "failure", ident=7)]),
    ("same-id-failure-then-success", [check_run("ci", "failure", ident=7), check_run("ci", "success", ident=7)]),
    ("identical-duplicate", [check_run("ci", "success", ident=7), check_run("ci", "success", ident=7)]),
    ("duplicate-hiding-a-missing-run", [check_run("ci", "success", ident=7), check_run("ci", "success", ident=7)]),
    ("run-of-another-commit", [{**check_run("ci", "success", ident=7), "head_sha": "e" * 40}]),
    ("no-integer-id", [{"id": "7", "name": "ci", "status": "completed", "conclusion": "success"}]),
    ("boolean-id", [{"id": True, "name": "ci", "status": "completed", "conclusion": "success"}]),
    ("zero-id", [check_run("ci", "success", ident=0)]),
    ("no-name", [{"id": 7, "status": "completed", "conclusion": "success"}]),
])
def test_f5_unreliable_check_observations_never_authorize_the_merge(rrig: Rig, launchers: Any, github_origin: Path,
                                                                  name: str, runs: list[dict[str, Any]]):
    """The original reproduction: required check `ci` id 7 listed as success AND failure; the first entry won."""
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    required(rrig, "ci")
    fake.checks[job.source["sha"]] = runs
    for apply in (False, True):
        report = merge(rrig, job.job_id, apply=apply)
        assert report.outcome in ("checks-pending", "would-defer") and not report.ok, (name, report.outcome)
        assert any(c["status"] == "unavailable" for c in report.checks if c["check"].startswith("required-check"))
    assert not fake.merge_requests and rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL


def test_f5_an_identity_listed_on_two_pages_makes_the_listing_unreliable(rrig: Rig, launchers: Any,
                                                                         github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    required(rrig, "ci")
    sha = job.source["sha"]
    fake.check_pages[sha] = [[check_run("lint", "success", ident=1), check_run("ci", "success", ident=7)],
                             [check_run("ci", "success", ident=7)]]            # id 7 again on page 2: 3 listed of 3 …
    fake.check_total[sha] = 3                                                    # … which would hide a missing run
    report = merge(rrig, job.job_id)
    assert report.outcome == "checks-pending" and "not a faithful enumeration" in " ".join(report.reasons)
    assert not fake.merge_requests
    fake.check_pages[sha] = [[check_run("lint", "success", ident=1), check_run("ci", "success", ident=7)],
                             [check_run("deploy", "success", ident=9)]]          # a faithful second page
    fake.check_total[sha] = 3
    assert merge(rrig, job.job_id).outcome == "completed"


def test_f5_distinct_runs_with_the_same_name_are_legitimate_and_the_newest_decides(rrig: Rig, launchers: Any,
                                                                                  github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    required(rrig, "ci")
    sha = job.source["sha"]
    fake.checks[sha] = [check_run("ci", "success", ident=7), check_run("ci", "failure", ident=8)]  # a failed re-run
    report = merge(rrig, job.job_id)
    assert report.outcome == "checks-failed" and not fake.merge_requests
    fake.checks[sha] = [check_run("ci", "failure", ident=7), check_run("ci", "success", ident=8)]  # a passing re-run
    assert merge(rrig, job.job_id).outcome == "completed" and len(fake.merge_requests) == 1


def test_f5_contradictory_status_contexts_block_and_distinct_statuses_of_one_context_are_history(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    required(rrig, "ci")
    sha = job.source["sha"]
    fake.statuses[sha] = [status_entry(5, "ci", "success"), status_entry(5, "ci", "failure")]
    assert merge(rrig, job.job_id).outcome == "checks-pending" and not fake.merge_requests
    fake.statuses[sha] = [status_entry(6, "ci", "failure"), status_entry(5, "ci", "success")]   # newest: failure
    assert merge(rrig, job.job_id).outcome == "checks-failed" and not fake.merge_requests
    fake.statuses[sha] = [status_entry(6, "ci", "success"), status_entry(5, "ci", "failure")]      # the newest passes
    assert merge(rrig, job.job_id).outcome == "completed"


@pytest.mark.parametrize(("runs", "outcome"), [
    ([], "checks-pending"),                                                       # missing
    ([check_run("ci", None, status="in_progress")], "checks-pending"),           # pending
    ([check_run("ci", None, status="queued")], "checks-pending"),
    ([check_run("lint", "success")], "checks-pending"),                           # another name only: ci is missing
    ([check_run("ci", "failure")], "checks-failed"),
    ([check_run("ci", "cancelled")], "checks-failed"),
    ([check_run("ci", "success")], "completed"),
    ([check_run("ci", "neutral")], "completed"),
    ([check_run("ci", "skipped")], "completed"),
])
def test_f5_a_missing_pending_or_failed_required_check_is_never_a_pass(rrig: Rig, launchers: Any, github_origin: Path,
                                                                      runs: list[dict[str, Any]], outcome: str):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    required(rrig, "ci")
    fake.checks[job.source["sha"]] = runs
    assert merge(rrig, job.job_id).outcome == outcome
    assert len(fake.merge_requests) == (1 if outcome == "completed" else 0)


def test_f5_with_no_check_configured_nothing_is_claimed_even_when_github_lists_conflicts(rrig: Rig, launchers: Any,
                                                                                        github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    fake.checks[job.source["sha"]] = [check_run("ci", "success", ident=7), check_run("ci", "failure", ident=7)]
    report = merge(rrig, job.job_id)                          # not required: the listing is not even read
    assert report.outcome == "completed"
    assert [c["status"] for c in report.checks if c["check"].startswith("required-check")] == ["not-configured"]


def test_f5_conflicting_post_merge_observations_keep_the_job_merged_never_completed(rrig: Rig, launchers: Any,
                                                                                   github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    with_integration(rrig, post_merge_checks=("ci",))
    with pytest.raises(SimulatedCrash):
        merge(rrig, job.job_id, faults=("after-merged-record",))
    commit = rrig.jobs.load(job.job_id).merge_result["commit_sha"]
    fake.checks[commit] = [check_run("ci", "success", ident=7), check_run("ci", "failure", ident=7)]
    report = merge(rrig, job.job_id)
    assert report.outcome == "pending-verification", (report.outcome, report.reasons)
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.MERGED and stored.merge_result["verified"] is False
    assert any(c["name"] == "check ci" and c["status"] == "unavailable"
               for c in stored.merge_result["verification"]["checks"])
    fake.checks[commit] = [check_run("ci", "success", ident=7)]
    assert merge(rrig, job.job_id).outcome == "completed"


def test_f5_unit_the_evaluation_and_the_listing_checks():
    ok = [CheckObservation("ci", "pass", "check-run", 7)]
    assert [r["status"] for r in evaluate_checks(["ci"], ok)] == ["pass"]
    assert [r["status"] for r in evaluate_checks([], ok)] == ["not-configured"]
    assert [r["status"] for r in evaluate_checks(["ci"], [])] == ["pending"]
    assert [r["status"] for r in evaluate_checks(["ci"], None, unavailable="x")] == ["unavailable"]

    class Response:  # pylint: disable=too-few-public-methods
        """A GitHub answer: its decoded body and headers."""

        def __init__(self, data: Any) -> None:
            self.data, self.headers = data, {}

    class Client:  # pylint: disable=too-few-public-methods
        """A minimal live client: one page of check runs, no statuses."""

        live = True

        def __init__(self, runs: list[dict[str, Any]]) -> None:
            self.runs = runs

        def _call(self, method: str, path: str, **_kw: Any) -> Any:
            return Response({"total_count": len(self.runs), "check_runs": self.runs} if "check-runs" in path else [])
    good = [check_run("ci", "success", ident=7), check_run("lint", "success", ident=8)]
    assert len(check_observations(Client(good), "o/r", "a" * 40, max_pages=3)) == 2
    for bad in ([check_run("ci", "success", ident=7)] * 2,
                [check_run("ci", "success", ident=7), check_run("ci", "failure", ident=7)]):
        with pytest.raises(GitHubResponseError, match="not a faithful enumeration"):
            check_observations(Client(bad), "o/r", "a" * 40, max_pages=3)


# -- F6: COMPLETED needs the complete verification evidence -------------------------------------------------------

def mutate_checks(change: Callable[[list[dict[str, Any]]], None]) -> Callable[[dict[str, Any]], None]:
    return lambda result: change(result["verification"]["checks"])


def remove(name: str) -> Callable[[list[dict[str, Any]]], None]:
    return lambda checks: checks.__setitem__(slice(None), [c for c in checks if c["name"] != name])


def test_f6_the_completed_fixture_carries_every_core_check_once_and_a_policy_entry(rrig: Rig,
                                                                                  completed: IntegrationJob):
    checks = completed.merge_result["verification"]["checks"]
    assert sorted(c["name"] for c in checks if c["name"] in CORE_CHECKS) == sorted(CORE_CHECKS)
    assert [c["status"] for c in checks if c["name"] == "checks"] == ["not-configured"]
    assert not completion_problems(checks)


@pytest.mark.parametrize("name", list(CORE_CHECKS))
def test_f6_removing_any_core_check_makes_a_completed_record_invalid(rrig: Rig, completed: IntegrationJob, name: str):
    Doc(rrig, completed.job_id).check(lambda d: mutate_checks(remove(name))(d["merge_result"]),
                                      f"core check '{name}' must appear exactly once")


def test_f6_the_original_reproduction_an_emptied_check_list_does_not_complete_a_job(rrig: Rig, launchers: Any,
                                                                                   github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    assert merge(rrig, job.job_id).outcome == "completed"
    data = rrig.jobs.load(job.job_id).to_dict()
    data["merge_result"]["verification"]["checks"] = []
    with pytest.raises(Exception, match="core check 'pr-merged' must appear exactly once"):
        IntegrationJob.from_dict(data)


def test_f6_pending_checks_cannot_be_erased_to_fabricate_a_completed_job(rrig: Rig, launchers: Any,
                                                                       github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    with_integration(rrig, post_merge_checks=("ci",))
    assert merge(rrig, job.job_id).outcome == "pending-verification"
    stored = rrig.jobs.load(job.job_id)
    assert stored.state is JobState.MERGED and any(c["status"] == "pending"
                                                   for c in stored.merge_result["verification"]["checks"])
    stored.merge_result["verified"] = True
    stored.merge_result["verification"]["state"] = "verified"
    stored.merge_result["verification"]["checks"] = []
    transition_job(stored, JobState.COMPLETED, "fabricated verification", actor="operator")
    with pytest.raises(Exception, match="complete post-merge verification"):
        IntegrationJob.from_dict(stored.to_dict())


COMPLETED_FABRICATIONS: list[tuple[str, Callable[[list[dict[str, Any]]], None], str]] = [
    ("empty", lambda checks: checks.clear(), "must appear exactly once"),
    ("duplicate-core-check", lambda checks: checks.append(copy.deepcopy(checks[0])), "exactly once"),
    ("no-pr-verification", remove("pr-merged"), "'pr-merged' must appear exactly once"),
    ("no-parent-verification", remove("parents"), "'parents' must appear exactly once"),
    ("no-tree-verification", remove("tree"), "'tree' must appear exactly once"),
    ("no-containment-verification", remove("dev-contains"), "'dev-contains' must appear exactly once"),
    ("no-commit-verification", remove("commit"), "'commit' must appear exactly once"),
    ("no-agreement-verification", remove("agreement"), "'agreement' must appear exactly once"),
    ("no-check-policy-evidence", remove("checks"), "check policy must be evaluated"),
    ("policy-both-ways", lambda checks: checks.append({"name": "check ci", "status": "pass", "detail": "ok"}),
     "check policy must be evaluated"),
    ("core-check-not-configured", lambda checks: checks[0].update(status="not-configured"), "must pass"),
    ("core-check-pending", lambda checks: checks[0].update(status="pending"), "must pass"),
    ("policy-check-failed", lambda checks: [c.update(status="fail") for c in checks if c["name"] == "checks"],
     "must be not-configured"),
    ("policy-check-pending", lambda checks: [c.update(status="pending") for c in checks if c["name"] == "checks"],
     "must be not-configured"),
    ("unknown-check", lambda checks: checks.append({"name": "mystery", "status": "pass", "detail": "x"}),
     "unknown verification checks"),
]


@pytest.mark.parametrize(("change", "match"), [(c, m) for _n, c, m in COMPLETED_FABRICATIONS],
                         ids=[n for n, _c, _m in COMPLETED_FABRICATIONS])
def test_f6_a_structurally_valid_but_incomplete_completed_record_is_rejected(
        rrig: Rig, completed: IntegrationJob, change: Callable[[list[dict[str, Any]]], None], match: str):
    """The digests inside the record are all consistent (the evidence entries are untouched); only the completion
    evidence is incomplete — the schema alone refuses it."""
    Doc(rrig, completed.job_id).check(lambda d: mutate_checks(change)(d["merge_result"]), match)


def test_f6_a_legitimate_no_ci_completion_a_legitimate_ci_completion_and_a_pending_job_all_load(
        rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    existing_pr(fake, job)
    with_integration(rrig, post_merge_checks=("ci",))
    assert merge(rrig, job.job_id).outcome == "pending-verification"          # pending CI: MERGED, loads
    pending = rrig.jobs.load(job.job_id)
    assert pending.state is JobState.MERGED and IntegrationJob.from_dict(pending.to_dict()).state is JobState.MERGED
    fake.checks[pending.merge_result["commit_sha"]] = [check_run("ci", "success", ident=3)]
    assert merge(rrig, job.job_id).outcome == "completed"                       # legitimate CI completion
    done = IntegrationJob.from_dict(rrig.jobs.load(job.job_id).to_dict())
    names = [c["name"] for c in done.merge_result["verification"]["checks"]]
    assert sorted(n for n in names if n in CORE_CHECKS) == sorted(CORE_CHECKS) and "check ci" in names
    assert "checks" not in names and not completion_problems(done.merge_result["verification"]["checks"])


def test_f6_historical_records_without_a_merge_stage_still_load(rrig: Rig, launchers: Any, github_origin: Path):
    """M1–M4 records (no merge_result, no merge evidence) are untouched by the completion rule."""
    job, _fake = ready_job(rrig, launchers, github_origin)                      # REVIEWED → AWAITING_MERGE_APPROVAL
    assert job.merge_result is None
    assert IntegrationJob.from_dict(job.to_dict()).state is JobState.AWAITING_MERGE_APPROVAL
    assert completion_problems([])                                             # only a COMPLETED job is asked for it
