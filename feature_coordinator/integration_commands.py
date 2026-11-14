"""W4.7 (M1, M2) commands: `python -m feature_coordinator integration …` — integration jobs of existing branches/PRs.

    integration intake --source-repo OWNER/NAME --source-ref BRANCH --source-sha SHA --base BRANCH --base-sha SHA
                       --issue N [--pr N] [--supersede JOB] [--actor NAME] [--apply] [--json]
                            validate the request read-only (origin's refs, local commits, authors, the PR);
                            --apply records the job (INTAKE → AWAITING_APPROVAL, or REFUSED) and needs
                            github.integration.enabled. Repeating the same request returns the same job.
    integration status [--job JOB] [--json]
                            every job, or one job's full record: state, SHAs, provenance, history, attention
                            (read-only)
    integration abort --job JOB --reason TEXT [--actor NAME] [--apply] [--json]
                            any state before MERGED → ABORTED (dry run unless --apply; idempotent)
    integration approve --job JOB --stage inspect --record FILE [--signature FILE] [--apply] [--json]
                            (M2) verify an operator authorization record signed with `ssh-keygen -Y sign` (default
                            signature: FILE.sig) and bound to the job's pinned SHAs; --apply appends it to the job's
                            approvals (dry run unless --apply; idempotent)
    integration inspect --job JOB [--actor NAME] [--apply] [--json]
                            (M2) read-only inspection under a valid inspect approval: pinned SHAs re-verified (STALE),
                            provenance with GitHub attribution, the complete PR lookup and reuse rules, the changed
                            paths classified through PathPolicy; --apply records the decision

M1/M2 only: no candidate is built, no gate runs, nothing is reviewed, no PR is created and nothing is merged; no Git
ref, index or working-tree file is written. The `integrate FEATURE` command (the feature integration barrier) is
unrelated and unchanged. See docs_agents/coordination/integration-jobs.md.

Exit codes: 0 OK (intake: the job is active past INTAKE, or would be; approve: recorded, existing or would record;
inspect: passed or would pass); 1 refused, deferred, stale, needs authorization, terminal or failed (with --json the
error is also printed as {"ok": false, "error": …}); 2 invalid arguments.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

from .config import ProjectConfig
from .errors import CoordinatorError
from .integration import IntakeRequest, IntegrationJobs, abort_job, job_summary
from .integration_approval import SshSignatureVerifier, approve, check_stage
from .integration_inspect import IntegrationInspector
from .integration_intake import IntegrationIntake, ReadOnlyGit, check_actor
from .locks import LockSet
from .paths import PathPolicy
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


def _state_path(args: argparse.Namespace, config: ProjectConfig) -> Path:
    return Path(args.state_dir) if args.state_dir else resolve_state_dir(app_dir=config.state_app_dir)


def _writable_state(args: argparse.Namespace, config: ProjectConfig) -> Path:
    return ensure_state_dir(_state_path(args, config), allowed=config.allowed_state_filesystems)


def _json_errors(func: Callable[[argparse.Namespace, ProjectConfig], int]) -> Callable[..., int]:
    """With --json, a refusal or failure is also printed as JSON on stdout (the CLI still prints `error: …` to
    stderr) — the same exceptions the CLI's main() reports, so no failure leaves --json without a JSON answer."""
    def wrapper(args: argparse.Namespace, config: ProjectConfig) -> int:
        try:
            return func(args, config)
        except (CoordinatorError, OSError, ValueError) as exc:
            if not getattr(args, "json", False):
                raise
            _print(json.dumps({"ok": False, "error": {"type": type(exc).__name__, "message": str(exc)}},
                              indent=2, sort_keys=True))
            sys.stderr.write(f"error: {exc}\n")
            return 1
    wrapper.__doc__ = func.__doc__
    return wrapper


def _job_lines(job: dict[str, Any]) -> list[str]:
    src, base = job["source"], job["base"]
    lines = [f"{job['job_id']}: {job['state']}{' (terminal)' if job['terminal'] else ''} — Issue #{job['issue']}",
             f"  source {src['repo']}:{src['ref']} @ {src['sha']}",
             f"  base   {base['ref']} @ {base['expected_sha']}"]
    if job.get("pr"):
        lines.append(f"  PR #{job['pr']['number']}" + (f" at {job['pr']['head_sha']}" if job["pr"].get("head_sha")
                                                         else " (not verified yet)"))
    if job.get("supersedes"):
        lines.append(f"  supersedes {job['supersedes']}")
    if job.get("provenance"):
        prov = job["provenance"]
        lines.append(f"  provenance: {prov['commits']} commit(s); authors {', '.join(prov['authors'])}")
    if job.get("attention"):
        lines.append("  ATTENTION: " + "; ".join(job["attention"].get("reasons") or [job["attention"]["reason"]]))
    if job.get("deferred"):
        lines.append(f"  deferred: {job['deferred']['reason']} (at {job['deferred']['at']})")
    if job.get("approvals"):
        effective = next(a for a in job["approvals"] if a["id"] == job.get("effective_approval"))
        lines.append(f"  approvals: {len(job['approvals'])}; effective {effective['id']} by {effective['operator']} "
                     f"(key {effective['signer']['key']}), expires {effective['expires_at']}, "
                     f"{len(effective['paths'])} path(s) authorized"
                     + (", unverified provenance accepted" if effective["accept_unverified_provenance"] else ""))
    if job.get("inspection"):
        ins = job["inspection"]
        lines.append(f"  inspection {ins['id']}: {ins['result']} under {ins['approval']} — {ins['paths']['count']} "
                     f"path(s), PR {ins['pr_match']['result']}"
                     + (f" #{ins['pr_match']['number']}" if ins["pr_match"]["number"] else ""))
    lines.append(f"  next: {', '.join(job['allowed_next']) or 'none (terminal)'}")
    return lines


@_json_errors
def cmd_intake(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Validate an integration request; --apply records (or resumes) its job."""
    request = IntakeRequest(source_repo=args.source_repo, source_ref=args.source_ref, source_sha=args.source_sha,
                            base_ref=args.base, base_sha=args.base_sha, issue=args.issue, pr_number=args.pr)
    actor = check_actor(args.actor)
    state_dir = _writable_state(args, config) if args.apply else _state_path(args, config)
    store = StateStore(state_dir)
    git = ReadOnlyGit(Path(args.repo), config, LockSet(state_dir / "locks"),
                      timeout=config.github.publish.git_timeout_seconds)

    def pr_client() -> Any:
        from .github_commands import _tracker  # pylint: disable=import-outside-toplevel,protected-access
        return _tracker(args, config, state_dir)

    report = IntegrationIntake(store, config, git, pr_client=pr_client).run(request, apply=args.apply, actor=actor,
                                                                           supersede=args.supersede)
    lines = [f"intake: {report.outcome}" + (" (new job)" if report.created else "")]
    lines += [f"  [{c['status']:8s}] {c['check']}: {c['detail']}" for c in report.checks]
    lines += _job_lines(report.job) if report.job else []
    lines += report.messages
    _emit(args, report.to_dict(), lines)
    return 0 if report.ok else 1


def _verifier(args: argparse.Namespace, config: ProjectConfig, state_dir: Path) -> SshSignatureVerifier:
    return SshSignatureVerifier(config.github.integration.allowed_signers, repo_root=Path(args.repo),
                                scratch=state_dir / "tmp")


@_json_errors
def cmd_approve(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Verify an operator authorization record; --apply records it."""
    stage = check_stage(args.stage)
    state_dir = _writable_state(args, config) if args.apply else _state_path(args, config)
    result = approve(IntegrationJobs(StateStore(state_dir)), config, args.job, stage=stage,
                     record_path=Path(args.record), signature_path=Path(args.signature) if args.signature else None,
                     verifier=_verifier(args, config, state_dir), policy=PathPolicy(Path(args.repo), config),
                     apply=args.apply)
    label = "refused" if result["outcome"] == "refused" else "note"
    lines = [f"approve: {result['outcome']}"] + [f"  {label}: {reason}" for reason in result["reasons"]]
    if result["approval"]:
        view = result["approval"]
        lines.append(f"  {view['id']}: {view['stage']} by {view['operator']} (key {view['signer']['key']}), "
                     f"issued {view['issued_at']}, expires {view['expires_at']}, {len(view['paths'])} path(s)"
                     + (", unverified provenance accepted" if view["accept_unverified_provenance"] else "")
                     + ("" if result["effective"] else " — superseded, authorizes nothing"))
    lines += _job_lines(result["job"])
    if result["outcome"] == "would-record":
        lines.append("dry run — nothing was recorded (pass --apply to record the approval)")
    ok = result["outcome"] != "refused"
    _emit(args, {"ok": ok, **result}, lines)
    return 0 if ok else 1


@_json_errors
def cmd_inspect(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Read-only inspection of a job under a valid inspect approval; --apply records the decision."""
    actor = check_actor(args.actor)
    state_dir = _writable_state(args, config) if args.apply else _state_path(args, config)
    store = StateStore(state_dir)
    git = ReadOnlyGit(Path(args.repo), config, LockSet(state_dir / "locks"),
                      timeout=config.github.publish.git_timeout_seconds)

    def tracker() -> Any:
        from .github_commands import _tracker  # pylint: disable=import-outside-toplevel,protected-access
        return _tracker(args, config, state_dir)

    report = IntegrationInspector(store, config, git, verifier=_verifier(args, config, state_dir),
                                  tracker=tracker).run(args.job, apply=args.apply, actor=actor)
    lines = [f"inspect: {report.outcome}" + ("" if report.changed or not report.applied else " (unchanged)")]
    lines += [f"  [{c['status']:9s}] {c['check']}: {c['detail']}" for c in report.checks]
    lines += [f"  - {reason}" for reason in report.reasons]
    lines += [f"  {p['authorization']:10s} {p['class']:17s} {p['path']}"
              + (f" ({', '.join(p['flags'])})" if p["flags"] else "") for p in report.paths
              if p["authorization"] != "not-needed"]
    lines += _job_lines(report.job) if report.job else []
    lines += report.messages
    _emit(args, report.to_dict(), lines)
    return 0 if report.ok else 1


@_json_errors
def cmd_status(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Every integration job, or one job's full record (read-only)."""
    jobs = IntegrationJobs(StateStore(_state_path(args, config)))
    if args.job:
        job = job_summary(jobs.load(args.job))
        lines = _job_lines(job) + [f"  history: {h['from'] or '-'} → {h['to']} at {h['at']} by {h['actor']}: "
                                   f"{h['reason']}" for h in job["history"]]
        _emit(args, {"ok": True, "job": job}, lines)
        return 0
    rows = [job_summary(job) for job in jobs.list()]
    lines = [f"{r['job_id']}  {r['state']:24s} #{r['issue']:<5} {r['source']['ref']} @ {r['source']['sha'][:12]} → "
             f"{r['base']['ref']} @ {r['base']['expected_sha'][:12]}" + ("  ATTENTION" if r["attention"] else "")
             for r in rows] or ["no integration jobs"]
    _emit(args, {"ok": True, "jobs": rows}, lines)
    return 0


@_json_errors
def cmd_abort(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Abort a job before MERGED (dry run unless --apply)."""
    actor = check_actor(args.actor)
    state_dir = _writable_state(args, config) if args.apply else _state_path(args, config)
    result = abort_job(IntegrationJobs(StateStore(state_dir)), args.job, args.reason, actor=actor, apply=args.apply)
    lines = [f"abort: {result['outcome']}"] + _job_lines(result["job"])
    if result["outcome"] == "would-abort":
        lines.append("dry run — nothing was changed (pass --apply to abort)")
    _emit(args, {"ok": True, **result}, lines)
    return 0


def register(sub: Any) -> None:
    """Add `integration intake|status|abort|approve|inspect` to the CLI's subparsers."""
    group = sub.add_parser("integration", help="W4.7: integration jobs of existing branches/PRs (M1: intake, "
                                               "status, abort; M2: approve, inspect)",
                           description=__doc__.split("\n\n", 1)[0],
                           epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    isub = group.add_subparsers(dest="integration_command", required=True)
    intake = isub.add_parser("intake", help="validate an existing branch by SHA; --apply records the job")
    intake.add_argument("--source-repo", required=True, help="owner/name of the source repository")
    intake.add_argument("--source-ref", required=True, help="the source branch name")
    intake.add_argument("--source-sha", required=True, help="the source commit (full 40-character SHA)")
    intake.add_argument("--base", required=True, help="the target branch (the configured base branch)")
    intake.add_argument("--base-sha", required=True, help="the commit the base is expected at (full SHA)")
    intake.add_argument("--issue", required=True, type=int, help="the GitHub Issue that justifies the job")
    intake.add_argument("--pr", type=int, default=None, help="an existing PR of exactly this head and base")
    intake.add_argument("--supersede", default=None, metavar="JOB",
                        help="start a new attempt for a TERMINAL job with exactly these inputs")
    intake.add_argument("--actor", default="operator", help="who asks (recorded in the history)")
    intake.add_argument("--apply", action="store_true", help="record the job (default: dry run, reads only)")
    intake.add_argument("--json", action="store_true")
    intake.set_defaults(func=cmd_intake)
    status = isub.add_parser("status", help="integration jobs (read-only)")
    status.add_argument("--job", default=None, help="one job's full record")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)
    abort = isub.add_parser("abort", help="abort a job before MERGED (dry run unless --apply)")
    abort.add_argument("--job", required=True)
    abort.add_argument("--reason", required=True)
    abort.add_argument("--actor", default="operator", help="who aborts (recorded in the history)")
    abort.add_argument("--apply", action="store_true")
    abort.add_argument("--json", action="store_true")
    abort.set_defaults(func=cmd_abort)
    approval = isub.add_parser("approve", help="(M2) verify a signed operator authorization record; --apply records "
                                               "it")
    approval.add_argument("--job", required=True)
    approval.add_argument("--stage", required=True, choices=("inspect",), help="the approved stage (M2: inspect)")
    approval.add_argument("--record", required=True, help="the authorization record (JSON) the operator signed")
    approval.add_argument("--signature", default=None, help="its SSH signature (default: RECORD.sig)")
    approval.add_argument("--apply", action="store_true", help="record the approval (default: dry run)")
    approval.add_argument("--json", action="store_true")
    approval.set_defaults(func=cmd_approve)
    inspect = isub.add_parser("inspect", help="(M2) read-only inspection under a valid inspect approval")
    inspect.add_argument("--job", required=True)
    inspect.add_argument("--actor", default="operator", help="who inspects (recorded in the history)")
    inspect.add_argument("--apply", action="store_true", help="record the decision (default: dry run)")
    inspect.add_argument("--json", action="store_true")
    inspect.set_defaults(func=cmd_inspect)
