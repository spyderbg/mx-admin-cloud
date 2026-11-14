"""W4.7 (M3): `integration validate --job J` — build the integration candidate and run its validation gates (design §4
CANDIDATE_BUILT / VALIDATING, §5, §6.3, §8.1 `validate`).

    python -m feature_coordinator integration validate --job J [--actor NAME] [--apply] [--json]

Order:
  0. the job exists; a terminal job is reported as it is. It is INSPECTING with a CURRENT passed inspection
     (integration.current_inspection_passed: nothing deferred, the newest inspection passed under the currently
     effective approval), or CANDIDATE_BUILT / VALIDATING with its recorded candidate (a resumed run). Its effective
     inspect approval is valid NOW (signature re-verified, operator listed, not expired, bound to the pinned SHAs) —
     else nothing happens (`no-approval`, also for a dry run). The checkout's `origin` and history must be fit.
  1. with --apply: one integration operation per job at a time (`locks/integration-<job>.lock`, shared with `inspect`,
     non-blocking: a concurrent one is refused). Without --apply (default) everything below runs read-only in a
     temporary repository, no gate runs, and the report says what --apply would do.
  2. the Issue: GitHub must report Issue #N of the job as an open Issue (not a pull request) — the signed inspect
     approval, which binds that Issue number, is the authorization for local candidate validation; no planning label is
     required (integration-jobs.md §22). Unreadable → deferred; closed or not an Issue → `issue-blocked`. Nothing is
     recorded for either.
  3. pinned values (integration_inspect.check_pinned, M2's rules): origin's source and base branches still at the
     pinned SHAs, and the pinned PR (verified at intake, or the PR the passed inspection decided to reuse) unchanged,
     a change reported by its single read confirmed by the complete PR lookup → else STALE; unreadable → deferred.
  4. the candidate (integration_candidate): `git merge-tree --write-tree base_sha source_sha` in the job's
     coordinator-owned repository, the deterministic candidate commit, both read back hash-verified. A job that
     recorded a candidate must rebuild exactly the same head and tree SHAs — else STALE (its candidate identity
     changed; no evidence transfers). A conflict → REFUSED (INSPECTING → REFUSED, design §8.4) with the conflicted
     paths in `attention`; the source is never patched.
  5. the candidate's paths: base tree → candidate tree, classified through PathPolicy with the effective approval's
     per-path authorizations (integration_paths.classify_paths, M2's rules). Refused (protected, gitlink, escaping
     symlink, ...) → REFUSED; approval-required / unowned / symlink / git-control paths not authorized → the job stays
     INSPECTING with `attention` (sign a new approval naming them, inspect again, validate again). On a resumed run the
     same problem is final: CANDIDATE_BUILT → REFUSED, VALIDATING → FAILED (approvals are only recorded before
     CANDIDATE_BUILT).
  6. INSPECTING → CANDIDATE_BUILT with the `candidate` and a `built` candidate evidence entry, decided like an M2
     inspection decision: the job is still INSPECTING with a current passed inspection under the same effective
     approval, re-verified just before the coordinator transaction and, inside it, its time window checked with a
     FRESH clock and the allowed-signers file unchanged; the checkout's configuration still names no program.
  7. CANDIDATE_BUILT → VALIDATING (naming the planned gates), then the validation ATTEMPT, recorded before any gate
     runs (write-ahead, Codex review of M3 F2): an `attempt` evidence entry with the plan's digest and the gates this
     run will run. From that moment an earlier passed decision no longer counts (integration.validated), so a crash
     during a re-validation never leaves an outdated success. An open attempt (no decision names it: interrupted) of
     the same plan is resumed, and a blocked attempt of the same plan is repeated without a new entry. Then every
     planned gate (integration_gates.gate_plan) in order: a FAIL recorded for this candidate stands under ANY gate
     definition and is never re-run (not after a changed timeout or command either: Codex review of M3 F1); a PASS or
     SKIPPED of the same gate definition stands; BLOCKED runs again. Otherwise the workspace is materialized (once per
     run) and the gate runs in the sandbox, and its evidence (status, exit code, duration, log digests, toolchain) is
     appended right after it finishes. Before each gate the job must still be VALIDATING (an operator abort stops it).
  8. the validation decision (a `validation` evidence entry naming the attempt, over the current result of every
     planned gate), recorded only after the pinned values are verified AGAIN (step 3's rules: source and base still at
     the pinned SHAs, the pinned PR unchanged; a change → STALE, never a decision for a stale candidate; unreadable →
     deferred, no decision — Codex review of M3 F3), under the same, still valid approval (re-verified; fresh clock
     and unchanged allowed-signers inside the transaction) and a checkout configuration that names no program — else
     `no-approval` and only the gate evidence stays:
       failed   a required gate FAILED, or ANY gate FAILED for this candidate (also one no longer in the plan: a
                recorded failure is never dropped) → VALIDATING → NEEDS_REPAIR (design §6.4; repair routing is M6);
                `attention` lists the failed gates and their logs;
       blocked  no FAIL, but a gate is BLOCKED (missing, unsafe, unavailable tool, timeout, error) → the job STAYS
                VALIDATING with `attention`; fix the cause and run validate again (passed gates are reused);
       passed   every gate PASS or SKIPPED → the job STAYS VALIDATING, validated (integration.validated): M4's input.
     Repeating it with nothing new changes nothing.

Every decision is recorded only while the job is still in the state it was based on, so an abort that lands meanwhile
wins and the run changes nothing more (`changed-meanwhile`). The workspace is removed when the job becomes terminal.
A crash anywhere leaves the job in its last recorded state; running validate again resumes it (a gate whose result was
not recorded runs again against the same SHAs, design §5.5). The report always carries the job as persisted when the
run ended (Codex review of M3 F5), so JSON and text describe the same state.
"""

from __future__ import annotations

import contextlib
import shutil
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping

from .config import ProjectConfig
from .errors import CoordinatorError, GitHubError, GitHubNotFoundError, LockBusyError
from .gates import GateContext
from .integration import (
    IntegrationError,
    IntegrationJob,
    IntegrationJobs,
    JobState,
    clean_text,
    current_gate_evidence,
    current_inspection_passed,
    effective_approval,
    evidence_digest,
    job_summary,
    latest_evidence,
    latest_inspection,
    open_attempt,
    paths_digest,
    recorded_failures,
    validated,
)
from .integration_approval import ApprovalRefused, SshSignatureVerifier, effective_approval_problems, now_utc, \
    time_problems
from .integration_candidate import Candidate, CandidateArea, CandidateGit, build_candidate, ensure_repository, \
    identity_problem, materialize
from .integration_gates import CandidateGateRunner, PlannedGate, Toolchain, gate_entry, gate_object_directories, \
    gate_plan, plan_view, prepare_toolchain, run_gate, same_blocked, standing
from .integration_inspect import IntegrationInspector
from .integration_intake import MAX_LISTED, ReadOnlyGit
from .integration_objects import HistoryRefused
from .integration_paths import ClassifiedPaths, classify_paths
from .issue_sync import SimulatedCrash
from .locks import FileLock
from .model import GateResult, utc_now
from .paths import PathPolicy

VALIDATABLE_STATES = frozenset({JobState.INSPECTING, JobState.CANDIDATE_BUILT, JobState.VALIDATING})
OK_OUTCOMES = ("validated", "would-validate", "would-run-gates")
GateRunnerFactory = Callable[[Path, Toolchain], Callable[[str, GateContext], GateResult]]


@dataclass
class ValidateReport:  # pylint: disable=too-many-instance-attributes
    """What one validation did (apply) or would do (dry run)."""

    applied: bool
    outcome: str = ""
    changed: bool = False
    approval: str | None = None
    job: dict[str, Any] | None = None
    checks: list[dict[str, str]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    candidate: dict[str, Any] | None = None
    gates: list[dict[str, Any]] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the candidate is validated (or a dry run found nothing in the way)."""
        return self.outcome in OK_OUTCOMES

    def note(self, name: str, status: str, detail: str = "") -> None:
        """Record one check: pass | fail | stale | deferred | blocked | attention."""
        self.checks.append({"check": name, "status": status, "detail": clean_text(detail, 300)})

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "ok": self.ok}


class IntegrationValidator:  # pylint: disable=too-many-instance-attributes
    """Candidate construction and validation gates for one checkout and state directory (module docstring)."""

    def __init__(self, store: Any, config: ProjectConfig, git: ReadOnlyGit, *, verifier: SshSignatureVerifier,
                 tracker: Callable[[], Any] | None = None, gate_runner: GateRunnerFactory | None = None,
                 faults: Iterable[str] = (), clock: Callable[[], datetime] = now_utc) -> None:
        self.store, self.config, self.git = store, config, git
        self.jobs = IntegrationJobs(store)
        self.verifier = verifier
        self.inspector = IntegrationInspector(store, config, git, verifier=verifier, tracker=tracker, clock=clock)
        self.policy = PathPolicy(git.root, config)
        self.gate_runner = gate_runner or self._sandboxed_runner
        self.faults = set(faults)
        self.clock = clock

    def _fault(self, name: str) -> None:
        if name in self.faults:
            self.faults.discard(name)
            raise SimulatedCrash(name)

    def _sandboxed_runner(self, log_root: Path, toolchain: Toolchain) -> Callable[[str, GateContext], GateResult]:
        return CandidateGateRunner(self.config, log_root, checkout=self.git.root, binds=toolchain.binds,
                                   state_root=self.store.root,
                                   timeouts=dict(self.config.github.integration.gate_timeouts))

    # -- the run -----------------------------------------------------------------------------------

    def run(self, job_id: str, *, apply: bool, actor: str = "operator") -> ValidateReport:
        """Validate *job_id*; with *apply*, build, run the gates and record. IntegrationError (nothing recorded) for a
        precondition that is not the job's fault."""
        with self.git.session():
            return self._run(job_id, apply=apply, actor=actor)

    def _run(self, job_id: str, *, apply: bool, actor: str) -> ValidateReport:
        report = ValidateReport(applied=apply)
        job = self.jobs.load(job_id)
        report.job = job_summary(job)
        if job.terminal:
            report.outcome = "terminal"
            report.messages.append(f"{job_id} is {job.state.value} (terminal) and is never validated again")
            return report
        if job.state not in VALIDATABLE_STATES:
            raise IntegrationError(f"{job_id} is {job.state.value}: validate applies to an INSPECTING job whose "
                                   "inspection passed, or to a CANDIDATE_BUILT / VALIDATING job")
        if job.state is not JobState.INSPECTING and job.candidate is None:
            raise IntegrationError(f"conflicting state: {job_id} is {job.state.value} but carries no candidate; it is "
                                   "never validated in that state — abort it (`integration abort`)")
        if apply and not self.config.github.integration.enabled:
            raise IntegrationError("github.integration.enabled is false: validations are recorded only once an "
                                   "operator enables integration jobs")
        if job.state is JobState.INSPECTING and not current_inspection_passed(job):
            return self._not_inspected(job, report, apply)
        approval, problems = effective_approval_problems(job, config=self.config, verifier=self.verifier,
                                                         now=self.clock())
        if not problems and approval is not None and job.candidate is not None:
            built = self._built_entry(job)
            if built["approval"] != approval["id"]:   # approvals are recorded only before CANDIDATE_BUILT
                problems = [f"the effective approval {approval['id']} is not {built['approval']}, under which the "
                            "candidate was built"]
        if problems:
            return self._refused(job, "no-approval", problems, report, apply,
                                 f"{job_id} is not validated: it has no valid inspect approval (nothing changed)")
        assert approval is not None
        report.approval = approval["id"]
        self._preflight()
        if not apply:
            return self._dry_run(job, approval, report)
        lock = FileLock(self.store.locks.dir / f"integration-{job_id}.lock")
        try:
            lock.acquire(timeout=0)
        except LockBusyError as exc:
            raise IntegrationError(f"another integration operation on {job_id} (inspect or validate) is running; "
                                   "nothing was done") from exc
        try:
            current = self.jobs.load(job_id)
            if current.state is not job.state:
                return self._meanwhile(report, current)
            report = self._apply(current, approval, report, actor)
            report.job = job_summary(self._tidy(job_id))   # the persisted job, whatever path the run took (F5)
            return report
        finally:
            lock.release()

    def _tidy(self, job_id: str) -> IntegrationJob:
        """After an applied run: a job that ended terminal (e.g. aborted while this run materialized) keeps nothing of
        its candidate but the gate logs — the repository too after an abort, like `integration abort --apply`. Returns
        the job as persisted."""
        final = self.jobs.load(job_id)
        if final.terminal:
            area = CandidateArea(self.store.root, job_id)
            if final.state is JobState.ABORTED:
                area.discard()
            else:
                area.discard_worktree()
        return final

    def _preflight(self) -> None:
        problem = self.git.origin_problem()
        if problem:
            raise IntegrationError(f"cannot resolve refs for a validation: {problem}; nothing was recorded")
        problem = self.git.repository_problem()
        if problem:
            raise IntegrationError(f"this checkout cannot be used for a validation: {problem}; nothing was recorded")

    @staticmethod
    def _built_entry(job: IntegrationJob) -> dict[str, Any]:
        assert job.candidate is not None
        return next(entry for entry in job.evidence if entry["id"] == job.candidate["evidence"])

    def _not_inspected(self, job: IntegrationJob, report: ValidateReport, apply: bool) -> ValidateReport:
        latest, approval = latest_inspection(job), effective_approval(job)
        if latest is None:
            why = "it has not been inspected"
        elif job.deferred is not None:
            why = f"its inspection is deferred ({job.deferred['reason']})"
        elif approval is not None and latest["approval"] != approval["id"]:
            why = (f"its newest inspection {latest['id']} ran under {latest['approval']}, not under the effective "
                   f"approval {approval['id']}")
        else:
            why = f"its newest inspection {latest['id']} is {latest['result']}"
        return self._refused(job, "not-inspected", [f"{job.job_id} has no current passed inspection: {why}; run "
                                                    f"`integration inspect --job {job.job_id} --apply` first"],
                             report, apply, "nothing changed")

    def _refused(self, job: IntegrationJob, outcome: str, reasons: list[str], report: ValidateReport, apply: bool,
                 message: str) -> ValidateReport:
        report.outcome, report.reasons = outcome, reasons
        report.messages.append(message)
        if apply:
            with self.store.transaction():
                self.store.append_event("integration-validation-refused", job=job.job_id, outcome=outcome,
                                        reason=clean_text(reasons[0]))
        return report

    @staticmethod
    def _meanwhile(report: ValidateReport, job: IntegrationJob) -> ValidateReport:
        report.outcome, report.job = "changed-meanwhile", job_summary(job)
        report.messages.append(f"{job.job_id} changed while the validation ran (now {job.state.value}); nothing more "
                               "was changed by this run")
        return report

    def _deferred(self, job: IntegrationJob, reason: str, report: ValidateReport, *, apply: bool = True,
                  outcome: str = "deferred") -> ValidateReport:
        report.outcome, report.reasons = outcome, [clean_text(reason)]
        report.messages.append(f"{job.job_id} is unchanged ({job.state.value}): {clean_text(reason)} — repeat the "
                               "same command later")
        if apply:
            with self.store.transaction():
                self.store.append_event(f"integration-validation-{outcome}", job=job.job_id,
                                        reason=clean_text(reason))
        return report

    # -- the checks shared by apply and dry run ----------------------------------------------------

    def _issue_problem(self, job: IntegrationJob, report: ValidateReport) -> tuple[str, str] | None:
        """(outcome, reason) when the job's Issue is not verified as an open Issue (step 2), else None."""
        tracker = self.inspector.tracker()
        number = job.issue
        try:
            if tracker is None:
                raise GitHubError("no GitHub client is configured")
            issue = tracker.get_issue(number)
        except GitHubNotFoundError:
            reason = (f"Issue #{number} cannot be found (HTTP 404): check the GitHub login's access to "
                      f"{self.config.github.repository}")
            report.note("issue", "deferred", reason)
            return "deferred", reason
        except (GitHubError, OSError) as exc:
            reason = f"Issue #{number} could not be read: {exc}"
            report.note("issue", "deferred", reason)
            return "deferred", reason
        problems = []
        if issue.number != number:
            problems.append(f"GitHub answered for #{issue.number}")
        if issue.is_pull_request:
            problems.append("it is a pull request, not an Issue")
        if issue.state != "open":
            problems.append(f"it is {issue.state}" + (f" ({issue.state_reason})" if issue.state_reason else ""))
        if problems:
            reason = f"Issue #{number} of the job does not justify a validation: {'; '.join(problems)}"
            report.note("issue", "blocked", reason)
            return "issue-blocked", reason
        report.note("issue", "pass", f"Issue #{number} is open in {self.config.github.repository}")
        return None

    def _pinned_pr(self, job: IntegrationJob) -> dict[str, Any] | None:
        """The PR the passed inspection decided to reuse, as pinned values (None: the PR verified at intake, if any,
        is checked by check_pinned itself)."""
        if job.pr and len(job.pr) > 1:
            return None
        latest = latest_inspection(job)
        match = latest["pr_match"] if latest is not None else {}
        if match.get("result") != "reuse" or not match.get("number"):
            return None
        return {"number": match["number"], "head_repo": job.source["repo"], "head_ref": job.source["ref"],
                "head_sha": job.source["sha"], "base_repo": self.config.github.repository,
                "base_ref": job.base["ref"]}

    def _objects_dir(self) -> Path:
        return self.git.isolated().objects_dir

    def _classify(self, candidate: Candidate, approval: Mapping[str, Any]) -> ClassifiedPaths:
        return classify_paths(candidate.changes, self.policy, self.config, approval["paths"])

    @staticmethod
    def _candidate_view(candidate: Candidate, classified: ClassifiedPaths | None, *, reused: bool) -> dict[str, Any]:
        entries = classified.entries if classified is not None else []
        return {"head_sha": candidate.head, "tree_sha": candidate.tree, "conflicts": candidate.conflicts[:MAX_LISTED],
                "messages": candidate.messages[:MAX_LISTED], "git": candidate.git, "recorded": reused,
                "roles": sorted({e["role"] for e in entries if e["role"]}),
                "paths": {"count": len(entries), "required": len(classified.required) if classified else 0,
                          "authorized": len(classified.authorized) if classified else 0,
                          "refused": len(classified.refusals) if classified else 0}}

    # -- the dry run -------------------------------------------------------------------------------

    def _dry_run(self, job: IntegrationJob, approval: dict[str, Any], report: ValidateReport) -> ValidateReport:
        report.messages.append("dry run — nothing was recorded and no gate ran (pass --apply to validate)")
        problem = self._issue_problem(job, report)
        if problem:
            report.outcome, report.reasons = {"deferred": "would-defer"}.get(problem[0], problem[0]), [problem[1]]
            return report
        pinned = self.inspector.check_pinned(job, self._pinned_pr(job))
        report.checks += pinned.checks
        if pinned.stale or pinned.deferred:
            report.outcome = "would-stale" if pinned.stale else "would-defer"
            report.reasons = pinned.stale or [pinned.deferred]
            return report
        scratch = self.store.root / "tmp"
        work = Path(tempfile.mkdtemp(prefix="integration-validate-", dir=scratch if scratch.is_dir() else None))
        try:
            ensure_repository(work / "repo.git", self._objects_dir())
            candidate = build_candidate(CandidateGit(work / "home", timeout=self.git.timeout), work / "repo.git", job)
        except HistoryRefused as exc:
            report.outcome, report.reasons = "would-refuse", [f"the candidate cannot be built from verified objects: "
                                                              f"{clean_text(exc)}"]
            return report
        except (CoordinatorError, OSError) as exc:
            report.outcome, report.reasons = "would-defer", [f"the candidate could not be built: {clean_text(exc)}"]
            return report
        finally:
            shutil.rmtree(work, ignore_errors=True)
        return self._dry_candidate(job, approval, candidate, report)

    def _dry_candidate(self, job: IntegrationJob, approval: dict[str, Any], candidate: Candidate,
                       report: ValidateReport) -> ValidateReport:
        mismatch = identity_problem(job.candidate, candidate) if job.candidate is not None else None
        classified = self._classify(candidate, approval) if candidate.clean and not mismatch else None
        report.candidate = self._candidate_view(candidate, classified, reused=job.candidate is not None)
        if mismatch:
            report.outcome, report.reasons = "would-stale", [mismatch]
        elif not candidate.clean:
            report.outcome, report.reasons = "would-refuse", [self._conflict_reason(candidate)]
        elif classified is not None and (classified.refusals or classified.required):
            report.outcome = "would-refuse" if classified.refusals or job.state is not JobState.INSPECTING \
                else "would-need-authorization"
            report.reasons = classified.refusals or self._authorization_reasons(classified)
        else:
            assert classified is not None
            self._dry_gates(job, gate_plan(classified.entries, self.config), report)
            actions = {gate["action"] for gate in report.gates}
            report.outcome = ("would-fail" if "fail" in actions else "would-block" if "blocked" in actions
                              else "would-run-gates" if "run" in actions else "would-validate")
        return report

    @staticmethod
    def _dry_gates(job: IntegrationJob, plan: list[PlannedGate], report: ValidateReport) -> None:
        """(Dry run) per gate what --apply would do: fail (a FAIL recorded for the candidate stands, also for a gate
        no longer planned), reuse, blocked or run; and whether an attempt is open or the plan changed since the
        decision."""
        current, failures = current_gate_evidence(job), recorded_failures(job)
        for planned in plan:
            entry = standing(current, failures, planned)
            action = ("fail" if entry is not None and entry["result"] == "FAIL" else "reuse" if entry is not None
                      else "blocked" if planned.kind in ("missing", "unsafe") else "run")
            report.gates.append({"gate": planned.name, "required_by": list(planned.required_by), "action": action,
                                 "detail": planned.detail, "timeout_seconds": planned.timeout})
        for name, entry in sorted(failures.items()):
            if name not in {planned.name for planned in plan}:
                report.gates.append({"gate": name, "required_by": entry["required_by"], "action": "fail",
                                     "detail": f"FAIL recorded for this candidate ({entry['id']}); the gate is no "
                                               "longer planned, and a recorded failure always stands",
                                     "timeout_seconds": None})
        attempt, latest = open_attempt(job), latest_evidence(job, "validation")
        if attempt is not None:
            report.messages.append(f"validation attempt {attempt['id']} (started {attempt['at']}) has no decision: it "
                                   "was interrupted; --apply resumes it")
        if latest is not None and latest["plan"]["sha256"] != plan_view(plan)["sha256"]:
            report.messages.append(f"the gate plan changed since the decision {latest['id']}"
                                   + (" (validated under the earlier plan)" if validated(job) else "")
                                   + ": --apply records a new attempt and decision")

    @staticmethod
    def _conflict_reason(candidate: Candidate) -> str:
        shown = ", ".join(candidate.conflicts[:MAX_LISTED])
        return (f"merging the source into the base conflicts in {len(candidate.conflicts)} path(s): {shown} — the "
                "source is never patched; a repair branch and a new job are needed")

    @staticmethod
    def _authorization_reasons(classified: ClassifiedPaths) -> list[str]:
        shown = ", ".join(classified.required[:MAX_LISTED])
        return [f"the candidate changes {len(classified.required)} path(s) that need an explicit per-path "
                f"authorization in the signed record (approval-required, unowned, symlink or git-control): {shown}"]

    # -- apply -------------------------------------------------------------------------------------

    def _apply(self, job: IntegrationJob, approval: dict[str, Any], report: ValidateReport,  # pylint: disable=too-many-return-statements
               actor: str) -> ValidateReport:
        area = CandidateArea(self.store.root, job.job_id)
        problem = self._issue_problem(job, report)
        if problem:
            return self._deferred(job, problem[1], report, outcome=problem[0])
        pinned = self.inspector.check_pinned(job, self._pinned_pr(job))
        report.checks += pinned.checks
        if pinned.stale:
            return self._final(job, approval, JobState.STALE, pinned.stale, pinned.observed, report, actor, area)
        if pinned.deferred:
            return self._deferred(job, pinned.deferred, report)
        try:
            area.ensure(self._objects_dir())
            candidate = build_candidate(CandidateGit(area.home, timeout=self.git.timeout), area.repo, job)
        except HistoryRefused as exc:
            return self._final(job, approval, JobState.REFUSED if job.state is not JobState.VALIDATING
                               else JobState.FAILED, [f"the candidate cannot be built from verified objects: {exc}"],
                               {}, report, actor, area)
        except (CoordinatorError, OSError) as exc:
            return self._deferred(job, f"the candidate could not be built: {exc}", report)
        self._fault("after-build")
        if job.candidate is not None:
            mismatch = identity_problem(job.candidate, candidate)
            if mismatch:
                report.candidate = self._candidate_view(candidate, None, reused=True)
                return self._final(job, approval, JobState.STALE, [f"candidate identity changed: {mismatch}"],
                                   {"recorded": {k: job.candidate[k] for k in ("head_sha", "tree_sha")},
                                    "rebuilt": {"head_sha": candidate.head, "tree_sha": candidate.tree}},
                                   report, actor, area)
        classified = self._classify(candidate, approval) if candidate.clean else None
        report.candidate = self._candidate_view(candidate, classified, reused=job.candidate is not None)
        report.note("candidate", "pass" if candidate.clean else "fail",
                    f"merge of {job.source['sha'][:12]} into {job.base['expected_sha'][:12]}: "
                    + (f"commit {candidate.head[:12]}, tree {candidate.tree[:12]}" if candidate.head
                       else f"{len(candidate.conflicts)} conflicted path(s)"))
        if job.state is JobState.INSPECTING:
            job = self._decide_candidate(job, approval, candidate, classified, report, actor, area)
            if job is None:
                return report
        elif classified is not None and (classified.refusals or classified.required):
            target = JobState.REFUSED if job.state is JobState.CANDIDATE_BUILT else JobState.FAILED
            reasons = classified.refusals or self._authorization_reasons(classified)
            return self._final(job, approval, target, [f"the recorded candidate is no longer authorized: {r}"
                                                       for r in reasons], {}, report, actor, area)
        assert classified is not None
        plan = gate_plan(classified.entries, self.config)
        if job.state is JobState.CANDIDATE_BUILT:
            job, _changed = self.jobs.transition(job.job_id, JobState.VALIDATING,
                                                 f"validation gates started: {', '.join(p.name for p in plan)}",
                                                 actor=actor, expect=(JobState.CANDIDATE_BUILT,))
            if job.state is not JobState.VALIDATING:
                return self._meanwhile(report, job)
            report.changed = True
        self._fault("after-validating")
        return self._gates(job, plan, report, actor, area, approval)

    # -- INSPECTING: the candidate decision ----------------------------------------------------------

    def _decide_candidate(self, job: IntegrationJob, approval: dict[str, Any], candidate: Candidate,  # pylint: disable=too-many-arguments
                          classified: ClassifiedPaths | None, report: ValidateReport, actor: str,
                          area: CandidateArea) -> IntegrationJob | None:
        """(INSPECTING) record the candidate decision; the job in CANDIDATE_BUILT, or None when the run ends here."""
        if not candidate.clean:
            result, reasons = "conflict", [self._conflict_reason(candidate)]
        elif classified is not None and classified.refusals:
            result, reasons = "refused", classified.refusals
        elif classified is not None and classified.required:
            result, reasons = "needs-authorization", self._authorization_reasons(classified)
        else:
            result, reasons = "built", []
        entry = self._candidate_entry(job, approval, candidate, classified, result, reasons)
        verified = self._confirmed(job.job_id, approval, report)
        if verified is None:
            return None
        try:
            with self._inspecting(job.job_id, approval, verified) as current:
                job = self._record_candidate(current, approval, candidate, classified, entry, result, reasons,
                                             report, actor, area)
        except _Undecided as undecided:
            self._undecided(undecided, approval, report)
            return None
        if job.state is JobState.CANDIDATE_BUILT:
            self._fault("after-candidate")
            return job
        report.job = job_summary(job)
        if job.terminal:
            area.discard_worktree()
        if result == "needs-authorization":
            report.messages.append("sign a new authorization record naming these paths, record it (`integration "
                                   "approve`), run `integration inspect --apply` and then validate again")
        return None

    def _record_candidate(self, current: IntegrationJob, approval: dict[str, Any], candidate: Candidate,  # pylint: disable=too-many-arguments
                          classified: ClassifiedPaths | None, entry: dict[str, Any], result: str, reasons: list[str],
                          report: ValidateReport, actor: str, area: CandidateArea) -> IntegrationJob:
        """(Inside the confirmed transaction) append the candidate entry: CANDIDATE_BUILT, REFUSED, or the job stays
        INSPECTING with `attention` (needs-authorization; repeating the same finding changes nothing)."""
        job = current
        entry["id"] = f"can-{len(current.evidence) + 1:04d}"
        if result == "built":
            assert classified is not None and candidate.head is not None
            field_ = {"evidence": entry["id"], "head_sha": candidate.head, "tree_sha": candidate.tree,
                      "base_sha": job.base["expected_sha"], "source_sha": job.source["sha"],
                      "repository": str(area.repo), "worktree": str(area.worktree), "git": candidate.git,
                      "built_at": utc_now(), "paths": classified.entries}
            job, report.changed = self.jobs.transition(
                job.job_id, JobState.CANDIDATE_BUILT, f"candidate {candidate.head} (tree {candidate.tree}) built "
                f"under approval {approval['id']}", actor=actor, expect=(JobState.INSPECTING,), evidence=[entry],
                candidate=field_, attention=None)
            self.store.append_event("integration-candidate", job=job.job_id, evidence=entry["id"],
                                    head_sha=candidate.head, tree_sha=candidate.tree, result=result)
            return job
        report.outcome, report.reasons = result, reasons
        attention = {"reason": reasons[0], "reasons": reasons[:MAX_LISTED],
                     "observed": {"tree_sha": candidate.tree, "conflicts": candidate.conflicts[:MAX_LISTED],
                                  "paths_requiring_authorization": (classified.required[:MAX_LISTED]
                                                                    if classified else [])},
                     "since": (current.attention or {}).get("since") or utc_now()}
        if result in ("conflict", "refused"):
            job, report.changed = self.jobs.transition(job.job_id, JobState.REFUSED, "; ".join(reasons), actor=actor,
                                                       expect=(JobState.INSPECTING,), evidence=[entry],
                                                       attention=attention)
            return job
        previous = latest_evidence(current, "candidate")
        if previous is None or previous["sha256"] != entry["sha256"] \
                or (current.attention or {}).get("reasons") != attention["reasons"]:
            current.evidence.append(entry)
            current.attention = attention
            self.jobs.save(current)
            self.store.append_event("integration-candidate", job=current.job_id, evidence=entry["id"],
                                    head_sha=candidate.head, tree_sha=candidate.tree, result=result)
            report.changed = True
        return current

    def _candidate_entry(self, job: IntegrationJob, approval: dict[str, Any], candidate: Candidate,  # pylint: disable=too-many-arguments
                         classified: ClassifiedPaths | None, result: str, reasons: list[str]) -> dict[str, Any]:
        entries = classified.entries if classified is not None else []
        inspection = latest_inspection(job)
        assert inspection is not None
        entry = {"kind": "candidate", "id": "", "at": utc_now(), "approval": approval["id"],
                 "inspection": inspection["id"], "source_sha": job.source["sha"], "base_sha": job.base["expected_sha"],
                 "issue": {"number": job.issue, "state": "open"}, "result": result, "head_sha": candidate.head,
                 "tree_sha": candidate.tree, "conflicts": [clean_text(p, 1000) for p in candidate.conflicts][:200],
                 "paths": {"count": len(entries), "sha256": paths_digest(entries) if entries else "",
                           "required": sum(1 for e in entries if e["authorization"] == "required"),
                           "authorized": sum(1 for e in entries if e["authorization"] == "authorized"),
                           "refused": sum(1 for e in entries if e["authorization"] == "refused")},
                 "roles": sorted({e["role"] for e in entries if e["role"]}), "git": candidate.git,
                 "reasons": [clean_text(r) for r in reasons[:MAX_LISTED]]}
        entry["sha256"] = evidence_digest(entry)
        return entry

    def _confirmed(self, job_id: str, approval: dict[str, Any], report: ValidateReport) -> str | None:
        """Before an INSPECTING decision (module docstring, step 6): the effective approval re-verified (signature
        included: no subprocess runs under the coordinator lock) and the checkout's configuration re-scanned. The
        allowed-signers digest the signature was verified against, or None (report filled: nothing is recorded)."""
        current, problems = effective_approval_problems(self.jobs.load(job_id), config=self.config,
                                                        verifier=self.verifier, now=self.clock())
        if current is None or current["id"] != approval["id"]:
            self._meanwhile(report, self.jobs.load(job_id))
            report.messages.append("a newer approval was recorded during the validation; run inspect and validate "
                                   "again")
            return None
        if problems:
            self._refused(self.jobs.load(job_id), "no-approval", problems, report, True, "nothing was recorded")
            return None
        self.git.recheck()
        return self.verifier.last_signers_digest

    @contextlib.contextmanager
    def _inspecting(self, job_id: str, approval: dict[str, Any], verified_signers: str) -> Iterator[IntegrationJob]:
        """The coordinator transaction of an INSPECTING decision, entered only while the job is still INSPECTING with
        a current passed inspection under the same effective approval, that approval's time window holds at a FRESH
        clock reading and the allowed-signers file is unchanged; else _Undecided (nothing is recorded)."""
        with self.store.transaction():
            job = self.jobs.load(job_id)
            effective = effective_approval(job)
            if job.state is not JobState.INSPECTING or not current_inspection_passed(job) or effective is None \
                    or effective["id"] != approval["id"]:
                raise _Undecided("meanwhile", job)
            if time_problems(effective, self.clock()):
                raise _Undecided("expired", job)
            if self.signers_changed(verified_signers):
                raise _Undecided("signers", job)
            yield job

    def _undecided(self, undecided: "_Undecided", approval: dict[str, Any], report: ValidateReport) -> None:
        job = undecided.job
        if undecided.reason == "expired":
            self._refused(job, "no-approval", [f"approval {approval['id']}: it expired before the decision"], report,
                          True, "nothing was recorded")
            return
        self._meanwhile(report, job)
        if undecided.reason == "signers":
            report.messages.append("the allowed-signers file changed after the approval's signature was verified (a "
                                   "key may have been revoked); nothing was recorded — run validate again")
        elif job.state is JobState.INSPECTING:
            report.messages.append("the job's approval or inspection changed during the validation; run inspect and "
                                   "validate again")

    def signers_changed(self, verified: str) -> bool:
        """(Inside the transaction) the allowed-signers file no longer holds what the signature was verified against."""
        try:
            return not verified or self.verifier.signers_digest() != verified
        except ApprovalRefused:
            return True

    # -- terminal decisions --------------------------------------------------------------------------

    def _final(self, job: IntegrationJob, approval: dict[str, Any], target: JobState, reasons: list[str],  # pylint: disable=too-many-arguments
               observed: Mapping[str, Any], report: ValidateReport, actor: str,
               area: CandidateArea) -> ValidateReport:
        """STALE, REFUSED or FAILED from the job's current state, with `attention`. In INSPECTING it is guarded like
        every other INSPECTING decision."""
        reasons = [clean_text(r) for r in reasons] or [target.value]
        attention = {"reason": reasons[0], "reasons": reasons[:MAX_LISTED], "observed": dict(observed),
                     "since": utc_now()}
        if job.state is JobState.INSPECTING:
            verified = self._confirmed(job.job_id, approval, report)
            if verified is None:
                return report
            try:
                with self._inspecting(job.job_id, approval, verified):
                    job, report.changed = self.jobs.transition(job.job_id, target, "; ".join(reasons), actor=actor,
                                                               expect=(JobState.INSPECTING,), attention=attention)
            except _Undecided as undecided:
                self._undecided(undecided, approval, report)
                return report
        else:
            job, report.changed = self.jobs.transition(job.job_id, target, "; ".join(reasons), actor=actor,
                                                       expect=(job.state,), attention=attention)
        if job.state is not target:
            return self._meanwhile(report, job)
        area.discard_worktree()
        report.outcome = {JobState.STALE: "stale", JobState.REFUSED: "refused", JobState.FAILED: "failed"}[target]
        report.reasons, report.job = reasons, job_summary(job)
        return report

    # -- the gates -----------------------------------------------------------------------------------

    def _gates(self, job: IntegrationJob, plan: list[PlannedGate], report: ValidateReport, actor: str,  # pylint: disable=too-many-arguments
               area: CandidateArea, approval: dict[str, Any]) -> ValidateReport:
        candidate = job.candidate
        assert candidate is not None
        context = GateContext(repo_root=area.worktree, head_sha=candidate["head_sha"],
                              base_sha=job.base["expected_sha"], feature_id=job.job_id)
        toolchain: Toolchain | None = None
        runner: Callable[[str, GateContext], GateResult] | None = None
        unavailable = ""
        if self._attempt(job.job_id, candidate, plan, report, approval) is None:
            return report
        self._fault("after-attempt")
        for planned in plan:
            current = self.jobs.load(job.job_id)
            if current.state is not JobState.VALIDATING:
                return self._meanwhile(report, current)
            newest = standing(current_gate_evidence(current), recorded_failures(current), planned)
            if newest is not None:
                report.gates.append(self._gate_view(newest, reused=True))
                continue
            if planned.kind in ("command", "lint") and runner is None and not unavailable:
                try:
                    materialize(CandidateGit(area.home, timeout=self.git.timeout), area, tree=candidate["tree_sha"],
                                head=candidate["head_sha"])
                    objects = gate_object_directories(area.repo / "objects", self._objects_dir())
                    toolchain = prepare_toolchain(plan, workspace=area.worktree, checkout=self.git.root,
                                                  object_dirs=objects)
                    runner = self.gate_runner(area.logs / "gates", toolchain)
                except (CoordinatorError, OSError) as exc:
                    unavailable = f"the candidate workspace could not be prepared: {clean_text(exc)}"
            outcome = run_gate(planned, runner=runner or _no_runner, context=context, config=self.config,
                               toolchain=toolchain, unavailable=unavailable, paths=candidate["paths"])
            self._fault(f"after-gate:{planned.name}")
            entry = gate_entry(candidate, planned, outcome)
            with self.store.transaction():
                current = self.jobs.load(job.job_id)
                if current.state is not JobState.VALIDATING:
                    return self._meanwhile(report, current)
                newest = current_gate_evidence(current).get(planned.name)
                if same_blocked(newest, entry):
                    assert newest is not None   # the recorded entry stands; the report shows this run's details
                    report.gates.append({**self._gate_view(newest, reused=False), "summary": entry["summary"]})
                    continue
                entry["id"] = f"gat-{len(current.evidence) + 1:04d}"
                current.evidence.append(entry)
                self.jobs.save(current)
                self.store.append_event("integration-gate", job=job.job_id, evidence=entry["id"], gate=planned.name,
                                        result=entry["result"], status=entry["status"])
            report.changed = True
            report.gates.append(self._gate_view(entry, reused=False))
        self._fault("before-summary")
        return self._summarize(job.job_id, plan, report, actor, approval, area)

    def _attempt(self, job_id: str, candidate: Mapping[str, Any], plan: list[PlannedGate],  # pylint: disable=too-many-arguments
                 report: ValidateReport, approval: dict[str, Any]) -> dict[str, Any] | None:
        """The validation attempt this run belongs to (module docstring, step 7), recorded under the coordinator lock
        BEFORE any gate runs. Reused without a new entry when the newest attempt has the same plan and is open
        (interrupted: resumed), was decided blocked (repeated), or was decided and nothing needs to run (a repeated
        validation). Otherwise a new attempt is appended: the first run, a changed plan (gates, reasons, definitions or
        timeouts) or a gate to re-run after a passed decision — which from now on is no longer `validated`. None: the
        job left VALIDATING (report filled)."""
        view = plan_view(plan)
        with self.store.transaction():
            current = self.jobs.load(job_id)
            if current.state is not JobState.VALIDATING:
                self._meanwhile(report, current)
                return None
            entries, failures = current_gate_evidence(current), recorded_failures(current)
            run = [planned.name for planned in plan if standing(entries, failures, planned) is None]
            newest, latest = latest_evidence(current, "attempt"), latest_evidence(current, "validation")
            decided = newest is not None and latest is not None and latest["attempt"] == newest["id"]
            if newest is not None and newest["plan"]["sha256"] == view["sha256"] \
                    and (not decided or latest["result"] != "passed" or not run):
                if not decided:
                    report.messages.append(f"resuming validation attempt {newest['id']} (started {newest['at']}), "
                                           "interrupted before its decision")
                return newest
            entry = {"kind": "attempt", "id": f"att-{len(current.evidence) + 1:04d}", "at": utc_now(),
                     "candidate": candidate["evidence"], "head_sha": candidate["head_sha"],
                     "tree_sha": candidate["tree_sha"], "approval": approval["id"], "plan": view, "run": run}
            entry["sha256"] = evidence_digest(entry)
            current.evidence.append(entry)
            self.jobs.save(current)
            self.store.append_event("integration-validation-attempt", job=job_id, evidence=entry["id"],
                                    plan=view["sha256"], gates=len(run))
        report.changed = True
        return entry

    def _undecidable(self, job: IntegrationJob, approval: dict[str, Any], verified_signers: str,
                     report: ValidateReport) -> ValidateReport | None:
        """(Inside the summary transaction) the report when the validation decision must not be recorded: the job left
        VALIDATING, the approval is no longer the same or its window has closed (fresh clock reading), or the
        allowed-signers file changed since the signature check. None: it may be recorded."""
        if job.state is not JobState.VALIDATING:
            return self._meanwhile(report, job)
        effective = effective_approval(job)
        late = time_problems(effective, self.clock()) if effective is not None else ["no approval"]
        if late or effective is None or effective["id"] != approval["id"]:
            return self._refused(job, "no-approval", [f"approval {approval['id']}: {p} (at the decision)"
                                                      for p in late or ["changed"]], report, True,
                                 "the gate results are recorded; the validation decision is not")
        if self.signers_changed(verified_signers):
            report = self._meanwhile(report, job)
            report.messages.append("the allowed-signers file changed after the approval's signature was verified; "
                                   "nothing more was recorded — run validate again")
            return report
        return None

    @staticmethod
    def _gate_view(entry: Mapping[str, Any], *, reused: bool) -> dict[str, Any]:
        return {"gate": entry["gate"], "required_by": entry["required_by"], "result": entry["result"],
                "status": entry["status"], "evidence": entry["id"], "reused": reused, "exit_code": entry["exit_code"],
                "duration_seconds": entry["duration_seconds"], "summary": entry["summary"], "log": entry["log"]}

    def _summarize(self, job_id: str, plan: list[PlannedGate], report: ValidateReport, actor: str,  # pylint: disable=too-many-arguments
                   approval: dict[str, Any], area: CandidateArea) -> ValidateReport:
        """The validation decision (module docstring, step 8), recorded like every other decision only for a candidate
        that is not stale (the pinned values verified again after the gates: Codex review of M3, F3), under the same,
        still valid approval (re-verified before the lock; inside it a fresh clock reading and the allowed-signers file
        unchanged) and a checkout configuration that still names no program (independent review, finding 3)."""
        job = self.jobs.load(job_id)
        if job.state is not JobState.VALIDATING:
            return self._meanwhile(report, job)
        pinned = self.inspector.check_pinned(job, self._pinned_pr(job))
        report.checks += [{**check, "check": f"{check['check']} (after the gates)"} for check in pinned.checks]
        if pinned.stale:
            return self._final(job, approval, JobState.STALE, [f"after the gates: {reason}" for reason in pinned.stale],
                               pinned.observed, report, actor, area)
        if pinned.deferred:
            return self._deferred(job, f"after the gates: {pinned.deferred} (the gate results are recorded, the "
                                       "validation decision is not)", report)
        current_approval, problems = effective_approval_problems(self.jobs.load(job_id), config=self.config,
                                                                 verifier=self.verifier, now=self.clock())
        if current_approval is None or current_approval["id"] != approval["id"] or problems:
            return self._refused(self.jobs.load(job_id), "no-approval", problems or ["the effective approval changed"],
                                 report, True, "the gate results are recorded; the validation decision is not")
        verified_signers = self.verifier.last_signers_digest
        self.git.recheck()
        with self.store.transaction():
            job = self.jobs.load(job_id)
            refusal = self._undecidable(job, approval, verified_signers, report)
            if refusal is not None:
                return refusal
            attempt = latest_evidence(job, "attempt")
            if job.candidate is None or attempt is None:
                raise IntegrationError(f"{job_id} is VALIDATING without a candidate or a validation attempt")
            gates, decisive = self._decided_gates(job, plan)
            results = {item["result"] for item in gates}
            result = ("failed" if any(e["result"] == "FAIL" for e in decisive)
                      else "blocked" if "BLOCKED" in results or not gates else "passed")
            reasons = [f"{e['gate']}: {e['result']} — {e['summary']}"
                       + ("" if e["gate"] in {p.name for p in plan} else f" ({e['id']}: recorded for this candidate; "
                          "no longer in the gate plan — a recorded failure always stands)") for e in decisive]
            summary = {"kind": "validation", "id": f"val-{len(job.evidence) + 1:04d}", "at": utc_now(),
                       "candidate": job.candidate["evidence"], "head_sha": job.candidate["head_sha"],
                       "tree_sha": job.candidate["tree_sha"], "attempt": attempt["id"], "result": result,
                       "plan": plan_view(plan), "gates": gates,
                       "reasons": [clean_text(r) for r in reasons[:MAX_LISTED]]}
            summary["sha256"] = evidence_digest(summary)
            observed = {"gates": {e["gate"]: {"result": e["result"], "evidence": e["id"], "log": e["log"]}
                                  for e in decisive}}
            if result == "failed":
                attention = {"reason": summary["reasons"][0], "reasons": summary["reasons"], "observed": observed,
                             "since": utc_now()}
                job, changed = self.jobs.transition(job_id, JobState.NEEDS_REPAIR, "; ".join(summary["reasons"]),
                                                    actor=actor, expect=(JobState.VALIDATING,), evidence=[summary],
                                                    attention=attention)
                report.changed = report.changed or changed
            else:
                attention = None if result == "passed" else {
                    "reason": summary["reasons"][0], "reasons": summary["reasons"], "observed": observed,
                    "since": (job.attention or {}).get("since") or utc_now()}
                latest = latest_evidence(job, "validation")
                unchanged = (latest is not None and latest["sha256"] == summary["sha256"]
                             and (job.attention or {}).get("reasons") == (attention or {}).get("reasons"))
                if not unchanged:
                    job.evidence.append(summary)
                    job.attention = attention
                    self.jobs.save(job)
                    self.store.append_event("integration-validation", job=job_id, evidence=summary["id"],
                                            result=result, gates=len(gates))
                    report.changed = True
        report.outcome = {"failed": "failed", "blocked": "blocked", "passed": "validated"}[result]
        report.reasons = summary["reasons"]
        report.job = job_summary(job)
        if result == "passed":
            report.messages.append(f"{job_id} is validated: candidate {job.candidate['head_sha']} passed every "
                                   "required gate; it stays VALIDATING for the independent review (M4)")
        elif result == "blocked":
            report.messages.append("validation is blocked: fix the cause and run validate again (passed gates are "
                                   "reused)")
        else:
            report.messages.append(f"{job_id} needs repair (NEEDS_REPAIR): the source is never patched; a repair "
                                   "branch and a new job are needed (repair routing is M6)")
        return report

    @staticmethod
    def _decided_gates(job: IntegrationJob, plan: list[PlannedGate]) -> tuple[list[dict[str, Any]],
                                                                             list[dict[str, Any]]]:
        """(gates, decisive): one {gate, evidence, result} per planned gate, decided by a FAIL recorded for the
        candidate under any definition, else by the newest entry of exactly the planned definition; and the entries
        that make the decision fail or block — FAILs first, a FAIL of a gate no longer planned included (F1)."""
        current, failures = current_gate_evidence(job), recorded_failures(job)
        gates, chosen = [], []
        for planned in plan:
            entry = failures.get(planned.name) or current.get(planned.name)
            if entry is None or (entry["result"] != "FAIL" and entry["fingerprint"] != planned.fingerprint):
                raise IntegrationError(f"{job.job_id}: gate {planned.name} has no evidence for its current definition; "
                                       "nothing was decided")
            gates.append({"gate": planned.name, "evidence": entry["id"], "result": entry["result"]})
            chosen.append(entry)
        planned_names = {planned.name for planned in plan}
        unplanned = [entry for name, entry in sorted(failures.items()) if name not in planned_names]
        decisive = ([e for e in chosen if e["result"] == "FAIL"] + unplanned
                    + [e for e in chosen if e["result"] == "BLOCKED"])
        return gates, decisive


def validated_for(job: IntegrationJob, config: ProjectConfig) -> bool:
    """integration.validated for the gate plan the CURRENT configuration derives for the job's recorded candidate paths:
    a decision for another plan (a gate added, removed or redefined — another command, timeout or reason) is not
    current; `validate --apply` then records a new attempt and decision (Codex review of M3, F1). M4's starting
    point, together with re-verifying the pinned values and the candidate."""
    if job.candidate is None:
        return False
    return validated(job, plan_view(gate_plan(job.candidate["paths"], config))["sha256"])


class _Undecided(Exception):
    def __init__(self, reason: str, job: IntegrationJob) -> None:
        super().__init__(reason)
        self.reason, self.job = reason, job


def _no_runner(name: str, _context: GateContext) -> GateResult:
    raise IntegrationError(f"no gate runner for {name}")
