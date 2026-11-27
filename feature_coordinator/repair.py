"""W4.8.1: autonomous repair of an approved GitHub Issue's Draft PR whose independent AI review requested changes.

When `github.repair.enabled` (off by default), the Worker service runs this step after its AI review step, at most
every `github.repair.poll_interval_seconds`, never while paused (service.py `_poll_github`, review_worker.py
`github_steps`). It adds no state machine of its own to the feature or the task: it re-uses, in order,

  1. the review's evidence. Only the CURRENT review entry of the publisher's verified head, complete, REQUEST_CHANGES,
     its binding digest intact and its comment posted (`review_evidence`), re-checked against fresh reads before
     anything starts (`ReviewRunner.recheck`: the PR re-read and still the publisher's verified, open candidate, the
     binding — head, merge base, approval, plan — unchanged). A stale, invalidated, ERROR, PASS or PASS_WITH_NOTES
     review never starts a repair; the validated BLOCKING findings (blocker, major) are what the attempt answers;
  2. the original approval and task ownership. v1 repairs a feature with exactly ONE task (one role) without steps,
     verified, of an enabled role, without an attention record; anything else (several tasks or roles: whose finding
     is it?) is NEEDS_ATTENTION, never a guess. The task keeps its role scope and grants: the findings are untrusted
     task data, never a permission (repair_record.repair_brief fences them);
  3. the task path. The attempt is COUNTED first (write-ahead, `state/repair/<feature>.json`), then the task is
     re-opened (verified → ready, workflow.reopen_task). The Worker service then runs it like any task of the active
     feature (repair_record.repairing_features): the ordinary launch — write-ahead launch count, S1 launch gate,
     scoped workspace, approval re-reads, commit with the task's trailers, its required gates, the watchdog, the
     post-launch integrity checks, the retry policy — with the attempt's brief appended to the task's own
     instructions, within the attempt's OWN launch budget (`max_launches_per_attempt` ≤ 2, counted in the attempt
     before each launch, never refunded). A second launch follows only an outcome the retry policy already retries by
     itself (transient, subscription, interrupted, cancelled); failed gates or an integrity failure hold the task
     (NEEDS_ATTENTION; `worker reset-retries` lets the agent try again within the same attempt while its launches
     last). Before each launch (`launch_refusal`) and on each step while the agent does not run, the findings must
     still apply (`_stale`: the candidate, the current review, the PR re-read — open, a draft, at the reviewed
     head); otherwise the attempt ends before its agent (re)starts;
  4. publication's push. Once the task is verified again — its launch settled by the Worker, whose own outcome needs
     no operator — at a new head that continues the PR's head, made only of the task's own plain commits within its
     role's paths (`_foreign`), that head is pushed to the PR's branch (gitops.push_branch: a fast-forward of exactly
     that commit, never forced, the publisher's single-push-URL checks, origin's branch at the reviewed head), after
     the PR (identity, open, head), the approval and the review's binding (the merge base: `_binding_problem`, also
     before each launch) were re-read. A write-ahead `pushing` marker precedes the network push; a push whose result
     was not recorded is reconciled with origin and never repeated;
  5. revalidation (revalidate.py, unchanged): the same PR at the pushed head — fast-forward only, plain commits,
     every path writable by the feature's tasks, the original candidate's integration gates, the approved plan's
     integration gates and every task's required gates, re-checked, then the candidate moved and the old review
     invalidated in one transaction. The review step then reviews the new head by itself (a new, independent review;
     an earlier verdict is never evidence for it).

Never: merge, mark a PR ready, create a second PR, force a push, rewrite a commit, widen a role scope or grant, skip
a gate, act on a stale review, or let an agent write coordinator state (the agent runs exactly as a task's agent).

Budget and recovery. At most `max_attempts` (≤ 2) attempts per feature, one per review entry, ever, and a new attempt
only while none is open (normally it answers the review of the head the previous one completed; after an operator
moved the candidate, the review of that head may start one). Exhausted, or an attempt that
FAILED (push refused, revalidation failed, the PR changed meanwhile) or changed NOTHING: NEEDS_ATTENTION with the
operator's options; the PR, its branch, every commit and all review, validation and attempt records are kept. Each
step reads what is true first: a crash after the attempt was counted re-opens the task on the next step; one after
the push finds the remote at the commit (nothing pushed twice); one after the revalidation finds the PR at the
candidate (`unchanged`). While an attempt is open, publication waits for it (publish.py). Records are never
rewritten, only extended. `github repair abandon` (an operator's decision) ends an open attempt.
"""

from __future__ import annotations

import dataclasses
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, NoReturn

from .autonomy import CANCELLED, CRITICAL, TRANSIENT, Outcome, epoch, load_scheduling, task_status
from .errors import (
    CoordinatorError,
    GitConfigExecutionError,
    GitHubNotFoundError,
    GitOperationError,
    HeadMismatchError,
    LockBusyError,
    RemoteRejectedError,
    ScopeViolationError,
)
from .github_api import sanitize
from .intake import issue_problem, known_problem, load_intake
from .integration_slot import execution_owner
from .issue_sync import SimulatedCrash
from .model import Feature, FeatureState, Task, TaskState, utc_now
from .publish import PROTECTED_BRANCHES, PullRequestPublisher
from .repair_record import RepairRecord, agent_attempt, findings_digest, load_repair
from .review import BINDING_KEYS, ReviewRunner, load_review, review_evidence
from .review_process import attempt_processes
from .review_result import BLOCKING
from .revalidate import PullRequestRevalidator, load_revalidation

WAITING, NEEDS_ATTENTION = "WAITING", "NEEDS_ATTENTION"  # the Worker's states (autonomy.py)
STALE_GRACE_SECONDS = 1800.0  # GitHub's PR head follows a push within seconds; still stale after this: an operator


class _Stopped(Exception):
    """This feature's step ends here; why is already in the report."""


@dataclass
class RepairStepReport:
    """What one service step did. `events`: what happened; `waiting` / `attention`: what the repairs wait for."""

    events: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    attention: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        """The service's log lines."""
        return [*self.events, *(f"waiting: {w}" for w in self.waiting),
                *(f"NEEDS ATTENTION: {a}" for a in self.attention), *(f"deferred: {sanitize(d, 300)}"
                                                                    for d in self.deferred)]

    def service_state(self) -> tuple[str, str] | None:
        """The Worker's idle state for the repairs (combined with publication's and the reviews': `idle_state`)."""
        if self.attention:
            return NEEDS_ATTENTION, "repair: " + "; ".join(self.attention)
        return (WAITING, "; ".join(self.waiting)) if self.waiting else None


class RepairStep:  # pylint: disable=too-few-public-methods
    """The Worker service's repair step (see the module docstring)."""

    def __init__(self, coord: Any, *, faults: Iterable[str] = (), clock: Callable[[], float] = time.time) -> None:
        self.coord, self.store, self.config, self.git = coord, coord.store, coord.config, coord.git
        self.cfg = coord.config.github.repair
        self.client = coord.sync.client
        self.publisher = PullRequestPublisher(coord)  # its identity, origin and head checks only; it never runs a pass
        self.faults = set(faults)
        self.clock = clock

    def _fault(self, name: str) -> None:
        if name in self.faults:
            self.faults.discard(name)
            raise SimulatedCrash(name)

    def poll(self, *, apply: bool = True) -> RepairStepReport:
        """One step over the approved Issues' features in review (see the module docstring)."""
        report = RepairStepReport()
        if not apply or not self.cfg.enabled:
            return report
        lock = self.store.locks.repair()
        try:
            lock.acquire()
        except LockBusyError:
            report.deferred.append("github-repair.lock is held (another repair step?); the step runs again later")
            return report
        try:
            for feature in self._features():
                try:
                    self._advance(feature, report)
                except _Stopped:
                    pass
                except CoordinatorError as exc:  # GitHub, origin or a lock unavailable: the next step retries
                    report.deferred.append(f"{feature.id}: {type(exc).__name__}: {sanitize(exc, 300)}")
        finally:
            lock.release()
        return report

    def _features(self) -> list[Feature]:
        """Approved Issues' features in review with a recorded PR, and any feature with an open attempt; Issue order."""
        found = []
        for feature in self.store.list_features():
            intake = load_intake(self.store, feature.id)
            if intake is None:
                continue
            cand = feature.pull_request
            reviewing = feature.state is FeatureState.REVIEW and cand is not None and bool(cand.number)
            record = load_repair(self.store, feature.id)
            if reviewing or (record is not None and record.open_attempt() is not None):
                found.append((intake.issue_number, feature))
        return [feature for _number, feature in sorted(found, key=lambda pair: pair[0])]

    def _advance(self, feature: Feature, report: RepairStepReport) -> None:
        record = load_repair(self.store, feature.id)
        attempt = record.open_attempt() if record else None
        if attempt is not None:
            self._continue(feature, attempt, report)
        else:
            self._consider(feature, record, report)

    # -- starting an attempt ------------------------------------------------------------------------------------------

    def _consider(self, feature: Feature, record: RepairRecord | None, report: RepairStepReport) -> None:
        cand = feature.pull_request
        assert cand is not None and cand.number is not None
        review = load_review(self.store, feature.id)
        entry = review.entry(review.current) if review else None
        if entry is None or entry["status"] != "complete" or entry["outcome"] != "REQUEST_CHANGES":
            return  # nothing to repair (PASS / PASS_WITH_NOTES wait for a person; ERROR is the review's attention)
        if (entry["binding"]["pr"], entry["binding"]["head_sha"]) != (cand.number, cand.head_sha):
            return  # not the review of the verified head: never repaired (the review step reviews that head)
        done = record.for_review(entry["key"]) if record else None
        if done is not None:
            self._ended(feature, done, report)
            return
        used = len(record.attempts) if record else 0
        blocking = [f for f in entry["findings"] if f.get("severity") in BLOCKING]
        where = f"PR #{cand.number} at {cand.head_sha[:12]}"
        if used >= self.cfg.max_attempts:
            self._stop(feature, entry, report, (
                f"automatic repair is exhausted ({used} of {self.cfg.max_attempts} attempt(s) used) and review "
                f"{entry['key']} still requests changes ({len(blocking)} blocking finding(s)) for {where}. The PR, "
                f"{feature.branch}, every commit and all review and validation records are kept. An operator decides: "
                f"fix it on {feature.branch} and run `python -m feature_coordinator github publish revalidate "
                f"{feature.id}`, re-review it (`github review retry {feature.id} --reason … --apply`), or merge or "
                "close the PR by hand"))
        problem = self._ineligible(feature, entry, blocking)
        if problem:
            self._stop(feature, entry, report, f"automatic repair of {where} is not possible: {problem}; an operator "
                                               "decides (the PR and every record are kept)")
        busy = self._busy(feature, entry)
        if busy:
            report.waiting.append(f"{feature.id}: the automatic repair of {where} waits: {busy}")
            return
        stale = ReviewRunner(self.coord).recheck(feature.id, entry)  # fresh reads; a failed read raises (deferred)
        if stale:
            report.attention.append(f"{feature.id}: review {entry['key']} no longer applies to {where}, so it is not "
                                    f"repaired: {sanitize('; '.join(stale), 400)}")
            return
        if not self.client.get_pull_request(cand.number).draft:  # a person may have marked it ready meanwhile
            self._stop(feature, entry, report, f"automatic repair of {where} is not possible: PR #{cand.number} is "
                                               "marked ready for review (v1 repairs a Draft PR); an operator decides")
        self._start(feature, entry, blocking, used, report)

    def _ineligible(self, feature: Feature, entry: Mapping[str, Any], blocking: list[dict[str, Any]]) -> str:
        """Why this feature can never be repaired automatically as it stands ('' : it can)."""
        cand = feature.pull_request
        assert cand is not None
        if not blocking:
            return "the REQUEST_CHANGES review has no blocking finding"
        if (entry.get("comment") or {}).get("status") == "posted" \
                and review_evidence(self.store, feature.id, int(cand.number or 0), cand.head_sha) is None:
            return f"review {entry['key']} is not intact evidence (its stored binding does not match its digest)"
        tasks = self.store.load_tasks(feature)
        roles = sorted({t.role for t in tasks.values()})
        if len(roles) > 1:
            return (f"the feature has tasks of several roles ({', '.join(roles)}): which role owns each finding is an "
                    "operator's decision (v1 repairs single-role features)")
        if len(tasks) != 1:
            return f"the feature has {len(tasks)} tasks ({', '.join(sorted(tasks))}): v1 repairs a single-task feature"
        task = next(iter(tasks.values()))
        intake = load_intake(self.store, feature.id)
        problems = ((not cand.draft, f"PR #{cand.number} is marked ready for review: v1 repairs a Draft PR"),
                    (intake is None or task.id not in intake.all_task_ids, f"{task.id} is not an approved task"),
                    (bool(task.steps), f"{task.id} has steps (W3 checkpoints): v1 repairs a single-step task"),
                    (task.state is not TaskState.VERIFIED, f"{task.id} is {task.state.value}, not verified"),
                    (not self.config.role(task.role).enabled, f"the role {task.role} is disabled"),
                    (bool(load_scheduling(self.store, task).attention), f"{task.id} needs an operator (attention)"))
        return next((why for bad, why in problems if bad), "")

    def _busy(self, feature: Feature, entry: Mapping[str, Any]) -> str:
        """Why no attempt can start NOW (it resolves by itself or through another step) ('' : it can)."""
        cand = feature.pull_request
        assert cand is not None
        if (entry.get("comment") or {}).get("status") != "posted":
            return "the review's PR comment is not posted yet"
        claims = sorted(self.store.list_claims())
        flights = [t for t in self.store.load_tasks(feature)
                   if (self.store.load_record("scheduling", t) or {}).get("in_flight")]
        stage = execution_owner(self.store)[1]  # W4.7 M6: never beside an integration stage
        problems = [
            (claims or flights or stage) and f"the checkout's execution slot is busy (claims {claims}, launches in "
                                             f"flight {flights}{'; ' if stage else ''}{stage})",
            self._reviewer_running() and "a reviewer process runs",
            cand.attention and f"publication is stopped ({cand.attention.get('reason')})"]
        if not any(problems):
            problems += [self.publisher._repository_problem(feature),  # pylint: disable=protected-access
                         self.publisher._head_problem(feature, cand),  # pylint: disable=protected-access
                         self.git.current_branch() != feature.branch and f"the checkout is not on {feature.branch}",
                         bool(self.coord.foreign_changes()) and "the shared checkout has uncommitted changes"]
        return next((str(p) for p in problems if p), "")

    def _reviewer_running(self) -> bool:
        for raw in self.store.list_records("review"):
            running = next((e.get("running") for e in raw.get("entries", []) if e.get("key") == raw.get("current")),
                           None) or {}
            if running.get("tag") and attempt_processes(running)[0] != "gone":
                return True
        return False

    def _start(self, feature: Feature, entry: Mapping[str, Any], blocking: list[dict[str, Any]], used: int,
               report: RepairStepReport) -> None:
        """Count the attempt (write-ahead), then re-open the task — under the publication and review locks."""
        held = []
        try:
            for lock in (self.store.locks.publish(), self.store.locks.review()):
                lock.acquire()
                held.append(lock)
            task = next(iter(self.store.load_tasks(feature).values()))
            attempt = self._count(feature, entry, blocking, task, used)
            self._fault(f"crash-after-attempt-counted:{feature.id}")
            self._reopen(feature, attempt)
        finally:
            for lock in reversed(held):
                lock.release()
        report.events.append(
            f"{feature.id}: repair attempt {attempt['number']} of {self.cfg.max_attempts} started for review "
            f"{entry['key']} ({len(blocking)} blocking finding(s)) of PR #{attempt['pr']} at "
            f"{attempt['review_head'][:12]}: {task.id} re-opened; the Worker runs its agent next")

    def _count(self, feature: Feature, entry: Mapping[str, Any], blocking: list[dict[str, Any]], task: Task,
               used: int) -> dict[str, Any]:
        """The attempt, recorded BEFORE anything runs, after the local facts were re-read in the same transaction."""
        cand = feature.pull_request
        assert cand is not None
        with self.store.transaction():
            fresh = self.store.load_feature(feature.id)
            now, review = fresh.pull_request, load_review(self.store, feature.id)
            record = load_repair(self.store, feature.id) or RepairRecord(id=feature.id, issue_number=int(
                load_intake(self.store, feature.id).issue_number))  # type: ignore[union-attr]
            changed = [why for bad, why in (
                (fresh.state is not FeatureState.REVIEW, f"the feature is {fresh.state.value}"),
                (now is None or (now.number, now.head_sha) != (cand.number, cand.head_sha), "the candidate changed"),
                (review is None or review.current != entry["key"], "the current review changed"),
                (self.store.load_task(task.id).state is not TaskState.VERIFIED, f"{task.id} changed"),
                (bool(self.store.list_claims()), "a claim started"),
                (record.open_attempt() is not None or record.for_review(entry["key"]) is not None
                 or len(record.attempts) != used, "another attempt was recorded")) if bad]
            if changed:
                raise CoordinatorError(f"{feature.id} changed while its repair was being started "
                                       f"({'; '.join(changed)})")
            attempt = {"id": f"a{used + 1}-{secrets.token_hex(4)}", "number": used + 1, "status": "started",
                       "review": entry["key"], "review_head": cand.head_sha, "pr": cand.number, "task": task.id,
                       "role": task.role, "findings": blocking, "findings_sha256": findings_digest(blocking),
                       "started_at": utc_now(), "reopened_at": "", "launches": 0, "verified_head": "",
                       "verified_at": "", "pushing": None, "pushed": None, "revalidation": None, "finished_at": "",
                       "reason": "", "actor": "worker"}
            record.attempts.append(attempt)
            record.stopped, record.updated_at = None, utc_now()
            self.store.save_record("repair", record)
            self.store.append_event("repair-started", feature=feature.id, attempt=attempt["id"], review=entry["key"],
                                    pr=cand.number, head=cand.head_sha, task=task.id, findings=len(blocking))
        return attempt

    def _reopen(self, feature: Feature, attempt: Mapping[str, Any]) -> None:
        """verified → ready (workflow.reopen_task), then `reopened_at`. A crash between both: re-opened next step."""
        task = self.store.load_task(attempt["task"])
        if task.state is TaskState.VERIFIED:
            self.coord.reopen_task(task.id, f"W4.8.1 repair attempt {attempt['number']}: review {attempt['review']} "
                                            f"of PR #{attempt['pr']} requested changes")
        self._fault(f"crash-after-reopen:{feature.id}")
        self._update(feature.id, attempt["id"], reopened_at=utc_now())

    # -- an attempt in progress ---------------------------------------------------------------------------------------

    def _continue(self, feature: Feature, attempt: dict[str, Any], report: RepairStepReport) -> None:
        if feature.state is not FeatureState.REVIEW:
            self._end(feature, attempt, "failed", f"the feature is {feature.state.value}, no longer in review", report)
        if attempt["status"] == "started" and not self._agent_done(feature, attempt, report):
            return
        attempt = self._attempt(feature.id, attempt["id"])
        if attempt["status"] in ("verified", "pushed") and not self._settled(feature, attempt, report):
            return
        if attempt["status"] == "verified":
            self._push(feature, attempt, report)
            attempt = self._attempt(feature.id, attempt["id"])
        if attempt["status"] == "pushed":
            self._fault(f"crash-before-revalidate:{feature.id}")
            self._revalidate(feature, attempt, report)

    def _settled(self, feature: Feature, attempt: Mapping[str, Any], report: RepairStepReport) -> bool:
        """A `verified` or `pushed` attempt advances (push, revalidation) only while its task's launch is settled and
        needs no operator, and its record is consistent (the task verified at exactly the recorded head); an
        inconsistent record fails closed (`failed`). False: wait or stop, nothing pushed or revalidated."""
        label = f"{feature.id}: repair attempt {attempt['number']} of PR #{attempt['pr']} ({attempt['status']})"
        task = self.store.load_task(attempt["task"])
        rec = load_scheduling(self.store, task)
        if self.store.load_claim(task.id) is not None or rec.in_flight:
            report.waiting.append(f"{label}: a launch of {task.id} is not settled yet; nothing is pushed or "
                                  "revalidated until the Worker settles it")
            return False
        if rec.attention:
            report.attention.append(
                f"{label} stopped: {task.id} is held for an operator ({sanitize(rec.attention.get('reason'), 300)}). "
                f"Nothing is pushed or revalidated; the attempt stays open: after inspecting it, `python -m "
                f"feature_coordinator worker reset-retries {task.id} --apply` lets it continue; `python -m "
                f"feature_coordinator github repair abandon {feature.id} --reason … --apply` ends it")
            return False
        head, evidence = attempt.get("verified_head") or "", task.evidence.head_sha if task.evidence else ""
        if task.state is not TaskState.VERIFIED or not head or evidence != head:
            self._end(feature, attempt, "failed", f"the attempt is recorded {attempt['status']} at {head[:12] or '?'}, "
                                                  f"but {task.id} is {task.state.value} (evidence at "
                                                  f"{evidence[:12] or 'none'}): an inconsistent record; nothing is "
                                                  "pushed or revalidated", report)
        return True

    def _agent_done(self, feature: Feature, attempt: dict[str, Any], report: RepairStepReport) -> bool:
        """True once the task was verified again at a new head continuing the PR's (the attempt is then verified)."""
        label = f"{feature.id}: repair attempt {attempt['number']} of PR #{attempt['pr']}"
        task = self.store.load_task(attempt["task"])
        rec, claim = load_scheduling(self.store, task), self.store.load_claim(task.id)
        if (not attempt.get("reopened_at") or task.state is not TaskState.VERIFIED) and claim is None \
                and not rec.in_flight:  # its agent does not run (yet): a changed PR or review ends the attempt
            self._moot(feature, attempt, report)
        if not attempt.get("reopened_at"):  # interrupted after it was counted
            self._reopen(feature, attempt)
            report.waiting.append(f"{label}: {attempt['task']} re-opened; the Worker runs its agent next")
            return False
        if task.state is not TaskState.VERIFIED:
            status = task_status(task, rec, claim, self.clock())
            if status == "needs-attention":
                why = (rec.attention or {}).get("reason") or (task.history[-1].reason if task.history else "")
                used, budget = int(attempt["launches"]), self.cfg.max_launches_per_attempt
                again = (f"`python -m feature_coordinator worker reset-retries {task.id} --apply` lets its agent try "
                         f"again ({used} of {budget} launch(es) used); " if used < budget else
                         f"its launches are used up ({used} of {budget}; never refunded); ")
                report.attention.append(
                    f"{label} stopped: {task.id} needs an operator ({sanitize(why, 300)}). The attempt stays open: "
                    f"{again}`python -m feature_coordinator github repair abandon {feature.id} --reason … --apply` "
                    "ends it")
            else:
                report.waiting.append(f"{label}: {task.id} is {status} (the Worker runs its agent)")
            return False
        if claim is not None or rec.in_flight:  # verified, but the Worker has not settled its launch yet
            report.waiting.append(f"{label}: {task.id} is verified; the Worker settles its launch first")
            return False
        if rec.attention:  # the Worker's own judgement of the launch overrides the task's verification
            self._moot(feature, attempt, report)  # but a PR merged or closed meanwhile ends the attempt (publication)
            report.attention.append(
                f"{label} stopped: {task.id} is verified, but the Worker's outcome of its launch needs an operator "
                f"({sanitize(rec.attention.get('reason'), 300)}). Nothing is pushed; the attempt stays open: after "
                f"inspecting it, `python -m feature_coordinator worker reset-retries {task.id} --apply` accepts the "
                f"verified result; `python -m feature_coordinator github repair abandon {feature.id} --reason … "
                "--apply` ends it")
            return False
        head = task.evidence.head_sha if task.evidence else ""
        local = self.git.rev_parse(f"refs/heads/{feature.branch}")
        if head == attempt["review_head"]:
            self._end(feature, attempt, "no-changes", f"the repair agent changed nothing: {task.id} was verified again "
                                                      f"at the reviewed head {head[:12]}", report)
        if head != local or not self.git.is_ancestor(attempt["review_head"], head):
            self._end(feature, attempt, "failed", f"{task.id} was verified at {head[:12]}, not at the feature branch's "
                                                  f"continuation of {attempt['review_head'][:12]} (now {local[:12]})",
                      report)
        foreign = self._foreign(task, attempt["review_head"], head)
        if foreign:
            self._end(feature, attempt, "failed", f"{attempt['review_head'][:12]}..{head[:12]} is not only {task.id}'s "
                                                  f"own work: {foreign}; nothing is pushed", report)
        self._update(feature.id, attempt["id"], event="repair-verified", status="verified", verified_head=head,
                     verified_at=utc_now())
        self._fault(f"crash-after-verified:{feature.id}")
        report.events.append(f"{label}: {task.id} verified again at {head[:12]} (its required gates passed)")
        return True

    def _foreign(self, task: Task, old: str, new: str) -> str:
        """Why the commits old..new are not all the task's own work, checked BEFORE they are pushed: plain commits made
        by the Git coordinator for this task, changing only paths its role may write ('' : they are)."""
        commits, merges = self.git.commits_between(old, new), self.git.merge_commits(old, new)
        others = [c[:12] for c in commits if self.git.trailers(c).get("Feature-Task") != [task.id]]
        touched = {p for c in commits if c not in merges for p in self.git.touched_paths(f"{c}^", c)}  # each commit's
        outside = sorted(p for p in touched if not self.coord.policy.check(task.role, p, grants=task.grants).allowed)
        return "; ".join(why for bad, why in (
            (merges, f"merge commit(s) {[m[:12] for m in merges]}"),
            (others, f"commit(s) {others} not made by the Git coordinator for {task.id}"),
            (outside, f"path(s) {task.role} may not write: {[sanitize(p, 120) for p in outside[:10]]}")) if bad)

    def _stale(self, feature: Feature, attempt: Mapping[str, Any]) -> str:
        """Why the attempt's findings no longer apply ('' : they still do): the recorded candidate, the current review
        and the PR — re-read by number: open, a draft, at the reviewed head."""
        cand = self.store.load_feature(feature.id).pull_request
        local = next((why for bad, why in (
            (cand is None or (cand.number, cand.head_sha) != (attempt["pr"], attempt["review_head"]),
             "the recorded PR candidate changed"),
            (True, self._review_gone(feature.id, attempt))) if bad and why), "")
        if local:
            return local
        try:
            pr = self.client.get_pull_request(attempt["pr"])
        except GitHubNotFoundError:
            return f"PR #{attempt['pr']} no longer exists"
        identity = self.publisher._pr_problem(feature, dataclasses.replace(cand, head_sha=pr.head_sha), pr)  # pylint: disable=protected-access
        return next((why for bad, why in (
            (bool(identity), f"PR #{attempt['pr']} is not the recorded PR any more ({identity})"),
            (pr.merged or pr.state != "open", f"PR #{attempt['pr']} was {'merged' if pr.merged else pr.state} by a "
                                              "person (publication completes a verified merge)"),
            (not pr.draft, f"PR #{attempt['pr']} was marked ready for review"),
            (pr.head_sha != attempt["review_head"], f"PR #{attempt['pr']} moved to {pr.head_sha[:12]} (someone "
                                                    f"pushed): the findings of {attempt['review_head'][:12]} no "
                                                    "longer apply")) if bad), "")

    def _binding_problem(self, feature: Feature, attempt: Mapping[str, Any]) -> str:
        """Why the answered review's binding no longer holds for the REVIEWED head ('' : it holds): ReviewRunner's own
        binding, re-derived now for the PR at the reviewed head — the base branch fetched, the merge base, the approval,
        the plan — must equal the entry's (the local branch may already be at the repaired head). A failed read
        raises (the step or the launch waits)."""
        review, intake = load_review(self.store, feature.id), load_intake(self.store, feature.id)
        entry = review.entry(attempt["review"]) if review else None
        if entry is None or intake is None:
            return f"review {attempt['review']} or the approval record is missing"
        try:
            pr = self.client.get_pull_request(attempt["pr"])
        except GitHubNotFoundError:
            return f"PR #{attempt['pr']} no longer exists"
        binding, problems = ReviewRunner(self.coord)._bind(  # pylint: disable=protected-access
            feature, intake, dataclasses.replace(pr, head_sha=attempt["review_head"]), True)
        changed = [k for k in BINDING_KEYS if entry["binding"].get(k) != binding.get(k)]
        why = "; ".join(([f"changed: {', '.join(changed)}"] if changed else []) + problems)
        return (f"review {attempt['review']} no longer holds for the reviewed head {attempt['review_head'][:12]} "
                f"({sanitize(why, 400)}): its findings are not acted on; the PR and its commits are kept — an operator "
                f"decides (e.g. `python -m feature_coordinator github review retry {feature.id} --reason … --apply`)"
                if why else "")

    def _review_gone(self, feature_id: str, attempt: Mapping[str, Any]) -> str:
        """Why the review the attempt answers is no longer the current, complete review ('' : it still is). Only an
        operator's `github review run` / `retry` or a withdrawn approval changes it while an attempt is open."""
        review = load_review(self.store, feature_id)
        entry = review.entry(attempt["review"]) if review else None
        if review is None or review.current != attempt["review"] or entry is None or entry["status"] != "complete":
            return (f"review {attempt['review']} is no longer the current, complete review "
                    f"({(entry or {}).get('status', 'missing')}; an operator's review run?)")
        return ""

    def _moot(self, feature: Feature, attempt: Mapping[str, Any], report: RepairStepReport) -> None:
        """End the attempt while its agent is not running when its findings no longer apply (`_stale`): nothing is
        (re)started or pushed."""
        problem = self._stale(feature, attempt)
        if problem:
            self._end(feature, attempt, "failed", f"{problem}; its agent is not started again and nothing is pushed",
                      report)

    def _push_problem(self, feature: Feature, attempt: Mapping[str, Any]) -> str:
        """Why the verified head may not be pushed now, from fresh reads ('' : it may): the recorded candidate, the
        approval, the PR (the recorded one, open, a draft, at the reviewed or already the repaired head), and every
        commit of the range the task's own (also for an attempt recorded `verified` by an earlier build)."""
        head, old = attempt["verified_head"], attempt["review_head"]
        cand = self.store.load_feature(feature.id).pull_request
        if cand is None or (cand.number, cand.head_sha) != (attempt["pr"], old):
            return "the recorded PR candidate changed during the repair"
        foreign = self._foreign(self.store.load_task(attempt["task"]), old, head)
        if foreign:
            return f"{old[:12]}..{head[:12]} is not only {attempt['task']}'s own work: {foreign}"
        intake = load_intake(self.store, feature.id)
        assert intake is not None
        withdrawn = next((p for p in (known_problem(self.store, self.store.load_task(t)) for t in intake.all_task_ids)
                          if p), None) or issue_problem(self.client, intake)[1]
        if withdrawn:
            return f"the approval no longer holds: {withdrawn}"
        try:
            pr = self.client.get_pull_request(cand.number)
        except GitHubNotFoundError:
            return f"PR #{cand.number} no longer exists"
        problem = self.publisher._pr_problem(feature, dataclasses.replace(cand, head_sha=pr.head_sha), pr)  # pylint: disable=protected-access
        return next((why for bad, why in (
            (problem or pr.state != "open" or pr.merged, f"PR #{cand.number} is not the recorded open PR "
                                                         f"({problem or ('merged' if pr.merged else pr.state)})"),
            (not pr.draft, f"PR #{cand.number} was marked ready for review during the repair"),
            (pr.head_sha not in (old, head), f"PR #{cand.number} is at {pr.head_sha[:12]}, neither the reviewed "
                                             f"{old[:12]} nor the repaired {head[:12]}: someone else pushed"),
            (True, self.publisher._origin(feature)[1] or "")) if bad and why), "") \
            or self._binding_problem(feature, attempt)  # pylint: disable=protected-access

    def _push(self, feature: Feature, attempt: dict[str, Any], report: RepairStepReport) -> None:
        """Push exactly the verified head to the PR's branch (a fast-forward), after fresh reads. Publication and the
        review are locked out from the review's last check through the push (an operator's review run: deferred)."""
        head, old = attempt["verified_head"], attempt["review_head"]
        problem = self._push_problem(feature, attempt)
        if problem:
            self._end(feature, attempt, "failed", f"{problem}; nothing is pushed", report)
        if attempt.get("pushing"):  # a push began earlier and its result was never recorded (a crash, a lost answer)
            live = self.git.remote_head(feature.branch, timeout=self.config.github.publish.git_timeout_seconds)
            if live != head:
                self._end(feature, attempt, "failed", (
                    f"a push of {head[:12]} began at {attempt['pushing'].get('at')} but was never confirmed, and "
                    f"origin has {feature.branch} at {(live or 'nothing')[:12]}: an interrupted push and an "
                    f"operator's rewind cannot be told apart, so it is not repeated. Push {head[:12]} yourself and run "
                    f"`python -m feature_coordinator github publish revalidate {feature.id}`, or leave the branch as "
                    "it is (the PR, the commits and every record are kept)"), report)
        held = []
        try:
            for lock in (self.store.locks.publish(), self.store.locks.review()):  # LockBusyError: deferred
                held.append(lock.acquire())
            gone = self._review_gone(feature.id, attempt)
            if gone:
                self._end(feature, attempt, "failed", f"{gone}; nothing is pushed", report)
            result = self.git.push_branch(  # the write-ahead marker goes right before the network push itself
                feature.branch, head, destination=self.publisher._origin(feature)[0],  # pylint: disable=protected-access
                timeout=self.config.github.publish.git_timeout_seconds, expect=(old, head),
                protected=(*PROTECTED_BRANCHES, feature.base_branch, self.config.base_branch),
                on_push=lambda: self._update(feature.id, attempt["id"], pushing={"sha": head, "at": utc_now()}))
        except (RemoteRejectedError, HeadMismatchError, ScopeViolationError) as exc:
            self._end(feature, attempt, "failed", f"push refused: {exc}", report)
        except GitOperationError as exc:  # an unclean checkout, a busy Git lock: retried by the next step
            if isinstance(exc, GitConfigExecutionError):
                raise
            report.deferred.append(f"{feature.id}: repair attempt {attempt['number']}: not pushed yet: "
                                   f"{sanitize(exc, 300)}")
            return
        finally:
            for lock in reversed(held):
                lock.release()
        self._fault(f"crash-after-push:{feature.id}")
        self._update(feature.id, attempt["id"], event="repair-pushed", status="pushed", pushed={
            "sha": result.sha, "before": result.before, "pushed": result.pushed, "at": utc_now()})
        report.events.append(f"{feature.id}: repair attempt {attempt['number']}: pushed {head[:12]} to "
                             f"{feature.branch} (PR #{attempt['pr']}; a fast-forward of {old[:12]})" if result.pushed
                             else f"{feature.id}: repair attempt {attempt['number']}: origin already has {head[:12]}")

    def _revalidate(self, feature: Feature, attempt: dict[str, Any], report: RepairStepReport) -> None:
        """revalidate.py at exactly the pushed head; the candidate moves only if it validates."""
        head = attempt["verified_head"]
        result = PullRequestRevalidator(self.coord).run(feature.id, apply=True, expected_head=head,
                                                        actor=f"worker repair {attempt['id']}")
        self._fault(f"crash-after-revalidate:{feature.id}")
        cand = self.store.load_feature(feature.id).pull_request
        if result.status == "revalidated" or (result.status == "unchanged" and cand and cand.head_sha == head):
            record = load_revalidation(self.store, feature.id)  # the finalized operation that validated exactly head
            op = next((e["op"] for e in reversed(record.entries if record else []) if e["status"] == "validated"
                       and (e["from_head"], e["to_head"]) == (attempt["review_head"], head)), "")
            if not op:
                self._end(feature, attempt, "failed", self._unfinished(feature, attempt), report)
            self._update(feature.id, attempt["id"], event="repair-completed", status="completed",
                         revalidation=op, finished_at=utc_now(),
                         reason=f"PR #{attempt['pr']} revalidated at {head[:12]}")
            report.events += [*result.lines, f"{feature.id}: repair attempt {attempt['number']} completed: PR "
                                             f"#{attempt['pr']} is at {head[:12]}; a new independent AI review of it "
                                             "is required (the review step starts it)"]
            report.deferred += result.deferred
            return
        if result.status == "deferred":
            report.deferred += result.deferred
            return
        pushed_at = epoch((attempt.get("pushed") or {}).get("at")) or 0.0
        if result.status == "stale" and result.to_head in ("", attempt["review_head"], head) \
                and time.time() - pushed_at < STALE_GRACE_SECONDS:
            report.waiting.append(f"{feature.id}: repair attempt {attempt['number']}: PR #{attempt['pr']} does not "
                                  f"show {head[:12]} on GitHub yet; it is revalidated by a later step")
            return
        reason = "; ".join(result.attention or result.deferred) or result.status
        self._end(feature, attempt, "failed", f"the revalidation of {head[:12]} ended {result.status}: {reason}. "
                                              f"The PR shows {head[:12]} while the candidate stays at "
                                              f"{attempt['review_head'][:12]}; publication stays stopped until an "
                                              "operator fixes it and runs `python -m feature_coordinator github "
                                              f"publish revalidate {feature.id}`", report)

    def _unfinished(self, feature: Feature, attempt: Mapping[str, Any]) -> str:
        """The diagnostic when the candidate is at the pushed head without a finalized revalidation of it."""
        head, old = attempt["verified_head"], attempt["review_head"]
        record = load_revalidation(self.store, feature.id)
        running = [e["op"] for e in (record.entries if record else []) if e["status"] == "running"
                   and (e["from_head"], e["to_head"]) == (old, head)]
        evidence = self.store.load_feature(feature.id).integration_evidence
        gates = bool(evidence and evidence.head_sha == head and evidence.gates
                     and all(g.passed for g in evidence.gates))
        cut = f": revalidation {running[0]} was cut short inside its final transaction and stays `running`"
        return (f"the candidate is at {head[:12]}, but no finalized revalidation of {old[:12]} → {head[:12]} is "
                f"recorded{cut if running else ''}. `github publish revalidate` cannot finish it: for the current "
                "candidate it reports `unchanged`. The attempt is not completed. "
                + (f"The feature's integration evidence shows every gate passed at {head[:12]} (it was saved with the "
                   "candidate), and the review step reviews it as a new head. An operator checks `python -m "
                   f"feature_coordinator github publish status {feature.id}` and, satisfied, leaves it (the next "
                   "revalidation marks the record `interrupted`)" if gates else
                   f"The feature has no passing gate evidence for {head[:12]}: treat it as unvalidated — no supported "
                   "command re-validates the current candidate, so an operator runs the feature's gates at it by hand, "
                   "or closes the PR, before any person merges it"))

    # -- endings and reports ------------------------------------------------------------------------------------------

    def _end(self, feature: Feature, attempt: Mapping[str, Any], status: str, reason: str,
             report: RepairStepReport) -> NoReturn:
        reason = sanitize(reason, 900)
        self._update(feature.id, attempt["id"], event="repair-ended", status=status, finished_at=utc_now(),
                     reason=reason)
        report.attention.append(f"{feature.id}: repair attempt {attempt['number']} ended ({status}): {reason}")
        raise _Stopped(reason)

    def _ended(self, feature: Feature, attempt: Mapping[str, Any], report: RepairStepReport) -> None:
        """The current review was already answered by *attempt*: a failed or empty attempt still needs an operator."""
        if attempt["status"] in ("failed", "no-changes"):
            report.attention.append(f"{feature.id}: repair attempt {attempt['number']} ended ({attempt['status']}): "
                                    f"{attempt['reason']}; automatic repair does not try this review again")

    def _stop(self, feature: Feature, entry: Mapping[str, Any], report: RepairStepReport, reason: str) -> NoReturn:
        """Automatic repair stops for this review: NEEDS_ATTENTION, recorded (and its event written) once."""
        reason = sanitize(reason, 1200)
        report.attention.append(f"{feature.id}: {reason}")
        with self.store.transaction():
            record = load_repair(self.store, feature.id) or RepairRecord(id=feature.id, issue_number=int(
                load_intake(self.store, feature.id).issue_number))  # type: ignore[union-attr]
            if (record.stopped or {}).get("review") != entry["key"] or record.stopped.get("reason") != reason:
                record.stopped = {"review": entry["key"], "reason": reason, "at": utc_now()}
                record.updated_at = utc_now()
                self.store.save_record("repair", record)
                self.store.append_event("repair-stopped", feature=feature.id, review=entry["key"],
                                        reason=sanitize(reason, 300))
        raise _Stopped(reason)

    def _attempt(self, feature_id: str, attempt_id: str) -> dict[str, Any]:
        record = load_repair(self.store, feature_id)
        assert record is not None
        return next(a for a in record.attempts if a["id"] == attempt_id)

    def _update(self, feature_id: str, attempt_id: str, *, event: str = "", **changes: Any) -> dict[str, Any]:
        with self.store.transaction():
            record = load_repair(self.store, feature_id)
            assert record is not None
            attempt = next(a for a in record.attempts if a["id"] == attempt_id)
            attempt.update(changes)
            record.updated_at = utc_now()
            self.store.save_record("repair", record)
            if event:
                self.store.append_event(event, feature=feature_id, attempt=attempt_id, **{
                    k: sanitize(v, 300) if isinstance(v, str) else v for k, v in changes.items()
                    if k in ("status", "verified_head", "reason", "revalidation")})
        return attempt


# -- the operator's commands (repair_commands.py) ----------------------------------------------------------------------

def abandon(store: Any, feature_id: str, *, reason: str, actor: str, apply: bool) -> list[str]:
    """An operator ends the feature's open attempt (`abandoned`, with who and why). Refused while its agent runs or a
    launch is in flight. Nothing else changes: the task, the branch, the PR and every record stay as they are, and
    publication resumes its own checks (a branch the agent moved stops it, as a person's push would)."""
    with store.locks.repair().acquire():
        record = load_repair(store, feature_id)
        attempt = record.open_attempt() if record else None
        if attempt is None:
            raise CoordinatorError(f"{feature_id} has no open repair attempt")
        flight = (store.load_record("scheduling", attempt["task"]) or {}).get("in_flight")
        if store.load_claim(attempt["task"]) or flight:
            raise CoordinatorError(f"the agent of repair attempt {attempt['number']} runs ({attempt['task']} is "
                                   "claimed or its launch is in flight): stop it first (`worker pause`, then wait, or "
                                   "`worker stop`)")
        lines = [f"{feature_id}: repair attempt {attempt['number']} ({attempt['status']}, review {attempt['review']}, "
                 f"task {attempt['task']} {store.load_task(attempt['task']).state.value})"]
        if not apply:
            return [*lines, "dry run — pass --apply to abandon it"]
        with store.transaction():  # the same checks again, in the transaction the service's launch write-ahead uses
            record = load_repair(store, feature_id)
            assert record is not None
            current = record.open_attempt()
            if current is None or current["id"] != attempt["id"]:
                raise CoordinatorError(f"{feature_id}'s open repair attempt changed; run it again")
            if store.load_claim(attempt["task"]) or (store.load_record("scheduling", attempt["task"]) or {}).get(
                    "in_flight"):
                raise CoordinatorError(f"the agent of repair attempt {attempt['number']} was just launched: stop it "
                                       "first")
            current.update(status="abandoned", finished_at=utc_now(), actor=actor,
                           reason=sanitize(f"abandoned by {actor}: {reason}", 600))
            record.updated_at = utc_now()
            store.save_record("repair", record)
            store.append_event("repair-abandoned", feature=feature_id, attempt=current["id"], actor=actor,
                               reason=sanitize(reason, 300))
    return [*lines, f"{feature_id}: repair attempt {attempt['number']} abandoned; automatic repair does not try "
                    f"review {attempt['review']} again"]


def launch_refusal(coord: Any, task: Task, launches: int, per_task: int) -> Outcome | None:
    """Right before the Worker service launches *task*: None, or the autonomy.Outcome that refuses the launch.

    - Not a due repair agent: `worker.max_launches_per_task` against the task's scheduling *launches*, exactly as
      before.
    - A due repair agent: github.repair enabled, the attempt's OWN launches against
      `github.repair.max_launches_per_attempt`, then its PR and review re-read now (`launch_problem`: an attempt
      whose PR or review changed ends there, and nothing starts)."""
    attempt = agent_attempt(coord.store, load_repair(coord.store, task.feature_id))
    if attempt is None or attempt["task"] != task.id:
        exhausted = launches >= per_task
        return Outcome(CRITICAL, f"launch budget exhausted ({launches} of {per_task})") if exhausted else None
    cfg, label = coord.config.github.repair, f"repair attempt {attempt['number']}"
    if not cfg.enabled:
        return Outcome(CANCELLED, f"github.repair.enabled is false: {label} starts no agent (`github repair abandon` "
                                  "ends it)")
    if int(attempt["launches"]) >= cfg.max_launches_per_attempt:
        return Outcome(CRITICAL, f"launch budget exhausted ({attempt['launches']} of {cfg.max_launches_per_attempt}) "
                                 f"for {label}")
    try:
        problem = launch_problem(coord, task)
    except CoordinatorError as exc:
        return Outcome(TRANSIENT, f"{label}: its PR could not be re-read before the launch: {exc}")
    return Outcome(CANCELLED, f"{label} ended before its agent started: {problem}") if problem else None


def launch_problem(coord: Any, task: Task) -> str:
    """Right before the Worker launches *task*'s repair agent (repair_record.launch_refusal): why its findings no
    longer apply (`RepairStep._stale`, the PR re-read now) — the attempt then ends (`failed`, under
    github-repair.lock) and nothing starts — or '' (it may start). A GitHub failure raises (the launch waits)."""
    step = RepairStep(coord)
    record = load_repair(coord.store, task.feature_id)
    attempt = record.open_attempt() if record else None
    if attempt is None or attempt["task"] != task.id or attempt["status"] != "started":
        return "no repair attempt of it is open"
    feature = coord.store.load_feature(task.feature_id)
    problem = step._stale(feature, attempt) or step._binding_problem(feature, attempt)  # pylint: disable=protected-access
    if not problem:
        return ""
    with coord.store.locks.repair().acquire():  # LockBusyError: an operator's command runs; the launch waits
        if step._attempt(feature.id, attempt["id"])["status"] == "started":  # pylint: disable=protected-access
            step._update(feature.id, attempt["id"], event="repair-ended", status="failed",  # pylint: disable=protected-access
                         finished_at=utc_now(), reason=sanitize(f"{problem}; its agent was not started", 900))
    return problem


def repair_rows(store: Any) -> list[dict[str, Any]]:
    """Each repair record's latest attempt and stop (read-only, local)."""
    rows = []
    for raw in store.list_records("repair"):
        record = RepairRecord.from_dict(raw)
        last = record.attempts[-1] if record.attempts else None
        rows.append({"feature": record.id, "issue": record.issue_number, "attempts": len(record.attempts),
                     "open": bool(record.open_attempt()), "status": last["status"] if last else None,
                     "attempt": last["id"] if last else None, "review": last["review"] if last else None,
                     "head": (last.get("verified_head") or last["review_head"]) if last else None,
                     "reason": (last or {}).get("reason") or "", "stopped": record.stopped})
    return rows
