"""W4.7 (M5), the focused Codex re-review: the four remaining blockers of the integration WRITE BOUNDARY
(integration_authority.py, github_api.GitHubClient, integration_merge.py, integration_merge_schema.py).

F1 — a caller-controlled authority: `integration_writes(<any WriteAuthority subclass>)` let a caller approve every write
     (fabricated job and attempt IDs, no signed approval, no intent, no dispatch). The client now accepts no authority
     at all: it builds the concrete one from trust anchors bound once by `integration merge`.
F2 — configuration changes DURING the boundary's own verification: the policy was read once, before the slow signature
     checks; operators removed meanwhile still merged. One snapshot now decides, and the configuration must still be
     that snapshot inside the dispatch transaction.
F3 — an unresolved PR creation forgotten across jobs: after an abort, a successor for the same source created a second
     PR. Other jobs' creation history for the branch now blocks a creation.
F7 — the current path and required-check policy at dispatch: a path newly protected, or a new required check without a
     result, still merged. The boundary now classifies the candidate's paths and observes the required checks itself,
     under the same snapshot.

Each test reproduces the original incorrect outcome's setup and asserts the corrected one, next to the legitimate
path. Disposable checkout/origin, throw-away operator keys and `FakeMergeGitHub` (real merges on the bare origin only):
no network, no real GitHub, no model."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument,too-many-lines
# pylint: disable=too-many-arguments,too-many-locals

from __future__ import annotations

import dataclasses
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import pytest
import yaml

from feature_coordinator import github_commands, integration_authority, integration_merge
from feature_coordinator.cli import main as cli_main
from feature_coordinator.config import load_config
from feature_coordinator.errors import IntegrationWriteRefused
from feature_coordinator.github_api import ApiResponse, TransportFailure
from feature_coordinator.integration import IntakeRequest, IntegrationError, IntegrationJobs, JobState, abort_job, \
    effective_approval
from feature_coordinator.integration_approval import SshSignatureVerifier
from feature_coordinator.integration_authority import IntegrationAnchors, IntegrationWriteAuthority, \
    configuration_fingerprint
from feature_coordinator.integration_candidate import CandidateArea
from feature_coordinator.integration_intake import IntegrationIntake
from feature_coordinator.integration_merge_schema import attempt_state, creation_history_problems, merge_attempts
from feature_coordinator.store import StateStore

from fc_integration_merge_rig import (
    FakeMergeGitHub, anchors_for, approve_merge, bare, bind, github_for, merge, merge_record_for, ready_job,
)
from fc_integration_rig import (  # pylint: disable=unused-import
    BRANCH, REPOSITORY, Rig, change_on, github_origin, live_client, origin, request_for,
)
from test_fc_integration_merge import attention, check_run, existing_pr, required
from test_fc_integration_review import kinds, launchers, of_kind, review, rrig  # pylint: disable=unused-import
from test_fc_integration_validate import (  # pylint: disable=unused-import
    X_PY, FakeGates, key, rig, seeded, tracker, with_config, with_integration,
)

LATER = timedelta(seconds=121)
ACTIONS = ["create-pr", "merge"]


def dispatches(rig: Rig, job_id: str) -> list[dict[str, Any]]:
    return of_kind(rig, job_id, "merge-dispatch")


def prepared(rig: Rig, launchers: Any, origin_path: Path, action: str, *, two_keys: bool = False,
             **kwargs: Any) -> tuple[Any, FakeMergeGitHub]:
    """A signed job about to make *action*: a PR exists for `merge`, none for `create-pr`. *two_keys*: the merge
    approval is signed by a SECOND key (the inspect approval and the merge approval rest on different keys)."""
    if two_keys:
        job, fake = ready_job(rig, launchers, origin_path, sign=False, **kwargs)
        second = rig.key.new_key("spyderbg")
        path = rig.write_record(merge_record_for(job), key=second)
        assert approve_merge(rig, job.job_id, path=path)["outcome"] == "recorded"
        job = rig.jobs.load(job.job_id)
    else:
        job, fake = ready_job(rig, launchers, origin_path, **kwargs)
    if action == "merge":
        existing_pr(fake, job)
    return job, fake


def at_boundary(monkeypatch: pytest.MonkeyPatch, operation: str, mutate: Callable[[], None]) -> list[int]:
    """`mutate()` once, when the write boundary is asked about *operation* — after every check of the workflow itself,
    before the boundary reads the configuration (the original F7 reproduction's point)."""
    done: list[int] = []
    real = IntegrationWriteAuthority.authorize

    def authorize(self: Any, op: str, **facts: Any) -> None:
        if op == operation and not done:
            mutate()
            done.append(1)
        real(self, op, **facts)
    monkeypatch.setattr(IntegrationWriteAuthority, "authorize", authorize)
    return done


def two_lines(rig: Rig) -> list[str]:
    lines = rig.key.allowed.read_text().splitlines()
    assert len(lines) == 2
    return lines


# == F1: no caller-controlled authority ===============================================================================

def test_f1_the_original_exploit_a_malicious_authority_subclass_reaches_no_transport(
        rrig: Rig, launchers: Any, github_origin: Path):
    """The reproduction: real GitHubClient, scripted transport, a WriteAuthority subclass whose authorize() approves
    everything, fabricated job and attempt IDs, no signed merge approval. Every route a caller has is tried."""
    job, fake = ready_job(rrig, launchers, github_origin, sign=False)
    before = bare(github_origin, "rev-parse", "dev").strip()

    class Unauthorized(IntegrationWriteAuthority):  # pylint: disable=too-few-public-methods
        """A caller's 'authority' (the concrete class is the only one left to subclass)."""

        def __init__(self) -> None:  # pylint: disable=super-init-not-called
            pass

        def authorize(self, *args: Any, **kwargs: Any) -> None:  # pylint: disable=arguments-differ
            """Approves everything."""

    client = rrig.client
    assert not hasattr(client, "integration_writes") and not hasattr(client, "transport")
    assert not [name for name in dir(client) if "bind" in name and not name.startswith("_")]   # no public binding
    with pytest.raises(IntegrationWriteRefused, match="must be an IntegrationAnchors"):    # not even the private one
        client._bind_integration(Unauthorized())  # type: ignore[arg-type]
    with pytest.raises(IntegrationWriteRefused, match="no GraphQL merge"):
        client.graphql('mutation { createPullRequest(input: {repositoryId: "R", baseRefName: "dev", headRefName: '
                       '"x", title: "t"}) { clientMutationId } }', mutation=True)
    with pytest.raises(TypeError):
        client.create_integration_pull_request(job.source["ref"], job.base["ref"], "unsigned", "unsigned",  # type: ignore[call-arg]
                                               job_id="int-20990101-9999", attempt="mat-9999", authority=Unauthorized())
    with pytest.raises(IntegrationWriteRefused, match="no integration anchors"):
        client.create_integration_pull_request(job.source["ref"], job.base["ref"], "unsigned", "unsigned",
                                               job_id="int-20990101-9999", attempt="mat-9999")
    bind(rrig)                                     # the coordinator's own anchors: fabricated IDs are still nothing
    with pytest.raises(IntegrationWriteRefused, match="unknown integration job"):
        client.create_integration_pull_request(job.source["ref"], job.base["ref"], "unsigned", "unsigned",
                                               job_id="int-20990101-9999", attempt="mat-9999")
    with pytest.raises(IntegrationWriteRefused, match="unknown integration job"):
        client.merge_integration_pull_request(101, job.source["sha"], job_id="int-20990101-9999", attempt="mat-9998")
    with pytest.raises(IntegrationWriteRefused, match="no effective merge approval|has no merge approval"):
        client.create_integration_pull_request(job.source["ref"], job.base["ref"], "unsigned", "unsigned",
                                               job_id=job.job_id, attempt="mat-9999")       # a real, unsigned job
    assert fake.writes() == [] and not fake.created and not fake.merge_requests            # no POST, no PUT
    assert bare(github_origin, "rev-parse", "dev").strip() == before
    stored = rrig.jobs.load(job.job_id)
    assert not effective_approval(stored, "merge") and not merge_attempts(stored) and not dispatches(rrig, job.job_id)


def test_f1_the_only_client_that_may_write_is_the_one_integration_merge_builds(rrig: Rig, launchers: Any,
                                                                               github_origin: Path):
    """Any other command's client (github_commands._tracker) carries no anchors, so it can send no integration write."""
    import argparse  # pylint: disable=import-outside-toplevel
    job, fake = ready_job(rrig, launchers, github_origin)
    built = github_commands._tracker(argparse.Namespace(tracker="github"), rrig.config, rrig.state)
    assert built.live and not built.integration_bound
    with pytest.raises(IntegrationWriteRefused, match="no integration anchors"):
        built.merge_integration_pull_request(101, job.source["sha"], job_id=job.job_id, attempt="mat-0001")
    with pytest.raises(IntegrationError, match="no integration anchors"):     # the workflow over such a client
        integration_merge.IntegrationMerger(rrig.store, rrig.config, rrig.git, verifier=rrig.verifier,
                                            tracker=lambda: rrig.client).run(job.job_id, apply=True)
    assert fake.writes() == []


def test_f1_the_dispatch_names_the_policy_snapshot_that_authorized_it(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    snapshot = configuration_fingerprint(rrig.config)
    assert [(d["action"], d["policy_sha256"]) for d in dispatches(rrig, job.job_id)] == \
        [("create-pr", snapshot), ("merge", snapshot)]
    assert len(fake.created) == len(fake.merge_requests) == 1


def test_f1_a_forged_job_directory_is_not_the_bound_one(rrig: Rig, launchers: Any, github_origin: Path,
                                                       tmp_path: Path):
    """The anchors decide WHICH state directory's jobs count: a copy of the job (and its intent) somewhere else, even a
    byte-for-byte one, authorizes nothing through the coordinator's client — and that client cannot be re-pointed."""
    job, fake = prepared(rrig, launchers, github_origin, "merge")
    bind(rrig)
    forged = tmp_path / "forged-state"
    shutil.copytree(rrig.state, forged)
    with pytest.raises(IntegrationWriteRefused, match="bound once"):
        rrig.client._bind_integration(IntegrationAnchors(forged, rrig.repo, configuration=lambda: rrig.config))
    forged_jobs = IntegrationJobs(StateStore(forged))
    assert forged_jobs.load(job.job_id).job_id == job.job_id                      # it exists — but not there
    with pytest.raises(IntegrationWriteRefused, match="intent"):
        rrig.client.merge_integration_pull_request(101, job.source["sha"], job_id=job.job_id, attempt="mat-0042")
    assert fake.writes() == []


# == F2: one policy snapshot, still current at the dispatch ==========================================================

PHASES = ["merge-signature", "inspect-signature", "path-classification", "dispatch-record"]
CHANGES = ["operator-removed", "operators-empty", "signers-path-changed", "inspect-key-revoked", "merge-key-revoked",
           "integration-disabled", "unreadable"]


def policy_change(rig: Rig, change: str, tmp: Path) -> Callable[[], None]:
    def signers_moved() -> None:
        copy = tmp / "allowed_signers.copy"
        copy.write_text(rig.key.allowed.read_text())                     # the same keys, another file named
        with_integration(rig, allowed_signers=str(copy))

    def drop(index: int) -> Callable[[], None]:
        def run() -> None:
            lines = two_lines(rig)
            rig.key.allowed.write_text(lines[1 - index] + "\n")
        return run
    return {
        "operator-removed": lambda: with_integration(rig, operators=("someone-else",)),
        "operators-empty": lambda: with_integration(rig, operators=()),
        "signers-path-changed": signers_moved,
        "inspect-key-revoked": drop(0),
        "merge-key-revoked": drop(1),
        "integration-disabled": lambda: with_integration(rig, enabled=False),
        "unreadable": lambda: setattr(rig, "config_unreadable", "project.yaml: [Errno 13] Permission denied"),
    }[change]


def during(monkeypatch: pytest.MonkeyPatch, phase: str, mutate: Callable[[], None]) -> list[int]:
    """`mutate()` once, DURING the write boundary's own verification, at *phase*: right after the merge approval's
    signature was verified, right after the inspect approval's, while the candidate's paths are being derived, or in
    the dispatch transaction before its final consistency checks."""
    done: list[int] = []
    inside: list[int] = []
    real_problems, real_unchanged = IntegrationWriteAuthority._problems, IntegrationWriteAuthority._unchanged
    real_verify, real_changes = SshSignatureVerifier.verify, integration_authority.candidate_changes
    verified: list[int] = []

    def fire() -> None:
        if not done:
            mutate()
            done.append(1)

    def problems(self: Any, *args: Any) -> Any:
        inside.append(1)
        try:
            return real_problems(self, *args)
        finally:
            inside.pop()

    def verify(self: Any, *args: Any, **kwargs: Any) -> Any:
        result = real_verify(self, *args, **kwargs)
        if inside:
            verified.append(1)
            if (phase, len(verified)) in (("merge-signature", 1), ("inspect-signature", 2)):
                fire()
        return result

    def changes(*args: Any, **kwargs: Any) -> Any:
        result = real_changes(*args, **kwargs)
        if phase == "path-classification":
            fire()
        return result

    def unchanged(self: Any, *args: Any) -> Any:
        if phase == "dispatch-record":
            fire()
        return real_unchanged(self, *args)
    monkeypatch.setattr(IntegrationWriteAuthority, "_problems", problems)
    monkeypatch.setattr(IntegrationWriteAuthority, "_unchanged", unchanged)
    monkeypatch.setattr(SshSignatureVerifier, "verify", verify)
    monkeypatch.setattr(integration_authority, "candidate_changes", changes)
    return done


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("phase", PHASES)
@pytest.mark.parametrize("change", CHANGES)
def test_f2_a_policy_change_during_the_boundarys_own_verification_stops_the_write(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
        action: str, phase: str, change: str):
    """The original reproduction (operators removed during the boundary's SSH verification) and its relatives: whatever
    changes, wherever in the boundary, the write is not sent under the older, more permissive policy."""
    job, fake = prepared(rrig, launchers, github_origin, action, two_keys=True)
    done = during(monkeypatch, phase, policy_change(rrig, change, tmp_path))
    report = merge(rrig, job.job_id)
    assert done and report.outcome == "no-approval", (report.outcome, report.reasons)
    assert fake.writes() == [] and not fake.created and not fake.merge_requests            # no POST, no PUT
    assert not dispatches(rrig, job.job_id) and rrig.state_of(job.job_id) is JobState.AWAITING_MERGE_APPROVAL


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("phase", PHASES)
def test_f2_with_the_configuration_unchanged_the_boundary_lets_the_write_through_once(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch, action: str, phase: str):
    job, fake = prepared(rrig, launchers, github_origin, action, two_keys=True)
    done = during(monkeypatch, phase, lambda: None)                       # the same hooks; nothing changes
    report = merge(rrig, job.job_id)
    assert done and report.outcome == "completed", (report.outcome, report.reasons)
    assert len(fake.merge_requests) == 1 and len(fake.created) == (1 if action == "create-pr" else 0)


def test_f2_a_rewrite_of_the_file_with_the_same_policy_is_no_change(rrig: Rig, launchers: Any, github_origin: Path,
                                                                    monkeypatch: pytest.MonkeyPatch):
    """The snapshot is the effective configuration, not the file's bytes or the object's identity: an equal policy
    rebuilt from scratch lets the write through."""
    job, fake = prepared(rrig, launchers, github_origin, "merge")
    done = during(monkeypatch, "dispatch-record", lambda: setattr(rrig, "config", dataclasses.replace(rrig.config)))
    assert merge(rrig, job.job_id).outcome == "completed" and done and len(fake.merge_requests) == 1


def test_f2_the_cli_refuses_when_the_file_changes_during_the_boundarys_signature_check(
        rrig: Rig, launchers: Any, github_origin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]):
    """The original reproduction through the real CLI and the real configuration FILE: the operators are removed from
    the file right after the boundary verified a signature. Before: PR created and merged, COMPLETED. Now: nothing."""
    from test_fc_integration_validate import _cli_config  # pylint: disable=import-outside-toplevel
    config_path = _cli_config(tmp_path, rrig.key)
    rrig.config = load_config(config_path)
    job, fake = ready_job(rrig, launchers, github_origin)
    monkeypatch.setattr(github_commands, "_tracker", lambda *args: rrig.client)

    def empty_the_operators() -> None:
        data = yaml.safe_load(config_path.read_text())
        data["github"]["integration"]["operators"] = []
        config_path.write_text(yaml.safe_dump(data, sort_keys=False))
    done = during(monkeypatch, "merge-signature", empty_the_operators)
    args = ["--repo", str(rrig.repo), "--state-dir", str(rrig.state), "--project-config", str(config_path),
            "--tracker", "github", "integration", "merge", "--job", job.job_id, "--apply", "--json"]
    assert cli_main(args) == 1
    output = json.loads(capsys.readouterr().out)
    assert done and output["outcome"] == "no-approval" and fake.writes() == [], output
    assert "configuration changed" in " ".join(output["reasons"])
    assert not load_config(config_path).github.integration.operators and not dispatches(rrig, job.job_id)


def test_f2_the_cli_without_a_configuration_file_sends_nothing(rrig: Rig, launchers: Any, github_origin: Path,
                                                               monkeypatch: pytest.MonkeyPatch,
                                                               capsys: pytest.CaptureFixture[str]):
    """No --project-config: no anchors are bound (and the built-in default configuration does not enable integration),
    so the command fails before anything is written."""
    job, fake = prepared(rrig, launchers, github_origin, "merge")
    monkeypatch.setattr(github_commands, "_tracker", lambda *args: rrig.client)
    args = ["--repo", str(rrig.repo), "--state-dir", str(rrig.state), "--tracker", "github", "integration", "merge",
            "--job", job.job_id, "--apply"]
    before = rrig.jobs.load(job.job_id).to_dict()
    assert cli_main(args) == 1
    err = capsys.readouterr().err
    assert "enabled is false" in err or "no integration anchors" in err, err
    assert fake.writes() == [] and not rrig.client.integration_bound and rrig.jobs.load(job.job_id).to_dict() == before


def test_f2_a_change_after_the_dispatch_record_applies_to_the_next_write(rrig: Rig, launchers: Any,
                                                                         github_origin: Path,
                                                                         monkeypatch: pytest.MonkeyPatch):
    """The unavoidable race, stated: GitHub is not in the coordinator's transaction. A change after the final re-read
    (here: right after the creation's dispatch was saved) does not reach back into a write already decided under the
    then-current snapshot (recorded in its dispatch) — it decides the NEXT write: the merge is refused."""
    job, fake = ready_job(rrig, launchers, github_origin)
    real_save = IntegrationJobs.save
    done: list[int] = []

    def save(self: Any, current: Any) -> None:
        real_save(self, current)
        last = current.evidence[-1] if current.evidence else {}
        if last.get("kind") == "merge-dispatch" and last.get("action") == "create-pr" and not done:
            with_integration(rrig, operators=())
            done.append(1)
    monkeypatch.setattr(IntegrationJobs, "save", save)
    report = merge(rrig, job.job_id)
    assert done and len(fake.created) == 1 and not fake.merge_requests, (report.outcome, report.reasons)
    assert report.outcome == "no-approval"
    assert [d["action"] for d in dispatches(rrig, job.job_id)] == ["create-pr"]


# == F3: an unresolved PR creation stays relevant across jobs ========================================================

def hide(fake: FakeMergeGitHub, number: int) -> Callable[[], None]:
    """GitHub stops showing PR *number* in every read (the listing, GraphQL and the PR itself): an eventually consistent
    or broken view. Returns the function that shows it again."""
    actual = fake.request

    def request(method: str, path: str, body: Any = None) -> ApiResponse:
        route = urlparse(path).path
        if (method == "GET" and "/pulls" in route) or (method == "POST" and route == "graphql"):
            saved = fake.prs
            fake.prs = [p for p in saved if p["number"] != number]
            try:
                return actual(method, path, body)
            finally:
                fake.prs = saved
        return actual(method, path, body)
    fake.request = request  # type: ignore[method-assign]
    return lambda: setattr(fake, "request", actual)


def unresolved_and_aborted(rig: Rig, launchers: Any, origin_path: Path) -> tuple[Any, FakeMergeGitHub, dict]:
    """Steps 1-6 of the original reproduction: job A creates PR #101, the answer is lost, GitHub does not show #101,
    #101 is closed, more than 120 seconds pass, the operator aborts A."""
    job, fake = ready_job(rig, launchers, origin_path)
    hide(fake, 101)
    fake.lose_create_response = True
    assert merge(rig, job.job_id).outcome == "pr-uncertain" and len(fake.created) == 1
    fake.prs[0]["state"] = "closed"                      # GitHub permits another PR for the same head after closure
    later = datetime.now(timezone.utc) + LATER
    assert merge(rig, job.job_id, clock=lambda: later).outcome == "pr-unresolved"
    assert abort_job(rig.jobs, job.job_id, "stop the unresolved operation", actor="operator", apply=True,
                     now=later)["outcome"] == "aborted"
    attempt = [a for a in merge_attempts(rig.jobs.load(job.job_id)) if a["action"] == "create-pr"][-1]
    return job, fake, attempt


def signed_successor(rig: Rig, launchers: Any, job: Any, *, supersede: bool = True,
                     request: IntakeRequest | None = None) -> str:
    """Step 7: a properly approved job for the same source (the M1-M4 chain and a signed merge approval)."""
    request = request or IntakeRequest(source_repo=job.source["repo"], source_ref=job.source["ref"],
                                       source_sha=job.source["sha"], base_ref=job.base["ref"],
                                       base_sha=job.base["expected_sha"], issue=job.issue)
    report = IntegrationIntake(rig.store, rig.config, rig.git, pr_client=lambda: rig.client).run(
        request, apply=True, supersede=job.job_id if supersede else None)
    assert report.outcome == "accepted", (report.outcome, report.reasons)
    successor = report.job["job_id"]
    assert rig.approve(successor)["outcome"] == "recorded"
    assert rig.inspect(successor).outcome == "passed"
    assert rig.validate(successor, gates=FakeGates()).outcome == "validated"
    assert review(rig, successor, launchers("pass")).outcome == "reviewed"
    assert approve_merge(rig, successor)["outcome"] == "recorded"
    return successor


def test_f3_the_original_sequence_no_second_pr_and_the_blocker_is_reported(rrig: Rig, launchers: Any,
                                                                           github_origin: Path):
    job, fake, attempt = unresolved_and_aborted(rrig, launchers, github_origin)
    successor = signed_successor(rrig, launchers, job)
    report = merge(rrig, successor)
    assert report.outcome == "pr-unresolved", (report.outcome, report.reasons)
    assert len(fake.created) == 1 and [p["number"] for p in fake.prs] == [101]          # no POST for PR #102
    text = " ".join(report.reasons)
    assert job.job_id in text and attempt["id"] in text and "UNKNOWN" in text            # the blocker, by name
    assert job.job_id in attention(rrig, successor)
    stored = rrig.jobs.load(job.job_id)                     # the original operation is still visible as unresolved
    assert stored.state is JobState.ABORTED and attempt_state(stored, attempt) == "dispatched"
    assert not dispatches(rrig, successor)


def test_f3_the_blocker_survives_a_restart(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake, _attempt = unresolved_and_aborted(rrig, launchers, github_origin)
    successor = signed_successor(rrig, launchers, job)
    assert merge(rrig, successor).outcome == "pr-unresolved"
    fresh = IntegrationJobs(StateStore(rrig.state))                       # a new process: only what is on disk
    assert creation_history_problems(fresh.list(), fresh.load(successor))
    assert merge(rrig, successor).outcome == "pr-unresolved" and len(fake.created) == 1


def test_f3_a_newer_commit_on_the_same_branch_is_blocked_too(rrig: Rig, launchers: Any, github_origin: Path):
    """A PR follows its branch, not a commit: another job for a newer SHA of the same branch would duplicate it."""
    job, fake, _attempt = unresolved_and_aborted(rrig, launchers, github_origin)
    sha = change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY + "\nY = 2\n"})
    newer = signed_successor(rrig, launchers, job, supersede=False, request=request_for(rrig.repo, sha))
    report = merge(rrig, newer)
    assert report.outcome == "pr-unresolved" and job.job_id in " ".join(report.reasons), report.reasons
    assert len(fake.created) == 1


def test_f3_an_unrelated_branch_is_not_blocked(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake, _attempt = unresolved_and_aborted(rrig, launchers, github_origin)
    sha = change_on(rrig.repo, "integration/other", {"backend/src/other.py": '"""O."""\nO = 1\n'})
    other = signed_successor(rrig, launchers, job, supersede=False,
                             request=request_for(rrig.repo, sha, source_ref="integration/other"))
    report = merge(rrig, other)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert [c["head"] for c in fake.created] == [BRANCH, "integration/other"]


def test_f3_the_operator_resolves_it_by_opening_the_pr_and_no_post_is_ever_repeated(rrig: Rig, launchers: Any,
                                                                                    github_origin: Path):
    """Reliable resolution is an OBSERVED PR, never a timer or an abort: once GitHub shows an open PR for the branch
    (the operator opened it by hand), the successor reuses it — still without a second creation."""
    job, fake, _attempt = unresolved_and_aborted(rrig, launchers, github_origin)
    successor = signed_successor(rrig, launchers, job)
    assert merge(rrig, successor).outcome == "pr-unresolved"
    existing_pr(fake, rrig.jobs.load(successor), number=102)                # opened by the operator on GitHub
    report = merge(rrig, successor)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert len(fake.created) == 1 and fake.merge_requests[0]["number"] == 102


def test_f3_a_creation_github_rejected_does_not_block_a_successor(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.create_failure = ApiResponse(422, {}, {"message": "Validation Failed"})
    assert merge(rrig, job.job_id).outcome == "github-refused"
    closed = of_kind(rrig, job.job_id, "merge-outcome")
    assert [(o["status"], o["basis"]) for o in closed] == [("not-created", "rejected")]
    assert abort_job(rrig.jobs, job.job_id, "retry", actor="operator", apply=True)["outcome"] == "aborted"
    successor = signed_successor(rrig, launchers, job)
    report = merge(rrig, successor)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert len(fake.created) == 2 and len(fake.prs) == 1                  # the first POST created nothing


def test_f3_a_creation_that_never_left_does_not_block_a_successor(rrig: Rig, launchers: Any, github_origin: Path):
    job, fake = ready_job(rrig, launchers, github_origin)
    fake.create_failure = TransportFailure("connection refused", maybe_applied=False)
    merge(rrig, job.job_id)
    assert [(o["status"], o["basis"]) for o in of_kind(rrig, job.job_id, "merge-outcome")] == \
        [("not-created", "not-sent")]
    assert abort_job(rrig.jobs, job.job_id, "retry", actor="operator", apply=True)["outcome"] == "aborted"
    successor = signed_successor(rrig, launchers, job)
    assert merge(rrig, successor).outcome == "completed" and len(fake.prs) == 1


def test_f3_a_reconciled_creation_is_reused_and_a_hidden_one_blocks(rrig: Rig, launchers: Any, github_origin: Path):
    """Job A created and recorded PR #101 (a failing required check stopped the merge), then A was aborted. A successor
    that GitHub does not show #101 to creates nothing (A's record says the PR exists); once GitHub shows it again, the
    successor reuses it."""
    job, fake = ready_job(rrig, launchers, github_origin)
    required(rrig, "ci")
    fake.checks[job.source["sha"]] = [check_run("ci", "failure")]
    assert merge(rrig, job.job_id).outcome == "checks-failed"
    assert [e["origin"] for e in of_kind(rrig, job.job_id, "merge-pr")] == ["created"]
    assert abort_job(rrig.jobs, job.job_id, "retry", actor="operator", apply=True)["outcome"] == "aborted"
    show = hide(fake, 101)
    successor = signed_successor(rrig, launchers, job)
    blocked = merge(rrig, successor)
    assert blocked.outcome == "pr-unresolved" and "recorded PR #101" in " ".join(blocked.reasons), blocked.reasons
    assert len(fake.created) == 1
    show()
    fake.checks[job.source["sha"]] = [check_run("ci", "success", ident=2)]
    report = merge(rrig, successor)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert len(fake.created) == 1 and fake.merge_requests[-1]["number"] == 101


def test_f3_concurrent_successors_one_job_and_the_boundary_rescans_inside_its_transaction(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch):
    """Two successors cannot both exist (intake returns the one that does); and a creation dispatched by another job
    AFTER the earlier scans (simulated: the workflow's scan and the boundary's first scan see nothing yet) is still
    found by the scan inside the dispatch transaction — the store-wide lock serializes it."""
    job, fake, attempt = unresolved_and_aborted(rrig, launchers, github_origin)
    successor = signed_successor(rrig, launchers, job)
    request = IntakeRequest(source_repo=job.source["repo"], source_ref=job.source["ref"], source_sha=job.source["sha"],
                            base_ref=job.base["ref"], base_sha=job.base["expected_sha"], issue=job.issue)
    again = IntegrationIntake(rrig.store, rrig.config, rrig.git, pr_client=lambda: rrig.client).run(
        request, apply=True, supersede=job.job_id)
    assert again.job["job_id"] == successor                                      # no second successor
    monkeypatch.setattr(integration_merge, "creation_history_problems", lambda *args: [])
    real = integration_authority.creation_history_problems
    calls: list[int] = []

    def scan(*args: Any) -> list[str]:
        calls.append(1)
        return [] if len(calls) == 1 else real(*args)
    monkeypatch.setattr(integration_authority, "creation_history_problems", scan)
    report = merge(rrig, successor)
    assert report.outcome == "no-approval" and attempt["id"] in " ".join(report.reasons), report.reasons
    assert len(calls) == 2 and len(fake.created) == 1 and not dispatches(rrig, successor)


def test_f3_creation_history_rules(rrig: Rig, launchers: Any, github_origin: Path):
    """The cross-job rule itself: dispatched-and-unknown and recorded PRs block, definite failures do not, another
    branch is unrelated, the job's own history is the job's own business, the source repository matches in any
    letter case, and the target repository is not part of the identity (review finding 5: a renamed or transferred
    repository keeps its PRs)."""
    job, _fake, _attempt = unresolved_and_aborted(rrig, launchers, github_origin)
    stored = rrig.jobs.load(job.job_id)
    me = dataclasses.replace(stored, job_id="int-20990101-0001")
    assert creation_history_problems([stored], me)
    assert not creation_history_problems([stored], stored)                               # its own: handled in-job
    other_branch = dataclasses.replace(me, source={**me.source, "ref": "integration/other"})
    assert not creation_history_problems([stored], other_branch)
    upper = dataclasses.replace(me, source={**me.source, "repo": me.source["repo"].upper()})
    assert creation_history_problems([stored], upper)                                     # repository names: any case


# == F7: the current path and required-check policy at dispatch ======================================================

def protect(rig: Rig, *patterns: str) -> None:
    rig.config = dataclasses.replace(rig.config, protected=(*rig.config.protected, *patterns))


def approval_required(rig: Rig, *patterns: str) -> None:
    rig.config = dataclasses.replace(rig.config, approval_required=(*rig.config.approval_required, *patterns))


def without_backend_ownership(rig: Rig) -> None:
    roles = dict(rig.config.roles)
    roles["backend"] = dataclasses.replace(roles["backend"], owned=("docs_agents/backend/**",))
    rig.config = dataclasses.replace(rig.config, roles=roles)


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("policy", ["exact-path", "directory-pattern", "glob-pattern"])
def test_f7_a_candidate_path_newly_protected_blocks_the_write(rrig: Rig, launchers: Any, github_origin: Path,
                                                              monkeypatch: pytest.MonkeyPatch, action: str,
                                                              policy: str):
    """The original reproduction (defect A): the configuration classifies a candidate path as protected after the
    approval. Before: merged, COMPLETED. Now: no request."""
    job, fake = prepared(rrig, launchers, github_origin, action)
    pattern = {"exact-path": "backend/src/x.py", "directory-pattern": "backend/src/**", "glob-pattern": "**/x.py"}
    done = at_boundary(monkeypatch, action, lambda: protect(rrig, pattern[policy]))
    report = merge(rrig, job.job_id)
    assert done and report.outcome == "no-approval", (report.outcome, report.reasons)
    assert "backend/src/x.py is protected" in " ".join(report.reasons)
    assert fake.writes() == [] and not dispatches(rrig, job.job_id)


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("change", ["approval-required", "role-removed"])
def test_f7_a_path_that_newly_needs_an_authorization_blocks_the_write(rrig: Rig, launchers: Any, github_origin: Path,
                                                                      monkeypatch: pytest.MonkeyPatch, action: str,
                                                                      change: str):
    job, fake = prepared(rrig, launchers, github_origin, action)
    mutate = {"approval-required": lambda: approval_required(rrig, "backend/src/**"),
              "role-removed": lambda: without_backend_ownership(rrig)}[change]
    done = at_boundary(monkeypatch, action, mutate)
    report = merge(rrig, job.job_id)
    assert done and report.outcome == "no-approval", (report.outcome, report.reasons)
    assert "needs an explicit per-path authorization" in " ".join(report.reasons) or \
        "validation" in " ".join(report.reasons), report.reasons
    assert fake.writes() == [] and not dispatches(rrig, job.job_id)


def test_f7_an_approval_required_path_the_inspect_approval_authorized_still_passes(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch):
    """The inspect approval named the path explicitly: newly approval-required, it is still authorized."""
    job = rrig.approved(request_for(rrig.repo, change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY})),
                        paths=["backend/src/x.py"])
    assert rrig.inspect(job.job_id).outcome == "passed"
    assert rrig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    assert review(rrig, job.job_id, launchers("pass")).outcome == "reviewed"
    github_for(rrig, github_origin)
    assert approve_merge(rrig, job.job_id)["outcome"] == "recorded"
    assert "backend/src/x.py" in effective_approval(rrig.jobs.load(job.job_id), "inspect")["paths"]
    done = at_boundary(monkeypatch, "create-pr", lambda: approval_required(rrig, "backend/src/**"))
    report = merge(rrig, job.job_id)
    assert done and report.outcome == "completed", (report.outcome, report.reasons)


def test_f7_a_path_only_in_the_source_history_newly_protected_blocks_the_write(rrig: Rig, launchers: Any,
                                                                               github_origin: Path,
                                                                               monkeypatch: pytest.MonkeyPatch):
    """A file added and removed again inside the source range is not in the candidate's change — but the merge brings
    those commits into the base's history (M2's history-wide path list). Newly protected, it blocks too."""
    change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY, "backend/src/scratch.py": '"""S."""\nS = 1\n'})
    sha = change_on(rrig.repo, BRANCH, {"backend/src/scratch.py": None})
    job = rrig.approved(request_for(rrig.repo, sha))
    assert rrig.inspect(job.job_id).outcome == "passed"
    assert "backend/src/scratch.py" in [e["path"] for e in rrig.jobs.load(job.job_id).paths]
    assert rrig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    assert "backend/src/scratch.py" not in [e["path"] for e in rrig.jobs.load(job.job_id).candidate["paths"]]
    assert review(rrig, job.job_id, launchers("pass")).outcome == "reviewed"
    fake = github_for(rrig, github_origin)
    assert approve_merge(rrig, job.job_id)["outcome"] == "recorded"
    done = at_boundary(monkeypatch, "create-pr", lambda: protect(rrig, "backend/src/scratch.py"))
    report = merge(rrig, job.job_id)
    assert done and report.outcome == "no-approval", (report.outcome, report.reasons)
    assert "source history path" in " ".join(report.reasons) and fake.writes() == []


def test_f7_the_boundary_derives_the_paths_itself_a_missing_candidate_repository_refuses(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch):
    """The workflow's own rebuild is not trusted for the classification: the boundary reads the candidate's change
    from the job's candidate repository (hash-verified) — without it there is no write."""
    job, fake = prepared(rrig, launchers, github_origin, "merge")
    area = CandidateArea(rrig.state, job.job_id)
    done = at_boundary(monkeypatch, "merge", lambda: shutil.rmtree(area.repo))
    report = merge(rrig, job.job_id)
    assert done and report.outcome == "no-approval" and "candidate repository" in " ".join(report.reasons)
    assert fake.writes() == []


def test_f7_a_new_required_check_without_a_result_blocks_the_merge(rrig: Rig, launchers: Any, github_origin: Path,
                                                                   monkeypatch: pytest.MonkeyPatch):
    """The original reproduction (defect B): a required check added after the workflow's own checks. Before: merged,
    COMPLETED with `not-configured`. Now: no merge request."""
    job, fake = prepared(rrig, launchers, github_origin, "merge")
    done = at_boundary(monkeypatch, "merge", lambda: required(rrig, "new-required-ci"))
    report = merge(rrig, job.job_id)
    assert done and report.outcome == "no-approval", (report.outcome, report.reasons)
    assert "required check new-required-ci is pending" in " ".join(report.reasons)
    assert not fake.merge_requests and not dispatches(rrig, job.job_id)


CHECK_CASES: list[tuple[str, list[dict[str, Any]], tuple[str, ...], str]] = [
    ("renamed", [check_run("ci", "success")], ("ci-2",), "ci-2 is pending"),
    ("pending", [check_run("ci", None, status="in_progress")], ("ci",), "ci is pending"),
    ("failed", [check_run("ci", "failure")], ("ci",), "ci is fail"),
    ("conflicting-duplicates", [check_run("ci", "success", ident=7), check_run("ci", "failure", ident=7)], ("ci",),
     "ci is unavailable"),
]


@pytest.mark.parametrize(("name", "runs", "names", "match"), CHECK_CASES, ids=[c[0] for c in CHECK_CASES])
def test_f7_the_required_checks_are_observed_again_at_the_boundary(rrig: Rig, launchers: Any, github_origin: Path,
                                                                   monkeypatch: pytest.MonkeyPatch, name: str,
                                                                   runs: list[dict[str, Any]], names: tuple[str, ...],
                                                                   match: str):
    """The workflow saw `ci` succeed; at the boundary the configuration (renamed) or GitHub (pending, failed,
    contradictory) says otherwise: no merge request."""
    job, fake = prepared(rrig, launchers, github_origin, "merge")
    required(rrig, "ci")
    fake.checks[job.source["sha"]] = [check_run("ci", "success")]

    def change() -> None:
        required(rrig, *names)
        fake.checks[job.source["sha"]] = runs
    done = at_boundary(monkeypatch, "merge", change)
    report = merge(rrig, job.job_id)
    assert done and report.outcome == "no-approval", (report.outcome, report.reasons)
    assert match in " ".join(report.reasons), report.reasons
    assert not fake.merge_requests and not dispatches(rrig, job.job_id)


def test_f7_successful_required_checks_under_an_unchanged_policy_merge(rrig: Rig, launchers: Any,
                                                                        github_origin: Path):
    job, fake = prepared(rrig, launchers, github_origin, "merge")
    required(rrig, "ci", "lint")
    fake.checks[job.source["sha"]] = [check_run("ci", "success", ident=1), check_run("lint", "success", ident=2)]
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    reads = [p for m, p in fake.calls if m == "GET" and "/check-runs" in p]
    assert len(reads) >= 2                         # the workflow's read and the boundary's own read


def test_f7_checks_are_not_a_condition_of_opening_the_pr(rrig: Rig, launchers: Any, github_origin: Path):
    """CI usually runs ON the PR: the creation goes ahead with a required check still missing; the merge waits."""
    job, fake = ready_job(rrig, launchers, github_origin)
    required(rrig, "ci")
    report = merge(rrig, job.job_id)
    assert report.outcome == "checks-pending" and len(fake.created) == 1 and not fake.merge_requests
    fake.checks[job.source["sha"]] = [check_run("ci", "success")]
    assert merge(rrig, job.job_id).outcome == "completed" and len(fake.created) == 1


def test_f7_a_check_policy_change_during_the_boundarys_check_read_stops_the_merge(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch):
    """A slow check read: the configuration changes while the boundary reads the checks. The snapshot it read them
    under is no longer current at the dispatch."""
    job, fake = prepared(rrig, launchers, github_origin, "merge")
    required(rrig, "ci")
    fake.checks[job.source["sha"]] = [check_run("ci", "success")]
    real = integration_authority.check_observations
    done: list[int] = []

    def observing(*args: Any, **kwargs: Any) -> Any:
        found = real(*args, **kwargs)
        if not done:
            required(rrig, "ci", "security-scan")
            done.append(1)
        return found
    monkeypatch.setattr(integration_authority, "check_observations", observing)
    report = merge(rrig, job.job_id)
    assert done and report.outcome == "no-approval", (report.outcome, report.reasons)
    assert "configuration changed" in " ".join(report.reasons) and not fake.merge_requests


def test_f7_an_unchanged_policy_merges_normally_with_the_boundary_doing_its_own_checks(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch):
    job, fake = ready_job(rrig, launchers, github_origin)
    calls: list[str] = []
    real = integration_authority.candidate_changes

    def changes(*args: Any, **kwargs: Any) -> Any:
        calls.append("paths")
        return real(*args, **kwargs)
    monkeypatch.setattr(integration_authority, "candidate_changes", changes)
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert calls == ["paths", "paths"] and len(fake.created) == len(fake.merge_requests) == 1


def test_f7_the_anchors_configuration_is_what_the_boundary_classifies_under(rrig: Rig, launchers: Any,
                                                                            github_origin: Path):
    """The workflow ran under one configuration object, the boundary reads the anchored source: when they differ (here:
    the source already protects the path when the run starts), the anchored source decides."""
    job, fake = prepared(rrig, launchers, github_origin, "merge")
    stale = rrig.config
    protect(rrig, "backend/src/x.py")
    runner = integration_merge.IntegrationMerger(rrig.store, stale, rrig.git, verifier=rrig.verifier,
                                                 tracker=lambda: rrig.client, sleep=lambda _s: None)
    bind(rrig)
    report = runner.run(job.job_id, apply=True)
    assert report.outcome == "no-approval" and "protected" in " ".join(report.reasons), report.reasons
    assert fake.writes() == []
    assert anchors_for(rrig).configuration() is rrig.config


def test_review_r2_the_merge_command_refuses_a_client_that_already_carries_anchors(
        rrig: Rig, launchers: Any, github_origin: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str]):
    """Finding 4: `integration merge` bound its anchors only `if not client.integration_bound`, so a client already
    bound elsewhere would have been used as it was. It now binds its own, once per run, and refuses such a client."""
    from test_fc_integration_validate import _cli_config  # pylint: disable=import-outside-toplevel
    config_path = _cli_config(tmp_path, rrig.key)
    rrig.config = load_config(config_path)
    job, fake = prepared(rrig, launchers, github_origin, "merge")
    rrig.client._bind_integration(IntegrationAnchors(tmp_path / "elsewhere", rrig.repo,
                                                     configuration=lambda: rrig.config))
    monkeypatch.setattr(github_commands, "_tracker", lambda *args: rrig.client)
    args = ["--repo", str(rrig.repo), "--state-dir", str(rrig.state), "--project-config", str(config_path),
            "--tracker", "github", "integration", "merge", "--job", job.job_id, "--apply"]
    assert cli_main(args) == 1
    assert "never replaced" in capsys.readouterr().err
    assert fake.writes() == [] and not dispatches(rrig, job.job_id)


def test_review_r2_a_shifty_pr_number_cannot_redirect_an_authorized_merge(rrig: Rig, launchers: Any,
                                                                          github_origin: Path):
    """Finding 3, end to end: with an open merge intent for PR #101, an int that compares equal to 101 but is 555 is
    refused before the boundary — no dispatch is consumed, no PUT is sent; the real intent still merges #101 once."""
    from test_fc_integration_merge import _Shifty  # pylint: disable=import-outside-toplevel
    from feature_coordinator.issue_sync import SimulatedCrash  # pylint: disable=import-outside-toplevel
    job, fake = prepared(rrig, launchers, github_origin, "merge")
    with pytest.raises(SimulatedCrash):
        merge(rrig, job.job_id, faults=("before-merge-request",))
    intent = [a for a in merge_attempts(rrig.jobs.load(job.job_id)) if a["action"] == "merge"][-1]
    with pytest.raises(IntegrationWriteRefused, match="plain int"):
        rrig.client.merge_integration_pull_request(_Shifty(555), job.source["sha"], job_id=job.job_id,
                                                   attempt=intent["id"])
    assert fake.writes() == [] and not dispatches(rrig, job.job_id)
    assert rrig.client.merge_integration_pull_request(101, job.source["sha"], job_id=job.job_id,
                                                      attempt=intent["id"]).merged
    assert [r["number"] for r in fake.merge_requests] == [101]
