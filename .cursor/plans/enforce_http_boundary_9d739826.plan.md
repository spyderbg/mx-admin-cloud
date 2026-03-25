---
name: Enforce HTTP Boundary
overview: Eliminate all `src.services` imports from `backend/src/core/` by moving shared infrastructure into `core/`, extracting registry factory logic, creating 4 HTTP endpoints, and rewriting `pipeline.py` and `tts.py` to use HTTP calls instead of direct service imports.
todos:
  - id: a-move-models
    content: "A1: Move services/models.py -> core/models.py, leave re-export shim, update ~12 absolute imports"
    status: in_progress
  - id: a-move-output-paths
    content: "A2: Move services/output_paths.py -> core/output_paths.py, leave re-export shim, update imports"
    status: pending
  - id: a-move-languages
    content: "A3: Move services/languages.py -> core/languages.py, leave re-export shim, update imports"
    status: pending
  - id: a-move-logging
    content: "A4: Move services/logging/ -> core/logging/, update internal import, leave re-export shim, update main.py + 2 services files"
    status: pending
  - id: a-move-input-loader
    content: "A5: Move services/generation/input_loader.py -> core/input_loader.py, update its imports, leave shim"
    status: pending
  - id: a-move-result-writer
    content: "A6: Move services/generation/result_writer.py -> core/result_writer.py, update its imports, leave shim"
    status: pending
  - id: b-registry-split
    content: "B: Strip core/registry.py to pure container, create services/registry_builder.py with factory logic, update main.py lifespan"
    status: pending
  - id: c-endpoint-generate
    content: "C1: Add POST /internal/generators/{provider}/generate endpoint (generate + normalize)"
    status: pending
  - id: c-endpoint-evaluate
    content: "C2: Add POST /internal/evaluators/evaluate endpoint (STT + score storage)"
    status: pending
  - id: c-endpoint-optimize
    content: "C3: Add POST /internal/optimizers/{optimizer}/optimize endpoint"
    status: pending
  - id: c-endpoint-generate-word
    content: "C4: Add POST /internal/generators/generate-word endpoint for single-word TTS"
    status: pending
  - id: d-rewrite-pipeline
    content: "D: Rewrite core/pipeline.py to use core/ imports + HTTP endpoints instead of direct service calls"
    status: pending
  - id: e-rewrite-tts
    content: "E: Rewrite core/tts.py to call /internal/generators/generate-word via HTTP (translation.py pattern)"
    status: pending
  - id: f-verify
    content: "F: Verify zero src.services imports in core/, run pylint, update README if needed"
    status: pending
isProject: false
---

# Enforce HTTP Boundary in core/ Files

## Context

Four `core/` files import from `src.services`, violating the [http-boundary rule](.cursor/rules/http-boundary.mdc). The goal is zero `from src.services` imports in `core/`.

**Violating files:**

- `core/pipeline.py` -- 8 service imports (logging, models, optimization, output_paths, evaluation, generation)
- `core/registry.py` -- 4 service imports (models, optimization, providers, translation)
- `core/tts.py` -- 5 service imports (config_loader, providers, models, languages, output_paths)
- `core/config_loader.py` -- 1 service import (models)

## Part A -- Move 6 shared modules to `core/`

These are data contracts, static lookups, path math, and cross-cutting concerns -- not business logic. After moving, both `core/` and `services/` can import them from `core/`.

### A1. `services/models.py` -> `core/models.py`

- Copy [services/models.py](backend/src/services/models.py) to `core/models.py`
- Replace `services/models.py` contents with a re-export shim: `from src.core.models import` *
- Update absolute imports in 4 core files + 6 services files that use `from src.services.models`:
  - `core/config_loader.py`, `core/pipeline.py`, `core/registry.py`, `core/tts.py`
  - `services/generation/audio_normalizer.py`, `services/generation/input_loader.py`
  - `services/evaluation/engine.py`, `services/optimization/base.py`
  - `services/optimization/__init__.py`, `services/translation/base.py`, `services/translation/__init__.py`
  - `tests/test_optimization.py`
- Relative imports (`from ..models`) in services/ sub-packages stay unchanged -- the shim makes them work

### A2. `services/output_paths.py` -> `core/output_paths.py`

- Copy to `core/output_paths.py`, update its import from `from .models import ...` to `from src.core.models import ...` (since relative `..models` won't work from core/)
- Replace `services/output_paths.py` with re-export shim
- Update absolute imports:
  - `services/generation/audio_normalizer.py` (`src.services.output_paths` -> `src.core.output_paths`)
  - `services/generation/input_loader.py`
  - `tests/test_optimization.py`
- Relative imports (`from ..output_paths`) in services/ stay unchanged via shim

### A3. `services/languages.py` -> `core/languages.py`

- Copy to `core/languages.py` (no internal deps)
- Replace `services/languages.py` with re-export shim
- Update absolute imports:
  - `services/translation/base.py` (`src.services.languages` -> `src.core.languages`)
  - `services/evaluation/engine.py`
  - `services/generation/input_loader.py`

### A4. `services/logging/` -> `core/logging/`

- Copy the 3-file package to `core/logging/`
- In `core/logging/request_logger.py`: change `from ..output_paths import requests_log_path` to `from src.core.output_paths import requests_log_path`
- Replace `services/logging/__init__.py` with re-export shim (re-export from `src.core.logging`)
- Keep `services/logging/logging_system.py` and `services/logging/request_logger.py` as stubs importing from core equivalents (so `from ..logging` relative imports in services/ still work)
- Update absolute imports:
  - `services/evaluation/engine.py` (`src.services.logging` -> `src.core.logging`)
  - `services/translation/openai_translate.py`
  - `main.py` (`from src.services.logging import LoggingSystem` -> `from src.core.logging import LoggingSystem`)

### A5. `services/generation/input_loader.py` -> `core/input_loader.py`

- Copy to `core/input_loader.py`
- Update its imports: `src.services.languages` -> `src.core.languages`, `src.services.models` -> `src.core.models`, `src.services.output_paths` -> `src.core.output_paths`
- Replace `services/generation/input_loader.py` with shim

### A6. `services/generation/result_writer.py` -> `core/result_writer.py`

- Copy to `core/result_writer.py`
- Update its imports: `from ..models` -> `from src.core.models`, `from ..output_paths` -> `from src.core.output_paths`
- Replace `services/generation/result_writer.py` with shim

---

## Part B -- Extract registry builder

[core/registry.py](backend/src/core/registry.py) imports `build_provider`, `build_translators`, `build_active_optimizers` from services to construct live instances at startup (before HTTP is available).

### B1. Strip `core/registry.py` to pure container

- Remove `_load()` method and its service imports
- Keep class with typed fields, `build()` becomes a simple constructor that receives populated data
- Only import from `src.core.models` (for `ProviderConfig` type hint)

### B2. Create `services/registry_builder.py`

- Extract factory logic from `_load()`:

```python
from src.core.config_loader import load_provider_configs, load_translator_configs, load_optimizer_configs
from src.core.registry import ServiceRegistry
from src.services.providers import build_provider
from src.services.translation import build_translators
from src.services.optimization import build_active_optimizers

async def build_registry(base_dir, config_dir, output_dir) -> ServiceRegistry: ...
async def reload_registry(registry, base_dir, config_dir, output_dir) -> None: ...
```

### B3. Update `main.py` lifespan

- Import `build_registry`/`reload_registry` from `src.services.registry_builder`
- Replace `ServiceRegistry.build(...)` with `build_registry(...)`
- Replace `registry.reload()` with `reload_registry(registry, ...)`

---

## Part C -- Create 4 HTTP endpoints

New internal endpoints wrap the service business logic that `pipeline.py` and `tts.py` need.

### C1. `POST /internal/generators/{provider}/generate` in [routes_generators.py](backend/src/api/internal/routes_generators.py)

- Request: `GenerateRequest(text, language_code, language_name, filename_text, expected_text, source_file, source_type, output_format, row_index, column_index)`
- Handler: gets registry, finds provider, constructs `TTSRequest`, calls `provider.generate()` + `normalize_audio()`, returns JSON result

### C2. `POST /internal/evaluators/evaluate` in [routes_evaluators.py](backend/src/api/internal/routes_evaluators.py)

- Request: `EvaluateRequest(provider, request_id, expected_text, language_code, raw_path, normalized_path, normalization_error, filename_text, evaluate_config, output_dir)`
- Handler: creates `EvaluationEngine` + `MultiScoreStore`, calls `engine.evaluate()` + `store.save()`, returns evaluation result

### C3. `POST /internal/optimizers/{optimizer}/optimize` in [routes_optimizers.py](backend/src/api/internal/routes_optimizers.py)

- Request: `OptimizeRequest(raw_path, output_path)`
- Handler: gets registry, finds optimizer, calls `optimizer.optimize()`, returns success/error

### C4. `POST /internal/generators/generate-word` in [routes_generators.py](backend/src/api/internal/routes_generators.py)

- Request: `GenerateWordRequest(word, lang)`
- Handler: picks first active provider, calls `provider.generate()`, returns raw audio bytes

---

## Part D -- Rewrite `core/pipeline.py`

After Parts A-C:

- Replace all `src.services` imports with `src.core` imports (models, output_paths, logging, input_loader, result_writer -- all now in core)
- Replace direct `provider.generate()` + `normalize_audio()` calls with HTTP POST to `/internal/generators/{provider}/generate`
- Replace direct `evaluation_engine.evaluate()` + `score_store.save()` with HTTP POST to `/internal/evaluators/evaluate`
- Replace direct `optimizer.optimize()` calls with HTTP POST to `/internal/optimizers/{optimizer}/optimize`
- Registry is still passed but only used for reading names/configs (e.g., `registry.provider_names_and_configs()`, `registry.optimizer_names()`)
- Uses `internal_token` (already available) for HTTP auth headers

---

## Part E -- Rewrite `core/tts.py`

Follow the [core/translation.py](backend/src/core/translation.py) pattern:

```python
def generate_audio(word: str, lang: str) -> bytes:
    token = create_internal_token("tts-service", timedelta(seconds=30))
    response = httpx.post(
        f"{settings.API_BASE_URL}/internal/generators/generate-word",
        json={"word": word, "lang": lang},
        headers={"Authorization": f"Bearer {token}"},
        timeout=60.0,
    )
    response.raise_for_status()
    return response.content
```

Zero `src.services` imports. Fixes the broken `from src.services.config_loader` import.

---

## Part F -- Verify and clean up

- Confirm zero `from src.services` imports in `backend/src/core/**/*.py`
- Run `pylint backend/src/` to catch stale imports
- Update README/CLAUDE.md if any user-facing contracts changed
- Ensure re-export shims don't break tests

