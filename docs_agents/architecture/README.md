# Architecture — Component Boundaries, Flows and the v4 Feature-Development Design

> **Status:** **v5 is the authoritative specification** (adopted 2026-10-16; [`docs/multiple_agents/agents_prompt_v5.md`](../../docs/multiple_agents/agents_prompt_v5.md)). v5 supersedes v4 ([`agents_prompt_v4.md`](../../docs/multiple_agents/agents_prompt_v4.md), adopted earlier the same day) and v3 where they conflict. All v4 feature-workflow decisions carry over unchanged. v5 changes the Qwen topology to a **remote** OpenAI-compatible endpoint (§11.11) and fixes its context accounting.
>
> **Implementation status (2026-10-17, D-22):** Phases 2–4 and the Phase 4 GitHub Projects extension are **implemented and approved**. §11.4, §11.6 and §11.9 carry the verified results, and the register (§12) the decisions taken since. The evidence is in [`../coordination/phase-status.md`](../coordination/phase-status.md); the implemented design is in [`feature-workflow.md`](../coordination/feature-workflow.md), [`agent-ownership.md`](../coordination/agent-ownership.md), [`github-integration.md`](../coordination/github-integration.md) and [`github-projects.md`](../coordination/github-projects.md).
>
> Sections 2–6 describe the verified application (Phase 0/1 evidence at commit `e2ddd9c`) and remain valid. Statements that a later version replaces are **kept and marked "Superseded by v4" / "Superseded by v5"** rather than deleted. The design is in §11 and the decision register in §12. Evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified — see [`../discovery/README.md`](../discovery/README.md). Contract decision: [`api-contracts.md`](api-contracts.md).

## 1. Components and role ownership (v4)

| Role | Default runner (configurable, §11.4) | Writable paths in scoped mode | Never writable by this role |
| --- | --- | --- | --- |
| `backend` | Claude Code | `backend/**` **except `backend/data/**`**; `docs_agents/backend/**` | everything else |
| `cli` | Claude Code | `cli/**`; `docs_agents/cli/**` | everything else |
| `frontend` | Codex | `frontend/**` (including **generated** TS destinations `frontend/src/{types,api,mocks}`); `docs_agents/frontend/**` | everything else |
| `automation` | configurable; **preferred: remote Qwen** via an OpenAI-compatible Chat Completions API, driven by a local runner, after validation (§11.11) | `scripts/**` **except `scripts/mxfix/**`, `scripts/mxissues/**` and protected deployment scripts**; `test_environment/**`; `docs_agents/automation/**` | the separate bug workflow; generator *outputs* in other roles' trees |
| `coordination` | configurable | `docs_agents/coordination/**`; the approved coordination runtime/config directory | application code |
| `full-project` | explicitly authorized Claude Code or Codex | only task-approved paths; runs **exclusively** | protected data by default |

Paths requiring **explicit task-specific approval** (v4 §3; this resolves Phase 0 decision D-6):

- `docs_agents/architecture/**` and `docs_agents/README.md` — the designated docs owner during bootstrap; afterwards, an assigned coordination or approved integration owner.
- Root agent instruction files (`CLAUDE.md`, `AGENTS.md`, `.claude/`, `.cursor/`, `.opencode/`, …).
- `.github/**`, `docker/**`, `.gitlab-ci.yml`, `testenv.toml`, `scripts/deploy.sh`, and configuration outside the per-role trees.
- **Protected, never written without specific approval:** `content/**`, `data/**`, `backend/data/**`, `.env`, tracked credentials, production DBs, deployment settings.

The **independent bug workflow** `scripts/mxissues/**` and `scripts/mxfix/**` belongs to no feature role. It is neither modified, imported, extended, scheduled nor required by the feature coordinator (§11.1).

> **Superseded by v4 (historical v3 ownership, Phase 0):**
> - The v3 table gave Automation `scripts/**` **including** `mxissues`/`mxfix`.
> - It proposed `test_environment/**` → automation (v4 confirms this) and full-project-only for deploy files (v4 replaces this with "explicit task-specific approval").
> - It left these paths for an operator decision (D-6), which v4 resolves.

## 2. Runtime topology **[S]**

```text
Browser ──HTTP──► frontend (Vite dev :5173 → proxy /api ; Docker nginx :9001)
                     │  /api/v1/**  and  /content/** (static media)
                     ▼
              backend FastAPI (uvicorn :8000 ; Docker :9000→8000)
                     ▲   │  service-to-service calls go over HTTP to API_BASE_URL
                     │   └─► /internal/**  (internal JWT minted at startup, 24 h)
kiddo-cli ──UDS /tmp/kiddo-backend.sock──► backend (same app, second uvicorn bound to UDS;
          │                                Docker service "backend-cli", profile "cli")
          └──HTTP (KIDDO_API_BASE_URL)───► backend (all other commands)
```

- Two uvicorn processes (TCP and UDS) share the same content tree, config dir and DB file. Cross-process safety for content CSV registries comes from file locks in `content/**/.<name>_registry.lock` **[S]**.
- External providers (OpenAI, ElevenLabs, Speaktor, Google GenAI, Qwen, ComfyUI) are called from provider classes, using credentials from environment variables **[S]**.
- In Docker, nginx proxies `/api/`, `/internal/` and `/content/` from the public frontend port to the backend **[S]** (phase-status SEC-12).

## 3. Backend layering and enforced boundaries **[S]**

```text
src/main.py ── app factory, lifespan, router composition
src/api/          v1 (public/auth/users/common) + internal (jobs, services) + dependencies (auth, registry DI)
src/core/         settings, models, pipeline, job store, log_client, logging_route — NO imports of src.services (rule)
src/services/     registry, factory, strategy, acceptance + one package per service
src/db/, src/security/   SQLite users (SHA-256 prehash + bcrypt), JWT, Google ID token
```

Documented rules (`backend/CLAUDE.md`):

1. `core/` never imports `services/`; pipeline steps call internal HTTP endpoints.
2. Every structured log record goes over HTTP through `core/log_client.py`. Outside `services/logging/`, nothing resolves `LoggingService` from the registry; `main.py` is the documented exception.
3. Any new mutating route must depend on `require_write_access`, `require_admin` or `require_internal` (guarded by `api/__tests__/test_viewer_access.py`).

Phase 1 verified that rule 1 has one deviation: `core/log_client.py` imports `src.services.logging.routes` (finding F-14; see [backend core](../backend/core/README.md#verified-boundary-deviation)) **[V]**.

## 4. Service and provider model **[S][V]**

```text
            ServiceRegistry._discover()  (pkgutil over src.services.*)
                    │ registers a package iff it exports
                    │ SERVICE_CLASS + SERVICE_NAME + SERVICE_ACCEPT_CRITERIA
                    ▼
 Service (ABC) ◄── XService            ServiceAcceptCriteria(config_subdir, validate, build,
   SUPPORTED_STRATEGIES                   build_runtime_providers?, available_providers?)
   DEFAULT_STRATEGY                             │
   replace_providers / snapshot /               ▼
   get_all / get_active / get_provider   ServiceRegistryBuilder.load_all_from_disk()
                                           config/<subdir>/*.json ──► ServiceProvider.from_dict()
 ServiceProvider (config envelope)                 │   (name=file stem, service, provider,
   ◄── XProviderBase (ABC, per service)            │    provider_category, enabled, priority, config)
         ◄── concrete provider classes             ▼
              (register(name) at import) ◄── ServiceProviderFactory[T].create()
                                                   │
                         sorted by priority, merged with runtime-wired providers,
                         strategy applied from config/_service_strategies.json
                         hot-reloaded by watchfiles on any config change
```

Key distinction for documentation:

- **Provider implementation class**: Python class registered under a provider-type name (e.g. `ElevenLabsTTSProvider` → `elevenlabs`).
- **Configured instance**: one JSON file in `backend/config/<subdir>/` (e.g. `elevenlabs.json` and `elevenlabs_slow.json` are two instances of the same class).
- **Runtime-wired provider**: built in code, not from a file (`FileSystemConfigurationProvider`).
- **Persistence adapter**: e.g. evaluation `JsonScoreStore`/`SQLiteScoreStore`; not a provider.

`evaluation` sits outside this model: it is not registered, has no providers, and its routes construct `EvaluationEngine` and the score stores directly **[V]**. Per-service details: [backend catalog](../backend/README.md).

## 5. Authentication and authorization model

| Caller | Mechanism | Token |
| --- | --- | --- |
| Frontend | `POST /api/v1/login` (form) or `POST /api/v1/login/google` | JWT, 30 min, `Authorization: Bearer` |
| CLI | `POST /api/v1/cli-login` over the UDS only (handler-level check) → then HTTP | JWT, 30 s |
| Internal services | JWT with role `internal` minted at startup | 24 h |
| Roles | `admin` (all), `viewer` (read-only on content; refused on internal and user routes), `internal` | |

**Effective posture depends on two settings that default to insecure values** (`AUTH_DISABLED=True`, `DEBUG_MODE=True`). Under those defaults every request is treated as admin, and the UDS/admin dependencies are bypassed. See SEC-1/SEC-2 in [discovery §11](../discovery/README.md#11-security-and-sensitive-state-findings). Any agent-driven API change must be tested with `AUTH_DISABLED=False`, as the existing integration tests already do via `monkeypatch`.

## 6. Contract and code-generation flow

Current state (Phase 1) **[V]**:

```text
                ┌────────────── backend Python source ──────────────┐
                │                                                    │
   runtime FastAPI app.openapi()  ◄── AUTHORITATIVE (D-2)  AST parse (no runtime import)
                │                                                    │
   scripts/export_openapi.py                 scripts/generate_ts_schemas.py   scripts/generate_ts_client.py <svc>
     ├─ --docs-agents → docs_agents/backend/**/openapi.yaml (module-owned, --check)
     ├─ default → backend/src/services/*/openapi.yaml (prefix-filtered, legacy location)
     └─ --all → backend/docs/openapi.yaml (STALE; D-9)
                                               └─ frontend/src/types/*Schemas.ts   ├─ frontend/src/api/<svc>Client.ts
                                                                                    └─ frontend/src/mocks/<svc>Handlers.ts
   frontend/docs/openapi.yaml — legacy, hand-authored, matches 0 runtime operations
```

- The OpenAPI and TypeScript pipelines do not share an intermediate artifact. A TS↔OpenAPI drift check is still missing.
- Generated destinations are owned by **Backend** (OpenAPI files) and **Frontend** (`frontend/src/{types,api,mocks}`). The generators are owned by **Automation**.

**Decision D-2 — approved 2026-10-16:**

- The runtime FastAPI OpenAPI is the single authoritative HTTP contract.
- Per-service documents are generated by explicit endpoint-module ownership with a `--check` drift mode. This was implemented in Phase 1.

Full record: [`api-contracts.md`](api-contracts.md). Under v4, contract checks must pass **on the combined feature branch HEAD** before a consumer task is unblocked or a feature PR is opened (§11.8).

## 7. Cross-role integration hotspots

These are places where disjoint write scopes **do not** prevent breakage, so integration checks are required:

| Hotspot | Producer → consumer | Existing guard |
| --- | --- | --- |
| Route paths and request/response schemas | backend → cli, frontend | `export_openapi.py --docs-agents --check`; TS regeneration (manual); none for CLI (proven by F-2, F-3) |
| Pydantic `schemas.py` field names | backend → frontend generated DTOs | AST generator; `backend/.cursor/rules/no-field-renames.md` |
| Auth dependencies on new routes | backend → everyone | `test_viewer_access.py` |
| Generated artifacts | automation (generator) → frontend/backend (destination) | none for TS (drift found: F-6) |
| Content filesystem layout and CSV registries | backend → e2e fixtures, mock-data generator | e2e (not runnable here) |
| `testenv.toml` / `.env` keys | automation ↔ all | none |

**New under v4 — shared-checkout runtime collisions** (writers share one working tree, so build and test outputs must not collide). These must be addressed before parallel writers are enabled (Phase 3):

| Collision source | Evidence | Mitigation to implement |
| --- | --- | --- |
| Backend tests write outside `backend/` | `test_content_filesystem_item_images.py` creates lock files in the real `content/` tree (F-5) **[V]** | A Backend fix so the tests use temp content; until then, a sandbox denies the write and the test fails visibly rather than touching protected data |
| Tool caches inside role roots | `backend/.pytest_cache`, `__pycache__`, `frontend/dist`, `frontend/node_modules/.vite*` **[S]** | Allowed inside the owning role's root; never staged (manifest-based commits) |
| Shared ports and sockets | backend `:8000`, Vite `:5173`, UDS `/tmp/kiddo-backend.sock`, testenv ports **[S]** | Per-role port and socket assignment in the role profile; a private `/tmp` per sandbox |
| Generators writing into another role's tree | `generate_ts_*.py` writes `frontend/src/**` **[V]** | Run only by the Frontend role (destination rule) |
| Git index | shared `.git/index` **[S]** | Only the Git coordinator mutates `.git` (§11.7) |

## 8. Existing bug-workflow automation and former v3 attachment points

> **Superseded by v4.** `scripts/mxissues/**` and `scripts/mxfix/**` are a **separate, autonomous bug-detection and repair workflow intended for a different machine**. The feature-development coordinator must not change, refactor, extend, schedule, import from, depend on or repurpose them (v4 §1 rule 3, §7.1).
>
> The "extension points" below were a **v3 proposal and will not be implemented**. They are kept only as a record of what the Phase 0 audit observed. The observed behavior remains accurate background (discovery §9).

```text
checks (validate_*.py, testenv e2e) ─► mxissues sources ─► sync (dedup, dry-run default, cap)
        ─► IssueTracker (GitHub REST/GraphQL + Projects v2 status | GitLab labels)
        ─► mxfix strategy (report | heal | work | batch)
               select open issues ─► set_status(in-progress) ─► .mxworktrees/<iid> off origin/<base>
               ─► Fixer (claude -p | codex exec) ─► review_diff ─► full checks ─► commit/push/PR
```

*(Historical v3 proposal — not to be implemented)*: extend `mxissues.tracker.STATUSES`/`IssueTracker` with role labels and dependencies; add role-filtered claims to `mxfix.strategy.common`; extend `review_diff` into the ownership check; change the `ClaudeFixer`/`CodexFixer` permissions.

What the two systems share under v4 is **only the GitHub repository**. The bug workflow keeps its own labels (`mx-auto`, `source::*`, `severity::*`), its `ai/*` branches, its `.mxworktrees/` and its own PRs. The feature workflow uses `feature:*` labels and `feature/<slug>` branches (§11.9).

## 9. Decisions

The decision register is in §12. Short form:

- Approved: V5-1…V5-5, V4-1…V4-12, D-1, D-2, D-3, D-8, D-11, D-12, D-13, D-14.
- Resolved by v4: D-6.
- Refined by v4: D-7.
- Open: D-4, D-5, D-9, D-10, D-15, D-16, D-17, D-18.

## 10. Approved decision D-3 — single-machine agent execution

**Status:** approved by the operator on 2026-10-16 and **confirmed by v4** (v4 §1 rules 5 and 11, §3.2, §10). **Implementation and validation belong to Phase 3**, with a `flock` demonstration in Phase 2. No locking code exists yet.

**Decision.** All agents execute exclusively on **one Linux machine**. Multi-machine or distributed execution is not a requirement. Redis, distributed databases and distributed locking infrastructure are **out of scope** and must not be introduced.

The Qwen model is served by a **separate remote inference server** (v5 §3.1, §10.1). Only inference requests and responses cross that API. The agent processes, coordinator, Git checkout, locks, tests and all tool execution stay on this host, and remote model hosting authorizes no remote agent, Git, shell or coordination.

**Required properties of the coordination mechanism:**

| Property | Required approach |
| --- | --- |
| Mutual exclusion | Local OS-level advisory file locks (`flock(2)`, via Python `fcntl.flock`) on lock files in a local directory **outside Git** |
| One active writer per specialization | One exclusive lock per role slot (`backend`, `cli`, `frontend`, `automation`, `coordination`); a second writer for the same role fails to acquire it and is queued or rejected |
| Parallelism across specializations | Different role slots are independent locks, so distinct roles may hold their slots concurrently (subject to path-overlap and dependency checks) |
| Exclusive full-project operations | A global project lock. Scoped writers hold it **shared**; a full-project writer needs it **exclusive**, so it waits for all scoped writers to finish and blocks new ones while it runs |
| Automatic release on termination | `flock` locks belong to the open file description and the kernel releases them when the holding process exits or crashes. No lease expiry is needed to free a dead holder's lock |
| Persistent task state | Local, durable task/claim records outside Git (role, task and feature IDs, owner token, PID + process start time, heartbeat, allowed paths, branch/base/head SHA, checkpoint, dependencies). Format chosen in Phase 2 |
| Recovery of interrupted tasks | On start-up, a record whose lock is free (holder gone) marks an **interrupted** task. Recovery inspects that role's paths in the shared checkout and the checkpoint, then resumes or requeues it. A record whose lock is still held is a live worker and must not be replaced |
| Serialized Git mutations (added by v4) | A dedicated Git lock (`flock`) held only by the Git coordinator; working-tree-changing Git operations additionally need the global lock **exclusive** (§11.7) |

**Consequences and constraints:**

- Lock files and state must live on a **local native filesystem**. Observed in Phase 1: the repository is on a `fuseblk` mount (`/media/spyder/_Projects`), while `/` and `$HOME` are `ext4` **[V]**.
- **D-12 (approved 2026-10-16):** coordinator state and `flock` locks live outside Git under `$XDG_STATE_HOME/mx-admin-agents/`. Verification on 2026-10-16, read-only, nothing created **[V]**:
  - `XDG_STATE_HOME` is unset, both in the shell and in the systemd user environment, so the XDG default applies: `~/.local/state/mx-admin-agents/`. The directory does not exist yet.
  - Its existing parent `~/.local/state` is on **ext4**, mounted `rw,relatime` on a local NVMe device, with mode `0700`, owned by the operator. ext4 supports `flock(2)` natively.
- **Before relying on `flock`:**
  - The coordinator resolves the directory the same way every time (`$XDG_STATE_HOME` if set, else `~/.local/state`).
  - At start-up it rejects a state directory on a network or FUSE filesystem (fail closed), creates the directory with mode `0700`, and runs a two-process contention self-test.
  - The Phase 2 tests demonstrate that contention test.
- Lock and state files are **never committed**.
- Because release is tied to the process, locks are held by the **long-lived launcher/coordinator process**. Agent runners (`claude`, `codex`, …) run as its children.
- GitHub Issues and labels are the human-visible record only. The local lock is the authority for "who may write now".
- This replaces Phase 0 open question U-5 and the multi-host alternative.
- **Existing precedent (verified in Phase 1):** `backend/src/services/content/providers/file_system/registry.py` combines a process-wide re-entrant `threading.RLock` with an `fcntl.flock` lock file and a commit marker for crash recovery. Follow the same idiom in the coordinator. It must not import backend code.

> **Superseded by v4:** the v3 wording above originally said recovery "inspects its worktree" and that "fixer CLIs" run under the launcher. Under v4 there are no per-task worktrees, and the children are role agent runners, not `mxfix` fixers.

## 11. v4 feature-development architecture (design baseline for Phases 2–7)

This is a **design baseline**, not an implementation. Phase 2 turns it into `docs_agents/coordination/feature-workflow.md`, a schema proposal and a tested skeleton. Items marked **[U]** must be verified before they are relied on.

> **v5:** this design carries over **unchanged** from v4 (v5 change-summary item 4). Only the provider/runner wiring changed: §11.2 and §11.4 were updated, and §11.11 (remote Qwen integration contract) is new.
>
> **Implemented (2026-10-17):** Phase 2 built the coordinator ([feature-workflow.md](../coordination/feature-workflow.md)); Phase 3 the real launchers, bubblewrap containment and the worker lifecycle ([agent-ownership.md](../coordination/agent-ownership.md), [operations.md](../coordination/operations.md)); Phase 4 the GitHub Issues adapter and dependency lifecycle ([github-integration.md](../coordination/github-integration.md), [task-lifecycle.md](../coordination/task-lifecycle.md)); the Phase 4 extension a GitHub Projects (v2) board ([github-projects.md](../coordination/github-projects.md)). Where those pages are more specific than this baseline, they govern.

### 11.1 Two separate systems, one repository

| | **Feature coordinator (new; built from Phase 2)** | **Bug workflow (existing; untouched)** |
| --- | --- | --- |
| Purpose | Operator-approved new features across Backend/CLI/Frontend | Autonomous bug detection and repair |
| Code | New standalone package (location: D-13) | `scripts/mxissues/**`, `scripts/mxfix/**` |
| Host | The single Linux agent host | A different, dedicated machine |
| Branches | One `feature/<slug>` per feature | `ai/<issue>` and `ai/batch-*` (`testenv.toml` `branch_prefix = "ai/"`) |
| Checkout | One shared checkout per active feature | `.mxworktrees/<iid>` worktrees |
| Issues/labels | `feature:parent`, `feature:backend`, `feature:cli`, `feature:frontend`, `feature:automation`, `feature:blocked`, `feature:review` | `mx-auto`, `source::*`, `severity::*`, Projects v2 `mx-status` |
| PRs | Exactly one aggregate PR per completed feature | Its own fix PRs |

**Isolation rules:**

- The coordinator never imports, calls, schedules or tests `mxfix`/`mxissues`.
- It never reads or writes issues labeled `mx-auto`.
- It never pushes to `ai/*` branches, and the bug workflow never pushes into a live `feature/*` branch.
- No `feature:*` label exists in the repository today (Phase 0 label list), so there is no collision **[V]**.
- Generic tools are **called, not copied**: `scripts/testenv*.py`, `scripts/testenvlib/**`, `scripts/validate*.py`, `export_openapi.py`, `generate_ts_*.py`, `check_docs_agents.py`.

### 11.2 Topology

```text
operator ──► feature coordinator (CLI, single Linux host; dry-run by default)
               │  state dir (ext4, outside Git — D-12)
               │    features/<id>.json   tasks/<id>.json   events.log
               │    locks/: coordinator.lock  project.lock  git.lock  role-{backend,cli,frontend,automation,coordination}.lock
               │
               ├─ launcher(backend)    ─► Claude Code ┐
               ├─ launcher(cli)        ─► Claude Code │  every runner: cwd = THE shared checkout on feature/<slug>,
               ├─ launcher(frontend)   ─► Codex       │  sandboxed to its role's writable roots, .git read-only
               ├─ launcher(automation) ─► local runner ┘  (preferred model: REMOTE Qwen, see §11.11)
               │        └─ HTTP(S) inference only ──► remote Qwen server (OpenAI-compatible Chat Completions;
               │           no checkout, no Git, no shell, no credentials beyond its own API key)
               │
               └─ Git mutation service — the only process that mutates .git:
                    manifest-validated commits, non-force push of feature/<slug>, ONE final PR
GitHub (via gh/REST, separate adapter): parent feature Issue + component Issues — visibility, never locks
```

### 11.3 Feature and task lifecycle (from v4 §7.2; legal transitions specified in Phase 2)

| Entity | Main path | Side states | Meaning of the terminal state |
| --- | --- | --- | --- |
| Feature | `planned → active → integration → review → done` | `blocked`, `failed` | `done` = operator-approved integration, normally a merged, human-reviewed PR, **not** PR creation |
| Component task | `proposed → ready → in-progress → verified` | `blocked`, `failed`, `needs-human` | `verified` = the role's acceptance and test evidence exists **on the combined feature branch HEAD** |

- One feature has one base branch and SHA, one `feature/<slug>`, many component tasks with explicit owners and dependencies, and one final PR.
- A checkout serves **one active feature at a time**; further features are queued.
- Claims of a task and its role slot are atomic under `coordinator.lock`.

### 11.4 Roles, runners and provider configuration

Permissions belong to the **role**; the runner and model are pluggable per role and never change permissions, task identity, branch or documentation contract (v4 §1 rule 12):

| Role | Default runner | Installed on this host | Must be verified before use (Phase 2/3) |
| --- | --- | --- | --- |
| backend, cli | Claude Code | `claude` 2.1.302 **[V]** | Non-interactive mode, permission/tool restriction flags, and how to override the repo's `.claude/settings.json` (`defaultMode: bypassPermissions`) **from the launcher without editing that root file**. **Verified in Phase 3 [V]:** every required flag exists, bypass flags can never be emitted, the launcher's settings live in the state directory ([agent-ownership §4](../coordination/agent-ownership.md#4-real-runner-verification-2026-10-17)) |
| frontend | Codex | `codex` 0.163.0 **[V]** | `exec` mode, sandbox modes and per-path restriction (its `workspace-write` sandbox covers the whole working root, so role confinement must come from the OS sandbox). **Verified in Phase 3 [V]:** Codex runs nested inside the bubblewrap role sandbox, which provides the role confinement ([agent-ownership §4](../coordination/agent-ownership.md#4-real-runner-verification-2026-10-17)) |
| automation | configurable; **preferred: remote Qwen** (`Qwen3.6-35B-A3B-Q4_K_M.gguf`) through an OpenAI-compatible Chat Completions API | D-17 (approved): a minimal native tool loop with a loopback-only HTTP transport, tested against a local fake server (Phase 3) **[V]**; never run against the real server **[U]** | A **local** runner that drives Chat Completions and executes tools on this host, plus every item of the v5 capability validation (§11.11). An OpenAI-compatible text endpoint alone is **not** a coding agent. The Claude Code executable must not be assumed to use this endpoint. Live use waits for D-15, D-16 and D-18 |
| coordination | configurable (v5 example: Claude Code) | — | — |

> **Superseded by v5:** v4 described Qwen as an optional *local* runner on the agent host. Under v5, Qwen is **remote-only**: the model runs on a separate inference server, and the agent and tool-executing process stays local.

Fallback runners are allowed only within the configured cost, privacy and access policy. An unavailable runner marks its role unavailable; it never silently widens another role's scope.

### 11.5 Shared checkout and concurrency

- **One checkout, one branch, no per-agent worktrees.** Throwaway read-only verification copies remain allowed with approval. Phase 0 used `git archive` exports in a scratch directory, which matches that rule.
- Lock order (deadlock-free): `project.lock` (shared for scoped writers, exclusive for full-project or tree-changing Git operations) → `role-<r>.lock` → `git.lock`. Never in reverse.
- Distinct roles run concurrently only when their writable roots are disjoint, their build/test outputs and ports cannot collide (§7), and their dependencies allow progress.
- A second writer for an occupied role is rejected or queued. This applies to Backend across all eight services.
- A full-project session first stops *scheduling*, lets active writers checkpoint and finish, then takes `project.lock` exclusively. It never kills workers.

### 11.6 Write-scope enforcement (layers; a diff check alone is only a detector)

| Layer | Mechanism | Status on this host |
| --- | --- | --- |
| 1. OS sandbox per runner (containment) | `bwrap` mount namespace: whole checkout bound **read-only**, role roots bound read-write, `.git` read-only, protected paths never writable, private `/tmp` | `bwrap` works unprivileged **[V]** (`kernel.apparmor_restrict_unprivileged_userns=1` with a working AppArmor profile); the Landlock LSM is active as an alternative **[V]**. **Implemented in Phase 3 [V]:** the containment matrix (19 write targets × 5 roles, 0 mismatches), private HOME, cleared environment; fails closed without `bwrap` ([agent-ownership.md](../coordination/agent-ownership.md)) |
| 2. Runner permissions | Claude Code permission settings / Codex sandbox flags set by the launcher | **Verified in Phase 3 [V]** (probed on the installed CLIs; live smoke runs observed). Residual risk: runner credentials are bound read-only into the runner sandbox (D-20) |
| 3. Commit gate | Git coordinator validates each path in the manifest: normalized, symlink-resolved, inside the caller's roots, not protected, hash-stable | Phase 2 skeleton, **Phase 3 enforcement [V]** (concurrent single-role commits, no foreign capture, clean `git fsck`) |
| 4. PR/CI gate | Changed-path re-verification across the whole feature diff | Phase 6 |

Generated files follow **destination ownership** (Frontend runs `generate_ts_*.py`). Negative tests (traversal, symlink, rename and generated-output writes outside the role) are mandatory before parallel writers are enabled (Phase 3 gate).

### 11.7 Git mutation coordinator (candidate design; validated in Phase 2)

- **Serialization:** every mutation holds `git.lock`. `checkout`/`switch`/`reset`/`stash`/`rebase`/`merge`/`clean` also need `project.lock` exclusive, meaning all writers are quiescent. Agents cannot run them because `.git` is read-only inside their sandboxes.
- **Commit request:** `{feature_id, task_id, role, paths[], sha256[], expected_head, evidence}`. The coordinator checks:
  - the branch is the feature branch and HEAD equals `expected_head`;
  - each path is within the role's roots and not protected;
  - the hashes are unchanged (the submitting role is paused for the duration);
  - no foreign entries are staged.
- **No shared-index capture (candidate technique):** build the commit through a **private index**:
  1. `GIT_INDEX_FILE=<tmp> git read-tree HEAD`;
  2. `git update-index --add/--remove -- <manifest>`;
  3. `git write-tree`;
  4. `git commit-tree -p HEAD`;
  5. advance the branch atomically with `git update-ref refs/heads/feature/<slug> <new> <expected_head>` (compare-and-swap);
  6. refresh only those paths in the shared index.

  Other roles' uncommitted files are never read or staged. A plain `git add -A` is forbidden. The technique must be proven by Phase 2 tests before adoption.
- **Push:** non-force `git push origin refs/heads/feature/<slug>` with the upstream set explicitly and the remote ref verified. Remote movement is a blocker for human review.
- **PR:** exactly **one** aggregate PR (`feature/<slug>` → approved base), created only after all component tasks are `verified` and the combined-HEAD integration gates pass, with operator approval. The PR body carries the changed-path manifest, component Issues, test/doc evidence, security notes and CI state. Pending CI is reported as pending. Never merge or deploy.

### 11.8 Dependency coordination (contract changes are dependencies, not locks)

A consumer task (CLI/Frontend) blocked on an API waits only for that step. It becomes `ready` when, **on the shared feature branch**, all of the following hold:

1. the Backend commit exists;
2. `python scripts/export_openapi.py --docs-agents --check` passes at that HEAD;
3. the Backend API tests pass at that HEAD.

The Phase 1 tooling is the contract gate. TS regeneration is a Frontend task. Consumers re-run their tests after every contract change. Duplicate requests reuse the existing Backend issue, and cycles are rejected.

### 11.9 GitHub integration (separate adapter)

- A small, independent adapter over `gh` or the official REST API, with ordinary Issues + `feature:*` labels + links. No Projects v2 is required.
- **No Issue or PR writes until the operator approves the scope and credentials exist.** Dry-run and mock trackers come first.
- If GitHub is unavailable, work is preserved and a **local PR candidate** is reported. No links are invented.
- Current constraints: the `gh` token lacks `project` scopes (not needed); branch protection is unavailable on the current plan (B-2).

> **Implemented in Phase 4 and its extension (approved 2026-10-17):**
>
> - **Issues** ([github-integration.md](../coordination/github-integration.md)): a separate adapter over the authenticated `gh` (`github_api.py`, `issue_sync.py`); versioned ownership markers; `feature:*` labels; dependency references; crash-safe, idempotent synchronization outside every coordinator lock. Live-tested with Issues #11–#14. Writes are limited to test features (`github.write_scope: test-features-only`) and pull requests are refused, until D-24 and D-25.
> - **"No Projects v2 is required" is superseded by the Phase 4 extension** ([github-projects.md](../coordination/github-projects.md)): the existing **Project #2** "mx-admin-cloud automation" shows each coordinator-owned Issue as a card (Status, Agent, Feature ID, Blocked Reason; views Kanban and Active Tasks). The board is a **projection only**: it is never read as input and never becomes the authority for locks, claims, permissions or approvals; a card moved by a person is restored on the next sync. Project #1 (the bug workflow's board) is protected and never written. Live-tested with Issues #15–#16.
> - The `gh` login **has** the `project` scope since 2026-10-17. Project #2's built-in workflows "Auto-close issue" and "Item closed" are switched off (D-28).
> - Because the coordinator shares the operator's account (D-26), neither GitHub comments nor board edits ever authorize a sensitive operation.

### 11.10 Recovery and safety defaults

- **Liveness:** stale-worker detection = lock free **and** (PID gone **or** PID start time mismatch, read from `/proc/<pid>/stat`). Only then is a task reclaimable, and never while its role lock is held.
- **Interrupted work:** uncommitted changes in a role's roots remain in the checkout. Recovery reports them for that role instead of discarding them.
- **Defaults:** `--dry-run` is the default. There are bounded retries, backoff, budgets, pause/cancel, and clear failure reports. Nothing is unattended without separate authorization (Phase 7).

### 11.11 Remote OpenAI-compatible Qwen integration contract (v5 §10.1)

**Topology.**

- The Qwen model runs on a **separate remote inference server**, reached from the agent host over an OpenAI-compatible **Chat Completions** API.
- The server serves inference only. It hosts no checkout, receives no Git credentials, executes no shell or tools, and is not a coordinator worker.
- All tool calls are *proposed* by model output and *executed locally*, only after role-scope authorization and validation.
- It is **distinct** from the dedicated remote machine of the independent `mxfix`/`mxissues` workflow; the two must not be conflated or made to depend on each other.

**Operator-supplied model profile (D-14; configuration input, not verified behavior).** These values are recorded exactly as supplied in [v5 §10.1.1](../../docs/multiple_agents/agents_prompt_v5.md):

| Field | Operator value | Status |
| --- | --- | --- |
| Model ID (`id`) | `Qwen3.6-35B-A3B-Q4_K_M.gguf` (exact; never shortened or substituted) | Non-secret default, overridable via `AGENT_QWEN_MODEL`; **verify against the server's response** |
| Display name | `Qwen 3.6 35B A3B` | — |
| Endpoint (`url`) | The operator's full **Chat Completions endpoint**: scheme **`http`** (plain, unencrypted), port `8080`, path `/v1/chat/completions` | The literal address is deliberately **not repeated in `docs_agents/`**; the operator input is in the v5 assignment. Coordinator configs must use runtime variables, never the literal IP (v5 §10.1.1) |
| `toolCalling`, `vision`, `streaming`, `thinking` | `true`, `true`, `true`, `true` | **Declared only. Not validated. Must not be relied on until probed** |
| `contextWindow` | 131,072 tokens (total budget) | Declared |
| `maxOutputTokens` | 16,384 tokens | Declared |
| `maxInputTokens` | 128,000 tokens | Declared metadata. **Not usable in full together with 16,384 output tokens** |
| `editTools` | `apply-patch`, `multi-find-replace`, `find-replace` | **Client-side** editing strategies implemented by the *local* runner and limited to Automation's paths; not server-side API operations |
| `modelOptions.temperature` | `0.2` | Use when supported |

**Endpoint configuration (runtime only; never committed).**

- SDK-style clients: `AGENT_QWEN_BASE_URL` = the endpoint **with `/chat/completions` removed** (ending in `/v1`). Then call `chat.completions.create(...)`, which appends `/chat/completions` exactly once.
- Full-URL clients: the complete endpoint.
- Never duplicate `/v1` or `/chat/completions`.
- `AGENT_QWEN_MODEL` = the model ID above. `AGENT_QWEN_API_KEY` is optional, held only in a private environment or secret store, and never placed in command lines, logs, Issues, `.env` or tracked files.
- Do not rewrite the URL to HTTPS unless the server is actually configured for it.

**Context accounting (mandatory).**

| Quantity | Value |
| --- | --- |
| Total context window | 131,072 |
| Output reservation | 16,384 |
| **Hard maximum input** (all prompt content: system, history, tool definitions, tool results, user context) | **114,688** = 131,072 − 16,384 |
| Safety margin (configurable) | 4,096 |
| **Recommended working input cap** | **110,592** = 114,688 − 4,096 |

- Recalculate downward if the server reports lower limits or extra hidden overhead.
- Truncate, summarize or fail clearly near the cap; never pad to the edge.
- Enforce bounded `max_tokens` per request.

**Recommended adapter defaults (v5 §10 example; illustrative):** `connect_timeout_seconds: 10`, `request_timeout_seconds: 180`, `max_retries: 2` (with backoff), `require_tool_use_validation: true`, `require_tls: true` (see the security gate below), `fallback_requires_policy: true`.

**Required adapter operations:**

- configurable base URL and model ID;
- chat requests (the Responses API only if explicitly verified; Chat Completions compatibility does not imply it);
- a capability profile or detection (`GET /models` only where supported; its absence proves nothing about chat support);
- deadlines and timeouts, bounded retries with backoff, cancellation;
- error classification (unreachable, auth, rate limit, model mismatch, invalid response);
- per-task telemetry that redacts prompts, credentials and code.

**Capability validation before write-capable use.** Steps 1–3 run before any write-capable use; step 4 is required only for a multimodal task.

1. **Mock first** (Phase 2–3), against a fake Chat Completions server:
   - exact model ID, and path joining without duplicated `/v1` or `/chat/completions`;
   - `temperature: 0.2`, bounded `max_tokens`, the context cap;
   - auth redaction, timeout, cancellation, retries, invalid responses, streaming chunk assembly including partial tool-call deltas;
   - tool-call approval and rejection, editing-path permissions, remote unavailability.
2. **Optional live smoke test**, only after operator approval and the transport decision (D-15): plain chat → structured tool/function calling → multi-step loop → streaming → compare actual behavior with the declared profile. No production credentials. No source code without approval (D-16).
3. **Thinking** is backend-specific: never assume OpenAI-standard request fields for it.
4. **Vision** needs its own multimodal smoke test.

If tool calling is unsupported or unreliable, Qwen stays **advisory/read-only**, or another approved runner is selected.

**Security gate for the plain-HTTP endpoint (v5 §10.1.1, §10.1.3):**

- Plain HTTP may expose code, prompts, model input and API keys in transit.
- **By default, no source code or repository context is sent to this endpoint.** Allowed only after an **explicit, documented operator exception and network security assessment** (D-15), or after the transport is secured (TLS with a trusted reverse proxy, HTTPS with certificate verification, or a VPN/tunnel such as WireGuard/Tailscale).
- Independently of transport, the operator approves which code/context may be sent (D-16). Never `.env`, production data, tokens or whole repositories.

Facts for that assessment **[V]** (addresses deliberately not repeated):

- The endpoint's host and port are the **same** as the `base_url` of the backend's three **enabled** `qwen_translate` configurations (`backend/config/text_translation/qwen_en_{bg,es,fr}.json`, plain HTTP). The *application* already sends translation text there.
- The endpoint's host is the **same IP address** as the self-hosted GitLab remote and `testenv.toml [issues.gitlab] host`, both plain HTTP. Whether this is the same machine is **[U]**; do not conflate it with the bug-workflow machine.

**Availability isolation.**

- Endpoint failures, rate limits, auth failures or model mismatch affect **only** the Automation role or task. Claude Code and Codex roles continue where independent.
- There is no automatic cross-provider fallback without an approved policy and budget.
- On failure: bounded retries, then mark the role unavailable or use the approved fallback. Never bypass scoped permissions or tests.

## 12. Decision register

### Approved

| ID | Decision | Status |
| --- | --- | --- |
| V5-1 | Adopt v5 as the authoritative specification; it supersedes v4 and v3 where they conflict | **Approved** 2026-10-16 |
| V5-2 | v4 migration report accepted; Phase 0 and Phase 1 approved and not to be repeated | **Approved** 2026-10-16 |
| V5-3 | Keep `mxfix` and `mxissues` fully independent | **Approved**; re-verified untouched on 2026-10-16 **[V]** |
| V5-4 | Preserve the one-feature, one-branch, one-final-PR policy | **Approved** |
| V5-5 | Do not assume tool calling or other advertised Qwen features work until validated | **Approved** (§11.11 validation sequence) |
| V4-1 | Adopt v4; it supersedes v3 for all new work | **Approved** 2026-10-16; itself superseded by V5-1 where they conflict |
| V4-2 | Preserve all Phase 0 and Phase 1 work; do not repeat either phase | **Approved** |
| V4-3 | Architecture and coordination docs updated to v4; superseded v3 statements annotated, not deleted | **Approved** (this revision) |
| V4-4 | The old Phase 2–7 plan is replaced by the v4 feature-development plan | **Approved** (see [phase-status](../coordination/phase-status.md)) |
| V4-5 | `mxfix` and `mxissues` stay completely independent; no code or behavior changes | **Approved**; verified untouched (`git status`/`git diff` empty for both trees) **[V]** |
| V4-6 | One shared checkout and one feature branch per feature | **Approved** |
| V4-7 | Parallel agents only with non-overlapping write scopes (after Phase 3 enforcement is proven) | **Approved** |
| V4-8 | Claude Code for Backend and CLI | **Approved** (default runner) |
| V4-9 | Codex for Frontend | **Approved** (default runner) |
| V4-10 | Agent providers independently configurable per role | **Approved** |
| V4-11 | Exactly one final PR per completed feature | **Approved** |
| V4-12 | No separate Git worktrees for individual agents | **Approved** |
| D-1 | Document all 8 service packages; `evaluation` marked unregistered/partial | **Approved** by the operator 2026-10-16 (confirmed twice) |
| D-2 | Runtime FastAPI OpenAPI is the single authoritative contract | **Approved** by the operator 2026-10-16 (confirmed twice) |
| D-3 | Single-machine execution; `flock` + local state; no distributed infrastructure | **Approved**; extended with the Git lock (§10); remote model inference does not change it (v5) |
| D-6 | Ownership of paths v3 left unassigned | **Resolved by v4 §3, unchanged in v5 §3** (§1 above) |
| D-7 | Unattended execution policy | **Refined by v4:** `ClaudeFixer`/`CodexFixer` are out of scope; the launcher policy (§11.4/§11.6) replaces them. Unattended runs remain disabled until Phase 7 authorization |
| D-8 | Phase 1 sequential, supervised | **Approved** (completed) |
| D-11 | How bootstrap work reaches the feature base | **Approved 2026-10-16:** the bootstrap work is integrated into `dev` through a **human-reviewed PR before the first feature**. This bootstrap PR is not a feature PR (v5 §11) |
| D-12 | Location of coordinator state and lock files | **Approved 2026-10-16:** outside Git, under `$XDG_STATE_HOME/mx-admin-agents/`. Filesystem verified suitable (local ext4) **[V]**; the `flock` contention self-test runs before first reliance (§10) |
| D-13 | Coordinator package | **Approved 2026-10-16:** `scripts/feature_coordinator/` with entry point `scripts/feature.py` (automation-owned; no imports from `mxfix`/`mxissues`/backend) |
| D-14 | Automation model | **Approved 2026-10-16:** Qwen is **not local**. It runs on a remote inference server behind an OpenAI-compatible Chat Completions API, using the exact operator profile (§11.11). Declared capabilities are unvalidated |
| D-4 | Feature base branch | **Approved 2026-10-17:** `dev` is the base for future `feature/*` branches |
| D-5 | Security remediation priority / authentication and debug defaults | **Approved 2026-10-17:** preserve the existing development authentication and debug defaults; unattended or parallel agent execution must use isolation and security controls (bubblewrap containment, Phase 3) |
| D-17 | Local runner for the remote Qwen model | **Approved 2026-10-17:** a minimal native OpenAI-compatible adapter and a narrowly scoped tool loop (implemented in Phase 3, tested against a local fake server only); no source code to the real server until the transport, disclosure, authentication and capability validation are approved |
| D-19 | Lint gates | **Approved 2026-10-17:** baseline-aware; existing findings reported, new findings block |
| D-10 | Committing the work | **Settled for Phases 0–3** by the operator (`12183e0`, `8454e63`, `b3f3691` on `agents`, not pushed). **2026-10-17:** a reviewed commit plan for Phase 4 and the Projects extension was requested; nothing is committed or pushed without approval ([phase-status](../coordination/phase-status.md)) |
| D-22 | Update this page and the docs index | **Approved 2026-10-17:** done in this revision (implementation status, §11.4, §11.6, §11.9, this register) |
| D-28 | Project #2 built-in workflows | **Approved 2026-10-17:** the board must not close Issues or overwrite coordinator-managed states; "Auto-close issue" and "Item closed" are disabled (verified through the API and by a behavior check) |
| D-29 | Historical test Issues and the board | **Approved 2026-10-17:** Issues #11–#14 stay off Project #2; #15–#16 stay in GitHub for audit; the Active Tasks view shows open Issues only, so finished test tasks do not appear |

> **Superseded (2026-10-17):** D-4, D-5 and D-17 were listed as open below. They were approved with the Phase 3 authorization and are kept in the open table only as history.

> **Superseded by v5:** D-14 was previously worded "Automation runner (local Qwen)". The remote topology replaces that wording.

### Open

| ID | Decision | Recommendation / notes |
| --- | --- | --- |
| D-4 | Feature base branch | *(approved 2026-10-17, see above)* D-11 makes `dev` the integration target for the bootstrap work. Please confirm `dev` explicitly as the base for `feature/*` branches |
| D-5 | Priority of security remediation (SEC-1..6, SEC-11, SEC-12) | *(defaults decision approved 2026-10-17, see above; the SEC remediation itself remains open)* Before any parallel or unattended write-capable run |
| D-9 | Stale `backend/docs/openapi.yaml` | Regenerate (Backend task) or mark superseded |
| D-10 | When and how to commit the bootstrap work | *(settled for Phases 0–3, see above)* A prerequisite for the D-11 PR. Recommended: three reviewed commits on `agents` (Phase 0–1 docs; docs tooling + tests; v4/v5 adoption docs + the v4/v5 prompt files). Nothing is committed yet |
| D-15 | **Transport for the remote Qwen endpoint** (currently plain HTTP) | Secure it (TLS reverse proxy, HTTPS with certificate verification, or WireGuard/Tailscale), **or** grant an explicit, documented plain-HTTP exception after a network security assessment. Until then, no source code or repository context is sent (v5 §10.1.1) |
| D-16 | **Disclosure policy for the remote model** | Which code/context categories may be sent; never `.env`, production data, tokens or whole repositories (v5 §10.1) |
| D-17 | **Local runner for the remote Qwen model** | *(approved 2026-10-17, see above)* A runner on this host that speaks Chat Completions, assembles tool calls and implements the client-side `apply-patch` / `multi-find-replace` / `find-replace` tools within Automation's paths. Candidates are evaluated in Phase 2 (read-only) and validated with mocks in Phase 3. Until validated, Automation uses an approved default runner or Qwen in advisory/read-only mode |
| D-18 | Qwen API authentication | Whether the server requires `AGENT_QWEN_API_KEY`; if so, it is provisioned through a private secret store, never in tracked files |
| D-20, D-21, D-23–D-27 | Runner credentials in the sandbox, runner network policy, the Phase 5 gate set, Phase 5 GitHub write scope, push and the single PR, a separate GitHub identity, test artifacts | Open; options and recommendations in [phase-status](../coordination/phase-status.md#human-decisions-required) |
