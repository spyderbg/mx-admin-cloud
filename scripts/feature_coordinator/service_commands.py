"""W4 operator commands: `feature.py worker …` (the autonomous Worker service, service.py).

    worker start [--max-cycles N] [--exit-when-idle]   run the service in the foreground (systemd runs this)
    worker status                                       one screen: state, current agent, waits, attention
    worker health [--json]                              structured health; exit 1 unless healthy
    worker waiting / worker attention                   what waits (retry, subscription) / what needs an operator
    worker pause [--reason R] / worker resume           stop / restart scheduling new work (an active agent finishes)
    worker stop [--after-current]                       graceful stop (cancel the active agent; checkpoints stay)
                                                        or after the active task has finished
    worker reset-retries TASK [--apply]                 an operator decision: clear a task's retry budgets and attention
    worker systemd-unit [--config FILE]                 print the systemd --user unit (nothing is installed)

Status, health, waiting and attention are READ-ONLY: they never create state, start work or ask
a model. Control commands record the request and signal only the recorded service process, after
re-verifying its identity (pid + start time + boot id).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path
from typing import Any

from .autonomy import health_report, iso, load_scheduling, load_service, write_control
from .config import ProjectConfig
from .locks import ProcessIdentity
from .model import TaskState, transition_task
from .statedir import ensure_state_dir, resolve_state_dir
from .store import StateStore

UNIT_TEMPLATE = Path(__file__).resolve().parent / "systemd" / "mx-admin-worker.service.in"
REPO_ROOT = Path(__file__).resolve().parents[2]


def _print(text: str = "") -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _state_path(args: argparse.Namespace, config: ProjectConfig) -> Path:
    return Path(args.state_dir) if args.state_dir else resolve_state_dir(app_dir=config.state_app_dir)


def _readonly_store(args: argparse.Namespace, config: ProjectConfig) -> StateStore | None:
    path = _state_path(args, config)
    return StateStore(path) if (path / "tasks").is_dir() else None


def _signal_service(store: StateStore, sig: int) -> str:
    record = load_service(store)
    if record is None or not record.identity:
        return "no service has run here"
    identity = ProcessIdentity.from_dict(record.identity)
    if not identity.is_alive():
        return f"the recorded service (pid {identity.pid}) is not running; the request applies at its next start"
    try:
        os.kill(identity.pid, sig)
    except ProcessLookupError:
        return "the service exited meanwhile"
    return f"signalled the service (pid {identity.pid})"


def cmd_worker_start(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Run the autonomous Worker service in the foreground until stopped."""
    from .service import WorkerService, echo_stdout  # pylint: disable=import-outside-toplevel
    from .supervisor import install_termination_handlers, install_wake_handler  # pylint: disable=import-outside-toplevel
    state = ensure_state_dir(_state_path(args, config), allowed=config.allowed_state_filesystems)
    install_termination_handlers()
    install_wake_handler()
    def idle(service: Any) -> bool:  # --exit-when-idle: nothing left that the service can do alone
        return bool(service.record) and service.record.state in ("AVAILABLE", "NEEDS_ATTENTION")
    service = WorkerService(config, Path(args.repo), state, echo=echo_stdout, max_cycles=args.max_cycles or None,
                            stop_when=idle if args.exit_when_idle else None,
                            project_config=getattr(args, "project_config", None),
                            tracker=getattr(args, "tracker", None) or "local")
    return service.run()


def cmd_worker_status(args: argparse.Namespace, config: ProjectConfig) -> int:
    """One screen: the service, the current agent, waits and attention (read-only)."""
    store = _readonly_store(args, config)
    if store is None:
        _print(f"no coordinator state at {_state_path(args, config)}")
        return 0
    report = health_report(store, config, time.time())
    _print(f"worker {report['worker_id'] or '-'}: {report['state']} — {report['reason']}")
    _print(f"  alive {report['alive']} (pid {report['pid']}), heartbeat {report['heartbeat_age_seconds']}s ago, "
           f"healthy {report['healthy']}; control {report['control'] or '{}'}")
    current = report["current"]
    if current:
        _print(f"  current: {current['task']} ({current['role']} → {current['agent']}, model {current['model']}), "
               f"step {current.get('step') or '-'}, launch {current['launch']}, since {current['started_at']}")
    if report["last_checkpoint"]:
        _print(f"  last checkpoint: {report['last_checkpoint']}")
    if report["last_error"]:
        _print(f"  last error: {report['last_error']}")
    for task in report["tasks"]:
        _print(f"  {task['task']:40s} {task['status']:20s} launches {task.get('launches', '-')} "
               f"attempts {task.get('attempts', {})}" + (f" next {task['next_attempt_at']}" if task.get(
                   "next_attempt_at") else ""))
    return 0


def cmd_worker_health(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Structured health (read-only). Exit 0 only when the service is alive, responsive and
    nothing needs an operator."""
    store = _readonly_store(args, config)
    if store is None:
        _print(json.dumps({"state": "NO-STATE", "healthy": False}))
        return 1
    report = health_report(store, config, time.time())
    _print(json.dumps(report, indent=2, sort_keys=True, default=str) if args.json else
           f"{report['state']}: {'healthy' if report['healthy'] else 'NOT healthy'} — {report['reason']}; "
           f"pending {report['pending']}, waiting {len(report['waiting'])}, attention {len(report['attention'])}")
    return 0 if report["healthy"] else 1


def cmd_worker_waiting(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Tasks waiting for a retry or a subscription (read-only)."""
    store = _readonly_store(args, config)
    report = health_report(store, config, time.time()) if store else {"waiting": []}
    for task in report["waiting"]:
        _print(f"{task['task']}: {task['status']} until {task['next_attempt_at']} — "
               f"{(task.get('waiting') or {}).get('reason', '')}; attempts {task['attempts']}")
    if not report["waiting"]:
        _print("nothing is waiting")
    return 0


def cmd_worker_attention(args: argparse.Namespace, config: ProjectConfig) -> int:
    """What needs an operator decision (read-only). Exit 1 when anything does."""
    store = _readonly_store(args, config)
    report = health_report(store, config, time.time()) if store else {"attention": [], "state": "NO-STATE"}
    for task in report["attention"]:
        attention = task.get("attention") or {}
        _print(f"{task['task']}: {attention.get('category', task.get('state'))} — "
               f"{attention.get('reason', task.get('reason', 'held for a human'))}")
        _print(f"  action: {attention.get('action', 'see `feature.py recover ' + task['task'] + '`')}")
    if report.get("state") == "NEEDS_ATTENTION" and not report["attention"]:
        _print(f"worker: {report.get('reason')}")
    if not report["attention"] and report.get("state") != "NEEDS_ATTENTION":
        _print("nothing needs attention")
        return 0
    return 1


def cmd_worker_control(args: argparse.Namespace, config: ProjectConfig) -> int:
    """pause / resume / stop: record the request, wake (or SIGTERM) the recorded service."""
    store = StateStore(ensure_state_dir(_state_path(args, config), allowed=config.allowed_state_filesystems))
    if args.worker_command == "pause":
        write_control(store, pause=True, pause_reason=args.reason)
        _print(f"pause requested: no new work is started; {_signal_service(store, signal.SIGUSR1)}")
    elif args.worker_command == "resume":
        write_control(store, pause=False, pause_reason=None)
        _print(f"resume requested; {_signal_service(store, signal.SIGUSR1)}")
    elif args.after_current:
        write_control(store, stop="after-current")
        _print(f"stop requested after the active task; {_signal_service(store, signal.SIGUSR1)}")
    else:
        write_control(store, stop="graceful")
        _print(f"graceful stop requested (the active agent is cancelled; checkpoints stay); "
               f"{_signal_service(store, signal.SIGTERM)}")
    return 0


def cmd_worker_reset(args: argparse.Namespace, config: ProjectConfig) -> int:
    """An operator decision: clear a task's retry budgets, waits and attention (FAILED → READY)."""
    store = StateStore(ensure_state_dir(_state_path(args, config), allowed=config.allowed_state_filesystems))
    task = store.load_task(args.task)
    rec = load_scheduling(store, task)
    _print(f"{task.id}: {task.state.value}; launches {rec.launches}, attempts {rec.attempts}, next "
           f"{iso(rec.next_attempt_at)}, attention {rec.attention}")
    if not args.apply:
        _print("dry run — pass --apply to reset its budgets and attention" +
               (" and make it ready again" if task.state is TaskState.FAILED else ""))
        return 0
    with store.transaction():
        task = store.load_task(args.task)
        rec = load_scheduling(store, task)
        rec.history = (rec.history + [{"at": iso(time.time()), "launch": None, "category": "operator-reset",
                                       "reason": f"budgets reset (launches {rec.launches}, attempts {rec.attempts})",
                                       "state": task.state.value}])[-20:]
        rec.launches, rec.attempts, rec.next_attempt_at, rec.waiting, rec.attention = 0, {}, None, None, None
        store.save_record("scheduling", rec)
        if task.state is TaskState.FAILED:
            transition_task(task, TaskState.READY, "worker retry budgets reset by the operator")
            store.save_task(task)
        store.append_event("worker-retries-reset", task=task.id, actor="operator")
    _print(f"{task.id}: budgets and attention cleared; state {store.load_task(task.id).state.value}")
    return 0


def cmd_worker_unit(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Print the systemd --user unit for this checkout (NOT installed, enabled or started)."""
    del config
    repo = Path(args.repo).resolve()
    python = Path(os.path.abspath(args.python or sys.executable))  # normalized; a venv symlink is kept
    project = Path(args.config).resolve() if args.config else None
    dirs = []
    for name in ("claude", "codex"):
        found = shutil.which(name)
        if found:
            dirs.append(str(Path(found).parent))
    path = ":".join(dict.fromkeys([*dirs, "/usr/local/bin", "/usr/bin", "/bin"]))
    text = UNIT_TEMPLATE.read_text(encoding="utf-8")
    for key, value in {"@REPO@": str(repo), "@PYTHON@": str(python), "@PATH@": path,
                       "@CONFIG_ARG@": f" --project-config {project}" if project else ""}.items():
        text = text.replace(key, value)
    _print(text.rstrip("\n"))
    if not project:
        sys.stderr.write("note: without --config the built-in configuration is used, where "
                         "worker.autonomous.enabled is false: the service would refuse to start (exit 78)\n")
    return 0


def register(sub: Any) -> None:
    """Add `feature.py worker …`."""
    worker = sub.add_parser("worker", help="W4: the autonomous Worker service (start, status, health, pause, …)")
    commands = worker.add_subparsers(dest="worker_command", required=True)
    start = commands.add_parser("start", help="run the service in the foreground until stopped")
    start.add_argument("--max-cycles", type=int, default=0, help="stop after N cycles (tests, diagnostics)")
    start.add_argument("--exit-when-idle", action="store_true",
                       help="stop when nothing is left to do (AVAILABLE / NEEDS_ATTENTION; tests, soak runs)")
    start.set_defaults(func=cmd_worker_start)
    commands.add_parser("status", help="state, current agent, waits, attention (read-only)") \
        .set_defaults(func=cmd_worker_status)
    health = commands.add_parser("health", help="structured health (read-only; exit 1 unless healthy)")
    health.add_argument("--json", action="store_true")
    health.set_defaults(func=cmd_worker_health)
    commands.add_parser("waiting", help="tasks waiting for a retry or a subscription (read-only)") \
        .set_defaults(func=cmd_worker_waiting)
    commands.add_parser("attention", help="what needs an operator (read-only; exit 1 if anything)") \
        .set_defaults(func=cmd_worker_attention)
    pause = commands.add_parser("pause", help="stop scheduling new work (the active agent finishes)")
    pause.add_argument("--reason", default="operator pause")
    pause.set_defaults(func=cmd_worker_control)
    commands.add_parser("resume", help="schedule work again").set_defaults(func=cmd_worker_control)
    stop = commands.add_parser("stop", help="graceful stop (cancel the active agent; checkpoints stay)")
    stop.add_argument("--after-current", action="store_true", help="let the active task finish first")
    stop.set_defaults(func=cmd_worker_control)
    reset = commands.add_parser("reset-retries", help="an operator decision: clear a task's budgets and attention")
    reset.add_argument("task")
    reset.add_argument("--apply", action="store_true")
    reset.set_defaults(func=cmd_worker_reset)
    unit = commands.add_parser("systemd-unit", help="print the systemd --user unit (nothing is installed)")
    unit.add_argument("--config", default=None, help="the project configuration the service runs with")
    unit.add_argument("--python", default=None, help="interpreter (default: this one)")
    unit.set_defaults(func=cmd_worker_unit)
