# Logging service reorganization (revised)

## Scope (unchanged from prior agreement)

- **Application/service structured logs** go through internal HTTP (`logging_routes.py`); bootstrap and uvicorn may stay on plain stdlib logging.
- Move implementation out of `src/core/logging` into `src/services/logging`; remove `core/logging` when done.
- `models.py` + `service.py` layout aligned with `text_translation`; internal routes + HTTP callers for non-core boundaries.

## New iteration: service_provider config + single `struct_log` provider

### Config layout

- **Replace** the current multi-file layout under [`config/loggers/`](config/loggers/) (`logging.json`, `structlog.json`, `request_logger.json` as separate top-level configs merged by directory scan) with **`service_provider` JSON files** loaded like other services.
- **Directory**: use [`config/logging/`](config/logging/) (new) with `config_subdir="logging"` on the logging service, so naming matches `SERVICE_NAME` and avoids mixing old “flat logger JSON” semantics with provider envelopes. **Remove or stop using** the old `config/loggers/` tree once migrated (delete after cutover, or leave a README pointer).
- **Single provider type**: exactly one implementation module [`src/services/logging/providers/struct_log.py`](src/services/logging/providers/struct_log.py) (plus [`providers/__init__.py`](src/services/logging/providers/__init__.py) to register on import), registered in a **`LoggingProviderFactory`** (`ServiceProviderFactory` subclass) under the key **`struct_log`** (matches `service_provider.provider` in JSON).

### Example config shape (one file, e.g. `config/logging/default.json`)

Envelope matches [`config/text_translation/openai_en_bg.json`](config/text_translation/openai_en_bg.json):

- `service`: `"logging"`
- `provider`: `"struct_log"`
- `enabled` / `priority` as usual (only one provider expected; priority is still valid for future extension).

**Inner `config` object** should carry **everything needed for “complex” structlog + project sinks**, in the spirit of today’s [`config/loggers/logging.json`](config/loggers/logging.json):

1. **`logging`** (optional key): a **stdlib `dictConfig` blob** (same structure as current `logging.json`: `version`, `formatters`, `handlers`, `root`, optional `loggers`, `active` on handlers, `${output_dir}` placeholders). The `struct_log` provider applies this via `logging.config.dictConfig` after path normalization (reuse/move logic from current `LoggingSystem._prepare_logging_config`).

2. **`structlog`** (optional key): **structlog-specific options**, e.g. `renderer` (`console` | `json`), and a declarative list of **processor names** (strings) that `struct_log.py` maps to real `structlog.processors` / `structlog.stdlib` pieces—mirroring the current chain in [`logging_system.py`](src/core/logging/logging_system.py) (`merge_contextvars`, `filter_by_level`, `add_log_level`, `TimeStamper`, `StackInfoRenderer`, `format_exc_info`, renderer). Keep the mapping table in code; JSON only selects names and simple params (e.g. timestamp format).

3. **`request_log`** (optional key): former [`request_logger.json`](config/loggers/request_logger.json) content: `path`, `fields` toggles. The provider applies these when wiring the **request** channel (logger name should follow the earlier plan stem rule, e.g. `request_logger`, unless you standardize on a single name in README).

**Goal:** one **service_provider** file is the single source of truth; no separate `structlog.json` / `request_logger.json` / raw `logging.json` in `config/loggers/`.

### Provider class (`struct_log.py`)

- Subclass the logging service’s **provider base** (new, analogous to `TextTranslationServiceProvider`: holds `config`, `config_path`, `enabled`, etc.).
- **Responsibilities:**
  - Parse the merged `config` dict.
  - Run stdlib `dictConfig` when `config["logging"]` is present (with existing path/Seq/active-handler behavior moved here or shared helpers in `logging_system.py`).
  - Call `structlog.configure(...)` from `config["structlog"]` + code-side processor registry.
  - Call `configure_request_logger(...)` (or inline equivalent) from `config["request_log"]`.
- **Registration:** `StructLogProvider.register()` → `LoggingProviderFactory.register("struct_log", StructLogProvider)` following the TTS pattern ([`openai_tts.py`](src/services/text_to_speech/providers/openai_tts.py)).

### Registry + startup order

- Export **`SERVICE_CLASS`**, **`SERVICE_NAME` = `"logging"`**, **`SERVICE_ACCEPT_CRITERIA`** from [`src/services/logging/__init__.py`](src/services/logging/__init__.py) with `config_subdir="logging"`, `validate_provider` / `build_provider` wired to `LoggingProviderFactory`.
- **Lifespan:** build registry → `load_all_from_disk()` → then **`LoggingService.apply_config()`** (or equivalent) so the active `struct_log` provider configures structlog + stdlib + request log. If no logging config exists, skip or no-op (stdlib defaults).
- **Remove** standalone `LoggingSystem` construction from **directory merge of `config/loggers/`** in [`main.py`](src/main.py); configuration is driven by the **logging service’s provider JSON** instead.

### Rest of plan (unchanged intent)

- [`models.py`](src/services/logging/models.py): Pydantic models for `/internal/logging/emit` (`logger_name`, `level`, `event`, `fields`, etc.).
- [`logging_routes.py`](src/api/internal/logging_routes.py): internal routes; handlers call `LoggingService` in-process.
- **HTTP clients** from `core` and other services for structured emits; thin `core` helper, no `LoggingService` import in `core`.
- **README:** document `config/logging/*.json`, `struct_log` provider, processor/renderer options, migration from `config/loggers/`.
- **Pylint** on touched Python files.

## Implementation todos (merged)

1. Add `config/logging/` service_provider JSON (single default file) consolidating logging + structlog + request_log sections; remove legacy `config/loggers/` after migration.
2. Implement `providers/struct_log.py`, provider base + `LoggingProviderFactory`, wire `SERVICE_*` and registry acceptance.
3. Refactor `LoggingSystem` / startup: apply config via `LoggingService` + struct_log provider; delete `src/core/logging`.
4. Add `models.py`, expand `LoggingService`, `logging_routes.py`, wire `app.state`, migrate callers to HTTP for structured logs.
5. README + pylint.
