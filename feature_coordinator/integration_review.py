"""W4.7 (M4): `integration review --job J` — the independent AI review of the EXACT integration candidate M3 built and
validated (design §4 IN_REVIEW, §5.6 evidence binding, §8.1 `review`).

    python -m feature_coordinator integration review --job J [--retry --reason TEXT] [--actor NAME] [--apply] [--json]

It extends the integration job (same record, same state machine, same append-only evidence) and reuses the W4.6.1
reviewer unchanged: `github.review`'s provider and model (Claude Code on the operator's subscription — never an API
key, never another provider), the reviewer process of review_process.py (request/result files, process tag, the
supervisor's deadline and cancellation), reviewer.ClaudeCodeReviewer (no tools, no MCP server, an empty working
directory, the post-run stream checks), review_result.validate_output / decide (the answer's shape, findings on
changed files, the outcome) and the reviewer context's sanitizers and confidentiality guard
(integration_review_context.py). `github.review.enabled` (the Worker's automatic Draft PR review) is NOT required;
`github.integration.enabled` is.

Order of one run:
  0. the job exists; a terminal job is reported as it is. VALIDATING (M3 validated) or IN_REVIEW (a review to start,
     resume or decide); AWAITING_MERGE_APPROVAL / NEEDS_REPAIR report their decision (a repeated review re-verifies
     and reuses it — nothing runs again — unless the current independence policy refuses it). Its effective inspect
     approval is valid NOW (signature, operator, expiry) and is the one the candidate was built under — else
     `no-approval`, also for a dry run. The reviewer is eligible (review_config.reviewer_problem).
  1. with --apply: `locks/integration-<job>.lock` (shared with inspect and validate; non-blocking: a concurrent run is
     refused). Without --apply (default) everything runs read-only (the candidate is rebuilt in a temporary repository),
     no reviewer runs and the report says what --apply would do.
  2. recovery FIRST: an attempt no run names (its reviewer process runs, or a crash interrupted it) — a live process
     (found by its tag) is waited for; then its result file is collected: no second reviewer while one may still
     answer. No result: `not-started` when the CLI never launched (no model request; given back), else `interrupted`
     (counted).
  3. the checks (`_verify`, repeated after every reviewer run and before every decision): the Issue is an open Issue
     (deferred / issue-blocked); origin's source and base branches and the pinned PR unchanged (M2's check_pinned,
     the PR change confirmed by the complete lookup → STALE; unreadable → deferred); the candidate REBUILT in the job's
     candidate repository is exactly the recorded head and tree (else STALE; a crafted object → FAILED); its paths
     still authorized under the current policy and the approval (else FAILED); M3's validation is current for the gate
     plan the current configuration derives (integration_validate.validated_for semantics; else `not-validated`).
  4. a valid answer of the newest attempt that awaits its decision (the checks could not complete, or a crash came
     first) is decided now — without another reviewer run — if the CURRENT independence policy accepts the attempt's
     recorded independence (else `independence-refused`: the answer waits as history).
  5. the round's budget: `github.review.max_attempts` counted runs; a refused run (a rule violation) or an exhausted
     budget records an ERROR decision: the job STAYS IN_REVIEW with `attention` until an operator starts the next
     round (`--retry --reason`) or aborts. Unavailable, cancelled and not-started runs are given back.
  6. `github.review.independence: require` refuses (the source's author is unknown: integration_review_context); the
     reviewer context of the exact candidate (`base_sha → candidate.head_sha`; a change list that is not the verified
     path list, a diff above `max_diff_characters` or a guard problem → `unreviewable`, no model call); the reviewer's
     availability without a model request (CLI, flags, subscription login) → else `reviewer-unavailable`.
  7. write-ahead: a `review-attempt` evidence entry (candidate head/tree, pinned SHAs, approval, the PASSED validation
     decision and its plan, round, reviewer, independence, input digests, process tag, run directory, deadline) —
     with VALIDATING → IN_REVIEW for the first one — recorded only while the job is still in the state the checks saw,
     its validation still current, the approval the same and inside its window at a FRESH clock reading and the
     allowed-signers file unchanged — after the pinned branches and PR were read AGAIN, right before (_final_pins).
     Then request.json, then the reviewer process (`integration review-exec RUN_DIR`, its own session; it runs only
     when its own configuration yields the attempt's reviewer identity and its independence policy accepts the
     attempt's) is started and waited for (deadline, operator abort, a stop signal: its processes are stopped; the
     attempt's deadline makes a late non-answer a timeout).
  8. the result becomes a `review-run` entry FIRST (status, counted, the validated answer and outcome, digests of the
     answer and of the result file; an answer whose run facts name another model than the attempt's is `refused`,
     unused): from here a crash never loses the answer. Then the checks of step 3 run AGAIN; a
     change → STALE / FAILED (the answer stays history only); unreadable, an invalid approval, a closed Issue or an
     outdated validation → no decision (the answer waits, step 4).
  9. the decision (`review` entry, repeating the attempt's bindings and the run's answer), recorded only while the job
     is IN_REVIEW, the decided attempt is still the newest and its run the one judged, the validation it relied on is
     still the newest and current, the approval the same and in its window (fresh clock), the allowed-signers file
     unchanged, the checkout configuration free of execution settings, the pinned branches and PR read AGAIN right
     before (moved → STALE, unreadable → no decision) and the independence policy in effect THEN — the configuration
     read again (*reload*), the stricter one deciding — accepting it (else `independence-refused`, step 4):
       PASS / PASS_WITH_NOTES  → AWAITING_MERGE_APPROVAL (findings and limitations retained)
       REQUEST_CHANGES         → NEEDS_REPAIR with `attention` (the blocking findings); the source is never modified
       ERROR                   → step 5

Nothing here creates, edits or comments on a PR, pushes, updates a ref, merges or modifies the source; the reviewer
process receives no GitHub credential. `reviewed_for` is the check M5 must make before relying on a review.
"""

from __future__ import annotations

import contextlib
import secrets
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, NoReturn

from .config import ProjectConfig
from .errors import CoordinatorError, GitHubError, GitHubNotFoundError, LockBusyError
from .integration import (
    JOB_TRANSITIONS,
    IntegrationError,
    IntegrationJob,
    IntegrationJobs,
    JobState,
    clean_text,
    current_review,
    effective_approval,
    evidence_digest,
    job_summary,
    latest_evidence,
    open_attempt,
    open_review_attempt,
    pending_answer,
    recorded_failures,
    review_attempts,
    review_decision,
    review_run,
    validated,
)
from .integration_approval import SshSignatureVerifier, effective_approval_problems, now_utc, time_problems
from .integration_candidate import Candidate, CandidateArea, CandidateGit, build_candidate, ensure_repository, \
    identity_problem
from .integration_intake import MAX_LISTED, ReadOnlyGit
from .integration_objects import HistoryRefused, HistoryUnverifiable
from .integration_review_context import IssueSpec, ReviewGit, build_candidate_context, independence_problem, \
    integration_independence, policy_problem, reviewer_identity, verify_changed_blobs
from .integration_review_process import POLL_SECONDS, REVIEW_SLACK_SECONDS, AttemptProcess, Starter, deadline_text, \
    running_of, run_entry
from .integration_schema import BLOCKING, UNCOUNTED_RUN_STATUSES
from .integration_validate import IntegrationValidator, current_plan_sha256, validated_for
from .issue_sync import SimulatedCrash
from .locks import FileLock
from .model import utc_now
from .review_config import reviewer_problem
from .review_context import ReviewContext
from .review_process import attempt_processes
from .supervisor import stop_requested

REVIEWABLE_STATES = frozenset({JobState.VALIDATING, JobState.IN_REVIEW})
DECIDED_STATES = frozenset({JobState.AWAITING_MERGE_APPROVAL, JobState.NEEDS_REPAIR})
OK_OUTCOMES = ("reviewed", "would-review", "would-recover", "would-decide")


@dataclass
class ReviewJobReport:  # pylint: disable=too-many-instance-attributes
    """What one review run did (apply) or would do (dry run)."""

    applied: bool
    outcome: str = ""
    changed: bool = False
    approval: str | None = None
    job: dict[str, Any] | None = None
    checks: list[dict[str, str]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    candidate: dict[str, Any] | None = None
    reviewer: dict[str, Any] | None = None
    independence: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    review: dict[str, Any] | None = None
    reviewer_runs: int = 0
    messages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the candidate is reviewed PASS / PASS_WITH_NOTES (or a dry run found nothing in the way)."""
        return self.outcome in OK_OUTCOMES

    def note(self, name: str, status: str, detail: str = "") -> None:
        """Record one check: pass | fail | stale | deferred | blocked | attention."""
        self.checks.append({"check": name, "status": status, "detail": clean_text(detail, 300)})

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "ok": self.ok}


@dataclass
class _Checked:
    """What the checks of one pass established (step 3)."""

    candidate: Candidate
    spec: IssueSpec
    validation: dict[str, Any]
    plan_sha256: str
    repo: Path


class _Stop(Exception):
    """This run ends here; the report says why."""


class _Undecided(Exception):
    def __init__(self, reason: str, job: IntegrationJob) -> None:
        super().__init__(reason)
        self.reason, self.job = reason, job


class IntegrationReviewer:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """The independent AI review of integration candidates for one checkout and state directory (module docstring)."""

    def __init__(self, store: Any, config: ProjectConfig, git: ReadOnlyGit, *,  # pylint: disable=too-many-arguments
                 verifier: SshSignatureVerifier, tracker: Callable[[], Any] | None = None,
                 start: Starter | None = None, preflight: Callable[[], str] | None = None, faults: Any = (),
                 clock: Callable[[], datetime] = now_utc, wall: Callable[[], float] = time.time,
                 stop: Callable[[], bool] = stop_requested, poll_seconds: float = POLL_SECONDS,
                 reload: Callable[[], ProjectConfig] | None = None) -> None:
        self.store, self.config, self.git, self.reload = store, config, git, reload
        self.cfg = config.github.review
        self.jobs = IntegrationJobs(store)
        self.verifier = verifier
        self.validator = IntegrationValidator(store, config, git, verifier=verifier, tracker=tracker, clock=clock)
        self.inspector = self.validator.inspector
        self.preflight = preflight or self._available
        self.faults = set(faults)
        self.clock, self.wall = clock, wall
        self.process = AttemptProcess(config, self.jobs, start=start, wall=wall, stop=stop, fault=self._fault,
                                      poll_seconds=poll_seconds)

    def _fault(self, name: str) -> None:
        if name in self.faults:
            self.faults.discard(name)
            raise SimulatedCrash(name)

    def _available(self) -> str:
        from .review_worker import reviewer_available  # pylint: disable=import-outside-toplevel
        return reviewer_available(self.config)

    # -- the run -----------------------------------------------------------------------------------

    def run(self, job_id: str, *, apply: bool, actor: str = "operator",
            retry: Mapping[str, str] | None = None) -> ReviewJobReport:
        """Review *job_id* (module docstring); with *apply*, run or recover the reviewer and record. *retry* ({by,
        reason}): start the next round after an ERROR decision. IntegrationError (nothing recorded) for a precondition
        that is not the job's fault."""
        with self.git.session():
            report = ReviewJobReport(applied=apply, reviewer=reviewer_identity(self.config),
                                     independence=integration_independence(self.config))
            try:
                self._run(job_id, report, apply=apply, actor=actor, retry=retry)
            except _Stop:
                pass
            if apply:
                report.job = job_summary(self._tidy(job_id))   # the persisted job, whatever path the run took
            return report

    def _run(self, job_id: str, report: ReviewJobReport, *, apply: bool, actor: str,  # pylint: disable=too-many-branches
             retry: Mapping[str, str] | None) -> None:
        job = self.jobs.load(job_id)
        report.job = job_summary(job)
        if job.terminal:
            report.outcome = "terminal"
            report.messages.append(f"{job_id} is {job.state.value} (terminal) and is never reviewed again")
            return
        if job.state not in REVIEWABLE_STATES | DECIDED_STATES:
            raise IntegrationError(f"{job_id} is {job.state.value}: review applies to a validated job (VALIDATING, "
                                   "after `integration validate --apply`) or to a job in review (IN_REVIEW)")
        if job.candidate is None:
            raise IntegrationError(f"conflicting state: {job_id} is {job.state.value} but carries no candidate; it is "
                                   "never reviewed in that state — abort it (`integration abort`)")
        if retry is not None and not self._round_ended(job):
            raise IntegrationError(f"--retry starts the next review round only after an ERROR decision of the current "
                                   f"round; {job_id} is {job.state.value} without one — nothing was done")
        if apply and not self.config.github.integration.enabled:
            raise IntegrationError("github.integration.enabled is false: reviews are recorded only once an operator "
                                   "enables integration jobs")
        if job.state is JobState.NEEDS_REPAIR:
            self._needs_repair(job, report)
            return
        problem = reviewer_problem(self.config)
        if problem:
            self._refuse(job, "reviewer-ineligible", [f"the configured reviewer cannot be used: {problem}"], report,
                         apply)
        approval, problems = effective_approval_problems(job, config=self.config, verifier=self.verifier,
                                                         now=self.clock())
        built = self._built_entry(job)
        if not problems and approval is not None and built["approval"] != approval["id"]:
            problems = [f"the effective approval {approval['id']} is not {built['approval']}, under which the "
                        "candidate was built"]
        if problems:
            self._refuse(job, "no-approval", problems, report, apply)
        assert approval is not None
        report.approval = approval["id"]
        self.validator._preflight()  # pylint: disable=protected-access  # origin and checkout fit, as for validate
        if not apply:
            self._dry_run(job, approval, report, retry)
            return
        lock = FileLock(self.store.locks.dir / f"integration-{job_id}.lock")
        try:
            lock.acquire(timeout=0)
        except LockBusyError as exc:
            raise IntegrationError(f"another integration operation on {job_id} (inspect, validate or review) is "
                                   "running; nothing was done") from exc
        try:
            current = self.jobs.load(job_id)
            if current.state is not job.state:
                self._meanwhile(report, current)
            if current.state in DECIDED_STATES:
                self._reverify(current, approval, report, actor, apply=True)
            else:
                self._apply(current, approval, report, actor, retry)
        finally:
            lock.release()

    def _tidy(self, job_id: str) -> IntegrationJob:
        """After an applied run: a terminal job keeps nothing of its workspace (the repository goes after an abort,
        like `integration abort --apply`); review run directories and gate logs stay. The job as persisted."""
        final = self.jobs.load(job_id)
        if final.terminal:
            area = CandidateArea(self.store.root, job_id)
            if final.state is JobState.ABORTED:
                area.discard()
            else:
                area.discard_worktree()
        return final

    @staticmethod
    def _built_entry(job: IntegrationJob) -> dict[str, Any]:
        assert job.candidate is not None
        return next(entry for entry in job.evidence if entry["id"] == job.candidate["evidence"])

    # -- reports -------------------------------------------------------------------------------------

    def _refuse(self, job: IntegrationJob, outcome: str, reasons: list[str], report: ReviewJobReport, apply: bool,
                message: str = "nothing changed") -> NoReturn:
        report.outcome, report.reasons = outcome, [clean_text(r) for r in reasons]
        report.messages.append(message)
        if apply:
            with self.store.transaction():
                self.store.append_event("integration-review-refused", job=job.job_id, outcome=outcome,
                                        reason=clean_text(reasons[0]))
        raise _Stop(outcome)

    @staticmethod
    def _meanwhile(report: ReviewJobReport, job: IntegrationJob) -> NoReturn:
        report.outcome, report.job = "changed-meanwhile", job_summary(job)
        report.messages.append(f"{job.job_id} changed while the review ran (now {job.state.value}); nothing more was "
                               "changed by this run")
        raise _Stop("meanwhile")

    def _deferred(self, job: IntegrationJob, reason: str, report: ReviewJobReport, apply: bool,
                  outcome: str = "deferred") -> NoReturn:
        shown = {"deferred": "would-defer"}.get(outcome, outcome) if not apply else outcome
        report.outcome, report.reasons = shown, [clean_text(reason)]
        report.messages.append(f"{job.job_id} is unchanged ({job.state.value}): {clean_text(reason)} — repeat the "
                               "same command later")
        if apply:
            with self.store.transaction():
                self.store.append_event(f"integration-review-{outcome}", job=job.job_id, reason=clean_text(reason))
        raise _Stop(outcome)

    def _final(self, job: IntegrationJob, target: JobState, reasons: list[str], observed: Mapping[str, Any],
               report: ReviewJobReport, actor: str, apply: bool) -> NoReturn:
        """STALE or FAILED from the job's current state, with `attention` (dry run: would-…). A state without that
        edge (AWAITING_MERGE_APPROVAL has no FAILED) is reported `not-current` and left unchanged."""
        reasons = [clean_text(r) for r in reasons] or [target.value]
        name = {JobState.STALE: "stale", JobState.FAILED: "failed"}[target]
        if target not in JOB_TRANSITIONS[job.state]:
            report.outcome, report.reasons = "not-current", reasons
            report.messages.append(f"{job.job_id} is {job.state.value}; its review no longer applies ({name}), and "
                                   "M5 refuses to merge it")
            raise _Stop("not-current")
        if not apply:
            report.outcome, report.reasons = f"would-{'stale' if name == 'stale' else 'fail'}", reasons
            raise _Stop(name)
        attention = {"reason": reasons[0], "reasons": reasons[:MAX_LISTED], "observed": dict(observed),
                     "since": utc_now()}
        current, changed = self.jobs.transition(job.job_id, target, "; ".join(reasons), actor=actor,
                                                expect=(job.state,), attention=attention)
        if current.state is not target:
            self._meanwhile(report, current)
        report.changed = report.changed or changed
        CandidateArea(self.store.root, job.job_id).discard_worktree()
        report.outcome, report.reasons, report.job = name, reasons, job_summary(current)
        raise _Stop(name)

    # -- step 3: the checks --------------------------------------------------------------------------

    def _issue(self, job: IntegrationJob, report: ReviewJobReport, apply: bool) -> IssueSpec:
        tracker = self.inspector.tracker()
        number, issue, failure = job.issue, None, ""
        try:
            if tracker is None:
                raise GitHubError("no GitHub client is configured")
            issue = tracker.get_issue(number)
        except GitHubNotFoundError:
            failure = (f"Issue #{number} cannot be found (HTTP 404): check the GitHub login's access to "
                       f"{self.config.github.repository}")
        except (GitHubError, OSError) as exc:
            failure = f"Issue #{number} could not be read: {exc}"
        if issue is None:
            report.note("issue", "deferred", failure)
            self._deferred(job, failure, report, apply)
        problems = []
        if issue.number != number:
            problems.append(f"GitHub answered for #{issue.number}")
        if issue.is_pull_request:
            problems.append("it is a pull request, not an Issue")
        if issue.state != "open":
            problems.append(f"it is {issue.state}" + (f" ({issue.state_reason})" if issue.state_reason else ""))
        if problems:
            reason = f"Issue #{number} of the job does not justify a review: {'; '.join(problems)}"
            report.note("issue", "blocked", reason)
            self._deferred(job, reason, report, apply, outcome="issue-blocked")
        report.note("issue", "pass", f"Issue #{number} is open in {self.config.github.repository}")
        return IssueSpec(number, issue.title or "", issue.body or "")

    def _verify(self, job: IntegrationJob, approval: dict[str, Any], report: ReviewJobReport, actor: str, *,  # pylint: disable=too-many-arguments
                apply: bool, scratch: Path | None = None, when: str = "") -> _Checked:
        """Step 3 (module docstring): every check a review (or its decision) needs; raises _Stop with the report filled
        when the run ends here. *scratch*: a temporary directory for the dry run's candidate repository."""
        spec = self._issue(job, report, apply)
        self._pinned(job, report, actor, apply=apply, when=when)
        candidate, repo = self._rebuild(job, report, actor, apply=apply, scratch=scratch)
        classified = self.validator._classify(candidate, approval)  # pylint: disable=protected-access
        if classified.refusals or classified.required:
            reasons = classified.refusals or self.validator._authorization_reasons(classified)  # pylint: disable=protected-access
            self._final(job, JobState.FAILED, [f"the recorded candidate is no longer authorized: {r}" for r in reasons],
                        {}, report, actor, apply)
        validation, why = self.validation_problem(job, classified.entries)
        if validation is None:
            report.note("validation", "fail", why)
            self._refuse(job, "not-validated", [why], report, apply)
        assert validation is not None
        report.note("validation", "pass", f"validation {validation['id']} passed for candidate "
                                          f"{job.candidate['head_sha'][:12]} under the current gate plan")
        plan = current_plan_sha256(job, self.config, classified.entries)
        return _Checked(candidate=candidate, spec=spec, validation=validation, plan_sha256=plan, repo=repo)

    def _pinned(self, job: IntegrationJob, report: ReviewJobReport, actor: str, *, apply: bool, when: str) -> None:
        """origin's source and base branches and the pinned PR are still the job's (M2's check_pinned; a PR change is
        confirmed by the complete lookup): moved → STALE, unreadable → deferred. Part of every _verify, and repeated
        immediately before every guarded record of an attempt or a decision (_final_pins)."""
        label = f" ({when})" if when else ""
        pinned = self.inspector.check_pinned(job, self.validator._pinned_pr(job))  # pylint: disable=protected-access
        report.checks += [{**check, "check": check["check"] + label} for check in pinned.checks]
        if pinned.stale:
            self._final(job, JobState.STALE, [f"{when}: {r}" if when else r for r in pinned.stale], pinned.observed,
                        report, actor, apply)
        if pinned.deferred:
            self._deferred(job, f"{pinned.deferred}{label}", report, apply)

    def _final_pins(self, job_id: str, report: ReviewJobReport, actor: str, when: str) -> None:
        """Codex review of M4, F1: the pinned identities read from origin and GitHub AGAIN, after everything else
        before a record (the rebuilt candidate, its authorization, the validation, the approval's signature) and
        right before the guarded transaction — a branch or PR that moved during those checks is STALE, an unreadable
        one defers; nothing is recorded either way. Not an atomic GitHub/local transaction: a move after this read is
        found by the next review and by M5's re-verification at merge time."""
        self._pinned(self.jobs.load(job_id), report, actor, apply=True, when=when)

    def _rebuild(self, job: IntegrationJob, report: ReviewJobReport, actor: str, *, apply: bool,
                 scratch: Path | None) -> tuple[Candidate, Path]:
        """The candidate rebuilt from the pinned SHAs (M3's deterministic build): the job's candidate repository with
        --apply, a temporary one for a dry run. Exactly the recorded head and tree, else STALE."""
        objects = self.validator._objects_dir()  # pylint: disable=protected-access
        if scratch is not None:
            repo, home = scratch / "repo.git", scratch / "home"
        else:
            area = CandidateArea(self.store.root, job.job_id)
            repo, home = area.repo, area.home
        candidate, refused, failure = None, "", ""
        try:
            if scratch is not None:
                ensure_repository(repo, objects)
            else:
                area.ensure(objects)
            candidate = build_candidate(CandidateGit(home, timeout=self.git.timeout), repo, job)
        except HistoryRefused as exc:
            refused = f"the candidate cannot be built from verified objects: {exc}"
        except (CoordinatorError, OSError) as exc:
            failure = f"the candidate could not be rebuilt: {exc}"
        if refused:
            self._final(job, JobState.FAILED, [refused], {}, report, actor, apply)
        if candidate is None:
            self._deferred(job, failure, report, apply)
        assert job.candidate is not None
        mismatch = identity_problem(job.candidate, candidate)
        report.candidate = {"head_sha": candidate.head, "tree_sha": candidate.tree, "git": candidate.git,
                            "recorded": {k: job.candidate[k] for k in ("head_sha", "tree_sha")}}
        if mismatch:
            self._final(job, JobState.STALE, [f"candidate identity changed: {mismatch}"],
                        {"recorded": report.candidate["recorded"],
                         "rebuilt": {"head_sha": candidate.head, "tree_sha": candidate.tree}}, report, actor, apply)
        report.note("candidate", "pass", f"rebuilt {candidate.head[:12]} (tree {candidate.tree[:12]}) is the recorded "
                                         "candidate")
        return candidate, repo

    def validation_problem(self, job: IntegrationJob, paths: Any = None) -> tuple[dict[str, Any] | None, str]:
        """(the PASSED validation decision this review relies on, '') when M3's validation is current for the gate
        plan the current configuration derives for the candidate's paths classified now (*paths*, else current_paths;
        integration_validate.validated_for), else (None, why not)."""
        assert job.candidate is not None
        plan = current_plan_sha256(job, self.config, paths)
        latest = latest_evidence(job, "validation")
        if validated(job, plan) and latest is not None:
            return latest, ""
        fix = (f"run `integration validate --job {job.job_id} --apply`" if job.state is JobState.VALIDATING else
               f"validate cannot run in {job.state.value}: restore the configuration, or abort the job and supersede "
               "it")
        attempt = open_attempt(job)
        if latest is None:
            why = f"{job.job_id} has no validation decision"
        elif attempt is not None:
            why = f"validation attempt {attempt['id']} is open (running or interrupted)"
        elif recorded_failures(job):
            why = f"a gate FAILED for this candidate ({', '.join(sorted(recorded_failures(job)))})"
        elif latest["result"] != "passed":
            why = f"its newest validation {latest['id']} is {latest['result']}"
        elif latest["plan"]["sha256"] != plan:
            why = f"the gate plan changed since validation {latest['id']} (the current configuration derives another)"
        else:
            why = f"a validation attempt or gate ran after validation {latest['id']}"
        return None, f"M3 validation is not current: {why}; {fix}"

    # -- the dry run ---------------------------------------------------------------------------------

    def _dry_run(self, job: IntegrationJob, approval: dict[str, Any], report: ReviewJobReport,
                 retry: Mapping[str, str] | None) -> None:
        report.messages.append("dry run — nothing was recorded and no reviewer ran (pass --apply to review)")
        if job.state in DECIDED_STATES:
            self._reverify(job, approval, report, "operator", apply=False)
            return
        scratch_root = self.store.root / "tmp"
        work = Path(tempfile.mkdtemp(prefix="integration-review-", dir=scratch_root if scratch_root.is_dir() else None))
        try:
            opened = open_review_attempt(job)
            if opened is not None:
                state, pids = attempt_processes(running_of(opened))
                report.outcome = "would-recover"
                report.messages.append(f"review attempt {opened['id']} has no result recorded yet (its reviewer "
                                       f"process is {state}{f', pids {pids}' if pids else ''}): --apply waits for it "
                                       "and collects its result before anything else")
                return
            checked = self._verify(job, approval, report, "operator", apply=False, scratch=work)
            if pending_answer(job) is not None:
                refused = independence_problem(self.config, review_attempts(job)[-1]["independence"])
                if refused:
                    self._refuse(job, "independence-refused", [refused], report, False)
                report.outcome = "would-decide"
                report.messages.append("a valid answer awaits its decision: --apply decides it without another "
                                       "reviewer run")
                return
            exhausted = self._exhausted(job)
            if self._round_ended(job) and retry is None:
                self._refuse(job, "error", self._error_reasons(job), report, False)
            if exhausted and retry is None:
                report.outcome, report.reasons = "would-error", [exhausted]
                return
            refused = independence_problem(self.config)
            if refused:
                self._refuse(job, "independence-refused", [refused], report, False)
            context = self._context(job, checked, approval, report, apply=False)
            problem = self.preflight()
            if problem:
                self._refuse(job, "reviewer-unavailable", [f"the reviewer cannot run now: {problem}"], report, False)
            report.outcome = "would-review"
            report.messages.append(f"--apply runs the reviewer ({self.cfg.provider} / {self.cfg.model}, no tools) on "
                                   f"{len(context.files)} changed file(s), at most {self.cfg.max_attempts} attempt(s) "
                                   f"of {self.cfg.timeout_seconds}s")
        finally:
            shutil.rmtree(work, ignore_errors=True)

    # -- decided jobs: a repeated review reuses the decision -----------------------------------------

    def _needs_repair(self, job: IntegrationJob, report: ReviewJobReport) -> None:
        attempts = review_attempts(job)
        decision = review_decision(job, attempts[-1]) if attempts else None
        if decision is None:
            report.outcome = "not-validated"
            report.reasons = [f"{job.job_id} is NEEDS_REPAIR without a review: a gate FAILED (M3); the source is never "
                              "patched — a repair branch and a new job are needed"]
            return
        report.outcome, report.review = "changes-requested", self._decision_view(decision)
        report.reasons = [clean_text(r) for r in decision["reasons"]]
        report.messages.append(f"{job.job_id} is NEEDS_REPAIR: review {decision['id']} requested changes "
                               f"({decision['blocking']} blocking finding(s)); the source is never modified — a repair "
                               "and a new job with fresh validation and review are needed")

    def _reverify(self, job: IntegrationJob, approval: dict[str, Any], report: ReviewJobReport, actor: str, *,
                  apply: bool) -> None:
        if job.state is JobState.NEEDS_REPAIR:
            self._needs_repair(job, report)
            return
        decision = current_review(job)
        scratch = None if apply else Path(tempfile.mkdtemp(prefix="integration-review-",
                                                           dir=self._scratch_dir()))
        try:
            checked = self._verify(job, approval, report, actor, apply=apply, scratch=scratch, when="re-verified")
        finally:
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)
        if decision is None or decision["plan_sha256"] != checked.plan_sha256 \
                or decision["validation"] != checked.validation["id"]:
            report.outcome = "not-current"
            report.reasons = [f"{job.job_id} is AWAITING_MERGE_APPROVAL, but its recorded review does not apply to the "
                              "current candidate, validation or gate plan"]
            return
        refused = independence_problem(self.config, decision["independence"])
        if refused:   # F2: kept as history, not reused; a compatible policy makes it current again (reviewed_for)
            report.review = self._decision_view(decision)
            self._refuse(job, "independence-refused", [refused], report, apply,
                         f"review {decision['id']} ({decision['outcome']}) stays recorded but is not reused under the "
                         f"current policy; M5 refuses to rely on it (reviewed_for) — {job.job_id} is unchanged")
        report.outcome, report.review = "reviewed", self._decision_view(decision)
        report.messages.append(f"{job.job_id}: review {decision['id']} ({decision['outcome']}) still applies to "
                               f"candidate {decision['head_sha']} — reused, nothing ran again")

    def _scratch_dir(self) -> Path | None:
        scratch = self.store.root / "tmp"
        return scratch if scratch.is_dir() else None

    # -- apply ---------------------------------------------------------------------------------------

    def _apply(self, job: IntegrationJob, approval: dict[str, Any], report: ReviewJobReport, actor: str,  # pylint: disable=too-many-branches
               retry: Mapping[str, str] | None) -> None:
        job_id = job.job_id
        new_round = retry is not None
        for _ in range(2 * self.cfg.max_attempts + 4):   # each pass records a run or a decision, or ends the run
            job = self.jobs.load(job_id)
            if job.state not in REVIEWABLE_STATES:
                self._meanwhile(report, job)
            opened = open_review_attempt(job)
            if opened is not None:
                self._recover(job, opened, report)
                continue
            pending = pending_answer(job)
            checked = self._verify(job, approval, report, actor, apply=True,
                                   when="after the review" if pending is not None else "")
            if pending is not None:
                self._decide(job_id, pending, checked, approval, report, actor)
                return
            if self._round_ended(job) and not new_round:
                self._refuse(job, "error", self._error_reasons(job), report, True,
                             "the review round ended in ERROR: `integration review --job J --retry --reason TEXT "
                             "--apply` starts the next round, or abort the job")
            exhausted = None if new_round else self._exhausted(job)
            if exhausted:
                self._error(job_id, exhausted, report)
                return
            refused = independence_problem(self.config)
            if refused:
                self._refuse(job, "independence-refused", [refused], report, True)
            context = self._context(job, checked, approval, report, apply=True)
            problem = self.preflight()
            if problem:
                self._refuse(job, "reviewer-unavailable", [f"the reviewer cannot run now: {problem}"], report, True,
                             "no attempt was used; run again once the reviewer is available")
            attempt = self._begin(job, checked, approval, context, report, actor,
                                  retry if new_round else None)
            new_round = False
            run = self._launch(job_id, attempt, context, report)
            if run is not None and run["status"] in UNCOUNTED_RUN_STATUSES:
                outcome = "reviewer-unavailable" if run["status"] in ("unavailable", "not-started") else "cancelled"
                self._refuse(self.jobs.load(job_id), outcome, [f"review attempt {attempt['id']}: {run['status']} — "
                                                              f"{run['error'] or 'no answer'}"], report, True,
                             "the attempt was given back (no budget used); run again")
        raise IntegrationError(f"{job_id}: the review did not settle within its attempts; nothing more was done")

    def _round_ended(self, job: IntegrationJob) -> bool:
        """True when the newest attempt's decision is ERROR: the round is over until an operator's retry."""
        attempts = review_attempts(job)
        decision = review_decision(job, attempts[-1]) if attempts else None
        return job.state is JobState.IN_REVIEW and decision is not None and decision["outcome"] == "ERROR"

    def _exhausted(self, job: IntegrationJob) -> str:
        """Why the current round must end in ERROR now ('' : an attempt may start): its last run was refused (a rule
        violation, never retried) or its counted runs reached `github.review.max_attempts`."""
        attempts = review_attempts(job)
        if not attempts:
            return ""
        runs = [run for run in (review_run(job, a) for a in attempts if a["round"] == attempts[-1]["round"]) if run]
        if runs and runs[-1]["status"] == "refused":
            return f"the reviewer run was refused (a rule violation, never retried): {runs[-1]['error']}"
        counted = [run for run in runs if run["counted"]]
        if len(counted) >= self.cfg.max_attempts:
            return (f"no valid review in {len(counted)} attempt(s) (github.review.max_attempts "
                    f"{self.cfg.max_attempts}); last: {counted[-1]['status']} — {counted[-1]['error'] or 'no answer'}")
        return ""

    def _error_reasons(self, job: IntegrationJob) -> list[str]:
        attempts = review_attempts(job)
        decision = review_decision(job, attempts[-1]) if attempts else None
        shown = (decision["id"] if decision else "?", attempts[-1]["round"] if attempts else "?")
        why = "; ".join((decision or {}).get("reasons") or ["no valid review"])
        return [f"review {shown[0]} of round {shown[1]} is ERROR: {why}"]

    def _context(self, job: IntegrationJob, checked: _Checked, approval: dict[str, Any], report: ReviewJobReport, *,
                 apply: bool) -> ReviewContext:
        gates = {entry["id"]: entry for entry in job.evidence if entry["kind"] == "gate"}
        reader = ReviewGit(checked.repo.parent / "home", timeout=self.git.timeout).command(checked.repo)
        pinned = self.validator._pinned_pr(job)  # pylint: disable=protected-access
        pr_number = (job.pr or {}).get("number") or (pinned or {}).get("number")
        context, failure = None, ""
        try:
            verify_changed_blobs(CandidateGit(checked.repo.parent / "home", timeout=self.git.timeout), checked.repo,
                                 base_tree=checked.candidate.base_tree, candidate_tree=checked.candidate.tree,
                                 paths=[entry["path"] for entry in job.candidate["paths"]])
        except (HistoryRefused, HistoryUnverifiable) as exc:
            self._refuse(job, "unreviewable", [f"a changed file's content in the object store does not hash to its "
                                               f"ID, so the reviewer could see other content than the validated "
                                               f"candidate: {exc}"], report, apply, "nothing was sent to the reviewer")
        except (CoordinatorError, OSError) as exc:
            self._deferred(job, f"the candidate's changed files could not be verified: {exc}", report, apply)
        try:
            context = build_candidate_context(self.config, reader, job=job, spec=checked.spec, approval=approval,
                                              validation=checked.validation, gates=gates, pr_number=pr_number)
        except (CoordinatorError, OSError) as exc:
            failure = f"the reviewer context could not be read: {exc}"
        if context is None:
            self._deferred(job, failure, report, apply)
        report.context = {"input_sha256": context.input_sha256, "diff_sha256": context.diff_sha256,
                          "spec_sha256": checked.spec.sha256, "files": len(context.files),
                          "redactions": context.redactions, "notes": list(context.notes)}
        if context.problem:
            self._refuse(job, "unreviewable", [f"the candidate cannot be reviewed in full: {context.problem}"], report,
                         apply, "nothing was sent to the reviewer")
        report.note("context", "pass", f"{len(context.files)} changed file(s), input sha256 "
                                       f"{context.input_sha256[:16]}…, {len(context.notes)} coordinator note(s)")
        return context

    # -- step 7: the write-ahead attempt and the reviewer process -------------------------------------

    def _begin(self, job: IntegrationJob, checked: _Checked, approval: dict[str, Any],  # pylint: disable=too-many-arguments
               context: ReviewContext, report: ReviewJobReport, actor: str,
               retry: Mapping[str, str] | None) -> dict[str, Any]:
        """The `review-attempt` entry, recorded BEFORE the reviewer process starts (with VALIDATING → IN_REVIEW for
        the first one), under the decision guard (module docstring, step 7)."""
        attempts = review_attempts(job)
        round_ = (attempts[-1]["round"] if attempts else 0) + (1 if retry is not None or not attempts else 0)
        tag = f"review-{secrets.token_hex(8)}"
        deadline = self.wall() + self.cfg.timeout_seconds + self.config.runtime.terminate_grace_seconds \
            + REVIEW_SLACK_SECONDS
        run_dir = self.store.root / "runs" / "review" / job.job_id / f"attempt-{tag.removeprefix('review-')}"
        entry = {"kind": "review-attempt", "id": "", "at": utc_now(), "candidate": job.candidate["evidence"],
                 "head_sha": job.candidate["head_sha"], "tree_sha": job.candidate["tree_sha"],
                 "source_sha": job.source["sha"], "base_sha": job.base["expected_sha"], "approval": approval["id"],
                 "validation": checked.validation["id"], "plan_sha256": checked.plan_sha256, "round": round_,
                 "reviewer": reviewer_identity(self.config), "independence": integration_independence(self.config),
                 "context": {"input_sha256": context.input_sha256, "diff_sha256": context.diff_sha256,
                             "spec_sha256": checked.spec.sha256, "files": len(context.files),
                             "redactions": context.redactions, "notes": list(context.notes)[:20]},
                 "timeout_seconds": self.cfg.timeout_seconds, "run_dir": str(run_dir), "tag": tag,
                 "deadline": deadline_text(deadline),
                 "retry": {"by": clean_text(retry.get("by"), 80), "reason": clean_text(retry.get("reason"))}
                 if retry is not None else None}
        verified = self._approval_confirmed(job.job_id, approval, report)
        self._final_pins(job.job_id, report, actor, "before the review attempt is recorded")
        try:
            with self._guard(job.job_id, job.state, checked, approval, verified) as current:
                entry["id"] = f"rva-{len(current.evidence) + 1:04d}"
                entry["sha256"] = evidence_digest(entry)
                if current.state is JobState.VALIDATING:
                    current, changed = self.jobs.transition(
                        job.job_id, JobState.IN_REVIEW, f"independent review of candidate {entry['head_sha']} started "
                        f"({entry['id']}, reviewer {self.cfg.provider}/{self.cfg.model})", actor=actor,
                        expect=(JobState.VALIDATING,), evidence=[entry], attention=None)
                    if not changed:
                        raise _Undecided("meanwhile", current)
                else:
                    current.evidence.append(entry)
                    current.attention = None
                    self.jobs.save(current)
                self.store.append_event("integration-review-attempt", job=job.job_id, evidence=entry["id"],
                                        round=round_, tag=tag, head_sha=entry["head_sha"])
        except _Undecided as undecided:
            self._undecided(undecided, report)
        report.changed = True
        self._fault("after-attempt")
        return entry

    def _launch(self, job_id: str, attempt: dict[str, Any], context: ReviewContext,
                report: ReviewJobReport) -> dict[str, Any] | None:
        """Write the request, start the reviewer process, wait for it and record its run (None: the job left
        IN_REVIEW meanwhile — nothing more is recorded)."""
        handle, failure = self.process.launch(job_id, attempt, context)
        if handle is None:
            return self._record_run(job_id, attempt, run_entry(attempt, "not-started", failure), report)
        report.reviewer_runs += 1
        report.messages.append(f"review attempt {attempt['id']}: reviewer process {getattr(handle, 'pid', '?')} "
                               f"started (deadline {attempt['deadline']})")
        self._fault("after-start")
        return self._finish(job_id, attempt, report)

    def _recover(self, job: IntegrationJob, attempt: dict[str, Any], report: ReviewJobReport) -> None:
        """Step 2: an attempt no run names — wait for its live reviewer process, then collect its result."""
        state, pids = attempt_processes(running_of(attempt))
        report.messages.append(f"resuming review attempt {attempt['id']} (started {attempt['at']}): its reviewer "
                               f"process is {state}{f' (pids {pids})' if pids else ''}")
        if self._finish(job.job_id, attempt, report) is None:
            self._meanwhile(report, self.jobs.load(job.job_id))

    def _finish(self, job_id: str, attempt: dict[str, Any], report: ReviewJobReport) -> dict[str, Any] | None:
        why = self.process.wait(job_id, attempt)
        if why == "meanwhile":
            return None
        self._fault("before-collect")
        run = self.process.collect(job_id, attempt, why)
        recorded = self._record_run(job_id, attempt, run, report)
        self._fault("after-run")
        return recorded

    # -- step 8: the result ---------------------------------------------------------------------------

    def _record_run(self, job_id: str, attempt: Mapping[str, Any], entry: dict[str, Any],
                    report: ReviewJobReport) -> dict[str, Any] | None:
        """Append the run (the job must still be IN_REVIEW with this attempt open; else nothing: None)."""
        with self.store.transaction():
            current = self.jobs.load(job_id)
            opened = open_review_attempt(current)
            if current.state is not JobState.IN_REVIEW or opened is None or opened["id"] != attempt["id"]:
                return None
            entry["id"] = f"rvr-{len(current.evidence) + 1:04d}"
            entry["sha256"] = evidence_digest(entry)
            current.evidence.append(entry)
            self.jobs.save(current)
            self.store.append_event("integration-review-run", job=job_id, evidence=entry["id"],
                                    attempt=attempt["id"], status=entry["status"], outcome=entry["outcome"])
        report.changed = True
        report.messages.append(f"review attempt {attempt['id']}: {entry['status']}"
                               + (f" — {entry['outcome']}" if entry["outcome"] else
                                  f" — {entry['error']}" if entry["error"] else "") + f" ({entry['id']})")
        return entry

    # -- step 9: the decision --------------------------------------------------------------------------

    def _approval_confirmed(self, job_id: str, approval: dict[str, Any], report: ReviewJobReport) -> str:
        """Before a guarded record: the effective approval re-verified (signature included: no subprocess runs under
        the coordinator lock) and the checkout's configuration re-scanned. The allowed-signers digest it was verified
        against (else the run ends: no-approval)."""
        job = self.jobs.load(job_id)
        current, problems = effective_approval_problems(job, config=self.config, verifier=self.verifier,
                                                        now=self.clock())
        if current is None or current["id"] != approval["id"] or problems:
            self._refuse(job, "no-approval", problems or ["the effective approval changed"], report, True,
                         "the run's results are recorded; no review decision or attempt is")
        self.git.recheck()
        return self.verifier.last_signers_digest

    @contextlib.contextmanager
    def _guard(self, job_id: str, state: JobState, checked: _Checked, approval: dict[str, Any],
               verified_signers: str) -> Iterator[IntegrationJob]:
        """The coordinator transaction of a guarded record: the job still in *state*, no attempt open, its validation
        still the one checked and current for the checked plan, the same approval inside its window at a FRESH clock
        reading, the allowed-signers file unchanged; else _Undecided (nothing recorded)."""
        with self.store.transaction():
            job = self.jobs.load(job_id)
            latest = latest_evidence(job, "validation")
            effective = effective_approval(job)
            if job.state is not state or open_review_attempt(job) is not None:
                raise _Undecided("meanwhile", job)
            if latest is None or latest["id"] != checked.validation["id"] or not validated(job, checked.plan_sha256):
                raise _Undecided("validation", job)
            if effective is None or effective["id"] != approval["id"] or time_problems(effective, self.clock()):
                raise _Undecided("expired", job)
            if self.validator.signers_changed(verified_signers):
                raise _Undecided("signers", job)
            yield job

    def _undecided(self, undecided: _Undecided, report: ReviewJobReport) -> NoReturn:
        job = undecided.job
        if undecided.reason == "expired":
            self._refuse(job, "no-approval", ["the effective approval expired or changed before the record"], report,
                         True, "nothing more was recorded")
        if undecided.reason == "validation":
            self._refuse(job, "not-validated", ["M3's validation changed before the record (a new validation "
                                                "attempt, gate or decision)"], report, True,
                         "nothing more was recorded")
        if undecided.reason == "signers":
            report.messages.append("the allowed-signers file changed after the approval's signature was verified (a "
                                   "key may have been revoked); nothing more was recorded — run review again")
        self._meanwhile(report, job)

    def _decide(self, job_id: str, run: dict[str, Any], checked: _Checked, approval: dict[str, Any],  # pylint: disable=too-many-arguments
                report: ReviewJobReport, actor: str) -> None:
        """The decision for a valid answer (PASS, PASS_WITH_NOTES → AWAITING_MERGE_APPROVAL; REQUEST_CHANGES →
        NEEDS_REPAIR), guarded (module docstring, step 9)."""
        self._fault("before-decision")
        job = self.jobs.load(job_id)
        attempt = review_attempts(job)[-1]
        verified = self._approval_confirmed(job_id, approval, report)
        self._final_pins(job_id, report, actor, "before the review decision is recorded")
        refused = policy_problem(self.config, self.reload, attempt["independence"])
        if refused:   # F2 / re-review 1: the policy in effect NOW decides, whatever the attempt was started under
            self._refuse(job, "independence-refused", [refused], report, True,
                         f"the answer of {attempt['id']} stays recorded as history ({run['id']}); no review decision "
                         "was recorded — it is decided once the policy accepts it")
        try:
            with self._guard(job_id, JobState.IN_REVIEW, checked, approval, verified) as current:
                if review_run(current, attempt) != run or review_decision(current, attempt) is not None \
                        or review_attempts(current)[-1]["id"] != attempt["id"] \
                        or attempt["validation"] != checked.validation["id"] \
                        or attempt["plan_sha256"] != checked.plan_sha256:
                    raise _Undecided("validation", current)
                entry = self._decision_entry(current, attempt, run)
                if entry["outcome"] == "REQUEST_CHANGES":
                    blocking = [f for f in entry["findings"] if f["severity"] in BLOCKING]
                    reasons = [clean_text(f"{f['severity']}: {f['file']}"
                                          + (f":{f['line_start']}" if f["line_start"] else "") + f" — {f['title']}")
                               for f in blocking][:MAX_LISTED]
                    attention = {"reason": f"the independent review requested changes ({len(blocking)} blocking "
                                           f"finding(s)): {reasons[0]}", "reasons": reasons,
                                 "observed": {"review": entry["id"], "head_sha": entry["head_sha"],
                                              "tree_sha": entry["tree_sha"]}, "since": utc_now()}
                    target = JobState.NEEDS_REPAIR
                else:
                    attention, target = None, JobState.AWAITING_MERGE_APPROVAL
                current, changed = self.jobs.transition(
                    job_id, target, f"independent review {entry['id']}: {entry['outcome']} for candidate "
                    f"{entry['head_sha']}", actor=actor, expect=(JobState.IN_REVIEW,), evidence=[entry],
                    attention=attention)
                if not changed:
                    raise _Undecided("meanwhile", current)
                self.store.append_event("integration-review", job=job_id, evidence=entry["id"],
                                        outcome=entry["outcome"], head_sha=entry["head_sha"])
        except _Undecided as undecided:
            self._undecided(undecided, report)
        report.changed = True
        report.review = self._decision_view(entry)
        report.job = job_summary(current)
        if entry["outcome"] == "REQUEST_CHANGES":
            report.outcome, report.reasons = "changes-requested", entry["reasons"]
            report.messages.append(f"{job_id} needs repair (NEEDS_REPAIR): the review requested changes; the source is "
                                   "never modified — a repair and a new job with fresh validation and review are "
                                   "needed")
        else:
            report.outcome = "reviewed"
            report.messages.append(f"{job_id} is reviewed ({entry['outcome']}, {len(entry['findings'])} finding(s) "
                                   "retained): AWAITING_MERGE_APPROVAL — the merge approval and the merge are M5")

    def _error(self, job_id: str, reason: str, report: ReviewJobReport) -> None:
        """Step 5: the ERROR decision of the current round — no successful review; the job stays IN_REVIEW with
        `attention` until an operator retries (`--retry`) or aborts."""
        with self.store.transaction():
            current = self.jobs.load(job_id)
            attempts = review_attempts(current)
            if current.state is not JobState.IN_REVIEW or not attempts \
                    or review_decision(current, attempts[-1]) is not None:
                self._meanwhile(report, current)
            attempt = attempts[-1]
            run = review_run(current, attempt)
            if run is None:
                self._meanwhile(report, current)
            assert run is not None
            entry = self._decision_entry(current, attempt, run, error=reason)
            current.evidence.append(entry)
            current.attention = {"reason": f"the independent review is ERROR: {clean_text(reason)}",
                                 "reasons": [clean_text(reason)],
                                 "observed": {"review": entry["id"], "round": attempt["round"],
                                              "head_sha": entry["head_sha"]}, "since": utc_now()}
            self.jobs.save(current)
            self.store.append_event("integration-review", job=job_id, evidence=entry["id"], outcome="ERROR",
                                    head_sha=entry["head_sha"])
        report.changed = True
        report.outcome, report.reasons = "error", [clean_text(reason)]
        report.review = self._decision_view(entry)
        report.messages.append(f"{job_id}: review round {attempt['round']} ended in ERROR (no successful review, no "
                               "merge-ready evidence); `integration review --job J --retry --reason TEXT --apply` "
                               "starts the next round, or abort the job")

    @staticmethod
    def _decision_entry(job: IntegrationJob, attempt: Mapping[str, Any], run: Mapping[str, Any], *,
                        error: str = "") -> dict[str, Any]:
        """The `review` decision entry for *attempt* and its *run* (ERROR with *error*), repeating their bindings."""
        findings = list(run["findings"])
        entry = {"kind": "review", "id": f"rev-{len(job.evidence) + 1:04d}", "at": utc_now(),
                 "candidate": attempt["candidate"], "head_sha": attempt["head_sha"], "tree_sha": attempt["tree_sha"],
                 "source_sha": attempt["source_sha"], "base_sha": attempt["base_sha"], "approval": attempt["approval"],
                 "validation": attempt["validation"], "plan_sha256": attempt["plan_sha256"], "round": attempt["round"],
                 "attempt": attempt["id"], "run": run["id"], "outcome": "ERROR" if error else run["outcome"],
                 "verdict": run["verdict"], "summary": run["summary"], "findings": findings,
                 "limitations": list(run["limitations"]), "coordinator_notes": list(attempt["context"]["notes"]),
                 "blocking": sum(1 for f in findings if f["severity"] in BLOCKING),
                 "reviewer": attempt["reviewer"], "independence": attempt["independence"],
                 "input_sha256": attempt["context"]["input_sha256"], "diff_sha256": attempt["context"]["diff_sha256"],
                 "spec_sha256": attempt["context"]["spec_sha256"], "output_sha256": run["output_sha256"],
                 "result_sha256": run["result_sha256"],
                 "reasons": [clean_text(error)] if error else [clean_text(f"{f['severity']}: {f['file']} — "
                                                                          f"{f['title']}") for f in findings][:20]}
        entry["sha256"] = evidence_digest(entry)
        return entry

    @staticmethod
    def _decision_view(entry: Mapping[str, Any]) -> dict[str, Any]:
        return {"id": entry["id"], "outcome": entry["outcome"], "verdict": entry["verdict"],
                "summary": entry["summary"], "findings": list(entry["findings"]),
                "limitations": list(entry["limitations"]), "coordinator_notes": list(entry["coordinator_notes"]),
                "blocking": entry["blocking"], "attempt": entry["attempt"], "run": entry["run"],
                "head_sha": entry["head_sha"], "tree_sha": entry["tree_sha"], "validation": entry["validation"],
                "independence": entry["independence"]["level"], "reviewer": entry["reviewer"]}


def reviewed_for(job: IntegrationJob, config: ProjectConfig) -> dict[str, Any] | None:
    """The review M5 may rely on (review_eligibility). M5 must still re-verify the pinned SHAs and rebuild the
    candidate (as `integration review` does) at merge time."""
    return review_eligibility(job, config)[0]


def review_eligibility(job: IntegrationJob, config: ProjectConfig, paths: Any = None,
                       ) -> tuple[dict[str, Any] | None, str]:
    """(decision, '') for integration.current_review (a PASS / PASS_WITH_NOTES that still applies to the job's
    candidate and its newest, current validation) whose recorded independence the CURRENT policy accepts (F2) and
    whose validation and decision are for the gate plan the CURRENT configuration derives; else (None, why that
    configuration objects — '' when no review applies). `integration status` shows the same (Codex re-review, 2)."""
    decision = current_review(job)
    if decision is None or job.candidate is None:
        return None, ""
    refused = independence_problem(config, decision["independence"])
    if refused:
        return None, f"the current reviewer independence requirement is not satisfied: {refused}"
    plan = current_plan_sha256(job, config, paths)   # *paths*: the candidate's paths classified now (Codex F7b)
    if not validated_for(job, config, paths) or decision["plan_sha256"] != plan:
        return None, "the current configuration derives another gate plan than the review was made for"
    return decision, ""
