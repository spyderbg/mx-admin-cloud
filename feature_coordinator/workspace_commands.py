"""Security S1 operator commands: `workspace status` and `workspace resolve` (workspace_hold.py).

`status` is read-only: the checkout's hold (if any) with its reference and evidence, the executions in flight, and
the sensitive entries that are not in the workspace baseline — with their exact paths, in text and in JSON, also
before a first baseline exists (exit code 1 whenever something needs an operator).

`resolve` is a dry run unless --apply. It never changes, deletes or resets a file: the operator remediates (removes
or restores what the agent left), then lifts the hold. With --apply it must NAME the hold it resolves
(`--hold <id>.<generation>`, printed by `status` and by the dry run): if another process added a finding or
replaced the hold meanwhile, nothing is changed. `--accept --reason TEXT` records the current state as the workspace
BASELINE although residue remains — an explicit, logged operator decision that changes what counts as a change and
nothing else: an accepted path stays exactly as protected, read-only, masked and denied as the security policy makes
it. A sensitive path staged in Git's index is never acceptable (unstage it first).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .fsguard import scan
from .statedir import ensure_state_dir, resolve_state_dir
from .store import StateStore
from .workspace_hold import (
    contamination,
    describe,
    executions_of,
    hold_of,
    hold_ref,
    resolve,
    staged_sensitive,
    trusted_of,
    unexplained,
)

MAX_PATHS = 2000   # paths listed by `status --json`


def _print(text: str = "") -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def _store(args: argparse.Namespace, config: ProjectConfig) -> StateStore:
    path = Path(args.state_dir) if args.state_dir else resolve_state_dir(app_dir=config.state_app_dir)
    return StateStore(ensure_state_dir(path, allowed=config.allowed_state_filesystems))


def cmd_workspace_status(args: argparse.Namespace, config: ProjectConfig) -> int:  # pylint: disable=too-many-locals
    """The hold, its evidence, the executions in flight and the sensitive entries outside the baseline."""
    store, root = _store(args, config), Path(args.repo).resolve()
    hold = hold_of(store, root)
    inventory = scan(root, config)
    current = unexplained(root, inventory)
    baseline = trusted_of(store, root)
    # Before a first baseline exists EVERY unexplained sensitive entry is unreviewed: list them all.
    untrusted = contamination(baseline["entries"] if baseline else {}, current)
    staged = staged_sensitive(root, config)
    running = executions_of(store, root)
    needs_operator = bool(hold or untrusted or staged)
    if args.json:
        _print(json.dumps({
            "checkout": str(root), "hold": hold, "hold_ref": hold_ref(hold) if hold else None,
            "baseline": baseline is not None, "baseline_entries": len(baseline["entries"]) if baseline else 0,
            "sensitive_entries": len(inventory.entries), "unexplained": len(current), "scanned": inventory.scanned,
            "untrusted": untrusted[:MAX_PATHS], "untrusted_count": len(untrusted),
            "untrusted_reasons": {p: list(inventory.entries[p].reasons) for p in untrusted[:MAX_PATHS]
                                  if p in inventory.entries},
            "staged_sensitive": staged, "executions": running, "needs_operator": needs_operator},
            indent=2, sort_keys=True))
        return 1 if needs_operator else 0
    _print(f"checkout {root}: {inventory.scanned} entries read, {len(inventory.entries)} sensitive, "
           f"{len(current)} not explained by Git")
    if baseline is None:
        _print("no workspace baseline yet: unexplained sensitive entries (below) need an operator's review before "
               "any agent runs — `workspace resolve --accept --reason TEXT` records them after the audit")
    if hold is None:
        _print("not on hold")
    else:
        _print(f"ON HOLD {hold_ref(hold)}: {describe(hold)}")
        for entry in hold.get("entries", []):
            _print(f"- {entry.get('at')}: {entry.get('reason')}")
            for key in ("task", "role", "runner", "status", "run_dir"):
                if entry.get(key):
                    _print(f"    {key}: {entry[key]}")
            for item in entry.get("paths", []):
                _print(f"    left: {item}")
    for key, execution in running.items():
        _print(f"in flight: execution {key} ({execution.get('task')}, {execution.get('role')})")
    for path in staged:
        _print(f"staged in the index (unstage it): {path}")
    for path in untrusted:
        entry: Any = inventory.entries.get(path)
        _print(f"untrusted: {path}  {', '.join(entry.reasons) if entry else ''}")
    return 1 if needs_operator else 0


def cmd_workspace_resolve(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Lift the hold once the residue is remediated (dry run unless --apply; --apply names the hold)."""
    store, root = _store(args, config), Path(args.repo).resolve()
    if args.apply and not args.hold:
        current = hold_of(store, root)
        now = hold_ref(current) if current else "not held"
        _print(f"--apply needs --hold REF: name the hold you inspected (now: {now}; see `workspace status`)")
        return 2
    out = resolve(store, root, config, apply=args.apply, accept=args.accept, reason=args.reason or "",
                  expect=args.hold or None)
    if out.stale:
        _print(f"STALE, nothing was changed: {out.stale}")
        return 3
    _print(f"hold {out.ref}" if out.held else f"not on hold ({out.ref})")
    for key in out.in_flight:
        _print(f"still running: execution {key} (wait for it, or recover its task)")
    for path in out.staged:
        _print(f"staged in Git's index, never acceptable (unstage it: git restore --staged -- <path>): {path}")
    for path in out.forbidden:
        _print(f"created by a run in a protected location, never acceptable (remove it): {path}")
    for path in out.remaining:
        _print(f"still untrusted (sensitive): {path}")
    for path in out.unresolved_paths:
        _print(f"still reported by Git: {path}")
    for item in out.unverifiable:
        _print(f"cannot be verified automatically: {item}")
    if out.applied:
        _print("hold lifted" + (" — the remaining state was ACCEPTED as the workspace baseline (recorded; its "
                                "protection by the security policy is unchanged)" if args.accept else ""))
        return 0
    if out.resolved:
        _print(f"resolvable (dry run) — apply with: workspace resolve --apply --hold {out.ref}"
               + (" --accept --reason TEXT" if args.accept else ""))
        return 0
    _print("NOT resolved: remediate the entries above (remove or restore what the run left; nothing is changed "
           "automatically), or pass --accept --reason TEXT to record them in the workspace baseline")
    return 1


def register(sub: Any) -> None:
    """Add `workspace status|resolve` to the CLI."""
    workspace = sub.add_parser("workspace", help="Security S1: the checkout's hold and workspace baseline")
    commands = workspace.add_subparsers(dest="workspace_command", required=True)
    status = commands.add_parser("status", help="the hold, its evidence and untrusted sensitive entries (read-only)")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_workspace_status)
    res = commands.add_parser("resolve", help="lift the hold once remediated (dry run unless --apply)")
    res.add_argument("--apply", action="store_true", help="lift the hold (needs --hold REF)")
    res.add_argument("--hold", default="", metavar="REF",
                     help="the hold this resolution is about: <id>.<generation> as `status` prints it")
    res.add_argument("--accept", action="store_true",
                     help="record the current state as the baseline although residue remains (needs --reason)")
    res.add_argument("--reason", default="", help="why the remaining state is accepted")
    res.set_defaults(func=cmd_workspace_resolve)
