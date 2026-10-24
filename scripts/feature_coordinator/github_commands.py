"""Phase 4 commands for scripts/feature.py: GitHub Issue coordination (registered by cli.build_parser).

    github status                      gh installed, authenticated, repository access, Issue permissions,
                                       rate limit and the endpoints the adapter needs (read-only)
    github labels [--apply]            create the missing feature:* labels (existing labels are never edited)
    github sync FEATURE [--apply]      local state → Issues, plus validated /feature commands → local state
    github reconcile [FEATURE…] [--apply]
                                       after a restart: sync, then report drift, duplicates and orphans
    github inspect NUMBER              ownership verdict for one Issue (read-only)
    github owned [--feature F]         Issues that carry this coordinator's ownership marker (read-only)
    github deps FEATURE [--remote]     the dependency graph with states and Issue numbers
    github test-lifecycle [--apply]    the Phase 4 live acceptance scenario (4 test Issues; see live_test.py)
    github cleanup-test FEATURE [--apply]
                                       final comment + close for a TEST feature's Issues (repeatable)
    github project …                   the GitHub Projects board (Phase 4 extension; see `github project -h`)
    github intake …                    W4.3.1: approved GitHub Issues become Worker tasks (`github intake -h`)
    github publish …                   W4.3.3: their Draft PR, the human merge, completion (`github publish -h`)
    hold TASK --reason TEXT [--apply]  hold a task for a human decision (needs-human)
    release TASK [--apply]             release a human hold

The `github` commands use the real adapter (`gh api`, existing login) unless `--tracker local`
is given. Writes happen only with --apply; the configured write scope (test features only in
Phase 4) still applies. Only `github publish poll --apply` creates a pull request: the ONE Draft PR of
an approved GitHub Issue's feature, with github.publish.enabled (W4.3.3). `github sync` also converges the
feature's project board items when `github.project` is enabled and the feature is authorized
for the board (`--no-board` skips it).

Exit codes: 0 OK; 1 the command ran but found a problem (a missing capability, deferred or
refused work, drift it could not repair); 2 invalid arguments.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .errors import GitHubError, ProjectBoardError
from .github_api import GitHubClient, sanitize
from .gitops import branch_contains
from .issue_sync import IssueSync
from .issues import LABEL_SPECS, MockIssueTracker, TrackerClient
from .operations import make_board
from .project_sync import ProjectSync
from .statedir import ensure_state_dir, resolve_state_dir
from .store import StateStore


def _print(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _emit(args: argparse.Namespace, data: Any, lines: list[str]) -> None:
    if getattr(args, "json", False):
        _print(json.dumps(data, indent=2, sort_keys=True, default=str))
    else:
        for line in lines:
            _print(line)


def _state(args: argparse.Namespace, config: ProjectConfig) -> Path:
    path = Path(args.state_dir) if args.state_dir else resolve_state_dir(app_dir=config.state_app_dir)
    return ensure_state_dir(path, allowed=config.allowed_state_filesystems)


def tracker_kind(args: argparse.Namespace, default: str) -> str:
    """The tracker chosen with --tracker, or the command group's default."""
    return getattr(args, "tracker", None) or default


def _tracker(args: argparse.Namespace, config: ProjectConfig, state_dir: Path) -> TrackerClient:
    if tracker_kind(args, "github") == "github":
        return GitHubClient.from_config(config.github)
    operator = config.github.command_authors[0] if config.github.command_authors else "operator"
    return MockIssueTracker(state_dir / "tracker.json", permissions={"feature-coordinator": "admin", operator: "admin"})


def _sync(args: argparse.Namespace, config: ProjectConfig) -> IssueSync:
    state_dir = _state(args, config)
    return IssueSync(StateStore(state_dir), _tracker(args, config, state_dir), config)


def project_sync(args: argparse.Namespace, config: ProjectConfig, *, issues: IssueSync | None = None) -> ProjectSync:
    """The board synchronizer for the CLI's state directory and tracker kind (Phase 4 extension)."""
    if not config.github.project.enabled:
        raise ProjectBoardError("github.project is disabled in the configuration")
    state_dir = _state(args, config)
    if issues is None:
        issues = IssueSync(StateStore(state_dir), _tracker(args, config, state_dir), config)
    client = make_board(tracker_kind(args, "github"), config, state_dir, issues.client)
    repo = Path(args.repo)
    return ProjectSync(issues.store, client, issues, config,
                       revision_check=lambda feature, sha: branch_contains(repo, feature.branch, sha))


def board_lines(report: dict[str, Any]) -> list[str]:
    """Human-readable lines of a BoardReport dict."""
    project = report.get("project") or {}
    lines = [f"board {report['feature_id']}: project #{project.get('number', '?')}; "
             f"{'applied' if report['applied'] else 'dry run'}; ok={report['ok']}"]
    for rid, info in sorted(report.get("items", {}).items()):
        reason = f" — {info['Blocked Reason']}" if info.get("Blocked Reason") else ""
        lines.append(f"  {rid:44s} #{info['issue']:<5} {info.get('Status', ''):12s} {info.get('Agent', ''):13s}"
                     f"{reason}")
    for key in ("writes", "drift", "warnings", "deferred"):
        lines += [f"  {key.rstrip('s')}: {sanitize(item, 240)}" for item in report.get(key, [])]
    for key in ("added", "adopted", "protected", "skipped"):
        if report.get(key):
            lines.append(f"  {key}: {report[key]}")
    return lines


def _report_lines(report: dict[str, Any]) -> list[str]:
    lines = [f"{report['feature_id']}: {'applied' if report['applied'] else 'dry run'}; ok={report['ok']}"]
    for rid, info in sorted(report.get("issues", {}).items()):
        lines.append(f"  {rid:44s} #{info['number']:<6} {info['state']:7s} {','.join(info['labels'])}")
    for title, label in (("writes", "write"), ("drift", "drift"), ("deferred", "deferred")):
        lines += [f"  {label}: {sanitize(item)}" for item in report.get(title, [])]
    for key in ("created", "adopted", "duplicates", "protected"):
        if report.get(key):
            lines.append(f"  {key}: {report[key]}")
    lines += [f"  command {c['command']} by {c['author']}: {c['result']}" for c in report.get("commands", [])]
    return lines


# -- status --------------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Verify every capability the adapter needs; report exactly what is missing."""
    gh = config.github
    missing: list[str] = []
    rows: list[tuple[str, str]] = []
    if tracker_kind(args, "github") != "github":
        _print("tracker: local JSON tracker (no GitHub connection)")
        return 0
    exe = shutil.which(gh.gh_executable)
    if exe is None:
        _print(f"gh: NOT INSTALLED ({gh.gh_executable!r} not on PATH) — install the GitHub CLI")
        return 1
    version = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=30,
                             check=False).stdout.splitlines()
    rows.append(("gh", f"{version[0] if version else '?'} at {exe}"))
    client = GitHubClient.from_config(gh)
    try:
        viewer = client.viewer_login()
        rows.append(("authentication", f"OK, acting as {viewer} (token never read or printed)"))
        info = client.repository_info()
        perms = info["permissions"]
        rows.append(("repository", f"{info['full_name']} (private={info['private']}, archived={info['archived']})"))
        rows.append(("issues enabled", str(info["has_issues"])))
        rows.append(("permissions", ", ".join(k for k, v in sorted(perms.items()) if v) or "none"))
        if not info["has_issues"]:
            missing.append("the repository has Issues disabled")
        if info["archived"]:
            missing.append("the repository is archived (read-only)")
        if not (perms.get("push") or perms.get("maintain") or perms.get("admin")):
            missing.append("write (push) permission: needed to create labels and close Issues")
        client.list_issues(labels=["feature:parent"], state="all")
        rows.append(("endpoint issues (list)", "OK"))
        client.list_labels()
        rows.append(("endpoint labels (list)", "OK"))
        client.list_pull_requests(head="feature/does-not-exist")
        rows.append(("endpoint pulls (read-only)", "OK"))
        rate = client.rate_limit()
        rows.append(("rate limit (core)", f"{rate['remaining']}/{rate['limit']} remaining"))
        scopes = client.token_scopes()
        rows.append(("token scopes (header)", ", ".join(scopes) if scopes is not None else "not reported"))
        if gh.project.enabled and scopes is not None and "project" not in scopes:
            missing.append("the `project` scope (GitHub Projects board): the operator runs "
                           "`gh auth refresh -s project`")
        rows.append(("write scope", f"{gh.write_scope}; pull requests: "
                     + ("Draft PRs of approved GitHub Issues only (github.publish.enabled)" if gh.publish.enabled
                        else "creation disabled (github.publish.enabled is false)")))
    except GitHubError as exc:
        missing.append(sanitize(exc))
    for name, value in rows:
        _print(f"{name:28s} {value}")
    for item in missing:
        _print(f"MISSING: {item}")
    _print("status: " + ("OK" if not missing else "INSUFFICIENT — live operations stopped"))
    return 0 if not missing else 1


def cmd_labels(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Create the missing feature:* labels."""
    sync = _sync(args, config)
    for name, (color, description) in sorted(LABEL_SPECS.items()):
        if args.apply:
            _print(f"{name:24s} {sync.client.ensure_label(name, color, description)}")
        else:
            _print(f"{name:24s} would ensure (#{color}) {description}")
    if not args.apply:
        _print("dry run — pass --apply to create missing labels")
    return 0


# -- sync / reconcile ------------------------------------------------------------------------

def cmd_sync(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Converge one feature's Issues (and its project board items) with the local state."""
    sync = _sync(args, config)
    report = sync.sync(args.feature, apply=args.apply).to_dict()
    lines = _report_lines(report)
    ok = report["ok"]
    if config.github.project.enabled and not args.no_board:
        board = project_sync(args, config, issues=sync).sync(args.feature, apply=args.apply).to_dict()
        report = {**report, "board": board}
        lines += board_lines(board)
        ok = ok and board["ok"]
    _emit(args, report, lines + ([] if args.apply else ["dry run — pass --apply to write"]))
    return 0 if ok else 1


def cmd_reconcile(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Reconcile after a restart."""
    sync = _sync(args, config)
    features = args.features or [f.id for f in sync.store.list_features()]
    result = sync.reconcile(features, apply=args.apply)
    lines = [line for report in result["features"].values() for line in _report_lines(report)]
    lines += [f"orphan (owned, not in the local state; not modified): {o}" for o in result["orphans"]]
    lines += [f"DUPLICATE key {k}: {v}" for k, v in result["duplicates"].items()]
    lines.append(f"reconcile: {'OK' if result['ok'] else 'ATTENTION NEEDED'}")
    _emit(args, result, lines)
    return 0 if result["ok"] else 1


def cmd_inspect(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Ownership verdict for one Issue."""
    data = _sync(args, config).inspect(args.number)
    _emit(args, data, [f"{k:16s} {v}" for k, v in data.items()])
    return 0


def cmd_owned(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Issues owned by this coordinator."""
    found = _sync(args, config).find_owned(args.feature)
    data = [{"number": i.number, "state": i.state, "feature": m.feature, "task": m.task, "purpose": m.purpose,
             "url": i.url} for i, m in found]
    _emit(args, data, [f"#{d['number']:<6} {d['state']:7s} {d['purpose']:15s} {d['feature']} {d['task']}"
                       for d in data] or ["no coordinator-owned Issues"])
    return 0


def cmd_deps(args: argparse.Namespace, config: ProjectConfig) -> int:
    """The feature's dependency graph, with local states and Issue numbers."""
    sync = _sync(args, config)
    feature = sync.store.load_feature(args.feature)
    tasks = sync.store.load_tasks(feature)
    data = []
    for task in tasks.values():
        entry = {"task": task.id, "role": task.role, "state": task.state.value, "issue": task.issue_number,
                 "depends_on": [{"task": d, "state": tasks[d].state.value, "issue": tasks[d].issue_number}
                                for d in task.depends_on],
                 "blocked_reason": task.blocked_reason}
        if args.remote and task.issue_number:
            entry["remote_labels"] = sorted(sync.client.get_issue(task.issue_number).labels)
        data.append(entry)
    lines = [f"{feature.id} ({feature.state.value}), parent Issue #{feature.issue_number}"]
    for e in data:
        deps = ", ".join(f"{d['task']} [{d['state']}, #{d['issue']}]" for d in e["depends_on"]) or "nothing"
        lines.append(f"  {e['task']:44s} {e['state']:12s} #{e['issue']}  needs: {deps}")
        if e.get("remote_labels") is not None:
            lines.append(f"  {'':44s} GitHub labels: {e['remote_labels']}")
    _emit(args, data, lines)
    return 0


# -- test lifecycle and cleanup -------------------------------------------------------------------

def cmd_test_lifecycle(args: argparse.Namespace, config: ProjectConfig) -> int:
    """The Phase 4 live acceptance scenario."""
    from .live_test import new_feature_id, run_lifecycle  # pylint: disable=import-outside-toplevel
    kind = tracker_kind(args, "github")
    workdir = Path(args.workdir) if args.workdir else resolve_state_dir(app_dir=config.state_app_dir) / "github-tests"
    if not args.apply:
        _print(f"dry run — would run the Phase 4 lifecycle against the {kind} tracker "
               f"({config.github.repository if kind == 'github' else 'local JSON'}) as feature {new_feature_id()} "
               f"in {workdir}: 4 test Issues (feature:test), a disposable Git repository, no pull request")
        return 0
    feature_id = new_feature_id()
    if kind == "github":
        client: Any = GitHubClient.from_config(config.github)
        operator_comment = client.create_comment
    else:
        # The local tracker lives where `--tracker local` looks for it, so the restart step's
        # separate process reconciles against the same Issues.
        operator = config.github.command_authors[0]
        state_dir = ensure_state_dir(workdir / feature_id / "state")
        client = MockIssueTracker(state_dir / "tracker.json", permissions={"feature-coordinator": "admin",
                                                                           operator: "admin"})

        def operator_comment(number: int, body: str) -> Any:
            return client.add_comment_as(number, body, operator)
    report = run_lifecycle(client, workdir, tracker_name=kind, operator_comment=operator_comment,
                           cleanup=not args.no_cleanup, feature_id=feature_id, log=_print)
    _print(f"\nfeature {report['feature']}: {'PASSED' if report['passed'] else 'FAILED'}; "
           f"report {report['report_path']}")
    for rid, info in report.get("issues", {}).items():
        _print(f"  {rid:44s} #{info['number']}  {info['url']}")
    _print(f"cleanup: {json.dumps(report.get('cleanup'), default=str)}")
    return 0 if report["passed"] else 1


def cmd_cleanup_test(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Final verification comment and close for a TEST feature's Issues."""
    summary = args.summary or "Closed by `feature.py github cleanup-test`; see the coordinator report for results."
    report = _sync(args, config).cleanup_test_feature(args.feature, summary, apply=args.apply).to_dict()
    _emit(args, report, _report_lines(report) + ([] if args.apply else ["dry run — pass --apply to close"]))
    return 0 if report["ok"] else 1


# -- human escalation (local) ---------------------------------------------------------------------

def _coordinator(args: argparse.Namespace, config: ProjectConfig):
    from .operations import build_coordinator  # pylint: disable=import-outside-toplevel
    return build_coordinator(config, Path(args.repo), _state(args, config), tracker_kind=tracker_kind(args, "local"))


def cmd_hold(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Hold a task for a human decision."""
    if not args.apply:
        _print(f"dry run — would hold {args.task}: {args.reason}")
        return 0
    task = _coordinator(args, config).hold_task(args.task, reason=args.reason, actor=args.actor)
    _print(f"{task.id}: {task.state.value}")
    return 0


def cmd_release(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Release a human hold."""
    if not args.apply:
        _print(f"dry run — would release {args.task}")
        return 0
    task = _coordinator(args, config).release_task(args.task, actor=args.actor)
    _print(f"{task.id}: {task.state.value}" + (f" ({task.blocked_reason})" if task.blocked_reason else ""))
    return 0


def register(sub: Any) -> Any:
    """Add the Phase 4 commands to the CLI's subparsers; returns the `github` group's subparsers."""
    gsub = _register_github(sub)
    hold = sub.add_parser("hold", help="hold a task for a human decision (needs-human)")
    hold.add_argument("task")
    hold.add_argument("--reason", required=True)
    hold.add_argument("--actor", default="operator")
    hold.add_argument("--apply", action="store_true")
    hold.set_defaults(func=cmd_hold)
    release = sub.add_parser("release", help="release a human hold")
    release.add_argument("task")
    release.add_argument("--actor", default="operator")
    release.add_argument("--apply", action="store_true")
    release.set_defaults(func=cmd_release)
    return gsub


def _register_github(sub: Any) -> Any:
    group = sub.add_parser("github", help="GitHub Issue coordination (real adapter unless --tracker local)",
                           description=__doc__.split("\n\n", 1)[0], epilog=__doc__,
                           formatter_class=argparse.RawDescriptionHelpFormatter)
    gsub = group.add_subparsers(dest="github_command", required=True)
    gsub.add_parser("status", help="verify gh, authentication, repository access and permissions (read-only)") \
        .set_defaults(func=cmd_status)
    labels = gsub.add_parser("labels", help="create missing feature:* labels")
    labels.add_argument("--apply", action="store_true", help="create them (default: dry run)")
    labels.set_defaults(func=cmd_labels)
    sync = gsub.add_parser("sync", help="synchronize one feature's Issues with the local state")
    sync.add_argument("feature")
    sync.add_argument("--apply", action="store_true", help="write to GitHub (default: dry run, reads only)")
    sync.add_argument("--json", action="store_true", help="print the report as JSON")
    sync.add_argument("--no-board", action="store_true", help="skip the project board (Issues only)")
    sync.set_defaults(func=cmd_sync)
    rec = gsub.add_parser("reconcile", help="after a restart: sync, report drift/duplicates/orphans")
    rec.add_argument("features", nargs="*", help="feature IDs (default: every feature in the state)")
    rec.add_argument("--apply", action="store_true", help="repair drift (default: dry run)")
    rec.add_argument("--json", action="store_true")
    rec.set_defaults(func=cmd_reconcile)
    inspect = gsub.add_parser("inspect", help="ownership verdict for one Issue (read-only)")
    inspect.add_argument("number", type=int)
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=cmd_inspect)
    owned = gsub.add_parser("owned", help="list Issues carrying this coordinator's ownership marker")
    owned.add_argument("--feature", default=None)
    owned.add_argument("--json", action="store_true")
    owned.set_defaults(func=cmd_owned)
    deps = gsub.add_parser("deps", help="dependency graph with states and Issue numbers")
    deps.add_argument("feature")
    deps.add_argument("--remote", action="store_true", help="also read each Issue's labels from GitHub")
    deps.add_argument("--json", action="store_true")
    deps.set_defaults(func=cmd_deps)
    life = gsub.add_parser("test-lifecycle", help="run the Phase 4 live acceptance scenario (4 test Issues)")
    life.add_argument("--apply", action="store_true", help="run it (default: describe it)")
    life.add_argument("--workdir", default=None, help="where the disposable repository and state go")
    life.add_argument("--no-cleanup", action="store_true", help="leave the test Issues open")
    life.set_defaults(func=cmd_test_lifecycle)
    clean = gsub.add_parser("cleanup-test", help="final comment + close for a TEST feature's Issues")
    clean.add_argument("feature")
    clean.add_argument("--summary", default="", help="text for the final verification comment")
    clean.add_argument("--apply", action="store_true")
    clean.add_argument("--json", action="store_true")
    clean.set_defaults(func=cmd_cleanup_test)
    return gsub
