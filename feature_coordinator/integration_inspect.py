"""W4.7 (M2): the read-only inspection of an integration job (design §4 INSPECTING, §4.1, §6.2, §8.1 `inspect`).

    python -m feature_coordinator integration inspect --job J [--actor NAME] [--apply] [--json]

READ-ONLY towards Git and GitHub (ReadOnlyGit in its private repository, integration_isolation.py; GET requests and
one GraphQL query type). It never builds a candidate, creates a worktree, fetches, pushes, creates/edits/closes a PR
or merges. Only the coordinator state directory is written (with --apply).

Order:
  0. the job exists and is in AWAITING_APPROVAL or INSPECTING (a terminal job is reported as it is); its EFFECTIVE
     inspect approval (the newest one, integration_approval.effective_approval_problems) is valid NOW — signature
     re-verified against the allowed-signers file, operator still listed, not expired, bound to the pinned SHAs — else
     nothing happens (`no-approval`, also for a dry run). `github.integration.trusted_logins` must be set; the
     checkout's `origin` and history must be fit, and its configuration must name no program (strict scan) — else an
     error and nothing is recorded;
  1. with --apply: one inspection per job at a time (`locks/integration-<job>.lock`, non-blocking: a concurrent one is
     refused); AWAITING_APPROVAL → INSPECTING is recorded first (write-ahead, naming the approval);
  2. the checks (no lock held):
       a. pinned values first (they decide STALE): origin's source and base branches must still be at the pinned
          SHAs, and a PR verified at intake must still have the pinned head repository, branch, SHA and base. A
          change reported by the read of that PR (`/pulls/{n}`) is only a claim: it makes the job STALE only once the
          complete PR lookup of 2d (listing, head query, commit query) agrees with that read, which is run at once
          for it; any disagreement, incomplete listing or failed lookup defers (no terminal state from one answer);
       b. policy again (allowlisted source repository, configured base branch), the commits present locally;
       c. provenance: the range re-verified from hash-verified commit objects (M1), every author in known_authors,
          then GitHub's attribution and signature verification per commit (integration_github.assess_provenance);
       d. the complete PR lookup (all states, branch ∪ SHA, strict pagination, strictly parsed PRs) cross-checked
          against GitHub's head and commit lookups and the intake PR read in 2a — two answers that disagree about
          one PR defer — and the reuse rules;
       e. the full changed-path list from hash-verified trees — every commit of the range plus the net diff from each
          proven merge base — classified through PathPolicy (integration_paths), with the record's per-path
          authorizations;
  3. the decision, recorded only if the job is still INSPECTING under the same effective approval and that approval
     is still valid (and the checkout's configuration still names no program). The signature is verified again just
     before the coordinator transaction (no subprocess runs under the lock); INSIDE the transaction — the decision
     point — the job is re-read, the effective approval must still be the same entry, its time window is checked
     again with a FRESH clock reading (an approval that expired while the checks or the final signature check ran
     authorizes nothing: `no-approval`, nothing recorded), and the allowed-signers file must still hold exactly the
     content the signature was verified against (a key revoked meanwhile: `changed-meanwhile`, nothing recorded).
     The evidence entry's `at` is that decision time:
       STALE      a pinned value changed (2a; a PR change only when every GitHub answer about the PR agrees) —
                  terminal, `attention` with the observed SHAs;
       REFUSED    an input never satisfied the rules: a protected path, a gitlink, a symlink leaving the repository or
                  pointing at a protected path, a malformed tree, too many paths, an unknown or untrusted author, a
                  PR that cannot be reused (closed, merged, other base/repository, moved branch match, several
                  candidates), a source no longer allowlisted — terminal, `attention`;
       deferred   a read could not complete (remote or GitHub unavailable, an incomplete PR listing, the intake PR not
                  found (404: an access problem), an object missing, attribution unavailable, the checkout's object
                  store contradicting what intake proved) — the job STAYS in INSPECTING with `deferred`; repeat;
       needs-authorization  approval-required, unowned, symlink or git-control paths without a per-path
                  authorization, or commits whose provenance is not verified while the record does not accept that —
                  the job stays in INSPECTING with `attention` listing what to authorize; record a new approval and
                  run inspect again;
       passed     the job stays in INSPECTING with its classified `paths` and a `passed` inspection evidence entry
                  bound to the pinned SHAs and the approval (M3 builds the candidate from there).
     STALE wins over REFUSED for the same observation (M1 decision 1), REFUSED over deferred, deferred over
     needs-authorization. Every decided inspection appends an evidence entry (stale, refused, needs-authorization,
     passed); repeating an inspection that finds exactly the same changes nothing.

A crash before the decision leaves the job in INSPECTING; the same command resumes it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Mapping

from .config import ProjectConfig
from .errors import CoordinatorError, GitHubError, GitHubNotFoundError, LockBusyError
from .integration import (
    IntegrationError,
    IntegrationJob,
    IntegrationJobs,
    JobState,
    clean_text,
    effective_approval,
    inspection_digest,
    job_summary,
    latest_inspection,
    paths_digest,
)
from .integration_approval import ApprovalRefused, SshSignatureVerifier, effective_approval_problems, now_utc, \
    time_problems
from .integration_github import (
    Discovery,
    PrDecision,
    ProvenanceAssessment,
    assess_provenance,
    decide_reuse,
    discover_pull_requests,
    fetch_attribution,
    fetch_pull,
    recorded_pr_changes,
)
from .integration_intake import MAX_LISTED, HistoryRefused, ReadOnlyGit, VerifiedRange
from .integration_paths import ClassifiedPaths, changed_paths, classify_paths
from .issue_sync import SimulatedCrash
from .issues import PullRequest
from .locks import FileLock
from .model import utc_now
from .paths import PathPolicy

INSPECTABLE_STATES = frozenset({JobState.AWAITING_APPROVAL, JobState.INSPECTING})


@dataclass
class InspectionResult:  # pylint: disable=too-many-instance-attributes
    """What the read-only checks found."""

    stale: list[str] = field(default_factory=list)
    refusals: list[str] = field(default_factory=list)
    deferred: str = ""
    observed: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, str]] = field(default_factory=list)
    paths: ClassifiedPaths | None = None
    pr: PrDecision | None = None
    pr_stale: bool = False
    recorded_pr: PullRequest | None = None    # the intake PR as read on its own (cross-checked with the listing)
    pr_changes: list[str] = field(default_factory=list)  # what that one read reports as changed (not yet confirmed)
    provenance: ProvenanceAssessment | None = None

    def note(self, name: str, status: str, detail: str = "") -> None:
        """Record one check: pass | fail | stale | deferred | skipped | attention."""
        self.checks.append({"check": name, "status": status, "detail": clean_text(detail, 300)})

    def mark_stale(self, name: str, reason: str) -> None:
        """A pinned value changed: the job becomes STALE."""
        self.stale.append(clean_text(reason))
        self.note(name, "stale", reason)

    def refuse(self, name: str, reason: str) -> None:
        """A definitive failure: the job becomes REFUSED."""
        self.refusals.append(clean_text(reason))
        self.note(name, "fail", reason)

    def defer(self, name: str, reason: str) -> None:
        """A read that could not complete: the job stays in INSPECTING."""
        self.deferred = self.deferred or clean_text(reason)
        self.note(name, "deferred", reason)

    def outcome(self, approval: dict[str, Any]) -> str:
        """stale | refused | deferred | needs-authorization | passed (precedence in the module docstring)."""
        if self.stale:
            return "stale"
        if self.refusals:
            return "refused"
        if self.deferred:
            return "deferred"
        return "needs-authorization" if self.authorization_needed(approval) else "passed"

    def authorization_needed(self, approval: dict[str, Any]) -> list[str]:
        """What the operator still has to authorize explicitly (empty: nothing)."""
        needed = []
        if self.paths is not None and self.paths.required:
            shown = ", ".join(self.paths.required[:MAX_LISTED])
            needed.append(f"{len(self.paths.required)} path(s) need an explicit per-path authorization in the signed "
                          f"record (approval-required, unowned, symlink or git-control): {shown}")
        if self.provenance is not None and self.provenance.needs_acceptance \
                and not approval["accept_unverified_provenance"]:
            needed.append(f"{len(self.provenance.levels) - self.provenance.count('verified')} commit(s) have "
                          f"unverified or unverifiable provenance ({self.provenance.source}); the record must set "
                          "accept_unverified_provenance to accept them explicitly")
        return needed


@dataclass
class InspectReport:  # pylint: disable=too-many-instance-attributes
    """What one inspection did (apply) or would do (dry run)."""

    applied: bool
    outcome: str = ""
    changed: bool = False
    approval: str | None = None
    job: dict[str, Any] | None = None
    checks: list[dict[str, str]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    deferred: str = ""
    paths: list[dict[str, Any]] = field(default_factory=list)
    pr: dict[str, Any] | None = None
    provenance: dict[str, Any] | None = None
    messages: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when the inspection passed (or would pass)."""
        return self.outcome in ("passed", "would-pass")

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "ok": self.ok}


class IntegrationInspector:  # pylint: disable=too-many-instance-attributes
    """Inspection of integration jobs for one checkout and state directory (see the module docstring)."""

    def __init__(self, store: Any, config: ProjectConfig, git: ReadOnlyGit, *, verifier: SshSignatureVerifier,
                 tracker: Callable[[], Any] | None = None, faults: Iterable[str] = (),
                 clock: Callable[[], datetime] = now_utc) -> None:
        self.store, self.config, self.git = store, config, git
        self.jobs = IntegrationJobs(store)
        self.verifier = verifier
        self.policy = PathPolicy(git.root, config)
        self._tracker_factory = tracker
        self._tracker: Any = None
        self.faults = set(faults)
        self.clock = clock

    def _fault(self, name: str) -> None:
        if name in self.faults:
            self.faults.discard(name)
            raise SimulatedCrash(name)

    def tracker(self) -> Any:
        """The GitHub client (None: none configured)."""
        if self._tracker is None and self._tracker_factory is not None:
            self._tracker = self._tracker_factory()
        return self._tracker

    # -- the run -----------------------------------------------------------------------------------

    def run(self, job_id: str, *, apply: bool, actor: str = "operator") -> InspectReport:
        """Inspect *job_id*; with *apply*, record the decision. IntegrationError (nothing recorded) for a precondition
        that is not the job's fault."""
        with self.git.session():
            return self._run(job_id, apply=apply, actor=actor)

    def _run(self, job_id: str, *, apply: bool, actor: str) -> InspectReport:
        report = InspectReport(applied=apply)
        job = self.jobs.load(job_id)
        report.job = job_summary(job)
        if job.terminal:
            report.outcome = "terminal"
            report.messages.append(f"{job_id} is {job.state.value} (terminal) and is never inspected again")
            return report
        if job.state not in INSPECTABLE_STATES:
            raise IntegrationError(f"{job_id} is {job.state.value}: inspection applies to AWAITING_APPROVAL or "
                                   "INSPECTING jobs only")
        if job.state is JobState.INSPECTING and effective_approval(job) is None:
            raise IntegrationError(f"conflicting state: {job_id} is INSPECTING but carries no inspect approval; it is "
                                   "never inspected in that state — abort it (`integration abort`)")
        if apply and not self.config.github.integration.enabled:
            raise IntegrationError("github.integration.enabled is false: inspections are recorded only once an "
                                   "operator enables integration jobs")
        approval, problems = effective_approval_problems(job, config=self.config, verifier=self.verifier,
                                                         now=self.clock())
        if problems:
            return self._no_approval(job, problems, report, apply)
        assert approval is not None
        report.approval = approval["id"]
        self._preflight()
        if not apply:
            result = self.check(job, approval)
            return self._report(report, job, approval, result, "would-")
        lock = FileLock(self.store.locks.dir / f"integration-{job_id}.lock")
        try:
            lock.acquire(timeout=0)
        except LockBusyError as exc:
            raise IntegrationError(f"another inspection of {job_id} is running; nothing was done") from exc
        try:
            if job.state is JobState.AWAITING_APPROVAL:
                job, _changed = self.jobs.transition(
                    job_id, JobState.INSPECTING, f"inspection started under approval {approval['id']} "
                    f"({approval['operator']})", actor=actor, expect=(JobState.AWAITING_APPROVAL,))
                if job.state is not JobState.INSPECTING:
                    return self._meanwhile(report, job)
            self._fault("after-enter")
            result = self.check(job, approval)
            self._fault("after-checks")
            self.git.recheck()
            return self._decide(job_id, approval, result, actor, report)
        finally:
            lock.release()

    def _preflight(self) -> None:
        if not self.config.github.integration.trusted_logins:
            raise IntegrationError("github.integration.trusted_logins is empty: GitHub attribution cannot be judged, "
                                   "so no inspection runs; nothing was recorded")
        problem = self.git.origin_problem()
        if problem:
            raise IntegrationError(f"cannot resolve refs for an inspection: {problem}; nothing was recorded")
        problem = self.git.repository_problem()
        if problem:
            raise IntegrationError(f"this checkout cannot prove the provenance of an integration: {problem}; nothing "
                                   "was recorded")

    def _no_approval(self, job: IntegrationJob, problems: list[str], report: InspectReport,
                     apply: bool) -> InspectReport:
        report.outcome, report.reasons = "no-approval", problems
        report.messages.append(f"{job.job_id} is not inspected: it has no valid inspect approval (nothing changed)")
        if apply:
            with self.store.transaction():
                self.store.append_event("integration-inspection-refused", job=job.job_id, reason=problems[0])
        return report

    @staticmethod
    def _meanwhile(report: InspectReport, job: IntegrationJob) -> InspectReport:
        report.outcome, report.job = "changed-meanwhile", job_summary(job)
        report.messages.append(f"{job.job_id} changed while the inspection ran (now {job.state.value}); nothing was "
                               "changed by this run")
        return report

    # -- the checks (read-only) --------------------------------------------------------------------

    def check(self, job: IntegrationJob, approval: dict[str, Any]) -> InspectionResult:
        """Every M2 inspection check. Reads only; holds no lock."""
        source, base = job.source, job.base
        result = InspectionResult(observed={"source_sha": source["sha"], "base_sha": base["expected_sha"]})
        self._pinned(job, result)
        if result.pr_changes and not result.deferred:
            self._confirm_pr_changes(job, result)
        if result.stale or result.deferred:
            return result
        self._policy(job, result)
        self._objects(job, result)
        if result.deferred or result.refusals:
            return result
        history = self._history(job, result)
        if history is not None:
            self._provenance(job, history, result)
        self._pull_requests(job, result)
        if history is not None:
            self._paths(job, approval, history, result)
        return result

    def check_pinned(self, job: IntegrationJob, recorded: Mapping[str, Any] | None = None) -> InspectionResult:
        """M3: only the pinned-value checks of step 2a — origin's source and base branches still at the pinned SHAs,
        and the pinned PR (*recorded*: {number, head_repo, head_ref, head_sha, base_repo, base_ref}; default: the PR
        verified at intake) unchanged, where a change reported by its single read counts only once the complete PR
        lookup agrees (Codex review F2). Reads only; `stale` / `deferred` of the result decide."""
        result = InspectionResult(observed={"source_sha": job.source["sha"], "base_sha": job.base["expected_sha"]})
        self._pinned(job, result, recorded)
        if result.pr_changes and not result.deferred:
            self._confirm_pr_changes(job, result)
        return result

    def _pinned(self, job: IntegrationJob, result: InspectionResult,
                recorded: Mapping[str, Any] | None = None) -> None:
        source, base = job.source, job.base
        try:
            now = {"source_ref_sha": self.git.remote_head(source["ref"]),
                   "base_ref_sha": self.git.remote_head(base["ref"])}
        except (CoordinatorError, OSError) as exc:
            result.defer("refs", f"origin's refs could not be read: {exc}")
            return
        result.observed.update(now)
        for label, ref, pinned, key in (("source", source["ref"], source["sha"], "source_ref_sha"),
                                        ("base", base["ref"], base["expected_sha"], "base_ref_sha")):
            if now[key] is None:
                result.mark_stale(f"{label}-ref", f"{label} branch {ref!r} no longer exists on origin (pinned at "
                                                  f"{pinned})")
            elif now[key] != pinned:
                result.mark_stale(f"{label}-ref", f"{label} branch {ref!r} moved from the pinned {pinned} to "
                                                  f"{now[key]}")
            else:
                result.note(f"{label}-ref", "pass", f"origin {ref} is still at {pinned[:12]}")
        if recorded is None:
            recorded = job.pr if job.pr and len(job.pr) > 1 else None
        if recorded is None:
            return
        tracker = self.tracker()
        if tracker is None:
            result.defer("recorded-pr", f"PR #{recorded['number']} cannot be read: no GitHub client")
            return
        try:
            pr = fetch_pull(tracker, self.config.github.repository, recorded["number"])
        except GitHubNotFoundError:
            # PRs are not deleted on GitHub: a 404 is far more likely a token or permission problem than a moved
            # PR, so it defers (repeat once the GitHub access is fixed) instead of making the job STALE for good.
            result.defer("recorded-pr", f"the PR #{recorded['number']} recorded at intake cannot be found (HTTP 404): "
                                        "check the GitHub login's access to the repository")
            return
        except (GitHubError, OSError) as exc:
            result.defer("recorded-pr", f"PR #{recorded['number']} could not be read: {exc}")
            return
        result.recorded_pr = pr
        result.observed["recorded_pr"] = {"number": pr.number, "head_sha": pr.head_sha, "head": pr.head,
                                          "base": pr.base, "state": "merged" if pr.merged else pr.state}
        # One GitHub answer never ends a job for good: a change it reports is only a claim until the complete PR
        # lookup agrees with it (_confirm_pr_changes). An unchanged read is cross-checked by _pull_requests.
        result.pr_changes = recorded_pr_changes(recorded, pr)
        if not result.pr_changes:
            result.note("recorded-pr", "pass", f"PR #{pr.number} still has the pinned head and base")

    def _confirm_pr_changes(self, job: IntegrationJob, result: InspectionResult) -> None:
        """The read of the intake PR reports that a pinned value changed. It makes the job STALE only when the complete
        PR lookup (listing, head query, commit query) agrees with that read — discover_pull_requests cross-checks every
        answer about the PR; a disagreement, an incomplete listing or a failed lookup defers and decides nothing."""
        number = result.recorded_pr.number if result.recorded_pr is not None else job.pr["number"]
        discovery = self._discover(job, result, "recorded-pr",
                                   f"the change that the read of PR #{number} reports cannot be confirmed")
        if discovery is None:
            return
        result.pr_stale = True
        for change in result.pr_changes:
            result.mark_stale("recorded-pr", change)
        result.note("pr-lookup", "pass", f"{discovery.listed} PR(s) listed ({discovery.method}): every answer about "
                                         f"PR #{number} agrees with its read, so the change is confirmed")

    def _policy(self, job: IntegrationJob, result: InspectionResult) -> None:
        allowed = self.config.github.repository
        if job.source["repo"].lower() != allowed.lower():
            result.refuse("allowlist", f"source repository {job.source['repo']} is not permitted (allowlist: "
                                       f"{allowed} only; fork sources are not supported)")
        if job.base["ref"] != self.config.base_branch:
            result.refuse("base", f"the base must be the configured base branch {self.config.base_branch!r}")
        if not result.refusals:
            result.note("policy", "pass", f"{job.source['repo']} into {job.base['ref']}")

    def _objects(self, job: IntegrationJob, result: InspectionResult) -> None:
        shas = (job.source["sha"], job.base["expected_sha"])
        try:
            problem = self.git.repository_problem()
            types = {sha: self.git.object_type(sha) for sha in shas}
        except (CoordinatorError, OSError) as exc:
            result.defer("objects", f"the checkout could not be read: {exc}")
            return
        if problem:
            result.defer("objects", problem)
            return
        missing = [sha[:12] for sha, kind in types.items() if kind is None]
        if missing:
            result.defer("objects", f"commit(s) {', '.join(missing)} are not present in this checkout; fetch them "
                                    f"(`git fetch origin {job.source['ref']} {job.base['ref']}`) — inspection never "
                                    "fetches")
        elif any(kind != "commit" for kind in types.values()):
            # the pinned SHAs were proven commits at intake: an unverified answer to the contrary is the checkout's
            # object store disagreeing, which defers (it must not be able to force a terminal REFUSED)
            result.defer("objects", "the checkout's object store does not hold a pinned SHA as a commit (damaged or "
                                    "tampered object store; `git fsck`)")
        else:
            result.note("objects", "pass", "both commits are present locally")

    def _history(self, job: IntegrationJob, result: InspectionResult) -> VerifiedRange | None:
        source, base = job.source["sha"], job.base["expected_sha"]
        try:
            if self.git.is_ancestor(source, base):
                # proven otherwise at intake for these very SHAs: Git's (unverified) walk disagrees → defer
                result.defer("range", f"Git now reports source_sha {source[:12]} as contained in base_sha {base[:12]}, "
                                      "which intake disproved: the checkout's object store is inconsistent")
                return None
            history = self.git.verified_range(base, source)
        except HistoryRefused as exc:
            result.refuse("history", str(exc))
            return None
        except (CoordinatorError, OSError) as exc:
            result.defer("history", f"the source history cannot be verified in this checkout: {exc}")
            return None
        known = set(self.config.github.integration.known_authors)
        unknown = sorted({commit.author.key for commit in history.commits} - known)
        result.observed["commits"] = len(history.commits)
        if unknown:
            shown = [clean_text(email, 200) for email in unknown[:MAX_LISTED]]
            result.observed["unknown_authors"] = shown
            result.refuse("authors", f"commit author(s) not in github.integration.known_authors: {', '.join(shown)}")
            return None
        result.note("history", "pass", f"{len(history.commits)} hash-verified commit(s); every author is known")
        return history

    def _provenance(self, job: IntegrationJob, history: VerifiedRange, result: InspectionResult) -> None:
        commits = history.commits
        tracker = self.tracker()
        try:
            attributions = fetch_attribution(tracker, self.config.github.repository,
                                             [c.sha for c in commits]) if tracker is not None else None
        except (GitHubError, OSError) as exc:
            result.defer("provenance", f"GitHub's attribution of the commits could not be read: {exc}")
            return
        assessment = assess_provenance(commits, attributions, self.config.github.integration.trusted_logins)
        result.provenance = assessment
        for reason in assessment.refusals:
            result.refuse("provenance", reason)
        if assessment.deferred:
            result.defer("provenance", assessment.deferred)
        if assessment.observed:
            result.observed["unverified_commits"] = assessment.observed
        if not assessment.refusals and not assessment.deferred:
            counts = {level: assessment.count(level) for level in ("verified", "unverified", "unverifiable")}
            status = "pass" if not assessment.needs_acceptance else "attention"
            result.note("provenance", status, f"{assessment.source}: {counts['verified']} verified, "
                                              f"{counts['unverified']} unverified, {counts['unverifiable']} "
                                              f"unverifiable commit(s) (job {job.job_id})")

    def _discover(self, job: IntegrationJob, result: InspectionResult, name: str, failure: str) -> Discovery | None:
        """The complete PR lookup, cross-checked with the intake PR as read on its own (None: deferred)."""
        tracker = self.tracker()
        if tracker is None:
            result.defer(name, "no GitHub client: the PR lookup cannot complete (nothing is created)")
            return None
        source = job.source
        try:
            observed = {"the read of the intake PR": [result.recorded_pr]} if result.recorded_pr else None
            return discover_pull_requests(tracker, repository=self.config.github.repository,
                                          source_repo=source["repo"], source_ref=source["ref"],
                                          source_sha=source["sha"], max_pages=self.config.github.max_pages,
                                          observed=observed)
        except (GitHubError, OSError) as exc:
            result.defer(name, f"{failure}: {exc}; nothing is created")
            return None

    def _pull_requests(self, job: IntegrationJob, result: InspectionResult) -> None:
        source = job.source
        discovery = self._discover(job, result, "pr-lookup", "the PR lookup could not complete")
        if discovery is None:
            return
        recorded = (job.pr or {}).get("number")
        decision = decide_reuse(discovery.candidates, repository=self.config.github.repository,
                                source_repo=source["repo"], source_ref=source["ref"], source_sha=source["sha"],
                                base_ref=job.base["ref"], recorded_number=recorded)
        result.pr = decision
        result.observed["pr_candidates"] = decision.observed
        if decision.result == "refused":
            for reason in decision.reasons:
                result.refuse("pr-lookup", reason)
        else:
            detail = (f"reuse PR #{decision.number}" if decision.result == "reuse"
                      else "no PR exists for this head in any state (none is created in M2)")
            result.note("pr-lookup", "pass", f"{discovery.listed} PR(s) listed ({discovery.method}): {detail}")

    def _paths(self, job: IntegrationJob, approval: dict[str, Any], history: VerifiedRange,
               result: InspectionResult) -> None:
        listed = {c.sha for c in history.commits}
        boundary = {p for c in history.commits for p in c.parents if p not in listed}
        try:
            merge_bases = self.git.merge_bases(job.base["expected_sha"], job.source["sha"])
            if not merge_bases:
                result.refuse("paths", "the source shares no history with the base (unrelated histories)")
                return
            unproven = [sha[:12] for sha in merge_bases if sha not in boundary]
            if unproven:   # every true merge base is a boundary parent of the verified range
                result.defer("paths", f"Git's merge base(s) {unproven} are not proven boundary parents of the "
                                      "verified range: the checkout's object store is inconsistent")
                return
            with self.git.reader() as reader:
                head = next(c for c in history.commits if c.sha == job.source["sha"])
                base_tree = reader.read(job.base["expected_sha"], strict=False).tree
                merge_base_trees = [reader.read(sha, strict=False).tree for sha in merge_bases]
                changes = changed_paths(reader, history.commits, source_tree=head.tree, base_tree=base_tree,
                                        merge_base_trees=merge_base_trees)
        except HistoryRefused as exc:
            result.refuse("paths", str(exc))
            return
        except (CoordinatorError, OSError) as exc:
            result.defer("paths", f"the changed paths cannot be read from verified objects: {exc}")
            return
        classified = classify_paths(changes, self.policy, self.config, approval["paths"])
        result.paths = classified
        for reason in classified.refusals:
            result.refuse("paths", reason)
        if classified.required:
            result.observed["paths_requiring_authorization"] = classified.required[:MAX_LISTED]
        if not classified.refusals:
            result.note("paths", "pass" if not classified.required else "attention",
                        f"{len(classified.entries)} changed path(s): {len(classified.authorized)} authorized, "
                        f"{len(classified.required)} need authorization")

    # -- the decision ------------------------------------------------------------------------------

    @staticmethod
    def _evidence(job: IntegrationJob, approval: dict[str, Any], result: InspectionResult, outcome: str,
                  reasons: list[str], at: datetime) -> dict[str, Any]:
        entries = result.paths.entries if result.paths is not None else []
        prov = result.provenance
        pr = result.pr
        if pr is not None:
            pr_match = {"result": pr.result, "number": pr.number, "candidates": pr.candidates}
        else:
            pr_match = {"result": "stale" if result.pr_stale else "unknown", "number": None, "candidates": []}
        entry = {"kind": "inspection", "id": f"ins-{len(job.evidence) + 1:04d}",
                 "at": at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                 "approval": approval["id"], "source_sha": job.source["sha"], "base_sha": job.base["expected_sha"],
                 "result": outcome, "pr_match": pr_match,
                 "provenance": {"source": prov.source if prov is not None else "unknown",
                                "verified": prov.count("verified") if prov else 0,
                                "unverified": prov.count("unverified") if prov else 0,
                                "unverifiable": prov.count("unverifiable") if prov else 0,
                                "accepted": bool(prov is not None and prov.needs_acceptance
                                                 and approval["accept_unverified_provenance"])},
                 "paths": {"count": len(entries), "sha256": paths_digest(entries) if entries else "",
                           "required": sum(1 for e in entries if e["authorization"] == "required"),
                           "authorized": sum(1 for e in entries if e["authorization"] == "authorized"),
                           "refused": sum(1 for e in entries if e["authorization"] == "refused")},
                 "reasons": [clean_text(r) for r in reasons[:MAX_LISTED]]}
        entry["sha256"] = inspection_digest(entry)
        return entry

    def _decide(self, job_id: str, approval: dict[str, Any], result: InspectionResult, actor: str,
                report: InspectReport) -> InspectReport:
        outcome = result.outcome(approval)
        # The approval is checked again (signature included) BEFORE the coordinator lock is taken — no subprocess
        # runs under the lock. Inside it (the decision point, see the module docstring, step 3) the job, the
        # effective approval, its time window (fresh clock) and the allowed-signers content are confirmed.
        current, problems = effective_approval_problems(self.jobs.load(job_id), config=self.config,
                                                        verifier=self.verifier, now=self.clock())
        if current is not None and current["id"] == approval["id"] and problems:
            return self._no_approval(self.jobs.load(job_id), problems, self._fill(report, result), apply=True)
        verified_signers = self.verifier.last_signers_digest
        with self.store.transaction():
            job = self.jobs.load(job_id)
            if job.state is not JobState.INSPECTING:
                return self._meanwhile(self._fill(report, result), job)
            effective = effective_approval(job)
            if current is None or effective is None or not current["id"] == effective["id"] == approval["id"]:
                report = self._meanwhile(self._fill(report, result), job)
                report.messages.append("a newer approval was recorded during the inspection; run inspect again")
                return report
            decided = self.clock()
            late = time_problems(effective, decided)
            if late:
                return self._no_approval(job, [f"approval {effective['id']}: {problem} (at the decision, "
                                               f"{decided.isoformat()})" for problem in late],
                                         self._fill(report, result), apply=True)
            if self._signers_changed(verified_signers):
                report = self._meanwhile(self._fill(report, result), job)
                report.messages.append("the allowed-signers file changed after the approval's signature was verified "
                                       "(a key may have been revoked); nothing was recorded — run inspect again")
                return report
            reasons = self._reasons(result, outcome, approval)
            if outcome in ("stale", "refused"):
                entry = self._evidence(job, approval, result, outcome, reasons, decided)
                attention = {"reason": reasons[0], "reasons": reasons[:MAX_LISTED], "observed": result.observed,
                             "since": utc_now()}
                updates: dict[str, Any] = {"attention": attention, "evidence": [entry]}
                if result.paths is not None:
                    updates["paths"] = result.paths.entries
                target = JobState.STALE if outcome == "stale" else JobState.REFUSED
                job, report.changed = self.jobs.transition(job_id, target, "; ".join(reasons), actor=actor,
                                                           expect=(JobState.INSPECTING,), **updates)
            elif outcome == "deferred":
                job.deferred = {"reason": result.deferred, "at": utc_now()}
                self.jobs.save(job)
                self.store.append_event("integration-inspection-deferred", job=job_id, reason=result.deferred)
                report.changed = True
            else:
                job, report.changed = self._record(job, approval, result, outcome, reasons, decided)
        report.outcome = outcome
        report.job = job_summary(job)
        return self._fill(report, result, reasons)

    def _signers_changed(self, verified: str) -> bool:
        """(Inside the transaction) True when the allowed-signers file no longer holds the content the approval's
        signature was just verified against, or can no longer be trusted (a plain file read, no subprocess)."""
        try:
            return not verified or self.verifier.signers_digest() != verified
        except ApprovalRefused:
            return True

    def _record(self, job: IntegrationJob, approval: dict[str, Any], result: InspectionResult, outcome: str,
                reasons: list[str], decided: datetime) -> tuple[IntegrationJob, bool]:
        """(Inside the transaction) a passed or needs-authorization inspection: the job stays in INSPECTING."""
        entry = self._evidence(job, approval, result, outcome, reasons, decided)
        entries = result.paths.entries if result.paths is not None else []
        attention = None
        if outcome == "needs-authorization":
            attention = {"reason": reasons[0], "reasons": reasons[:MAX_LISTED], "observed": result.observed,
                         "since": (job.attention or {}).get("since") or utc_now()}
        previous = latest_inspection(job)
        unchanged = (previous is not None and previous["sha256"] == entry["sha256"] and job.paths == entries
                     and job.deferred is None
                     and (job.attention or {}).get("reasons") == (attention or {}).get("reasons"))
        if unchanged:
            return job, False
        job.evidence.append(entry)
        job.paths = entries
        job.deferred = None
        job.attention = attention
        self.jobs.save(job)
        self.store.append_event("integration-inspection", job=job.job_id, evidence=entry["id"], result=outcome,
                                approval=approval["id"], paths=len(entries), pr=entry["pr_match"])
        return job, True

    @staticmethod
    def _reasons(result: InspectionResult, outcome: str, approval: dict[str, Any]) -> list[str]:
        if outcome == "stale":
            return result.stale
        if outcome == "refused":
            return result.refusals
        if outcome == "deferred":
            return [result.deferred]
        if outcome == "needs-authorization":
            return [clean_text(reason) for reason in result.authorization_needed(approval)]
        return []

    def _report(self, report: InspectReport, job: IntegrationJob, approval: dict[str, Any], result: InspectionResult,
                prefix: str) -> InspectReport:
        outcome = result.outcome(approval)
        word = {"passed": "pass", "needs-authorization": "need-authorization", "refused": "refuse", "stale": "stale",
                "deferred": "defer"}[outcome]
        report.outcome = prefix + word
        report.job = job_summary(job)
        report.messages.append("dry run — nothing was recorded (pass --apply to record the inspection)")
        return self._fill(report, result, self._reasons(result, outcome, approval))

    @staticmethod
    def _fill(report: InspectReport, result: InspectionResult, reasons: list[str] | None = None) -> InspectReport:
        report.checks = result.checks
        report.deferred = result.deferred
        if reasons is not None:
            report.reasons = reasons
        report.paths = result.paths.entries if result.paths is not None else []
        if result.paths is not None and result.paths.unused:
            unused = ", ".join(result.paths.unused[:10])
            report.messages.append(f"authorized but unchanged path(s) (harmless): {unused}")
        if result.pr is not None:
            report.pr = {"result": result.pr.result, "number": result.pr.number, "candidates": result.pr.candidates,
                         "observed": result.pr.observed}
        if result.provenance is not None:
            prov = result.provenance
            report.provenance = {"source": prov.source, "verified": prov.count("verified"),
                                 "unverified": prov.count("unverified"), "unverifiable": prov.count("unverifiable"),
                                 "observed": prov.observed}
        return report
