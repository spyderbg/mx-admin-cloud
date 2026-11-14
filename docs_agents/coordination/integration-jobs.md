# Integration Jobs — M1/M2 Operator Notes, Decisions and Open Questions (W4.7)

> Owner role: **coordination**. Status: **M1 implemented and merged (PR #79). M2 — read-only inspection and operator authorization — implemented, awaiting operator review** (Issue #65, route B: trusted development in a separate clone). Nothing past M2 is implemented: no candidate, no gate, no review, no PR creation, no merge.
> Design (approved, unchanged): [existing-branch-integration-design.md](existing-branch-integration-design.md). Related: [operations.md](operations.md) · [feature-workflow.md](feature-workflow.md) · [github-intake.md](github-intake.md).
> Code: `feature_coordinator/integration.py` (record, state machine, persistence) · `integration_intake.py` (read-only checks) · `integration_commands.py` (CLI) · `integration_config.py` (`github.integration`). M2: `integration_approval.py` (authorization) · `integration_inspect.py` (inspection) · `integration_paths.py` (changed paths, classification) · `integration_github.py` (PR discovery, attribution) · `integration_isolation.py` (Git process isolation) · `integration_objects.py` (hash-verified objects, moved from `integration_intake.py`). Tests: `feature_coordinator/tests/test_fc_integration.py`, `test_fc_integration_intake.py`; M2: `test_fc_integration_approve.py`, `test_fc_integration_inspect.py`, `test_fc_integration_github.py`, `test_fc_integration_gitsafety.py` (shared rig: `fc_integration_rig.py`).

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

**Explicit blocker for enabling write-capable integration (M3 or later).** The M2 guarantee covers read-only processes only. Before any milestone builds a candidate, checks out, merges or pushes, it must:

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
