"""W4.3.1 commands: `feature.py github intake …` — approved GitHub Issues become Worker tasks.

    github intake show NUMBER [--json]   review an Issue before approving it: eligibility, the digest of
                                         its title and body, the exact `/worker approve` line per role
                                         (read-only)
    github intake poll [--apply] [--json]
                                         find approvals, record and plan approved Issues, activate the
                                         next one, check approved texts (dry run: reads only; --apply
                                         needs github.intake.enabled)
    github intake status [--json]        the local intake records with their feature and task states
                                         (read-only; no GitHub request)

The Worker service polls by itself when `github.intake.enabled` is true. Nothing is written to an
Issue before it is approved. See docs_agents/coordination/github-intake.md.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .github_api import sanitize
from .github_commands import _emit, _state, _tracker, tracker_kind  # pylint: disable=protected-access
from .intake_poll import IntakePoller, intake_status
from .store import StateStore


def _coordinator(args: argparse.Namespace, config: ProjectConfig) -> Any:
    from .operations import build_coordinator  # pylint: disable=import-outside-toplevel
    state_dir = _state(args, config)
    return build_coordinator(config, Path(args.repo), state_dir, tracker=_tracker(args, config, state_dir),
                             tracker_kind=tracker_kind(args, "github"))


def _traffic(client: Any, before: tuple[int, int]) -> str:
    requests = getattr(client, "requests", None)
    if requests is None:
        return f"tracker writes: {client.writes - before[1]}"
    return f"GitHub requests: {requests - before[0]}, of them writes: {client.writes - before[1]}"


def cmd_show(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Everything an operator needs to approve one Issue (read-only)."""
    data = IntakePoller(_coordinator(args, config)).describe(args.number)
    lines = [f"#{data['number']} {data['title']!r} ({data['state']}, by {data['author']}) {data['url']}",
             f"  eligible: {'yes' if not data['not_eligible'] else 'NO — ' + data['not_eligible']}",
             f"  spec sha256 (title + body, {data['characters']} characters): {data['digest']}"]
    approved: dict[str, Any] = dict(data["approved"] or {})
    if approved:
        matches = "still matches" if approved["matches_issue"] else "NO LONGER MATCHES"
        lines.append(f"  APPROVED by {approved['by']} at {approved['at']} as {approved['task']} "
                     f"({approved['status']}); the Issue {matches} the approved text")
    elif not data["not_eligible"]:
        lines.append("  to approve exactly this text, comment ONE of these lines (role → gates):")
        lines += [f"    {data['approve'][role]}    ({', '.join(gates)})" for role, gates in data["roles"].items()]
    if not data["intake_enabled"]:
        lines.append("  note: github.intake.enabled is false — approvals are only acted on once an operator enables it")
    _emit(args, data, lines)
    return 0


def cmd_poll(args: argparse.Namespace, config: ProjectConfig) -> int:
    """One bounded intake poll (dry run unless --apply)."""
    coord = _coordinator(args, config)
    client = coord.sync.client
    before = (getattr(client, "requests", 0), client.writes)
    report = IntakePoller(coord).poll(apply=args.apply)
    lines = report.lines() or ["nothing to do: no new approval, every approved Issue matches its approved text"]
    lines.append(f"examined Issues: {report.examined or 'none'}; {_traffic(client, before)}")
    if not args.apply:
        lines.append("dry run — nothing was written (pass --apply; github.intake.enabled must be true)")
    _emit(args, report.to_dict(), lines)
    return 0 if report.ok else 1


def cmd_status(args: argparse.Namespace, config: ProjectConfig) -> int:
    """The local intake records (read-only)."""
    rows = intake_status(StateStore(_state(args, config)))
    lines = [f"#{r['issue']:<5} {r['task']:32s} {r['status']:8s} feature {r['feature_state'] or '-':11s} "
             f"task {r['task_state'] or '-':12s} approved by {r['approved_by']} at {r['approved_at']}"
             + (f"\n       CHANGED after approval: {sanitize(r['spec_changed'].get('reason', ''), 200)}"
                if r["spec_changed"] else "")
             + (f"\n       activation deferred: {r['activation_error']}" if r["activation_error"] else "")
             for r in rows] or ["no approved Issues"]
    _emit(args, rows, lines)
    return 0


def register(gsub: Any) -> None:
    """Add `github intake …` to the `github` command group."""
    group = gsub.add_parser("intake", help="approved GitHub Issues become Worker tasks (W4.3.1)",
                            description=__doc__.split("\n\n", 1)[0], epilog=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    isub = group.add_subparsers(dest="intake_command", required=True)
    show = isub.add_parser("show", help="review an Issue: eligibility, spec digest, approval lines (read-only)")
    show.add_argument("number", type=int)
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_show)
    poll = isub.add_parser("poll", help="find approvals, plan and activate approved Issues (dry run unless --apply)")
    poll.add_argument("--apply", action="store_true", help="record, plan, activate and hold (default: reads only)")
    poll.add_argument("--json", action="store_true")
    poll.set_defaults(func=cmd_poll)
    status = isub.add_parser("status", help="the local intake records (read-only)")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)
