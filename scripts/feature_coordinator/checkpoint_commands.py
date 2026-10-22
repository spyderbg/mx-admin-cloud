"""W3 operator commands: `checkpoints TASK` (read-only) and `recover TASK [--apply]` (checkpoints.py).

    checkpoints TASK [--json]     steps done / remaining, every checkpoint with its commit, the Git
                                  consistency check, uncommitted work, the claim and its processes
    recover TASK [--show-prompt]  DRY RUN: what recovery would do, the exact checkpoint and Git
                                  revision a resume continues from (and, with --show-prompt, the
                                  reconstructed context of the next step); no model request
    recover TASK --apply [--terminate-orphans] [--abandon-pending] [--redo-from STEP]
                                  reclaim the stale claim, settle interrupted checkpoints, record
                                  conflicts (needs-human) — never resets Git, never discards files,
                                  never starts an agent (`launch TASK --apply` / `work` resumes)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .checkpoints import Inspection, inspect, recover_task, step_by_key, step_prompt
from .config import ProjectConfig
from .errors import CoordinatorError


def _print(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _coordinator(args: argparse.Namespace, config: ProjectConfig) -> Any:
    """The full coordinator (real runners, tracker, board) for `--apply`."""
    from .commands import _coordinator as build  # pylint: disable=import-outside-toplevel
    return build(args, config)


def cmd_recover(args: argparse.Namespace, config: ProjectConfig) -> int:
    """`recover`: one task from its checkpoints (W3) with a TASK, else the claim recovery (Phase 3)."""
    from .commands import cmd_recover as recover_claims  # pylint: disable=import-outside-toplevel
    check_recover_args(args)
    return cmd_recover_task(args, config) if args.task else recover_claims(args, config)


def _readonly_coordinator(args: argparse.Namespace, config: ProjectConfig) -> Any:
    """A coordinator for inspection only: no runners, no tracker or board writes, no sync, and the
    state directory is not created — a preview changes nothing."""
    from .issues import MockIssueTracker  # pylint: disable=import-outside-toplevel
    from .statedir import resolve_state_dir  # pylint: disable=import-outside-toplevel
    from .store import StateStore  # pylint: disable=import-outside-toplevel
    from .workflow import FeatureCoordinator  # pylint: disable=import-outside-toplevel
    state = Path(args.state_dir) if args.state_dir else resolve_state_dir(app_dir=config.state_app_dir)
    if not (state / "tasks").is_dir():
        raise CoordinatorError(f"no coordinator state at {state}")
    return FeatureCoordinator(config, Path(args.repo), StateStore(state), tracker=MockIssueTracker(), runners={},
                              gate_runner=lambda _name, _context: 0, auto_sync=False)


def _show(inspection: Inspection, config: ProjectConfig) -> None:
    model = config.model_for(inspection.role) or "CLI default"
    _print(f"{inspection.task_id}: {inspection.task_state} — {inspection.status}; role {inspection.role} → "
           f"{config.provider_for(inspection.role).name}, model {model}")
    if inspection.steps:
        marks = [f"{s} ✓" if s in inspection.done else (f"{s} ← next" if inspection.remaining[:1] == [s] else s)
                 for s in inspection.steps]
        _print(f"  steps ({len(inspection.done)}/{len(inspection.steps)} done"
               + (", counting the safe actions" if inspection.actions else "") + f"): {', '.join(marks)}")
    else:
        _print("  single-step task (no checkpoints; W2 behaviour)")
    for cp in inspection.checkpoints:
        _print(f"  #{cp['sequence']:<3} {cp['step']:16s} {cp['status']:11s} {str(cp['commit'] or '-')[:12]:12s} "
               f"base {cp['base'][:12]}  {len(cp['files'])} file(s)  attempt {cp['attempt']}  {cp['created_at']}"
               + (f"  — {cp['note']}" if cp.get("note") else ""))
    if inspection.head:
        _print(f"  feature branch HEAD {inspection.head}")
    if inspection.resume_from:
        r = inspection.resume_from
        _print(f"  resume from checkpoint #{r['sequence']} ({r['step']}, {r['checkpoint']}) at revision {r['commit']}"
               + (" — after the safe actions below" if inspection.actions else ""))
    claim = inspection.claim
    if claim.get("state") in ("live", "stale"):
        _print(f"  claim: {claim['state']} (worker pid {claim['pid']}); progress {claim.get('progress', {})}")
    if inspection.orphans:
        _print(f"  surviving agent processes of the interrupted run: {inspection.orphans}")
    for action in inspection.actions:
        _print(f"  safe action: {action}")
    for warning in inspection.warnings:
        _print(f"  warning: {warning}")
    for conflict in inspection.conflicts:
        _print(f"  CONFLICT: {conflict}")


def cmd_checkpoints(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Read-only checkpoint history and consistency of one task."""
    coord = _readonly_coordinator(args, config)
    inspection = inspect(coord, args.task)
    if args.json:
        data = {k: v for k, v in asdict(inspection).items() if k != "record"}
        data["portable_record"] = inspection.record.portable() if inspection.record else None
        _print(json.dumps(data, indent=2, sort_keys=True))
    else:
        _show(inspection, config)
    return 1 if inspection.conflicts else 0


def cmd_recover_task(args: argparse.Namespace, config: ProjectConfig) -> int:
    """`recover TASK`: dry run unless --apply (see the module docstring)."""
    if not args.apply:
        coord = _readonly_coordinator(args, config)
        inspection = inspect(coord, args.task)
        _show(inspection, config)
        if inspection.claim.get("state") == "stale":
            _print("  would reclaim the stale claim" + (" (after --terminate-orphans)" if inspection.orphans else ""))
        if args.show_prompt:
            _show_prompt(coord, args.task, inspection)
        _print("dry run — nothing was changed and no model was asked; pass --apply to recover"
               + ("" if not inspection.conflicts else " (conflicts need --abandon-pending / --redo-from or a fix)"))
        return 1 if inspection.conflicts else 0
    coord = _coordinator(args, config)
    result = recover_task(coord, args.task, terminate_orphans=args.terminate_orphans,
                          abandon_pending=args.abandon_pending, redo_from=args.redo_from)
    coord.sync_issues(result.inspection.feature_id)  # the task's Issue shows its new state
    for line in result.applied:
        _print(f"done: {line}")
    if result.refused:
        _print(f"REFUSED: {result.refused}")
        return 1
    _show(result.inspection, config)
    nxt = result.inspection.remaining[:1]
    _print(f"{args.task}: {result.state}" + (f"; next: `feature.py launch {args.task} --apply` resumes at step "
                                             f"{nxt[0]}" if nxt and result.state == "ready" else ""))
    return 1 if result.inspection.conflicts else 0


def _show_prompt(coord: Any, task_id: str, inspection: Inspection) -> None:
    task = coord.store.load_task(task_id)
    if not task.steps or not inspection.remaining:
        _print("  (no step left to run: no prompt)")
        return
    if inspection.conflicts and inspection.record is None:
        _print("  (no prompt: the checkpoint metadata is unusable)")
        return
    record = inspection.projected  # as the safe actions will leave it
    if record is None:
        from .checkpoints import new_record  # pylint: disable=import-outside-toplevel
        record = new_record(task)
    feature = coord.store.load_feature(task.feature_id)
    prompt = step_prompt(feature, task, record, step_by_key(task, inspection.remaining[0]),
                         uncommitted=inspection.uncommitted.get("own", []))
    _print("  --- reconstructed context of the next step (the role's rules are added by the launcher) ---")
    for line in prompt.splitlines():
        _print(f"  | {line}")


def register(sub: Any) -> None:
    """Add `checkpoints`, and extend the existing `recover` command with TASK and its options."""
    parser = sub.add_parser("checkpoints", help="W3: a task's checkpoints, progress and consistency (read-only)")
    parser.add_argument("task")
    parser.add_argument("--json", action="store_true", help="machine-readable, with the portable record")
    parser.set_defaults(func=cmd_checkpoints)
    recover = sub.choices["recover"]  # registered by commands.py (Phase 3)
    add_recover_options(recover)
    recover.set_defaults(func=cmd_recover)


def add_recover_options(parser: argparse.ArgumentParser) -> None:
    """The W3 options of `recover` (they need a TASK)."""
    parser.add_argument("task", nargs="?", default=None,
                        help="W3: recover ONE task from its checkpoints (default: reclaim every dead worker's claim)")
    parser.add_argument("--show-prompt", action="store_true", help="with TASK: print the next step's context")
    parser.add_argument("--terminate-orphans", action="store_true",
                        help="with TASK --apply: stop the interrupted run's surviving agent processes")
    parser.add_argument("--abandon-pending", action="store_true",
                        help="with TASK --apply: abandon pending checkpoints that have no commit (files are kept)")
    parser.add_argument("--redo-from", default=None, metavar="STEP",
                        help="with TASK --apply: run STEP and every later step again (their commits stay)")


def check_recover_args(args: argparse.Namespace) -> None:
    """The W3 options need a task."""
    if not args.task and (args.show_prompt or args.terminate_orphans or args.abandon_pending or args.redo_from):
        raise CoordinatorError("--show-prompt, --terminate-orphans, --abandon-pending and --redo-from need a TASK")
