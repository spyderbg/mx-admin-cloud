"""W2 operator commands: `work` (drive a whole feature with one Worker) and `subscription-smoke`."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .model import validate_dag, utc_now
from .statedir import ensure_state_dir, resolve_state_dir
from .workflow import DEFAULT_INTEGRATION_GATES
from .workspace import workspace_problems

# The documented example configuration (as in cli.py; not imported from there to avoid a cycle).
EXAMPLE_CONFIG_PATH = (Path(__file__).resolve().parents[1] / "docs_agents" / "coordination"
                       / "project-config.example.yaml")


def _print(text: str = "") -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _state(args: argparse.Namespace, config: ProjectConfig) -> Path:
    path = Path(args.state_dir) if args.state_dir else resolve_state_dir(app_dir=config.state_app_dir)
    return ensure_state_dir(path, allowed=config.allowed_state_filesystems)


def cmd_work(args: argparse.Namespace, config: ProjectConfig) -> int:  # pylint: disable=too-many-locals
    """Drive a feature's tasks through their agents (one Worker); --apply runs it."""
    from .checkpoints import progress_line  # pylint: disable=import-outside-toplevel
    from .commands import _coordinator  # pylint: disable=import-outside-toplevel
    from .supervisor import install_termination_handlers  # pylint: disable=import-outside-toplevel
    from .claims import drop_exclusive, hold_exclusive  # pylint: disable=import-outside-toplevel
    from .worker import FeatureWorker, cli_spawner  # pylint: disable=import-outside-toplevel
    coord = _coordinator(args, config)
    feature = coord.store.load_feature(args.feature)
    tasks = coord.store.load_tasks(feature)
    # W4: one agent at a time unless explicitly asked for more (worker.max_parallel, --max-parallel N).
    parallel = args.max_parallel or config.worker.max_parallel
    backend = config.runtime.execution_backend
    _print(f"{feature.id}: {feature.state.value} on {feature.branch}; execution backend {backend}"
           f"{' (NOT a security sandbox)' if backend == 'native-cli' else ''}; up to {parallel} task(s) at a "
           f"time; at most {args.max_attempts} launch(es) per task")
    for task_id in validate_dag(tasks):
        task = tasks[task_id]
        role = config.role(task.role)
        agent = config.provider_for(task.role)
        deps = ", ".join(task.depends_on) or "-"
        steps = progress_line(coord.store, task)
        _print(f"  {task_id:30s} {task.state.value:11s} {task.role:12s} → {agent.name} ({agent.kind.value}), "
               f"model {config.model_for(task.role) or 'CLI default'}{'' if role.enabled else ', ROLE DISABLED'}; "
               f"depends on {deps}" + (f"; {steps}" if steps else ""))
    problems = workspace_problems(coord.root, feature)
    for problem in problems:
        _print(f"workspace: {problem}")
    if not args.apply:
        _print("dry run — pass --apply to run the Worker")
        return 0
    install_termination_handlers()
    spawn = cli_spawner(coord.root, coord.store.root, tracker=getattr(args, "tracker", None) or "local",
                        project_config=getattr(args, "project_config", None))
    if parallel == 1:  # sequential: no other agent (e.g. a manual launch) may run beside this Worker's
        hold_exclusive(coord.store, f"python -m feature_coordinator work {feature.id}")
    try:
        report = FeatureWorker(coord, spawn, max_parallel=parallel, max_attempts=args.max_attempts,
                               echo=_print).run(feature.id)
    finally:
        if parallel == 1:
            drop_exclusive(coord.store)
    _print(f"{feature.id}: {sum(s == 'verified' for s in report.states.values())}/{len(report.states)} task(s) "
           f"verified; {len(report.launches)} launch(es); up to {report.max_concurrency} at once"
           f"{'; STOPPED by request' if report.stopped else ''}")
    for task_id, why in report.skipped.items():
        _print(f"  not run: {task_id}: {why}")
    if report.recovered:
        _print(f"  recovered at start (stale claims): {report.recovered}")
    if report.all_verified and args.integrate:
        gates = tuple(args.integration_gate) or DEFAULT_INTEGRATION_GATES
        feature = coord.integrate(feature.id, gates=gates)
        _print(f"{feature.id}: {feature.state.value}; PR candidate rendered (not opened)")
    elif report.all_verified:
        _print(f"{feature.id}: ready for integration (`python -m feature_coordinator integrate {feature.id} --apply`)")
    return 0 if report.all_verified else 1


def cmd_subscription_smoke(args: argparse.Namespace, config: ProjectConfig) -> int:
    """The bounded real-subscription smoke test (native_smoke.py); --apply needs operator approval."""
    from .native_smoke import run_smoke, smoke_plan, write_smoke_config  # pylint: disable=import-outside-toplevel
    state = _state(args, config)
    workdir = Path(args.workdir) if args.workdir else \
        state / "subscription-smoke" / utc_now().replace(":", "").replace("-", "")[:15]
    plan = smoke_plan(config, workdir)
    _print(json.dumps(plan, indent=2))
    blockers = [f"{role}: {entry.get('authentication', 'CLI unavailable')}" for role, entry in plan["roles"].items()
                if not (entry.get("cli_ok") and entry.get("authentication_ok"))]
    for blocker in blockers:
        _print(f"BLOCKER: {blocker}")
    if not args.apply:
        _print("dry run — no model request was made. --apply runs the bounded smoke test with REAL subscription "
               "model requests (operator approval required)")
        return 1 if blockers else 0
    if blockers:
        return 1
    workdir.mkdir(parents=True, exist_ok=False)
    base = Path(args.project_config) if getattr(args, "project_config", None) else EXAMPLE_CONFIG_PATH
    result = run_smoke(write_smoke_config(workdir / "smoke-config.yaml", base), workdir, _print)
    _print(json.dumps(result, indent=2))
    _print(f"subscription smoke test: {'PASSED' if result['passed'] else 'FAILED'}")
    return 0 if result["passed"] else 1


def register(sub: Any) -> None:
    """Add the W2 commands to the CLI's subparsers."""
    work = sub.add_parser("work", help="drive a whole feature through its agents (one Worker)")
    work.add_argument("feature")
    work.add_argument("--apply", action="store_true")
    work.add_argument("--max-parallel", type=int, default=0,
                      help="agents at a time (default: worker.max_parallel = 1; N > 1 = the W2 parallel scheduler)")
    work.add_argument("--max-attempts", type=int, default=1, help="launches per task in this run (default 1)")
    work.add_argument("--integrate", action="store_true", help="when every task is verified, run the integration")
    work.add_argument("--integration-gate", action="append", default=[],
                      help=f"integration gate (repeatable; default: {', '.join(DEFAULT_INTEGRATION_GATES)})")
    work.set_defaults(func=cmd_work)
    smoke = sub.add_parser("subscription-smoke", help="bounded real-subscription smoke test (dry run unless --apply)")
    smoke.add_argument("--apply", action="store_true", help="make the REAL model requests (operator approval)")
    smoke.add_argument("--workdir", default=None, help="a new directory for the disposable repository and state")
    smoke.set_defaults(func=cmd_subscription_smoke)
