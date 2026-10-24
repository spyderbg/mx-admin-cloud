"""W4.3.3: automatic publication and completion of an approved GitHub Issue's feature.

    GitHub Issue → `/worker approve` (intake.py) → task run and verified (W2/W3/W4) → integration barrier
    → push of the feature's OWN branch → ONE Draft PR to its base branch → a PERSON reviews and merges it
    → the verified merge completes the feature → the approved Issue is closed → its Project #2 card Done.

Only for a feature made from an approved Issue (it has an intake record) and only while
`github.publish.enabled` is true: nothing else is integrated, pushed or opened automatically. The Worker
never merges, never marks a PR ready, never pushes a protected branch and never forces a push.

`PullRequestPublisher.poll()` is one bounded pass over the intake features, idempotent and restartable:
each step first reads what is already true (the local records, the remote branch, the PRs on GitHub) and
only then writes, and each write is recorded locally before the next one (write-ahead where GitHub is not
idempotent). Per feature, by its EXISTING state (no new state machine):

  active, every task verified, no claim, no launch in flight → `integrate()`, the existing quiescent
      barrier (it re-reads the approved Issue). A failure leaves the feature active; it is not retried
      until a task is verified again or an operator integrates.
  review (the single PR candidate):
      1. the checkout is the feature's repository (root commits; `origin` as recorded at activation, a
         plain github.com/<github.repository> URL that no pushurl/insteadOf rewrites, and its ONLY push
         URL: a push publishes to every one) and the feature branch is still at the validated head;
      2. the PR is looked for BEFORE anything is created: the recorded number, else every PR, open AND
         closed, of the feature branch. A PR is adopted only when it is exactly the candidate: this
         repository's branch → the feature's base branch at the verified head commit, opened by the
         coordinator account or a command author (this also adopts a PR a person opened by hand, e.g.
         #27 of Issue #25). Anything else on the branch stops publication: a second PR is never created;
      3. no PR yet: the approval is re-read (a changed or closed Issue stops publication), the branch is
         pushed (gitops.push_branch), `pending_create` is recorded, then the Draft PR is created and
         recorded. A lost response is recovered by listing again; GitHub refuses a second open PR anyway;
      4. the PR is followed: draft or open → waiting for a person; closed without merge → not complete
         (reported until it is reopened); merged → verified independently: the PR read again by number
         (identity, verified head, base branch, `merged`, the merge commit) AND the base branch fetched from
         the remote, which must contain the merge commit, which must contain the verified head (a merge
         commit; a squash or rebase merge stops for an operator). `complete()` then records the evidence
         and moves the feature to done, in one transaction.
  done → one completion comment on the approved Issue (a fixed outbox ID), then the Issue is closed (its
      identity verified; one a person already closed stays closed), then Project #2 must show Done; the
      intake record becomes `done`. The W4.3.1 withdrawal check never looks at a done feature, so the
      coordinator's own close is never taken for a withdrawn approval.

Doubtful evidence sets `attention` on the candidate (sticky until `feature.py github publish reset FEATURE
--apply`): the branch moved, a PR that does not match, several PRs, a rejected (non-fast-forward) push, a
wrong or rewritten remote, a withdrawn approval before publication, merge evidence that does not hold.
Transient failures (network, GitHub 5xx, a busy lock) are retried by the next pass.

Locks: one pass at a time (github-publish.lock, taken first). GitHub is never called under a coordinator,
project, role or Git lock; only the push holds project.lock EXCLUSIVE + git.lock (no writer can start).
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable, Mapping, NoReturn, Sequence

from .errors import (
    CoordinatorError,
    EvidenceError,
    GitHubNotFoundError,
    GitHubUncertainWriteError,
    GitHubValidationError,
    GitOperationError,
    HeadMismatchError,
    LockBusyError,
    OwnershipError,
    PullRequestPolicyError,
    RemoteRejectedError,
    RemoteUnavailableError,
    ScopeViolationError,
)
from .github_api import sanitize
from .intake import IntakeRecord, issue_problem, known_problem, load_intake, verify_intake_issue
from .issue_sync import OutboxEntry, SimulatedCrash
from .issues import PullRequest, escape_markdown_comment
from .model import Evidence, Feature, FeatureState, PullRequestCandidate, Task, TaskState, utc_now
from .workspace import identity as workspace_identity

PROTECTED_BRANCHES = ("main", "master", "dev")  # never pushed (gitops pushes `feature/…` only, besides)
_GITHUB_URLS = (r"https://{host}/(?P<repo>[A-Za-z0-9-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?",
                r"(?:ssh://)?git@{host}[:/](?P<repo>[A-Za-z0-9-]+/[A-Za-z0-9._-]+?)(?:\.git)?/?")


def github_repository_of(url: str, hostname: str) -> str | None:
    """owner/name when *url* is a plain GitHub URL of *hostname* (https without credentials, or ssh), else None."""
    for pattern in _GITHUB_URLS:
        match = re.fullmatch(pattern.format(host=re.escape(hostname)), url.strip())
        if match:
            return match.group("repo")
    return None


def pr_marker(coordinator: str, feature_id: str, op: str) -> str:
    """The marker the coordinator appends to the PRs it creates (audit only: identity is always checked)."""
    return f"<!-- feature-coordinator:pr v1 coordinator={coordinator} feature={feature_id} op={op} -->"


class _Stopped(Exception):
    """This feature's pass ends here; why is already in the report (and, if sticky, on the candidate)."""


@dataclass
class PublishReport:  # pylint: disable=too-many-instance-attributes
    """What one pass did (apply) or would do (dry run)."""

    applied: bool
    features: dict[str, dict[str, Any]] = field(default_factory=dict)  # feature → {state, status, pr, detail}
    integrated: list[str] = field(default_factory=list)
    pushed: list[str] = field(default_factory=list)
    created: dict[str, int] = field(default_factory=dict)
    adopted: dict[str, int] = field(default_factory=dict)
    completed: dict[str, str] = field(default_factory=dict)  # feature → merge commit
    closed: dict[str, int] = field(default_factory=dict)     # feature → Issue number
    waiting: list[str] = field(default_factory=list)
    attention: list[str] = field(default_factory=list)
    writes: list[str] = field(default_factory=list)          # dry run: what would be written
    deferred: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when nothing was deferred and nothing needs an operator."""
        return not self.deferred and not self.attention

    def to_dict(self) -> dict[str, Any]:
        """JSON form."""
        return {**asdict(self), "ok": self.ok}

    def lines(self) -> list[str]:
        """Human-readable lines (also the service's log lines)."""
        verb = "" if self.applied else "would "
        out = [f"integrated {f}" for f in self.integrated] + [f"pushed {f}" for f in self.pushed]
        out += [f"opened Draft PR #{n} for {f}" for f, n in self.created.items()]
        out += [f"{verb}record PR #{n} as the PR of {f}" for f, n in self.adopted.items()]
        out += [f"completed {f}: merged as {sha[:12]}" for f, sha in self.completed.items()]
        out += [f"closed Issue #{n} of {f}" for f, n in self.closed.items()]
        out += [f"waiting: {w}" for w in self.waiting] + [f"NEEDS ATTENTION: {a}" for a in self.attention]
        out += [f"dry run: {w}" for w in self.writes] + [f"deferred: {sanitize(d, 300)}" for d in self.deferred]
        return out

    def service_state(self) -> tuple[str, str] | None:
        """The Worker service's idle state while no feature is active: what the published features wait for."""
        if self.attention:
            return "NEEDS_ATTENTION", "publication: " + "; ".join(self.attention)
        if self.waiting:
            return "WAITING", "; ".join(self.waiting)
        return None


class PullRequestPublisher:  # pylint: disable=too-few-public-methods
    """One bounded publication pass for one coordinator (see the module docstring)."""

    def __init__(self, coord: Any, *, faults: Iterable[str] = (), sleep: Callable[[float], None] = time.sleep) -> None:
        self.coord, self.store, self.config, self.git = coord, coord.store, coord.config, coord.git
        self.cfg = coord.config.github.publish
        self.client = coord.sync.client
        self.faults = set(faults)
        self.sleep = sleep

    def _fault(self, name: str) -> None:
        if name in self.faults:
            self.faults.discard(name)
            raise SimulatedCrash(name)

    # -- the pass -------------------------------------------------------------------------

    def poll(self, *, apply: bool, features: Sequence[str] = ()) -> PublishReport:
        """Advance every intake feature (or the given ones) as far as it can go now. A dry run reads GitHub,
        the remote branch and the local state and writes nothing."""
        report = PublishReport(apply)
        if apply and not self.cfg.enabled:
            raise CoordinatorError("github.publish.enabled is false: automatic publication is off until an operator "
                                   "enables it in the project configuration")
        lock = self.store.locks.publish() if apply else None
        if lock is not None:
            try:
                lock.acquire()
            except LockBusyError:
                report.deferred.append("another publication pass is running")
                return report
        try:
            for feature in self._features(features, report):
                try:
                    self._advance(feature, report, apply)
                except _Stopped:
                    pass
                except CoordinatorError as exc:  # GitHub or the remote unreachable, a busy lock, a concurrent change
                    detail = sanitize(f"{type(exc).__name__}: {exc}", 300)
                    self._status(report, feature, "deferred", detail)
                    report.deferred.append(f"{feature.id}: {detail}")
        finally:
            if lock is not None:
                lock.release()
        return report

    def _features(self, wanted: Sequence[str], report: PublishReport) -> list[Feature]:
        chosen = []
        for feature in self.store.list_features():
            if wanted and feature.id not in wanted:
                continue
            record = load_intake(self.store, feature.id)
            if record is None or record.task_id not in feature.task_ids:
                if wanted:
                    report.attention.append(f"{feature.id} is not an approved GitHub Issue's feature: it is never "
                                            "published automatically (integrate it and open its PR by hand)")
                continue
            chosen.append(feature)
        missing = sorted(set(wanted) - {f.id for f in self.store.list_features()})
        report.attention += [f"{m}: no such feature" for m in missing]
        return chosen

    def _advance(self, feature: Feature, report: PublishReport, apply: bool) -> None:
        record = load_intake(self.store, feature.id)
        assert record is not None
        if feature.state is FeatureState.ACTIVE:
            self._integrate(feature, record, report, apply)
        elif feature.state is FeatureState.REVIEW:
            self._review(feature, record, report, apply)
        elif feature.state is FeatureState.DONE and record.status != "done":
            self._finish(feature, record, report, apply)
        else:
            self._status(report, feature, "done" if record.status == "done" else feature.state.value, "")

    def _status(self, report: PublishReport, feature: Feature, status: str, detail: str) -> None:
        current = self.store.load_feature(feature.id)
        pr = current.pull_request
        report.features[feature.id] = {"state": current.state.value, "status": status, "detail": detail,
                                       "pr": pr.number if pr else None, "url": pr.url if pr else None}

    def _stop(self, feature: Feature, report: PublishReport, reason: str, *, sticky: bool = True,
              apply: bool = True) -> NoReturn:
        """Stop this feature's pass for an operator. Sticky: recorded on the candidate (an operator resets it)."""
        reason = sanitize(reason, 600)
        report.attention.append(f"{feature.id}: {reason}")
        if sticky and apply:
            with self.store.transaction():
                fresh = self.store.load_feature(feature.id)
                if fresh.pull_request is not None and not fresh.pull_request.attention:
                    fresh.pull_request.attention = {"reason": reason, "since": utc_now()}
                    self.store.save_feature(fresh)
                    self.store.append_event("publish-attention", feature=feature.id, reason=reason)
        self._status(report, feature, "attention", reason)
        raise _Stopped(reason)

    # -- active: the integration barrier ------------------------------------------------------

    def _integrate(self, feature: Feature, record: IntakeRecord, report: PublishReport, apply: bool) -> None:
        tasks = self.store.load_tasks(feature)
        pending = sorted(t.id for t in tasks.values() if t.state is not TaskState.VERIFIED)
        if pending:
            self._status(report, feature, "implementing", f"not verified yet: {pending}")
            return
        busy = sorted(self.store.list_claims()) + sorted(
            t for t in tasks if (self.store.load_record("scheduling", t) or {}).get("in_flight"))
        if busy:
            self._status(report, feature, "waiting", f"claims or launches are still active: {busy}")
            return
        withdrawn = next((p for p in (known_problem(self.store, t) for t in tasks.values()) if p), None)
        if withdrawn:
            self._stop(feature, report, f"not integrated: {withdrawn}", sticky=False)
        failed = self._integration_failed_at(feature, tasks)
        if failed:
            self._stop(feature, report, f"the integration gates failed at {failed}; it is retried once a task is "
                                        f"verified again, or run `feature.py integrate {feature.id} --apply`",
                       sticky=False)
        if not apply:
            report.writes.append(f"integrate {feature.id}: every task is verified (the quiescent integration barrier)")
            self._status(report, feature, "verified", "ready for the integration barrier")
            return
        try:
            feature = self.coord.integrate(feature.id)
        except (EvidenceError, PullRequestPolicyError) as exc:  # gates failed, approval withdrawn, unclean checkout
            self._stop(feature, report, f"integration refused: {exc}", sticky=False)
        report.integrated.append(feature.id)
        self._review(feature, load_intake(self.store, feature.id) or record, report, apply)

    @staticmethod
    def _integration_failed_at(feature: Feature, tasks: Mapping[str, Task]) -> str | None:
        """When the last integration failed, unless a task was verified again since then."""
        last = feature.history[-1] if feature.history else None
        if last is None or last.to_state != "active" or not last.reason.startswith("integration gates failed"):
            return None
        again = [h for t in tasks.values() for h in t.history if h.to_state == "verified" and h.at > last.at]
        return None if again else last.at

    # -- review: push, the single Draft PR, the human merge -----------------------------------------

    def _review(self, feature: Feature, record: IntakeRecord, report: PublishReport, apply: bool) -> None:
        cand = feature.pull_request
        if cand is None:
            self._stop(feature, report, "the feature is in review without a PR candidate", sticky=False)
        assert cand is not None
        if cand.attention:
            self._stop(feature, report, f"{cand.attention.get('reason')} (since {cand.attention.get('since')}; after "
                                        f"resolving it: `feature.py github publish reset {feature.id} --apply`)",
                       sticky=False)
        problem = self._repository_problem(feature) or self._head_problem(feature, cand)
        if problem:
            self._stop(feature, report, problem, apply=apply)
        pr = self._find(feature, cand, report, apply)
        if pr is None:
            self._open(feature, record, cand, report, apply)
            return
        self._follow(feature, record, self.store.load_feature(feature.id).pull_request or cand, pr, report, apply)

    def _repository_problem(self, feature: Feature) -> str | None:
        """Why this checkout's `origin` is not the feature's GitHub repository (None: it is)."""
        return self._origin(feature)[1]

    def _origin(self, feature: Feature) -> tuple[str, str | None]:
        """(origin's single push URL, why it is not exactly the feature's GitHub repository — None: it is)."""
        recorded = feature.workspace or {}
        if recorded.get("root_commits") and recorded["root_commits"] != workspace_identity(self.coord.root)[
                "root_commits"]:
            return "", "repository identity mismatch: different root commit(s) than at activation"
        configured, push = self.git.remote_urls()
        if len(push) != 1:
            shown = ", ".join(repr(sanitize(url, 120)) for url in push[:5])
            return "", (f"a push to origin would publish to {len(push)} URLs ({shown}); exactly one push "
                        "destination is required; nothing is pushed or verified")
        if configured != push:
            return "", (f"the push URL of origin is rewritten ({sanitize(push[0], 120)!r}, configured "
                        f"{', '.join(repr(sanitize(url, 120)) for url in configured[:5]) or 'nothing'}); nothing "
                        "is pushed or verified")
        url = push[0]
        if recorded.get("origin") and recorded["origin"] != url:
            return "", f"origin changed since activation (now {sanitize(url, 120)!r})"
        repository = github_repository_of(url, self.config.github.hostname)
        if repository is None or repository.lower() != self.config.github.repository.lower():
            return "", (f"origin ({sanitize(url, 120)!r}) is not {self.config.github.hostname}/"
                        f"{self.config.github.repository}; nothing is pushed or verified")
        return url, None

    def _head_problem(self, feature: Feature, cand: PullRequestCandidate) -> str | None:
        try:
            local = self.git.rev_parse(f"refs/heads/{feature.branch}")
        except GitOperationError:
            return f"the feature branch {feature.branch} does not exist in this checkout"
        if local != cand.head_sha:
            return (f"{feature.branch} is at {local[:12]}, not the validated head {cand.head_sha[:12]}: it moved after "
                    "integration")
        return None

    def _authors(self) -> set[str]:
        return {self.coord.sync.viewer(), *self.config.github.command_authors}

    def _pr_problem(self, feature: Feature, cand: PullRequestCandidate, pr: PullRequest) -> str | None:
        """Why *pr* is not exactly the candidate's PR (None: it is)."""
        repo = self.config.github.repository.lower()
        checks = ((pr.head == feature.branch, f"its head branch is {pr.head!r}"),
                  (pr.head_repo.lower() == repo, f"its head is in {pr.head_repo or 'an unknown repository'!r}"),
                  (pr.base_repo.lower() == repo, f"it targets the repository {pr.base_repo or '?'!r}"),
                  (pr.base == feature.base_branch, f"it targets {pr.base!r}, not {feature.base_branch!r}"),
                  (pr.head_sha == cand.head_sha, f"its head commit {pr.head_sha[:12] or '?'} is not the verified "
                                                 f"{cand.head_sha[:12]}"),
                  (pr.author in self._authors(), f"it was opened by {pr.author!r}, neither the coordinator account "
                                                 "nor a command author"))
        return "; ".join(why for ok, why in checks if not ok) or None

    def _find(self, feature: Feature, cand: PullRequestCandidate, report: PublishReport,
              apply: bool) -> PullRequest | None:
        """The candidate's PR: the recorded one (verified), else the one matching PR of the branch (adopted);
        None when the branch has no PR at all. Anything ambiguous stops: a second PR is never created."""
        if cand.number:
            try:
                pr = self.client.get_pull_request(cand.number)
            except GitHubNotFoundError:
                self._stop(feature, report, f"the recorded PR #{cand.number} no longer exists on GitHub", apply=apply)
            problem = self._pr_problem(feature, cand, pr)
            if problem:
                self._stop(feature, report, f"PR #{pr.number} no longer matches the verified candidate: {problem}",
                           apply=apply)
            return pr
        prs = self._branch_prs(feature)
        if not prs and cand.pending_create:  # an interrupted create: GitHub's listing may lag behind
            for _attempt in range(3):
                self.sleep(self.config.github.create_settle_seconds)
                prs = self._branch_prs(feature)
                if prs:
                    break
        if not prs:
            return None
        matching = [p for p in prs if self._pr_problem(feature, cand, p) is None]
        adoptable = {m.number for m in matching}
        live_others = [p for p in prs if p.number not in adoptable and (p.state == "open" or p.merged)]
        if len(matching) == 1 and not live_others:
            return self._adopt(feature, cand, matching[0], report, apply)
        reasons = "; ".join(f"#{p.number} ({p.state}{', merged' if p.merged else ''}): "
                            f"{self._pr_problem(feature, cand, p) or 'matches'}" for p in prs)
        self._stop(feature, report, f"{feature.branch} already has pull request(s) that cannot be adopted as its "
                                    f"single PR — {reasons}; no PR is created", apply=apply)

    def _branch_prs(self, feature: Feature) -> list[PullRequest]:
        return [p for p in self.client.list_pull_requests(feature.branch, state="all") if p.head == feature.branch]

    def _adopt(self, feature: Feature, cand: PullRequestCandidate, pr: PullRequest, report: PublishReport,
               apply: bool) -> PullRequest:
        origin = "created" if cand.pending_create and pr.author == self.coord.sync.viewer() else "adopted"
        report.adopted[feature.id] = pr.number
        if apply:
            self._record_pr(feature, pr, origin)
        else:
            report.writes.append(f"record PR #{pr.number} ({pr.state}{', merged' if pr.merged else ''}) as the "
                                 f"single PR of {feature.id}")
        return pr

    def _record_pr(self, feature: Feature, pr: PullRequest, origin: str) -> None:
        """The PR's number and identity on the candidate, the PR link on the approved Issue — one transaction."""
        record = load_intake(self.store, feature.id)
        assert record is not None
        with self.store.transaction():
            fresh = self.store.load_feature(feature.id)
            cand = fresh.pull_request
            if cand is None or cand.number not in (None, pr.number):
                raise PullRequestPolicyError(f"feature {feature.id!r} changed while its PR #{pr.number} was recorded")
            cand.number, cand.url, cand.origin, cand.author = pr.number, pr.url or f"pr#{pr.number}", origin, pr.author
            cand.draft, cand.pending_create, cand.ci_state = pr.draft, None, "pending"
            self.store.save_feature(fresh)
            self.store.append_event("pull-request-opened" if origin == "created" else "pull-request-adopted",
                                    feature=feature.id, pr=pr.number, head=pr.head_sha, base=pr.base, author=pr.author,
                                    state=pr.state, merged=pr.merged, draft=pr.draft)
            if pr.state == "open":
                kind = "Draft pull request opened" if origin == "created" else "Pull request found and recorded"
                self._queue(record.task_id, f"pr-{feature.id}", (
                    f"**{kind}**: #{pr.number} ({pr.url}) — `{feature.branch}` → `{feature.base_branch}` at the "
                    f"verified `{pr.head_sha[:12]}`. The Worker never merges: a person reviews it, marks it ready and "
                    f"merges it. Merging completes task `{record.task_id}`; the coordinator then closes this Issue and "
                    "moves its Project card to Done. Closing the pull request without merging completes nothing."))
        self.coord.sync_issues(feature.id)  # posts the PR link (outside every lock)

    def _queue(self, record_id: str, op: str, body: str) -> None:
        """A coordinator comment with a FIXED operation ID: queued once, posted once (inside a transaction)."""
        if self.store.load_record("outbox", op) is None:
            self.store.save_record("outbox", OutboxEntry(id=op, record_id=record_id, body=body))

    def _open(self, feature: Feature, record: IntakeRecord, cand: PullRequestCandidate, report: PublishReport,
              apply: bool) -> None:
        """No PR exists for the branch: re-check the approval, push, then create the ONE Draft PR."""
        if not apply:
            report.writes += [f"push {feature.branch} at {cand.head_sha[:12]} to origin (never forced)",
                              f"open ONE Draft PR {feature.branch} → {feature.base_branch}"]
            self._status(report, feature, "integrated", "the Draft PR is opened next")
            return
        task = self.store.load_task(record.task_id)
        withdrawn = known_problem(self.store, task) or issue_problem(self.client, record)[1]
        if withdrawn:
            self._stop(feature, report, f"the approval no longer holds; nothing is published: {withdrawn}")
        self._push(feature, cand, report)
        fresh = self.store.load_feature(feature.id).pull_request
        assert fresh is not None
        self._create(feature, fresh, report)

    def _push(self, feature: Feature, cand: PullRequestCandidate, report: PublishReport) -> None:
        self._fault(f"crash-before-push:{feature.id}")
        destination, problem = self._origin(feature)  # push_branch checks it again under its locks
        if problem:
            self._stop(feature, report, f"push refused: {problem}")
        try:
            result = self.git.push_branch(feature.branch, cand.head_sha, destination=destination,
                                          timeout=self.cfg.git_timeout_seconds,
                                          protected=(*PROTECTED_BRANCHES, feature.base_branch, self.config.base_branch))
        except RemoteUnavailableError:
            raise
        except (RemoteRejectedError, HeadMismatchError, ScopeViolationError) as exc:
            self._stop(feature, report, f"push refused: {exc}")
        except GitOperationError as exc:  # e.g. an unclean checkout: reported, retried by the next pass
            self._stop(feature, report, f"not pushed: {exc}", sticky=False)
        self._fault(f"crash-after-push:{feature.id}")
        with self.store.transaction():
            fresh = self.store.load_feature(feature.id)
            if fresh.pull_request is not None:
                fresh.pull_request.pushed = {"sha": result.sha, "before": result.before, "at": utc_now()}
                self.store.save_feature(fresh)
            self.store.append_event("publish-pushed", feature=feature.id, branch=feature.branch, head=result.sha,
                                    before=result.before, pushed=result.pushed)
        if result.pushed:
            report.pushed.append(feature.id)

    def _create(self, feature: Feature, cand: PullRequestCandidate, report: PublishReport) -> None:
        op = f"p{secrets.token_hex(6)}"
        with self.store.transaction():  # write-ahead: a crash after the request is recovered by listing first
            fresh = self.store.load_feature(feature.id)
            assert fresh.pull_request is not None
            fresh.pull_request.pending_create = {"op": op, "started_at": utc_now(), "head_sha": cand.head_sha}
            self.store.save_feature(fresh)
            self.store.append_event("pull-request-create-started", feature=feature.id, op=op, head=cand.head_sha)
        self._fault(f"crash-before-create:{feature.id}")
        body = f"{cand.body}\n\n{pr_marker(self.config.github.coordinator_id, feature.id, op)}\n"
        authorize = getattr(self.client, "authorize_pull_request", None)
        if authorize is not None:
            authorize(feature.branch, feature.base_branch)
        try:
            pr = self.client.create_pull_request(feature.branch, feature.base_branch, cand.title, body, draft=True)
        except GitHubUncertainWriteError as exc:
            detail = f"the Draft PR request may have been applied ({sanitize(exc, 200)}); the next pass lists first"
            report.deferred.append(f"{feature.id}: {detail}")
            self._status(report, feature, "deferred", detail)
            return
        except (GitHubValidationError, PullRequestPolicyError) as exc:  # e.g. GitHub: a PR for the branch exists
            found = [p for p in self._branch_prs(feature) if self._pr_problem(feature, cand, p) is None]
            if len(found) != 1:
                self._stop(feature, report, f"GitHub refused the Draft PR: {exc}")
            pr = found[0]
        self._fault(f"crash-after-create:{feature.id}")
        problem = self._pr_problem(feature, cand, pr)
        if problem:  # never recorded, never linked on the Issue; the next pass finds it and refuses a second PR
            self._stop(feature, report, f"the new PR #{pr.number} does not match the verified candidate: {problem}; "
                                        "it is not recorded")
        self._record_pr(feature, pr, "created")
        report.created[feature.id] = pr.number
        report.waiting.append(f"{feature.id}: Draft PR #{pr.number} waits for a person to review and merge it "
                              f"({pr.url})")
        self._status(report, feature, "draft", f"Draft PR #{pr.number} opened")

    def _follow(self, feature: Feature, record: IntakeRecord, cand: PullRequestCandidate, pr: PullRequest,  # pylint: disable=too-many-arguments
                report: PublishReport, apply: bool) -> None:
        """The PR exists: wait for the person, or verify their merge."""
        pr = self.client.get_pull_request(pr.number)  # fresh, by number (a listed PR does not say `merged`)
        problem = self._pr_problem(feature, cand, pr)
        if problem:
            self._stop(feature, report, f"PR #{pr.number} no longer matches the verified candidate: {problem}",
                       apply=apply)
        if pr.state == "open":
            if apply and cand.draft is not pr.draft:
                with self.store.transaction():
                    fresh = self.store.load_feature(feature.id)
                    if fresh.pull_request is not None:
                        fresh.pull_request.draft = pr.draft
                        self.store.save_feature(fresh)
                        self.store.append_event("pull-request-ready" if not pr.draft else "pull-request-draft",
                                                feature=feature.id, pr=pr.number)
            what = "Draft PR" if pr.draft else "PR (ready for review)"
            report.waiting.append(f"{feature.id}: {what} #{pr.number} waits for a person to review and merge it "
                                  f"({pr.url})")
            self._status(report, feature, "draft" if pr.draft else "open", f"{what} #{pr.number}")
            return
        if not pr.merged:
            self._stop(feature, report, f"PR #{pr.number} was closed without merge: task {record.task_id} is NOT "
                                        f"complete and Issue #{record.issue_number} stays open; reopen and merge the "
                                        "PR (nothing is created again)", sticky=False)
        self._complete(feature, record, cand, pr, report, apply)

    def _complete(self, feature: Feature, record: IntakeRecord, cand: PullRequestCandidate,  # pylint: disable=too-many-arguments
                  pr: PullRequest, report: PublishReport, apply: bool) -> None:
        """Verify the person's merge independently, then complete the feature (see the module docstring)."""
        merge = pr.merge_commit_sha
        if not merge or not pr.merged_at:
            self._stop(feature, report, f"GitHub reports PR #{pr.number} merged without a merge commit or time",
                       apply=apply)
        if not apply:
            report.writes.append(f"verify that {feature.base_branch} contains PR #{pr.number}'s merge {merge[:12]}, "
                                 f"then complete {feature.id} and close Issue #{record.issue_number}")
            self._status(report, feature, "merged", f"PR #{pr.number} merged as {merge[:12]}")
            return
        tip = self.git.fetch_branch(feature.base_branch, timeout=self.cfg.git_timeout_seconds)
        if not self.git.is_ancestor(merge, tip):
            self._stop(feature, report, f"PR #{pr.number}'s merge commit {merge[:12]} is not on origin/"
                                        f"{feature.base_branch} ({tip[:12]})")
        if not self.git.is_ancestor(cand.head_sha, merge):
            self._stop(feature, report, f"PR #{pr.number}'s merge commit {merge[:12]} does not contain the verified "
                                        f"head {cand.head_sha[:12]} (a squash or rebase merge?): not completed "
                                        "automatically")
        evidence = {"number": pr.number, "url": pr.url, "merge_commit_sha": merge, "merged_at": pr.merged_at,
                    "merged_by": pr.merged_by, "head_sha": pr.head_sha, "base": pr.base, "base_tip": tip,
                    "verified_at": utc_now()}
        self._fault(f"crash-before-done:{feature.id}")
        self.coord.complete(feature.id, merged_sha=merge, merge=evidence)
        report.completed[feature.id] = merge
        self._fault(f"crash-after-done:{feature.id}")
        self._finish(self.store.load_feature(feature.id), load_intake(self.store, feature.id) or record, report, apply)

    # -- done: completion comment, close the approved Issue, Project #2 Done -----------------------------

    def _finish(self, feature: Feature, record: IntakeRecord, report: PublishReport, apply: bool) -> None:
        merge = (feature.pull_request.merge if feature.pull_request else None) or {}
        if not apply:
            report.writes.append(f"comment on and close Issue #{record.issue_number}, show Done on the board")
            self._status(report, feature, "finishing", "done; the Issue is closed next")
            return
        op = f"done-{feature.id}"
        with self.store.transaction():
            self._queue(record.task_id, op, self._completion_text(feature, record, merge))
        self.coord.sync_issues(feature.id)  # posts it before the Issue is closed
        if (self.store.load_record("outbox", op) or {}).get("status") != "done":
            detail = "the completion comment is not posted yet; the Issue is closed after it"
            report.deferred.append(f"{feature.id}: {detail}")
            self._status(report, feature, "finishing", detail)
            return
        self._fault(f"crash-after-comment:{feature.id}")
        closed_by = self._close_issue(feature, record, report)
        self._fault(f"crash-after-close:{feature.id}")
        if not self._board_done(feature, record, report):
            return
        with self.store.transaction():
            fresh = load_intake(self.store, feature.id)
            if fresh is not None and fresh.status != "done":
                fresh.status = "done"
                fresh.completed = {"pr": merge.get("number"), "merge_commit_sha": feature.merged_sha,
                                   "issue": record.issue_number, "closed_by": closed_by, "at": utc_now()}
                self.store.save_record("intake", fresh)
                self.store.append_event("intake-completed", feature=feature.id, issue=record.issue_number,
                                        pr=merge.get("number"), closed_by=closed_by)
        if closed_by == "coordinator":
            report.closed[feature.id] = record.issue_number
        self._status(report, feature, "done", f"Issue #{record.issue_number} {closed_by.replace('-', ' ')}")

    def _close_issue(self, feature: Feature, record: IntakeRecord, report: PublishReport) -> str:
        """Close exactly the approved Issue (number, database and node ID), once. Returns who closed it."""
        link = self.coord.sync.load_link(record.task_id)
        try:
            issue = self.client.get_issue(record.issue_number)
        except GitHubNotFoundError:
            return "issue-missing"  # deleted or transferred: nothing to close
        try:
            verify_intake_issue(issue, record, known_id=link.issue_id if link else None)
        except OwnershipError as exc:
            self._stop(feature, report, f"Issue #{record.issue_number} is not closed: {exc}", sticky=False)
        if issue.state != "open":
            return "already-closed"
        self.client.update_issue(issue.number, state="closed", state_reason="completed")
        with self.store.transaction():
            self.store.append_event("intake-issue-closed", feature=feature.id, issue=issue.number)
        return "coordinator"

    def _board_done(self, feature: Feature, record: IntakeRecord, report: PublishReport) -> bool:
        board = self.coord.board
        if board is None:
            return True
        result = board.sync(feature.id, apply=True)
        cfg = self.config.github.project
        if result.ok and (result.items.get(record.task_id) or {}).get(cfg.status_field) == cfg.status("done"):
            return True
        why = "; ".join([*result.deferred, *result.protected.values()]) or "the card is not Done yet"
        detail = f"Project #{cfg.number} does not show Done yet ({sanitize(why, 200)}); the next pass retries"
        report.deferred.append(f"{feature.id}: {detail}")
        self._status(report, feature, "finishing", detail)
        return False

    @staticmethod
    def _completion_text(feature: Feature, record: IntakeRecord, merge: Mapping[str, Any]) -> str:
        done = f"Feature `{feature.id}` is done: the coordinator closes this Issue and its Project card shows Done."
        if not merge:
            return f"**Completed.** Merged as `{(feature.merged_sha or '')[:12]}`. {done}"
        by = f" by `{merge['merged_by']}`" if merge.get("merged_by") else ""
        return (f"**Completed.** Pull request #{merge.get('number')} ({merge.get('url')}) was merged into "
                f"`{merge.get('base')}`{by} at {merge.get('merged_at')}; its merge commit "
                f"`{str(merge.get('merge_commit_sha'))[:12]}` contains the verified head "
                f"`{str(merge.get('head_sha'))[:12]}` of task `{record.task_id}`. {done}")


# ---------------------------------------------------------------------------
# The Draft PR's body (rendered at integration: workflow._render_pr)
# ---------------------------------------------------------------------------

def _cell(text: str, limit: int = 200) -> str:
    return escape_markdown_comment(sanitize(text, limit)).replace("|", "\\|")


def intake_pr_body(feature: Feature, tasks: Mapping[str, Task], evidence: Evidence, changed: Sequence[str],  # pylint: disable=too-many-arguments
                   record: IntakeRecord) -> str:
    """The Draft PR of an approved GitHub Issue: the Issue, feature and task, the approved task summary,
    changed files, validation and integration results. No closing keyword: the coordinator closes the Issue
    itself, and only after the verified merge."""
    gates = {t.id: ", ".join(f"`{g.gate}` {g.status} (exit {g.exit_code})" for g in t.evidence.gates)
             if t.evidence else "no evidence" for t in tasks.values()}
    lines = [f"Implements GitHub Issue #{record.issue_number} ({record.url}), approved for the Worker by "
             f"`{record.approved_by}`.", "", "## Approved task", "", "| Field | Value |", "| --- | --- |",
             f"| Issue | #{record.issue_number} — {_cell(record.title)} |",
             f"| Feature | `{feature.id}`: branch `{feature.branch}` from `{feature.base_branch}` at "
             f"`{feature.base_sha[:12]}` |",
             f"| Task | `{record.task_id}` (role `{record.role}`, gates {', '.join(f'`{g}`' for g in record.gates)}) |",
             f"| Approved | by `{record.approved_by}` at {record.approved_at} ({record.approval_comment_url}) |",
             f"| Specification | the Issue's title and body at approval, sha256 `{record.digest}` |",
             "", "## Changed files", *[f"- `{_cell(p, 300)}`" for p in changed[:200]],
             *([f"- … and {len(changed) - 200} more"] if len(changed) > 200 else []),
             "", "## Validation (task gates on the feature branch)",
             *[f"- `{t.id}` ({t.role}): {gates[t.id]}; commits "
               f"{', '.join(f'`{c[:12]}`' for c in t.commits) or 'none'}" for t in tasks.values()],
             "", f"## Integration gates at `{evidence.head_sha[:12]}`",
             *[f"- `{g.gate}`: {'pass' if g.passed else 'FAIL'} ({g.status}, exit {g.exit_code})"
               for g in evidence.gates],
             "", "## Review",
             "Opened as a **Draft** by the feature coordinator (W4.3.3). The Worker never merges and never marks it "
             "ready: a person reviews it, marks it ready and merges it with a merge commit. The verified merge "
             f"completes feature `{feature.id}`; the coordinator then closes the approved Issue and moves its Project "
             "card to Done. Closing this pull request without merging completes nothing.", "",
             "Remote CI: pending / not configured (branch protection is unavailable on the current plan)."]
    return "\n".join(lines)
