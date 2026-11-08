# Phase 0 Discovery — Verified Inventory and Baseline

> **Phase:** 0 (Inspect and Baseline) · **Produced:** 2026-10-16 · **Baseline commit:** `e2ddd9c` (branch `agents`, clean working tree)
> **Assignment:** [`docs/multiple_agents/agents_prompt_v3.md`](../../docs/multiple_agents/agents_prompt_v3.md) · **Status record:** [`../coordination/phase-status.md`](../coordination/phase-status.md) · **Architecture (preliminary):** [`../architecture/README.md`](../architecture/README.md)

> **v4 annotation (2026-10-16):** this is **historical Phase 0 evidence**, produced under v3 and preserved unchanged. v4 ([`agents_prompt_v4.md`](../../docs/multiple_agents/agents_prompt_v4.md)) supersedes v3's plan to build agent coordination on `mxfix`/`mxissues` (§9). Those tools are now a separate bug workflow that is observed only and never modified. All observations below remain valid. Current status: [`../coordination/phase-status.md`](../coordination/phase-status.md).

This document records what was **observed** in the live repository, not what the assignment's directory snapshot implies. Every claim carries an evidence tag:

| Tag | Meaning |
| --- | --- |
| **[V]** | Verified by executing something (test run, import-time introspection, regeneration diff, CLI invocation, read-only API query). |
| **[S]** | Verified by reading source/config; not executed. |
| **[U]** | Unverified assumption or not observable from this checkout; the next verification step is stated. |

---

## 1. How the baseline was produced (non-destructive method)

- All executed checks ran against an **isolated export** of `HEAD` (`git archive HEAD | tar -x` into a session scratch directory). The export contains no `.env`, so no real provider credentials were available to any test or script. No credential-like variables were present in the shell environment (names checked only).
- `frontend/node_modules` was **copied** (not symlinked) into the export so that Vite's config bundling could not write into the real tree.
- Generators that overwrite tracked files (`export_openapi.py`, `generate_ts_client.py`, `generate_ts_schemas.py`) ran in a **second, separate export**, and their output was diffed against the committed files.
- `scripts/validate.sh`, `validate_build.py`, `validate_tests.py` and `validate_frontend_build.py` were **not** run, because they `pip install` / `npm install` into the shared environments. Their component checks were run directly (see §12).
- After all runs: `git status --porcelain` was unchanged, and SHA-256 checksums of `backend/data/users.db` and `data/users.db` matched their pre-run values **[V]**.
- GitHub was queried **read-only** (`gh repo view`, `gh issue list/view`, `gh label list`, `gh pr list`, `gh run list/view`, `gh api …/protection`). Nothing was created, labeled or commented.

## 2. Repository snapshot

| Item | Observed value |
| --- | --- |
| Remotes | `origin` → GitHub `spyderbg/mx-admin-cloud` (private); `gitlab` → self-hosted GitLab `marxman/mx-admin` (plain HTTP) **[V]** |
| Branches | local `main`, `dev`, `agents` (current), `test-environment`; remote `codex/*`, `cursor/*` feature branches **[V]** |
| Default branch | `main` (GitHub) **[V]**; automation base branch in `testenv.toml` is `dev` **[S]** |
| Tracked files | 2,473 (`frontend/src` 1,545, mostly vendored assets; `content/` ~440 media/CSV files; `backend/src` 202) **[V]** |
| Python | repo-root `.venv` = CPython 3.12.3 (FastAPI 0.141.1, Pydantic 2.13.5, pytest 9.1.1, pytest-asyncio 1.4.0, Typer 0.22.0, pylint 4.0.8) **[V]**; Docker image uses `python:3.11-slim` **[S]**; `backend/CLAUDE.md` documents Python 3.10 **[S]**. `requirements.txt` is unpinned. |
| Node | 22.23.3 / npm 10.9.9 (IDE-bundled binary on `PATH`) **[V]** |
| Not available on this host | `docker`, Playwright browsers / `test_environment/e2e/node_modules`, any OpenAPI validator package (`openapi-spec-validator` etc.) **[V]** |
| `gh` CLI | authenticated as `spyderbg`; token scopes `gist, read:org, repo, workflow` (no `project`/`read:project`) **[V]** |

### Top-level components

| Path | Role | Notes |
| --- | --- | --- |
| `backend/` | FastAPI app (`backend/src/main.py`), service packages, config JSON, tests | Primary API |
| `cli/` | Typer CLI `kiddo-cli` (`cli/src/kiddo_cli/`), editable-installed in root `.venv` | HTTP-only client |
| `frontend/` | Vue 3 + TS + Vite SPA | Star Admin Bootstrap 4 theme vendored |
| `scripts/` | Generators, validation, `mxissues`, `mxfix`, `testenv*`, deploy/app helpers | Automation-owned |
| `test_environment/` | Playwright e2e (9 specs), stub provider server | Used by `test_environment/testenv.py` |
| `docker/` | Compose (backend, frontend, backend-cli on UDS, cli), test compose, Dockerfiles, nginx | |
| `content/` | Real content tree (categories/items/images/voices + CSV registries), **tracked in git** | Production-like data |
| `data/`, `backend/data/` | `users.db` (SQLite, **tracked in git**), `users.sql` | Sensitive state — never touched |
| `workspaces/`, `output/` | Editor workspace, run output (gitignored) | Out of scope |

## 3. Instruction files and existing agent tooling

| File | Observed content | Notes |
| --- | --- | --- |
| `CLAUDE.md`, `AGENTS.md` (root) | Identical; project overview + OpenCode commands `/api-sync`, `/verify`, `/frontend`, `/mockdata` | |
| `backend/CLAUDE.md` | Detailed architecture, logging-over-HTTP rule, provider strategy, content FS layout, auth model, env vars | Lists TTS without `pyttsx3` and translators without `qwen` — minor drift **[S]** |
| `frontend/CLAUDE.md` | Data layer/cache, generated-file rule, auth/roles, modal pattern | States "No test framework is configured" — **stale**: `package.json` defines `test:voices`, `test:content`, `test:auth` (`node --test`) **[V]** |
| `cli/CLAUDE.md` | API-only rule, auth priority, UDS requirement | Subcommand list omits `config` and `logging` **[S]** |
| `.opencode/commands/*.md` | `api-sync`, `verify`, `frontend`, `mockdata`, `commit` command recipes | `/verify` scripts install dependencies |
| `.claude/settings.json`, `backend/.claude/settings.json` | `permissions.defaultMode = "bypassPermissions"` | Conflicts with v3 scoped-write enforcement — see §11 **[S]** |
| `.claude/`, `backend/.claude/` | commands, readme rules, a `code-reviewer` skill/agents | |
| `.cursor/`, `backend/.cursor/`, `frontend/.cursor/`, `.aiassistant/`, `.agents/skills/` | Rules, prompts, historical plans | Legacy agent context; preserve |

## 4. Existing documentation to preserve (not moved, not rewritten)

Component READMEs: [`README.md`](../../README.md), [`backend/README.md`](../../backend/README.md), [`cli/README.md`](../../cli/README.md), [`frontend/README.md`](../../frontend/README.md), [`docker/README.md`](../../docker/README.md), [`test_environment/README.md`](../../test_environment/README.md). Vendored icon-font READMEs under `frontend/src/assets/vendors/iconfonts/*` are third-party and out of scope.

Technical and planning docs:

- `docs/`: [`required_endpoints.md`](../../docs/required_endpoints.md) (887 lines, legacy frontend-endpoint wish list using `{category_name}`/`objects` URLs that no longer match runtime), [`backend_first_build.md`](../../docs/backend_first_build.md), [`game_plugin_generate.md`](../../docs/game_plugin_generate.md), [`content_service_features/categories_features.md`](../../docs/content_service_features/categories_features.md), [`item_voices/`](../../docs/item_voices/), [`test_environment/{initial_spec,implementation_plan,what_next}.md`](../../docs/test_environment/implementation_plan.md), [`multiple_agents/`](../../docs/multiple_agents/) (this assignment and its predecessors), `kiddo-admin-api.postman_collection.json`.
- `backend/`: [`code_improvements.md`](../../backend/code_improvements.md) (424-line prioritized backlog), [`docs/openapi.yaml`](../../backend/docs/openapi.yaml) (stale snapshot, see §6), per-service `src/services/*/openapi.yaml`.
- `frontend/`: [`code_improvements.md`](../../frontend/code_improvements.md), [`docs/types-usage.md`](../../frontend/docs/types-usage.md), [`docs/item-voices-api.md`](../../frontend/docs/item-voices-api.md), [`docs/backend-regeneration-agent-tasks.md`](../../frontend/docs/backend-regeneration-agent-tasks.md), [`docs/openapi.yaml`](../../frontend/docs/openapi.yaml) (legacy design spec, see §6).

## 5. Backend

### 5.1 Application composition **[S][V]**

`backend/src/main.py` builds one FastAPI app and includes 13 routers: `api/v1/{routes_auth,routes_common,routes_users}` (prefix `/api/v1`), `api/internal/{jobs_routes,services_routes}`, and one router per service package (`logging`, `content`, `configuration`, `text_translation`, `text_to_speech`, `text_to_image`, `voice_optimization`, `evaluation`). It also mounts `/content` as `ContentStaticFiles`. Lifespan: `UsersDb.init_db()`, registry build and load from `backend/config/`, `LoggingService.configure()`, in-memory `JobStore`, a freshly signed 24 h internal JWT, `log_client.configure()`, a `watchfiles` config hot-reload task, and a job-eviction task.

### 5.2 The service / provider model (actual, not assumed)

A literal **`ServiceProvider` class exists** — `backend/src/services/models.py:107`. It is the parsed `service_provider` envelope of one config JSON file (`name` = file stem, `service`, `provider`, `provider_category`, `enabled`, `priority` (default 999), raw `config` dict, `config_path`, `base_dir`; helpers `is_active()`, `get_priority()`, `api_key_env_name()` → `<CONFIG_NAME>_API_KEY`, `resolve_api_key()`, `get_info()`) **[S]**.

| Concept | Verified implementation | Source |
| --- | --- | --- |
| Service root contract | `Service(ABC)`: abstract `replace_providers`, `providers_snapshot`, `get_all_providers`, `get_active_providers`; concrete `get_provider(name)`; class attrs `SUPPORTED_STRATEGIES`, `DEFAULT_STRATEGY` | `services/models.py:34` |
| Per-service provider base | Abstract subclass of `ServiceProvider` per service (e.g. `TTSProvider`, `TextToImageProvider`, `ContentProvider`, `LoggingServiceProvider`, `TextTranslationServiceProvider`, `AudioOptimizerBase`, `ConfigurationProvider`, `EvaluationServiceProvider`) | `services/<svc>/models.py` |
| Provider factory | `ServiceProviderFactory[ProviderT]` — per-subclass `_builders` map; `register(name, cls)`, `supports`, `available_names`, `create(service_provider, base_dir)` | `services/provider_factory.py` |
| Registration | Each concrete provider module calls `<Cls>.register()` at import time; each `providers/__init__.py` auto-imports its modules with `pkgutil` | `services/*/providers/*.py` |
| Service registration | A package is registered only if it exports `SERVICE_CLASS`, `SERVICE_NAME`, `SERVICE_ACCEPT_CRITERIA` (`ServiceAcceptCriteria`: `config_subdir`, `validate_provider`, `build_provider`, optional `build_runtime_providers`, `available_providers`) | `services/service_registry.py:76`, `services/acceptance.py` |
| Loading | `ServiceRegistryBuilder.load_all_from_disk()` reads `config/<config_subdir>/*.json`, validates + materializes via the factory, merges runtime-wired providers, sorts by priority, applies `config/_service_strategies.json` | `services/service_registry.py:247` |
| Selection strategy | `ProviderSelectionStrategy` = `ALL_PROVIDERS` / `FIRST_SUCCESS`; helpers `first_success`, `dispatch_each`, `AllProvidersFailedError` | `services/provider_strategy.py` |

### 5.3 Service inventory — 8 service packages, 7 registered

The assignment lists 7 services and omits **`content`**, the largest service and the only one with a public `/api/v1` API surface. Separately, **`evaluation` is not registered**: its package exports no `SERVICE_CLASS`. The registry discovery below was executed **[V]**.

| Package | Registered | Service class | Provider implementation classes (registered name) | Configured instances (active / total) | Strategy (supported → active) | HTTP prefix |
| --- | --- | --- | --- | --- | --- | --- |
| `configuration` | yes | `ConfigurationService` | `FileSystemConfigurationProvider` (`file_system`) — **runtime-wired** via `build_runtime_providers`; no `backend/config/configuration/` dir exists | 1/1 (`configuration_file_system`) | none | `/internal/configurations` |
| `content` | yes | `ContentService` | `FileSystemContentProvider` (`file_system`; composed of `FileSystemCategoriesMixin`, `FileSystemItemsMixin`, `FileSystemInternalApiMixin`; CSV `Registry` in `registry.py`) | 1/1 (`file_system`) | FIRST_SUCCESS → FIRST_SUCCESS | `/api/v1/content` + `/content` static mount |
| `evaluation` | **no** | `EvaluationService` (defined, unused) | **none** — `EvaluationProviderFactory` has no registrations; `EvaluationServiceProvider.evaluate_text` docstring is copy-pasted translator text | n/a | n/a | `/internal/evaluators` (routes build `EvaluationEngine` directly) |
| `logging` | yes | `LoggingService` | `StructLogProvider` (`struct_log`) | 2/3 (`requests`, `default`; `trace` disabled) | both → ALL_PROVIDERS | `/internal/loggers` |
| `text_to_image` | yes | `TextToImageService` | `OpenAIImageProvider` (`openai`), `OpenAIHttpImageProvider` (`openai_http`), `GenAIImageProvider` (`genai`), `GenAIHttpImageProvider` (`genai_http`), `ComfyUIImageProvider` (`comfyui`); support: `governor.py` (`RequestGovernor`), `pricing.py`, `templating.py` | 1/5 (`openai`) | FIRST_SUCCESS → FIRST_SUCCESS | `/internal/image-generators` |
| `text_to_speech` | yes | `TextToSpeechService` | `ElevenLabsTTSProvider` (`elevenlabs`), `OpenAITTSProvider` (`openai`), `Pyttsx3TTSProvider` (`pyttsx3`), `SpeaktorTTSProvider` (`speaktor`), `StaticTTSProvider` (`static`) | 2/6 (`elevenlabs`, `pyttsx3`) | FIRST_SUCCESS → FIRST_SUCCESS | `/internal/generators` |
| `text_translation` | yes | `TextTranslationService` | `OpenAITextTranslate` (`openai_translate`), `QwenTextTranslate` (`qwen_translate`), `StaticTextTranslate` (`static_translate`) | 4/7 (`static`, `qwen_en_{bg,es,fr}`) | FIRST_SUCCESS → FIRST_SUCCESS | `/internal/translators` |
| `voice_optimization` | yes | `VoiceOptimizationService` | `FfmpegAudioOptimizer` (`ffmpeg`), `PydubAudioOptimizer` (`pydub`); `normalizer.py` is a function (`normalize_audio`), not a provider | 3/3 (`ffmpeg_low`, `ffmpeg_high`, `pydub_optimized`) | both → ALL_PROVIDERS | `/internal/optimizers` |

Evaluation persistence: `ScoreStore` (ABC), `JsonScoreStore`, `SQLiteScoreStore`, `MultiScoreStore` are **persistence adapters**, not providers **[S]**. STT backends (`openai`, `elevenlabs`, `speaktor`) are selected inside `EvaluationEngine` by a `stt_provider` key in the TTS config's `evaluate` block, not through the provider model **[S]**. Provider config JSON files contain **no secret-named keys**: credentials resolve from environment variables **[V]**.

### 5.4 Runtime HTTP surface **[V]**

Introspection of the imported app (no server, no lifespan) found **91 API operations on 70 paths**, plus the framework routes `/openapi.json`, `/docs`, `/docs/oauth2-redirect`, `/redoc` and the `/content` static mount. Classification by **declared dependencies**:

| Class | Count | Operations |
| --- | --- | --- |
| Public (no auth dependency) | 6 | `POST /api/v1/login`, `GET /api/v1/auth/config`, `POST /api/v1/login/google`, `POST /api/v1/cli-login` (UDS check inside the handler), `GET /api/v1/ping`, `GET /` |
| Authenticated (any role) | 2 | `GET /api/v1/status`, `POST /api/v1/list-files` |
| Authenticated, viewer read-only (`require_write_access`) | 38 | 37 `/api/v1/content/**` operations + `POST /api/v1/operation` |
| UDS-only + admin (`require_uds` + `require_admin`) | 5 | `/api/v1/users` CRUD, password, role |
| Internal (`require_internal`: role `internal` or `admin`) | 40 | jobs 2, services 1, loggers 8, configurations 16, translators 3, generators 3, image-generators 3, optimizers 2, evaluators 2 |

**Important:** this is the *declared* model. With current defaults (`AUTH_DISABLED=True`, `DEBUG_MODE=True`), `verify_token` returns an admin identity for every request, and `require_uds` / `require_admin` are overridden. The effective runtime posture therefore depends on the deployment environment (§11).

### 5.5 Backend tests and conventions

- `backend/pytest.ini`: `testpaths = src`, `python_files = test_*.py`, `asyncio_mode = auto`. Tests live in `src/**/__tests__/` (38 test files).
- No tests in `services/evaluation/__tests__/` or `services/voice_optimization/__tests__/` (only `__init__.py`) **[V]**.
- Isolation gap **[V]**: `services/content/__tests__/test_content_filesystem_item_images.py` creates `.categories_registry.lock` and `foods/.items_registry.lock` in the **real** `content/` tree. These files are gitignored lock files, not data changes, but the test touches non-temporary state.
- `api/__tests__/test_viewer_access.py` enumerates every mutating operation from `app.openapi()` and asserts that viewers get 403. This is the existing auth regression guard.

## 6. API contract artifacts and generators

| Artifact | Produced by | Input | Reproducible at `HEAD`? |
| --- | --- | --- | --- |
| `backend/src/services/{configuration,content,logging,text_to_image,text_to_speech,text_translation,voice_optimization}/openapi.yaml` | `scripts/export_openapi.py` (default mode) | Runtime `app.openapi()`, filtered by **hard-coded URL prefixes** | **Yes — 7/7 byte-identical [V]** |
| `backend/docs/openapi.yaml` | `scripts/export_openapi.py --all` | Runtime `app.openapi()` | **No — stale [V]**: 50 ops / 44 paths vs runtime 91 / 70; 4 ops no longer exist (`/api/v1/content/categories/{category_name}` GET/PATCH/DELETE, `POST /api/v1/content/items`) |
| `frontend/docs/openapi.yaml` | Unknown (hand-authored, "KiddoWords Content Management API") | — | **Not derived [V]**: 0 of 24 ops match runtime paths; uses `BearerAuth`, no per-op security. Legacy design document. |
| `frontend/src/types/*Schemas.ts` (9 files) | `scripts/generate_ts_schemas.py` | **Python AST** of `services/*/schemas.py` + `services/schemas.py` (not OpenAPI) | **Yes — 9/9 identical [V]** |
| `frontend/src/api/contentClient.ts`, `frontend/src/mocks/contentHandlers.ts` | `scripts/generate_ts_client.py` (default `content`) | **Python AST** of `services/content/routes/` | **Yes [V]** |
| `frontend/src/api/configurationClient.ts` | `scripts/generate_ts_client.py configuration` | AST of `services/configuration/routes.py` | **No — drift [V]**: +6/−6 lines (function order and one summary string differ) |
| `frontend/src/data/mockData.ts` | `scripts/generate_mockdata.py` | `content/` tree | Not checked (writes from real content) |

Contract findings:

1. There are **two independent derivation pipelines** from the same Python source: runtime FastAPI OpenAPI → YAML, and AST parsing → TypeScript. Neither consumes the other, and no drift check exists in CI **[V]**.
2. `export_openapi.py` assigns ownership by URL prefix (`/internal/translat`, `/internal/log`, …). No per-service spec covers `evaluation`, `/internal/generate*`, `/internal/services`, or the `/api/v1` auth/common/users routes: **19 of 91 operations** are in no per-service spec **[V]**.
3. The `/api-sync` recipe in `.opencode/commands/api-sync.md` regenerates only the `content` client (`generate_ts_client.py` without arguments), so `configurationClient.ts` silently drifts **[S][V]**.

## 7. CLI (`kiddo_cli`)

- Package `kiddo-cli` 0.1.1, entry `kiddo_cli.main:app`, Typer 0.22, editable install in the root `.venv` **[V]**. Groups: `common`, `config`, `content`, `list`, `generate`, `translate`, `users`, `logging` **[V]** (`--help` renders for all 9 levels, exit 0).
- **No imports from `backend/`/`src.*`** — the API-only boundary holds today **[S]**.
- **No CLI tests exist** (no `tests/`, `test_*.py` or `conftest.py` under `cli/`) **[V]**.
- Auth: `--token` → `KIDDO_TOKEN` → UDS `cli-login` (`/tmp/kiddo-backend.sock` or `KIDDO_CLI_SOCKET`) → HTTP login with `KIDDO_USER` / `KIDDO_PASSWORD` **[S]**. User-management calls use httpx over UDS when the socket exists **[S]**.
- **Coverage [V]** (static call-site extraction vs runtime route table): CLI commands reach **41 of 91** runtime operations; the auth flow additionally uses `login`/`cli-login`. 50 operations have no CLI command, most of them content image/voice/item endpoints, `text_to_image`, optimize, evaluate, configuration strategy/providers/PUT, and jobs.
- **Broken CLI paths [V]** (the path is absent from the runtime route table; HTTP not exercised):
  - `generate run` → `POST /internal/generate-sync` — the endpoint is commented out (`api/internal/jobs_routes.py`, around line 130).
  - `translate word` → `POST /internal/translate`; `translate file` → `POST /internal/translate-file`. The real paths are `/internal/translators/translate` and `/internal/translators/translate-file`.
- Help-text drift: the `list` group help mentions `evaluators`, but there is no such command. The `content` group help lists 3 commands, but there are 7 **[V]**.
- Pylint (CLI): 9.62/10, 34 messages, exit 28 **[V]**.

## 8. Frontend

- Vue 3 + TS + Vite 8, axios, vue-router, MSW for mock mode; no state library **[S]**. Views: Dashboard, Categories, ItemDetails, Login, Configurations, ServiceConfiguration **[S]**.
- **Tests [V]:** `npm run test:voices` 29/29, `test:content` 5/5, `test:auth` 8/8 (Node's built-in `node --test`, run with `--experimental-strip-types` for TypeScript). There is no component or DOM test framework.
- **Cache layers [S]** (to be documented in depth by the Frontend agent):
  - `src/api/cache.ts` — module-level reactive state (`sharedCategories`, `sharedCategoryNames`, `sharedArchivedCategories`, loading/error/unavailable flags), in-flight dedup (`withInflight`), `ensure*` / `invalidate*` pairs, `revalidateContent()` on cache hit, and 503 handling via `sharedContentUnavailable`.
  - Media cache-busting: `categoryIconVersions`, `itemThumbnailVersions` (persisted in `localStorage`) + `utils/cacheBustedUri.ts` (`?v=<n>`).
  - `src/api/configurationCache.ts` — services/config-file lists with ensure/refresh.
  - `src/api/uiConfigurationDb.ts` — IndexedDB `mx-admin` / `ui_configuration` store for UI preferences.
- Preliminary risks **[S]**:
  - Logout (`layouts/AppNavbar.vue:383`) only removes `localStorage.token`, so module-level caches survive a user switch within the same tab.
  - No 401 response interceptor (`api/client.ts`), so an expired token is not handled.
  - Both overlap `frontend/code_improvements.md` §3.4.
- Generated vs hand-written: see §6. `api/cache.ts`, `itemVoicesClient.ts`, `auth.ts`, `client.ts`, `internalClient.ts` and `types/{viewModels,index,itemVoices,localization}.ts` are hand-written.

## 9. Automation (`scripts/`)

**No automated tests exist for any of `scripts/`** (`mxissues`, `mxfix`, `testenv*`, generators, validators) **[V]**. Behavior below is from source reading **[S]**.

### 9.1 `mxissues` (issue tracking)

- `Finding` model (sources `e2e | pytest | pylint | detector | manual`; severities `high | medium | low`); source parsers in `sources/`.
- Dedup: `fingerprint.compute()` = first 12 hex characters of `sha1("<source>:<key>")`, embedded in the issue body as a fenced `mx-meta` block. A local cache in `.mxissues/index-<tracker>.json` is a fast path only; the API stays authoritative.
- `sync.run_sync()` runs **dry by default**, needs `apply=True` to write, caps new issues with `max_new_issues` (`testenv.toml`: 1), and reopens done issues as regressions.
- `IssueTracker` ABC with GitHub (REST + GraphQL) and GitLab backends. Statuses: **`open`, `in-progress`, `done`, `needs-human`** (4 only). On GitHub, status is a Projects v2 single-select field (`mx-status`, project `spyderbg/1`); on GitLab it is exclusive `status::*` labels.
- Ownership: the tool creates and rewrites only issues labeled `mx-auto`.
- `set_status()` is write-then-read-back-assert, **not compare-and-swap**.
- Tokens: `GITHUB_PAT` / `GITLAB_TOKEN` from the repo-root `.env`. **Neither key is present in the local `.env`** (key names checked) **[V]**.

### 9.2 `mxfix` (fix automation)

- `Fixer` protocol + `ClaudeFixer` (`claude -p --output-format json --permission-mode bypassPermissions`, cwd = worktree) + `CodexFixer` (`codex exec --json --output-schema …`, `-C <worktree>`, `--sandbox workspace-write`).
- `worktree.py` touches only `.mxworktrees/<name>` (created from `origin/<base>`) and never runs `git worktree prune`.
- `git_target.py`: `git add -A` + commit with `Closes <ref>`, push, PR via REST + `GITHUB_PAT`; the PR body avoids closing keywords.
- Strategies (`testenv.py run`):
  - `report` — check → sync; no code changes.
  - `heal` — check → sync → one issue at a time.
  - `work` — the **`testenv.toml` default**; one issue at a time from the existing backlog.
  - `batch` — up to `concurrency=3` parallel worktrees, one integration branch, bisect on red.
- Guards: `review_diff()` rejects changes to `pylintrc` / `.pylintrc` / `pytest.ini` / `pyproject.toml`, and test-only diffs for pytest/e2e issues. `max_fix_attempts=3` → `needs-human`. `needs-human` issues are retried only after a fresh human comment.

### 9.3 Test environment and validators

- `testenv.py` (`doctor | seed | up | down | reset | logs | status | shell | e2e | run`) seeds an isolated `.testenv/` with dummy credentials and refuses symlinks. It supports docker and native modes and uses a stub provider. Not run here: Docker is absent, and native mode starts servers.
- `validate_build.py` / `validate_tests.py` / `validate_frontend_build.py` wrap compile + pylint, pytest + frontend tests, and type-check + build, but they create venvs and run `pip install` / `npm install`.

### 9.4 Gaps relative to v3 requirements (all verified absent by source search for lock/lease/claim/heartbeat)

> **Superseded by v4:** these gaps were measured against the v3 idea of extending `mxissues`/`mxfix` into the agent coordinator. Under v4 they are **not** to be closed in those tools. The new feature coordinator provides roles, states, dependencies, claims, locks and path enforcement independently ([architecture §11](../architecture/README.md#11-v4-feature-development-architecture-design-baseline-for-phases-27)). The table remains an accurate description of the bug workflow.

| v3 requirement | Current state |
| --- | --- |
| Role/owner per task (`agent:*`) | None. No owner concept; labels only `mx-auto`, `source::*`, `severity::*`. |
| States `proposed/ready/blocked/review/failed` | Absent. Only `open/in-progress/done/needs-human`. |
| Dependency IDs, blocking/unblocking | Absent. |
| Atomic issue + role-slot claim, lease, heartbeat, expiry, stale-worker check | Absent. A "claim" is `set_status(iid, "in-progress")` after listing open issues. Duplicate prevention is only local (`.mxworktrees/<iid>` exists / branch exists). |
| One active writer per role; global full-project lock | Absent (`batch` deliberately runs 3 concurrent writers with no role model). |
| Write-path enforcement (launcher + pre-commit/PR changed-path check) | Absent. `review_diff()` covers check-config files and test-only diffs only. `ClaudeFixer` runs with `bypassPermissions`. |
| Persistent resumable state, budgets, backoff | Partial: per-invocation attempt limits and timeouts only. Attempt counts are not persisted across runs (`docs/test_environment/what_next.md`). |
| Tests for the automation | None. |

## 10. GitHub, CI and issue state **[V]**

- Repo `spyderbg/mx-admin-cloud`: **private**, issues and projects enabled. **Branch protection and rulesets are unavailable** on the current plan; `GET …/branches/{main,dev,agents}/protection` and `…/rulesets` return HTTP 403 "Upgrade to GitHub Pro or make this repository public". As a result, "no merge without CI/review" **cannot be enforced server-side today**.
- GitHub Actions: only `.github/workflows/mirror-gitlab.yml` (push to `main`/tags → mirror to GitLab). **3 of the last 5 runs failed** (`fatal: Could not read from remote repository`). No GitHub workflow runs tests, lint or builds.
- GitLab CI (`.gitlab-ci.yml`): a single `deploy` job on `main` (`deploy.sh build/start-core/start-cli/validate/install-cli`) on a shell runner. **No test stage.**
- Issues: 5 total. Open: **#9 "S3 content provider"**, **#10 "Database content provider"** (no labels; bodies are design notes). Closed: #3, #5, and #8 (an `mx-auto` tracker smoke test). The `mxissues` labels exist (`mx-auto`, `source::*`, `severity::*`). **No `agent:*` or `ai:*` labels exist.**
- PRs: #1, #2, #4, #6, #7, all merged; none open.
- Project board `spyderbg/projects/1` (the `mx-status` field) **could not be read**: the `gh` token lacks `read:project` **[V]**. Whether the field and options exist is **[U]** — next step: `gh auth refresh -s read:project`, then `issues.py --tracker github doctor`, both operator-approved.

## 11. Security and sensitive-state findings

Severity reflects impact if the condition holds in a deployed environment. No values are reproduced here.

| ID | Finding | Evidence | What remains unknown |
| --- | --- | --- | --- |
| SEC-1 | `Settings.AUTH_DISABLED` defaults to `True`; `verify_token` then returns `{"username": "dev-user", "role": "admin"}` for **every** request, including requests without a token | `core/config.py:59`, `security/auth.py:26` **[S]** | No tracked deployment artifact sets it to false (`docker/docker-compose.yml`, `backend.Dockerfile`, `scripts/deploy.sh`, `.gitlab-ci.yml`, testenv seed) **[S]**. The deploy host's own `.env` is **[U]**. The e2e setup (`test_environment/e2e/auth.setup.ts`) relies on this behavior. |
| SEC-2 | `DEBUG_MODE` defaults to `True`; lifespan then overrides `require_uds` and `require_admin` with no-ops, exposing UDS-only user management over TCP and disabling admin checks | `core/config.py:52`, `main.py:96-103` **[S]** | Same as SEC-1. |
| SEC-3 | `SECRET_KEY` default is a placeholder literal. Compose passes `SECRET_KEY=${SECRET_KEY}`, which becomes an empty string when the variable is unset. The local repo-root `.env` defines no `SECRET_KEY` (key names checked) | `core/config.py:55`, `docker/docker-compose.yml:27,75` **[S][V]** | Deploy host `.env` **[U]**. Docker not available to confirm substitution **[U]**. |
| SEC-4 | Hard-coded default admin password literal in `core/config.py`, also used as the `KIDDO_PASSWORD` default in `docker-compose.yml`. `UsersDb.init_db()` seeds `admin` with it when absent | **[S]** | Whether production DBs still use it **[U]**. Not testable without touching real DBs (forbidden). |
| SEC-5 | `backend/data/users.db` and `data/users.db` (password hashes) are **tracked in git**. `DB_PATH` is relative and resolved against the process **CWD** (`os.path.abspath`), so the DB location depends on where the server starts | `git ls-files`, `db/database.py:24` **[V][S]** | Which DB each deployment uses **[U]**. |
| SEC-6 | Repo `.claude/settings.json` and `backend/.claude/settings.json` set `defaultMode: bypassPermissions`. `ClaudeFixer` also uses `bypassPermissions`. Issue bodies (untrusted text) are passed verbatim into fixer prompts | **[S]** | — (design decision required before unattended agents). |
| SEC-7 | No server-side branch protection is possible on the current GitHub plan; no CI runs tests on GitHub or GitLab | §10 **[V]** | — |
| SEC-8 | `POST /internal/evaluators/evaluate` takes filesystem paths (`output_dir`, `raw_path`, `normalized_path`) from the request body (internal role required) | `services/evaluation/routes.py` **[S]**; also `backend/code_improvements.md` §4.3 | Exploitability not tested. |
| SEC-9 | GitLab tracker host is plain HTTP; a token crosses the network in clear text (documented in `testenv.toml`, warned by `issues.py doctor`) | **[S]** | — |
| SEC-10 | `GET /api/v1/status` returns all non-secret settings, including `AUTH_DISABLED`/`DEBUG_MODE` and paths, to any authenticated user. Secrets are masked (`core/tasks.py`). Backlog item §4.2 ("leaks the admin password") **appears already fixed** | **[S]** | — |

## 12. Baseline check results

| # | Check | Command (in isolated export) | Observed result |
| --- | --- | --- | --- |
| B1 | Backend unit/integration tests | `cd backend && python -m pytest -q -p no:cacheprovider --durations=10` | **919 passed, 1 warning** (Starlette/anyio deprecation), 25.5 s, exit 0 **[V]** |
| B2 | Backend compile | `python -m compileall -q src` | exit 0 **[V]** |
| B3 | Backend lint | `PYTHONPATH=src python -m pylint src/` | **10.00/10**, 1 message `R0915` at `services/content/providers/file_system/categories.py:336`, exit 8 **[V]** |
| B4 | CLI lint | `python -m pylint src/kiddo_cli` (in `cli/`) | **9.62/10**, 34 messages, exit 28 **[V]** |
| B5 | CLI help | `kiddo-cli [group] --help` for root + 8 groups | All render, exit 0 **[V]** |
| B6 | Frontend type-check | `npm run type-check` | exit 0, no errors **[V]** |
| B7 | Frontend build | `npm run build` | exit 0, no warnings **[V]** |
| B8 | Frontend tests | `npm run test:voices` / `test:content` / `test:auth` | **29/29, 5/5, 8/8** pass **[V]** |
| B9 | Per-service OpenAPI reproducibility | `python scripts/export_openapi.py` + diff | **7/7 identical** **[V]** |
| B10 | Full-app OpenAPI snapshot | `python scripts/export_openapi.py --all` + diff | **Drift**: committed 50 ops vs runtime 91 **[V]** |
| B11 | TS schemas reproducibility | `python scripts/generate_ts_schemas.py` + diff | **9/9 identical** **[V]** |
| B12 | TS clients reproducibility | `generate_ts_client.py` (content), `generate_ts_client.py configuration` + diff | content **identical**; configuration **drift (+6/−6)** **[V]** |
| B13 | Runtime route inventory | app import + route introspection | 91 operations / 70 paths **[V]** |
| B14 | Registry discovery + config load | `ServiceRegistry._discover()` + `load_all_from_disk()` | 7 services, 26 provider instances, no load errors **[V]** |
| B15 | OpenAPI YAML structural parse | `yaml.safe_load` on the 9 YAML files | all parse; `openapi: 3.1.0` **[V]** (full schema validation **not run** — no validator installed) |

**Not run (with reason):**

- Playwright e2e (`testenv.py e2e`): Docker is absent, e2e `node_modules`/browsers are not installed, and it requires starting a stack.
- `validate_*.py` / `validate.sh`: they mutate shared venv/node_modules.
- Live GitHub/GitLab tracker commands (`issues.py doctor|sync`): need tokens missing from `.env` and would contact external services.
- `testenv.py run` (any strategy): writes code and pushes branches.
- CLI against a live backend: would require starting servers that touch real data.
- `generate_mockdata.py`: writes from real content.
- No CLI or automation test suites exist.

## 13. Correctness findings (pre-existing; not introduced by this assignment)

| ID | Finding | Owner role | Evidence |
| --- | --- | --- | --- |
| F-1 | `POST /internal/generate` references the undefined `_run_job` (its definition is commented out at `api/internal/jobs_routes.py:45`) → `NameError` after the upload is saved; also `backend/code_improvements.md` §1.1 | backend | **[S]** |
| F-2 | CLI `generate run` calls the removed `/internal/generate-sync` | cli (depends on backend F-1) | **[V]** |
| F-3 | CLI `translate word/file` call `/internal/translate*` instead of `/internal/translators/translate*` | cli | **[V]** |
| F-4 | `evaluation` is unregistered, has no providers, and its provider base has a copy-pasted translator contract | backend | **[V][S]** |
| F-5 | Test writes lock files into the real `content/` tree | backend | **[V]** |
| F-6 | `backend/docs/openapi.yaml` stale; `configurationClient.ts` drift; no drift checks anywhere | automation / frontend | **[V]** |
| F-7 | `export_openapi.py` uses prefix ownership; 19 operations are in no per-service spec | automation | **[V]** |
| F-8 | GitHub→GitLab mirror workflow failing (3/5 recent runs) | operator / automation | **[V]** |
| F-9 | Stale agent instructions: `frontend/CLAUDE.md` says there are no tests; `cli/CLAUDE.md` and `backend/CLAUDE.md` provider/command lists are incomplete | frontend / cli / backend (approval needed — files outside `docs_agents/`) | **[V][S]** |
| F-10 | CLI help text inaccuracies (`list` mentions evaluators; `content` lists 3 of 7 commands) | cli | **[V]** |

## 14. Ambiguities and unverified assumptions

| ID | Question | Why it matters | Next verification step |
| --- | --- | --- | --- |
| U-1 | Does the deployed backend run with `AUTH_DISABLED=false`, `DEBUG_MODE=false` and a real `SECRET_KEY`? | Decides whether SEC-1..3 are live vulnerabilities | Operator inspects the deploy host `.env` (do not paste values), or queries `GET /api/v1/status` on the deployed instance as an authenticated user and reports only the two booleans |
| U-2 | Is the `content` service in scope for per-service documentation (the assignment lists 7, the code has 8 packages)? | Phase 1 deliverable set | Operator decision (recommended: yes; document `evaluation` as an unregistered, partially implemented service) |
| U-3 | Which contract source is authoritative: runtime FastAPI OpenAPI, or a checked-in spec? | `api-contracts.md`, drift checks | Operator decision in Phase 1 (recommendation in [architecture §6](../architecture/README.md#6-contract-and-code-generation-flow)) |
| U-4 | Do the GitHub Project `mx-status` field and options exist? | Phase 4 status storage | `gh auth refresh -s read:project`, then `issues.py --tracker github doctor` |
| U-5 | Will agents ever run on more than one machine at once? | Lock-store choice (local file lock vs shared store) for role leases | Operator answer |
| U-6 | Which branch should agent PRs target: `dev` (as in `testenv.toml`), `agents`, or `main`? | PR policy and integration | Operator decision |
| U-7 | Is unattended `claude -p` / `codex exec` use permitted under the operator's plan and terms, and within which budgets? | Phase 7 continuous operation | Operator confirms; check official scheduling/integration options |
| U-8 | Is `frontend/docs/openapi.yaml` still referenced by anything? | Preserve vs. mark historical | Search usages in Phase 1 (preserve regardless) |
