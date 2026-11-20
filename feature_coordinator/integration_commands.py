"""W4.7 (M1-M5) commands: `python -m feature_coordinator integration …` — integration jobs of existing branches/PRs.

    integration intake --source-repo OWNER/NAME --source-ref BRANCH --source-sha SHA --base BRANCH --base-sha SHA
                       --issue N [--pr N] [--supersede JOB] [--actor NAME] [--apply] [--json]
                            validate the request read-only (origin's refs, local commits, authors, the PR);
                            --apply records the job (INTAKE → AWAITING_APPROVAL, or REFUSED) and needs
                            github.integration.enabled. Repeating the same request returns the same job.
    integration status [--job JOB] [--json]
                            every job, or one job's full record: state, SHAs, provenance, history, attention
                            (read-only). A review is CURRENT (`reviewed`) only if it is eligible for M5 under the
                            current configuration (reviewed_for); otherwise it is shown as history with the reason
    integration abort --job JOB --reason TEXT [--actor NAME] [--apply] [--json]
                            any state before MERGED → ABORTED (dry run unless --apply; idempotent)
    integration approve --job JOB --stage inspect|merge --record FILE [--signature FILE] [--apply] [--json]
                            (M2) verify an operator authorization record signed with `ssh-keygen -Y sign` (default
                            signature: FILE.sig) and bound to the job's pinned SHAs; --apply appends it to the job's
                            approvals (dry run unless --apply; idempotent). (M5) --stage merge: the version 2 record
                            that binds the candidate, the validation and review evidence, the PR identity and the merge
                            operation — for a job in AWAITING_MERGE_APPROVAL whose evidence is current
    integration inspect --job JOB [--actor NAME] [--apply] [--json]
                            (M2) read-only inspection under a valid inspect approval: pinned SHAs re-verified (STALE),
                            provenance with GitHub attribution, the complete PR lookup and reuse rules, the changed
                            paths classified through PathPolicy; --apply records the decision
    integration validate --job JOB [--actor NAME] [--apply] [--json]
                            (M3) under a current passed inspection and a valid approval: verify the Issue and the
                            pinned SHAs, build the candidate (merge of source_sha into base_sha in a coordinator-owned
                            repository), classify its exact changes, run the gates of the affected roles in the gate
                            sandbox against it; --apply records (INSPECTING → CANDIDATE_BUILT → VALIDATING); repeat to
                            resume (passed gates are reused). Dry run: read-only, no gate runs
    integration review --job JOB [--retry --reason TEXT] [--actor NAME] [--apply] [--json]
                            (M4) the independent AI review of the EXACT validated candidate (base_sha → candidate
                            head): M3 validation current, the pinned SHAs, the Issue, the approval and the rebuilt
                            candidate re-verified, then `github.review`'s reviewer (Claude Code on the subscription, no
                            tools) in a separate process; --apply records SHA-bound evidence (VALIDATING → IN_REVIEW →
                            AWAITING_MERGE_APPROVAL, or NEEDS_REPAIR for requested changes); repeat to resume or reuse.
                            --retry starts the next round after an ERROR decision. Dry run: read-only, no reviewer runs
    integration review-exec RUN_DIR
                            INTERNAL (M4): the reviewer process `review --apply` starts for one attempt; refused unless
                            it is the job's open review attempt with its process tag
    integration merge --job JOB [--actor NAME] [--apply] [--json]
                            (M5) the operator-approved GitHub merge and its verified completion: reconcile a merge that
                            may have happened FIRST, then — under a valid signed merge approval — the shared review
                            checks, the PR (reused, or created when the approval authorizes it; a Draft is reported),
                            GitHub's merge requirements, the write-ahead intent, `PUT /pulls/N/merge` (merge-commit
                            method pinned to the approved source SHA), and the post-merge verification (parents, tree,
                            checks) that alone completes the job. Dry run (default): reads only, writes nothing

Only `merge --apply` writes to GitHub (the one PR the approval authorizes, and the merge); nothing is pushed and dev is
never touched directly; the checkout's refs, index and working tree are never written (M3-M5 write only the
coordinator state directory). The `integrate FEATURE` command (the feature integration
barrier) is unrelated and unchanged. See docs_agents/coordination/integration-jobs.md.

Exit codes: 0 OK (intake: the job is active past INTAKE, or would be; approve: recorded, existing or would record;
inspect: passed or would pass; validate: validated, or the dry run found nothing in the way; review: reviewed PASS or
PASS_WITH_NOTES, or the dry run found nothing in the way; merge: COMPLETED (merged and verified), or the dry run found
nothing in the way); 1 refused, pending verification, deferred, stale, needs authorization, blocked,
changes requested, error, terminal or failed (with --json the error is also printed as {"ok": false, "error": …});
2 invalid arguments.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import signal
import sys
from pathlib import Path
from typing import Any, Callable, Iterator

from .config import ProjectConfig, load_config
from .errors import CoordinatorError
from .integration import IntakeRequest, IntegrationJobs, abort_job, job_from_summary, job_summary
from .integration_approval import SshSignatureVerifier, approve, check_stage, now_utc
from .integration_authority import IntegrationAnchors
from .integration_candidate import CandidateArea
from .integration_inspect import IntegrationInspector
from .integration_intake import IntegrationIntake, ReadOnlyGit, check_actor
from .integration_merge import IntegrationMerger
from .integration_merge_status import merge_approval_blockers, merge_status
from .integration_review import IntegrationReviewer, review_eligibility
from .integration_validate import IntegrationValidator
from .locks import LockSet
from .paths import PathPolicy
from .statedir import ensure_state_dir, resolve_state_dir
from .store import StateStore


MERGE_VIEW_STATES = frozenset({"AWAITING_MERGE_APPROVAL", "MERGED", "COMPLETED", "FAILED"})


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


def _eligible(job: dict[str, Any], config: ProjectConfig) -> dict[str, Any]:
    """Codex re-review of M4, finding 2: a job summary's `reviewed` made the review's eligibility for M5 under the
    CURRENT configuration (integration_review.review_eligibility, as reviewed_for); `review_bound` stays the record's
    binding of the review to the candidate (integration.current_review) and `review_blocker` says why a bound review
    is not eligible. Completed in place, so the text and --json say the same; read-only, nothing is recorded."""
    decision, why = review_eligibility(job_from_summary(job), config) if job.get("review_bound") else (None, "")
    job["reviewed"], job["review_blocker"] = decision is not None, why
    return job


def _job_lines(job: dict[str, Any], config: ProjectConfig) -> list[str]:
    _eligible(job, config)
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
    lines += _candidate_lines(job)
    lines += _review_lines(job)
    lines += _merge_lines(job)
    lines.append(f"  next: {', '.join(job['allowed_next']) or 'none (terminal)'}")
    return lines


def _merge_lines(job: dict[str, Any]) -> list[str]:  # pylint: disable=too-many-branches
    """M5: the signed merge approval, the PR, the merge result and its verification, what blocks (read-only)."""
    lines: list[str] = []
    merge = job.get("merge")
    if merge:
        approval = merge["approval"]
        if approval:
            lines.append(f"  merge approval {approval['id']} by {approval['operator']} "
                         f"(key {approval['signer']['key']}): issued {approval['issued_at']}, EXPIRES "
                         f"{approval['expires_at']} — "
                         + ("VALID" if approval["valid"] else "NOT VALID: " + "; ".join(approval["problems"])))
            bound = approval["merge"]
            lines.append(f"    binds candidate {bound['candidate']['head_sha']} "
                         f"(tree {bound['candidate']['tree_sha']}), validation "
                         f"{bound['validation']['evidence']}, review {bound['review']['evidence']} "
                         f"({bound['review']['outcome']}), PR {approval['pr'] or 'none known'}"
                         + (", PR creation authorized" if bound["authorizes"]["create_pr"] else ""))
        else:
            lines.append("  merge approval: none")
        lines.append(f"  merge: validation {'eligible' if merge['validation']['eligible'] else 'NOT eligible'}, review "
                     f"{'eligible' if merge['review']['eligible'] else 'NOT eligible'}; PR "
                     + (f"#{merge['pr']['bound']} (bound)" if merge["pr"]["bound"] else
                        f"#{merge['pr']['known']} (known)" if merge["pr"]["known"] else "not known")
                     + f"; {'MERGE-READY (re-verified at merge time)' if merge['ready'] else 'not merge-ready'}")
        lines += [f"    blocker: {text}" for text in merge["blockers"]] + [f"    action: {text}" for text in
                                                                         merge["actions"]]
        if merge.get("record_template"):
            lines.append("    merge record to sign (fill operator, issued_at and expires_at, then sign it):")
            lines += ["      " + row for row in json.dumps(merge["record_template"], indent=2).splitlines()]
    elif job.get("merge_pr"):
        lines.append(f"  merge PR #{job['merge_pr']['number']} ({job['merge_pr']['origin']})")
    result = job.get("merge_result")
    if result:
        verification = result.get("verification") or {}
        lines.append(f"  merge_result: commit {result['commit_sha']}"
                     + (f" (parents {', '.join(p[:12] for p in result['parents'])}, tree {result['tree_sha']})"
                        if result.get("parents") else "")
                     + f", PR #{result.get('pr', '?')}, verification {verification.get('state', 'n/a')}"
                     + (", VERIFIED" if result.get("verified") else ""))
        lines += [f"    {c['status']:14s} {c['name']}: {c['detail'][:140]}" for c in verification.get("checks", [])]
    return lines


def _review_lines(job: dict[str, Any]) -> list[str]:
    """M4: the newest review attempt, its run, the review decision with its findings, and what still blocks."""
    lines = []
    attempt, run, review = job.get("review_attempt"), job.get("review_run"), job.get("review")
    if attempt:
        reviewer, independence = attempt["reviewer"], attempt["independence"]
        lines.append(f"  review attempt {attempt['id']} (round {attempt['round']}): {attempt['at']}, reviewer "
                     f"{reviewer['provider']} / {reviewer['model']} ({reviewer['kind']}), candidate "
                     f"{attempt['head_sha']} (tree {attempt['tree_sha']}), validation {attempt['validation']}")
        lines.append(f"    independence: {independence['level']} ({independence['mode']}) — "
                     f"{independence['limitation'] or 'established'}")
        if job.get("review_open"):
            lines.append("    OPEN: no result recorded yet (its reviewer process runs, or it was interrupted: run "
                         "review --apply again)")
        elif run:
            lines.append(f"    run {run['id']}: {run['status']}" + (f" — {run['outcome']}" if run["outcome"] else "")
                         + ("" if run["counted"] else " (given back)") + (f" — {run['error'][:160]}" if run["error"]
                                                                            else ""))
        if job.get("review_pending"):
            lines.append("    PENDING: a valid answer awaits its decision (run review --apply; no reviewer runs again)")
    if review:
        counts: dict[str, int] = {}
        for finding in review["findings"]:
            counts[finding["severity"]] = counts.get(finding["severity"], 0) + 1
        shown = ", ".join(f"{n} {s}" for s, n in sorted(counts.items())) or "no finding"
        eligibility = " (CURRENT: valid for this candidate)" if job.get("reviewed") else \
            " (HISTORICAL: not eligible for M5 under the current configuration)" if job.get("review_bound") else ""
        lines.append(f"  review {review['id']}: {review['outcome']} (verdict {review['verdict'] or '-'}) — {shown}; "
                     f"{len(review['limitations'])} limitation(s), {len(review['coordinator_notes'])} coordinator "
                     f"note(s)" + eligibility)
        lines.append(f"    evidence: candidate {review['head_sha']} (tree {review['tree_sha']}), validation "
                     f"{review['validation']}, attempt {review['attempt']}, run {review['run']}, input sha256 "
                     f"{review['input_sha256'][:16]}…, output sha256 {review['output_sha256'][:16] or '-'}")
        lines += [f"    - {f['severity']}: {f['file']}" + (f":{f['line_start']}" if f["line_start"] else "")
                  + f" {f['title']}" for f in review["findings"]]
        lines += [f"    limitation: {text}" for text in review["limitations"] + review["coordinator_notes"]]
    blockers = _blockers(job)
    if blockers:
        lines.append("  blockers: " + "; ".join(blockers))
    return lines


def _blockers(job: dict[str, Any]) -> list[str]:  # pylint: disable=too-many-return-statements
    """What still stands between the job and its next stage (derived from the record; read-only)."""
    state = job["state"]
    if job["terminal"]:
        return []
    if state == "VALIDATING":
        return [] if job.get("validated") else ["M3 validation is not complete (integration validate --apply)"]
    if state == "IN_REVIEW":
        review = job.get("review")
        if job.get("review_open"):
            return ["a review attempt has no result recorded yet (integration review --apply)"]
        if job.get("review_pending"):
            return ["a valid review answer awaits its decision (integration review --apply)"]
        if review and review["outcome"] == "ERROR" and review["attempt"] == (job.get("review_attempt") or {}).get("id"):
            return ["the review round ended in ERROR (integration review --retry --reason TEXT --apply, or abort)"]
        return ["the independent review is not decided (integration review --apply)"]
    if state == "AWAITING_MERGE_APPROVAL":
        if job.get("reviewed"):
            return ["merge approval and merge (M5: `integration approve --stage merge`, then "
                    "`integration merge --apply`; `status --job` shows the signed approval)"]
        if job.get("review_blocker"):
            return [f"{job['review_blocker']} (the review is kept as history; it is not eligible for M5)"]
        return ["the recorded review no longer applies (integration review)"]
    if state == "NEEDS_REPAIR":
        return ["repair on a new branch and a new job (M6); the source is never patched"]
    if state == "MERGED":
        return ["post-merge verification is pending (`integration merge --apply`)"]
    return []


def _candidate_lines(job: dict[str, Any]) -> list[str]:
    """M3: the candidate, the newest gate evidence per gate and the validation summary."""
    lines = []
    candidate = job.get("candidate")
    if candidate:
        roles = sorted({p["role"] for p in candidate["paths"] if p["role"]})
        lines.append(f"  candidate {candidate['evidence']}: {candidate['head_sha']} (tree {candidate['tree_sha']}), "
                     f"{len(candidate['paths'])} changed path(s), roles {', '.join(roles) or 'none'}")
        lines.append(f"    built {candidate['built_at']} with {candidate['git']}; worktree {candidate['worktree']}")
    for gate in job.get("gates") or []:
        lines.append(f"    gate {gate['gate']:22s} {gate['result']:8s} ({gate['status']}, {gate['id']}, "
                     f"{gate['duration_seconds']:.1f}s)" + (f" — {gate['summary'][:120]}" if gate["result"] != "PASS"
                                                             and gate["summary"] else ""))
    attempt = job.get("attempt")
    if attempt:
        lines.append(f"  attempt {attempt['id']}: started {attempt['at']}, {len(attempt['run'])} gate(s) to run — "
                     + ("OPEN: no decision names it (running, or interrupted: run validate again)"
                        if job.get("attempt_open") else "decided"))
    validation = job.get("validation")
    if validation:
        counts = {}
        for item in validation["gates"]:
            counts[item["result"]] = counts.get(item["result"], 0) + 1
        shown = ", ".join(f"{n} {r}" for r, n in sorted(counts.items()))
        lines.append(f"  validation {validation['id']}: {validation['result']} — {len(validation['gates'])} gate(s)"
                     + (f": {shown}" if shown else "") + (" (VALIDATED: ready for M4 review)" if job.get("validated")
                                                          else ""))
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
    lines += _job_lines(report.job, config) if report.job else []
    lines += report.messages
    _emit(args, report.to_dict(), lines)
    return 0 if report.ok else 1


def _verifier(args: argparse.Namespace, config: ProjectConfig, state_dir: Path) -> SshSignatureVerifier:
    repo = getattr(args, "repo", None)   # always set by the CLI; a direct cmd_status(Namespace) call may omit it
    return SshSignatureVerifier(config.github.integration.allowed_signers, repo_root=Path(repo) if repo else None,
                                scratch=state_dir / "tmp")


@_json_errors
def cmd_approve(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Verify an operator authorization record; --apply records it."""
    stage = check_stage(args.stage)
    state_dir = _writable_state(args, config) if args.apply else _state_path(args, config)
    result = approve(IntegrationJobs(StateStore(state_dir)), config, args.job, stage=stage,
                     record_path=Path(args.record), signature_path=Path(args.signature) if args.signature else None,
                     verifier=_verifier(args, config, state_dir), policy=PathPolicy(Path(args.repo), config),
                     apply=args.apply,
                     ready=(lambda job: merge_approval_blockers(job, config)) if stage == "merge" else None)
    label = "refused" if result["outcome"] == "refused" else "note"
    lines = [f"approve: {result['outcome']}"] + [f"  {label}: {reason}" for reason in result["reasons"]]
    if result["approval"]:
        view = result["approval"]
        lines.append(f"  {view['id']}: {view['stage']} by {view['operator']} (key {view['signer']['key']}), "
                     f"issued {view['issued_at']}, expires {view['expires_at']}"
                     + (f", candidate {view['merge']['candidate']['head_sha']}, validation "
                        f"{view['merge']['validation']['evidence']}, review {view['merge']['review']['evidence']}, PR "
                        f"{view['pr'] or 'none known'}" + (", PR creation authorized"
                                                           if view["merge"]["authorizes"]["create_pr"] else "")
                        if view["stage"] == "merge" else f", {len(view['paths'])} path(s)"
                        + (", unverified provenance accepted" if view["accept_unverified_provenance"] else ""))
                     + ("" if result["effective"] else " — superseded, authorizes nothing"))
    lines += _job_lines(result["job"], config)
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
    lines += _job_lines(report.job, config) if report.job else []
    lines += report.messages
    _emit(args, report.to_dict(), lines)
    return 0 if report.ok else 1


@_json_errors
def cmd_validate(args: argparse.Namespace, config: ProjectConfig) -> int:
    """(M3) Build the candidate and run its gates under a current passed inspection; --apply records."""
    actor = check_actor(args.actor)
    state_dir = _writable_state(args, config) if args.apply else _state_path(args, config)
    store = StateStore(state_dir)
    git = ReadOnlyGit(Path(args.repo), config, LockSet(state_dir / "locks"),
                      timeout=config.github.publish.git_timeout_seconds)

    def tracker() -> Any:
        from .github_commands import _tracker  # pylint: disable=import-outside-toplevel,protected-access
        return _tracker(args, config, state_dir)

    report = IntegrationValidator(store, config, git, verifier=_verifier(args, config, state_dir),
                                  tracker=tracker).run(args.job, apply=args.apply, actor=actor)
    lines = [f"validate: {report.outcome}" + ("" if report.changed or not report.applied else " (unchanged)")]
    lines += [f"  [{c['status']:9s}] {c['check']}: {c['detail']}" for c in report.checks]
    lines += [f"  - {reason}" for reason in report.reasons]
    if report.candidate:
        cand = report.candidate
        lines.append(f"  candidate: {cand['head_sha'] or 'CONFLICT'} (tree {cand['tree_sha']}), "
                     f"{cand['paths']['count']} changed path(s), roles {', '.join(cand['roles']) or 'none'}"
                     + (f"; conflicts: {', '.join(cand['conflicts'])}" if cand["conflicts"] else ""))
    for gate in report.gates:
        if "action" in gate:
            lines.append(f"  gate {gate['gate']:22s} would {gate['action']}"
                         + (f" ({gate['detail']})" if gate.get("detail") else ""))
        else:
            lines.append(f"  gate {gate['gate']:22s} {gate['result']:8s} ({gate['status']}"
                         + (", reused" if gate["reused"] else f", {gate['duration_seconds']:.1f}s") + ")"
                         + (f" — {gate['summary'][:160]}" if gate["result"] != "PASS" and gate["summary"] else ""))
    lines += _job_lines(report.job, config) if report.job else []
    lines += report.messages
    _emit(args, report.to_dict(), lines)
    return 0 if report.ok else 1


@_json_errors
def cmd_review(args: argparse.Namespace, config: ProjectConfig) -> int:
    """(M4) The independent AI review of the validated candidate; --apply runs or recovers it and records."""
    actor = check_actor(args.actor)
    if args.retry and not args.reason:
        raise CoordinatorError("--retry needs --reason TEXT (recorded with the new review round)")
    if args.reason and not args.retry:
        raise CoordinatorError("--reason is only used with --retry")
    state_dir = _writable_state(args, config) if args.apply else _state_path(args, config)
    store = StateStore(state_dir)
    git = ReadOnlyGit(Path(args.repo), config, LockSet(state_dir / "locks"),
                      timeout=config.github.publish.git_timeout_seconds)

    def tracker() -> Any:
        from .github_commands import _tracker  # pylint: disable=import-outside-toplevel,protected-access
        return _tracker(args, config, state_dir)

    start = None
    if args.apply:
        from .worker import cli_spawner  # pylint: disable=import-outside-toplevel
        start = cli_spawner(Path(args.repo), state_dir, tracker=args.tracker or "local",
                            project_config=args.project_config).start  # type: ignore[attr-defined]
    retry = {"by": actor, "reason": args.reason} if args.retry else None
    # the configuration file read again right before a decision (the policy in effect then; Codex re-review, 1)
    reload = (lambda: load_config(Path(args.project_config))) if args.project_config else None
    with _stop_on_signals(args.apply):
        report = IntegrationReviewer(store, config, git, verifier=_verifier(args, config, state_dir), tracker=tracker,
                                     start=start, reload=reload).run(args.job, apply=args.apply, actor=actor,
                                                                     retry=retry)
    lines = [f"review: {report.outcome}" + ("" if report.changed or not report.applied else " (unchanged)")]
    lines += [f"  [{c['status']:9s}] {c['check']}: {c['detail']}" for c in report.checks]
    lines += [f"  - {reason}" for reason in report.reasons]
    if report.reviewer:
        lines.append(f"  reviewer: {report.reviewer['provider']} / {report.reviewer['model']} "
                     f"({report.reviewer['kind']}); independence {report.independence['level']} "
                     f"({report.independence['mode']})")
    if report.context:
        ctx = report.context
        lines.append(f"  context: {ctx['files']} changed file(s), input sha256 {ctx['input_sha256'][:16]}…, diff "
                     f"sha256 {ctx['diff_sha256'][:16]}…")
        lines += [f"    note: {note}" for note in ctx["notes"]]
    if report.review:
        rev = report.review
        lines.append(f"  review {rev['id']}: {rev['outcome']} — {len(rev['findings'])} finding(s), {rev['blocking']} "
                     f"blocking, candidate {rev['head_sha']}")
        lines += [f"    - {f['severity']}: {f['file']}" + (f":{f['line_start']}" if f["line_start"] else "")
                  + f" {f['title']}" for f in rev["findings"]]
    lines.append(f"  reviewer runs: {report.reviewer_runs}")
    lines += _job_lines(report.job, config) if report.job else []
    lines += report.messages
    _emit(args, report.to_dict(), lines)
    return 0 if report.ok else 1


@_json_errors
def cmd_merge(args: argparse.Namespace, config: ProjectConfig) -> int:
    """(M5) The operator-approved GitHub merge and its verified completion; --apply writes (dry run by default)."""
    actor = check_actor(args.actor)
    state_dir = _writable_state(args, config) if args.apply else _state_path(args, config)
    store = StateStore(state_dir)
    git = ReadOnlyGit(Path(args.repo), config, LockSet(state_dir / "locks"),
                      timeout=config.github.publish.git_timeout_seconds)

    built: list[Any] = []

    def tracker() -> Any:
        """The ONE GitHub client of this run, with the TRUST ANCHORS of its integration writes bound before the
        workflow gets it (Codex F1/F2): this state directory, this checkout and the --project-config FILE, which the
        client's write boundary reads again at every write. Without --project-config no anchors are bound and every
        integration write is refused (there is no cached fallback to a permissive policy); a client that already
        carries anchors is refused (they are never replaced)."""
        if not built:
            from .github_commands import _tracker  # pylint: disable=import-outside-toplevel,protected-access
            client = _tracker(args, config, state_dir)
            if args.project_config and getattr(client, "live", False):
                client._bind_integration(  # pylint: disable=protected-access  # the merge command's own client
                    IntegrationAnchors.from_file(state_dir, Path(args.repo), Path(args.project_config)))
            built.append(client)
        return built[0]

    report = IntegrationMerger(store, config, git, verifier=_verifier(args, config, state_dir), tracker=tracker
                               ).run(args.job, apply=args.apply, actor=actor)
    lines = [f"merge: {report.outcome}" + ("" if report.changed or not report.applied else " (unchanged)")]
    lines += [f"  [{c['status']:14s}] {c['check']}: {c['detail']}" for c in report.checks]
    lines += [f"  - {reason}" for reason in report.reasons]
    if report.pr:
        lines.append(f"  PR #{report.pr['number']} ({report.pr['origin']}, {report.pr['state']}"
                     f"{', DRAFT' if report.pr['draft'] else ''}): {report.pr['url'] or report.pr['head']}")
    lines += [f"  GitHub write: {write}" for write in report.github_writes]
    lines += _job_lines(report.job, config) if report.job else []
    lines += report.messages
    _emit(args, report.to_dict(), lines)
    return 0 if report.ok else 1


@contextlib.contextmanager
def _stop_on_signals(active: bool) -> Iterator[None]:
    """While a review runs with --apply: SIGTERM/SIGINT request a graceful stop (the reviewer process is stopped and
    its attempt given back) instead of ending the command at once; the previous handlers are restored afterwards."""
    if not active:
        yield
        return
    from .supervisor import install_termination_handlers  # pylint: disable=import-outside-toplevel
    previous = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
    install_termination_handlers()
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def cmd_review_exec(args: argparse.Namespace, config: ProjectConfig) -> int:
    """INTERNAL (M4): run one integration review attempt as the reviewer process `review --apply` started."""
    from .integration_review_process import integration_exec  # pylint: disable=import-outside-toplevel
    from .supervisor import install_termination_handlers  # pylint: disable=import-outside-toplevel
    install_termination_handlers()  # SIGTERM/SIGINT: the supervisor cancels the CLI; the attempt says so
    return integration_exec(config, _state_path(args, config), Path(args.run_dir))


@_json_errors
def cmd_status(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Every integration job, or one job's full record (read-only)."""
    state_dir = _state_path(args, config)
    jobs = IntegrationJobs(StateStore(state_dir))
    if args.job:
        loaded = jobs.load(args.job)
        job = job_summary(loaded)
        if loaded.candidate is not None and loaded.state.value in MERGE_VIEW_STATES:
            job["merge"] = merge_status(loaded, config, _verifier(args, config, state_dir), now_utc())
        lines = _job_lines(job, config) + [f"  history: {h['from'] or '-'} → {h['to']} at {h['at']} by {h['actor']}: "
                                   f"{h['reason']}" for h in job["history"]]
        _emit(args, {"ok": True, "job": job}, lines)
        return 0
    rows = [_eligible(job_summary(job), config) for job in jobs.list()]
    lines = [f"{r['job_id']}  {r['state']:24s} #{r['issue']:<5} {r['source']['ref']} @ {r['source']['sha'][:12]} → "
             f"{r['base']['ref']} @ {r['base']['expected_sha'][:12]}" + ("  ATTENTION" if r["attention"] else "")
             + (f"  review {r['review']['outcome']}" if r.get("review") else "")
             + ("  (not eligible: see status --job)" if r["review_bound"] and not r["reviewed"] else "")
             for r in rows] or ["no integration jobs"]
    _emit(args, {"ok": True, "jobs": rows}, lines)
    return 0


@_json_errors
def cmd_abort(args: argparse.Namespace, config: ProjectConfig) -> int:
    """Abort a job before MERGED (dry run unless --apply)."""
    actor = check_actor(args.actor)
    state_dir = _writable_state(args, config) if args.apply else _state_path(args, config)
    result = abort_job(IntegrationJobs(StateStore(state_dir)), args.job, args.reason, actor=actor, apply=args.apply)
    if args.apply and result["job"]["state"] == "ABORTED":
        CandidateArea(state_dir, args.job).discard()   # M3: the disposable candidate (gate logs are kept)
    lines = [f"abort: {result['outcome']}"] + _job_lines(result["job"], config)
    if result["outcome"] == "would-abort":
        lines.append("dry run — nothing was changed (pass --apply to abort)")
    _emit(args, {"ok": True, **result}, lines)
    return 0


def register(sub: Any) -> None:  # pylint: disable=too-many-statements
    """Add `integration intake|status|abort|approve|inspect|validate|review|merge` to the CLI's subparsers."""
    group = sub.add_parser("integration", help="W4.7: integration jobs of existing branches/PRs (M1: intake, "
                                               "status, abort; M2: approve, inspect; M3: validate; M4: review; M5: "
                                               "merge)",
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
    approval.add_argument("--stage", required=True, choices=("inspect", "merge"),
                          help="the approved stage: inspect (M2) or merge (M5: a version 2 record)")
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
    validate = isub.add_parser("validate", help="(M3) build the integration candidate and run its validation gates")
    validate.add_argument("--job", required=True)
    validate.add_argument("--actor", default="operator", help="who validates (recorded in the history)")
    validate.add_argument("--apply", action="store_true", help="build, run the gates and record (default: dry run)")
    validate.add_argument("--json", action="store_true")
    validate.set_defaults(func=cmd_validate)
    _register_review(isub)
    merge = isub.add_parser("merge", help="(M5) the operator-approved GitHub merge and its verified completion")
    merge.add_argument("--job", required=True)
    merge.add_argument("--actor", default="operator", help="who merges (recorded in the history)")
    merge.add_argument("--apply", action="store_true",
                       help="write to GitHub (the authorized PR, the merge) and record (default: dry run, reads only)")
    merge.add_argument("--json", action="store_true")
    merge.set_defaults(func=cmd_merge)


def _register_review(isub: Any) -> None:
    """(M4) `integration review` and the internal `integration review-exec`."""
    review = isub.add_parser("review", help="(M4) the independent AI review of the validated integration candidate")
    review.add_argument("--job", required=True)
    review.add_argument("--retry", action="store_true",
                        help="start the next review round after an ERROR decision (needs --reason)")
    review.add_argument("--reason", default=None, help="why the operator retries (recorded with the new round)")
    review.add_argument("--actor", default="operator", help="who reviews (recorded in the history)")
    review.add_argument("--apply", action="store_true",
                        help="run or recover the reviewer and record (default: dry run)")
    review.add_argument("--json", action="store_true")
    review.set_defaults(func=cmd_review)
    review_exec = isub.add_parser("review-exec", help="INTERNAL (M4): the reviewer process of one review attempt")
    review_exec.add_argument("run_dir")
    review_exec.set_defaults(func=cmd_review_exec)
