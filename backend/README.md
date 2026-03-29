# Kiddo Backend

FastAPI backend for Kiddo content management. All public API routes are under `/api/v1`. Protected endpoints require a Bearer token (obtained via `/login` or `/cli-login`). Internal service routes are under `/internal/*`.

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
```
pip install -r backend/requirements.txt
```

## Run the backend

From the backend directory:

```bash
./uvicorn_start.sh
```

This starts the HTTP server and the CLI Unix socket. To stop:

```bash
./uvicorn_stop.sh
```

## Tests

From the backend directory:

```bash
PYTHONPATH="$PWD/src" .venv/bin/python -m pytest tests/ -v
```

Test layout:

```
tests/
├── translation/
│   ├── test_translation.py     # Unit tests: TextTranslate base, TranslatorFactory, build_translator*, translate_with_all_active, StaticTTSTranslate
│   └── test_integration.py     # Integration tests: GET /internal/translators, POST /internal/translate
├── text_to_speech/
│   ├── test_text_to_speech.py  # Unit tests: _boolish, TTSProvider base, ProviderFactory, build_provider*, load_provider_configs
│   └── test_integration.py     # Integration tests: GET /internal/generators, POST /internal/generators/{provider}/generate, POST /internal/generators/generate-word
├── generation/
│   ├── test_generation.py      # Unit tests: normalize_audio (no ffmpeg, success, failure, ffmpeg args)
│   └── test_integration.py     # Integration tests: normalization pipeline, request_id construction, provider matching, generate-word
└── test_optimization.py        # Unit tests: audio optimizers
```

## Auth

- **HTTP login:** `POST /api/v1/login` with `application/x-www-form-urlencoded` body `username` and `password` (default `admin` / value of `DEFAULT_ADMIN_PASSWORD`). Returns `access_token`; use as `Authorization: Bearer <token>`.
- **CLI login:** `POST /api/v1/cli-login` is allowed only when the request is made over the backend's Unix socket (e.g. by the CLI). Returns a short-lived token.
- **Internal auth:** Internal endpoints require a JWT with role `"internal"` or `"admin"`. An internal token is generated at startup (stored in `app.state.internal_token`, 24-hour lifetime) and never shared as a static secret. Admin JWTs also satisfy the check, so the CLI can call internal endpoints with its normal login token.

Env (see `src/core/config.py`):

- `SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `CLI_TOKEN_EXPIRE_SECONDS` — JWT settings
- `AUTH_DISABLED` — if true, auth checks are skipped
- `DEBUG_MODE` — if true, `/api/v1/users*` skips Unix socket + admin dependency checks
- `CONTENT_DIR` — content data path
- `ARCHIVE_DIR` — directory for archived (removed) categories. If empty, remove deletes categories permanently instead of archiving.
- `OPENAI_API_KEY` — required for TTS, translation, and category icon generation (create/rename category, create object).
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
| `POST` | [`/login`](src/api/v1/routes_auth.py#L34)             | No   | Login with username/password; returns JWT.                |
| `POST` | [`/cli-login`](src/api/v1/routes_auth.py#L52)         | No   | CLI-only login over Unix socket; returns short-lived JWT. |

### Tasks

| Method | Path                                                     | Auth | Description                                                                              |
|--------|----------------------------------------------------------|------|------------------------------------------------------------------------------------------|
| `GET`  | [`/ping`](src/api/v1/routes_common.py#L11)               | No   | Health check; returns PONG with backend platform info.                                   |
| `GET`  | [`/status`](src/api/v1/routes_common.py#L16)             | Yes  | Service status.                                                                          |
| `POST` | [`/operation`](src/api/v1/routes_common.py#L21)          | Yes  | Perform a service operation. Query param: `name`.                                        |
| `POST` | [`/list-files`](src/api/v1/routes_common.py#L30)         | Yes  | List files in the configured directory. Body: `{"depth": int}` (depth < 1 treated as 1). |

### Content

| Method   | Path                                                                            | Auth | Description                                                                                                                       |
|----------|---------------------------------------------------------------------------------|------|-----------------------------------------------------------------------------------------------------------------------------------|
| `GET`    | [`/content/categories`](src/api/v1/routes_content.py#L38)                      | Yes  | List content categories. Query: `include_archived`, `archived_only` (bool) to include or list only archived categories.           |
| `POST`   | [`/content/categories`](src/api/v1/routes_content.py#L51)                      | Yes  | Create a category. Body: `{"name": "<category_name>"}`. Creates folder, voice files per language, and category icon. Returns 201. |
| `POST`   | [`/content/categories/restore`](src/api/v1/routes_content.py#L62)              | Yes  | Restore a category from archive. Body: `{"name": "<category_name_or_archive_folder>"}` (e.g. `foods` or `foods_1738456789`).      |
| `GET`    | [`/content/categories/{category_name}`](src/api/v1/routes_content.py#L103)     | Yes  | Category details by name.                                                                                                         |
| `PATCH`  | [`/content/categories/{category_name}`](src/api/v1/routes_content.py#L77)      | Yes  | Rename category. Body: `{"name": "<new_name>"}`. Renames folder and regenerates category voice files.                             |
| `DELETE` | [`/content/categories/{category_name}`](src/api/v1/routes_content.py#L90)      | Yes  | Remove category. If `ARCHIVE_DIR` is set, archives it (date suffix); otherwise deletes permanently.                               |
| `POST`   | [`/content/objects`](src/api/v1/routes_content.py#L114)                        | Yes  | Create a content object. Multipart: `image` (file), `category` (form), `object` (form, English word). Returns 201.                |

### Users

User routes are under `/api/v1/users*` and require both admin role and Unix socket access.
When `DEBUG_MODE=true`, those two dependencies are overridden at startup for local debugging.

| Method   | Path                                                                 | Auth              | Description                                                       |
|----------|----------------------------------------------------------------------|-------------------|-------------------------------------------------------------------|
| `POST`   | [`/users`](src/api/v1/routes_users.py#L45)                           | Yes (admin + UDS) | Create a user with role `admin` or `viewer`.                      |
| `GET`    | [`/users`](src/api/v1/routes_users.py#L66)                           | Yes (admin + UDS) | List all users. Query: `username` (fuzzy), `role` (admin/viewer). |
| `DELETE` | [`/users/{username}`](src/api/v1/routes_users.py#L77)                | Yes (admin + UDS) | Delete a user (cannot remove the last admin).                     |
| `PATCH`  | [`/users/{username}/password`](src/api/v1/routes_users.py#L107)      | Yes (admin + UDS) | Change a user's password.                                         |
| `PATCH`  | [`/users/{username}/role`](src/api/v1/routes_users.py#L131)          | Yes (admin + UDS) | Change a user's role (cannot demote the last admin).              |

### Internal

Internal endpoints are under `/internal/*`. They require `Authorization: Bearer <token>` where the token's role is `"internal"` or `"admin"`. An internal token is automatically generated at startup; admin JWTs (from `/login`) also work.

#### Jobs

| Method | Path                                                                                  | Description                                                               |
|--------|---------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| `POST` | [`/internal/generate`](src/api/internal/jobs_routes.py#L86)                          | Upload input file, trigger TTS pipeline in background; returns `job_id`.  |
| `GET`  | [`/internal/generate/{job_id}/status`](src/api/internal/jobs_routes.py#L115)         | Poll job status (`pending`, `running`, `completed`, `failed`).            |
| `POST` | [`/internal/generate-sync`](src/api/internal/jobs_routes.py#L141)                    | Upload input file, run pipeline synchronously; returns `RunSummary` JSON. |

**Generate request (multipart form):**

- `file` — input file (.txt, .csv, or .xlsx)
- `language` — default language code (default: `en`)
- `output_format` — audio format: mp3, wav, pcm, opus (default: `mp3`)
- `max_concurrency` — parallel provider requests (default: `5`)

#### Generators

| Method | Path                                                                                       | Description                                                              |
|--------|--------------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| `GET`  | [`/internal/generators`](src/api/internal/generators_routes.py#L45)                       | List all configured TTS providers with activation state and settings.    |
| `POST` | [`/internal/generators/{provider}/generate`](src/api/internal/generators_routes.py#L74)   | Generate audio for a single TTS request via the named provider.          |
| `POST` | [`/internal/generators/generate-word`](src/api/internal/generators_routes.py#L128)        | Generate audio for a single word using the first active provider.        |

#### Evaluators

| Method | Path                                                                                  | Description                                                              |
|--------|---------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| `GET`  | [`/internal/evaluators`](src/api/internal/evaluators_routes.py#L43)                  | List all configured STT evaluators with activation state.                |
| `POST` | [`/internal/evaluators/evaluate`](src/api/internal/evaluators_routes.py#L70)         | Run STT transcription, score, persist, and return evaluation result.     |

#### Optimizers

| Method | Path                                                                                       | Description                                                              |
|--------|--------------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| `GET`  | [`/internal/optimizers`](src/api/internal/optimizers_routes.py#L28)                       | List all configured audio optimizers with activation state and priority. |
| `POST` | [`/internal/optimizers/{optimizer}/optimize`](src/api/internal/optimizers_routes.py#L57)  | Run the named optimizer on a given audio file.                           |

#### Translators

| Method | Path                                                                                  | Description                                                              |
|--------|---------------------------------------------------------------------------------------|--------------------------------------------------------------------------|
| `GET`  | [`/internal/translators`](src/api/internal/translators_routes.py#L40)                | List all configured translation services with activation state.          |
| `POST` | [`/internal/translate`](src/api/internal/translators_routes.py#L69)                  | Translate text via all active translators in priority order.             |
| `POST` | [`/internal/translate-file`](src/api/internal/translators_routes.py#L97)             | Translate a word list file; returns per-word translations for all langs. |

## Architecture

### Service Registry

At startup, `src/services/registry_builder.py` builds a `ServiceRegistry` that parses all JSON configs once and instantiates TTS providers, translators, and optimizers. The registry is stored in `app.state.registry` and injected via `Depends(get_registry)`.

This eliminates per-request JSON parsing. Provider instances are reused across all requests.

**Hot-reload:** The `watchfiles` background task watches `config/` for changes. When a file changes, `registry.reload()` re-parses configs and replaces instances in-place behind an `asyncio.Lock`, so in-flight requests finish cleanly.

### Job Store

Async job tracking (`src/core/job_store.py`) is stored in `app.state.job_store` and injected via `Depends(get_job_store)`.

- Jobs expire after 1 hour (TTL eviction runs every 5 minutes via background task)
- Thread-safe via `asyncio.Lock`
- Drop-in replaceable with SQLite or Redis for multi-worker deployments

### HTTP Boundary

`core/` has zero imports from `services/`. The pipeline and content service call internal HTTP endpoints instead of importing service implementations directly. This keeps the core infrastructure layer decoupled from provider-specific code.

### Source layout

```
src/
├── api/
│   ├── dependencies.py           # get_registry, get_job_store, require_internal, require_admin, require_uds
│   ├── internal/
│   │   ├── jobs_routes.py        # /internal/generate, /generate/{id}/status, /generate-sync
│   │   ├── generators_routes.py  # /internal/generators, /generators/{provider}/generate, /generators/generate-word
│   │   ├── evaluators_routes.py  # /internal/evaluators, /evaluators/evaluate
│   │   ├── optimizers_routes.py  # /internal/optimizers, /optimizers/{optimizer}/optimize
│   │   └── translators_routes.py # /internal/translators, /translate, /translate-file
│   └── v1/
│       ├── routes_auth.py
│       ├── routes_common.py
│       ├── routes_content.py
│       └── routes_users.py
├── core/                         # Zero src.services imports (HTTP boundary enforced)
│   ├── config.py                 # Pydantic Settings (BASE_DIR, env vars)
│   ├── config_loader.py          # JSON config parsing (providers, translators, optimizers)
│   ├── models.py                 # Core dataclasses (TTSRequest, GeneratedAudio, EvaluationResult, etc.)
│   ├── output_paths.py           # Stable output directory and filename helpers
│   ├── languages.py              # Language code/name lookups and BCP-47 conversion
│   ├── input_loader.py           # Load structured requests from CSV/XLSX/TXT files
│   ├── result_writer.py          # Write per-run JSON and CSV reports
│   ├── logging/                  # Centralized logging (structlog, Seq, request logging)
│   ├── content_service.py        # Category / object filesystem operations; DALL-E icon generation
│   ├── job_store.py              # Async JobStore with TTL eviction
│   ├── pipeline.py               # TTS pipeline orchestration (calls internal HTTP endpoints)
│   ├── registry.py               # Pure data container for service instances (no service imports)
│   ├── tts.py                    # Single-word TTS via HTTP (used by content_service)
│   └── translation.py            # Single-word translation via HTTP (used by content_service)
├── db/
│   └── database.py               # SQLite user store
├── security/
│   ├── auth.py                   # JWT verification
│   └── tokens.py                 # create_access_token, create_internal_token
└── services/                     # Business logic implementations (provider-specific code)
    ├── registry_builder.py       # Builds and reloads ServiceRegistry from config files
    ├── text_to_speech/           # OpenAI / ElevenLabs / Speaktor TTS providers
    │   └── providers/            # OpenAI / ElevenLabs / Speaktor provider implementations
    ├── translation/              # Translation service
    │   ├── models.py             # TextTranslate abstract base class
    │   ├── service.py            # TranslatorFactory, builders, orchestration
    │   └── providers/            # OpenAI / static translator implementations
    ├── optimization/             # FFmpeg / pydub audio optimizers
    │   └── providers/            # FFmpeg / pydub optimizer implementations
    ├── evaluation/               # STT transcription, Levenshtein scoring, score stores
    ├── generation/               # Audio normalization, input loading, result writing
    │   └── providers/            # Audio normalizer
    └── logging/                  # Structlog and Seq logging implementation
```

## TTS config

TTS providers and translators are configured via JSON files in `config/`:

```
config/
|-- loggers/
|   |-- logging.json
|   |-- request_logger.json
|   `-- structlog.json
|-- optimizers/
|   |-- ffmpeg_high.json
|   |-- ffmpeg_low.json
|   `-- pydub_optimized.json
|-- generators/
|   |-- openai.json
|   |-- elevenlabs.json
|   `-- speaktor.json
`-- translators/
    |-- openai_en_es.json
    |-- openai_en_fr.json
    |-- openai_en_bg.json
    `-- static.json
```

Set `service_provider.enabled` to `true` or `false` in each config file to enable/disable entries. For generators, `service_provider.config.generate.enabled` can optionally override generation activation per provider. The registry hot-reloads within seconds when any file in `config/` is saved.

### Translators

Each translator is configured in its own JSON file under `config/translators/`.
Each file uses a top-level `service_provider` object containing both metadata and translator settings.

`service_provider` fields:

| Field      | Type   | Description                                          |
|------------|--------|------------------------------------------------------|
| `service`  | string | Service type (currently `translator`).               |
| `provider` | string | Translator implementation name (`openai`, `static_translate`). |
| `enabled`  | bool   | Set `true` to enable the translator.                 |
| `priority` | int    | Execution order (lower runs first).                  |
| `version`  | string | Config schema version (typically `1.0`).             |
| `config`   | object | Translator-specific settings.                        |

Common `service_provider.config` fields:

| Field                    | Type   | Description                                                |
|--------------------------|--------|------------------------------------------------------------|
| `input_language`         | string | Default source language when request/CLI value is missing. |
| `output_language`        | string | Default output language when request/CLI value is missing. |
| `content_dir`            | string | Optional path used by `static_translate` provider.         |

Example (`config/translators/openai_en_fr.json`):

```json
{
  "service_provider": {
    "service": "translator",
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

Each optimizer is configured in its own JSON file under `config/optimizers/`.
Each file uses a top-level `service_provider` object containing both metadata and optimizer settings.

`service_provider` fields:

| Field      | Type   | Description                                        |
|------------|--------|----------------------------------------------------|
| `service`  | string | Service type (currently `optimizer`).              |
| `provider` | string | Optimizer implementation name (`ffmpeg`, `pydub`). |
| `enabled`  | bool   | Set `true` to enable the optimizer.                |
| `priority` | int    | Execution order (lower runs first).                |
| `version`  | string | Config schema version (typically `1.0`).           |
| `config`   | object | Optimizer-specific settings.                       |

Common `service_provider.config` fields:

| Field                | Type | Description                                          |
|----------------------|------|------------------------------------------------------|
| `sample_rate`        | int  | Output sample rate (default `22050`).                |
| `mono`               | bool | Convert output to mono when `true` (default `true`). |
| `vbr_quality`        | int  | MP3 VBR quality `0-9` (default `9`).                 |

Example (`config/optimizers/ffmpeg_high.json`):

```json
{
  "service_provider": {
    "service": "optimizer",
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

```
output/
|-- tts_audio_en/openai/hello_en.mp3          # raw generator output
|-- optimized_ffmpeg_high/
|   `-- tts_audio_en/openai/hello_en.mp3      # optimized copy
```

Requirements: `ffmpeg` must be on PATH for the ffmpeg optimizer type. The pydub type additionally
requires the `pydub` Python package (included in `requirements.txt`).

## Example requests

```bash
# Health check (no auth)
curl http://localhost:8000/api/v1/ping

# Login and use token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/login \
  -d "username=admin&password=$DEFAULT_ADMIN_PASSWORD" | jq -r .access_token)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/status

# List categories
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/content/categories

# List categories including archived
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8000/api/v1/content/categories?include_archived=true"

# Create category (creates folder, voices, icon)
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "animals"}' http://localhost:8000/api/v1/content/categories

# Rename category
curl -X PATCH -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "creatures"}' http://localhost:8000/api/v1/content/categories/animals

# Remove category (archives if ARCHIVE_DIR set)
curl -X DELETE -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/v1/content/categories/creatures

# Restore category from archive
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"name": "creatures"}' http://localhost:8000/api/v1/content/categories/restore

# Create object (multipart)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -F "image=@./pic.png" -F "category=foods" -F "object=apple pie" \
  http://localhost:8000/api/v1/content/objects

# List generators — admin JWT satisfies require_internal
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/internal/generators

# List translators
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/internal/translators

# Translate text
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"text": "apple", "target_lang_code": "fr"}' \
  http://localhost:8000/internal/translate

# Run TTS pipeline asynchronously (returns job_id)
JOB=$(curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -F "file=@words.txt" -F "language=bg" -F "output_format=mp3" \
  http://localhost:8000/internal/generate | jq -r .job_id)

# Poll job status
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/internal/generate/$JOB/status

# Run TTS pipeline synchronously
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -F "file=@words.txt" -F "language=bg" -F "output_format=mp3" \
  http://localhost:8000/internal/generate-sync
```
