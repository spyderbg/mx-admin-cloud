---
name: Evaluators list via registry
overview: Refactor `list_evaluators` to use `ServiceRegistry` and `TextToSpeechService.providers_snapshot()` like `list_translators` and `list_generators`, removing the direct `config_loader` disk read while preserving the same JSON fields for clients.
todos:
  - id: refactor-list-evaluators
    content: Refactor list_evaluators to use get_registry + TextToSpeechService.providers_snapshot(); remove config_loader
    status: completed
  - id: pylint-evaluators-routes
    content: Run pylint on evaluators_routes.py and fix any issues
    status: completed
isProject: false
---

# Align `list_evaluators` with registry-based listing

## Context

- [`text_translation_routes.py`](file:///home/plamen/_Projects/MarXman/mx-admin/backend/src/api/internal/text_translation_routes.py) lists services by: `Depends(get_registry)` → `registry.get_service(text_translation.SERVICE_NAME)` → type check → `service.providers_snapshot()` → sort by `priority` → build dicts.
- [`evaluators_routes.py`](file:///home/plamen/_Projects/MarXman/mx-admin/backend/src/api/internal/evaluators_routes.py) today calls [`load_service_configs`](file:///home/plamen/_Projects/MarXman/mx-admin/backend/src/services/config_loader.py) on `settings.config_path / "text_to_speech"` with `ServiceProvider`, duplicating what the registry already loaded.
- STT evaluators are **per TTS provider config** (the `evaluate` block under `service_provider.config`). [`TextToSpeechService.providers_snapshot()`](file:///home/plamen/_Projects/MarXman/mx-admin/backend/src/services/text_to_speech/service.py) returns `TTSProvider` instances (subclass of `ServiceProvider`) with the same `name`, `config`, and `config_path` as the old loader.
- There is an [`EvaluationService`](file:///home/plamen/_Projects/MarXman/mx-admin/backend/src/services/evaluation/service.py) with `providers_snapshot()`, but the evaluation package does **not** export `SERVICE_NAME` / `SERVICE_ACCEPT_CRITERIA`, so it is **not** registered in [`ServiceRegistry._discover`](file:///home/plamen/_Projects/MarXman/mx-admin/backend/src/services/service_registry.py). The correct source for this endpoint remains **`text_to_speech`**, matching [`list_generators`](file:///home/plamen/_Projects/MarXman/mx-admin/backend/src/api/internal/text_to_speech_routes.py) (which already exposes `evaluate_enabled` on the same configs).

## Implementation

1. **Update [`evaluators_routes.py`](file:///home/plamen/_Projects/MarXman/mx-admin/backend/src/api/internal/evaluators_routes.py)**
   - Add imports: `HTTPException`, `TextToSpeechService`, and `src.services.text_to_speech` (for `SERVICE_NAME`, same style as translation routes).
   - Change `list_evaluators` signature to inject `registry: ServiceRegistry = Depends(get_registry)`.
   - Body:
     - `service = registry.get_service(text_to_speech.SERVICE_NAME)` (or `"text_to_speech"` if you prefer mirroring generators literally).
     - `if not isinstance(service, TextToSpeechService): raise HTTPException(500, detail="...")`.
     - `configs = service.providers_snapshot()`.
     - `configs.sort(key=lambda c: c.priority)` (parity with translators).
     - Build the same response as today: `name`, `enabled` from `c.config.get("evaluate", {}).get("enabled", False)`, `config_path`.
   - Remove the inline `config_loader` import and the `ServiceProvider` import (only used for `load_service_configs` typing).

2. **Lint**
   - Run `pylint` on the touched module per project rules.

3. **README**
   - No change required unless you want a one-line clarification that `GET /internal/evaluators` reads from the in-memory registry (same source as generators). Optional; behavior and response shape stay the same for API clients.

## Notes

- **Response shape**: Keeping `name` / `enabled` (evaluate flag) / `config_path` avoids breaking consumers; the change is internal (registry vs re-reading JSON).
- **Consistency with `list_generators`**: Both endpoints will reflect the same loaded TTS providers; any future runtime config apply through `ServiceRegistryBuilder` will stay consistent automatically.
