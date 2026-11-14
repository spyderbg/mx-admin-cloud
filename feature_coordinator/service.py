"""W4: the autonomous Worker service — one long-running loop per state directory (checkout).

`python -m feature_coordinator worker start` (systemd --user runs exactly this). Each cycle:

  1. heartbeat; operator requests (worker-control: pause, stop); a stop signal; W4.3.3: when `github.publish.enabled`,
     a publication pass (publish.py: an approved Issue's verified feature is integrated, pushed, opened as ONE Draft
     PR, and completed once a person merged it); W4.6.1 R2: when `github.review.enabled`, the AI review step
     (review_worker.py: starts a reviewer process for a verified Draft PR, watches it and collects its result on a
     later cycle — it never waits for the model); W4.3.1: when `github.intake.enabled`, at most every
     `poll_interval_seconds` a GitHub intake poll (intake.py): an Issue an operator approved with `/worker approve`
     is planned and, on a free checkout, activated — the approval is the operator's plan and activation (W4.5: also
     `worker:*` labels);
  2. the active feature — only one an operator planned AND activated; nothing else is started;
  3. the checkout: workspace identity/branch/base (W2), the feature's Worker lock (so `python -m feature_coordinator
     work` cannot drive it at the same time), and claims: a LIVE claim (a manual launch, or a launch of an earlier
     service instance) means wait — never a second writer; a stale claim is recovered (W3, orphan-aware); an
     orphaned agent is waited for, and stopped only when its identity is certain and its run deadline has passed;
  4. eligible tasks (READY, dependencies verified, role enabled, no attention record, next attempt due) → ONE task:
     pre-launch checks (CLI present, subscription login — never an API key, checkpoint consistency, launch budget;
     W4.3.1: an approved Issue's task only while the Issue is open and still matches the approved text, re-read now
     — and re-read while it runs: a withdrawn approval stops the run through the existing cancellation), the launch
     counted BEFORE it starts (write-ahead), `python -m feature_coordinator launch TASK --apply` as a subprocess
     (the existing W2/W3 path: claim, agent, acceptance, checkpoints, gates), watched with heartbeats and a watchdog;
  5. after the launch: the agent has stopped (no process carries its run tags), its claim is
     released, the branch was not rewritten, no foreign commit or unexplained file change
     appeared, its checkpoints are consistent — else CRITICAL; then the outcome is classified
     and the bounded retry policy applied (autonomy.py); only then is the next task considered;
  6. nothing eligible → WAITING (until the next retry) / NEEDS_ATTENTION / AVAILABLE, idle.

One agent at a time is ENFORCED, not only scheduled: the service holds the `agent-exclusive` flag, so any claim — the
service's own launch or a manual one — is refused while another claim exists on the checkout (claims.py). A second
service on the same state directory is refused (worker-service.lock, exit 73). Startup failures are counted per
cause; the same cause `startup_failure_limit` times in a row exits 78, which the systemd unit does not restart.

Stop: SIGTERM / `worker stop` cancels the active agent gracefully (W3: completed checkpoints stay; the interrupted
step runs again later) and exits; W4.6.1 R2: a running reviewer process is asked to stop first and records its
attempt as cancelled (given back). `worker stop --after-current` lets the agent finish first. Pause stops
scheduling; an active launch finishes (a running reviewer is cancelled through the control file).
"""

from __future__ import annotations

import json
import re
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

from .autonomy import (
    AVAILABLE,
    BUSY,
    CANCELLED,
    CRITICAL,
    EXIT_ALREADY_RUNNING,
    EXIT_DO_NOT_RESTART,
    INTERRUPTED,
    NEEDS_ATTENTION,
    OFFLINE,
    PAUSED,
    RECOVERING,
    STOPPING,
    SUBSCRIPTION,
    SUCCESS,
    TRANSIENT,
    UNKNOWN,
    WAITING,
    Outcome,
    Scheduling,
    ServiceRecord,
    apply_outcome,
    blocking_survivors,
    claim_view,
    classify,
    eligible_tasks,
    epoch,
    hung_after_seconds,
    iso,
    load_scheduling,
    load_service,
    new_service_record,
    process_start_epoch,
    read_control,
    safe_identity,
    scrub,
    task_status,
)
from .checkpoints import classify_uncommitted, inspect, load_record, recover_task
from .claims import drop_exclusive, heartbeat_age, hold_exclusive, orphaned_agents
from .config import ProjectConfig
from .errors import ClaimError, CoordinatorError, LockBusyError
from .locks import FileLock, ProcessIdentity
from .model import Feature, FeatureState, Task, TaskState, transition_task, utc_now
from .store import atomic_write_json
from .supervisor import RUN_TAG_ENV, stop_requested, tagged_processes, terminate_pids, wait_for_wake
from .workspace import workspace_problems
from .workspace_hold import hold_problems

LAUNCH_TAG_ENV = "FC_WORKER_LAUNCH"  # set on each launch process: found again after a service crash
_OUTCOME_EVENTS = {SUCCESS: "worker-task-verified", TRANSIENT: "worker-retry-scheduled",
                   SUBSCRIPTION: "worker-subscription-unavailable"}


class StartupRefused(CoordinatorError):
    """The service must not run with this configuration (restarting cannot help)."""


class WorkerService:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """The autonomous Worker. See the module docstring."""

    def __init__(self, config: ProjectConfig, repo: Path, state_dir: Path, *, coord: Any = None,  # pylint: disable=too-many-arguments
                 spawn: Callable[[str, int], Any] | None = None, clock: Callable[[], float] = time.time,
                 idle_sleep: Callable[[float], Any] | None = None, poll_sleep: Callable[[float], Any] = time.sleep,
                 poll_seconds: float = 0.5, echo: Callable[[str], None] = print, max_cycles: int | None = None,
                 stop_when: Callable[["WorkerService"], bool] | None = None, project_config: str | None = None,
                 tracker: str = "local") -> None:
        if coord is None:
            from .operations import build_coordinator  # pylint: disable=import-outside-toplevel
            coord = build_coordinator(config, repo, state_dir, tracker_kind=tracker)
        if spawn is None:
            from .worker import cli_spawner  # pylint: disable=import-outside-toplevel
            spawn = cli_spawner(repo, state_dir, tracker=tracker, project_config=project_config)
        self.config, self.cfg, self.coord, self.store = config, config.worker, coord, coord.store
        self.spawn, self.clock, self.poll_sleep, self.poll_seconds = spawn, clock, poll_sleep, poll_seconds
        self.idle_sleep = idle_sleep or wait_for_wake
        self.echo, self.max_cycles, self.stop_when = echo, max_cycles, stop_when
        self.instance = secrets.token_hex(6)
        self.record: ServiceRecord | None = None
        self.cycles = 0
        self.launched: list[tuple[str, int]] = []
        self._beat_at = 0.0
        self._feature_lock: FileLock | None = None
        self._feature_id = ""
        self._exclusive = False
        self._stop_reason = ""
        self._cycle_errors = 0
        self._uncertain: dict[str, set[int]] = {}  # processes that survived SIGKILL: not signalled again
        self._polled_at: dict[str, float] = {}  # W4.3.1/W4.3.3: the last GitHub polls (service clock)
        self._poll_errors: dict[str, str] = {}
        self._publication: tuple[str, str] | None = None  # W4.3.3: what a published feature waits for
        self._waits: dict[str, Any] = {}  # W4.6.1 R2: each GitHub step's idle state (combined: _publication)
        self._intake_watch_at = 0.0           # W4.3.1: the last re-read of a running task's approved Issue

    # -- the whole lifetime ------------------------------------------------------------------

    def run(self) -> int:
        """Run until stopped. Returns the process exit code (0, 1, 73 or 78)."""
        lock = self.store.locks.service()
        try:
            lock.acquire()
        except LockBusyError:
            self.echo("worker: refused — another Worker service already runs for this state directory")
            return EXIT_ALREADY_RUNNING
        try:
            code = self._startup()
            if code is not None:
                return code
            while not self._should_stop():
                try:
                    self._cycle()
                    self._cycle_errors = 0
                except (CoordinatorError, OSError, ValueError, subprocess.SubprocessError) as exc:
                    self._cycle_failed(exc)
                self.cycles += 1
                if self.stop_when and self.stop_when(self):
                    self._stop_reason = self._stop_reason or "stop condition reached"
                    break
                if self.max_cycles and self.cycles >= self.max_cycles:
                    self._stop_reason = self._stop_reason or "cycle limit reached"
                    break
            return 0
        finally:
            self._shutdown()
            lock.release()

    def _should_stop(self) -> bool:
        if stop_requested():
            self._stop_reason = "stop signal"
            return True
        if read_control(self.store).get("stop"):
            self._stop_reason = f"operator stop ({read_control(self.store).get('stop')})"
            return True
        return False

    # -- startup and shutdown ------------------------------------------------------------------

    def _startup(self) -> int | None:
        try:
            if not self.cfg.autonomous_enabled:
                raise StartupRefused("worker.autonomous.enabled is false: the autonomous service is off until an "
                                     "operator enables it in the project configuration")
            if self.cfg.max_parallel != 1:
                raise StartupRefused("worker.max_parallel must be 1: the W4 service runs one agent at a time "
                                     "(explicit parallelism: `python -m feature_coordinator work --max-parallel N`)")
            record = load_service(self.store) or new_service_record()
            me = ProcessIdentity.current()
            record.identity, record.instance, record.started_at = me.to_dict(), self.instance, utc_now()
            record.heartbeat_at, record.current = iso(self.clock()), None  # the service's clock
            self.record = record
            with self.store.transaction():
                control = read_control(self.store)
                if control.get("stop"):  # a stop request applies to a running instance only
                    control["stop"] = None
                    self.store.set_flag("worker-control", control)
            hold_exclusive(self.store, f"worker-service {record.worker_id}")
            self._exclusive = True
            self._set_state(RECOVERING, "startup: inspecting interrupted work")
            self._event("worker-started", pid=me.pid, instance=self.instance, worker_id=record.worker_id)
            self._log(f"started (pid {me.pid}, instance {self.instance})")
            if self.cfg.auto_recover_safe:
                self._recover_at_startup()
        except StartupRefused as exc:
            self._startup_failed(exc)
            self._log(f"refused to start: {exc} (exit {EXIT_DO_NOT_RESTART}: do not restart)")
            return EXIT_DO_NOT_RESTART
        except (CoordinatorError, OSError, ValueError, KeyError) as exc:
            count = self._startup_failed(exc)
            final = count >= self.cfg.startup_failure_limit
            self._log(f"startup failed ({count}x in a row): {exc}"
                      + (f" — exit {EXIT_DO_NOT_RESTART}: repeated identical failure, do not restart" if final else ""))
            return EXIT_DO_NOT_RESTART if final else 1
        self._startup_ok()
        return None

    def _startup_path(self) -> Path:
        return self.store.root / "worker-startup.json"

    def _startup_failed(self, exc: Exception) -> int:
        cause = f"{type(exc).__name__}: {re.sub(r'[0-9]+', 'N', str(exc))[:200]}"
        path = self._startup_path()
        try:
            last = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            last = {}
        count = last.get("consecutive", 0) + 1 if last.get("cause") == cause else 1
        try:
            atomic_write_json(path, {"cause": cause, "consecutive": count, "last_reason": scrub(str(exc), 500),
                                     "last_at": utc_now()})
        except OSError:
            pass  # the state directory itself is unusable: systemd's StartLimitBurst still bounds restarts
        return count

    def _startup_ok(self) -> None:
        try:
            atomic_write_json(self._startup_path(), {"cause": None, "consecutive": 0, "last_ok_at": utc_now()})
        except OSError:
            pass

    def _shutdown(self) -> None:
        if self.record is not None:
            try:
                with self.store.transaction():
                    control = read_control(self.store)
                    if control.get("stop"):  # consumed: a later start runs normally
                        control["stop"] = None
                        self.store.set_flag("worker-control", control)
                self.record.current = None
                self._set_state(OFFLINE, f"stopped: {self._stop_reason or 'exited'}")
                self._event("worker-stopped", reason=self._stop_reason, cycles=self.cycles)
                self._log(f"stopped ({self._stop_reason or 'exited'})")
            except CoordinatorError as exc:
                self._log(f"shutdown bookkeeping failed: {exc}")
        from .review_worker import stop_reviews  # pylint: disable=import-outside-toplevel
        stop_reviews(self.coord, self._log)  # W4.6.1 R2: a reviewer process records its cancellation first
        if self._exclusive:
            drop_exclusive(self.store)
            self._exclusive = False
        self._release_feature()

    # -- one cycle ---------------------------------------------------------------------------------

    def _cycle(self) -> None:  # pylint: disable=too-many-return-statements
        now = self.clock()
        self._beat(now)
        if read_control(self.store).get("pause"):
            self._set_state(PAUSED, "paused by the operator: no new work is started")
            self._idle(self.cfg.idle_poll_seconds)
            return
        self._poll_github(now)
        feature = self._active_feature()
        if feature is None:
            return
        if not self._own_feature(feature):
            self._set_state(WAITING, f"another Worker (`python -m feature_coordinator work`) drives {feature.id}")
            self._idle(self.cfg.idle_poll_seconds)
            return
        problems = workspace_problems(self.coord.root, feature) + hold_problems(self.store, self.coord.root)  # S1
        if problems:
            self._set_state(NEEDS_ATTENTION, "workspace: " + "; ".join(problems))
            self._idle(self.cfg.idle_poll_seconds)
            return
        if not self._claims_clear():
            self._idle(self.cfg.idle_poll_seconds)
            return
        tasks = self.store.load_tasks(feature)
        schedules = self._schedules(tasks, now)
        settled, running = self._settle_in_flight(tasks, schedules)
        if running:
            self._set_state(WAITING, f"a launch of an earlier service instance still runs ({', '.join(running)}): "
                                     "no second agent is started")
            self._idle(self.cfg.idle_poll_seconds)
            return
        if settled:
            return  # outcomes recorded; look again with fresh state
        blocking = {t: pids for t, rec in schedules.items() for pids in [blocking_survivors(rec)] if pids}
        if blocking:
            self._set_state(NEEDS_ATTENTION, f"processes of an earlier run are still alive and could not be stopped: "
                                             f"{blocking}; no agent is started while they may modify the workspace")
            self._idle(self.cfg.idle_poll_seconds)
            return
        candidates = eligible_tasks(self.config, tasks, schedules, now)
        if candidates and not hold_problems(self.store, self.coord.root, self.config):  # S1: inventory, then launch
            self._run_task(feature, candidates[0])
            return
        state, reason, wake = self._waiting_state(feature, tasks, schedules, now)
        self._set_state(state, reason)
        self._idle(min(self.cfg.idle_poll_seconds, max(wake - now, 0.01)) if wake else self.cfg.idle_poll_seconds)

    def _poll_github(self, now: float) -> None:
        """Each step at most every its poll_interval_seconds, in `review_worker.github_steps` order: publication,
        the AI review, intake (why: see there). A failure is recorded once per distinct error; the service goes on."""
        from .intake_poll import planning_hooks  # pylint: disable=import-outside-toplevel
        from .review_worker import github_steps, idle_state  # pylint: disable=import-outside-toplevel
        hooks = planning_hooks(self.store, lambda: self._beat(self.clock()))  # W4.5: while a planner runs
        for name, cfg, poller in github_steps(self.config, self.coord, hooks, getattr(self.spawn, "start", None)):
            if not cfg.enabled or now - self._polled_at.get(name, float("-inf")) < cfg.poll_interval_seconds:
                continue
            self._polled_at[name] = now
            try:
                report = poller().poll(apply=True)
                lines, error = report.lines(), "; ".join(report.deferred)
                self._waits[name] = getattr(report, "service_state", lambda: None)()
            except CoordinatorError as exc:
                error = scrub(f"{type(exc).__name__}: {exc}", 500)
                lines = [f"deferred: {error}"]
            if error and error != self._poll_errors.get(name, ""):
                self._event(f"worker-{name}-deferred", error=scrub(error, 500))
            self._poll_errors[name] = error
            for line in lines:
                self._log(f"{name}: {line}")
        self._publication = idle_state(self._waits)

    def _intake_changed(self, task: Task) -> bool:
        """W4.3.1: a task from an approved GitHub Issue starts only while that Issue still matches the
        approved text (re-read now). Changed → the intake holds the task (needs-human); the Issue
        could not be read → a transient wait. True = do not launch."""
        from .intake_poll import IntakePoller  # pylint: disable=import-outside-toplevel
        try:
            reason = IntakePoller(self.coord).verify_task(task)
        except CoordinatorError as exc:
            self._apply(task.id, Outcome(TRANSIENT, f"the approved GitHub Issue could not be re-read before the "
                                                    f"launch: {exc}"), None)
            return True
        self._intake_watch_at = self.clock()
        if reason:
            self._event("worker-intake-held", task=task.id, reason=scrub(reason, 300))
            self._log(f"{task.id}: not started — {reason}; held for an operator")
            return True
        return False

    def _watch_intake(self, task: Task) -> None:
        """W4.3.1: while an approved Issue's task runs, re-read the Issue every poll interval (the
        service holds no coordinator lock). A changed or closed Issue is recorded and the run asked to
        stop through the existing graceful cancellation; its result is not accepted (workflow.py).
        A read failure is only logged: the run's own boundaries check again."""
        now = self.clock()
        if now - self._intake_watch_at < self.config.github.intake.poll_interval_seconds:
            return
        self._intake_watch_at = now
        from .intake_poll import IntakePoller  # pylint: disable=import-outside-toplevel
        try:
            reason = IntakePoller(self.coord).verify_task(task)
        except CoordinatorError as exc:
            self._log(f"{task.id}: the approved GitHub Issue could not be re-read while running: {exc}")
            return
        if reason:
            self._event("worker-intake-stopping", task=task.id, reason=scrub(reason, 300))
            self._log(f"{task.id}: stopping — {reason}; completed checkpoints stay, the task is held")

    def _cycle_failed(self, exc: Exception) -> None:
        """An unexpected error in one cycle: recorded, NEEDS_ATTENTION, looked at again after an
        idle interval (never a tight loop; a launch in progress keeps its claim meanwhile)."""
        self._cycle_errors += 1
        reason = scrub(f"a cycle failed ({self._cycle_errors}x in a row): {type(exc).__name__}: {exc}", 600)
        if self._cycle_errors == 1:
            self._event("worker-cycle-failed", error=reason)
        if self.record is not None:
            self.record.last_error = {"task": None, "category": CRITICAL, "reason": reason, "at": utc_now()}
            self.record.current = None
        try:
            self._set_state(NEEDS_ATTENTION, reason.replace(f"{self._cycle_errors}x", "repeatedly")
                            if self._cycle_errors > 1 else reason)
        except (CoordinatorError, OSError):  # the state directory itself fails: still say why
            self._log(reason)
        self._idle(self.cfg.idle_poll_seconds)

    def _schedules(self, tasks: dict[str, Task], now: float) -> dict[str, Scheduling]:
        """Each task's scheduling record. An unreadable one is kept on disk as evidence; its task
        alone is held for the operator (in memory), the other tasks continue."""
        schedules = {}
        for task_id, task in tasks.items():
            try:
                schedules[task_id] = load_scheduling(self.store, task)
            except CoordinatorError as exc:
                schedules[task_id] = Scheduling(id=task_id, feature_id=task.feature_id, attention={
                    "category": CRITICAL, "reason": scrub(f"unreadable scheduling record: {exc}", 600),
                    "since": iso(now), "action": f"inspect and repair or remove scheduling/{task_id}.json "
                                                 "(it is kept as evidence), then restart the service"})
        return schedules

    def _active_feature(self) -> Feature | None:
        active = [f for f in self.store.list_features() if f.state is FeatureState.ACTIVE]
        if len(active) > 1:
            self._set_state(NEEDS_ATTENTION, f"several active features {[f.id for f in active]} (one per checkout)")
        elif not active:  # W4.3.3: a published feature may wait for its merge (or an operator) meanwhile
            self._release_feature()
            self._set_state(*(self._publication or (AVAILABLE, "no active feature: waiting for an approved, activated "
                                                               "assignment")))
        else:
            return active[0]
        self._idle(self.cfg.idle_poll_seconds)
        return None

    def _own_feature(self, feature: Feature) -> bool:
        if self._feature_id == feature.id and self._feature_lock is not None:
            return True
        self._release_feature()
        lock = self.store.locks.worker(feature.id)
        try:
            lock.acquire()
        except LockBusyError:
            return False
        self._feature_lock, self._feature_id = lock, feature.id
        return True

    def _release_feature(self) -> None:
        if self._feature_lock is not None:
            self._feature_lock.release()
        self._feature_lock, self._feature_id = None, ""

    def _claims_clear(self) -> bool:
        """True when no claim exists on the checkout. A live one: wait (or, for a hung launch of an
        earlier service instance, continue the watchdog's termination). Stale ones: W3 recovery.
        An unreadable holder identity: nothing is assumed (fail closed)."""
        claims = self.store.list_claims()
        if not claims:
            return True
        live: dict[str, tuple[dict[str, Any], ProcessIdentity]] = {}
        stale, ambiguous = [], []
        for task_id, rec in claims.items():
            holder = safe_identity(rec.get("identity"))
            if holder is None:
                ambiguous.append(task_id)
            elif holder.is_alive():
                live[task_id] = (rec, holder)
            else:
                stale.append(task_id)
        if live:
            task_id, (rec, holder) = next(iter(live.items()))
            self._live_claim(task_id, rec, holder)
            return False
        if ambiguous:
            self._set_state(NEEDS_ATTENTION, f"the claims of {ambiguous} have no readable holder identity: their "
                                             "processes cannot be verified, so nothing is recovered, stopped or "
                                             "started automatically (`python -m feature_coordinator recover TASK`)")
            return False
        report = self.coord.claims.recover(task_ids=stale)
        for rec in report.recovered:
            self._event("worker-recovered", task=rec["task_id"], pid=rec["identity"]["pid"])
            self._log(f"recovered {rec['task_id']}: its worker (pid {rec['identity']['pid']}) is gone")
        for rec in report.orphaned:
            self._orphan(rec)
        return not self.store.list_claims()

    def _live_claim(self, task_id: str, rec: dict[str, Any], holder: ProcessIdentity) -> None:
        """A live process holds a claim: no second writer. Its heartbeat tells slow from hung; only
        a launch of an earlier instance of THIS service (its identity, and the launch tag recorded
        before it started) is stopped by the watchdog — never a launch started by anyone else."""
        runtime = self.config.runtime
        age = heartbeat_age(rec) or 0.0
        flight = self._earlier_flight(task_id)
        ours = bool(flight and flight.get("tag") and holder.pid in tagged_processes(LAUNCH_TAG_ENV, flight["tag"]))
        decided = ours and (flight.get("watchdog") or {}).get("pid") == holder.pid  # recorded for THIS process
        if ours and (decided or age > hung_after_seconds(runtime)):
            self._stop_earlier_launch(task_id, holder, flight, age)
        elif age > hung_after_seconds(runtime):
            self._set_state(NEEDS_ATTENTION, f"the launch holding {task_id} (pid {holder.pid}) has not heartbeated for "
                                             f"{age:.0f}s (hung?); this service did not start it, so it is not "
                                             f"stopped automatically (`python -m feature_coordinator stop {task_id}`)")
        elif age > runtime.stale_heartbeat_seconds:
            self._set_state(WAITING, f"another agent is active on this checkout: {task_id} (pid {holder.pid}), no "
                                     f"heartbeat for {age:.0f}s (slow: a gate or a long step) — no second writer "
                                     "is started")
        else:
            self._set_state(WAITING, f"another agent is active on this checkout: {task_id} (pid {holder.pid}) — "
                                     "no second writer is started")

    def _earlier_flight(self, task_id: str) -> dict[str, Any] | None:
        """The in-flight launch an EARLIER service instance recorded for *task_id* (None if none)."""
        try:
            flight = load_scheduling(self.store, self.store.load_task(task_id)).in_flight
        except CoordinatorError:
            return None
        return flight if flight and flight.get("instance") != self.instance else None

    def _stop_earlier_launch(self, task_id: str, holder: ProcessIdentity, flight: dict[str, Any], age: float) -> None:
        """The watchdog decided (now, or before this service restarted) that this launch is hung:
        record the decision first, then stop exactly that process (identity re-checked before every
        signal). Idempotent: a later instance continues from the recorded decision."""
        recorded = flight.get("watchdog") if (flight.get("watchdog") or {}).get("pid") == holder.pid else None
        if holder.pid in self._uncertain.get(task_id, set()):  # tried, SIGKILL included: no repeated signals
            self._cleanup_uncertain(task_id, [holder.pid], "the hung launch could not be confirmed terminated")
            return
        decision = dict(recorded or {"decision": "hung-launch", "pid": holder.pid, "since": utc_now(),
                                     "heartbeat_age": round(age, 1), "by": self.instance})
        if recorded is None:
            self._persist_watchdog(task_id, flight.get("launch"), decision)
        self._set_state(RECOVERING, f"stopping the hung launch {holder.pid} of {task_id} (earlier service instance, "
                                    f"decided {decision['since']})")
        self._event("worker-watchdog-hung", task=task_id, pid=holder.pid, heartbeat_age=round(age, 1),
                    earlier_instance=flight.get("instance"), resumed=recorded is not None)
        tag = str(flight["tag"])
        survivors = terminate_pids([holder.pid], grace_seconds=self.config.runtime.terminate_grace_seconds,
                                   still_ours=lambda pid: holder.is_alive()
                                   and pid in tagged_processes(LAUNCH_TAG_ENV, tag))
        self._event("worker-watchdog-terminated", task=task_id, pids=[holder.pid], survivors=survivors)
        if survivors:  # one bounded attempt per service instance; it keeps blocking (the claim stays)
            self._uncertain[task_id] = set(survivors)
            self._cleanup_uncertain(task_id, survivors, "the hung launch could not be confirmed terminated")

    def _persist_watchdog(self, task_id: str, launch: Any, decision: dict[str, Any]) -> None:
        """Write-ahead for the watchdog: the decision to terminate is durable BEFORE any signal."""
        with self.store.transaction():
            rec = load_scheduling(self.store, self.store.load_task(task_id))
            if rec.in_flight and rec.in_flight.get("launch") == launch:
                rec.in_flight["watchdog"] = decision
                self.store.save_record("scheduling", rec)

    def _cleanup_uncertain(self, task_id: str, pids: list[int], why: str) -> None:
        """Processes that may still modify the workspace could not be confirmed gone: everything
        stays blocked (their claim, or the task's blocking run tags) until they are."""
        reason = f"{why}: processes {pids} of {task_id} are still alive; no agent is started while they may " \
                 "modify the workspace"
        if self.record is not None:
            self.record.last_error = {"task": task_id, "category": "cleanup-uncertain", "pids": pids,
                                      "reason": scrub(reason, 500), "at": utc_now()}
        self._set_state(NEEDS_ATTENTION, reason)

    def _orphan(self, rec: dict[str, Any]) -> None:
        """An agent of a dead worker's run still lives: wait; stop it only with a certain identity
        after its run deadline (never an unidentified process)."""
        runtime = self.config.runtime
        agent = (rec.get("checkpoint") or {}).get("agent")
        identity = safe_identity(agent) if isinstance(agent, dict) else None
        started = process_start_epoch(identity) if identity else None
        deadline = started + runtime.task_timeout_seconds + runtime.terminate_grace_seconds if started else None
        pids, task_id = list(rec["orphans"]), rec["task_id"]
        if deadline is None:
            self._set_state(NEEDS_ATTENTION, f"processes {pids} of {task_id}'s interrupted run are alive and the "
                                             "agent's identity is unknown: not stopped automatically "
                                             f"(`python -m feature_coordinator recover {task_id}`)")
            return
        if not self.cfg.stop_orphans_after_deadline or time.time() < deadline:
            self._set_state(WAITING, f"an agent of {task_id}'s interrupted run is still running (pids {pids}); "
                                     + (f"it is stopped after its run deadline {iso(deadline)}"
                                        if self.cfg.stop_orphans_after_deadline else "waiting for it to end"))
            return
        if set(pids) <= self._uncertain.get(task_id, set()):  # already tried, SIGKILL included: stay blocked
            self._cleanup_uncertain(task_id, pids, "orphaned processes could not be stopped")
            return
        survivors = terminate_pids(pids, grace_seconds=runtime.terminate_grace_seconds,
                                   still_ours=lambda pid: pid in set(orphaned_agents(rec)))
        self._event("worker-orphans-stopped", task=task_id, pids=pids, survivors=survivors, deadline=iso(deadline))
        self._log(f"stopped orphaned agent processes {pids} of {task_id} (past their run deadline)")
        if survivors:
            self._uncertain[task_id] = set(survivors)
            self._cleanup_uncertain(task_id, survivors, "orphaned processes could not be stopped")
        else:
            self.coord.claims.recover(task_ids=[task_id])

    def _recover_at_startup(self) -> None:
        """W3 recovery for the active feature: stale claims, launches of earlier instances,
        checkpoint consistency (conflicts → attention; nothing is repaired that W3 calls unsafe)."""
        active = [f for f in self.store.list_features() if f.state is FeatureState.ACTIVE]
        self._claims_clear()
        for feature in active[:1]:
            tasks = self.store.load_tasks(feature)
            schedules = self._schedules(tasks, self.clock())
            self._settle_in_flight(tasks, schedules)
            for task in tasks.values():
                if task.state is TaskState.VERIFIED or self.store.load_claim(task.id) or not task.steps \
                        or schedules[task.id].attention:
                    continue
                problem = self._checkpoint_problem(task)
                if problem:
                    self._apply(task.id, problem, None)
        self._event("worker-recovery-done", features=[f.id for f in active])

    def _settle_in_flight(self, tasks: dict[str, Task], schedules: dict[str, Any]) -> tuple[bool, list[str]]:
        """Launches recorded by an EARLIER service instance whose outcome was never recorded (the
        service died): still running (its launch process carries the recorded tag, claimed or not
        yet) → wait; else classify it now from the task and its events. The launch stays counted."""
        settled, running = False, []
        for task_id, rec in schedules.items():
            flight = rec.in_flight
            if not flight or flight.get("instance") == self.instance:
                continue
            if self.store.load_claim(task_id) or (flight.get("tag") and tagged_processes(LAUNCH_TAG_ENV,
                                                                                          flight["tag"])):
                running.append(task_id)
                continue
            since = flight.get("started_at") or ""
            events = [e for e in self.store.events() if e.get("task") == task_id and e.get("at", "") >= since]
            outcome = classify(tasks[task_id], tasks[task_id], events, exit_code=None,
                               log_text=self._launch_log(task_id, flight.get("launch")),
                               run_texts=self._run_texts(task_id, epoch(since) or 0.0))
            if flight.get("watchdog") and outcome.category in (UNKNOWN, INTERRUPTED, CANCELLED):
                outcome = Outcome(TRANSIENT, "watchdog: the launch was hung and was stopped "
                                             f"({flight['watchdog'].get('since')})")
            elif outcome.category == UNKNOWN and tasks[task_id].state is TaskState.READY:
                # no evidence of what happened: the run was cut short by the service's loss
                outcome = Outcome(INTERRUPTED, "the launch of an earlier service instance ended while the service "
                                               "was down")
            self._apply(task_id, outcome, flight.get("launch"))
            settled = True
        return settled, running

    def _waiting_state(self, feature: Feature, tasks: dict[str, Task], schedules: dict[str, Any],
                       now: float) -> tuple[str, str, float | None]:
        statuses = {t: task_status(task, schedules[t], None, now) for t, task in tasks.items()}
        if all(s == "verified" for s in statuses.values()):
            return AVAILABLE, f"feature {feature.id} is complete: ready for integration " \
                              f"(`python -m feature_coordinator integrate {feature.id} --apply`)", None
        waits = sorted((rec.next_attempt_at, t) for t, rec in schedules.items()
                       if rec.next_attempt_at and rec.next_attempt_at > now and not rec.attention)
        attention = sorted(t for t, s in statuses.items() if s == "needs-attention")
        if waits:
            when, task_id = waits[0]
            kind = (schedules[task_id].waiting or {}).get("category", "retry")
            extra = f"; needing an operator: {attention}" if attention else ""
            return WAITING, f"next attempt {iso(when)} ({task_id}: {kind}){extra}", when
        if attention:
            return NEEDS_ATTENTION, f"no task can progress without an operator: {attention}", None
        return WAITING, f"nothing eligible now: {statuses}", None

    # -- one task ------------------------------------------------------------------------------------

    def _run_task(self, feature: Feature, task: Task) -> None:
        rec = load_scheduling(self.store, task)
        self._event("worker-task-selected", task=task.id, role=task.role, launches_so_far=rec.launches,
                    attempts=rec.attempts)
        if rec.launches >= self.cfg.max_launches_per_task:
            self._apply(task.id, Outcome(CRITICAL, f"launch budget exhausted ({rec.launches} of "
                                                   f"{self.cfg.max_launches_per_task})"), None)
            return
        if self._intake_changed(task):
            return
        pre = self._preflight(task)
        if pre is not None:
            self._apply(task.id, pre, None)
            return
        with self.store.transaction():  # write-ahead: counted BEFORE the agent can start
            rec = load_scheduling(self.store, task)
            rec.launches += 1
            launch = rec.launches
            tag = f"{self.instance}.{launch}"
            rec.in_flight = {"launch": launch, "instance": self.instance, "started_at": utc_now(), "tag": tag,
                             "worker_id": self.record.worker_id if self.record else ""}
            self.store.save_record("scheduling", rec)
        started = time.time()  # wall time: compared with run directories' modification times
        mark = len(self.store.events())
        branch = f"refs/heads/{feature.branch}"
        head = self.coord.git.rev_parse(branch)
        foreign_before = self._foreign_dirty(task)
        profile = self.config.provider_for(task.role)
        assert self.record is not None
        self.record.current = {"feature": feature.id, "task": task.id, "role": task.role, "agent": profile.name,
                               "model": self.config.model_for(task.role) or "CLI default", "launch": launch,
                               "started_at": utc_now(), "step": None}
        self._set_state(BUSY, f"running {task.id} ({task.role} → {profile.name}, launch {launch})", force=True)
        self._event("worker-agent-started", task=task.id, role=task.role, agent=profile.name, launch=launch,
                    model=self.record.current["model"])
        self.launched.append((task.id, launch))
        handle = self.spawn(task.id, launch, env={LAUNCH_TAG_ENV: tag})
        exit_code, tags, decision = self._watch(task, handle, launch)
        self._event("worker-agent-stopped", task=task.id, launch=launch, exit_code=exit_code)
        outcome = self._judge(feature, task, mark, exit_code, tags, head, foreign_before, launch, started, decision)
        self._apply(task.id, outcome, launch)
        self.record.current = None
        self._beat(self.clock(), force=True)

    def _preflight(self, task: Task) -> Outcome | None:
        """Before an agent may start: its CLI and subscription login (never an API key), and the
        task's checkpoint consistency. No model request."""
        runner = self.coord.runners.get(task.role)
        if hasattr(runner, "probe"):  # the real Claude Code / Codex runners (mocks and remote runners: no CLI)
            from .launchers import probe_runner  # pylint: disable=import-outside-toplevel
            from .native_backend import check_subscription, native_environment  # pylint: disable=import-outside-toplevel
            profile = self.config.provider_for(task.role)
            probe = probe_runner(profile)
            if not probe.ok:
                if not probe.resolved:
                    return Outcome(TRANSIENT, probe.error or f"{profile.executable} is not installed")
                return Outcome(CRITICAL, probe.error or f"{probe.resolved} lacks required flags "
                                                        f"{list(probe.missing_flags)}")
            if self.config.runtime.execution_backend == "native-cli":
                auth = check_subscription(profile.kind, probe.resolved, native_environment())
                if not auth.ok:
                    return Outcome(CRITICAL, f"subscription authentication: {auth.method} — {auth.detail}")
        return self._checkpoint_problem(task) if task.steps else None

    def _checkpoint_problem(self, task: Task) -> Outcome | None:
        """W3 consistency of a multi-step task's checkpoints (an unreadable record is CRITICAL for this task only)."""
        try:
            conflicts = inspect(self.coord, task.id).conflicts
        except CoordinatorError as exc:
            return Outcome(CRITICAL, f"checkpoint state unreadable: {exc}")
        return Outcome(CRITICAL, "recovery conflict: " + "; ".join(conflicts)) if conflicts else None

    def _watch(self, task: Task, handle: Any, launch: int) -> tuple[int | None, set[str], dict[str, Any] | None]:  # pylint: disable=too-many-branches
        """Wait for the launch: heartbeats, the claim's progress, graceful stop, the watchdog.

        The agent's own deadline is enforced by the launch process (the Supervisor: SIGTERM to the
        agent's process group, grace, SIGKILL, then every process with the run tag). This watchdog
        covers the launch process itself: no claim heartbeat for longer than any legitimate silence
        (hung_after_seconds) → the decision is recorded, then the launch (our child: its identity is
        certain) gets SIGTERM, grace, SIGKILL, and its end is confirmed. A stale heartbeat below that
        threshold is only reported (a gate or a slow step)."""
        runtime = self.config.runtime
        hung_after = hung_after_seconds(runtime)
        tags: set[str] = set()
        cancelled = warned = False
        decision: dict[str, Any] | None = None
        quiet_since = time.time()  # the launch's last sign of life: spawn, or its claim's heartbeat
        while handle.poll() is None:
            self._beat(self.clock())
            claim = self.store.load_claim(task.id)
            if claim:
                progress = claim.get("checkpoint") or {}
                if progress.get("run_tag"):
                    tags.add(str(progress["run_tag"]))
                self._track_progress(claim, progress)
                age = heartbeat_age(claim) or 0.0
                quiet_since = time.time() - age
            else:
                age = time.time() - quiet_since  # not claimed (yet, or any more): the launch must still end
            if age > runtime.stale_heartbeat_seconds and not warned:
                warned = True
                self._watchdog_phase("slow", heartbeat_age=round(age, 1))
                self._event("worker-watchdog-stale", task=task.id, heartbeat_age=round(age, 1))
                self._log(f"watchdog: {task.id}'s launch has not heartbeated for {age:.0f}s (gates or a slow "
                          "step can do that; it is not stopped for this alone)")
            if age > hung_after and decision is None:
                decision = {"decision": "hung-launch", "pid": handle.pid, "since": utc_now(),
                            "heartbeat_age": round(age, 1), "by": self.instance}
                self._persist_watchdog(task.id, launch, decision)
                self._watchdog_phase("terminating", **decision)
                self._event("worker-watchdog-hung", task=task.id, pid=handle.pid, heartbeat_age=round(age, 1))
                self._log(f"watchdog: {task.id}'s launch is hung ({age:.0f}s without a heartbeat): stopping it")
                confirmed = self._terminate(handle)
                self._event("worker-watchdog-terminated", task=task.id, pids=[handle.pid], confirmed=confirmed)
                if not confirmed:
                    self._watchdog_phase("uncertain", **decision)
                    self._cleanup_uncertain(task.id, [handle.pid], "the hung launch could not be confirmed "
                                                                   "terminated")
            if claim and not cancelled:
                self._watch_intake(task)
            stop = stop_requested() or read_control(self.store).get("stop") == "graceful"
            if stop and not cancelled and claim:
                try:
                    self.coord.request_cancel(task.id, reason="worker service stop")
                    cancelled = True
                    self._set_state(STOPPING, f"stopping: cancelling {task.id} gracefully (checkpoints stay)")
                except ClaimError:
                    pass  # released meanwhile
            self.poll_sleep(self.poll_seconds)
        return handle.wait(), tags, decision

    def _track_progress(self, claim: dict[str, Any], progress: dict[str, Any]) -> None:
        """Mirror the claim's progress (step, agent, its run deadline) into the health record."""
        current = self.record.current if self.record else None
        if not current:
            return
        view = claim_view(claim, self.config.runtime)
        agent = {"pid": view["agent_pid"], "started_at": view["agent_started_at"], "deadline": view["deadline"]}
        if progress.get("step") != current.get("step") or (view["agent_pid"] and current.get("agent_process") != agent):
            current["step"] = progress.get("step")
            if view["agent_pid"]:
                current["agent_process"] = agent
            self._beat(self.clock(), force=True)

    def _watchdog_phase(self, phase: str, **details: Any) -> None:
        if self.record is not None and self.record.current is not None:
            self.record.current["watchdog"] = {"phase": phase, "since": utc_now(), **details}
            self._beat(self.clock(), force=True)

    def _terminate(self, handle: Any) -> bool:
        """Our own child launch process (certain identity: not reaped while we wait): SIGTERM, grace,
        SIGKILL, bounded. True once it has ended; False if even SIGKILL could not be confirmed."""
        for sig, wait in ((signal.SIGTERM, self.config.runtime.terminate_grace_seconds), (signal.SIGKILL, 10.0)):
            if handle.poll() is not None:
                return True
            try:
                handle.send_signal(sig)
                handle.wait(timeout=wait)
                return True
            except subprocess.TimeoutExpired:
                continue
            except (OSError, TypeError, AttributeError):
                break
        return handle.poll() is not None

    def _foreign_dirty(self, task: Task) -> set[str]:
        found = classify_uncommitted(self.coord, task)
        return set(found["other_roles"]) | set(found["unowned"])

    def _judge(self, feature: Feature, task: Task, mark: int, exit_code: int | None, tags: set[str],  # pylint: disable=too-many-arguments,too-many-locals
               head: str, foreign_before: set[str], launch: int, started: float,
               decision: dict[str, Any] | None = None) -> Outcome:
        """Integrity first (agent stopped, branch, foreign commits and files, checkpoints), then the
        launch's classified outcome. A launch the watchdog stopped is a transient failure (backoff,
        budget), whatever it reported on the way out."""
        hung = Outcome(TRANSIENT, f"watchdog: the launch was hung ({decision.get('heartbeat_age')}s without a "
                                  "heartbeat) and was stopped") if decision else None
        after = self.store.load_task(task.id)
        events = [e for e in self.store.events()[mark:] if e.get("task") == task.id]
        problems: list[str] = []
        tags = tags | self._run_tags(task.id, started)  # also runs too short to be seen while polling
        survivors = sorted({pid for tag in tags for pid in tagged_processes(RUN_TAG_ENV, tag)})
        git = self.coord.git
        now_head = git.rev_parse(f"refs/heads/{feature.branch}")
        if not git.is_ancestor(head, now_head):
            problems.append(f"the feature branch was rewritten during the launch ({head[:12]} → {now_head[:12]})")
        else:
            for sha in git.commits_between(head, now_head):
                if not git.trailers(sha).get("Feature-Task"):
                    problems.append(f"commit {sha[:12]} was not made by the Git coordinator")
        unexplained = sorted(self._foreign_dirty(task) - foreign_before)
        if unexplained:
            problems.append(f"unexplained changes outside {task.role}'s paths: {unexplained[:20]}")
        if self.store.load_claim(task.id) is not None:
            if problems:
                return Outcome(CRITICAL, "; ".join(problems))
            # the launch process died holding its claim: W3 recovery (next cycle) releases it and
            # applies the orphan policy to an agent still running; then the task runs again
            return hung or Outcome(INTERRUPTED, f"the launch process ended (exit {exit_code}) without releasing its "
                                                "claim")
        blocking: list[str] = []
        if survivors:  # the launch's own leftover sweep missed them: the same policy, bounded, once
            remaining = terminate_pids(survivors, grace_seconds=self.config.runtime.terminate_grace_seconds,
                                       still_ours=lambda pid: any(pid in tagged_processes(RUN_TAG_ENV, t)
                                                                  for t in tags))
            self._event("worker-leftovers-stopped", task=task.id, pids=survivors, remaining=remaining)
            problems.insert(0, f"agent processes {survivors} survived their launch ("
                               + (f"{remaining} could not be stopped)" if remaining else "stopped by the service)"))
            blocking = sorted(tags) if remaining else []
        if after.steps:
            problems += inspect(self.coord, task.id).conflicts
        if problems:
            return Outcome(CRITICAL, "; ".join(problems), evidence={"blocking_run_tags": blocking} if blocking else {})
        outcome = classify(task, after, events, exit_code=exit_code, log_text=self._launch_log(task.id, launch),
                           run_texts=self._run_texts(task.id, started))
        return hung if hung and outcome.category in (CANCELLED, INTERRUPTED, UNKNOWN) else outcome

    def _run_tags(self, task_id: str, since: float) -> set[str]:
        """The run tags the native backend recorded (native.json) for this task's runs since *since*."""
        found = set()
        for run in (self.store.root / "runs").glob(f"{task_id}.*"):
            try:
                if run.stat().st_mtime >= since - 1:
                    tag = json.loads((run / "native.json").read_text(encoding="utf-8")).get("run_tag")
                    if tag:
                        found.add(str(tag))
            except (OSError, ValueError, AttributeError):
                continue
        return found

    def _launch_log(self, task_id: str, launch: Any) -> str:
        path = self.store.root / "logs" / f"work-{task_id}.{launch}.log"
        try:
            return path.read_text(encoding="utf-8", errors="replace")[-4000:]
        except OSError:
            return ""

    def _run_texts(self, task_id: str, since: float) -> list[str]:
        """The agents' own final messages and errors of runs since *since* (bounded)."""
        texts = []
        for run in sorted((self.store.root / "runs").glob(f"{task_id}.*")):
            try:
                if run.stat().st_mtime < since - 1:
                    continue
                for line in (run / "stdout.jsonl").read_text(encoding="utf-8", errors="replace").splitlines():
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(event, dict) and event.get("type") in ("result", "error", "turn.failed"):
                        texts.append(str(event.get("result") or event.get("error") or event.get("message"))[:2000])
            except OSError:
                continue
        return texts[-10:]

    # -- outcomes ------------------------------------------------------------------------------------

    def _apply(self, task_id: str, outcome: Outcome, launch: int | None) -> None:
        now = self.clock()
        with self.store.transaction():
            task = self.store.load_task(task_id)
            rec = load_scheduling(self.store, task)
            actions = apply_outcome(rec, task, outcome, self.cfg, now, launch)
            if rec.attention and outcome.evidence.get("blocking_run_tags"):
                rec.attention["blocking_run_tags"] = list(outcome.evidence["blocking_run_tags"])
            if rec.in_flight and (launch is None or rec.in_flight.get("launch") == launch):
                rec.in_flight = None
            if actions["ready"] and task.state is TaskState.FAILED:
                transition_task(task, TaskState.READY, f"worker service: retry scheduled after a {outcome.category} "
                                                       "failure")
                self.store.save_task(task)
            self.store.save_record("scheduling", rec)
            kind = "worker-attention-required" if rec.attention else _OUTCOME_EVENTS.get(outcome.category,
                                                                                          "worker-outcome")
            self.store.append_event(kind, task=task_id, category=outcome.category, reason=scrub(outcome.reason, 500),
                                    launch=launch, next_attempt_at=iso(rec.next_attempt_at),
                                    attention=bool(rec.attention))
        self._log(f"{task_id}: {outcome.category} — {outcome.reason}"
                  + (f"; next attempt {iso(rec.next_attempt_at)}" if rec.next_attempt_at and not rec.attention else "")
                  + ("; NEEDS ATTENTION: " + rec.attention["action"] if rec.attention else ""))
        if actions["redo_step"]:
            result = recover_task(self.coord, task_id, redo_from=actions["redo_step"])
            if result.refused or result.inspection.conflicts:
                why = result.refused or result.inspection.conflicts
                self._apply(task_id, Outcome(CRITICAL, f"validation auto-fix could not redo step "
                                                       f"{actions['redo_step']}: {why}"), None)
        if self.record is not None:
            self.record.counters[outcome.category] = self.record.counters.get(outcome.category, 0) + 1
            if outcome.category not in (SUCCESS, "dependency", "cancelled", "interrupted", "external"):
                self.record.last_error = {"task": task_id, "category": outcome.category,
                                          "reason": scrub(outcome.reason, 500), "at": utc_now()}
            checkpoint = self._last_checkpoint(task_id)
            if checkpoint:
                self.record.last_checkpoint = checkpoint

    def _last_checkpoint(self, task_id: str) -> dict[str, Any] | None:
        try:
            record = load_record(self.store, task_id)
        except CoordinatorError:
            return None
        done = record.last_done() if record else None
        return {"task": task_id, "checkpoint": done.id, "step": done.step, "commit": done.commit,
                "at": done.committed_at or done.created_at} if done else None

    # -- the service record, events, logs ------------------------------------------------------------------

    def _set_state(self, state: str, reason: str, force: bool = False) -> None:
        if self.record is None:
            return
        if not force and self.record.state == state and self.record.reason == reason:
            return
        changed = self.record.state != state
        self.record.state, self.record.reason = state, scrub(reason, 600)
        if changed:
            self.record.state_since = utc_now()
        self._save_record()
        if changed or force:
            self._event("worker-state", state=state, reason=self.record.reason)
            self._log(f"{state}: {self.record.reason}")

    def _beat(self, now: float, force: bool = False) -> None:
        if self.record is None or (not force and now - self._beat_at < self.cfg.heartbeat_seconds):
            return
        self._beat_at = now
        self.record.heartbeat_at = iso(now)
        self._save_record()

    def _save_record(self) -> None:
        assert self.record is not None
        with self.store.transaction():
            on_disk = load_service(self.store)
            self.record.revision = on_disk.revision if on_disk else 0
            self.store.save_record("service", self.record)

    def _event(self, kind: str, **fields: Any) -> None:
        with self.store.transaction():
            self.store.append_event(kind, **fields)

    def _log(self, message: str) -> None:
        worker = self.record.worker_id if self.record else "worker"
        self.echo(f"{utc_now()} {worker}: {scrub(message, 700)}")

    def _idle(self, seconds: float) -> None:
        self.idle_sleep(seconds)


def echo_stdout(line: str) -> None:
    """journald-friendly output (one flushed line per message)."""
    sys.stdout.write(line + "\n")
    sys.stdout.flush()
