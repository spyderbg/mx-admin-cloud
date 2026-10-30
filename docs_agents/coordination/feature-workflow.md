# Feature Workflow — Coordinator Design (v5, Phases 2–4 and the Phase 4 extension)

> Owner role: **coordination**. Status: Phase 2 design and Phase 3 additions (§17–§18) **approved** (2026-10-17); Phase 4 additions (§10, §19) and the Phase 4 extension (§20, GitHub Projects board) **approved** (2026-10-17). W2 functional Worker (§21: native subscription execution, role → agent → model routing, the Worker loop) **implemented and tested offline, awaiting review** (2026-10-22). Companion pages: [worker-functional.md](worker-functional.md) (W2), [agent-ownership.md](agent-ownership.md) (write enforcement and evidence), [operations.md](operations.md) (launch/stop/resume/recover, GitHub commands), [github-integration.md](github-integration.md) and [task-lifecycle.md](task-lifecycle.md) (Phase 4), [github-projects.md](github-projects.md) (Phase 4 extension), [backend-task-b7.md](backend-task-b7.md).
> Governing assignment: [`agents_prompt_v5.md`](../../docs/multiple_agents/agents_prompt_v5.md) §11 Phase 2. Design baseline: [architecture §11](../architecture/README.md#11-v4-feature-development-architecture-design-baseline-for-phases-27), remote Qwen contract [§11.11](../architecture/README.md#1111-remote-openai-compatible-qwen-integration-contract-v5-101), decisions [§12](../architecture/README.md#12-decision-register). Progress: [`phase-status.md`](phase-status.md).
>
> Evidence tags: **[V]** verified by execution (a test or command named here), **[S]** verified by reading source, **[U]** unverified.

The feature coordinator is a small, **stdlib-only** Python package that runs one feature at a time on this Linux host, on **one shared checkout** and **one `feature/<slug>` branch**. Role agents edit disjoint paths in parallel; a single Git coordinator lands their commits one at a time; exactly **one PR** is opened per feature, after all gates pass and only with operator approval.

Phase 2 delivered the design and a skeleton with mock runners, a mock issue tracker and a disabled live model transport. Phase 3 adds real Claude Code and Codex launchers contained by **bubblewrap**, process supervision (heartbeat, stop, timeout, safe termination, recovery), **sandboxed** gates with timeouts, logs and D-19 lint baselines, and a loopback-only HTTP transport for the remote Qwen model (§17). Phase 4 adds **real GitHub Issue coordination**: a `gh`-based adapter, owned parent and component Issues with versioned ownership markers, idempotent and recoverable synchronization, validated operator commands, and the complete dependency lifecycle (§19). The Phase 4 extension adds a **GitHub Projects board** (Project #2) that shows every coordinator Issue's state, derived from the local state and never read as input (§20). Nothing pushes or opens a PR, GitHub writes are limited to test features, and no request is sent to the remote Qwen server.

## 1. Boundaries

| Area | Rule | Evidence |
| --- | --- | --- |
| Bug workflow (`test_environment/mxfix/**`, `test_environment/mxissues/**`, `test_environment/issues.py`, `testenv.py run`) | Independent system. Never imported, called, extended, scheduled or tested. Its paths are **protected** for every role; its issues and labels (`mx-auto`, `source::*`, `severity::*`) are never written | `test_the_coordinator_is_independent_of_the_bug_workflow_and_the_backend` (AST import scan; both trees must be tracked, so a moved or deleted tree fails it, and their `git status` empty), `test_the_test_environment_does_not_import_the_coordinator`, `test_bug_workflow_issues_are_never_touched` **[V]** |
| Application code (`backend/`, `cli/`, `frontend/`) | Not imported. Reached only as **gate commands** run in a subprocess (§11) and as role-owned paths the runners edit | Same AST scan (`backend`, `src`, `testenvlib` forbidden) **[V]** |
| Generic scripts | Called as subprocess gates (`export_openapi.py --docs-agents --check`, `check_docs_agents.py`). Generators are owned by the destination's role (§6) | §11 |
| Runtime state | Outside Git, under `$XDG_STATE_HOME/mx-admin-agents/` (D-12); never committed | §5 |
| Remote Qwen | Inference only, over an OpenAI-compatible Chat Completions API. Agents, tools, files and Git stay local. **No request was sent in Phase 2** | §9 |
| GitHub | Phase 4: a separate adapter over the authenticated `gh` CLI (`github_api.py`) and a synchronizer (`issue_sync.py`); `feature:*` labels only; Issues are the visible record, never locks or state; writes limited to test features (`github.write_scope`); no PR creation. Phase 4 extension: the Projects board (Project #2) as a picture of that state; Project #1 is never written | [github-integration.md](github-integration.md), [github-projects.md](github-projects.md) |

## 2. Package map

`feature_coordinator/` (D-13; at the repository root since 2026-10-30), run as `python -m feature_coordinator` (`__main__.py`) from the checkout root. Python 3.12 standard library, plus PyYAML only for loading the YAML example, and the installed GitHub CLI (`gh`) as an external program for live Issue operations **[V]**. Phase 3 modules are marked ³, Phase 4 modules ⁴, Phase 4 extension modules ⁴ˣ. `issues.py` was rewritten in Phase 4 (tracker types, ownership markers, the mock tracker).

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
| `cli.py`, `commands.py`³ | The CLI (§12); every mutating command is a dry run unless `--apply` |
| `sandbox.py`³ | bubblewrap profiles per role and for gates, built from `PathPolicy`: shadow root, writable roots, read-only overlays, hidden secrets, stray-write detection |
| `supervisor.py`³ | Process supervision: heartbeat, deadline, cancellation, SIGTERM→SIGKILL of the whole sandbox |
| `launchers.py`³ | Real Claude Code and Codex launchers, the flag probe, `RealCliRunner` |
| `gates.py`³, `lint_baseline.py`³ | Sandboxed gate runner (offline, read-only, timeouts, logs, unsafe-gate refusal); baseline-aware Pylint gates (D-19) |
| `http_transport.py`³, `capabilities.py`³ | Loopback-only Chat Completions transport (streaming, partial tool calls, cancellation); capability validation |
| `operations.py`³ | Containment self-test, real runner wiring per execution backend (W2), tracker selection (`make_tracker`)⁴ |
| `native_backend.py`ᵂ² | The `native-cli` execution backend: subscription-only environment and status checks, supervision with run-tag cleanup, workspace-wide acceptance (NOT a sandbox) |
| `worker.py`ᵂ², `worker_commands.py`ᵂ² | The Worker loop (`python -m feature_coordinator work`): dependencies, one writer per role, bounded launches; the `subscription-smoke` command |
| `workspace.py`ᵂ², `python_env.py`ᵂ², `native_smoke.py`ᵂ² | Workspace verification (identity, branch, base, no unfinished Git operation); Python interpreter discovery for `{python}` role commands; the bounded real-subscription smoke test |
| `checkpoints.py`ᵂ³, `checkpoint_commands.py`ᵂ³ | Multi-step tasks: checkpoint records, the intent → commit → complete protocol, reconciliation and recovery, context reconstruction; `python -m feature_coordinator checkpoints` / `recover TASK` |
| `github_api.py`⁴ | The real GitHub adapter: `gh api --include` transport, retries, rate limits, pacing, pagination, response validation, sanitized errors |
| `issue_sync.py`⁴ | Local state ⇄ Issues: create/adopt, managed block, labels, open/closed, progress comments, outbox, validated commands, reconciliation, test cleanup |
| `escalation.py`⁴ | Human holds (`needs-human`) and releases |
| `live_test.py`⁴, `github_commands.py`⁴ | The Phase 4 acceptance scenario; the `github`, `hold` and `release` commands |
| `github_projects.py`⁴ˣ, `projects_local.py`⁴ˣ | Projects (v2) GraphQL client with the write guard; the local JSON board |
| `project_sync.py`⁴ˣ, `project_commands.py`⁴ˣ, `live_projects.py`⁴ˣ | Board synchronization (status mapping, items, recovery); `python -m feature_coordinator github project …`; the live Projects test |

## 3. Configuration

The documented example is [`project-config.example.yaml`](project-config.example.yaml). It must load **equal to** the built-in `default_config()` (`test_documented_example_equals_the_built_in_configuration` **[V]**; `python -m feature_coordinator config`).

| Role | Provider (runner) | Owned paths | Excluded |
| --- | --- | --- | --- |
| backend | `claude` (Claude Code) | `backend/**`, `docs_agents/backend/**` | `backend/data/**` |
| cli | `claude` (Claude Code) | `cli/**`, `docs_agents/cli/**` | — |
| frontend | `codex` (Codex) | `frontend/**`, `docs_agents/frontend/**` | — |
| automation | `remote_qwen` (OpenAI-compatible) | `scripts/**`, `test_environment/**`, `docs_agents/automation/**` | `test_environment/mxfix/**`, `test_environment/mxissues/**`, `test_environment/testenv.toml`, `test_environment/testenv.local.toml`, `scripts/deploy.sh` |
| coordination | `claude` (configurable) | `docs_agents/coordination/**` | — |

- **Protected** (no role, no grant): `.git/**`, `.env`, `.env.*`, `**/.env`, `**/.env.*` (any depth, Phase 3), `content/**`, `data/**`, `backend/data/**`, `test_environment/mxfix/**`, `test_environment/mxissues/**`, `test_environment/testenv.local.toml` (Git-ignored per-developer test-environment overrides; a run that creates or changes it is rejected).
- Phase 3 configuration sections: `runtime` (containment fixed to `bwrap`, timeouts, heartbeat, Claude budget, secret patterns, per-role command allowlists; gates are always offline), `lint_gates` (policy fixed to `block-new`) and `unsafe_gates` (§11).
- **Approval-required** (only with a task grant): root instruction files (`CLAUDE.md`, `AGENTS.md`, `README.md`), `.gitignore`, `.gitlab-ci.yml`, `test_environment/testenv.toml` (also excluded from the automation role, which owns `test_environment/**`), `scripts/deploy.sh`, agent-tool configuration (`.claude/**`, `.cursor/**`, `.opencode/**`, `.aiassistant/**`, `.agents/**`), `.github/**`, `docker/**`, `docs_agents/architecture/**`, `docs_agents/README.md`.
- Anything else, for example `package.json` at the root, is **unowned and denied**. That includes the coordinator itself: `feature_coordinator/**` (its code, tests and systemd unit template) belongs to no role and is not approval-required, so no task grant can open it either (`test_the_coordinator_itself_is_writable_by_no_role`).

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

Mock runners and the Qwen tool loop write only through `ScopedWorkspace`, which enforces the policy **at write time** and refuses to read `.env*`. Real Claude Code and Codex runs are contained by bubblewrap mounts derived from the same policy (§17.1, [agent-ownership.md](agent-ownership.md)).

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
- `python -m feature_coordinator doctor --apply` created `~/.local/state/mx-admin-agents` (ext4, mode 0700) on this host and passed the two-process `flock` contention self-test on 2026-10-17 **[V]**.

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

Plumbing runs no hooks. Commit trailers record `Feature:`, `Feature-Task:` and `Feature-Role:`, and W3 checkpoint commits add `Feature-Checkpoint: <id>`. A request whose checkpoint id already has a commit since its expected HEAD returns that commit instead of creating another (idempotent; [worker-checkpoints §5](worker-checkpoints.md#5-the-checkpoint-transaction-protocol)). The subject is generated by the coordinator (`gitops.commit_subject`, W2 finalization): `<role>: <task title>` for a short one-line title, else `<role>: task <task id>`, so a task's instruction text is never a subject ([worker-functional §12](worker-functional.md#12-commits-checkpoints-and-w3)).

**Result:** another agent's unfinished, modified or untracked files can never enter a commit (`test_commit_contains_only_the_manifest_never_another_agents_unfinished_work` **[V]**), and three processes committing in parallel produce a linear chain of three single-role commits with a clean `git fsck` (`test_parallel_commits_from_two_processes_are_serialized` **[V]**).

### 8.2 Tree-changing operations

`create_feature_branch` and `switch_branch` require `project.lock` **exclusive** (no writer active) and a clean checkout. An existing branch is never reused. No separate worktrees are created.

### 8.3 Push and PR

`push_plan()` renders a non-force `git push --set-upstream origin refs/heads/<b>:refs/heads/<b>`. **It is never executed in Phase 2.**

## 9. Providers and runners

### 9.1 Runners (mocks in tests; real launchers in §17.2)

| Role | Mock runner (tests, demo) | Phase 2 sketch (superseded by the real command lines in §17.2) |
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
- Neither the endpoint address nor the key is printed (`python -m feature_coordinator providers`), and both are absent from tracked files.
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

## 10. Issues and the single PR

> **Superseded by Phase 4:** the Phase 2 tracker wrote Issues directly inside state transactions and used an unversioned `<!-- feature-coordinator key=… -->` marker. Phase 4 replaces both: the workflow changes the local state first and an `IssueSync` converges the Issues afterwards, outside every coordinator lock, with a versioned ownership marker. Full design: [github-integration.md](github-integration.md).

**Labels** form a namespace disjoint from the bug workflow's: `feature:parent`, `feature:test`, the role labels `feature:backend`, `feature:cli`, `feature:frontend`, `feature:automation`, `feature:coordination`, and the state labels `feature:in-progress`, `feature:blocked`, `feature:review`, `feature:verified`. Any label outside `feature:*` is refused, and an Issue carrying `mx-auto`, `source::*` or `severity::*` is never modified.

**Ownership and deduplication:** each Issue carries `<!-- feature-coordinator:v1 coordinator=… feature=… task=… purpose=… key=… -->`. Before creating, the synchronizer searches for the key; after a crash it adopts the Issue it already created (§19, [github-integration §5, §8](github-integration.md#5-ownership-verification)).

**Pull requests:**

- `create_pull_request` accepts only `feature/*` heads and refuses a second open PR for the same head.
- `FeatureCoordinator.open_pull_request` additionally requires the feature in `review`, the branch HEAD unchanged since integration, and `approved=True`.
- The PR body lists the component tasks with their commits, acceptance criteria, changed paths, the integration gates, and an explicit "Remote CI: pending / not configured" line (B-2).

## 11. Gate catalog (existing generic checks)

| Gate | Command | Working directory | Baseline exit (Phase 0/1 evidence) |
| --- | --- | --- | --- |
| `backend-tests` | `python -m pytest -q -p no:cacheprovider` | `backend/` | 0 — 919 passed, but it writes into the real `content/` (B-7) |
| `backend-lint` (lint gate, D-19) | Pylint on changed files under `backend/src`, against the feature base | `backend/` | baseline report: **1** existing finding (`too-many-statements`) |
| `cli-lint` (lint gate, D-19) | Pylint on changed files under `cli/src/kiddo_cli`, against the feature base | `cli/` | baseline report: **34** existing findings |
| `contract-check` | `python scripts/export_openapi.py --docs-agents --check` | root | 0 — 91/91 operations up to date |
| `docs-check` | `python scripts/check_docs_agents.py` | root | 0 — 6/6 |
| `frontend-type-check` | `npm run type-check` | `frontend/` | 0 |
| `frontend-build` | `npm run build -- --outDir /tmp/fc-frontend-dist --emptyOutDir` (output stays in the sandbox) | `frontend/` | 0 |
| `frontend-tests` / `-content` / `-auth` | `npm run test:voices` / `test:content` / `test:auth` | `frontend/` | 0 — 29/29, 5/5, 8/8 |
| `coordinator-tests` | `python -m pytest -q -p no:cacheprovider scripts/tests feature_coordinator/tests` | root | 0 — 290 passed, 4 opt-in skipped |

- The CLI has no test suite (Phase 0), so `cli-lint` and the per-task acceptance checks are its only gates. D-19: lint gates are **baseline-aware**: existing findings are reported, findings the feature introduces block (§17.5).

- `validate_build.py`, `validate_tests.py`, `validate_frontend_build.py` and `validate.sh` are **not** gates: they create virtual environments and run `pip install` / `npm install` in shared environments.
- Gate evidence is recorded with the HEAD it ran on. If HEAD moves while gates run, or between the gates and verification, they are re-run (bounded); then the task is deferred.
- **Every gate runs in the gate sandbox** (Phase 3): the checkout read-only, no network, private HOME and `/tmp`, a timeout, and logs. There is no unsandboxed gate runner. `backend-tests` is listed in `unsafe_gates` and reported `blocked` without running until [B-7](backend-task-b7.md) is fixed.

## 12. CLI (`python -m feature_coordinator …`)

| Command | Effect |
| --- | --- |
| `doctor [--apply]` | Resolves the state directory and checks its filesystem. `--apply` creates it (0700) and runs the `flock` self-test — **the only command that writes, and only to the state directory** |
| `config [--config FILE]` | Validates a configuration (default: the documented example) and checks it equals the built-in configuration |
| `providers` | Prints the provider per role and the Qwen limits, capabilities and endpoint status, without connecting |
| `plan SPEC` | Validates a feature spec (YAML/JSON): branch name, roles, gates, DAG. Writes nothing |
| `status` | Read-only view of features, tasks and live/stale claims |
| `demo` | Runs the complete mock feature in a temporary repository |
| `runners`, `sandbox-check [--apply]`, `gate`, `lint-report` | Phase 3 inspection and sandboxed gates ([operations.md](operations.md)) |
| `plan --apply`, `activate`, `launch`, `stop`, `recover`, `integrate` | Phase 3 feature operations; dry runs unless `--apply` ([operations.md](operations.md)) |
| `hold`, `release` | Phase 4 human escalation (`needs-human`) |
| `github status \| labels \| sync \| reconcile \| inspect \| owned \| deps \| test-lifecycle \| cleanup-test` | Phase 4 GitHub Issue coordination ([operations §7](operations.md#7-github-issues-phase-4)); global `--tracker local\|github` |
| `github project inspect \| validate \| setup \| associate \| sync \| reconcile \| active \| task \| test` | The Projects board (Phase 4 extension, [operations §8](operations.md#8-github-projects-board-phase-4-extension)) |

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
| Edit role-owned paths | That role's single active writer | Claim + role lock + bubblewrap containment + runner permissions + `PathPolicy` at commit |
| Edit approval-required paths | A task with an explicit grant | Operator-approved grant; protected paths can never be granted |
| Edit protected paths, the bug workflow, `.env`, data/content | Nobody | Policy has no override |
| Commit | Only `GitCoordinator` | `git.lock`, manifest, hashes, compare-and-swap |
| Create or switch the branch | Only `GitCoordinator` | Exclusive `project.lock` (no writer active), clean tree |
| Issues and labels | Coordinator (`IssueSync`) **after operator approval** | Phase 4: only `feature:*` labels; only owned Issues (§19); only test features (`github.write_scope`); `--apply` required |
| Issue comments as commands | Operators in `github.command_authors` with ≥ write permission | Only `/feature hold` / `/feature release`; never a grant of paths, tools or shell |
| Push, PR | Coordinator **after operator approval** (Phase 5) | Phase 4: dry-run push plan; `create_pull_request` refused by the GitHub adapter |
| Merge | Humans only | Never automated |
| Send a request to the remote Qwen model | Automation role | D-15 (transport) and D-18 (auth) decided, a live test approved; the Phase 3 transport reaches loopback test servers only |
| Send source code to the remote model | — | Additionally D-16 (a disclosure policy) |
| Enable a declared Qwen capability (for example tool calling) | — | Only after it is validated (V5-5) and recorded in `validated_capabilities` |
| Run a real (paid) agent model call | Operator-approved runs only | Phase 3: one approved smoke run per runner, in a disposable repository |
| Change authentication or debug defaults, application code, CI | — | Separate explicit approval (unchanged in Phases 2–3; D-5) |

## 15. Limitations (as of Phase 3)

- **Real agents run only through `python -m feature_coordinator launch --apply`** (directly or through the W2 Worker, `python -m feature_coordinator work`), with the configured execution backend. The default since W2 is `native-cli` — **no OS containment**, write scopes verified after each run ([worker-functional §3, §11](worker-functional.md#11-known-limitations)). With `bwrap` they are contained by bubblewrap; residual risks are in [agent-ownership §5](agent-ownership.md#5-residual-risks-operator-decisions-in-phase-status): readable runner credentials, network for Claude Code, protected-name creation inside a role's own root (rejected at commit), and unverified OAuth refresh.
- **No live remote model.** The HTTP transport reaches loopback servers only; capability validation has run only against the local fake server. `apply-patch` is declared by the profile but refused by the client.
- **GitHub:** the live adapter exists (Phase 4) but writes only test features; no push or PR yet (Phase 5). Body updates are last-writer-wins, and relationships are Issue references, not GitHub sub-issues ([github-integration §14](github-integration.md#14-limitations)).
- **`backend-tests` is blocked** until [B-7](backend-task-b7.md). Gates run against the working tree of the shared checkout; evidence is bound to the HEAD at verification time, and integration requires a clean tree.
- **Tokens are estimated, not counted** (bytes / 3) until a live validation can compare with the server.
- **Heartbeats** are written every `heartbeat_interval_seconds`; a stale but alive worker is reported, never killed automatically.
- **One active feature per checkout** by design. A second feature waits in `planned`.

## 16. Phase 2 exit gate → evidence

| v5 Phase 2 exit criterion | Evidence **[V]** |
| --- | --- |
| A mock feature with Backend/CLI/Frontend tasks on **one branch** with the **one-PR** policy | `test_mock_feature_end_to_end_meets_the_phase2_exit_gate` and `python -m feature_coordinator demo`. Branches are `dev` and `feature/items-export` only; 3 serialized commits (backend, cli, frontend); 4 tasks verified, including the dynamically created Backend contract task; **1 PR**, and a second is refused; the foreign bug-workflow issue is untouched; a second backend writer is rejected; the feature ends in `review`, not `done` |
| Mocked remote Qwen configuration and offline/error behavior documented; no live connection | §9.3–§9.4; 31 tests in `test_fc_config_providers.py` and 18 in `test_fc_runners_issues.py`; the default transport refuses every request |
| No dependent calls to the independent bug systems | AST import scan in both directions; `test_environment/mxfix` and `test_environment/mxissues` tracked and their `git status` empty; foreign issues are read-only |
| Safety and approval model documented | §14 |
| Coordinator transitions and Git serialization tested | `test_fc_model.py` (27), `test_fc_gitops.py` (12), `test_fc_claims.py` (14), `test_fc_statedir_locks.py` (16), `test_fc_paths.py` (34), `test_fc_workflow.py` (22) |
| Phase 1 gates still pass | `check_docs_agents.py` 6/6; the 30 Phase 1 tests pass within the full `scripts/tests` run (204 passed) |

Run them:

```bash
.venv/bin/python -m pytest -p no:cacheprovider scripts/tests feature_coordinator/tests      # 204 tests then, all temporary repositories
python -m feature_coordinator demo                                     # the mock end-to-end feature
python -m feature_coordinator doctor                                   # state-dir suitability (read-only)
```

## 17. Phase 3 — runtime design

### 17.1 OS containment (`sandbox.py`)

Each real runner and each gate runs as `bwrap … <command>` with:

| Area | Setting |
| --- | --- |
| Process | `--die-with-parent --new-session --unshare-all` (mount, PID, IPC, UTS, cgroup and user namespaces); `--share-net` only for runners, which need their model API, never for gates |
| System | `/usr` and `/etc` read-only; `/proc`, `/dev`, private `/tmp` and `/var/tmp`; `--remount-ro /`, so nothing outside the explicit binds is writable |
| Home and environment | A private HOME (`<state>/runs/<id>/home`); the runner's credential files are bound read-only; `--clearenv` plus an allowlisted environment (no `XDG_STATE_HOME`, no `AGENT_QWEN_*`) |
| Repository | A **shadow root**: every top-level entry of the checkout is bound read-only into a private directory mounted at the repository path. The role's writable roots are then bound read-write, and paths the role must not write inside them are re-bound read-only: exclusions, protected and approval-required paths, nested `.git` |
| Secrets | Untracked or ignored files matching `runtime.secret_patterns` (`.env`, `.env.*`, any depth; `*.example` exempt) are hidden at top level or masked with `/dev/null` |
| State | The coordinator state directory is never visible: it lives under the real HOME, and a tmpfs covers it if a bound toolchain prefix would expose it |
| Shadow writability | Read-only for Claude Code and gates (`EROFS` everywhere else). Writable only for Codex, whose own nested bubblewrap sandbox must create guard mount points in its working root. Anything written there is discarded and rejects the run as a **stray write** |

Containment fails closed: if `bwrap` is missing or cannot create namespaces, real runners are *unavailable* and gates are *blocked*.

### 17.2 Real launchers (`launchers.py`)

| Runner | Command line (prompt on stdin) |
| --- | --- |
| Claude Code 2.1.302 | `claude -p --output-format stream-json --verbose --restricted --tools Read,Glob,Grep,Edit,Write[,Bash] --settings $HOME/.fc/claude-settings.json --strict-mcp-config --permission-mode acceptEdits --permission-prompts none --no-session-persistence --disable-slash-commands --max-budget-usd 5` |
| Codex 0.163.0 | `codex exec --json --sandbox workspace-write --ephemeral --ignore-user-config -C <repo> -o $HOME/.fc/last-message.txt -c sandbox_workspace_write.network_access=false -` |

Claude Code:

- `--restricted` ignores the user, project and local settings files. The repository's `.claude/settings.json` (`bypassPermissions`) is therefore never loaded and never edited, and bypass is refused.
- The generated settings allow only the file tools and the role's Bash prefixes (`runtime.role_commands`), and deny Web tools, state-changing Git, `curl`/`wget` and reads of the runner credential directories.

Codex:

- keeps its own sandbox, nested inside ours, so model-generated commands get no network.

Fail closed:

- `probe_runner()` checks every required flag in `--help`; a missing flag makes the runner unavailable;
- `FORBIDDEN_ARGS` (every bypass and danger flag) can never be emitted.

After a run, `RealCliRunner`:

- takes the role's dirty paths (`git status`, read-only, optional locks off) as the commit manifest;
- **rejects** the run if it wrote outside its roots (strays) or created a path the role may not commit (for example a new `.env` or a symlink);
- leaves pre-existing untracked files alone, judging only changes made during the run by content fingerprints.

### 17.3 Supervision, stop and recovery (`supervisor.py`, `workflow.py`, `claims.py`)

- The worker process holding the claim supervises the sandbox:
  - it heartbeats the claim, with a checkpoint naming the run directory;
  - it polls the operator stop flag, bound to the claim token;
  - it enforces `task_timeout_seconds`.
- Stopping sends SIGTERM to the process group, waits `terminate_grace_seconds`, then sends SIGKILL. The PID namespace guarantees that a detached grandchild dies too.
- SIGTERM or SIGINT to the worker is a graceful stop.
- If the worker is SIGKILLed, bwrap ends the sandbox and the kernel releases the flocks; `recover` reclaims the task.
- Outcomes:
  - `cancelled` → task **ready** (the role's uncommitted work stays);
  - `timeout` → **failed**;
  - an alive worker without heartbeats → reported **unresponsive**, never reclaimed.

### 17.4 Sandboxed gates (`gates.py`)

- **Execution:** the gate sandbox has no writable repository path and no network. `python` resolves to the repository `.venv`. Host tools such as `npm` and `node` get their install prefix bound read-only; a broad prefix such as `~/.local` is never bound.
- **Logs and evidence:** logs go to `<state>/logs/<gate>.<stamp>/`. A result carries `status` (`passed`, `failed`, `timeout` = 124, `blocked` = 125, `error` = 126), the duration, the log path and a summary; only `passed` counts.
- **Unsafe gates:** a gate in `unsafe_gates` is not run.
- **Frontend build:** writes only to the sandbox's private `/tmp`.

### 17.5 Baseline-aware lint (D-19, `lint_baseline.py`)

1. **Files:** the Python files changed between the feature base and HEAD under the gate's targets.
2. **Two runs:** Pylint runs on those files twice in the gate sandbox: on the checkout, and on a `git archive` snapshot of the base (with the root Pylint configuration).
3. **Fingerprints:** each finding is identified by (path, symbol, object, message with digits normalized). Moved code and changed counts ("Too many statements (55/50)") are therefore not new.
4. **Verdict:** new = head − base, as a multiset. Any new finding fails the gate; the summary always reports base, HEAD, new and fixed counts.

The policy is fixed to `block-new`: Pylint is never disabled and no finding is suppressed. `lint-report` gives the full baseline.

### 17.6 Remote Qwen transport (D-17, `http_transport.py`, `capabilities.py`)

The transport is a minimal native Chat Completions client and the tool loop stays in `OpenAICompatibleRunner`; neither is a general agent.

| Behavior | Implementation |
| --- | --- |
| Request | Connect timeout plus an overall deadline. The blocking exchange runs in a helper thread, and cancellation or the deadline closes the raw socket |
| Errors | 401/403 → auth error (not retried); 429 → rate limit (retried); 5xx, refused connection and timeout → unavailable (retried with backoff); anything else → invalid response |
| Streaming | SSE streams are assembled into the non-streaming shape, including tool calls whose id, name and arguments arrive in fragments. Used only once `streaming` is validated |
| Remote endpoints | `LIVE_REMOTE_ENABLED = False`: any non-loopback endpoint is refused before DNS or a socket |
| Validation | `validate_capabilities()`: chat (exact model echo) → tool calling (probe tool plus a two-step loop) → streaming. Synthetic prompts only; it reports results and never edits `validated_capabilities`. `thinking` and `vision` are reported as not tested |

## 18. Phase 3 exit gate → evidence

| v5 Phase 3 exit criterion | Evidence **[V]** |
| --- | --- |
| Backend + Frontend write concurrently in disjoint paths | `test_backend_and_frontend_write_concurrently_and_land_separately`: two worker processes (the Claude Code and Codex launchers with the fake agent, real bwrap) prove overlap through a barrier. Each lands one single-role commit; the third role's dirty file is not captured |
| A second Backend writer is rejected | Same test: `RoleBusyError` in-process and from another process; plus `test_fc_claims.py` races |
| Unauthorized symlink, traversal and generated-output writes are rejected | `test_containment_matrix_for_every_role` (19 targets × 5 roles, 0 mismatches); `test_traversal_and_symlink_escapes_never_reach_the_host`; generated `*Schemas.ts` writable only by Frontend |
| A Git branch switch is rejected while any writer is active | Parallel test (`WritersActiveError` while both sandboxes run); `test_branch_switch_is_refused_while_a_scoped_writer_is_active` |
| Concurrent commits cannot corrupt the index or capture foreign files | Parallel test (`git fsck` clean, two single-role commits); `test_parallel_commits_from_two_processes_are_serialized` |
| Full-project cannot overlap scoped writers | Parallel test (`claim_full_project` → `WritersActiveError` naming both roles) |
| Git metadata read-only for agents; only the Git coordinator mutates Git | `test_git_metadata_is_read_only_but_readable` (`commit`, `branch`, `update-ref`, `config` fail in the sandbox); `git commit` refused for every role in the matrix |
| Real Claude Code and Codex launchers; flags verified | `test_installed_cli_supports_every_required_flag`, `test_installed_cli_starts_inside_the_sandbox`, opt-in login checks, and the **approved live smoke runs** ([agent-ownership §4](agent-ownership.md#4-real-runner-verification-2026-10-17)) |
| Heartbeat, cancellation, recovery, safe termination | `test_fc_supervisor.py` (7) and `test_fc_worker_lifecycle.py` (9) |
| Integration checks with timeouts and logs | `test_fc_gates.py` (7); live checkout gates in [operations §4](operations.md#4-gates) |
| Remote Qwen mocked failure, security and capability tests before write-capable use | `test_fc_http_transport.py` (23) against the local fake server; remote endpoints refused |
| No unproven enforcement assertions | Every claim above names a test or command; open items are marked **[U]** in [agent-ownership §5](agent-ownership.md#5-residual-risks-operator-decisions-in-phase-status) |

```bash
.venv/bin/python -m pytest -p no:cacheprovider scripts/tests feature_coordinator/tests     # 294 collected then: 290 passed, 4 opt-in skipped
python -m feature_coordinator sandbox-check --apply                   # the containment matrix (disposable repository)
python -m feature_coordinator runners                                 # installed CLIs and planned command lines
```

## 19. Phase 4 — GitHub Issue coordination and task dependencies

| Area | Design | Where |
| --- | --- | --- |
| Adapter | `gh api --include` per request (existing login, no token handling), validated responses, bounded retries, rate limits, pacing | `github_api.py`, [github-integration §2–§3, §9](github-integration.md#2-architecture) |
| Ownership | Versioned marker + coordinator account + exact number and database ID; foreign and unmarked Issues are never written | `issues.verify_ownership`, [github-integration §5](github-integration.md#5-ownership-verification) |
| Authority | Local state first; Issues derived afterwards outside every coordinator lock; GitHub → local only through validated `/feature hold\|release` | `workflow.sync_issues`, `issue_sync.py`, [github-integration §7](github-integration.md#7-synchronization) |
| Idempotency | Persisted `pending_create`, history cursors and outbox entries; key search and operation markers before every re-send | [github-integration §8](github-integration.md#8-idempotency-and-recovery) |
| Dependencies | Create or reuse one Backend contract task; block only consumers; probe = evidence only; unblock only on verified evidence; re-block on invalidation; human holds survive | [task-lifecycle §3](task-lifecycle.md#3-dependency-lifecycle) |
| Fixes to Phase 2 behavior | `reopen_task` now re-blocks **ready** dependents too, not only verified ones; a task can no longer be verified while a dependency is unverified (e.g. re-opened during its run) | `workflow.reopen_task`, `_finish`, `verify_task` |

Exit-gate evidence, live Issue numbers and test results: [phase-status](phase-status.md).

## 20. Phase 4 extension — GitHub Projects board

| Area | Design | Where |
| --- | --- | --- |
| Role of the board | A picture of the local state: GitHub Issues stay the tasks, the board shows their state. It is never a lock, a claim, a state source, a permission or an approval | [github-projects.md](github-projects.md) |
| Target | `github.project` (owner, number, node ID; protected numbers). mx-admin-cloud: Project #2 `PVT_kwHOACEIPc4BndH-`; Project #1 (mxissues) protected. The generic default is disabled | `config.GitHubProjectConfig`, `MX_ADMIN_CLOUD_PROJECT` |
| Status mapping | Derived from the existing task and feature state machines; Review = commit landed and gates running (claim checkpoint `verifying`) or feature review; Done only with evidence on the feature branch (tasks) or merge evidence (feature) | `ProjectSync.board_status`, [github-projects §3](github-projects.md#3-status-mapping-from-the-existing-state-machines) |
| Synchronization | After every Issue sync, outside every coordinator lock; `run_task` shows In Progress and Review through a detached `python -m feature_coordinator github sync` process that holds no worker lock | `workflow.sync_board`, `operations.DetachedPublisher` |
| Ownership and protection | Feature authorization (`plan` or `associate`); the Issue ownership proof; item content verified; a write guard bound to the configured project; protected projects refused by node ID | `project_sync.py`, `github_projects.BoardGuard` |
| Idempotency and recovery | `pending_add` + adoption by the Issue's project items; read-back after writes; drift restored | [github-projects §7](github-projects.md#7-recovery-and-reconciliation) |

Exit-gate evidence, the live item IDs and test results: [phase-status](phase-status.md).

## 21. W2 — Functional multi-agent Worker

Design, tests, limitations and the subscription smoke test: [worker-functional.md](worker-functional.md).

| Area | Design | Where |
| --- | --- | --- |
| Execution backend | `runtime.execution_backend`: `native-cli` (default; installed CLIs with the subscription logins, no OS sandbox) or `bwrap` (§17). Never a fallback between them | `operations.build_runners`, `native_backend.py` |
| Subscription authentication | Allowlisted environment; the CLIs' status commands must report the claude.ai / ChatGPT subscription before a run; API keys refused; undetermined → operator confirmation; Claude Code's `init` event checked after the run | `native_backend.check_subscription`, `claude_session_problems` |
| Role → agent → model | `roles.<r>.provider` + `roles.<r>.model` (`default` = CLI default), validated per agent kind; `roles.<r>.enabled` | `config.model_for`, `providers.check_model` |
| Worker | `python -m feature_coordinator work`: READY tasks as separate `launch` processes, one per role, dependents after verification, bounded launches, graceful stop | `worker.py` |
| Workspace | Identity recorded at activation, verified before every run, with branch, base revision and no unfinished Git operation | `workspace.py` |
| Python | `{python}` in role commands → workspace `.venv`, else `python3` (Ubuntu 24.04 has no `python`) | `python_env.py` |
| Commit messages | Coordinator-generated short subject (`<role>: <title>` or `<role>: task <id>`), never the task prompt or agent output; trailers unchanged | `gitops.commit_subject`, `workflow._commit` |

## 22. W3 — Multi-commit checkpoints and task recovery

Design, protocol, recovery, examples, limitations: [worker-checkpoints.md](worker-checkpoints.md). **Implemented and verified with a real-agent recovery test**: every capability offline, and the core recovery guarantee with real Claude Code (an interruption between completed steps, W3.2).

| Area | Design | Where |
| --- | --- | --- |
| Steps | `TaskSpec.steps` / `Task.steps` (≤ 20, unique keys); task schema v2 with an explicit v1 migration; no steps = the W2 behaviour | `model.py`, `workflow.py` |
| Execution | One bounded agent invocation per remaining step (existing runners and backends), judged as in W2; gates once after the last step | `workflow._run_steps` |
| Checkpoint protocol | INTENT (state) → COMMIT (Git coordinator, `Feature-Checkpoint` trailer, idempotent) → COMPLETE; `no-changes` without an empty commit | `checkpoints.py`, `gitops.py` |
| Reconciliation | Adopt a landed commit, commit from an intent whose files still match, record missing SHAs; conflicts → `needs-human`; never resets or rewrites Git | `checkpoints.inspect`, `reconcile_for_run` |
| Process recovery | Run tag + agent process identity in the claim; a dead worker with a live agent is not reclaimed (`orphaned`); `--terminate-orphans` stops only those processes | `claims.py`, `native_backend.py`, `supervisor.terminate_pids` |
| Context | Completed / remaining / not to repeat / to verify, untrusted summaries, current step last | `checkpoints.step_prompt` |
| Operator | `checkpoints TASK`, `recover TASK` (preview by default; `--apply`, `--terminate-orphans`, `--abandon-pending`, `--redo-from`) | `checkpoint_commands.py` |

## 23. W4 — Autonomous Worker operations

Design, policies, operator commands, systemd instructions, limitations: [worker-autonomy.md](worker-autonomy.md). **Implemented and verified offline**: fake agents, real processes, simulated time. No real-agent W4 run has taken place.

| Area | Design | Where |
| --- | --- | --- |
| Service | `python -m feature_coordinator worker start`: one long-running loop per state directory (`worker-service.lock`; a second service exits 73); one cycle = heartbeat → control → active feature → workspace → claims → in-flight launches → eligible task → launch → integrity → outcome | `service.py` |
| Sequential | Default `worker.max_parallel: 1` (the service requires it; `work` defaults to it). The `agent-exclusive` flag makes `ClaimManager.claim` refuse a claim while another exists; `work --max-parallel N` keeps the W2 scheduler | `claims.py`, `worker_commands.py` |
| Scheduling | `scheduling/<task>.json`: launches (write-ahead), attempts per category, next attempt, waiting, attention + action, in-flight launch (tagged `FC_WORKER_LAUNCH`), history; no new task states | `autonomy.py` |
| Classification | transient / subscription / validation / critical / unknown (fails closed); not failures: verified, dependency, cancelled, interrupted, external | `autonomy.classify` |
| Retries | Exponential backoff, per-category budgets and a launch cap, persistent across restarts; validation auto-fix off by default; operator reset `worker reset-retries` | `autonomy.apply_outcome` |
| Recovery | W3 at startup and every cycle; orphaned agents waited for, stopped only with certain identity after their deadline; per-task isolation of unreadable state | `service.py`, `claims.py`, `checkpoints.py` |
| Health and control | `service/worker.json` (heartbeat, current agent, last checkpoint and error, counters); `worker status/health/waiting/attention` (read-only); `pause/resume/stop [--after-current]` (control flag + SIGUSR1/SIGTERM to the verified process); exit 78 = do not restart | `service_commands.py`, `supervisor.py` |
| Redaction | `redact_secrets` (provider keys, secret assignments) in task reasons and everything the service records or prints | `github_api.py`, `workflow._finish`, `autonomy.scrub` |
| systemd | User unit template, rendered by `worker systemd-unit`; never installed or enabled automatically | `systemd/mx-admin-worker.service.in` |
