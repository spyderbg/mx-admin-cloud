# GitHub Integration — Feature and Task Issues (Phase 4)

> Owner role: **coordination**. Status: **Phase 4 deliverable, awaiting operator approval** (2026-10-17).
> Lifecycle and dependency rules: [task-lifecycle.md](task-lifecycle.md) · Rendered Issue and comment formats: [templates/](templates/README.md) · Commands: [operations §7](operations.md#7-github-issues-phase-4) · Design baseline: [feature-workflow.md](feature-workflow.md) · Status and live evidence: [phase-status.md](phase-status.md)
>
> Evidence tags: **[V]** verified by execution (the named test or command), **[S]** source-read, **[U]** unverified.

GitHub Issues are the **human-visible record** of feature work. They are never the lock service, never the source of task state, and never a source of permissions. The local coordinator state (`~/.local/state/mx-admin-agents/`, D-12) stays authoritative for claims, workers, process identity, locks, heartbeats, task states and execution permissions; GitHub shows a projection of it.

## 1. Boundaries

| Rule | How it is enforced | Evidence |
| --- | --- | --- |
| Independent of `scripts/mxfix/` and `scripts/mxissues/` | Separate adapter (`github_api.py`) and synchronizer (`issue_sync.py`); no import, no shared tracker code, no shared state | AST import scan `test_the_coordinator_is_independent_of_the_bug_workflow_and_the_backend` **[V]**; `git status` of both trees empty (live check Q4) **[V]** |
| Own label namespace `feature:*` | Every label write goes through `check_feature_labels`; labels are created only when missing and existing labels are never edited | `test_labels_are_feature_namespace_only_and_never_edited` **[V]**; live check Q2 **[V]** |
| Bug-workflow Issues are never written | An Issue with `mx-auto`, `source::*` or `severity::*` fails ownership verification | `test_ownership_needs_every_proof`, `test_forged_and_foreign_issues_are_never_adopted_or_modified` **[V]** |
| No bulk updates | The synchronizer only touches the Issues mapped to (or found by the exact idempotency key of) the records of one feature | live check Q compares every pre-existing Issue and PR before/after **[V]** |
| No GitHub Projects v2 | Plain Issues, labels and comments only | — |
| No pull request creation in Phase 4 | `github_api.LIVE_PULL_REQUESTS_ENABLED = False`; `create_pull_request` refuses before any request | `test_pull_requests_are_flagged_and_never_created`, `test_the_live_adapter_never_creates_a_pull_request` **[V]**; live check R/Q3 **[V]** |
| Writes limited to test features in Phase 4 | `github.write_scope: test-features-only`: a sync of a feature without `test: true` against the live adapter raises `SyncScopeError` before any write | `test_write_scope_limits_live_writes_to_test_features` **[V]** |

## 2. Architecture

```text
FeatureCoordinator (workflow.py) ── local state change inside StateStore.transaction()  (authoritative)
        │   after the transaction, outside every coordinator / project / role / Git lock:
        ▼
IssueSync (issue_sync.py) ── holds only github-sync.lock (one sync at a time)
   │  derives each Issue from its local record; persists an operation record before each remote write
   │  ingests validated /feature commands (the only GitHub → local path)
   ▼
TrackerClient protocol (issues.py)
   ├── GitHubClient (github_api.py) ── retries, rate limits, pacing, pagination, response validation
   │       └── GhCliTransport ── one `gh api --include` subprocess per request (existing gh login)
   └── MockIssueTracker (issues.py) ── tests, the demo, and `--tracker local` (JSON file + flock)
```

| Module | Responsibility |
| --- | --- |
| `github_api.py` | `GhCliTransport` (argv, JSON on stdin, deadline with process-group kill, `--include` parsing), `GitHubClient` (the `TrackerClient` for one repository), `parse_issue`/`parse_comment` (strict validation), `sanitize` |
| `issues.py` | Labels and their specs, `OwnershipMarker`, `verify_ownership`, the managed body block (`replace_block`, `extract_block`), comment operation markers, `TrackerClient`, `MockIssueTracker` |
| `issue_sync.py` | `IssueSync` (sync, reconcile, cleanup, inspect, find_owned), `IssueLink` and `OutboxEntry` records, command ingestion |
| `escalation.py` | `hold_task` / `release_task` (needs-human), shared by the CLI and validated commands |
| `live_test.py` | The Phase 4 acceptance scenario (§12) |
| `github_commands.py` | `feature.py github …`, `hold`, `release` |

**Why `gh api`:** the coordinator never requests, reads, stores or prints a token. `gh` uses its existing login (keyring or `GH_TOKEN`). `--include` returns the status line and headers, so errors and rate limits are classified exactly as with a REST client. No new Python dependency is needed.

## 3. Authentication and required capabilities

`python scripts/feature.py github status` (read-only) verifies, and names precisely what is missing:

| Capability | Check |
| --- | --- |
| `gh` installed | `shutil.which(github.gh_executable)` and `gh --version` |
| `gh` authenticated | `GET /user` returns a valid login (the token itself is never touched) |
| Repository access | `GET /repos/{repo}`: not archived |
| Issues enabled | `has_issues` |
| Issue/label permissions | `push` (or `maintain`/`admin`): creating labels and closing Issues need write access |
| Endpoints | list Issues, list labels, list pull requests (read-only) |
| Rate limit | `GET /rate_limit` (core budget) |

Observed on 2026-10-17 **[V]**: `gh` 2.86.0 (snap), authenticated as `spyderbg` (scopes `repo`, `workflow`, `gist`, `read:org`), repository private, Issues enabled, permissions admin/maintain/push/triage/pull, all endpoints OK. If authentication or a permission is missing, the affected live operation stops with `GitHubAuthError`/`GitHubPermissionError`; there is **no silent fallback** to the local tracker.

## 4. Issue schemas

### 4.1 Ownership marker (first line of every coordinator Issue)

```text
<!-- feature-coordinator:v1 coordinator=<coordinator-id> feature=<feature-id> task=<task-id|-> purpose=<feature-parent|component-task> key=<idempotency-key> -->
```

| Field | Meaning |
| --- | --- |
| `v1` | Marker schema version |
| `coordinator` | `github.coordinator_id` (`mx-admin-cloud.feature-coordinator`) |
| `feature`, `task` | Local record IDs (`task=-` for the parent) |
| `purpose` | `feature-parent` or `component-task` |
| `key` | Idempotency key: `<feature>/parent` or `<feature>/<task-id>` |

Values are restricted to `[A-Za-z0-9._:/@-]`; a body with zero or more than one marker is not owned. Rendered text is escaped (`<!--` → `&lt;!--`) so a title or acceptance criterion can never forge or break a marker (`test_rendered_text_cannot_forge_or_break_markers` **[V]**).

### 4.2 Body

```text
<ownership marker>
<!-- feature-coordinator:begin -->
… managed block (rewritten on every sync) …
<!-- feature-coordinator:end -->

… anything a human adds here is preserved …
```

- **Parent (feature) block:** state; branch, base and base SHA; roles; the one-PR policy; test flag; acceptance criteria; a task list of component Issues (`- [x] #12 …`, checked when verified); the dependency graph as Issue references; verification requirements per task; the PR candidate (rendered locally, not opened); recent history.
- **Component (task) block:** feature and parent Issue; role (one writer per role); state with its reason; writable paths (from the role, plus task grants); feature branch; required gates; whether it provides an API contract; acceptance criteria; dependencies and dependents as Issue references with their states; verification evidence (HEAD SHA, each gate's status, exit code and duration); commits.

Examples: [templates/feature-issue.md](templates/feature-issue.md), [templates/component-task-issue.md](templates/component-task-issue.md).

### 4.3 Title

`[Feature Coordinator Test] ` (test features only) + the feature title, or `[<role>] <task title>`. Titles are set at creation and **never used as identifiers**.

### 4.4 Comments

Every coordinator comment starts with an operation marker:

```text
<!-- feature-coordinator:op v1 coordinator=<coordinator-id> key=<idempotency-key> op=<operation-id> -->
```

| Operation ID | Comment |
| --- | --- |
| `h<start>-<end>` | Progress: the state-history entries `start…end-1` of the record (the history list is the outbox) |
| `o<16 hex>` | An outbox entry: dependency probe result, command acknowledgement, drift notice, test failure |
| `final` | The final verification comment of a test cleanup |

Examples: [templates/progress-comment.md](templates/progress-comment.md), [templates/command-comments.md](templates/command-comments.md).

## 5. Ownership verification

Before **any** write to an existing Issue, `verify_ownership` requires all of:

1. it is an Issue, not a pull request;
2. it carries no bug-workflow label;
3. it carries exactly one marker, **equal** to the marker expected for this coordinator, feature, task, purpose and key;
4. it was created by the coordinator's account (`GET /user`);
5. once mapped: its number and GitHub database ID equal the ones recorded locally when it was created or adopted.

An Issue that fails is reported as **protected** and left untouched; nothing else in that sync is blocked. Being open, carrying a `feature:*` label or mentioning a task ID never makes an Issue owned. Simulated human writes in the live test (labels, closing, command comments) are made only after `inspect` confirmed ownership.

## 6. Labels and state mapping

| Label | Kind | Applied when |
| --- | --- | --- |
| `feature:parent` | fixed | the feature's parent Issue |
| `feature:backend`, `feature:cli`, `feature:frontend`, `feature:automation`, `feature:coordination` | fixed | the task's role |
| `feature:test` | fixed | every Issue of a test feature |
| `feature:in-progress` | state | task `in-progress`; feature `active` or `integration` |
| `feature:blocked` | state | task `blocked`, `needs-human` or `failed`; feature `blocked` or `failed` (the exact state and reason are in the body) |
| `feature:review` | state | feature `review` (single PR candidate rendered) |
| `feature:verified` | state | task `verified`; feature `done` |

The synchronizer adds and removes labels **one at a time** (never replaces the set), only within the known `feature:*` set, so other labels and concurrent human labeling survive. Colors and descriptions: `issues.LABEL_SPECS`. Open/closed: an Issue is open unless its feature is `done` or a test cleanup closed it.

## 7. Synchronization

### 7.1 Local → GitHub (derived)

Each `sync` of a feature, for the parent and every task, in this order:

1. **Ensure labels** exist (create missing `feature:*` labels only).
2. **Ensure the Issue:** the mapped Issue (verified, §5); otherwise search by idempotency key (`GET /issues?labels=<fixed label>&creator=<account>&state=all`, filtered by exact marker) and **adopt** the oldest match, reporting any others as duplicates; otherwise persist `pending_create`, create, and record the number and database ID.
3. **Ingest commands** (§7.2).
4. **Reconcile:** managed block, labels, open/closed. A difference from what the coordinator *last wrote* (stored block hash, label set, state) is reported as **drift** and repaired from the local state.
5. **Comments:** new state-history entries, then pending outbox entries.

The workflow calls `sync` after every operation that changes state (`plan`, `activate`, `run_task` after the claim is released, `request_contract`, `probe_dependency`, `verify_task`, `reopen_task`, `integrate`, `hold`, `release`, `complete`). Failures are recorded as `tracker-sync-deferred` events; `feature.py github sync FEATURE --apply` completes them later.

### 7.2 GitHub → local (validated commands only)

| Accepted | Grammar (first non-empty line, comment ≤ 2,000 characters) | Effect |
| --- | --- | --- |
| hold | `/feature hold <task-id> <reason>` | Task → `needs-human` (not while it runs) |
| release | `/feature release <task-id>` | `needs-human` → `ready`, then blocked again at once if its dependencies are unverified |

Policy:

- The author must be listed in `github.command_authors` **and** have at least `github.command_min_permission` (`write`) on the repository (`GET /collaborators/{login}/permission`). Otherwise the comment is recorded as ignored and not answered.
- The target must be a task of the Issue's feature; on a task Issue only that task.
- A `release` never frees a dependency block ("released only by verified evidence"), and a release written before the current hold is **stale** and rejected. For holds that came from GitHub, comment IDs are compared, so no clock is involved; for local holds, timestamps are.
- Each comment is processed once: the command, the ingestion cursor, the audit event (`github-command`) and the acknowledgement comment are written in one local transaction.
- Coordinator comments (operation marker plus coordinator account) are never parsed as commands.
- **Nothing else is input:** labels, body edits, closing, reopening and any other comment text never change local state. No command grants a path, a tool, shell access or any permission, and nothing in an Issue is executed.

## 8. Idempotency and recovery

| Failure point | What is persisted first | Recovery on the next sync | Test |
| --- | --- | --- | --- |
| Crash after GitHub created an Issue, before the number was recorded | `IssueLink.pending_create` | Search by key (re-searching for up to 3 × `create_settle_seconds` while a pending create is recorded) → adopt; never create twice | `test_crash_after_remote_create_is_recovered_without_a_duplicate` **[V]**; live A1–A3 **[V]** |
| Ambiguous create failure (timeout, 5xx, connection loss after sending) | same | `GitHubUncertainWriteError` stops the sync; the next sync searches first | `test_no_http_response_is_retried_for_reads_and_uncertain_for_creates` **[V]** |
| Crash after a progress comment was posted | `IssueLink.pending_history` | Find the comment by `op=h<start>-…`, advance the cursor | `test_crash_after_a_remote_comment_never_duplicates_it[crash-after-comment]` **[V]** |
| Crash after an outbox comment was posted | `OutboxEntry.attempts` | Find the comment by `op=o…`, mark it done | `…[crash-after-outbox]` **[V]** |
| GitHub unavailable mid-sync | — | Local state and the dependency graph are untouched (the sync never edits dependencies); a later sync completes | `test_github_failure_never_corrupts_local_state_and_recovers` **[V]** |
| Coordinator restarted | everything above is on disk | `github reconcile` in a new process: same mapping, no writes when nothing changed; orphans and duplicates reported, never modified | `test_reconcile_reports_orphans_without_touching_them` **[V]**; live N–P **[V]** |
| Two syncs at once | — | `github-sync.lock` serializes them | `test_concurrent_sync_processes_never_create_duplicates` (3 processes) **[V]** |

Repeated synchronization is a no-op: the second sync of an unchanged feature sends **no write request** (`test_plan_creates_one_owned_issue_per_record_and_resync_writes_nothing` **[V]**, live L **[V]**).

## 9. Error handling

| Response | Classification | Behavior |
| --- | --- | --- |
| 2xx | success | — |
| 401, or `gh` not logged in | `GitHubAuthError` | Not retried; "run `gh auth login`" |
| 403 without rate-limit signals | `GitHubPermissionError` | Not retried; names the missing capability |
| 403/429 rate limit (secondary-limit message, `Retry-After`, or `X-RateLimit-Remaining: 0`) | wait | `Retry-After` if present; else until `X-RateLimit-Reset` for a primary limit; else at least 60 s, doubling. A wait above `max_rate_limit_wait_seconds` (120 s) → `GitHubRateLimitError`, the sync is deferred. A rate-limited request was rejected, so even a create is safely re-sent |
| 404 / 410 | `GitHubNotFoundError` | A mapped Issue that disappeared is reported, **not recreated**; removing an absent label counts as success |
| 409 / 422 | `GitHubValidationError` | Not retried; creating an existing label counts as success |
| 5xx | unavailable | Reads and idempotent writes (PATCH, label add/remove): bounded retries (`max_retries` 3) with exponential backoff (1 s, 2 s, 4 s, ≤ 30 s). Creates: `GitHubUncertainWriteError`, never blindly re-sent |
| Timeout / no response | `TransportFailure` | The whole `gh` process group is killed at `request_timeout_seconds`; reads retried; creates uncertain |
| Unexpected format | `GitHubResponseError` | Every field type-checked (number, ID, state, labels, login, URL scheme, timestamps); more than `max_pages` pages is an error, never a silent partial result |

Every write is paced by `min_write_interval_seconds` (1 s), following GitHub's guidance for content-creating requests. Error messages are sanitized: token patterns are redacted, terminal control sequences are stripped, and length is bounded (`test_sanitize_redacts_tokens_and_strips_terminal_escapes` **[V]**).

## 10. Concurrency

- **No coordinator lock during network requests.** A sync holds only `github-sync.lock`, which no worker uses. Every tracker request runs with `coordinator.lock`, `project.lock` and the role locks free (`test_no_coordinator_lock_is_held_during_tracker_requests`, a tracker that tries to take them on every call **[V]**).
- **Workers are never delayed:** `run_task` syncs only after it released its role slot; a sync runs while another process holds a role claim (`test_sync_runs_while_a_worker_holds_its_claim_and_one_sync_at_a_time` **[V]**).
- **No stale overwrite:** every local write of the synchronizer reloads the record inside a short `StateStore.transaction()`. It writes only Issue mappings, cursors, outbox entries and — through validated commands — `needs-human` transitions. A record already mapped to a different Issue is a conflict reported for a human, never overwritten.
- **Labels are not locks:** claims and locks stay local `flock`s (Phase 2–3); live check I1 shows the atomic claim rejecting a duplicate claim and a second Backend writer **[V]**.
- **Concurrent Issue edits:** labels are changed one by one, and comments are append-only. The body is replaced only when its managed block differs, using the latest read. GitHub offers no compare-and-swap for Issue bodies, so a human edit made between that read and the write can be lost (§14).

## 11. Security

- No token is requested, read, stored or printed. Tokens never appear in Git, Issue bodies, comments, logs, documentation or coordinator records; `gh auth status` was printed only with the token line redacted.
- GitHub responses are untrusted. They are validated (§9), sanitized for display (`feature.py github inspect`), never executed, and never allowed to set permissions or paths.
- Command policy: §7.2. Rendered text is escaped against marker forgery (§4.1).
- `AUTH_DISABLED`, `DEBUG_MODE`, production content and databases, the remote Qwen server and D-15/D-16/D-18 are untouched by Phase 4.

## 12. Live acceptance test

```bash
python scripts/feature.py github status                                   # read-only capability check
FC_GITHUB_LIVE=1 FC_GITHUB_LIVE_DIR=~/.local/state/mx-admin-agents/github-tests \
  .venv/bin/python -m pytest -p no:cacheprovider scripts/tests/feature_coordinator/test_fc_github_live.py -v
# or, equivalently, the operator command (it prints every check):
python scripts/feature.py github test-lifecycle --apply [--workdir DIR] [--no-cleanup]
python scripts/feature.py --tracker local github test-lifecycle --apply   # rehearsal, no GitHub contact
```

The scenario (`live_test.py`) uses a **new** feature ID (`fc-p4-<UTC timestamp>`), a disposable Git repository and a state directory under the work directory, and the real coordinator, Git coordinator and **bubblewrap-sandboxed gates**. Only the agents are scripted. It creates exactly four Issues (one parent, three components) and walks steps A–R of the assignment ([task-lifecycle §8](task-lifecycle.md#8-worked-example-the-phase-4-live-scenario)). The results of the run are recorded in [phase-status](phase-status.md).

**Cleanup** (only if every check passed): a final verification comment (operation ID `final`) listing the checks, the feature ID and task IDs on each test Issue, then close with reason "completed", parent last. The Issues stay in GitHub for audit. `feature.py github cleanup-test FEATURE --apply --state-dir <work>/<feature>/state` repeats it safely: it refuses non-test features and Issues without `feature:test` or a valid ownership proof, skips closed Issues, and finds the final comment by its operation ID (a repeat sends no write). If a check fails, the Issues stay open with a failure comment on the parent, and the report lists their numbers and URLs.

## 13. Configuration (`github:` in [project-config.example.yaml](project-config.example.yaml))

`repository`, `coordinator_id`, `transport` (`gh-cli`), `gh_executable`, `hostname`, `write_scope` (`test-features-only` | `all-features`), `test_title_prefix`, `command_authors`, `command_min_permission`, `request_timeout_seconds`, `max_retries`, `backoff_base_seconds`, `max_backoff_seconds`, `max_rate_limit_wait_seconds`, `min_write_interval_seconds`, `sync_lock_timeout_seconds`, `create_settle_seconds`, `max_pages`. Unknown keys (for example a token) are rejected (`test_github_configuration_is_validated` **[V]**).

## 14. Limitations

- **Body updates are last-writer-wins.** GitHub has no conditional update for Issue bodies. Edits are confined to the managed block and skipped when it is unchanged; text outside the block is preserved from the latest read.
- **Discovery by label.** An Issue whose creation was interrupted *and* whose fixed label a human removed before the next sync would not be found by the key search, and a duplicate would be created. `reconcile` reports duplicates; it never deletes them.
- **Relationships are Issue references** (task lists and `#N` links), not GitHub's sub-issue or "blocked by" APIs (not required; possible later).
- **One account.** Coordinator and operator share the `spyderbg` account. Coordinator comments are told apart by their operation marker; a human comment that copies such a marker is ignored as a command, which is the safe direction.
- **Pull requests** are refused until a later phase sets `LIVE_PULL_REQUESTS_ENABLED` with operator approval. The feature keeps its single local PR candidate.
- **Write scope:** test features only, until the operator approves `all-features` (Phase 5).
