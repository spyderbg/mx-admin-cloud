# Integration Jobs — M1–M6 Operator Notes, Decisions and Open Questions (W4.7)

> Owner role: **coordination**. Status: **M1 (PR #79), M2 (PR #80), Security S1 (PR #81) and M3 (PR #82: the integration candidate and its validation gates, including the corrections of the Codex functional review, §29) implemented and merged into `dev`. M4 — the independent AI review of the exact validated candidate (§30–§41) — implemented and merged (PR #83); the four defects of the Codex functional review (F1–F4) corrected (§42) and the two remaining defects of its focused re-review (RR1, RR2) corrected (§43); the final focused Codex re-review returned PASS_WITH_NOTES (§43)** (Issue #65, route B: trusted development in a separate clone). **M5 — the operator-approved GitHub merge and its verified completion (§44–§61) — is implemented and merged (PR #84). M6 — Worker scheduling of the automatable stages, crash recovery and its failure-injection tests, and the repair-routing report (§62–§71, including the corrections of the independent Codex review, §71) — is implemented in this change and not yet merged into `dev`; Worker scheduling is off by default and the merge is never scheduled.** Not implemented: iterative revisions of a published PR (Issue #67) and any automatic merge (design §9).
> Design (approved, unchanged): [existing-branch-integration-design.md](existing-branch-integration-design.md). Related: [operations.md](operations.md) · [feature-workflow.md](feature-workflow.md) · [github-intake.md](github-intake.md).
> Code: `feature_coordinator/integration.py` (record, state machine, persistence) · `integration_intake.py` (read-only checks) · `integration_commands.py` (CLI) · `integration_config.py` (`github.integration`). M2: `integration_approval.py` (authorization) · `integration_inspect.py` (inspection) · `integration_paths.py` (changed paths, classification) · `integration_github.py` (PR discovery, attribution) · `integration_isolation.py` (Git process isolation) · `integration_objects.py` (hash-verified objects, moved from `integration_intake.py`). M3: `integration_validate.py` (`validate`) · `integration_candidate.py` (candidate repository, merge, workspace) · `integration_gates.py` (gate plan, sandboxed runner, evidence) · `integration_schema.py` (the record's schema checks, split out of `integration.py`, which re-exports them). M5: `integration_merge.py` (`merge`: reconcile, PR, intent, merge, record) · `integration_merge_record.py` (the signed merge approval, format v2) · `integration_merge_schema.py` (merge evidence and `merge_result`) · `integration_merge_github.py` (GitHub reads, check states) · `integration_merge_status.py` (status view) · `integration_postmerge.py` (post-merge verification); `github_api.py` (the two granted writes) and `integration_isolation.isolated_fetch`. M4: `integration_review.py` (`review`: checks, attempts, decisions) · `integration_review_context.py` (the reviewer's input from the candidate repository) · `integration_review_process.py` (the reviewer process: start, wait, stop, collect; `review-exec`); shared W4.6.1 code reused with minimal, behaviour-preserving changes (§31). M6: `integration_schedule.py` (the next step of a job and who takes it; the Worker's bookkeeping rules) · `integration_slot.py` (the checkout's execution slot: the schedule record and who holds the slot) · `integration_worker.py` (the Worker service's scheduling step) · `integration_recovery.py` (diagnosis, repair routing) · `integration_recovery_commands.py` (`recover`, `schedule`) · `IntegrationMerger.settle` in `integration_merge.py` · `IntegrationWorkerConfig` in `integration_config.py`. Tests: `feature_coordinator/tests/test_fc_integration.py`, `test_fc_integration_intake.py`; M2: `test_fc_integration_approve.py`, `test_fc_integration_inspect.py`, `test_fc_integration_github.py`, `test_fc_integration_gitsafety.py`; M3: `test_fc_integration_validate.py` (shared rig: `fc_integration_rig.py`); M4: `test_fc_integration_review.py` (fake reviewer process: `fc_fake_integration_reviewer.py`), `test_fc_integration_review_corrections.py` (Codex functional corrections F1–F4 and re-review RR1–RR2); M5: `test_fc_integration_merge_approval.py` (signed approval, record format, schema, status and CLI), `test_fc_integration_merge.py` (preconditions, merge behaviour, client restrictions), `test_fc_integration_merge_pr.py` (PR reuse and creation), `test_fc_integration_merge_recovery.py` (post-merge verification, fault injection, concurrency), `test_fc_integration_merge_reconcile.py` (reconcile-first, the signing template), `test_fc_integration_merge_independent.py` (the independent review's findings), `test_fc_integration_merge_e2e.py` (shared scripted GitHub: `fc_integration_merge_rig.py`). M6: `test_fc_integration_worker.py` (scheduling), `test_fc_integration_worker_service.py` (the service's one execution slot), `test_fc_integration_worker_concurrency.py` (real Worker processes, real kills), `test_fc_integration_worker_processes.py` (real stage processes killed with SIGKILL at persisted checkpoints), `test_fc_integration_recovery.py` (failure injection per operation, repair), `test_fc_integration_recovery_cli.py` (shared rig: `fc_integration_worker_rig.py`, `fc_integration_step_process.py`).

## 1. What M1 does and does not do

M1 is the design's milestone 1 (§8.5): the job record, the lifecycle state machine, persistence, and `intake` / `status` / `abort`. M2 adds `approve` and `inspect` (§8–§16 below).

| M1 does | M1 never does |
| --- | --- |
| Validates an integration request strictly and records one job per request | Build a candidate, create a worktree, run a gate, review, approve or merge |
| Reads Git read-only: `ls-remote origin`, `rev-parse`, `rev-list`, `cat-file`, `merge-base --is-ancestor`, `config`/`remote get-url` (§7) | Fetch (explicitly or lazily), create, move or push a ref; touch the index or working tree |
| Reads one pull request (`GET`) when `--pr` is given | Create, edit, comment on or close a PR |
| Writes only `integration/<job>.json` and `events.jsonl` in the coordinator state directory | Schedule anything for the Worker service; the service does not know integration jobs |

## 2. Commands

`integrate FEATURE` (the feature integration barrier) already exists, so the job commands live under **`integration`** (see §6, item 5).

```bash
# Dry run (reads only, records nothing):
python -m feature_coordinator integration intake --source-repo spyderbg/mx-admin-cloud \
    --source-ref integration/x --source-sha <40-hex> --base dev --base-sha <40-hex> --issue N [--pr N] [--json]
# Record (needs github.integration.enabled): INTAKE → AWAITING_APPROVAL, or REFUSED
python -m feature_coordinator integration intake … --apply [--actor NAME] [--json]
python -m feature_coordinator integration status [--job int-YYYYMMDD-NNNN] [--json]          # read-only
python -m feature_coordinator integration abort --job int-… --reason TEXT [--apply] [--json]  # dry run without --apply
```

Exit codes: `0` OK (intake: the job is active past `INTAKE`, or would be); `1` refused, deferred, terminal or failed; `2` invalid arguments. With `--json`, a refusal is also printed on stdout as `{"ok": false, "error": {"type", "message"}}`.

Before `--apply`, the commits must already be present in the checkout (`git fetch origin <source-ref> dev`). The intake never fetches.

## 3. Configuration

```yaml
github:
  integration:          # absent = disabled (every existing configuration loads unchanged)
    enabled: false
    known_authors: []   # commit author e-mails accepted at intake; enabled with an empty list is a config error
```

The M1 source allowlist is `github.repository` itself. Refs are resolved through the checkout's `origin`, which must be exactly that repository, with one URL that is not rewritten. A checkout whose own Git configuration would run a program (gitsafe) is refused before the first read. Remote reads use `github.publish.git_timeout_seconds`.

## 4. Intake checks and outcomes

| Step | Failure → |
| --- | --- |
| Malformed input: SHA not 40 lowercase hex, invalid branch or `owner/name`, number ≤ 0, missing field | error, **nothing recorded** |
| `--apply` without `github.integration.enabled`; `origin` not `github.repository`; execution config in the checkout | error, **nothing recorded** |
| The checkout is shallow, has a graft file, uses another object format than SHA-1, or is a partial clone (promisor remote) while Git has no `--no-lazy-fetch` (Git < 2.44) | error, **nothing recorded** (also checked again before the reads: then `INTAKE` with `deferred`) |
| An active job (non-terminal) holds the same source branch, source commit or PR with another request | error, **nothing recorded** (abort it first) |
| Policy: source repository not allowlisted; base ≠ configured base branch; source = base | `REFUSED` (no remote read) |
| `origin`'s source or base branch is missing or not at the stated SHA | `REFUSED` (never `STALE` at intake) |
| Remote or GitHub unreadable; a commit not present locally | stays `INTAKE` with `deferred`; repeat the same command |
| Source already contained in base, more than 2000 commits, or a fork point more than 100 000 base commits back | `REFUSED` |
| The history cannot be proven complete and unmodified (§7): a missing ancestor, Git's parents differ from the commit objects, an object that does not hash to its SHA, unexpected Git output | stays `INTAKE` with `deferred`; never accepted |
| A commit object in the range is malformed (strict form, §7): e.g. a control or bidirectional-text character in a name, an e-mail with whitespace or control characters, a second `author` line, a NUL byte | `REFUSED` |
| A commit author (from the raw object, no mailmap) not in `known_authors` | `REFUSED`; committers are recorded, not checked |
| `--pr`: missing, closed/merged, other head repo/branch, head ≠ `source_sha`, other base | `REFUSED` |
| Everything passes | `AWAITING_APPROVAL` with `provenance` (and verified PR facts) |

A `REFUSED` job records `attention`: every reason plus the observed SHAs. It is terminal and is never retried automatically.

## 5. Record, idempotence, concurrency, recovery

- **Record.** One JSON file per job (`int-YYYYMMDD-NNNN`, allocated under `coordinator.lock`), with the design §8.2 keys. M2–M5 placeholders (`paths`, `candidate`, `evidence`, `approvals`, `merge_result`) are empty. Loading is strict (`validate_job`, the same checks before every save): the record must be a JSON object under its own file name; every field must have its type and constraints (`revision` an integer ≥ 1 on disk, timestamps, SHAs, the nested `source`, `base`, `pr`, `provenance`, `attention`, `deferred` and history entries with exactly their keys); the history must replay through the state machine and end at `state`; `request_key` must match the inputs; and the invariants hold (`deferred` only in `INTAKE`; provenance once `AWAITING_APPROVAL` was reached; a merge commit for `MERGED`). Anything else — and any other file in `integration/` — is a `StateDirError`: `status`, `abort` and `intake` fail (with `--json`: `{"ok": false, "error": …}`, exit 1). A corrupt record is never skipped or repaired.
- **Persistence** reuses `StateStore`: atomic write (temp + fsync + rename + directory fsync), revision check against stale copies, and `transaction()` (`coordinator.lock`). Every save compares with the record on disk. The job ID, source, base, Issue, PR number, request key and `supersedes` are immutable; `history` is append-only; a terminal record is final.
- **Transitions.** The design table plus operator `ABORTED` from every non-terminal state before `MERGED`. A transition to the current state is a no-op. Terminal states have no outgoing edge. `MERGED` requires a `merge_result` commit and `COMPLETED` a verified one; M1 never records either. Events: `integration-intake`, `integration-transition`, `integration-intake-deferred`.
- **Idempotence.** A request's key is the SHA-256 of its canonical inputs. Repeating the same request returns its job unchanged (no write, no event); a job still in `INTAKE` is resumed. A terminal job is returned as it is. A new attempt with identical inputs needs `--supersede <that job>`, which is recorded on the new job; repeating it returns the same new job. There is no force option.
- **Crash recovery.** The job is recorded in `INTAKE` before any check (write-ahead). A crash before the decision leaves it in `INTAKE`, and the same command resumes it.
- **Concurrency.** Two identical intakes produce one job; of several conflicting intakes (same branch, commit or PR) exactly one records a job. The dry run applies the same conflict check as `--apply` (without the lock: a preview); `--apply` decides again under `coordinator.lock`. A transition carries the state its decision was based on: an abort that lands while the checks run wins, and the intake then changes nothing.

## 6. Design ambiguities found during M1, and the resolutions used

The approved design was not changed. Where M1 had to choose, it chose the more restrictive option, and each choice is listed here for operator confirmation.

1. **`REFUSED` versus `STALE` for PRs (§4.1).** "A candidate PR that matches by branch with a different head SHA → `REFUSED`" overlaps with "the recorded PR's head has moved away from `source_sha` → `STALE`". A PR given with `--pr` and verified at intake is both, once its head moves. Likewise, a moved `source_ref` (`STALE` by §4) shows up as a branch-matched PR with another head (`REFUSED` by §4.1). **Proposed rule:** `STALE` ⇔ a value pinned earlier in the job record no longer holds (source ref, base ref, recorded PR head, candidate head/tree). `REFUSED` ⇔ an input or a newly discovered fact never satisfied the rules. Pinned values are evaluated first, so `STALE` wins for the same observation. At `INTAKE` nothing is pinned yet, so every mismatch is `REFUSED`, which matches §4's "States without a `STALE` transition". M1 implements only the intake half (no `INTAKE → STALE` edge). M2 should apply the rest when it adds the PR lookup.
2. **`INTAKE` is "entered when validated", yet `INTAKE → REFUSED` exists.** M1: `INTAKE` is entered when a well-formed request is recorded; the checks run in `INTAKE`. Malformed requests create no record.
3. **§3 lists `authorization` and `provenance` as mandatory intake inputs; §8.1 `intake` takes neither.** M1: provenance is derived read-only from `git log base..source`. Authorization is the later `approve` step (`approvals` stays empty), which is why the job waits in `AWAITING_APPROVAL`.
4. **Milestone order.** `AWAITING_APPROVAL → INSPECTING` needs the inspect-stage approval (`approve --stage inspect`), but `approve` is scheduled for M5. **Proposal:** implement `approve --stage inspect` in M2 (bound to the SHAs, with expiry), keeping the `merge` stage in M5.
5. **Command name.** The design proposes `python -m feature_coordinator integrate …`, but `integrate FEATURE [--apply]` already exists and is referenced by the Worker, the publisher and these docs. `status`, `abort` and `intake` are also valid feature IDs, so overloading it would be ambiguous. M1 uses **`integration`** and leaves `integrate FEATURE` unchanged (tested). The design's `--repo` / `--ref` / `--sha` flags collide with the global `--repo PATH`, so M1 uses `--source-repo` / `--source-ref` / `--source-sha`.
6. **`ABORTED` edges.** The prose allows abort from every non-terminal state before `MERGED`, but the table lists `ABORTED` only for `NEEDS_REPAIR`. M1 follows the prose.
7. **`NEEDS_REPAIR → INTAKE`** is listed as a transition but described as "a **new job** linked to this one" (§6.4). An in-place edge would reopen a job with different SHAs, so M1 does not encode it. **Open question for M6:** the old job's final state once its repair job exists (proposal: `ABORTED` with reason `superseded by <job>`, or a design amendment adding a terminal `SUPERSEDED`).
8. **`CANDIDATE_BUILT → REFUSED (conflict)`.** A merge conflict happens while building the candidate, before `CANDIDATE_BUILT` is entered (where `INSPECTING → REFUSED` already exists). M1 keeps the edge as written; M3 should decide.
9. **`NEEDS_REPAIR` versus `FAILED`** after a failed gate or review has no stated criterion (§8.4: "`NEEDS_REPAIR` or `FAILED`"). This is for M3/M4.
10. **`issue`** "links the job to an approved plan", but what counts as approved is not defined. M1 validates and records the number only. M2 should verify the Issue (open, not bug-workflow, and an approval record or plan).
11. **Allowlist and known authors are "config", but no such configuration existed.** M1 adds the strict, disabled-by-default `github.integration` section (§3). The allowlist is fixed to `github.repository`, because refs are resolved through `origin`; fork sources need M2+ remote handling. Authors are matched by commit e-mail. That is weak provenance, since Git identities are self-asserted. **Proposal for M2:** also require GitHub's verified login for each commit (compare API) to be in `github.command_authors`. "Who pushed" is not readable from Git and is left to M2.
12. **`base_ref` "normally `dev`".** M1 requires the configured base branch, which is fail-closed.

## 7. Security notes

- No new write path to Git or GitHub. A runtime allowlist in `ReadOnlyGit` refuses non-query subcommands (`rev-parse`, `rev-list`, `cat-file`, `merge-base`, `config`, `remote`; `ls-remote` through the bounded `GitCoordinator.remote_head`), and a test intercepts every `git` process to assert that only read-only subcommands run and that the checkout's `.git`, its remote and the working tree are byte-identical afterwards.
- **The local history is not trusted (Codex review, findings 1–3).** Every local intake command runs with `--no-replace-objects`, `GIT_NO_REPLACE_OBJECTS=1`, `core.useReplaceRefs=false`, `GIT_GRAFT_FILE` pointing at a path that cannot exist, `core.commitGraph=false`, `gc.auto=0`, `maintenance.auto=false`, `GIT_NO_LAZY_FETCH=1`, and every transport refused (`protocol.allow=never`, `protocol.<name>.allow=never` for each built-in transport, `GIT_PROTOCOL_FROM_USER=0`); inherited `GIT_SHALLOW_FILE`/`GIT_REPLACE_REF_BASE` are removed. Provenance comes from `ReadOnlyGit.verified_range`: Git lists base..source (`rev-list --parents`), every listed commit object is read raw through `cat-file --batch` (length-delimited — no field delimiter a commit could contain), its SHA-1 is recomputed, it is parsed strictly, and Git's parents must equal the object's parents; every parent outside the list must be reached from `base_sha` through hash-verified commit objects. A commit reachable from the source but not from the base therefore cannot be missing from the check.
- Partial clones: the refused transports stop any download, but Git's promisor fetch still writes `remote.<name>.partialclonefilter` to `.git/config` before it fails. A partial clone is therefore accepted only when Git has `--no-lazy-fetch` (Git ≥ 2.44, where `GIT_NO_LAZY_FETCH=1` stops the fetch before it starts) and refused otherwise. Distributions that backport only the variable (Ubuntu's 2.43.0-1ubuntu7.4 honours it) are treated as not supporting it.
- A repository `.mailmap` cannot turn an unknown author into a known one (raw objects, tested). Author e-mails are compared with only ASCII letters lowercased (no Unicode case folding: U+212A KELVIN SIGN does not become `k`).
- Remaining trust in the checkout: the "already contained in base" refusal and the size limits use Git's own (unverified) walk, which can only cause a refusal, never an acceptance; commit-object hashing is plain SHA-1 (Git's collision detection is not re-implemented).
- Worker role ownership is unchanged: `feature_coordinator/**` stays unowned, and no role, path or protected-path rule was edited. Automatic merge stays impossible: no M1 code path can reach `MERGED`.
- **M2 change:** every object and remote read of the intake now runs in a private repository instead of the checkout, and the configuration scan is stricter and repeated before the decision (§14). The M1 outcomes are unchanged.

## 8. What M2 adds

M2 is the design's milestone 2 (§8.5, phase P2): read-only inspection (path and role classification, PR match) and — moved forward from M5 as proposed in §6, item 4 — the operator authorization of the inspect stage.

| M2 does | M2 never does |
| --- | --- |
| Verifies an operator authorization record signed with the operator's SSH key and bound to the job's pinned SHAs, Issue and PR; appends it to the job's `approvals` (append-only, audit events) | Infer an approval from GitHub (comments, labels, reviews, PR state); accept an unsigned, expired, mismatched or replayed record |
| Inspects read-only: pinned refs re-verified, history re-verified from hash-verified commits, GitHub attribution, the complete PR lookup, the full changed-path list classified through PathPolicy | Build a candidate, create a worktree, run a gate, review, create/edit/close a PR or merge; fetch, push or write any ref, index or working-tree file |
| Runs every object and remote Git read in a private repository linked to the checkout only by `objects/info/alternates` (§14) | Run a Git process that reads the checkout's configuration, hooks or attributes for an object or remote read |
| Writes only `integration/<job>.json` and `events.jsonl` (and a private repository under the state directory's `tmp/`, removed after each run) | Schedule anything for the Worker service |

## 9. Commands (M2)

```bash
# 1. Intake as in M1 (job → AWAITING_APPROVAL). 2. The operator writes and signs an authorization record (§11):
ssh-keygen -Y sign -f ~/.ssh/<key> -n mx-admin-cloud-integration-approval@feature-coordinator approval.json  # → approval.json.sig
# 3. Verify it (dry run), then record it:
python -m feature_coordinator integration approve --job int-… --stage inspect --record approval.json [--signature FILE] [--json]
python -m feature_coordinator integration approve --job int-… --stage inspect --record approval.json --apply
# 4. Inspect (dry run reads only; --apply records the decision):
python -m feature_coordinator integration inspect --job int-… [--json]
python -m feature_coordinator integration inspect --job int-… --apply [--actor NAME]
# 5. If the result is `needs-authorization`: sign a new record listing the paths (and, if needed,
#    "accept_unverified_provenance": true), approve it, and run inspect --apply again.
```

Exit codes as in §2; `approve` exits 0 for recorded / existing / would-record, `inspect` exits 0 only for passed / would-pass. Both require a valid approval for the dry run too: the approval authorizes the inspection, not only its recording.

## 10. Configuration (M2)

```yaml
github:
  integration:
    enabled: true
    known_authors: [dev@example.org]
    operators: [spyderbg]                          # who may sign authorization records
    allowed_signers: /home/op/.config/mx-admin/allowed_signers   # absolute, outside every checkout
    trusted_logins: [spyderbg]                     # GitHub accounts trusted for attribution and verified signatures
```

All three keys are optional, so every M1 configuration loads unchanged; an empty `operators` or `allowed_signers` makes `approve` refuse, an empty `trusted_logins` makes `inspect` refuse to run. The allowed-signers file uses the OpenSSH format, one line per operator key, with the namespace restricted:

```text
spyderbg namespaces="mx-admin-cloud-integration-approval@feature-coordinator" ssh-ed25519 AAAA… operator@host
```

It must be a regular file (not a symbolic link), owned by the user running the coordinator, not writable by group or others, in a directory not writable by group or others, and outside the `--repo` checkout. Removing a key from it revokes every approval made with that key at its next use.

## 11. Operator authorization (`approve`)

The record is one JSON object; every key is required except `note`:

```json
{"kind": "w47-integration-approval", "version": 1, "job": "int-20261112-0001", "stage": "inspect",
 "operator": "spyderbg", "issued_at": "2026-11-12T09:00:00Z", "expires_at": "2026-11-12T21:00:00Z",
 "source": {"repo": "spyderbg/mx-admin-cloud", "ref": "integration/x", "sha": "<40 hex>"},
 "base": {"ref": "dev", "sha": "<40 hex>"}, "issue": 63, "pr": null,
 "paths": ["feature_coordinator/x.py", "README.md"], "accept_unverified_provenance": true, "note": "first look"}
```

| Check | Refused when |
| --- | --- |
| Form | Not one JSON object; a duplicated key, NaN/Infinity, an unknown or missing key, a value of the wrong type; a path with `..`, a leading `/`, a backslash or a control character |
| Identity | `ssh-keygen -Y verify` fails for principal = `operator` in the namespace above against `allowed_signers` (another key, another principal, a Git commit signature, a changed byte); `operator` not in `operators`; the allowed-signers file untrustworthy |
| Binding | `job`, `source` (repo, ref, SHA), `base` (ref, SHA), `issue` or `pr` differ from the job's pinned values — an identical request under a superseding job needs its own record |
| Time | A timestamp not `YYYY-MM-DDTHH:MM:SS[.f]Z` with at most 6 fractional digits (compared at full precision: `…:00.9Z` is later than `…:00.1Z`); `issued_at` more than 5 minutes in the future; `expires_at` not in the future; a lifetime above 7 days |
| Order | `issued_at` not STRICTLY later than that of every approval of the stage already recorded: an older record (e.g. a broader one signed earlier, never recorded) cannot undo a later, narrower decision, and a different record issued at the same instant (in any spelling, `…:00Z` = `…:00.000Z`) is refused as ambiguous — sign a new record with a later `issued_at` |
| Scope | `stage` other than `inspect` (the merge stage is M5); a protected path in `paths`; a job not in AWAITING_APPROVAL or INSPECTING |

`--apply` appends `{id: apr-NNNN, …, record, record_sha256, signature, signer: {method: ssh-sig, namespace, principal, key}}` to `approvals` with an `integration-approval` event; the exact signed text is kept, so the signature can be checked again at every use. Repeating a recorded record byte for byte changes nothing (`existing`); the result's `effective` says whether it is the effective approval, and replaying a superseded record never makes it effective again (the CLI prints "superseded, authorizes nothing"). The same content serialized differently is a different signed record and is refused as ambiguous. The duplicate, order and time checks run again inside the coordinator transaction that appends the entry, so concurrent `approve` runs are serialized and the newest-issued record ends up effective whatever order they finish in. A refused attempt is logged as `integration-approval-refused` and changes nothing else. On every load the stored text must hash to `record_sha256`, its parsed fields must equal the entry, it must bind the job, and the approvals of a stage must be in strictly increasing `issued_at` order; `approvals` is append-only.

**Effective approval.** The newest inspect approval is the effective one. At every inspection (dry run included, and again right before the decision is recorded) it must still verify against the current allowed-signers file, its operator must still be listed and it must not have expired; otherwise it authorizes nothing and there is no fallback to an older approval. The signature check runs just before the coordinator transaction (no subprocess under the lock); inside the transaction — the decision point — the time window is checked again with a fresh clock reading, and the allowed-signers file must still hold exactly the content the signature was verified against (`ssh-keygen` verifies against a private copy of it). An approval that expires while the checks run therefore authorizes nothing (`no-approval`), and a key revoked meanwhile records nothing (`changed-meanwhile`). An expired approval leaves the job in AWAITING_APPROVAL or INSPECTING (§15, item 2).

## 12. Inspection (`inspect`)

| Step | Finding → outcome |
| --- | --- |
| Approval | No valid effective approval → `no-approval`, nothing changes (an `integration-inspection-refused` event with `--apply`) |
| Preflight | `trusted_logins` empty, `origin` not `github.repository`, a shallow/grafted/non-SHA-1 checkout or an unsupported partial clone, execution settings in the checkout configuration → error, nothing recorded |
| Concurrency | Another `inspect --apply` of the same job holds `locks/integration-<job>.lock` → error, nothing done (dry runs take no lock) |
| Write-ahead | AWAITING_APPROVAL → INSPECTING, naming the approval; a crash later leaves the job in INSPECTING and the same command resumes |
| Pinned values | origin's source or base branch moved or deleted → **STALE**. A PR verified at intake whose own read (`/pulls/{n}`) reports a changed head SHA, head repository/branch or base → **STALE** only once the complete PR lookup (listing, head query, commit query) agrees with that read; a disagreement, an incomplete listing or a failed lookup → deferred, so one GitHub answer never ends a job. That PR not found (HTTP 404, almost always an access problem), or the read answering for another PR → deferred |
| Policy, objects | Source no longer allowlisted, base not the configured base → REFUSED; commits not present locally, or the checkout's object store contradicting what intake proved (a pinned SHA not a commit, the source suddenly contained in the base) → deferred, never REFUSED |
| History, authors | The range re-verified from hash-verified commit objects (M1); an author not in `known_authors` → REFUSED |
| Provenance | GitHub attributes an author e-mail to, or a valid signature is by, an account outside `trusted_logins` → REFUSED; attribution unavailable or a transient verification state → deferred; unverified/unverifiable commits without `accept_unverified_provenance` → needs-authorization |
| PR lookup | Incomplete listing, API failure, a PR object missing a field the rules read, or two GitHub answers that disagree about the same PR (listing, head query, commit query, the intake PR read on its own) → deferred; any candidate that cannot be reused, or more than one → REFUSED (the PR numbers, reasons and SHAs in `attention`); none → nothing created |
| Paths | Protected path (the path or any of its directories; in any commit of the range or in the net diff from a merge base), gitlink, an added empty tree, symlink leaving the repository / into `.git` / at or above a protected path, a symlink in the source tip not resolving to a regular file, malformed tree or name, unrelated histories, more than 2000 paths → REFUSED; Git's merge base not a proven boundary parent → deferred; approval-required, unowned, symlink or git-control path without a per-path authorization → needs-authorization |
| Decision | Recorded only while the job is still INSPECTING under the same effective approval, that approval is still valid at the decision time (fresh clock reading, inside the transaction), the allowed-signers file is unchanged since the signature check, and the checkout configuration still names no program |

Precedence for one inspection: STALE > REFUSED > deferred > needs-authorization > passed. **Deferred** keeps the job in INSPECTING with `deferred` (M2 allows `deferred` in INSPECTING as well as INTAKE). **needs-authorization** keeps it in INSPECTING with `attention` listing exactly what to authorize. **passed** keeps it in INSPECTING with `paths` and a `passed` evidence entry; M3 starts from there and must require `integration.current_inspection_passed(job)` (INSPECTING, nothing deferred, the newest evidence passed under the currently effective approval) and re-verify that approval at use time — an older `passed` entry stays in the append-only evidence after a newer approval or a later deferral. Every decided inspection appends an evidence entry `{kind: inspection, id: ins-NNNN, approval, source_sha, base_sha, result, pr_match, provenance, paths: {count, sha256, required, authorized, refused}, reasons, sha256}`; `sha256` binds the payload, and a `passed` entry cannot carry an unauthorized or refused path or an unresolved PR. Repeating an inspection that finds exactly the same changes nothing.

**Changed paths.** The union over every commit of the range — single-parent commits against their parent, root commits entirely, merge commits only for the paths that differ from every parent — plus the net diff from each merge base to the source tip, read from trees and blobs whose SHA-1 is recomputed (Git's own diff is not trusted with the checkout's object store). A protected file added and later removed is therefore still refused (the merge brings that commit into `dev`'s history), and so is a merge inside the source that takes a file from an old base commit the base has since deleted or hardened (it would come back into `dev`'s tree). Merge bases are computed by Git but accepted only when each one is a boundary parent of the verified range. M3 must also re-classify the exact candidate diff (base tree → candidate tree).

| Class (PathPolicy, lexical) | Meaning |
| --- | --- |
| `protected` | `protected_paths`, matched against the path AND each of its directories (`backend/src/.env/secret.py` is protected through `backend/src/.env`, `**/.env`) — refused, never authorizable |
| `approval-required` | `approval_required_paths` (same ancestor rule) — needs a per-path authorization |
| `owned` | owned by a role (`role` recorded) |
| `unowned` | no role owns it (e.g. `feature_coordinator/**`, the root) — needs a per-path authorization |

Flags: `symlink` (added or changed; target read and checked — it may not be, or be a directory containing, a protected path, and a symlink kept in the source tip must resolve to a regular file of that tree; needs authorization), `gitlink` (refused), `executable`, `git-control` (`.gitattributes`, `.gitmodules`; needs authorization), `renamed` (exact rename; both paths classified), `history-only` (identical in the source tip and the base tip), `empty-tree` (an added empty directory — an empty tree object, which `git add` never records, so it only comes from a crafted tree; refused). A changed directory is otherwise represented by the files below it, each carrying the directory in its path; an empty tree has none, so it is listed as a path of its own (added, deleted or type change) and name-checked and classified like any other. Changes: `added`, `deleted`, `modified`, `mode`, `type`.

**Provenance levels.** `verified`: GitHub verified the signature (state VALID) and the signer is in `trusted_logins`; a commit GitHub signed itself (web UI/API, "web-flow") is not verified, because its author fields can be set freely through the API. `unverified`: no valid signature; the identity is only claimed (an e-mail, and GitHub's account match derived from it, prove nothing). `unverifiable`: no GitHub attribution (the local tracker). Accepting unverified provenance is recorded as `accepted` next to the counts, never as verified. GitHub's answer is parsed fail-closed: every requested field (`oid`, `author { user }`, `committer { user }`, `signature { isValid state wasSignedByGitHub signer }`) must be present, `null` only where GitHub's schema allows it; a missing or null `wasSignedByGitHub`, `isValid` or `state`, or a contradictory pair (`state` VALID with `isValid` false, or the reverse), defers the inspection — missing data is never read as an explicit "false".

**PR reuse** (design §4.1, exactly): every PR in every state is listed with strict pagination (`Link` headers followed exactly; a short page announcing more, a full last page followed by a non-empty one, too many pages, duplicates or a malformed PR fail closed). Each PR object must carry every field the rules read (`state`, `draft`, `merged_at`, head and base `ref`/`sha`/`repo`; `merged` for a single PR). The listing is cross-checked against GitHub's lookups by head branch and by commit and against the intake PR read on its own: every PR they return must be in the listing with the same state, merged and draft flags, head repository, branch and SHA, and base repository and branch, and the head lookup must only return PRs with the queried head. Any disagreement defers the inspection and neither snapshot is used, so "no matching PR" is never concluded while one lookup reports a possible match. Candidates are the union of the branch match (any SHA) and the SHA match (any repository or branch). Reuse only one open candidate from `source_repo:source_ref` at `source_sha` into `github.repository:base_ref` (and the intake PR, if one was given). Nothing is ever created in M2.

## 13. State transitions in M2

The transition table is unchanged. M2 uses AWAITING_APPROVAL → INSPECTING (only with a valid approval, by `inspect --apply`), INSPECTING → STALE and INSPECTING → REFUSED, plus staying in INSPECTING (deferred, needs-authorization, passed). It never enters EXPIRED, REJECTED, NEEDS_REPAIR or CANDIDATE_BUILT. M1 behaviour is unchanged; one M1 test was adjusted (`test_a_graft_that_git_honoured_would_be_caught_by_the_parent_check`) because its simulated attack relied on Git reading the checkout's own `info/grafts`, which the private repository now prevents; it forces the graft through `GIT_GRAFT_FILE` instead, so the parent check is still exercised.

## 14. Git security: control-file races (the remaining Codex finding on M1)

**Problem.** M1 checked the checkout configuration once and then ran each Git process with the checkout as its repository; each process re-reads `.git/config` (and its includes), hooks, attributes, grafts, shallow, replace refs and promisor settings. A change after the check reaches every later process. Two related gaps were reproduced on this host (Git 2.43): `remote.<name>.uploadpack`, which gitsafe does not flag, runs a program during `ls-remote origin` (positive-control test); and gitsafe's patterns miss subsection names containing a dot (`credential.https://host.example.helper`, `filter.a.b.smudge`, `merge.a.b.driver`). Re-checking before each process would only narrow the window.

**Mechanism.** No integration Git process that reads objects or a remote has the checkout as its repository. Each run creates a private bare repository with `mkdtemp` (mode 0700) under the state directory's `tmp/`, written by the coordinator (no `git init`): `HEAD`, `refs/`, its own `config`, and `objects/info/alternates` naming the checkout's object directory. `rev-list`, `cat-file`, `merge-base` and `ls-remote` run with `--git-dir=<private>`, so Git reads only the system/global configuration (trusted, as in gitsafe) and the private config. `ls-remote` gets the URL validated at preflight on its command line, with only https/ssh allowed (file for the tests' local remotes). There is no promisor remote, so lazy fetching, and the `.git/config` write it caused, cannot happen. Object data from the checkout is hash-verified before use (commits: M1; trees and blobs: M2). The private repository is removed when the run ends.

**What still reads the checkout:** the preflight queries `config`, `remote get-url` and `rev-parse`. They run no program and never write, whatever the configuration says (tested with every known execution key and every hook planted). The strict configuration scan (a tested superset of gitsafe, including the keys above) runs at preflight and again right before a decision is recorded. A checkout whose configuration was changed during the run is refused as tampered, and nothing is recorded.

**Adversarial tests** (`test_fc_integration_gitsafety.py`): settings planted right after preflight (and removed before the decision) never run during intake and inspection; settings still present at decision time refuse the run and record nothing; a thread rewriting `.git/config` continuously during six intakes runs nothing; a promisor configuration with `GIT_NO_LAZY_FETCH` ignored leaves the checkout byte-identical; every object/remote query carries `--git-dir=<private>`, the private repository holds only the coordinator's files and is removed afterwards; unsafe URLs and refs are refused before any process starts.

**Residual risk (accepted for read-only M2, documented):** the Git binary, the system and global Git configuration and the operator account are trusted. Git parses the checkout's object files (loose objects and packs) through the alternate, so a Git parser vulnerability stays reachable; results are hash-verified and reads are bounded by timeouts, and a hostile object store can still make a run slow or defer. The preflight queries can be stalled by a hostile `include.path` (for example a FIFO) until their timeout. The state directory must remain writable only by the operator.

**Explicit blocker for enabling write-capable integration (M3 or later).** The M2 guarantee covers read-only processes only. Before any milestone builds a candidate, checks out, merges or pushes, it must (status after M3 in §21):

1. keep the same isolation: build in a coordinator-owned repository with coordinator-written configuration, never `git worktree add` or `checkout` in the checkout;
2. disable content-triggered execution, which configuration isolation does not cover: filters and merge or diff drivers that the SOURCE's `.gitattributes` assigns to drivers defined in the trusted global configuration (for example git-lfs smudge/process). Either use `GIT_CONFIG_GLOBAL`/`GIT_CONFIG_NOSYSTEM` with an explicit minimal configuration, or override every `filter.*`/`merge.*.driver` and run `merge-tree` without drivers;
3. keep hooks off (`core.hooksPath=/dev/null`, `--no-verify`) and push only fast-forward, by full SHA, to a validated URL;
4. fix gitsafe's two gaps (dotted subsections, `remote.*.uploadpack`/`receivepack`/`vcs`) for the Worker too, or route the Worker's integration writes through the strict scan. This is a change to a production Worker module, so it needs an operator decision (§15, item 6).

## 15. Decisions taken in M2 that need operator approval

1. **Operator identity = SSH signature.** An authorization identifies the operator only through `ssh-keygen -Y verify` against the operator-maintained allowed-signers file, in a dedicated namespace, plus `github.integration.operators`. A name typed into a file, a local actor name and anything on GitHub are never identity. Alternatives: GPG, or a GitHub-side check. Both are rejected because they depend on GitHub or on keyrings an agent may reach.
2. **EXPIRED is not entered automatically.** An expired approval is simply invalid: the job stays in AWAITING_APPROVAL or INSPECTING until a fresh approval is recorded, or the operator aborts. Proposal: reserve EXPIRED for a future "approval deadline" on the job itself.
3. **Approval-required, unowned, symlink and git-control paths do not refuse the job.** They leave it in INSPECTING as needs-authorization until a new signed record authorizes them by exact path. The user requirement says "require explicit authorization", not "refuse". Protected paths and gitlinks still REFUSE. The design's §8.3 wording "an unowned-path diff is refused without a per-path approval" is read as "the inspection does not pass".
4. **Dry runs need the approval too.** The approval authorizes the inspection, not only its recording.
5. **Unverified provenance needs explicit acceptance.** The current commits are unsigned (GitHub-signed merges aside), so requiring verified signatures would block every job. Instead, any commit that is not verified needs `accept_unverified_provenance` in the signed record. A GitHub account match or a valid signature that points to an untrusted login REFUSES.
6. **gitsafe is not changed in M2** (no production Worker change). Its gaps are documented, and integration uses the stricter scan and isolation. Fixing gitsafe for the Worker is proposed as a separate, reviewed change and is a blocker for M3 (§14).
7. **Path list = every commit of the range plus the net diff from each merge base.** The per-commit part catches content that only passes through the history; the net-diff part catches what would land in `dev`'s tree (§17).
8. **No new top-level record field.** The inspection result is an `evidence` entry of kind `inspection` (schema version unchanged). M3 will add gate and review entries next to it.
9. **The Issue is still not verified** (open item 10 of §6): what counts as an approved plan is undefined. Proposal: M3 should require the Issue to be open, not in the bug workflow, and carry the planning approval of W4.5.
10. **Approvals of a stage are strictly ordered by `issued_at`** (Codex review F1, §18). A different record issued at the same instant as a recorded one is refused as ambiguous rather than ordered by arrival or by record content. Operators who write timestamps by hand must give each new record a later `issued_at` (seconds suffice; fractions are honoured). Alternative: a monotonic sequence number in the record; rejected for M2 as a format change.
11. **An added empty tree refuses the job** (Codex review F3), whatever its path. Git cannot record an empty directory through normal use, so one only comes from a crafted tree, and it would carry an invisible entry into `dev`. Deleting an empty tree that the base carries is classified like any other path, not refused for being empty.
12. **The ancestor rule also applies to approval-required patterns.** A path below a directory named like an approval-required file (`README.md/x`) needs a per-path authorization. Ordinary directories are restricted only when their name matches a configured pattern (`backend/src/data/` stays owned; only root `data/**` is protected).
13. **The Worker's PathPolicy has the same ancestor gap and is not changed in M2.** Reproduced: `PathPolicy.check("backend", "backend/src/.env/secret.py")` and `check("frontend", "frontend/.env.local/config.ts")` are allowed, because `**/.env` and `**/.env.*` match only a full path. The integration classifier applies the ancestor rule itself. Fixing `paths.py` (and the sandbox mounts derived from it) is a production Worker change; it needs an operator decision and should land together with the gitsafe fix (item 6). **Fixed by Security S1** (`security/worker-path-policy`): PathPolicy, the sandbox mounts, both backends' post-run checks, the planner and the reviewer now apply the ancestor rule (`paths.protected_prefix` / `matching_prefix`). The `.git` alias rule is now one function, `paths.is_git_dir_name` (any case, trailing dots or spaces, HFS+-ignorable code points, `git~N`). `integration_paths.name_problem`, `symlink_problem` and `path_class` use it, together with the Worker's `protected_prefix`, so the integration inspection and the Worker classify `backend/sub/.git/…` and its aliases identically.

## 16. Relation to Issue #67 (W4.6.2: revise and re-review a published Draft PR)

M2 does not depend on #67, and #67 does not depend on M2. The overlaps that must be decided before M4/M6:

- **Authorization model.** #67 needs an explicit revision request bound to the PR, the head SHA and the Issue, and it must never trust PR comments. The signed-record mechanism of §11 fits that need. Proposal: share it, with a namespace per purpose.
- **Repair routing.** The W4.7 design (§6.4, M6) sends a defect to a repair branch and starts a NEW integration job. #67 instead revises the SAME Draft PR. If #67 becomes the review/repair loop for integration jobs, as #67 suggests, §6.4 needs an amendment. Either way, a moved PR head keeps making an integration job STALE: W4.7 never follows a moving head.
- **Evidence invalidation.** #67's "invalidate prior head's evidence and keep history" matches W4.7's SHA-bound, append-only evidence. A common evidence model is advisable before M4.
- **gitsafe.** #67 adds pushes to existing branches by the Worker, so the gitsafe gaps of §14 affect it directly.

## 17. Independent review of M2 (before hand-off) and its corrections

An independent read-only review of the M2 change, not anchored on the author's reasoning, reported:

| # | Finding | Resolution |
| --- | --- | --- |
| 1 | **Critical.** A merge commit inside the source that takes a path unchanged from an old base-side commit (e.g. a `.env` the base deleted, or a workflow file the base hardened) produced no changed path, so the inspection could pass while bringing that content back into `dev`'s tree (reproduced) | The path list also contains the net diff from each merge base (accepted only as a proven boundary parent) to the source tip; regression tests for both cases, and a test that merging newer base commits into the source is not mistaken for the source's change |
| 2 | A symlink to a directory containing protected paths (`-> ../backend` exposes `backend/data/**`) only needed a per-path authorization | Refused; a symlink kept in the source tip must resolve to a regular file of that tree (directories, chains, dangling links refuse) |
| 3 | GitHub-signed ("web-flow") commits counted as verified for their API-settable author | Never verified |
| 4 | An older, broader signed record could be recorded after a narrower one and become effective | Refused; stored approvals must be in issue order |
| 5 | A 404 on the intake PR made the job STALE for good | Deferred |
| 6 | Unverified Git answers (pinned SHA "not a commit", source "contained in base") could force a terminal REFUSED | Deferred |
| 7 | Record validation gaps (path text, class/authorization/role agreement, `paths` not bound to the evidence; an older `passed` entry outliving a newer approval) | Checked on every load; `current_inspection_passed` for M3 |
| 8 | `gpg.ssh.defaultKeyCommand` missing from the strict scan | Added |

## 18. Codex review of M2 (REQUEST_CHANGES) and its corrections

An independent Codex review of M2 reproduced two MAJOR and three MINOR defects. Each fix has permanent regression tests (`test_fc_integration_approve.py`, `test_fc_integration_github.py`, `test_fc_integration_inspect.py`, sections "Codex review F1–F5"). Each fix was also checked by reverting it: its tests fail without it. Codex's own script (`test_m2_independent_adversarial.py`) no longer reproduces any of the five findings.

| # | Finding | Resolution |
| --- | --- | --- |
| F1 | **MAJOR.** `issued_at` lost its fraction of a second, so a broad record at `T.100000Z` and a narrow one at `T.900000Z` compared equal and the older broad record replaced the newer narrow one | Timestamps keep full precision. A new record must be strictly later than every recorded approval of its stage; a different record at the same instant is refused as ambiguous. Replay is idempotent and never revives a superseded record (`effective` in the result). The checks repeat inside the coordinator transaction, including a fresh time check, so concurrent approvals from several processes always leave the newest-issued record effective. Stored approvals must be strictly ordered |
| F2 | **MAJOR.** The PR cross-check compared only PR numbers: the listing could show a PR at another SHA or open while the head/commit lookups showed it at the pinned SHA or closed, and the inspection passed on the listing's snapshot | Every observation of a PR (listing, head lookup, commit lookup, the intake PR read alone) must agree on state, merged, draft, head repository/branch/SHA and base repository/branch; the head lookup must only return the queried head; PR objects must carry every field the rules read. Any disagreement or gap defers, and neither snapshot is used |
| F3 | **MINOR.** Directory entries escaped classification: an added empty `.env` or `.git` tree produced no path, and `backend/src/.env/secret.py` matched no protected pattern | Empty trees are listed as paths, name-checked and classified (an added one is flagged `empty-tree` and refused). Protected and approval-required patterns apply to every ancestor directory, in classification, in authorization records and in symlink targets |
| F4 | **MINOR.** The approval's expiry was checked before the final signature verification and the state transaction, so a slow final check could record `passed` after expiry | Inside the decision transaction: a fresh clock reading against the time window, and the allowed-signers content compared with the content just verified (a revocation meanwhile records nothing). The evidence `at` is the decision time |
| F5 | **MINOR.** A missing `wasSignedByGitHub` defaulted to false, so incomplete attribution could become `verified` | Every requested attribution field must be present; missing, null or contradictory values are malformed and defer. Verification requires explicit `isValid: true`, `state: VALID` and `wasSignedByGitHub: false` |

**Shared causes reviewed.**
- **Approval ordering:** timestamp parsing and the ordering rules were fixed in one place (`parse_record_time`, `ordering_problem`, `_check_approvals`).
- **GitHub consistency:** one snapshot comparison now covers every PR read.
- **Path policy:** one ancestor-aware matcher (`matching_prefix`) feeds classification, authorization and symlink checks.
- **Expiry:** one time-window check (`time_problems`) runs again under the lock in both `approve` and `inspect`.
- **Fail-closed parsing:** M2 code no longer relies on any parser default for an attribution or PR field it judges.

The same ancestor gap existed in the Worker's PathPolicy (§15, item 13). It was outside M2's scope and is fixed separately by Security S1.

**Second Codex review: F2 follow-up.** The second review confirmed F1, F3, F4 and F5 and found one remaining F2 path. The single read of the intake PR (`/pulls/{n}`) was compared with the values pinned at intake *before* the cross-check, and a change it reported made the job STALE at once. The listing, head and commit lookups were then skipped. So one GitHub answer that contradicted the complete listing could end a job for good (reproduced: listing at the pinned SHA, `/pulls/123` at another SHA → STALE).
- **Correction:** the change reported by the single read is only a claim. The inspection runs the same complete lookup (`discover_pull_requests` with that read as an observation, i.e. the existing `cross_check`) at once. Only when every answer agrees is the change STALE. A disagreement, an incomplete listing or a failed lookup defers and records no evidence. The source-ref and base-ref checks are unchanged, and the normal path (an unchanged read) makes the same requests as before.
- `fetch_pull` also requires the `/pulls/{n}` answer to describe PR #n.
- **Tests:** "Codex review F2 (second review)" in `test_fc_integration_inspect.py` (scenarios A–F) and `test_the_single_pr_read_must_answer_for_the_pr_asked_for` in `test_fc_integration_github.py`. Reverting any part of the correction fails them.

## 19. What M3 adds

M3 is the design's milestone 3 (§8.5, phase P3): the isolated integration candidate and the validation gates of the affected roles. It extends the same job, record and state machine; there is no second integration system.

| M3 does | M3 never does |
| --- | --- |
| Builds the merge of `source_sha` into `base_sha` with `git merge-tree --write-tree` in a coordinator-owned repository per job, with a deterministic candidate commit (parents `base_sha`, `source_sha`) | Write the checkout's refs, index, working tree or object contents (Git may only refresh the modification time of an existing object it would otherwise write, §27); rebase, amend, push, force-push, create or edit a PR, review or merge |
| Classifies the candidate's exact change (base tree → candidate tree) through PathPolicy with the effective approval's per-path authorizations | Trust the source history's paths alone (M2 stays authoritative for the history; M3 adds the candidate) |
| Runs the gates of the affected roles in the existing gate sandbox against a materialized checkout of the candidate, and records each result bound to the candidate's head and tree SHAs | Run a repository-controlled hook, filter or merge driver while building the candidate; install dependencies; report a gate that did not run as passed |
| Writes only the coordinator state directory: `integration/<job>.json`, `events.jsonl`, `integration-candidates/<job>/` | Schedule anything for the Worker service, or change `gitsafe.py`, PathPolicy or any role, path or protected-path rule |

## 20. Command (M3)

```bash
# After a passed inspection (§9, step 4):
python -m feature_coordinator integration validate --job int-…            # dry run: read-only, no gate runs
python -m feature_coordinator integration validate --job int-… --apply    # build, run the gates, record
python -m feature_coordinator integration status --job int-…              # candidate, gate results, validation
```

Exit code 0 for `validated` (and for a dry run that found nothing in the way: `would-run-gates`, `would-validate`); 1 otherwise. `--json` as for the other commands. Repeat the same command to resume after a crash, after fixing a blocked gate, or simply to re-check: passed gates are reused, never re-run.

| Outcome | Meaning | Job afterwards |
| --- | --- | --- |
| `validated` | every planned gate PASS or SKIPPED for the candidate | VALIDATING, `validated` — the input of M4 |
| `blocked` | no gate failed, but at least one is BLOCKED (missing, unsafe, tool or dependency unavailable, timeout, error) | VALIDATING with `attention`; fix the cause, repeat |
| `failed` | a required gate FAILED — or any gate FAILED for this candidate earlier, also one no longer in the plan (a recorded failure always stands, §23) | NEEDS_REPAIR with `attention` (failed gates, logs) |
| `conflict` / `refused` | the merge conflicts / the candidate changes a refused path (protected, gitlink, escaping symlink, …) | REFUSED with `attention` |
| `needs-authorization` | the candidate changes an approval-required, unowned, symlink or git-control path the effective approval does not name | INSPECTING with `attention`: sign a new record naming the paths, `approve`, `inspect --apply`, `validate` again |
| `stale` | source or base branch moved, the pinned PR changed (confirmed by the complete lookup), or the rebuilt candidate differs from the recorded one — checked at the start of every run AND again after the gates, before any decision (a move while a gate runs is never validated) | STALE with `attention` |
| `not-inspected`, `no-approval` | no current passed inspection (e.g. a newer approval, a deferral) / the effective approval is invalid now (expired, revoked key, operator removed) | unchanged |
| `issue-blocked`, `deferred` | the Issue is closed or is a pull request / a read could not complete (refs, Issue, PR, objects; also the re-check after the gates: the gate results stay, no decision) | unchanged |
| `changed-meanwhile`, `terminal` | an abort (or another change) landed during the run / the job is terminal | unchanged by this run |

## 21. Candidate construction and Git isolation

**Repository.** `<state>/integration-candidates/<job>/repo.git` is a bare repository written by the coordinator (no `git init`): `HEAD` names an unborn branch and never resolves, `config` is the private configuration of §14, `objects/info/alternates` names the checkout's object directory. It is verified on every run and recreated if anything differs (an extra file, a ref, another alternate): everything in it is derived from the pinned SHAs. New objects (merged trees and blobs, the candidate commit) land only there.

**Merge.** `git merge-tree --write-tree -z --name-only --messages <base_sha> <source_sha>` with Git's own merge defaults (the ort merge GitHub performs; rename and directory-rename detection included). Exit 1 lists the conflicted paths, which are reported; nothing is resolved or patched.

**Candidate commit.** Composed by the coordinator from fixed parts — the merged tree, the parents `base_sha` then `source_sha` (the parents GitHub's merge commit has, design §5.10), the identity `W4.7 integration candidate <integration-candidate@feature-coordinator.invalid>`, the job's creation time as both dates, and a message from the job's immutable inputs — and written with `hash-object`; its SHA is computed independently and must match. Building again from the same inputs gives the same commit, so the candidate is **reproducible**: every run rebuilds it, and a job that recorded a candidate must get exactly the recorded head and tree SHAs again, otherwise its candidate identity changed and the job is STALE (design §4; no evidence transfers). The Git version is recorded with the candidate.

**Isolation (§14, items 1–3).** Every candidate Git process runs with `GIT_CONFIG_NOSYSTEM=1`, `GIT_CONFIG_GLOBAL=/dev/null`, a private empty `HOME` and `XDG_CONFIG_HOME`, `GIT_ATTR_NOSYSTEM=1`, `-c core.attributesFile=/dev/null`, `--attr-source=<empty tree>`, `core.hooksPath=/dev/null`, every transport refused and replace refs, grafts and the commit-graph ignored, and only the subcommands `merge-tree`, `hash-object`, `cat-file`, `read-tree`, `update-index` and `status`. So no `.gitattributes` of the source, the base or the candidate can select a filter, merge driver or diff driver, and none is defined. Reproduced before the fix and kept as a positive-control test: a bare repository whose `HEAD` names a tree with `* merge=evil` runs a merge driver defined in the global configuration during `merge-tree` (Git 2.43 reads a bare repository's attributes from `HEAD`, and from `attr.tree`). Objects are read back hash-verified (`integration_objects`) before they are believed.

| §14 blocker item | Status after M3 |
| --- | --- |
| 1. coordinator-owned repository, never `worktree add` / `checkout` in the checkout | done (above) |
| 2. no content-triggered execution | done for candidate construction (above). Gates run candidate code by design, inside the gate sandbox |
| 3. hooks off, fast-forward push by full SHA to a validated URL | hooks off; M3 never pushes (push is M5) |
| 4. gitsafe's gaps for the Worker | unchanged: M3 does not use gitsafe's scan for safety and does not modify `gitsafe.py` (the Worker change remains a separate operator decision, §15 item 6) |

## 22. Candidate paths, the Issue and the approval

**Paths.** The exact change base tree → candidate tree, read from hash-verified trees, is classified with M2's classifier (additions, modifications, deletions, both sides of exact renames, symlinks and their targets, gitlinks, empty trees, git-control files), against the effective approval's `paths`. The candidate is reclassified because the merge can put content where the source's commits never did: tested with a base that renamed `backend/src/old.py` to `feature_coordinator/old.py` (unowned → needs authorization) or `content/old.py` (protected → REFUSED) after the source forked and edited `old.py` — the inspection saw only `backend/src/old.py`.

**Issue (decision).** Before anything is built, GitHub must report the job's Issue as an **open Issue** of `github.repository` (not a pull request, the number it was asked for). A closed Issue or a PR number → `issue-blocked`, unreadable or 404 → `deferred`; nothing is recorded either way. **The signed operator inspection approval — which binds the Issue number together with the job, the pinned SHAs and the PR — is the authorization for local candidate validation.** No W4.5 planning label or plan approval is required for integration jobs: requiring one would block the approved trusted-development route (§15 item 9 is decided this way). A richer Issue-approval integration can be added later as a separate change.

**Approval at use time.** Validation requires a current passed inspection (`current_inspection_passed`) and re-verifies the effective approval at the start of every run (signature, operator, expiry). The INSPECTING → CANDIDATE_BUILT decision is recorded like an M2 decision: approval re-verified just before the coordinator transaction; inside it, the same approval still effective, its time window checked with a fresh clock reading, the allowed-signers file unchanged, and the checkout's configuration still free of execution settings. Approvals can be recorded only in AWAITING_APPROVAL and INSPECTING (unchanged), so the candidate's approval is the effective one for the rest of the job.

## 23. Gates

**Which.** No second registry; the plan is derived from the existing configuration:

| Changed paths (candidate) | Gates |
| --- | --- |
| owned by role R | `github.intake.role_gates[R]` — backend: `backend-tests`, `backend-lint`, `docs-check`; cli: `cli-lint`, `docs-check`; frontend: `frontend-type-check`, `frontend-build`, `docs-check`; coordination: `docs-check` |
| owned by no role (`feature_coordinator/**`, root files, approval-required paths without a role) | `github.integration.unowned_gates` (new, default `coordinator-tests`; at least one name) |
| always (combined checks) | the feature integration gates `workflow.DEFAULT_INTEGRATION_GATES`: `contract-check`, `docs-check` |

A role without gates (e.g. automation) is the missing gate `role:<name>`; a name that is not configured is missing; a gate in `unsafe_gates` is not run. All three are BLOCKED, never PASS. Gates run in registry order, each once (its `required_by` lists every reason); a FAIL does not stop the remaining gates, so the repair has the full picture.

**Where.** The existing `SandboxedGateRunner` (bubblewrap, read-only tree, no network, private HOME and `/tmp`, timeouts, logs) with the candidate workspace as repository root. A gate's timeout is `github.integration.gate_timeouts[gate]` when listed, else `runtime.gate_timeout_seconds` (900 s); the default mapping is `{coordinator-tests: 1800}` because the Coordinator suite runs longer than 900 s, and giving the key replaces it (whole seconds, 1–86400). The effective timeout is part of the gate's fingerprint; a timeout is BLOCKED, never FAIL or PASS. The workspace `integration-candidates/<job>/worktree` is materialized from hash-verified objects (files byte for byte, executable bits, symlinks; no checkout, filter or eol conversion) with its own `.git` (HEAD detached at the candidate commit, index from `read-tree`, objects through the alternates chain), and `git status` must report it clean before use. Gates therefore see exactly the candidate, and tools that ask Git (`git ls-files` in docs-check, `git status` in the coordinator tests, the lint gates' `git diff base..head` and `git archive base`) work. The sandbox binds exactly two object directories read-only, the candidate repository's and the checkout's (gates on the checkout see its `.git` read-only today); the checkout's own alternates are never followed (a checkout whose object directory names alternates leaves the gates BLOCKED). Every bound checkout directory (`.venv`, `node_modules`) must be a real directory inside the checkout — never a symlink, which a bind would follow on the host — and no bind may contain the coordinator state directory or HOME; such a bind leaves the gate BLOCKED. Lint gates are the baseline-aware D-19 gates comparing the candidate commit with `base_sha`; one without a changed Python file is SKIPPED, and one whose diff (added, modified, renamed) does not list a Python file the candidate adds or changes (a type change, a copy) is BLOCKED.

**Toolchain.** `python` is the checkout's `.venv` interpreter when present (the interpreter the same gate uses on the checkout), else the coordinator's own interpreter — never an interpreter inside the candidate workspace: the plain gate runner's fallback looks for `<repository root>/.venv/bin/python`, which in a candidate workspace is candidate content, so the candidate runner never uses it. The interpreter is recorded in each Python gate's evidence (`toolchain.python`). A gate that runs a known Python tool (`python -m pytest`, `python -m pylint`) first looks the tool up — without importing it, isolated from the current directory, `PYTHONPATH` and the user site — with the same interpreter in the same sandbox: a missing tool is BLOCKED ("the gate tool … is not installed"), never FAIL. Only the tool itself is checked: an import error of the code under test is still a FAIL. A Node gate (`npm`, `npx`, `node`, `pnpm`, `yarn`) in a directory with `package.json` gets the checkout's `node_modules` read-only (a symlink in the workspace) only when the candidate's `package-lock.json` equals the checkout's byte for byte, the candidate's `package.json` declares the same dependencies (`dependencies`, `devDependencies`, `optionalDependencies`, `peerDependencies`, `overrides`, bundled, `workspaces`), and every package of the lock file's `packages` list (optional, dev-optional and peer entries excepted) is present in the checkout's `node_modules`; otherwise — or when `node_modules` is not installed — the gate is BLOCKED. The coordinator never installs dependencies; limitations: §29.

**Evidence.** One `gate` entry per run of a gate: `gate`, `required_by`, `fingerprint` (the gate's definition and timeout), `command`, `result` (PASS, FAIL, SKIPPED, BLOCKED), the runner's `status` (passed, failed, skipped, timeout, blocked, error, missing), `exit_code`, `duration_seconds`, `started_at`, `summary` (log tail), `log` (kept under `integration-candidates/<job>/logs/`), `stdout`/`stderr` `{sha256, bytes}`, `toolchain`, bound to the candidate's head and tree SHAs and digested like every entry. A PASS or SKIPPED with the same fingerprint for the candidate stands and is never re-run. **A FAIL recorded for the candidate stands under any gate definition**: it is never re-run — not after a changed timeout, command or plan — and never dropped when its gate leaves the plan; every later decision is `failed` (the record schema refuses a passed or blocked validation after a FAIL of the same candidate). The candidate's content is immutable, so the resolution is explicit: NEEDS_REPAIR (a repair branch and a new job, M6), or `abort` and a new job (`intake --supersede`) with a new approval, e.g. when the gate itself was wrong. BLOCKED (nothing learned about the content) runs again, and a repeated BLOCKED result of the same gate definition and status is not appended again.

**Attempts.** Before the first gate of a run starts, an `attempt` entry is recorded (write-ahead): the plan (`plan.sha256`, gates and reasons) and the gates the run will run. From that moment an earlier passed decision no longer counts, so a re-validation that crashes — even while its first gate runs, before any result — never leaves an outdated success. An attempt that no decision names is open (running, or interrupted): `status` shows it as OPEN, and the next run resumes it when the plan is the same, or records a new attempt for a changed plan. Repeating a blocked attempt of the same plan writes nothing new; a repeated validation with nothing to run writes nothing. A `validation` entry decides the newest attempt (`attempt`, same plan) over the whole plan (one result per planned gate referring to its gate entry); its result must follow from the gate results and the recorded failures. It is recorded only after the pinned source, base and PR are verified again (a change → STALE, an unreadable read → deferred, no decision), under the same, still valid approval (re-verified, fresh clock, unchanged allowed-signers file) and a checkout configuration that names no program. A job counts as validated (`integration.validated`) only while its newest decision passed, decides the newest attempt, no gate ran after it and no FAIL was recorded for the candidate; `integration_validate.validated_for(job, config)` additionally requires the decision's plan to be the plan the current configuration derives (a changed plan is not validated until `validate --apply` decides it again — M4's starting point). A timeout is BLOCKED, not FAIL: it proves no defect of the source.

## 24. State transitions in M3

```text
INSPECTING (passed) ──validate──▶ CANDIDATE_BUILT ──▶ VALIDATING ──(all PASS/SKIPPED)──▶ VALIDATING, validated  (M4: IN_REVIEW)
     │  conflict / refused path ─▶ REFUSED                │ FAIL ──────────────────────▶ NEEDS_REPAIR
     │  needs authorization ────▶ INSPECTING (attention)  │ BLOCKED ───────────────────▶ VALIDATING (attention)
     └─ moved ref / PR ─────────▶ STALE                   └ moved ref / PR / other candidate ─▶ STALE; refused path on resume ─▶ FAILED
```

No new state and no new edge. Decisions: a merge conflict is detected while building, before CANDIDATE_BUILT is entered, so it uses `INSPECTING → REFUSED` (§6 item 8); `CANDIDATE_BUILT → REFUSED` is used when a resumed run finds the recorded candidate no longer authorized (e.g. the protected-path configuration changed), and from VALIDATING that is FAILED (no REFUSED edge). A failed gate is a defect the source can repair → `NEEDS_REPAIR` (design §6.4, §6 item 9); environment problems stay in VALIDATING as BLOCKED; `FAILED` is reserved for a final, non-repairable problem. `IN_REVIEW` is M4's. The record schema: `candidate` (set once with the transition to CANDIDATE_BUILT, immutable afterwards), evidence kinds `candidate`, `attempt`, `gate`, `validation` (§23). A job in CANDIDATE_BUILT or VALIDATING without a candidate (only possible by editing the record) is refused as conflicting state, like an INSPECTING job without an approval.

## 25. Recovery, idempotence, concurrency

| Situation | Behaviour (tested) |
| --- | --- |
| Crash while building / after building, before recording | job still INSPECTING; the next run rebuilds the same candidate (same commit SHA) and records it |
| Crash after CANDIDATE_BUILT, before VALIDATING | the next run moves to VALIDATING and runs the gates |
| Crash during a gate, or after a gate finished but before its result was recorded | that gate runs again against the same SHAs (design §5.5) within the same, resumed attempt; recorded PASS/SKIPPED results are not repeated, and a recorded FAIL is decided `failed` on the next run — under any later gate definition or plan |
| Crash during a re-validation of a validated job (e.g. a changed gate definition), even while its first gate runs | the attempt recorded before the first gate revokes the earlier success (`validated` is false, `status` shows the attempt OPEN); the next run resumes it, or records a new attempt if the plan changed back or again, and decides anew — the outdated success is never restored |
| Source, base or pinned PR moves while the gates run | the re-check before the decision finds it: STALE, no decision recorded |
| Gate plan changed after a validation (gate added, removed or redefined) | `validated_for` is false at once; `validate --apply` records a new attempt (re-running only gates without matching evidence) and a new decision |
| Candidate repository or workspace lost, tampered or half-built | repository recreated, workspace re-materialized from verified objects; leftovers of an interrupted build removed |
| Repeated `validate` on a validated job | refs, Issue, approval and candidate identity re-verified; every gate reused; nothing written |
| Concurrent runs | `locks/integration-<job>.lock` (shared with `inspect`, non-blocking): a second `validate` or `inspect --apply` of the job is refused; dry runs take no lock |
| Source, base or pinned PR changed | STALE (§20), also from VALIDATING after validation; the workspace is removed, logs kept |
| Operator abort | every decision is recorded only while the job is in the state it was based on: an abort during the gates wins, the running gate's result is discarded, no further gate runs. `abort --apply` removes the candidate repository and workspace; logs stay |

## 26. Decisions taken in M3 that need operator approval

1. **Issue verification and authorization** (§22): open Issue required; the signed inspect approval authorizes local validation; no planning label.
2. **Unowned paths** run `github.integration.unowned_gates` (default `coordinator-tests`), a list of names of the existing gate registry — not a second registry.
3. **The feature integration gates** (`contract-check`, `docs-check`) run for every candidate, as for every feature integration.
4. **Failed gate → NEEDS_REPAIR; blocked or timed-out gate → stays VALIDATING**; conflict → REFUSED from INSPECTING (§24).
5. **Dependencies are never installed**; a Node gate needs the candidate's lock file to equal the checkout's (§23).
6. **The candidate is rebuilt on every run** and must reproduce the recorded head and tree; a difference is STALE.
7. **The approval must still be valid at every run and at every decision.** It cannot be renewed after CANDIDATE_BUILT (approvals stay limited to AWAITING_APPROVAL/INSPECTING); an approval that expires before validation finishes records the gate results but no decision (`no-approval`) — abort and start a new job (`--supersede`). Sign approvals with enough lifetime for inspect and validate.
8. **`integration.py` split** at the 1000-line module limit: the schema checks moved to `integration_schema.py` and are re-exported unchanged.
9. **A recorded FAIL is final for the candidate** (§23): never re-run, never dropped from the decision; the resolution is NEEDS_REPAIR or abort and a new job (Codex review F1).
10. **Per-gate timeouts** `github.integration.gate_timeouts` (default `{coordinator-tests: 1800}`; others keep `runtime.gate_timeout_seconds`, 900 s), part of each gate's fingerprint (Codex review F6).
11. **Missing known Python gate tools are BLOCKED** (pytest, pylint, looked up in the gate sandbox), so missing infrastructure never routes a job to NEEDS_REPAIR (Codex review F4).

## 27. Security review of M3 (HIGH / MEDIUM / LOW)

| Severity | Finding | Status |
| --- | --- | --- |
| HIGH | A merge driver or filter named by the source's `.gitattributes` (or the global attributes file) and defined in the trusted global/system configuration (git-lfs-style) would run during candidate construction; a bare repository with a resolvable `HEAD` also reads the candidate's attributes (reproduced) | **Fixed** by §21's isolation; positive-control and negative tests |
| MEDIUM | Gate sandbox binds followed host symlinks and alternates: a `node_modules` or `.venv` symlink, or an alternates line, planted in the checkout mapped any host directory (HOME, the coordinator state) into the sandbox, readable by candidate code and printed into the gate summary (independent review, finding 1; reproduced there with bubblewrap) | **Fixed**: only real directories inside the checkout, exactly two object directories, no bind containing the state directory or HOME (§23) |
| MEDIUM | `validated` could stay true after gates re-ran and failed but the run was interrupted before deciding (review, finding 2); and a FAIL recorded by such a run would have been re-run — a retry that could turn a flaky failure into a pass | **Fixed**: a gate after the summary invalidates it; recorded FAIL results stand (§23, §25) |
| MEDIUM | Gate toolchain trust: the checkout's `.venv` and `node_modules` are used unverified; with the native-cli backend (not a sandbox) a compromised agent could alter them so gates pass. Unchanged from gates on the live checkout; M4 review and M5's signed merge approval still stand between a passed validation and `dev` | Documented |
| MEDIUM | Without a checkout `.venv`, the gate runner's fallback selected the candidate workspace's own `.venv/bin/python` — candidate content executed as the trusted gate tool (Codex review of M3) | **Fixed**: the checkout's `.venv` or the coordinator's interpreter only (§23, §29) |
| MEDIUM | A candidate that changes Python dependencies is validated against the installed `.venv` (Node changes are BLOCKED instead): a removed requirement still imported would pass | Documented (functional limitation) |
| LOW | Gates run candidate code (by design) in the gate sandbox, which now also binds the candidate's and the checkout's object directories read-only; gates on the checkout already see its whole `.git`; no network | Documented |
| LOW | The lint gate's host-side `git archive base_sha` honours filter drivers of the system configuration for the BASE tree (existing lint-gate behaviour; trusted system configuration; `dev` content) | Documented |
| LOW | An operator abort does not kill the gate already running; its result is discarded and nothing more is recorded | Documented |
| LOW | Resumed runs re-checked the approval only at their start (review, finding 3) | **Fixed**: the validation decision is guarded like an INSPECTING decision (§23) |
| LOW | Building the candidate refreshes the modification time of an object that already exists in the checkout's store (Git "freshens" an object it would otherwise write; also in a dry run). No content, ref, index or working-tree change; it can only delay pruning of unreachable objects (review, finding 4) | Documented |
| LOW | An empty `unowned_gates` disabled the unowned-path gates (review, finding 5) | **Fixed**: at least one gate name is required |
| LOW | Repeated BLOCKED runs with varying log tails filled the 500-entry evidence list (review, finding 6) | **Fixed**: a repeated BLOCKED result of the same gate definition and status is not appended |
| LOW | The shared lint gate's diff lists added, modified and renamed files only, so a Python type change or copy could be SKIPPED (review, finding 7) | **Guarded**: such a candidate's lint gate is BLOCKED (`lint_baseline.py` unchanged) |
| LOW | Schema: the validation entry's `plan.sha256` is not recomputable from the record, and digests are unkeyed (only someone who can already write the state directory could exploit it); an `assert` guarded an invariant (review, finding 8) | `assert` replaced by an explicit check; the rest documented |
| LOW | Merge results depend on the Git version; a different result after a Git upgrade makes the job STALE (fail-safe) and M5 compares GitHub's merge tree with the candidate tree | Documented |

Residual risks of §14 (trusted Git binary, system/global configuration and operator account; Git parsing untrusted objects) apply unchanged.

## 28. Independent review of M3 (before hand-off)

An independent, read-only adversarial review of the M3 change, not anchored on the author's reasoning, reproduced its findings on a snapshot with real bubblewrap where relevant and found no HIGH issue. Every finding and its resolution is in §27; each fix has a regression test in `test_fc_integration_validate.py` (section "independent review of M3"). While writing those tests, one further gap appeared and was fixed: a FAIL recorded by a run interrupted before its decision would have been re-run instead of decided. The review confirmed as sound: the candidate Git isolation, the unborn `HEAD` and the repository verification, the strict `merge-tree` parsing and deterministic commit, STALE on a different rebuild, the workspace materialization (refused names, `O_EXCL|O_NOFOLLOW`, temporary directory and swap, clean `git status`), the shared per-job lock, abort handling, the INSPECTING decision guard, the gate result mapping and the gate plan, the dry run (apart from finding 4), and that `gitsafe.py`, `gates.py`, `sandbox.py`, `paths.py` and `lint_baseline.py` are unchanged.

## 29. Codex functional review of M3 (REQUEST_CHANGES) and its corrections

The independent Codex functional review confirmed candidate construction, the deterministic merge, workspace isolation and the basic state machine, and reproduced six functional defects. Each is corrected with minimal, focused changes in the existing M3 architecture; M1/M2 and Security S1 behaviour is unchanged. Regression tests: `test_fc_integration_validate.py`, section "Codex functional review of M3". Each finding's reproduction tests were also run against a copy with that fix reverted, and fail there (F4's reproduces the review's `FAIL — … No module named pytest`); the other tests listed guard the boundaries (an ordinary import error stays FAIL, the `status` display, the plan check).

| Finding | Correction | Regression tests |
| --- | --- | --- |
| **F1 (HIGH)** A recorded FAIL was re-run after only `gate_timeout_seconds` changed; removing a failing gate from `role_gates` produced `validated` with a current FAIL | A FAIL recorded for the candidate stands under any gate definition (`integration.recorded_failures`, `integration_gates.standing`) and makes every later decision `failed`, also when its gate left the plan; the schema refuses a passed or blocked validation after a FAIL of the same candidate; evidence is never rewritten. A changed plan is not validated until decided again (`validated_for`) | `…f1_a_recorded_fail_is_never_retried_after_a_gate_definition_change` (runtime timeout, per-gate timeout, command), `…f1_removing_a_failed_gate_from_the_plan_never_validates`, `…f1_a_record_that_passes_after_a_recorded_fail_fails_closed`, `…f1_a_changed_gate_plan_is_not_validated_until_decided_again` |
| **F2 (HIGH)** A successful validation stayed valid after a new validation started and crashed | Write-ahead `attempt` entry before the first gate; `validated` requires the newest decision to decide the newest attempt; an open attempt is resumed (same plan) or superseded (changed plan); `status` shows OPEN / decided | `…f2_an_interrupted_revalidation_revokes_the_earlier_success` (crash after the attempt or during the first gate; resume or revert the change), `…f2_status_distinguishes_an_open_attempt_from_a_decided_one` |
| **F3 (HIGH)** The source branch moved while a gate ran and the job still validated | The pinned source, base and PR (M2's `check_pinned`, a single PR read confirmed by the complete lookup) are verified again after the gates, before any decision: a change → STALE, unreadable → deferred without a decision; the approval is re-verified as before | `…f3_a_ref_or_pr_that_moves_during_the_gates_makes_the_job_stale` (source, base, PR head), `…f3_a_failed_gate_of_a_candidate_whose_source_moved_is_stale_not_needs_repair`, `…f3_unreadable_refs_after_the_gates_defer_the_decision` |
| **F4 (MEDIUM)** A missing pytest/Pylint module produced FAIL and NEEDS_REPAIR | `python -m pytest` / `python -m pylint` gates first look the tool up in the sandbox with the same interpreter (isolated): missing → BLOCKED; anything else unchanged | `…f4_a_missing_python_gate_tool_is_blocked_not_failed` (bwrap), `…f4_an_import_error_in_the_tests_is_still_a_failure` (bwrap), `…f4_the_tool_check_maps_only_a_missing_tool_to_blocked` |
| **F5 (MEDIUM)** After an approval expiry the CLI/JSON response showed an older job than the persisted one | Every applied run returns the job as persisted when it ended; text and JSON are rendered from it | `…f5_the_report_after_an_approval_expiry_is_the_persisted_job`, `…f5_cli_text_after_an_approval_expiry_shows_the_persisted_job` |
| **F6 (MEDIUM)** The 900 s default timeout is shorter than the Coordinator suite | `github.integration.gate_timeouts` (default `coordinator-tests: 1800`), effective timeout in the fingerprint, passed to the sandboxed runner; timeout stays BLOCKED | `…f6_coordinator_tests_get_1800_seconds_and_others_keep_the_default`, `…f6_a_per_gate_timeout_is_enforced_as_blocked` (bwrap) |
| MEDIUM — trusted interpreter | Without a checkout `.venv`, the fallback could select the candidate's `.venv/bin/python`; now the checkout's `.venv` or the coordinator's interpreter only, else BLOCKED | `…a_candidate_interpreter_is_never_executed_as_a_gate_tool` |
| Observation — Node dependencies | An identical lock file with other dependency declarations in `package.json`, or a lock-file package missing from the checkout's `node_modules`, is BLOCKED | `…node_gates_need_the_same_declared_and_installed_dependencies` |

**Remaining limitations (documented, not addressed without broader dependency verification).**

- **Installed packages are checked for presence, not for version or integrity**: a `node_modules` package of another version than the lock file names, a lockfileVersion 1 lock file (no `packages` list), yarn/pnpm installs and Python packages in `.venv` are not verified. A candidate that changes Python dependencies is still validated against the installed `.venv` (§27).
- **Reused evidence is about the immutable candidate content and gate definition, not the current toolchain**: a PASS recorded earlier is reused even if a tool has been removed since (the PASS was a real execution against the same content; the interpreter and `node_modules` used are recorded in its `toolchain`). Re-verifying the toolchain at every decision would mean re-running every gate. To validate against a changed toolchain, abort and start a new job.
- **The tool check covers `python -m pytest` and `python -m pylint` only**; other tools reached through a gate command (e.g. a script importing a missing third-party module) still report what the command reports. `npm`, `node` and other programs that are not installed are BLOCKED by the runner as before.
- **Pinned values are re-verified after the gates, not continuously**: a move between that check and the decision's transaction (the approval's signature re-verification and the configuration re-scan run in between, typically well under a second) is not seen by this run; the next `validate` run and M4/M5's own verification of the pinned SHAs catch it.
- A FAIL caused by a wrong gate definition cannot be retried within the same job by design; the operator aborts and supersedes the job.

## 30. What M4 adds

M4 is the design's milestone 4 (§8.5, phase P4): the independent AI review of the EXACT integration candidate that M3 built and validated, with SHA-bound evidence. It extends the same job, record, state machine and append-only evidence; there is no second review system, and the W4.6.1 Draft PR review is unchanged.

| M4 does | M4 never does |
| --- | --- |
| Reviews the diff `base_sha → candidate.head_sha` (the coordinator's candidate merge commit), never `base_sha → source_sha` | Create, edit, comment on, mark ready or merge a PR; push; update any ref; modify the source |
| Re-verifies, before the review, after it and before the decision: the Issue, the pinned refs and PR, the rebuilt candidate (head and tree), its path authorization, M3's validation for the current gate plan, and the signed approval | Accept a review of another commit, another tree, an outdated validation or a changed gate plan as evidence |
| Runs `github.review`'s reviewer (Claude Code on the operator's subscription, no tools) in a separate process per attempt, bounded by `max_attempts`, with write-ahead attempt evidence | Switch to an API key, another model or another provider; implement a Codex reviewer |
| Records `review-attempt`, `review-run` and `review` evidence bound to the candidate's head and tree SHAs; PASS / PASS_WITH_NOTES → AWAITING_MERGE_APPROVAL, REQUEST_CHANGES → NEEDS_REPAIR | Approve or perform the merge (M5), route repairs (M6), revise a PR (Issue #67) |
| Writes only the coordinator state directory (`integration/<job>.json`, `events.jsonl`, `integration-candidates/<job>/`, `runs/review/<job>/`) | Schedule anything for the Worker service; depend on `github.review.enabled` or the Worker's pause |

## 31. Reused W4.6.1 components

| Need | Reused as it is | Change to shared code (behaviour preserved) |
| --- | --- | --- |
| Subscription login, no API key | `reviewer.ClaudeCodeReviewer` (native allowlist environment, `claude auth status` must report claude.ai, post-run stream checks, `apiKeySource: none`, model family) | — |
| Process supervision | `review_process` (request/result files, process TAG, `attempt_processes`, `stop_attempt`, `run_exec`), the supervisor's deadline and cancellation | `run_exec(…, refusal=)`: the caller may decide who may run; default unchanged (`github.review.enabled`) |
| Provider/model selection and eligibility | `github.review` (provider, model, independence, timeout, attempts, limits) | `review_config.reviewer_problem()` extracted from `validate_review` (same messages); M4 checks it at use time |
| Independence modes | `prefer` / `require` (§34) | — |
| Structured output, validation, outcome | `review_result.review_schema`, `validate_output`, `decide` | — |
| Prompt sanitization, confidentiality | `review_context` (change list, `content_guard`, withheld files, `clean`, per-field guard, nonce fences, conventions from the base, final boundary check, `max_diff_characters`) | its readers accept a callable Git reader besides the checkout path (`Root`), so they read the candidate repository |
| Availability without a model request | `review_worker.reviewer_available` | — |

The W4.6.1 tests (`test_fc_review.py`, `test_fc_reviewer.py`, `test_fc_review_worker.py`) pass unchanged.

## 32. Command (M4)

```bash
# After `integration validate --apply` reported `validated`:
python -m feature_coordinator integration review --job int-…             # dry run: every check, the context digest; no reviewer
python -m feature_coordinator integration review --job int-… --apply     # run or recover the review and record
python -m feature_coordinator integration review --job int-… --retry --reason "TEXT" --apply   # next round after ERROR
python -m feature_coordinator integration status --job int-…             # candidate, validation, review, findings, blockers
```

`status` (text and `--json`) calls a review CURRENT (`reviewed: true`) only when `reviewed_for` accepts it under the CURRENT configuration; a review bound to the candidate that the configuration no longer accepts (an independence policy changed to `require`, another gate plan) is shown as HISTORICAL with its verdict and findings, `reviewed: false`, `review_bound: true` and the objection in `review_blocker` (§43 RR2).

Exit code 0 for `reviewed` (PASS or PASS_WITH_NOTES decided, or a decided review re-verified and reused) and for a dry run that found nothing in the way (`would-review`, `would-recover`, `would-decide`); 1 otherwise. `--json` as for the other commands. With `--apply`, SIGTERM/SIGINT stop the reviewer process gracefully (the attempt is given back).

| Outcome | Meaning | Job afterwards |
| --- | --- | --- |
| `reviewed` | PASS / PASS_WITH_NOTES decided for the candidate (findings retained), or a repeated review found the recorded decision still current | AWAITING_MERGE_APPROVAL |
| `changes-requested` | REQUEST_CHANGES: at least one blocker/major finding | NEEDS_REPAIR with `attention`; the source is never modified |
| `error` | the round ended without a valid review: a refused run, or `github.review.max_attempts` counted runs without one | IN_REVIEW with `attention`; `--retry --reason` starts the next round, or abort |
| `stale` / `failed` | a pinned ref, the pinned PR or the candidate identity changed / the candidate is no longer authorized or not buildable from verified objects | STALE / FAILED with `attention` |
| `not-current` | (AWAITING_MERGE_APPROVAL) a check fails that has no edge from there (FAILED), or the recorded review does not apply to the current validation or plan | unchanged; M5 refuses |
| `not-validated` | M3's validation is missing, blocked, failed, open, followed by a new attempt or gate, or decided for another gate plan | unchanged (VALIDATING: run validate; IN_REVIEW: restore the configuration or abort) |
| `no-approval`, `issue-blocked`, `deferred` | the approval is invalid now / the Issue is closed or a PR / a read could not complete | unchanged (a valid answer stays recorded and is decided by the next run) |
| `reviewer-ineligible`, `reviewer-unavailable`, `independence-refused`, `unreviewable`, `cancelled` | not Claude Code or an invalid model / CLI or subscription login missing, or an attempt given back / `require` cannot be met (§34) / the change list, a changed blob or the diff size forbids a complete review / a stop signal | unchanged; no budget used |
| `changed-meanwhile`, `terminal` | an abort (or another change) landed during the run / the job is terminal | unchanged by this run |

## 33. Review context and candidate binding

The reviewer's input is assembled by `integration_review_context.build_candidate_context` from the job's coordinator-owned candidate repository (`integration-candidates/<job>/repo.git`, which holds the candidate commit and, through its one alternate, the checkout's objects) with M3's isolation (no system/global configuration, no attributes, no hooks, no transport) and only `diff`, `ls-tree`, `cat-file` and `log`. Before every review the candidate is REBUILT (M3's deterministic merge) and must be exactly the recorded head and tree.

- **Exactly the validated change.** Git's change list for `base_sha → candidate.head_sha` (both paths of renames) must equal the candidate's verified path list (M3 classified it from hash-verified trees). Every blob of every changed path at the base and in the candidate is read through the hash-verifying reader before the prompt is built (Git's diff does not re-hash objects). A difference or a blob that does not verify refuses the review (`unreviewable`).
- **What the reviewer sees.** The job's identity (job ID, repository, source ref/SHA, base ref/SHA, the pinned PR if any), the reviewed head (the candidate commit; `Reviewed head:` as in W4.6.1) and tree, the Issue's title and body as GitHub reports them at review time (untrusted; digest bound as `spec_sha256`), the operator authorization (approval ID, operator, the explicitly authorized paths), the classified paths (class, role, changes, flags, authorization), the path rules of the touched roles plus the protected and approval-required lists, M3's validation decision and each gate's result and exit code (never gate logs, which candidate code wrote), the known validation limitations, the commit subjects (at most 60), the conventions from the BASE commit, the changed files and the complete diff.
- **Untrusted data and confidentiality.** Everything from the repository, the Issue and commits is fenced as DATA and sanitized exactly as in W4.6.1; the confidentiality guard covers secret files at the base, at the candidate and in every commit between; the complete prompt is checked once more at the boundary. A diff larger than `github.review.max_diff_characters` is never truncated: the review is refused, and the operator splits the source or raises the limit.
- **Binding.** The answer's `head_sha` must be the candidate head (`validate_output`); findings must name changed files with lines inside them. The attempt records the digests of the exact prompt (`input_sha256`), the raw diff and the Issue text; the run records the digests of the raw answer and of the result file. The reviewer output format is unchanged.

## 34. Reviewer process, independence and subscription

- **Process.** Each attempt runs as a separate reviewer process, `python -m feature_coordinator integration review-exec RUN_DIR` (internal), in its own session, with the native environment allowlist plus its process TAG (no GitHub token, no API key). It is `review_process.run_exec` running `ClaudeCodeReviewer` unchanged. It runs only while `github.integration.enabled`, the reviewer is eligible, the run directory and tag are the job's OPEN review attempt and its OWN configuration yields the attempt's recorded reviewer identity (provider, kind, model and `config_sha256`, §42 F3) and its OWN `github.review.independence` accepts the attempt's recorded independence (§43 RR1) — otherwise it writes a `cancelled` result without a model request; it is cancelled by a stop signal or by the job leaving IN_REVIEW (read from the record at most once a second) — never by the Worker's `pause`, so the production Worker can stay paused.
- **Waiting.** `review --apply` waits in the foreground; "ours" is decided by the tag alone. The tagged processes are stopped at the attempt's deadline (`timeout_seconds` + termination grace + 120 s, recorded in the attempt), on SIGTERM/SIGINT of the waiting command, and when the job left IN_REVIEW (abort).
- **Independence.** The source branch existed before the job: its authoring agent, provider and model are not recorded anywhere the coordinator could trust (commit trailers are untrusted text). Independence is therefore always `unknown`, never inferred from a separate process or session, and recorded with its limitation in every attempt and decision. `github.review.independence: require` refuses every integration review (`independence-refused`) — the CURRENT policy is checked before a reviewer starts, before any decision (also of an answer started under `prefer`), when a decided review is reused, and by `reviewed_for` (§42 F2); the reviewer process checks its own policy before it runs, and before a decision the configuration file is read AGAIN (the policy in effect then; the stricter of the run's and the file's decides, an unreadable file confirms nothing) (§43 RR1); `prefer` reviews and records the limitation. The reviewer is still a separate process and a fresh Claude Code session (no session persistence, no tools, an empty working directory).
- **Subscription only.** The reviewer is `github.review`'s Claude Code provider and model; `reviewer_problem` refuses anything else, `reviewer_available` checks the CLI, its flags and the claude.ai login before an attempt is counted, and `ClaudeCodeReviewer` refuses a run that used an API key or another model family; collection refuses an answer whose run facts (the model the reviewer process ran, the model Claude Code reported) are not the attempt's model (§42 F3). `github.review.enabled` (the Worker's automatic Draft PR review) is not required and is not changed.

## 35. Evidence (M4)

Three evidence kinds (ids `rva-`, `rvr-`, `rev-` + position), each digested like every entry and checked on every load (`integration_schema.py`):

| Kind | Recorded | Carries |
| --- | --- | --- |
| `review-attempt` | BEFORE the reviewer process starts (with VALIDATING → IN_REVIEW for the first) | candidate, head/tree SHAs, pinned source/base SHAs, approval, the PASSED validation decision and its plan digest, round, reviewer {provider, kind, model, config_sha256: the digest of provider, kind, model and the provider's executable}, independence {mode, level, author, session, limitation}, context {input, diff and spec digests, files, redactions, coordinator notes}, timeout, run directory, process tag, deadline, the operator's retry (first attempt of a later round) |
| `review-run` | right after the attempt's result is collected — before any freshness check, so a crash never loses an answer | the attempt, status (`ok`, `invalid`, `failed`, `timeout`, `refused`, `cancelled`, `unavailable`, `interrupted`, `not-started`), `counted`, the validated answer (verdict, summary, findings, limitations) and its outcome for `ok`, digests of the raw answer and the result file, error, run facts (auth, CLI version, model, reported model, API-key source, exit code, duration), start/end |
| `review` | the decision, after every freshness check, under the decision guard | the attempt's bindings repeated (candidate, SHAs, approval, validation, plan, round, reviewer, independence, digests) and exactly the decided run's answer; outcome; blocking count; reasons |

Schema rules: a run names the newest attempt, at most one per attempt; an `ok` run's outcome must be `review_result.decide()`'s for its answer and the attempt's notes; `counted` is false exactly for `cancelled`, `unavailable`, `not-started`; a new attempt follows only an ended attempt; a new round only an ERROR decision with a retry; nothing follows a PASS / PASS_WITH_NOTES / REQUEST_CHANGES decision; a decision names the newest attempt and its run and repeats their bindings; PASS / PASS_WITH_NOTES / REQUEST_CHANGES carry the run's answer exactly, ERROR follows a run without one; `blocking` counts blocker/major findings and only REQUEST_CHANGES has any; an attempt with `independence: require` must have established independence; a review attempt — and a PASS / PASS_WITH_NOTES / REQUEST_CHANGES decision — names the validation that is current AT ITS POSITION (the newest validation decision, no validation attempt or gate after it, same candidate head, tree and plan; §42 F4). Evidence is append-only; a terminal record is final.

**For M5 and Issue #67.** `integration.current_review(job)` returns the newest decision only when it is PASS / PASS_WITH_NOTES, bound to the job's candidate head and tree, relies on the NEWEST validation decision, that validation is still `validated` (no gate or attempt after it, no FAIL), no attempt followed it and the job is AWAITING_MERGE_APPROVAL. `integration_review.reviewed_for(job, config)` additionally requires the validation and the decision to match the gate plan the current configuration derives, and the CURRENT `github.review.independence` to accept the decision's recorded independence (§42 F2); `review_eligibility(job, config)` returns the same decision or why the current configuration objects, which `integration status` shows (§43 RR2). M5 must still re-verify the pinned SHAs and rebuild the candidate at merge time (as `review` does). A new candidate SHA is a new job (design §5.5), so a revision always needs fresh validation and review; earlier entries stay as history.

## 36. State transitions in M4

```text
VALIDATING (validated) ──first attempt──▶ IN_REVIEW ──PASS / PASS_WITH_NOTES──▶ AWAITING_MERGE_APPROVAL  (M5)
                                              │ REQUEST_CHANGES ─────────────▶ NEEDS_REPAIR (attention)
                                              │ ERROR (round over) ──────────▶ IN_REVIEW (attention; --retry or abort)
                                              │ moved ref / PR / candidate ──▶ STALE
                                              └ unauthorized / unbuildable ──▶ FAILED
AWAITING_MERGE_APPROVAL ── moved ref / PR / candidate (repeated review) ──▶ STALE
```

No new state and no new edge (design §4: VALIDATING → IN_REVIEW, IN_REVIEW → AWAITING_MERGE_APPROVAL / NEEDS_REPAIR / FAILED / STALE, AWAITING_MERGE_APPROVAL → STALE). `validated()` stays true in IN_REVIEW and AWAITING_MERGE_APPROVAL (no gate can run there). A job in IN_REVIEW cannot be validated, inspected or approved again; there is no edge back to VALIDATING.

## 37. Recovery, idempotence, concurrency

| Situation | Behaviour (tested) |
| --- | --- |
| Crash after the attempt is recorded, before the process starts | the next run finds the open attempt, no process and no CLI output: a `not-started` run (given back, no budget), then a new attempt |
| Crash while the reviewer runs | the reviewer survives in its own session; the next run waits for its tagged processes and collects its result — never a second reviewer for that attempt |
| Crash after the reviewer answered, before the run is recorded | the next run collects `result.json` without a reviewer run |
| Crash after the run is recorded, before the decision | the next run decides the recorded answer after the freshness checks, without a reviewer run (`would-decide` in a dry run) |
| Timeout, invalid or contradictory output, failed run, process gone without a result | a counted run; the next attempt while the budget lasts, else the ERROR decision |
| Refused run (a rule violation, e.g. a tool offered or an API key) | ERROR at once, never retried automatically |
| Reviewer unavailable, cancelled (SIGTERM/SIGINT), not started | given back: no budget used; run again |
| Source, base, pinned PR or candidate changes while reviewing | the run is recorded as history; STALE; no decision |
| Source, base or pinned PR moves during the last checks before a record (candidate rebuild, authorization, the approval's signature) | read again right before the attempt's or the decision's guarded record: STALE, nothing recorded; unreadable → deferred, nothing recorded (§42 F1) |
| `github.review.independence` changed to `require` after an attempt started | the run is collected and kept; no decision (`independence-refused`), a decided review is not reused and `reviewed_for` returns nothing; back to `prefer`: the pending answer is decided, the decided review applies again — the record unchanged (§42 F2) |
| `require` set after the attempt's record, before its reviewer process starts | the process refuses before any model request (its identity is unchanged; its policy refuses): a `cancelled` run, given back; under `require` the next run records no attempt; back to `prefer`: reviewed (§43 RR1) |
| `require` set while the reviewer process runs | it finishes (the request is already spent); the run is recorded as history; the decision reads the configuration again → `independence-refused`, no decision; back to `prefer`: decided without another run (§43 RR1) |
| Reviewer configuration (provider, model, executable) changed between the attempt's record and its reviewer process | the process refuses to run: a `cancelled` run (given back); the next attempt is recorded for the current configuration. A run whose facts name another model → `refused` (ERROR), its answer unused (§42 F3) |
| Approval expires or is revoked while reviewing; Issue closed; validation changes (a new M3 attempt or a changed gate plan) | the run is recorded; no decision; the job stays IN_REVIEW and the answer is decided once the cause is gone (or abort) |
| Concurrent `review --apply` | `locks/integration-<job>.lock` (shared with inspect and validate): the second is refused; one reviewer process at most per job |
| Operator abort while reviewing | the waiting run stops the reviewer and records nothing more; an orphaned reviewer process stops itself (the job left IN_REVIEW); the candidate area is removed, run directories stay |
| Repeated `review` after a decision; repeated `status` | re-verified and reused, nothing written, no reviewer run / read-only |

Worker integration is not implemented (§38 item 6); the attempt evidence, the tag and the deadline are what a later Worker step (M6) needs to watch, stop and collect a reviewer it did not start.

## 38. Decisions taken in M4 that need operator approval

1. **ERROR keeps the job IN_REVIEW with `attention`** instead of FAILED: a reviewer problem (timeout, invalid output, refusal) is not a defect of the source and the validated candidate stays valid; FAILED (terminal) would force a new job, a new approval and new gate runs. An explicit, recorded operator retry (`--retry --reason`) starts the next round of at most `max_attempts`; abort ends it. FAILED is used for a candidate that is no longer authorized or not buildable from verified objects (final).
2. **Independence is always `unknown`** for integration jobs (§34); `require` therefore refuses every integration review. A future signed approval field could declare the authoring agent.
3. **`github.review.enabled` is not required** — the integration review uses `github.review`'s reviewer settings and its own switch, `github.integration.enabled`; enabling integration reviews does not enable the Worker's Draft PR review.
4. **The Issue text is read from GitHub at review time** (integration jobs record no approved spec text); its digest is bound into the attempt and the decision. A change of the Issue text after the review does not invalidate it; a closed Issue blocks the decision.
5. **No PR comment** is published: the evidence lives in the job record only (no GitHub write). A later, explicit and idempotent publication can reuse W4.6.1's operation markers.
6. **Foreground review, no Worker scheduling.** `review --apply` waits for its reviewer process; nothing starts reviews by itself.
7. **Precondition refusals record no evidence** (only an event): no-approval, not-validated, ineligible or unavailable reviewer, independence, unreviewable. Evidence starts with the write-ahead attempt.
8. **A changed gate plan in IN_REVIEW blocks the decision** (no edge back to VALIDATING): restore the configuration, or abort and supersede the job.
9. **`integration.py` stays under the 1000-line module limit**: the M4 evidence checks and their pure helpers are in `integration_schema.py`, the reviewer process in `integration_review_process.py`.

## 39. Relation to Issue #67

M4 implements no revision loop. It preserves what a revision workflow needs: the reviewed candidate (head, tree, validation, plan) on every attempt and decision, the findings with severities and the `blocking` count, REQUEST_CHANGES → NEEDS_REPAIR as the blocking signal, and SHA binding that makes every review of a previous candidate inapplicable (`current_review` / `reviewed_for`). A revised source is a new SHA and therefore a new job (`--supersede`, design §5.5, §6.4) with fresh validation and review; how #67's "revise the SAME Draft PR" maps onto that is still the open question of §16.

## 40. Security review of M4 (HIGH / MEDIUM / LOW)

| Severity | Finding | Status |
| --- | --- | --- |
| MEDIUM | Git's diff reads the checkout's objects through the candidate repository's alternate without re-hashing them: a writer of the checkout's object store could show the reviewer other content than the validated candidate (reproduced: a substituted loose object) | **Fixed**: every blob of every changed path is read hash-verified before the prompt is built (§33); a mismatch refuses the review. Remaining: a writer racing between that check and Git's read (the state directory and checkout must stay operator-only) |
| MEDIUM | The candidate's content can try to steer the reviewer (prompt injection) towards PASS | Documented. Mitigations as W4.6.1: no tools, DATA fences, deterministic validation and outcome; the review is evidence for M5, never sufficient alone: M5 requires the operator's signed merge approval |
| MEDIUM | The reviewer's independence from the source's author cannot be established | Documented (§34); `unknown` is recorded on every attempt and decision, `require` refuses |
| LOW | The prompt (`request.json`, `prompt.txt`) and the CLI output stay in `runs/review/<job>/` after the job ends | Documented (as W4.6.1 R2; mode 0600 in the operator-only state directory) |
| LOW | An abort reaches an orphaned reviewer process within about a second plus the termination grace; the model request in flight is not refunded | Documented |
| LOW | Digests are unkeyed (as M2/M3): only someone who can already write the state directory could forge consistent review evidence | Documented |

| MEDIUM | Codex F1–F3: a late pin movement, a policy changed to `require` and a changed reviewer model could each yield PASS evidence that was not true for the current state | **Fixed** (§42) |
| LOW | Codex F4: the schema accepted a review attempt relying on a revoked validation | **Fixed** (§42) |
| MEDIUM | Codex re-review RR1: the reviewer process admitted an attempt without its current independence policy; a policy changed while the reviewer ran was not seen by the decision | **Fixed** (§43) |
| LOW | Codex re-review RR2: `integration status` showed a review the current policy refuses as CURRENT / `reviewed: true` | **Fixed** (§43) |

No HIGH finding. The reviewer process never receives a GitHub credential, and no M4 code path writes to GitHub, pushes or updates a ref (tested with the scripted transport and checkout/origin snapshots).

## 41. Remaining limitations (M4)

- **Reviewer capacity.** At most `review_context.MAX_FILES` (300) changed files and `max_diff_characters` per review; a larger candidate is refused, not sharded (design §6.3's per-role sharding is not implemented).
- **Empty change.** A candidate whose tree equals the base tree changes no file and is refused as unreviewable.
- **Commit subjects.** At most 60 are shown (context only; the reviewed diff is complete).
- **Foreground only.** The operator's command waits for the reviewer; the Worker does not run integration reviews (M6).
- **Pinned values are read again right before every record, not atomically with it**: a move between that read and the guarded transaction (typically milliseconds) is not seen by this run; the next `review` and M5's re-verification at merge time catch it. There is no GitHub/local-state transaction.
- **A policy changed while the reviewer runs does not stop it**: its request is spent; the answer is kept as history and decided only once the policy accepts it. The configuration is read again right before the decision, not atomically with the record (as the pins).
- **`status` of a pending answer** under `require` still names `integration review --apply` as the next step (which then reports `independence-refused`); only a decided review's eligibility is shown per configuration.
- **The reviewer identity binds the configuration that decides which reviewer runs** (provider, kind, model, executable), not the CLI binary's content: replacing the executable file itself is caught only by the post-run checks (reported model, no API key, no tools).
- **No real reviewer run was made** while developing M4: the tests use scripted reviewer processes and a fake `claude` executable; the first real review is an operator decision (it uses the subscription).

## 42. Codex functional review of M4 (REQUEST_CHANGES) and its corrections

The independent Codex functional review accepted the M4 architecture (the exact candidate diff, head/tree binding, prompt sanitization and withholding, structured output validation, transitions, crash recovery, attempt reuse, concurrency, timeouts, bounded retries, the dry run and JSON output, PR and no-PR jobs, W4.6.1 compatibility) and verified four defects. Each is corrected inside the existing design — no new state, edge, provider or framework; earlier evidence is never rewritten. Regression tests: `test_fc_integration_review_corrections.py` ("Codex functional corrections F1–F4"); Codex's reproduction script (`test_m4_independent_review.py`) was re-run: its seven `test_repro_*` cases, which assert the defective outcomes, now fail at exactly those assertions, and its thirteen checks of behaviour that already worked still pass.

| Finding (functional / security) | Correction | Regression tests |
| --- | --- | --- |
| **F1 (HIGH / MEDIUM)** The final pinned-reference check ran before the candidate rebuild, its authorization and the approval's signature verification: a source branch, base branch or PR moving during those checks was recorded as PASS → AWAITING_MERGE_APPROVAL | `_final_pins`: origin's source and base branches and the pinned PR (M2's `check_pinned`, a PR change confirmed by the complete lookup) are read AGAIN after all other checks, immediately before the guarded record of a new attempt and of every decision: moved → STALE, unreadable → deferred, nothing recorded either way; the abort, approval-window, validation and concurrency guards are unchanged. Not an atomic GitHub/local transaction (§41) | `…f1_a_pin_that_moves_during_the_final_signature_check_is_stale` (source, base, PR × attempt, decision), `…f1_an_unreadable_pin_at_the_final_check_records_nothing_and_never_passes` (attempt, decision; then readable: decided without a new run) |
| **F2 (HIGH / MEDIUM)** An attempt started under `independence: prefer` was decided PASS after the configuration became `require`; a completed review was reused and returned by `reviewed_for` | `integration_review_context.independence_problem(config, recorded)`: the CURRENT policy against the recorded level (never upgraded) before a reviewer starts, before every decision (recovered result, persisted run, interrupted attempt), when a decided review is reused (dry run and `--apply`) and in `reviewed_for`. Refused → `independence-refused`; the answer or decision stays as history; no new edge (an AWAITING_MERGE_APPROVAL job stays, M5 refuses it). A compatible policy again: the pending answer is decided, the decided review applies again | `…f2_an_answer_started_under_prefer_is_not_decided_once_the_policy_requires_independence` (recorded run, interrupted attempt, REQUEST_CHANGES), `…f2_a_completed_review_is_neither_reused_nor_exposed_once_the_policy_requires_independence` (PASS, PASS_WITH_NOTES), `…f2_the_policy_check_never_upgrades_a_recorded_level` |
| **F3 (HIGH / MEDIUM)** The attempt recorded Opus; the configuration changed to Sonnet before the reviewer process started; that process loaded it and ran Sonnet; the PASS evidence named Opus (reproduced through the real CLI with a fake `claude`) | The attempt's reviewer identity carries `config_sha256` (provider, kind, model, the provider's executable). The reviewer process compares its OWN configuration's identity with the attempt's before invoking the model: different → a `cancelled` result, no model request (given back; the next attempt is recorded for the current configuration). Collection compares the run's facts with the attempt: the model the process ran (`model`) must be the attempt's and Claude Code's reported model (`reported_model`, its init event) of that model (`native_backend.model_substitution`, extracted unchanged from the reviewer's own post-run check); different or missing → a `refused` run (ERROR), the answer unused, the facts kept as diagnostics | `…f3_a_reviewer_process_whose_model_changed_before_it_started_does_not_run`, `…f3_an_answer_whose_run_facts_are_not_the_attempts_reviewer_is_refused` (another model, no facts), `…f3_run_facts_must_name_the_attempts_model`, `…f3_crash_recovery_with_an_unchanged_identity_still_reviews`, `…f3_the_reviewer_identity_binds_the_configuration_that_decides_the_run`; real CLI + fake `claude`: `…f3_real_cli_a_model_changed_before_the_reviewer_process_starts_is_never_run_or_relabelled`, `…f3_real_cli_a_model_changed_while_the_reviewer_runs_does_not_change_what_ran`, `…f3_real_cli_claude_code_reporting_another_model_is_refused` |
| **F4 (MEDIUM / LOW)** The schema accepted `passed validation → new M3 attempt → review-attempt naming the old validation` | A review attempt must name the validation that is current at its position: the newest validation decision with no validation attempt or gate after it (`integration.validated()` evaluated on the preceding evidence), for its candidate's head, tree and plan. The same rule holds for a PASS / PASS_WITH_NOTES / REQUEST_CHANGES decision | `…f4_a_review_attempt_relying_on_a_revoked_validation_is_refused_on_load` (open attempt, gate, newer validation), `…f4_a_review_attempt_after_the_current_validation_is_accepted`, `…f4_a_positive_decision_after_a_later_gate_is_refused_on_load` |

**Reverted-fix check.** On a disposable copy (never the worktree) each correction was reverted on its own — the final pin check before the attempt, before the decision; the policy check at the decision, at reuse, in `reviewed_for`, in the dry run; the reviewer process's identity check, the collection's run-facts check, the reported-model requirement, the configuration digest; the schema rule for attempts and for decisions. The unmutated copy passes all 37 tests; each of the 12 reversions makes exactly its finding's tests fail (12 of 12).

**Security classification** (Codex): F1, F2, F3 MEDIUM, F4 LOW — all VERIFIED and fixed; no HIGH. The documented residual limitations of §40 are unchanged; §41 adds the non-atomic final pin read and the identity's scope (configuration, not the executable's content).

## 43. Focused Codex re-review of M4 (REQUEST_CHANGES) and its corrections

The focused Codex re-review verified F1, F3 and F4 as fixed, and F2's recovery, reuse and evidence-access behaviour as fixed; the M4 architecture stays accepted. It verified two remaining defects. Both are corrected with the smallest change inside the existing design — no new state, edge, provider, framework or configuration machinery; the F1–F4 corrections are unchanged; earlier evidence is never rewritten. Regression tests: `test_fc_integration_review_corrections.py` ("Codex functional re-review: RR1 child admission, RR2 status eligibility"). Codex's earlier script (`test_m4_independent_review.py`) has no case for these two findings: targeted reproductions in its style — each asserting the defective outcome — passed on the unfixed code (the reviewer ran and PASS was recorded under `require`; `status` said CURRENT) and fail at exactly those assertions after the corrections; Codex's script itself still gives its earlier result (7 `test_repro_*` fail at the F1–F4 defect assertions, 13 checks pass).

| Finding (functional / security) | Correction | Regression tests |
| --- | --- | --- |
| **RR1 (HIGH / MEDIUM)** Child admission ignored the independence policy: the attempt recorded under `prefer`, the configuration changed to `require`, the reviewer process loaded it, checked only its identity, ran the reviewer, and the waiting side — still on its earlier configuration — recorded PASS. A `require` set while the reviewer ran was not seen either | The reviewer process's admission (`integration_review_process.attempt_problem`, already the identity check) also applies `independence_problem(own configuration, attempt's recorded independence)`: refused → a `cancelled` result before any model request (given back, the diagnostic names the policy). The waiting side checks the policy right before the decision's guarded record, after the final pin check, with `integration_review_context.policy_problem`: its own configuration AND the configuration file read again (`IntegrationReviewer(reload=…)`, the CLI's `--project-config`, which the reviewer process also loads) — the stricter one decides (`require` is never downgraded); an unreadable file confirms nothing. Refused → `independence-refused`, the complete answer stays as history (a pending answer), decided once the policy accepts it | `…rr1_a_require_set_before_the_reviewer_process_starts_never_runs_it` (then recovery: given back, reviewed), `…rr1_admission_checks_the_identity_and_then_the_current_policy` (unchanged `prefer`; identity unchanged but policy changed; identity first), `…rr1_an_unchanged_require_policy_records_no_attempt`, `…rr1_a_require_set_while_the_reviewer_runs_never_becomes_an_authoritative_review`, `…rr1_the_policy_in_effect_is_the_stricter_one_and_an_unreadable_one_confirms_nothing`; real CLI + real `review-exec` + fake `claude`: `…rr1_real_cli_a_require_set_before_the_reviewer_process_starts_never_runs_claude` (the fake `claude` is never invoked), `…rr1_real_cli_a_require_set_while_the_reviewer_runs_keeps_its_answer_as_history` |
| **RR2 (MEDIUM / LOW)** After `prefer` → `require`, `reviewed_for` returned nothing but `integration status` said `CURRENT: valid for this candidate`, `reviewed: true` and named only M5 as the blocker | `integration_review.review_eligibility(job, config)` — `reviewed_for`'s logic, now also saying why the current configuration objects (`reviewed_for` returns its decision). Every job summary the CLI prints or emits (`status`, its list view, and the job shown by the other commands) is completed by it: `reviewed` = eligible under the current configuration, `review_bound` = the record's binding to the candidate (`current_review`), `review_blocker` = the objection. A bound but ineligible review is shown as HISTORICAL with its verdict and findings, its blocker names the objection; no CURRENT and no M5 blocker. Read-only | `…rr2_a_review_the_current_policy_refuses_is_shown_as_history_never_as_current` (PASS, PASS_WITH_NOTES: eligible, then `require`, then `prefer` again; text, JSON and the list view; no state file changes), `…rr2_no_review_and_a_request_changes_review_are_never_eligible`, `…rr2_a_review_whose_binding_is_obsolete_is_never_current` (another gate plan, STALE after the source moved) |

**Reverted-fix check.** On a disposable copy (never the worktree) each correction was reverted on its own — the reviewer process's policy admission; the decision's check against the configuration read again; `policy_problem` ignoring the re-read configuration; the CLI not passing the file to read again; `reviewed` from the record alone; no HISTORICAL label; a blocker without the configuration's objection; a list view that is not configuration-aware. The unmutated copy passes all 49 tests of the module; each of the 8 reversions makes only its finding's tests fail (8 of 8; the F1–F4 tests keep passing).

**Security classification** (Codex): RR1 MEDIUM, RR2 LOW — both VERIFIED and fixed; no HIGH. §40's documented residual limitations are unchanged; §41 adds that a policy changed while the reviewer runs does not stop it (the answer is kept as history) and that the policy is read again right before the decision, not atomically with it.

**Final focused Codex re-review: PASS_WITH_NOTES.** Every functional defect it had reported (F1–F4, RR1, RR2) is verified as fixed; no functional blocker and no verified HIGH or MEDIUM security finding remains. The known limitations are those documented in §40 and §41 (no real reviewer run yet, foreground execution only, the non-atomic re-reads, `unknown` authoring independence for existing branches, the LOW pending-answer `status` suggestion).

## 44. What M5 adds

M5 is the design's milestone 5 (§8.5, phase P5): the operator's signed merge approval, the GitHub merge of the approved PR, and the verified completion. It implements exactly the last two edges of the state machine — AWAITING_MERGE_APPROVAL → MERGED and MERGED → COMPLETED — on the same job record, with the same append-only evidence; there is no new state and no new edge.

| M5 does | M5 never does |
| --- | --- |
| Verifies a SSH-signed **merge approval** (record format version 2, same namespace, allowed-signers file, operators and ordering rules as the inspect approval) bound to the candidate, the M3 validation and M4 review evidence, the PR identity and the merge operation (§46) | Treat a successful AI review, a GitHub comment, label, PR approval or a CLI actor name as an authorization; merge without a valid, unexpired, current signed approval |
| Re-verifies, at merge time, everything the review verified (the same code, `IntegrationReviewer._verify`) plus the review's eligibility, then GitHub's merge requirements and the configured required checks | Rebase, regenerate or silently update the approved SHAs; carry an approval or evidence to another candidate; create a second PR |
| Reuses the one compatible PR, or — only when the signed approval says so — creates ONE (§48) | Retarget, edit, close or mark ready someone else's PR |
| Records the **intent** before each GitHub write (`merge-attempt` evidence), then performs `PUT /pulls/{n}/merge` with `merge_method=merge` and `sha=<approved source SHA>` (§49) | Squash, rebase, fast-forward, push to `dev`, merge locally, auto-merge, or repeat a merge request blindly |
| Reads the actual merge commit and records it as `merge_result`; completes the job only when the post-merge verification passes (§50) | Treat `source_sha` being reachable from `dev` as proof; revert, reset or force-push `dev` after a failed verification |
| Writes only the coordinator state directory (and a private repository under its `tmp/`) and, with `--apply`, the two GitHub writes | Schedule anything for the Worker service (M6), change the production Worker, or write the checkout |

## 45. Commands (M5)

```bash
# After `integration review --apply` reported `reviewed` (the job is AWAITING_MERGE_APPROVAL):
integration status --job int-…                       # candidate, validation, review, blockers, what to sign
# The operator writes the merge record (§46) and signs it with the same key and namespace as an inspect approval:
ssh-keygen -Y sign -f ~/.ssh/<key> -n mx-admin-cloud-integration-approval@feature-coordinator merge-approval.json
python -m feature_coordinator integration approve --job int-… --stage merge --record merge-approval.json [--signature FILE] [--json]
python -m feature_coordinator integration approve --job int-… --stage merge --record merge-approval.json --apply
python -m feature_coordinator integration merge --job int-… [--json]                      # dry run: reads only
python -m feature_coordinator integration merge --job int-… --apply [--actor NAME] [--json]
```

`merge` needs the live GitHub client (`--tracker github`, the default): the local mock tracker cannot merge or verify a merge. `--apply` additionally needs `github.integration.enabled`. Exit code 0 for `completed` (merged and verified, or an already COMPLETED job) and for a dry run that found nothing in the way; 1 otherwise (including `pending-verification`).

| Outcome | Meaning | Job afterwards |
| --- | --- | --- |
| `completed` | merged by GitHub and verified (§50); a repeated command reports it again and writes nothing | COMPLETED |
| `would-merge`, `would-create-pr`, `would-reconcile`, `would-verify` | dry run: every check and read passed; what `--apply` would do (a dry run never creates a PR, merges or writes state) | unchanged |
| `pending-verification` | merged, but a post-merge check is running or not reported, or GitHub/origin could not be read | MERGED (repeat `merge --apply`) |
| `failed` | merged, but not what was approved (parents, tree, containment, a failed check) | FAILED with `attention`: manual remediation (§50) |
| `stale` | origin's source or base branch, the PR head or the candidate moved before the merge; or the PR was merged outside this job | STALE with `attention` |
| `no-approval`, `not-validated`, `not-reviewed` | the signed merge approval is missing, invalid, expired, revoked or for other evidence; M3's validation / M4's review is not current or eligible | unchanged |
| `pr-blocked`, `pr-missing`, `pr-draft`, `merge-blocked`, `checks-failed` | the PR cannot be used or merged now (§48): duplicates, a closed/merged/wrong-base PR, no PR and no authorization to create one, a Draft, GitHub's merge state, a failed required check | unchanged with `attention` |
| `checks-pending`, `deferred`, `issue-blocked` | required checks not successful yet, or GitHub's merge state not computed / a read could not complete / the Issue is not open | unchanged; nothing written |
| `pr-uncertain`, `merge-uncertain` | an ambiguous PR creation / merge request: its outcome is not established | unchanged; the attempt stays OPEN (§52) |
| `merge-rejected`, `github-refused` | GitHub refused the merge / the PR creation (HTTP 4xx) and nothing changed | unchanged with `attention` |
| `changed-meanwhile`, `terminal` | an abort or other change landed during the run / the job is terminal | unchanged by this run |

`integration status --job` shows, for AWAITING_MERGE_APPROVAL, MERGED, COMPLETED and FAILED jobs: the signed merge approval (operator, key, issue time, **expiry**, VALID or why not), the candidate head/tree it binds, the validation and review evidence it binds, the PR identity, whether M3's validation and M4's review are **currently** eligible, the merge result and its post-merge verification, the blockers and the next operator actions. A job is shown as MERGE-READY only when nothing objects (validation and review eligible, a valid signed approval, no write in flight); the merge itself re-verifies everything against GitHub and origin. `status --json` carries the same in `job.merge`.

## 46. The signed merge approval

An inspect approval (version 1) authorizes inspection and validation; it cannot authorize a merge. The merge stage is a **version 2 record** — the same file-and-signature mechanism, a different stage — so existing inspect approvals keep parsing, loading and verifying unchanged.

```json
{"kind": "w47-integration-approval", "version": 2, "job": "int-20261116-0001", "stage": "merge",
 "operator": "spyderbg", "issued_at": "2026-11-16T09:00:00Z", "expires_at": "2026-11-16T21:00:00Z",
 "repository": "spyderbg/mx-admin-cloud",
 "source": {"repo": "spyderbg/mx-admin-cloud", "ref": "integration/x", "sha": "<40 hex>"},
 "base": {"ref": "dev", "sha": "<40 hex>"}, "issue": 65, "pr": null,
 "candidate": {"head_sha": "<40 hex>", "tree_sha": "<40 hex>"},
 "validation": {"evidence": "val-0012", "sha256": "<64 hex>", "plan_sha256": "<64 hex>"},
 "review": {"evidence": "rev-0016", "sha256": "<64 hex>", "outcome": "PASS"},
 "authorizes": {"operation": "merge", "method": "merge", "create_pr": true}, "note": "optional"}
```

`integration status --job` prints the evidence ids and digests to put into the record (they are the `id` and `sha256` of the job's newest validation and review evidence entries; `review.outcome` is PASS or PASS_WITH_NOTES, `validation.plan_sha256` is that validation's `plan.sha256`).

| Check | Refused when |
| --- | --- |
| Form | the M2 rules (one JSON object, no duplicate key, NaN, unknown or missing key, wrong type); a version other than 2 for the merge stage, or stage `merge` in a version 1 record, or `inspect` in a version 2 record; `authorizes` other than `{operation: merge, method: merge, create_pr: bool}` — a squash, a rebase or any other operation cannot be authorized; `create_pr: true` while `pr` is set |
| Identity | exactly the M2 rules: `ssh-keygen -Y verify` against the allowed-signers file for the principal = `operator`, the integration namespace, `operator` in `github.integration.operators`, a trustworthy file; a revoked key authorizes nothing at its next use |
| Binding | `job`, `repository` (= `github.repository`), `source` (repository, ref, SHA), `base` (ref, SHA), `issue` differ from the job's pinned values; `pr` differs from the PR **known** for the job (the PR given at intake, else the PR the inspection decided to reuse, else null); the candidate head or tree differs from the job's candidate; the validation or review evidence is not an entry of this job for this candidate with exactly that id and SHA-256, or is not the NEWEST of its kind (an approval for superseded evidence), the validation is not `passed` for the signed plan, the review is not the signed PASS / PASS_WITH_NOTES |
| Time, order, scope | as for the inspect stage (§11): not in the future, unexpired, at most 7 days, strictly increasing `issued_at` per stage; only a job in AWAITING_MERGE_APPROVAL takes it, and only while M3's validation is current for the gate plan the current configuration derives and M4's review is eligible under the current configuration (`reviewed_for` semantics: independence policy, gate plan) |

The approval entry is appended to the same `approvals` list (ids `apr-NNNN`; an entry of the merge stage carries an extra `merge` key with the signed binding). Ordering, duplicate and replay rules are per stage: a newer merge record supersedes an older one and the older one authorizes nothing; replaying a recorded record is `existing`; concurrent `approve` runs are serialized under the coordinator lock exactly as in §11.

**Effective approval, used again.** `effective_approval_problems(…, stage="merge")` verifies the newest merge approval's signature, operator, key and time window at every use: at the start of `merge`, again right before the decision (a signature check cannot run under the coordinator lock, so the allowed-signers file's digest is compared inside the transaction — a key revoked meanwhile records nothing), and with a fresh clock reading inside the decision transaction. The inspect approval is NOT required to be unexpired at merge time: the merge approval is the operative authorization; the inspect approval's per-path authorizations are static recorded data that the candidate's classification still uses (§22), against the CURRENT path policy. Its signature, key and operator ARE re-verified (as of its own time window, `_inspect_still_verifies`): removing a key from the allowed-signers file revokes every approval made with it, the inspect approval that authorized the candidate's paths included (independent review, L3).

## 47. Order of one `merge` run (apply)

0. **Reconcile first** (§52, §58). If this job has a merge attempt, GitHub is asked whether the PR is merged before anything else: before the approval is looked at and before any staleness check. What it finds is judged by what this job actually did: a merge attempt that was **never dispatched** is closed as a recorded fact (`not-merged`, basis `not-dispatched`; it needs no approval — a never-dispatched PR-creation intent is closed in the creation step, after the approval check); a **dispatched** request whose outcome is unknown ends the run (`merge-uncertain`, later `merge-unresolved` with `attention`) — nothing else is checked and no second request is ever sent over it, however long it takes; a merged PR is recorded as this job's merge only when the facts attribute it to this job (§58, F4), otherwise it is an external merge (STALE).
1. The effective merge approval is valid now and binds the current evidence (§46).
2. The shared checks (`IntegrationReviewer._verify`, inherited — no second implementation): the Issue is an open Issue; origin's source and base branches and the pinned PR are unchanged (`check_pinned`: moved → STALE, unreadable → deferred); the candidate rebuilt from the pinned SHAs is exactly the recorded head and tree (else STALE); its paths are still authorized under the current policy (else `not-current`: AWAITING_MERGE_APPROVAL has no FAILED edge); M3's validation is current for the current gate plan. Plus `review_eligibility`: PASS or PASS_WITH_NOTES, the independence policy and the gate plan under the CURRENT configuration (REQUEST_CHANGES and ERROR never reach this state).
3. The PR (§48), GitHub's merge requirements and the required checks of the PR head (§50, "checks").
4. The pinned branches and PR are read again, and the bound PR itself is read again (still open, from the pinned branch at the approved SHA, into the pinned base — the request pins the head, not the base; a moved head is STALE, a retargeted or closed PR blocks); then the **decision** — one coordinator transaction: the job is still AWAITING_MERGE_APPROVAL (an abort wins before it), the same merge approval is still effective and inside its window at a fresh clock reading, the allowed-signers file is unchanged, M3's validation and M4's review are still current — and the write-ahead `merge-attempt` (the intent) is recorded. No lock is held while GitHub is called.
5. **The write boundary** (§58, §59, §60): the client builds the concrete integration write authority itself, from the trust anchors `integration merge` bound to it (its state directory, checkout and `--project-config` file — nothing the workflow passes can authorize a write), and the authority re-derives the whole authorization from the persisted job under ONE snapshot of the configuration read again from the file: both signatures, the evidence, the intent matched against the request, the candidate's paths classified again, the required checks observed again (merge), other jobs' PR creations for the branch (creation); inside the dispatch transaction the configuration must still be that snapshot, and `merge-dispatch` (carrying the snapshot's fingerprint) is recorded; only then does the request leave. The merge request (§49), the PR read (the answer is never trusted alone), and the record of the result (MERGED).
6. The post-merge verification (§50).

A dry run performs steps 0–3 read-only (the candidate is rebuilt in a temporary repository) and reports what `--apply` would do; for a job that needs a PR it stops at `would-create-pr` because the remaining checks need the PR.

## 48. PR reuse and creation (design §4.1)

The complete lookup is `integration_github.discover_pull_requests` — every PR in every state, strict pagination, the head and commit queries and the single PR read cross-checked — with `decide_reuse`'s rules, unchanged.

| Lookup result | M5 |
| --- | --- |
| exactly one compatible candidate (open, from `source_repo:source_ref` at `source_sha`, into `github.repository:base_ref`, and — if a PR is known — that PR) | reused and bound as `merge-pr` evidence (`origin`: `recorded` = the job's PR, `inspected` = the PR the inspection decided to reuse, `discovered` = found now) |
| the known PR's head, branch or base moved (confirmed by the complete lookup) | STALE |
| none, in any state | ONE PR is created, only when the signed approval has `authorizes.create_pr` (which needs `pr: null`); otherwise `pr-missing`. Not while ANOTHER job's history for the same source branch says a PR may exist that GitHub does not show — a dispatched creation with an unknown outcome (after an abort or a supersession too) or a PR another job recorded: `pr-unresolved`, naming the job and the attempt (§59, F3) |
| more than one candidate; a closed, merged, wrong-base or other-repository candidate; a branch match at another SHA | `pr-blocked` (reasons and numbers in `attention`); **no** PR is created, nothing is retargeted |
| a Draft PR | `pr-draft`: the coordinator never marks someone else's Draft ready for review, never edits it, and sends no merge request — the operator must mark it ready on GitHub, then repeat |
| the lookup cannot complete | `deferred`; nothing is created |

**Binding rule for a PR created after the approval was signed** (§44's question). The approval's `pr` is the PR known when the operator signs; null authorizes "a PR for exactly this head and base, created if none exists" through `authorizes.create_pr`, and never a PR of another identity: a non-null `pr` is merged by that PR number or not at all. The job's immutable inputs (`pr`) are never edited. The PR the coordinator opens (or finds) is recorded as `merge-pr` evidence bound to the approval's record digest; the schema accepts only a PR from the pinned source branch at the pinned SHA into the pinned base, never a draft, at most one per job, and — when the approval names a PR — only that number. The PR is created with a fixed title and body (job, approval id and record digest, candidate; no source or model text) and a marker comment, and `draft: false` (GitHub refuses to merge a draft).

**Creation is idempotent and never repeated on a timer.** The intent (`merge-attempt`, action `create-pr`) is recorded first; the request then passes the write boundary (§58), which records `merge-dispatch`. After a crash between the creation and the record of its number, the next run's lookup finds the PR (recognized as the DISPATCHED attempt's by its marker) and binds it — never a second PR. An intent that was never dispatched (a crash before the boundary) is closed (`not-created`, basis `not-dispatched`) and creation is decided afresh. An ambiguous answer (timeout, HTTP 5xx, a lost response) is not retried: GitHub is searched at once in every state; a PR found is used (a closed one is reported as `pr-blocked`, never ignored and never replaced); none found leaves the attempt OPEN and UNRESOLVED — `pr-uncertain` inside the observation window (`UNCERTAIN_SETTLE_SECONDS`, 120), `pr-unresolved` with `attention` afterwards — and **no second creation is requested however long that takes**: a listing that shows nothing proves nothing (a PR just created may be missing from it, or created and closed). The operator's ways out are to open the PR on GitHub (the next run finds it, closes the attempt as `superseded` and merges that PR) or to abort the job. A definite answer — an HTTP error, or a transport that never sent the request — closes the attempt (`rejected` / `not-sent`) and a later run may try again.

## 49. The merge operation and the client's write restrictions

The only merge mechanism is `PUT /repos/{owner}/{repo}/pulls/{n}/merge` with `merge_method=merge` and `sha=<approved source SHA>`: GitHub refuses it (HTTP 409) if the PR head is no longer that SHA. There is no squash, rebase, fast-forward, direct push, local merge or auto-merge; the request body is fixed in the client, and the schema refuses any attempt whose recorded request is anything else.

The Feature Publisher's rules are unchanged (Draft PRs only, `github.publish.enabled`, never a merge). The client has exactly two integration writes, `create_integration_pull_request` and `merge_integration_pull_request`. Neither takes a grant, a token, an "authorized" flag or an authorization object from its caller (the former public `authorize_integration_write` and the pluggable `WriteAuthority` are removed, Codex F1, §58–§59): each is refused unless `github.integration.enabled`, the client carries the trust anchors `integration merge` bound to it, and the **write boundary** finds the persisted job authorizing exactly that operation now, under the current policy. There is no general-purpose merge method (the client also refuses a merge PUT outside `merge_integration_pull_request` and every GraphQL mutation but the GitHub Projects sync's fixed documents, §61); no credential reaches a reviewer process (`merge` starts none).

**The answer is never trusted alone.** After a response, an error or no response, the PR is read: merged → recorded (outcome `merged`, `merge_result`, MERGED, in one transaction) after the attribution of §58 (F4); open after an explicit refusal (HTTP 403, 405, 409, 422, …, or a transport that never sent) → outcome `not-merged` with basis `rejected` / `not-sent`, `merge-rejected`; open after an uncertain answer, or the PR unreadable → the attempt stays OPEN and UNRESOLVED (`merge-uncertain`, then `merge-unresolved`): the next run reconciles first and **never repeats the request** until GitHub's state shows it did not take effect — which, for an unknown outcome, it never does (§58, F3). A merge request is repeated only after a recorded `not-merged` outcome whose basis is GitHub's error answer, the transport's report that nothing was sent, or the absence of any dispatch. `abort` is refused while a young DISPATCHED merge request may still be in flight (§52).

## 50. Post-merge verification (design §5.10)

The merge commit GitHub wrote is a different object from the candidate commit (its SHA normally differs; only its TREE is expected to equal the candidate tree). The verification (`integration_postmerge.verify_merge`) therefore reads the ACTUAL merge commit and runs every check on every run:

| Check | Passes when |
| --- | --- |
| `pr-merged` | GitHub reports the bound PR merged, into the target repository's base branch, from the pinned source branch at exactly the approved source SHA, with the recorded merge commit |
| `dev-contains` | GitHub's comparison of the merge commit with the base branch's tip is `identical` or `ahead` |
| `commit` | origin's base branch is fetched into a coordinator-owned **private repository** (`isolated_fetch`: only the objects, never the checkout; the same isolation as `ls-remote`), the merge commit is read through the hash-verifying reader (its SHA-1 is recomputed) and is an ancestor of the fetched tip |
| `parents` | the first parent is the approved `base_sha`, the second the approved `source_sha` |
| `tree` | its tree is exactly the approved candidate `tree_sha` |
| `agreement` | GitHub's REST description of the commit (tree, parents) equals the hash-verified Git object — a difference is a failure |
| checks | the configured `github.integration.post_merge_checks` of the merge commit are successful |

`source_sha` being reachable from `dev` never replaces `parents` and `tree`. Results: `pass`; `fail` → the job is **FAILED** (the merge is on `dev` but is not what was approved: operator attention, **manual remediation only** — a reviewed follow-up change or revert through a new job; the coordinator never reverts, resets or force-pushes `dev`, and pushes nothing); `pending` (a check is running or not reported yet; or GitHub says the base branch contains the merge commit while origin's fetched copy does not yet — origin has not caught up) and `unavailable` (GitHub or origin could not be read) → the job stays MERGED and `merge` reports `pending-verification`; `not-configured`.

**Project policy for checks.** The repository has no branch protection and no CI on its current plan (docs_agents/discovery/README.md, SEC-7). `github.integration.required_checks` (the PR head, before the merge) and `post_merge_checks` (the merge commit, before COMPLETED) default to empty: each result is then recorded as `not-configured` — explicitly, and distinct from `pass` and from `unavailable` — no CI result is ever claimed, and the local M3 validation and M4 review are the evidence. When names are configured, a check run or commit status of that name must be successful (newest observation of each source — distinct run ids with one name are legitimate re-runs —, every page; `neutral` and `skipped` count as successful, as on GitHub); one that is running, missing or unreadable is `pending`/`unavailable` (never `pass`), one that failed blocks the merge (pre-merge: `checks-failed`) or fails the verification (post-merge). The observations are untrusted data (§58, F5): an id that occurs twice — contradictory or identical, in one page or across pages — makes the listing unreliable (`unavailable`: the merge defers, the verification stays pending), and no first-or-last entry ever decides. GitHub's own `mergeable_state` is also read before the merge (`dirty`, `blocked`, `draft` block; an uncomputed state waits).

`merge_result` records `commit_sha`, `parents`, `tree_sha`, `pr`, `merged_at`, `merged_by`, the merging `attempt`, `verified` and the `verification` (state, time and every check with its status and detail). The commit identity never changes once recorded; the parents and the tree are filled in once; `verified` is true only for a COMPLETED job — and the schema then demands the **complete evidence** (§58, F6): parents `[base_sha, source_sha]`, tree = candidate tree, every core check (`pr-merged`, `dev-contains`, `commit`, `parents`, `tree`, `agreement`) present exactly once and `pass`, and the check policy evaluated (one `checks` entry that is `not-configured`/`pass`, or one `check NAME` entry per required check, all `pass`). An empty or reduced check list proves nothing; only the check POLICY entry may be `not-configured`.

## 51. State transitions in M5

```text
AWAITING_MERGE_APPROVAL ──merged by GitHub (recorded in one transaction)──▶ MERGED ──verification passed──▶ COMPLETED
        │ moved ref / PR / candidate, or the PR merged by someone else ─▶ STALE        │ pending ─▶ MERGED (repeat)
        │ every other refusal ─▶ unchanged (with `attention` when the operator must act) └ failed ─▶ FAILED
```

No new state and no new edge (design §4). AWAITING_MERGE_APPROVAL has no REFUSED or FAILED edge: a PR that cannot be used, a path that is no longer authorized or an evidence problem leaves the job where it is, with `attention` where an operator must act, and M5 refuses to merge it (as M4 did for `not-current`). A job in MERGED or COMPLETED is never STALE: `dev` moved because of the merge itself.

## 52. Recovery, idempotence and concurrency

| Situation | Behaviour (tested) |
| --- | --- |
| Crash before the merge approval is recorded / after | `approve --apply` is atomic: nothing / the approval is recorded and the next `merge` uses it |
| Crash before the PR creation intent; after the intent, before the request | nothing was written to GitHub; the intent has no `merge-dispatch` — it was provably never sent: the next run closes it (`not-created`, `not-dispatched`) and decides afresh, ONE creation |
| Crash after the PR was created, before its number was recorded | the attempt is DISPATCHED: the next run's lookup finds the PR (the attempt's marker) and binds it; never a second PR. If the lookup cannot see it, the attempt stays unresolved — no second creation (§48) |
| Crash after the merge intent, before the merge request | the intent was never dispatched: the next run reads GitHub first; the PR is not merged → the intent is closed (`not-merged`, `not-dispatched`; needs no approval) and a fresh decision follows with ONE merge request. If the PR was merged meanwhile it was merged by someone else: STALE (§58, F4). A STALE the pinned-value checks would conclude for a job that has a merge attempt is confirmed by reading the PR again: merged → `merge-uncertain`, judged by the next run's reconciliation, never a terminal STALE over a merge this job may have made |
| Crash after GitHub merged, before MERGED was recorded (including a lost response) | the attempt is dispatched; the next run **reconciles first**: the PR is merged at the approved head by the account this coordinator acts as → outcome `merged` (basis `actor`), `merge_result`, MERGED, then verification — NOT STALE although `dev` advanced, and no second request |
| Crash after MERGED was recorded; before COMPLETED was recorded | the next run verifies again (a MERGED job only verifies; it sends nothing to GitHub) and completes |
| A PR merged by someone else (no dispatched request of this job behind it, or another account) | STALE with the merge's details in `attention` (commit, merger, time, `attribution: external`); never COMPLETED, never adopted, nothing reverted. When the facts do not decide (the authenticated or merging account cannot be read): `merge-ambiguous`, the job stays with `attention` |
| Another integration job moved `dev` before the merge | STALE before any write; after our merge it does not matter: the parents are still `base_sha`, `source_sha` |
| Repeated `merge` | after COMPLETED: reported, nothing written; a recorded merge is never repeated |
| Two `merge --apply` for one job | `locks/integration-<job>.lock` (shared with inspect, validate and review): the second is refused; one merge request |
| Operator `abort` | before the decision transaction: it wins, no intent, no write; an intent that was never dispatched does not block it; a DISPATCHED merge request younger than the settle window: **refused** until `merge --apply` has reconciled it (a job must not say ABORTED over a PR GitHub may be merging); older (measured from the DISPATCH, not the intent) and still unresolved: the operator's call, recorded in the abort's reason |
| Approval revoked, expired or superseded meanwhile | re-verified inside the decision transaction: nothing is recorded or written; a newer merge approval is the one used |
| The configuration (operators, signers, integration switch, paths, roles, required checks, …) changes while the write boundary verifies | the write is refused (the configuration inside the dispatch transaction is not the snapshot it was decided under); a fresh run decides under the new policy (§59, F2) |
| A PR creation of an aborted (or superseded, or stale) job was dispatched and its outcome is unknown; a new job for the same branch | no creation by the new job, across restarts: `pr-unresolved` naming the old job and attempt; resolved only by GitHub showing a PR for the branch (the lookup then reuses or refuses it) — the operator can open it by hand (§59, F3) |
| Two successors of one job | intake returns the existing successor; two active jobs never hold one branch (`active_conflicts`); the boundary re-scans other jobs' creations inside its store-wide transaction |

A successful recovery never rewrites the source branch or `dev`: the only change to `dev` is GitHub's merge commit. Fault points for the tests: `after-pr-intent`, `after-pr-created`, `before-merge-request`, `after-merge-request`, `after-merged-record`, `before-completed` (`after-pr-intent` and `before-merge-request` sit BEFORE the write boundary: nothing was dispatched there).

## 53. Configuration (M5)

```yaml
github:
  integration:
    required_checks: []      # check runs / statuses that must be successful on the PR head before the merge
    post_merge_checks: []    # … on the merge commit before COMPLETED
```

Both are optional (empty: `not-configured`, §50). `github.integration.enabled`, `operators`, `allowed_signers` and `trusted_logins` are needed as before; `github.review` is unchanged and `github.review.enabled` is not required. `integration merge --apply` needs `--project-config FILE`: the file is the configuration anchor of the client's write boundary, read again at every GitHub write (§59); `required_checks` is evaluated again there, from a fresh GitHub read, for the merge (not for the PR creation: CI usually runs on the PR).

## 54. Ambiguities in the approved design resolved in M5 (need operator confirmation)

1. **Approval shape.** The design lists a `merge` stage in `approve` and an `approvals` entry with head/tree SHAs; it does not specify the signed content. M5 signs the candidate, the evidence ids and digests, the PR identity and the operation (§46), as a version 2 record — an inspect record can never be mistaken for it.
2. **PR created after the approval was signed.** The approval never names a PR that does not exist; `create_pr` is the explicit authorization, the PR is bound as evidence, the job's inputs are not edited (§48). A PR discovered when the approval names none is reused (it can only be a PR for exactly the approved head and base); an approval signed for PR N never merges another PR.
3. **`REFUSED`/`FAILED` edges do not exist in AWAITING_MERGE_APPROVAL.** §4.1 says a non-reusable PR → REFUSED; the table has no such edge from there. The job stays, with `attention` (§51). A moved PR head is STALE as the design says.
4. **Merge reconciliation and attribution.** A merged PR is this job's merge only when this job DISPATCHED a merge request for it and GitHub shows the merge by the account the coordinator's client is authenticated as; otherwise it is an external merge (STALE) — an intent alone proves nothing (§58, F4). (The design's "ask GitHub whether the PR is merged first" is kept: step 0.)
5. **Where the merge commit is read.** The design says the Worker "reads the actual merge commit"; the checkout is never written, so the merge commit is fetched into a private repository and read through the hash-verifying reader, and cross-checked with GitHub's REST view (§50).
6. **"Required post-merge checks".** None are required by default (no CI on the plan); configured names are enforced; not-configured is recorded, not passed (§50).
7. **Uncertain writes.** An ambiguous PR creation or merge request is NEVER closed by a timer: `UNCERTAIN_SETTLE_SECONDS` (120) is only the minimum observation interval after which the job gets `attention`; the attempt stays unresolved until GitHub shows its result or the operator aborts the job (§48, §49, §58 F3).
8. **`abort` is refused while a young DISPATCHED merge request may be in flight, allowed for an undispatched one and — as the operator's explicit decision — for an unresolved one older than the settle window** (§52) — a change to `abort_job` outside the merge command.
11. **The write boundary and the new evidence** (Codex F1–F4, §58): the former public grant is replaced by an authority that re-derives the authorization from the persisted job at the moment of the write; a new `merge-dispatch` evidence kind and a `basis` on every outcome; the removed time-based closing. These add to the record format of M5 (not yet released).
12. **Attribution by the authenticated account** (§58 F4): GitHub's API does not tell a request of this job from an operator merging the same PR through the same account at the same moment; the residual is documented, not hidden. A marker in the merge commit message (extra `commit_message` in the request) would close it but changes the request body — not done without approval.
13. **Trust anchors instead of a pluggable authority** (§59, F1): the client builds the concrete authority itself; `integration merge` binds the anchors (state directory, checkout, `--project-config` file) once per client; any other client sends no integration write. The client API changed (`bind_integration`; `integration_writes` and `WriteAuthority` removed).
14. **One policy snapshot per write, the WHOLE effective configuration** (§59, F2): any configuration change during the boundary's verification refuses the write — also an unrelated one (a timeout); the operator repeats the command. `merge-dispatch` records the snapshot's fingerprint (`policy_sha256`, an M5 record-format addition).
15. **The boundary classifies paths and observes checks itself** (§59, F7): the candidate's change re-derived from verified objects AND M2's history-wide path list, under the current configuration; required checks for the merge only (not a condition of opening the PR).
16. **Cross-job creation history blocks creation** (§59, F3): a dispatched creation with an unknown outcome in any job for the same source branch (any SHA, any state) blocks new creations until GitHub shows a PR for the branch; there is no command that clears it — the resolution is an observed PR (opened by hand if need be).
17. **The route is part of what is authorized** (§60, F1): `github_api.integration_route` builds the method and path of an integration write from exact values (the configured repository, the intent's PR); the boundary compares it before recording anything; the request core sends only the one granted request. The client's other methods (reads, Issue sync, the publisher) keep `repo_path`, unchanged.
18. **The boundary re-derives the source history from the checkout's verified objects** (§60, F7a) with M2's own derivation (extracted into `integration_inspect.history_changes`, which M2 now calls too); a checkout that no longer holds the source's objects refuses the write. M2's rule for a symlink kept in the source tip (it must resolve to a regular file of that tree) now holds in every commit of the range in which a symlink exists — M2 refuses such histories at inspection too (stricter than before: a history-only symlink to a directory, a symlink, or nothing).
19. **The current owning roles derive the gate plan everywhere** (§60, F7b): `validated_for` and `review_eligibility` — and so M4, `status` and the merge-approval readiness — classify the recorded candidate paths under the current configuration (the boundary uses its own classification from verified objects). A role change after validation makes the validation not current (`not-validated` in M4 and M5; a refusal at the boundary), exactly as a changed gate definition already did; nothing is re-run or replaced.
20. **GraphQL writes are an allowlist of fixed documents** (§61): `GitHubClient.graphql()` sends a document containing `mutation` only when it is, character for character, one of the Projects sync's seven documents (`github_projects.MUTATIONS`, pinned by SHA-256 in the client); adding a Projects mutation means adding its document there and its digest to `github_api._ALLOWED_MUTATIONS`.
9. **The inspect approval's expiry does not block the merge, its revocation does** (§46).
10. **Existing tests changed** (the M5 semantics replaced two M2 assertions): `test_fc_integration_approve.py` expected a version 1 record with stage `merge` to be refused with "merge stage is M5" and `--stage merge` to exit 2; they now expect the new message and an unknown `--stage` value to exit 2. No M3 or M4 assertion changed.

## 55. Security review of M5 (HIGH / MEDIUM / LOW)

| Severity | Finding | Status |
| --- | --- | --- |
| — | **No HIGH finding known.** A merge without a signed approval, a merge of another source or candidate, a merge approval accepted for other evidence or another job, an unauthorized write to `dev` and a credential reaching a reviewer were each checked by a test (§56) | — |
| MEDIUM | The merge call pins the PR head (`sha`) but not the base: if `dev` moves between the final read and the request, GitHub merges onto the new base | Detected after the fact: the verification requires the first parent to be `base_sha` → FAILED, operator remediation (design §5.9) |
| MEDIUM | `abort` and the merge are two commands: an abort between the decision and the request is refused only once the intent is recorded; the window between the final pin read and the decision transaction is not atomic with GitHub | Documented (§57): the decision transaction re-checks the state; nothing is written to GitHub before the intent |
| MEDIUM | (was) A merge request closed `not-merged` by a timer could still merge the PR later, and a repeated request could follow | **Fixed (Codex F3, §58):** no timer ever closes a dispatched request; an outcome is final and needs GitHub's state as its basis |
| LOW | The post-merge verification needs origin readable by the coordinator (a fetch); an origin that is unreachable keeps the job MERGED | By design: pending, never a pass |
| LOW | Digests are unkeyed (as M2–M4): only someone who can write the state directory could forge consistent merge evidence | Documented |
| LOW | `merge_result.merged_by` is GitHub's report, not an identity the coordinator verified | Informational only |

### Independent review of M5 (before hand-off)

An independent, adversarial read-only review of the working tree (a fresh agent given the requirements and the code, not the author's conclusions) found **no HIGH** finding and reproduced four MEDIUM and several LOW ones. All four MEDIUM and L1–L4 are fixed; regression tests in `test_fc_integration_merge_independent.py` (and `…_approval.py` for M4).

| Severity | Finding | Resolution |
| --- | --- | --- |
| MEDIUM (M1) | A PR created before a crash, followed by a NEWER merge approval, could never be bound: the schema demanded the same approval for the PR and for its create-pr attempt, so every retry failed and the job could only be aborted | A created PR may name a create-pr attempt made under any merge approval of the job (the marker names the attempt's approval) |
| MEDIUM (M2) | A merge request still in flight could land after the reconcile read and before the pinned-value check: `dev` moved and the job became terminal STALE over a merged PR | A young open attempt ends the run right after the reconcile read; every STALE of a job that has sent a merge request is confirmed by reading the PR again (merged → `merge-uncertain`) |
| MEDIUM (M3) | The bound PR was not read again right before the merge request: a PR retargeted after the lookup would be merged into the other branch (FAILED afterwards, the other branch already modified) | The PR is read again after the final pin read: a moved head is STALE; a retargeted, closed or merged PR blocks |
| MEDIUM (M4) | The schema accepted a COMPLETED / MERGED job with a minimal `merge_result` and no verification | A job with a candidate that reached MERGED must carry the complete `merge_result` (the minimal shape stays valid only for a record without a candidate: state-machine unit records) |
| LOW (L1) | A post-merge check name of 95–100 characters wedged a MERGED job (the `check ` prefix exceeded the schema's cap) | The cap is 120 |
| LOW (L2) | GitHub already containing the merge commit while origin's copy lagged was a terminal FAILED | `pending` until origin has caught up |
| LOW (L3) | The inspect approval's key was not re-verified at merge time | Re-verified (signature, key, operator; not its expiry) |
| LOW (L4) | `status` could say MERGE-READY while the job carried `attention` | Attention is a blocker |
| LOW (L5–L7) | (L5 was) `authorize_integration_write` was a public method that carried no proof of an approval — **removed (Codex F1, §58)**; an abort between the PR-creation intent and the request is allowed; evidence digests are unkeyed | L5 fixed; the others documented |
| — | No check, before the merge, that GitHub's would-merge tree equals the candidate tree | Not implemented: the difference is detected after the merge (FAILED), §50; branch protection "require up to date" is the real control |

## 56. Tests

`fc_integration_merge_rig.py` scripts GitHub behind the REAL `GitHubClient` and performs a REAL merge with Git plumbing on the disposable bare `origin` (merge, squash, wrong tree, wrong parent), with failure injection (refusals, timeouts, lost responses, unreadable routes, hidden PRs). No real GitHub, no network, no real repository merge, no model call. The end-to-end test drives M1 intake → M2 inspection → M3 validation (recording fake gate runner) → M4 review (fake reviewer process) → M5 signed approval → merge → verified completion. The Codex-review corrections (§58) have their own files: `test_fc_integration_merge_authority.py` (F1, F2), `…_unknown_outcome.py` (F3, F4) and `…_verification_evidence.py` (F5, F6); the focused re-review's (§59) are in `test_fc_integration_merge_boundary.py` (F1, F2, F3, F7); the final re-review's (§60) in `test_fc_integration_merge_destination.py` (F1, routing) and `…_current_policy.py` (F7a, F7b); the GraphQL allowlist (§61) in `test_fc_github_graphql_allowlist.py`. **The merge was NOT tested against the real GitHub service**: the scripted GitHub follows GitHub's documented REST contract; the first real merge is an operator decision.

## 57. Remaining limitations (M5)

- **Foreground only.** The operator's command performs the merge; the Worker does not schedule it (M6).
- **Real GitHub behaviour is assumed from the documented REST contract** (merge response, `mergeable_state` values, check-run and status shapes); the first run against it may need small adjustments.
- **Non-atomic reads.** Pinned values are read again right before the decision, not atomically with it (§41's limitation applies).
- **Checks are matched by name** and read as the newest observation per name and source; matrix or renamed checks need their names configured.
- **An abort between the PR-creation intent and the creation request is allowed**, and so is an abort of a job whose creation outcome is unknown: a PR created by a request already on its way stays unbound — it is never merged by that job, and no other job creates a second PR for the branch while the outcome is unknown (§59, F3).
- **An unknown outcome needs the operator.** A PR creation or merge request whose answer was lost and which GitHub never shows is not retried by any timer (§58, F3): the job stays with `attention` until the result appears, the operator opens/merges the PR by hand, or the job is aborted and a new one started.
- **Attribution is by the authenticated account** (§58, F4): a dispatched request with a lost answer and an operator merging the same PR at the same moment through the same account cannot be told apart by anything GitHub's API offers; what is adopted is still verified to be exactly the approved merge (parents, tree).
- **The write boundary protects the supported API** — a holder of a GitHub client, or any caller of its write methods, cannot authorize a write — **and the workflow against its own stale policy**, not against arbitrary code running inside the coordinator process (which can replace the client, the authority or the transport). The anchors are as trustworthy as the command that binds them: a client built with another state directory and configuration is another coordinator (§59).
- **The client's reads still use its public `repo_path`** (§60): a caller who changes it makes the workflow read another repository's Issue (the M4/M5 open-Issue check); no write follows from it — every integration write goes to the route its boundary authorized, and the workflow's PR, check, branch and commit reads name the configured repository explicitly.
- **GraphQL writes are the Projects sync's seven fixed documents** (§61): a new Projects mutation needs its document in `github_projects.MUTATIONS` and its SHA-256 pinned in `github_api._ALLOWED_MUTATIONS`; any other GraphQL mutation is refused by the client.
- **External races at dispatch.** A configuration or allowed-signers change after the boundary's final re-read (microseconds before the dispatch record) applies from the next write; a required check that flips after the boundary read it, or a base branch that moves, is GitHub's to enforce (§59).
- **One merge per job; no auto-merge.** A failed verification is manual remediation (a new job), as designed.
- **The signed `merge` record is written by hand** (JSON + `ssh-keygen -Y sign`); a helper to compose it from `status` is a convenience for later.

## 58. Corrections after the independent Codex review of M5 (F1–F6)

Codex's functional review (REQUEST_CHANGES) reproduced six defects. None changes the architecture (the state machine, the signed approval formats, `merge_method=merge`, candidate binding, the post-merge parent/tree verification and the Feature Publisher are unchanged); each is a missing check at a boundary. Evidence for the corrections: §56, and the mutation experiments (each fix reverted separately in a disposable copy: the new tests fail without it).

### F1 (HIGH) — the write boundary replaces the public grant

*(The mechanism described in this subsection — an authority the workflow hands to the client with `integration_writes`, and `reload_config` — was replaced after the focused re-review: §59. The checks it lists still apply, extended there.)*

`authorize_integration_write(job-shaped facts)` accepted enabled integration and syntactically valid arguments, and nothing proved a signed approval: PR creation and merge could be sent through the public client API. It is **removed**. The write methods now take the id of a persisted intent (`attempt`) and nothing that claims authorization, and ask the **integration write authority** (`integration_authority.IntegrationWriteAuthority`) the client runs under (`GitHubClient.integration_writes`, bound for one workflow run only, not reentrant; without it both writes raise `IntegrationWriteRefused` and send nothing). At the write boundary — after the request is fully formed, immediately before it is sent — the authority derives the authorization from **persisted state and current policy**:

1. the project configuration read again from its file (`reload_config`; the CLI passes the file the command was started with): integration enabled, the configured repository the one the client writes to;
2. the job read again: AWAITING_MERGE_APPROVAL with its candidate;
3. the effective **merge** approval re-verified (signature, operator, namespace, key against the allowed-signers file, time window at a fresh clock, binding to this candidate and to the validation/review evidence), and the **inspect** approval the candidate was built under re-verified (signature, key, operator; not its expiry — §46);
4. M3's validation and M4's review still current under the reloaded configuration's gate plan;
5. the persisted **intent** (`merge-attempt`): the newest, still open, never dispatched, repeating the effective approval, the candidate and the pinned SHAs, and naming exactly this operation — `create-pr`: the job's head and base, `draft: false`, an approval that authorizes it, no PR bound yet; `merge`: the one bound PR, `merge_method` merge and the approved source SHA;
6. in one coordinator transaction: the cheap checks again, the allowed-signers file unchanged since the signatures were verified, and then the `merge-dispatch` evidence is appended — the **one-shot** (a second request for the attempt is refused, across processes and restarts).

`create-pr` and `merge` are distinct scopes, each tied to its own intent. Anything that cannot be established (a failing reload, an unreadable file, an exception) is a refusal — the boundary fails closed. Defence in depth in the client (independent review of the fixes): `integration_writes` accepts only a `WriteAuthority` (a duck-typed object with an `authorize` method is refused), the merge PUT is refused anywhere but inside `merge_integration_pull_request`, GraphQL merge mutations are refused, and each integration write is **one transmission** — no resend inside the request core after a rate limit or a transport failure (a resend would be a second request without a second authorization; the next run decides afresh). The boundary re-derives the approvals, operators, signers, evidence currency (validation and review under the reloaded configuration's gate plan) and the intent; the candidate's path classification and the configured `required_checks` are workflow checks made just before and are not repeated at the boundary. **Residual (stated, not claimed away):** code running inside the coordinator process can replace the client, the authority or the transport; the boundary protects against a caller that merely holds a client and calls its public methods, and against the workflow's stale view of the policy, not against arbitrary code execution in the trusted process. An exception after the dispatch entry was saved (a failing audit-event append) leaves the attempt dispatched although nothing was sent: unresolved, never duplicated, the operator aborts it.

### F2 (HIGH) — the policy is read again, for both writes

The workflow's final check used the configuration the run started with, and verified only the merge approval. At the write boundary (above) the configuration FILE is read again (operators, integration switch, allowed-signers path), both approvals are verified again against the current allowed-signers file, the expiry is judged at a fresh clock and a change of the allowed-signers file between the verification and the transaction (or between the two signatures) stops the write. An unreadable configuration or signer file is a refusal with no cached fallback — and a merge run constructed without a `reload_config` source refuses every GitHub write (the CLI passes the configuration file; without `--project-config` the default configuration has integration disabled anyway). The documented exception stays: an inspect approval that merely EXPIRED still counts; a revoked inspect key does not. The same boundary protects PR creation.

### F3 (MEDIUM) — a timer is not an observation

Four situations are kept apart (`integration_merge_recovery`): **never dispatched** (no `merge-dispatch` entry: the request provably never left — closed as `not-merged`/`not-created` with basis `not-dispatched`, or `not-sent` when the transport reports it), **submitted, confirmed failure** (GitHub answered with an error: basis `rejected`), **submitted, confirmed success** (the merge: basis `response` or `actor`) and **submitted, unknown** (dispatched, no answer). An unknown outcome is closed by nothing but GitHub's state; `UNCERTAIN_SETTLE_SECONDS` (120) stays as the minimum observation interval (`*-uncertain`, then `*-unresolved` with `attention`). The schema enforces it: every outcome carries a `basis` (`response`/`actor` for merged; `not-dispatched`/`rejected`/`not-sent`/`superseded` otherwise), `not-dispatched` is impossible with a dispatch entry, every other basis needs one, an outcome is final (no "merged after not-merged"), and a dispatch is once per attempt and never after the attempt ended. PR creation searches every state (a closed PR created by the lost request is reported, `pr-blocked`, never ignored or replaced). `status` shows the dispatch state of the open attempt.

### F4 (MEDIUM) — an external merge is not this job's

The old reconciliation adopted any merged PR for which an intent existed. Now the verdict (`own` / `external` / `ambiguous`) needs facts: the attempt must be **dispatched** (an intent is not proof a request was sent), GitHub must show the merge by the account the client is authenticated as (`GET /user`), and a merge answered by GitHub's own response is certain. External (no dispatch, a rejected request, another account) → **STALE** with the merge's details in `attention` (`merge_commit`, `merged_by`, `merged_at`, `pr`, `attribution: external`), whatever its parents or tree (nothing of this job's is wrong, so not FAILED); nothing is reverted or rewritten. Ambiguous (the authenticated or merging account cannot be read) → `merge-ambiguous`: the job stays with `attention`, nothing is sent or completed. Genuine merges followed by a crash or a lost response stay recoverable (basis `actor`). **Residual:** GitHub's API cannot tell a dispatched request whose answer was lost from an operator merging the same PR at the same instant through the same account; what is adopted is still verified to be exactly the approved merge (decision 12, §54).

### F5 (MEDIUM) — check observations are untrusted data

An id that occurs twice (contradictory or identical, in one page or across pages; check runs and statuses are separate id spaces) is `GitHubResponseError` — the listing is not a faithful enumeration (a repeated id can hide a missing run behind a matching `total_count`): the merge defers (`checks-pending`, the check `unavailable`), the verification stays pending. Ids must be positive integers, names non-empty, and an observation that names another commit is refused. Distinct ids with the same name remain legitimate re-runs (the newest of each source decides). Nothing is ever read as `pass` from an unreliable listing.

### F6 (LOW, functional blocker) — COMPLETED needs complete evidence

`all([])` is true: a COMPLETED record whose verification checks were removed loaded. `completion_problems` now requires every core check (`pr-merged`, `dev-contains`, `commit`, `parents`, `tree`, `agreement`) exactly once and `pass`, and the check policy evaluated (exactly one `checks` entry that is `not-configured` — the only place that status is allowed — or `pass`, or one `check NAME` entry per required check, all `pass`); unknown names and duplicates are refused. Pending (MERGED) and FAILED records are unaffected; M1–M4 records have no `merge_result` and load as before.

### Independent review of the F1–F6 fixes

A second fresh-agent adversarial review (read-only) found no HIGH defect and these; all but L2 (documentation) and L4/L5 (decisions) are fixed, with regression tests and mutation proof.

| Severity | Finding | Resolution |
| --- | --- | --- |
| MEDIUM (M1) | An HTTP 5xx whose body is not JSON reaches the merge as `GitHubResponseError(status=502)` (the transport raises before the request core can class it as an uncertain write); `status is not None` was read as "GitHub rejected it", so a PUT that had landed became a terminal STALE ("external"), and a landed POST was closed as `not-created` | Only an HTTP **4xx** answer is a definite rejection; a 5xx (even with an unreadable body), an unreadable 2xx and a lost answer stay unknown |
| MEDIUM (M2) | `integration_writes` accepted any object with an `authorize` method; `client._call("PUT", …/merge)` and a GraphQL `mergePullRequest` mutation were not gated | `WriteAuthority` base class required; merge PUT outside the one method and GraphQL merge mutations refused; the residual (arbitrary in-process code) is stated |
| LOW-MEDIUM (M3) | The request core resent a rate-limited PUT after the dispatch with no new authorization (an abort or key revocation in the wait was not seen); the abort window was measured from the intent | Integration writes are one transmission (`retry=False`); the window starts at the dispatch |
| LOW-MEDIUM (L1) | Post-merge verification ran right after the merge: a lagging read (PR without a merge commit, PR not yet merged, branch tip behind the merge commit) became a terminal FAILED | `pending` for those; `behind` becomes a failure only when origin's branch (the git server) does not hold the merge commit either (moved back or rewritten); a contradiction (another base, head or non-null merge commit; `diverged`) still fails |
| LOW (L2) | The boundary does not repeat the candidate path classification or `required_checks` | Was documented; **fixed after the focused re-review (§59, F7)**: the boundary classifies the paths and observes the checks itself |
| LOW (L3) | Without a `reload_config` the startup configuration stayed in force | A merge run without a source refuses every write (§59: a client without anchors sends no integration write) |
| LOW (L4) | Attribution has no clock sanity check (`merged_at` before the dispatch) | Not done: GitHub's clock and the coordinator's can differ by more than any safe tolerance, and a false EXTERNAL verdict is a terminal STALE over the job's own merge; the same-account residual is documented (§57) |
| LOW (L5) | A dispatch can be recorded although nothing was sent (an exception after the save) | Conservative by design (no duplicate write); the operator aborts the unresolved attempt; documented |

## 59. The focused Codex re-review of M5: the remaining blockers (F1, F2, F3, F7)

The re-review verified F4–F6 as fixed and found one bypass left in each of F1–F3, plus a new finding, F7. The corrections consolidate the final authorization into ONE boundary (`integration_authority.py`); the state machine, the signed approval formats, `merge_method=merge`, candidate binding, crash recovery, the post-merge verification and the Feature Publisher are unchanged.

### F1 (HIGH) — no caller-controlled authority

`GitHubClient.integration_writes(authority)` accepted any `WriteAuthority` subclass: a caller's `authorize()` that approved everything sent a PR creation and a merge with fabricated job and attempt IDs, no signed approval, no intent and no dispatch. The pluggable authority is **removed** (`WriteAuthority` and `integration_writes` no longer exist). The client builds the concrete `IntegrationWriteAuthority` **itself**, for each write, from **trust anchors** bound to it once — by the `integration merge` command, while it builds its one client for the run, through the client's PRIVATE `_bind_integration` (exactly an `IntegrationAnchors`; never replaced; a client that already carries anchors is refused by the command):

- the coordinator **state directory** whose persisted jobs authorize writes (an unknown job is a refusal);
- the **checkout** (the signature verifier's repository; the candidate objects);
- the **project configuration source** — for `integration merge`, the `--project-config` FILE, read again at every write.

The client's public API has no way to bind, replace or supply anchors: every other client (issue sync, publisher, board, …) sends no integration write, and neither does a merge run without `--project-config`. No parameter of any write accepts an authorization, a verifier, a clock or a policy; the signature verifier is always the real `SshSignatureVerifier` for the allowed-signers file the current configuration names, and the boundary reads the real clock. The client object is closed around its writes too (independent review below): its transport is private; on the parsed route (no query string or trailing slash slips past) a PR merge PUT is refused outside `merge_integration_pull_request`, a PR creation POST outside the publisher's Draft creation and `create_integration_pull_request`, and GitHub's branch-merge endpoint (`POST /merges`) always; GraphQL merge, merge-queue (`enqueuePullRequest`), auto-merge and PR-creation (`createPullRequest`) mutations are refused; and the facts of a write must be plain `str` / `int` values (an `int` subclass that compares equal to the authorized PR number but converts to another cannot redirect the request). Boundary stated honestly: this protects the supported API — a holder of a client, or any caller of its public methods, cannot authorize a write, choose or replace the anchors, or reach the transport. It does not protect against arbitrary code running in the coordinator process (code that calls private members such as `_bind_integration` or `_call`, replaces the client, this module or the transport, or runs `gh api` with the operator's login — the GitHub credential is the operator's), and the anchors are as trustworthy as the command line that names them: `integration merge --state-dir X --project-config Y` with another directory or file (a copied state directory included) is operating another coordinator.

### F2 (HIGH) — one policy snapshot, still current at the dispatch

The configuration was read once, before the slow SSH verifications; the operators removed meanwhile still created and merged (COMPLETED). Now each write is decided under ONE **snapshot**: the configuration read at the start of the boundary, identified by `configuration_fingerprint` — the SHA-256 of the whole effective configuration, canonical (not the file's bytes, not scattered fields: integration switch, operators, allowed-signers path, repository, protected and approval-required patterns, roles, required checks, gates, review policy all count). Approvals, evidence, paths and checks are all evaluated under that snapshot. Inside the dispatch transaction (immediately before `merge-dispatch` is appended) the configuration is read **again** and must have the same fingerprint, and the allowed-signers content must be unchanged since the signatures were verified; a change refuses the write, an unreadable configuration is never "unchanged". A fresh run decides under the new policy. Both approvals are verified against the current signing authority (the inspect approval's documented exception stays: its expiry does not matter, a revoked key does). `merge-dispatch` records the snapshot's fingerprint (`policy_sha256`).

### F3 (MEDIUM) — an unresolved PR creation stays relevant across jobs

Inside one job an uncertain creation already blocked a second one; after an abort, a successor for the same source created PR #102 while #101 was hidden from GitHub's listings. Now, before a PR POST (in the workflow, and again in the boundary — first before and then INSIDE the store-wide dispatch transaction, so a concurrent dispatch is serialized), `integration_merge_schema.creation_history_problems` scans **every persisted job** for the same source branch (source repository, in any letter case, and branch — the PR-discovery identity; any base, any SHA, and any target repository: a renamed or transferred repository keeps its PRs, and a different one blocks conservatively and is resolved the same way), whatever its state (aborted, stale, superseded, open):

- a **dispatched** `create-pr` attempt that never ended (outcome unknown) blocks;
- a PR another job **recorded** for the branch (`merge-pr`, any origin) blocks — the lookup that just showed nothing is lagging;
- a creation that ended on GitHub's own error answer (`rejected`) or that never left (`not-sent`) is resolved and blocks nothing.

It is consulted only when the complete lookup shows no PR for the branch in any state. The blocked job reports `pr-unresolved` with `attention`, naming the blocking job and attempt; the blocking job's history is untouched (an abort is not evidence that a request failed, and the 120-second window only enables attention). There is no force flag and no command that clears the uncertainty: it is resolved when GitHub shows a PR for the branch — the lookup then reuses or refuses it by the existing rules; an operator who knows none exists opens the PR by hand. Works across restarts (it reads only persisted jobs; a corrupt record refuses).

### F7 (HIGH for paths, MEDIUM for checks) — the current path and check policy at dispatch

The boundary re-read the configuration but did not apply it to the candidate's paths or to the required checks: a candidate path newly protected, and a new required check with no result, both merged (COMPLETED). Now, under the snapshot:

- **Paths** (both writes): the candidate's change is **re-derived from hash-verified objects** of the job's candidate repository (the candidate commit must be the recorded head with the recorded tree and the parents `base_sha`, `source_sha`; the changed paths must be the recorded ones; symlink targets read from the objects) and classified with `integration_paths.classify_paths` — the M2/M3 classifier, not a second one — with the inspect approval's per-path authorizations; **M2's recorded history-wide path list** (every path changed anywhere in the source range, with its kinds of change and flags) is classified the same way. A protected path refuses; an approval-required, unowned (a role removed) or otherwise authorization-requiring path the inspect approval does not name refuses; symlink, git-control and gitlink rules apply. A missing candidate repository refuses (the workflow's own rebuild is not trusted for this). (The residual stated here — history-only symlink TARGETS not re-checked — was the final re-review's F7a: **fixed in §60**; the history is now re-derived from verified objects, targets included.)
- **Required checks** (merge only): every name the snapshot requires is observed again on GitHub — the approved head's check runs and statuses, all pages — and must pass; missing, pending, failed, unreadable and contradictory (a repeated check id) all refuse; an empty list claims no CI result (`not-configured`, as documented). Checks are not a condition of opening the PR: CI usually runs on the PR.

### The consolidated boundary, in order

1. Read the configuration (snapshot + fingerprint); integration enabled for the client's repository.
2. Load the job from the anchored state directory: AWAITING_MERGE_APPROVAL, with its candidate.
3. The effective merge approval (signature, operator, key, namespace, window at a fresh clock, binding) and the inspect approval (signature, key, operator), against the same allowed-signers content.
4. M3's validation current for the snapshot's gate plan; M4's review eligible under it.
5. The open, undispatched intent names exactly this operation, PR, SHA, head, base and repository.
6. Paths (above).
7. Creation: other jobs' creation history (above). Merge: required checks (above).
8. One transaction: state, approval and window, intent, cross-job creations (creation), allowed-signers content, configuration fingerprint — then `merge-dispatch` with `policy_sha256`.
9. The request (one transmission).

**Unavoidable races, stated.** GitHub is not in the coordinator's transaction: a configuration or allowed-signers change after step 8's re-read (microseconds before the record) applies from the next write — tested: a change right after the creation's dispatch lets that creation go and refuses the merge; a check that flips after step 7's read, or a base branch that moves, is GitHub's to enforce (the merge pins the head SHA; branch protection is the only atomic guard).

### Independent review of the re-review fixes

A fresh-agent adversarial review (read-only; exploits run only in a disposable copy) confirmed the original F1 exploit gone and F2/F3/F7 holding for the coordinator's own client, and reproduced five bypasses of the client's PUBLIC API; all are fixed, with regression tests and mutation proof.

| Severity | Finding | Resolution |
| --- | --- | --- |
| MEDIUM (HIGH if binding counts as supported API) | `bind_integration` was public on every unbound client and `IntegrationAnchors.configuration` an arbitrary callable: a crafted policy (no required checks; an extra operator and signer, with a self-signed approval written through the public `approve()`) or a copied state directory bound to a `_tracker` client merged — bypassing F7, F1's impact again, and the one-shot | Binding is PRIVATE (`_bind_integration`), done only by `integration merge` for its one client; the client's API cannot bind or replace anchors |
| MEDIUM | `client.transport` was public (a raw merge PUT needed no authority); GraphQL `createPullRequest` / `enqueuePullRequest` were not refused; the request core's guard was a suffix match | Transport private; guards on the parsed route (merge PUT, creation POST, branch-merge endpoint); GraphQL merge, merge-queue, auto-merge and creation mutations refused |
| LOW–MEDIUM | An `int` / `str` subclass comparing equal to the authorized value but converting to another: authorized for PR #101, sent to #555 (and the real attempt consumed) | The facts of a write must be exactly `str` / `int`, in the client and again at the boundary |
| LOW | `integration merge` bound anchors only to an unbound client (a pre-bound one would be used as it was) | One client per run, bound by the command; an already bound client is refused |
| LOW (suspected) | The F3 scan matched the target repository exactly: a renamed or transferred repository dropped the history | The target repository is not part of the identity |
| LOW | History paths are re-classified without symlink targets | **Fixed (§60, F7a)** |

Not scanned by F3 (LOW, stated): the Feature Publisher's own uncertain Draft creations for a `feature/` branch — a different subsystem; the integration job's lookup reports such a Draft as `pr-draft` once GitHub shows it.

### Tests and evidence

`test_fc_integration_merge_boundary.py` (107 tests) and the client tests in `test_fc_integration_merge.py`: the original exploit with a malicious authority subclass and fabricated IDs (no POST, no PUT, origin unchanged); clients without anchors; anchors bound once; a forged copy of the state directory; the dispatch's fingerprint; a 7 × 4 × 2 matrix of configuration changes (operator removed, operators emptied, allowed-signers path changed, inspect key revoked, merge key revoked, integration disabled, configuration unreadable) at four points inside the boundary (after the merge signature, after the inspect signature, during path derivation, in the dispatch transaction) for both writes, with unchanged-configuration controls; the CLI reproduction with the real file; the post-dispatch race; F3's original sequence, a restart, a newer SHA, an unrelated branch, the operator's resolution, rejected and never-sent creations, a reconciled creation (hidden and visible), concurrent successors and the transactional re-scan; F7's newly protected path (exact, directory, glob) for both writes, newly approval-required and role-removed paths, an approval-required path the inspect approval authorized, a history-only path, a missing candidate repository, a new required check, renamed/pending/failed/contradictory checks, checks not gating creation, a policy change during the check read, and the unchanged-policy merge. The previous F1/F2 tests were moved to the new API (no authority objects; the boundary clock is moved in-process where a test needs it). the independent review's findings (no public binding, no public transport, GraphQL creation and merge-queue mutations, route guards with query strings and the branch-merge endpoint, subclassed request facts end to end, a pre-bound client refused by the command). Mutation proof: each of 17 corrections reverted separately in a disposable copy (caller authority accepted, anchors rebindable, no final re-read, unreadable treated as unchanged, no cross-job scan, no in-transaction re-scan, terminal jobs forgotten, no path classification, no history classification, no check observation, recorded paths trusted; public binding, public transport, GraphQL creation allowed, suffix-only guard, subclassed facts accepted, a pre-bound client kept) — the new tests fail for each. The reviewer's five proof-of-concept exploits, run against a copy of the final code, no longer write. Codex's focused harness (`test_m5_focused_positive.py`) no longer imports as written (`WriteAuthority` is gone); with that one import pointed at the concrete class, its six defect reproductions fail (the old outcomes no longer occur) and its ten corrected-behaviour controls pass.

## 60. The final focused Codex re-review of M5: routing, historical symlinks, current roles (F1, F7a, F7b)

The re-review verified F2–F6 as fixed and found three remaining functional defects. The corrections refine the existing boundary (§59): no new authorization mechanism, record kind or state transition.

### F1 (HIGH) — the authorized identity is the outbound destination

The boundary authorized the approved repository and PR, but the client sent the request to `{repo_path}/…`, a public, mutable field: `client.repo_path = "repos/unapproved/target"` sent both writes to another repository, and `client.repo_path = "repos/<approved>/pulls/555/merge?unused="` merged PR #555 into `release` although the approval authorized PR #101 into `dev`. Now:

- `github_api.integration_route(operation, repository, number)` builds the ONE method and path of a write from exact values: the plain `owner/name` repository and, for a merge, the plain positive PR number (`POST repos/{owner}/{name}/pulls`, `PUT repos/{owner}/{name}/pulls/{n}/merge`). `repo_path` plays no part; any other value (a slash, a query, a dot segment, a subclass) is refused.
- The client computes the route before it asks the boundary and passes it as a fact. The boundary derives the route itself — the anchored configuration's repository and the recorded intent's PR, the PR bound to the approved head and base — and refuses any difference (another PR, another repository, a query, a trailing slash, dot segments, another letter case, another method) BEFORE anything is recorded; inside the dispatch transaction it checks the intent, the request and the route again.
- The request core sends exactly that request and nothing else: the client grants it the authorized (method, path) for ONE request; any other request in that window, or a second one, is refused. Outside a grant a PR merge PUT is always refused, and the guard judges the normalized route (query, fragment, percent-encoding, dot segments, repeated slashes and letter case cannot disguise it), so a PR creation disguised in another method's `{repo_path}/…` is refused as well.
- The source SHA stays pinned in the merge body (`sha`); the target repository is the anchored configuration's; the target branch is the bound PR's base, verified when the PR was bound and read again right before the merge (§55, M3).

Unchanged: the Feature Publisher's Draft PR (`create_pull_request`, under its one-shot `(head, base)` authorization and `github.publish.enabled`), the transport, the one-shot dispatch, PR creation and merge recovery. Residual (LOW, stated): the client's READS still use the public `repo_path` (§57) — no write follows from a changed one.

### F7a (HIGH) — historical symlink targets

The boundary classified M2's recorded history entries, which keep each path's kinds of change and flags but not its symlink TARGETS: a symlink added in one commit and deleted in a later one (authorized by the inspect approval), whose target the current configuration newly protects, merged (COMPLETED), on both write paths. Now the boundary re-derives the source history's change from hash-verified objects of the anchored checkout with M2's OWN derivation, `integration_inspect.history_changes` (extracted from M2's inspection, which now calls it too): every commit of the verified range plus the net diff from each merge base proven as a boundary parent, with every target each path was ever set to read from its verified blob. The derived list must be M2's recorded one (paths, kinds of change, flags) — the recorded list is never classified on its own — and it is classified by `classify_paths` under the snapshot with the inspect approval's authorizations. A symlink — kept, history-only, any of its versions — whose target (resolved from the symlink's own directory) is, or contains, a newly protected path refuses; so does a history that cannot be re-derived (objects missing from the checkout, an unprovable merge base). The lexical target check alone cannot judge a symlink that goes through a directory symlink, along a chain or to a directory (a glob such as `**/.env` matches below it), so M2's existing rule for a symlink kept in the source tip — it must resolve to a regular file of that tree — now holds for the history too: in every commit of the range in which a path the range sets to a symlink exists as one, it must resolve to a regular file of that commit's tree (`PathChange.history_kinds`, computed by the shared derivation). A history-only symlink to a directory, through a directory symlink, to another symlink or to nothing — or one whose target becomes a directory in a later commit — refuses at M2's inspection already, and at the boundary for a job inspected before (the independent review's finding, below). The approval and review evidence are not reinterpreted: the job stays AWAITING_MERGE_APPROVAL and the workflow reports `no-approval` with the reason.

### F7b (MEDIUM, functional blocker) — the gate plan of the current owners

The boundary derived the gate plan from the role recorded with each candidate path when M3 classified it: a path whose owner changed (backend → frontend) after validation merged (COMPLETED) without `frontend-build` and `frontend-type-check`. Now the plan is derived from the candidate's paths AS CLASSIFIED NOW:

- **at the boundary**, from its own classification of the candidate (verified objects, the snapshot): M3's validation, M4's review (`review_eligibility(…, paths)`) and the merge approval's bound validation (`plan_sha256`) must all be for exactly that plan. Otherwise the refusal names the validation, both plan digests, the current owners, the gates never run for the current plan and the gates no longer planned ("restore the configuration, or abort the job and supersede it"). Nothing is run, nothing is replaced, no transition is made;
- **everywhere else** (`integration_validate.validated_for` / `current_plan_sha256`, M4's `_verify` and `validation_problem`, `review_eligibility`, `status`, the merge-approval readiness): from the recorded candidate paths re-classified lexically under the current configuration (`current_paths`: class and owner by `path_class`), so a role change before a run is reported by the existing rule for a stale plan — `not-validated` — before any intent or write.

An unchanged configuration classifies exactly as recorded: the plan, and every existing decision, stay valid.

### The consolidated boundary, in order (replaces §59's list)

1. The configuration snapshot and its fingerprint; integration enabled; the client's repository is the configured one.
2. The job: AWAITING_MERGE_APPROVAL with its candidate.
3. The merge approval and the inspect approval, against the same allowed-signers content.
4. The open, undispatched intent names exactly this operation, PR, SHA, head, base and repository — and the request goes to exactly the route that operation has (F1).
5. Paths: the candidate's change (candidate repository) and the source history's change (the checkout, M2's derivation, symlink targets included — F7a), both from verified objects, both the recorded ones, classified under the snapshot.
6. M3's validation, M4's review and the approval's bound validation are for the gate plan of the paths as classified in 5 (F7b).
7. Creation: other jobs' creation history. Merge: the required checks, observed again.
8. One transaction: state, approval and window, intent with request and route, cross-job creations (creation), allowed-signers content, configuration fingerprint — then `merge-dispatch` with `policy_sha256`.
9. Exactly that one request, to exactly that route.

### Independent review of the final re-review fixes

A fresh-agent adversarial review (read-only; exploits run only in a disposable copy) found the routing and the F7b corrections holding against everything it tried (settings and value types, the one-request grant, re-entrancy, concurrency, normalization, every public request path; the boundary, M3, M4 and status deriving the same plan) and reported:

| Severity | Finding | Resolution |
| --- | --- | --- |
| MEDIUM | F7a still bypassable: a history-only symlink was resolved as text only — `backend/tools/d → ../src` with `**/app.py` newly protected, `link.py → d/../app.py` through a directory symlink, and `d → ../..` + `link → d/../../../../../etc/passwd` (really outside the repository, inside as text) merged; M2 had the same gap | **Fixed:** every symlink version must resolve to a regular file in each range commit where it exists (above); all three variants, and their M2 counterparts, are refused at inspection |
| LOW | A target differing only in letter case (`→ ../src/App.py` with `backend/src/app.py` protected) passed the lexical check | **Closed by the same rule:** Git trees are case-sensitive, so such a target resolves to nothing in the commit's tree and refuses |
| MEDIUM | The client's GraphQL guard refused five mutation NAMES (`mergePullRequest`, `enablePullRequestAutoMerge`, `enqueuePullRequest`, `mergeBranch`, `createPullRequest`); the public `graphql()` still sent `updateRef`/`updateRefs` (moves `refs/heads/dev`: in effect a merge without the boundary), `createCommitOnBranch`, `revertPullRequest` (opens a PR) and `updatePullRequest` (retargets a PR) — through any client, anchors or not | **Fixed (§61):** GraphQL writes are an allowlist of the Projects sync's fixed documents |
| LOW (open, pre-existing) | The Feature Publisher's Draft PR (`create_pull_request`) still goes to `{repo_path}/pulls` | Not changed: the publisher is preserved as instructed (Draft only, `github.publish.enabled`, its one-shot `(head, base)` authorization) |

### Tests and evidence

`test_fc_integration_merge_destination.py` (38 tests): Codex's two reproductions (another repository; a `/pulls/555/merge?unused=` suffix merging another PR into `release`) and their variants (fragment, dot segments, trailing slash, letter case, absolute URL) for both writes, checked on the arguments that reach the transport — exactly the authorized request is sent and the legitimate write succeeds; a whole run with `repo_path` redirected from the first write on; the repository changed after binding (refused, nothing recorded); `repo_path` and the settings changed during the checks, inside the dispatch transaction and after the authorization (the write still goes to the authorized route); the boundary refusing every route but the authorized one before recording (another PR, another repository, query, trailing slash, dot segments, letter case, another method); `integration_route`'s exact values; disguised PR creations through another method; one request per grant; the Feature Publisher's Draft PR unchanged. `test_fc_integration_merge_current_policy.py` (39 tests): F7a's reproduction for both writes, with the policy changed at the boundary and before the run; a symlink kept in the candidate; relative resolution from a nested directory, a deeply nested target in a newly protected directory, a target deleted later, several historical versions (either one protected), an earlier target of a symlink the candidate keeps; targets that stay allowed (another path protected; a relative target resolving elsewhere; no symlink); the derivation compared with M2's record; history-only symlinks that do not resolve to a regular file — a missing target, a directory symlink, `..` through a directory symlink, a chain leaving the repository, a symlink to a symlink, a target that becomes a directory later — refused by M2, and three of them refused at the boundary (both writes) for a job inspected under the earlier rule. F7b's reproduction (backend → frontend), frontend → backend and a removed role with an authorized path, for both writes (same head and tree; the gates the old validation never ran named; nothing run inside M5); a role change before the run (`not-validated`, no intent); mixed roles with one path moved; a history path that newly needs an authorization; the same roles and plan (nothing changed, an unrelated pattern changed) still merge; the derivation under two configuration snapshots with the recorded evidence intact. Mutation proof: each of 11 corrections reverted separately in a disposable copy (the request sent to `repo_path` again, no route check at the boundary, any request allowed inside a grant, an unnormalized guard, M2's recorded history entries classified without targets, the derivation not compared with the record, the per-commit symlink resolution not computed, its result ignored by the classification, recorded roles everywhere, recorded roles at the boundary only, recorded roles in the workflow only) — the new tests fail for each. The independent reviewer's exploit file, run against a copy of the corrected code: its seven F7a variants are refused (at inspection), its GraphQL finding is closed by §61, its publisher finding still reproduces (open, above). Codex's reproduction file (`test_m5_rereview_extra.py`), run against a copy of the corrected code: its five defect reproductions fail for the expected reasons (the history-only symlink is refused for both writes, naming the protected target; the merge PUT goes to PR #101; the obsolete plan is refused for both writes) and its two candidate-symlink controls pass; its route probe (`test_m5_final_route_probe.py`, defect A) fails on its outbound-request assertions — nothing reaches `repos/unapproved/target`, the legitimate request goes to the approved repository — and its final F1–F7 harness (`test_m5_final_independent.py`, 16 tests) and the F3 matrix (`test_m5_rereview_matrix.py`, 31 tests) pass unchanged.

## 61. GraphQL writes: an allowlist of fixed documents

The independent review of §60 reproduced, with the real `GitHubClient` and a recording fake transport, that the public `graphql()` refused only five mutation names (`mergePullRequest`, `enablePullRequestAutoMerge`, `enqueuePullRequest`, `mergeBranch`, `createPullRequest`) and sent every other mutation — `updateRef`, `updateRefs`, `createRef`, `deleteRef` (move, create or delete `refs/heads/dev` or any branch: in effect a merge or a destruction without the signed merge's write boundary), `createCommitOnBranch`, `revertPullRequest` (opens a PR), `updatePullRequest` (retargets a PR) — through any client, with or without integration anchors. (These are documented mutations of GitHub's GraphQL schema; that the real service applies them for the operator's token was not tested: no real GitHub write is made.)

**Correction.** The denylist is replaced by an allowlist of FIXED DOCUMENTS (`github_api._graphql_allowed`): a document that contains `mutation` anywhere — GraphQL cannot express a mutation without that keyword; a comment or string that merely mentions it is refused too, conservatively — is sent only when it is, character for character, one of the seven documents the GitHub Projects sync sends (`github_projects.MUTATIONS`). The client pins them itself as the SHA-256 of each document (`github_api._ALLOWED_MUTATIONS`) — it imports nothing to decide, and widening the allowlist needs a change to the client; a test keeps the pins equal to the Projects module's documents:

| Document | Mutation | Used for |
| --- | --- | --- |
| `ADD_ITEM` | `addProjectV2ItemById` | adding an Issue to the board |
| `SET_VALUE` | `updateProjectV2ItemFieldValue` | Status, Priority, Size, Role (text and single-select) |
| `CLEAR_VALUE` | `clearProjectV2ItemFieldValue` | clearing a field value |
| `CREATE_FIELD` | `createProjectV2Field` | the board layout (missing fields) |
| `UPDATE_FIELD` | `updateProjectV2Field` | the board layout (options) |
| `CREATE_VIEW` | `createProjectV2View` | the board's views |
| `UPDATE_VIEW` | `updateProjectV2View` | the board's views |

Nothing about the caller counts: not the `mutation` flag, not an operation name, not an alias. An allowed operation name around another field, an alias named like an allowed field, an extra field or operation, a query followed by a mutation (commas are whitespace in GraphQL), a comment, a fragment, a reformatted copy of an allowed document, and a `str` subclass are all refused before anything is sent; read-only documents (no `mutation` in them: the Projects queries, M2's commit attribution) are sent as before, with the same transport, error classification, rate-limit waits and write accounting. The variables of an allowed document stay the Projects layer's to check (its project-binding tripwire, `github-projects.md`): they cannot change which mutation runs. No flag or method bypasses the allowlist; integration anchors grant no GraphQL write (the two integration writes are REST only).

**Tests and evidence.** `test_fc_github_graphql_allowlist.py` (58 tests): the seven documents sent (and the client's pinned digests equal to exactly the mutation documents the Projects module defines); seven read-only documents sent; fifteen refs/branch/commit/PR and unknown mutations refused with either `mutation` flag, the transport receiving nothing; eleven alias / operation-name / multi-operation / comment / fragment / formatting tricks refused; a lying `str` subclass refused; a client with integration anchors still limited to the allowlist. The Projects, GitHub client and M5 tests pass unchanged (three M5 tests that send merge and creation mutations still expect "no GraphQL merge"). Mutation proof: the old denylist restored (33 tests fail), an operation-name allowlist (6 fail), no plain-`str` check (1 fails).

## 62. M6: scope, and the scheduling decision taken before implementation

M6 in the approved design (§8.5, phase P6) is **crash-recovery hardening and failure-injection tests**, plus the repair routing of §6.4. **Worker scheduling is not named in that milestone.** It is nevertheless what the design is for (§1: "a Worker job type"), what Issue #65 asks for ("Worker-managed"), and what M1–M5 deferred to M6 in these notes (§1, §37, §38 item 6, §44, §57). This section records how the two fit, written before the M6 code.

**Discrepancy and resolution.** Scheduling is added without changing the approved architecture: no new state, no new edge, no new evidence kind, no new GitHub write, and no change to the merge authorization. The Worker only *starts the existing commands* for jobs an operator already authorized; every check, transition and record stays in the M2–M5 code, which a scheduled run and an operator's run execute identically.

| Question | Decision |
| --- | --- |
| Who authorizes a scheduled run? | The operator, twice: `github.integration.worker.enabled` (off by default) and the job's **signed inspect approval**, which already authorizes inspection, validation (§26 item 1) and the review of that candidate. The Worker signs nothing, approves nothing and never runs `intake`, `approve` or `abort` |
| What may the Worker start? | A closed list of four commands, each as its own child process: `integration inspect`, `validate`, `review` (never `--retry`: a new review round is an operator decision, §38 item 1) and the new read-only-towards-GitHub `integration recover`. Which of them it starts is configured (`worker.stages`, default `validate`, `review`) |
| The merge | **Never scheduled.** The Worker has no code path to `integration merge`, and its process never binds the client's integration trust anchors (§59), so even a defect in the scheduler could not send the PR-creation or merge request. A job in AWAITING_MERGE_APPROVAL waits for the operator's signed merge approval and the operator's `integration merge --apply` |
| After the operator's merge | `integration recover` settles what a merge run left behind — an intent that was never dispatched is closed, a dispatched merge request is reconciled from GitHub's state, a MERGED job's verification is repeated — through M5's own reconcile-first and verification code, with a client that cannot write. It never dispatches, never repeats and never creates a PR |
| Feature Jobs | They keep priority: an integration stage starts only when no agent holds a claim and no feature task is ready to start. The checkout has **one execution slot**: while a stage holds it, no agent is claimed (by the Worker or by hand), no feature is integrated and the service runs none of its other steps — a candidate's gates and a feature's gates never run side by side. On a checkout that never scheduled a stage nothing changes |
| Ownership of a job's execution | The existing `locks/integration-<job>.lock` (a kernel lock: it dies with its process, so it is never stale). Whoever holds it — an operator's command or a Worker's child — excludes the other; the loser changes nothing |
| Bounded automation | A stage that changes nothing is repeated with a growing delay and, after `max_stalls` such runs or `max_launches` runs in total, held for the operator. A job that carries `attention` is the operator's turn and is not scheduled |
| Repair (design §6.4) | Not automated, and Issue #67 is not implemented. NEEDS_REPAIR is reported with the roles the affected paths route to; the repaired source is a new commit and therefore a **new job** with its own SHA, approval and evidence (§5.5) |

Nothing in this decision changes a security boundary: Worker role ownership is untouched (`feature_coordinator/**` stays unowned), the gates run in the same sandbox as an operator's `validate`, and the signed approvals are verified by the same code at the same points.

## 63. Worker scheduling (M6)

```yaml
github:
  integration:
    enabled: true
    worker:                       # absent = off: the service never looks at integration jobs
      enabled: false              # needs integration.enabled
      stages: [validate, review]  # of inspect, validate, review, recover — `merge`, `approve`, `intake`, `abort` are refused
      poll_interval_seconds: 30
      retry_seconds: 300          # after a run that changed nothing; doubled per repeat …
      max_retry_seconds: 3600     # … up to this
      max_stalls: 3               # such runs in a row → the job is HELD for the operator
      max_launches: 12            # runs of one stage per job → HELD
      stage_timeouts: {inspect: 900, validate: 14400, review: 7200, recover: 900}   # then the stage process is stopped
```

**One decision, three readers.** `integration_schedule.next_step(job, config)` reads the job record and the current configuration and says what happens next and who does it. The Worker's step, `integration schedule` and `integration recover` all use it, so the Worker never starts what the operator's view does not show.

| Job | Next step |
| --- | --- |
| INTAKE | operator: repeat the same `integration intake` |
| AWAITING_APPROVAL | operator: sign the inspect approval — once one is recorded: **inspect** |
| INSPECTING | **inspect**, or **validate** once the inspection passed; `attention` (needs authorization) → operator |
| CANDIDATE_BUILT | **validate** |
| VALIDATING | **validate** (open attempt, or no validation current for the gate plan of the current configuration), **review** once validated; `attention` (a BLOCKED gate) → operator |
| IN_REVIEW | **review** (open attempt, pending answer, next attempt of the round); `attention` (ERROR: the round is over) → operator's `--retry` |
| AWAITING_MERGE_APPROVAL | **operator** (the signed merge approval and `integration merge --apply`). Only what a merge run left behind is the Worker's: an undispatched intent or a dispatched merge request → **recover** |
| MERGED | **recover** (the post-merge verification) |
| NEEDS_REPAIR | operator (§66) |
| terminal | nobody |

A stage in bold is the Worker's only while `worker.stages` lists it; otherwise the same step is shown as the operator's, with the command.

**The step** (`integration_worker.IntegrationStep`, the service's fourth GitHub step, appended only while scheduling is on). It never waits for a gate, a reviewer or GitHub:

1. **Watch** the stage process it (or an earlier service instance) started, found by its process tag (`FC_INTEGRATION_STAGE`), never by a recorded PID alone. Running and within its deadline: nothing. Past its deadline: the decision is recorded, then the processes are stopped (SIGTERM, grace, SIGKILL). Gone: **settled** — the job record is compared with the state and revision the run started from. Changed is progress; unchanged is a stall. The exit code and the command's printed outcome only word the operator's message.
2. **Start** at most one stage: the oldest job whose next step is the Worker's, not held, not delayed, whose job lock is free — and only while the checkout is idle for Feature Jobs. The launch is counted before the process starts.

What it starts is `python -m feature_coordinator integration <stage> --job J --apply --actor worker-service --json`: the command an operator would run. The scheduler holds no GitHub client, verifies no approval and writes no job record; its own bookkeeping is `integration-schedule/<job>.json` (launches, the running process, delay, hold), which is not evidence.

**One execution slot** (`integration_slot.execution_owner`). A candidate's gates and a feature's gates must never run side by side: a recorded gate FAIL is final for a candidate (§23), so a failure caused by a concurrent test run would be permanent. The slot is therefore not a variable of the service but a fact everyone reads from the schedule records and `/proc`:

| Who asks | When the slot is taken |
| --- | --- |
| The Worker service, before each cycle's work — so also in the first cycle after a restart, and also when scheduling was switched off while a stage ran (the step is then still constructed: it watches and settles, it starts nothing) | no agent, no publication pass (it runs a feature's integration gates), no intake (a planner), no Draft PR review; state `BUSY` |
| Every task claim (`claims.py`), in the coordinator's critical section — the Worker's own launch and a manual `launch` / `work` alike | refused: "the checkout's execution slot is taken" |
| The feature integration barrier (`integrate FEATURE`, the publisher) | refused |
| The admission of a stage, in that same critical section | not recorded while a claim exists, a quiescent barrier holds the project lock, or the slot is owned |

The slot is taken while a stage process lives (found by its tag); while a launch is recorded for which **no process is recorded yet** (that reservation is released by a settlement in a critical section and by nothing else — not by elapsed time, and not by its launcher's death); while a stage left processes behind; and while **a reviewer process of the job's `review` stage** still runs. M4 runs a reviewer in its own session, so it outlives a killed stage: it is found through the tags on the job's own review attempts, so every reader sees it from the first moment, and it is collected by the next `review` run, never duplicated. Because the review command that would enforce its attempt's deadline is dead, the Worker enforces it — under the job's own lock and for the strictly loaded job, attempt by attempt: the reviewer of the open attempt within its deadline runs on; past that deadline, or when its attempt is no longer open, it is stopped; a service stop stops it too; and while another command (an operator's `review`) owns the job, nothing is touched. M4 then records the attempt by its own rules.

**The hand-off from the launcher to its stage process** is decided in critical sections only. A recorded launch without a recorded process holds the slot until, under `coordinator.lock`, one of three things happens: the stage process **confirms its launch before it does anything** and records itself (`integration_slot.confirm_launch`, called first by the stage commands when the Worker started them — they carry the stage tag: the launch must still be the recorded one, no claim may exist and no barrier may hold the project lock); the scheduling step closes the launch, having read again there that nothing of it lives; or an operator's `schedule --resume` does. Whichever comes first wins and the others see it: a closed launch is not confirmed (the stage process then does nothing), and a confirmed one is not closed. So neither a launcher that dies at the worst moment nor a restarted service — which reads the slot before any of its steps — finds the slot free while a stage process is about to work. An operator's own command carries no tag and is unaffected. A schedule record that cannot be read makes the ownership **unknown, and unknown is not free**: `NEEDS_ATTENTION`, no agent, until an operator has looked (`integration schedule --job J --resume`, refused while any stage process lives). Feature Jobs keep their priority: a stage only ever starts on an idle checkout — no claim, no task of the active feature ready to start, no Draft PR reviewer running. A checkout that never scheduled a stage has no schedule directory, and every one of these checks is then one `stat`.

**Bounded.** A stall — a run that left the job record as it was, or a stage process that could not be started — is repeated after `retry_seconds`, doubled each time; `max_stalls` in a row, or `max_launches` runs of one stage, HOLD the job (`NEEDS_ATTENTION`, with the last outcome). A stall hold lapses when the job record changes; `integration schedule --job J --resume --reason TEXT --apply` clears any hold and the counters, as a recorded operator decision. A job with `attention` is never started.

**A terminal outcome does not vanish.** When a scheduled stage ends a job FAILED (a wrong merge found by `recover`), STALE (a moved ref, someone else's merge) or REFUSED, nobody continues it — but it stays in the Worker's view and in `NEEDS_ATTENTION` until an operator acknowledges it with the same `--resume` (recorded with the reason).

**Lifecycle.** `worker pause`: nothing new starts, a running stage finishes. A graceful service stop asks the stage process to stop (a review gives its attempt back) and closes the launch as nobody's stall. After a service crash the stage process either survives (outside systemd: the next instance watches it by its tag) or is gone (the next instance settles it); it is never started a second time.

```bash
python -m feature_coordinator integration schedule [--job J] [--json]                       # the Worker's view (read-only)
python -m feature_coordinator integration schedule --job J --resume --reason TEXT --apply   # clear a hold
python -m feature_coordinator worker status                                                 # includes the open integration jobs
```

## 64. Crash recovery (M6)

Every stage command resumes its own interrupted work (§25, §37, §52). M6 adds the one place that names each interruption, the Worker that repeats the command, and failure-injection tests that stop a stage at each fault point.

| Interrupted operation | What the record shows | Recovery (tested) | GitHub write |
| --- | --- | --- | --- |
| Candidate preparation | INSPECTING, inspection passed, no candidate | rebuilt from the pinned SHAs; it must reproduce the same commit and tree, else STALE. Nothing of the interrupted build is trusted | none |
| Validation gates | CANDIDATE_BUILT, or VALIDATING with an attempt that has no decision | the attempt is resumed against the same SHAs: recorded PASS/SKIPPED results are kept and not re-run, a recorded FAIL is final, the interrupted gate runs again | none |
| Independent AI review | IN_REVIEW with an attempt that has no run, or an answer without a decision | the attempt's reviewer process is waited for and collected, or recorded as interrupted; never a second reviewer for one attempt; a recorded answer is decided without a new run | none |
| PR discovery / creation | a `create-pr` intent without an outcome | **no dispatch**: no request left — `recover` closes it (`not-created`, `not-dispatched`). **Dispatched**: never repeated; only the operator's `integration merge --apply` looks the PR up (every state) and binds it | not sent / unknown |
| Merge dispatch | a `merge` intent without an outcome | **no dispatch**: closed (`not-merged`, `not-dispatched`). **Dispatched**: never repeated; `recover` reads the PR — merged by the coordinator's account → recorded (MERGED) and verified; merged by someone else → STALE; not merged → `merge-uncertain`, after the observation window `attention` | not sent / unknown |
| Post-merge verification | MERGED | `recover` repeats the whole verification; COMPLETED only with the complete evidence; a failure is FAILED (manual remediation, nothing reverted); a pending check waits | none |

```bash
python -m feature_coordinator integration recover [--job J] [--json]          # diagnosis: local records only, nothing changes
python -m feature_coordinator integration recover --job J --apply [--json]    # settle an unfinished merge run from GitHub's state
```

`recover --apply` is `IntegrationMerger.settle`: step 0 of the merge (reconcile first, §47) and the post-merge verification (§50), and nothing else. It asks for no approval because it authorizes nothing: it creates no PR, sends no merge request and repeats none. Its GitHub client carries no integration trust anchors, so the client itself refuses both integration writes (§59); a client that does carry anchors is refused by `settle` before anything is read. For an interruption before the merge stage there is nothing to settle, and `recover` names the stage command that resumes it.

Recovery never changes the pinned source, base or candidate SHAs, never creates a job, and never moves evidence. Evidence is append-only across every crash (the tests compare the evidence list before and after). A job that cannot continue is terminal or waits for the operator; a new attempt is a new `integration intake`.

**Stale approvals and changed configuration** are not recovery's to work around. A revoked or expired approval, a changed gate plan, a moved source or base branch and a retargeted PR are found by the stage commands exactly as for an operator's run: `no-approval` (the Worker stalls, then holds, naming the outcome), a new validation before any review, STALE.

## 65. Concurrency (M6)

| Scope | Mechanism | Tested with |
| --- | --- | --- |
| One job, one executor | `locks/integration-<job>.lock`, taken non-blocking by `inspect`, `validate`, `review`, `merge` and `recover` with `--apply` | 8 real processes: one owner, seven refused; an operator's command against a scheduled stage |
| One stage process per state directory | the schedule records, written under `coordinator.lock`; a launch is recorded before its process starts, only for the job record as it was read, and only when admitted (no claim, no barrier, slot free) | 8 real Worker processes stepping at once: one stage process, one launch |
| A stage and Feature work | the execution slot (§63): claims and the feature integration barrier ask it; the service asks it before any other work | a claim and `integrate` refused while a stage runs; a claim or barrier appearing at the last moment refuses the admission; a restarted service with a surviving stage |
| `schedule --resume` and a launch in flight | both take `integration-worker.lock`: a resume is refused while a step is between a launch record and its process | a resume issued inside the launch handoff |
| One scheduling step at a time | `locks/integration-worker.lock` | the same |
| One service per state directory | `worker-service.lock` (unchanged) | existing tests |
| No stale lock | every lock is a kernel `flock`: it ends with its process; the file that stays means nothing | a holder killed with SIGKILL: the job is free at once |
| Killed between the launch record and the process start | the next step finds no process with the tag: settled as a stall, one launch spent | a real Worker process killed there |
| A stage process killed (SIGKILL) inside a command | the kernel releases its job lock; the record is where the last completed write left it; the next launch resumes | real stage processes killed inside a gate and at four persisted checkpoints of `validate`, in `review` after its reviewer started, in `recover` before COMPLETED was recorded |
| Killed right after the start | the stage process is found by its tag although its PID was never recorded: watched, not started again | the same |

GitHub writes are not part of any of this: only `integration merge --apply` reaches the write boundary, whose `merge-dispatch` record is one-shot per attempt across processes and restarts (§58).

## 66. Repair routing (design §6.4) and Issue #67

The design sends a defect to a repair task on its own branch and starts a new job for the new SHA. M6 implements the **routing as a report** and none of the automation:

- `integration recover --job J` shows, for NEEDS_REPAIR, what failed (the failed gates, or the review's findings), the paths concerned, and the role each path routes to under the current configuration; a path no role owns, or one that needs approval, routes to the operator.
- The repair itself is ordinary authored work through the existing feature workflow: an approved Issue, the owning roles' write scopes and commit gates, its own branch. Nothing new is opened or scheduled by an integration job, and the Worker starts nothing for a NEEDS_REPAIR job.
- The repaired source is a new commit: `integration abort --job J` and a **new** `integration intake`. The new job has its own SHA, its own signed approval and its own evidence. The old job's approval record is refused for it (it names the other job and SHA), and no validation or review evidence is copied. While the old job is open the branch cannot be taken by a second job.

**Not implemented: Issue #67** (revising and re-reviewing an already published Draft PR). §16's open question stays open: W4.7 never follows a moving PR head, so a revision is a new job. The state machine is unchanged: NEEDS_REPAIR ends in ABORTED by the operator's abort (§6 item 7's proposal); no `SUPERSEDED` state and no in-place `NEEDS_REPAIR → INTAKE` edge were added.

## 67. Decisions taken in M6 that need operator confirmation

1. **Worker scheduling is part of M6** although the design's milestone list names only crash recovery (§62).
2. **The inspect approval authorizes the scheduled stages**; no separate approval for automation beyond `worker.enabled`.
3. **Default stages are `validate` and `review`.** `inspect` and `recover` are opt-in.
4. **`recover` may be scheduled.** It moves a job by evidence only (MERGED, COMPLETED, FAILED, STALE for an external merge) and cannot write to GitHub. Without it in `worker.stages`, a MERGED job waits for the operator's `recover` or `merge`.
5. **One execution slot shared with Feature Jobs, Feature Jobs first.** A long validation delays the next feature task that becomes ready while it runs, and also publication, intake and the Draft PR review (a merged PR or a newly approved Issue is seen once the stage has ended). **This touches Feature Job code**: `claims.py` refuses a claim and `workflow.integrate` refuses the barrier while an integration stage holds the slot (§71, finding 3). Both checks are one `stat` on a checkout without schedule records.
6. **Job-level `attention` stops scheduling**, including a dispatched merge request left unresolved after the observation window (the operator runs `recover` once GitHub shows a result).
7. **A new command group member, not a merge flag**: `integration recover` is separate from `integration merge`, so that nothing scheduled can reach the merge run.
8. **Repair routing is a report** (§66); the design's automatic repair task is not implemented.
9. **`service.py` import block reformatted** (same names) to stay within the 1000-line module limit; `_poll_github` returns whether an integration stage holds the slot.
10. **An unreadable schedule record stops agents too** (`NEEDS_ATTENTION`) until an operator resets it — also on a service whose configuration no longer schedules integration jobs.
11. **A terminal outcome produced by a scheduled stage needs an acknowledgement** (`schedule --resume`) before it leaves the Worker's attention.

## 68. Security review of M6 (HIGH / MEDIUM / LOW)

| Severity | Finding | Status |
| --- | --- | --- |
| — | **No HIGH finding known.** A merge or PR creation by the Worker, a scheduled command outside the four stages, a stage started twice, and a repeated uncertain write were each checked by a test (§69) | — |
| MEDIUM | The Worker now runs a candidate's gates without an operator at the keyboard. The candidate is code from an existing branch | Unchanged controls: the signed inspect approval, known authors and trusted logins, path authorization, and the gate sandbox of M3 — the scheduled command is the operator's command. A gate that cannot run in the sandbox is BLOCKED, never passed, and stops scheduling |
| MEDIUM | (was) An operator's manual `launch`, `work` or `integrate` was not refused while an integration stage ran; a restarted service did not see a surviving stage before its other steps; a reviewer outliving its killed stage did not hold the slot | **Fixed (Codex review, §71):** the execution slot is read from the records by claims, the barrier and the service |
| MEDIUM | An operator's foreground `integration validate` / `review` (not started by the Worker) still runs beside the Worker's agents, as before M6: only the per-job lock coordinates it | Documented (§70) |
| LOW | The lock probe before a launch takes the job lock for an instant; an operator's command started in that instant is refused once ("another operation is running") | Documented; repeat the command |
| LOW | Schedule records are unkeyed and writable by whoever can write the state directory | They decide nothing about a job, and a record naming anything but a stage is refused on load and stops scheduling |
| LOW | Stage logs (`logs/integration-<job>.<stage>.*.log`) keep the command's `--json` output | Mode and location as the other Worker logs; no credential is printed by the stage commands |

**Claude Code's Bash sandbox (W1 / B1) is not on M6's path.** A scheduled stage starts no agent with a shell: `validate` runs gates in the coordinator's own gate sandbox (bubblewrap, M3), and `review` runs Claude Code without tools (M4). The separate finding about the installed Claude Code and `claude_shell: sandboxed` is reported in [worker-security.md](worker-security.md) §14.

## 69. Tests (M6)

No network, no real GitHub, no model call, no real merge. Stages run through the M2–M5 rig — the real inspector, validator, reviewer and merger over disposable repositories, fake gates, a fake reviewer process and the scripted GitHub that merges for real on a disposable bare origin — bracketed by a real tagged process that stands for the stage process (`fc_integration_worker_rig.Stages`).

- **Scheduling** (`test_fc_integration_worker.py`): off by default; the merge and every operator command cannot be configured, built or recorded as a stage; an approved job is driven to AWAITING_MERGE_APPROVAL and nothing more happens, also with a valid signed merge approval on record (zero GitHub writes); one stage at a time across jobs; a claim defers a stage; `attention`, NEEDS_REPAIR and a missing approval start nothing; stall, delay, hold, resume; the launch budget; the deadline; crashes between counting and starting and right after starting; a graceful stop; unreadable job and schedule records stop every start; an operator's command owns its job.
- **The service** (`test_fc_integration_worker_service.py`): a ready feature task starts before any stage; while a stage runs the service is BUSY, starts no agent and runs no other step; pause; stop and restart; with scheduling off the service does nothing new.
- **Real processes** (`test_fc_integration_worker_concurrency.py`): eight Worker processes at once start one stage; a Worker killed at either launch point never causes a second stage; a lock left by a killed process is free; the job lock admits one process.
- **Real stage processes** (`test_fc_integration_worker_processes.py`): the stage's work runs in a separate process that holds the job's real lock and is killed with SIGKILL — inside a hanging gate, at four persisted checkpoints of a validation, in a review after its reviewer started (the reviewer keeps the slot: no other stage, no claim; one reviewer in the end), in a recovery before COMPLETED was recorded (no GitHub write by either recovery process).
- **The slot** (in `test_fc_integration_worker.py` and `…_service.py`): a claim and a feature integration refused while a stage runs; an admission refused by a claim or a barrier appearing at the last moment; `schedule --resume` refused inside the launch handoff; a restarted service — also one with scheduling switched off — finding a surviving stage before any other step and any agent; a record corrupted while its stage runs stopping agents; a terminal outcome staying in attention until acknowledged; the strictness of the schedule record (exact types, finite numbers, the job id and its file name, absent versus unreadable); a failed start counted as a stall and held.
- **Recovery** (`test_fc_integration_recovery.py`): a validation stopped at each of six fault points and a review stopped at each of seven are resumed by the Worker with append-only evidence, the same candidate, no repeated gate result and one reviewer; `recover` refuses a client that could write and its own client cannot; an undispatched intent is closed; a merge GitHub performed before the crash is reconciled with exactly one merge request ever sent; a lost answer is never followed by a second request; a pending check is waited for; a wrong merge is FAILED and nothing is reverted; someone else's merge is STALE; PR creation is never repeated; moved source and base, a retargeted PR, a revoked approval, a changed gate plan and a GitHub timeout; repair routing; a repaired source is a new job to which no approval or evidence moves.
- **CLI** (`test_fc_integration_recovery_cli.py`): `recover` (diagnosis, `--apply`, exit codes), `schedule` (view, `--resume`), `worker status`.

## 70. Remaining limitations (M6)

- **Not run against the real GitHub service or a real reviewer**, as M4 and M5. Enabling scheduling in production is an operator decision after a supervised job.
- **The stage command line is not executed for `validate` and `review` in the tests.** Their work needs the test doubles of the gate runner and the reviewer, so it runs through the same classes the commands call — in the test process in most tests, and in a separate, SIGKILLed process in `test_fc_integration_worker_processes.py`. The real command line is executed for `recover` and `schedule`, and parsed by the real CLI for all four stages.
- **An operator's own foreground integration command is outside the slot.** `integration validate` or `review` typed by hand takes the per-job lock but not the execution slot (as before M6): do not run one while the Worker runs an agent. A stage the Worker started does hold the slot against `launch`, `work` and `integrate`.
- **A corrupt schedule record blocks agents** until it is reset by hand, by design.
- **`worker stop --after-current`** does not wait for a running stage: it is stopped and started again by the next instance.
- **A stage past its deadline is stopped, not its reviewer.** The reviewer process of M4 runs in its own session and is collected by the next `review` run, as after any crash.
- **Delays use the service's clock** and are not persisted across a changed `retry_seconds`; a hold is.
- **Repair is manual** (§66); Issue #67 is open.
- **No automatic merge** (design §9, phase P8): unchanged, and not prepared by M6.

## 71. The independent Codex review of M6 (REQUEST_CHANGES) and its corrections

A read-only Codex review of the first M6 commit (given the requirements, not the author's conclusions) found **no path to a GitHub write**: no scheduled route to PR creation, a merge request, a repeated uncertain write or `integration merge`; `settle` refuses bound clients and reuses M5's reconciliation, attribution and verification; no SHA mutation, evidence transfer, automatic replacement job or implicit Issue #67. Its findings concern how firmly the one execution slot was held. All HIGH and MEDIUM findings are corrected.

| # | Severity | Finding | Correction | Test |
| --- | --- | --- | --- | --- |
| 1 | HIGH | `schedule --resume` could discard a launch between its record and its process; the process then ran untracked | `--resume` takes the scheduling step's lock (`integration-worker.lock`) and is refused while a step holds it; with the lock held, a `running` entry without a process is nobody's launch in flight | `test_resume_cannot_discard_a_launch_that_is_between_its_record_and_its_process` |
| 2 | HIGH | After a restart the service ran publication, review and intake before it found a surviving stage; with scheduling switched off the watcher was not constructed at all | The slot is read from the records before any step (`execution_owner`); the step is constructed whenever schedule records exist and then only watches and settles | `test_a_restarted_service_finds_a_surviving_stage_before_it_does_anything_else` (scheduling on and off) |
| 3 | HIGH | The idle check reserved nothing: a manual `launch` or `integrate`, or a claim taken between the check and the launch, could run beside a stage | A shared execution slot: claims and the feature integration barrier refuse while a stage holds it; a stage is admitted in the claim's own critical section only while no claim exists and no barrier holds the project lock | `test_a_claim_and_a_feature_integration_are_refused_while_a_stage_holds_the_slot`, `test_a_stage_is_not_admitted_beside_a_claim_or_a_barrier_that_appears_at_the_last_moment` |
| 4 | MEDIUM | A reviewer process that outlived its killed `review` stage was invisible: the slot was freed while it ran | The step records the reviewer's tag when it finds the stage gone and the reviewer alive; the slot stays taken until it has ended | `test_a_reviewer_that_outlives_its_killed_review_stage_keeps_the_slot_and_is_collected_once` (a real stage process, SIGKILL) |
| 5 | MEDIUM | An unreadable schedule record blocked integration starts but not feature agents | Unknown ownership is `uncertain`: the service is `NEEDS_ATTENTION` and starts nothing; claims are refused; the record cannot be set aside while any stage process lives | `test_a_stage_record_that_becomes_unreadable_while_it_runs_stops_agents_too` |
| 6 | MEDIUM | A job a scheduled stage ended FAILED or STALE dropped out of the view and of the service's attention | Category `ended`: shown and `NEEDS_ATTENTION` until acknowledged with `schedule --resume` | `test_a_terminal_outcome_of_a_scheduled_stage_stays_in_attention_until_acknowledged` |
| 7 | MEDIUM | `Schedule.from_dict` accepted boolean versions, invalid job ids, malformed history and non-finite numbers (a `NaN` deadline never passes); a `null` file counted as absent | Exact types, finite bounded numbers, the job id and its file name, the launch's own log name; absent only when the file does not exist; the watcher's deadline comparison cannot be won by a bad number | `test_schedule_records_are_strict`, `test_a_stage_with_an_unusable_deadline_is_overdue_not_immortal` |
| 8 | MEDIUM | A stage process that could never be started was retried for ever outside both budgets | A failed start is a stall (growing delay, then a hold); it still costs no launch | `test_a_stage_process_that_cannot_start_is_a_stall_not_a_launch_and_is_held_in_the_end` |
| 9 | MEDIUM | The crash tests ran the stage's work in the test process: no real process death, no real lock | `ForkedStages`: the work runs in a separate process that is killed with SIGKILL inside a gate and at persisted checkpoints; restart-with-survivor, resume-versus-launch, live corruption and repeated start failure are tested | `test_fc_integration_worker_processes.py` and the tests above |
| 10 | LOW (documentation) | §66 said `status` shows the repair routing | The claim is limited to `integration recover` | — |

**Focused re-review (second round): REQUEST_CHANGES.** Findings 1, 2, 5, 6, 8, 9 and 10 were confirmed resolved; no lock deadlock and no Feature Job refusal on a checkout without schedule records were found; the clean areas stayed clean. Findings 3, 4 and 7 were partially resolved, and two new ones were reported. All are corrected:

| # | Severity | Finding | Correction | Test |
| --- | --- | --- | --- | --- |
| A (3) | HIGH | A launch reservation expired after 60 seconds: a launcher suspended between its record and its process lost the slot, and a claim could be taken before it started the stage | No timer: the reservation holds until the launch is settled (first bound to the launcher's life, then — fourth round — to a settlement in a critical section) | `test_an_unstarted_launch_holds_the_slot_until_it_is_settled_not_until_a_clock_or_its_launcher_gives_up`; a killed launcher in `…_concurrency.py` |
| B (4) | MEDIUM | A surviving reviewer was visible to other readers only after the step had noted it; `--resume` could discard its launch in that window | Reviewer ownership is derived from the tags on the job's own review attempts by every reader (`reviewer_processes`); `--resume` asks the same question | the reviewer test asserts ownership, a refused claim and a refused resume **before** any step |
| C (7) | MEDIUM | `last` and `history` were arbitrary dictionaries: a record that loaded could crash the view | Each closed launch has an exact, typed schema (also the watchdog entry) | `test_schedule_records_are_strict` |
| D | MEDIUM | The reset of an unreadable record looked only for stage-tagged processes, so a surviving reviewer was ignored | The reset is refused while any stage process or any reviewer of the job lives | the reviewer test (an unreadable record beside a living reviewer) |
| E | MEDIUM | A reviewer left behind was waited for without a deadline and skipped at a service stop | Stopped at its attempt's deadline, when its attempt is no longer open, and at a service stop | `test_a_reviewer_left_behind_is_stopped_at_its_attempts_deadline_and_the_review_is_repeated_by_m4s_rules`, `test_a_service_stop_also_stops_a_reviewer_its_dead_stage_left_behind` |

**Final focused re-review (third round): REQUEST_CHANGES.** B, C and D were confirmed resolved, the strict entry schema was confirmed to match what the code writes, and no deadlock and no write path were found. A and E were partially resolved, with three findings, all corrected:

| # | Severity | Finding | Correction | Test |
| --- | --- | --- | --- | --- |
| 1 (A) | HIGH | Two unsynchronized looks: no stage process yet, then the launcher spawns and dies, then the launcher looks dead — the slot read free while a stage ran | The stage process confirms its launch — with a fresh admission while unregistered — before doing anything (`confirm_launch`); the reservation itself was made independent of the launcher in the fourth round | `test_a_stage_process_confirms_its_launch_before_it_does_anything`, `test_the_real_stage_commands_do_nothing_when_their_launch_is_not_confirmed` (the real CLI, all four stages), `test_a_claim_that_overtakes_a_dead_launchers_stage_wins_and_the_stage_does_nothing` |
| 2 (E) | MEDIUM | The reviewer cleanup could stop a newer reviewer an operator's own `review` had started meanwhile | The cleanup takes the job's lock (busy: hands off), reloads the job under it and judges each attempt on its own | `test_reviewer_cleanup_keeps_its_hands_off_while_another_command_owns_the_job`; the deadline and service-stop tests |
| 3 (E) | MEDIUM | Tags read from a job file that is another job's record could be attributed, and signalled at shutdown | Tag discovery refuses a file that does not name the job (unknown, not free); a process is signalled only for tags of the strictly loaded job | `test_reviewer_tags_of_a_file_that_is_not_the_jobs_record_are_never_trusted_or_signalled` |

**Verification (fourth round).** One run ended without a verdict (a provider-side content filter stopped it); its last check showed one more interleaving, and the completed run that followed confirmed findings 2 and 3 and that interleaving resolved, found no ordering that admits a claim or a feature integration while a scheduled stage works, no new defect, no deadlock and no write path — and one remaining MEDIUM. Both are corrected:

| # | Severity | Finding | Correction | Test |
| --- | --- | --- | --- | --- |
| 4 | HIGH | The watcher closed a launch it had judged finished from an earlier read, although the stage process had confirmed that launch in between | Closing reads again inside the critical section that a confirmation uses and is abandoned if anything of the stage lives; `schedule --resume` and the service stop do the same | `test_a_launch_is_not_closed_once_its_stage_process_has_confirmed_it` |
| 1 (rest) | MEDIUM | A restarted service sampled the slot as free after the launcher's death and could run its intake or Draft PR review step while the orphaned stage process confirmed | A launch without a recorded process holds the slot until it is settled — the launcher's death no longer releases it. The restarted service therefore runs only its scheduling step first, which closes the launch (the late stage process then does nothing) or finds it confirmed | `test_an_unstarted_launch_holds_the_slot_until_it_is_settled_not_until_a_clock_or_its_launcher_gives_up`, `test_a_dead_launchers_unstarted_launch_is_not_free_for_a_restarted_service_either`; a killed real launcher in `…_concurrency.py` |

**Confirmation (fifth round): PASS.** The remaining finding was confirmed resolved, with the replacement-service ordering walked through step by step and a way out confirmed for each blocked case (no Worker running: `schedule --resume`; the scheduling step throttled by its interval; scheduling switched off with a record present). No new defect of any severity; no scheduled path to a PR creation, a merge request or `integration merge`; SHA binding, evidence and repair unchanged; no lock-order deadlock.

**Unresolved findings: none** (HIGH, MEDIUM or LOW). The reviews read the code and ran short in-memory checks in a read-only sandbox; they did not execute the test suite (it writes files). After the final verdict only test-rig code changed: the forked-stage rig now reaps every process it forks (the existing Worker smoke tests check that the test process has no unreaped child).
