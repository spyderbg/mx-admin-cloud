"""W4.8.1 commands: `python -m feature_coordinator github repair …` — the autonomous repair of an approved GitHub
Issue's Draft PR after its independent AI review requested changes (repair.py).

    github repair status [--json]            every repair record: its latest attempt and stop (read-only, local)
    github repair show FEATURE [--json]      one feature's attempts, the findings each answered (read-only, local)
    github repair abandon FEATURE --reason TEXT [--actor NAME] [--apply]
                                             an operator's decision: end the feature's open attempt (refused while
                                             its agent runs); nothing else changes

The repair itself runs only in the Worker service (github.repair.enabled; off by default): its repair step counts an
attempt, re-opens the feature's single task, lets the service run the task's agent with the review's findings,
pushes the verified head to the SAME PR and revalidates it (revalidate.py); the review step then reviews the new
head. Nothing here merges, pushes or starts anything. See docs_agents/coordination/github-intake.md §14.
"""

from __future__ import annotations

import argparse
from typing import Any

from .config import ProjectConfig
from .github_commands import _emit, _state  # pylint: disable=protected-access
from .repair import abandon, repair_rows
from .repair_record import load_repair
from .store import StateStore

_NOTE_OFF = "note: github.repair.enabled is false — the Worker service repairs nothing"


def cmd_status(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Every repair record (read-only)."""
    rows = sorted(repair_rows(StateStore(_state(args, config))), key=lambda r: r["issue"])
    lines = [f"#{r['issue']:<5} {r['feature']:24s} attempts {r['attempts']}  latest {r['attempt'] or '-'} "
             f"{r['status'] or '-'}{' (open)' if r['open'] else ''}  review {r['review'] or '-'}  head "
             f"{(r['head'] or '-')[:12]}" + (f"\n       {r['reason']}" if r["reason"] else "")
             + (f"\n       stopped: {r['stopped']['reason']}" if r["stopped"] else "") for r in rows] or ["no repairs"]
    if not config.github.repair.enabled:
        lines.append(_NOTE_OFF)
    _emit(args, rows, lines)
    return 0


def cmd_show(args: argparse.Namespace, config: ProjectConfig) -> int:
    """One feature's repair attempts (read-only)."""
    record = load_repair(StateStore(_state(args, config)), args.feature)
    if record is None:
        _emit(args, None, [f"{args.feature}: no repair record"])
        return 0
    lines = [f"{record.id} (Issue #{record.issue_number}): {len(record.attempts)} attempt(s)"]
    for attempt in record.attempts:
        lines.append(f"  {attempt['id']}: {attempt['status']} — review {attempt['review']} of PR #{attempt['pr']} at "
                     f"{attempt['review_head'][:12]}, task {attempt['task']} ({attempt['role']}), started "
                     f"{attempt['started_at']}" + (f", verified at {attempt['verified_head'][:12]}"
                                                   if attempt["verified_head"] else ""))
        if attempt.get("pushed"):
            lines.append(f"    pushed: {attempt['pushed']['sha'][:12]} (before {str(attempt['pushed']['before'])[:12]},"
                         f" pushed {attempt['pushed']['pushed']}) at {attempt['pushed']['at']}")
        if attempt.get("revalidation"):
            lines.append(f"    revalidation: {attempt['revalidation']}")
        if attempt["reason"]:
            lines.append(f"    reason: {attempt['reason']}")
        lines += [f"    - {f['severity']}: {f['file']}:{f.get('line_start') or ''} {f['title']}"
                  for f in attempt["findings"]]
    if record.stopped:
        stop = record.stopped
        lines.append(f"  stopped ({stop['at']}, review {stop['review']}): {stop['reason']}")
    _emit(args, record.to_dict(), lines)
    return 0


def cmd_abandon(args: argparse.Namespace, config: ProjectConfig) -> int:
    """End the feature's open repair attempt (dry run unless --apply)."""
    store = StateStore(_state(args, config))
    _emit(args, None, abandon(store, args.feature, reason=args.reason, actor=args.actor, apply=args.apply))
    return 0


def register(gsub: Any) -> None:
    """Add `github repair …` to the `github` command group."""
    group = gsub.add_parser("repair", help="the autonomous repair of a Draft PR after REQUEST_CHANGES (W4.8.1)",
                            description=__doc__.split("\n\n", 1)[0], epilog=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    rsub = group.add_subparsers(dest="repair_command", required=True)
    status = rsub.add_parser("status", help="every repair record (read-only)")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)
    show = rsub.add_parser("show", help="one feature's repair attempts (read-only)")
    show.add_argument("feature")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_show)
    end = rsub.add_parser("abandon", help="end the feature's open repair attempt (dry run unless --apply)")
    end.add_argument("feature")
    end.add_argument("--reason", required=True)
    end.add_argument("--actor", default="operator")
    end.add_argument("--apply", action="store_true")
    end.set_defaults(func=cmd_abandon)
