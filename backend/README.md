# Kiddo Backend

FastAPI backend for Kiddo content management. All API routes are under `/api/v1`. Protected endpoints require a Bearer token (obtained via `/login` or `/cli-login`). Internal service routes are under `/internal/voice/*`.

## Create .venv
```bash
python3.10 -m venv .venv
```

```batch
py -3.10 -m venv .venv
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

## Auth

- **HTTP login:** `POST /api/v1/login` with `application/x-www-form-urlencoded` body `username` and `password` (default `admin` / `admin`). Returns `access_token`; use as `Authorization: Bearer <token>`.
- **CLI login:** `POST /api/v1/cli-login` is allowed only when the request is made over the backend's Unix socket (e.g. by the CLI). Returns a short-lived token.
- **Internal dual-auth:** Internal endpoints accept either `X-Internal-Token` header (service-to-service) or `Authorization: Bearer <jwt>` with admin role (CLI).

Env (see `src/core/config.py`):

- `SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `CLI_TOKEN_EXPIRE_SECONDS` -- JWT settings
- `AUTH_DISABLED` -- if true, auth checks are skipped
- `CONTENT_DIR` -- content data path
- `ARCHIVE_DIR` -- directory for archived (removed) categories. If empty, remove deletes categories permanently instead of archiving.
- `OPENAI_API_KEY` -- required for TTS, translation, and category icon generation (create/rename category, create object).
- `ELEVENLABS_API_KEY` -- ElevenLabs TTS provider (optional)
- `SPEAKTOR_API_KEY` -- Speaktor TTS provider (optional)
- `INTERNAL_SERVICE_TOKEN` -- shared secret for service-to-service internal API calls
- `CONFIG_DIR` -- path to TTS config directory (default `config`)
- `OUTPUT_DIR` -- path to TTS output directory (default `../output`)
- `TTS_MAX_CONCURRENCY` -- max parallel provider requests (default 5)
- `LOG_PROFILE` -- logging profile: `dev` or `prod` (default `dev`)

## Endpoints

Base URL for all routes below: `/api/v1`. Protected routes require `Authorization: Bearer <token>`.

### Auth

| Method | Path         | Auth | Description |
|--------|--------------|------|-------------|
| `POST` | `/login`     | No   | Login with username/password; returns JWT. |
| `POST` | `/cli-login` | No   | CLI-only login over Unix socket; returns short-lived JWT. |

### Tasks

| Method | Path           | Auth | Description |
|--------|----------------|------|-------------|
| `GET`  | `/ping`        | No   | Health check; returns PONG. |
| `GET`  | `/status`      | Yes  | Service status. |
| `POST` | `/operation`   | Yes  | Perform a service operation. Query param: `name`. |
| `POST` | `/list-files`  | Yes  | List files in the configured directory. Body: `{"depth": int}` (depth < 1 treated as 1). |

### Content

| Method | Path                             | Auth | Description |
|--------|----------------------------------|------|-------------|
| `GET`  | `/content/categories`            | Yes  | List content categories. Query: `include_archived`, `archived_only` (bool) to include or list only archived categories. |
| `POST` | `/content/categories`            | Yes  | Create a category. Body: `{"name": "<category_name>"}`. Creates folder, voice files per language, and category icon. Returns 201. |
| `POST` | `/content/categories/restore`    | Yes  | Restore a category from archive. Body: `{"name": "<category_name_or_archive_folder>"}` (e.g. `foods` or `foods_1738456789`). |
| `GET`  | `/content/categories/{name}`     | Yes  | Category details by name. |
| `PATCH`| `/content/categories/{name}`     | Yes  | Rename category. Body: `{"name": "<new_name>"}`. Renames folder and regenerates category voice files. |
| `DELETE`| `/content/categories/{name}`    | Yes  | Remove category. If `ARCHIVE_DIR` is set, archives it (date suffix); otherwise deletes permanently. |
| `POST` | `/content/objects`              | Yes  | Create a content object. Multipart: `image` (file), `category` (form), `object` (form, English word). Returns 201. |

### Internal (voice generation)

Internal endpoints are under `/internal/voice/*`. They require dual-auth: `X-Internal-Token` header or admin JWT.

| Method | Path                                    | Description |
|--------|-----------------------------------------|-------------|
| `POST` | `/internal/voice/generate`              | Upload input file, trigger TTS pipeline in background; returns `job_id`. |
| `GET`  | `/internal/voice/generate/{job_id}/status` | Get job status and result when complete. |
| `POST` | `/internal/voice/generate-sync`         | Upload input file, run pipeline synchronously; returns `RunSummary` JSON. |
| `GET`  | `/internal/voice/generators`            | List configured generators with activation states. |
| `GET`  | `/internal/voice/translators`           | List configured translators with priority and states. |

**Generate request (multipart form):**

- `file` -- input file (.txt, .csv, or .xlsx). The backend parses it using `input_loader` which handles CSV headers, XLSX language columns, etc.
- `language` -- default language code (default: `en`)
- `output_format` -- audio format: mp3, wav, pcm, opus (default: `mp3`)
- `max_concurrency` -- parallel provider requests (default: `5`)

## TTS config

TTS providers and translators are configured via JSON files in `config/`:

```
config/
|-- logging.json
|-- generators/
|   |-- openai.json
|   |-- elevenlabs.json
|   `-- speaktor.json
`-- translators/
    |-- openai.json
    `-- static.json
```

Set `generate.active` to `"yes"` or `"no"` in each generator config to enable/disable providers.

## Example requests

```bash
# Health check (no auth)
curl http://localhost:8000/api/v1/ping

# Login and use token
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/login -d "username=admin&password=admin" | jq -r .access_token)
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

# List generators (internal endpoint, admin JWT)
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/internal/voice/generators

# Run TTS pipeline synchronously (upload file, internal endpoint)
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -F "file=@words.txt" -F "language=bg" -F "output_format=mp3" \
  http://localhost:8000/internal/voice/generate-sync
```
