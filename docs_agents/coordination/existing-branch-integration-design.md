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
| `INTAKE` | Intake record validated (allowlist, SHAs resolve, authors known) | `AWAITING_APPROVAL`, `REFUSED` |
| `AWAITING_APPROVAL` | Waiting for the operator authorization | `INSPECTING`, `REJECTED`, `EXPIRED` |
| `INSPECTING` | **Read-only** analysis: ancestry vs base, diff and path classification, role mapping, secret/protected-path scan, PR match | `CANDIDATE_BUILT`, `REFUSED`, `NEEDS_REPAIR` |
| `CANDIDATE_BUILT` | Merge of `source_sha` into `base_sha` done in a **separate, disposable worktree**; head SHA and tree SHA recorded | `VALIDATING`, `REFUSED` (conflict), `STALE` |
| `VALIDATING` | Validation gates per affected role (§6) running | `IN_REVIEW`, `NEEDS_REPAIR`, `FAILED` |
| `IN_REVIEW` | Independent AI Review (a different runner/session than any author) of the exact diff | `AWAITING_MERGE_APPROVAL`, `NEEDS_REPAIR`, `FAILED` |
| `AWAITING_MERGE_APPROVAL` | Operator approval of the specific head/tree SHA | `MERGED`, `REJECTED`, `STALE` |
| `MERGED` | `dev` fast-forwarded or merged via the PR | `COMPLETED`, `FAILED` |
| `COMPLETED` | Verified completion: `dev` contains the head SHA, PR shows merged, post-merge checks green | terminal |
| `NEEDS_REPAIR` | A repair task is open (§6.4) | `INTAKE` (new SHA, a **new job** linked to this one), `ABORTED` |

**Terminal states:** `COMPLETED`, `REFUSED`, `REJECTED`, `EXPIRED`, `FAILED`, `ABORTED`. `STALE` is terminal for the job instance; recovery is a new job with fresh SHAs. Any state can move to `ABORTED` by operator command (before `MERGED`). A terminal job is never reopened.

### 4.1 PR reuse versus creation

- **Reuse** an existing PR only when its head SHA equals `source_sha`, its head repo is the allowlisted `source_repo`, its base branch equals `base_ref`, and it is open. The job records the PR number and does not push to it.
- If a PR exists for the head but any of those differ (head moved, wrong base, closed, different repo): **refuse** (`STALE`/`REFUSED`); never open a second PR for the same head.
- A **new PR** may be created only when a lookup by head repo and branch/SHA finds *no* PR in any state that is open for that head. Creation is idempotent: the job stores the PR number immediately after creation, and on resume it looks up before it creates.
- The Worker never closes, retargets or edits someone else's PR except to add its own status/comment.

## 5. Git safeguards

1. **No force-push**, anywhere, ever. Pushes are fast-forward only; a rejected push fails the job.
2. **Production Worker checkout is untouched.** All merge work happens in a separate worktree under a job-specific directory; the live checkout's `HEAD`, index and files are never read-write in an integration job.
3. **No duplicate PRs** (§4.1).
4. **Refuse**: stale refs (`source_ref` no longer at `source_sha`), diverged base (`dev` no longer at `base_sha` and the change cannot be shown to still be a clean merge — the job fails and a new job is needed rather than silently re-basing), merge conflicts, missing or failed checks, unknown authors, SHAs that do not resolve, and sources outside the allowlist.
5. **No silent retries that rewrite validated history.** A retry may re-run a gate against the *same* SHAs. Any step that would produce a different head or tree (re-merge, rebase, amend) is a new candidate and a new job; earlier evidence is not carried over.
6. **Evidence binding.** Every gate result, review verdict and approval stores `head_sha` and `tree_sha` plus a hash of the evidence payload. Evidence is valid only while *both* still match the candidate; a change to either invalidates all evidence and approvals, and the job moves to `STALE`.
7. **Crash/restart idempotence.** State is persisted (§8.2) with an atomic write before and after each side effect. On resume the Worker (a) reloads the record, (b) re-resolves `source_sha`, `base_sha`, candidate head and tree and PR head, (c) compares with the record, and (d) continues only if everything matches; any mismatch → `STALE`. Each step is written to be repeatable: worktree creation checks for an existing worktree at the recorded SHA, PR creation looks up first, the merge step checks whether `dev` already contains the head.
8. **Final merge is re-verified**: immediately before merging, `dev` must still equal `base_sha` and the candidate head/tree must equal the approved values.

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
| `merge --job J` | Re-verify, then merge into `dev` (only after merge approval) |
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
- Source ref moved after approval → `STALE`; `dev` moved → `STALE`.
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
| Stale/diverged ref | `STALE`; report both SHAs; new job required |
| Merge conflict | `REFUSED`; no auto-resolution; operator may request a repair task |
| Missing or failing check/gate | Block at `VALIDATING`/`IN_REVIEW`; `NEEDS_REPAIR` or `FAILED` |
| Unknown author / non-allowlisted source | `REFUSED` at intake |
| Protected path touched | `REFUSED`; never overridable |
| Process crash | Resume with SHA re-verification (§5.7) |
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

**Default: no automatic merge into `dev`.** Merge needs an explicit, checkable operator decision record (§8.2 `approvals`, stage `merge`) bound to the exact head and tree SHAs; without it the `merge` command refuses.

A *future* auto-merge policy would require, at a minimum, a separate operator decision record that is itself versioned and checkable, and:

- an allowlisted source repository and known authors only;
- no protected, approval-required or unowned paths in the diff;
- all gates green and independent AI Review passing, bound to the exact head and tree SHAs;
- `dev` still at `base_sha`, branch protection and required status checks green;
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
