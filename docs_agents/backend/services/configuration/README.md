# `configuration` service

> Registered service · `SERVICE_NAME = "configuration"` · owner role: **backend** · HTTP spec: [`openapi.yaml`](openapi.yaml) (generated) · evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified — see [discovery](../../../discovery/README.md)

## Purpose and Usage

Runtime management of the provider configuration files of **every registered service**. It lists, reads, creates, merge-patches, replaces, deletes, enables and disables the JSON files under `backend/config/<config_subdir>/`, and reads or sets each service's provider selection strategy (`backend/config/_service_strategies.json`). Every persisted change reloads the service registry, so it takes effect in the running process **[S]**.

Two operations act **in memory only** and are not persisted: `POST /internal/configurations/apply` (JSON body) and `POST /internal/configurations/upload` (file). They validate a payload and swap it into the running registry through `ServiceRegistryBuilder.apply_runtime_payload` **[S]**.

Typical callers: the frontend Configuration pages, the CLI `config` group, and the content service (which reads the `voice_optimization` strategy over HTTP).

## Location and Dependencies

| Path | Role |
| --- | --- |
| [`backend/src/services/configuration/__init__.py`](../../../../backend/src/services/configuration/__init__.py) | `SERVICE_CLASS`, `SERVICE_NAME`, `SERVICE_ACCEPT_CRITERIA` (`config_subdir="configuration"`, `build_runtime_providers`) |
| [`service.py`](../../../../backend/src/services/configuration/service.py) | `ConfigurationService`, `ConfigurationProviderFactory`, `LOG_SOURCE = "configuration"` |
| [`models.py`](../../../../backend/src/services/configuration/models.py) | `ConfigurationProvider` contract, result dataclasses, error hierarchy |
| [`providers/file_system/`](../../../../backend/src/services/configuration/providers/file_system/) | `FileSystemConfigurationProvider` |
| [`merge.py`](../../../../backend/src/services/configuration/merge.py) | `deep_merge` (patch semantics), `prune_payload` (drops empty/default values) |
| [`routes.py`](../../../../backend/src/services/configuration/routes.py), [`schemas.py`](../../../../backend/src/services/configuration/schemas.py) | HTTP API and Pydantic models |

Depends on: `ServiceRegistryBuilder` (validation, reload, accept criteria — `services/service_registry.py`), `provider_strategy.STRATEGIES_FILE`, `core/log_client.emit_event_nowait`, `core/logging_route.LoggingAPIRoute`.

## Service Interface / ServiceProvider Contract

**Service:** `ConfigurationService(Service)` — `service.py:36`. `SUPPORTED_STRATEGIES = ()`, `DEFAULT_STRATEGY = None` (single provider). Public methods (all synchronous) **[V]**:

| Method | Returns |
| --- | --- |
| `list_configurations(service_name)` | `list[ConfigurationSummary]` |
| `get_configuration(service_name, config_name)` | `ConfigurationFileResult` |
| `add_configuration(service_name, config_name, payload)` / `remove_configuration(service_name, config_name)` | `ConfigurationMutationResult` |
| `patch_configuration(service_name, config_name, patch)` / `replace_configuration(service_name, config_name, payload)` | `ConfigurationMutationResult` |
| `enable_configurations(service_name, config_names)` / `disable_configurations(...)` | `list[ConfigurationMutationResult]` |
| `enable_all_configurations(service_name)` / `disable_all_configurations(service_name)` | `list[ConfigurationMutationResult]` |
| `get_service_provider_strategy(service_name)` / `set_service_provider_strategy(service_name, strategy)` | `ProviderStrategyInfo` |

**Provider contract:** `ConfigurationProvider(ServiceProvider, ABC)` — `models.py:84`, "Provider contract for CRUD and batch config operations". Abstract methods **[V]**: `list_configurations`, `get_configuration`, `add_configuration`, `remove_configuration`, `patch_configuration`, `replace_configuration`, `set_configurations_enabled(service_name, config_names, enabled)`, `set_all_configurations_enabled(service_name, enabled)`, `get_provider_strategy(service_name)`, `set_provider_strategy(service_name, strategy)`.

Result types (`models.py`): `ConfigurationSummary` (`config_name`, `filename`, `provider`, `enabled`, `priority`, `valid_json`, `error`, `version`), `ConfigurationFileResult` (`config_name`, `filename`, `payload`), `ConfigurationMutationResult` (`config_name`, `changed`, `error`), `ProviderStrategyInfo` (`service_name`, `strategy`, `supported_strategies`, `default_strategy`).

## Implemented Providers

| Class | Registered name | Path | Notes |
| --- | --- | --- | --- |
| `FileSystemConfigurationProvider` | `file_system` | `providers/file_system/provider.py:40` | **Runtime-wired**: built by `FileSystemConfigurationProvider.build_runtime(config_root, registry_builder)` through `build_runtime_providers`, because it needs the registry builder, which no config file can supply **[S]** |

Behavior **[S]**:

- Config names must match `^[A-Za-z0-9._-]+$` and must not contain traversal segments (`_validate_config_name`).
- Every write first validates the payload with `ServiceRegistryBuilder.validate_config_payload` (`_validate`).
- Patches use `deep_merge` followed by `prune_payload`.
- Files are written atomically: a temp file, then `replace()`.
- The registry is reloaded after each mutation (`_reload_registry`).

## Configured Provider Instances

There are none on disk. `backend/config/configuration/` does not exist **[V]**. At runtime the registry holds exactly one instance, named `configuration_file_system` (enabled, priority 1) **[V]**.

The file this service manages for strategies is [`backend/config/_service_strategies.json`](../../../../backend/config/_service_strategies.json). At baseline it contains only a `text_translation` entry **[V]**; every other service uses its default strategy.

## Selection and Lifecycle

- `ConfigurationService._select_provider()` picks the provider named `file_system`, falls back to the first loaded provider, and otherwise raises `ConfigurationProviderUnavailableError` **[S]**.
- The provider list is replaced on every registry load (start-up, `watchfiles` hot reload, explicit `_reload_registry`).

## HTTP API

16 operations, all under `/internal/configurations`, all requiring `require_internal` (role `internal` or `admin`) **[V]**. The full contract is in [`openapi.yaml`](openapi.yaml).

| Operations | Purpose |
| --- | --- |
| `GET /internal/configurations` | Services that accept configuration files |
| `GET /internal/configurations/{service_name}` · `POST …/{service_name}` | List files · create a file |
| `GET` / `PATCH` / `PUT` / `DELETE …/{service_name}/{config_name}` | Read · merge-patch · replace · delete one file |
| `POST …/{service_name}/enable-configurations` · `disable-configurations` · `enable-all-configurations` · `disable-all-configurations` | Toggle `service_provider.enabled` |
| `GET` / `PUT …/{service_name}/strategy` | Read / persist the provider selection strategy |
| `GET …/{service_name}/providers` | Provider types the service can instantiate |
| `POST /internal/configurations/apply` · `POST …/upload` | Validate and apply a payload **in memory only** |

## Errors, Observability and Security

- Error mapping in `routes.py` **[S]**:
  - `UnknownConfigurationServiceError` / `ConfigurationNotFoundError` → 404
  - `ConfigurationConflictError` → 409
  - `InvalidConfigurationNameError` / `ConfigurationValidationError` / invalid JSON → 422
  - `ConfigurationProviderUnavailableError` has no explicit mapping in `routes.py`, so it would surface as a 500 **[S]**.
- Observability: the router uses `LoggingAPIRoute.for_source("configuration")`, and the service emits events over HTTP with `emit_event_nowait` **[S]**.
- Security: these endpoints rewrite files that decide which **paid external providers** are enabled, so they stay internal/admin-only. They must never be exposed to viewers. Effective protection depends on `AUTH_DISABLED`/`DEBUG_MODE` (discovery SEC-1/SEC-2).

## Testing

| Command | Scope | Baseline |
| --- | --- | --- |
| `cd backend && ../.venv/bin/python -m pytest -q src/services/configuration` | `test_configuration_service.py` (56), `test_configuration_service_integration.py` (49) | 105 passed as part of the 919-test baseline **[V]** |

The integration tests set `AUTH_DISABLED=False` with `monkeypatch` to exercise the 401/403 paths.
