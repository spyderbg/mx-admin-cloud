"""W4.6.1 commands: `python -m feature_coordinator github review …` — the advisory AI review of an approved
GitHub Issue's Draft PR (review.py).

    github review status [--json]            every review record and its current entry (read-only, local)
    github review show FEATURE [--json]      one feature's review entries, findings and attempts (read-only, local)
    github review run FEATURE [--apply] [--json]
                                             review the feature's Draft PR at its verified head now: binding,
                                             reviewer (Claude Code, no tools), outcome, one PR comment (dry run:
                                             reads GitHub and the checkout, runs no reviewer, writes nothing;
                                             --apply needs github.review.enabled)
    github review retry FEATURE --reason TEXT [--actor NAME] [--apply] [--json]
                                             supersede the current review and run a new generation for the same head
    github review exec RUN_DIR               INTERNAL (R2): the reviewer process the Worker service starts for one
                                             attempt; refused without the attempt's process tag (review_process.py)

`run` and `retry` review in the foreground (R1). R2: with github.review.enabled the Worker service also starts
reviews by itself, each attempt in a separate reviewer process (review_worker.py); while one runs, `run` and
`retry` wait for it. A review is advisory: it never merges, approves or marks a pull request ready. See
docs_agents/coordination/github-intake.md §13.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .github_commands import _emit, _state  # pylint: disable=protected-access
from .publish_commands import _coordinator  # pylint: disable=protected-access
from .review import ReviewRunner, load_review, review_rows
from .store import StateStore

_NOTE_OFF = "note: github.review.enabled is false — reviews are not run (`run --apply` is refused)"


def cmd_status(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Every review record (read-only)."""
    rows = sorted(review_rows(StateStore(_state(args, config))), key=lambda r: r["issue"])
    lines = [f"#{r['issue']:<5} {r['feature']:24s} {r['current'] or '-':28s} {r['status'] or '-':11s} "
             f"{r['outcome'] or '-':16s} independence {r['independence'] or '-'}"
             + (f"\n       {r['reason']}" if r["reason"] else "")
             + (f"\n       invalidated: {r['invalidated']}" if r["invalidated"] else "")
             + (f"\n       comment {r['comment']}" if r["comment"] else "") for r in rows] or ["no reviews"]
    if not config.github.review.enabled:
        lines.append(_NOTE_OFF)
    _emit(args, rows, lines)
    return 0


def cmd_show(args: argparse.Namespace, config: ProjectConfig) -> int:
    """One feature's review entries (read-only)."""
    record = load_review(StateStore(_state(args, config)), args.feature)
    if record is None:
        _emit(args, None, [f"{args.feature}: no review record"])
        return 0
    lines = [f"{record.id} (Issue #{record.issue_number}): {len(record.entries)} review(s), current "
             f"{record.current or '-'}"]
    for entry in record.entries:
        binding = entry["binding"]
        lines.append(f"  {entry['key']}: {entry['status']} {entry['outcome'] or ''} — PR #{binding['pr']} head "
                     f"{binding['head_sha'][:12]} base {binding['merge_base'][:12]} "
                     f"(tip {str(binding['base_tip'])[:12]}), reviewer {binding['reviewer']['provider']}/"
                     f"{binding['reviewer']['model']}, independence {entry['independence'] or '-'}, attempts "
                     f"{entry['attempts']}, binding {entry['binding_sha256'][:16]}…")
        if entry["reason"]:
            lines.append(f"    reason: {entry['reason']}")
        lines += [f"    - {f['severity']}: {f['file']}:{f['line_start'] or ''} {f['title']}" for f in entry["findings"]]
        lines += [f"    attempt {a['attempt']}: {a['status']} {a.get('error') or ''}".rstrip()
                  for a in entry["attempt_log"]]
        running = entry.get("running") or {}
        if running.get("tag"):
            lines.append(f"    running: attempt {running['attempt']} in reviewer process {running.get('pid') or '-'} "
                         f"(Worker service), since {running['started_at']}, deadline {running.get('deadline')}")
        if entry.get("held") and entry["status"] == "pending":
            lines.append(f"    held: {entry['held']['outcome']} not recorded yet ({entry['held']['error']}); the next "
                         "`run` confirms or discards it")
        if entry.get("invalidated"):
            lines.append(f"    invalidated: {entry['invalidated']['reason']}")
        for slot in ("comment", "notice"):
            if entry.get(slot):
                lines.append(f"    {slot}: {entry[slot].get('status')} {entry[slot].get('url') or ''}".rstrip())
    _emit(args, record.to_dict(), lines)
    return 0


def _run(args: argparse.Namespace, config: ProjectConfig, retry: dict[str, str] | None) -> int:
    coord = _coordinator(args, config)
    report = ReviewRunner(coord).run(args.feature, apply=args.apply, retry=retry)
    lines = [*report.lines, *(f"would {w}" for w in report.writes), *(f"deferred: {d}" for d in report.deferred),
             *(f"NEEDS ATTENTION: {a}" for a in report.attention)] or [f"{args.feature}: nothing to do"]
    lines.append(f"reviewer runs: {report.reviewer_runs}")
    if not args.apply:
        lines.append("dry run — no reviewer ran and nothing was written (pass --apply; github.review.enabled must "
                     "be true)")
    _emit(args, report.to_dict(), lines)
    return 0 if report.ok else 1


def cmd_run(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Review a feature's Draft PR (dry run unless --apply)."""
    return _run(args, config, None)


def cmd_retry(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Supersede the current review and run a new generation (dry run unless --apply)."""
    return _run(args, config, {"by": args.actor, "reason": args.reason})


def cmd_exec(args: argparse.Namespace, config: ProjectConfig) -> int:
    """INTERNAL (R2): run one review attempt as the reviewer process the Worker service started."""
    from .review_process import run_exec  # pylint: disable=import-outside-toplevel
    from .supervisor import install_termination_handlers  # pylint: disable=import-outside-toplevel
    install_termination_handlers()  # SIGTERM/SIGINT: the supervisor cancels the CLI; the attempt says so
    return run_exec(config, _state(args, config), Path(args.run_dir))


def register(gsub: Any) -> None:
    """Add `github review …` to the `github` command group."""
    group = gsub.add_parser("review", help="the advisory AI review of a published Draft PR (W4.6.1)",
                            description=__doc__.split("\n\n", 1)[0], epilog=__doc__,
                            formatter_class=argparse.RawDescriptionHelpFormatter)
    rsub = group.add_subparsers(dest="review_command", required=True)
    status = rsub.add_parser("status", help="every review record (read-only)")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)
    show = rsub.add_parser("show", help="one feature's review entries (read-only)")
    show.add_argument("feature")
    show.add_argument("--json", action="store_true")
    show.set_defaults(func=cmd_show)
    run = rsub.add_parser("run", help="review a feature's Draft PR now (dry run unless --apply)")
    run.add_argument("feature")
    run.add_argument("--apply", action="store_true")
    run.add_argument("--json", action="store_true")
    run.set_defaults(func=cmd_run)
    retry = rsub.add_parser("retry", help="supersede the current review and run a new one (dry run unless --apply)")
    retry.add_argument("feature")
    retry.add_argument("--reason", required=True)
    retry.add_argument("--actor", default="operator")
    retry.add_argument("--apply", action="store_true")
    retry.add_argument("--json", action="store_true")
    retry.set_defaults(func=cmd_retry)
    run_one = rsub.add_parser("exec", help="INTERNAL: the reviewer process the Worker service starts (R2)")
    run_one.add_argument("run_dir")
    run_one.set_defaults(func=cmd_exec)
