# API Contracts — Source of Truth and Pipeline

> Owner: **coordination** (decision record) · Generated artifacts are owned by their destination role · Verified at commit `e2ddd9c` · evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified — see [discovery §6](../discovery/README.md#6-api-contract-artifacts-and-generators)

## Decision D-2 — single authoritative HTTP contract

| Field | Value |
| --- | --- |
| Status | **Approved by the operator on 2026-10-16** (recommended in Phase 0, applied in Phase 1; confirmed explicitly by the operator). Re-affirmed by v4 §8 and v5 §8 (unchanged). |
| Authoritative source | The **runtime FastAPI schema** — `app.openapi()` of `backend/src/main.py` — at a given commit. It is produced by importing the app; no server, lifespan or credentials are required. |
| Why | It is derived from the route decorators and Pydantic models that actually serve requests. The 7 checked-in per-service specs already regenerate from it byte-for-byte **[V]**, while the hand-written and full-app snapshots have drifted (see the table below) **[V]**. |
| What it is not | Not a hand-maintained YAML file, and not `backend/docs/openapi.yaml` or `frontend/docs/openapi.yaml`. Both are derived or legacy. |
| Compatibility | No existing output was moved, deleted or rewritten in Phase 1. Migrating any existing artifact requires explicit approval. |

**v4 integration rule:** contract gates are evaluated **on the combined feature branch HEAD** (`feature/<slug>`), not only in a role's isolated tests. A consumer (CLI/Frontend) task depending on an API change becomes ready when, at that HEAD, all of the following hold:

1. the Backend commit is present;
2. `python scripts/export_openapi.py --docs-agents --check` passes;
3. the Backend API tests pass.

Consumers re-run their tests after every contract change. The single final feature PR requires all contract checks to pass at its head (see [architecture §11.8](README.md#118-dependency-coordination-contract-changes-are-dependencies-not-locks)).

## Contract artifacts

| Artifact | Role | Produced by | Owner (destination) | Status at `e2ddd9c` |
| --- | --- | --- | --- | --- |
| `app.openapi()` (in memory) | **Authoritative** | FastAPI | backend | 91 operations / 70 paths **[V]** |
| `docs_agents/backend/services/<name>/openapi.yaml` (8) + `docs_agents/backend/api/openapi.yaml` | Derived (agent docs) | `scripts/export_openapi.py --docs-agents` | backend | **Up to date**; 91/91 operations, each owned once; `--check` passes **[V]** |
| `backend/src/services/<name>/openapi.yaml` (7) | Derived (legacy location) | `scripts/export_openapi.py` (URL-prefix split) | backend | Reproducible; same operation sets as the module-owned docs for all 7 services **[V]**; no `evaluation` file |
| `backend/docs/openapi.yaml` | Derived snapshot | `scripts/export_openapi.py --all` | backend | **Stale**: 50 operations vs 91 **[V]** — explicitly blocked, see below |
| `frontend/docs/openapi.yaml` | **Legacy design document** | hand-written | frontend | 0 of 24 operations exist at runtime **[V]**; keep as history, never as a contract |
| `frontend/src/types/*Schemas.ts` (9) | Derived types | `scripts/generate_ts_schemas.py` (**AST of `schemas.py`**, not OpenAPI) | frontend | Reproducible **[V]** |
| `frontend/src/api/contentClient.ts` + `src/mocks/contentHandlers.ts` | Derived client | `scripts/generate_ts_client.py content` (AST of routes) | frontend | Reproducible **[V]** |
| `frontend/src/api/configurationClient.ts` | Derived client | `scripts/generate_ts_client.py configuration` | frontend | **Drift** (+6/−6) **[V]** — explicitly blocked, see below |
| CLI (`cli/src/kiddo_cli/`) | Hand-written consumer | — | cli | No generation; 3 commands call paths that do not exist (discovery F-2, F-3) **[V]** |

## Ownership rule for per-service documents

Each operation belongs to the service package that **defines its endpoint function** (`endpoint.__module__`):

| Endpoint module | Document |
| --- | --- |
| `src.services.<name>` and submodules, where `<name>` is a package with `__init__.py` | `docs_agents/backend/services/<name>/openapi.yaml` |
| `src.api.*`, `src.main` | `docs_agents/backend/api/openapi.yaml` |
| anything else | **generation fails** (fail closed) |

Generation also fails when an operation has zero owners or more than one, or when the exported operation count differs from the runtime count. A service package without routes gets a valid document with `paths: {}`. URL prefixes are deliberately **not** used, because a prefix guess silently drops operations: the legacy exporter leaves 19 operations in no per-service file. Implementation: `scripts/openapi_docs.py`, tests in `scripts/tests/test_openapi_docs.py`.

Each generated document contains only its operations, the component schemas reachable from them (transitively), and the security schemes they use. It carries a generated-file header. Security: 85 operations declare `OAuth2PasswordBearer`. The 6 without a requirement are exactly the public routes (`GET /`, `GET /api/v1/ping`, `GET /api/v1/auth/config`, `POST /api/v1/login`, `POST /api/v1/login/google`, `POST /api/v1/cli-login`) **[V]**.

> The contract cannot express runtime auth posture. With the default `AUTH_DISABLED=True`, the declared security is not enforced (discovery SEC-1). Consumers must not infer deployment security from the spec.

## Pipeline and checks

```bash
# Regenerate agent contract docs (Backend role: destination is docs_agents/backend/**)
python scripts/export_openapi.py --docs-agents
# Verify: exits 1 on missing, drifted or stale files, or incomplete ownership (read-only)
python scripts/export_openapi.py --docs-agents --check
# Full documentation validation (read-only): links, inventories, OpenAPI structure, README index, drift
python scripts/check_docs_agents.py
```

The OpenAPI structural validation covers:

- the `openapi: 3.x.y` version and `info`;
- `paths` as a mapping;
- non-empty `responses`;
- declared path parameters;
- known security schemes;
- unique operationIds;
- resolvable local `$ref`s.

**Full JSON Schema validation is not performed**: no validator package is installed (blocker B-1).

When a backend route or schema changes, the Backend task:

1. regenerates `docs_agents/backend/**/openapi.yaml`;
2. runs `--check` and `check_docs_agents.py`;
3. opens owner-specific issues for Frontend (regenerate TS) and CLI (adapt commands) with the contract delta.

Frontend regeneration is a Frontend task. Generator changes are Automation tasks.

## Explicitly blocked drift (Phase 1)

| Item | Why not fixed in Phase 1 | Owner | Unblocks when |
| --- | --- | --- | --- |
| `backend/docs/openapi.yaml` stale (50 vs 91 operations) | A 3,000-line rewrite of a pre-existing file outside `docs_agents/`; the preservation rule needs approval | backend | Operator approves regenerating it with `export_openapi.py --all`, or approves marking it deprecated in favor of `docs_agents/backend/` |
| `frontend/src/api/configurationClient.ts` drift | Application code outside the documentation scope of Phase 1 | frontend | A Frontend task runs `generate_ts_client.py configuration` and verifies type-check, build and tests |
| No TypeScript ↔ OpenAPI cross-check; TS generators have no `--check` | New drift tooling is a Phase 6 deliverable | automation | Phase 6 |
| CLI paths not validated against the contract | No CLI tests exist | cli | CLI test suite (Phase 5/6) |
