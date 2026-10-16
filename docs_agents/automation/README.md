# Automation — Agent Documentation Index

> Owner role: **automation**. Under v4 that means:
> - writable: `scripts/**` **except `scripts/mxfix/**`, `scripts/mxissues/**` and protected deployment scripts**; `test_environment/**`; `docs_agents/automation/**`;
> - generic tests, generators, environment and CI utilities;
> - **not** the separate bug workflow.
>
> Evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified — see [discovery](../discovery/README.md).
>
> Pre-existing docs (preserved): [`test_environment/README.md`](../../test_environment/README.md) (execution strategies, issue tracking, test environment), [`docs/test_environment/implementation_plan.md`](../../docs/test_environment/implementation_plan.md), [`docs/test_environment/initial_spec.md`](../../docs/test_environment/initial_spec.md), [`docs/test_environment/what_next.md`](../../docs/test_environment/what_next.md), [`docker/README.md`](../../docker/README.md), [`testenv.toml`](../../testenv.toml) (commented settings)
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
| `scripts/testenv.py doctor\|seed\|up\|down\|reset\|logs\|status\|shell\|e2e`, `scripts/testenvlib/**` | Isolated `.testenv/` sandbox (dummy credentials), Docker or native stack, Playwright e2e | Creates/destroys `.testenv/`, starts processes and containers | Automation role; Docker is not installed on the current host (B-4) |
| `scripts/feature.py`, `scripts/feature_coordinator/**` (Phase 2) | The feature coordinator and its dry-run CLI: `doctor [--apply]`, `config`, `providers`, `plan`, `status`, `demo` ([feature-workflow](../coordination/feature-workflow.md)) | None, except `doctor --apply`, which creates the private state directory `~/.local/state/mx-admin-agents/`; `demo` works in a temporary directory. No Git mutation of the real checkout, no GitHub or model calls | Any role (read-only commands); operator for `--apply` |
| `scripts/app.sh`, `deploy.sh`, `setup*.py/sh`, `cli.sh`, `sync_repo.py` | Local run, deployment, setup, CLI install, repo sync | Start/stop services, install packages, deploy | **Explicit task-specific approval** (v4 §3); `deploy.sh` is a protected deployment script |

## Independent bug workflow (observe only; never part of the feature system)

| Tool | What it is | v4 rule |
| --- | --- | --- |
| `scripts/mxissues/**`, `scripts/issues.py` | Finding → GitHub/GitLab issue sync with fingerprint dedup; statuses `open`/`in-progress`/`done`/`needs-human`; labels `mx-auto`, `source::*`, `severity::*` | **Do not modify, import, extend, schedule, test or depend on it.** Its issues and labels are never touched by the feature coordinator |
| `scripts/mxfix/**`, `scripts/testenv.py run --strategy report\|heal\|work\|batch` | AI repair loop in `.mxworktrees/`, `ai/*` branches, its own PRs; intended for a **different, dedicated machine** | Same as above. Feature agents never run it, and it never pushes into a live `feature/*` branch |

Phase 0 described how these tools behave ([discovery §9](../discovery/README.md#9-automation-scripts)). That description is kept as background only. Their current state is verified untouched: `git status`/`git diff` are empty for both trees **[V]**.

## Tests for automation code

| Suite | Command (repo root) | Status |
| --- | --- | --- |
| Docs tooling (`openapi_docs.py`, `check_docs_agents.py`) — added in Phase 1 | `.venv/bin/python -m pytest -p no:cacheprovider scripts/tests` | 30 passed **[V]** |
| Feature coordinator (v5 Phase 2): model, config/providers (remote Qwen mocked), paths, state dir and locks, claims and recovery, Git serialization, runners and tracker, workflow | same command (`scripts/tests/feature_coordinator/`, temporary repositories and state dirs only) | **174 passed** **[V]** (full `scripts/tests`: 204 passed, 2026-10-17) |
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

- **v5 Phase 2 (implemented, awaiting approval):** the feature coordinator in `scripts/feature_coordinator/` (D-13) with `flock` locks and local state (D-3, D-12), a serialized Git mutation path, mock runners, a mock tracker and a mocked remote-Qwen runner. Design and evidence: [feature-workflow](../coordination/feature-workflow.md); status: [phase-status](../coordination/phase-status.md). It follows the content registry's `fcntl.flock` idiom without importing backend code.
- **Phase 3:** real launchers and OS containment, the live Chat Completions client (mock server first), the D-17 runner.
- **Phase 6:** a TS↔OpenAPI drift check. The TypeScript generators have no `--check` mode of their own.
