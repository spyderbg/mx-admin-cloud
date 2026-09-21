#!/usr/bin/env python3
"""GitLab issues as the task store (Workstream 2) — see
docs/test_environment/implementation_plan.md Workstream 2 for the design.

    issues.py doctor            token valid? project reachable? labels present?
    issues.py init               create missing labels (idempotent)
    issues.py list [--status S] [--source S] [--all]
    issues.py show <iid>
    issues.py open "<title>" [--source manual] [--severity S] [--file F ...]
    issues.py start <iid>       -> status::in-progress
    issues.py done <iid>        -> status::done + close
    issues.py reopen <iid>
    issues.py needs-human <iid>
    issues.py sync --e2e <json> | --pytest <xml> | --pylint <json>  [--apply]
    issues.py reconcile

`doctor` runs first, always: it's the only thing standing between a typo'd project path
and a pile of issues filed into the wrong repository.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.stdout.reconfigure(line_buffering=True)

import gitlab  # noqa: E402
from gitlab.exceptions import GitlabAuthenticationError, GitlabGetError  # noqa: E402

from mxissues import client, fingerprint, state, sync  # noqa: E402
from mxissues.config import ConfigError, GitLabConfig, is_insecure_remote_host, load_config  # noqa: E402
from mxissues.models import SEVERITIES, Finding  # noqa: E402
from mxissues.sources import detector as detector_source  # noqa: E402
from mxissues.sources import e2e as e2e_source  # noqa: E402
from mxissues.sources import pylint_source, pytest_source  # noqa: E402

RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

ISSUE_URL_TEMPLATE = "{host}/{project}/-/issues/{iid}"


# ---------------------------------------------------------------------------
# Connection helper — every command but --help needs this
# ---------------------------------------------------------------------------

def _connect() -> tuple[GitLabConfig, "gitlab.Gitlab", "gitlab.v4.objects.Project"] | None:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"{RED}{exc}{RESET}", file=sys.stderr)
        return None

    try:
        gl = client.connect(config)
    except GitlabAuthenticationError as exc:
        print(f"{RED}Authentication failed against {config.host}: {exc}{RESET}", file=sys.stderr)
        return None
    except Exception as exc:  # noqa: BLE001 - connection errors come in many shapes
        hint = ""
        if "Connection refused" in str(exc) or "Failed to establish" in str(exc):
            hint = (
                f"\n{YELLOW}If GITLAB_HOST points at 127.0.0.1, the SSH tunnel probably isn't up:"
                f"\n  ssh -L 8080:localhost:80 <user>@82.103.77.45{RESET}"
            )
        print(f"{RED}Could not reach {config.host}: {exc}{RESET}{hint}", file=sys.stderr)
        return None

    try:
        project = client.get_project(gl, config)
    except GitlabGetError as exc:
        print(f"{RED}Project {config.project!r} not reachable on {config.host}: {exc}{RESET}", file=sys.stderr)
        return None

    return config, gl, project


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

def cmd_doctor(_args: argparse.Namespace) -> int:
    try:
        config = load_config()
    except ConfigError as exc:
        print(f"{RED}[FAIL] {exc}{RESET}")
        return 1

    print(f"{BOLD}host:{RESET}    {config.host}")
    print(f"{BOLD}project:{RESET} {config.project}")

    if is_insecure_remote_host(config.host):
        print(
            f"{YELLOW}[WARN] {config.host} is plain HTTP and not loopback — the token crosses the "
            f"network in the clear on every call. See implementation_plan.md Workstream 2 finding 1 "
            f"for the SSH-tunnel mitigation.{RESET}",
        )

    connected = _connect()
    if connected is None:
        return 1
    _config, gl, project = connected
    print(f"{GREEN}[OK] token valid, project reachable{RESET} (id={project.id}, default_branch={project.default_branch})")

    try:
        version_info = gl.http_get("/version")
        version = version_info.get("version", "?")
        enterprise = bool(version_info.get("enterprise", False))
        edition = "Enterprise" if enterprise else "Community"
        print(f"{GREEN}[OK]{RESET} GitLab {version} ({edition} edition)")
        if enterprise:
            print(
                f"{DIM}  Scoped-label (status::*) exclusivity MAY be enforced server-side on Premium+ — "
                f"this tool enforces it client-side regardless (see mxissues/state.py), so it's "
                f"correct either way.{RESET}",
            )
        else:
            print(f"{DIM}  Community edition: scoped labels are not exclusive server-side — client-side enforcement is load-bearing here.{RESET}")
    except Exception as exc:  # noqa: BLE001
        print(f"{YELLOW}[WARN] could not read /version: {exc}{RESET}")

    try:
        existing = {label.name for label in project.labels.list(all=True)}
    except Exception as exc:  # noqa: BLE001
        print(f"{RED}[FAIL] could not list labels: {exc}{RESET}")
        return 1
    missing = [name for name in client.ALL_LABELS if name not in existing]
    if missing:
        print(f"{YELLOW}[WARN] {len(missing)} label(s) not yet created — run `issues.py init`:{RESET}")
        for name in missing:
            print(f"  {YELLOW}- {name}{RESET}")
    else:
        print(f"{GREEN}[OK] all {len(client.ALL_LABELS)} labels present{RESET}")

    print()
    print(f"{GREEN}{BOLD}doctor: all checks passed.{RESET}" if not missing else f"{YELLOW}{BOLD}doctor: reachable, but run `init` first.{RESET}")
    return 0


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def cmd_init(_args: argparse.Namespace) -> int:
    connected = _connect()
    if connected is None:
        return 1
    _config, _gl, project = connected

    existing = {label.name for label in project.labels.list(all=True)}
    created = 0
    for name in client.ALL_LABELS:
        if name in existing:
            continue
        project.labels.create({"name": name, "color": f"#{client.LABEL_COLORS.get(name, '999999')}"})
        print(f"{GREEN}  created {name}{RESET}")
        created += 1

    if created == 0:
        print(f"{DIM}All labels already present — nothing to do.{RESET}")
    else:
        print(f"{GREEN}{BOLD}init: created {created} label(s).{RESET}")
    return 0


# ---------------------------------------------------------------------------
# list / show
# ---------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    connected = _connect()
    if connected is None:
        return 1
    _config, _gl, project = connected

    kwargs: dict[str, object] = {"all": True}
    labels: list[str] = []
    if args.status:
        labels.append(f"status::{args.status}")
    if args.source:
        labels.append(f"source::{args.source}")
    if labels:
        kwargs["labels"] = ",".join(labels)
    if not args.all:
        kwargs["labels"] = (kwargs.get("labels", "") + ("," if kwargs.get("labels") else "") + client.OWNERSHIP_LABEL)

    issues = project.issues.list(**kwargs)
    if not issues:
        print(f"{DIM}No matching issues.{RESET}")
        return 0

    for issue in issues:
        status = client.status_label_of(issue) or f"{DIM}(no status label){RESET}"
        owned = "" if client.is_owned(issue) else f" {DIM}(not mx-auto){RESET}"
        print(f"  #{issue.iid:<5} [{status}] {issue.title}{owned}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    connected = _connect()
    if connected is None:
        return 1
    _config, _gl, project = connected

    try:
        issue = project.issues.get(args.iid)
    except GitlabGetError:
        print(f"{RED}No issue #{args.iid} in {project.path_with_namespace}.{RESET}", file=sys.stderr)
        return 1

    print(f"{BOLD}#{issue.iid} {issue.title}{RESET}")
    print(f"  state: {issue.state}")
    print(f"  labels: {', '.join(issue.labels) or '(none)'}")
    print(f"  owned by this tool: {client.is_owned(issue)}")
    print(f"  url: {issue.web_url}")
    meta = fingerprint.parse_meta_block(issue.description or "")
    if meta:
        print(f"  fingerprint: {meta.get('fp')} (source={meta.get('source')}, occurrences={meta.get('occurrences')})")
    print()
    print(issue.description or "(no description)")
    return 0


# ---------------------------------------------------------------------------
# open (manual issue creation — still tool-owned, gets mx-auto)
# ---------------------------------------------------------------------------

def cmd_open(args: argparse.Namespace) -> int:
    connected = _connect()
    if connected is None:
        return 1
    _config, _gl, project = connected

    source = args.source
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fp = fingerprint.compute(source, args.title)
    meta_block = fingerprint.build_meta_block(fingerprint=fp, source=source, key=args.title, first_seen=now, occurrences=1)

    body_lines = []
    if args.file:
        body_lines.append("**Affected files:**")
        body_lines.extend(f"- `{f}`" for f in args.file)
        body_lines.append("")
    body_lines.append(meta_block)
    body = "\n".join(body_lines)

    labels = [client.OWNERSHIP_LABEL, "status::open", f"source::{source}", f"severity::{args.severity}"]
    issue = project.issues.create({"title": args.title, "description": body, "labels": labels})

    print(f"{GREEN}Created #{issue.iid}: {issue.title}{RESET}")
    print(f"  {issue.web_url}")
    return 0


# ---------------------------------------------------------------------------
# state transitions
# ---------------------------------------------------------------------------

def _do_transition(iid: int, target: str) -> int:
    connected = _connect()
    if connected is None:
        return 1
    _config, _gl, project = connected

    try:
        result = state.set_status_by_iid(project, iid, target)
    except GitlabGetError:
        print(f"{RED}No issue #{iid} in {project.path_with_namespace}.{RESET}", file=sys.stderr)
        return 1
    except state.TransitionError as exc:
        print(f"{RED}{exc}{RESET}", file=sys.stderr)
        return 1

    extra = " (closed)" if result.closed else " (reopened)" if result.reopened else ""
    print(f"{GREEN}#{result.iid}: {result.from_status or '(none)'} -> {result.to_status}{extra}{RESET}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    return _do_transition(args.iid, "status::in-progress")


def cmd_done(args: argparse.Namespace) -> int:
    return _do_transition(args.iid, "status::done")


def cmd_reopen(args: argparse.Namespace) -> int:
    return _do_transition(args.iid, "status::open")


def cmd_needs_human(args: argparse.Namespace) -> int:
    return _do_transition(args.iid, "status::needs-human")


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------

def _load_findings(args: argparse.Namespace) -> list[Finding] | None:
    findings: list[Finding] = []
    try:
        if args.pytest:
            findings.extend(pytest_source.parse(args.pytest))
        if args.pylint:
            findings.extend(pylint_source.parse(args.pylint))
        if args.e2e:
            findings.extend(e2e_source.parse(args.e2e))
        if args.detector:
            findings.extend(detector_source.parse(args.detector))
    except (OSError, ValueError) as exc:
        print(f"{RED}Could not parse input: {exc}{RESET}", file=sys.stderr)
        return None
    return findings


def cmd_sync(args: argparse.Namespace) -> int:
    if not any((args.pytest, args.pylint, args.e2e, args.detector)):
        print(f"{RED}sync needs at least one of --pytest/--pylint/--e2e/--detector.{RESET}", file=sys.stderr)
        return 1

    findings = _load_findings(args)
    if findings is None:
        return 1
    if not findings:
        print(f"{DIM}No failures/findings in the given input(s) — nothing to sync.{RESET}")
        return 0

    connected = _connect()
    if connected is None:
        return 1
    _config, _gl, project = connected

    if not args.apply:
        print(f"{YELLOW}Dry run (pass --apply to write to GitLab). Plan for {len(findings)} finding(s):{RESET}\n")

    report = sync.run_sync(project, findings, apply=args.apply)

    for item in report.items:
        marker = {"create": GREEN, "update": YELLOW, "reopen": RED, "skip-not-owned": DIM}[item.action]
        iid_str = f"#{item.iid}" if item.iid is not None else "(new)"
        print(f"  {marker}{item.action:<16}{RESET} {iid_str:<6} {item.finding.title}")
        if item.detail:
            print(f"    {DIM}{item.detail}{RESET}")

    print()
    print(f"{BOLD}sync: {report.summary()}{RESET}")
    if not args.apply:
        print(f"{DIM}(dry run — nothing was written; re-run with --apply){RESET}")
    return 0


# ---------------------------------------------------------------------------
# reconcile
# ---------------------------------------------------------------------------

def cmd_reconcile(_args: argparse.Namespace) -> int:
    """Fix issues the GitHub->GitLab mirror auto-closed via `Closes #N` on merge.

    The mirror parses commit messages on push to GitLab's default branch and closes
    referenced issues itself — useful, but it races with `issues.py done`, and either
    way it never touches the status:: label. This sweeps every tool-owned issue whose
    GitLab state is closed but whose label isn't status::done, and corrects the label.
    """
    connected = _connect()
    if connected is None:
        return 1
    _config, _gl, project = connected

    candidates = project.issues.list(labels=[client.OWNERSHIP_LABEL], state="closed", all=True)
    fixed = 0
    for issue in candidates:
        if client.status_label_of(issue) == "status::done":
            continue
        try:
            result = state.transition(project, issue, "status::done")
        except state.TransitionError as exc:
            print(f"{RED}#{issue.iid}: {exc}{RESET}", file=sys.stderr)
            continue
        print(f"{GREEN}#{result.iid}: {result.from_status or '(none)'} -> status::done (label corrected){RESET}")
        fixed += 1

    if fixed == 0:
        print(f"{DIM}No mismatched issues found — nothing to reconcile.{RESET}")
    else:
        print(f"{BOLD}reconcile: corrected {fixed} issue(s).{RESET}")
    return 0


# ---------------------------------------------------------------------------
# argparse wiring
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="token valid? project reachable? labels present?").set_defaults(func=cmd_doctor)
    sub.add_parser("init", help="create missing labels (idempotent)").set_defaults(func=cmd_init)

    p_list = sub.add_parser("list", help="list issues")
    p_list.add_argument("--status", choices=["open", "in-progress", "done", "needs-human"])
    p_list.add_argument("--source", choices=["detector", "e2e", "pytest", "pylint", "manual"])
    p_list.add_argument("--all", action="store_true", help="include issues this tool doesn't own (no mx-auto label)")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="show one issue")
    p_show.add_argument("iid", type=int)
    p_show.set_defaults(func=cmd_show)

    p_open = sub.add_parser("open", help="file a new issue (always tool-owned: mx-auto)")
    p_open.add_argument("title")
    p_open.add_argument("--source", default="manual", choices=["detector", "e2e", "pytest", "pylint", "manual"])
    p_open.add_argument("--severity", default="medium", choices=list(SEVERITIES))
    p_open.add_argument("--file", action="append", default=[], help="an affected file path; repeatable")
    p_open.set_defaults(func=cmd_open)

    p_start = sub.add_parser("start", help="-> status::in-progress")
    p_start.add_argument("iid", type=int)
    p_start.set_defaults(func=cmd_start)

    p_done = sub.add_parser("done", help="-> status::done + close")
    p_done.add_argument("iid", type=int)
    p_done.set_defaults(func=cmd_done)

    p_reopen = sub.add_parser("reopen", help="-> status::open (reopens if closed)")
    p_reopen.add_argument("iid", type=int)
    p_reopen.set_defaults(func=cmd_reopen)

    p_needs_human = sub.add_parser("needs-human", help="-> status::needs-human")
    p_needs_human.add_argument("iid", type=int)
    p_needs_human.set_defaults(func=cmd_needs_human)

    p_sync = sub.add_parser("sync", help="create/update issues from a machine-readable results file")
    p_sync.add_argument("--pytest", type=Path, default=None, metavar="XML", help="JUnit XML from validate_tests.py --junit-xml")
    p_sync.add_argument("--pylint", type=Path, default=None, metavar="JSON", help="pylint JSON from validate_build.py --json")
    p_sync.add_argument("--e2e", type=Path, default=None, metavar="JSON", help="Playwright JSON reporter output")
    p_sync.add_argument("--detector", type=Path, default=None, metavar="JSON", help="detector findings (initial_spec.md §1 schema)")
    p_sync.add_argument("--apply", action="store_true", help="actually write to GitLab (default: dry run)")
    p_sync.set_defaults(func=cmd_sync)

    sub.add_parser("reconcile", help="fix labels on issues the GitHub->GitLab mirror auto-closed").set_defaults(func=cmd_reconcile)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
