# `logging` service

> Registered service · `SERVICE_NAME = "logging"` · owner role: **backend** · HTTP spec: [`openapi.yaml`](openapi.yaml) (generated) · evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified — see [discovery](../../../discovery/README.md)

## Purpose and Usage

The central collector for structured log records. Other services and modules **never** call it in-process. They post records to its internal HTTP API through `backend/src/core/log_client.py`, because services may run on different hosts (rule in `backend/CLAUDE.md`). The service routes each record to logging providers: structlog-based console, file and Seq handlers, including a dedicated request log.

Usage from code (see [core](../../core/README.md#core-modules)): `await log_client.emit_event(LOG_SOURCE, "event", LogLevel.INFO, **fields)`, or `emit_event_nowait(...)` from synchronous code.

## Location and Dependencies

| Path | Role |
| --- | --- |
| [`backend/src/services/logging/__init__.py`](../../../../backend/src/services/logging/__init__.py) | Registration (`config_subdir="logging"`) |
| [`service.py`](../../../../backend/src/services/logging/service.py) | `LoggingService`, `LoggingProviderFactory` |
| [`models.py`](../../../../backend/src/services/logging/models.py) | `LogLevel`, `LogMessage`, `LoggingServiceProvider` contract |
| [`providers/struct_log/`](../../../../backend/src/services/logging/providers/struct_log/) | `StructLogProvider` |
| [`routes.py`](../../../../backend/src/services/logging/routes.py), [`schemas.py`](../../../../backend/src/services/logging/schemas.py) | HTTP API and wire contract (`LogRequest`, `LogApiRequest`, `LogServiceRequest`, `LogProviderRequest`, …) |

Depends on: `structlog`, the standard `logging` module, optionally `seqlog` (Seq handlers are disabled when the package or server is unavailable), and `provider_strategy.dispatch_each`. `backend/src/main.py` calls `LoggingService.configure(output_dir, profile)` at start-up. That call is the documented exception to the out-of-process rule.

## Service Interface / ServiceProvider Contract

**Service:** `LoggingService(Service)` — `service.py:23`; `SUPPORTED_STRATEGIES = (ALL_PROVIDERS, FIRST_SUCCESS)`, default `ALL_PROVIDERS`. Public methods (synchronous) **[V]**:

| Method | Meaning |
| --- | --- |
| `configure(*, output_dir, profile)` | Ask each active provider to apply its logging configuration |
| `reset()` → `list[str]` | Reset active providers; return affected files |
| `emit(msg: LogMessage)` | Send to active providers **without** `provider_category`, in priority order, using the strategy (`dispatch_each`); a failing provider never fails the call |
| `log(logger_name, msg)` | Send through the provider whose name is *logger_name* |
| `log_to_all_active_loggers(msg)` | Broadcast through every active provider |
| `find_provider(name)`, `get_logger(name)`, `get_active_loggers()`, `get_all_loggers()`, `list_logging_managers(*, include_all=False)` | Lookup and introspection |
| `bind_request_id(request_id)`, `clear_request_context()` | Request context on all active providers |

**Provider contract:** `LoggingServiceProvider(ServiceProvider, ABC)` — `models.py:34`. Abstract methods **[V]**: `configure(*, output_dir, profile)`, `reset()`, `get_logger(name)`, `log(logger_name, msg)`, `bind_request_id(request_id)`, `clear_request_context()`, `log_to_all_active_loggers(msg)`. Concrete helper: `apply_levels()`.

## Implemented Providers

| Class | Registered name | Path | Capabilities |
| --- | --- | --- | --- |
| `StructLogProvider` | `struct_log` | `providers/struct_log/provider.py:86` | Builds stdlib handlers from the config (console, file, rotating file, Seq), structlog rendering, request-log field filtering, reset of file handlers |

## Configured Provider Instances

| File | Provider | `provider_category` | Enabled | Priority | Config keys |
| --- | --- | --- | --- | --- | --- |
| [`default.json`](../../../../backend/config/logging/default.json) | `struct_log` | — | yes | 20 | `formatters`, `handlers` (`console`, `file`, `seq`), `structlog.renderer` |
| [`requests.json`](../../../../backend/config/logging/requests.json) | `struct_log` | `requests` | yes | 1 | `formatters`, `handlers.requests_file`, `request_log.fields` |
| [`trace.json`](../../../../backend/config/logging/trace.json) | `struct_log` | — | **no** | 10 | same shape as `default.json` |

All three live in `backend/config/logging/` **[V]**. Named loggers resolve to these file stems. `emit` reaches only `default.json` (and `trace.json` if enabled), because `requests.json` has a category.

## Selection and Lifecycle

- `emit`: active providers without a category, ascending priority, using `ALL_PROVIDERS` (default) or `FIRST_SUCCESS` from `_service_strategies.json`.
- Named endpoints bypass the strategy and go to one logger: `api-request` and `provider` → `requests`; `service` → `service`; `payment` → `payment`; `{logger_name}` → that name.
- **The `service` and `payment` configurations do not exist in `backend/config/logging/`, so those two endpoints answer 404 by design** **[S]**. The same applies to `trace` while it is disabled.
- Reconfiguration happens at start-up (`configure`). Provider objects are replaced on every registry reload.

## HTTP API

Eight internal operations under `/internal/loggers`, all `require_internal` **[V]**. Full contract: [`openapi.yaml`](openapi.yaml).

| Operation | Target |
| --- | --- |
| `GET /internal/loggers/` | List logging providers |
| `POST /internal/loggers/emit` | `LoggingService.emit` |
| `POST /internal/loggers/{logger_name}/log` | Named logger (404 when missing or disabled) |
| `POST /internal/loggers/logging/log/api-request` · `…/provider` | `requests` logger |
| `POST /internal/loggers/logging/log/service` · `…/payment` | `service` / `payment` loggers (not configured) |
| `POST /internal/loggers/reset` | `LoggingService.reset` |

The CLI `logging` group covers all eight.

## Errors, Observability and Security

- Unknown or disabled logger → 404. Provider failures in `emit` are swallowed by `dispatch_each`. On the client side, `emit_event`, `log_event` and `emit_event_nowait` swallow every failure; the lower-level calls swallow only `httpx.RequestError` (see `backend/CLAUDE.md`) **[S]**.
- Output: by default under `<OUTPUT_DIR>/results/logs/` and `<OUTPUT_DIR>/results/requests.log` (`core/config.py`); exact files depend on the handler config.
- Security:
  - The internal token or admin role is required.
  - Records may contain request metadata, so field filtering for the request log (`request_log.fields`) is configuration-driven.
  - Seq is a network destination configured in `default.json`.

## Testing

| Command | Scope | Baseline |
| --- | --- | --- |
| `cd backend && ../.venv/bin/python -m pytest -q src/services/logging src/core/__tests__/test_log_client.py src/core/__tests__/test_logging_route.py` | `test_logging_service.py` (11), `test_request_log_fields.py` (3); client/route tests in `core` (7 + 5) | Passed in the 919-test baseline **[V]** |
