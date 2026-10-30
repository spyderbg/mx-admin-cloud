#!/usr/bin/env python3
"""Issues as the task store (Workstream 2) — GitLab or GitHub, selected by testenv.toml's
[issues] tracker (or --tracker). See test_environment/README.md's "Issue tracking"
section, and docs/test_environment/implementation_plan.md Workstream 2 for the design.

    issues.py [--tracker gitlab|github] <command> ...

    issues.py doctor            token valid? project reachable? labels/status field present?
    issues.py init               create missing labels (+ GitHub's project status field), idempotent
    issues.py list [--status S] [--source S] [--all]
    issues.py show <number>
    issues.py open "<title>" [--source manual] [--severity S] [--file F ...]
    issues.py start <number>       -> in-progress
    issues.py done <number>        -> done + close
    issues.py reopen <number>
    issues.py needs-human <number>
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

from mxfix.config import ConfigError, load_issues_config  # noqa: E402
from mxissues import fingerprint, sync, trackers  # noqa: E402
from mxissues.models import SEVERITIES, SOURCES, Finding  # noqa: E402
from mxissues.sources import detector as detector_source  # noqa: E402
from mxissues.sources import e2e as e2e_source  # noqa: E402
from mxissues.sources import pylint_source, pytest_source  # noqa: E402
from mxissues.tracker import (  # noqa: E402
    OWNERSHIP_LABEL,
    STATUSES,
    IssueNotFoundError,
    IssueTracker,
    TrackerError,
    TransitionError,
)

RED = "\033[91m"
YELLOW = "\033[93m"
GREEN = "\033[92m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"


# ---------------------------------------------------------------------------
# Connection helper — every command but --help needs this
# ---------------------------------------------------------------------------

def _create(args: argparse.Namespace) -> IssueTracker | None:
    try:
        return trackers.create(load_issues_config(tracker=args.tracker))
    except (ConfigError, TrackerError) as exc:
        print(f"{RED}{exc}{RESET}", file=sys.stderr)
        return None


def _connect(args: argparse.Namespace) -> IssueTracker | None:
    tracker = _create(args)
    if tracker is None:
        return None
    try:
        tracker.connect()
    except TrackerError as exc:
        print(f"{RED}{exc}{RESET}", file=sys.stderr)
        return None
    return tracker


def _status_text(status: str | None) -> str:
    return status or f"{DIM}(no status){RESET}"


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

_DOCTOR_STYLE = {"ok": (GREEN, "[OK]"), "warn": (YELLOW, "[WARN]"), "fail": (RED, "[FAIL]"), "info": (DIM, "[INFO]")}


def cmd_doctor(args: argparse.Namespace) -> int:
    tracker = _create(args)
    if tracker is None:
        return 1

    print(f"{BOLD}tracker:{RESET} {tracker.display_name}")
    for key, value in tracker.describe():
        print(f"{BOLD}{key}:{RESET} {value}")
    for warning in tracker.config_warnings():
        print(f"{YELLOW}[WARN] {warning}{RESET}")

    try:
        tracker.connect()
    except TrackerError as exc:
        print(f"{RED}[FAIL] {exc}{RESET}")
        return 1

    checks = tracker.doctor()
    for check in checks:
        color, tag = _DOCTOR_STYLE[check.level]
        print(f"{color}{tag}{RESET} {check.message}" + (f" {YELLOW}— run `issues.py init`{RESET}" if check.fixed_by_init else ""))
        for detail in check.details:
            print(f"  {DIM}- {detail}{RESET}")

    print()
    if any(check.level == "fail" for check in checks):
        print(f"{RED}{BOLD}doctor: problems found — see [FAIL] above.{RESET}")
        return 1
    if any(check.fixed_by_init for check in checks):
        print(f"{YELLOW}{BOLD}doctor: reachable, but run `init` first.{RESET}")
    else:
        print(f"{GREEN}{BOLD}doctor: all checks passed.{RESET}")
    return 0


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    tracker = _connect(args)
    if tracker is None:
        return 1

    created, problems = tracker.init()
    for item in created:
        print(f"{GREEN}  created {item}{RESET}")
    for problem in problems:
        print(f"{RED}  {problem}{RESET}")

    if not created and not problems:
        print(f"{DIM}Everything already present — nothing to do.{RESET}")
    elif created:
        print(f"{GREEN}{BOLD}init: created {len(created)} item(s).{RESET}")
    return 1 if problems else 0


# ---------------------------------------------------------------------------
# list / show
# ---------------------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    tracker = _connect(args)
    if tracker is None:
        return 1

    labels = (f"source::{args.source}",) if args.source else ()
    issues = tracker.list_issues(status=args.status, labels=labels, owned_only=not args.all)
    if not issues:
        print(f"{DIM}No matching issues.{RESET}")
        return 0

    for issue in issues:
        owned = "" if issue.owned else f" {DIM}(not {OWNERSHIP_LABEL}){RESET}"
        closed = f" {DIM}(closed){RESET}" if issue.state == "closed" else ""
        print(f"  #{issue.number:<5} [{_status_text(issue.status)}] {issue.title}{owned}{closed}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    tracker = _connect(args)
    if tracker is None:
        return 1

    try:
        issue = tracker.get(args.number)
    except IssueNotFoundError as exc:
        print(f"{RED}{exc}{RESET}", file=sys.stderr)
        return 1

    print(f"{BOLD}#{issue.number} {issue.title}{RESET}")
    print(f"  state: {issue.state}")
    print(f"  status: {_status_text(issue.status)}")
    print(f"  labels: {', '.join(issue.labels) or '(none)'}")
    print(f"  owned by this tool: {issue.owned}")
    print(f"  url: {issue.web_url}")
    meta = fingerprint.parse_meta_block(issue.description)
    if meta:
        print(f"  fingerprint: {meta.get('fp')} (source={meta.get('source')}, occurrences={meta.get('occurrences')})")
    print()
    print(issue.description or "(no description)")
    return 0


# ---------------------------------------------------------------------------
# open (manual issue creation — still tool-owned, gets mx-auto)
# ---------------------------------------------------------------------------

def cmd_open(args: argparse.Namespace) -> int:
    tracker = _connect(args)
    if tracker is None:
        return 1

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

    labels = [OWNERSHIP_LABEL, f"source::{source}", f"severity::{args.severity}"]
    try:
        issue = tracker.create(title=args.title, description=body, labels=labels, status="open")
    except TrackerError as exc:
        print(f"{RED}{exc}{RESET}", file=sys.stderr)
        return 1

    print(f"{GREEN}Created #{issue.number}: {issue.title}{RESET}")
    print(f"  {issue.web_url}")
    return 0


# ---------------------------------------------------------------------------
# state transitions
# ---------------------------------------------------------------------------

def _do_transition(args: argparse.Namespace, target: str) -> int:
    tracker = _connect(args)
    if tracker is None:
        return 1

    try:
        result = tracker.set_status(args.number, target)
    except (TrackerError, TransitionError) as exc:  # IssueNotFoundError is a TrackerError
        print(f"{RED}{exc}{RESET}", file=sys.stderr)
        return 1

    extra = " (closed)" if result.closed else " (reopened)" if result.reopened else ""
    print(f"{GREEN}#{result.number}: {result.from_status or '(none)'} -> {result.to_status}{extra}{RESET}")
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    return _do_transition(args, "in-progress")


def cmd_done(args: argparse.Namespace) -> int:
    return _do_transition(args, "done")


def cmd_reopen(args: argparse.Namespace) -> int:
    return _do_transition(args, "open")


def cmd_needs_human(args: argparse.Namespace) -> int:
    return _do_transition(args, "needs-human")


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

    tracker = _connect(args)
    if tracker is None:
        return 1

    if not args.apply:
        print(f"{YELLOW}Dry run (pass --apply to write to {tracker.display_name}). Plan for {len(findings)} finding(s):{RESET}\n")

    report = sync.run_sync(tracker, findings, apply=args.apply, max_new_issues=args.max_new_issues)

    for item in report.items:
        marker = {"create": GREEN, "update": YELLOW, "reopen": RED, "skip-not-owned": DIM, "skip-limit": YELLOW}[item.action]
        iid_str = f"#{item.number}" if item.number is not None else "(new)"
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

def cmd_reconcile(args: argparse.Namespace) -> int:
    """Fix tool-owned issues something else closed without setting status done.

    GitLab: the GitHub->GitLab mirror closes issues itself when it pushes a commit with a
    `Closes <url>` reference to GitLab's default branch. GitHub: the fix commit's
    `Closes owner/repo#N` does the same on reaching the default branch, or a human closes
    one by hand. Either way that races with `issues.py done` and never touches the
    status. This sweeps every tool-owned closed issue whose status isn't done and
    corrects it.
    """
    tracker = _connect(args)
    if tracker is None:
        return 1

    fixed = 0
    for issue in tracker.list_issues(owned_only=True, state="closed"):
        if issue.status == "done":
            continue
        try:
            result = tracker.set_status(issue.number, "done")
        except (TrackerError, TransitionError) as exc:
            print(f"{RED}#{issue.number}: {exc}{RESET}", file=sys.stderr)
            continue
        print(f"{GREEN}#{result.number}: {result.from_status or '(none)'} -> done (status corrected){RESET}")
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
    # --tracker is top-level only (must precede the subcommand), for the same argparse
    # reason testenv.py's --mode is — see the comment in testenv.py's main().
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tracker", choices=["gitlab", "github"], default=None, help="overrides testenv.toml's [issues] tracker; must precede the subcommand")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="token valid? project reachable? labels/status field present?").set_defaults(func=cmd_doctor)
    sub.add_parser("init", help="create missing labels (+ GitHub's project status field), idempotent").set_defaults(func=cmd_init)

    p_list = sub.add_parser("list", help="list issues")
    p_list.add_argument("--status", choices=list(STATUSES))
    p_list.add_argument("--source", choices=list(SOURCES))
    p_list.add_argument("--all", action="store_true", help="include issues this tool doesn't own (no mx-auto label)")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="show one issue")
    p_show.add_argument("number", type=int)
    p_show.set_defaults(func=cmd_show)

    p_open = sub.add_parser("open", help="file a new issue (always tool-owned: mx-auto)")
    p_open.add_argument("title")
    p_open.add_argument("--source", default="manual", choices=list(SOURCES))
    p_open.add_argument("--severity", default="medium", choices=list(SEVERITIES))
    p_open.add_argument("--file", action="append", default=[], help="an affected file path; repeatable")
    p_open.set_defaults(func=cmd_open)

    p_start = sub.add_parser("start", help="-> in-progress")
    p_start.add_argument("number", type=int)
    p_start.set_defaults(func=cmd_start)

    p_done = sub.add_parser("done", help="-> done + close")
    p_done.add_argument("number", type=int)
    p_done.set_defaults(func=cmd_done)

    p_reopen = sub.add_parser("reopen", help="-> open (reopens if closed)")
    p_reopen.add_argument("number", type=int)
    p_reopen.set_defaults(func=cmd_reopen)

    p_needs_human = sub.add_parser("needs-human", help="-> needs-human")
    p_needs_human.add_argument("number", type=int)
    p_needs_human.set_defaults(func=cmd_needs_human)

    p_sync = sub.add_parser("sync", help="create/update issues from a machine-readable results file")
    p_sync.add_argument("--pytest", type=Path, default=None, metavar="XML", help="JUnit XML from validate_tests.py --junit-xml")
    p_sync.add_argument("--pylint", type=Path, default=None, metavar="JSON", help="pylint JSON from validate_build.py --json")
    p_sync.add_argument("--e2e", type=Path, default=None, metavar="JSON", help="Playwright JSON reporter output")
    p_sync.add_argument("--detector", type=Path, default=None, metavar="JSON", help="detector findings (initial_spec.md §1 schema)")
    p_sync.add_argument("--apply", action="store_true", help="actually write to the issue tracker (default: dry run)")
    p_sync.add_argument(
        "--max-new-issues", type=int, default=None, metavar="N",
        help="cap how many NEW issues this call files (existing-issue updates/reopens are never capped); default: unlimited "
             "(the testenv.toml-driven strategy runner always passes [limits] max_new_issues)",
    )
    p_sync.set_defaults(func=cmd_sync)

    sub.add_parser("reconcile", help="set status done on tool-owned issues something else closed").set_defaults(func=cmd_reconcile)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
