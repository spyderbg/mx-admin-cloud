# CLI Capability Matrix (internal coordination data)

> Owner: **coordination** (maintained by the Coordination agent per v4 §5.5, not by the CLI agent) · Source: runtime route table (91 operations) and static extraction of every CLI HTTP call site, at commit `e2ddd9c` **[V]** · User-facing CLI docs: [`../cli/README.md`](../cli/README.md)
>
> Status values: **covered** (a CLI command calls the operation) · **broken** (the CLI calls a path that does not exist) · **gap** (CLI-relevant, no command yet) · **exception** (deliberately not a CLI command; reason given). "Tests" is `none` for every row, because the CLI has no test suite **[V]**. No gap issues have been filed yet; filing them is Phase 4 work (rows marked "issue: —").

## Summary

| Metric | Value |
| --- | --- |
| Runtime operations | **91** |
| **Covered (reachable from the CLI)** | **43** = 41 by commands + 2 indirectly by the authentication flow (`POST /api/v1/login`, `POST /api/v1/cli-login`, called from `cli/src/kiddo_cli/api/auth.py`) |
| Broken (CLI calls a wrong path for this operation) | 3 (`POST /internal/generate`, `…/translators/translate`, `…/translators/translate-file`) |
| Gaps (CLI-relevant, no command yet) | 38 |
| Exceptions (deliberately no command) | 7 |
| **Check** | 43 + 3 + 38 + 7 = **91** |

> **Reconciliation (2026-10-16):** the Phase 1 report summarized this matrix as "41 covered, 3 broken, 38 gaps, 7 exceptions", which sums to 89. It omitted the 2 operations covered indirectly by the auth flow; the matrix itself always included them. A fresh recount from the generated specs and the CLI call sites confirms 41 + 2 + 3 + 38 + 7 = 91 **[V]**. The CLI makes 44 distinct HTTP calls: 41 match runtime operations and 3 target paths that do not exist.

## Broken call sites

| CLI command | Calls | Correct operation | Owner of fix | Issue |
| --- | --- | --- | --- | --- |
| `translate word` | `POST /internal/translate` | `POST /internal/translators/translate` | cli | — |
| `translate file` | `POST /internal/translate-file` | `POST /internal/translators/translate-file` | cli | — |
| `generate run` | `POST /internal/generate-sync` (commented out in the backend) | `POST /internal/generate` + `GET /internal/generate/{job_id}/status`, which is **itself broken** (F-1) | backend first (F-1), then cli | — |

## Matrix

### API layer (`docs_agents/backend/api/openapi.yaml`)

| Operation | Access | CLI | Status |
| --- | --- | --- | --- |
| `GET /` | public | — | exception: welcome text |
| `GET /api/v1/ping` | public | `common ping` | covered |
| `GET /api/v1/status` | authenticated | `common status` | covered |
| `POST /api/v1/operation` | write | `common operation` | covered |
| `POST /api/v1/list-files` | authenticated | `common list-files` | covered |
| `POST /api/v1/login` | public | auth flow (HTTP login) | covered (indirect) |
| `POST /api/v1/cli-login` | public, UDS | auth flow (socket login) | covered (indirect) |
| `POST /api/v1/login/google` | public | — | exception: browser-only Google ID token flow |
| `GET /api/v1/auth/config` | public | — | exception: login-page configuration |
| `GET`/`POST /api/v1/users`, `DELETE …/{username}`, `PATCH …/password`, `PATCH …/role` | UDS + admin | `users list` (and `list users`), `users create`, `users delete`, `users update` | covered (5) |
| `POST /internal/generate` | internal | `generate run` (wrong path) | broken |
| `GET /internal/generate/{job_id}/status` | internal | — | gap (with F-1/F-2) |
| `GET /internal/services` | internal | `list services` | covered |

### configuration (16)

| Operation | CLI | Status |
| --- | --- | --- |
| `GET /internal/configurations` | `config services` | covered |
| `GET`/`POST …/{service_name}` | `config list` / `config create` | covered |
| `GET`/`PATCH`/`DELETE …/{service_name}/{config_name}` | `config show` / `config set`, `config patch` / `config delete` | covered |
| `PUT …/{service_name}/{config_name}` | — | gap (full replace) |
| `POST …/enable-configurations`, `disable-configurations`, `enable-all-configurations`, `disable-all-configurations` | `config enable`, `disable`, `enable-all`, `disable-all` | covered |
| `POST /internal/configurations/apply`, `…/upload` | `config apply`, `config upload` | covered |
| `GET`/`PUT …/{service_name}/strategy` | — | gap (strategy get/set) |
| `GET …/{service_name}/providers` | — | gap (provider types) |

### content (37)

| Operation group | CLI | Status |
| --- | --- | --- |
| `GET`/`POST /api/v1/content/categories` | `content categories`, `content create-category` | covered |
| `GET`/`PATCH`/`DELETE …/categories/{category_id}` | `content category-details`, `content rename-category` (id change via `new_category_id`), `content remove-category` | covered |
| `POST …/categories/restore` | `content restore-category` | covered |
| `POST …/categories/{category_id}/items` | `content create-item` | covered |
| `DELETE …/categories/archive/{archive_name}` | — | gap |
| Category icon (`POST`/`DELETE …/icon`), images (`GET`/`POST …/images`, `PUT`/`DELETE …/images/{image_id}`), voices (`GET`/`POST …/voices`, `PUT`/`DELETE …/voices/{voice_id}`, `POST …/regenerate`) — 11 operations | — | gap (11) |
| Items: `GET …/items`, `GET`/`PUT`/`DELETE …/items/{item_id}` — 4 operations | — | gap (4) |
| Item images: `GET`/`POST …/images`, `POST …/images/generate`, `PUT`/`DELETE …/images/{image_id}`, `PUT …/main` — 6 operations | — | gap (6) |
| Item voices: `GET`/`POST …/voices`, `POST …/voices/generate`, `PUT`/`DELETE …/voices/{voice_id}`, `PUT …/main`, `POST …/optimize`, `POST …/regenerate` — 8 operations | — | gap (8) |

### Other services

| Operation | CLI | Status |
| --- | --- | --- |
| `GET /internal/evaluators/` | — (`list` help mentions evaluators) | gap |
| `POST /internal/evaluators/evaluate` | — | exception: takes server-side filesystem paths (SEC-8); not CLI-safe as-is |
| `GET /internal/loggers/`, `POST …/emit`, `POST …/{logger_name}/log`, `POST …/logging/log/{api-request,service,provider,payment}`, `POST …/reset` | `list loggers`, `logging emit`, `logging log`, `logging api-request`, `logging service`, `logging provider-request`, `logging payment`, `logging reset` | covered (8) |
| `GET /internal/image-generators/` | — | gap |
| `POST /internal/image-generators/generate-image` | — | gap (could save bytes like `generate word`) |
| `POST /internal/image-generators/{provider}/generate` | — | exception: low-level per-provider debug endpoint (writes under `OUTPUT_DIR`) |
| `GET /internal/generators/` | `list generators` | covered |
| `POST /internal/generators/generate-word` | `generate word` | covered |
| `POST /internal/generators/{provider}/generate` | — | exception: low-level pipeline-step endpoint (`GenerateRequest` carries source-file bookkeeping) |
| `GET /internal/translators/` | `list translators` | covered |
| `POST /internal/translators/translate`, `…/translate-file` | `translate word`, `translate file` (wrong paths) | broken (2) |
| `GET /internal/optimizers/` | `list optimizers` | covered |
| `POST /internal/optimizers/{optimizer}/optimize` | — | exception: takes server-side `raw_path`/`output_path` (SEC-8) |

**Counting check (per operation):**

| Group | Ops | Covered | Indirect | Broken | Gap | Exception |
| --- | --- | --- | --- | --- | --- | --- |
| API layer | 17 | 10 | 2 | 1 | 1 | 3 |
| configuration | 16 | 12 | — | — | 4 | — |
| content | 37 | 7 | — | — | 30 | — |
| evaluation | 2 | — | — | — | 1 | 1 |
| logging | 8 | 8 | — | — | — | — |
| text_to_image | 3 | — | — | — | 2 | 1 |
| text_to_speech | 3 | 2 | — | — | — | 1 |
| text_translation | 3 | 1 | — | 2 | — | — |
| voice_optimization | 2 | 1 | — | — | — | 1 |
| **Total** | **91** | **41** | **2** | **3** | **38** | **7** |

> Recount when this file changes. The authoritative inputs are `docs_agents/backend/**/openapi.yaml`, `cli/src/kiddo_cli/commands/*.py` and `cli/src/kiddo_cli/api/auth.py`. Under v4, gap issues are filed as `feature:*` tasks by the feature coordinator (Phase 4), never through the separate `mxissues` workflow. Automating this matrix belongs to the v4 Phase 6 drift/quality gates.
