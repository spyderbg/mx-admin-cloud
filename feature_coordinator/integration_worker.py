"""W4.7 (M6): the Desktop Worker schedules integration jobs — by starting the EXISTING commands, one at a time.

When `github.integration.worker.enabled` (and `github.integration.enabled`), the Worker service runs this step with
its other GitHub steps (service.py `_poll_github`; `review_worker.github_steps` adds it last), at most every
`poll_interval_seconds`. A step holds `locks/integration-worker.lock` for moments and never waits for a gate, a
reviewer or GitHub:

  1. WATCH the stage process it (or an earlier service instance) started — found by its process TAG, never by a
     recorded PID alone:
       * running, within its deadline → nothing; the Worker's one execution slot stays taken;
       * past its deadline → the decision is recorded FIRST, then its processes are stopped (SIGTERM, grace, SIGKILL;
         the tag re-checked before every signal). What it left is exactly what a crash leaves: the stage commands
         resume their own interrupted work (integration-jobs.md §25, §37, §52);
       * gone → SETTLED: the job record is read again and compared with the state and revision the run started from.
         Changed: progress. Unchanged: a stall (integration_schedule.finish — delay, then hold). The command's exit code
         and printed outcome only word the operator's message; they decide nothing.
  2. START at most one stage, only while nothing of it runs: the oldest job whose next step is the Worker's
     (integration_schedule.next_step — the same decision `integration schedule` shows), not held, not delayed, whose
     job lock is free, and only while the checkout is IDLE for Feature Jobs: no agent holds a claim, no feature task
     is ready to start, no Draft PR reviewer runs. The launch is counted BEFORE the process starts (write-ahead), so
     a crash in between costs a launch, never a second process — and it is ADMITTED in the coordinator's critical
     section, the one a task claim is decided in: no claim exists and no quiescent barrier holds the project lock
     at that moment, or nothing is recorded (`integration_slot.admission_problem`).

What it starts is `python -m feature_coordinator integration <stage> --job J --apply --actor worker-service --json`
for a stage out of inspect, validate, review, recover (`integration_schedule.stage_argv`, a closed list) — the very
command an operator would run, with every check, lock, transition and record of M2–M5. This module holds no GitHub
client, verifies no approval, records no evidence and changes no job: it reads job records and writes only its own
schedule records and events.

THE MERGE IS NOT HERE. No stage is the merge; nothing here can name it; and no process this step starts binds the
GitHub client's integration trust anchors, which only `integration merge` does (integration-jobs.md §59) — so a
scheduled process cannot send the PR-creation or merge request even by mistake.

Exclusive execution. Per job: the stage commands' own `locks/integration-<job>.lock` (a kernel lock — it ends with
its process, so there is no stale lock to break): an operator's command and a Worker's stage exclude each other, the
loser changes nothing and the Worker counts no launch when it sees the lock taken. Per state directory: one service
(`worker-service.lock`), one step at a time (`integration-worker.lock`, shared with `integration schedule --resume`,
which therefore cannot discard a launch that is between its record and its process), one stage process at a time
(the schedule records).

THE EXECUTION SLOT (integration_slot.execution_owner) is read from the schedule records and /proc by everyone who
executes work on the checkout: the service before each cycle's work — so also in the first cycle after a restart,
and also when scheduling was switched off while a stage ran (the step is then still constructed, to watch and
settle; it starts nothing) —, every task claim (a manual `launch` is refused) and the feature integration barrier.
While a stage runs, while the reviewer process a killed review stage left behind runs, and while a schedule record
cannot be read (unknown is not free), the service starts no agent and runs none of its other steps: no publication
pass with its integration gates, no intake with its planner, no reviewer. Feature Jobs keep priority: a stage only
ever starts on an idle checkout.

Lifecycle. `worker pause`: nothing new starts; a running stage finishes. A graceful service stop (`stop_stages`) asks
the stage process to stop (a review gives its attempt back, as for an operator's Ctrl-C), waits, and records the stop
as nobody's stall. After a service crash outside systemd the stage process survives, tagged and recorded: the next
instance watches it. Under systemd it ends with the service: the next instance finds it gone and settles it.
"""

from __future__ import annotations

import dataclasses
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from .autonomy import eligible_tasks, load_scheduling
from .errors import CoordinatorError, LockBusyError
from .github_api import sanitize
from .integration import IntegrationJob, IntegrationJobs, JobState
from .issue_sync import SimulatedCrash
from .integration_schedule import (
    WORKER,
    assigned,
    basis_of,
    finish,
    launch_hold,
    next_step,
    not_started,
    read_outcome,
    refresh,
    stage_argv,
    unacknowledged_end,
)
from .integration_schema import open_review_attempt, parse_record_time
from .integration_slot import (
    RECORD_KIND,
    REVIEW_TAG_ENV,
    STAGE_TAG_ENV,
    Schedule,
    admission_problem,
    execution_owner,
    list_schedules,
    live_processes,
    load_schedule,
    log_name,
    new_tag,
    owner_of,
    reviewer_processes,
    save_schedule,
    stage_processes,
    stop_stage,
)
from .locks import FileLock, ProcessIdentity
from .model import FeatureState, utc_now
from .review_process import stop_attempt
from .supervisor import tagged_processes

BUSY, WAITING, NEEDS_ATTENTION = "BUSY", "WAITING", "NEEDS_ATTENTION"  # the Worker's states (autonomy.py)
STAGE_SLACK_SECONDS = 60.0     # a stage process's start-up and bookkeeping beyond its timeout
STEP_LOCK = "integration-worker.lock"   # one scheduling step (or `schedule --resume`) at a time
_CHILDREN: dict[str, Any] = {}  # this service's stage processes (Popen), polled so that none stays a zombie

# start(argv tail, log file name, environment) -> a started process with .pid and .poll() (worker.cli_spawner)
Starter = Callable[[list[str], str, Mapping[str, str]], Any]


def iso(seconds: float) -> str:
    """ISO-8601 UTC for an epoch timestamp (as autonomy.iso)."""
    return datetime.fromtimestamp(seconds, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- the checkout's other work ------------------------------------------------------------------------------------

def feature_work_pending(store: Any, config: Any, now: float) -> str:
    """Why an integration stage must not start now ('' : the checkout is idle for Feature Jobs): an agent holds a claim,
    a feature task is ready to start, or a Draft PR reviewer process runs. Anything unreadable counts as busy."""
    try:
        if store.list_claims():
            return "an agent holds a claim on the checkout"
        for feature in store.list_features():
            if feature.state is not FeatureState.ACTIVE:
                continue
            tasks = store.load_tasks(feature)
            schedules = {task_id: load_scheduling(store, task) for task_id, task in tasks.items()}
            ready = eligible_tasks(config, tasks, schedules, now)
            if ready:
                return f"task {ready[0].id} of the active feature {feature.id} is ready to start (Feature Jobs first)"
        if config.github.review.enabled:
            from .review_worker import review_view  # pylint: disable=import-outside-toplevel
            for row in review_view(store):
                if (row.get("process") or {}).get("state") == "running":
                    return f"the Draft PR reviewer of {row['feature']} runs"
    except (CoordinatorError, OSError, ValueError, KeyError) as exc:
        return f"the checkout's feature state cannot be read ({type(exc).__name__}: {sanitize(exc, 200)})"
    return ""


# -- the step -----------------------------------------------------------------------------------------------------

@dataclass
class IntegrationStepReport:
    """What one service step did. `events`: what happened (logged once); `view`: the jobs as they stand now."""

    events: list[str] = field(default_factory=list)
    deferred: list[str] = field(default_factory=list)
    attention: list[str] = field(default_factory=list)   # processes that could not be stopped, unreadable records
    started: list[str] = field(default_factory=list)
    busy: str = ""                                        # the stage that holds the Worker's execution slot
    view: list[dict[str, Any]] = field(default_factory=list)

    def lines(self) -> list[str]:
        """The service's log lines."""
        return [*self.events, *(f"deferred: {sanitize(d, 300)}" for d in self.deferred),
                *(f"NEEDS ATTENTION: {a}" for a in self.attention)]

    def service_state(self) -> tuple[str, str] | None:
        """The Worker's state for the integration jobs: BUSY while a stage runs (no agent starts: service.py), else
        what `integration_state` derives."""
        if self.busy:
            return BUSY, self.busy
        return integration_state(self.view, self.attention)


class IntegrationStep:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """The Worker service's integration step (see the module docstring)."""

    def __init__(self, coord: Any, *, start: Starter | None = None, faults: Any = (),  # pylint: disable=too-many-arguments
                 clock: Callable[[], float] = time.time, wall: Callable[[], float] = time.time,
                 instance: str = "", environ: Mapping[str, str] | None = None) -> None:
        self.coord, self.store, self.config = coord, coord.store, coord.config
        self.cfg = coord.config.github.integration.worker
        self.scheduling = bool(coord.config.github.integration.enabled and self.cfg.enabled)
        self.jobs = IntegrationJobs(coord.store)
        self.start, self.faults = start, set(faults)
        self.clock, self.wall, self.instance, self.environ = clock, wall, instance, environ

    def _fault(self, name: str) -> None:
        if name in self.faults:
            self.faults.discard(name)
            raise SimulatedCrash(name)

    def poll(self, *, apply: bool = True) -> IntegrationStepReport:
        """One step: watch, settle, start (see the module docstring). With scheduling off it still WATCHES and
        settles what an earlier configuration started (nothing new starts); without any schedule record it does
        nothing at all."""
        report = IntegrationStepReport()
        if not apply or not (self.scheduling or (self.store.root / RECORD_KIND).is_dir()):
            return report
        for handle in list(_CHILDREN.values()):
            handle.poll()   # reaped here; its exit code is read when its stage is settled
        lock = FileLock(self.store.locks.dir / STEP_LOCK)
        try:
            lock.acquire()
        except LockBusyError:
            report.deferred.append(f"{STEP_LOCK} is held (another scheduling step, or `integration schedule "
                                   "--resume`); this step runs again later")
            report.busy = execution_owner(self.store)[1]
            report.view = schedule_view(self.store, self.config, self.clock())
            return report
        try:
            try:
                jobs = self.jobs.list()
            except (CoordinatorError, OSError, ValueError) as exc:  # a corrupt record is never skipped: nothing starts
                report.attention.append(f"the integration job records cannot be read ({sanitize(exc, 300)}); no "
                                        "integration stage is started until they can")
                jobs = None
            if not self._watch_all(report) and jobs is not None and self.scheduling:
                self._start_one(jobs, report)
        finally:
            lock.release()
        report.view = schedule_view(self.store, self.config, self.clock())
        return report

    # -- watch and settle ------------------------------------------------------------------------------------

    def _watch_all(self, report: IntegrationStepReport) -> bool:
        """Every schedule record with a stage process. True while one runs, could not be stopped or cannot be read:
        nothing else starts."""
        busy = False
        for stem, rec, error in list_schedules(self.store):
            if rec is None:
                report.attention.append(f"integration schedule record {stem!r} cannot be read: {sanitize(error, 300)} "
                                        f"(kept as evidence; inspect it, then `integration schedule --job {stem} "
                                        "--resume --reason TEXT --apply`)")
                busy = True
                continue
            try:
                if rec.running is not None:
                    busy = self._watch(rec, report) or busy
            except (CoordinatorError, OSError, ValueError, KeyError) as exc:
                report.attention.append(f"{rec.id}: its stage cannot be settled: {type(exc).__name__}: "
                                        f"{sanitize(exc, 300)}")
                busy = True
        return busy

    def _watch(self, rec: Schedule, report: IntegrationStepReport) -> bool:
        running = rec.running
        assert running is not None
        job_id, stage = rec.id, running["stage"]
        state, pids = stage_processes(running)
        overdue = not self.wall() < float(running["deadline_epoch"])   # (a comparison no bad number can win)
        if state == "running" and not overdue:
            report.busy = owner_of(self.store, rec)
            return True
        cause = "exited"
        if state != "gone":
            cause = "deadline" if state == "running" else "orphaned"
            why = (f"it passed its deadline {running['deadline']}" if cause == "deadline" else
                   "its stage process ended and left processes behind")
            if not running.get("watchdog"):   # the decision is durable BEFORE any signal
                self._update(job_id, running["tag"], lambda r: r.running.update(watchdog={
                    "kind": cause, "reason": why, "at": utc_now(), "pids": pids}),
                             event=("integration-stage-stopping", {"stage": stage, "cause": cause, "pids": pids}))
            survivors = stop_stage(running, self.config.runtime.terminate_grace_seconds)
            with self.store.transaction():
                self.store.append_event("integration-stage-stopped", job=job_id, stage=stage, cause=cause, pids=pids,
                                        survivors=survivors)
            report.events.append(f"{job_id}: stopped `{stage}`: {why}")
            if survivors:
                report.attention.append(f"{job_id}: processes {survivors} of `{stage}` could not be stopped; no "
                                        "integration stage and no agent starts while they live")
                report.busy = f"integration job {job_id}: processes {survivors} of `{stage}` could not be stopped"
                return True
        if stage == "review" and self._reviewer_alive(rec, report):
            return True
        self._settle(job_id, running, cause, report)
        return False

    def _reviewer_alive(self, rec: Schedule, report: IntegrationStepReport) -> bool:
        """The review stage is gone — but a reviewer process it started (M4: its own session, its own tag) may run
        on. It keeps the slot until it has ended, under M4's own supervision rules, applied here because the command
        that would apply them is dead (`stop_stale_reviewers`). The next `review` run then records the attempt as M4
        does (an answer collected, a timeout or an interruption counted) — never a second reviewer for it."""
        running = rec.running
        assert running is not None
        pids, unknown = reviewer_processes(self.store, rec)
        if unknown:
            report.attention.append(f"{rec.id}: its job record cannot be read, so whether the reviewer of its `review` "
                                    "stage still runs is unknown; the slot stays taken")
            return True
        if not pids:
            return False
        found = stop_stale_reviewers(self.store, self.jobs, rec.id, self.wall(),
                                     self.config.runtime.terminate_grace_seconds)
        if found["owned"]:   # an operator's own command on the job runs: it supervises its reviewer; hands off
            report.busy = (f"integration job {rec.id}: another integration command on the job runs and supervises its "
                           "reviewer; no agent is started meanwhile")
            return True
        if found["stopped"]:
            with self.store.transaction():
                self.store.append_event("integration-stage-reviewer-stopped", job=rec.id, reasons=found["stopped"],
                                        survivors=found["survivors"])
            report.events.append(f"{rec.id}: stopped the reviewer process its killed `review` stage left behind: "
                                 + "; ".join(found["stopped"]))
        if found["survivors"]:
            report.attention.append(f"{rec.id}: reviewer processes {found['survivors']} could not be stopped; no "
                                    "integration stage and no agent starts while they live")
            return True
        tag, deadline = str(found["running_tag"]), str(found["running_deadline"])
        if not tag:
            return False
        if running.get("reviewer") != tag and self._update(
                rec.id, running["tag"], lambda r: r.running.update(reviewer=tag),
                event=("integration-stage-reviewer-running", {"reviewer": tag})):
            report.events.append(f"{rec.id}: `review` ended but its reviewer process still runs (until {deadline} at "
                                 "the latest); the slot stays taken until it has ended")
        report.busy = (f"integration job {rec.id}: the reviewer process of its `review` stage still runs; no agent "
                       "is started meanwhile")
        return True

    def _settle(self, job_id: str, running: Mapping[str, Any], cause: str, report: IntegrationStepReport) -> None:
        """The stage process is gone: close the launch from what the job record says now (module docstring)."""
        handle = _CHILDREN.pop(running["tag"], None)
        exit_code = handle.poll() if handle is not None else None
        outcome, detail = read_outcome(self.store.root / "logs" / str(running.get("log") or ""))
        job = self.jobs.load(job_id)
        after = assigned(next_step(job, self.config), self.cfg)
        entry: dict[str, Any] = {}
        held = ""

        def close(rec: Schedule) -> None:
            nonlocal held
            entry.update(finish(rec, job, self.cfg, self.clock(), cause=cause, outcome=outcome, detail=detail,
                                exit_code=exit_code, at=utc_now(),
                                repeats=after.who == WORKER and after.stage == running["stage"]))
            held = (rec.hold or {}).get("reason", "")
        if not self._update(job_id, running["tag"], close, event=None, closing=True):
            return   # (the launch was closed by someone else, or its stage process showed up meanwhile: next step)
        with self.store.transaction():
            self.store.append_event("integration-stage-ended", job=job_id, stage=entry["stage"], cause=cause,
                                    exit_code=exit_code, outcome=entry["outcome"], progressed=entry["progressed"],
                                    state=entry["state_after"], held=bool(held))
        shown = f"`{entry['outcome']}`" if entry["outcome"] else f"exit {exit_code}"
        report.events.append(f"{job_id}: `{entry['stage']}` ended ({cause}, {shown}): job {entry['state_before']} → "
                             f"{entry['state_after']}" + ("" if entry["progressed"] else " — nothing changed"))
        if held:
            report.events.append(f"{job_id}: HELD for the operator: {held} (`integration schedule --job {job_id}`)")
        if job.terminal and job.state not in (JobState.COMPLETED, JobState.ABORTED):
            report.events.append(f"{job_id}: NEEDS ATTENTION: the job ended {job.state.value}: "
                                 f"{sanitize((job.attention or {}).get('reason') or 'see its record', 300)} "
                                 f"(`integration status --job {job_id}`)")

    def _update(self, job_id: str, tag: str, change: Callable[[Schedule], Any],
                event: tuple[str, dict[str, Any]] | None, *, closing: bool = False) -> bool:
        """Apply *change* to the job's schedule record while its running stage is still the one tagged *tag*.
        *closing*: the change CLOSES the launch (it was judged gone from an earlier read) — it is applied only if
        nothing of the stage lives NOW, read again in this critical section: the stage process may have confirmed
        its launch (integration_slot.confirm_launch, the same critical section) since that read. Either its
        confirmation comes first — then it is seen here and the launch stays open — or this comes first, and the
        stage process finds no launch to confirm and does nothing."""
        with self.store.transaction():
            rec = load_schedule(self.store, job_id)
            if rec.running is None or rec.running.get("tag") != tag:
                return False
            if closing and live_processes(self.store, rec):
                return False
            change(rec)
            save_schedule(self.store, rec)
            if event is not None:
                self.store.append_event(event[0], job=job_id, **event[1])
        return True

    # -- start -------------------------------------------------------------------------------------------------

    def _start_one(self, jobs: Sequence[IntegrationJob], report: IntegrationStepReport) -> None:
        candidates = [(job, step) for job in jobs
                      for step in [assigned(next_step(job, self.config), self.cfg)] if step.who == WORKER]
        if not candidates:
            return
        if feature_work_pending(self.store, self.config, self.clock()):
            return   # shown by the view (waiting); nothing is logged per step
        for job, step in candidates:
            try:
                if self._consider(job, step.stage, report):
                    return
            except CoordinatorError as exc:
                report.deferred.append(f"{job.job_id}: {type(exc).__name__}: {sanitize(exc, 300)}")

    def _consider(self, job: IntegrationJob, stage: str, report: IntegrationStepReport) -> bool:
        """True when a stage process now runs for *job* (no other is started in this step)."""
        job_id, held = job.job_id, ""
        with self.store.transaction():
            rec = load_schedule(self.store, job_id)
            changed = refresh(rec, job)
            if rec.hold is None and launch_hold(rec, job, stage, self.cfg, utc_now()):
                changed, held = True, rec.hold["reason"]  # type: ignore[index]
                self.store.append_event("integration-stage-held", job=job_id, stage=stage, reason=held)
            if changed:
                save_schedule(self.store, rec)
        if held:
            report.events.append(f"{job_id}: HELD for the operator: {held} (`integration schedule --job {job_id}`)")
        if rec.hold is not None or rec.running is not None \
                or (rec.next_attempt_at is not None and rec.next_attempt_at > self.clock()):
            return False
        if job_lock_busy(self.store, job_id):
            return False   # an operator's command (or an earlier stage) owns the job now: no launch is counted
        if self.start is None:
            report.deferred.append(f"{job_id}: no stage process can be started here (only the Worker service starts "
                                   "them)")
            return False
        return self._launch(job, stage, report)

    def _launch(self, job: IntegrationJob, stage: str, report: IntegrationStepReport) -> bool:
        job_id = job.job_id
        argv = stage_argv(stage, job_id)   # refuses anything but a schedulable stage
        tag = new_tag(secrets.token_hex(8))
        deadline = self.wall() + self.cfg.timeout(stage) + STAGE_SLACK_SECONDS
        launched: dict[str, Any] = {}
        with self.store.transaction():   # counted BEFORE it starts, for the job record as it is now
            rec = load_schedule(self.store, job_id)
            current = self.jobs.load(job_id)
            if rec.running is not None or rec.hold is not None or basis_of(current) != basis_of(job) \
                    or admission_problem(self.store) or execution_owner(self.store)[0]:
                return False   # (admitted in the critical section a claim is decided in: integration_slot.py)
            launch = rec.launches.get(stage, 0) + 1
            log = log_name(job_id, stage, launch, tag)
            rec.launches[stage] = launch
            rec.running = {"stage": stage, "launch": launch, "tag": tag, "pid": None, "process": None,
                           "launcher": ProcessIdentity.current().to_dict(),   # the reservation lives with this process
                           "started_at": utc_now(), "started_epoch": self.wall(), "deadline": iso(deadline),
                           "deadline_epoch": deadline, "instance": self.instance, "log": log,
                           "basis": basis_of(current), "watchdog": None, "reviewer": None}
            save_schedule(self.store, rec)
            self.store.append_event("integration-stage-starting", job=job_id, stage=stage, launch=launch, tag=tag)
            launched.update(launch=launch, log=log)
        self._fault(f"crash-after-stage-counted:{job_id}")
        environ = {**(os.environ if self.environ is None else self.environ), STAGE_TAG_ENV: tag,
                   "PYTHONUNBUFFERED": "1"}
        try:
            handle = self.start(argv, launched["log"], environ)  # type: ignore[misc]
        except OSError as exc:   # nothing started: the launch is given back, the failed start is a stall
            held = ""

            def give_back(rec: Schedule) -> None:
                nonlocal held
                not_started(rec, job, self.cfg, self.clock(), stage, str(exc), utc_now())
                held = (rec.hold or {}).get("reason", "")
            self._update(job_id, tag, give_back,
                         event=("integration-stage-not-started", {"stage": stage, "error": sanitize(exc, 300)}))
            report.deferred.append(f"{job_id}: `{stage}` could not be started: {sanitize(exc, 300)}")
            if held:
                report.events.append(f"{job_id}: HELD for the operator: {held} (`integration schedule --job "
                                     f"{job_id}`)")
            return False
        _CHILDREN[tag] = handle
        self._fault(f"crash-after-stage-started:{job_id}")
        identity = ProcessIdentity.of_pid(handle.pid)
        self._update(job_id, tag, lambda r: r.running.update(
            pid=handle.pid, process=identity.to_dict() if identity else None),
                     event=("integration-stage-started", {"stage": stage, "launch": launched["launch"],
                                                          "pid": handle.pid}))
        report.started.append(job_id)
        report.busy = (f"integration job {job_id}: `{stage}` runs (launch {launched['launch']}, pid {handle.pid}, "
                       f"until {iso(deadline)}); no agent is started meanwhile")
        report.events.append(f"{job_id}: started `integration {stage}` (launch {launched['launch']}, pid "
                             f"{handle.pid}, deadline {iso(deadline)}); a later step settles it")
        return True


def stop_stale_reviewers(store: Any, jobs: IntegrationJobs, job_id: str, wall: float, grace: float) -> dict[str, Any]:
    """M4's supervision of the reviewers of *job_id*, for a review stage whose command is dead. UNDER THE JOB'S LOCK
    (the one every integration command of the job holds): if it is taken, a command — an operator's `review` — owns
    the job and supervises its own reviewer, and nothing is touched (`owned`). With the lock held the job is loaded
    STRICTLY (a file that is not this job's record raises: no tag of it is ever signalled) and each attempt is judged
    on its own: the reviewer of the OPEN attempt within its deadline runs on (`running_tag`, `running_deadline`); the
    open attempt's reviewer past its deadline, and processes left by an attempt that is no longer open, are stopped
    (`stopped`: why, per attempt; `survivors`: what could not be stopped)."""
    found: dict[str, Any] = {"owned": False, "running_tag": "", "running_deadline": "", "stopped": [],
                             "survivors": []}
    lock = FileLock(store.locks.dir / f"integration-{job_id}.lock")
    try:
        lock.acquire(timeout=0)
    except LockBusyError:
        found["owned"] = True
        return found
    try:
        job = jobs.load(job_id)
        opened = open_review_attempt(job)
        for attempt in [e for e in job.evidence if e["kind"] == "review-attempt"]:
            tag = attempt["tag"]
            if not tagged_processes(REVIEW_TAG_ENV, tag):
                continue
            is_open = opened is not None and opened["id"] == attempt["id"]
            if is_open and wall < parse_record_time(attempt["deadline"], "deadline").timestamp():
                found["running_tag"], found["running_deadline"] = tag, attempt["deadline"]
                continue
            found["stopped"].append(f"attempt {attempt['id']} passed its deadline {attempt['deadline']}" if is_open
                                    else f"attempt {attempt['id']} is no longer open")
            found["survivors"] += stop_attempt({"tag": tag, "process": None}, grace)
    finally:
        lock.release()
    return found


def job_lock_busy(store: Any, job_id: str) -> bool:
    """True while a process holds the job's `integration-<job>.lock` (an inspect, validate, review, merge or recover
    run). A probe: the lock is taken and released at once."""
    lock = FileLock(store.locks.dir / f"integration-{job_id}.lock")
    if not lock.path.exists():   # never held yet (and a read-only view creates nothing)
        return False
    try:
        lock.acquire(timeout=0)
    except LockBusyError:
        return True
    lock.release()
    return False


# -- reporting (read-only) ----------------------------------------------------------------------------------------

def _row(step: Any, category: str) -> dict[str, Any]:
    return {**step.to_dict(), "category": category, "running": None, "hold": None, "next_attempt_at": None,
            "stalls": 0, "launches": {}, "last": None}


def schedule_view(store: Any, config: Any, now: float) -> list[dict[str, Any]]:  # pylint: disable=too-many-locals,too-many-branches
    """Each open job as the Worker sees it: its next step (who, stage, why, the command), and the Worker's
    bookkeeping for it (running stage and whether its processes live, delay, hold, counters) — plus every job a
    Worker-started stage ended in a terminal state nobody acknowledged yet (category `ended`: it is not continued,
    but it must not vanish from attention). Read-only: local records and /proc; nothing is started, written or
    requested, and no lock is taken. An unreadable record is a row (category `unreadable`), never an exception."""
    rows: list[dict[str, Any]] = []
    cfg = config.github.integration.worker
    try:
        jobs = IntegrationJobs(store).list()
    except (CoordinatorError, OSError, ValueError) as exc:
        return [{"job": "?", "category": "unreadable", "who": "operator", "urgent": True, "state": "?",
                 "reason": f"the integration job records cannot be read: {sanitize(exc, 300)}", "action": "",
                 "stage": "", "phase": "", "interrupted": False, "running": None, "hold": None,
                 "next_attempt_at": None, "stalls": 0, "launches": {}, "last": None}]
    for job in jobs:
        step = assigned(next_step(job, config), cfg)
        row = _row(step, "operator" if step.who != WORKER else "ready")
        try:
            rec = load_schedule(store, job.job_id)
        except (CoordinatorError, OSError, ValueError) as exc:
            row.update(category="unreadable", urgent=True,
                       reason=f"its schedule record cannot be read: {sanitize(exc, 300)}")
            rows.append(row)
            continue
        row.update(stalls=rec.stalls, launches=dict(rec.launches), last=rec.last)
        if rec.running is not None:
            state, pids = stage_processes(rec.running)
            reviewers = reviewer_processes(store, rec)[0] if state == "gone" else []
            if reviewers:
                state, pids = "reviewer", reviewers
            row.update(category="running" if state != "gone" else "unsettled",
                       running={"stage": rec.running["stage"], "launch": rec.running["launch"], "state": state,
                                "pid": rec.running.get("pid"), "pids": pids,
                                "started_at": rec.running["started_at"], "deadline": rec.running["deadline"]})
        elif job.terminal:
            if not unacknowledged_end(rec, job):
                continue
            row.update(category="ended", urgent=True, who="operator",
                       reason=f"a scheduled `{rec.last['stage']}` ended it {job.state.value}: "  # type: ignore[index]
                              f"{sanitize((job.attention or {}).get('reason') or 'see its record', 300)}",
                       action=f"`integration status --job {job.job_id}`; then acknowledge with `integration schedule "
                              f"--job {job.job_id} --resume --reason TEXT --apply`")
        elif step.who == WORKER:
            lapsed = rec.hold is not None and rec.hold["kind"] == "stalls" and rec.hold["basis"] != basis_of(job)
            if rec.hold is not None and not lapsed:
                row.update(category="held", hold=dict(rec.hold), urgent=True)
            elif rec.next_attempt_at is not None and rec.next_attempt_at > now and not lapsed:
                row.update(category="delayed", next_attempt_at=iso(rec.next_attempt_at))
        rows.append(row)
    return rows


def status_line(row: Mapping[str, Any]) -> str:
    """One open integration job as `worker status` shows it (records and /proc only; never a credential)."""
    head = f"integration {row['job']} ({row['state']}): {row['category']}"
    if row["category"] in ("running", "unsettled"):
        run = row["running"]
        return f"{head} — `{run['stage']}` launch {run['launch']}, process {run['pid'] or '-'} {run['state']} " \
               f"(deadline {run['deadline']})"
    if row["category"] == "held":
        return f"{head} — {row['hold']['reason']}; `integration schedule --job {row['job']} --resume --reason TEXT " \
               "--apply` after fixing it"
    if row["category"] == "delayed":
        return f"{head} — `{row['stage']}` again at {row['next_attempt_at']} (it changed nothing {row['stalls']}x)"
    if row["category"] == "ready":
        return f"{head} — `{row['stage']}` starts when the checkout is idle"
    return f"{head}{' URGENT' if row['urgent'] else ''} — {row['reason']}" + (f" — {row['action']}" if row["action"]
                                                                             else "")


def _wait_text(row: Mapping[str, Any]) -> str:
    job = f"integration job {row['job']} ({row['state']})"
    if row["category"] == "delayed":
        return f"{job}: `{row['stage']}` is repeated at {row['next_attempt_at']} (it changed nothing {row['stalls']}x)"
    if row["category"] == "ready":
        return f"{job}: `{row['stage']}` starts when the checkout is idle"
    if row["category"] == "unsettled":
        return f"{job}: `{row['running']['stage']}` ended and is settled by the next step"
    return f"{job}: {row['reason']} — {row['action']}"


def integration_state(view: Sequence[Mapping[str, Any]], attention: Sequence[str] = ()) -> tuple[str, str] | None:
    """The idle state the integration jobs call for: a held job, a job stopped for an operator (`urgent`), a job a
    scheduled stage ended in a terminal state nobody acknowledged, an unreadable record or an unstoppable process
    needs attention; a job waiting for its turn, a delay or an operator's decision (the merge approval above all)
    waits; no open job adds nothing."""
    urgent = [f"integration job {r['job']} ({r['state']}): "
              + (f"HELD — {r['hold']['reason']} (`integration schedule --job {r['job']} --resume --reason TEXT "
                 f"--apply` after fixing it)" if r["category"] == "held" else f"{r['reason']} — {r['action']}")
              for r in view if r["category"] in ("held", "unreadable", "ended")
              or (r["category"] == "operator" and r["urgent"])]
    if attention or urgent:
        return NEEDS_ATTENTION, "; ".join([*attention, *urgent])
    waits = [_wait_text(r) for r in view if r["category"] in ("ready", "delayed", "unsettled", "operator")]
    return (WAITING, "; ".join(waits)) if waits else None


# -- the service's shutdown and step list ---------------------------------------------------------------------------

def stop_stages(coord: Any, log: Callable[[str], None]) -> None:
    """A graceful service stop: each stage process still running is asked to stop and waited for (a review stops its
    reviewer and gives the attempt back), then its launch is closed as `service-stop` — nobody's stall; the next
    service instance starts the stage again and the stage command resumes its own work. Also with scheduling
    switched off (a stage an earlier configuration started). Never raises."""
    cfg = coord.config.github.integration.worker
    if not (coord.store.root / RECORD_KIND).is_dir():
        return
    grace = coord.config.runtime.terminate_grace_seconds + 10.0
    jobs = IntegrationJobs(coord.store)
    for _stem, rec, _error in list_schedules(coord.store):
        try:
            running = rec.running if rec is not None else None
            if rec is None or running is None or not live_processes(coord.store, rec):
                continue
            survivors = stop_stage(running, grace)   # a review stops its reviewer itself and gives the attempt back
            if running["stage"] == "review" and reviewer_processes(coord.store, rec)[0]:   # … one it left behind:
                survivors += _stop_reviewers(coord.store, jobs, rec.id, grace)             # stopped here
            log(f"integration: stopped `{running['stage']}` of {rec.id} for the service stop"
                + (f"; processes {survivors} could not be stopped" if survivors else ""))
            if survivors or live_processes(coord.store, rec):
                continue
            handle = _CHILDREN.pop(running["tag"], None)
            job = jobs.load(rec.id)
            with coord.store.transaction():
                current = load_schedule(coord.store, rec.id)
                if current.running is None or current.running.get("tag") != running["tag"] \
                        or live_processes(coord.store, current):   # (read again here: nothing of it may live)
                    continue
                entry = finish(current, job, cfg, time.time(), cause="service-stop", at=utc_now(),
                               exit_code=handle.poll() if handle is not None else None)
                save_schedule(coord.store, current)
                coord.store.append_event("integration-stage-ended", job=rec.id, stage=entry["stage"],
                                         cause="service-stop", progressed=entry["progressed"],
                                         state=entry["state_after"], held=False)
        except (CoordinatorError, OSError, ValueError, KeyError) as exc:
            log(f"integration: could not stop the stage process of a job: {exc}")


def _stop_reviewers(store: Any, jobs: IntegrationJobs, job_id: str, grace: float) -> list[int]:
    """A service stop: every reviewer process of the job's review attempts is stopped (it records its cancellation:
    the attempt is given back) — under the job's lock and for the strictly loaded job only; while another command
    owns the job nothing is touched. Returns the processes still alive."""
    lock = FileLock(store.locks.dir / f"integration-{job_id}.lock")
    try:
        lock.acquire(timeout=0)
    except LockBusyError:
        return []
    try:
        survivors: list[int] = []
        for attempt in [e for e in jobs.load(job_id).evidence if e["kind"] == "review-attempt"]:
            survivors += stop_attempt({"tag": attempt["tag"], "process": None}, grace)
        return survivors
    finally:
        lock.release()


def integration_steps(coord: Any, start: Starter | None, instance: str = "",
                      clock: Callable[[], float] = time.time) -> list[tuple[str, Any, Callable[[], Any]]]:
    """The service's integration step, as one more `review_worker.github_steps` entry — LAST, so a stage starts only
    when the other steps left the checkout idle. Empty while scheduling is off AND nothing was ever scheduled: the
    service then never constructs the step, reads no integration record and starts nothing. With scheduling off
    but schedule records present the step is still there, to WATCH and settle (it starts nothing): switching
    scheduling off never drops the supervision of a stage that is running."""
    integration = coord.config.github.integration
    scheduling = integration.enabled and integration.worker.enabled
    if not scheduling and not (coord.store.root / RECORD_KIND).is_dir():
        return []
    cfg = integration.worker if scheduling else dataclasses.replace(integration.worker, enabled=True)
    return [("integration", cfg, lambda: IntegrationStep(coord, start=start, instance=instance, clock=clock))]
