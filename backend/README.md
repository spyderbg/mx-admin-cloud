# Kiddo Backend

FastAPI backend for Kiddo content management. All public API routes are under `/api/v1`. Protected endpoints require a
Bearer token (obtained via `/login` or `/cli-login`). Internal service routes are under `/internal/*`.

## Create .venv

```bash
python3.10 -m venv .venv
```

```batch
py -3.10 -m venv .venv
```

## Activate .venv

```bash
source .venv/bin/activate
```

```batch
.venv\Scripts\activate
```

## Install Python packages

```bash
pip install -r requirements.txt
```

Use a virtual environment (see above); avoid `--break-system-packages` on system Python unless you intentionally install
globally.

## Validate backend code

From the repository root, run:

```bash
python scripts/validate_build.py
```

On Unix-like systems you can still use:

```bash
./scripts/validate.sh
```

This script:

- creates `backend/.venv` when missing,
- installs `backend/requirements.txt` (including `pylint`),
- compiles all backend Python modules under `backend/src`,
- runs `pylint` on `backend/src`.

## Run the backend

From the backend directory:

```bash
./uvicorn_start.sh
```

This starts the HTTP server and the CLI Unix socket, records both PIDs in `uvicorn.pid`, and uses `python -m uvicorn` (
prefers `.venv/bin/python`, or set `UVICORN_PYTHON`). `nohup` keeps the servers running after the script exits.

To stop:

```bash
./kill_uvicorn.sh
```

## Logs

```
tail --lines 40 -f /tmp/backend_http.log
```

```
tail -f /tmp/backend_uds.log
```

## Tests

From the backend directory:

```bash
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -v
```

Tests for a feature module usually live under a sibling `__tests__` tree. For `src/api/internal/`, omit
`__tests__/__init__.py` so it stays out of the installable package. Co-located services
(e.g. `logging`, `text_translation`, `text_to_speech`) use `src/services/<name>/__tests__/` with a package
`__init__.py` for pytest discovery.

Example for the co-located text_translation service (see `.cursor/rules/service-tests-layout.mdc`):

```
src/services/text_translation/
├── routes.py
├── schemas.py
├── service.py
└── __tests__/
    ├── __init__.py
    ├── test_text_translation_service.py              # Unit (schemas / pure logic)
    └── test_text_translation_service_integration.py  # HTTP + ASGI
```

Run only those tests:

```bash
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest src/services/text_translation/__tests__ -v
```

Text-to-speech service tests:

```bash
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest src/services/text_to_speech/__tests__ -v
```

Configuration service tests:

```bash
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest src/services/configuration/__tests__ -v
```

Logging service tests (when added):

```bash
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest src/services/logging/__tests__ -v
```

Remaining internal routes still under `src/api/internal/` keep their `__tests__/` tree without an
`__init__.py`:

```bash
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest src/api/internal/__tests__ -v
```

## Auth

- **HTTP login:** `POST /api/v1/login` with `application/x-www-form-urlencoded` body `username` and `password` (default
  `admin` / value of `DEFAULT_ADMIN_PASSWORD`). Returns `access_token`; use as `Authorization: Bearer <token>`.
- **CLI login:** `POST /api/v1/cli-login` is allowed only when the request is made over the backend’s Unix socket (e.g.
  by the CLI). Returns a short-lived token.
- **Internal auth:** Internal endpoints require a JWT with role `"internal"` or `"admin"`. An internal token is
  generated at startup (stored in `app.state.internal_token`, 24-hour lifetime) and never shared as a static secret.
  Admin JWTs also satisfy the check, so the CLI can call internal endpoints with its normal login token.

Env (see `src/core/config.py`):

- `SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `CLI_TOKEN_EXPIRE_SECONDS` — JWT settings
- `AUTH_DISABLED` — if true, auth checks are skipped
- `DEBUG_MODE` — if true, `/api/v1/users` skips Unix socket + admin dependency checks
- `CONTENT_DIR` — content data path
- `ARCHIVE_DIR` — directory for archived (removed) categories. If empty, remove deletes categories permanently instead
  of archiving. Removing a category also deletes its rows from the root `categories_ids.csv` / `categories_{lang}.csv` /
  `descriptions_{lang}.csv` registry. Archiving first writes a `_category.json` manifest into the archived folder holding
  the id, names and descriptions, so restore can rebuild those rows; restore moves the snapshot back and removes the
  manifest.
- `OPENAI_API_KEY` — required for TTS, translation, and category icon generation (create/rename category, create
  object).
- `ELEVENLABS_API_KEY` — ElevenLabs TTS provider (optional)
- `SPEAKTOR_API_KEY` — Speaktor TTS provider (optional)
- `CONFIG_DIR` — path to TTS config directory (default `config`)
- `OUTPUT_DIR` — path to TTS output directory (default `../output`)
- `API_BASE_URL` — internal base URL for service-to-service HTTP calls (default `http://127.0.0.1:8000`)
- `TTS_MAX_CONCURRENCY` — max parallel provider requests (default 5)
- `LOG_PROFILE` — logging profile: `dev` or `prod` (default `dev`)

## Endpoints

Base URL for all routes below: `/api/v1`. Protected routes require `Authorization: Bearer <token>`.

### Auth

| Method | Path                                                  | Auth | Description                                               |
|--------|-------------------------------------------------------|------|-----------------------------------------------------------|
| `POST` | [/login → login()](src/api/v1/routes_auth.py)         | No   | Login with username/password; returns JWT.                |
| `POST` | [/cli-login → cli_login()](src/api/v1/routes_auth.py) | No   | CLI-only login over Unix socket; returns short-lived JWT. |

### Tasks

| Method | Path                                           | Auth | Description                                                                              |
|--------|------------------------------------------------|------|------------------------------------------------------------------------------------------|
| `GET`  | [/ping](src/api/v1/routes_common.py#L11)       | No   | Health check; returns PONG with backend platform info.                                   |
| `GET`  | [/status](src/api/v1/routes_common.py#L16)     | Yes  | Service status.                                                                          |
| `POST` | [/operation](src/api/v1/routes_common.py#L21)  | Yes  | Perform a service operation. Query param: `name`.                                        |
| `POST` | [/list-files](src/api/v1/routes_common.py#L30) | Yes  | List files in the configured directory. Body: `{"depth": int}` (depth < 1 treated as 1). |

### Content

| Method   | Path                                                          | Auth | Description                                                                      |
|----------|---------------------------------------------------------------|------|----------------------------------------------------------------------------------|
| `GET`    | [/content/categories](src/services/content/routes.py)         | Yes  | List categories. Query: `include_archived`, `archived_only`.                     |
| `POST`   | [/content/categories](src/services/content/routes.py)         | Yes  | Create a category. Body: `{"name": "..."}`. Returns 201. Name is normalized to lowercase snake_case. |
| `POST`   | [/content/categories/restore](src/services/content/routes.py) | Yes  | Restore from archive. Body: `{"name": "..."}` — a folder name (newest archive) or an archive folder. |
| `GET`    | [/content/categories/{name}](src/services/content/routes.py)  | Yes  | Category details by name.                                                        |
| `PUT`    | [/content/categories/{name}](src/services/content/routes.py)  | Yes  | Update category name or description. **Stub (501).**                             |
| `PATCH`  | [/content/categories/{name}](src/services/content/routes.py)  | Yes  | Rename category. Body: `{"name": "..."}`.                                        |
| `DELETE` | [/content/categories/{name}](src/services/content/routes.py)  | Yes  | Remove category by id or folder name. Archives if `ARCHIVE_DIR` set; else deletes. |
| `POST`   | [/content/items](src/services/content/routes.py)              | Yes  | Create a content item. Multipart: `image`, `category`, `item`. Returns 201.      |

#### Category Voices (stub — 501)

| Method   | Path                                                                          | Auth | Description                        |
|----------|-------------------------------------------------------------------------------|------|------------------------------------|
| `GET`    | [/content/categories/{name}/voices](src/services/content/routes.py)           | Yes  | List voices for a category.        |
| `PUT`    | [/.../voices/{voiceId}](src/services/content/routes.py)                       | Yes  | Rename a category voice.           |
| `DELETE` | [/.../voices/{voiceId}](src/services/content/routes.py)                       | Yes  | Delete or reset a category voice.  |
| `PUT`    | [/.../voices/{voiceId}/audio](src/services/content/routes.py)                 | Yes  | Upload audio for a category voice. |

#### Images (stub — 501)

| Method   | Path                                                                          | Auth | Description                     |
|----------|-------------------------------------------------------------------------------|----- |---------------------------------|
| `GET`    | [/content/categories/{name}/images](src/services/content/routes.py)           | Yes  | List images for a category.     |
| `POST`   | [/content/categories/{name}/images](src/services/content/routes.py)           | Yes  | Add an image to a category.     |
| `GET`    | [/.../images/{imageId}](src/services/content/routes.py)                       | Yes  | Get a single image by ID.       |
| `PUT`    | [/.../images/{imageId}](src/services/content/routes.py)                       | Yes  | Update image name.              |
| `DELETE` | [/.../images/{imageId}](src/services/content/routes.py)                       | Yes  | Delete an image.                |

#### Image Files (stub — 501)

| Method   | Path                                                                          | Auth | Description              |
|----------|-------------------------------------------------------------------------------|------|--------------------------| 
| `POST`   | [/.../images/{imageId}/files](src/services/content/routes.py)                 | Yes  | Upload a file variant.   |
| `PUT`    | [/.../images/{imageId}/files/{idx}](src/services/content/routes.py)           | Yes  | Replace a file variant.  |
| `DELETE` | [/.../images/{imageId}/files/{idx}](src/services/content/routes.py)           | Yes  | Delete a file variant.   |

#### Image Voices (stub — 501)

| Method   | Path                                                                          | Auth | Description                      |
|----------|-------------------------------------------------------------------------------|------|----------------------------------|
| `GET`    | [/.../images/{imageId}/voices](src/services/content/routes.py)                | Yes  | List voices for an image.        |
| `PUT`    | [/.../images/{imageId}/voices/{voiceId}](src/services/content/routes.py)      | Yes  | Rename an image voice.           |
| `DELETE` | [/.../images/{imageId}/voices/{voiceId}](src/services/content/routes.py)      | Yes  | Delete or reset an image voice.  |
| `PUT`    | [/.../images/{imageId}/voices/{voiceId}/audio](src/services/content/routes.py)| Yes  | Upload audio for an image voice. |

### Users

User routes are under `/api/v1/users`* and require both admin role and Unix socket access.  
On Windows, Unix socket checks are bypassed automatically.  
When `DEBUG_MODE=true`, both Unix socket and admin-role dependencies are overridden at startup for local debugging.

| Method   | Path                                                          | Auth              | Description                                                       |
|----------|---------------------------------------------------------------|-------------------|-------------------------------------------------------------------|
| `POST`   | [/users](src/api/v1/routes_users.py#L45)                      | Yes (admin + UDS) | Create a user with role `admin` or `viewer`.                      |
| `GET`    | [/users](src/api/v1/routes_users.py#L66)                      | Yes (admin + UDS) | List all users. Query: `username` (fuzzy), `role` (admin/viewer). |
| `DELETE` | [/users/{username}](src/api/v1/routes_users.py#L77)           | Yes (admin + UDS) | Delete a user (cannot remove the last admin).                     |
| `PATCH`  | [/users/{username}/password](src/api/v1/routes_users.py#L107) | Yes (admin + UDS) | Change a user’s password.                                         |
| `PATCH`  | [/users/{username}/role](src/api/v1/routes_users.py#L131)     | Yes (admin + UDS) | Change a user’s role (cannot demote the last admin).              |

### Internal

Internal endpoints are under `/internal/`*. They require `Authorization: Bearer <token>` where the token’s role is
`"internal"` or `"admin"`. An internal token is automatically generated at startup; admin JWTs (from `/login`) also
work.

#### Jobs

| Method | Path                                                                  | Description                                                              |
|--------|-----------------------------------------------------------------------|--------------------------------------------------------------------------|
| `POST` | [/internal/generate](src/api/internal/jobs_routes.py)                 | Upload input file, trigger TTS pipeline in background; returns `job_id`. |
| `GET`  | [/internal/generate/{job_id}/status](src/api/internal/jobs_routes.py) | Poll job status (`pending`, `running`, `completed`, `failed`).           |

**Generate request (multipart form):**

- `file` — input file (.txt, .csv, or .xlsx)
- `language` — default language code (default: `en`)
- `output_format` — audio format: mp3, wav, pcm, opus (default: `mp3`)
- `max_concurrency` — parallel provider requests (default: `5`)

#### Generators

| Method | Path                                                                                      | Description                                                                                                                                                                                                        |
|--------|-------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `GET`  | [/internal/generators/](src/services/text_to_speech/routes.py)                     | List all configured TTS providers with activation state and settings.                                                                                                                                              |
| `POST` | [/internal/generators/{provider}/generate](src/services/text_to_speech/routes.py) | Generate raw audio for a single TTS request via the named provider. JSON response is the `TtsGenerateResult` model in `src/services/text_to_speech/schemas.py` (handler return type matches `response_model`). |
| `POST` | [/internal/generators/generate-word](src/services/text_to_speech/routes.py)      | Generate raw audio for a single word using the highest-priority active provider; returns `audio/mpeg` bytes.                                                                                                       |

#### Evaluators

| Method | Path                                                                       | Description                                                          |
|--------|----------------------------------------------------------------------------|----------------------------------------------------------------------|
| `GET`  | [/internal/evaluators](src/services/evaluation/routes.py)          | List all configured STT evaluators with activation state.            |
| `POST` | [/internal/evaluators/evaluate](src/services/evaluation/routes.py) | Run STT transcription, score, persist, and return evaluation result. |

#### Optimizers

| Method | Path                                                                                   | Description                                                              |
|--------|----------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| `GET`  | [/internal/optimizers](src/services/voice_optimization/routes.py)                      | List all configured audio optimizers with activation state and priority. |
| `POST` | [/internal/optimizers/{optimizer}/optimize](src/services/voice_optimization/routes.py) | Run the named optimizer on a given audio file.                           |

#### Translators

| Method | Path                                                                     | Description                                                                                              |
|--------|--------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------|
| `GET`  | [/internal/translators/](src/services/text_translation/routes.py) | List all configured translation services with activation state.                                          |
| `POST` | [/internal/translators/translate](src/services/text_translation/routes.py)   | Translate text via all active translators; returns `success` and `lang_text` (`lang_code` + `text`).     |
| `POST` | [/internal/translators/translate-file](src/services/text_translation/routes.py)  | Translate a file; loads segments via `load_words` (skips source = target lang); returns per-row results. |

#### Logging

| Method | Path                                                                       | Description                                            |
|--------|----------------------------------------------------------------------------|--------------------------------------------------------|
| `GET`  | [/internal/loggers](src/services/logging/routes.py)            | List active logging providers (or all with `?all=true`). |
| `POST` | [/internal/loggers/emit](src/services/logging/routes.py)       | Emit a structured log message to the default logger route. |
| `POST` | [/internal/loggers/{logger_name}/log](src/services/logging/routes.py) | Emit a structured log message to a specific logger route. |
| `POST` | [/internal/loggers/logging/log/api-request](src/services/logging/routes.py) | Emit a structured API request log to the `requests` logger. |
| `POST` | [/internal/loggers/logging/log/provider](src/services/logging/routes.py) | Log a provider request (TTS, STT, translator) via the request logger. |
| `POST` | [/internal/loggers/logging/log/service](src/services/logging/routes.py) | Emit a structured service log to the `service` logger. |
| `POST` | [/internal/loggers/logging/log/payment](src/services/logging/routes.py) | Emit a structured payment log to the `payment` logger. |
| `POST` | [/internal/loggers/reset](src/services/logging/routes.py)      | Reset all active logging providers; file-based ones truncate log files. |

All logging endpoints accept a JSON body and delegate to the `LoggingService` (backed by the
`struct_log` provider). The `logger_name` field in `/emit` selects which logger channel receives
the message, mirroring structlog semantics with an explicit logger name.

#### Runtime Service Configs

| Method   | Path                                                                                                             | Description                                     |
|----------|------------------------------------------------------------------------------------------------------------------|-------------------------------------------------|
| `POST`   | [/internal/configurations/apply](src/services/configuration/routes.py)                                     | Validate and apply one JSON payload in memory.  |
| `POST`   | [/internal/configurations/upload](src/services/configuration/routes.py)                                    | Validate uploaded JSON in memory.               |
| `GET`    | [/internal/configurations/{service_name}](src/services/configuration/routes.py)                            | List config files for one service.              |
| `POST`   | [/internal/configurations/{service_name}](src/services/configuration/routes.py)                            | Create one config file and reload the registry. |
| `PATCH`  | [/internal/configurations/{service_name}/{config_name}](src/services/configuration/routes.py)              | Merge patch and prune defaults.                 |
| `DELETE` | [/internal/configurations/{service_name}/{config_name}](src/services/configuration/routes.py)              | Delete one config file.                         |
| `POST`   | [/internal/configurations/{service_name}/enable-configurations](src/services/configuration/routes.py)      | Enable selected configs.                        |
| `POST`   | [/internal/configurations/{service_name}/disable-configurations](src/services/configuration/routes.py)     | Disable selected configs.                       |
| `POST`   | [/internal/configurations/{service_name}/enable-all-configurations](src/services/configuration/routes.py)  | Enable all configs.                             |
| `POST`   | [/internal/configurations/{service_name}/disable-all-configurations](src/services/configuration/routes.py) | Disable all configs.                            |                        |

`/apply` and `/upload` are runtime-only and return `config_name`, `accepted`, and `error`.
Persisted CRUD and batch toggle routes operate on `config/<service_subdir>/*.json`,
validate payloads against service acceptance criteria, and call
`ServiceRegistryBuilder.load_all_from_disk()` after successful mutations.

## Architecture

### Service Registry

When `src/services/service_registry.py` is imported, it scans immediate subpackages of `src.services` (via `pkgutil`)and
records packages that export all three fields:

- `SERVICE_CLASS` (orchestrator class)
- `SERVICE_NAME` (registry identity string)
- `SERVICE_ACCEPT_CRITERIA` (validation + materialization contract)

`ServiceRegistry` itself is generic: it does not contain service-specific getters. Consumers resolve services by name
with `registry.get_service(service_name)`.

At startup, `main.py` calls `ServiceRegistry.build_runtime(settings.BASE_DIR)`, then
`ServiceRegistryBuilder(...).load_all_from_disk()`. The builder reads config files one by one, validates each file with
`ServiceAcceptCriteria`, materializes provider instances, and atomically replaces provider sets in services.
The configuration HTTP routes resolve the shared `ConfigurationService` directly from this registry
instead of constructing a new service per request.

This avoids per-request JSON parsing for provider endpoints while keeping config ingestion outside service classes.
For the configuration domain specifically, each registry reload also restores one runtime-wired
`file_system` provider so CRUD operations can validate payloads and trigger in-memory reloads consistently.

**Hot-reload:** If `watchfiles` is installed, a background task triggers `builder.load_all_from_disk()` when files under
`config/` change.

### Job Store

Async job tracking (`src/core/job_store.py`) is stored in `app.state.job_store` and injected via
`Depends(get_job_store)`.

- Jobs expire after 1 hour (TTL eviction runs every 5 minutes via background task)
- Thread-safe via `asyncio.Lock`
- Drop-in replaceable with SQLite or Redis for multi-worker deployments

### HTTP Boundary

`core/` has zero imports from `services/`. The pipeline calls internal HTTP endpoints instead of
importing service implementations directly. The content service lives in `services/content/` and
also communicates with TTS and translation via internal HTTP calls. This keeps the core
infrastructure layer decoupled from provider-specific code.

### Output path helpers

Output naming/path helpers now live on `Settings` in `src/core/config.py` (not a separate `output_paths.py` module).

- Raw/optimized/normalized outputs: `raw_output_dir`, `optimized_output_dir`, `normalized_output_dir`
- Results/log folders: `reports_output_dir`, `logs_output_dir`, `scores_output_dir`
- Request log file: `requests_log_file`

### Source layout

Paths are relative to `backend/` (set `PYTHONPATH` to `src` when running uvicorn).

```text
src/
├── main.py                       # FastAPI app factory, lifespan, router registration
├── api/
│   ├── dependencies.py           # get_registry, builder/service deps, get_job_store, auth deps
│   ├── internal/                 # Internal routes (internal JWT or admin)
│   │   ├── jobs_routes.py        # /internal/generate, /generate/{id}/status
│   │   └── schemas/              # Internal OpenAPI contract models (Pydantic)
│   │       └── jobs.py
│   └── v1/                       # Public /api/v1
│       ├── routes_auth.py
│       ├── routes_common.py      # ping, status, operation, list-files
│       ├── routes_users.py
│       └── schemas/              # Public OpenAPI contract models (Pydantic)
│           ├── common.py
│           └── users.py
├── core/                         # No src.services imports (HTTP boundary)
│   ├── config.py                 # Settings + output path helpers
│   ├── models.py                 # e.g. TranslationSourceWord, GeneratedAudio, RunSummary
│   ├── languages.py
│   ├── localization.py           # LocalizedString: multilingual str with property access + Pydantic support
│   ├── input_loader.py           # load_words() for translate-file (TXT/CSV/XLSX)
│   ├── result_writer.py
│   ├── content_naming.py         # Content folder / image id naming
│   ├── job_store.py
│   ├── log_client.py             # Async HTTP client → `/internal/loggers/*` (see services/logging/routes.py)
│   ├── pipeline.py               # TTS pipeline (internal HTTP)
│   ├── tasks.py                  # ping, status, operation, list-files
│   ├── utils.py                  # boolish, platform / debug helpers
│   └── (logging/ removed — now in services/logging)
├── db/
│   └── database.py               # SQLite user store
├── security/
│   ├── auth.py                   # JWT verification
│   └── tokens.py                 # access + internal tokens
└── services/                     # Registry + provider implementations
    ├── acceptance.py             # ServiceAcceptCriteria
    ├── service_registry.py       # Subpackage discovery, get_service
    ├── registry_builder.py       # load_all_from_disk, hot-reload
    ├── models.py                 # Service / ServiceProvider bases
    ├── content/                  # Content management service (co-located routes + schemas)
    │   ├── __init__.py           # SERVICE_CLASS, SERVICE_NAME, SERVICE_ACCEPT_CRITERIA
    │   ├── models.py             # ContentProvider base, result dataclasses (name fields use LocalizedString)
    │   ├── schemas.py            # Pydantic bodies for /api/v1/content/* (id, LocalizedString name, thumbnail_uri)
    │   ├── routes.py             # /api/v1/content/* (categories, voices, images, files)
    │   ├── service.py            # ContentService + ContentProviderFactory
    │   └── providers/            # file_system provider
    ├── configuration/            # Co-located service (routes + schemas live next to service.py)
    │   ├── __init__.py           # SERVICE_CLASS, SERVICE_NAME, SERVICE_ACCEPT_CRITERIA
    │   ├── models.py             # ConfigurationProvider base + error/result dataclasses
    │   ├── schemas.py            # Pydantic bodies for /internal/configurations/*
    │   ├── routes.py             # runtime apply/upload + persisted CRUD/batch config routes
    │   ├── service.py
    │   ├── merge.py
    │   ├── __tests__/             # `test_configuration_service*.py` only (see service-tests-layout rule)
    │   │   ├── __init__.py
    │   │   ├── test_configuration_service.py
    │   │   └── test_configuration_service_integration.py
    │   └── providers/            # file_system provider
    ├── text_to_speech/           # Co-located service (routes + schemas live next to service.py)
    │   ├── __init__.py           # SERVICE_CLASS, SERVICE_NAME, SERVICE_ACCEPT_CRITERIA
    │   ├── models.py
    │   ├── schemas.py            # Pydantic bodies for /internal/generators/*
    │   ├── routes.py             # /internal/generators/, /{provider}/generate, /generate-word
    │   ├── service.py
    │   ├── __tests__/             # `test_text_to_speech_service*.py` only (see service-tests-layout rule)
    │   │   ├── __init__.py
    │   │   ├── test_text_to_speech_service.py
    │   │   └── test_text_to_speech_service_integration.py
    │   └── providers/            # openai_tts, elevenlabs_tts, speaktor_tts
    ├── text_translation/         # Co-located service (routes + schemas live next to service.py)
    │   ├── __init__.py            # SERVICE_CLASS, SERVICE_NAME, SERVICE_ACCEPT_CRITERIA
    │   ├── models.py
    │   ├── schemas.py             # Pydantic bodies for /internal/translators/* routes
    │   ├── routes.py              # /internal/translators/, /translate, /translate-file (under that prefix)
    │   ├── service.py
    │   ├── __tests__/             # `test_text_translation_service*.py` only (see service-tests-layout rule)
    │   │   ├── __init__.py
    │   │   ├── test_text_translation_service.py
    │   │   └── test_text_translation_service_integration.py
    │   └── providers/            # openai_translate, static_translate
    ├── voice_optimization/       # Co-located service (routes + schemas live next to service.py)
    │   ├── __init__.py            # SERVICE_CLASS, SERVICE_NAME, SERVICE_ACCEPT_CRITERIA
    │   ├── models.py              # AudioOptimizerBase
    │   ├── schemas.py             # Pydantic bodies for /internal/optimizers/*
    │   ├── routes.py              # /internal/optimizers, /optimizers/{optimizer}/optimize
    │   ├── service.py             # VoiceOptimizationService + AudioOptimizerFactory
    │   ├── normalizer.py
    │   ├── __tests__/             # Pytest tree (co-located with service)
    │   │   └── __init__.py
    │   └── providers/             # ffmpeg_optimizer, pydub_optimizer
    ├── evaluation/               # Co-located service (routes + schemas live next to service.py)
    │   ├── __init__.py            # Re-exports engine + score stores
    │   ├── models.py              # EvaluationServiceProvider base
    │   ├── schemas.py             # Pydantic bodies for /internal/evaluators/*
    │   ├── routes.py              # /internal/evaluators, /evaluators/evaluate
    │   ├── service.py             # EvaluationService + EvaluationProviderFactory
    │   ├── engine.py
    │   ├── score_store.py
    │   ├── score_store_json.py
    │   ├── score_store_sqlite.py
    │   └── __tests__/             # Pytest tree (co-located with service)
    │       └── __init__.py
    └── logging/                  # Logging service (structlog + stdlib + request logger)
        ├── __init__.py           # SERVICE_CLASS, SERVICE_NAME, SERVICE_ACCEPT_CRITERIA
        ├── models.py             # LoggingServiceProvider base class
        ├── schemas.py            # Pydantic bodies for /internal/loggers/* (wire contract)
        ├── routes.py             # /internal/loggers/emit, /internal/loggers/reset, …
        ├── service.py            # LoggingService + LoggingProviderFactory
        ├── __tests__/            # Pytest tree (co-located with service)
        │   ├── __init__.py
        │   └── unit/
        │       └── test_logging_service.py
        └── providers/
            └── struct_log.py     # Structlog-based provider: dictConfig, structlog, request log
```

## Adding a new service type

**1. Add a subpackage** under `src/services/` (e.g. `voice_cloning/`) with a
`Service` subclass in `service.py` whose constructor matches existing orchestrators:
`(service_name, package_name, service_dir, base_dir)`.

**2. Implement** provider attachment hooks on that subclass:

- `replace_providers(self, providers: list[Any])`
- `providers_snapshot(self) -> list[Any]`

**3. Export** all registry metadata from package `__init__.py`:

- `SERVICE_CLASS = YourService`
- `SERVICE_NAME = "<service_identity>"`
- `SERVICE_ACCEPT_CRITERIA = ServiceAcceptCriteria(...)`

**4. Provide** service-specific validation/materialization in `SERVICE_ACCEPT_CRITERIA`:

- validate accepted `service_provider` envelopes for that service
- build concrete provider instances via the service factory
- point `config_subdir` to the service configuration directory

**5. Add JSON configs** under the service criteria `config_subdir`, using the `service_provider` envelope.

No registry API change is required to add a new service as long as the package exports these descriptors.

---

## TTS config

TTS providers and translators are configured via JSON files in `config/`:

```text
config/
├── content/
│   └── file_system.json          # file_system content provider
├── logging/
│   ├── default.json              # struct_log provider (console + file handlers, structlog)
│   └── requests.json             # struct_log provider (request file handler, field filtering)
├── voice_optimizers/
│   ├── ffmpeg_high.json
│   ├── ffmpeg_low.json
│   └── pydub_optimized.json
├── text_to_speech/
│   ├── openai.json
│   ├── elevenlabs.json
│   ├── speaktor.json
│   └── static.json
└── text_translation/
    ├── openai_en_es.json
    ├── openai_en_fr.json
    ├── openai_en_bg.json
    └── static.json
```

Set `service_provider.enabled` to `true` or `false` in each config file to enable/disable entries. For texttospeech
providers, `service_provider.config.generate.enabled` can optionally override generation activation per provider.

### Translators

Each translator is configured in its own JSON file under `config/text_translation/`.  
Each file uses a top-level `service_provider` object containing both metadata and translator settings.

`service_provider` fields:

| Field      | Type   | Description                                                                       |
|------------|--------|-----------------------------------------------------------------------------------|
| `service`  | string | Service type (`text_translation`). Must match the service package `SERVICE_NAME`. |
| `provider` | string | Translator implementation name (`openai`, `static_translate`).                    |
| `provider_category` | string | Optional provider grouping label (default: empty string).                  |
| `enabled`  | bool   | Set `true` to enable the translator.                                              |
| `priority` | int    | Execution order (lower runs first).                                               |
| `version`  | string | Config schema version (typically `1.0`).                                          |
| `config`   | object | Translator-specific settings.                                                     |

Common `service_provider.config` fields:

| Field             | Type   | Description                                                |
|-------------------|--------|------------------------------------------------------------|
| `input_language`  | string | Default source language when request/CLI value is missing. |
| `output_language` | string | Default output language when request/CLI value is missing. |
| `content_dir`     | string | Optional path used by `static_translate` provider.         |

Example (`config/text_translation/openai_en_fr.json`):

```json
{
  "service_provider": {
    "service": "text_translation",
    "provider": "openai",
    "enabled": true,
    "priority": 2,
    "version": "1.0",
    "config": {
      "input_language": "en",
      "output_language": "fr",
      "model": "gpt-4o",
      "prompt": "You are a professional translator...",
      "temperature": 0
    }
  }
}
```

`service_provider.provider` selects the implementation class, and every active translator is executed in sequence.  
If CLI/API translation language parameters are not provided, each translator uses  
`service_provider.config.input_language` and `service_provider.config.output_language` as defaults.

For `service_provider.provider: "static_translate"`, `service_provider.config.content_dir` is optional.  
When it is missing or points to a  
non-existing directory, the static translator automatically falls back to `CONTENT_DIR`  
from `src/core/config.py`.

### Audio optimizers

Each optimizer is configured in its own JSON file under `config/voice_optimizers/`.  
Each file uses a top-level `service_provider` object containing both metadata and optimizer settings.

`service_provider` fields:

| Field      | Type   | Description                                                                         |
|------------|--------|-------------------------------------------------------------------------------------|
| `service`  | string | Service type (`voice_optimization`). Must match the service package `SERVICE_NAME`. |
| `provider` | string | Optimizer implementation name (`ffmpeg`, `pydub`).                                  |
| `provider_category` | string | Optional provider grouping label (default: empty string).                    |
| `enabled`  | bool   | Set `true` to enable the optimizer.                                                 |
| `priority` | int    | Execution order (lower runs first).                                                 |
| `version`  | string | Config schema version (typically `1.0`).                                            |
| `config`   | object | Optimizer-specific settings.                                                        |

Common `service_provider.config` fields:

| Field         | Type | Description                                          |
|---------------|------|------------------------------------------------------|
| `sample_rate` | int  | Output sample rate (default `22050`).                |
| `mono`        | bool | Convert output to mono when `true` (default `true`). |
| `vbr_quality` | int  | MP3 VBR quality `0-9` (default `9`).                 |

Example (`config/voice_optimizers/ffmpeg_high.json`):

```json
{
  "service_provider": {
    "service": "voice_optimization",
    "provider": "ffmpeg",
    "enabled": true,
    "priority": 1,
    "version": "1.0",
    "config": {
      "sample_rate": 22050,
      "mono": true,
      "vbr_quality": 9
    }
  }
}
```

`service_provider.provider` selects the implementation class. Multiple files can use the same  
implementation with different providers and different config values.

Optimized files are written alongside generator output, preserving the same hierarchy:

```text
output/
├── tts_audio_en/openai/hello_en.mp3              # raw generator output
└── optimized_ffmpeg_high/
    └── tts_audio_en/openai/hello_en.mp3          # optimized copy
```

Requirements: `ffmpeg` must be on PATH for the ffmpeg optimizer type. The pydub type additionally  
requires the `pydub` Python package (included in `requirements.txt`).

### Logging

Logging is a registered service (`SERVICE_NAME = "logging"`) configured via JSON files in
`config/logging/`. Each file uses the standard `service_provider` envelope and creates an
independent provider instance. The only implementation is `struct_log`
(`src/services/logging/providers/struct_log.py`). Internal HTTP handlers and request/response models live in
`src/services/logging/routes.py` and `src/services/logging/schemas.py` (mounted at `/internal/loggers/*`).

**Priority-ordered emit.** `LoggingService.emit(msg)` filters active providers to those with
`provider_category == "normal"` and tries them in ascending priority order (1 = highest).
When a provider successfully logs the message, every other provider at the same priority is also
invoked. Lower-priority groups are only attempted when all providers in the current group fail.

**Name-based routing.** `LoggingService.log(logger_name, …)` looks up the provider whose
config-file name matches `logger_name`. If no match, falls back to the provider named `"default"`.
For example, `log("requests", …)` routes to the provider from `requests.json`; any unmatched
name routes to `default.json`. Non-default providers automatically receive a named stdlib
logger (keyed by the config-file stem) with `propagate=False`, isolating their handlers from
the root logger where the `"default"` provider's handlers live.

`service_provider.config` keys (all optional except `handlers`):

| Key           | Purpose                                                                           |
|---------------|-----------------------------------------------------------------------------------|
| `formatters`  | Python logging formatter definitions.                                             |
| `handlers`    | Handler definitions. Custom `active` flag (default `true`) controls inclusion.    |
|               | `${output_dir}` placeholder is expanded in `filename` values. Seq handlers are    |
|               | auto-disabled when `seqlog` is missing or unreachable.                            |
| `loggers`     | Optional named stdlib logger overrides; auto-generated for non-default providers. |
| `structlog`   | Structlog options. `renderer`: `"console"` (default in dev) or `"json"`.          |
| `request_log` | Per-field `fields` boolean toggles for request-event payload filtering.            |

The provider auto-injects `version: 1` and `disable_existing_loggers: false` into the
`dictConfig` dict. For non-default providers, a named stdlib logger matching the config-file
stem is auto-generated with all active handlers and `propagate: false`, keeping their output
isolated from root. The `"default"` provider is exempt: its handlers are added to
`root.handlers` so they serve as the application-wide catch-all.

Each handler may reference its own formatter, allowing different output formats per handler:

```json
"formatters": {
  "plain": { "format": "%(message)s" },
  "detailed": { "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s" }
},
"handlers": {
  "console": { "formatter": "plain", "..." : "..." },
  "file":    { "formatter": "detailed", "..." : "..." }
}
```

General logging config (`config/logging/default.json`):

```json
{
  "service_provider": {
    "service": "logging",
    "provider": "struct_log",
    "provider_category": "normal",
    "enabled": true,
    "priority": 2,
    "version": "1.0",
    "config": {
      "formatters": {
        "plain": { "format": "%(message)s" }
      },
      "handlers": {
        "console": {
          "class": "logging.StreamHandler",
          "level": "INFO",
          "formatter": "plain",
          "stream": "ext://sys.stdout"
        },
        "file": {
          "class": "logging.FileHandler",
          "level": "INFO",
          "formatter": "plain",
          "filename": "${output_dir}/results/logs/app.log",
          "encoding": "utf-8"
        },
        "seq": {
          "active": false,
          "class": "seqlog.structured_logging.SeqLogHandler",
          "level": "INFO",
          "server_url": "http://localhost:5341",
          "api_key": ""
        }
      },
      "structlog": { "renderer": "console" }
    }
  }
}
```

Request logging config (`config/logging/requests.json`):

```json
{
  "service_provider": {
    "service": "logging",
    "provider": "struct_log",
    "provider_category": "normal",
    "enabled": true,
    "priority": 1,
    "version": "1.0",
    "config": {
      "formatters": {
        "plain": { "format": "%(message)s" }
      },
      "handlers": {
        "requests_file": {
          "class": "logging.handlers.RotatingFileHandler",
          "level": "INFO",
          "formatter": "plain",
          "filename": "${output_dir}/results/requests.log",
          "maxBytes": 10485760,
          "backupCount": 5
        }
      },
      "request_log": {
        "fields": {
          "provider": true,
          "model": true,
          "actor": true,
          "voice_id": true,
          "language": true,
          "input": true,
          "response_duration": true,
          "timestamp": true
        }
      }
    }
  }
}
```

Trace logging config (`config/logging/trace.json`):

```json
{
  "service_provider": {
    "service": "logging",
    "provider": "struct_log",
    "provider_category": "normal",
    "enabled": true,
    "priority": 9,
    "version": "1.0",
    "config": {
      "formatters": {
        "plain": { "format": "%(message)s" }
      },
      "handlers": {
        "console": {
          "class": "logging.StreamHandler",
          "level": "TRACE",
          "formatter": "plain",
          "stream": "ext://sys.stdout"
        },
        "file": {
          "class": "logging.FileHandler",
          "level": "TRACE",
          "formatter": "plain",
          "filename": "${output_dir}/results/logs/trace.log",
          "encoding": "utf-8"
        }
      },
      "structlog": { "renderer": "console" }
    }
  }
}
```

No `"loggers"` section is needed: the provider auto-generates a `"trace"` named logger with
`propagate=false` at runtime, keeping trace output isolated from the default root handlers.

At startup, `LoggingService.configure(output_dir=…, profile=…)` calls `configure()` on each
active provider. Each provider calls `dictConfig` independently; with
`disable_existing_loggers: false`, the calls are additive. Structured application logs are
emitted via internal HTTP endpoints under `/internal/loggers/*` (see `src/services/logging/routes.py`); callers use the async helpers
in `src/core/log_client.py`, which posts JSON built from `src/services/logging/schemas.py`.

## Example requests

```bash
# Health check (no auth)
curl http://localhost:8000/api/v1/ping

# Login and use token
TOKEN=$(
  curl -s -X POST http://localhost:8000/api/v1/login \
    -d "username=admin&password=$DEFAULT_ADMIN_PASSWORD" |
    jq -r .access_token
)

curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/status

# List categories
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/content/categories

# List categories including archived
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/v1/content/categories?include_archived=true"

# Create category (creates folder, voices, icon)
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "animals"}' \
  http://localhost:8000/api/v1/content/categories

# Rename category
curl -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "creatures"}' \
  http://localhost:8000/api/v1/content/categories/animals

# Remove category (archives if ARCHIVE_DIR set)
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/v1/content/categories/creatures

# Restore category from archive
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "creatures"}' \
  http://localhost:8000/api/v1/content/categories/restore

# Create item (multipart)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -F "image=@./pic.png" -F "category=foods" -F "item=apple pie" \
  http://localhost:8000/api/v1/content/items

# List generators — admin JWT satisfies require_internal
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/internal/generators/

# List translators
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/internal/translators/

# Translate text
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text": "apple", "target_lang_code": "fr"}' \
  http://localhost:8000/internal/translators/translate
# Example response: {"success": true, "lang_text": {"lang_code": "fr", "text": "pomme"}}

# generate-word returns raw audio bytes from highest-priority active provider
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"word": "apple", "lang": "en"}' \
  --output apple.mp3 \
  http://localhost:8000/internal/generators/generate-word

# If you need normalized audio, call the optimizer endpoint separately.

# Run TTS pipeline asynchronously (returns job_id)
JOB=$(
  curl -s -X POST -H "Authorization: Bearer $TOKEN" \
    -F "file=@words.txt" -F "language=bg" -F "output_format=mp3" \
    http://localhost:8000/internal/generate |
    jq -r .job_id
)

# Poll job status
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/internal/generate/$JOB/status
```
