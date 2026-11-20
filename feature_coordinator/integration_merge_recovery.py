"""W4.7 (M5, Codex F3 and F4): what the merge does with a write whose outcome it does not know, and with a merge it did
not make.

Four situations are kept apart, because a timer is not an observation:

  1. DEFINITELY NOT SUBMITTED — the attempt has no `merge-dispatch` entry (the write boundary never let a request
     leave: a crash between the intent and the boundary, or a refusal at the boundary), or the transport reports that
     the request never left. The attempt is closed (`not-merged` / `not-created`, basis `not-dispatched` / `not-sent`)
     and the operation may be decided afresh — with a new intent and the full authorization again;
  2. SUBMITTED, CONFIRMED FAILURE — GitHub ANSWERED with an error (basis `rejected`): the request had no effect;
  3. SUBMITTED, CONFIRMED SUCCESS — GitHub answered with the merge (basis `response`), or reads as having merged the PR
     for this job's dispatched request (basis `actor`, below);
  4. SUBMITTED, OUTCOME UNKNOWN — a dispatched attempt with neither: a lost response, a timeout, a 5xx. The request may
     still land, so NO second request is sent for it, ever, however much time passes and whatever a listing shows (a
     listing can lag; a PR it does not show yet may exist, even closed). Each run reads GitHub (the PR; for a PR
     creation, the complete lookup in every state) and reconciles what it finds. `settle_seconds` is only the minimum
     observation interval: younger, the run ends `*-uncertain`; older, the job stays with `attention`
     (`*-unresolved`) for the operator, who either sees the result appear or aborts the job and starts a new one
     (`abort` is refused while a young dispatched merge request may be in flight, and allowed afterwards).

ATTRIBUTION. A merged PR is recorded as THIS job's merge only when the facts support it: the attempt was dispatched,
and GitHub shows the merge by the account this coordinator's GitHub client is authenticated as (the account that sends
its requests). A merge with no dispatched request of this job behind it (no attempt, an attempt without a dispatch,
one GitHub rejected), or by another account, is an EXTERNAL merge: the job is STALE (the base moved), with the merge's
details kept in `attention`; it is never COMPLETED, never adopted, and nothing is reverted. When the facts do not
decide (the authenticated account or the merging account cannot be read) the verdict is AMBIGUOUS: the job stays with
`attention`, nothing is sent, nothing is completed. Residual, GitHub gives no better: a dispatched request whose
answer was lost and an operator merging the same PR at the same moment through the SAME account cannot be told apart
(the merge is then verified against the approved parents and tree like any other, so what is adopted is still exactly
the approved merge).
"""

# pylint: disable=no-member  # a mixin of IntegrationMerger: the attributes below belong to the merger and its bases

from __future__ import annotations

from typing import Any, Mapping, NoReturn

from .errors import CoordinatorError, GitHubError
from .integration import IntegrationJob, JobState, clean_text, evidence_digest, parse_record_time
from .integration_github import fetch_pull
from .integration_merge_schema import (
    SETTLE_SECONDS,
    attempt_dispatch,
    attempt_state,
    merge_attempts,
    open_merge_attempt,
)
from .integration_review import IntegrationReviewer, _Stop  # pylint: disable=protected-access
from .issues import PullRequest
from .model import utc_now

UNCERTAIN_SETTLE_SECONDS = SETTLE_SECONDS   # the minimum observation interval of an unknown outcome — never a verdict
OWN, EXTERNAL, AMBIGUOUS = "own", "external", "ambiguous"


def pull_view(pr: PullRequest, origin: str) -> dict[str, Any]:
    """The report's short description of a PR."""
    return {"number": pr.number, "origin": origin, "state": "merged" if pr.merged else pr.state, "draft": pr.draft,
            "url": pr.url, "head": f"{pr.head_repo}:{pr.head}", "head_sha": pr.head_sha, "base": pr.base}


class MergeRecovery:  # pylint: disable=too-few-public-methods
    """The recovery half of IntegrationMerger (module docstring)."""

    # -- the outcome entry -------------------------------------------------------------------------------------------

    @staticmethod
    def _outcome_entry(job: IntegrationJob, attempt: Mapping[str, Any], status: str, pr: int | None, merge_commit: str,
                       detail: str, basis: str) -> dict[str, Any]:
        candidate = job.candidate
        assert candidate is not None
        entry = {"kind": "merge-outcome", "id": f"mou-{len(job.evidence) + 1:04d}", "at": utc_now(),
                 "candidate": candidate["evidence"], "head_sha": candidate["head_sha"],
                 "tree_sha": candidate["tree_sha"], "attempt": attempt["id"], "status": status, "basis": basis,
                 "pr": attempt["pr"] if pr is None else pr, "merge_commit_sha": merge_commit,
                 "detail": clean_text(detail, 300)}
        entry["sha256"] = evidence_digest(entry)
        return entry

    def _close_attempt(self, job_id: str, attempt: Mapping[str, Any], status: str, basis: str, detail: str,
                       report: Any) -> bool:
        """Record that *attempt* had no effect, in one transaction and only while it is still the open attempt of a
        job awaiting its merge approval (an outcome is final: it is never recorded twice). True when recorded."""
        with self.store.transaction():
            current = self.jobs.load(job_id)
            opened = open_merge_attempt(current)
            if current.state is not JobState.AWAITING_MERGE_APPROVAL or opened is None \
                    or opened["id"] != attempt["id"] \
                    or (basis == "not-dispatched") != (attempt_dispatch(current, opened) is None):
                return False
            current.evidence.append(self._outcome_entry(current, attempt, status, None, "", detail, basis))
            self.jobs.save(current)
        report.changed = True
        return True

    # -- an attempt whose outcome is unknown -------------------------------------------------------------------------

    def _unresolved(self, job: IntegrationJob, attempt: Mapping[str, Any], what: str, report: Any,
                    apply: bool) -> NoReturn:
        """A DISPATCHED attempt without an outcome, and GitHub shows no result: nothing is sent again (module
        docstring, situation 4). Younger than the settle window the run just ends; older, the job gets `attention`."""
        dispatch = attempt_dispatch(job, attempt)
        assert dispatch is not None
        age = (self.clock() - parse_record_time(dispatch["at"], "at")).total_seconds()
        kind = "pr" if attempt["action"] == "create-pr" else "merge"
        text = (f"the {what} request {attempt['id']} was dispatched {int(age)}s ago and its outcome is UNKNOWN (its "
                "answer was lost or ambiguous), and GitHub shows no result of it yet: it may still land, so NO second "
                "request is sent — an elapsed timeout is not proof that it was not accepted")
        if age < self.settle_seconds:
            self._deferred(job, text + " — wait and repeat; nothing else is checked", report, apply,
                           outcome=f"{kind}-uncertain")
        hint = (f"Check GitHub: if the {'PR exists' if kind == 'pr' else 'PR is merged'} the next `integration merge "
                f"--apply` reconciles it; if you are sure that no request was accepted, `integration abort` this job "
                "and start a new one")
        self._blocked(job, f"{kind}-unresolved", [text, hint], report, apply)

    def _close_undispatched(self, job: IntegrationJob, attempt: Mapping[str, Any], report: Any, apply: bool) -> None:
        """An open attempt WITHOUT a dispatch entry: no request was ever sent for it (situation 1). It is closed as a
        recorded fact (so it needs no approval — an operator can abort a job whose approval expired meanwhile)."""
        status = "not-created" if attempt["action"] == "create-pr" else "not-merged"
        report.note("reconcile", "pass", f"the {attempt['action']} intent {attempt['id']} was never dispatched: no "
                                         "request was sent for it")
        if apply:
            self._close_attempt(job.job_id, attempt, status, "not-dispatched",
                                f"no request was dispatched for the intent {attempt['id']}", report)

    # -- step 0: reconcile first -------------------------------------------------------------------------------------

    def _reconcile_first(self, job: IntegrationJob, report: Any, client: Any, actor: str, apply: bool) -> bool:
        """GitHub is asked whether the PR is already merged BEFORE any other check, when this job has a merge attempt.
        True: it was, it is this job's merge, and it is now recorded (MERGED). An external merge is STALE; an
        undecidable one stops the run with attention; an unresolved request is never repeated (module docstring)."""
        sent = [a for a in merge_attempts(job) if a["action"] == "merge"]
        if not sent:
            return False
        newest, number = sent[-1], sent[-1]["pr"]
        try:
            pr = fetch_pull(client, self.config.github.repository, number)
        except (GitHubError, CoordinatorError, OSError) as exc:
            report.note("reconcile", "deferred", f"PR #{number} could not be read: {exc}")
            self._deferred(job, f"a merge request of this job may have been sent and PR #{number} cannot be read, so "
                                f"GitHub's state is unknown — nothing is merged or repeated: {exc}", report, apply)
        state = attempt_state(job, newest)
        if not pr.merged:
            if state == "dispatched":
                report.note("reconcile", "pending", f"PR #{number} is not merged; the merge request {newest['id']} was "
                                                    "dispatched and its outcome is unknown")
                self._unresolved(job, newest, "merge", report, apply)
            if state == "not-dispatched":
                self._close_undispatched(job, newest, report, apply)
            else:
                report.note("reconcile", "pass", f"PR #{number} is not merged")
            return False
        if pr.head != job.source["ref"] or pr.head_sha != job.source["sha"] or pr.base != job.base["ref"]:
            self._external(job, pr, report, actor, apply,
                           f"PR #{number} is merged, but from {pr.head}@{pr.head_sha or '?'} into {pr.base} (not the "
                           f"approved {job.source['ref']}@{job.source['sha']} into {job.base['ref']})")
        if not pr.merge_commit_sha:
            self._deferred(job, f"PR #{number} is merged but GitHub reports no merge commit yet — repeat later",
                           report, apply)
        verdict, why = self._attribution(job, newest, state, pr, client)
        self._after_merged(job, newest, pr, pr.merge_commit_sha, verdict, why, report, actor, apply)
        return True

    # -- who merged it ---------------------------------------------------------------------------------------------

    @staticmethod
    def _attribution(job: IntegrationJob, attempt: Mapping[str, Any], state: str, pr: PullRequest,
                     client: Any) -> tuple[str, str]:
        """(`own` | `external` | `ambiguous`, why) for the merged *pr* and the job's newest merge *attempt* (module
        docstring, ATTRIBUTION). A DISPATCHED request and the authenticated account are the only things it relies on."""
        if state != "dispatched":
            return EXTERNAL, (f"no merge request of this job was ever dispatched ({attempt['id']} is {state}), so the "
                              f"merge by {pr.merged_by or 'an unknown account'} is not this job's")
        try:
            viewer = client.viewer_login()
        except (GitHubError, CoordinatorError, OSError) as exc:
            return AMBIGUOUS, f"the account this coordinator acts as cannot be read ({exc}), so the merger of PR " \
                              f"#{pr.number} cannot be attributed"
        if not pr.merged_by:
            return AMBIGUOUS, f"GitHub does not say who merged PR #{pr.number}, so the merge cannot be attributed"
        if pr.merged_by.lower() != viewer.lower():
            return EXTERNAL, (f"PR #{pr.number} was merged by {pr.merged_by}, not by {viewer}, the account that "
                              f"sends this job's requests")
        dispatch = attempt_dispatch(job, attempt)
        assert dispatch is not None
        return OWN, (f"the merge request {attempt['id']} was dispatched at {dispatch['at']} and GitHub shows PR "
                     f"#{pr.number} merged by {viewer}, the account that sends it")

    def _external(self, job: IntegrationJob, pr: PullRequest, report: Any, actor: str, apply: bool,
                  why: str) -> NoReturn:
        """The PR was merged, but not by this job's request: STALE with the merge's details preserved (the base moved;
        the job never completes, nothing is reverted, reset or force-pushed). Bypasses the merger's own `_final`
        (which defers a STALE verdict over a PR this job may have merged): the attribution has just been decided."""
        observed = {"pr": pr.number, "merge_commit": pr.merge_commit_sha or "", "merged_by": pr.merged_by or "",
                    "merged_at": pr.merged_at or "", "head": f"{pr.head}@{pr.head_sha or '?'}", "base": pr.base,
                    "attribution": EXTERNAL}
        IntegrationReviewer._final(  # pylint: disable=protected-access
            self, job, JobState.STALE, [f"{why} — it is an EXTERNAL merge, not this job's: the base moved"], observed,
            report, actor, apply)

    def _after_merged(self, job: IntegrationJob, attempt: Mapping[str, Any], pr: PullRequest, commit: str,
                      verdict: str, why: str, report: Any, actor: str, apply: bool, *, basis: str = "actor") -> None:
        """What a merged PR means for the job, by *verdict* (module docstring): own → recorded (MERGED); external →
        STALE; ambiguous → attention, nothing recorded or sent."""
        if verdict == EXTERNAL:
            report.note("reconcile", "stale", why)
            self._external(job, pr, report, actor, apply, why)
        if verdict == AMBIGUOUS:
            report.note("reconcile", "attention", why)
            self._blocked(job, "merge-ambiguous", [why, "the PR is merged on GitHub but the merge cannot be attributed "
                                                        "to this job: an operator decides (abort the job)"],
                          report, apply)
        report.note("reconcile", "pass", f"PR #{pr.number} is merged (merge commit {commit}) and it is this job's "
                                         f"merge: {why}")
        if not apply:
            report.outcome, report.pr = "would-reconcile", pull_view(pr, "recorded")
            report.messages.append("--apply records the merge GitHub performed (MERGED) and verifies it; no second "
                                   "merge request is made")
            raise _Stop("would-reconcile")
        self._record_merged(job.job_id, attempt, pr, commit, report, actor, "reconciled", basis)
