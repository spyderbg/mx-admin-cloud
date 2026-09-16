# Docker

Dockerfiles and Compose setup for running the backend, frontend, and CLI as containers. All
commands below assume the **repository root** as the working directory.

## Services (`docker-compose.yml`)

| Service       | Dockerfile              | Purpose                                                      | Port(s)          | Profile     |
|---------------|--------------------------|---------------------------------------------------------------|-------------------|-------------|
| `backend`     | `backend.Dockerfile`     | FastAPI API (HTTP)                                             | `9000` → `8000`   | *(default)* |
| `frontend`    | `frontend.Dockerfile`    | Vue 3 SPA built and served by nginx; proxies API calls to `backend` | `9001` → `80` | *(default)* |
| `backend-cli` | `backend.Dockerfile`     | Same backend image, started on a Unix socket instead of HTTP, for CLI use | — | `cli`       |
| `cli`         | `cli.Dockerfile`         | `kiddo-cli` itself, talking to `backend` over HTTP             | —                 | `cli-tool`  |

`backend` and `frontend` start with a plain `docker compose up`. `backend-cli` and `cli` are
opt-in via `--profile` (see [Profiles](#profiles)).

## Quick start

```bash
# from repo root
./scripts/app.sh start
# or directly:
docker compose -f docker/docker-compose.yml up -d --build
```

Backend: `http://localhost:9000` — Frontend: `http://localhost:9001`.

```bash
./scripts/app.sh stop
# or:
docker compose -f docker/docker-compose.yml down
```

`scripts/app.sh` also supports starting/stopping a single service: `app.sh start backend`,
`app.sh start frontend`, `app.sh start cli` (see the script for the full list).

## Environment variables

Compose reads a `.env` file from the repo root automatically (see `.env.example`). Provider API
keys are passed straight through into the containers; nothing else needs to be set for a default
setup.

| Variable                                          | Used by                       | Purpose                                                                                     |
|----------------------------------------------------|--------------------------------|-----------------------------------------------------------------------------------------------|
| `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `SPEAKTOR_API_KEY` | `backend`, `backend-cli`  | TTS/translation/icon-generation provider keys.                                               |
| `CONTENT_HOST_DIR`                                 | `backend`, `frontend`, `backend-cli` | **Optional.** Host directory bind-mounted to `/app/content`. Defaults to `../content` (the repo's own `content/` folder) when unset. Set this in your own local `.env` if your real content lives elsewhere on this machine. |
| `KIDDO_USER`, `KIDDO_PASSWORD`, `KIDDO_TOKEN`      | `cli`                          | Credentials the containerized `kiddo-cli` logs in with (defaults: `admin` / the backend's default admin password). |

`CONTENT_DIR`, `ARCHIVE_DIR`, `CONFIG_DIR`, `OUTPUT_DIR`, and `DB_PATH` are set directly in
`docker-compose.yml` to the in-container mount points (`/app/content`, `/app/archive`, etc.) —
you don't need to set these yourself; override the **host** side via the volumes below instead.

> `INTERNAL_SERVICE_TOKEN` is intentionally not set here — `Settings` (`backend/src/core/config.py`)
> uses `extra="ignore"`, so an unrecognized env var is silently dropped rather than read by anything.

## Volumes

| Mount                                | Target             | Kind         | Notes                                                                 |
|---------------------------------------|---------------------|--------------|------------------------------------------------------------------------|
| `${CONTENT_HOST_DIR:-../content}`     | `/app/content`      | Bind mount   | Category/item images and audio. `frontend` mounts it read-only.        |
| `../archive`                          | `/app/archive`      | Bind mount   | Archived (removed) categories.                                        |
| `../backend/config`                   | `/app/config`       | Bind mount   | TTS/translation/optimizer/logging provider configs — edit on the host, no rebuild needed. |
| `backend-data` (named volume)         | `/app/data`         | Named volume | SQLite user database (`users.db`).                                    |
| `tts-output` (named volume)           | `/app/tts-output`   | Named volume | Pipeline output (raw + optimized audio, reports).                     |
| `/tmp` (host)                         | `/tmp`              | Bind mount   | `backend-cli` only — hosts the `kiddo-backend.sock` Unix socket the CLI connects to. |

Named volumes (`backend-data`, `tts-output`) persist across `docker compose down` but not
`docker compose down -v`.

## Profiles

- **`cli` profile** — `docker compose --profile cli up -d backend-cli` starts a second backend
  instance listening on a Unix socket at `/tmp/kiddo-backend.sock` (bind-mounted from the host),
  for CLI subcommands that strictly require socket auth (`users`, `list users`).
- **`cli-tool` profile** — `docker compose --profile cli-tool run --rm cli <command>` runs
  `kiddo-cli` itself in a container against the `backend` service over HTTP
  (`KIDDO_API_BASE_URL=http://backend:8000`). Commands that require the Unix socket aren't
  available this way — use `backend-cli` (or the host-installed CLI) for those.

```bash
docker compose -f docker/docker-compose.yml --profile cli-tool run --rm cli content categories
```

## Frontend routing

`nginx.conf` serves the built SPA and proxies `/api/`, `/internal/`, and `/content/` to
`http://backend:8000` (the `backend` service's in-network address) — the browser only ever talks
to the frontend's port 9001.

## Troubleshooting

**"Add image" (or any content write) fails only in Docker, not when running the backend locally.**
This is almost always the content mount, not the request:

1. **Content root missing or empty.** Check `CONTENT_HOST_DIR` (or the `../content` default)
   actually points at your real content directory *on the host running Docker*, not a path from
   someone else's machine or `.env`. If the source directory doesn't exist, Docker silently
   creates an empty one and mounts that — categories that exist on your host filesystem won't be
   visible inside the container. `backend` now reports this explicitly (e.g. *"Content root
   '/app/content' does not exist... verify the content directory is mounted as a volume"*) instead
   of a misleading "category not found".
2. **Permission denied writing to the content directory.** A UID/GID mismatch between the host
   and the container is the usual cause — the mount is present and correct, but the container's
   user can't write into it. The backend now reports this as *"Permission denied writing... check
   the volume mount permissions and that the host and container users/UIDs match"*.
3. **Env vars not reaching the container.** Anything that worked via a local shell/`.env` when
   running the backend directly (`CONTENT_DIR`, provider API keys, etc.) must be passed into the
   container explicitly — via `environment:` in `docker-compose.yml` (already done for the
   variables above) or your own `.env` at the repo root. A value that's silently missing tends to
   resolve to a wrong default path rather than fail loudly.

**Rebuild after Python/JS dependency changes** — `docker compose up` alone won't pick up a
changed `requirements.txt`/`package.json`; use `--build` (as in the Quick start commands above).
