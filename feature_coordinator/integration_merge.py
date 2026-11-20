"""W4.7 (M5): `integration merge --job J` — the operator-approved GitHub merge of an integration job and its verified
completion (design §4 AWAITING_MERGE_APPROVAL → MERGED → COMPLETED, §4.1 PR reuse and creation, §5.7 recovery, §5.8
final verification, §5.9 merge mechanism, §5.10 post-merge verification, §9).

    python -m feature_coordinator integration merge --job J [--actor NAME] [--apply] [--json]

Dry run by default: every check and every GitHub read runs, nothing is written (no PR, no merge, no state) and the
report says what --apply would do. A merge is permitted ONLY with an effective, valid, signed MERGE approval
(integration_merge_record.py) bound to this exact candidate and its validation and review evidence; a successful AI
review authorizes nothing, and neither do GitHub comments, labels, PR approvals or CLI actor names.

Order of one run (AWAITING_MERGE_APPROVAL):
  0. RECONCILE FIRST. If this job has a merge attempt, GitHub is asked whether the PR is already merged BEFORE anything
     else — before the approval is looked at and before any staleness check: `dev` moved because of the job's own merge
     is not STALE. What GitHub shows is judged by what this job did (integration_merge_recovery.py): an attempt that
     was never DISPATCHED (no `merge-dispatch`: the write boundary was never reached) is closed as a recorded fact; a
     DISPATCHED request with an unknown outcome ends the run (`merge-uncertain`, later `merge-unresolved` with
     attention) and is never repeated; a merged PR is recorded (merge-outcome `merged`, `merge_result`,
     AWAITING_MERGE_APPROVAL → MERGED) and verified (step 7) only when the facts attribute the merge to this job (a
     dispatched request, merged by the authenticated account); a merge by anyone else is STALE (the base moved),
     never COMPLETED; one that cannot be attributed stops with attention.
  1. The effective merge approval is valid NOW (signature against the allowed-signers file, operator, time window) and
     binds the current candidate, validation and review evidence; no fallback to an older approval.
  2. The checks every review makes, shared with it (IntegrationReviewer._verify — not reimplemented): the Issue is an
     open Issue, origin's source and base branches and the pinned PR are unchanged (moved → STALE), the candidate
     REBUILT from the pinned SHAs is exactly the recorded head and tree, its paths are still authorized, M3's
     validation is current for the gate plan the current configuration derives; plus M4's review is eligible
     (integration_review.review_eligibility: PASS or PASS_WITH_NOTES, independence policy, gate plan).
  3. The PR (design §4.1): the complete lookup over every state; exactly one compatible OPEN PR is reused; none → ONE is
     created, only when the signed approval says `authorizes.create_pr` (a moved, closed, merged, wrong-base or
     duplicate PR is never worked around, and no creation follows a dispatched one whose outcome is unknown — this
     job's, or ANY other job's for the same source branch, aborted and superseded ones included, nor a PR another job
     recorded for the branch: integration_merge_schema.creation_history_problems); a Draft PR is reported (never
     marked ready by the coordinator). The PR is bound as `merge-pr` evidence (the job's inputs are never edited).
  4. GitHub's merge requirements for the PR (`mergeable_state`, no draft) and the configured `required_checks` of the
     approved head (observations are untrusted data: a repeated check id makes the listing unreliable).
  5. The DECISION, one coordinator transaction: the job still in AWAITING_MERGE_APPROVAL (an operator abort wins before
     it), the same merge approval still effective inside its window at a fresh clock reading, the allowed-signers file
     unchanged, validation and review still current — then the write-ahead `merge-attempt` (the auditable INTENT, not
     proof that a request was sent). The pinned branches and PR were read again right before. No lock is held while
     GitHub is called.
  6. THE WRITE BOUNDARY (integration_authority.py), inside the client, immediately before the request: the client
     builds the concrete authority from the trust anchors `integration merge` bound to it (nothing this workflow
     passes can authorize a write) and the whole authorization is derived again from the PERSISTED job under ONE
     snapshot of the project configuration read again from its file (both signatures, the operators, the
     allowed-signers file, the evidence, the intent naming exactly this operation, the candidate's paths, the
     required checks observed again, other jobs' PR creations) and — while the file is still that snapshot — the
     `merge-dispatch` evidence is recorded: the one-shot of the write. Then `PUT /pulls/{n}/merge` with
     merge_method=merge and sha=<approved source SHA> (GitHub refuses it if the head moved). The answer — or the lack
     of one — is never trusted alone: the PR is READ. Merged → attributed and recorded (step 0's record). Rejected
     (an HTTP error answer, or a transport that never sent) and still open → `merge-outcome not-merged`; an uncertain
     answer (timeout, 5xx, a response without the merge commit) leaves the attempt OPEN and unresolved — no timer
     ever turns it into a failure, so a merge request is never repeated blindly; `abort` is refused while a young
     dispatched one may still be in flight.
  7. Post-merge verification (integration_postmerge.py): COMPLETED only when GitHub shows the PR merged into the base,
     the actual merge commit is on the base branch, its parents are base_sha and source_sha and its tree is the
     approved candidate tree, and the configured post-merge checks passed — and the record then carries every core
     check once and passing (integration_merge_schema.completion_problems). Pending (a check running, GitHub or origin
     unreadable): the job stays MERGED. Failed: FAILED, operator attention — nothing is ever reverted, reset or
     force-pushed. A job in MERGED only repeats step 7.

Crashes (fault points `before-merge-request`, `after-merge-request`, `after-merged-record`, `before-completed`,
`after-pr-intent`, `after-pr-created`): every step is repeatable; the intent is on disk before any write and the
dispatch before any request leaves, so the next run knows whether a request may have been sent, and starts with step 0
(or the PR lookup, which finds a PR created before the crash and never creates a second one).
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, NoReturn

from .config import ProjectConfig
from .errors import (
    CoordinatorError,
    GitHubError,
    GitHubUncertainWriteError,
    IntegrationWriteRefused,
    LockBusyError,
)
from .github_api import TransportFailure
from .integration import (
    IntegrationError,
    IntegrationJob,
    JobState,
    clean_text,
    effective_approval,
    evidence_digest,
    job_summary,
    latest_evidence,
    validated,
)
from .integration_approval import SshSignatureVerifier, effective_approval_problems, now_utc, time_problems
from .integration_authority import inspect_still_verifies
from .integration_github import IncompleteListing, decide_reuse, discover_pull_requests, fetch_pull, \
    recorded_pr_changes, reuse_problems
from .integration_intake import MAX_LISTED, ReadOnlyGit
from .integration_merge_github import check_observations, evaluate_checks, pull_merge_state, pull_request_text
from .integration_merge_record import known_pr_number
from .integration_merge_recovery import EXTERNAL, OWN, UNCERTAIN_SETTLE_SECONDS, MergeRecovery, pull_view
from .integration_merge_schema import attempt_dispatch, creation_history_problems, merge_attempts, merge_pr_entry, \
    open_merge_attempt
from .integration_postmerge import Verification, verify_merge
from . import integration_review as _review
from .integration_review import IntegrationReviewer, review_eligibility
from .locks import FileLock
from .model import utc_now
from .issues import PullRequest

MERGEABLE_STATES = frozenset({JobState.AWAITING_MERGE_APPROVAL, JobState.MERGED})
OK_OUTCOMES = ("completed", "would-merge", "would-create-pr", "would-reconcile", "would-verify")
MARKER = "w47-integration-job"


@dataclass
class MergeReport:  # pylint: disable=too-many-instance-attributes
    """What one merge run did (apply) or would do (dry run). Shaped like the review report: the shared checks of
    IntegrationReviewer fill `checks`, `candidate`, `outcome`, `reasons`, `messages`, `job` and `changed`."""

    applied: bool
    outcome: str = ""
    changed: bool = False
    approval: str | None = None
    job: dict[str, Any] | None = None
    checks: list[dict[str, str]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    candidate: dict[str, Any] | None = None
    pr: dict[str, Any] | None = None
    github_writes: list[str] = field(default_factory=list)
    merge_result: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    messages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True for a verified completion (or a dry run that found nothing in the way)."""
        return self.outcome in OK_OUTCOMES

    def note(self, name: str, status: str, detail: str = "") -> None:
        """Record one check: pass | fail | stale | deferred | blocked | attention | pending | not-configured."""
        self.checks.append({"check": name, "status": status, "detail": clean_text(detail, 300)})

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "ok": self.ok}


class _Undecided(Exception):
    def __init__(self, reason: str, job: IntegrationJob) -> None:
        super().__init__(reason)
        self.reason, self.job = reason, job


class IntegrationMerger(MergeRecovery, IntegrationReviewer):  # pylint: disable=too-many-public-methods
    """The operator-approved merge of integration jobs for one checkout and state directory (module docstring). It
    shares the review's checks (`_verify`, `_pinned`, `_rebuild`, `validation_problem`, `_final`) by inheritance, so
    the merge re-verifies exactly what the review verified with no second implementation; everything else here is the
    merge's."""

    def __init__(self, store: Any, config: ProjectConfig, git: ReadOnlyGit, *,  # pylint: disable=too-many-arguments
                 verifier: SshSignatureVerifier, tracker: Callable[[], Any] | None = None, faults: Any = (),
                 clock: Callable[[], datetime] = now_utc, sleep: Callable[[float], None] = time.sleep,
                 settle_seconds: float = UNCERTAIN_SETTLE_SECONDS) -> None:
        """The GitHub writes are authorized at the client's write boundary, by the authority the client builds from
        the trust anchors bound to it (integration_authority.py): nothing given here can authorize one."""
        super().__init__(store, config, git, verifier=verifier, tracker=tracker, faults=faults, clock=clock)
        self.sleep, self.settle_seconds = sleep, settle_seconds

    # -- the run -----------------------------------------------------------------------------------------------

    def run(self, job_id: str, *, apply: bool, actor: str = "operator") -> MergeReport:  # type: ignore[override]  # pylint: disable=arguments-differ
        """Merge *job_id* (module docstring); with *apply*, write. IntegrationError (nothing recorded) for a
        precondition that is not the job's fault."""
        with self.git.session():
            report = MergeReport(applied=apply)
            try:
                self._merge(job_id, report, apply=apply, actor=actor)
            except _review._Stop:  # pylint: disable=protected-access
                pass
            if apply:
                report.job = job_summary(self._tidy(job_id))   # the persisted job, whatever path the run took
            return report

    def _merge(self, job_id: str, report: MergeReport, *, apply: bool, actor: str) -> None:
        job = self.jobs.load(job_id)
        report.job = job_summary(job)
        if job.state is JobState.COMPLETED:
            report.outcome, report.merge_result = "completed", job.merge_result
            report.messages.append(f"{job_id} is COMPLETED: its merge was verified; nothing to do")
            return
        if job.terminal:
            report.outcome = "terminal"
            report.messages.append(f"{job_id} is {job.state.value} (terminal) and is never merged")
            return
        if job.state not in MERGEABLE_STATES:
            raise IntegrationError(f"{job_id} is {job.state.value}: merge applies to a reviewed job awaiting its merge "
                                   "approval (AWAITING_MERGE_APPROVAL) or to a merged job whose verification is "
                                   "pending (MERGED)")
        if job.candidate is None:
            raise IntegrationError(f"conflicting state: {job_id} is {job.state.value} but carries no candidate; "
                                   "abort it")
        if apply and not self.config.github.integration.enabled:
            raise IntegrationError("github.integration.enabled is false: merges are performed only once an operator "
                                   "enables integration jobs")
        client = self.inspector.tracker()
        if client is None or not getattr(client, "live", False):
            raise IntegrationError("integration merge needs the live GitHub client (the local tracker cannot merge or "
                                   "verify a merge); nothing was done")
        if apply and not getattr(client, "integration_bound", False):
            raise IntegrationError("the GitHub client has no integration anchors, so it can send no integration write: "
                                   "run `integration merge` with --project-config FILE (its write boundary reads the "
                                   "configuration again from that file); nothing was done")
        self.validator._preflight()  # pylint: disable=protected-access  # origin and checkout fit, as for review
        if not apply:
            report.messages.append("dry run — nothing was written (no PR, no merge, no state); pass --apply to merge")
            self._process(job, report, client, actor, apply=False)
            return
        lock = FileLock(self.store.locks.dir / f"integration-{job_id}.lock")
        try:
            lock.acquire(timeout=0)
        except LockBusyError as exc:
            raise IntegrationError(f"another integration operation on {job_id} (inspect, validate, review or merge) "
                                   "is running; nothing was done") from exc
        try:
            current = self.jobs.load(job_id)
            if current.state is not job.state:
                self._meanwhile(report, current)
            self._process(current, report, client, actor, apply=True)
        finally:
            lock.release()

    def _process(self, job: IntegrationJob, report: MergeReport, client: Any, actor: str, *, apply: bool) -> None:
        if job.state is JobState.MERGED:
            self._verify_merged(job, report, client, actor, apply)
            return
        if self._reconcile_first(job, report, client, actor, apply):
            self._verify_merged(self.jobs.load(job.job_id), report, client, actor, apply)
            return
        approval = self._merge_approval(job, report, apply)
        inspect = effective_approval(job, "inspect")
        built = self._built_entry(job)
        if inspect is None or built["approval"] != inspect["id"]:
            self._refuse(job, "no-approval", ["the inspect approval the candidate was built under is missing"], report,
                         apply)
        assert inspect is not None
        self._inspect_still_verifies(job, inspect, report, apply)
        scratch = None if apply else Path(tempfile.mkdtemp(prefix="integration-merge-", dir=self._scratch_dir()))
        try:
            checked = self._verify(job, inspect, report, actor, apply=apply, scratch=scratch, when="")
        finally:
            if scratch is not None:
                shutil.rmtree(scratch, ignore_errors=True)
        decision, why = review_eligibility(job, self.config)
        if decision is None:
            self._refuse(job, "not-reviewed", [f"the review is not eligible for the merge: "
                                               f"{why or 'no current PASS or PASS_WITH_NOTES review applies'}"],
                         report, apply)
        assert decision is not None
        report.note("review", "pass", f"review {decision['id']} ({decision['outcome']}) is current and eligible")
        pr = self._pull_request(job, approval, report, actor, client, apply, checked)
        self._readiness(job, pr, client, report, apply)
        if not apply:
            report.outcome = "would-merge"
            report.messages.append(f"--apply would merge PR #{pr.number} with GitHub's merge-commit method pinned to "
                                   f"the approved source SHA {job.source['sha']}, then verify the merge")
            return
        self._merge_and_record(job.job_id, approval, checked, pr, report, actor, client)
        self._verify_merged(self.jobs.load(job.job_id), report, client, actor, True)

    # -- reports (the merge's own event names) ------------------------------------------------------------------

    def _refuse(self, job: IntegrationJob, outcome: str, reasons: list[str], report: Any, apply: bool,  # pylint: disable=arguments-renamed
                message: str = "nothing changed") -> NoReturn:
        report.outcome, report.reasons = outcome, [clean_text(r) for r in reasons]
        report.messages.append(message)
        if apply:
            with self.store.transaction():
                self.store.append_event("integration-merge-refused", job=job.job_id, outcome=outcome,
                                        reason=clean_text(reasons[0]))
        raise _review._Stop(outcome)  # pylint: disable=protected-access

    @staticmethod
    def _meanwhile(report: Any, job: IntegrationJob) -> NoReturn:
        report.outcome, report.job = "changed-meanwhile", job_summary(job)
        report.messages.append(f"{job.job_id} changed while the merge ran (now {job.state.value}); nothing more was "
                               "changed by this run")
        raise _review._Stop("meanwhile")  # pylint: disable=protected-access

    def _deferred(self, job: IntegrationJob, reason: str, report: Any, apply: bool,  # pylint: disable=arguments-renamed
                  outcome: str = "deferred") -> NoReturn:
        report.outcome, report.reasons = (outcome if apply else {"deferred": "would-defer"}.get(outcome, outcome)), \
            [clean_text(reason)]
        report.messages.append(f"{job.job_id} is unchanged ({job.state.value}): {clean_text(reason)} — repeat the same "
                               "command later; nothing was merged")
        if apply:
            with self.store.transaction():
                self.store.append_event(f"integration-merge-{outcome}", job=job.job_id, reason=clean_text(reason))
        raise _review._Stop(outcome)  # pylint: disable=protected-access

    def _blocked(self, job: IntegrationJob, outcome: str, reasons: list[str], report: MergeReport,
                 apply: bool) -> NoReturn:
        """The merge cannot go on and the job has no edge for it (AWAITING_MERGE_APPROVAL has no REFUSED or FAILED):
        it stays, with `attention` for the operator."""
        reasons = [clean_text(r) for r in reasons][:MAX_LISTED] or [outcome]
        report.outcome, report.reasons = outcome, reasons
        if apply:
            with self.store.transaction():
                current = self.jobs.load(job.job_id)
                if current.state is JobState.AWAITING_MERGE_APPROVAL:
                    current.attention = {"reason": reasons[0], "reasons": reasons, "observed": {"outcome": outcome},
                                         "since": utc_now()}
                    self.jobs.save(current)
                self.store.append_event(f"integration-merge-{outcome}", job=job.job_id, reason=reasons[0])
            report.messages.append(f"{job.job_id} stays AWAITING_MERGE_APPROVAL with attention: nothing was merged")
        raise _review._Stop(outcome)  # pylint: disable=protected-access

    def _scratch_dir(self) -> Path | None:
        scratch = self.store.root / "tmp"
        return scratch if scratch.is_dir() else None

    # -- step 1: the approval --------------------------------------------------------------------------------------

    def _merge_approval(self, job: IntegrationJob, report: MergeReport, apply: bool) -> dict[str, Any]:
        approval, problems = effective_approval_problems(job, config=self.config, verifier=self.verifier,
                                                         now=self.clock(), stage="merge")
        if problems:
            self._refuse(job, "no-approval", problems, report, apply)
        assert approval is not None
        report.approval = approval["id"]
        assert job.candidate is not None
        report.note("merge-approval", "pass", f"{approval['id']} by {approval['operator']} (key "
                                              f"{approval['signer']['key']}) is valid until {approval['expires_at']} "
                                              f"and binds candidate {job.candidate['head_sha'][:12]}")
        return approval

    def _inspect_still_verifies(self, job: IntegrationJob, inspect: dict[str, Any], report: MergeReport,
                                apply: bool) -> None:
        """integration_authority.inspect_still_verifies: the inspect approval's signature, key and operator (not its
        expiry) must still verify under the current policy."""
        problems = inspect_still_verifies(job, inspect, self.config, self.verifier)
        if problems:
            self._refuse(job, "no-approval", problems, report, apply)

    # -- step 3: the PR -----------------------------------------------------------------------------------------

    def _pull_request(self, job: IntegrationJob, approval: dict[str, Any], report: MergeReport, actor: str,  # pylint: disable=too-many-arguments,too-many-locals
                      client: Any, apply: bool, checked: Any) -> PullRequest:
        repo, bound = self.config.github.repository, merge_pr_entry(job)
        known = bound["number"] if bound else known_pr_number(job)
        facts = {"repository": repo, "source_repo": job.source["repo"], "source_ref": job.source["ref"],
                 "source_sha": job.source["sha"]}
        try:
            read = fetch_pull(client, repo, known) if known is not None else None
            discovery = discover_pull_requests(client, **facts, max_pages=self.config.github.max_pages,
                                               observed={"the PR read": [read]} if read is not None else None)
        except (GitHubError, IncompleteListing, CoordinatorError, OSError) as exc:
            report.note("pr-lookup", "deferred", str(exc))
            self._deferred(job, f"the complete PR lookup could not be completed: {exc}", report, apply)
        report.note("pr-lookup", "pass", f"{discovery.listed} PR(s) listed in every state ({discovery.method}); "
                                         f"{len(discovery.candidates)} candidate(s) for the head")
        if read is not None:
            moved = recorded_pr_changes({"head_sha": job.source["sha"], "head_repo": job.source["repo"],
                                         "head_ref": job.source["ref"], "base_repo": repo, "base_ref": job.base["ref"]},
                                        read)
            if moved and not read.merged:   # every answer about the PR agreed (the lookup cross-checked them)
                self._final(job, JobState.STALE, moved, {"pr": known}, report, actor, apply)
        decision = decide_reuse(discovery.candidates, **facts, base_ref=job.base["ref"], recorded_number=known)
        opened = open_merge_attempt(job)
        if decision.result == "refused":
            self._blocked(job, "pr-blocked", decision.reasons, report, apply)
        if decision.result == "none":
            return self._create_pull_request(job, approval, report, actor, client, apply, checked, opened)
        pr = next(p for p in discovery.candidates if p.number == decision.number)
        return self._bind(job, approval, pr, report, apply, checked, opened,
                          created=self._opened_by_us(job, pr, opened))

    @staticmethod
    def _opened_by_us(job: IntegrationJob, pr: PullRequest, opened: Mapping[str, Any] | None) -> bool:
        """True when *pr* is the PR of the job's open, DISPATCHED `create-pr` attempt: it carries the marker the
        coordinator wrote (job and approval) — a crash after the creation, before it was recorded, finds it again here.
        A PR with the marker but no dispatched request behind it was not made by this job."""
        return opened is not None and opened["action"] == "create-pr" and attempt_dispatch(job, opened) is not None \
            and f"<!-- {MARKER}: {job.job_id} approval: {opened['approval']} -->" in (pr.body or "")

    def _bind(self, job: IntegrationJob, approval: dict[str, Any], pr: PullRequest, report: MergeReport,  # pylint: disable=too-many-arguments
              apply: bool, checked: Any, opened: dict[str, Any] | None, *, created: bool = False) -> PullRequest:
        """Bind *pr* as the PR of the merge (merge-pr evidence), after the rules: open, not a draft, not another PR."""
        if pr.draft:
            self._blocked(job, "pr-draft", [f"PR #{pr.number} is a Draft: GitHub will not merge it. The coordinator "
                                            "never marks someone else's Draft ready for review — an operator must "
                                            f"do it on GitHub ({pr.url or 'the PR page'}), then repeat "
                                            "`integration merge`"], report, apply)
        bound = merge_pr_entry(job)
        if bound is not None and bound["number"] != pr.number:
            self._blocked(job, "pr-blocked", [f"the PR of this merge is #{bound['number']} (recorded); GitHub now "
                                              f"offers #{pr.number}: the PR identity never changes"], report, apply)
        origin = "created" if created else "recorded" if job.pr and job.pr.get("number") == pr.number else \
            "inspected" if known_pr_number(job) == pr.number else "discovered"
        report.pr = pull_view(pr, "bound" if bound is not None else origin)
        report.note("pr", "pass", f"PR #{pr.number} ({origin}) is open, not a draft, from {job.source['ref']} at the "
                                  f"approved SHA into {job.base['ref']}")
        if bound is not None or not apply:
            return pr
        verified = self._merge_confirmed(job.job_id, approval, report)
        try:
            with self._merge_guard(job.job_id, approval, checked, verified) as current:
                attempt_id = opened["id"] if created and opened is not None else None
                if opened is not None and opened["action"] == "create-pr" and not created:
                    dispatched = attempt_dispatch(current, opened) is not None
                    current.evidence.append(self._outcome_entry(
                        current, opened, "not-created", None, "",
                        f"PR #{pr.number} exists but was not created by this attempt",
                        "superseded" if dispatched else "not-dispatched"))
                entry = {"kind": "merge-pr", "id": f"mpr-{len(current.evidence) + 1:04d}", "at": utc_now(),
                         "candidate": current.candidate["evidence"] if current.candidate else "",
                         "head_sha": current.candidate["head_sha"] if current.candidate else "",
                         "tree_sha": current.candidate["tree_sha"] if current.candidate else "",
                         "approval": approval["id"], "approval_sha256": approval["record_sha256"], "number": pr.number,
                         "origin": origin, "attempt": attempt_id,
                         "head": {"repo": pr.head_repo, "ref": pr.head, "sha": pr.head_sha},
                         "base": {"repo": pr.base_repo, "ref": pr.base}, "draft": False, "url": pr.url or "",
                         "pr_id": pr.id}
                entry["sha256"] = evidence_digest(entry)
                current.evidence.append(entry)
                current.attention = None
                self.jobs.save(current)
                self.store.append_event("integration-merge-pr", job=job.job_id, evidence=entry["id"], pr=pr.number,
                                        origin=origin)
        except _Undecided as undecided:
            self._undecided(undecided, report)
        report.changed = True
        return pr

    def _create_pull_request(self, job: IntegrationJob, approval: dict[str, Any], report: MergeReport, actor: str,  # pylint: disable=too-many-arguments,too-many-locals
                             client: Any, apply: bool, checked: Any, opened: dict[str, Any] | None) -> PullRequest:
        """No PR exists for the head in ANY state (the complete lookup): create ONE — if the signed approval says so,
        and no earlier creation may still be pending (a dispatched one with an unknown outcome — this job's or another
        job's for the branch — is never followed by a second: a lagging listing, or a PR created and closed meanwhile,
        would make a duplicate)."""
        if not approval["merge"]["authorizes"]["create_pr"]:
            self._blocked(job, "pr-missing", ["no PR exists for the head in any state and the signed merge approval "
                                              "does not authorize creating one (authorizes.create_pr is false): open "
                                              "the PR yourself, or sign a new approval"], report, apply)
        if opened is not None and opened["action"] == "create-pr":
            if attempt_dispatch(job, opened) is not None:
                report.note("pr-lookup", "pending", f"GitHub shows no PR, but the creation {opened['id']} was "
                                                    "dispatched and its outcome is unknown")
                self._unresolved(job, opened, "PR creation", report, apply)
            self._close_undispatched(job, opened, report, apply)
        history = creation_history_problems(self.jobs.list(), job)
        if history:   # Codex F3: another job's creation for the branch may have made a PR GitHub does not show
            report.note("pr-lookup", "blocked", history[0])
            self._blocked(job, "pr-unresolved", history, report, apply)
        if not apply:
            report.outcome = "would-create-pr"
            report.messages.append("no PR exists for the head in any state: --apply would create ONE (the signed "
                                   "approval authorizes it), then check GitHub's merge requirements and merge; the "
                                   "remaining checks cannot run before it exists")
            raise _review._Stop("would-create-pr")  # pylint: disable=protected-access
        verified = self._merge_confirmed(job.job_id, approval, report)
        self._final_pins(job.job_id, report, actor, "before the PR is created")
        request = {"head": job.source["ref"], "base": job.base["ref"], "draft": False}
        attempt = self._record_attempt(job.job_id, approval, checked, verified, "create-pr", None, request, actor,
                                       report)
        self._fault("after-pr-intent")
        title, body = pull_request_text(self.jobs.load(job.job_id), approval)
        failure, basis = "", None
        created: PullRequest | None = None
        try:
            report.github_writes.append("POST pulls")
            created = client.create_integration_pull_request(job.source["ref"], job.base["ref"], title, body,
                                                             job_id=job.job_id, attempt=attempt["id"])
        except IntegrationWriteRefused as exc:
            report.github_writes.pop()
            self._write_refused(job, exc, report)
        except GitHubUncertainWriteError as exc:
            failure = f"the answer to the PR creation was lost: {exc}"
        except GitHubError as exc:
            failure, basis = f"GitHub rejected the PR creation: {exc}", self._definite_basis(exc)
        self._fault("after-pr-created")
        if created is None:
            return self._after_failed_creation(job, approval, attempt, failure, basis, report, client, checked)
        problems = [p for p in ("it is a draft" if created.draft else "",
                                f"its head is {created.head}@{created.head_sha}" if (created.head, created.head_sha) !=
                                (job.source["ref"], job.source["sha"]) else "",
                                f"it targets {created.base}" if created.base != job.base["ref"] else "") if p]
        if problems:
            self._final(job, JobState.STALE, [f"the PR #{created.number} just created does not match the approved head "
                                              f"and base: {'; '.join(problems)} (the source moved after the checks?)"],
                        {"pr": created.number}, report, actor, True)
        report.note("pr-created", "pass", f"PR #{created.number} created from {job.source['ref']} into "
                                          f"{job.base['ref']}")
        return self._bind(self.jobs.load(job.job_id), approval, created, report, True, checked, attempt,
                          created=True)

    @staticmethod
    def _definite_basis(exc: GitHubError) -> str | None:
        """`rejected` when GitHub ANSWERED with an HTTP 4xx error (the request had no effect), `not-sent` when the
        transport reports that the request never left, else None: the outcome is unknown (a 5xx — even one whose body
        is not JSON —, a 2xx that cannot be read, a lost answer)."""
        status = getattr(exc, "status", None)
        if isinstance(status, int) and 400 <= status < 500:
            return "rejected"
        return "not-sent" if isinstance(exc, TransportFailure) and not exc.maybe_applied else None

    def _write_refused(self, job: IntegrationJob, exc: IntegrationWriteRefused, report: MergeReport) -> NoReturn:
        """The write boundary withheld the request (nothing was sent; the intent has no dispatch entry and is closed by
        the next run as never dispatched). The operator sees why."""
        self._refuse(job, "no-approval", exc.reasons, report, True, "nothing was written to GitHub")

    def _after_failed_creation(self, job: IntegrationJob, approval: dict[str, Any], attempt: dict[str, Any],  # pylint: disable=too-many-arguments
                               failure: str, basis: str | None, report: MergeReport, client: Any,
                               checked: Any) -> PullRequest:
        """The creation failed or its answer was lost. GitHub is looked at again before anything else: a PR that exists
        (in any state) is used or reported, never duplicated; GitHub's error answer — or a transport that never sent —
        closes the attempt (*basis*); an unknown outcome leaves it OPEN, however long that takes."""
        repo = self.config.github.repository
        facts = {"repository": repo, "source_repo": job.source["repo"], "source_ref": job.source["ref"],
                 "source_sha": job.source["sha"]}
        try:
            discovery = discover_pull_requests(client, **facts, max_pages=self.config.github.max_pages)
        except (GitHubError, IncompleteListing, CoordinatorError, OSError) as exc:
            if basis:
                self._close_attempt(job.job_id, attempt, "not-created", basis, failure, report)
            self._deferred(job, f"{failure}; GitHub could not be searched afterwards ({exc}): "
                                + ("the attempt was closed (the request had no effect) — repeat later"
                                   if basis else f"the attempt {attempt['id']} stays open — no second creation is "
                                                 "requested"), report, True, outcome="pr-uncertain")
        decision = decide_reuse(discovery.candidates, **facts, base_ref=job.base["ref"], recorded_number=None)
        if decision.result == "reuse":
            pr = next(p for p in discovery.candidates if p.number == decision.number)
            report.note("pr-created", "pass", f"{failure}; the PR #{pr.number} exists on GitHub and is used (no "
                                              "second PR)")
            return self._bind(self.jobs.load(job.job_id), approval, pr, report, True, checked, attempt,
                              created=self._opened_by_us(self.jobs.load(job.job_id), pr, attempt))
        if decision.result == "refused":
            self._blocked(job, "pr-blocked", [failure, *decision.reasons], report, True)
        if basis is None:
            self._unresolved(self.jobs.load(job.job_id), attempt, "PR creation", report, True)
        self._close_attempt(job.job_id, attempt, "not-created", basis, failure, report)
        self._blocked(job, "github-refused", [failure], report, True)

    # -- step 4: GitHub's merge requirements ------------------------------------------------------------------------

    def _readiness(self, job: IntegrationJob, pr: PullRequest, client: Any, report: MergeReport, apply: bool) -> None:
        repo = self.config.github.repository
        try:
            state = pull_merge_state(client, repo, pr.number)
        except (GitHubError, CoordinatorError, OSError) as exc:
            report.note("github-merge-state", "deferred", str(exc))
            self._deferred(job, f"GitHub's merge requirements for PR #{pr.number} could not be read: {exc}", report,
                           apply)
        verdict, why = state.problem()
        report.note("github-merge-state", {"ok": "pass", "pending": "pending", "blocked": "blocked"}[verdict], why)
        if verdict == "blocked":
            self._blocked(job, "pr-draft" if state.draft else "merge-blocked", [f"PR #{pr.number}: {why}"], report,
                          apply)
        if verdict == "pending":
            self._deferred(job, f"PR #{pr.number}: {why}", report, apply, outcome="checks-pending")
        required = self.config.github.integration.required_checks
        observed, unreadable = None, ""
        if required:
            try:
                observed = check_observations(client, repo, job.source["sha"], max_pages=self.config.github.max_pages)
            except (GitHubError, CoordinatorError, OSError) as exc:
                unreadable = f"the checks of the PR head could not be read: {exc}"
        results = evaluate_checks(required, observed, unavailable=unreadable)
        for item in results:
            report.note(f"required-check {item['name']}", item["status"], item["detail"])
        failed = [f"{i['name']}: {i['detail']}" for i in results if i["status"] == "fail"]
        waiting = [f"{i['name']}: {i['detail']}" for i in results if i["status"] in ("pending", "unavailable")]
        if failed:
            self._blocked(job, "checks-failed", failed, report, apply)
        if waiting:
            self._deferred(job, "required checks are not successful yet: " + "; ".join(waiting), report, apply,
                           outcome="checks-pending")

    # -- step 5: the decision ------------------------------------------------------------------------------------

    def _merge_confirmed(self, job_id: str, approval: dict[str, Any], report: MergeReport) -> str:
        """Before a guarded record: the effective MERGE approval re-verified (signature included: no subprocess runs
        under the coordinator lock) and the checkout's configuration re-scanned. The allowed-signers digest it was
        verified against."""
        job = self.jobs.load(job_id)
        current, problems = effective_approval_problems(job, config=self.config, verifier=self.verifier,
                                                        now=self.clock(), stage="merge")
        if current is None or current["id"] != approval["id"] or problems:
            self._refuse(job, "no-approval", problems or ["the effective merge approval changed"], report, True,
                         "nothing was written to GitHub")
        self.git.recheck()
        return self.verifier.last_signers_digest

    @contextlib.contextmanager
    def _merge_guard(self, job_id: str, approval: dict[str, Any], checked: Any, verified_signers: str,
                     ) -> Iterator[IntegrationJob]:
        """The coordinator transaction of a guarded record: the job still AWAITING_MERGE_APPROVAL (an abort wins before
        it), the same MERGE approval effective inside its window at a FRESH clock reading, the allowed-signers file
        unchanged, M3's validation and M4's review still current; else _Undecided (nothing recorded)."""
        with self.store.transaction():
            job = self.jobs.load(job_id)
            effective = effective_approval(job, "merge")
            if job.state is not JobState.AWAITING_MERGE_APPROVAL:
                raise _Undecided("meanwhile", job)
            if effective is None or effective["id"] != approval["id"] or time_problems(effective, self.clock()):
                raise _Undecided("expired", job)
            if self.validator.signers_changed(verified_signers):
                raise _Undecided("signers", job)
            latest = latest_evidence(job, "validation")
            if latest is None or latest["id"] != checked.validation["id"] or not validated(job, checked.plan_sha256):
                raise _Undecided("validation", job)
            if review_eligibility(job, self.config)[0] is None:
                raise _Undecided("review", job)
            yield job

    def _undecided(self, undecided: _Undecided, report: MergeReport) -> NoReturn:
        job = undecided.job
        reasons = {"expired": ("no-approval", "the effective merge approval expired or changed before the record"),
                   "validation": ("not-validated", "M3's validation changed before the record"),
                   "review": ("not-reviewed", "the review is no longer eligible")}
        if undecided.reason in reasons:
            self._refuse(job, reasons[undecided.reason][0], [reasons[undecided.reason][1]], report, True,
                         "nothing was written to GitHub")
        if undecided.reason == "signers":
            report.outcome = "no-approval"
            report.messages.append("the allowed-signers file changed after the approval's signature was verified (a "
                                   "key may have been revoked); nothing was written to GitHub — run merge again")
            raise _review._Stop("signers")  # pylint: disable=protected-access
        self._meanwhile(report, job)

    def _record_attempt(self, job_id: str, approval: dict[str, Any], checked: Any, verified: str, action: str,  # pylint: disable=too-many-arguments
                        pr: int | None, request: dict[str, Any], actor: str, report: MergeReport) -> dict[str, Any]:
        """The write-ahead intent (merge-attempt evidence), under the guard — before any GitHub write."""
        try:
            with self._merge_guard(job_id, approval, checked, verified) as current:
                candidate = current.candidate
                assert candidate is not None
                entry = {"kind": "merge-attempt", "id": f"mat-{len(current.evidence) + 1:04d}", "at": utc_now(),
                         "candidate": candidate["evidence"], "head_sha": candidate["head_sha"],
                         "tree_sha": candidate["tree_sha"], "source_sha": current.source["sha"],
                         "base_sha": current.base["expected_sha"], "approval": approval["id"],
                         "approval_sha256": approval["record_sha256"],
                         "validation": approval["merge"]["validation"]["evidence"],
                         "review": approval["merge"]["review"]["evidence"], "action": action, "pr": pr,
                         "request": request, "actor": actor}
                entry["sha256"] = evidence_digest(entry)
                current.evidence.append(entry)
                self.jobs.save(current)
                self.store.append_event("integration-merge-attempt", job=job_id, evidence=entry["id"], action=action,
                                        pr=pr, approval=approval["id"])
        except _Undecided as undecided:
            self._undecided(undecided, report)
        report.changed = True
        return entry

    # -- step 6: the merge request and its reconciliation -----------------------------------------------------------

    def _merge_and_record(self, job_id: str, approval: dict[str, Any], checked: Any, pr: PullRequest,  # pylint: disable=too-many-arguments,too-many-locals
                          report: MergeReport, actor: str, client: Any) -> None:
        job = self.jobs.load(job_id)
        source_sha = job.source["sha"]
        verified = self._merge_confirmed(job_id, approval, report)
        self._final_pins(job_id, report, actor, "before the merge request")
        self._recheck_pull_request(job, pr, client, report, actor)
        opened = open_merge_attempt(job)
        if opened is not None:   # the reconciliation settles every earlier attempt first: none may be open here
            self._deferred(job, f"an earlier {opened['action']} attempt ({opened['id']}) is still open: no request is "
                                "sent over it", report, True, outcome="merge-uncertain")
        request = {"merge_method": "merge", "sha": source_sha}
        attempt = self._record_attempt(job_id, approval, checked, verified, "merge", pr.number, request, actor, report)
        self._fault("before-merge-request")
        sent = self._send_merge(job_id, pr, attempt, client, report)
        self._fault("after-merge-request")
        self._settle_request(job_id, attempt, pr, sent, report, actor, client)

    def _send_merge(self, job_id: str, pr: PullRequest, attempt: dict[str, Any], client: Any, report: MergeReport,
                    ) -> tuple[Any, str, str | None]:
        """The merge request through the write boundary: (GitHub's answer or None, why it failed, the *basis* that
        establishes a failure — `rejected` / `not-sent` — or None when the outcome is unknown)."""
        try:
            report.github_writes.append(f"PUT pulls/{pr.number}/merge")
            return client.merge_integration_pull_request(pr.number, self.jobs.load(job_id).source["sha"], job_id=job_id,
                                                         attempt=attempt["id"]), "", None
        except IntegrationWriteRefused as exc:
            report.github_writes.pop()
            self._write_refused(self.jobs.load(job_id), exc, report)
        except GitHubUncertainWriteError as exc:
            return None, f"the answer to the merge request was lost or ambiguous: {exc}", None
        except GitHubError as exc:
            return None, f"GitHub refused the merge: {exc}", self._definite_basis(exc)

    def _settle_request(self, job_id: str, attempt: dict[str, Any], pr: PullRequest,  # pylint: disable=too-many-arguments
                        sent: tuple[Any, str, str | None], report: MergeReport, actor: str, client: Any) -> None:
        """What the request did: GitHub is READ (the answer, or the lack of one, is never trusted alone). Merged → by
        whom (module integration_merge_recovery); a definite failure with the PR still open closes the attempt;
        everything else leaves it OPEN for the next run — never a blind repeat."""
        response, failure, basis = sent
        try:
            after = fetch_pull(client, self.config.github.repository, pr.number)
        except (GitHubError, CoordinatorError, OSError) as exc:
            after = None
            if response is None:
                self._deferred(self.jobs.load(job_id), f"{failure}; PR #{pr.number} cannot be read to find out "
                                                       f"whether it was merged ({exc}): the attempt {attempt['id']} "
                                                       "stays open — the next run reads GitHub first and never "
                                                       "repeats the request blindly", report, True,
                               outcome="merge-uncertain")
        if after is not None and after.merged:
            commit = after.merge_commit_sha or (response.sha if response is not None else "")
            if not commit:
                self._deferred(self.jobs.load(job_id), f"PR #{pr.number} is merged but GitHub reports no merge "
                                                       "commit yet — the next run reconciles it", report, True,
                               outcome="merge-uncertain")
            verdict, why = self._request_verdict(self.jobs.load(job_id), attempt, after, sent, client)
            self._after_merged(self.jobs.load(job_id), attempt, after, commit, verdict, why, report, actor, True,
                               basis="response" if response is not None else "actor")
            return
        if response is not None and after is None:   # GitHub said merged; the PR read failed: the answer has the commit
            self._record_merged(job_id, attempt, pr, response.sha, report, actor, "merged", "response")
            return
        if basis is not None and after is not None:   # GitHub explicitly refused and the PR is still open: no effect
            self._close_attempt(job_id, attempt, "not-merged", basis, failure, report)
            self._blocked(self.jobs.load(job_id), "merge-rejected", [failure], report, True)
        what = failure or f"GitHub confirmed a merge, but PR #{pr.number} is not reported as merged"
        self._deferred(self.jobs.load(job_id), f"{what}: the attempt {attempt['id']} stays open — the next run reads "
                                               "GitHub first and never repeats the request blindly", report, True,
                       outcome="merge-uncertain")

    def _request_verdict(self, job: IntegrationJob, attempt: dict[str, Any], after: PullRequest,
                         sent: tuple[Any, str, str | None], client: Any) -> tuple[str, str]:
        """Whose is the merge of the PR GitHub shows merged right after this job's request?"""
        response, failure, basis = sent
        if response is not None:   # GitHub's own answer says this request merged it
            return OWN, f"GitHub's answer to the request {attempt['id']} confirms the merge"
        if basis is not None:      # GitHub refused (or the request never left) and the PR is merged: not ours
            return EXTERNAL, (f"the merge request {attempt['id']} had no effect ({failure}) and PR #{after.number} is "
                              f"merged by {after.merged_by or 'someone else'}")
        return self._attribution(job, attempt, "dispatched", after, client)

    def _recheck_pull_request(self, job: IntegrationJob, pr: PullRequest, client: Any, report: MergeReport,
                              actor: str) -> None:
        """The PR the merge goes through, read AGAIN right before the intent: still open, from the pinned branch at the
        approved SHA, into the pinned base. (The request pins the head, not the base: a PR retargeted since the lookup
        would be merged into the other branch.) A moved head is STALE; anything else blocks."""
        repo = self.config.github.repository
        try:
            now = fetch_pull(client, repo, pr.number)
        except (GitHubError, CoordinatorError, OSError) as exc:
            self._deferred(job, f"PR #{pr.number} could not be read again right before the merge request: {exc}",
                           report, True)
        problems = reuse_problems(now, repository=repo, source_repo=job.source["repo"], source_ref=job.source["ref"],
                                  source_sha=job.source["sha"], base_ref=job.base["ref"])
        if not problems:
            report.note("pr (before the merge request)", "pass", f"PR #{pr.number} is unchanged: open, at the approved "
                                                                 f"SHA, into {job.base['ref']}")
            return
        if now.merged or now.head_sha != job.source["sha"] or now.head != job.source["ref"]:
            self._final(job, JobState.STALE, [f"PR #{pr.number} changed right before the merge request: "
                                              f"{'; '.join(problems)}"], {"pr": pr.number}, report, actor, True)
        self._blocked(job, "pr-blocked", [f"PR #{pr.number} cannot be merged now: {'; '.join(problems)}"], report, True)

    def _final(self, job: IntegrationJob, target: JobState, reasons: list[str], observed: Mapping[str, Any],  # pylint: disable=too-many-arguments
               report: Any, actor: str, apply: bool) -> NoReturn:
        """The review's STALE / FAILED — except that a job which has SENT a merge request is STALE only once the PR was
        read again and is not merged: a request that landed after the last read moves the base branch, and that is the
        job's own merge, not drift (a terminal STALE over a merged PR would leave the merge unrecorded)."""
        sent = [a for a in merge_attempts(job) if a["action"] == "merge"]
        if target is JobState.STALE and sent:
            number = sent[-1]["pr"]
            try:
                merged = fetch_pull(self.inspector.tracker(), self.config.github.repository, number).merged
            except (GitHubError, CoordinatorError, OSError, AttributeError) as exc:
                self._deferred(job, f"{reasons[0]} — but a merge request of this job may have landed and PR #{number} "
                                    f"cannot be read to find out ({exc}): repeat later", report, apply,
                               outcome="merge-uncertain")
            if merged:
                self._deferred(job, f"{reasons[0]} — but PR #{number} is merged: a merge request of this job landed "
                                    "after the last read; the next run reconciles it first", report, apply,
                               outcome="merge-uncertain")
        super()._final(job, target, reasons, observed, report, actor, apply)

    def _record_merged(self, job_id: str, attempt: Mapping[str, Any], pr: PullRequest, commit: str,  # pylint: disable=too-many-arguments
                       report: MergeReport, actor: str, how: str, basis: str) -> None:
        """The merge GitHub performed, recorded in ONE transaction: merge-outcome `merged`, `merge_result` and
        AWAITING_MERGE_APPROVAL → MERGED. (An abort cannot have intervened: it is refused while an attempt is open.)"""
        with self.store.transaction():
            current = self.jobs.load(job_id)
            if current.state is not JobState.AWAITING_MERGE_APPROVAL:
                report.messages.append(f"WARNING: PR #{pr.number} is merged on GitHub (merge commit {commit}) but "
                                       f"{job_id} is {current.state.value}: reconcile it by hand")
                self._meanwhile(report, current)
            result = {"commit_sha": commit, "parents": [], "tree_sha": "", "verified": False, "pr": pr.number,
                      "merged_at": pr.merged_at or "", "merged_by": pr.merged_by or "", "attempt": attempt["id"],
                      "verification": {"state": "pending", "at": utc_now(), "checks": []}}
            outcome = self._outcome_entry(current, attempt, "merged", pr.number, commit,
                                          f"PR #{pr.number} merged by GitHub ({how})", basis)
            _, changed = self.jobs.transition(
                job_id, JobState.MERGED, f"PR #{pr.number} merged by GitHub as {commit} ({how})", actor=actor,
                expect=(JobState.AWAITING_MERGE_APPROVAL,), evidence=[outcome], merge_result=result, attention=None)
            if not changed:
                self._meanwhile(report, self.jobs.load(job_id))
            self.store.append_event("integration-merged", job=job_id, pr=pr.number, merge_commit=commit, how=how)
        report.changed = True
        report.merge_result = result
        report.messages.append(f"PR #{pr.number} was merged by GitHub as {commit}: {job_id} is MERGED")
        self._fault("after-merged-record")

    # -- step 7: post-merge verification ---------------------------------------------------------------------------

    def _verify_merged(self, job: IntegrationJob, report: MergeReport, client: Any, actor: str, apply: bool) -> None:
        result = job.merge_result
        assert result is not None
        verification = verify_merge(job, result, client=client, config=self.config, git=self.git)
        state = verification.state
        report.merge_result = result
        report.verification = {"state": state, "checks": verification.checks}
        for check in verification.checks:
            report.note(f"post-merge {check['name']}", check["status"], check["detail"])
        if not apply:
            report.outcome = {"verified": "would-verify", "pending": "pending-verification",
                              "failed": "would-fail"}[state]
            report.reasons = verification.reasons("fail", "pending", "unavailable")
            return
        self._fault("before-completed")
        if state == "verified":
            self._complete(job, verification, report, actor)
        elif state == "failed":
            self._fail(job, verification, report, actor)
        else:
            self._pending(job, verification, report)

    @staticmethod
    def _updated(result: Mapping[str, Any], verification: Verification, state: str) -> dict[str, Any]:
        return {**result, "parents": verification.parents or list(result.get("parents") or []),
                "tree_sha": verification.tree or result.get("tree_sha", ""), "verified": state == "verified",
                "verification": {"state": state, "at": utc_now(), "checks": verification.checks}}

    def _complete(self, job: IntegrationJob, verification: Verification, report: MergeReport, actor: str) -> None:
        with self.store.transaction():
            current = self.jobs.load(job.job_id)
            if current.state is not JobState.MERGED:
                self._meanwhile(report, current)
            merged = self._updated(current.merge_result or {}, verification, "verified")
            final, changed = self.jobs.transition(
                job.job_id, JobState.COMPLETED, f"post-merge verification passed for merge commit "
                                                f"{merged['commit_sha']}", actor=actor, expect=(JobState.MERGED,),
                merge_result=merged)
            if not changed:
                self._meanwhile(report, final)
            self.store.append_event("integration-completed", job=job.job_id, merge_commit=merged["commit_sha"])
        report.changed, report.outcome, report.merge_result = True, "completed", merged
        report.messages.append(f"{job.job_id} is COMPLETED: the merge was verified (design §5.10)")

    def _fail(self, job: IntegrationJob, verification: Verification, report: MergeReport, actor: str) -> None:
        reasons = verification.reasons("fail")[:MAX_LISTED]
        with self.store.transaction():
            current = self.jobs.load(job.job_id)
            if current.state is not JobState.MERGED:
                self._meanwhile(report, current)
            merged = self._updated(current.merge_result or {}, verification, "failed")
            attention = {"reason": f"post-merge verification FAILED: {reasons[0]}", "reasons": reasons,
                         "observed": {"merge_commit": merged["commit_sha"], "pr": merged["pr"]}, "since": utc_now()}
            final, changed = self.jobs.transition(
                job.job_id, JobState.FAILED, "post-merge verification failed: " + "; ".join(reasons), actor=actor,
                expect=(JobState.MERGED,), merge_result=merged, attention=attention)
            if not changed:
                self._meanwhile(report, final)
            self.store.append_event("integration-merge-failed", job=job.job_id, merge_commit=merged["commit_sha"],
                                    reason=reasons[0])
        report.changed, report.outcome, report.reasons, report.merge_result = True, "failed", reasons, merged
        report.messages.append(f"{job.job_id} is FAILED: the merge is already on {job.base['ref']} but is not what was "
                               "approved. Manual operator remediation is required — the coordinator never "
                               "reverts, resets or force-pushes; a reviewed follow-up change or revert goes through "
                               "a new job")

    def _pending(self, job: IntegrationJob, verification: Verification, report: MergeReport) -> None:
        with self.store.transaction():
            current = self.jobs.load(job.job_id)
            if current.state is not JobState.MERGED:
                self._meanwhile(report, current)
            before = current.merge_result or {}
            merged = self._updated(before, verification, "pending")
            if (before.get("verification") or {}).get("checks") != verification.checks \
                    or before.get("parents") != merged["parents"] or before.get("tree_sha") != merged["tree_sha"]:
                current.merge_result = merged
                self.jobs.save(current)
                report.changed = True
            else:
                merged = before
            self.store.append_event("integration-merge-pending", job=job.job_id, reason=clean_text(
                "; ".join(verification.reasons("pending", "unavailable")), 300))
        report.outcome, report.merge_result = "pending-verification", merged
        report.reasons = verification.reasons("pending", "unavailable")
        report.messages.append(f"{job.job_id} is MERGED but NOT completed: post-merge verification is pending — repeat "
                               "`integration merge --apply` once the checks have finished")
