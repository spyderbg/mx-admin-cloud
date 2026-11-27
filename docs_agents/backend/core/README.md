# Backend core (`backend/src/core/`)

> Owner role: **backend** · no HTTP endpoints of its own · evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified

## Purpose

Shared infrastructure used by the API layer and the services: settings, core dataclasses, language helpers, input loading, report writing, the job store, the logging HTTP client, and the request-tracing route class. The intended rule (`backend/CLAUDE.md`) is that `core/` has **zero** `src.services` imports, so that pipeline steps cross service boundaries over HTTP only.

## Core modules

| Module | Responsibility | Notes |
| --- | --- | --- |
| [`config.py`](../../../backend/src/core/config.py) | `Settings` (Pydantic Settings) + `settings` singleton; resolved path properties (`config_path`, `content_path`, `archive_path`, `output_path`, `temp_path`); output directory helpers | Resolves one env file at import (`resolve_env_file`, see [Env file resolution](#env-file-resolution)) and passes it to both `load_dotenv(..., override=False)` and `Settings.model_config["env_file"]`. **Insecure defaults:** `DEBUG_MODE=True`, `AUTH_DISABLED=True`, placeholder `SECRET_KEY`, hard-coded default admin password (discovery SEC-1..4) |
| [`languages.py`](../../../backend/src/core/languages.py), [`localization.py`](../../../backend/src/core/localization.py) | `to_bcp47`, `normalize_language_code`, `language_name`, `LANG_MAP`; `LocalizedString` | |
| [`pipeline.py`](../../../backend/src/core/pipeline.py) | Async TTS pipeline orchestration, its run records (`RunSummary`, `ProviderRunRecord`) and `write_run_summary` (per-run JSON/CSV reports). Service value types live in the service (`GeneratedAudio` → `text_to_speech/models.py`, also used by evaluation and voice_optimization; `GeneratedImage` → `text_to_image/models.py`; `EvaluationResult`, `ProviderAggregate`, `ScoreRecord` → `evaluation/models.py`; `TranslationSourceWord` → `text_translation/models.py`) | **Disabled:** `run_pipeline` and its helpers are commented out **[S]**; only the run records and `write_run_summary` are live, and nothing calls them. This is why `POST /internal/generate` fails (discovery F-1) |
| [`job_store.py`](../../../backend/src/core/job_store.py) | In-memory async job store with TTL eviction | Single-process only (comment in `main.py`) |
| [`log_client.py`](../../../backend/src/core/log_client.py) | HTTP client for the logging service: `configure`, `shutdown`, `emit_event`, `log_event`, `emit_event_nowait`, `emit`, `log`, `log_api_request`, `log_service_request`, `log_provider_request`, `log_payment_request` | See the [logging service](../services/logging/README.md) |
| [`logging_route.py`](../../../backend/src/core/logging_route.py) | `LoggingAPIRoute.for_source(LOG_SOURCE, …)` route class: traces requests and reports endpoint failures | |
| [`tasks.py`](../../../backend/src/core/tasks.py) | `ping`, `status` (masks keys in `_SENSITIVE_KEYS` and `*_API_KEY`), `operation`, `list_files` | |
| [`utils.py`](../../../backend/src/core/utils.py) | `read_json_object`, `boolish`, platform checks, `is_debug_mode`, `is_production` | |

## Env file resolution

`config.resolve_env_file()` picks exactly one file (Issue #88) **[V]**; none of the steps depend on the current working directory:

1. `MX_ADMIN_ENV_FILE`, if set and non-empty (`~` is expanded). If the file is missing, `MissingEnvFileError` (a `FileNotFoundError`) names the variable and path and tells the user to create the file or unset the variable. There is no fallback, and file contents are never included.
2. `${XDG_CONFIG_HOME:-$HOME/.config}/mx-admin-cloud/app.env`, if it exists. A relative `XDG_CONFIG_HOME` is ignored, as the XDG specification requires.
3. The legacy repository-root `.env`, if it exists. The root is `Path(__file__).resolve().parents[3]`, so it is the main checkout or the feature worktree that holds the code.
4. Otherwise no file is used.

Precedence: process environment, then the env file, then the `Settings` defaults. `load_dotenv` runs with `override=False`, and Pydantic gives environment variables priority over `env_file`.

## Verified boundary deviation

`core/` imports from `src.services.logging` **[V]**:

- `core/log_client.py` imports the wire contract (`LogLevel` and the `Log*Request` models) from the `src.services.logging` facade, plus the route path constants from `src.services.logging.routes`.
- `core/logging_route.py` imports `LogLevel` from the `src.services.logging` facade.

The wire contract is part of the logging facade (`backend/CLAUDE.md`, "Service and provider encapsulation"). **The `routes` import is the one documented exception** (`ALLOWED_DEEP_IMPORTS` in `src/__tests__/test_architecture.py`): re-exporting the paths from the facade would pull FastAPI router code and dependencies into every logging import. No other `src.services` imports exist in `core/` **[V]**. This confirms part of `backend/code_improvements.md` §2.1. No change was made in Phase 1.

## Testing

| Command | Scope | Baseline |
| --- | --- | --- |
| `cd backend && ../.venv/bin/python -m pytest -q src/core` | `test_log_client.py` (7), `test_logging_route.py` (5), `test_tasks.py` (1) | 13 passed in the 919-test baseline **[V]** |

`test_config_env_file.py` covers the env file resolution. It uses only a temporary `HOME`, `XDG_CONFIG_HOME` and repository root, with synthetic values. The rest of the settings, the input loader, pipeline, job store and language helpers have no dedicated tests.
