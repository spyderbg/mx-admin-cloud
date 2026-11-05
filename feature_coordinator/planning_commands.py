"""W4.5 commands: `python -m feature_coordinator github planning …` — label-driven Issue planning (intake_plan.py).

    github planning status [--json]      the local plan records: version, status, tasks, approval (read-only;
                                         no GitHub request)
    github planning show NUMBER [--json] one Issue's plan record with every version (read-only, local)
    github planning labels [--apply]     create the four `worker:*` labels that are missing (never edits one;
                                         dry run unless --apply)

Planning itself runs inside the intake poll (`github intake poll --apply`, or the Worker service) when
`github.planning.enabled` is true. See docs_agents/coordination/github-intake.md §12.
"""

from __future__ import annotations

import argparse
from typing import Any

from .config import ProjectConfig
from .github_commands import _emit, _state, _tracker  # pylint: disable=protected-access
from .intake import feature_id_for
from .intake_plan import ensure_worker_labels
from .planning import PlanRecord, load_plan
from .store import StateStore


def _row(record: PlanRecord) -> dict[str, Any]:
    latest = record.latest or {}
    plan = latest.get("plan") or {}
    return {"issue": record.issue_number, "feature": record.id, "status": record.status,
            "version": latest.get("version"), "sha256": latest.get("sha256"), "comment": latest.get("comment_url"),
            "tasks": [f"{t['id']} ({t['role']})" for t in plan.get("tasks", [])],
            "attention": (record.attention or {}).get("reason"), "approval": record.approval,
            "versions": len(record.versions), "updated_at": record.updated_at}


def cmd_status(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Every local plan record (read-only)."""
    store = StateStore(_state(args, config))
    rows = sorted((_row(PlanRecord.from_dict(raw)) for raw in store.list_records("plan")), key=lambda r: r["issue"])
    lines = [f"#{r['issue']:<5} {r['status']:9s} v{r['version'] or '-'} {', '.join(r['tasks']) or '-'}"
             + (f"\n       attention: {r['attention']}" if r["attention"] else "")
             + (f"\n       approved by {r['approval'].get('actor')} (approval sha256 "
                f"{str(r['approval'].get('approval_sha256'))[:16]}…)" if r["approval"] else "")
             for r in rows] or ["no planned Issues"]
    if not config.github.planning.enabled:
        lines.append("note: github.planning.enabled is false — `worker:*` labels are not acted on")
    _emit(args, rows, lines)
    return 0


def cmd_show(args: argparse.Namespace, config: ProjectConfig) -> int:
    """One Issue's plan record (read-only)."""
    record = load_plan(StateStore(_state(args, config)), feature_id_for(args.number))
    if record is None:
        _emit(args, None, [f"#{args.number}: no plan record"])
        return 0
    data = record.to_dict()
    lines = [f"#{record.issue_number} {record.status} ({len(record.versions)} version(s))"]
    for version in record.versions:
        lines.append(f"  v{version['version']} sha256 {version['sha256']} spec {version['spec_sha256'][:16]}… "
                     f"comment {version.get('comment_url') or '(not published)'}")
        lines += [f"    - {t['id']} ({t['role']}) depends on {t['depends_on'] or '-'} gates {', '.join(t['gates'])}"
                  for t in version["plan"]["tasks"]]
    if record.attention:
        lines.append(f"  attention: {record.attention.get('reason')}")
    if record.approval:
        lines.append(f"  approved v{record.approval.get('version')} by {record.approval.get('actor')}: approval "
                     f"sha256 {record.approval.get('approval_sha256')}")
    _emit(args, data, lines)
    return 0


def cmd_labels(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Create the missing `worker:*` labels (dry run unless --apply)."""
    client = _tracker(args, config, _state(args, config))
    lines = ensure_worker_labels(client, apply=args.apply)
    if not args.apply:
        lines.append("dry run — nothing was written (pass --apply)")
    _emit(args, lines, lines)
    return 0


def register(gsub: Any) -> None:
    """Add `github planning …` to the `github` command group."""
    group = gsub.add_parser("planning", help="label-driven Issue planning and approval (W4.5)",
                            description=__doc__.split("\n\n", 1)[0], epilog=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    psub = group.add_subparsers(dest="planning_command", required=True)
    status = psub.add_parser("status", help="the local plan records (read-only)")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)
    show = psub.add_parser("show", help="one Issue's plan versions (read-only)")
    show.add_argument("number", type=int)
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_show)
    labels = psub.add_parser("labels", help="create the missing worker:* labels (dry run unless --apply)")
    labels.add_argument("--apply", action="store_true")
    labels.add_argument("--json", action="store_true")
    labels.set_defaults(func=cmd_labels)
