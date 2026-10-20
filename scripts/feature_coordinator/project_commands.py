"""Phase 4 extension commands: `feature.py github project …` — the GitHub Projects (v2) board.

    github project inspect             the configured project: identity proof, protected projects, token
                                       scopes, fields and options, views, workflows (read-only)
    github project validate            are the required fields, options and views present? what would
                                       `setup` change? (read-only; exit 1 when the board is not ready)
    github project setup [--apply]     create or reuse the Status options, the Agent / Feature ID / Blocked
                                       Reason fields and the Kanban and Active Tasks views (never deletes)
    github project associate FEATURE [--apply]
                                       authorize an existing feature for the board and add its Issues
    github project sync FEATURE [--apply]
                                       local state → the feature's board items (add, Status, fields)
    github project reconcile [FEATURE…] [--apply]
                                       after a restart: sync, then report foreign/orphan items, duplicates
    github project active [--feature F]
                                       active tasks (ready, in progress, review, blocked): local state + board
    github project task RECORD         one task or feature: local state, derived values, board values
    github project test [--apply]      the live Projects integration test (two test Issues; see live_projects.py)

The board is a projection: the local state stays authoritative, and nothing on the board (a
moved card, an edited field) is ever input. Writes happen only with --apply, only to the
configured project (owner, number and node ID verified first), never to a protected project,
and only to items of coordinator-owned Issues of authorized features within the GitHub write
scope (test features only in Phase 4). `--tracker local` uses a local JSON board.

Exit codes: 0 OK; 1 the command ran but found a problem (identity not proven, setup incomplete,
deferred or refused work, drift it could not repair); 2 invalid arguments.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .errors import CoordinatorError, ProjectBoardError
from .github_api import sanitize
from .github_commands import _emit, _print, board_lines, project_sync, tracker_kind  # pylint: disable=protected-access
from .model import TaskState
from .project_sync import active_filter
from .statedir import resolve_state_dir

ACTIVE_STATES = (TaskState.READY, TaskState.IN_PROGRESS, TaskState.BLOCKED, TaskState.NEEDS_HUMAN, TaskState.FAILED)


def _setup_lines(report: dict[str, Any]) -> list[str]:
    project = report.get("project") or {}
    lines = [f"project #{project.get('number', '?')} {project.get('title', '')!r} ({project.get('id', '?')}): "
             f"{'applied' if report['applied'] else 'dry run'}"]
    lines += [f"  change: {c}" for c in report["changes"]] or ["  change: none (already configured)"]
    lines += [f"  reused: {r}" for r in report["reused"]]
    lines += [f"  operator: {m}" for m in report["manual"]]
    lines += [f"  PROBLEM: {p}" for p in report["problems"]]
    return lines


# -- read-only ---------------------------------------------------------------------------------

def cmd_inspect(args: argparse.Namespace, config: ProjectConfig) -> int:
    """The configured project, its identity proof and configuration (read-only)."""
    cfg = config.github.project
    sync = project_sync(args, config)
    data: dict[str, Any] = {"configured": {"owner": cfg.owner, "owner_type": cfg.owner_type, "number": cfg.number,
                                           "node_id": cfg.node_id, "title": cfg.title,
                                           "protected_numbers": list(cfg.protected_numbers)}}
    if hasattr(sync.issues.client, "token_scopes"):
        scopes = sync.issues.client.token_scopes()  # type: ignore[attr-defined]
        data["token_scopes"] = list(scopes) if scopes is not None else None
        data["project_scope"] = scopes is None or "project" in scopes
    warnings: list[str] = []
    try:
        project = sync.resolve(warnings)
        data["identity"] = "verified (owner, number and node ID match; not protected; updatable)"
    except (ProjectBoardError, CoordinatorError) as exc:
        data["identity"] = f"NOT VERIFIED: {sanitize(exc)}"
        project = sync.board.project(cfg.owner, cfg.owner_type, cfg.number)
    data["warnings"] = warnings
    data["protected"] = {str(n): pid for n, pid in sync.protected_ids().items()}
    if project is not None:
        names = {f.id: f.name for f in project.fields}
        data["project"] = {"id": project.id, "number": project.number, "title": project.title, "url": project.url,
                           "closed": project.closed, "viewer_can_update": project.viewer_can_update,
                           "items": project.items_total, "updated_at": project.updated_at}
        data["fields"] = [{"name": f.name, "type": f.data_type, "options": [o.name for o in f.options]}
                          for f in project.fields]
        data["views"] = [{"number": v.number, "name": v.name, "layout": v.layout, "filter": v.filter,
                          "columns": [names.get(i, i) for i in v.group_by],
                          "visible": [names.get(i, i) for i in v.visible]} for v in project.views]
        data["workflows"] = [{"name": w.name, "enabled": w.enabled} for w in project.workflows]
        data["interfering_workflows"] = sync.interfering_workflows(project)
    lines = [f"{k:20s} {v}" for k, v in data["configured"].items()]
    lines += [f"{'token scopes':20s} {data.get('token_scopes')}", f"{'identity':20s} {data['identity']}",
              f"{'protected':20s} {data['protected']} (never written)"]
    if "project" in data:
        lines += [f"{'project':20s} {data['project']}"]
        lines += [f"  field {f['name']!r} {f['type']} {f['options'] or ''}" for f in data["fields"]]
        lines += [f"  view {v['number']} {v['name']!r} {v['layout']} columns={v['columns']} filter={v['filter']!r}"
                  for v in data["views"]]
        lines += [f"  workflow {w['name']!r}: {'enabled' if w['enabled'] else 'disabled'}" for w in data["workflows"]]
    lines += [f"WARNING: {w}" for w in warnings]
    _emit(args, data, lines)
    return 0 if data["identity"].startswith("verified") and data.get("project_scope", True) else 1


def cmd_validate(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Required fields, options and views present? (read-only)"""
    sync = project_sync(args, config)
    report = sync.setup(apply=False).to_dict()
    ready = report["ok"] and not report["changes"]
    lines = _setup_lines(report)
    if ready:
        project = sync.resolve()
        sync.layout(project)
        cfg = config.github.project
        active = project.view(cfg.active_view)
        ready = project.view(cfg.board_view) is not None and active is not None and \
            active.filter == active_filter(cfg)
    lines.append("board: " + ("READY" if ready else "NOT READY — run `feature.py github project setup --apply`"))
    _emit(args, {**report, "ready": ready}, lines)
    return 0 if ready else 1


def cmd_active(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Active tasks: the local state (authoritative) and what the board shows."""
    sync = project_sync(args, config)
    rows = []
    for feature in sync.store.list_features():
        if args.feature and feature.id != args.feature:
            continue
        for task in sync.store.load_tasks(feature).values():
            if task.state not in ACTIVE_STATES and not (task.state is TaskState.VERIFIED and args.all):
                continue
            info = sync.item_status(task.id)
            rows.append({"task": task.id, "role": task.role, "local": task.state.value, "issue": task.issue_number,
                         "derived": info["desired"], "board": info["board"], "in_sync": info.get("in_sync")})
    cfg = config.github.project
    lines = [f"{r['task']:44s} {r['local']:12s} #{r['issue']}  derived={r['derived'][cfg.status_field]:12s} "
             f"board={(r['board'] or {}).get(cfg.status_field, '(not on the board)')}"
             + (f"  reason={r['derived'][cfg.blocked_reason_field]}" if r['derived'][cfg.blocked_reason_field] else "")
             for r in rows] or ["no active tasks"]
    _emit(args, rows, lines)
    return 0


def cmd_task(args: argparse.Namespace, config: ProjectConfig) -> int:
    """One task or feature on the board (read-only)."""
    data = project_sync(args, config).item_status(args.record)
    _emit(args, data, [f"{k:14s} {v}" for k, v in data.items()])
    return 0 if data.get("in_sync", True) else 1


# -- writes (dry run unless --apply) ---------------------------------------------------------------

def cmd_setup(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Create or reuse the board's fields, options and views."""
    report = project_sync(args, config).setup(apply=args.apply).to_dict()
    _emit(args, report, _setup_lines(report) + ([] if args.apply else ["dry run — pass --apply to configure"]))
    return 0 if report["ok"] else 1


def cmd_associate(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Authorize an existing feature for the board, then add its Issues."""
    sync = project_sync(args, config)
    feature = sync.store.load_feature(args.feature)
    if not args.apply:
        state = "already authorized" if sync.authorized(feature.id) else "not authorized yet"
        records = [feature, *sync.store.load_tasks(feature).values()]
        _print(f"dry run — {feature.id} ({state}): would authorize it for project #{config.github.project.number} "
               f"and add {sum(1 for r in records if r.issue_number)} Issue(s): "
               + ", ".join(f"#{r.issue_number}" for r in records if r.issue_number))
        return 0
    sync.authorize(feature.id, actor=args.actor)
    report = sync.sync(feature.id, apply=True).to_dict()
    _emit(args, report, board_lines(report))
    return 0 if report["ok"] else 1


def cmd_sync(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Converge one feature's board items with the local state."""
    report = project_sync(args, config).sync(args.feature, apply=args.apply).to_dict()
    _emit(args, report, board_lines(report) + ([] if args.apply else ["dry run — pass --apply to write"]))
    return 0 if report["ok"] else 1


def cmd_reconcile(args: argparse.Namespace, config: ProjectConfig) -> int:
    """After a restart: sync, then classify every item of the board."""
    sync = project_sync(args, config)
    features = args.features or [f.id for f in sync.store.list_features() if sync.authorized(f.id)]
    result = sync.reconcile(features, apply=args.apply)
    lines = [line for report in result["features"].values() for line in board_lines(report)]
    lines += [f"items mapped to local records: {result['mapped']}"]
    lines += [f"orphan (owned Issue, no local record; not modified): {o}" for o in result["orphans"]]
    lines += [f"foreign item (not modified): {o}" for o in result["foreign"]]
    lines += [f"DUPLICATE items for Issue #{n}: {ids}" for n, ids in result["duplicates"].items()]
    if result.get("error"):
        lines.append(f"ERROR: {result['error']}")
    lines.append(f"reconcile: {'OK' if result['ok'] else 'ATTENTION NEEDED'}")
    _emit(args, result, lines)
    return 0 if result["ok"] else 1


def cmd_test(args: argparse.Namespace, config: ProjectConfig) -> int:
    """The live Projects integration test (two test Issues on the configured board)."""
    from .live_projects import new_feature_id, run_projects_test  # pylint: disable=import-outside-toplevel
    kind = tracker_kind(args, "github")
    workdir = Path(args.workdir) if args.workdir else resolve_state_dir(app_dir=config.state_app_dir) / "github-tests"
    if not args.apply:
        _print(f"dry run — would run the Projects integration test against the {kind} tracker as feature "
               f"{new_feature_id()} in {workdir}: two test Issues (feature:test) added to project "
               f"#{config.github.project.number}, a disposable Git repository, no pull request")
        return 0
    report = run_projects_test(config, workdir, tracker_name=kind, cleanup=not args.no_cleanup, log=_print)
    _print(f"\nfeature {report['feature']}: {'PASSED' if report['passed'] else 'FAILED'}; "
           f"report {report['report_path']}")
    for rid, info in report.get("issues", {}).items():
        _print(f"  {rid:40s} #{info['number']}  item {info.get('item', '-')}  {info['url']}")
    _print(f"cleanup: {json.dumps(report.get('cleanup'), default=str)}")
    return 0 if report["passed"] else 1


def register(gsub: Any) -> None:
    """Add `github project …` to the github command group."""
    group = gsub.add_parser("project", help="the GitHub Projects board (Phase 4 extension)",
                            description=__doc__.split("\n\n", 1)[0], epilog=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    psub = group.add_subparsers(dest="project_command", required=True)
    inspect = psub.add_parser("inspect", help="identity proof, scopes, fields, views, workflows (read-only)")
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=cmd_inspect)
    validate = psub.add_parser("validate", help="are the required fields, options and views present? (read-only)")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=cmd_validate)
    setup = psub.add_parser("setup", help="create or reuse Status options, fields and views (never deletes)")
    setup.add_argument("--apply", action="store_true", help="configure the project (default: dry run)")
    setup.add_argument("--json", action="store_true")
    setup.set_defaults(func=cmd_setup)
    assoc = psub.add_parser("associate", help="authorize an existing feature for the board and add its Issues")
    assoc.add_argument("feature")
    assoc.add_argument("--actor", default="operator", help="who authorized it (recorded in the local audit log)")
    assoc.add_argument("--apply", action="store_true")
    assoc.add_argument("--json", action="store_true")
    assoc.set_defaults(func=cmd_associate)
    sync = psub.add_parser("sync", help="local state → the feature's board items")
    sync.add_argument("feature")
    sync.add_argument("--apply", action="store_true", help="write to the board (default: dry run, reads only)")
    sync.add_argument("--json", action="store_true")
    sync.set_defaults(func=cmd_sync)
    rec = psub.add_parser("reconcile", help="after a restart: sync, then report foreign/orphan items and duplicates")
    rec.add_argument("features", nargs="*", help="feature IDs (default: every authorized feature)")
    rec.add_argument("--apply", action="store_true", help="repair drift (default: dry run)")
    rec.add_argument("--json", action="store_true")
    rec.set_defaults(func=cmd_reconcile)
    active = psub.add_parser("active", help="active tasks: local state and board status (read-only)")
    active.add_argument("--feature", default=None)
    active.add_argument("--all", action="store_true", help="include verified tasks")
    active.add_argument("--json", action="store_true")
    active.set_defaults(func=cmd_active)
    task = psub.add_parser("task", help="one task or feature: local state, derived and board values (read-only)")
    task.add_argument("record", help="a task ID or a feature ID")
    task.add_argument("--json", action="store_true")
    task.set_defaults(func=cmd_task)
    test = psub.add_parser("test", help="run the live Projects integration test (two test Issues)")
    test.add_argument("--apply", action="store_true", help="run it (default: describe it)")
    test.add_argument("--workdir", default=None, help="where the disposable repository and state go")
    test.add_argument("--no-cleanup", action="store_true", help="leave the test Issues open")
    test.set_defaults(func=cmd_test)
