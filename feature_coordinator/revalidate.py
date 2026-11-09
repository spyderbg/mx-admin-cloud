"""Explicit revalidation of an approved Issue's EXISTING Draft PR after its head changed (publish.py, review.py).

    python -m feature_coordinator github publish revalidate FEATURE [--json]                 # dry run
    python -m feature_coordinator github publish revalidate FEATURE --head SHA --apply [--actor NAME] [--json]

Publication verifies ONE head: the integration barrier validated it, the publisher pushed it and opened the single
Draft PR at it, and every later pass requires the PR to stay exactly there. When a person pushes commits to the PR's
branch (e.g. to address a REQUEST_CHANGES review), publication stops (`attention`) by design, and `publish reset`
alone does not help: the new head was never validated. This operator command validates the new head of the SAME PR
and only then moves the recorded candidate to it. It reuses the publisher's identity checks, the integration
barrier's cleanliness check and gate evidence, and the review's invalidation; it adds no state machine.

  1. read (dry run and --apply): the feature is an approved Issue's feature in `review` with its PR recorded, every
     task verified, nothing merged; the checkout is the feature's repository (the publisher's `_repository_problem`);
     the PR, read by number, is still the recorded PR — the publisher's `_pr_problem` (head branch and repository,
     base branch and repository, author) with only its head commit allowed to differ, its URL and (when an earlier
     review bound it) its node ID unchanged — and open; origin's feature branch is at the PR's head; the approval
     still holds (the Issue re-read); no claim, no launch in flight, no reviewer process running. A PR head equal
     to the candidate: nothing to revalidate (`unchanged`);
  2. the new head belongs to the feature: it contains the validated head (a fast-forward: history was not
     rewritten), adds no merge commit, and every path it adds, changes or removes is writable by one of the
     feature's tasks (their role scopes and grants; protected paths never);
  3. --apply (needs github.publish.enabled and `--head`: the PR head the dry run showed, as the full 40-character
     SHA, never abbreviated; a PR at any other head is `stale` and nothing is done), under github-publish.lock and
     github-review.lock (no publication pass and no review run meanwhile): a write-ahead attempt record; the feature
     branch fetched (2 is checked on that commit) and FAST-FORWARDED in the shared checkout, under project.lock
     EXCLUSIVE + git.lock (clean, on the branch; never reset, never forced, nothing pushed); the original
     candidate's integration gates, the approved plan's (or the default) integration gates and every task's
     required gates run at the new head (`gate_evidence`, project.lock still held: no writer can start);
  4. checked AGAIN after the gates: the PR re-read (identity, open, head) and origin's branch read. A head that
     moved meanwhile changes nothing (`stale`: run it again);
  5. one transaction, which first re-reads the local facts (the candidate still at the old head, the same PR, the
     branch at the new head, no claim): the candidate's head, draft flag and integration evidence move to the new
     head and its stop is cleared; the previous candidate (head, integration evidence, push, stop) is kept in the
     attempt record; the current AI review, bound to the old head, is INVALIDATED (kept as history);
  6. then the review's invalidation notice is posted on the PR (idempotent; a failure is completed later).

Afterwards the publisher follows the same PR at the new head, and a NEW independent AI review is required: the
Worker's review step starts it (an invalidated review of an older head never settles a newer one), or `github review
run FEATURE --apply`. An earlier PASS or REQUEST_CHANGES is never evidence for the new head (`review_evidence` is
bound to one head and only to the current entry). Nothing here creates or edits a PR, pushes, merges, changes a
feature's or a task's state, or activates anything.

Recovery: each step re-reads what is true first. Interrupted before the transaction, the candidate is still at the
old head (the branch may already be fast-forwarded: a re-run continues from anywhere on the way) and the attempt is
marked `interrupted` by the next run; after it, a re-run finds the PR at the candidate (`unchanged`) and only posts
a notice that is still due. Failed gates leave the candidate and the stop as they were (recorded as `failed`); push
a fix, then revalidate again. Records: `state/revalidation/<feature>.json` (every attempt, never rewritten).
"""

from __future__ import annotations

import dataclasses
import re
import secrets
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, NoReturn

from .errors import CoordinatorError, GitHubNotFoundError, StateDirError, WritersActiveError
from .github_api import sanitize
from .intake import IntakeRecord, issue_problem, known_problem, load_intake
from .issue_sync import SimulatedCrash
from .issues import PullRequest
from .model import Evidence, Feature, FeatureState, PullRequestCandidate, Task, TaskState, utc_now
from .publish import PullRequestPublisher
from .review import ReviewRunner, invalidate_superseded, load_review
from .review_process import attempt_processes
from .workflow import DEFAULT_INTEGRATION_GATES

REVALIDATION_SCHEMA = 1
ATTEMPT_STATUSES = ("running", "validated", "failed", "stale", "refused", "deferred", "interrupted")
_RECORD_FIELDS = frozenset({"schema_version", "id", "entries", "updated_at", "revision"})
_SHA_RE = re.compile(r"[0-9a-f]{40}")  # a full commit SHA; `--head` must be exactly this (never abbreviated)


@dataclass
class RevalidationRecord:
    """Every revalidation attempt of one feature's PR (kind `revalidation`, id = the feature ID)."""

    id: str
    entries: list[dict[str, Any]] = field(default_factory=list)
    updated_at: str = field(default_factory=utc_now)
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "schema_version": REVALIDATION_SCHEMA}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RevalidationRecord":
        """Strict: another schema, unknown fields or an unknown attempt status are refused."""
        if data.get("schema_version") != REVALIDATION_SCHEMA:
            raise StateDirError(f"revalidation record {data.get('id')!r}: unsupported schema "
                                f"{data.get('schema_version')!r}")
        unknown = sorted(set(data) - _RECORD_FIELDS)
        if unknown:
            raise StateDirError(f"revalidation record {data.get('id')!r} has unknown fields {unknown}")
        record = cls(**{k: v for k, v in data.items() if k != "schema_version"})
        bad = [e.get("op") for e in record.entries if e.get("status") not in ATTEMPT_STATUSES]
        if bad:
            raise StateDirError(f"revalidation record {record.id!r}: attempts with an unknown status {bad}")
        return record


def load_revalidation(store: Any, feature_id: str) -> RevalidationRecord | None:
    """The revalidation record of a feature (None: never revalidated)."""
    raw = store.load_record("revalidation", feature_id)
    return RevalidationRecord.from_dict(raw) if raw is not None else None


def revalidation_gates(feature: Feature, record: IntakeRecord, tasks: Mapping[str, Task]) -> tuple[str, ...]:
    """The gates a new head must pass — at least what validated the original candidate: the integration gates
    publication uses (the approved plan's, else the default), the gates of the recorded integration evidence, and
    every task's required gates (the new commits were never verified by a task)."""
    planned = tuple(record.integration_gates) if record.planned and record.integration_gates \
        else DEFAULT_INTEGRATION_GATES
    previous = tuple(g.gate for g in feature.integration_evidence.gates) if feature.integration_evidence else ()
    return tuple(dict.fromkeys((*planned, *previous, *(g for t in tasks.values() for g in t.required_gates))))


class _Stopped(Exception):
    """This run ends here; why is already in the report."""


@dataclass
class RevalidationReport:  # pylint: disable=too-many-instance-attributes
    """What one run did (apply) or would do (dry run)."""

    applied: bool
    feature: str
    status: str = ""          # unchanged | dry-run | revalidated | failed | stale | refused | deferred
    pr: int | None = None
    from_head: str = ""
    to_head: str = ""
    entry: str | None = None  # the attempt (apply)
    gates: list[dict[str, Any]] = field(default_factory=list)
    commits: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    review: dict[str, Any] | None = None   # the review the old head had: key, outcome, findings, invalidated
    lines: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)      # dry run: what would be done
    deferred: list[str] = field(default_factory=list)
    attention: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing was deferred and nothing needs an operator."""
        return not self.deferred and not self.attention

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "ok": self.ok}


class PullRequestRevalidator:  # pylint: disable=too-few-public-methods
    """One revalidation run for one feature (see the module docstring)."""

    def __init__(self, coord: Any, *, faults: Iterable[str] = ()) -> None:
        self.coord, self.store, self.config, self.git = coord, coord.store, coord.config, coord.git
        self.client = coord.sync.client
        self.publisher = PullRequestPublisher(coord)  # its identity checks only; it never runs a pass here
        self.timeout = coord.config.github.publish.git_timeout_seconds
        self.faults = set(faults)

    def _fault(self, name: str) -> None:
        if name in self.faults:
            self.faults.discard(name)
            raise SimulatedCrash(name)

    # -- the run ----------------------------------------------------------------------------------

    def run(self, feature_id: str, *, apply: bool, expected_head: str | None = None,
            actor: str = "operator") -> RevalidationReport:
        """Revalidate the feature's Draft PR at its current head (dry run: reads GitHub, origin and the checkout,
        fetches, runs and writes nothing). *expected_head* (required with *apply*): the head the operator checked,
        as the exact full 40-character lowercase SHA; the PR's head must be equal to it."""
        report = RevalidationReport(apply, feature_id)
        expected = expected_head or ""
        if expected_head is not None and not _SHA_RE.fullmatch(expected_head):
            raise CoordinatorError("--head must be the PR head commit's full 40-character SHA (lowercase hex, as the "
                                   "dry run shows it); an abbreviated or malformed SHA is refused")
        if apply and not self.config.github.publish.enabled:
            raise CoordinatorError("github.publish.enabled is false: a PR head is revalidated only while publication "
                                   "is enabled in the project configuration")
        if apply and expected_head is None:
            raise CoordinatorError("--apply needs --head SHA: the full 40-character PR head commit the dry run showed "
                                   "(a head that changed since is never revalidated by surprise)")
        held = []
        try:
            for lock in ((self.store.locks.publish(), self.store.locks.review()) if apply else ()):
                try:
                    lock.acquire()
                except CoordinatorError:
                    report.status = "deferred"
                    report.deferred.append(f"{feature_id}: a publication pass or a review run is in progress; run it "
                                           "again")
                    return report
                held.append(lock)
            try:
                self._run(feature_id, report, apply, expected, actor)
            except _Stopped:
                pass
            except CoordinatorError as exc:  # GitHub or origin unreachable, a busy lock, a concurrent change
                report.status = "deferred"
                report.deferred.append(f"{feature_id}: {sanitize(f'{type(exc).__name__}: {exc}', 300)}")
        finally:
            for lock in reversed(held):
                lock.release()
        return report

    def _stop(self, report: RevalidationReport, reason: str, *, status: str = "refused") -> NoReturn:
        """End the run: deferred (retry later) or for an operator (refused, failed, stale)."""
        report.status = status
        (report.deferred if status == "deferred" else report.attention).append(
            f"{report.feature}: {sanitize(reason, 600)}")
        raise _Stopped(reason)

    def _run(self, feature_id: str, report: RevalidationReport, apply: bool, expected: str, actor: str) -> None:  # pylint: disable=too-many-arguments
        try:
            feature = self.store.load_feature(feature_id)
        except KeyError:
            self._stop(report, "no such feature")
        record = load_intake(self.store, feature_id)
        if record is None:
            self._stop(report, "not an approved GitHub Issue's feature: its PR is never revalidated by the coordinator")
        assert record is not None
        cand = feature.pull_request
        if feature.state is not FeatureState.REVIEW or cand is None or not cand.number:
            self._stop(report, f"the feature is {feature.state.value} without a recorded PR: nothing to revalidate "
                               "(publication opens and records the PR first)")
        assert cand is not None and cand.number is not None
        if cand.merge:
            self._stop(report, f"PR #{cand.number} is already recorded as merged: nothing to revalidate")
        report.pr, report.from_head = cand.number, cand.head_sha
        tasks = self.store.load_tasks(feature)
        pending = sorted(t.id for t in tasks.values() if t.state is not TaskState.VERIFIED)
        if pending:
            self._stop(report, f"tasks that are not verified: {pending}; nothing is revalidated")
        problem = self.publisher._repository_problem(feature)  # pylint: disable=protected-access
        if problem:
            self._stop(report, problem)
        pr = self._pull(feature, cand, report)
        report.to_head = pr.head_sha
        report.review = self._review_facts(feature_id)
        if expected and pr.head_sha != expected:  # exact: --apply never acts on a head the operator did not confirm
            self._stop(report, f"PR #{pr.number} is at {pr.head_sha}, not the expected {expected}: the head changed "
                               "since it was checked; run the dry run again", status="stale")
        if pr.head_sha == cand.head_sha:
            self._unchanged(feature, cand, report, apply)
            return
        remote = self.git.remote_head(feature.branch, timeout=self.timeout)
        if remote != pr.head_sha:
            self._stop(report, f"origin has {feature.branch} at {(remote or 'nothing')[:12]}, but PR #{pr.number} "
                               f"reports {pr.head_sha[:12]}; run it again once they agree", status="stale")
        self._quiet(feature, record, tasks, report)
        if not apply:
            self._preview(feature, record, tasks, cand, pr, report)
            return
        self._apply(feature, record, tasks, cand, pr, report, actor)

    # -- reads -------------------------------------------------------------------------------------

    def _pull(self, feature: Feature, cand: PullRequestCandidate, report: RevalidationReport) -> PullRequest:
        """The recorded PR, read fresh by number: still exactly that PR, open (its head may differ)."""
        try:
            pr = self.client.get_pull_request(cand.number)
        except GitHubNotFoundError:
            self._stop(report, f"the recorded PR #{cand.number} no longer exists on GitHub")
        problem = self._identity_problem(feature, cand, pr)
        if problem:
            self._stop(report, f"PR #{cand.number} is not the recorded PR of {feature.id}: {problem}")
        if pr.state != "open" or pr.merged:
            self._stop(report, f"PR #{pr.number} is {'merged' if pr.merged else 'closed'}: only an open PR is "
                               "revalidated (a closed one: reopen it first)")
        return pr

    def _identity_problem(self, feature: Feature, cand: PullRequestCandidate, pr: PullRequest) -> str | None:
        """The publisher's PR identity checks with only the head commit allowed to differ; plus the recorded number,
        URL and the node ID an earlier review bound."""
        problems = [self.publisher._pr_problem(feature, dataclasses.replace(cand, head_sha=pr.head_sha), pr)]  # pylint: disable=protected-access
        if pr.number != cand.number:
            problems.append(f"GitHub answered with PR #{pr.number}")
        if cand.url and not cand.url.startswith("pr#") and pr.url and pr.url != cand.url:
            problems.append(f"its URL is {sanitize(pr.url, 120)!r}, not the recorded {sanitize(cand.url, 120)!r}")
        node = self._bound_node_id(feature.id, int(cand.number or 0))
        if node and pr.node_id != node:
            problems.append(f"its node ID is {sanitize(pr.node_id, 60)!r}, not {sanitize(node, 60)!r} as reviewed")
        if not _SHA_RE.fullmatch(pr.head_sha or ""):
            problems.append("GitHub reports no full head commit SHA")
        return "; ".join(p for p in problems if p) or None

    def _bound_node_id(self, feature_id: str, number: int) -> str:
        record = load_review(self.store, feature_id)
        nodes = [e["binding"].get("pr_node_id") for e in (record.entries if record else [])
                 if e["binding"].get("pr") == number and e["binding"].get("pr_node_id")]
        return str(nodes[-1]) if nodes else ""

    def _review_facts(self, feature_id: str) -> dict[str, Any] | None:
        record = load_review(self.store, feature_id)
        entry = record.entry(record.current) if record else None
        if entry is None:
            return None
        severities = [f.get("severity") for f in entry.get("findings") or []]
        return {"key": entry["key"], "status": entry["status"], "outcome": entry["outcome"],
                "head": entry["binding"]["head_sha"], "blocking": sum(s in ("blocker", "major") for s in severities),
                "findings": len(severities), "invalidated": None}

    def _quiet(self, feature: Feature, record: IntakeRecord, tasks: Mapping[str, Task],
               report: RevalidationReport) -> None:
        """The approval holds and nothing writes: no claim, no launch in flight, no reviewer process."""
        withdrawn = next((p for p in (known_problem(self.store, t) for t in tasks.values()) if p), None) \
            or issue_problem(self.client, record)[1]
        if withdrawn:
            self._stop(report, f"the approval no longer holds; nothing is revalidated: {withdrawn}")
        busy = sorted(self.store.list_claims()) + sorted(
            t for t in tasks if (self.store.load_record("scheduling", t) or {}).get("in_flight"))
        if busy:
            self._stop(report, f"writers are active (claims or launches in flight: {busy}); nothing is revalidated "
                               "while they run", status="deferred")
        review = load_review(self.store, feature.id)
        entry = review.entry(review.current) if review else None
        if entry is not None and (entry.get("running") or {}).get("tag"):
            state, pids = attempt_processes(entry["running"])
            if state != "gone":
                self._stop(report, f"a reviewer process runs for review {entry['key']} ({state}, pids {pids}); let "
                                   "the Worker collect it, or pause the Worker, first", status="deferred")

    def _belongs(self, feature: Feature, tasks: Mapping[str, Task], old: str, new: str,
                 report: RevalidationReport) -> str | None:
        """Why the commit *new* is not the feature branch's own continuation of *old* (None: it is)."""
        if not self.git.is_ancestor(old, new):
            return (f"{new[:12]} does not contain the validated head {old[:12]}: the branch was rewritten (a forced "
                    "push?); it is never revalidated")
        report.commits = self.git.commits_between(old, new)
        report.paths = self.git.touched_paths(old, new)
        merges = self.git.merge_commits(old, new)
        if merges:
            return (f"the new commits include merge commit(s) {[m[:12] for m in merges]}: only plain commits on top of "
                    "the validated head are revalidated")
        outside = [p for p in report.paths if not any(
            self.coord.policy.check(t.role, p, grants=t.grants).allowed for t in tasks.values())]
        if outside:
            return (f"the new commits change path(s) no task of {feature.id} may write: "
                    f"{[sanitize(p, 120) for p in outside[:10]]}")
        return None

    # -- unchanged / dry run -----------------------------------------------------------------------

    def _unchanged(self, feature: Feature, cand: PullRequestCandidate, report: RevalidationReport,
                   apply: bool) -> None:
        report.status = "unchanged"
        record = load_revalidation(self.store, feature.id)
        last = next((e for e in reversed(record.entries if record else [])
                     if e["status"] == "validated" and e["to_head"] == cand.head_sha), None)
        report.lines.append(f"{feature.id}: PR #{cand.number} is at the recorded candidate {cand.head_sha[:12]}: "
                            "nothing to revalidate" + (f" (revalidated by {last['op']} at {last['finished_at']})"
                                                       if last else ""))
        problem = self.publisher._head_problem(feature, cand)  # pylint: disable=protected-access
        if problem:
            report.attention.append(f"{feature.id}: {problem}")
        if cand.attention:
            report.attention.append(f"{feature.id}: publication is stopped for another reason "
                                    f"({sanitize(cand.attention.get('reason'), 300)}); after resolving it: "
                                    f"`python -m feature_coordinator github publish reset {feature.id} --apply`")
        if apply:  # completes what an interruption after the transaction left: the review's invalidation and notice
            reason = f"PR #{cand.number}'s verified head is {cand.head_sha[:12]}; a new review of it is required"
            with self.store.transaction():
                key = invalidate_superseded(self.store, feature.id, cand.head_sha, reason)
            if key and report.review is not None:
                report.review["invalidated"] = key
                report.lines.append(f"{feature.id}: review {key} of another head invalidated (history kept)")
            self._notices(feature.id, report)

    def _preview(self, feature: Feature, record: IntakeRecord, tasks: Mapping[str, Task],  # pylint: disable=too-many-arguments
                 cand: PullRequestCandidate, pr: PullRequest, report: RevalidationReport) -> None:
        old, new = cand.head_sha, pr.head_sha
        if self.git.has_commit(new):
            problem = self._belongs(feature, tasks, old, new, report)
            if problem:
                self._stop(report, problem)
            local = self.git.rev_parse(f"refs/heads/{feature.branch}")
            if not (self.git.is_ancestor(old, local) and self.git.is_ancestor(local, new)):
                self._stop(report, f"{feature.branch} is at {local[:12]} in this checkout, not on the way from "
                                   f"{old[:12]} to {new[:12]}: it is never moved there")
        else:
            report.lines.append(f"{feature.id}: {new[:12]} is not in this repository yet: its ancestry, merge commits "
                                "and paths are checked after the fetch (--apply)")
        if self.git.current_branch() != feature.branch:
            self._stop(report, f"the checkout is on {self.git.current_branch()!r}, not {feature.branch!r}")
        dirty = self.coord.foreign_changes()
        if dirty:
            self._stop(report, f"the shared checkout has uncommitted or foreign changes: {dirty[:10]}")
        gates = revalidation_gates(feature, record, tasks)
        review = report.review
        report.status = "dry-run"
        report.lines.append(f"{feature.id}: PR #{pr.number} moved {old[:12]} → {new[:12]} "
                            f"({len(report.commits) or '?'} commit(s) on top of the validated head)")
        report.writes += [
            f"fetch {feature.branch} and fast-forward the shared checkout {old[:12]} → {new[:12]} (never reset, never "
            "forced, nothing pushed)",
            f"run the gates {list(gates)} at {new[:12]}",
            f"re-read PR #{pr.number} and origin, then move the recorded candidate to {new[:12]}, keep the previous "
            "candidate as history and clear the publication stop",
            (f"invalidate review {review['key']} ({review['outcome'] or review['status']}) of {review['head'][:12]}; "
             if review and review["status"] != "invalidated" and review["head"] != new else "")
            + f"a new independent AI review of {new[:12]} is required"]
        report.lines.append(f"to revalidate it: github publish revalidate {feature.id} --head {new} --apply")

    # -- apply -------------------------------------------------------------------------------------

    def _apply(self, feature: Feature, record: IntakeRecord, tasks: Mapping[str, Task],  # pylint: disable=too-many-arguments
               cand: PullRequestCandidate, pr: PullRequest, report: RevalidationReport, actor: str) -> None:
        gates = revalidation_gates(feature, record, tasks)
        op = report.entry = self._begin(feature.id, cand, pr, gates, actor)
        try:
            evidence, fresh = self._validate(feature, tasks, cand, pr, gates, report)
            self._record(feature.id, op, cand, fresh, evidence, report, actor)
        except _Stopped as stop:
            self._end(feature.id, op, report.status or "refused", str(stop), gates=report.gates)
            raise
        except CoordinatorError as exc:
            self._end(feature.id, op, "deferred", f"{type(exc).__name__}: {exc}", gates=report.gates)
            raise
        self._fault(f"crash-before-notice:{feature.id}")
        self._notices(feature.id, report)

    def _validate(self, feature: Feature, tasks: Mapping[str, Task], cand: PullRequestCandidate,  # pylint: disable=too-many-arguments
                  pr: PullRequest, gates: tuple[str, ...], report: RevalidationReport) -> tuple[Evidence, PullRequest]:
        old, new = cand.head_sha, pr.head_sha
        fetched = self.git.fetch_branch(feature.branch, timeout=self.timeout)
        if fetched != new:
            self._stop(report, f"origin has {feature.branch} at {fetched[:12]} now, not {new[:12]}: the head moved; "
                               "run it again", status="stale")
        problem = self._belongs(feature, tasks, old, new, report)
        if problem:
            self._stop(report, problem)
        self._fault(f"crash-before-fast-forward:{feature.id}")
        project = self.store.locks.project(shared=False, busy_error=WritersActiveError).acquire()
        try:
            dirty = self.coord.foreign_changes()
            if dirty:
                self._stop(report, f"the shared checkout has uncommitted or foreign changes: {dirty[:10]}; nothing "
                                   "is revalidated", status="deferred")
            moved = self.git.fast_forward_branch(feature.branch, since=old, to=new, project=project)
            report.lines.append(f"{feature.id}: {feature.branch} " + (f"fast-forwarded {old[:12]} → {new[:12]}"
                                                                     if moved else f"already at {new[:12]}"))
            self._fault(f"crash-after-fast-forward:{feature.id}")
            evidence = self.coord.gate_evidence(self.store.load_feature(feature.id), gates)
        finally:
            project.release()
        report.gates = [g.to_dict() for g in evidence.gates]
        failing = [g.gate for g in evidence.gates if not g.passed]
        if evidence.head_sha != new:
            self._stop(report, f"the gates ran at {evidence.head_sha[:12]}, not {new[:12]}", status="stale")
        if failing:
            self._stop(report, f"the gates failed at {new[:12]}: {failing}; the candidate stays at {old[:12]} and "
                               "publication stays stopped — push a fix, then revalidate again", status="failed")
        self._fault(f"crash-after-gates:{feature.id}")
        fresh = self._pull(feature, cand, report)
        if fresh.head_sha != new:
            self._stop(report, f"PR #{pr.number} moved to {fresh.head_sha[:12]} while {new[:12]} was validated; "
                               "nothing was recorded — run it again", status="stale")
        remote = self.git.remote_head(feature.branch, timeout=self.timeout)
        if remote != new:
            self._stop(report, f"origin moved {feature.branch} to {(remote or 'nothing')[:12]} while {new[:12]} was "
                               "validated; nothing was recorded — run it again", status="stale")
        return evidence, fresh

    def _record(self, feature_id: str, op: str, cand: PullRequestCandidate, pr: PullRequest,  # pylint: disable=too-many-arguments
                evidence: Evidence, report: RevalidationReport, actor: str) -> None:
        """Move the candidate to the validated head, keep the previous one, invalidate the old review — one
        transaction, after the local facts were re-read in it."""
        old, new = cand.head_sha, pr.head_sha
        reason = (f"superseded: PR #{pr.number}'s head moved {old[:12]} → {new[:12]} and was revalidated by {actor}; a "
                  f"new independent review of {new[:12]} is required")
        with self.store.transaction():
            fresh = self.store.load_feature(feature_id)
            now = fresh.pull_request
            changed = [why for bad, why in (
                (fresh.state is not FeatureState.REVIEW, f"the feature is {fresh.state.value}"),
                (now is None or now.number != pr.number or bool(now.merge), "the recorded PR changed"),
                (now is not None and now.head_sha != old, f"the candidate is at {now.head_sha[:12] if now else '?'}"),
                (bool(self.store.list_claims()), "a claim started"),
                (self.git.rev_parse(f"refs/heads/{fresh.branch}") != new, f"{fresh.branch} moved")) if bad]
            if changed:
                self._stop(report, f"{feature_id} changed while {new[:12]} was validated ({'; '.join(changed)}); "
                                   "nothing was recorded — run it again", status="stale")
            assert now is not None
            previous = {"head_sha": old, "draft": now.draft, "pushed": now.pushed, "attention": now.attention,
                        "integration_evidence": fresh.integration_evidence.to_dict() if fresh.integration_evidence
                        else None}
            fresh.integration_evidence = evidence
            now.head_sha, now.draft, now.attention = new, pr.draft, None
            now.pushed = {"sha": new, "before": old, "at": utc_now(), "by": "observed on origin (revalidated)"}
            self.store.save_feature(fresh)
            key = invalidate_superseded(self.store, feature_id, new, reason)
            self._end(feature_id, op, "validated", f"the candidate moved {old[:12]} → {new[:12]}", gates=report.gates,
                      evidence=evidence.to_dict(), previous=previous, review_invalidated=key)
            self.store.append_event("publish-revalidated", feature=feature_id, op=op, pr=pr.number, before=old,
                                    head=new, gates=[g.gate for g in evidence.gates], review_invalidated=key,
                                    actor=actor)
        report.status = "revalidated"
        if report.review is not None:
            report.review["invalidated"] = key
        passed = ", ".join(f"{g.gate} pass" for g in evidence.gates)
        report.lines += [
            f"{feature_id}: revalidated PR #{pr.number}: the recorded candidate moved {old[:12]} → {new[:12]} "
            f"({len(report.commits)} commit(s), a fast-forward; nothing pushed, no PR created)",
            f"{feature_id}: gates at {new[:12]}: {passed}"]
        if key:
            review = report.review or {}
            report.lines.append(f"{feature_id}: review {key} ({review.get('outcome') or review.get('status')}, head "
                                f"{old[:12]}) invalidated — kept as history, never evidence for {new[:12]}")
        report.lines += [f"{feature_id}: a new independent AI review of {new[:12]} is required: the Worker's review "
                         "step starts it (github.review.enabled, Worker running), or `python -m feature_coordinator "
                         f"github review run {feature_id} --apply`",
                         f"{feature_id}: publication continues with the next pass (the same PR #{pr.number})"]

    def _notices(self, feature_id: str, report: RevalidationReport) -> None:
        try:
            sub = ReviewRunner(self.coord).post_notices(feature_id)
        except CoordinatorError as exc:
            report.deferred.append(f"{feature_id}: the review's invalidation notice is not posted yet "
                                   f"({sanitize(exc, 200)}); the next revalidate or review run posts it")
            return
        report.lines += sub.lines
        report.deferred += sub.deferred

    # -- the attempt record (write-ahead) -----------------------------------------------------------

    def _begin(self, feature_id: str, cand: PullRequestCandidate, pr: PullRequest, gates: tuple[str, ...],
               actor: str) -> str:
        op = f"r{secrets.token_hex(6)}"
        with self.store.transaction():
            record = load_revalidation(self.store, feature_id) or RevalidationRecord(id=feature_id)
            for entry in record.entries:
                if entry["status"] == "running":
                    entry.update(status="interrupted", finished_at=utc_now(),
                                 reason="the run ended before it finished (found by a later run)")
            record.entries.append({"op": op, "status": "running", "pr": pr.number, "from_head": cand.head_sha,
                                   "to_head": pr.head_sha, "gates": list(gates), "gate_results": [], "actor": actor,
                                   "started_at": utc_now(), "finished_at": "", "reason": "", "evidence": None,
                                   "previous": None, "review_invalidated": None})
            record.updated_at = utc_now()
            self.store.save_record("revalidation", record)
            self.store.append_event("publish-revalidation-started", feature=feature_id, op=op, pr=pr.number,
                                    before=cand.head_sha, head=pr.head_sha)
        return op

    def _end(self, feature_id: str, op: str, status: str, reason: str, *, gates: list[dict[str, Any]],  # pylint: disable=too-many-arguments
             **fields: Any) -> None:
        with self.store.transaction():
            record = load_revalidation(self.store, feature_id)
            entry = next((e for e in record.entries if e["op"] == op), None) if record else None
            if record is None or entry is None or entry["status"] != "running":
                return
            entry.update(status=status, reason=sanitize(reason, 600), finished_at=utc_now(), gate_results=gates,
                         **fields)
            record.updated_at = utc_now()
            self.store.save_record("revalidation", record)
            if status != "validated":
                self.store.append_event("publish-revalidation-ended", feature=feature_id, op=op, status=status,
                                        reason=sanitize(reason, 300))
