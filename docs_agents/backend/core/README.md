# Backend core (`backend/src/core/`)

> Owner role: **backend** · no HTTP endpoints of its own · evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified

## Purpose

Shared infrastructure used by the API layer and the services: settings, core dataclasses, language helpers, input loading, report writing, the job store, the logging HTTP client, and the request-tracing route class. The intended rule (`backend/CLAUDE.md`) is that `core/` has **zero** `src.services` imports, so that pipeline steps cross service boundaries over HTTP only.

## Core modules

| Module | Responsibility | Notes |
| --- | --- | --- |
| [`config.py`](../../../backend/src/core/config.py) | `Settings` (Pydantic Settings) + `settings` singleton; resolved path properties (`config_path`, `content_path`, `archive_path`, `output_path`, `temp_path`); output directory helpers | Calls `load_dotenv()` at import, so the repo-root `.env` is found by walking up from this file. **Insecure defaults:** `DEBUG_MODE=True`, `AUTH_DISABLED=True`, placeholder `SECRET_KEY`, hard-coded default admin password (discovery SEC-1..4) |
| [`models.py`](../../../backend/src/core/models.py) | Core dataclasses: `GeneratedAudio`, `GeneratedImage`, `EvaluationResult`, `ProviderAggregate`, `ScoreRecord`, … | |
| [`languages.py`](../../../backend/src/core/languages.py), [`localization.py`](../../../backend/src/core/localization.py) | `to_bcp47`, `normalize_language_code`, `language_name`, `LANG_MAP`; `LocalizedString` | |
| [`input_loader.py`](../../../backend/src/core/input_loader.py) | `load_words()` for CSV/XLSX/TXT | Used by `translate-file` |
| [`result_writer.py`](../../../backend/src/core/result_writer.py) | Per-run JSON/CSV reports | |
| [`pipeline.py`](../../../backend/src/core/pipeline.py) | Async TTS pipeline orchestration | **Disabled:** 377 of 384 non-blank lines are commented out **[V]**, which is why `POST /internal/generate` fails (discovery F-1) |
| [`job_store.py`](../../../backend/src/core/job_store.py) | In-memory async job store with TTL eviction | Single-process only (comment in `main.py`) |
| [`log_client.py`](../../../backend/src/core/log_client.py) | HTTP client for the logging service: `configure`, `shutdown`, `emit_event`, `log_event`, `emit_event_nowait`, `emit`, `log`, `log_api_request`, `log_service_request`, `log_provider_request`, `log_payment_request` | See the [logging service](../services/logging/README.md) |
| [`logging_route.py`](../../../backend/src/core/logging_route.py) | `LoggingAPIRoute.for_source(LOG_SOURCE, …)` route class: traces requests and reports endpoint failures | |
| [`tasks.py`](../../../backend/src/core/tasks.py) | `ping`, `status` (masks keys in `_SENSITIVE_KEYS` and `*_API_KEY`), `operation`, `list_files` | |
| [`utils.py`](../../../backend/src/core/utils.py) | `read_json_object`, `boolish`, platform checks, `is_debug_mode`, `is_production` | |

## Verified boundary deviation

`core/` imports from `src.services.logging` **[V]**:

- `core/log_client.py` imports route path constants from `src.services.logging.routes`, plus `models.LogLevel` and the `schemas` request models.
- `core/logging_route.py` imports `src.services.logging.models.LogLevel`.

`backend/CLAUDE.md` allows importing logging `schemas`/`models` as the wire contract. **Importing `routes` goes beyond that exception**, because `routes` pulls in FastAPI router code and dependencies. No other `src.services` imports exist in `core/` **[V]**. This confirms part of `backend/code_improvements.md` §2.1. No change was made in Phase 1.

## Testing

| Command | Scope | Baseline |
| --- | --- | --- |
| `cd backend && ../.venv/bin/python -m pytest -q src/core` | `test_log_client.py` (7), `test_logging_route.py` (5), `test_tasks.py` (1) | 13 passed in the 919-test baseline **[V]** |

The settings, input loader, pipeline, job store and language helpers have no dedicated tests.
