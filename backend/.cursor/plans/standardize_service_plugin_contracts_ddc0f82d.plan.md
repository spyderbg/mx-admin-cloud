---
name: Standardize service plugin contracts
overview: Standardize all three services (TTS, translation, optimization) around a uniform `ConfigModel.from_dict` + `Factory.create` contract, simplify `ServicePlugin` to remove repetitive callbacks, fix TTS tuple storage, and extract duplicated utilities.
todos:
  - id: boolish
    content: Extract shared `boolish` utility to `src/core/utils.py` and replace all copies
    status: pending
  - id: from-dict-tts
    content: Add `enabled` field and `from_dict` classmethod to `ProviderConfig`
    status: pending
  - id: from-dict-translation
    content: Add `from_dict` classmethod to `TranslatorConfig`
    status: pending
  - id: from-dict-optimization
    content: Add `from_dict` classmethod to `OptimizerConfig` (include default config merge)
    status: pending
  - id: generic-loader
    content: Create generic `load_service_configs()` in `src/services/config_loader.py`
    status: pending
  - id: plugin-redesign
    content: Redesign `ServicePlugin` dataclass + hybrid `service_key` + update `ServicePluginRegistry.register`
    status: pending
  - id: registry-builder
    content: Simplify `registry_builder._load_into` to use generic loader and uniform `factory_create` + `cfg.enabled` filter
    status: pending
  - id: tts-tuple-fix
    content: Store plain TTS provider instances in registry; update route code to drop tuple unpacking
    status: pending
  - id: init-cleanup
    content: Remove `_*_load` / `_*_build` boilerplate from all three service `__init__.py` files
    status: pending
  - id: config-loader-cleanup
    content: Remove or simplify per-service `config_loader.py` files
    status: pending
  - id: tests-update
    content: Update unit and integration tests for new config loader API and registry shape
    status: pending
  - id: readme
    content: Update README to reflect new plugin registration contract
    status: pending
isProject: false
---

# Standardize Service Plugin Contracts

## Current problems

- `_boolish` is duplicated 4+ times across config loaders and factories.
- Three config loaders (`text_to_speech/config_loader.py`, `text_translation/config_loader.py`, `optimization/config_loader.py`) share the same JSON-envelope-parsing boilerplate.
- `ServicePlugin.load_configs` and `ServicePlugin.build_instances` are manually wired per service in each `__init__.py` via near-identical `_*_load` / `_*_build` wrapper functions.
- TTS stores `(provider, cfg)` tuples in the registry, unlike translation and optimization which store plain instances. The tuple is redundant since `provider.cfg` holds the same config.
- `ProviderConfig` lacks an `enabled` field, so TTS active-filtering diverges from the other two services.

---

## Phase 1 -- Foundation utilities

### 1a. Extract shared `_boolish`

Move to a new helper or to `src/core/utils.py` (which already exists). Replace all copies in:

- `text_to_speech/config_loader.py`
- `text_to_speech/base.py`
- `text_to_speech/__init__.py` (`ProviderFactory._boolish`)
- `text_translation/config_loader.py`
- `optimization/config_loader.py`

### 1b. Add `from_dict` to all three config models

Each takes the raw `service_provider` dict + `config_path` and returns a validated config instance, absorbing the parsing logic currently in per-service config loaders.

`**ProviderConfig.from_dict**` ([text_to_speech/models.py](backend/src/services/text_to_speech/models.py)):

- Extracts `name`, `version`, `generate`, `evaluate` from the service_provider envelope.
- Adds a new `enabled: bool` field (derived from `generate["enabled"]`).
- Absorbs logic currently in `load_provider_configs` (lines 47-76 of config_loader.py).

`**TranslatorConfig.from_dict**` ([text_translation/models.py](backend/src/services/text_translation/models.py)):

- Extracts `service_type`, `name`, `version`, `enabled`, `priority`, `config`.
- Absorbs logic from `load_translator_configs` (lines 51-72 of config_loader.py).

`**OptimizerConfig.from_dict**` ([optimization/models.py](backend/src/services/optimization/models.py)):

- Extracts `service_type`, `name`, `version`, `enabled`, `priority`, `config`.
- Absorbs default config merging (`_OPTIMIZER_DEFAULT_CONFIG`) currently in `load_optimizer_configs` (lines 50-65 of config_loader.py).

---

## Phase 2 -- Generic config loader

Create a shared config-loading function (in `src/services/config_loader.py` or `src/core/config_loader.py`):

```python
def load_service_configs(
    config_dir: Path,
    config_class: type[T],
) -> list[T]:
    """Read all JSON files in *config_dir*, call config_class.from_dict() on each."""
```

This replaces the three per-service config loaders. The per-service `config_loader.py` files can either:

- Be removed entirely (if no service-specific pre/post-processing remains).
- Become thin wrappers that call the generic loader with the correct subdirectory and class.

The `list_generators` / `list_translators` / `list_optimizers` endpoints that currently import per-service loaders directly will be updated to use the generic loader or the thin wrappers.

---

## Phase 3 -- Simplify `ServicePlugin` and `registry_builder`

### 3a. New `ServicePlugin` shape ([plugin_registry.py](backend/src/services/plugin_registry.py))

```python
@dataclass(frozen=True)
class ServicePlugin:
    registry_attr: str                    # e.g. "providers"
    config_dir: str                       # subfolder name, e.g. "text_to_speech"
    config_class: type                    # has from_dict(data, config_path) classmethod
    factory_create: Callable              # Factory.create(cfg, base_dir) -> instance
    base_dir_key: str = "output_path"     # settings attribute for base_dir
    service_key: str | None = None        # auto-derived from module name if None
```

- `load_configs` is gone -- the generic config loader + `config_class.from_dict` replaces it.
- `build_instances` is gone -- the registry builder calls `factory_create` per config.
- `service_key` becomes optional (hybrid model: auto-derived from `__name__`, explicit override allowed).

### 3b. Simplified `_load_into` ([registry_builder.py](backend/src/services/registry_builder.py))

```python
async def _load_into(registry, settings):
    for plugin in ServicePluginRegistry.all_plugins():
        config_path = settings.config_path / plugin.config_dir
        configs = load_service_configs(config_path, plugin.config_class)
        base_dir = getattr(settings, plugin.base_dir_key)
        instances = [
            plugin.factory_create(cfg, base_dir)
            for cfg in configs if cfg.enabled
        ]
        registry.set_service(plugin.registry_attr, instances)
```

---

## Phase 4 -- Fix TTS tuple storage

### 4a. `ProviderConfig` gets `enabled` field

Add `enabled: bool` to `ProviderConfig` (set in `from_dict` from `generate["enabled"]`). The base `ServiceProvider.is_active()` (which checks `cfg.enabled`) then works uniformly for TTS too. Remove the `is_active` override in `TTSProvider`.

### 4b. Store plain provider instances

The registry "providers" slot changes from `list[(TTSProvider, ProviderConfig)]` to `list[TTSProvider]`.

### 4c. Update route code

In [text_to_speech_routes.py](backend/src/api/internal/text_to_speech_routes.py):

- Line 86: `matched = [p for p in registry.get("providers") if p.name == provider]` (drop tuple unpacking)
- Line 89: `prov = matched[0]` (no `_cfg`)
- Line 145-149: `prov = providers[0]` (no tuple)

---

## Phase 5 -- Clean up `__init__.py` boilerplate

Each service `__init__.py` currently has ~15 lines of boilerplate (`_*_load`, `_*_build`, `ServicePluginRegistry.register(...)`). After the changes, registration becomes:

```python
ServicePluginRegistry.register(
    ServicePlugin(
        registry_attr="providers",
        config_dir="text_to_speech",
        config_class=ProviderConfig,
        factory_create=ProviderFactory.create,
    ),
    module_name=__name__,
)
```

Files affected:

- [text_to_speech/**init**.py](backend/src/services/text_to_speech/__init__.py) -- remove `_tts_load`, `_tts_build`
- [text_translation/**init**.py](backend/src/services/text_translation/__init__.py) -- remove `_tt_load`, `_tt_build`
- [optimization/**init**.py](backend/src/services/optimization/__init__.py) -- remove `_opt_load`, `_opt_build`

---

## Phase 6 -- Tests and README

- Update unit tests that call per-service config loaders directly (e.g. `test_text_to_speech.py` references `load_provider_configs`).
- Update integration tests that construct `ServiceRegistry` and populate it with tuples.
- Update `README.md` to reflect the new plugin registration contract.

---

## Files changed (summary)


| File                                             | Change                                            |
| ------------------------------------------------ | ------------------------------------------------- |
| `src/core/utils.py`                              | Add shared `boolish()`                            |
| `src/services/text_to_speech/models.py`          | Add `enabled` field + `from_dict` classmethod     |
| `src/services/text_translation/models.py`        | Add `from_dict` classmethod                       |
| `src/services/optimization/models.py`            | Add `from_dict` classmethod + move default config |
| `src/services/config_loader.py` (new)            | Generic `load_service_configs()`                  |
| `src/services/plugin_registry.py`                | Redesign `ServicePlugin` + hybrid `service_key`   |
| `src/services/registry_builder.py`               | Simplified `_load_into` using generic loader      |
| `src/services/text_to_speech/__init__.py`        | Remove boilerplate, slim registration             |
| `src/services/text_to_speech/base.py`            | Remove `is_active` override, use shared `boolish` |
| `src/services/text_to_speech/config_loader.py`   | Remove or thin wrapper                            |
| `src/services/text_translation/__init__.py`      | Remove boilerplate, slim registration             |
| `src/services/text_translation/config_loader.py` | Remove or thin wrapper                            |
| `src/services/optimization/__init__.py`          | Remove boilerplate, slim registration             |
| `src/services/optimization/config_loader.py`     | Remove or thin wrapper                            |
| `src/api/internal/text_to_speech_routes.py`      | Drop tuple unpacking                              |
| `backend/README.md`                              | Update plugin registration docs                   |
| Tests                                            | Update config loader calls + tuple references     |


