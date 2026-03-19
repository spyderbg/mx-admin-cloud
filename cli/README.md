# Kiddo CLI

CLI client for the Kiddo content management backend. All commands call the FastAPI backend over HTTP. Auth is done automatically via the backend's Unix socket (CLI-only login) unless you pass a token.

## Install as terminal command `kiddo-cli`

From the project root:

```bash
./scripts/cli.sh install
```

Or: `pip install -e cli/`

## Uninstall

```bash
./scripts/cli.sh uninstall
```

Or: `pip uninstall kiddo-cli`

## Auth

- **Automatic (default):** The CLI logs in over the backend's Unix socket (`/tmp/kiddo-backend.sock`) using `KIDDO_USER` / `KIDDO_PASSWORD` (default `admin` / `admin`). Start the backend with `./uvicorn_start.sh` so both the HTTP server and the CLI socket are running.
- **Manual token:** Set `KIDDO_TOKEN` or use `--token` to skip socket login.

Env:

- `KIDDO_CLI_SOCKET` -- socket path (default `/tmp/kiddo-backend.sock`)
- `KIDDO_USER` / `KIDDO_PASSWORD` -- credentials for socket login (default `admin` / `admin`)
- `BACKEND_URL` -- API base URL (default `http://localhost:8000`)
- `KIDDO_TOKEN` -- bearer token (if set, no socket login)

## Commands

The CLI is organized into sub-apps: **tasks**, **content**, and **generate**. Commands that call the backend accept optional `--url` (backend base URL) and `--token` (bearer token). `tasks ping` uses no auth. Run `kiddo-cli --help`, `kiddo-cli tasks --help`, `kiddo-cli content --help`, or `kiddo-cli generate --help` for details.

### Tasks (backend routes_tasks)

| Command                    | Description                                                       |
|----------------------------|-------------------------------------------------------------------|
| `tasks ping`               | Ping the backend (expected response PONG; no auth)                |
| `tasks status`             | Check service status                                             |
| `tasks operation <name>`   | Perform a service operation                                      |
| `tasks list-files`         | List files in the configured directory (`--depth`, default 1)    |

### Content (backend routes_content)

| Command                              | Description                                                                 |
|--------------------------------------|-----------------------------------------------------------------------------|
| `content categories`                 | List content categories. Options: `--include-archived`, `--archived-only`  |
| `content create-category --name <n>`  | Create a category (folder, voices per language, icon)                       |
| `content rename-category <old> --to <new>` | Rename a category (folder + regenerate voices)                        |
| `content remove-category <name>`     | Remove a category (archive if ARCHIVE_DIR set, else delete permanently)    |
| `content restore-category <name>`   | Restore a category from archive (name or archive folder e.g. foods_1738...)  |
| `content category-details <name>`    | Show category details                                                      |
| `content create-object`              | Create a new content object (image)                                        |

### Generate (backend voice generation via HTTP)

| Command                                | Description                                              |
|----------------------------------------|----------------------------------------------------------|
| `generate run INPUT_FILE`              | Run TTS pipeline (translate, generate, evaluate)         |
| `generate list-generators`             | Show configured generators with activation states        |
| `generate list-translators`            | Show configured translators with priority and states     |

Options for `generate run`:

- `-l`, `--lang` -- Default language code (default: en)
- `-f`, `--output-format` -- Output format: mp3, wav, pcm, opus (default: mp3)
- `--max-concurrency` -- Parallel provider requests (default: 5)
- `--url` -- Backend base URL
- `--token` -- Bearer token (else UDS auth)

## Usage examples

```bash
kiddo-cli --help
kiddo-cli tasks --help
kiddo-cli content --help
kiddo-cli generate --help

# Tasks
kiddo-cli tasks ping
kiddo-cli tasks status
kiddo-cli tasks operation refresh
kiddo-cli tasks list-files
kiddo-cli tasks list-files --depth 2

# Content
kiddo-cli content categories
kiddo-cli content categories --include-archived
kiddo-cli content categories --archived-only
kiddo-cli content create-category --name animals
kiddo-cli content rename-category animals --to creatures
kiddo-cli content remove-category creatures
kiddo-cli content restore-category creatures
kiddo-cli content category-details foods
kiddo-cli content create-object --image ./pic.png --category foods --object "apple pie"

# Generate
kiddo-cli generate run -l bg -f mp3 words.txt
kiddo-cli generate run -l fr -f wav words.csv
kiddo-cli generate list-generators
kiddo-cli generate list-translators
```
