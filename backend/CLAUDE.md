# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in the backend directory.

## Project Overview

**mx-admin** is a content management and voice synthesis platform for the Kiddo language learning app. It manages multilingual categories/images and orchestrates a TTS pipeline across multiple providers.

Three components: **FastAPI backend**, **Vue 3 frontend SPA**, **Typer CLI client**.

## Commands

```bash
python3.10 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run (HTTP + Unix socket)
./uvicorn_start.sh
./kill_uvicorn.sh

# Run (HTTP only, dev)
PYTHONPATH="$PWD/src" uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# Lint
pylint src/
```

### Docker (all services, from project root)

```bash
./scripts/app.sh start       # Backend :9000, Frontend :9001
./scripts/app.sh stop
# Or directly:
docker compose -f docker/docker-compose.yml up -d --build
```

---

## Architecture

### Backend (`src/`)

- **`api/v1/`** — Public REST routes (`routes_*.py`) and Pydantic request/response models in **`schemas/`**
  (login, cli-login, user management over UDS+admin, common utils)
- **`api/internal/`** — Internal HTTP endpoints for TTS generation, evaluation, optimization, and translation
  (requires internal service token or admin JWT)
- **`core/`** — Shared infrastructure with **zero** `src.services` imports (HTTP boundary enforced):
  - `config.py` — Pydantic Settings; all env vars and defaults
  - `models.py` — Core dataclasses (TTSRequest, GeneratedAudio, EvaluationResult, etc.)
  - `content_naming.py` — Slugification and output filename helpers
  - `languages.py` — Language code/name lookups and BCP-47 conversion
  - `input_loader.py` — `load_words()` parses CSV/XLSX/TXT for translation
  - `result_writer.py` — Write per-run JSON and CSV reports
  - `pipeline.py` — Async TTS pipeline orchestration (calls internal HTTP endpoints)
  - `job_store.py` — In-memory store for async pipeline jobs
  - `logging_route.py` — `LoggingAPIRoute`, the shared router route class that traces
    requests and reports endpoint failures
- **`services/`** — Business logic implementations (providers, translators, optimizers, evaluation)
  - `service_registry.py` — Unified registry and builder for all runtime services
  - `configuration/` — Runtime configuration service (merges disk JSON with runtime updates)
  - `content/` — Content management service (categories, items, filesystem, generated icons)
  - `text_to_speech/` — TTS provider implementations (OpenAI, ElevenLabs, Speaktor, Static)
  - `text_to_image/` — Image provider implementations (OpenAI SDK/HTTP, Google GenAI SDK/HTTP,
    ComfyUI) with a shared traffic governor (concurrency, spacing, retries) and cost estimates
  - `text_translation/` — Translator implementations (OpenAI, Static CSV)
  - `voice_optimization/` — Audio post-processing (FFmpeg, Pydub, Normalizer)
  - `evaluation/` — STT transcription and Levenshtein-based scoring
  - `logging/` — Logging service (structlog provider, HTTP emit client, request logger)
- **`db/`** — SQLite user store; passwords use SHA256 prehash + bcrypt
- **`security/`** — JWT auth and token generation

### TTS Pipeline

The pipeline is driven by JSON config files in `backend/config/` and managed by the `ConfigurationService`:

| Directory | Purpose |
|-----------|---------|
| `content/` | Content management provider (file_system) |
| `text_to_speech/` | TTS providers (OpenAI, ElevenLabs, Speaktor, Static) |
| `text_to_image/` | Image providers (OpenAI, OpenAI HTTP, GenAI, GenAI HTTP, ComfyUI) |
| `text_translation/` | Translation configs (static CSV or OpenAI) |
| `voice_optimizers/` | Audio post-processing (ffmpeg/pydub) |
| `logging/` | Logging service provider config |

Pipeline flow: `Input file (TXT/CSV/XLSX) → Translate → Generate audio → Optimize → Evaluate (STT scoring) → Write results`

Image generation is a separate config-driven service under `/internal/image-generators/*`; the content
service calls it for category icons, so icons follow whichever image provider is active.

All pipeline steps call internal HTTP endpoints (`/internal/generators/{provider}/generate`,
`/internal/evaluators/evaluate`, `/internal/optimizers/{optimizer}/optimize`) so that `core/` never
imports `services/` directly.

Output lands in `output/tts_audio_<lang>/<provider>/` and `output/optimized_<optimizer>/`.

### Logging — always out-of-process (HTTP emit)

Services are **not** guaranteed to share a host. `text_to_image`, for example, can be deployed on
a GPU box while the logging service runs elsewhere, so an in-process `LoggingService` may simply
not exist in the process that wants to log.

**Rule: emit every structured record over the internal logging API through
`src/core/log_client.py`.** Outside `src/services/logging/`, never import
`src.services.logging.service`, never resolve `LoggingService` from the registry
(`registry.get_service("logging")`), and never accept one by injection.

Each module declares `LOG_SOURCE = "<service name>"`; the wrappers put it in `fields["service"]`
so a record stays attributable once it reaches a collector that runs none of the emitting code.

| Need | Call |
|------|------|
| Record from async code | `await log_client.emit_event(LOG_SOURCE, "event", LogLevel.INFO, **fields)` |
| Record from sync code (threadpool) | `log_client.emit_event_nowait(LOG_SOURCE, "event", LogLevel.INFO, **fields)` |
| A named logger (`trace`, `requests`, `default`) | `await log_client.log_event(LOG_SOURCE, "<logger>", "event", LogLevel.TRACE, **fields)` |
| Provider call ledger (model, latency, cost, attempts) | `await log_client.log_provider_request(LogProviderRequest(...))` |
| API request record | `await log_client.log_api_request(LogApiRequest(...))` |
| Trace every endpoint on a router | `route_class=LoggingAPIRoute.for_source(LOG_SOURCE)` |

- `log_client.configure(base_url=API_BASE_URL, internal_token=...)` runs once at startup in
  `src/main.py`. Point `API_BASE_URL` at the host running the logging service.
- **Sync service methods cannot await.** FastAPI runs `def` handlers (and the service methods they
  call) in its threadpool, where there is no event loop. Use `emit_event_nowait`: it hands the post
  to the loop captured by `configure()` and returns immediately. A record can be dropped if that
  loop is gone — the accepted trade for never stalling an operation on a remote logging host.
- `emit_event`, `log_event` and `emit_event_nowait` swallow every failure themselves (unconfigured
  client, unreachable collector, bad payload), so call sites need no `try/except`. The lower-level
  `emit`, `log`, `log_provider_request` and `log_api_request` only swallow `httpx.RequestError` —
  guard those yourself with `try/except Exception` plus a stdlib `logger.debug`.
- A named logger resolves to a config-file stem (`config/logging/<name>.json`) and 404s when that
  config is missing or disabled, dropping the record by design — `trace` ships disabled, so trace
  records go nowhere until you enable it. `emit_event` cannot 404; prefer it when a record must land.
- Importing `src.services.logging.schemas` / `.models` (`LogRequest`, `LogLevel`) is fine — those are
  the wire contract, not the implementation.
- Plain `logging.getLogger(__name__)` stays fine for local diagnostics, but those lines stay on the
  local host and are never collected centrally.
- Only exceptions: `src/services/logging/` itself, and `src/main.py`, which calls
  `LoggingService.configure()` on the host that owns the logging providers.

Every service emits over HTTP today. Coverage lives in `src/core/__tests__/test_log_client.py`.

### Provider selection strategy

How a service dispatches one task to its active providers (always in ascending `priority`):
**ALL_PROVIDERS** calls every one, **FIRST_SUCCESS** tries them until one succeeds.

| Service | Supported | Default | Applied in |
|---------|-----------|---------|------------|
| `logging` | both | ALL_PROVIDERS | `LoggingService.emit` (providers without `provider_category`) |
| `voice_optimization` | both | ALL_PROVIDERS | content `optimize_item_voice` (reads the strategy over the internal configuration API) |
| `text_to_speech`, `text_to_image` | FIRST_SUCCESS | FIRST_SUCCESS | `generate()` without an explicit `provider_name` |
| `text_translation` | FIRST_SUCCESS | FIRST_SUCCESS | `translate_text` (no chaining) |
| `content` | FIRST_SUCCESS | FIRST_SUCCESS | every `ContentService` operation; with no active config the API and `/content` files answer 503 |
| `configuration`, `evaluation` | none | — | single provider |

- Shared code: `src/services/provider_strategy.py` (enum, `first_success`, `dispatch_each`,
  `AllProvidersFailedError`, `read_strategies`). A service declares `SUPPORTED_STRATEGIES` and
  `DEFAULT_STRATEGY` as class attributes (`src/services/models.py`) and reads `self.provider_strategy`.
- Stored per service in `config/_service_strategies.json` (config root, never a provider directory);
  the registry applies it on every load, keeping the default for a missing, unsupported or broken entry.
  Changed through `GET/PUT /internal/configurations/{service}/strategy` and the service Configuration page.
- Calls that name a provider (`/internal/generators/{provider}/generate`,
  `/internal/optimizers/{name}/optimize`, `LoggingService.log(name, ...)`) bypass the strategy.

### Content Filesystem Layout (relative to project root)

```
content/<category>/
├── <item>/                          # named after the item id: I_WaterBottle -> water_bottle
│   ├── <item>.png                   # main image (a copy of one variant)
│   ├── <item>_01.png, <item>_02.jpg # image variants
│   └── <item>_en.mp3, <item>_en_01.mp3, <item>_en.csv, ...  # voices, voice variants, metadata sidecars
├── items_ids.csv, items_<lang>.csv  # row-aligned registry: id, then the name per language
└── <category>.png, <category>_en.mp3, ...   # category icon and voices

archive/<category>_<unix_timestamp>/   # Deleted categories
```

An item's folder is derived from its **id only** (`_folder_name_from_item_id`), never from its names:
editing an item's English word leaves the folder alone, while changing its id renames the folder
and the files inside it that carry the old folder name.

### Authentication

- **Frontend** → HTTP login → long-lived JWT (30 min) → `Authorization: Bearer <token>`
- **CLI** → Unix socket (`/tmp/kiddo-backend.sock`) → short-lived JWT (30 sec) → subsequent HTTP calls.
  - `cli-login` and all `users` commands **strictly** require the Unix socket.
- **Internal services** → JWT with role `internal` or `admin`.
- **User roles**: `admin` (full access), `viewer` (read-only).

---

## Key Environment Variables

Set in `backend/.env` or repository root `.env`:

```bash
# Provider API Keys
OPENAI_API_KEY=
ELEVENLABS_API_KEY=
SPEAKTOR_API_KEY=
GEMINI_API_KEY=         # Google GenAI image providers
COMFYUI_API_KEY=        # only if your ComfyUI server sits behind an authenticating proxy

# Security
SECRET_KEY=             # JWT secret
ALGORITHM=HS256

# Auth
ACCESS_TOKEN_EXPIRE_MINUTES=30
CLI_TOKEN_EXPIRE_SECONDS=30
DEFAULT_ADMIN_PASSWORD=

# Paths (absolute or relative to backend/)
CONFIG_DIR=config
CONTENT_DIR=../content
ARCHIVE_DIR=../archive
OUTPUT_DIR=../output
DB_PATH=data/users.db

# Logging & Pipeline
LOG_PROFILE=dev         # dev (pretty) or prod (JSON)
TTS_MAX_CONCURRENCY=5
API_BASE_URL=http://127.0.0.1:8000
```

Internal `/internal/*` routes accept a JWT with role `internal` (created at backend startup) or `admin`.
