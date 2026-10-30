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

## Test stack (`docker-compose.test.yml`)

A second, fully isolated stack for the sandbox test environment (Playwright E2E, the
provider stub) — see `docs/test_environment/implementation_plan.md` for the design and
`test_environment/README.md` for day-to-day commands. Everything below is driven
through `python test_environment/testenv.py` (or its `test_environment/testenv.sh` wrapper); you should
not normally invoke `docker compose` directly, but the underlying invocation is:

```bash
docker compose \
  -f docker/docker-compose.yml \
  -f docker/docker-compose.test.yml \
  --project-directory docker \
  --env-file .testenv/test.env \
  -p mxadmin-test up -d
```

**Never edit `docker-compose.yml` to support the test stack.** `docker-compose.test.yml`
is a pure override — it renames containers (`mxtest-backend`/`mxtest-frontend`/
`mxtest-stub`, vs. the dev stack's `kiddo-*`), replaces the published ports (needs
**Compose ≥ 2.24** for the `ports: !override` tag — without it, ports *concatenate*
instead of replacing, and the test stack silently double-publishes on the dev stack's
9000/9001 too), and repoints the content/archive/config bind mounts at `.testenv/`. The
dev and test stacks can run side by side without colliding.

**Two run modes, same `.testenv/` tree.** Docker is not required — `python
test_environment/testenv.py` auto-detects whether a Docker daemon answers and falls back to
**native mode** (`test_environment/app.py`: stub + `uvicorn` + a built `frontend/dist/` served by
a small stdlib proxy) when it doesn't. Both modes share the identical seeded fixture,
neutralised config, and stub — only how backend/frontend/stub run differs. Native mode's
one hard rule: it never runs `npm run dev`, because `frontend/vite.config.ts` serves
`/content` from a **hardcoded `../content`** via `sirv` and would silently expose the
real content directory regardless of `CONTENT_DIR`; `app.py` always builds
`frontend/dist/` once and serves that instead.

**Never symlink anything under `.testenv/`.** Every file there is a real copy
(`test_environment/testenv_seed.py`, `test_environment/testenv_config.py`), asserted after every seed —
the suite includes destructive specs (archive, delete item, delete image) that would
follow a symlink straight into tracked, real content or `backend/config/`.

Stub container: stock `python:3.11-slim` with `test_environment/stub-provider/`
bind-mounted read-only — no Dockerfile, no image to rebuild.
