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

## 3. Inputs and authenticated operator decisions

The only allowed target is the coordinator's configured repository and branch `dev`. A source must be the same repository or an allowlisted eligible fork, with an immutable, fully qualified commit SHA. Bind repository identities to **GitHub numeric repository IDs**, not names alone; unknown sources, invalid SHAs and any alternative target are refused.

| Input | Meaning |
| --- | --- |
| `source_repo`, `source_ref`, `source_sha` | Allowlisted repository ID, source branch and full pinned 40-hex SHA; re-resolve the ref |
| `base_ref`, `base_sha` | Exactly `dev` in the configured target repository ID, at the recorded SHA |
| `pr_number` (optional) | Existing PR, independently checked by numeric number/node ID, head/base repo/ref and SHA |
| `provenance` | Author/committer strings and source origin; these strings do **not** authenticate a pusher |
| `issue` | Approved Issue and immutable plan/scope digest |
| `intake_request` | Requester identity, time, scope; **not** permission to create a PR or merge |

### 3.1 Authenticated approvals and trust boundary

Only **authenticated GitHub operator actions** count. Extend the existing W4.5 event/receipt pattern ([github-intake.md](github-intake.md) §12.5–12.6) with explicitly versioned approval types for `inspect`, `scope` and `merge`. This extension is a **prerequisite to implement**, not a capability that W4.5 currently provides. A trusted coordinator verifies the GitHub event's actor and current repository permission, unique event and receipt IDs, the matching GitHub Actions run, receipt author and edit history, Issue/plan digest, expiry, revocation and **single-use nonce**. Reused/forged/modified receipts and wrong or unverifiable identities fail closed.

The authenticated approval binds `job_id`, stage, Issue and plan, immutable source and target repo IDs/refs/SHAs, scope/action, existing candidate/evidence digests, approving user, creation/expiry, revocation and nonce. A **merge** approval additionally binds PR number/node ID, candidate head/tree SHA, gate/review evidence hashes, exact `dev` target, merge method, required post-merge checks and single-use operation ID. The merge approver must be distinct from the requesting/authoring identity; the reviewer cannot approve. A **scope/publish** approval binds the full classified-path report digest, individual exceptional-path decisions and permission to create **one** PR. Inspection approval precedes the candidate and therefore binds **only known** source/base/Issue facts.

A local file, `operator` JSON field or an unkeyed SHA-256 digest **never authenticates** the operator. The `approve --receipt REF` command can only verify a real GitHub event/receipt; no self-attesting file fallback. The authoritative approval ledger, GitHub merge credential and privileged merge executor stay outside every agent/reviewer/repair process write/read scope. Merge permission is not shared with untrusted validation tools and cannot bypass branch protection. If isolation is not **actually verified** on the host (see [worker-security.md](worker-security.md) §0), integration execution/merge stays disabled.

### 3.2 Three approval gates

1. **Before inspection**, `AWAITING_APPROVAL` requires an authenticated `inspect` event.
2. **After complete history/path classification** (§6.2), `AWAITING_SCOPE_APPROVAL` requires an authenticated `scope` event for **each** approval-required or unowned path and the canonical full path report hash, plus permission to create one PR. An empty list of exceptions is explicitly approved; protected paths are never approvable.
3. **After gates, PR preparation and independent review**, `AWAITING_MERGE_APPROVAL` requires a separate authenticated `merge` event tied to exact verified PR, source/base, candidate tree, method and checks. Inspect/scope approval never substitutes for merge approval.

Revalidate the relevant event, expiry and revocation immediately before each permitted side effect. Approval validation must be implemented **before** PR creation, not deferred to the merge milestone.

## 4. Lifecycle state machine

```text
INTAKE → AWAITING_APPROVAL → INSPECTING → AWAITING_SCOPE_APPROVAL
       → BUILDING_CANDIDATE → CANDIDATE_BUILT → VALIDATING
       → PR_PREPARING → IN_REVIEW → AWAITING_MERGE_APPROVAL
       → MERGING → MERGED → COMPLETED
```

| State | Entered when | Allowed next states |
| --- | --- | --- |
| `INTAKE` | Input validation is in progress | `AWAITING_APPROVAL`, `REFUSED` |
| `AWAITING_APPROVAL` | Authenticated inspect authorization awaited | `INSPECTING`, `REJECTED`, `EXPIRED`, `STALE`, `ABORTED` |
| `INSPECTING` | Read-only history/path/security/PR discovery | `AWAITING_SCOPE_APPROVAL`, `REFUSED`, `NEEDS_REPAIR`, `STALE`, `ABORTED` |
| `AWAITING_SCOPE_APPROVAL` | Per-path and one-PR publish permission awaited | `BUILDING_CANDIDATE`, `REJECTED`, `EXPIRED`, `STALE`, `ABORTED` |
| `BUILDING_CANDIDATE` | Isolated deterministic merge commit under construction | `CANDIDATE_BUILT`, `REFUSED` (conflict), `FAILED`, `STALE`, `ABORTED` |
| `CANDIDATE_BUILT` | Candidate head/tree/parents pinned | `VALIDATING`, `FAILED`, `STALE`, `ABORTED` |
| `VALIDATING` | Sandboxed role/security gates | `PR_PREPARING`, `REFUSED`, `NEEDS_REPAIR`, `FAILED`, `STALE`, `ABORTED` |
| `PR_PREPARING` | Scope approval verified; reuse/create one PR | `IN_REVIEW`, `REFUSED`, `REJECTED`, `EXPIRED`, `FAILED`, `STALE`, `ABORTED` |
| `IN_REVIEW` | Independent structured review of landing delta | `AWAITING_MERGE_APPROVAL`, `REFUSED` (oversize), `NEEDS_REPAIR`, `FAILED`, `STALE`, `ABORTED` |
| `AWAITING_MERGE_APPROVAL` | Independent authenticated merge approval awaited | `MERGING`, `EXPIRED`, `REJECTED`, `STALE`, `ABORTED` |
| `MERGING` | Merge intent persisted, outcome may be unknown | `MERGED`, `STALE` (confirmed no merge), `REJECTED`/`EXPIRED` (confirmed no merge), `FAILED` |
| `MERGED` | GitHub confirmed merge, actual result recorded | `COMPLETED`, `FAILED` **only** |
| `COMPLETED` | Actual PR/parents/tree/status checks verified | terminal |
| `NEEDS_REPAIR` | Separate repair is required | `SUPERSEDED`, `ABORTED`, `STALE` |
| `STALE`, `SUPERSEDED`, `REFUSED`, `REJECTED`, `EXPIRED`, `FAILED`, `ABORTED` | Named terminal outcome | terminal |

**Precedence.** After intake, any movement of `dev` from approved `base_sha` **before** the merge, even if the change would still merge cleanly, makes the job `STALE`. First reconcile any recorded PR-creation or GitHub merge attempt, then check recorded source/base/candidate/PR SHAs, then approval. A **normal or recovered completed merge** transitions `MERGING → MERGED` without a pre-merge STALE test; `MERGED` must perform post-merge verification and can **never** become `ABORTED`. Authenticated rejection/cancellation before a merge is in flight takes precedence over forward-progress STALE checks.

**Attention holds** are durable: `attention: {code, reason, since, evidence_refs, last_retry, owner}` keeps the current non-terminal state, forbids side effects and requires trusted `resolve-attention --job --evidence` to clear. GitHub read failures are retried with bounded read-only backoff, then escalated to the operator. If `MERGING` has an **unknown external result**, it cannot be aborted, force-completed, or have its PR reservation released; only verified GitHub evidence resolves it. Once definitively not merged, cancellation/rejection/expiry is permitted; after merge, `COMPLETED` or `FAILED` is mandatory. Other pre-merge states may abort after pending PR-create effects are reconciled. Terminal states never reopen.

### 4.1 PR reuse versus creation

- **Lookup:** fully paginate `GET /repos/{owner}/{repo}/pulls?state=all&per_page=100&page=N`; match by immutable `head.repo.id` and `head.sha`, record head/base repo IDs/refs, PR number/node ID, state and draft flag. A filtered `head=owner:ref` lookup is not a substitute for all-states same-head discovery. A PR for the same branch at another SHA does not block.
- **Reuse:** one open matching PR for pinned source repo/ref/SHA and target repo/`dev`; otherwise hold with `non_reusable_pr` and never create a same-head duplicate. A previously merged PR is not proof the current job completed.
- **Create:** only in `PR_PREPARING` after verified **scope/publish** receipt and gates, persisted `pr_create_attempt` and a complete all-states lookup. Re-query after a crash/timeout; GitHub 422 "already exists" triggers lookup, not blind retry. Sources must be in the target repository or an eligible fork; no source branch is pushed by the integration job.
- **Recheck:** before review/merge, fetch expected PR number/node ID, source/target repo IDs/ref and SHA, target exactly `dev`, **open and non-Draft**. Moved head/base → `STALE` before merge; closed/Draft PR → operator hold or authenticated rejection. Do not edit another author's source PR.

## 5. Git safeguards

1. **No force-push, source rewrite or direct push to `dev`.** Only the trusted merge executor may merge via GitHub, after an authenticated operator decision. Integration can reuse/create a PR referencing an eligible existing source ref, never push one.
2. **Immutable candidate isolated from production.** Build a deterministic explicit **two-parent** merge in a separate job-specific Git clone (not `git worktree add` on the live checkout), with fixed Git identity/timestamps. Record parents `[base_sha,source_sha]`, candidate head/tree SHAs, pin `refs/integration/<job>/candidate`. On resume read immutable Git objects, not mutable worktree HEAD. Missing/changed candidate object fails closed; never silently rebuild and inherit earlier approvals.
3. **Only test approved bytes.** Run gates in fresh clean candidate checkouts; check HEAD, tree, index and worktree before and after each gate. Untrusted source code executes **only** under verified offline isolation without provider/GitHub/merge credentials, bounded filesystem writes and timeouts. If verified sandboxing is unavailable on the host, disable integration gates/merge rather than falling back to native unsandboxed execution. The reviewer has no edit tools and its structured output is validated.
4. **Scan the entire source history.** At `INSPECTING` traverse every introduced commit (`git rev-list base_sha..source_sha`, including internal merge commits); compare each commit against each parent, inspect added/changed blob content and protected paths, modes, renames, symlinks/targets, submodules, `.gitmodules` and `.gitattributes`. An intermediate commit that adds then deletes a secret/protected file still **refuses integration** although the final diff is clean. Separately classify the actual incoming tree delta between approved `base_sha` and candidate `tree_sha`, not merely the merge-base-to-source diff; reconcile it with approved `path_report_sha256` (§6.2). Unknown/oversize scans fail closed.
5. **Strict stale-base.** Any movement of target `dev` before this job's approved merge, including cleanly mergeable movement, means `STALE`; an already performed, intent-bound merge recovered after a crash is **not** pre-merge drift. A stale job cannot be resumed/rebased: a new job/approval/evidence are required.
6. **Deterministic failure semantics.** Invalid input at `INTAKE` → `REFUSED`. After intake pinned drift → `STALE`. Conflict in `BUILDING_CANDIDATE`, prohibited history content at `INSPECTING` or `VALIDATING`, or oversized review at `IN_REVIEW` → `REFUSED`. Failing gates/review → `NEEDS_REPAIR` or `FAILED`. A non-reusable same-head PR remains held for operator action, not falsely completed.
7. **Evidence is authenticated and SHA-bound.** Preserve complete immutable gate/review payloads, hashes, candidate SHA/tree and source/base SHAs. Only verified GitHub events/receipts (§3.1) authenticate operator actions; never a free-form JSON field or unkeyed digest. Inspection approval binds pre-candidate facts only; scope approval binds path report and one PR creation; merge approval binds exact PR identity, candidate tree, method, check policy, single-use nonce, expiry and revocation. Revalidate before every write.
8. **Write-ahead effects and crash recovery.** Keep state, `state_revision` and side-effect intents on a protected **local filesystem**; write temp, fsync file, atomic rename, then **fsync parent directory**, plus a durable append-audited event log. Record `pr_create_attempt` before PR creation and reconcile by all-state lookup after crash. Before GitHub merge, atomically persist `MERGING` and `merge_attempt` (PR/source/base/candidate, approved receipt, target, method and operation ID). Normal success records **actual** GitHub merge result and enters `MERGED`, without a post-write pre-merge STALE check:
   - On restart or timeout in `MERGING`, **first** fetch GitHub PR/merge result. If pinned PR is confirmed merged into `dev` at the expected head, record `merge_result` commit/parents/tree and run §5.10, **not** a blind retry or ancestry-based completion. If another actor merged the exact PR, flag actor mismatch for the operator and still verify the actual result.
   - If GitHub **definitively confirms not merged**, recheck source/base/PR/approval and preview: drift → `STALE`; expired/revoked approval → `EXPIRED`/`REJECTED`; unchanged and approved → bounded retry with same persisted intent after another remote check.
   - If outcome is unknown, API unavailable or contradictory, remain held in `MERGING` with the intent and PR reservation intact. Never guess, force-abort or retry the merge blindly.
9. **GitHub pre-merge test commit is mandatory.** The only mechanism is GitHub's PR **merge commit** pinned with `sha=source_sha` into configured `dev`; no squash/rebase/direct push/bypass. Under the global target lock (§5.11), re-read PR and require exact repo IDs/ref, number/node ID, head SHA, open/non-Draft, base `dev` at `base_sha`, valid independent approval/expiry/revocation, gates and review. Request `GET /pulls/{number}` to refresh GitHub's temporary test merge, poll `mergeable=true`, fetch the **pre-merge** `merge_commit_sha` Git object, and compare its parents **exactly** `[base_sha,source_sha]` and its **tree SHA** exactly `candidate.tree_sha`. A missing/stale/mismatched preview or unknown mergeability **blocks the merge call**. GitHub warns that test merge refs may become stale without an explicit refresh ([GitHub Docs](https://docs.github.com/en/rest/guides/using-the-rest-api-to-interact-with-your-git-database)). Recheck identity/base/approval immediately before writing.
10. **Post-merge verification and residual target race.** On confirmed success, independently refetch GitHub PR. Require its **post-merge** `merge_commit_sha` equals recorded `merge_result.commit_sha`, that commit is reachable from intended `dev`, its parents are exactly `[base_sha,source_sha]` and its tree is **identical** to approved `candidate.tree_sha` (no free-form equivalence exception). All required checks in the authenticated merge approval must pass on this exact commit within a policy deadline (even an empty check set must be explicitly approved). A post-merge mismatch/failure/timeout → `FAILED`, `merged_unverified` incident, **disable subsequent integration merges** until authenticated operator resolution; never silently repair `dev`.
   - **Explicit decision:** GitHub's merge API pins PR head SHA, **not target base SHA or a PR's target branch**. External actors can move `dev` or retarget a PR between preflight and write, even while our global lock is held. Post-write parent/tree checks detect but cannot prevent this. This architecture supports **supervised operator-triggered merges only** under that risk; unattended auto-merge is **not authorized** unless separately approved guarded target-atomic mechanism/branch restrictions actually eliminate it. Never claim this API gives an absolute no-wrong-target guarantee.
11. **Serialize all coordinator writes to `dev`.** Hold a **global lock keyed by target repo ID/`dev`**, plus per-job locks, unique active source/PR reservations and `state_revision` compare-and-swap. Keep the target lock through remote preflight, write-ahead intent, GitHub call, reconciliation and persistence of merge result. On crash, the new owner reconciles outstanding `MERGING` before any later merge; the second job sees changed base and becomes `STALE`. Multiple hosts require one authoritative shared fenced lock/lease/reservation store; **disable multi-host merges** without it. Locks cannot control actors outside the coordinator.
12. **Operator safety and durable evidence.** `MERGED` cannot be aborted; unresolved `MERGING` cannot be aborted/force-completed. Operator holds persist reasons, evidence, owner and retries; only authenticated `resolve-attention` with verified facts clears them. Leases have owner, heartbeat, expiry and recovery rules; a stale owner is reclaimed only after reconciling pending operations. Preserve immutable candidate objects and full evidence for any `merged_unverified` incident, despite normal temporary clone cleanup on terminal states.

## 6. Multiple roles and large diffs

### 6.1 Importing is not authoring

In normal features an agent writes edits, and role write scopes ([agent-ownership.md](agent-ownership.md)) constrain what it may write. In an integration job the content already exists; **source content is read-only at intake** and the Worker does not edit it. This changes *who wrote* the bytes, not *what may land in `dev`*.

### 6.2 Full history/path classification and a separate scope approval

`INSPECTING` produces a canonical, protected report of **every introduced source commit** and changed blob/path against every parent (including intermediate secrets/protected paths), plus the actual landing-tree diff between approved `base_sha` and candidate tree. Include all renames, deletes, file modes, symlinks and targets, submodules/`.gitmodules`, `.gitattributes`, dependency/CI changes and authorized role ownership. Protected content/paths touched **in any intermediate commit** are refused, even if later removed.

Classify each path using `PathPolicy` as protected (never approvable), owned, approval-required or unowned, and persist the report with canonical `path_report_sha256`. The candidate build must reconcile the final tree and this report, or the old scope approval is invalid and a **new job** is required. At `AWAITING_SCOPE_APPROVAL` the operator sees the complete path/report digest and authenticates a **per-path decision** for every exceptional path, plus permission to create one PR. Even an empty exception list is explicit. Before this gate **no source-code execution, PR creation or review** is permitted.

### 6.3 Sandboxed gates and independent review

Each affected role gets its configured backend/CLI/frontend/docs checks plus whole-history security scans. Gates execute on the pinned candidate tree under **verified offline sandboxing**, without network/credentials; a host isolation failure must stop validation. Verify the tree before and after each gate, and refuse changed tested bytes. The independent reviewer receives the **complete actual landing delta**, never a truncated sample, and returns schema-validated findings; a diff exceeding capacity is `REFUSED`, not silently shortened.

### 6.4 Repair routing

If inspection, a gate or the review finds a defect, the integration job does **not** patch the source. It moves to `NEEDS_REPAIR` and opens a repair task:

1. The affected paths determine the authorized role(s); a path with no role goes to the operator (§7).
2. The repair runs as a normal feature task on its **own branch** (`feature/issue-<N>-repair-<k>`), with normal role/path enforcement and commit gate, based on the candidate or source.
3. On approval of a new source commit SHA, create a **separate** integration job at `INTAKE`, linked to the old job. Move the old job `NEEDS_REPAIR → SUPERSEDED` and retain its history. No SHA-bound gate/review/approval evidence transfers; neither job is silently reused.

## 7. Implementation prerequisite (operator decision)

Current role ownership covers `backend/**`, `cli/**`, `frontend/**`, `scripts/**` (automation), `docs_agents/coordination/**` and similar. It does **not** cover `feature_coordinator/**`, where the Worker runtime lives. Therefore the current Planner/roles **cannot implement this capability** as the system stands.

**Operator decision required** — one of:

- **A.** Explicitly approve a role/policy change that grants a named role write access to `feature_coordinator/**` (with tests, review and approval-required handling for that path), or
- **B.** Implement it in a separate, trusted development workflow (human or operator-run), reviewed independently, outside the Worker's own role policy.

This design does not choose. Until the decision is recorded, the follow-up engineering Issue must not be scheduled for the Worker.

## 8. Concrete proposal

### 8.1 CLI/API commands

Proposed under `python -m feature_coordinator integrate …`. Mutating commands acquire scope/target locks and check `state_revision`; none takes a force flag. Approval commands **verify authenticated GitHub event/receipt evidence**, never create authorization from an arbitrary local file.

| Command | Effect |
| --- | --- |
| `intake --repo --ref --sha --base dev --base-sha [--pr N] --issue N` | Validate and reserve immutable source and target |
| `approve --job J --stage {inspect,scope,merge} --receipt REF` | Verify GitHub event, actor/permission, unique receipt/run, nonce, expiry and SHA-bound scope |
| `inspect --job J` | Read-only full commit-history/path/PR inspection; compute `path_report_sha256` |
| `build --job J` | After scope approval, build/pin candidate; refuse conflicts |
| `validate --job J` | Run verified sandboxed gates on pinned candidate |
| `prepare-pr --job J` | Recheck scope/publish approval and idempotently reuse/create one PR |
| `review --job J` | Independently review complete actual landing-tree delta |
| `merge --job J` | Trusted executor only: new authenticated merge receipt, global `dev` lock, GitHub test-merge preflight, write-ahead intent and actual-result verification |
| `resolve-attention --job J --evidence REF` | Authenticated operator evidence, no fabricated GitHub result |
| `reject --job J --receipt REF` | Authenticated rejection before merge effect |
| `abort --job J --reason` | Forbidden in `MERGED` and unresolved `MERGING`; reconcile any PR-create intent first |
| `status --job J [--json]` | Read-only SHA/receipt/revision, holds, locks and incidents |

### 8.2 State record schema and durable evidence

The trusted coordinator owns a protected schema-versioned JSON job record, immutable evidence payloads and an append-audited log on a **local filesystem**, using per-job lock and `state_revision` CAS. Every side-effect intent and state transition needs temp-file fsync, atomic rename **and parent-directory fsync**. Agents/reviewers/repair tasks cannot write the store. Multi-host merging requires a fenced shared lock (§5.11).

```json
{
  "schema_version": 1,
  "job_id": "int-20261108-0001",
  "issue": 64,
  "state": "VALIDATING",
  "state_revision": 8,
  "attention": null,
  "source": {"repo_id": 123, "repo": "owner/source", "ref": "integration/x", "sha": "<40hex>"},
  "base": {"repo_id": 456, "repo": "owner/target", "ref": "dev", "expected_sha": "<40hex>"},
  "candidate": {"clone": "<isolated job clone>", "ref": "refs/integration/<job>/candidate",
                "head_sha": "<40hex>", "tree_sha": "<40hex>", "parents": ["<base_sha>", "<source_sha>"]},
  "pr": null,
  "pr_create_attempt": null,
  "merge_attempt": null,
  "merge_result": null,
  "path_report": {"sha256": "<64hex>", "evidence_ref": "<protected path>"},
  "approval_receipts": [
    {"stage": "inspect", "event_id": 1001, "run_id": 3001, "receipt_id": 4001,
     "nonce": "<unique>", "approval_sha256": "<64hex>", "expires": "<UTC>", "revoked": false},
    {"stage": "scope", "event_id": 1002, "run_id": 3002, "receipt_id": 4002,
     "nonce": "<unique>", "approval_sha256": "<64hex>", "expires": "<UTC>", "revoked": false}
  ],
  "evidence": [{"kind": "gate:backend", "head_sha": "<40hex>", "tree_sha": "<40hex>",
                "result": "pass", "sha256": "<64hex>", "payload_ref": "<protected path>"}],
  "required_post_merge_checks": null,
  "reservations": {"source": "<repo-id>:<sha>", "pr": null, "target": null},
  "supersedes": null,
  "superseded_by": null,
  "history_ref": "<protected append-only journal>",
  "created_at": "<UTC>",
  "updated_at": "<UTC>"
}
```

The example shows `VALIDATING`, before PR/merge effects. **Inspect** approval cannot bind a candidate that does not exist yet; **scope** binds the classified report hash, per-path decisions and permission to create one PR. Only later, a separate authenticated **merge** receipt binds exact PR number/node ID, repo IDs/refs, source/base/candidate head/tree, gate/review evidence payload hashes, method, check policy, expiry, revocation and one-use operation ID. `required_post_merge_checks` is a verified read-only **copy** of that receipt, not a second authority.

Save `pr_create_attempt` before PR creation, reconcile GitHub all-state lookup after uncertain results. Save state `MERGING` **together with** `merge_attempt` (PR/source/base/candidate, preview parent/tree evidence, authenticated receipt and operation ID) before GitHub merge. On confirmed remote merge, save `merge_result` containing actual commit SHA/tree/parents/PR and observed actor, **then re-fetch GitHub and verify independently** before `COMPLETED`. A temporary test-merge SHA never substitutes for an actual result. Hold indeterminate outcomes and preserve candidate/evidence objects for unresolved `merged_unverified` incidents.

### 8.3 Acceptance tests (for the later engineering Issue)

- Forge/edit/replay a GitHub approval receipt, use wrong actor/permissions, expired/revoked token, duplicate run/nonce, agent approval or JSON `operator` field: **none** may authorize PR creation or merging; merge credential invisible to agent/reviewer.
- Order: authenticated inspect → **full introduced-commit history/path classification** → scope/per-path and single-PR approval → candidate/gates → PR creation → independent review → separate authenticated merge approval. Missing or expired scope prevents PR write.
- An intermediate source commit adds then removes a protected file or secret; reject despite clean net diff. Scan each introduced commit and parents, symlink targets, submodules, attributes, modes and exact candidate landing paths; changed path digest invalidates approval.
- PR lookup covers all states and source repo ID/head SHA; another SHA on same branch does not block. Closed/merged/wrong-base same-head PR holds. GitHub 422, ambiguous timeout and crash do not create duplicate PRs.
- Preflight GitHub's **fresh test merge commit**, requiring exact parents `[base_sha,source_sha]`, tree identical to candidate, PR open/non-Draft, expected repo IDs/refs, `dev` tip, valid gate/review and independent merge receipt. Failure means **no merge call**.
- Normal merge and crash recovery: `AWAITING_MERGE_APPROVAL → MERGING → MERGED → COMPLETED`; reconcile GitHub first after timeout/crash. Never mark an already completed merge `STALE`, never accept ancestry alone.
- Two different approved jobs with the same `dev` base cannot concurrently merge: **global target lock** serializes, second rechecks changed base → `STALE`. Multi-host merging without fenced lock refused.
- An external change of `dev` or PR retarget after preflight can still cause a bad write; verify actual PR/parents/tree, mark `FAILED` + `merged_unverified`, stop integration merges and alert operator. **No unattended auto-merge** by this API.
- `MERGED → ABORTED` forbidden. Unknown `MERGING` result cannot be aborted or force-accepted; a definitively unmerged result may expire/reject/stale. Operator resolution requires verified evidence.
- Build conflict in `BUILDING_CANDIDATE`, prohibited history content in `INSPECTING`/`VALIDATING`, and oversized review in `IN_REVIEW` all map to `REFUSED`. Missing gates map to repair/failure, not success.
- Crash around event log fsync/rename, PR creation, GitHub merge, lock lease expiry and operator intervention without lost approvals/duplicate side effects; preserve incident evidence.
- A repair starts a new source-based job and closes the old as `SUPERSEDED`, without inheriting approvals. Historical merged-PR dry run is read-only and never creates PRs.

### 8.4 Failure modes and responses

| Failure | Response |
| --- | --- |
| Invalid intake source/target/sha | `REFUSED` |
| Source/base/candidate/PR drift after intake (even cleanly mergeable) | `STALE`, new job/evidence |
| Unauthenticated, forged, expired or revoked approval | `REJECTED`/`EXPIRED`, no GitHub write |
| Unapproved exceptional path or missing scope approval | Hold in `AWAITING_SCOPE_APPROVAL` |
| Secret/protected path in source history | `REFUSED` even if deleted before final tree |
| Merge conflict at candidate build | `REFUSED` from `BUILDING_CANDIDATE` |
| Failed/incomplete PR lookup, closed/Draft or non-reusable PR | Hold, do not duplicate or merge |
| Missing/unmergeable/wrong-parent/tree temporary test merge | No merge call; hold or `REFUSED` |
| Confirmed no merge, but approval expired/revoked | `EXPIRED`/`REJECTED`, no retry |
| Uncertain GitHub merge timeout/crash | Hold in `MERGING`, remote reconciliation first |
| Confirmed result violates parents/tree/PR/required status | `FAILED` + `merged_unverified`, halt further integration merges |
| Lock/CAS conflict, API outage | Hold and retry reads only, then authenticated operator resolution |
| Abort in `MERGED` or unresolved `MERGING` | Refuse; preserve external result |

### 8.5 Milestones

1. **M1:** Protected journal and evidence, authenticated GitHub inspect/scope approval receipt verifier, per-job/global locks and `intake`/`status`/`abort`, no PR writes.
2. **M2:** Full source history and landing-tree path classification, per-path approvals, all-states PR lookup.
3. **M3:** Isolated candidate, verified offline gates and approved/idempotent PR preparation.
4. **M4:** Independent structured review with SHA/tree binding.
5. **M5:** Separate authenticated merge receipt, restricted executor, global `dev` lock, GitHub preview merge and actual-result verification.
6. **M6:** Crash/race/forged-approval/security tests and operator incident handling.

### 8.6 Migration from today's publisher/reviewer

The feature publisher and reviewer stay unchanged. Integration reuses their components (PathPolicy, commit gate, reviewer runner, PR client) behind a new job type; shared code is refactored only behind tests. Integration is disabled by default. The first pilot is **inspect-only**: historical, already-merged PR #62 may be inspected for audit evidence but records `already_integrated`, never creates a new PR, and never merges. **Subscription-only execution is preserved** (no API-key billing path) and **review stays independent**: the reviewer never shares a session with the author or repair task.

## 9. Operator decision: automatic merge

**Decision: no unattended automatic merge into `dev`.** GitHub's merge API pins the PR head but **cannot atomically pin the target branch/ref and SHA**. A global coordinator lock controls our jobs, not external GitHub actors; even verified preflight cannot prevent a target change between check and write. This design authorizes only **supervised operator-triggered merging**, with independent authenticated one-use GitHub approval (§3.1), exact PR/SHA/tree binding, global `dev` lock and mandatory result verification. Crash recovery of an already approved in-flight merge is not new merge authorization.

Future unattended auto-merge needs a separate operator decision, proven guarded target-atomic merge mechanism or equivalent target/retarget isolation, least privilege, fenced locks, passing security gates and independent review, kill switch and documented recovery of external incidents.

## 10. Phase-by-phase implementation plan (later engineering Issue)

| Phase | Work | Depends on |
| --- | --- | --- |
| P0 | Operator chooses implementation owner (§7), GitHub approval authority, safe credential/isolation and global lock deployment; accepts residual external GitHub race only for supervised merges | none; **blocks all** |
| P1 | Protected state/evidence store, authenticated inspect/scope verifier, locks, safe `intake`/`status` | P0 |
| P2 | Full introduced-commit history/path scan, per-path scope gate and all-state PR matching | P1 |
| P3 | Isolated candidate, verified sandboxed gates and scope-approved PR creation | P2 |
| P4 | Independent structured review bound to immutable head/tree | P3 |
| P5 | Authenticated merge receipt, trusted executor, global target lock, test-merge preflight and strict post-write verification | P4 |
| P6 | Fault/race/forgery/security regressions, operator attention and incident workflows | P3–P5 |
| P7 | Historical **inspect-only** audit pilot, then supervised live pilot and operations docs | P5, P6 |
| P8 | **Not approved here:** future unattended auto-merge needs a separate guarded-mechanism design and operator decision | P7 plus new authorization |

Non-goals: force-push, production checkout writes, missing authorization, path-policy bypass and unattended merge under the current GitHub API.
