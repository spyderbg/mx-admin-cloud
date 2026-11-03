"""W4.3.3 commands: `python -m feature_coordinator github publish …` — the Draft PR and completion of
an approved GitHub Issue.

    github publish status [--json]           every approved Issue's feature: its PR candidate, the published PR,
                                             the merge evidence, a stop (read-only, local: no GitHub request)
    github publish poll [FEATURE…] [--apply] [--json]
                                             one publication pass: integrate, push the feature branch, open or
                                             adopt the ONE Draft PR, detect the human merge, complete, close the
                                             Issue, Project #2 Done (dry run: reads GitHub and the remote only;
                                             --apply needs github.publish.enabled)
    github publish reset FEATURE [--apply]   after an operator resolved a stop: let publication continue

The Worker service runs the pass by itself when `github.publish.enabled` is true. Nothing ever merges a
pull request. See docs_agents/coordination/github-intake.md §10.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .github_api import sanitize
from .github_commands import _emit, _state, _tracker, tracker_kind  # pylint: disable=protected-access
from .intake import load_intake
from .model import utc_now
from .publish import PullRequestPublisher
from .store import StateStore


def _coordinator(args: argparse.Namespace, config: ProjectConfig) -> Any:
    from .operations import build_coordinator  # pylint: disable=import-outside-toplevel
    state_dir = _state(args, config)
    return build_coordinator(config, Path(args.repo), state_dir, tracker=_tracker(args, config, state_dir),
                             tracker_kind=tracker_kind(args, "github"))


def publication_rows(store: StateStore) -> list[dict[str, Any]]:
    """Every approved Issue's feature with its publication facts (read-only, local)."""
    rows = []
    for feature in store.list_features():
        record = load_intake(store, feature.id)
        if record is None:
            continue
        pr = feature.pull_request
        rows.append({"feature": feature.id, "issue": record.issue_number, "state": feature.state.value,
                     "intake": record.status, "branch": feature.branch, "base": feature.base_branch,
                     "head_sha": pr.head_sha if pr else None, "pr": pr.number if pr else None,
                     "url": pr.url if pr else None, "origin": pr.origin if pr else None,
                     "draft": pr.draft if pr else None, "pushed": pr.pushed if pr else None,
                     "pending_create": pr.pending_create if pr else None, "merge": pr.merge if pr else None,
                     "attention": pr.attention if pr else None, "merged_sha": feature.merged_sha,
                     "completed": record.completed})
    return sorted(rows, key=lambda r: r["issue"])


def cmd_status(args: argparse.Namespace, config: ProjectConfig) -> int:
    """The local publication facts (read-only)."""
    rows = publication_rows(StateStore(_state(args, config)))
    lines = []
    for r in rows:
        pr = f"PR #{r['pr']} ({r['origin']}{', draft' if r['draft'] else ''}) {r['url']}" if r["pr"] else "no PR yet"
        lines.append(f"#{r['issue']:<5} {r['feature']:14s} {r['state']:8s} intake {r['intake']:7s} "
                     f"head {(r['head_sha'] or '-')[:12]:12s} {pr}")
        if r["merge"]:
            merge, closed = r["merge"], (r["completed"] or {}).get("closed_by", "not yet")
            lines.append(f"       merged as {merge.get('merge_commit_sha', '')[:12]} by {merge.get('merged_by')} at "
                         f"{merge.get('merged_at')}; Issue closed: {closed}")
        if r["attention"]:
            lines.append(f"       STOPPED: {sanitize(r['attention'].get('reason', ''), 300)} — after resolving it: "
                         f"`python -m feature_coordinator github publish reset {r['feature']} --apply`")
    lines = lines or ["no approved GitHub Issue features"]
    if not config.github.publish.enabled:
        lines.append("note: github.publish.enabled is false — nothing is published until an operator enables it")
    _emit(args, rows, lines)
    return 0


def cmd_poll(args: argparse.Namespace, config: ProjectConfig) -> int:
    """One publication pass (dry run unless --apply)."""
    coord = _coordinator(args, config)
    client = coord.sync.client
    before = (getattr(client, "requests", 0), client.writes)
    report = PullRequestPublisher(coord).poll(apply=args.apply, features=args.features)
    lines = report.lines() or ["nothing to do: no approved GitHub Issue's feature is ready for publication"]
    lines += [f"{fid}: {info['state']} — {info['status']}{': ' + info['detail'] if info['detail'] else ''}"
              for fid, info in sorted(report.features.items())]
    requests = getattr(client, "requests", None)
    lines.append(f"GitHub requests: {requests - before[0]}, of them writes: {client.writes - before[1]}"
                 if requests is not None else f"tracker writes: {client.writes - before[1]}")
    if not args.apply:
        lines.append("dry run — nothing was written (pass --apply; github.publish.enabled must be true)")
    _emit(args, report.to_dict(), lines)
    return 0 if report.ok else 1


def cmd_reset(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Clear a stopped publication (local only); the next pass checks everything again."""
    store = StateStore(_state(args, config))
    feature = store.load_feature(args.feature)
    stop = feature.pull_request.attention if feature.pull_request else None
    if not stop:
        _emit(args, {"feature": feature.id, "reset": False}, [f"{feature.id}: publication is not stopped"])
        return 0
    if not args.apply:
        _emit(args, {"feature": feature.id, "attention": stop},
              [f"{feature.id}: stopped since {stop.get('since')}: {sanitize(stop.get('reason', ''), 300)}",
               "dry run — pass --apply to let publication continue (every check runs again)"])
        return 0
    with store.transaction():
        fresh = store.load_feature(args.feature)
        assert fresh.pull_request is not None
        fresh.pull_request.attention = None
        store.save_feature(fresh)
        store.append_event("publish-reset", feature=fresh.id, actor=args.actor, attention=stop, at=utc_now())
    _emit(args, {"feature": feature.id, "reset": True}, [f"{feature.id}: publication continues with the next pass"])
    return 0


def register(gsub: Any) -> None:
    """Add `github publish …` to the `github` command group."""
    group = gsub.add_parser("publish", help="the Draft PR and completion of an approved GitHub Issue (W4.3.3)",
                            description=__doc__.split("\n\n", 1)[0], epilog=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    psub = group.add_subparsers(dest="publish_command", required=True)
    status = psub.add_parser("status", help="publication facts of every approved Issue's feature (read-only, local)")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)
    poll = psub.add_parser("poll", help="one publication pass (dry run unless --apply)")
    poll.add_argument("features", nargs="*", help="feature IDs (default: every approved Issue's feature)")
    poll.add_argument("--apply", action="store_true", help="integrate, push, open the Draft PR, complete")
    poll.add_argument("--json", action="store_true")
    poll.set_defaults(func=cmd_poll)
    reset = psub.add_parser("reset", help="let a stopped publication continue (local)")
    reset.add_argument("feature")
    reset.add_argument("--actor", default="operator")
    reset.add_argument("--apply", action="store_true")
    reset.add_argument("--json", action="store_true")
    reset.set_defaults(func=cmd_reset)
