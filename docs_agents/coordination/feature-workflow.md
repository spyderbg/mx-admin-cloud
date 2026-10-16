# Feature Workflow — Coordinator Design and Phase 2 Skeleton (v5)

> Owner role: **coordination**. Status: **Phase 2 deliverable, awaiting operator approval** (2026-10-17).
> Governing assignment: [`agents_prompt_v5.md`](../../docs/multiple_agents/agents_prompt_v5.md) §11 Phase 2. Design baseline: [architecture §11](../architecture/README.md#11-v4-feature-development-architecture-design-baseline-for-phases-27), remote Qwen contract [§11.11](../architecture/README.md#1111-remote-openai-compatible-qwen-integration-contract-v5-101), decisions [§12](../architecture/README.md#12-decision-register). Progress: [`phase-status.md`](phase-status.md).
>
> Evidence tags: **[V]** verified by execution (a test or command named here), **[S]** verified by reading source, **[U]** unverified.

The feature coordinator is a small, **stdlib-only** Python package that runs one feature at a time on this Linux host, on **one shared checkout** and **one `feature/<slug>` branch**. Role agents edit disjoint paths in parallel; a single Git coordinator lands their commits one at a time; exactly **one PR** is opened per feature, after all gates pass and only with operator approval.

Phase 2 delivers the design, a working skeleton with **mock runners, a mock issue tracker and a disabled live model transport**, and tests. Phase 2 has no command that changes the real checkout, pushes, talks to GitHub, starts an agent or contacts a model.

## 1. Boundaries

| Area | Rule | Evidence |
| --- | --- | --- |
| Bug workflow (`scripts/mxfix/**`, `scripts/mxissues/**`, `scripts/issues.py`, `testenv.py run`) | Independent system. Never imported, called, extended, scheduled or tested. Its paths are **protected** for every role; its issues and labels (`mx-auto`, `source::*`, `severity::*`) are never written | `test_the_coordinator_is_independent_of_the_bug_workflow_and_the_backend` (AST import scan + empty `git status` for both trees), `test_bug_workflow_issues_are_never_touched` **[V]** |
| Application code (`backend/`, `cli/`, `frontend/`) | Not imported. Reached only as **gate commands** run in a subprocess (§11) and as role-owned paths the runners edit | Same AST scan (`backend`, `src`, `testenvlib` forbidden) **[V]** |
| Generic scripts | Called as subprocess gates (`export_openapi.py --docs-agents --check`, `check_docs_agents.py`). Generators are owned by the destination's role (§6) | §11 |
| Runtime state | Outside Git, under `$XDG_STATE_HOME/mx-admin-agents/` (D-12); never committed | §5 |
| Remote Qwen | Inference only, over an OpenAI-compatible Chat Completions API. Agents, tools, files and Git stay local. **No request was sent in Phase 2** | §9 |
| GitHub | Interface plus in-memory/JSON mock only. A live adapter is Phase 4 and needs approval | §8 |

## 2. Package map

`scripts/feature_coordinator/` (D-13), entry point `scripts/feature.py`. About 3,900 lines including docstrings; Python 3.12 standard library, plus PyYAML only for loading the YAML example **[V]**.

| Module | Responsibility |
| --- | --- |
| `errors.py` | One exception hierarchy rooted at `CoordinatorError` (busy locks, scope violations, Git refusals, provider errors…) |
| `config.py` | `ProjectConfig`: roles → owned/excluded paths and provider, protected and approval-required paths, gates, branch and PR policy, state-dir policy. Enforces the v5 ceilings (§3.1) |
| `providers.py` | Provider profiles: Claude Code, Codex, and the exact remote-Qwen profile; URL joining, endpoint resolution from the environment, transport/disclosure gates, context budget, redaction |
| `paths.py` | Deny-by-default write policy and `ScopedWorkspace` (the only way runners write) |
| `statedir.py` | State-dir resolution, filesystem suitability (`/proc/self/mountinfo`), private creation, two-process `flock` self-test |
| `locks.py` | `flock(2)` wrappers and the process identity (PID + start time + boot ID) |
| `model.py` | Feature/task records, state machines, evidence, dependency DAG |
| `store.py` | Atomic, revision-checked JSON records and the audit log, under `coordinator.lock` |
| `claims.py` | Atomic task + role-slot claims, full-project exclusivity, crash recovery |
| `gitops.py` | The single Git mutation path: manifest commits via a private index and compare-and-swap; quiescence-gated branch operations; push plan (dry run) |
| `issues.py` | Issue-tracker interface and `MockIssueTracker` |
| `runners.py` | Runner interface; mock Claude Code and Codex runners; the OpenAI-compatible runner with mock and disabled transports |
| `workflow.py` | `FeatureCoordinator`: plan → activate → run tasks → integrate → one PR → done |
| `demo.py` | The mock end-to-end feature in a temporary repository |
| `cli.py` | The dry-run CLI (§12) |

## 3. Configuration

The documented example is [`project-config.example.yaml`](project-config.example.yaml). It must load **equal to** the built-in `default_config()` (`test_documented_example_equals_the_built_in_configuration` **[V]**; `python scripts/feature.py config`).

| Role | Provider (runner) | Owned paths | Excluded |
| --- | --- | --- | --- |
| backend | `claude` (Claude Code) | `backend/**`, `docs_agents/backend/**` | `backend/data/**` |
| cli | `claude` (Claude Code) | `cli/**`, `docs_agents/cli/**` | — |
| frontend | `codex` (Codex) | `frontend/**`, `docs_agents/frontend/**` | — |
| automation | `remote_qwen` (OpenAI-compatible) | `scripts/**`, `test_environment/**`, `docs_agents/automation/**` | `scripts/mxfix/**`, `scripts/mxissues/**`, `scripts/deploy.sh` |
| coordination | `claude` (configurable) | `docs_agents/coordination/**` | — |

- **Protected** (no role, no grant): `.git/**`, `.env`, `.env.*`, `content/**`, `data/**`, `backend/data/**`, `scripts/mxfix/**`, `scripts/mxissues/**`.
- **Approval-required** (only with a task grant): root instruction files (`CLAUDE.md`, `AGENTS.md`, `README.md`), `.gitignore`, `.gitlab-ci.yml`, `testenv.toml`, `scripts/deploy.sh`, agent-tool configuration (`.claude/**`, `.cursor/**`, `.opencode/**`, `.aiassistant/**`, `.agents/**`), `.github/**`, `docker/**`, `docs_agents/architecture/**`, `docs_agents/README.md`.
- Anything else, for example `package.json` at the root, is **unowned and denied**.

### 3.1 Ceilings the loader refuses to relax

Each is a negative test in `test_fc_config_providers.py` **[V]**:

- exactly the five roles;
- `max_writers` fixed at 1 per role;
- pairwise disjoint owned scopes, written only as `dir/**` or exact files;
- `execution_host: single-linux`, `checkout_mode: shared-single-feature`;
- Git flags: single mutation coordinator, serialized commit/push/branch/PR, no branch switches by agents, no per-agent worktrees;
- `pr_policy: one-after-all-gates`;
- branch pattern `feature/{slug}`;
- endpoints and keys come **only** from environment variables: a `base_url` or `api_key` key in YAML is rejected;
- a Qwen `effective_max_input_tokens` above the computed cap is rejected;
- FUSE filesystems cannot be allowed for state.

## 4. Data model and schema proposal

Records are versioned JSON (`schema_version: 1`); an unknown version is refused on load. IDs match `^[a-z0-9][a-z0-9._-]{0,79}$`, so no ID can traverse paths. A task ID is `<feature>.<key>`.

### 4.1 State directory layout (D-12)

```text
$XDG_STATE_HOME/mx-admin-agents/      # default ~/.local/state/mx-admin-agents, mode 0700
├── features/<feature-id>.json        # Feature records
├── tasks/<task-id>.json              # Task records
├── claims/<task-id>.json             # live (or stale) claims
├── flags/full-project.json           # present while a full-project session is requested
├── locks/coordinator.lock  project.lock  role-<role>.lock  git.lock
├── events.jsonl                      # append-only audit trail
└── tmp/                              # private Git index files
```

Writes are atomic: temp file → `fsync` → `rename` → `fsync` of the directory. Every record carries a `revision`. A save whose revision is stale ("modified concurrently") is refused, and every save must happen inside `StateStore.transaction()`, which holds `coordinator.lock` (`test_state_writes_are_revision_checked` **[V]**).

### 4.2 Records (abridged real output of the demo)

```json
{
  "schema_version": 1, "id": "items-export.view", "feature_id": "items-export", "role": "frontend",
  "title": "Export button in the category view", "acceptance": [], "depends_on": [],
  "required_gates": ["frontend-type-check", "frontend-tests"], "provides_contract": false, "grants": [],
  "state": "verified", "blocked_reason": "", "issue_number": 2,
  "commits": ["aa6bfe9eb1bf6f2bc5d7d2eff212cb51303f47a7"],
  "evidence": {"head_sha": "aa6bfe9eb1bf…", "recorded_at": "2026-10-16T22:22:16Z", "summary": "",
               "gates": [{"gate": "frontend-type-check", "exit_code": 0, "command": ["npm", "run", "type-check"]}, "…"]},
  "history": [{"at": "…", "from_state": "proposed", "to_state": "ready", "reason": "feature activated"}, "…"],
  "revision": 6
}
```

- A **Feature** record holds: `id`, `slug`, `title`, `base_branch`, `branch`, `base_sha`, `acceptance`, `state`, `task_ids`, `issue_number`, `pull_request` (candidate: head, base, title, body, `head_sha`, `url`, `ci_state`), `integration_evidence`, `merged_sha`, `history` and `revision`.
- A **claim** record holds: `task_id`, `feature_id`, `role`, a random `token`, `identity` (`pid`, `start_ticks`, `boot_id`), `branch`, `head_sha`, `acquired_at`, `heartbeat_at` and `checkpoint`.
- An **event** is one JSON line: `{"at", "kind", …}`. Kinds include `claim`, `release`, `recovered`, `task-finished`, `runner-unavailable`, `commit-deferred`, `verification-deferred`, `task-result-superseded`, `contract-requested`, `feature-integrated` and `feature-done`.

## 5. State machines

### 5.1 Task

| From | Allowed to |
| --- | --- |
| `proposed` | `ready`, `blocked`, `needs-human` |
| `ready` | `in-progress` (claim only), `blocked`, `needs-human` |
| `in-progress` | `verified`, `ready` (yield or recovery), `blocked`, `failed`, `needs-human` |
| `verified` | `ready`, `blocked` (re-opened, or a dependency changed) |
| `blocked` | `ready`, `needs-human`, `failed` |
| `failed` | `ready`, `needs-human` |
| `needs-human` | `ready`, `failed` |

Rules (each tested in `test_fc_model.py` **[V]**):

- Re-applying the current state is an **idempotent no-op**: it returns `False` and records no history.
- `verified` requires evidence: a 40-hex `head_sha` **equal to the branch HEAD**, and every required gate present with exit code 0.
- Leaving `verified` drops the evidence.

### 5.2 Feature

| From | Allowed to |
| --- | --- |
| `planned` | `active`, `blocked`, `failed` |
| `active` | `integration`, `blocked`, `failed` |
| `integration` | `review`, `active` (a gate failed), `blocked`, `failed` |
| `review` | `done`, `active`, `blocked`, `failed` |
| `blocked` | `planned`, `active`, `failed` |
| `failed` | `planned` |
| `done` | — (terminal) |

`done` requires a PR **and** a `merged_sha`. Creating the PR is never enough (`test_feature_done_needs_a_merged_pr_not_just_pr_creation` **[V]**). Only **one feature is active per checkout**; others stay `planned` (`test_one_active_feature_per_checkout` **[V]**).

### 5.3 Dependencies (DAG)

- `plan()` validates the task graph: unknown IDs, self-dependencies and cycles are rejected; `validate_dag` returns a topological order.
- A task whose dependencies are not all `verified` is `blocked` with the reason `waiting for <ids>`. `refresh_readiness` resumes it automatically when they are.
- A block for any other reason (a human decision) is **never** auto-resumed.
- **Contract requests:** when a consumer (for example the CLI) needs an API that does not exist, `request_contract(consumer, key=…)` creates — or **reuses** — exactly one Backend task `<feature>.contract-<key>` with gates `contract-check` + `backend-tests`, and one `feature:backend` issue. Only that consumer is blocked; unrelated tasks continue. A verified consumer must be re-opened first.
- **Unblocking** = the Backend task is `verified` on the shared branch, i.e. its commit landed and its gates passed at the branch HEAD.
- **Re-opening** a verified task (`reopen_task`) blocks every transitively dependent verified task, so they re-verify against the new contract.

Tests: `test_blocked_consumer_does_not_freeze_unrelated_work`, `test_contract_requests_are_deduplicated`, `test_reopened_contract_invalidates_its_consumer`, `test_invalidate_dependents_after_contract_change` **[V]**.

## 6. Write scopes (deny-by-default)

`PathPolicy.check(role, path, grants)` normalizes lexically, then resolves symlinks, and allows a write only if every rule below passes:

1. the path is inside the repository: no `..` escape, no absolute path outside, no NUL, not empty;
2. it is not protected;
3. it is not approval-required, unless the task has a grant for it — and a grant can never name a protected path or a non-approval path (`validate_grants`);
4. it is inside the role's owned scope and outside its exclusions; the `full-project` pseudo-role owns nothing and needs explicit grants;
5. the resolved path stays in the repository and still belongs to the same role, so a symlink can neither escape the repository nor reach into another role's tree; a symlink entry itself cannot be written.

Generated outputs follow **destination ownership**. For example, `frontend/src/types/*Schemas.ts` (from `generate_ts_schemas.py`) and `frontend/src/api/*Client.ts` belong to Frontend, and Backend-owned OpenAPI files belong to Backend; a denial names the generator.

Runners write only through `ScopedWorkspace`, which enforces the policy **at write time** and refuses to read `.env*`. In Phase 2 this is in-process enforcement; OS containment (bwrap/Landlock) is Phase 3.

Tests: 34 in `test_fc_paths.py` **[V]**.

## 7. Locks, claims and recovery (D-3, D-12)

### 7.1 Locks

| Lock file | Mode | Held by | Purpose |
| --- | --- | --- | --- |
| `coordinator.lock` | exclusive, short | `StateStore.transaction()` | Serializes state reads-modify-writes |
| `project.lock` | **shared** by each scoped writer; **exclusive** for branch creation/switch, full-project sessions and the integration barrier | the claiming process | Quiescence: exclusive is impossible while any writer is active |
| `role-<role>.lock` | exclusive | the claiming process | **One active writer per specialization** |
| `git.lock` | exclusive, short | `GitCoordinator` | Serializes every Git mutation |

- **Lock order:** `coordinator → project → role → git`.
- All locks are `flock(2)`: the kernel drops them when the holder dies, including on `SIGKILL` (`test_lock_held_by_another_process_and_released_when_it_crashes` **[V]**).
- The state directory must be on a local filesystem. `fuse*` (including `fuseblk`, where this checkout lives), NFS/CIFS/SMB, sshfs and any unknown type are rejected (`test_unsupported_filesystems_are_rejected`, `test_the_repository_checkout_would_be_rejected_as_state_location` **[V]**).
- `python scripts/feature.py doctor --apply` created `~/.local/state/mx-admin-agents` (ext4, mode 0700) on this host and passed the two-process `flock` contention self-test on 2026-10-17 **[V]**.

### 7.2 Claiming a task (atomic)

Inside one `coordinator.lock` section:

1. refuse while a full-project session is requested;
2. refuse if a claim record exists — "live" if its holder is alive, otherwise "stale, run recovery";
3. refuse unless the task is `ready` and the role matches;
4. take `project.lock` shared;
5. take `role-<role>.lock` exclusive (fails with `RoleBusyError` when another writer of that role is active);
6. write the claim (PID + `/proc` start ticks + boot ID + random token) and move the task to `in-progress`.

The two flocks stay held until `release()` or process death.

Tests in `test_fc_claims.py` **[V]**:

- six racing processes claim one task: exactly one wins;
- six racing processes claim two tasks of the same role: exactly one writer;
- three distinct roles all proceed in parallel;
- a same-role claim from another process is rejected.

### 7.3 Full-project sessions

1. `request_full_project(reason)` sets a flag, so no new scoped claims start.
2. Running writers are **not preempted**.
3. `claim_full_project()` succeeds only when `project.lock` can be taken exclusively; otherwise `WritersActiveError` names the active roles.
4. Release clears the flag.

Test: `test_full_project_waits_for_scoped_writers_then_runs_exclusively` **[V]**.

### 7.4 Recovery

`recover()` reclaims a claim only when its holder is **provably gone**: the PID is absent, its start time differs (PID reuse), it is a zombie, or the boot ID differs. It then deletes the record, returns the task to `ready` and logs `recovered`.

- A live holder is never touched.
- A role lock held by a process that has no claim record is reported as an `unknown_lock_holder` and never stolen.
- A stale full-project flag whose requester is gone is cleared.

Tests: `test_crashed_worker_is_recovered_without_a_duplicate_writer` (SIGKILL), `test_recovery_never_steals_a_role_lock_held_without_a_claim`, `test_stale_full_project_request_is_cleared`, `test_zombie_counts_as_dead`, `test_process_identity_detects_death_reuse_and_reboot` **[V]**.

## 8. Git coordinator protocol (shared checkout)

Agents never run mutating Git commands. `GitCoordinator` is the only mutation path.

### 8.1 Role-scoped commit by manifest

A runner reports the changed paths; the coordinator hashes them (SHA-256, or `null` for a deletion) into a `CommitRequest`. Then, under `git.lock`:

1. the checkout must be on the feature branch;
2. every manifest path passes the role's write policy;
3. the **shared index must have nothing staged**, otherwise `ForeignStagedContentError`;
4. if HEAD moved since the request was prepared, continue only when the new commits descend from the expected HEAD and touch **none** of the manifest paths (disjoint parallel work); otherwise `HeadMismatchError`;
5. each file still has its declared hash, otherwise `UnstableFileError`;
6. build the tree in a **private index** (`GIT_INDEX_FILE` under the state `tmp/`): `read-tree HEAD`, then add or remove **only** the manifest paths;
7. re-hash the staged blobs against the declared hashes;
8. `commit-tree`, then `update-ref <branch> <new> <old>` (atomic compare-and-swap);
9. refresh only those paths in the shared index.

Plumbing runs no hooks. Commit trailers record `Feature:`, `Feature-Task:` and `Feature-Role:`.

**Result:** another agent's unfinished, modified or untracked files can never enter a commit (`test_commit_contains_only_the_manifest_never_another_agents_unfinished_work` **[V]**), and three processes committing in parallel produce a linear chain of three single-role commits with a clean `git fsck` (`test_parallel_commits_from_two_processes_are_serialized` **[V]**).

### 8.2 Tree-changing operations

`create_feature_branch` and `switch_branch` require `project.lock` **exclusive** (no writer active) and a clean checkout. An existing branch is never reused. No separate worktrees are created.

### 8.3 Push and PR

`push_plan()` renders a non-force `git push --set-upstream origin refs/heads/<b>:refs/heads/<b>`. **It is never executed in Phase 2.**

## 9. Providers and runners

### 9.1 Runners (Phase 2: mocks only)

| Role | Runner | Planned real invocation (rendered, never executed) |
| --- | --- | --- |
| backend, cli, coordination | `MockClaudeCodeRunner` | `claude -p --output-format json --setting-sources user,local --permission-mode acceptEdits --no-session-persistence` |
| frontend | `MockCodexRunner` | `codex exec --json --sandbox workspace-write --ephemeral -C <repo>` |
| automation | `OpenAICompatibleRunner` | Local tool loop over Chat Completions (§9.3) |

- `--setting-sources user,local` skips the repository's project settings (`bypassPermissions`) **without editing them**. No planned invocation uses a bypass or `dangerously…` flag (`test_planned_cli_invocations_avoid_bypass_modes` **[V]**).
- The mock runners apply scripted edits through `ScopedWorkspace`. An out-of-scope edit returns `rejected` and the file is unchanged.

### 9.2 Runner probe (read-only `--help`, no model calls; B-6) **[V]**

- **`claude` 2.1.302:**
  - `-p`, `--output-format text|json|stream-json`;
  - `--permission-mode acceptEdits|auto|bypassPermissions|manual|dontAsk|plan`, `--permission-prompts`;
  - `--allowedTools`/`--disallowedTools`/`--tools`, `--add-dir`, `--settings`, `--setting-sources`;
  - `--strict-mcp-config`, `--no-session-persistence`.
- **`codex exec` 0.163.0:**
  - `-s read-only|workspace-write|danger-full-access`, `-C`, `--add-dir`, `-m`, `-p/--profile`, `-c key=value`;
  - `--oss` (lmstudio/ollama only), `--json`, `--output-schema`, `-o`, `--ephemeral`, `--skip-git-repo-check`.
- **OpenCode** 1.18.35 is installed (`-m provider/model`).

**D-17 shortlist** for a local runner for the remote Qwen model:

1. **the coordinator's own Chat Completions tool loop** (implemented here against mocks; recommended, because it keeps edit tools and path checks in code we test);
2. Codex with a custom OpenAI-compatible provider via `-c` **[U]**;
3. OpenCode with a custom provider **[U]**.

### 9.3 Remote Qwen profile (exact v5 values)

| Field | Value |
| --- | --- |
| Model ID / display name | `Qwen3.6-35B-A3B-Q4_K_M.gguf` / Qwen 3.6 35B A3B |
| Context / output / declared input | 131,072 / 16,384 / 128,000 |
| Hard input cap (context − output) | **114,688** |
| Working input cap (hard − 4,096 margin) | **110,592** |
| Temperature | 0.2 |
| Client edit tools | `apply-patch`, `multi-find-replace`, `find-replace` (only the two find-replace tools are implemented; `apply-patch` is refused) |
| Timeouts / retries | connect 10 s, request 180 s; 2 retries with backoff 0.5 s, 1.0 s |
| Endpoint, model override, key | **environment only:** `AGENT_QWEN_BASE_URL`, `AGENT_QWEN_MODEL`, `AGENT_QWEN_API_KEY` |
| Declared capabilities | tool calling, streaming, thinking, vision — **all disabled until validated** (`validated_capabilities` is empty) |
| Transport | `require_tls: true`; `plain_http_exception: false` (D-15); `code_disclosure_allowed: false` (D-16) |

- `chat_completions_url` appends `/chat/completions` exactly once and accepts a base URL with or without a trailing slash, or a full URL. It rejects duplicated `/v1` or suffixes, queries, fragments, embedded credentials and non-HTTP schemes.
- Neither the endpoint address nor the key is printed (`feature.py providers`), and both are absent from tracked files.
- The token estimate is deliberately conservative: UTF-8 bytes / 3.

### 9.4 Offline and error behavior (all mocked)

| Condition | Runner status | Task outcome | Other roles | Test |
| --- | --- | --- | --- | --- |
| `AGENT_QWEN_BASE_URL` unset (the default) or malformed | `unavailable` | back to `ready`; claim released; `runner-unavailable` event | unaffected | `test_unconfigured_endpoint_marks_the_role_unavailable`, `test_unavailable_remote_model_only_affects_automation` |
| Plain-HTTP non-loopback endpoint, no D-15 exception | `rejected` before any request | `failed` | unaffected | `test_plain_http_remote_endpoint_is_blocked_before_any_request` |
| Code-bearing request without a D-16 policy (even over TLS) | `rejected` before any request | `failed` | unaffected | `test_transport_and_disclosure_gates` |
| The default live transport (Phase 2) | `rejected` ("disabled in Phase 2") | `failed` | unaffected | `test_default_transport_never_connects` |
| Timeout, connection error, 5xx | retried twice, then `unavailable` | back to `ready` | unaffected | `test_bounded_retries_then_success`, `test_retries_are_bounded_and_unavailability_is_reported` |
| 401/403 | `failed`, not retried; key redacted | `failed` | unaffected | `test_auth_errors_are_not_retried_and_keys_are_redacted` |
| No `choices`, or a different `model` in the response | `failed` | `failed` | unaffected | `test_invalid_responses_fail` |
| Input above 110,592 tokens, or `max_tokens` above 16,384 | `failed`, **nothing sent** | `failed` | unaffected | `test_context_budget_is_enforced_before_sending` |
| Model calls tools while tool calling is unvalidated | `rejected`, no edit applied | `failed` | unaffected | `test_unvalidated_tool_calls_are_refused` |
| Tools validated: out-of-scope path, `apply-patch`, non-unique `find` | that call is refused and reported back as a tool message; a multi-edit is atomic | continues | — | `test_validated_tool_calls_edit_locally_within_scope` |
| Tools unvalidated, plain answer | `advisory` (read-only review) | verified only if its gates pass | — | `test_request_shape_matches_the_exact_profile` |

The demo uses a loopback mock endpoint (`127.0.0.1`, never contacted; the transport is an in-memory script).

## 10. Issues and the single PR (mock tracker)

**Labels** form a namespace disjoint from the bug workflow's:

- `feature:parent`, `feature:blocked`, `feature:review`;
- role labels `feature:backend`, `feature:cli`, `feature:frontend`, `feature:automation`, `feature:coordination`.

The tracker refuses any label outside `feature:*` and refuses to modify, comment on or close an issue that carries `mx-auto`, `source::*` or `severity::*`.

**Deduplication:** each issue carries a hidden marker `<!-- feature-coordinator key=… -->` (`feature:<id>` or `task:<id>`). `ensure_issue` returns the open issue with that key, and the JSON-backed mock survives a restart.

**Pull requests:**

- `create_pull_request` accepts only `feature/*` heads and refuses a second open PR for the same head.
- `FeatureCoordinator.open_pull_request` additionally requires the feature in `review`, the branch HEAD unchanged since integration, and `approved=True`.
- The PR body lists the component tasks with their commits, acceptance criteria, changed paths, the integration gates, and an explicit "Remote CI: pending / not configured" line (B-2).

## 11. Gate catalog (existing generic checks)

| Gate | Command | Working directory | Baseline exit (Phase 0/1 evidence) |
| --- | --- | --- | --- |
| `backend-tests` | `python -m pytest -q -p no:cacheprovider` | `backend/` | 0 — 919 passed, but it writes into the real `content/` (B-7) |
| `backend-lint` | `env PYTHONPATH=src python -m pylint src/` | `backend/` | **8** — 10.00/10 with one `R0915` message |
| `cli-lint` | `python -m pylint src/kiddo_cli` | `cli/` | **28** — 9.62/10, 34 messages |
| `contract-check` | `python scripts/export_openapi.py --docs-agents --check` | root | 0 — 91/91 operations up to date |
| `docs-check` | `python scripts/check_docs_agents.py` | root | 0 — 6/6 |
| `frontend-type-check` | `npm run type-check` | `frontend/` | 0 |
| `frontend-build` | `npm run build` | `frontend/` | 0 |
| `frontend-tests` / `-content` / `-auth` | `npm run test:voices` / `test:content` / `test:auth` | `frontend/` | 0 — 29/29, 5/5, 8/8 |
| `coordinator-tests` | `python -m pytest -q -p no:cacheprovider scripts/tests` | root | 0 — 204 passed |

- The CLI has no test suite (Phase 0), so `cli-lint` and the per-task acceptance checks are its only gates. The two lint gates fail on the unchanged baseline: a feature cannot require them as blocking until the messages are fixed or the threshold is decided (D-19).

- `validate_build.py`, `validate_tests.py`, `validate_frontend_build.py` and `validate.sh` are **not** gates: they create virtual environments and run `pip install` / `npm install` in shared environments.
- Gate evidence is recorded with the HEAD it ran on. If HEAD moves while gates run, or between the gates and verification, they are re-run (bounded); then the task is deferred.
- **Phase 2 runs gates only through the fake `RecordingGates`.** `subprocess_gate_runner` exists but is not exercised.

## 12. CLI (`python scripts/feature.py …`)

| Command | Effect |
| --- | --- |
| `doctor [--apply]` | Resolves the state directory and checks its filesystem. `--apply` creates it (0700) and runs the `flock` self-test — **the only command that writes, and only to the state directory** |
| `config [--config FILE]` | Validates a configuration (default: the documented example) and checks it equals the built-in configuration |
| `providers` | Prints the provider per role and the Qwen limits, capabilities and endpoint status, without connecting |
| `plan SPEC` | Validates a feature spec (YAML/JSON): branch name, roles, gates, DAG. Writes nothing |
| `status` | Read-only view of features, tasks and live/stale claims |
| `demo` | Runs the complete mock feature in a temporary repository |

## 13. One feature, one branch, one PR — lifecycle and approval points

1. **Plan** (`plan`): feature and task records, the parent issue and one issue per task. *Approval: the operator approves the feature itself (Phase 5).*
2. **Activate** (`activate`): under exclusive `project.lock`, create `feature/<slug>` from the base (D-4: `dev`; never an existing branch); tasks go `ready` or `blocked`.
3. **Run tasks in parallel by role** (`run_task`): claim → runner edits through `ScopedWorkspace` → serialized manifest commit → required gates at the combined HEAD → `verified`. Every exit leaves a defined state:
   - `failed` — runner failure, failing gates, or a refused commit such as foreign staged content;
   - `needs-human` — a conflicting HEAD;
   - back to `ready` — the provider was unavailable, a commit was deferred because a file was unstable, the branch kept moving, or an unexpected error occurred;
   - kept `blocked` — a contract request superseded the result.

   (`test_commit_refusal_fails_the_task_instead_of_stranding_it`, `test_deferred_commit_and_unexpected_errors_return_the_task_to_ready`, `test_branch_moving_after_the_gates_reruns_them`, `test_contract_request_during_a_run_supersedes_its_result` **[V]**)
4. **Integrate** (`integrate`): a **quiescent barrier** under exclusive `project.lock`. It requires every task `verified`, no tracked or untracked changes, and passing integration gates at the final HEAD; it then renders the single PR candidate and moves the feature to `review`. A gate failure returns the feature to `active` with no candidate (`test_integration_is_a_quiescent_barrier`, `test_failing_integration_gate_returns_the_feature_to_active` **[V]**).
5. **Open the PR** (`open_pull_request(approved=True)`). *Approval: explicit operator approval; the push and PR are live actions from Phase 4/5 only.*
6. **Done** (`complete(merged_sha)`): only after a human-reviewed merge. The coordinator never merges.

## 14. Safety and approval model

| Action | Who may | Gate |
| --- | --- | --- |
| Edit role-owned paths | That role's single active writer | Claim + role lock + `PathPolicy` (Phase 3 adds OS containment) |
| Edit approval-required paths | A task with an explicit grant | Operator-approved grant; protected paths can never be granted |
| Edit protected paths, the bug workflow, `.env`, data/content | Nobody | Policy has no override |
| Commit | Only `GitCoordinator` | `git.lock`, manifest, hashes, compare-and-swap |
| Create or switch the branch | Only `GitCoordinator` | Exclusive `project.lock` (no writer active), clean tree |
| Push, Issues, PR | Coordinator **after operator approval** (Phase 4/5) | Phase 2: mock tracker and dry-run push plan only |
| Merge | Humans only | Never automated |
| Send a request to the remote Qwen model | Automation role | D-15 (transport) and D-18 (auth) decided, a live test approved; Phase 2 transport is disabled |
| Send source code to the remote model | — | Additionally D-16 (a disclosure policy) |
| Enable a declared Qwen capability (for example tool calling) | — | Only after it is validated (V5-5) and recorded in `validated_capabilities` |
| Change authentication or debug defaults, application code, CI | — | Separate explicit approval (unchanged in Phase 2) |

## 15. Limitations of the Phase 2 skeleton

- **No real agents run.** Runners are mocks; `claude`/`codex` invocations are rendered, not executed. OS containment (bwrap/Landlock) is not applied yet (Phase 3).
- **No live model transport.** `DisabledLiveTransport` always refuses; there is no HTTP client yet. Streaming, partial tool-call assembly and cancellation are not implemented (Phase 3).
- `apply-patch` is declared by the profile but refused by the client.
- **No live GitHub adapter**, push or PR (Phase 4/5). The mock tracker has no network or rate-limit behavior.
- **Gates are simulated** in all tests. The gate catalog is defined, but the real commands, especially `backend-tests` (which writes into `content/`, B-7/F-5), were not run by the coordinator.
- **Tokens are estimated, not counted** (bytes / 3). A real tokenizer check belongs to the Phase 3 live validation.
- **Heartbeats are recorded, not monitored.** Liveness relies on process identity plus `flock`, which is sufficient on a single host.
- **One active feature per checkout** by design. A second feature waits in `planned`.

## 16. Phase 2 exit gate → evidence

| v5 Phase 2 exit criterion | Evidence **[V]** |
| --- | --- |
| A mock feature with Backend/CLI/Frontend tasks on **one branch** with the **one-PR** policy | `test_mock_feature_end_to_end_meets_the_phase2_exit_gate` and `feature.py demo`. Branches are `dev` and `feature/items-export` only; 3 serialized commits (backend, cli, frontend); 4 tasks verified, including the dynamically created Backend contract task; **1 PR**, and a second is refused; the foreign bug-workflow issue is untouched; a second backend writer is rejected; the feature ends in `review`, not `done` |
| Mocked remote Qwen configuration and offline/error behavior documented; no live connection | §9.3–§9.4; 31 tests in `test_fc_config_providers.py` and 18 in `test_fc_runners_issues.py`; the default transport refuses every request |
| No dependent calls to the independent bug systems | AST import scan; `git status` empty for `scripts/mxfix` and `scripts/mxissues`; foreign issues are read-only |
| Safety and approval model documented | §14 |
| Coordinator transitions and Git serialization tested | `test_fc_model.py` (27), `test_fc_gitops.py` (12), `test_fc_claims.py` (14), `test_fc_statedir_locks.py` (16), `test_fc_paths.py` (34), `test_fc_workflow.py` (22) |
| Phase 1 gates still pass | `check_docs_agents.py` 6/6; the 30 Phase 1 tests pass within the full `scripts/tests` run (204 passed) |

Run them:

```bash
.venv/bin/python -m pytest -p no:cacheprovider scripts/tests      # 204 tests, all temporary repositories
python scripts/feature.py demo                                     # the mock end-to-end feature
python scripts/feature.py doctor                                   # state-dir suitability (read-only)
```
