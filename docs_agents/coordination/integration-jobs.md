# Integration Jobs — M1 Operator Notes, Decisions and Open Questions (W4.7)

> Owner role: **coordination**. Status: **M1 implemented; Codex review findings (REQUEST_CHANGES) corrected, awaiting operator review** (Issue #65, route B: trusted development in a separate clone). Nothing past M1 is implemented.
> Design (approved, unchanged): [existing-branch-integration-design.md](existing-branch-integration-design.md). Related: [operations.md](operations.md) · [feature-workflow.md](feature-workflow.md) · [github-intake.md](github-intake.md).
> Code: `feature_coordinator/integration.py` (record, state machine, persistence) · `integration_intake.py` (read-only checks) · `integration_commands.py` (CLI) · `integration_config.py` (`github.integration`). Tests: `feature_coordinator/tests/test_fc_integration.py`, `test_fc_integration_intake.py`.

## 1. What M1 does and does not do

M1 is the design's milestone 1 (§8.5): the job record, the lifecycle state machine, persistence, and `intake` / `status` / `abort`.

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
