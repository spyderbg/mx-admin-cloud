# mx-admin

Admin application for **Kiddo** content management: categories, content objects, multi-language voices (TTS), translation, and category icons. Consists of a FastAPI backend, a Vue 3 frontend, and a Typer-based CLI that talks to the backend over HTTP.

## Project structure

```
mx-admin/
├── backend/          # FastAPI API (auth, tasks, content)
├── frontend/         # Vue 3 + TypeScript + Vite SPA
├── cli/              # kiddo-cli (Typer) – HTTP client for the backend
├── docker/           # Dockerfiles and docker-compose
├── content/          # Content data (categories: drinks, foods, toys, etc.)
├── docs/             # Backend/CLI layout, game plugin generation
├── scripts/          # app.sh, cli.sh, setup.sh, export_env_variables.py
├── existing_scripts/ # Legacy scripts (translate_csv, generate, export, etc.)
└── README.md
```

### Backend (`backend/`)

- **Stack:** Python 3.10+, FastAPI, Uvicorn, Pydantic Settings, python-jose, passlib, OpenAI (TTS/translation/icons).
- **Layout:** `src/main.py`; `src/api/v1/` (auth, tasks, content); `src/core/` (config, content_service, translation, TTS, icon); `src/security/` (auth, tokens).
- **API base:** `/api/v1` — auth (`/login`, `/cli-login`), tasks (`/ping`, `/status`, `/operation`, `/list-files`), content (categories CRUD, restore, objects).
- **Docs:** See [backend/README.md](backend/README.md) for endpoints and env vars.

### Frontend (`frontend/`)

- **Stack:** Vue 3, TypeScript, Vite, Vue Router, Axios.
- **Layout:** `src/api/`, `src/components/`, `src/views/`, `src/layouts/`, `src/router/`, `src/types/`, `src/assets/`.
- **Routes:** `/login`, `/categories`, `/categories/:id`, `/categories/:id/images/:imageId` (auth required for categories).
- **Docs:** See [frontend/README.md](frontend/README.md) for structure, routes, and components.

### CLI (`cli/`)

- **Stack:** Python 3.10+, Typer, requests/httpx. No backend code; all calls over HTTP.
- **Install:** `./scripts/cli.sh install` or `pip install -e cli/` → `kiddo-cli` command.
- **Commands:** `tasks` (ping, status, operation, list-files), `content` (categories, create-category, rename-category, remove-category, restore-category, category-details, create-object).
- **Docs:** See [cli/README.md](cli/README.md) for auth and usage.

### Docker (`docker/`)

- **Services:** `backend` (port 8000), `frontend` (port 5173). Content mounted at `/app/content`.
- **Compose:** `docker compose -f docker/docker-compose.yml up` (from repo root). Helper: `./scripts/app.sh start` or `./scripts/app.sh stop`.

### Docs (`docs/`)

- [backend_first_build.md](docs/backend_first_build.md) — Backend/CLI layout, run instructions, dependencies.
- [game_plugin_generate.md](docs/game_plugin_generate.md) — Mustache template and plugin CPP generation from merged config.

---

## Getting started

### Backend (local)

From repo root:

```bash
cd backend
./uvicorn_start.sh
```

This starts HTTP on `http://0.0.0.0:8000` and a CLI-only Unix socket at `/tmp/kiddo-backend.sock`. To stop: `./uvicorn_stop.sh`.

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

Backend: `http://localhost:8000`. Frontend: `http://localhost:5173`. Stop: `./scripts/app.sh stop` or `docker compose -f docker/docker-compose.yml down`.

### CLI

```bash
./scripts/cli.sh install
kiddo-cli --help
kiddo-cli tasks ping
kiddo-cli content categories
```

Use `BACKEND_URL` and `KIDDO_TOKEN` (or socket-based login) as in [cli/README.md](cli/README.md).

---

## Environment (backend)

Key settings (see `backend/src/core/config.py`):

| Variable | Description |
|----------|-------------|
| `CONTENT_DIR` | Content data directory (default `../content`) |
| `ARCHIVE_DIR` | Archive directory for removed categories (optional) |
| `OPENAI_API_KEY` | Required for TTS, translation, and category icon generation |
| `SECRET_KEY`, `ALGORITHM` | JWT auth |
| `AUTH_DISABLED` | If true, skip auth checks |

---

## Repository

Git remote: `http://82.103.77.45/marxman/mx-admin.git` (GitLab). Default branch: `main`.
