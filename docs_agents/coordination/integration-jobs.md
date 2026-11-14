# Integration Jobs — M1/M2/M3 Operator Notes, Decisions and Open Questions (W4.7)

> Owner role: **coordination**. Status: **M1 (PR #79), M2 (PR #80) and Security S1 (PR #81) implemented and merged. M3 — the integration candidate and its validation gates — implemented; the Codex functional review's findings F1–F6 are corrected (§29), awaiting a focused re-review** (Issue #65, route B: trusted development in a separate clone). Nothing past M3 is implemented: no independent review (M4), no merge approval or merge (M5), no PR creation.
> Design (approved, unchanged): [existing-branch-integration-design.md](existing-branch-integration-design.md). Related: [operations.md](operations.md) · [feature-workflow.md](feature-workflow.md) · [github-intake.md](github-intake.md).
> Code: `feature_coordinator/integration.py` (record, state machine, persistence) · `integration_intake.py` (read-only checks) · `integration_commands.py` (CLI) · `integration_config.py` (`github.integration`). M2: `integration_approval.py` (authorization) · `integration_inspect.py` (inspection) · `integration_paths.py` (changed paths, classification) · `integration_github.py` (PR discovery, attribution) · `integration_isolation.py` (Git process isolation) · `integration_objects.py` (hash-verified objects, moved from `integration_intake.py`). M3: `integration_validate.py` (`validate`) · `integration_candidate.py` (candidate repository, merge, workspace) · `integration_gates.py` (gate plan, sandboxed runner, evidence) · `integration_schema.py` (the record's schema checks, split out of `integration.py`, which re-exports them). Tests: `feature_coordinator/tests/test_fc_integration.py`, `test_fc_integration_intake.py`; M2: `test_fc_integration_approve.py`, `test_fc_integration_inspect.py`, `test_fc_integration_github.py`, `test_fc_integration_gitsafety.py`; M3: `test_fc_integration_validate.py` (shared rig: `fc_integration_rig.py`).

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
