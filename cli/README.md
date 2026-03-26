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

The CLI is organized into sub-apps: **common**, **content**, **list**, **translate**, **users**, and **generate**.
Commands that call the backend accept optional `--url` (backend base URL) and `--token` (bearer token). `common ping` uses no auth.
Use global `--verbose` (or `-v`) before subcommands to show HTTP/debug messages from the client.
Run `kiddo-cli --help`, `kiddo-cli common --help`, `kiddo-cli content --help`, `kiddo-cli list --help`, `kiddo-cli translate --help`, `kiddo-cli users --help`, or `kiddo-cli generate --help` for details.

### Common (backend routes_common)

| Command                                                                      | Description |
|------------------------------------------------------------------------------|-------------|
| [`common ping`](cli/src/kiddo_cli/commands/common_commands.py#L14)           | Ping the backend (expected response PONG; no auth). |
| [`common status`](cli/src/kiddo_cli/commands/common_commands.py#L21)         | Check service status. |
| [`common operation <name>`](cli/src/kiddo_cli/commands/common_commands.py#L34) | Perform a service operation. |
| [`common list-files`](cli/src/kiddo_cli/commands/common_commands.py#L48)       | List files in the configured directory (`--depth`, default 1). |

### Content (backend routes_content)

| Command | Description |
|---------|-------------|
| [`content categories`](cli/src/kiddo_cli/commands/content_commands.py#L16) | List content categories. Options: `--include-archived`, `--archived-only`. |
| [`content create-category --name <n>`](cli/src/kiddo_cli/commands/content_commands.py#L35) | Create a category (folder, voices per language, icon). |
| [`content rename-category <old> --to <new>`](cli/src/kiddo_cli/commands/content_commands.py#L48) | Rename a category (folder + regenerate voices). |
| [`content remove-category <name>`](cli/src/kiddo_cli/commands/content_commands.py#L75) | Remove a category (archive if `ARCHIVE_DIR` is set, else delete permanently). |
| [`content restore-category <name>`](cli/src/kiddo_cli/commands/content_commands.py#L62) | Restore a category from archive (name or archive folder like `foods_1738...`). |
| [`content category-details <name>`](cli/src/kiddo_cli/commands/content_commands.py#L88) | Show category details. |
| [`content create-object`](cli/src/kiddo_cli/commands/content_commands.py#L101) | Create a new content object (image). |

### Generate (backend voice generation via HTTP)

| Command | Description |
|---------|-------------|
| [`generate run INPUT_FILE`](cli/src/kiddo_cli/commands/generate_commands.py#L17) | Run the TTS pipeline (translate, generate, evaluate). |

### List (backend internal voice routes)

| Command | Description |
|---------|-------------|
| [`list generators`](cli/src/kiddo_cli/commands/list_commands.py#L13) | Show configured generators with activation states. |
| [`list translators`](cli/src/kiddo_cli/commands/list_commands.py#L26) | Show configured translators with priority and states. |
| [`list optimizers`](cli/src/kiddo_cli/commands/list_commands.py#L39) | Show configured optimizers with activation states. |
| [`list evaluators`](cli/src/kiddo_cli/commands/list_commands.py#L52) | Show configured evaluators with activation states. |
| `list services` | Show all configured internal services (translators, generators, optimizers, evaluators) with colorized output. |
| [`list users`](cli/src/kiddo_cli/commands/list_commands.py#L64) | Show all users. |

### Translate (backend internal translation routes)

| Command | Description |
|---------|-------------|
| [`translate word <input_lang> <output_lang> <word>`](cli/src/kiddo_cli/commands/translate_commands.py#L16) | Translate a word/short text using positional arguments. |
| `translate word --json '<payload>'` | Translate with JSON input. Accepts inline JSON or `@file.json`. |
| [`translate file <input_file>`](cli/src/kiddo_cli/commands/translate_commands.py#L53) | Translate entries from `.txt`, `.csv`, or `.xlsx` file. Options: `--input-lang`, `--output-lang`, `--category`. |

### Users (backend user management)

| Command | Description |
|---------|-------------|
| [`users list`](cli/src/kiddo_cli/commands/users_commands.py#L12) | Show configured users. Options: `--username` (fuzzy), `--role`. |
| [`users create <user> <pass>`](cli/src/kiddo_cli/commands/users_commands.py#L32) | Create a new user. Option: `--role`. |
| [`users delete <user>`](cli/src/kiddo_cli/commands/users_commands.py#L52) | Delete a user by username. |
| [`users update <user>`](cli/src/kiddo_cli/commands/users_commands.py#L65) | Update user password or role. Options: `--password`, `--role`. |

Options for `generate run`:

- `-l`, `--lang` -- Default language code (default: en)
- `-f`, `--output-format` -- Output format: mp3, wav, pcm, opus (default: mp3)
- `--max-concurrency` -- Parallel provider requests (default: 5)
- `--url` -- Backend base URL
- `--token` -- Bearer token (else UDS auth)

## Usage examples

```bash
kiddo-cli --help
kiddo-cli --verbose list services
kiddo-cli common --help
kiddo-cli content --help
kiddo-cli list --help
kiddo-cli translate --help
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
kiddo-cli list services
kiddo-cli list services --category translators
kiddo-cli list services --enabled --name openai

# Translate
kiddo-cli translate word en es "apple"
kiddo-cli translate word --json '{"text":"banana","input_lang_code":"en","target_lang_code":"fr"}'
kiddo-cli translate word --json @translate_payload.json
kiddo-cli translate file words.csv --input-lang en --output-lang bg
kiddo-cli translate file words.xlsx --output-lang es --category foods

# Users
kiddo-cli users list
kiddo-cli users list --username ali --role admin
kiddo-cli users create new_user secret_pass --role viewer
kiddo-cli users update new_user --password new_secret --role admin
kiddo-cli users delete old_user
```
