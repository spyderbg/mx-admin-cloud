# Automation — Agent Documentation Index

> Owner role: **automation**. Under v4 that means:
> - writable: `scripts/**` **except protected deployment scripts**; `test_environment/**` **except `test_environment/mxfix/**`, `test_environment/mxissues/**`, the protected `test_environment/testenv.local.toml` and the approval-required `test_environment/testenv.toml`**; `docs_agents/automation/**`;
> - generic tests, generators, environment and CI utilities;
> - **not** the separate bug workflow.
>
> Evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified — see [discovery](../discovery/README.md).
>
> Pre-existing docs (preserved): [`test_environment/README.md`](../../test_environment/README.md) (execution strategies, issue tracking, test environment), [`docs/test_environment/implementation_plan.md`](../../docs/test_environment/implementation_plan.md), [`docs/test_environment/initial_spec.md`](../../docs/test_environment/initial_spec.md), [`docs/test_environment/what_next.md`](../../docs/test_environment/what_next.md), [`docker/README.md`](../../docker/README.md), [`test_environment/testenv.toml`](../../test_environment/testenv.toml) (commented settings)
>
> **Superseded by v4:** this page originally announced `mxfix.md` and `mxissues.md` audits as Phase 2 deliverables. v4 withdraws them: `mxfix`/`mxissues` are an independent bug workflow that is not audited, extended or tested as part of feature development. v4 Phase 2 instead builds a **separate** feature coordinator ([architecture §11](../architecture/README.md#11-v4-feature-development-architecture-design-baseline-for-phases-27), [phase-status](../coordination/phase-status.md)). `testing.md` remains planned (generic tests only).

## Generic tools (reusable by the feature workflow, subject to ownership)

| Tool | What it does | Side effects | Who may run it |
| --- | --- | --- | --- |
| `scripts/check_docs_agents.py` | Validates `docs_agents/`: links + anchors, service/provider inventory, OpenAPI structure, README index, drift | **None** (read-only); imports the backend only for the drift check (`--skip-drift` avoids that) | Any role |
| `scripts/export_openapi.py --docs-agents --check` | Compares `docs_agents/backend/**/openapi.yaml` with a fresh runtime export; the **contract gate** for v4 dependencies | None | Any role |
| `scripts/export_openapi.py --docs-agents` | Regenerates `docs_agents/backend/**/openapi.yaml` | Writes Backend-owned docs | Backend role (destination rule) |
| `scripts/export_openapi.py [--service X \| --all]` | Prefix-split per-service specs into `backend/src/services/*/openapi.yaml`, or the full spec into `backend/docs/openapi.yaml` | Writes Backend-owned files | Backend role; `--all` would rewrite a stale file (+3178/−280 lines; D-9) |
| `scripts/generate_ts_schemas.py` | Python `schemas.py` (AST) → `frontend/src/types/*Schemas.ts` | Writes Frontend-owned files | Frontend role |
| `scripts/generate_ts_client.py [service]` | Python routes (AST) → `frontend/src/api/<svc>Client.ts` + MSW stubs | Writes Frontend-owned files | Frontend role |
| `scripts/generate_mockdata.py` | `content/` → `frontend/src/data/mockData.ts` | Writes a Frontend-owned file; reads real content | Frontend role |
| `scripts/validate_build.py`, `validate_tests.py`, `validate_frontend_build.py`, `validate.sh` | Compile + pylint; pytest + frontend tests; type-check + build | **Create venvs and run `pip install` / `npm install`** in shared environments | Only with operator approval; feature gates call the component commands instead (see [backend](../backend/README.md#running-backend-checks), [frontend](../frontend/README.md#commands-all-exist-in-frontendpackagejson)) |
| `test_environment/testenv.py doctor\|seed\|up\|down\|reset\|logs\|status\|shell\|e2e`, `test_environment/testenvlib/**` | Isolated `.testenv/` sandbox (dummy credentials), Docker or native stack, Playwright e2e | Creates/destroys `.testenv/`, starts processes and containers | Automation role; Docker is not installed on the current host (B-4) |
| `feature_coordinator/**` (Phases 2–4; at the repository root since 2026-10-30, owned by no role, run as `python -m feature_coordinator`) | The feature coordinator and its CLI: inspection (`doctor`, `config`, `providers`, `runners`, `sandbox-check`, `status`, `demo`), sandboxed gates (`gate`, `lint-report`), feature operations (`plan`, `activate`, `launch`, `stop`, `recover`, `integrate`, `hold`, `release`) and GitHub Issue coordination (`github status/labels/sync/reconcile/inspect/owned/deps/test-lifecycle/cleanup-test`) ([feature-workflow](../coordination/feature-workflow.md), [operations](../coordination/operations.md), [github-integration](../coordination/github-integration.md)) | Every mutating command is a dry run unless `--apply`. `--apply` writes the state directory `~/.local/state/mx-admin-agents/` and, for feature operations, the feature branch via the Git coordinator; `launch --apply` starts a real, bubblewrap-contained Claude Code/Codex run. Gates run read-only and offline. GitHub writes (Phase 4) only with `--apply` and only for test features (`github.write_scope`), through the existing `gh` login; never a pull request; no remote model calls | Any role (read-only commands); operator for `--apply` |
| `scripts/app.sh`, `deploy.sh`, `setup*.py/sh`, `cli.sh`, `sync_repo.py` | Local run, deployment, setup, CLI install, repo sync | Start/stop services, install packages, deploy | **Explicit task-specific approval** (v4 §3); `deploy.sh` is a protected deployment script |

## Independent bug workflow (observe only; never part of the feature system)

| Tool | What it is | v4 rule |
| --- | --- | --- |
| `test_environment/mxissues/**`, `test_environment/issues.py` | Finding → GitHub/GitLab issue sync with fingerprint dedup; statuses `open`/`in-progress`/`done`/`needs-human`; labels `mx-auto`, `source::*`, `severity::*` | **Do not modify, import, extend, schedule, test or depend on it.** Its issues and labels are never touched by the feature coordinator |
| `test_environment/mxfix/**`, `test_environment/testenv.py run --strategy report\|heal\|work\|batch` | AI repair loop in `.mxworktrees/`, `ai/*` branches, its own PRs; intended for a **different, dedicated machine** | Same as above. Feature agents never run it, and it never pushes into a live `feature/*` branch |

Phase 0 described how these tools behave ([discovery §9](../discovery/README.md#9-automation-scripts)). That description is kept as background only. Their current state is verified untouched: `git status`/`git diff` are empty for both trees **[V]**.

## Tests for automation code

| Suite | Command (repo root) | Status |
| --- | --- | --- |
| Docs tooling (`openapi_docs.py`, `check_docs_agents.py`) — added in Phase 1 | `.venv/bin/python -m pytest -p no:cacheprovider scripts/tests` | 30 passed **[V]** |
| Feature coordinator (v5 Phases 2–3): model, config/providers, paths, state dir and locks, claims and recovery, Git serialization, runners and tracker, workflow; Phase 3: bubblewrap containment, launchers, supervision, parallel writers, sandboxed gates, lint baselines, HTTP transport against a local fake server; **Phase 4:** GitHub adapter (fake `gh` executable and scripted transport for failures), issue sync, ownership, recovery, commands, dependencies, concurrency, the lifecycle rehearsal | same command (`feature_coordinator/tests/`, temporary repositories, state dirs and fake credentials only) | Full `scripts/tests`: **368 passed, 12 opt-in skipped** **[V]** (2026-10-17; 290 + 4 before Phase 4) |
| Live GitHub acceptance (Phase 4, opt-in: `FC_GITHUB_LIVE=1`) | `.venv/bin/python -m pytest -p no:cacheprovider feature_coordinator/tests/test_fc_github_live.py` | **8 passed** against `spyderbg/mx-admin-cloud` (Issues #11–#14, 2026-10-17) **[V]**; skipped without the variable |
| `testenv*`, generators, validators | — | **No tests exist** **[V]**; candidates for generic tests (`testing.md`) |
| `mxissues`, `mxfix` | — | No tests exist; **out of scope under v4** (independent system) |

`-p no:cacheprovider` matters because `.pytest_cache/` is not gitignored.

## Phase 1 additions

| File | Purpose |
| --- | --- |
| `scripts/openapi_docs.py` | Pure helpers: discover service packages, own each operation by **endpoint module** (fail closed on unowned/ambiguous operations), keep only reachable schemas and used security schemes, deterministic YAML rendering, compare/write |
| `scripts/export_openapi.py` (extended) | New `--docs-agents` and `--check` flags. **Existing modes are byte-for-byte unchanged**: all 7 prefix-split specs and the `--all` output regenerate identically after the change **[V]** |
| `scripts/check_docs_agents.py` | Read-only documentation validator (six checks) |
| `scripts/tests/` | `conftest.py`, `test_openapi_docs.py` (18 tests, including one that proves every live operation is owned exactly once), `test_check_docs_agents.py` (12 tests) |

None of these files reference `mxfix` or `mxissues` **[V]**.

## Open items for the automation role

- **v5 Phase 2 (approved, committed as `8454e63`):** the feature coordinator in `feature_coordinator/` (D-13) with `flock` locks and local state (D-3, D-12), a serialized Git mutation path, mock runners, a mock tracker and a mocked remote-Qwen runner. Design and evidence: [feature-workflow](../coordination/feature-workflow.md); status: [phase-status](../coordination/phase-status.md). It follows the content registry's `fcntl.flock` idiom without importing backend code.
- **v5 Phase 3 (approved, committed as `b3f3691`):** real launchers with bubblewrap containment, supervision, sandboxed gates with D-19 lint baselines, and the loopback-only Qwen transport ([agent-ownership](../coordination/agent-ownership.md), [operations](../coordination/operations.md)).
- **v5 Phase 4 (approved 2026-10-17):** the GitHub Issues adapter over `gh`, idempotent synchronization with ownership markers, validated operator commands and the dependency lifecycle ([github-integration](../coordination/github-integration.md), [task-lifecycle](../coordination/task-lifecycle.md)).
- **Phase 4 extension (approved 2026-10-17):** the GitHub Projects (v2) board on Project #2: GraphQL client with a write guard, board synchronization, `python -m feature_coordinator github project …` ([github-projects](../coordination/github-projects.md)).
- **Phase 6:** a TS↔OpenAPI drift check. The TypeScript generators have no `--check` mode of their own.
