# mx-admin

Admin application for **Kiddo** content management: categories, content objects, multi-language voices (TTS), translation, and category icons. Consists of a FastAPI backend, a Vue 3 frontend, and a Typer-based CLI that talks to the backend over HTTP.

## Project structure

```
mx-admin/
|-- backend/          # FastAPI API (auth, tasks, content, internal voice generation)
|   |-- config/       # TTS provider and translator JSON configs
|   `-- src/
|       |-- api/      # v1/ (public routes) and internal/ (voice generation)
|       |-- core/     # config, content_service, translation, TTS wrappers
|       `-- services/ # Multi-provider TTS, translation, evaluation, logging
|-- frontend/         # Vue 3 + TypeScript + Vite SPA
|-- cli/              # kiddo-cli (Typer) - HTTP client for the backend
|-- docker/           # Dockerfiles and docker-compose
|-- content/          # Content data (categories: drinks, foods, toys, etc.)
|-- docs/             # Backend/CLI layout, game plugin generation
|-- scripts/          # app.sh, cli.sh, setup.sh, export_env_variables.py;
|                     #   validate_build.py (backend lint), validate_frontend_build.py (TS/Vue build);
|                     #   generate_ts_schemas.py, generate_ts_client.py (codegen)
`-- README.md
```

### Backend (`backend/`)

- **Stack:** Python 3.10+, FastAPI, Uvicorn, Pydantic Settings, python-jose, bcrypt (password hashing), OpenAI (TTS/translation/icons), httpx, structlog. `LocalizedString` (`src/core/localization.py`) provides multilingual string handling with Pydantic v2 integration.
- **Layout:** `src/main.py`; `src/api/v1/` (auth, common routes, content, users); `src/api/internal/` (voice generation); `src/core/` (config, pipeline, content_service, models); `src/services/` (multi-provider TTS, translation, evaluation, logging); `src/security/` (auth, tokens).
- **API base:** `/api/v1` -- auth, common routes, content; `/internal/*` -- voice generation (JWT with role `internal` or `admin`; short-lived internal JWT is created at startup for in-process calls).
- **Internal translate response:** `POST /internal/translate` returns `success` and `lang_text` (`{ "lang_code", "text" }`).
- **Category/item names:** `name` fields in category and item responses are `LocalizedString` objects — a `dict[str, str]` mapping language codes to translations (e.g. `{"en": "coffee", "fr": "café"}`). The frontend maps these to plain strings based on the selected content language.
- **Category thumbnails:** `CategorySummary` now includes `thumbnail_uri`; category thumbnail images (`<category>.png`) live at the root of each category folder under `content/`.
- **Services:** Multi-provider TTS (Static, OpenAI, ElevenLabs, Speaktor), priority-based translation (static CSV + OpenAI fallback), evaluation engine (STT + Levenshtein scoring), structured logging (structlog, Seq, file rotation).
- **Service config flow:** JSON files in `backend/config/*` use a `service_provider` envelope with `service` (type), `provider` (implementation name), `enabled`, `priority`, and a `config` payload. Each service package self-registers a `ServicePlugin` with `ServicePluginRegistry` at import time; `registry_builder` iterates the plugin list generically — adding a new service type requires no changes to registry logic.
- **TTS priority:** Lower `priority` values are selected first. `backend/config/text_to_speech/static.json` is configured with `priority: 0`, so it is preferred ahead of providers configured with `priority: 1`.
- **Docs:** See [backend/README.md](backend/README.md) for endpoints and env vars.

### Frontend (`frontend/`)

- **Stack:** Vue 3, TypeScript, Vite, Vue Router, Axios.
- **Layout:** `src/api/`, `src/components/`, `src/views/`, `src/layouts/`, `src/router/`, `src/types/` (hand-written view-models in `index.ts`, auto-generated DTOs in `contentSchemas.ts`, `LocalizedString` in `localization.ts`), `src/assets/`.
- **Routes:** `/login`, `/categories`, `/categories/:id`, `/categories/:id/images/:imageId` (auth required for categories).
- **Docs:** See [frontend/README.md](frontend/README.md) for structure, routes, and components.

### CLI (`cli/`)

- **Stack:** Python 3.10+, Typer, requests/httpx, Rich (formatted terminal output). No backend code; all calls over HTTP. Typer/Click versions are pinned so the CLI can share an environment with packages that require `click<8.2` (for example gtts).
- **Install:** `./scripts/cli.sh install` or `pip install -e cli/` -> `kiddo-cli` command.
- **Verbose mode:** Use `kiddo-cli --verbose ...` to print HTTP/debug request messages from the CLI client.
- **Commands:** `common` (ping, status, operation, list-files), `content` (categories, CRUD, objects), `list` (generators, translators, optimizers, evaluators, services, users), `translate` (word, file), `users` (list, create, update, delete), `generate` (run).
- **User management from the CLI:** On Linux/macOS, `kiddo-cli users …` and `kiddo-cli list users` use the backend’s Unix socket (same as socket login); start the backend with `backend/uvicorn_start.sh` so that socket exists.
- **Docs:** See [cli/README.md](cli/README.md) for auth and usage.

### Docker (`docker/`)

- **Ports:** Backend listens on 8000 inside the container and is published on host **9000**. Frontend is nginx on port 80 inside the container, published on host **9001**.
- **Volumes:** Content `/app/content`, archive `/app/archive`, SQLite on `backend-data`, TTS configs `/app/config`, outputs `/app/tts-output`.
- **CLI profile:** `docker compose --profile cli` starts `backend-cli` with a Unix socket at `/tmp/kiddo-backend.sock` (requires host `/tmp` mount as in compose).
- **CLI-tool profile:** `docker compose --profile cli-tool run --rm cli <command>` runs `kiddo-cli` itself in a container, talking to the `backend` service over HTTP (`KIDDO_API_BASE_URL=http://backend:8000`). Commands that strictly require the Unix socket (`users`, `list users`) are not available this way -- use the host-installed CLI for those.
- **Compose:** `docker compose -f docker/docker-compose.yml up` from repo root, or `./scripts/app.sh start` / `./scripts/app.sh stop`.
- **Docs:** See [docker/README.md](docker/README.md) for services, env vars, volumes, profiles, and troubleshooting.

### Docs (`docs/`)

- [backend_first_build.md](docs/backend_first_build.md) -- Backend/CLI layout, run instructions, dependencies.
- [game_plugin_generate.md](docs/game_plugin_generate.md) -- Mustache template and plugin CPP generation from merged config.

---

## Getting started

### Backend (local)

From repo root:

```bash
cd backend
./uvicorn_start.sh
```

This starts HTTP on `http://0.0.0.0:8000` and a CLI-only Unix socket at `/tmp/kiddo-backend.sock`, and writes `backend/uvicorn.pid`. To stop: `cd backend && ./kill_uvicorn.sh`.

Or without the socket (HTTP only):

```bash
cd backend
export PYTHONPATH="$PWD/src"
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend (local)

```bash
cd frontend
npm install
npm run dev
```

Dev server at `http://localhost:5173`; API proxied to `http://127.0.0.1:8000` (see [frontend/vite.config.ts](frontend/vite.config.ts)).

### Docker (backend + frontend)

From repo root:

```bash
./scripts/app.sh start
# or: docker compose -f docker/docker-compose.yml up -d --build
```

Backend: `http://localhost:9000`. Frontend: `http://localhost:9001`. Stop: `./scripts/app.sh stop` or `docker compose -f docker/docker-compose.yml down`.

### CLI

```bash
./scripts/cli.sh install
kiddo-cli --help
kiddo-cli common ping
kiddo-cli content categories
kiddo-cli generate list-generators
kiddo-cli generate run -l bg -f mp3 words.txt
```

Use `BACKEND_URL` and `KIDDO_TOKEN` (or socket-based login) as in [cli/README.md](cli/README.md).
Running `kiddo-cli` with no arguments shows the same help output as `kiddo-cli --help`.

---

## Environment (backend)

Key settings (see `backend/src/core/config.py`). Use `backend/.env` or the process environment; the repo root [`.env.example`](.env.example) lists optional provider API keys you can copy into `.env`.

| Variable | Description |
|----------|-------------|
| `CONTENT_DIR` | Content data directory (default `../content`) |
| `ARCHIVE_DIR` | Archive directory for removed categories (optional) |
| `DB_PATH` | SQLite user database path (default `data/users.db`) |
| `OPENAI_API_KEY` | Required for TTS, translation, and category icon generation |
| `ELEVENLABS_API_KEY` | ElevenLabs TTS provider (optional) |
| `SPEAKTOR_API_KEY` | Speaktor TTS provider (optional) |
| `CONFIG_DIR` | Path to TTS config directory (default `config`) |
| `OUTPUT_DIR` | Path to TTS output directory (default `../output`) |
| `TEMP_DIR` | Temp directory for uploads and intermediates (default `../temp`) |
| `API_BASE_URL` | Base URL for internal HTTP calls (e.g. pipeline → `/internal/...`, default `http://127.0.0.1:8000`) |
| `TTS_MAX_CONCURRENCY` | Max parallel provider requests (default 5) |
| `LOG_PROFILE` | Logging profile: `dev` or `prod` (default `dev`) |
| `SECRET_KEY`, `ALGORITHM` | JWT signing (`ACCESS_TOKEN_EXPIRE_MINUTES`, `CLI_TOKEN_EXPIRE_SECONDS`) |
| `DEFAULT_ADMIN_PASSWORD` | Initial `admin` password on first DB init |
| `DEBUG_MODE` | Relaxes some admin/UDS checks when true (local dev only) |
| `AUTH_DISABLED` | If true, skip auth checks |

---

## OpenCode AI Commands

This project defines custom commands for [OpenCode](https://opencode.ai) (terminal AI coding agent) under `.opencode/commands/`:

| Command | Description |
|---------|-------------|
| `/api-sync` | Export OpenAPI spec + regenerate TypeScript client + schemas |
| `/verify` | Validate backend build + run tests + verify frontend build |
| `/frontend` | Regenerate TypeScript types + build frontend |
| `/mockdata` | Generate mock data from `content/` directory |

Usage: type `/<command>` in the OpenCode TUI.

---

## Repository

Git remote: `http://82.103.77.45/marxman/mx-admin.git` (GitLab). Default branch: `main`.
