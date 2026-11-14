"""W4.6.1 (R2): the Desktop Worker reviews each verified Draft PR by itself — asynchronously.

When `github.review.enabled`, the Worker service runs this step right after its publication step, at most every
`github.review.poll_interval_seconds` (service.py `_poll_github`, `github_steps` below). A step holds
`github-review.lock` for seconds and never waits for the model:

  1. WATCH every review whose current attempt runs in a reviewer process (review_process.py). Whether its
     processes live is decided by the attempt's process TAG, never by a recorded PID alone:
       * running, within its deadline (timeout + termination grace + slack) → nothing, no GitHub request;
       * past its deadline → the decision is recorded FIRST, then its processes are stopped (SIGTERM to the
         reviewer process, grace, SIGKILL, then any tagged leftover; the tag re-checked before every signal):
         the attempt is a timeout and stays counted;
       * its feature no longer in review, another PR, or a head the publisher no longer records as verified →
         stopped the same way and the entry invalidated: its result is discarded, nothing is published;
       * the reviewer process ended but processes of its CLI are left (orphaned) → those are stopped;
       * gone → its result is collected through R1's judgement (review.py: given back, refused, validated,
         re-checked against fresh reads, recorded, ONE comment); no result is an interrupted attempt (counted).
  2. START at most one review, only while no reviewer process runs and no agent holds a claim on the checkout:
     the first approved Issue's feature in `review` whose recorded PR is not stopped by publication (`attention`)
     and still needs a review. That is R1's run (fresh binding, the publisher's identity checks, preconditions,
     one review per verified head and generation), except that its attempt — counted BEFORE it starts — runs as
     ONE reviewer process, `python -m feature_coordinator github review exec RUN_DIR`, its tag, identity and
     deadline recorded. Before counting, the reviewer's availability is checked without a model request (the
     CLI, its flags, the subscription login): unavailable → no attempt, no process, and ERROR once it has been
     unavailable for `unavailable_wait_seconds` (R1's rule).

A review complete for the publisher's verified head is not looked at again, ERROR included (no GitHub request,
no model request); neither is one invalidated for that head: a newly verified head, or an operator's
`github review retry`, starts the next. Nothing here merges, approves, marks a PR ready or changes a feature or
task state. With the review disabled the service never runs this step: no record, no process, no GitHub write.

Lifecycle. `worker pause` / `worker stop` cancel a running reviewer through the control file (R1's rule: the
attempt is given back) and no review starts while paused. A graceful service stop asks its reviewer process to
stop and waits for it (`stop_reviews`), so it records its cancellation before systemd's final SIGKILL
(KillMode=mixed) could end it unrecorded. Outside systemd a service crash leaves the reviewer running, tagged and
recorded: the next instance waits for it, collects its result or stops it at its deadline. Under systemd a crash
of the service also ends the reviewer: no result, an interrupted attempt (counted; with `max_attempts: 1` the
review is ERROR and an operator decides, `github review retry`). Reporting (`review_view`) is read-only.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from .errors import CoordinatorError, LockBusyError, StateDirError
from .github_api import sanitize
from .intake import known_problem, load_intake
from .locks import ProcessIdentity
from .model import FeatureState, utc_now
from .review import ReviewReport, ReviewRecord, ReviewRunner, load_review, run_dir_of
from .review_context import ReviewContext
from .review_process import attempt_processes, process_environment, stop_attempt, write_request

WAITING, NEEDS_ATTENTION = "WAITING", "NEEDS_ATTENTION"  # the Worker's states (autonomy.py; it imports this module)
REVIEW_SLACK_SECONDS = 120.0   # the reviewer process's start-up and bookkeeping beyond its timeout and grace
_CHILDREN: dict[str, Any] = {}  # this service's reviewer processes (Popen), polled so that none stays a zombie

# start(argv tail, log file name, environment) -> a started process with .pid and .poll() (worker.cli_spawner)
Starter = Callable[[list[str], str, Mapping[str, str]], Any]


def iso(seconds: float) -> str:
    """ISO-8601 UTC for an epoch timestamp (as autonomy.iso)."""
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reviewer_available(config: Any) -> str:
    """'' when the reviewer could run now — no model request: its CLI with the required flags and the
    subscription login (never an API key) — else why not (the same checks ClaudeCodeReviewer makes first)."""
    from .launchers import probe_runner  # pylint: disable=import-outside-toplevel
    from .native_backend import check_subscription, native_environment  # pylint: disable=import-outside-toplevel
    from .providers import CliRunnerProfile, ProviderKind  # pylint: disable=import-outside-toplevel
    profile = config.providers.get(config.github.review.provider)
    if not isinstance(profile, CliRunnerProfile) or profile.kind is not ProviderKind.CLAUDE_CODE:
        return f"provider {config.github.review.provider!r} is not Claude Code"
    probe = probe_runner(profile)
    if not probe.ok:
        return f"Claude Code is unavailable: {probe.error or probe.missing_flags}"
    auth = check_subscription(ProviderKind.CLAUDE_CODE, probe.resolved, native_environment())
    return "" if auth.ok else sanitize(auth.detail or auth.method, 300)


class WorkerReviewRunner(ReviewRunner):  # pylint: disable=too-few-public-methods
    """R1's run with each attempt in a separate reviewer process (see the module docstring)."""

    background = True

    def __init__(self, coord: Any, *, start: Starter | None, preflight: Callable[[], str] | None = None,  # pylint: disable=too-many-arguments
                 faults: Any = (), clock: Callable[[], float] = time.time,
                 wall: Callable[[], float] = time.time) -> None:
        super().__init__(coord, faults=faults, clock=clock)
        self.start = start
        self.preflight = preflight or (lambda: reviewer_available(self.config))
        self.wall = wall

    def unavailable(self, feature_id: str, key: str, problem: str, report: ReviewReport) -> None:
        """The reviewer cannot run now: no attempt is used and no process starts; the first time is reported (and
        recorded), later checks only wait — until it has been unavailable for `unavailable_wait_seconds`: ERROR."""
        now = self.clock()
        first = not self._now(feature_id, key)["unavailable_since"]
        entry = self._update(feature_id, key, lambda e: e.update(
            unavailable_since=e["unavailable_since"] or {"at": utc_now(), "epoch": now}),
                             event=("review-deferred", {"status": "unavailable", "error": sanitize(problem, 300)})
                             if first else None)
        since = entry["unavailable_since"]
        if now - float(since["epoch"]) >= self.cfg.unavailable_wait_seconds:
            self._complete(feature_id, key, "ERROR", f"the reviewer has been unavailable since {since['at']}: "
                                                     f"{problem}", report)
            self._publish(feature_id, key, report)
            return
        report.status = "deferred"
        if first:
            report.deferred.append(f"{feature_id}: the reviewer is unavailable: {sanitize(problem, 300)} (no attempt "
                                   f"used; checked again every step, ERROR after {self.cfg.unavailable_wait_seconds}s)")

    def _background(self, feature_id: str, key: str, context: ReviewContext, report: ReviewReport) -> None:
        problem = self.preflight()
        if problem:
            self.unavailable(feature_id, key, problem, report)
            return
        if self.start is None:
            report.status = "deferred"
            report.deferred.append(f"{feature_id}: no reviewer process can be started here (only the Worker service "
                                   "starts them)")
            return
        attempt = self._now(feature_id, key)["attempts"] + 1
        tag = f"review-{secrets.token_hex(8)}"
        run = f"attempt-{attempt}-{tag.removeprefix('review-')}"  # its own directory: a given-back attempt runs again
        run_dir = run_dir_of(self.store, key) / run
        deadline = self.wall() + self.cfg.timeout_seconds + self.config.runtime.terminate_grace_seconds \
            + REVIEW_SLACK_SECONDS
        running = {"attempt": attempt, "started_at": utc_now(), "run_dir": str(run_dir), "tag": tag,
                   "deadline": iso(deadline), "deadline_epoch": deadline, "pid": None, "process": None,
                   "watchdog": None}
        self._update(feature_id, key, lambda e: e.update(attempts=attempt, status="running", running=running),
                     event=("review-process-starting", {"attempt": attempt, "tag": tag}))  # counted BEFORE it starts
        self._fault(f"crash-after-attempt-counted:{feature_id}")
        write_request(run_dir, key=key, attempt=attempt, tag=tag, feature=feature_id, context=context,
                      timeout_seconds=self.cfg.timeout_seconds)
        try:
            handle = self.start(["github", "review", "exec", str(run_dir)], f"review-{key}.{run}.log",
                                process_environment(tag))
        except OSError as exc:  # nothing started, no model request: the attempt is given back
            self._update(feature_id, key, lambda e: (e["attempt_log"].append({
                "attempt": attempt, "status": "not-started", "error": sanitize(exc, 300), "ended_at": utc_now()}),
                e.update(attempts=attempt - 1, running=None, status="pending")),
                         event=("review-deferred", {"status": "not-started", "error": sanitize(exc, 300)}))
            report.status = "deferred"
            report.deferred.append(f"{feature_id}: the reviewer process could not be started: {sanitize(exc, 300)}")
            return
        _CHILDREN[tag] = handle
        self._fault(f"crash-after-reviewer-started:{feature_id}")
        identity = ProcessIdentity.of_pid(handle.pid)
        self._update(feature_id, key, lambda e: e["running"].update(
            pid=handle.pid, process=identity.to_dict() if identity else None),
                     event=("review-process-started", {"attempt": attempt, "pid": handle.pid}))
        report.status = "running"
        report.lines.append(f"{feature_id}: review {key}: attempt {attempt} runs in reviewer process {handle.pid} "
                            f"(deadline {iso(deadline)}); a later step collects its result")


@dataclass
class ReviewStepReport:
    """What one service step did. `lines`: what happened (logged once); `view`: the reviews as they stand now."""

    events: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    attention: list[str] = field(default_factory=list)   # processes that could not be stopped
    started: list[str] = field(default_factory=list)
    view: list[dict[str, Any]] = field(default_factory=list)

    def lines(self) -> list[str]:
        """The service's log lines."""
        return [*self.events, *(f"deferred: {sanitize(d, 300)}" for d in self.deferred),
                *(f"NEEDS ATTENTION: {a}" for a in self.attention)]

    def service_state(self) -> tuple[str, str] | None:
        """The Worker's idle state for the reviews (combined with publication's: `idle_state`)."""
        return review_state(self.view, self.attention)


class ReviewStep:  # pylint: disable=too-few-public-methods
    """The Worker service's review step (see the module docstring)."""

    def __init__(self, coord: Any, *, start: Starter | None = None, preflight: Callable[[], str] | None = None,  # pylint: disable=too-many-arguments
                 faults: Any = (), clock: Callable[[], float] = time.time,
                 wall: Callable[[], float] = time.time) -> None:
        self.coord, self.store, self.config = coord, coord.store, coord.config
        self.cfg = coord.config.github.review
        self.runner = WorkerReviewRunner(coord, start=start, preflight=preflight, faults=faults, clock=clock,
                                         wall=wall)
        self.wall = wall

    def poll(self, *, apply: bool = True) -> ReviewStepReport:
        """One step: watch, collect, start (see the module docstring)."""
        report = ReviewStepReport()
        if not apply or not self.cfg.enabled:
            return report
        for tag, handle in list(_CHILDREN.items()):
            if handle.poll() is not None:
                del _CHILDREN[tag]
        lock = self.store.locks.review()
        try:
            lock.acquire()
        except LockBusyError:
            report.deferred.append("github-review.lock is held (an operator's `github review run`?); the step runs "
                                   "again later")
            report.view = review_view(self.store)
            return report
        try:
            if not self._watch_all(report) and not self.store.list_claims():
                self._start_one(report)
        finally:
            lock.release()
        report.view = review_view(self.store)
        return report

    # -- watch -----------------------------------------------------------------------------------------

    def _watch_all(self, report: ReviewStepReport) -> bool:
        """Every current entry with a reviewer process; pending entries whose feature left review are retired.
        True while a reviewer process runs (or could not be stopped): no other starts."""
        busy = False
        for raw in self.store.list_records("review"):
            record = ReviewRecord.from_dict(raw)
            entry = record.entry(record.current)
            if entry is None:
                continue
            try:
                if (entry.get("running") or {}).get("tag"):
                    busy = self._watch(record.id, entry, report) or busy
                elif entry["status"] in ("pending", "running") and self._moot(record.id, entry):
                    self._discard(record.id, entry, self._moot(record.id, entry), report)
            except CoordinatorError as exc:  # GitHub or Git unavailable while collecting: the next step retries
                report.deferred.append(f"{record.id}: {type(exc).__name__}: {sanitize(exc, 300)}")
                busy = busy or bool((self.runner._now(record.id, entry["key"]).get("running") or {}).get("tag"))  # pylint: disable=protected-access
        return busy

    def _moot(self, feature_id: str, entry: Mapping[str, Any]) -> str:
        """Why *entry* can no longer become a current review, from local facts only ('' : it still can)."""
        binding = entry["binding"]
        try:
            feature = self.store.load_feature(feature_id)
        except CoordinatorError:
            return "its feature no longer exists"
        cand = feature.pull_request
        if load_intake(self.store, feature_id) is None:
            return "its feature is no longer an approved Issue's feature"
        if feature.state is not FeatureState.REVIEW:
            return f"its feature is {feature.state.value}, no longer in review"
        if cand is None or cand.number != binding["pr"]:
            return f"PR #{binding['pr']} is no longer the feature's recorded PR"
        if cand.head_sha != binding["head_sha"]:
            return f"the publisher's verified head is {str(cand.head_sha)[:12]}, not {binding['head_sha'][:12]}"
        return ""

    def _watch(self, feature_id: str, entry: dict[str, Any], report: ReviewStepReport) -> bool:
        key, running = entry["key"], entry["running"]
        state, pids = attempt_processes(running)
        moot = self._moot(feature_id, entry)
        overdue = self.wall() >= float(running.get("deadline_epoch") or 0)
        if state == "running" and not moot and not overdue:
            return True
        if state != "gone":
            kind = "moot" if moot else "deadline" if overdue else "orphaned"
            why = moot or (f"it passed its deadline {running.get('deadline')}" if overdue else
                           "its reviewer process ended and left processes of its CLI")
            if kind != "orphaned" and not running.get("watchdog"):  # the decision is durable BEFORE any signal
                self.runner._update(feature_id, key, lambda e: e["running"].update(watchdog={  # pylint: disable=protected-access
                    "kind": kind, "reason": sanitize(why, 300), "at": utc_now(), "pids": pids}),
                                    event=("review-process-stopping", {"cause": kind, "pids": pids}))
            survivors = stop_attempt(running, self.config.runtime.terminate_grace_seconds)
            with self.store.transaction():
                self.store.append_event("review-process-stopped", feature=feature_id, review=key, cause=kind, pids=pids,
                                        survivors=survivors)
            report.events.append(f"{feature_id}: stopped the reviewer process of review {key}: {why}")
            if survivors:
                report.attention.append(f"{feature_id}: processes {survivors} of review {key}'s reviewer could not be "
                                        "stopped; no reviewer starts while they live")
                return True
        if moot:
            self._discard(feature_id, self.runner._now(feature_id, key), moot, report)  # pylint: disable=protected-access
            return False
        sub = ReviewReport(True, feature_id)
        self.runner.run_held(feature_id, sub)  # collects the result (review.py `_worker_attempt`)
        self._absorb(sub, report)
        return False

    def _discard(self, feature_id: str, entry: Mapping[str, Any], why: str, report: ReviewStepReport) -> None:
        """A review that can no longer become current: invalidated; an uncollected result is not judged."""
        running = entry.get("running")
        if running:
            self.runner._update(feature_id, entry["key"], lambda e: e["attempt_log"].append({  # pylint: disable=protected-access
                **e["running"], "status": "discarded", "ended_at": utc_now()}))
        sub = ReviewReport(True, feature_id)
        self.runner._invalidate(feature_id, dict(entry), why, sub, True)  # pylint: disable=protected-access
        self._absorb(sub, report)

    # -- start -----------------------------------------------------------------------------------------

    def _candidates(self) -> list[str]:
        """Approved Issues' features in review with a recorded PR that publication has not stopped, Issue order."""
        found = []
        for feature in self.store.list_features():
            cand = feature.pull_request
            if feature.state is not FeatureState.REVIEW or cand is None or not cand.number or cand.attention:
                continue
            intake = load_intake(self.store, feature.id)
            if intake is not None:
                found.append((intake.issue_number, feature.id))
        return [feature_id for _number, feature_id in sorted(found)]

    def _start_one(self, report: ReviewStepReport) -> None:
        for feature_id in self._candidates():
            try:
                if self._consider(feature_id, report):
                    return
            except CoordinatorError as exc:  # GitHub, Git or a lock: deferred; the next step retries
                report.deferred.append(f"{feature_id}: {type(exc).__name__}: {sanitize(exc, 300)}")

    def _settled(self, feature_id: str, entry: Mapping[str, Any] | None) -> bool:
        """True when nothing is to be done for the publisher's verified head (no GitHub request is made)."""
        if entry is None:
            return False
        cand = self.store.load_feature(feature_id).pull_request
        binding = entry["binding"]
        if cand is None or (binding["pr"], binding["head_sha"]) != (cand.number, cand.head_sha):
            return False
        if entry["status"] == "invalidated":
            return True  # only a newly verified head or an operator's retry reviews again
        if entry["status"] != "complete" or (entry.get("comment") or {}).get("status") != "posted":
            return False
        intake = load_intake(self.store, feature_id)
        withdrawn = intake is not None and any(known_problem(self.store, self.store.load_task(t))
                                               for t in intake.all_task_ids)
        return entry["outcome"] == "ERROR" or not withdrawn  # a known withdrawal invalidates a real outcome (R1)

    def _consider(self, feature_id: str, report: ReviewStepReport) -> bool:
        """True when a reviewer process now runs for *feature_id* (no other is started in this step)."""
        record = load_review(self.store, feature_id)
        entry = record.entry(record.current) if record else None
        if self._settled(feature_id, entry):
            return False
        sub = ReviewReport(True, feature_id)
        if entry is not None and entry["status"] == "pending" and entry.get("unavailable_since") \
                and not entry.get("held"):
            problem = self.runner.preflight()  # still unavailable: no GitHub request, no attempt
            if problem:
                self.runner.unavailable(feature_id, entry["key"], problem, sub)
                self._absorb(sub, report)
                return False
        self.runner.run_held(feature_id, sub)
        self._absorb(sub, report)
        if sub.status == "running":
            report.started.append(feature_id)
            return True
        return False

    @staticmethod
    def _absorb(sub: ReviewReport, report: ReviewStepReport) -> None:
        report.events += sub.lines + [f"NEEDS ATTENTION: {a}" for a in sub.attention]
        report.deferred += sub.deferred


# -- reporting (read-only) -------------------------------------------------------------------------------

def _category(entry: Mapping[str, Any]) -> str:
    status, outcome = entry["status"], entry["outcome"]
    if status in ("running", "invalidated"):
        return status
    if status == "pending":
        return "held" if entry.get("held") else "reviewer-unavailable" if entry.get("unavailable_since") else "pending"
    return {"ERROR": "error", "REQUEST_CHANGES": "request-changes"}.get(outcome, "complete")


def review_view(store: Any) -> list[dict[str, Any]]:
    """Each review record's current entry as the Worker reports it (status, health, its idle state): pending,
    running, held, reviewer-unavailable, complete, request-changes, error, invalidated (or unreadable). Read-only:
    local records and /proc; nothing is created, started or requested."""
    rows: list[dict[str, Any]] = []
    try:
        raws = store.list_records("review")
    except (CoordinatorError, OSError):
        return rows
    for raw in raws:
        try:
            record = ReviewRecord.from_dict(raw)
        except StateDirError as exc:
            rows.append({"feature": str(raw.get("id")), "category": "unreadable", "feature_state": "review",
                         "reason": sanitize(exc, 300), "pr": None})
            continue
        entry = record.entry(record.current)
        if entry is None:
            continue
        try:
            feature_state: str | None = store.load_feature(record.id).state.value
        except CoordinatorError:
            feature_state = None
        running = entry.get("running") or {}
        process = None
        if running.get("tag"):
            state, pids = attempt_processes(running)
            process = {"state": state, "pid": running.get("pid"), "pids": pids, "attempt": running.get("attempt"),
                       "started_at": running.get("started_at"), "deadline": running.get("deadline")}
        binding = entry["binding"]
        rows.append({"feature": record.id, "issue": record.issue_number, "feature_state": feature_state,
                     "entry": entry["key"], "category": _category(entry), "status": entry["status"],
                     "outcome": entry["outcome"], "pr": binding["pr"], "head": binding["head_sha"],
                     "attempts": entry["attempts"], "process": process,
                     "unavailable_since": (entry.get("unavailable_since") or {}).get("at"),
                     "blocking": sum(f.get("severity") in ("blocker", "major") for f in entry["findings"]),
                     "reason": sanitize(entry["reason"] or (entry.get("invalidated") or {}).get("reason") or "", 300),
                     "comment": (entry.get("comment") or {}).get("status")})
    return rows


def _wait_text(row: Mapping[str, Any]) -> str:
    what = f"the AI review of PR #{row['pr']} ({row['feature']})"
    process = row.get("process") or {}
    return {"running": f"{what} is running (attempt {process.get('attempt')}, until {process.get('deadline')})",
            "pending": f"{what} is pending",
            "held": f"{what} holds an answer until its binding can be re-read",
            "reviewer-unavailable": f"{what} waits for the reviewer (unavailable since {row['unavailable_since']})",
            "request-changes": f"{what} requests changes ({row['blocking']} blocking finding(s)); it waits for a "
                               "person"}[row["category"]]


def review_state(view: Sequence[Mapping[str, Any]], attention: Sequence[str] = ()) -> tuple[str, str] | None:
    """The idle state the reviews of features in review call for: an ERROR (or an unreadable record, or a reviewer
    process that could not be stopped) needs an operator; a running, pending, held or unavailable review and
    REQUEST_CHANGES wait; PASS, PASS_WITH_NOTES and invalidated reviews add nothing (publication's wait stands)."""
    rows = [r for r in view if r.get("feature_state") == "review"]
    errors = [f"the AI review of PR #{r['pr']} ({r['feature']}) is ERROR: {r['reason']} — an operator decides "
              f"(`github review show {r['feature']}`; `github review retry {r['feature']} --reason … --apply`)"
              if r["category"] == "error" else f"the review record of {r['feature']} is unreadable: {r['reason']}"
              for r in rows if r["category"] in ("error", "unreadable")]
    if attention or errors:
        return NEEDS_ATTENTION, "; ".join([*attention, *errors])
    waits = [_wait_text(r) for r in rows if r["category"] in ("running", "pending", "held", "reviewer-unavailable",
                                                               "request-changes")]
    return (WAITING, "; ".join(waits)) if waits else None


def idle_state(waits: Mapping[str, tuple[str, str] | None]) -> tuple[str, str] | None:
    """The service's state while no feature is active, from each GitHub step's (publication, review): the most
    urgent kind, its reasons in step order (None: AVAILABLE)."""
    states = [s for s in waits.values() if s]
    for kind in (NEEDS_ATTENTION, WAITING):
        reasons = [reason for state, reason in states if state == kind]
        if reasons:
            return kind, "; ".join(reasons)
    return None


def github_steps(config: Any, coord: Any, hooks: Mapping[str, Any],
                 start: Starter | None) -> list[tuple[str, Any, Callable[[], Any]]]:
    """The service's GitHub steps in order: W4.3.3 publication first (a merge is seen before a closed Issue could
    look like a withdrawn approval; a Draft PR opened now is reviewable at once), then W4.6.1 R2 the AI review
    (it only starts, watches and collects reviewer processes), then the W4.3.1 intake poll (W4.5: a planner run
    inside it keeps the service's heartbeat through *hooks*)."""
    from .intake_poll import IntakePoller  # pylint: disable=import-outside-toplevel
    from .publish import PullRequestPublisher  # pylint: disable=import-outside-toplevel
    github = config.github
    return [("publish", github.publish, lambda: PullRequestPublisher(coord)),
            ("review", github.review, lambda: ReviewStep(coord, start=start)),
            ("intake", github.intake, lambda: IntakePoller(coord, **hooks))]


def stop_reviews(coord: Any, log: Callable[[str], None]) -> None:
    """A graceful service stop: each reviewer process still running is asked to stop — SIGTERM to the recorded
    reviewer process, identity and tag re-checked; its supervisor cancels the CLI and the attempt is recorded as
    cancelled (given back, R1's rule) — and waited for, before systemd ends whatever is left (KillMode=mixed).
    Nothing is started, collected or published here: the next start collects the result. Never raises."""
    grace = coord.config.runtime.terminate_grace_seconds + 10.0
    try:
        raws = coord.store.list_records("review")
    except (CoordinatorError, OSError):
        return
    for raw in raws:
        try:
            record = ReviewRecord.from_dict(raw)
        except CoordinatorError:
            continue
        entry = record.entry(record.current) or {}
        running = entry.get("running") or {}
        if not running.get("tag") or attempt_processes(running)[0] == "gone":
            continue
        try:
            survivors = stop_attempt(running, grace)
            log(f"review: stopped the reviewer process of {record.id} ({entry['key']}) for the service stop"
                + (f"; processes {survivors} could not be stopped" if survivors else ""))
        except (CoordinatorError, OSError) as exc:
            log(f"review: could not stop the reviewer process of {record.id}: {exc}")
