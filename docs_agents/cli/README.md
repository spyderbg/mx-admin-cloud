# `kiddo-cli` — User Guide

> Owner role: **cli** (default runner: Claude Code) · User documentation only. The backend HTTP contract is documented by Backend under [`docs_agents/backend/`](../backend/README.md), and the internal API-to-CLI capability matrix is kept by Coordination in [`../coordination/cli-coverage.md`](../coordination/cli-coverage.md).
> Pre-existing CLI docs (preserved): [`cli/README.md`](../../cli/README.md), [`cli/CLAUDE.md`](../../cli/CLAUDE.md) · Verified against `kiddo-cli` 0.1.1 at commit `e2ddd9c` by introspecting the installed Typer app **[V]**

`kiddo-cli` is a command-line client for the mx-admin backend. Every command calls the backend **over HTTP** (or the backend's local Unix socket). It never imports backend code. It lets you browse and edit content categories, manage provider configurations, translate words, generate a voice for a word, send log records, and manage users.

## Installation

```bash
./scripts/cli.sh install        # pip install -e cli/ (from the repository root)
kiddo-cli --help
```

Requires Python ≥ 3.10. Dependencies: Typer 0.22.x, click < 8.2, requests, httpx, rich (`cli/pyproject.toml`).

## Connecting to the backend

| Setting | How to set it | Default | Used for |
| --- | --- | --- | --- |
| Backend URL for commands | `--url <base-url>` on a command, or the `BACKEND_URL` environment variable | `http://localhost:8000` | Every API call |
| Backend URL for HTTP login | `KIDDO_API_BASE_URL` | `http://localhost:8000` | Only the automatic HTTP login (see below) |
| Unix socket | `KIDDO_CLI_SOCKET` | `/tmp/kiddo-backend.sock` | Socket login and user management |
| Verbose output | `kiddo-cli -v …` / `--verbose` | off | Prints each HTTP request |

> **Note:** `--url` does **not** change where the automatic HTTP login goes; set `KIDDO_API_BASE_URL` too when the backend is not on `localhost:8000` **[S]**.

## Signing in

Most commands accept `--token <JWT>`. Without one, the CLI obtains a token automatically, in this order:

1. `--token` on the command line;
2. the `KIDDO_TOKEN` environment variable;
3. if the Unix socket exists (Linux/macOS): **socket login** with `KIDDO_USER` / `KIDDO_PASSWORD`, which returns a short-lived token (30 s by default);
4. otherwise: **HTTP login** at `KIDDO_API_BASE_URL` with the same credentials (30 min token by default).

> **Caution [S]:**
> - When `KIDDO_TOKEN` is set, it is sent even if you also pass `--token`. Unset `KIDDO_TOKEN` to use a different token.
> - If `KIDDO_USER` / `KIDDO_PASSWORD` are not set, the CLI falls back to built-in default credentials. Always set your own.
> - `--verbose` prints the value of `KIDDO_TOKEN`. Do not use verbose mode in shared logs.

**Roles.**

- Read-only content commands work for any user, including `viewer`.
- Changing content requires `admin`.
- The `config`, `list` (except `list users`), `generate`, `translate` and `logging` commands call internal endpoints and require `admin`.
- `users` and `list users` require `admin` **and** the Unix socket: run them on the machine where the backend was started with `./backend/uvicorn_start.sh` (which creates the socket).

## Command reference

Commands print the backend's JSON response; `generate word` instead saves the audio file and prints its path. On a failure (connection error, HTTP error, authentication failure, invalid input) it prints an error message and exits with status **1** **[S]**. Run `kiddo-cli <group> <command> --help` for full option help. Most commands also take `--url` and `--token`, omitted from the tables below.

### `common` — status and utilities

| Command | Arguments / options | Purpose |
| --- | --- | --- |
| `ping` | — | Check that the backend answers |
| `status` | — | Service status and non-secret settings |
| `operation NAME` | — | Placeholder operation (admin) |
| `list-files` | `--depth N` (default 1) | List the content directory |

### `content` — categories and items

| Command | Arguments / options | Purpose |
| --- | --- | --- |
| `categories` | `--include-archived`, `--archived-only` | List categories |
| `category-details CATEGORY_NAME` | — | Show one category with its items |
| `create-category` | `--name NAME` (required) | Create a category (folder, voices, icon) |
| `rename-category OLD` | `--to NEW_ID` (required) | Change a category's id (names and description stay) |
| `remove-category NAME` | — | Archive (when the backend has `ARCHIVE_DIR`) or delete a category |
| `restore-category NAME` | — | Restore from the archive (name or archive folder such as `foods_1738456789`) |
| `create-item` | `--category ID_OR_NAME` and `--item WORD` (required); `--image PATH`, `--generate-voices`, `--generate-image` (not with `--image`) | Create an item; image generation may take minutes (12-minute client timeout) |

Item images and voices, category icons and voices, and item edits or deletion are **not available in the CLI yet**. Use the web UI.

### `config` — provider configurations (admin)

| Command | Arguments / options | Purpose |
| --- | --- | --- |
| `services` | — | Services that accept configuration files |
| `list SERVICE` | — | Configuration files of a service |
| `show SERVICE NAME` | — | Full JSON of one configuration |
| `set SERVICE NAME` | `--enabled/--disabled`, `--priority N`, `--version V` (empty removes it) | Change common fields |
| `patch SERVICE NAME` | `--json JSON_OR_@FILE` (required) | Merge-patch a configuration |
| `create SERVICE NAME` | `--json JSON_OR_@FILE` (required) | Create a configuration file |
| `delete SERVICE NAME` | — | Delete a configuration file |
| `enable SERVICE NAMES…` / `disable SERVICE NAMES…` | — | Toggle selected configurations |
| `enable-all SERVICE` / `disable-all SERVICE` | — | Toggle every configuration |
| `apply` | `--json JSON_OR_@FILE` (required) | Validate and apply a payload **in memory only** |
| `upload FILE_PATH` | — | Upload a JSON file and apply it **in memory only** |

Changes made with `set`, `patch`, `create`, `delete`, `enable*` and `disable*` are **saved on the server** and take effect immediately. Enabling a configuration may enable a **paid** external provider.

### `list` — what is configured

| Command | Options | Purpose |
| --- | --- | --- |
| `generators` | — | Text-to-speech configurations |
| `translators` | — | Translation configurations |
| `optimizers` | — | Voice optimizer configurations |
| `loggers` | `--all` | Loggers known to the backend's logging service (active ones only unless `--all`) |
| `services` | `--enabled/--disabled`, `--name`, `--category`, `--json` | Provider configurations of all services |
| `users` | — | Users (admin, Unix socket) |

The group help mentions "evaluators", but there is no `list evaluators` command.

### `translate`

| Command | Arguments / options | Purpose |
| --- | --- | --- |
| `word [INPUT_LANG] [OUTPUT_LANG] [WORD]` | `--json JSON_OR_@FILE` | Translate a word or short text |
| `file INPUT_FILE` | `--input-lang`, `--output-lang` (default `en`), `--category` | Translate the words of a TXT/CSV/XLSX file |

> **Known issue — currently broken [V]:** both commands call a backend path that does not exist and fail with an HTTP 404 error. They will work again after the CLI fix tracked as discovery finding F-3.

### `generate`

| Command | Arguments / options | Purpose |
| --- | --- | --- |
| `word WORD` | `-l/--lang` (default `en`), `-f/--output-format` (default `mp3`; only names the output file), `-o/--output PATH` (default `<WORD>.<format>`) | Generate the spoken audio of one word with the first active voice configuration |
| `run INPUT_FILE` | `-l/--lang`, `-f/--output-format`, `--max-concurrency` (default 5) | Run the full pipeline on a file |

> **Known issue — `generate run` is currently broken [V]:** the backend pipeline it relies on is disabled (discovery F-1/F-2).

### `logging` (admin)

| Command | Arguments / options | Purpose |
| --- | --- | --- |
| `emit [EVENT]` | `--level` (default `info`), `--fields JSON`, `--json JSON_OR_@FILE` | Send a record to the default logger |
| `log LOGGER_NAME [EVENT]` | same as `emit` | Send to a named logger |
| `api-request [EVENT]` · `provider-request` · `service [EVENT]` · `payment [EVENT]` | see `--help` | Send to the `requests`, `service` or `payment` logger |
| `reset` | — | Reset the logging providers |

`service` and `payment` currently return "not found", because the backend has no such loggers configured.

### `users` (admin, Unix socket only)

| Command | Arguments / options | Purpose |
| --- | --- | --- |
| `list` | `--username`, `--role admin\|viewer` | List users |
| `create USERNAME PASSWORD` | `--role` (default `viewer`) | Create a user |
| `update USERNAME` | `--password`, `--role` | Change password and/or role |
| `delete USERNAME` | — | Delete a user |

Passing a password as an argument stores it in your shell history. Prefer an interactive shell session, and clear the history afterwards.

## Examples

```bash
export KIDDO_USER=me KIDDO_PASSWORD='…'          # your own credentials
kiddo-cli common ping
kiddo-cli content categories --include-archived
kiddo-cli content create-item --category foods --item "apple" --generate-voices
kiddo-cli config list text_to_speech
kiddo-cli config set text_to_speech pyttsx3 --disabled
kiddo-cli generate word "apple" -l en -o apple.mp3
```

## Troubleshooting

| Message | Meaning / action |
| --- | --- |
| "User management only accepts requests via the CLI Unix socket" | Run on the backend host after `./backend/uvicorn_start.sh`, or remove a stale socket file |
| UDS / HTTP login failed | Check the backend is running, `KIDDO_CLI_SOCKET` / `KIDDO_API_BASE_URL`, and your credentials |
| HTTP 401 / 403 | Token expired (socket tokens last 30 s) or your role is not allowed |
| HTTP 404 on `translate` or `generate run` | Known issues above |
| HTTP 503 on `content` commands | No content provider is enabled on the server |

## Docker

`docker compose -f docker/docker-compose.yml --profile cli-tool run cli <args>` runs the CLI in a container. **[S]** The compose file sets only `KIDDO_API_BASE_URL=http://backend:8000`, not `BACKEND_URL`. Commands therefore target `localhost:8000` inside the container unless you pass `--url http://backend:8000`. This was not verified at runtime (no Docker on the verification host).
