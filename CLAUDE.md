# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**mx-admin** is a content management and voice synthesis platform for the Kiddo language learning app. It manages multilingual categories/images and orchestrates a TTS pipeline across multiple providers.

Three components: **FastAPI backend**, **Vue 3 frontend SPA**, **Typer CLI client**.

---

## Commands

### Backend

```bash
cd backend
python3.10 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run (HTTP + Unix socket)
./uvicorn_start.sh
./uvicorn_stop.sh

# Run (HTTP only, dev)
PYTHONPATH="$PWD/src" uvicorn src.main:app --reload --host 0.0.0.0 --port 8000

# Lint
pylint src/
```

### Frontend

```bash
cd frontend
npm install
npm run dev        # Dev server at http://localhost:5173 (proxies API to :8000)
npm run build      # Production build with type-check
npm run type-check
```

### CLI

```bash
./scripts/cli.sh install     # pip install -e cli/
./scripts/cli.sh uninstall
kiddo-cli --help
```

### Docker (all services)

```bash
./scripts/app.sh start       # Backend :9000, Frontend :9001
./scripts/app.sh stop
# Or directly:
docker compose -f docker/docker-compose.yml up -d --build
```

---

## Architecture

### Backend (`backend/src/`)

- **`api/v1/`** — Public REST routes: login, cli-login, content CRUD, user management, common utils
- **`api/internal/`** — Voice generation endpoints (requires admin JWT or `X-Internal-Token` header)
- **`core/config.py`** — Pydantic Settings; all env vars and defaults live here
- **`core/content_service.py`** — Filesystem ops for categories/images; category icon generation via OpenAI DALL-E
- **`db/`** — SQLite user store (auto-creates `admin` on first run); passwords use SHA256 prehash + bcrypt
- **`security/`** — JWT auth; HTTP login → 30 min token, CLI socket login → 30 sec token
- **`services/orchestrator.py`** — Async TTS pipeline coordinator (translate → generate → optimize → evaluate)
- **`services/config_loader.py`** — Loads and validates JSON configs from `backend/config/`

### TTS Pipeline

The pipeline is driven by JSON config files in `backend/config/`:

| Directory | Purpose |
|-----------|---------|
| `generators/` | TTS providers (OpenAI, ElevenLabs, Speaktor) — set `"active": "yes/no"` |
| `translators/` | Translation configs (static CSV or OpenAI) per language pair |
| `optimizers/` | Audio post-processing (ffmpeg/pydub) — sample rate, mono, VBR |
| `loggers/` | Structlog + Seqlog configuration |

Pipeline flow: `Input file (TXT/CSV/XLSX) → Translate → Generate audio → Optimize → Evaluate (STT scoring) → Write results`

Output lands in `output/tts_audio_<lang>/<provider>/` and `output/optimized_<optimizer>/`.

### Content Filesystem Layout

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
- **CLI** → Unix socket (`/tmp/kiddo-backend.sock`) → short-lived JWT (30 sec) → subsequent HTTP calls
- **Internal services** → `X-Internal-Token` header or admin JWT
- **User roles**: `admin` (full access), `viewer` (read-only)

### Frontend (`frontend/src/`)

- Vue 3 + TypeScript + Vite; API proxied to `:8000` in dev via `vite.config.ts`
- Routes: `/login`, `/categories`, `/categories/:id`, `/categories/:id/images/:imageId`
- Auth guard in `router/index.ts`; token in `localStorage`
- Star Admin Bootstrap theme (vendor CSS in `assets/`)
- Supported languages: `en`, `es`, `fr`, `bg`

### CLI (`cli/src/kiddo_cli/`)

- Pure HTTP client — no backend code shared
- Subcommands: `common`, `content`, `generate`, `list`, `users`
- Auth priority: `KIDDO_TOKEN` env var → Unix socket login → HTTP login (`BACKEND_URL` + `KIDDO_USER`/`KIDDO_PASSWORD`)

---

## Key Environment Variables

Set in `backend/.env` (copy from `.env.example`):

```
OPENAI_API_KEY=
ELEVENLABS_API_KEY=
SPEAKTOR_API_KEY=
INTERNAL_SERVICE_TOKEN=
JWT_SECRET_KEY=
```
