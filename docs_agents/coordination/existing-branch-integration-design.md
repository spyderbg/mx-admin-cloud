# Integration Job — Worker Integration of Existing Branches and PRs into `dev` (W4.7 design)

> Owner role: **coordination**. Status: **DESIGN ONLY (Issue #64)**. Nothing here is implemented; no runtime code, test, CI or configuration changes accompany it.
> Related: [agent-ownership.md](agent-ownership.md) (role write scopes) · [feature-workflow.md](feature-workflow.md) (feature runtime) · [task-lifecycle.md](task-lifecycle.md) · [github-integration.md](github-integration.md) · [github-intake.md](github-intake.md) · [worker-security.md](worker-security.md) · [operations.md](operations.md)

## 1. Context and motivation

The Worker publishes only verified `feature/issue-<N>` branches that it created itself. Issue #63 could not be planned because the change it depended on already existed as the branch `integration/improvements` (PR #62), which the Worker had no way to adopt. PR #62 was later merged by hand; the operator-run validation passed 1010 backend, 19 CLI and 53 frontend tests. That manual path had no recorded authorization, no SHA-bound review evidence and no machine-checkable completion.

This document designs a first-class **integration job**: a Worker job type, separate from feature implementation, that brings an *already existing* branch or open PR into `dev` without rebuilding the change set and without duplicating the PR.

## 2. Goals and non-goals

**Goals**

- Adopt an existing commit range (branch or PR head) by immutable SHA.
- Reuse the existing PR when it matches; create one only when none exists for that head.
- Apply the same role, path, security and validation rules as for agent-authored work.
- Bind every approval and every piece of review/gate evidence to the exact head SHA and tree SHA.
- Survive crashes and restarts without duplicating or rewriting anything.

**Non-goals**

- No runtime code is written by Issue #64 (this document is its only output).
- No automatic merge by default (§9).
- No bypass of role ownership, protected paths or approval-required paths.
- No change to the production Worker's checkout, to PR #62, or to `dev` history.
- No force-push, rebase of the source, or history rewriting of any validated ref.

## 3. Inputs

An integration job is created from one intake record. All fields are mandatory unless marked optional; a missing or malformed field fails intake.

| Input | Meaning |
| --- | --- |
| `source_repo` | Repository holding the source ref; must be on the **allowlist of permitted source repositories** (config). Anything else is refused |
| `source_ref` + `source_sha` | Branch name **and** the full 40-hex commit SHA. The SHA is authoritative; the name is a label. The ref is re-resolved and must still equal the SHA |
| `base_ref` + `base_sha` | Intended target (normally `dev`) and the SHA the operator expects it to be at |
| `pr_number` (optional) | Existing PR. If given, its head SHA and base branch must match `source_sha` and `base_ref` |
| `authorization` | Operator authorization record: operator identity, UTC time, the exact `source_sha`/`base_sha`, scope (which stages are approved) and an expiry |
| `provenance` | Commit authors/committers and origin (who pushed, where from). Every author must be on the known-authors list; unknown authors are refused |
| `issue` | The GitHub Issue that justifies the job (links the job to an approved plan) |

## 4. Lifecycle state machine

```text
INTAKE ─▶ AWAITING_APPROVAL ─▶ INSPECTING ─▶ CANDIDATE_BUILT ─▶ VALIDATING ─▶ IN_REVIEW
                                                                                  │
   COMPLETED ◀─ MERGED ◀─ AWAITING_MERGE_APPROVAL ◀──────────────────────────────┘
```

| State | Entered when | Allowed next states |
| --- | --- | --- |
| `INTAKE` | Intake record validated (allowlist, SHAs resolve, authors known) | `AWAITING_APPROVAL`, `REFUSED`, `STALE` |
| `AWAITING_APPROVAL` | Waiting for the operator authorization | `INSPECTING`, `REJECTED`, `EXPIRED`, `STALE` |
| `INSPECTING` | **Read-only** analysis: ancestry vs base, diff and path classification, role mapping, secret/protected-path scan, PR match (§4.1) | `CANDIDATE_BUILT`, `REFUSED`, `NEEDS_REPAIR`, `STALE` |
| `CANDIDATE_BUILT` | Merge of `source_sha` into `base_sha` done in a **separate, disposable worktree**; head SHA and tree SHA recorded | `VALIDATING`, `REFUSED` (conflict), `STALE` |
| `VALIDATING` | Validation gates per affected role (§6) running | `IN_REVIEW`, `NEEDS_REPAIR`, `FAILED`, `STALE` |
| `IN_REVIEW` | Independent AI Review (a different runner/session than any author) of the exact diff | `AWAITING_MERGE_APPROVAL`, `NEEDS_REPAIR`, `FAILED`, `STALE` |
| `AWAITING_MERGE_APPROVAL` | Operator approval of the specific head/tree SHA | `MERGED`, `REJECTED`, `STALE` |
| `MERGED` | GitHub has merged the PR into `dev` using the one allowed mechanism (§5.9). The resulting dev merge commit is a different object from the local candidate merge commit and is recorded separately as `merge_result` (§8.2) | `COMPLETED`, `FAILED` (a completion check in §5.10 fails; operator attention required) |
| `COMPLETED` | All completion checks of §5.10 passed: the dev merge commit contains the source head, its tree SHA equals the approved candidate `tree_sha`, the PR shows merged, post-merge checks green | terminal (none) |
| `NEEDS_REPAIR` | A repair task is open (§6.4) | `INTAKE` (new SHA, a **new job** linked to this one), `ABORTED`, `STALE` |
| `STALE` | The source, base, candidate or PR head SHA differs from the recorded value | terminal (none) |

**STALE rule.** In every pre-merge and awaiting-approval state (`INTAKE`, `AWAITING_APPROVAL`, `INSPECTING`, `CANDIDATE_BUILT`, `VALIDATING`, `IN_REVIEW`, `AWAITING_MERGE_APPROVAL`, `NEEDS_REPAIR`) a change to the source SHA (`source_ref` no longer at `source_sha`), the base (`dev` no longer at `base_sha`), the candidate (`head_sha` or `tree_sha`) or the PR head SHA moves the job to `STALE`. The check runs on every resume and immediately before every state transition. A `STALE` job is never resumed; recovery is a fresh job with new inputs, a new approval and new evidence (§5.4).

**Where `STALE` does not apply:**

- `MERGED`: the merge has already happened and `dev` legitimately moved past `base_sha`, so the pre-merge SHA comparison is meaningless. Problems here are caught by the completion checks (§5.10) and end in `FAILED`.
- `COMPLETED`, `REFUSED`, `REJECTED`, `EXPIRED`, `FAILED`, `ABORTED`, `STALE`: terminal states have no outgoing transitions.

**Terminal states:** `COMPLETED`, `REFUSED`, `REJECTED`, `EXPIRED`, `FAILED`, `ABORTED`, `STALE`. No transition leaves a terminal state, and a terminal job is never reopened. Any non-terminal state before `MERGED` can move to `ABORTED` by operator command.

### 4.1 PR reuse versus creation

- **Lookup.** Before any PR is created or reused, search for PRs in **all** GitHub states (open, closed and merged), keyed by source repo/ref and head SHA (`source_sha`). The search is paginated to completion; an incomplete or failed lookup pauses the job and creates nothing.
- **Reuse** a PR only when it is **open** and unambiguously matches: its head repo is the allowlisted `source_repo`, its head SHA equals `source_sha`, and its base branch equals `base_ref`. Exactly one such PR may exist. The job records the PR number and does not push to it.
- If a PR with the same head exists in any state but is not reusable (closed, merged, wrong base, different repo, or more than one candidate so the match is ambiguous): **do not create a new PR**. Stop and require operator attention. Never open a second PR for the same head.
- A **new PR** may be created only when the all-states lookup finds *no* PR for that head. Creation is idempotent: the job stores the PR number immediately after creation, and on resume it repeats the lookup before it creates.
- The Worker never closes, retargets or edits someone else's PR except to add its own status/comment.

## 5. Git safeguards

1. **No force-push**, anywhere, ever. Any push the job makes (never to `dev`, see rule 9) is fast-forward only; a rejected push fails the job.
2. **Production Worker checkout is untouched.** All merge work happens in a separate worktree under a job-specific directory; the live checkout's `HEAD`, index and files are never read-write in an integration job.
3. **No duplicate PRs** (§4.1).
4. **Stale `dev` rule.** Any movement of `dev` away from the recorded `base_sha`, including movement that would still merge cleanly, makes the current job `STALE`. A `STALE` job cannot be resumed: a fresh job with new inputs, a new approval and new evidence is required. The Worker never re-bases or re-merges inside the existing job.
5. **Refuse**: stale refs (`source_ref` no longer at `source_sha`), a moved base (`dev` no longer at `base_sha`, see rule 4), merge conflicts, missing or failed checks, unknown authors, SHAs that do not resolve, and sources outside the allowlist.
6. **No silent retries that rewrite validated history.** A retry may re-run a gate against the *same* SHAs. Any step that would produce a different head or tree (re-merge, rebase, amend) is a new candidate and a new job; earlier evidence is not carried over.
7. **Evidence binding.** Every gate result, review verdict and approval stores `head_sha` and `tree_sha` plus a hash of the evidence payload. Evidence is valid only while *both* still match the candidate; a change to either invalidates all evidence and approvals, and the job moves to `STALE`.
8. **Crash/restart idempotence.** State is persisted (§8.2) with an atomic write before and after each side effect. On resume the Worker (a) reloads the record, (b) re-resolves `source_sha`, `base_sha`, candidate head and tree and PR head, (c) compares with the record, and (d) continues only if everything matches; any mismatch → `STALE`. Each step is written to be repeatable: worktree creation checks for an existing worktree at the recorded SHA, PR creation looks up first, the merge step checks whether `dev` already contains the head.
9. **Allowed merge mechanism.** The only allowed mechanism is a GitHub **merge commit** of the PR into `dev`, with the expected PR head SHA pinned (the merge request carries `sha=<source_sha>`, so GitHub rejects it if the head moved). Squash, rebase-merge, fast-forward and direct pushes to `dev` are not allowed.
10. **Candidate versus dev merge commit; completion checks.** The locally built candidate merge commit (`candidate.head_sha`, `candidate.tree_sha`, built in the disposable worktree) is distinct from the merge commit GitHub writes to `dev` (`merge_result.commit_sha`, `merge_result.tree_sha`). The two commit SHAs normally differ; only the trees are expected to be identical. Checks:
    - (a) **Before merging**: `dev` head equals the approved `base_sha`, evaluated immediately before the merge call.
    - (b) **Before merging**: the reviewed candidate `head_sha`/`tree_sha` evidence is current (matches the approved values and the recorded evidence).
    - (c) **After merging**: the resulting dev merge commit contains the intended source head (`source_sha` is an ancestor of it).
    - (d) **After merging**: the tree SHA of the resulting dev merge commit is identical to the approved candidate `tree_sha`, or a formally justified equivalent deterministic check is used (the justification is written down and recorded in the approval record).
    - **Ancestry alone does not prove the trees are equal**: check (c) never substitutes for check (d).
    - If any check fails, completion is refused and operator attention is required. A failure of (a) or (b) before the merge moves the job to `STALE`; a failure of (c) or (d) after the merge moves it to `FAILED`. The Worker never repairs `dev` itself.

## 6. Multiple roles and large diffs

### 6.1 Importing is not authoring

In normal features an agent writes edits, and role write scopes ([agent-ownership.md](agent-ownership.md)) constrain what it may write. In an integration job the content already exists; **source content is read-only at intake** and the Worker does not edit it. This changes *who wrote* the bytes, not *what may land in `dev`*.

### 6.2 Every path is still classified

At `INSPECTING` the full changed-path list (including renames, deletions, mode changes and symlinks) is classified with the same `PathPolicy` used for features:

- mapped to the owning role (backend, cli, frontend, automation, coordination);
- checked against protected paths (`.env*`, `content/`, `data/`, `backend/data/`, the bug workflow) and approval-required paths;
- paths owned by **no** role (for example `feature_coordinator/**` or the repository root) are flagged as *unowned*.

**Protected paths cannot be bypassed.** A diff touching a protected path is refused outright. Approval-required and unowned paths need an explicit, per-path operator approval recorded in the authorization record and bound to the SHAs; the approval cannot be inherited from the source PR.

### 6.3 Gates per affected role

Each role with at least one changed path gets its own gates: that role's validation commands (e.g. backend `pytest`/`pylint`, CLI tests, frontend tests, `scripts/check_docs_agents.py` for docs), plus the security checks (secret scan, protected-path scan, dependency/CI-config change flags). Large diffs are handled by sharding gates per role and per path group, with size limits that *refuse* (not truncate) when the diff exceeds the review capacity; the operator may split the source into several jobs instead. The AI Review sees the complete diff per role, never a sample.

### 6.4 Repair routing

If inspection, a gate or the review finds a defect, the integration job does **not** patch the source. It moves to `NEEDS_REPAIR` and opens a repair task:

1. The affected paths determine the authorized role(s); a path with no role goes to the operator (§7).
2. The repair runs as a normal feature task on its **own branch** (`feature/issue-<N>-repair-<k>`), with normal role/path enforcement and commit gate, based on the candidate or source.
3. The result is a new commit SHA. The integration is re-entered at `INTAKE` as a **new job** referencing the old job and the new SHA. No evidence transfers.

## 7. Implementation prerequisite (operator decision)

Current role ownership covers `backend/**`, `cli/**`, `frontend/**`, `scripts/**` (automation), `docs_agents/coordination/**` and similar. It does **not** cover `feature_coordinator/**`, where the Worker runtime lives. Therefore the current Planner/roles **cannot implement this capability** as the system stands.

**Operator decision required** — one of:

- **A.** Explicitly approve a role/policy change that grants a named role write access to `feature_coordinator/**` (with tests, review and approval-required handling for that path), or
- **B.** Implement it in a separate, trusted development workflow (human or operator-run), reviewed independently, outside the Worker's own role policy.

This design does not choose. Until the decision is recorded, the follow-up engineering Issue must not be scheduled for the Worker.

## 8. Concrete proposal

### 8.1 CLI/API commands

Proposed under `python -m feature_coordinator integrate …` with equivalent API calls. Every mutating command takes `--job` and re-verifies SHAs; none takes a "force" option.

| Command | Effect |
| --- | --- |
| `intake --repo --ref --sha --base dev --base-sha [--pr N] --issue N` | Validate inputs, create the job record (`INTAKE`→`AWAITING_APPROVAL`) |
| `approve --job J --stage {inspect,merge} --record FILE` | Record operator approval bound to SHAs and expiry |
| `inspect --job J` | Read-only inspection, path/role classification, PR match |
| `validate --job J` | Build the candidate worktree (if needed) and run per-role gates |
| `review --job J` | Run independent AI Review on the exact diff |
| `merge --job J` | Re-verify (§5.10 a, b), merge via the allowed mechanism (§5.9), then verify (§5.10 c, d) before `COMPLETED` (only after merge approval) |
| `status --job J [--json]` | Show state, SHAs, evidence, blockers |
| `abort --job J --reason` | Move to `ABORTED`; removes the disposable worktree |

### 8.2 State record schema

One JSON file per job, written atomically (write-temp + fsync + rename), append-only history:

```json
{
  "job_id": "int-20261108-0001",
  "issue": 64,
  "state": "VALIDATING",
  "source": {"repo": "owner/repo", "ref": "integration/x", "sha": "<40hex>"},
  "base": {"ref": "dev", "expected_sha": "<40hex>"},
  "candidate": {"worktree": "<job dir>", "head_sha": "<40hex>", "tree_sha": "<40hex>"},
  "pr": {"number": 62, "head_sha": "<40hex>", "reused": true},
  "merge_result": {"commit_sha": "<40hex, dev merge commit written by GitHub>", "tree_sha": "<40hex>", "method": "github-merge-commit"},
  "provenance": {"authors": ["name <email>"], "origin": "..."},
  "paths": [{"path": "backend/x.py", "role": "backend", "class": "owned"}],
  "evidence": [{"kind": "gate:backend", "head_sha": "...", "tree_sha": "...",
                "result": "pass", "sha256": "...", "at": "<UTC>"}],
  "approvals": [{"stage": "merge", "operator": "...", "head_sha": "...",
                 "tree_sha": "...", "at": "<UTC>", "expires": "<UTC>"}],
  "history": [{"from": "INSPECTING", "to": "CANDIDATE_BUILT", "at": "<UTC>"}],
  "created_at": "<UTC>", "updated_at": "<UTC>"
}
```

### 8.3 Acceptance tests (for the later engineering Issue)

- Intake refuses: non-allowlisted repo, unresolvable or abbreviated SHA, unknown author, base SHA mismatch.
- Existing PR with matching head/base is reused; mismatched head, base or repo is refused; no second PR is ever created; resume after a crash between "create PR" and "persist number" finds the PR instead of creating another.
- Source ref moved after approval → `STALE`; `dev` moved from `base_sha` (even if it would still merge cleanly) → `STALE`, and the job cannot be resumed.
- A same-head PR that is closed, merged, wrong-base or ambiguous stops the job for operator attention; no new PR is created. The lookup covers open, closed and merged PRs.
- Merge result whose tree differs from the approved candidate `tree_sha` → completion refused, even though the source head is an ancestor.
- Merge conflict → `REFUSED`; the source and the live checkout are unchanged.
- A protected-path diff is refused; an unowned-path diff is refused without a per-path approval.
- Evidence with a different head or tree SHA is rejected; approvals expire.
- Kill the process at every state boundary; resume yields the same final result with no duplicate side effect.
- No code path emits a force-push; a non-fast-forward push fails the job.
- Review runner is distinct from any authoring session; subscription-only execution (no per-token API key) is asserted.
- Without a merge decision record, `merge` refuses.

### 8.4 Failure modes and responses

| Failure | Response |
| --- | --- |
| Stale ref, or `dev` moved from `base_sha` (clean or not) | `STALE`; report both SHAs; fresh job with new inputs, approval and evidence required |
| Same-head PR exists but is not reusable | No new PR; stop and require operator attention |
| Completion check (§5.10) fails | Completion refused; `STALE` (before merge) or `FAILED` (after); operator attention |
| Merge conflict | `REFUSED`; no auto-resolution; operator may request a repair task |
| Missing or failing check/gate | Block at `VALIDATING`/`IN_REVIEW`; `NEEDS_REPAIR` or `FAILED` |
| Unknown author / non-allowlisted source | `REFUSED` at intake |
| Protected path touched | `REFUSED`; never overridable |
| Process crash | Resume with SHA re-verification (§5.8) |
| Push rejected | `FAILED`; no retry that rewrites history |
| GitHub API unavailable | Pause in current state; retry reads only; no side effect without lookup |

### 8.5 Milestones

1. **M1** State record and state machine, `intake`/`status`/`abort`, no Git writes.
2. **M2** Read-only `inspect`: path/role classification, PR match.
3. **M3** Isolated candidate worktree and per-role gates.
4. **M4** Independent review with SHA-bound evidence.
5. **M5** Operator approvals and `merge` with re-verification.
6. **M6** Crash-recovery hardening and failure-injection tests.

### 8.6 Migration from today's publisher/reviewer

The feature publisher and reviewer stay unchanged. Integration reuses their components (PathPolicy, commit gate, reviewer runner, PR client) behind a new job type; shared code is refactored only behind tests. Integration is opt-in per job, disabled by default, and first used on dry-run (inspect-only) jobs against known merged branches (such as #62's `integration/improvements`) before any real merge. **Subscription-only execution is preserved** (no API-key billing path) and **review stays independent**: the reviewer never shares a session with the author or repair task.

## 9. Operator decision: automatic merge

**Default: no automatic merge into `dev`.** Merge needs an explicit, checkable operator decision record (§8.2 `approvals`, stage `merge`) bound to the exact head and tree SHAs; without it the `merge` command refuses. Merging uses only the mechanism in §5.9 and completes only after the checks in §5.10.

A *future* auto-merge policy would require, at a minimum, a separate operator decision record that is itself versioned and checkable, and:

- an allowlisted source repository and known authors only;
- no protected, approval-required or unowned paths in the diff;
- all gates green and independent AI Review passing, bound to the exact head and tree SHAs;
- `dev` still at `base_sha` (any movement makes the job `STALE`), branch protection and required status checks green;
- a diff-size and affected-role ceiling;
- an audit log entry, a kill switch, and an operator-revocable policy with an expiry;
- a successful history of supervised (manual-approval) integrations first.

## 10. Phase-by-phase implementation plan (later engineering Issue)

| Phase | Work | Depends on |
| --- | --- | --- |
| P0 | Operator decision on §7 (role change vs trusted workflow) and approval of this design | none; **blocks all** |
| P1 | State record, state machine, persistence, `intake`/`status`/`abort` | P0 |
| P2 | Read-only inspection, path/role classification, PR matching and reuse rules | P1 |
| P3 | Isolated candidate worktree, merge, per-role validation gates | P2 |
| P4 | Independent review with SHA/tree-bound evidence | P3 |
| P5 | Operator approvals, `merge` with final re-verification, verified completion | P4 |
| P6 | Crash/restart failure injection, repair-task routing (§6.4) | P3–P5 |
| P7 | Dry-run pilot on historical branches, docs and operations updates | P5, P6 |
| P8 | (Optional, separate decision) auto-merge policy per §9 | P7 and a recorded operator decision |

Non-goals for every phase: force-push, touching the production checkout, bypassing path policy, and automatic merge before P8's separate decision.
