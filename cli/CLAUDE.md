# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in the cli directory.

## Project Overview

**mx-admin** is a content management and voice synthesis platform for the Kiddo language learning app. It manages multilingual categories/images and orchestrates a TTS pipeline across multiple providers.

Three components: **FastAPI backend**, **Vue 3 frontend SPA**, **Typer CLI client**.

## Commands

```bash
# From project root
./scripts/cli.sh install     # pip install -e cli/
./scripts/cli.sh uninstall
kiddo-cli --help
```

---

## Architecture

### CLI (`src/kiddo_cli/`)

- Pure HTTP client — no backend code shared
- Subcommands: `common`, `content`, `generate`, `list`, `translate`, `users`
- Auth priority: `--token` option → `KIDDO_TOKEN` env var → Unix socket login → HTTP login (`KIDDO_API_BASE_URL` + `KIDDO_USER`/`KIDDO_PASSWORD`)

### Authentication

- **CLI** → Unix socket (`/tmp/kiddo-backend.sock` or `KIDDO_CLI_SOCKET`) → short-lived JWT (30 sec) → subsequent HTTP calls
- **Alternative** → HTTP login → long-lived JWT (30 min) → `Authorization: Bearer <token>`
- **User Management**: Strictly requires Unix socket (UDS) on Linux/macOS for `users` commands.
- **User roles**: `admin` (full access), `viewer` (read-only)
