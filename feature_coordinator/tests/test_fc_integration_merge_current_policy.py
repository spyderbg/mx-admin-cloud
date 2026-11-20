"""W4.7 (M5), the F7a / F7b re-review: the CURRENT path and role policy at the write boundary.

F7a — a symlink that exists only in the source history (added in one commit, deleted in a later one) pointed to a file
      the current configuration newly protects; the boundary classified M2's recorded history entries, which keep the
      kinds of change and the flags but not the symlink targets, so it merged (COMPLETED). Now the boundary re-derives
      the source history's change from hash-verified objects with M2's own derivation
      (integration_inspect.history_changes) — every target each path was ever set to, read from its blob — and
      classifies it under the policy snapshot.
F7b — a candidate path changed owner (backend → frontend) after validation; the boundary derived the gate plan from the
      role recorded with the candidate under the old configuration, so it merged without the frontend gates. Now the
      plan is derived from the candidate's paths as classified under the snapshot (the current owning roles), and M3's
      validation, M4's review and the merge approval's bound validation must be for exactly that plan.

Disposable checkout/origin, throw-away operator keys, a recording fake gate runner, a fake reviewer process and
`FakeMergeGitHub` behind the real GitHubClient: no network, no real GitHub, no model."""

# pylint: disable=missing-function-docstring,redefined-outer-name,protected-access,unused-argument
# pylint: disable=too-many-arguments,too-many-locals

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any, Callable

import pytest

from feature_coordinator.integration import current_gate_evidence, latest_evidence
from feature_coordinator.integration_gates import gate_plan
from feature_coordinator.integration_review import review_eligibility
from feature_coordinator.integration_validate import current_paths, current_plan_sha256, validated_for

from fc_integration_merge_rig import FakeMergeGitHub, approve_merge, github_for, merge, ready_job
from fc_integration_rig import BRANCH, Rig, change_on, github_origin, head, origin, request_for  # pylint: disable=unused-import
from test_fc_integration_merge import existing_pr
from test_fc_integration_merge_boundary import at_boundary, dispatches, protect
from test_fc_integration_review import launchers, review, rrig  # pylint: disable=unused-import
from test_fc_integration_validate import X_PY, FakeGates, key, rig, seeded, tracker  # pylint: disable=unused-import

ACTIONS = ["create-pr", "merge"]
APP = "backend/src/app.py"           # in the base (seeded), unchanged by the sources below


def link(path: str, target: str) -> Callable[[Path], None]:
    """A change_on `prepare`: (re)create the symlink *path* → *target*."""
    def prepare(repo: Path) -> None:
        where = repo / path
        where.parent.mkdir(parents=True, exist_ok=True)
        if where.is_symlink():
            where.unlink()
        os.symlink(target, where)
    return prepare


def signed(rig: Rig, launchers: Any, origin_path: Path, sha: str, action: str, *,
           paths: tuple[str, ...] = ()) -> tuple[Any, FakeMergeGitHub]:
    """The job for source *sha* driven M1 → M4 (inspect approval naming *paths*) with a signed merge approval; a PR
    exists for `merge`."""
    job = rig.approved(request_for(rig.repo, sha), paths=list(paths))
    report = rig.inspect(job.job_id)
    assert report.outcome == "passed", (report.outcome, report.reasons, report.deferred)
    assert rig.validate(job.job_id, gates=FakeGates()).outcome == "validated"
    assert review(rig, job.job_id, launchers("pass")).outcome == "reviewed"
    fake = github_for(rig, origin_path)
    assert approve_merge(rig, job.job_id)["outcome"] == "recorded"
    job = rig.jobs.load(job.job_id)
    if action == "merge":
        existing_pr(fake, job)
    return job, fake


def unchanged_evidence(rig: Rig, job: Any) -> list[dict[str, Any]]:
    """The job's evidence up to the merge approval: nothing of it may be reinterpreted, replaced or extended."""
    return [e for e in rig.jobs.load(job.job_id).evidence
            if e["kind"] not in ("merge-attempt", "merge-outcome", "merge-pr")]   # a run's own, not reinterpretations


def refused(rig: Rig, job: Any, fake: FakeMergeGitHub, report: Any, before: list[dict[str, Any]]) -> str:
    """No write, no dispatch, the job still awaiting its merge approval with its evidence intact; the reasons."""
    assert report.outcome == "no-approval", (report.outcome, report.reasons)
    assert fake.writes() == [] and not dispatches(rig, job.job_id)
    assert rig.jobs.load(job.job_id).state.value == "AWAITING_MERGE_APPROVAL"
    assert unchanged_evidence(rig, job) == before
    return " ".join(report.reasons)


# == F7a: historical symlink targets ==================================================================================

@pytest.mark.parametrize("action", ACTIONS)
def test_f7a_a_symlink_only_in_the_source_history_whose_target_becomes_protected_blocks_the_write(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch, action: str):
    """The original reproduction: `backend/src/link.py → app.py` added in commit A (authorized by the inspect
    approval), deleted in commit B; then backend/src/app.py becomes protected. Before: merged, COMPLETED. Now: the
    boundary reads the target from the history's verified objects and refuses; nothing is written."""
    change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY}, prepare=link("backend/src/link.py", "app.py"))
    sha = change_on(rrig.repo, BRANCH, {"backend/src/link.py": None})
    job, fake = signed(rrig, launchers, github_origin, sha, action, paths=("backend/src/link.py",))
    stored = rrig.jobs.load(job.job_id)
    assert "backend/src/link.py" not in [e["path"] for e in stored.candidate["paths"]]
    assert [e["flags"] for e in stored.paths if e["path"] == "backend/src/link.py"] == [["history-only", "symlink"]]
    assert APP not in [e["path"] for e in stored.paths]          # the recorded list does not even name the target
    before = unchanged_evidence(rrig, job)
    done = at_boundary(monkeypatch, action, lambda: protect(rrig, APP))
    reasons = refused(rrig, job, fake, merge(rrig, job.job_id), before)
    assert done and "source history path: symlink backend/src/link.py points to the protected path " \
                    "'backend/src/app.py'" in reasons


def test_f7a_a_history_only_symlink_is_judged_under_a_policy_changed_before_the_run_too(rrig: Rig, launchers: Any,
                                                                                         github_origin: Path):
    change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY}, prepare=link("backend/src/link.py", "app.py"))
    sha = change_on(rrig.repo, BRANCH, {"backend/src/link.py": None})
    job, fake = signed(rrig, launchers, github_origin, sha, "create-pr", paths=("backend/src/link.py",))
    before = unchanged_evidence(rrig, job)
    protect(rrig, APP)
    assert "points to the protected path" in refused(rrig, job, fake, merge(rrig, job.job_id), before)


@pytest.mark.parametrize("action", ACTIONS)
def test_f7a_a_symlink_kept_in_the_candidate_whose_target_becomes_protected_blocks_the_write(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch, action: str):
    """The symlink is in the final candidate: both the candidate's change and the history's carry its target."""
    sha = change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY}, prepare=link("backend/src/link.py", "app.py"))
    job, fake = signed(rrig, launchers, github_origin, sha, action, paths=("backend/src/link.py",))
    before = unchanged_evidence(rrig, job)
    done = at_boundary(monkeypatch, action, lambda: protect(rrig, APP))
    reasons = refused(rrig, job, fake, merge(rrig, job.job_id), before)
    assert done and "candidate path: symlink backend/src/link.py points to the protected path" in reasons
    assert "source history path: symlink backend/src/link.py points to the protected path" in reasons


HISTORIES: dict[str, tuple[list[tuple[dict[str, str | None], tuple[str, str] | None]], str]] = {
    # name: ([(edits, symlink (path, target) to create), ...] — commits in order, the inspect approval names every
    #        symlink path; the path the policy newly protects)
    "relative-target-from-a-nested-directory": (
        [({"backend/src/x.py": X_PY}, ("backend/src/sub/link.py", "../app.py")),
         ({"backend/src/sub/link.py": None}, None)], APP),
    "deeply-nested-target-in-a-newly-protected-directory": (
        [({"backend/src/x.py": X_PY, "backend/src/core/settings.py": "S = 1\n"},
          ("backend/src/a/b/link.py", "../../core/settings.py")),
         ({"backend/src/a/b/link.py": None, "backend/src/core/settings.py": None}, None)], "backend/src/core/**"),
    "broken-symlink-whose-target-was-deleted": (
        [({"backend/src/x.py": X_PY, "backend/src/gone.py": "G = 1\n"}, ("backend/src/link.py", "gone.py")),
         ({"backend/src/gone.py": None, "backend/src/link.py": None}, None)], "backend/src/gone.py"),
    "first-of-several-historical-versions": (
        [({"backend/src/x.py": X_PY}, ("backend/src/link.py", "app.py")),
         ({}, ("backend/src/link.py", "old.py")),
         ({"backend/src/link.py": None}, None)], APP),
    "second-of-several-historical-versions": (
        [({"backend/src/x.py": X_PY}, ("backend/src/link.py", "old.py")),
         ({}, ("backend/src/link.py", "app.py")),
         ({"backend/src/link.py": None}, None)], APP),
    "earlier-target-of-a-symlink-kept-with-another-target": (
        [({"backend/src/x.py": X_PY}, ("backend/src/link.py", "app.py")),
         ({}, ("backend/src/link.py", "old.py"))], APP),
}


def build(rig: Rig, commits: list[tuple[dict[str, str | None], tuple[str, str] | None]]) -> tuple[str, list[str]]:
    sha, links = "", []
    for edits, symlink in commits:
        sha = change_on(rig.repo, BRANCH, edits, prepare=link(*symlink) if symlink else None)
        if symlink:
            links.append(symlink[0])
    return sha, sorted(set(links))


@pytest.mark.parametrize("case", sorted(HISTORIES))
def test_f7a_every_target_a_historical_symlink_ever_had_is_judged_under_the_current_policy(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch, case: str):
    """Relative resolution from the symlink's own directory, nested directories, a target deleted later, several
    versions of one symlink, and an earlier target of a symlink the candidate keeps: each one newly protected blocks
    the merge."""
    commits, protected = HISTORIES[case]
    sha, links = build(rrig, commits)
    job, fake = signed(rrig, launchers, github_origin, sha, "merge", paths=tuple(links))
    before = unchanged_evidence(rrig, job)
    done = at_boundary(monkeypatch, "merge", lambda: protect(rrig, protected))
    reasons = refused(rrig, job, fake, merge(rrig, job.job_id), before)
    assert done and f"source history path: symlink {links[0]}" in reasons, reasons


@pytest.mark.parametrize("case", ["target-stays-allowed", "relative-target-resolves-elsewhere", "no-symlink"])
def test_f7a_a_historical_symlink_whose_target_stays_allowed_still_merges(rrig: Rig, launchers: Any,
                                                                          github_origin: Path,
                                                                          monkeypatch: pytest.MonkeyPatch, case: str):
    """The legitimate paths: the policy protects something else; `backend/src/sub/link.py → app.py` resolves to
    backend/src/sub/app.py, not to the newly protected backend/src/app.py; or the source has no symlink at all."""
    if case == "no-symlink":
        sha, links = change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY}), []
    else:
        where = "backend/src/link.py" if case == "target-stays-allowed" else "backend/src/sub/link.py"
        change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY, "backend/src/sub/app.py": "SUB = 1\n"},
                  prepare=link(where, "app.py"))
        sha, links = change_on(rrig.repo, BRANCH, {where: None}), [where]
    job, fake = signed(rrig, launchers, github_origin, sha, "create-pr", paths=tuple(links))
    newly = "backend/src/other.py" if case == "target-stays-allowed" else APP
    protect(rrig, newly)
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert len(fake.created) == 1 and len(fake.merge_requests) == 1


UNRESOLVED: dict[str, tuple[list[tuple[dict[str, str | None], list[tuple[str, str]]]], str, str]] = {
    # name: ([(edits, symlinks to create), ...] — commits in order), the symlink refused, what it resolves to
    "missing-target": (
        [({"backend/src/x.py": X_PY}, [("backend/src/link.py", "nowhere.py")]),
         ({"backend/src/link.py": None}, [])], "backend/src/link.py", "nothing"),
    "directory-symlink": (
        [({"backend/src/x.py": X_PY}, [("backend/tools/d", "../src")]),
         ({"backend/tools/d": None}, [])], "backend/tools/d", "a directory"),
    "dot-dot-through-a-directory-symlink": (
        [({"backend/src/x.py": X_PY, "backend/src/pkg/m.py": "M = 1\n"},
          [("backend/tools/d", "../src/pkg"), ("backend/tools/link.py", "d/../app.py")]),
         ({"backend/tools/d": None, "backend/tools/link.py": None}, [])], "backend/tools/d", "a directory"),
    "chain-leaving-the-repository": (
        [({"backend/src/x.py": X_PY}, [("backend/src/a/b/d", "../.."),
                                       ("backend/src/a/b/link", "d/../../../../../etc/passwd")]),
         ({"backend/src/a/b/d": None, "backend/src/a/b/link": None}, [])], "backend/src/a/b/d", "a directory"),
    "symlink-to-a-symlink": (
        [({"backend/src/x.py": X_PY}, [("backend/src/other.py", "app.py"), ("backend/src/link.py", "other.py")]),
         ({"backend/src/other.py": None, "backend/src/link.py": None}, [])], "backend/src/link.py",
        "another symlink"),
    "target-becomes-a-directory-in-a-later-commit": (
        [({"backend/src/x.py": X_PY, "backend/src/tgt": "T = 1\n"}, [("backend/src/link.py", "tgt")]),
         ({"backend/src/tgt": None, "backend/src/tgt/inner.py": "I = 1\n"}, []),
         ({"backend/src/link.py": None, "backend/src/tgt/inner.py": None}, [])], "backend/src/link.py",
        "a directory"),
}


def unresolved_history(rig: Rig, case: str) -> tuple[str, list[str]]:
    commits, _refused, _kind = UNRESOLVED[case]
    sha, links = "", []
    for edits, symlinks in commits:
        def prepare(repo: Path, symlinks: list[tuple[str, str]] = symlinks) -> None:
            for path, target in symlinks:
                link(path, target)(repo)
        sha = change_on(rig.repo, BRANCH, edits, prepare=prepare if symlinks else None)
        links += [path for path, _target in symlinks]
    return sha, sorted(set(links))


@pytest.mark.parametrize("case", sorted(UNRESOLVED))
def test_f7a_a_historical_symlink_that_does_not_resolve_to_a_regular_file_is_refused_by_m2(rrig: Rig, case: str):
    """The independent review's bypass of the lexical check: a history-only symlink to a directory (a glob such as
    `**/.env` or `**/app.py` would then match below it), `..` through a directory symlink, a chain that leaves the
    repository, a symlink to a symlink, a missing target, a target that becomes a directory later. M2's rule for a
    symlink kept in the source tip — it must resolve to a regular file of that tree — now holds for every commit of
    the range in which a symlink exists; the shared derivation refuses at inspection already."""
    _commits, culprit, kind = UNRESOLVED[case]
    sha, links = unresolved_history(rrig, case)
    job = rrig.approved(request_for(rrig.repo, sha), paths=links)
    report = rrig.inspect(job.job_id)
    assert report.outcome == "refused", (report.outcome, report.reasons)
    assert f"symlink {culprit} points to {kind} in a commit of the source history" in " ".join(report.reasons)


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("case", ["directory-symlink", "dot-dot-through-a-directory-symlink", "missing-target"])
def test_f7a_the_boundary_refuses_an_unresolvable_historical_symlink_of_a_job_inspected_under_the_earlier_rule(
        rrig: Rig, launchers: Any, github_origin: Path, monkeypatch: pytest.MonkeyPatch, case: str, action: str):
    """A job inspected before this rule existed (simulated: M2's derivation without the per-commit resolution) is
    refused at the write boundary, which re-derives the history itself — no policy change needed."""
    from feature_coordinator import integration_inspect  # pylint: disable=import-outside-toplevel
    real = integration_inspect.history_changes

    def earlier_rule(*args: Any, **kwargs: Any) -> Any:
        found = real(*args, **kwargs)
        for item in found.values():
            item.history_kinds = set()
        return found
    _commits, culprit, kind = UNRESOLVED[case]
    sha, links = unresolved_history(rrig, case)
    monkeypatch.setattr(integration_inspect, "history_changes", earlier_rule)
    job, fake = signed(rrig, launchers, github_origin, sha, action, paths=tuple(links))
    monkeypatch.setattr(integration_inspect, "history_changes", real)
    before = unchanged_evidence(rrig, job)
    reasons = refused(rrig, job, fake, merge(rrig, job.job_id), before)
    assert f"source history path: symlink {culprit} points to {kind} in a commit of the source history" in reasons


def test_f7a_the_history_is_rederived_from_verified_objects_and_must_be_m2s(rrig: Rig, launchers: Any,
                                                                             github_origin: Path,
                                                                             monkeypatch: pytest.MonkeyPatch):
    """The boundary never classifies the recorded history list on its own: what it re-derives must be M2's recorded
    paths, kinds of change and flags — anything else (here: the derivation sees one more path) refuses."""
    from feature_coordinator import integration_authority  # pylint: disable=import-outside-toplevel
    from feature_coordinator.integration_paths import PathChange  # pylint: disable=import-outside-toplevel
    job, fake = signed(rrig, launchers, github_origin, change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY}),
                       "create-pr")
    real = integration_authority.history_changes

    def more(*args: Any, **kwargs: Any) -> Any:
        found = real(*args, **kwargs)
        found["backend/src/extra.py"] = PathChange(changes={"added"})
        return found
    monkeypatch.setattr(integration_authority, "history_changes", more)
    before = unchanged_evidence(rrig, job)
    assert "not M2's recorded ones" in refused(rrig, job, fake, merge(rrig, job.job_id), before)


# == F7b: the gate plan of the CURRENT owning roles ===================================================================

def move(rig: Rig, path_pattern: str, *, to: str | None, away_from: tuple[str, ...]) -> None:
    """The configuration with the ownership pattern *path_pattern* taken from the *away_from* roles and (unless *to* is
    None) given to role *to* instead (role scopes never overlap)."""
    roles = dict(rig.config.roles)
    for name in away_from:
        role = roles[name]
        assert path_pattern in role.owned
        roles[name] = dataclasses.replace(role, owned=tuple(p for p in role.owned if p != path_pattern))
    if to is not None:
        roles[to] = dataclasses.replace(roles[to], owned=(*roles[to].owned, path_pattern))
    rig.config = dataclasses.replace(rig.config, roles=roles)


def owner(rig: Rig, path: str) -> str | None:
    from feature_coordinator.paths import PathPolicy  # pylint: disable=import-outside-toplevel
    return PathPolicy(rig.repo, rig.config).owner_of(path)


FRONTEND = "frontend/src/main.ts"
OWNERSHIP = {
    # name: (edits, inspect approval paths, pattern moved, new owner, old owners, gates the old validation never ran)
    "backend-to-frontend": ({"backend/src/x.py": X_PY}, (), "backend/**", "frontend", ("backend",),
                            ["frontend-build", "frontend-type-check"]),
    "frontend-to-backend": ({FRONTEND: "export const x = 2\n"}, (), "frontend/**", "backend", ("frontend",),
                            ["backend-lint", "backend-tests"]),
    "role-removed-path-authorized": ({"backend/src/x.py": X_PY}, ("backend/src/x.py",), "backend/**", None,
                                     ("backend",), ["coordinator-tests"]),
}


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("case", sorted(OWNERSHIP))
def test_f7b_a_path_that_changed_owner_needs_the_current_gate_plan(rrig: Rig, launchers: Any, github_origin: Path,
                                                                   monkeypatch: pytest.MonkeyPatch, case: str,
                                                                   action: str):
    """The original reproduction and its variants: the candidate (same head and tree) was validated and reviewed for
    its owners' gates; at the boundary the configuration gives a path another owner (or none). Before: merged,
    COMPLETED, without the new owner's gates. Now: the boundary derives the plan from the current roles, finds the
    validation obsolete and refuses; no gate is run, no evidence is replaced."""
    edits, paths, pattern, new, old, missing = OWNERSHIP[case]
    job, fake = signed(rrig, launchers, github_origin, change_on(rrig.repo, BRANCH, edits), action, paths=paths)
    path = next(iter(edits))
    validation = latest_evidence(job, "validation")
    before, head_tree = unchanged_evidence(rrig, job), (job.candidate["head_sha"], job.candidate["tree_sha"])
    done = at_boundary(monkeypatch, action, lambda: move(rrig, pattern, to=new, away_from=old))
    reasons = refused(rrig, job, fake, merge(rrig, job.job_id), before)
    assert done and owner(rrig, path) == new
    assert f"M3's validation {validation['id']} was decided for gate plan" in reasons
    assert f"{path} → {new or 'unowned'}" in reasons and f"gates never run for it {missing or 'none'}" in reasons
    stored = rrig.jobs.load(job.job_id)
    assert (stored.candidate["head_sha"], stored.candidate["tree_sha"]) == head_tree
    assert not set(missing) & set(current_gate_evidence(stored))        # nothing was run inside M5


def test_f7b_a_role_change_before_the_run_is_found_by_the_workflow_as_an_obsolete_validation(
        rrig: Rig, launchers: Any, github_origin: Path):
    """Changed before the run, the workflow itself (M4's shared checks, now on the current roles) reports the validation
    as not current — the existing state machine's rule for a stale plan — before any intent or write."""
    job, fake = ready_job(rrig, launchers, github_origin)
    before = unchanged_evidence(rrig, job)
    move(rrig, "backend/**", to="frontend", away_from=("backend",))
    report = merge(rrig, job.job_id)
    assert report.outcome == "not-validated", (report.outcome, report.reasons)
    assert fake.writes() == [] and unchanged_evidence(rrig, job) == before
    assert not [e for e in rrig.jobs.load(job.job_id).evidence if e["kind"] == "merge-attempt"]


def test_f7b_mixed_roles_one_path_moved_changes_the_plan(rrig: Rig, launchers: Any, github_origin: Path,
                                                         monkeypatch: pytest.MonkeyPatch):
    job, fake = signed(rrig, launchers, github_origin,
                       change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY, FRONTEND: "export const x = 2\n"}),
                       "merge")
    before = unchanged_evidence(rrig, job)
    done = at_boundary(monkeypatch, "merge", lambda: move(rrig, "frontend/**", to="cli", away_from=("frontend",)))
    reasons = refused(rrig, job, fake, merge(rrig, job.job_id), before)
    assert done and f"{FRONTEND} → cli" in reasons and "backend/src/x.py → backend" in reasons
    assert "gates never run for it ['cli-lint']" in reasons
    assert "gates no longer planned ['frontend-build', 'frontend-type-check']" in reasons


def test_f7b_a_history_path_that_newly_needs_an_authorization_blocks_the_write(rrig: Rig, launchers: Any,
                                                                               github_origin: Path,
                                                                               monkeypatch: pytest.MonkeyPatch):
    """A path only the source history changes loses its owner: unowned, it needs a per-path authorization the inspect
    approval does not give."""
    change_on(rrig.repo, BRANCH, {"backend/src/x.py": X_PY, "cli/src/kiddo_cli/scratch.py": "S = 1\n"})
    sha = change_on(rrig.repo, BRANCH, {"cli/src/kiddo_cli/scratch.py": None})
    job, fake = signed(rrig, launchers, github_origin, sha, "create-pr")
    before = unchanged_evidence(rrig, job)
    done = at_boundary(monkeypatch, "create-pr", lambda: move(rrig, "cli/**", to=None, away_from=("cli",)))
    reasons = refused(rrig, job, fake, merge(rrig, job.job_id), before)
    assert done and "source history path: cli/src/kiddo_cli/scratch.py needs an explicit per-path authorization" \
        in reasons


@pytest.mark.parametrize("change", ["nothing", "an-unrelated-role-pattern"])
def test_f7b_the_same_roles_and_gate_plan_still_merge(rrig: Rig, launchers: Any, github_origin: Path, change: str):
    job, fake = ready_job(rrig, launchers, github_origin)
    if change == "an-unrelated-role-pattern":
        roles = dict(rrig.config.roles)
        roles["frontend"] = dataclasses.replace(roles["frontend"], owned=(*roles["frontend"].owned, "frontend2/**"))
        rrig.config = dataclasses.replace(rrig.config, roles=roles)
    report = merge(rrig, job.job_id)
    assert report.outcome == "completed", (report.outcome, report.reasons)
    assert len(fake.created) == 1 and len(fake.merge_requests) == 1


def test_f7b_the_current_plan_is_derived_from_the_current_roles_and_the_evidence_is_kept(rrig: Rig, launchers: Any,
                                                                                         github_origin: Path):
    """The derivation itself, under two configuration snapshots: the recorded candidate keeps the role it was
    classified with; current_paths gives the current owner; the plan, validated_for and review_eligibility follow
    the current one (and the boundary's own classification, passed explicitly, decides the same)."""
    job, _fake = ready_job(rrig, launchers, github_origin)
    original = rrig.config
    validated_plan = latest_evidence(job, "validation")["plan"]["sha256"]
    assert current_plan_sha256(job, original) == validated_plan and validated_for(job, original)
    assert review_eligibility(job, original)[0] is not None
    move(rrig, "backend/**", to="frontend", away_from=("backend",))
    changed = rrig.config
    assert [e["role"] for e in job.candidate["paths"]] == ["backend"]             # as recorded, never rewritten
    assert [e["role"] for e in current_paths(job, changed)] == ["frontend"]
    names = [p.name for p in gate_plan(current_paths(job, changed), changed)]
    assert "frontend-build" in names and "backend-tests" not in names
    assert current_plan_sha256(job, changed) != validated_plan
    assert not validated_for(job, changed) and not validated_for(job, changed, current_paths(job, changed))
    decision, why = review_eligibility(job, changed)
    assert decision is None and "another gate plan" in why
    assert validated_for(job, original)                                            # the evidence itself is intact
