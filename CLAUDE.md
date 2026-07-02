# CLAUDE.md

This file provides guidance to the AI assistant when working with the mx-admin project.

## Project Overview

**mx-admin** is a content management and voice synthesis platform for the Kiddo language learning app. It manages multilingual categories/images and orchestrates a TTS pipeline across multiple providers.

Three components: **FastAPI backend**, **Vue 3 frontend SPA**, **Typer CLI client**.

## Available Commands

Use `/` in the OpenCode TUI to run these project commands:

| Command | Description |
|---------|-------------|
| `/api-sync` | Export OpenAPI + regenerate TS client + schemas |
| `/verify` | Validate backend build + tests + frontend build |
| `/frontend` | Regenerate TS types + build frontend |
| `/mockdata` | Generate mock data from content directory |

## Workflow Instructions

When modifying backend APIs:
1. Update backend route/schema code
2. Run `/api-sync` (or manually: `export_openapi.py` → `generate_ts_client.py` → `generate_ts_schemas.py`)
3. Run `/frontend` to verify the frontend still builds

When fixing issues:
1. Run `/verify` to identify all problems
2. Fix grouped by area (backend/frontend/tests)
3. Run `/verify` again to confirm

## Project Structure

- `backend/` — FastAPI application
- `frontend/` — Vue 3 + TypeScript SPA
- `cli/` — Typer CLI client
- `scripts/` — Development and validation helper scripts
- `.opencode/commands/` — OpenCode project commands
