Summary of backend and CLI layout:

**Backend** – source lives under `backend/src/kiddo_app/`:
- Config: `backend/src/kiddo_app/core/config.py` (SUPPORTED_LANGUAGES, OPENAI_API_KEY, DATA_DIRECTORY)
- Core: naming, translation, TTS, content_service under `backend/src/kiddo_app/core/`
- API routes under `backend/src/kiddo_app/api/v1/` (auth, tasks, content)
- Main app: `backend/src/kiddo_app/main.py`

**CLI** – separate project under `cli/src/kiddo_cli/`:
- All commands call the FastAPI backend over HTTP only (no backend code imports).
- Install as terminal command `kiddo-cli` via `scripts/cli.sh install` or `pip install -e cli/`.
- Commands: run-test, run-task, status, do-operation, list, list-categories, category-details, create-item. Use `--url` and `--token` (or env BACKEND_URL, KIDDO_TOKEN).

**Running the backend**
- From repo root: `cd backend && ./uvicorn_start.sh` (sets PYTHONPATH=backend/src), or `PYTHONPATH=backend/src uvicorn kiddo_app.main:app --reload --host 0.0.0.0 --port 8000`.
- Docker: `docker compose -f docker/docker-compose.yml up` (builds from `backend/src`, mounts content, env DATA_DIRECTORY and OPENAI_API_KEY).

**Dependencies**
- Backend: requirements.txt and pyproject.toml in `backend/` (no typer; CLI is separate).
- CLI: `cli/pyproject.toml` (typer, requests only).
