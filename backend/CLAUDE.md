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
- **`services/`** — Business logic implementations (providers, translators, optimizers, evaluation)
  - `service_registry.py` — Unified registry and builder for all runtime services
  - `configuration/` — Runtime configuration service (merges disk JSON with runtime updates)
  - `content/` — Content management service (categories, items, filesystem, DALL-E icons)
  - `text_to_speech/` — TTS provider implementations (OpenAI, ElevenLabs, Speaktor, Static)
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
| `text_translation/` | Translation configs (static CSV or OpenAI) |
| `voice_optimizers/` | Audio post-processing (ffmpeg/pydub) |
| `logging/` | Logging service provider config |

Pipeline flow: `Input file (TXT/CSV/XLSX) → Translate → Generate audio → Optimize → Evaluate (STT scoring) → Write results`

All pipeline steps call internal HTTP endpoints (`/internal/generators/{provider}/generate`,
`/internal/evaluators/evaluate`, `/internal/optimizers/{optimizer}/optimize`) so that `core/` never
imports `services/` directly.

Output lands in `output/tts_audio_<lang>/<provider>/` and `output/optimized_<optimizer>/`.

### Content Filesystem Layout (relative to project root)

```
content/<category>/
├── images/<name>/
│   ├── <name>.png, <name>_thumb.png
│   └── <name>_en.mp3, <name>_es.mp3, <name>_fr.mp3, <name>_bg.mp3
├── category_icon.png
└── category_en.mp3, category_es.mp3, ...

archive/<category>_<unix_timestamp>/   # Deleted categories
```

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
