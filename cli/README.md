# Kiddo CLI

CLI client for the Kiddo content management backend. All commands call the FastAPI backend over HTTP. Auth uses `--token`/`KIDDO_TOKEN` when provided; otherwise it tries Unix socket login first and falls back to HTTP login.

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

- **Automatic (default):** The CLI resolves auth from `KIDDO_TOKEN` first; if not set, it tries Unix socket login (`/tmp/kiddo-backend.sock`) using `KIDDO_USER` / `KIDDO_PASSWORD` (default `admin` / `admin`), then falls back to HTTP login with the same credentials.
- **Manual token:** Set `KIDDO_TOKEN` or use `--token`.

Env:

- `KIDDO_CLI_SOCKET` -- socket path (default `/tmp/kiddo-backend.sock`)
- `KIDDO_USER` / `KIDDO_PASSWORD` -- credentials for socket login (default `admin` / `admin`)
- `BACKEND_URL` -- API base URL (default `http://localhost:8000`)
- `KIDDO_TOKEN` -- bearer token (if set, no socket login)

## Commands

The CLI is organized into sub-apps: **common**, **content**, **list**, and **generate**.
Commands that call the backend accept optional `--url` (backend base URL) and `--token` (bearer token). `common ping` uses no auth.
Run `kiddo-cli --help`, `kiddo-cli common --help`, `kiddo-cli content --help`, `kiddo-cli list --help`, or `kiddo-cli generate --help` for details.

### Common (backend routes_tasks)

| Command                                                                      | Description |
|------------------------------------------------------------------------------|-------------|
| [`common ping`](cli/src/kiddo_cli/commands/common_commands.py#L14)           | Ping the backend (expected response PONG; no auth). |
| [`common status`](cli/src/kiddo_cli/commands/common_commands.py#L21)         | Check service status. |
| [`common operation <name>`](./src/kiddo_cli/commands/common_commands.py#L34) | Perform a service operation. |
| [`common list-files`](./src/kiddo_cli/commands/common_commands.py#L48)       | List files in the configured directory (`--depth`, default 1). |

### Content (backend routes_content)

| Command | Description |
|---------|-------------|
| [`content categories`](./src/kiddo_cli/commands/content_commands.py#L16) | List content categories. Options: `--include-archived`, `--archived-only`. |
| [`content create-category --name <n>`](./src/kiddo_cli/commands/content_commands.py#L35) | Create a category (folder, voices per language, icon). |
| [`content rename-category <old> --to <new>`](./src/kiddo_cli/commands/content_commands.py#L48) | Rename a category (folder + regenerate voices). |
| [`content remove-category <name>`](./src/kiddo_cli/commands/content_commands.py#L75) | Remove a category (archive if `ARCHIVE_DIR` is set, else delete permanently). |
| [`content restore-category <name>`](./src/kiddo_cli/commands/content_commands.py#L62) | Restore a category from archive (name or archive folder like `foods_1738...`). |
| [`content category-details <name>`](./src/kiddo_cli/commands/content_commands.py#L88) | Show category details. |
| [`content create-object`](./src/kiddo_cli/commands/content_commands.py#L101) | Create a new content object (image). |

### Generate (backend voice generation via HTTP)

| Command | Description |
|---------|-------------|
| [`generate run INPUT_FILE`](./src/kiddo_cli/commands/generate_commands.py#L17) | Run TTS pipeline (translate, generate, evaluate). |

### List (backend internal voice routes)

| Command | Description |
|---------|-------------|
| [`list generators`](./src/kiddo_cli/commands/list_commands.py#L13) | Show configured generators with activation states. |
| [`list translators`](./src/kiddo_cli/commands/list_commands.py#L26) | Show configured translators with priority and states. |
| [`list optimizers`](./src/kiddo_cli/commands/list_commands.py#L39) | Show configured optimizers with activation states. |
| [`list evaluators`](./src/kiddo_cli/commands/list_commands.py#L52) | Show configured evaluators with activation states. |

Options for `generate run`:

- `-l`, `--lang` -- Default language code (default: en)
- `-f`, `--output-format` -- Output format: mp3, wav, pcm, opus (default: mp3)
- `--max-concurrency` -- Parallel provider requests (default: 5)
- `--url` -- Backend base URL
- `--token` -- Bearer token (else UDS auth)

## Usage examples

```bash
kiddo-cli --help
kiddo-cli common --help
kiddo-cli content --help
kiddo-cli list --help
kiddo-cli generate --help

# Common
kiddo-cli common ping
kiddo-cli common status
kiddo-cli common operation refresh
kiddo-cli common list-files
kiddo-cli common list-files --depth 2

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

# List
kiddo-cli list generators
kiddo-cli list translators
kiddo-cli list optimizers
kiddo-cli list evaluators
```
