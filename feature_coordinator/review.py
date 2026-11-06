"""W4.6.1: the independent AI review of an approved GitHub Issue's Draft PR — binding, records, comments.

    python -m feature_coordinator github review run FEATURE [--apply]
    python -m feature_coordinator github review retry FEATURE --reason TEXT [--apply]

An operator starts a run (R1: the reviewer runs in the foreground); R2: once `github.review.enabled`, the Worker
service starts runs by itself (review_worker.py), each attempt in a separate reviewer process (review_process.py)
whose result a later service step collects through the SAME judgement below. One run, under `github-review.lock`:

  1. the feature is an approved Issue's feature in `review` whose single Draft PR is recorded (publish.py). The PR
     is read fresh by number and must still be exactly the publisher's verified candidate — the publisher's own
     checks decide (`_repository_problem`, `_head_problem`, `_pr_problem`: repository, branches, verified head,
     author), so publish.py stays authoritative for the PR's identity and verified head — and open, with
     publication not stopped. A PR that no longer matches invalidates the current review; nothing new is reviewed;
  2. the BINDING: repository, feature, Issue, PR (number, node ID), head branch and SHA, base branch, the merge base
     of the freshly fetched base branch and the head (it must be the feature's base commit), the approved spec
     digest, the plan and approval digests (W4.5) or the approval comment (legacy), the reviewer provider and model.
     `binding_sha256` covers exactly these. The observed base tip is recorded but not bound: the review is of the
     PR HEAD's diff (`subject: pr-head-diff`), not of a future merge result, which a merge step must validate itself;
  3. the current entry is reused while its binding is unchanged: one review per verified head and generation. A
     changed binding invalidates it (one notice on the PR if it was published) and a new generation starts;
  4. preconditions that make a review meaningless — the approval no longer holds (Issue changed or closed, a plan
     record that does not match its digest), the base does not match, `independence: require` unmet, a diff that
     cannot be reviewed (too large, no file) — complete the entry as ERROR without a model call;
  5. otherwise the reviewer runs at most `max_attempts` times, each attempt counted BEFORE it starts (write-ahead:
     an attempt a crash interrupted stays counted). An unavailable reviewer or a cancelled run gives the attempt
     back (after `unavailable_wait_seconds` of unavailability: ERROR); a refused run (a rule violation) is ERROR at
     once; an invalid or contradictory answer uses the attempt, and when none is left the entry is ERROR;
  6. a valid answer is checked AGAIN before it is recorded (`recheck`: the reviewer may have run for minutes): the
     PR re-read and still the publisher's verified, open candidate, the binding re-derived (head, merge base,
     approval, plan) and unchanged. A stale answer is kept as history only (the entry is invalidated with the
     original reviewed head, never published, never evidence) and nothing new is reviewed in this run; when the
     re-read fails (GitHub, `git fetch`, a busy lock) the answer is HELD — nothing is current — and the next run,
     whose own fresh binding check must pass, records it without another reviewer run;
  7. the outcome is recorded, then ONE comment is posted on the PR: an operation marker, the operation recorded
     before the request and looked for before any repost, so a crash never duplicates it.

R2: while the current entry's attempt runs in a reviewer process the Worker started (found by its process tag),
a run does nothing else for that feature — no second reviewer, no re-binding, no invalidation; once that process
is gone, its result is collected FIRST, with the context it was given (5 and 6 apply unchanged; no result: an
interrupted attempt, which stays counted).

History, outcome and evidence are different things: the record keeps every entry (history); `review_rows` and
`show` report the latest completed outcome, ERROR included (for people); `review_evidence()` returns only a current,
complete, real review outcome of exactly one PR head — and even that is advisory input, never approval to merge.

`retry` supersedes the current entry (an operator decision, recorded with its reason) and starts a new generation
for the same head. Records: `state/review/<feature>.json`. Nothing here merges, approves, marks a PR ready, changes
a feature or task state, or touches publication, completion or the approved Issue. The reviewer process gets no
GitHub credential; only this trusted code writes the comments.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, NoReturn

from .errors import (
    CoordinatorError,
    GitHubError,
    GitHubNotFoundError,
    GitHubUncertainWriteError,
    GitOperationError,
    LockBusyError,
    StateDirError,
)
from .github_api import sanitize
from .intake import IntakeRecord, issue_problem, known_problem, load_intake
from .issue_sync import SimulatedCrash
from .issues import RemoteComment, op_marker, parse_op
from .model import Feature, FeatureState, utc_now
from .plan_digest import canonical_json, plan_sha256
from .publish import PullRequestPublisher
from .review_context import ReviewContext, build_context, independence, merge_base, provider_kind
from .review_process import attempt_processes, load_context, load_result
from .review_result import Review, decide, render_comment, render_invalidation, validate_output
from .reviewer import ReviewRequest, ReviewResult, Reviewer

REVIEW_RECORD_SCHEMA = 1
ENTRY_STATUSES = ("pending", "running", "complete", "invalidated")
BINDING_KEYS = ("repository", "feature", "issue", "pr", "pr_node_id", "head_branch", "head_sha", "base_branch",
                "merge_base", "spec_sha256", "plan_sha256", "approval_sha256", "approval_ref", "reviewer", "subject")
EVIDENCE_OUTCOMES = ("PASS", "PASS_WITH_NOTES", "REQUEST_CHANGES")  # ERROR: no review happened, no evidence
_RECORD_FIELDS = frozenset({"schema_version", "id", "issue_number", "entries", "current", "updated_at", "revision"})


@dataclass
class ReviewRecord:
    """Every review of one approved Issue's feature (kind `review`, id = the feature ID)."""

    id: str
    issue_number: int
    entries: list[dict[str, Any]] = field(default_factory=list)
    current: str | None = None          # the key of the entry that applies now (None: none yet)
    updated_at: str = field(default_factory=utc_now)
    revision: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "schema_version": REVIEW_RECORD_SCHEMA}

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReviewRecord":
        """Strict: another schema, unknown fields or an unknown entry status are refused."""
        if data.get("schema_version") != REVIEW_RECORD_SCHEMA:
            raise StateDirError(f"review record {data.get('id')!r}: unsupported schema {data.get('schema_version')!r}")
        unknown = sorted(set(data) - _RECORD_FIELDS)
        if unknown:
            raise StateDirError(f"review record {data.get('id')!r} has unknown fields {unknown}")
        record = cls(**{k: v for k, v in data.items() if k != "schema_version"})
        bad = [e.get("key") for e in record.entries if e.get("status") not in ENTRY_STATUSES]
        if bad:
            raise StateDirError(f"review record {record.id!r}: entries with an unknown status {bad}")
        return record

    def entry(self, key: str | None) -> dict[str, Any] | None:
        """The entry with *key*."""
        return next((e for e in self.entries if e["key"] == key), None) if key else None


def load_review(store: Any, feature_id: str) -> ReviewRecord | None:
    """The review record of a feature (None: never reviewed)."""
    raw = store.load_record("review", feature_id)
    return ReviewRecord.from_dict(raw) if raw is not None else None


def review_evidence(store: Any, feature_id: str, pr: int, head_sha: str) -> dict[str, Any] | None:
    """The CURRENT review evidence of exactly PR *pr* at *head_sha*, from the local record only; else None.

    Not the history (`load_review`: every entry, invalidated and stale ones included) and not merely the latest
    completed outcome (`review_rows`, ERROR included): evidence is the record's current entry, complete, with a
    real review outcome (`EVIDENCE_OUTCOMES`, never ERROR), its stored binding intact (`binding_sha256`), bound to
    exactly this PR and head, and its comment posted on the PR.

    Evidence is ADVISORY and never approval to merge — REQUEST_CHANGES is evidence too. Nothing acts on it in R1. A
    later caller that does must itself, at that moment: re-derive the binding from fresh reads
    (`ReviewRunner.recheck(feature, entry)` must return []), apply its own rule for which outcome it accepts, keep
    the deterministic gates authoritative, and treat the local record as forgeable while the shared identity and
    native hardening limitations stand (github-intake.md §13.6)."""
    record = load_review(store, feature_id)
    entry = record.entry(record.current) if record else None
    if entry is None or entry["status"] != "complete" or entry["outcome"] not in EVIDENCE_OUTCOMES:
        return None
    binding = entry["binding"]
    if binding_digest(binding) != entry["binding_sha256"] or binding["pr"] != pr or binding["head_sha"] != head_sha:
        return None
    return entry if (entry.get("comment") or {}).get("status") == "posted" else None


def binding_digest(binding: Mapping[str, Any]) -> str:
    """SHA-256 of the canonical JSON of the bound identity fields."""
    return hashlib.sha256(canonical_json({k: binding.get(k) for k in BINDING_KEYS})).hexdigest()


def _epoch(stamp: str) -> float:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


class _Stopped(Exception):
    """This run ends here; why is already in the report."""


@dataclass
class ReviewReport:  # pylint: disable=too-many-instance-attributes
    """What one run did (apply) or would do (dry run)."""

    applied: bool
    feature: str
    status: str = ""                 # reviewed | unchanged | error | deferred | stopped | dry-run | running | stale
    entry: str | None = None
    outcome: str | None = None
    reviewer_runs: int = 0
    lines: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)      # dry run: what would be written
    deferred: list[str] = field(default_factory=list)
    attention: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing was deferred and nothing needs an operator."""
        return not self.deferred and not self.attention

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "ok": self.ok}


class ReviewRunner:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """One review run for one feature (see the module docstring)."""

    background = False  # R2 (review_worker.WorkerReviewRunner): an attempt runs in a separate reviewer process

    def __init__(self, coord: Any, *, reviewer: Reviewer | None = None, faults: Iterable[str] = (),
                 clock: Callable[[], float] = time.time) -> None:
        self.coord, self.store, self.config, self.git = coord, coord.store, coord.config, coord.git
        self.cfg = coord.config.github.review
        self.client = coord.sync.client
        self.publisher = PullRequestPublisher(coord)  # its identity checks only; it never runs a pass here
        self.reviewer = reviewer
        self.faults = set(faults)
        self.clock = clock

    def _fault(self, name: str) -> None:
        if name in self.faults:
            self.faults.discard(name)
            raise SimulatedCrash(name)

    # -- the run ----------------------------------------------------------------------------------

    def run(self, feature_id: str, *, apply: bool, retry: Mapping[str, str] | None = None) -> ReviewReport:
        """Review the feature's Draft PR now (dry run: reads GitHub and the checkout, writes and runs nothing).
        *retry* ({by, reason}): supersede the current entry first (an operator decision)."""
        report = ReviewReport(apply, feature_id)
        if apply and not self.cfg.enabled:
            raise CoordinatorError("github.review.enabled is false: the AI review is off until an operator enables it "
                                   "in the project configuration")
        lock = self.store.locks.review() if apply else None
        if lock is not None:
            try:
                lock.acquire()
            except LockBusyError:
                report.status = "deferred"
                report.deferred.append("another review run is in progress")
                return report
        try:
            self.run_held(feature_id, report, apply=apply, retry=retry)
        finally:
            if lock is not None:
                lock.release()
        return report

    def run_held(self, feature_id: str, report: ReviewReport, *, apply: bool = True,
                 retry: Mapping[str, str] | None = None) -> None:
        """`run` for a caller that already holds `github-review.lock` (R2: the Worker service's review step)."""
        try:
            self._run(feature_id, report, apply, retry)
        except _Stopped:
            pass

    def _stop(self, report: ReviewReport, reason: str, *, status: str = "stopped") -> NoReturn:
        report.status = status
        report.attention.append(f"{report.feature}: {sanitize(reason, 600)}")
        raise _Stopped(reason)

    def _run(self, feature_id: str, report: ReviewReport, apply: bool,  # pylint: disable=too-many-branches
             retry: Mapping[str, str] | None) -> None:
        feature = self.store.load_feature(feature_id)
        intake = load_intake(self.store, feature_id)
        if intake is None:
            self._stop(report, "not an approved GitHub Issue's feature: it is never reviewed automatically")
        assert intake is not None
        if apply and self._worker_attempt(feature_id, report):
            return
        if apply:
            self._pending_notices(feature_id, report)
        cand = feature.pull_request
        if feature.state is not FeatureState.REVIEW or cand is None or not cand.number:
            self._stop(report, f"the feature is {feature.state.value} without a recorded Draft PR: nothing to review "
                               "(publication opens the PR first)")
        assert cand is not None and cand.number is not None
        try:
            pr = self.client.get_pull_request(cand.number)
        except GitHubNotFoundError:
            pr = None
        problem = "the recorded PR no longer exists" if pr is None else self._identity_problem(feature, cand, pr)
        if problem:
            self._invalidate_current(feature_id, f"PR #{cand.number} is no longer the verified candidate: {problem}",
                                     report, apply)
            self._stop(report, f"PR #{cand.number} is not reviewed: {problem}")
        assert pr is not None
        if pr.state != "open":
            self._stop(report, f"PR #{pr.number} is {'merged' if pr.merged else 'closed'}: nothing is reviewed")
        if cand.attention:
            self._stop(report, f"publication of {feature_id} is stopped ({cand.attention.get('reason')}); resolve it "
                               "and reset publication first")
        tasks = list(self.store.load_tasks(feature).values())
        binding, problems = self._bind(feature, intake, pr, apply)
        context = ReviewContext()
        level = independence(self.config, tasks)
        if not problems and self.cfg.independence == "require" and level == "none":
            problems.append(f"github.review.independence is `require`, but the reviewer ({self.cfg.provider} / "
                            f"{self.cfg.model}) is the same provider and model as an implementing agent")
        if not problems:
            context = build_context(self.config, self.coord.root, feature=feature, record=intake, tasks=tasks,
                                    binding=binding)
            if context.problem:
                problems.append(context.problem)
        entry = self._entry(feature_id, intake, binding, problems, report, apply, retry)
        if entry is None:  # dry run, or the current entry is complete and published
            return
        if entry["status"] != "complete":
            held = entry.get("held")
            if not held:  # a held answer keeps the digests of the prompt it answered
                self._update(feature_id, entry["key"], lambda e: e.update(
                    independence=level, input_sha256=context.input_sha256, diff_sha256=context.diff_sha256,
                    coordinator_notes=context.notes, files=len(context.files)))
            if problems:
                self._complete(feature_id, entry["key"], "ERROR", "; ".join(problems), report)
            elif held:  # this run re-read the PR and re-derived the binding (above): unchanged, so it is confirmed
                self._complete(feature_id, entry["key"], held["outcome"], held["reason"], report, review=Review(
                    held["verdict"], held["summary"], held["findings"], held["limitations"]),
                               output_sha=held["output_sha256"])
            else:
                self._attempts(feature_id, entry["key"], context, report)
        self._publish(feature_id, entry["key"], report)

    def _identity_problem(self, feature: Feature, cand: Any, pr: Any) -> str | None:
        """The publisher's own checks: publish.py stays authoritative for the PR's identity and verified head."""
        return (self.publisher._repository_problem(feature)  # pylint: disable=protected-access
                or self.publisher._head_problem(feature, cand)  # pylint: disable=protected-access
                or self.publisher._pr_problem(feature, cand, pr))  # pylint: disable=protected-access

    def recheck(self, feature_id: str, entry: Mapping[str, Any]) -> list[str]:
        """Why *entry*'s binding no longer holds, from fresh reads NOW ([]: it holds): the feature still in review
        with the same PR, that PR still the publisher's verified, open candidate, publication not stopped, and the
        binding re-derived (head, merge base, approval, plan) equal to the entry's. A failed read (GitHub,
        `git fetch`, a busy lock) raises: nothing is confirmed. Run after the reviewer, before a valid answer is
        recorded; a later step must run it too before it relies on `review_evidence`."""
        bound = entry["binding"]
        feature = self.store.load_feature(feature_id)
        intake = load_intake(self.store, feature_id)
        cand = feature.pull_request
        if intake is None or feature.state is not FeatureState.REVIEW or cand is None or cand.number != bound["pr"]:
            return [f"the feature is no longer in review with PR #{bound['pr']} (now {feature.state.value})"]
        try:
            pr = self.client.get_pull_request(cand.number)
        except GitHubNotFoundError:
            return [f"PR #{cand.number} no longer exists"]
        problem = self._identity_problem(feature, cand, pr)
        if problem:
            return [f"PR #{cand.number} is no longer the verified candidate: {problem}"]
        if pr.state != "open":
            return [f"PR #{pr.number} is {'merged' if pr.merged else 'closed'}"]
        if cand.attention:
            return [f"publication of {feature_id} is stopped ({cand.attention.get('reason')})"]
        binding, problems = self._bind(feature, intake, pr, True)
        changed = [k for k in BINDING_KEYS if bound.get(k) != binding.get(k)]
        if changed:
            problems.insert(0, f"the binding changed ({', '.join(changed)})" + (
                f": head {bound['head_sha'][:12]} → {binding['head_sha'][:12]}" if "head_sha" in changed else ""))
        return problems

    # -- binding ------------------------------------------------------------------------------------

    def _bind(self, feature: Feature, intake: IntakeRecord, pr: Any,
              apply: bool) -> tuple[dict[str, Any], list[str]]:
        """The binding facts (fresh) and the problems that make this head unreviewable."""
        problems: list[str] = []
        head = pr.head_sha
        if apply:
            tip = self.git.fetch_branch(feature.base_branch, timeout=self.config.github.publish.git_timeout_seconds)
        else:  # a dry run fetches nothing: the last fetched remote-tracking branch, if any
            try:
                tip = self.git.rev_parse(f"refs/remotes/origin/{feature.base_branch}")
            except CoordinatorError:
                tip = feature.base_sha
        try:
            base = merge_base(self.coord.root, tip, head)
        except GitOperationError as exc:  # unrelated histories, a missing object: never reviewed
            base = ""
            problems.append(f"no merge base of {feature.base_branch} ({tip[:12]}) and the head: {sanitize(exc, 200)}")
        if base and base != feature.base_sha:
            problems.append(f"the merge base {base[:12]} of {feature.base_branch} ({tip[:12]}) and the head is not "
                            f"the feature's base commit {feature.base_sha[:12]}: the PR's diff is not the integrated "
                            "work")
        withdrawn = next((p for p in (known_problem(self.store, self.store.load_task(t))
                                      for t in intake.all_task_ids) if p), None) \
            or issue_problem(self.client, intake)[1]
        if withdrawn:
            problems.append(f"the approval no longer holds: {withdrawn}")
        plan = intake.plan or {}
        try:
            plan_ok = plan.get("sha256") == plan_sha256(plan.get("canonical") or {}) and plan.get("approval_sha256")
        except (TypeError, ValueError):
            plan_ok = False
        if intake.planned and not plan_ok:
            problems.append("the recorded approved plan does not match its digest or lacks its approval digest")
        binding = {"repository": self.config.github.repository, "feature": feature.id, "issue": intake.issue_number,
                   "pr": pr.number, "pr_node_id": pr.node_id, "head_branch": feature.branch, "head_sha": head,
                   "base_branch": feature.base_branch, "merge_base": base, "spec_sha256": intake.digest,
                   "plan_sha256": plan.get("sha256") if intake.planned else None,
                   "approval_sha256": plan.get("approval_sha256") if intake.planned else None,
                   "approval_ref": f"comment {intake.approval_comment_id} by {intake.approved_by}",
                   "reviewer": {"provider": self.cfg.provider, "kind": provider_kind(self.config),
                                "model": self.cfg.model},
                   "subject": "pr-head-diff", "base_tip": tip, "github_base_sha": pr.base_sha}
        return binding, problems

    # -- entries ------------------------------------------------------------------------------------

    def _record(self, feature_id: str, issue_number: int) -> ReviewRecord:
        record = load_review(self.store, feature_id)
        if record is None:
            record = ReviewRecord(id=feature_id, issue_number=issue_number)
            self.store.save_record("review", record)
            record = load_review(self.store, feature_id)
            assert record is not None
        return record

    def _now(self, feature_id: str, key: str) -> dict[str, Any]:
        """The entry as stored now (read-only)."""
        record = load_review(self.store, feature_id)
        entry = record.entry(key) if record else None
        assert entry is not None
        return entry

    def _update(self, feature_id: str, key: str, mutate: Callable[[dict[str, Any]], Any],
                event: tuple[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        with self.store.transaction():
            record = load_review(self.store, feature_id)
            assert record is not None
            entry = record.entry(key)
            assert entry is not None
            mutate(entry)
            record.updated_at = utc_now()
            self.store.save_record("review", record)
            if event is not None:
                self.store.append_event(event[0], feature=feature_id, review=key, **event[1])
        return entry

    def _entry(self, feature_id: str, intake: IntakeRecord, binding: dict[str, Any], problems: list[str],  # pylint: disable=too-many-arguments
               report: ReviewReport, apply: bool, retry: Mapping[str, str] | None) -> dict[str, Any] | None:
        """The entry this run works on: the current one while its binding holds, else a new generation."""
        digest = binding_digest(binding)
        record = load_review(self.store, feature_id)
        current = record.entry(record.current) if record else None
        if current is not None and current["status"] != "invalidated":
            changed = [k for k in BINDING_KEYS if current["binding"].get(k) != binding.get(k)]
            if retry is not None:
                self._invalidate(feature_id, current, f"superseded by an operator retry ({retry.get('by')}: "
                                                      f"{sanitize(retry.get('reason'), 300)})", report, apply)
            elif changed:
                self._invalidate(feature_id, current, f"its binding changed ({', '.join(changed)})", report, apply)
            elif problems and current["status"] == "complete" and current["outcome"] != "ERROR":
                self._invalidate(feature_id, current, "; ".join(problems), report, apply)
            else:
                report.entry = current["key"]
                if current["status"] == "complete":
                    report.status, report.outcome = "unchanged", current["outcome"]
                    report.lines.append(f"{feature_id}: review {current['key']} is {current['outcome']} for this head "
                                        "(unchanged binding; `retry` starts a new generation)")
                    if (current.get("comment") or {}).get("status") == "posted":
                        return None
                if not apply:
                    step = ("post the comment of" if current["status"] == "complete" else
                            "confirm the held answer of" if current.get("held") else "continue")
                    report.writes.append(f"{step} review {current['key']}")
                    return None
                return current
        pr, head = binding["pr"], binding["head_sha"]
        generation = 1 + max((e["generation"] for e in (record.entries if record else [])
                              if e["binding"]["pr"] == pr and e["binding"]["head_sha"] == head), default=0)
        key = f"pr{pr}-{head[:12]}-g{generation}"
        report.entry = key
        if not apply:
            report.status = "dry-run"
            report.writes.append(f"review PR #{pr} at {head[:12]} as {key} (reviewer {self.cfg.provider} / "
                                 f"{self.cfg.model}, at most {self.cfg.max_attempts} attempt(s))")
            return None
        entry = {"key": key, "generation": generation, "status": "pending", "outcome": None, "reason": "",
                 "binding": binding, "binding_sha256": digest, "independence": None, "input_sha256": "",
                 "diff_sha256": "", "files": 0, "coordinator_notes": [], "attempts": 0, "attempt_log": [],
                 "running": None, "unavailable_since": None, "verdict": None, "summary": "", "findings": [],
                 "limitations": [], "output_sha256": "", "held": None, "comment": None, "invalidated": None,
                 "notice": None, "retry": dict(retry) if retry else None, "created_at": utc_now(), "completed_at": ""}
        with self.store.transaction():  # write-ahead: the entry exists before anything runs
            record = self._record(feature_id, intake.issue_number)
            record.entries.append(entry)
            record.current, record.updated_at = key, utc_now()
            self.store.save_record("review", record)
            self.store.append_event("review-started", feature=feature_id, review=key, pr=pr, head=head,
                                    binding=digest, retry=bool(retry))
        return entry

    def _complete(self, feature_id: str, key: str, outcome: str, reason: str, report: ReviewReport,  # pylint: disable=too-many-arguments
                  review: Any = None, output_sha: str = "", stale: str = "") -> None:
        """Record the outcome. *stale* (why the binding no longer held after the review): in the same write the
        entry is invalidated — the outcome and the original reviewed head stay as history, never current."""
        def done(e: dict[str, Any]) -> None:
            e.update(status="complete", outcome=outcome, reason=sanitize(reason, 1500), running=None,
                     completed_at=utc_now(), output_sha256=output_sha)
            if review is not None:
                e.update(verdict=review.verdict, summary=review.summary, findings=review.findings,
                         limitations=review.limitations)
            if stale:
                e.update(status="invalidated", invalidated={
                    "reason": sanitize(f"stale when the review finished: {stale}", 600), "at": utc_now()})
        self._update(feature_id, key, done, event=("review-stale" if stale else "review-completed", {
            "outcome": outcome, "reason": sanitize(stale or reason, 300)}))
        if stale:
            report.status = "stale"
            report.attention.append(f"{feature_id}: the {outcome} answer of review {key} is history only — the "
                                    f"binding no longer held when the reviewer finished: {sanitize(stale, 400)}. "
                                    "Nothing was published and nothing new was reviewed; run again once the "
                                    "publisher has verified the current head")
        else:
            report.status = "error" if outcome == "ERROR" else "reviewed"
            report.outcome = outcome
            report.lines.append(f"{feature_id}: review {key}: {outcome} — {sanitize(reason, 300)}")
            if outcome == "ERROR":
                report.attention.append(f"{feature_id}: review {key} is ERROR: {sanitize(reason, 300)}")
        self._fault(f"crash-after-review-recorded:{feature_id}")

    # -- the reviewer --------------------------------------------------------------------------------

    def _attempts(self, feature_id: str, key: str, context: ReviewContext, report: ReviewReport) -> None:
        entry = self._now(feature_id, key)
        if entry["running"]:  # a crash interrupted that attempt: it stays counted
            self._update(feature_id, key, self._interrupted, event=("review-attempt", {"status": "interrupted"}))
        if self.background and self._now(feature_id, key)["attempts"] < self.cfg.max_attempts:
            self._background(feature_id, key, context, report)  # R2: ONE attempt, collected by a later step
            return
        reviewer = self.reviewer or self._default_reviewer()
        last = "an interrupted attempt"
        while (entry := self._now(feature_id, key))["attempts"] < self.cfg.max_attempts:
            attempt = entry["attempts"] + 1
            run_dir = self.store.root / "runs" / "review" / key / f"attempt-{attempt}"
            self._update(feature_id, key, lambda e, a=attempt, d=run_dir: e.update(  # counted BEFORE the run
                attempts=a, status="running", running={"attempt": a, "started_at": utc_now(), "run_dir": str(d)}))
            self._fault(f"crash-after-attempt-counted:{feature_id}")
            run_dir.mkdir(parents=True, exist_ok=True)
            result = reviewer.review(ReviewRequest(key=key, prompt=context.prompt, schema=context.schema,
                                                   run_dir=run_dir, timeout_seconds=self.cfg.timeout_seconds))
            report.reviewer_runs += 1
            last = self._handle(feature_id, key, attempt, result, context, report)
            if last is None:
                return
        self._complete(feature_id, key, "ERROR", f"no valid review in {self.cfg.max_attempts} attempt(s); last: "
                                                 f"{last}", report)

    def _background(self, feature_id: str, key: str, context: ReviewContext, report: ReviewReport) -> None:
        """R2: start one attempt in a separate reviewer process (review_worker.WorkerReviewRunner)."""
        raise NotImplementedError

    def _handle(self, feature_id: str, key: str, attempt: int, result: ReviewResult,  # pylint: disable=too-many-arguments
                context: ReviewContext, report: ReviewReport) -> str | None:
        """One reviewer result — of a run here or of a Worker reviewer process. None: no further attempt now (given
        back, refused, a valid answer recorded, held or stale); else the attempt was used without a valid answer
        (logged) and why is returned."""
        log = {"attempt": attempt, "status": result.status, "error": sanitize(result.error, 600),
               "details": {k: result.details.get(k) for k in ("auth", "cli_version", "model", "reported_model",
                                                              "api_key_source", "exit_code", "duration_seconds")
                           if k in result.details}, "ended_at": utc_now()}
        if result.status in ("unavailable", "cancelled"):
            self._given_back(feature_id, key, result, log, report)
            return None
        if result.status == "refused":
            self._log(feature_id, key, log)
            self._complete(feature_id, key, "ERROR", f"the reviewer run was refused: {result.error}", report)
            return None
        if result.status == "ok" and self._judge(feature_id, key, result, context, log, report):
            return None
        self._log(feature_id, key, log)
        return log["error"] or result.status

    def _worker_attempt(self, feature_id: str, report: ReviewReport) -> bool:
        """R2: the current entry's attempt runs — or ran — in a reviewer process the Worker service started. While
        any process carrying its tag lives, nothing else happens for this feature (no second reviewer, no
        re-binding, no invalidation). Once they are gone its result is collected FIRST, with the context it was
        given, through the same judgement as a run here. True: this run ends here."""
        record = load_review(self.store, feature_id)
        entry = record.entry(record.current) if record else None
        if entry is None or not (entry.get("running") or {}).get("tag"):
            return False
        key = report.entry = entry["key"]
        state, pids = attempt_processes(entry["running"])
        if state != "gone":
            report.status = "running"
            report.deferred.append(f"{feature_id}: attempt {entry['running']['attempt']} of review {key} runs in a "
                                   f"reviewer process the Worker service started ({state}, pids {pids}); the "
                                   "service collects its result")
            return True
        last = self._collect(feature_id, key, report)
        entry = self._now(feature_id, key)
        if last is not None and entry["status"] == "pending" and entry["attempts"] >= self.cfg.max_attempts:
            self._complete(feature_id, key, "ERROR", f"no valid review in {self.cfg.max_attempts} attempt(s); "
                                                     f"last: {last}", report)
        self._publish(feature_id, key, report)
        return True

    def _collect(self, feature_id: str, key: str, report: ReviewReport) -> str | None:
        """Judge the finished Worker attempt (its processes are gone): `_handle`'s answer. No result — or one that
        is not this attempt's — is an interrupted attempt; past its deadline (the service's watchdog stopped it),
        anything but a valid answer is a timeout. Both stay counted."""
        running = self._now(feature_id, key)["running"]
        context, result = load_context(key, running), load_result(key, running)
        if context is None or result is None:
            self._update(feature_id, key, self._interrupted, event=("review-attempt", {"status": "interrupted"}))
            return "the reviewer process ended without a result (it crashed or was killed)"
        watchdog = running.get("watchdog") or {}
        if watchdog.get("kind") == "deadline" and result.status != "ok":
            result = ReviewResult("timeout", error=f"the reviewer process was stopped at its deadline "
                                                   f"({watchdog.get('reason')})", details=result.details)
        return self._handle(feature_id, key, int(running["attempt"]), result, context, report)

    @staticmethod
    def _interrupted(entry: dict[str, Any]) -> None:
        entry["attempt_log"].append({**entry["running"], "status": "interrupted", "ended_at": utc_now()})
        entry.update(running=None, status="pending")

    def _log(self, feature_id: str, key: str, log: dict[str, Any]) -> None:
        self._update(feature_id, key, lambda e: (e["attempt_log"].append(log), e.update(running=None)),
                     event=("review-attempt", {"attempt": log["attempt"], "status": log["status"]}))

    def _given_back(self, feature_id: str, key: str, result: ReviewResult, log: dict[str, Any],
                    report: ReviewReport) -> None:
        """Unavailable or cancelled: nothing was judged; the attempt is given back (bounded by the wait)."""
        now = self.clock()

        def back(e: dict[str, Any]) -> None:
            e["attempt_log"].append(log)
            e.update(attempts=max(e["attempts"] - 1, 0), running=None, status="pending")
            if result.status == "unavailable" and not e["unavailable_since"]:
                e["unavailable_since"] = {"at": utc_now(), "epoch": now}
        entry = self._update(feature_id, key, back, event=("review-deferred", {"status": result.status,
                                                                               "error": log["error"][:300]}))
        since = entry["unavailable_since"]
        waited = now - float(since["epoch"]) if since else 0.0
        if result.status == "unavailable" and since and waited >= self.cfg.unavailable_wait_seconds:
            self._complete(feature_id, key, "ERROR", f"the reviewer has been unavailable since {since['at']}: "
                                                     f"{result.error}", report)
            return
        report.status = "deferred"
        report.deferred.append(f"{feature_id}: the reviewer is {result.status}: {sanitize(result.error, 300)} "
                               "(no attempt used; run again later)")

    def _judge(self, feature_id: str, key: str, result: ReviewResult, context: ReviewContext,  # pylint: disable=too-many-arguments
               log: dict[str, Any], report: ReviewReport) -> bool:
        """Validate an answer; True when the entry is complete."""
        entry = self._update(feature_id, key, lambda e: e.update(unavailable_since=None))
        output_sha = hashlib.sha256(json.dumps(result.output, sort_keys=True, ensure_ascii=False,
                                               default=str).encode("utf-8")).hexdigest()
        review, problems = validate_output(result.output, head_sha=entry["binding"]["head_sha"], files=context.files,
                                           max_findings=self.cfg.max_findings)
        if review is not None:
            outcome, reason = decide(review, context.notes)
            if outcome != "ERROR":
                self._confirm(feature_id, key, (outcome, reason, review, output_sha), log, report)
                return True
            problems = [reason]
        log.update(status="invalid", error=sanitize("; ".join(problems[:8]), 600), output_sha256=output_sha)
        return False

    def _confirm(self, feature_id: str, key: str, answer: tuple[str, str, Review, str], log: dict[str, Any],
                 report: ReviewReport) -> None:
        """A valid answer becomes the current review only if its binding still holds NOW (`recheck`); a stale one
        is history only; a failed re-read HOLDS it (nothing current) for the next run to confirm or discard."""
        outcome, reason, review, output_sha = answer
        try:
            stale = self.recheck(feature_id, self._now(feature_id, key))
        except (GitHubError, GitOperationError, LockBusyError) as exc:
            held = {"outcome": outcome, "reason": reason, "verdict": review.verdict, "summary": review.summary,
                    "findings": review.findings, "limitations": review.limitations, "output_sha256": output_sha,
                    "error": sanitize(exc, 300), "at": utc_now()}
            self._update(feature_id, key, lambda e: (e["attempt_log"].append({**log, "status": "held"}), e.update(
                held=held, running=None, status="pending")), event=("review-held", {"outcome": outcome,
                                                                                    "error": held["error"]}))
            report.status = "deferred"
            report.deferred.append(f"{feature_id}: the {outcome} answer of review {key} is held, not recorded: the "
                                   f"binding could not be re-read after the review ({held['error']}); run again — "
                                   "the next run checks the binding first and records it without a reviewer run")
            return
        self._log(feature_id, key, log)
        self._complete(feature_id, key, outcome, reason, report, review=review, output_sha=output_sha,
                       stale="; ".join(stale))

    def _default_reviewer(self) -> Reviewer:
        from .reviewer import ClaudeCodeReviewer  # pylint: disable=import-outside-toplevel
        return ClaudeCodeReviewer(self.config)

    # -- invalidation ---------------------------------------------------------------------------------

    def _invalidate_current(self, feature_id: str, reason: str, report: ReviewReport, apply: bool) -> None:
        record = load_review(self.store, feature_id)
        current = record.entry(record.current) if record else None
        if current is not None and current["status"] != "invalidated":
            self._invalidate(feature_id, current, reason, report, apply)

    def _invalidate(self, feature_id: str, entry: dict[str, Any], reason: str, report: ReviewReport,
                    apply: bool) -> None:
        if not apply:
            report.writes.append(f"invalidate review {entry['key']}: {sanitize(reason, 300)}")
            return
        self._update(feature_id, entry["key"], lambda e: e.update(
            status="invalidated", running=None, invalidated={"reason": sanitize(reason, 600), "at": utc_now()}),
                     event=("review-invalidated", {"reason": sanitize(reason, 300)}))
        report.lines.append(f"{feature_id}: review {entry['key']} invalidated: {sanitize(reason, 300)}")
        self._notice(feature_id, entry["key"], report)

    def _pending_notices(self, feature_id: str, report: ReviewReport) -> None:
        """After a crash: finish the invalidation notices that were due."""
        record = load_review(self.store, feature_id)
        for entry in record.entries if record else []:
            if entry["status"] == "invalidated" and (entry.get("notice") or {}).get("status") != "posted":
                self._notice(feature_id, entry["key"], report)

    # -- comments (trusted code only; idempotent) -------------------------------------------------------

    def _own_op(self, number: int, key: str, op: str) -> RemoteComment | None:
        viewer = self.coord.sync.viewer()
        for comment in self.client.list_comments(number):
            if parse_op(comment.body) == (self.config.github.coordinator_id, key, op) and comment.author == viewer:
                return comment
        return None

    def _post(self, feature_id: str, key: str, slot: str, op: str, body: str, report: ReviewReport) -> None:
        """Post *body* once on the entry's PR: recorded first, looked for before any (re)post."""
        entry = self._now(feature_id, key)
        if (entry.get(slot) or {}).get("status") == "posted":
            return
        number, marker_key = entry["binding"]["pr"], f"{feature_id}/review"
        self._update(feature_id, key, lambda e: e.update({slot: {"op": op, "status": "pending", "at": utc_now()}}))
        found = self._own_op(number, marker_key, op)
        if found is None:
            try:
                marker = op_marker(self.config.github.coordinator_id, marker_key, op)
                found = self.client.create_comment(number, f"{marker}\n{body}")
            except GitHubUncertainWriteError as exc:
                report.deferred.append(f"{feature_id}: the {slot} for {key} may have been posted "
                                       f"({sanitize(exc, 200)}); the next run looks for it first")
                return
            self._fault(f"crash-after-review-{slot}:{feature_id}")
        self._update(feature_id, key, lambda e, c=found: e.update({slot: {
            "op": op, "status": "posted", "id": c.id, "url": c.url, "at": utc_now()}}),
                     event=(f"review-{slot}-posted", {"pr": number, "op": op}))
        report.lines.append(f"{feature_id}: posted the {slot} of {key} on PR #{number}")

    def _publish(self, feature_id: str, key: str, report: ReviewReport) -> None:
        entry = self._now(feature_id, key)
        if entry["status"] != "complete":
            return
        self._post(feature_id, key, "comment", f"review-{key}", render_comment(entry), report)

    def _notice(self, feature_id: str, key: str, report: ReviewReport) -> None:
        """The invalidation notice: only when the review's comment was (or may have been) posted."""
        entry = self._now(feature_id, key)
        comment = entry.get("comment") or {}
        if comment.get("status") == "pending":  # a crash between the request and its record: look first
            found = self._own_op(entry["binding"]["pr"], f"{feature_id}/review", comment["op"])
            if found is not None:
                entry = self._update(feature_id, key, lambda e, c=found: e["comment"].update(
                    status="posted", id=c.id, url=c.url))
        if (entry.get("comment") or {}).get("status") == "posted":
            self._post(feature_id, key, "notice", f"review-{key}-invalidated", render_invalidation(entry), report)


def review_rows(store: Any) -> list[dict[str, Any]]:
    """Every review record with its current entry (read-only, local)."""
    rows = []
    for raw in store.list_records("review"):
        record = ReviewRecord.from_dict(raw)
        entry = record.entry(record.current) or {}
        binding = entry.get("binding") or {}
        rows.append({"feature": record.id, "issue": record.issue_number, "entries": len(record.entries),
                     "current": record.current, "status": entry.get("status"), "outcome": entry.get("outcome"),
                     "pr": binding.get("pr"), "head": binding.get("head_sha"),
                     "independence": entry.get("independence"),
                     "attempts": entry.get("attempts"), "reason": entry.get("reason"),
                     "comment": (entry.get("comment") or {}).get("url"),
                     "invalidated": (entry.get("invalidated") or {}).get("reason")})
    return rows


def run_dir_of(store: Any, key: str) -> Path:
    """Where a review's attempts keep their prompt and the reviewer's output."""
    return store.root / "runs" / "review" / key
