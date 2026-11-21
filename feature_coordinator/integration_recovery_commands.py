"""W4.7 (M6) commands: `integration recover` and `integration schedule` (integration_recovery.py has the rules,
integration_worker.py the Worker's step). `register` adds the whole `integration` group: M1–M5's commands, then these.

Neither command can write to GitHub: `recover --apply` runs IntegrationMerger.settle with a GitHub client that carries
no integration anchors (only `integration merge` binds them), and `schedule` reads and writes the Worker's own
schedule records only.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .errors import CoordinatorError, LockBusyError, StateDirError
from .integration import IntegrationJobs, check_reason
from .integration_approval import now_utc
from . import integration_commands as _commands
from .integration_commands import _confirm_scheduled, _emit, _json_errors, _state_path, _verifier, _writable_state
from .integration_intake import ReadOnlyGit, check_actor
from .integration_merge import IntegrationMerger
from .integration_recovery import SETTLED_OUTCOMES, diagnose, diagnosis_lines
from .integration_schedule import resume, unacknowledged_end
from .integration_slot import REVIEW_TAG_ENV, Schedule, any_stage_processes, live_processes, \
    load_schedule, save_schedule, schedule_path
from .integration_worker import STEP_LOCK, job_lock_busy, schedule_view
from .locks import FileLock, LockSet
from .model import utc_now
from .store import StateStore
from .supervisor import tagged_processes


STEP_WAIT_SECONDS = 5.0   # how long `schedule --resume --apply` waits for a scheduling step to leave its lock


@_json_errors
def cmd_recover(args: argparse.Namespace, config: ProjectConfig) -> int:
    """(M6) Diagnose interrupted integration jobs; --apply settles what a merge run left behind (never a write)."""
    actor = check_actor(args.actor)
    if args.apply and not args.job:
        raise CoordinatorError("recover --apply settles ONE job: pass --job JOB (without --apply every open job is "
                               "diagnosed, read-only)")
    _confirm_scheduled(args, config)
    state_dir = _writable_state(args, config) if args.apply else _state_path(args, config)
    store = StateStore(state_dir)
    jobs = IntegrationJobs(store)
    report = None
    if args.apply:
        git = ReadOnlyGit(Path(args.repo), config, LockSet(state_dir / "locks"),
                          timeout=config.github.publish.git_timeout_seconds)

        def tracker() -> Any:
            """A plain GitHub client: no integration anchors are bound here, so it can send no integration write."""
            from .github_commands import _tracker  # pylint: disable=import-outside-toplevel,protected-access
            return _tracker(args, config, state_dir)

        report = IntegrationMerger(store, config, git, verifier=_verifier(args, config, state_dir),
                                   tracker=tracker).settle(args.job, apply=True, actor=actor)
    selected = [jobs.load(args.job)] if args.job else [job for job in jobs.list() if not job.terminal]
    rows = {row["job"]: row for row in schedule_view(store, config, time.time())}
    found = [diagnose(job, config, now=now_utc(), schedule=rows.get(job.job_id),
                      running=job_lock_busy(store, job.job_id) if args.job else None) for job in selected]
    lines: list[str] = []
    if report is not None:
        lines.append(f"recover: {report.outcome}" + ("" if report.changed else " (unchanged)"))
        lines += [f"  [{c['status']:14s}] {c['check']}: {c['detail']}" for c in report.checks]
        lines += [f"  - {reason}" for reason in report.reasons] + report.messages
    for item in found:
        lines += diagnosis_lines(item)
    if not found:
        lines.append("no open integration jobs")
    if report is None:
        lines.append("diagnosis only — nothing was read from GitHub and nothing was changed"
                     + (f" (`integration recover --job {args.job} --apply` settles an unfinished merge run)"
                        if args.job else ""))
    outcome = report.outcome if report is not None else "diagnosed"
    ok = report is None or outcome in SETTLED_OUTCOMES
    _emit(args, {"ok": ok, "outcome": outcome, "applied": bool(args.apply),
                 "changed": bool(report and report.changed), "reasons": report.reasons if report else [],
                 "checks": report.checks if report else [], "messages": report.messages if report else [],
                 "jobs": found}, lines)
    return 0 if ok else 1


def _row_lines(row: dict[str, Any]) -> list[str]:
    head = f"{row['job']}  {row['state']:24s} {row['category']:10s}"
    if row["category"] in ("running", "unsettled"):
        run = row["running"]
        return [f"{head} `{run['stage']}` launch {run['launch']} ({run['state']}, pid {run['pid']}, deadline "
                f"{run['deadline']})"]
    if row["category"] == "held":
        return [f"{head} HELD: {row['hold']['reason']}",
                f"    `integration schedule --job {row['job']} --resume --reason TEXT --apply` after fixing the cause"]
    if row["category"] == "delayed":
        return [f"{head} `{row['stage']}` again at {row['next_attempt_at']} (changed nothing {row['stalls']}x)"]
    if row["category"] == "ended":
        return [f"{head} NEEDS ATTENTION: {row['reason']}", f"    {row['action']}"]
    who = "Worker" if row["category"] == "ready" else "operator" + (", URGENT" if row["urgent"] else "")
    return [f"{head} next ({who}): {row['reason']}"] + ([f"    {row['action']}"] if row["action"] else [])


@_json_errors
def cmd_schedule(args: argparse.Namespace, config: ProjectConfig) -> int:
    """(M6) The Worker's view of the open integration jobs; --resume clears one job's hold (an operator decision)."""
    actor = check_actor(args.actor)
    if args.resume and not (args.job and args.reason):
        raise CoordinatorError("--resume needs --job JOB and --reason TEXT (recorded with the decision)")
    if (args.reason or args.apply) and not args.resume:
        raise CoordinatorError("--reason and --apply are only used with --resume")
    state_dir = _writable_state(args, config) if args.apply else _state_path(args, config)
    store = StateStore(state_dir)
    outcome = "listed"
    if args.resume:
        outcome = _resume(store, args.job, check_reason(args.reason), actor, args.apply)
    rows = [row for row in schedule_view(store, config, time.time()) if not args.job or row["job"] in (args.job, "?")]
    cfg = config.github.integration.worker
    lines = [f"Worker scheduling of integration jobs: {'ON' if cfg.enabled else 'OFF'}"
             + (f" (stages: {', '.join(cfg.stages)}; the merge is never scheduled)" if cfg.enabled else
                " (github.integration.worker.enabled is false: every step below is the operator's)")]
    if args.resume:
        lines.append(f"schedule: {outcome}" + ("" if args.apply else " — dry run, nothing was changed (pass --apply)"))
    for row in rows:
        lines += _row_lines(row)
    if not rows:
        lines.append("no open integration jobs" if not args.job else f"{args.job} is terminal: nothing is scheduled")
    _emit(args, {"ok": True, "outcome": outcome, "enabled": cfg.enabled, "stages": list(cfg.stages), "jobs": rows},
          lines)
    return 0


def _resume(store: StateStore, job_id: str, reason: str, actor: str, apply: bool) -> str:
    """Clear the job's hold, delay and counters, and acknowledge a terminal outcome a scheduled stage produced. A
    schedule record that cannot be read is set aside (kept beside it as evidence) and replaced by none.

    Serialized with the scheduling step through its lock (`integration-worker.lock`): while a step is between
    recording a launch and starting its process, nothing may discard that record — so --apply is refused while a
    step runs, and with the lock held a `running` entry without a living process is known to be nobody's launch in
    flight. Refused while a stage process (or the reviewer it started) of the job lives."""
    job = IntegrationJobs(store).load(job_id)   # an unknown or unreadable job is refused
    lock = FileLock(store.locks.dir / STEP_LOCK)
    if apply:
        try:
            lock.acquire(timeout=STEP_WAIT_SECONDS)
        except LockBusyError as exc:
            raise CoordinatorError("a scheduling step of the Worker is running right now; nothing was changed — "
                                   "repeat the command") from exc
    try:
        try:
            rec, corrupt = load_schedule(store, job_id), ""
        except StateDirError as exc:
            rec, corrupt = Schedule(id=job_id), str(exc)
        if live_processes(store, rec):   # its stage process, processes it left, or a reviewer of its review stage
            raise CoordinatorError(f"a stage process of {job_id} still runs ({live_processes(store, rec)}): nothing is "
                                   "resumed while it lives")
        pending = bool(rec.hold or rec.stalls or rec.launches or rec.next_attempt_at is not None or rec.running
                       or unacknowledged_end(rec, job))
        if not apply:
            return "would-reset" if corrupt else "would-resume" if pending else "nothing-held"
        if corrupt:   # its tag cannot be trusted: no stage process at all, and no reviewer of this job, may live
            alive = any_stage_processes() + sorted({
                pid for entry in job.evidence if entry["kind"] == "review-attempt"   # (the strictly loaded job)
                for pid in tagged_processes(REVIEW_TAG_ENV, entry["tag"])})
            if alive:
                raise CoordinatorError(f"the schedule record of {job_id} cannot be read and stage processes {alive} "
                                       "are alive (stage processes of this checkout's user, reviewers of this job): "
                                       "it cannot be told whether one of them is this job's stage, so the record is "
                                       "not set aside while they live — wait for them or stop them")
        with store.transaction():
            if corrupt:
                path = schedule_path(store, job_id)
                aside = path.with_name(f"{path.name}.unreadable-{utc_now().replace(':', '')}")
                path.rename(aside)
                store.append_event("integration-schedule-reset", job=job_id, by=actor, reason=reason, kept=aside.name)
                return "reset"
            current = load_schedule(store, job_id)
            if live_processes(store, current):   # read again in the critical section a stage confirms its launch in
                raise CoordinatorError(f"a stage process of {job_id} appeared meanwhile "
                                       f"({live_processes(store, current)}): nothing was resumed")
            if not resume(current, job, by=actor, reason=reason, at=utc_now()):
                return "nothing-held"
            save_schedule(store, current)
            store.append_event("integration-schedule-resumed", job=job_id, by=actor, reason=reason,
                               acknowledged=(current.acknowledged or {}).get("state"))
        return "resumed"
    finally:
        lock.release()


def register(sub: Any) -> None:
    """Add the `integration` command group to the CLI's subparsers: M1–M5's commands (integration_commands.register)
    plus `integration recover` and `integration schedule`."""
    isub = _commands.register(sub)
    recover = isub.add_parser("recover", help="(M6) diagnose interrupted jobs; --apply settles an unfinished merge "
                                              "run from GitHub's state (it never writes to GitHub)")
    recover.add_argument("--job", default=None, help="one job (required with --apply)")
    recover.add_argument("--actor", default="operator", help="who recovers (recorded in the history)")
    recover.add_argument("--apply", action="store_true",
                         help="reconcile and verify, and record the result (default: diagnosis only)")
    recover.add_argument("--json", action="store_true")
    recover.set_defaults(func=cmd_recover)
    schedule = isub.add_parser("schedule", help="(M6) the Worker's view of the open jobs; --resume clears a hold")
    schedule.add_argument("--job", default=None)
    schedule.add_argument("--resume", action="store_true", help="clear the job's hold, delay and launch counters")
    schedule.add_argument("--reason", default=None, help="why the operator resumes (recorded)")
    schedule.add_argument("--actor", default="operator", help="who resumes (recorded)")
    schedule.add_argument("--apply", action="store_true", help="with --resume: record it (default: dry run)")
    schedule.add_argument("--json", action="store_true")
    schedule.set_defaults(func=cmd_schedule)
