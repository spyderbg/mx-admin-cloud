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

The only allowed target is the coordinator's configured repository and branch \`dev\`. A source must be the same repository or an allowlisted eligible fork, with an immutable, fully qualified commit SHA. Bind repository identities to **GitHub numeric repository IDs**, not names alone; unknown sources, invalid SHAs and any alternative target are refused.

| Input | Meaning |
| --- | --- |
| \`source_repo\`, \`source_ref\`, \`source_sha\` | Allowlisted repository ID, source branch and full pinned 40-hex SHA; re-resolve the ref |
| \`base_ref\`, \`base_sha\` | Exactly \`dev\` in the configured target repository ID, at the recorded SHA |
| \`pr_number\` (optional) | Existing PR, independently checked by numeric number/node ID, head/base repo/ref and SHA |
| \`provenance\` | Author/committer strings and source origin; these strings do **not** authenticate a pusher |
| \`issue\` | Approved Issue and immutable plan/scope digest |
| \`intake_request\` | Requester identity, time, scope; **not** permission to create a PR or merge |

### 3.1 Authenticated approvals and trust boundary

Only **authenticated GitHub operator actions** count. Extend the existing W4.5 event/receipt pattern ([github-intake.md](github-intake.md) §12.5–12.6) with explicitly versioned approval types for \`inspect\`, \`scope\` and \`merge\`. This extension is a **prerequisite to implement**, not a capability that W4.5 currently provides. A trusted coordinator verifies the GitHub event's actor and current repository permission, unique event and receipt IDs, the matching GitHub Actions run, receipt author and edit history, Issue/plan digest, expiry, revocation and **single-use nonce**. Reused/forged/modified receipts and wrong or unverifiable identities fail closed.

The authenticated approval binds \`job_id\`, stage, Issue and plan, immutable source and target repo IDs/refs/SHAs, scope/action, existing candidate/evidence digests, approving user, creation/expiry, revocation and nonce. A **merge** approval additionally binds PR number/node ID, candidate head/tree SHA, gate/review evidence hashes, exact \`dev\` target, merge method, required post-merge checks and single-use operation ID. The merge approver must be distinct from the requesting/authoring identity; the reviewer cannot approve. A **scope/publish** approval binds the full classified-path report digest, individual exceptional-path decisions and permission to create **one** PR. Inspection approval precedes the candidate and therefore binds **only known** source/base/Issue facts.

A local file, \`operator\` JSON field or an unkeyed SHA-256 digest **never authenticates** the operator. The \`approve --receipt REF\` command can only verify a real GitHub event/receipt; no self-attesting file fallback. The authoritative approval ledger, GitHub merge credential and privileged merge executor stay outside every agent/reviewer/repair process write/read scope. Merge permission is not shared with untrusted validation tools and cannot bypass branch protection. If isolation is not **actually verified** on the host (see [worker-security.md](worker-security.md) §0), integration execution/merge stays disabled.

### 3.2 Three approval gates

1. **Before inspection**, \`AWAITING_APPROVAL\` requires an authenticated \`inspect\` event.
2. **After complete history/path classification** (§6.2), \`AWAITING_SCOPE_APPROVAL\` requires an authenticated \`scope\` event for **each** approval-required or unowned path and the canonical full path report hash, plus permission to create one PR. An empty list of exceptions is explicitly approved; protected paths are never approvable.
3. **After gates, PR preparation and independent review**, \`AWAITING_MERGE_APPROVAL\` requires a separate authenticated \`merge\` event tied to exact verified PR, source/base, candidate tree, method and checks. Inspect/scope approval never substitutes for merge approval.

Revalidate the relevant event, expiry and revocation immediately before each permitted side effect. Approval validation must be implemented **before** PR creation, not deferred to the merge milestone.

## 4. Lifecycle state machine

\`\`\`text
INTAKE → AWAITING_APPROVAL → INSPECTING → AWAITING_SCOPE_APPROVAL
       → BUILDING_CANDIDATE → CANDIDATE_BUILT → VALIDATING
       → PR_PREPARING → IN_REVIEW → AWAITING_MERGE_APPROVAL
       → MERGING → MERGED → COMPLETED
\`\`\`

| State | Entered when | Allowed next states |
| --- | --- | --- |
| \`INTAKE\` | Input validation is in progress | \`AWAITING_APPROVAL\`, \`REFUSED\` |
| \`AWAITING_APPROVAL\` | Authenticated inspect authorization awaited | \`INSPECTING\`, \`REJECTED\`, \`EXPIRED\`, \`STALE\`, \`ABORTED\` |
| \`INSPECTING\` | Read-only history/path/security/PR discovery | \`AWAITING_SCOPE_APPROVAL\`, \`REFUSED\`, \`NEEDS_REPAIR\`, \`STALE\`, \`ABORTED\` |
| \`AWAITING_SCOPE_APPROVAL\` | Per-path and one-PR publish permission awaited | \`BUILDING_CANDIDATE\`, \`REJECTED\`, \`EXPIRED\`, \`STALE\`, \`ABORTED\` |
| \`BUILDING_CANDIDATE\` | Isolated deterministic merge commit under construction | \`CANDIDATE_BUILT\`, \`REFUSED\` (conflict), \`FAILED\`, \`STALE\`, \`ABORTED\` |
| \`CANDIDATE_BUILT\` | Candidate head/tree/parents pinned | \`VALIDATING\`, \`FAILED\`, \`STALE\`, \`ABORTED\` |
| \`VALIDATING\` | Sandboxed role/security gates | \`PR_PREPARING\`, \`REFUSED\`, \`NEEDS_REPAIR\`, \`FAILED\`, \`STALE\`, \`ABORTED\` |
| \`PR_PREPARING\` | Scope approval verified; reuse/create one PR | \`IN_REVIEW\`, \`REFUSED\`, \`REJECTED\`, \`EXPIRED\`, \`FAILED\`, \`STALE\`, \`ABORTED\` |
| \`IN_REVIEW\` | Independent structured review of landing delta | \`AWAITING_MERGE_APPROVAL\`, \`REFUSED\` (oversize), \`NEEDS_REPAIR\`, \`FAILED\`, \`STALE\`, \`ABORTED\` |
| \`AWAITING_MERGE_APPROVAL\` | Independent authenticated merge approval awaited | \`MERGING\`, \`EXPIRED\`, \`REJECTED\`, \`STALE\`, \`ABORTED\` |
| \`MERGING\` | Merge intent persisted, outcome may be unknown | \`MERGED\`, \`STALE\` (confirmed no merge), \`REJECTED\`/\`EXPIRED\` (confirmed no merge), \`FAILED\` |
| \`MERGED\` | GitHub confirmed merge, actual result recorded | \`COMPLETED\`, \`FAILED\` **only** |
| \`COMPLETED\` | Actual PR/parents/tree/status checks verified | terminal |
| \`NEEDS_REPAIR\` | Separate repair is required | \`SUPERSEDED\`, \`ABORTED\`, \`STALE\` |
| \`STALE\`, \`SUPERSEDED\`, \`REFUSED\`, \`REJECTED\`, \`EXPIRED\`, \`FAILED\`, \`ABORTED\` | Named terminal outcome | terminal |

**Precedence.** After intake, any movement of \`dev\` from approved \`base_sha\` **before** the merge, even if the change would still merge cleanly, makes the job \`STALE\`. First reconcile any recorded PR-creation or GitHub merge attempt, then check recorded source/base/candidate/PR SHAs, then approval. A **normal or recovered completed merge** transitions \`MERGING → MERGED\` without a pre-merge STALE test; \`MERGED\` must perform post-merge verification and can **never** become \`ABORTED\`. Authenticated rejection/cancellation before a merge is in flight takes precedence over forward-progress STALE checks.

**Attention holds** are durable: \`attention: {code, reason, since, evidence_refs, last_retry, owner}\` keeps the current non-terminal state, forbids side effects and requires trusted \`resolve-attention --job --evidence\` to clear. GitHub read failures are retried with bounded read-only backoff, then escalated to the operator. If \`MERGING\` has an **unknown external result**, it cannot be aborted, force-completed, or have its PR reservation released; only verified GitHub evidence resolves it. Once definitively not merged, cancellation/rejection/expiry is permitted; after merge, \`COMPLETED\` or \`FAILED\` is mandatory. Other pre-merge states may abort after pending PR-create effects are reconciled. Terminal states never reopen.

### 4.1 PR reuse versus creation

- **Lookup:** fully paginate \`GET /repos/{owner}/{repo}/pulls?state=all&per_page=100&page=N\`; match by immutable \`head.repo.id\` and \`head.sha\`, record head/base repo IDs/refs, PR number/node ID, state and draft flag. A filtered \`head=owner:ref\` lookup is not a substitute for all-states same-head discovery. A PR for the same branch at another SHA does not block.
- **Reuse:** one open matching PR for pinned source repo/ref/SHA and target repo/\`dev\`; otherwise hold with \`non_reusable_pr\` and never create a same-head duplicate. A previously merged PR is not proof the current job completed.
- **Create:** only in \`PR_PREPARING\` after verified **scope/publish** receipt and gates, persisted \`pr_create_attempt\` and a complete all-states lookup. Re-query after a crash/timeout; GitHub 422 "already exists" triggers lookup, not blind retry. Sources must be in the target repository or an eligible fork; no source branch is pushed by the integration job.
- **Recheck:** before review/merge, fetch expected PR number/node ID, source/target repo IDs/ref and SHA, target exactly \`dev\`, **open and non-Draft**. Moved head/base → \`STALE\` before merge; closed/Draft PR → operator hold or authenticated rejection. Do not edit another author's source PR.

## 5. Git safeguards

1. **No pushes by an integration job.** It reads the existing source ref and may create a GitHub PR referencing that ref; it never force-pushes, rewrites the source branch or pushes directly to `dev`.
2. **Isolated, durable candidate.** Use a job-specific **independent Git clone** and object store, never `git worktree add` against the production checkout (which would modify its `.git`). Build a merge of the approved `base_sha` and `source_sha` with fixed Git author/committer identity and dates. Require candidate parents `[base_sha, source_sha]`, record `candidate.head_sha` and `candidate.tree_sha`, and pin the commit under `refs/integration/<job>/candidate` in the isolated clone to survive cleanup/GC. On resume, validate that immutable object and parents/tree from the Git object database, not from a mutable worktree `HEAD`. If the object/ref is missing or differs, **do not silently rebuild or inherit old evidence**; fail closed and require a new approved job (a deterministic rebuild must still be treated as a fresh job unless separately designed and verified).
3. **Validate exactly what is approved.** Run each role gate in a fresh, clean checkout of the pinned candidate commit. Before and after each gate verify HEAD, tree and worktree/index cleanliness (including generated or untracked files, except declared external temporary artifacts). A tool that changes tested bytes invalidates its gate evidence; never attest to a `tree_sha` that was not actually tested.
4. **No duplicate PRs.** Use §4.1's all-states search, persisted PR-create intent, and post-write verification. PR lookup is read-only in `INSPECTING`; the GitHub creation side effect is in `PR_PREPARING`.
5. **Unconditional stale-base rule before merge.** Any change of `dev` from approved `base_sha` **before the authorized merge takes effect**, even a cleanly mergeable change, makes the job `STALE`. A completed-but-not-recorded merge is **not** pre-merge drift; resolve it from its persisted intent first (§5.8). No rebase or re-merge within the same job.
6. **Refuse unsafe inputs.** Refuse unknown/non-allowlisted sources, unexpected authors, protected paths, invalid SHAs or merge conflicts before a candidate is accepted. A mismatched base **at intake** is `REFUSED`; a base/source mismatch **after intake** is `STALE`. Do not confuse these with a non-reusable same-head PR, which creates operator attention (§4.1).
7. **SHA-bound evidence.** Every gate, review and operator authorization stores candidate `head_sha`/`tree_sha`, source/base SHAs and a digest. Changing any such value invalidates the evidence. The merge approval also binds PR identity, both repositories/refs, the GitHub merge-commit method, expiry and revocation state.
8. **Write-ahead side effects and crash recovery.** Each job has durable `state_revision` and a side-effect record saved atomically before GitHub writes. `pr_create_attempt` prevents duplicate creation (§4.1). Before any merge API call, atomically persist state `MERGING` **together with** an authorized `merge_attempt` (PR number, source/target identity, pinned source/base/candidate SHAs, merge method, approval digest, operation ID and time). A normal successful response records the actual GitHub result and moves `MERGING → MERGED`; no pre-merge `STALE` test runs at this transition. After a crash/timeout in `MERGING`, resolve GitHub PR state **first**, with no blind retry:
    - If GitHub confirms **that pinned PR** merged into the intended target at the approved source head, read its resulting merge commit and tree, verify merge identity/parents, save `merge_result`, enter `MERGED`, and perform §5.10. This is not a free pass for an unrelated `dev` change.
    - If GitHub **confirms no merge occurred**, compare approved source/base/candidate and PR identity. Drift → `STALE`; unchanged inputs and still-valid approval → a repeat attempt only after another definitive PR read. If the PR is now closed, Draft, or otherwise unavailable, hold for operator attention; do not blindly retry.
    - If GitHub is unavailable, evidence is contradictory, or the merge outcome cannot be established unambiguously, **hold in `MERGING`**. Never overwrite the intent or assume the lack of a local `merge_result` means the merge failed. Finding `source_sha` as an ancestor of `dev` alone is only a clue; it never proves which PR was merged or that the candidate tree landed.
9. **Single allowed merge mechanism and approval.** GitHub **merge commit** of the exact pinned PR head `sha=source_sha` into the configured repository's **`dev`** branch, and only upon an unexpired, unrevoked, explicitly recorded operator merge approval. Squash, rebase-merge, fast-forward and direct pushes to `dev` are refused. Immediately before issuing the call, re-read GitHub's PR and require all §4.1 identity/state conditions, exact base/head refs and repositories, the approved `base_sha` still at `dev`, candidate head/tree and gate/review evidence unchanged, and the approval's bound fields, expiry and non-revocation. An expired approval transitions to `EXPIRED`; a withdrawn one to `REJECTED` **before** the merge intent is persisted.
10. **Result verification — required for `COMPLETED`.** The isolated candidate merge commit and GitHub's actual `merge_result.commit_sha` may differ; their **trees must be identical**. A checked result requires **all** of:
    - (a) **Before writing:** approved `base_sha` was the `dev` tip immediately before the API call and the source, PR and target identity/state matched §5.9.
    - (b) **Before writing:** reviewed `candidate.head_sha`/`candidate.tree_sha`, recorded gates, source SHA and operator approval scope/expiry/revocation all matched the immutable recorded evidence.
    - (c) **After writing or recovery:** GitHub reports the pinned PR as merged, its `merge_commit_sha` equals the recorded `merge_result.commit_sha`, that commit is reachable from the intended target `dev`, and the Git commit has exactly the approved parents `[base_sha, source_sha]` (in that order). Ancestry alone is not proof.
    - (d) **After writing or recovery:** the Git tree SHA of the resulting `dev` merge commit **equals** the approved `candidate.tree_sha`. No unenumerated equivalent-check exception is allowed.
    - (e) **After writing or recovery:** explicitly configured required post-merge status checks for that exact merge result are green; a bounded wait/timeout is defined by policy. Missing, failed or timed-out checks prevent completion and trigger `FAILED` with operator attention. The permitted set of required checks (including an explicitly approved empty set, if allowed by policy) is recorded in the approval; do not infer an empty set from an API failure.
    - A violation of (a)/(b) **before** a merge is confirmed is `STALE`, `EXPIRED` or `REJECTED`, as appropriate; a violation of (c)/(d)/(e) after a confirmed merge is `FAILED` with `merged_unverified` incident evidence. The Worker cannot automatically undo a write to `dev`.
    - **GitHub target race:** `sha=source_sha` pins only the PR head, **not** the target `dev` SHA. Between check (a) and the API call, an unrelated update or PR retarget can still occur. The first-parent/target and tree checks detect deviations **after the write**; branch protection, restricted PR-retarget permissions or a merge queue reduce, but do not eliminate, this risk. If the design requires an absolute atomic no-wrong-target guarantee, the GitHub merge API alone cannot provide it: require a different guarded merge mechanism and new approval before enabling automatic merge.
11. **Concurrency and ownership.** Hold an exclusive per-job lock across each read–decide–write step and compare-and-swap `state_revision` on every persisted transition. Also reserve at most one active job for each pinned `(source_repo, source_sha)` and each PR number in the coordinator's shared state, fail closed on a conflict. A concurrent manual merge/abort and Worker resume must not both advance a job. External GitHub actors are not governed by these locks; always recheck remote PR/base state. Do not allow `abort` while `MERGING` has an unresolved outcome.
12. **Cleanup and operator visibility.** Preserve event log, intent, approval and resulting SHA/tree evidence even when disposing the isolated clone. Release temporary resources and locks on every terminal state. Record whether a GitHub merge took effect separately from terminal `FAILED` and alert urgently on `merged_unverified`; merely pausing a job must never silently discard it.

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
3. On approval of a new source commit SHA, create a **separate** integration job at `INTAKE`, linked to the old job. Move the old job `NEEDS_REPAIR → SUPERSEDED` and retain its history. No SHA-bound gate/review/approval evidence transfers; neither job is silently reused.

## 7. Implementation prerequisite (operator decision)

Current role ownership covers `backend/**`, `cli/**`, `frontend/**`, `scripts/**` (automation), `docs_agents/coordination/**` and similar. It does **not** cover `feature_coordinator/**`, where the Worker runtime lives. Therefore the current Planner/roles **cannot implement this capability** as the system stands.

**Operator decision required** — one of:

- **A.** Explicitly approve a role/policy change that grants a named role write access to `feature_coordinator/**` (with tests, review and approval-required handling for that path), or
- **B.** Implement it in a separate, trusted development workflow (human or operator-run), reviewed independently, outside the Worker's own role policy.

This design does not choose. Until the decision is recorded, the follow-up engineering Issue must not be scheduled for the Worker.

## 8. Concrete proposal

### 8.1 CLI/API commands

Proposed under `python -m feature_coordinator integrate …` with equivalent API calls. Every mutating command takes `--job`, obtains its concurrency guard and verifies the persisted `state_revision`; none takes a "force" option.

| Command | Effect |
| --- | --- |
| `intake --repo --ref --sha --base dev --base-sha [--pr N] --issue N` | Validate immutable inputs and create the job record (`INTAKE → AWAITING_APPROVAL`) |
| `approve --job J --stage {inspect,merge} --record FILE` | Record operator approval (inspection or SHA/PR/method-bound merge), including expiry/revocation |
| `inspect --job J` | Read-only classification and all-state PR lookup (`INSPECTING`) |
| `validate --job J` | Build/pin an isolated candidate and run fresh-checkout gates; then `PR_PREPARING` |
| `prepare-pr --job J` | Recheck all-state PR matches, reuse a safe PR or create one with persisted intent |
| `review --job J` | Independently review the pinned candidate/PR diff |
| `merge --job J` | Require exact approval and checks (§5.9/§5.10), persist `MERGING` intent, call GitHub, reconcile result and verify tree/status before `COMPLETED` |
| `status --job J [--json]` | Show state, immutable refs, revision, evidence, pending actions and operator holds |
| `abort --job J --reason` | Terminate only when no merge outcome is unresolved; preserve audit evidence |

### 8.2 State record schema

One append-audited JSON record per job, updated atomically (write-temp + fsync + rename), with locked, revision-checked writes. Sensitive values are never placed in logs. The following illustrates a job at `VALIDATING`; optional effects have not started.

```json
{
  "job_id": "int-20261108-0001",
  "issue": 64,
  "state": "VALIDATING",
  "state_revision": 8,
  "attention": null,
  "source": {"repo": "owner/source", "ref": "integration/x", "sha": "<40hex>"},
  "base": {"repo": "owner/target", "ref": "dev", "expected_sha": "<40hex>"},
  "candidate": {"clone": "<isolated job clone>", "ref": "refs/integration/<job>/candidate",
                "head_sha": "<40hex>", "tree_sha": "<40hex>",
                "parents": ["<base_sha>", "<source_sha>"]},
  "pr": null,
  "pr_create_attempt": null,
  "merge_attempt": null,
  "merge_result": null,
  "provenance": {"authors": ["name <email>"], "origin": "..."},
  "paths": [{"path": "backend/x.py", "role": "backend", "class": "owned"}],
  "evidence": [{"kind": "gate:backend", "head_sha": "<40hex>", "tree_sha": "<40hex>",
                "result": "pass", "sha256": "<64hex>", "at": "<UTC>"}],
  "approvals": [{"stage": "inspect", "operator": "...", "source_sha": "<40hex>",
                 "base_sha": "<40hex>", "at": "<UTC>", "expires": "<UTC>", "revoked": false}],
  "required_post_merge_checks": ["<named-check>"],
  "history": [{"from": "INSPECTING", "to": "CANDIDATE_BUILT", "at": "<UTC>", "revision": 7}],
  "created_at": "<UTC>",
  "updated_at": "<UTC>"
}
```

Before a PR creation, `pr_create_attempt` records `operation_id`, source repository/ref/SHA, target repository/`dev` and timestamp; it is reconciled by an all-states lookup on resume. Once the PR is known, `pr` records its number, repo/ref/head SHA, base repository/`dev`, reused/created status and observed GitHub identity. A merge approval binds this PR number, both repositories/refs, `source_sha`, `base_sha`, candidate head/tree, `github-merge-commit`, check policy, expiry and revocation; the SHA-256 digest of this complete authorization is persisted.

Immediately before a merge API call, **in one durable transition** to `MERGING`, `merge_attempt` records `operation_id`, `pr_number`, source/target repo/ref, pinned `source_sha`, `expected_base_sha`, candidate head/tree, `method=github-merge-commit`, `approval_sha256` and timestamp. After a confirmed GitHub merge (including recovery from an uncertain response), `merge_result` records `commit_sha`, `tree_sha`, actual parents, PR number and merge method; it is persisted before `MERGED` verification. Do not treat `merge_attempt` alone as evidence that GitHub merged. These records remain in the audit trail even when the isolated clone is removed.

### 8.3 Acceptance tests (for the later engineering Issue)

- Intake rejects non-allowlisted sources, unknown authors, invalid SHAs, and a target repository/ref other than the configured `dev`; an intake base mismatch is `REFUSED`, later drift is `STALE`.
- A PR lookup paginates **all** states; the same branch at a different SHA does not block. A same-repository/head-SHA PR that is closed, merged, wrong-ref/base or ambiguous holds with `non_reusable_pr`. No new PR is created in that case.
- PR creation happens only in `PR_PREPARING`, with a durable `pr_create_attempt`. Crash/timeout after creation but before PR number persistence reuses the discovered PR and does not open another.
- Source/base/candidate/PR identity changes before merge → `STALE`. A cleanly mergeable change to `dev` is **not** an exception.
- Expired/revoked/unbound merge authorization, PR retargeting to `main`, changed source SHA, wrong repo, closed PR and a still-Draft PR are all refused or held **before** calling GitHub's merge API. The PR must be open and non-Draft; the target is exactly `dev`.
- Normal merge: `AWAITING_MERGE_APPROVAL → MERGING → MERGED → COMPLETED`. Confirm a persisted merge intent, correct GitHub PR/commit, parents `[base_sha, source_sha]`, identical tree SHA and all configured post-merge checks. The pre-merge `STALE` test never vetoes a completed merge during the transition to `MERGED`.
- Inject crashes **before and after every external side effect** (PR creation and GitHub merge), after GitHub merges but before `MERGED` is saved, and on an ambiguous timeout. Resume reconciles GitHub first, never blindly retries or invents completion.
- A merge that completes after `dev` moves between the last precheck and GitHub's write, a wrong-parent result, a mismatched tree, or missing/failing/timed-out post-merge checks → `FAILED` with `merged_unverified` operator alert; no automatic repair of `dev`.
- Create/abort/restart races: per-job lock plus revision CAS prevents lost transitions; competing jobs for a PR or source SHA fail closed. Abort in `MERGING` is rejected until the external outcome is known.
- Removing a candidate worktree does not change `candidate.head_sha`/`tree_sha` or gate evidence; a lost pinned Git object fails closed rather than silently rebuilding. A modifying gate fails cleanliness and cannot certify the old tree.
- `NEEDS_REPAIR → SUPERSEDED` preserves the old record, while a new linked job begins at `INTAKE` with entirely fresh approvals and evidence.
- Merge conflicts and protected/unowned-path violations are refused with no source changes; all terminal states release temporary clones but retain audit records.
- Independent reviewer runs in a separate session (subscription only, no API-key billing), sees the complete relevant diff and cannot authorize merge.
- No operator merge authorization → `merge` refuses. No integration-job push, force-push, source rewrite, or direct push to `dev` exists.

### 8.4 Failure modes and responses

| Failure | Response |
| --- | --- |
| Invalid input or `dev` mismatch during intake | `REFUSED`; no candidate created |
| Source/base/candidate/PR drift before merge, including cleanly mergeable `dev` movement | `STALE`; record both SHAs; fresh job and approval required |
| Non-reusable same-head PR | Hold in `INSPECTING` or `PR_PREPARING` with `non_reusable_pr`; no duplicate |
| Closed or Draft PR before merge, failed/incomplete GitHub lookup | Hold for operator attention; do not merge or create another PR |
| Merge approval expired or revoked before a merge attempt | `EXPIRED` or `REJECTED`; no merge call |
| GitHub merge result unknown after timeout/crash | Hold in `MERGING`; read remote PR/commit before any next action |
| Confirmed merge result violates PR/parent/tree checks | `FAILED` + `merged_unverified` alert; human assesses `dev` |
| Post-merge required check fails/is missing/times out | `FAILED` + `merged_unverified`; never claim `COMPLETED` |
| Merge conflict, invalid author, or protected-path diff | `REFUSED` before merge; no automated conflict resolution |
| Missing/failing validation gate or reviewer | `NEEDS_REPAIR` or `FAILED`; source remains untouched |
| Candidate object missing/different or changed gate checkout | Fail closed; previous gate/review evidence cannot transfer |
| Competing source/PR job, lock failure or revision CAS conflict | Hold for attention; do not start concurrent side effects |
| Process crash in other stages | Resume with immutable SHA verification and side-effect reconciliation (§5.8) |
| Operator abort with merge outcome unresolved | Refuse abort until GitHub confirms whether merge happened |
| GitHub API temporarily unavailable | Hold state; read-only retry/reconciliation before writes |

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

**Default: no automatic merge into `dev`.** Merge needs an explicit, checkable operator decision record (§8.2 `approvals`, stage `merge`) bound to the PR, source/target identities, head/tree SHAs, merge method, required checks and expiry; without it the `merge` command refuses. Merging uses only §5.9 and completes only after §5.10. Even with this approval, GitHub's merge API does not atomically pin the target base (§5.10); a strict no-wrong-target policy requires a separately approved guarded merge mechanism.

A *future* auto-merge policy would require, at a minimum, a separate operator decision record that is itself versioned and checkable, and:

- an allowlisted source repository and known authors only;
- no protected, approval-required or unowned paths in the diff;
- all gates green and independent AI Review passing, bound to the exact head and tree SHAs;
- `dev` still at `base_sha` before the write (any earlier movement makes the job `STALE`), correct PR target and required pre-/post-merge status checks green;
- a diff-size and affected-role ceiling;
- an audit log entry, a kill switch, and an operator-revocable policy with an expiry;
- a successful history of supervised (manual-approval) integrations first.

## 10. Phase-by-phase implementation plan (later engineering Issue)

| Phase | Work | Depends on |
| --- | --- | --- |
| P0 | Operator decision on §7 (role change vs trusted workflow) and approval of this design | none; **blocks all** |
| P1 | Revision-checked state record, side-effect intents, durable locks, terminal states, `intake`/`status`/`abort` | P0 |
| P2 | Read-only inspection, role/path classification, paginated all-states PR lookup and identity rules | P1 |
| P3 | Durable isolated candidate clone/ref, deterministic build, clean-checkout gates and PR_PREPARING | P2 |
| P4 | Independent review with SHA/tree-bound evidence | P3 |
| P5 | PR/target-bound operator approvals, guarded MERGING reconciliation, merge API call, tree and post-merge status verification | P4 |
| P6 | Crash/timeout/race injection, optimistic-lock tests, candidate durability and SUPERSEDED repair routing (§6.4) | P3–P5 |
| P7 | Dry-run pilot on historical branches, docs and operations updates | P5, P6 |
| P8 | (Optional, separate decision) auto-merge policy per §9 | P7 and a recorded operator decision |

Non-goals for every phase: force-push, touching the production checkout, bypassing path policy, and automatic merge before P8's separate decision.
