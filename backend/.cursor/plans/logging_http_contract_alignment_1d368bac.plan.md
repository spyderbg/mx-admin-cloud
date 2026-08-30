---
name: Logging HTTP contract alignment
overview: Align `log_client.emit()` with `LogEmitRequest`, keep schemas in `src/api/internal/schemas/`, and relax layered rules so `services/` and `core/` may import those schema modules only—avoiding a duplicate model layer.
todos:
  - id: update-layer-rules
    content: "Amend backend-general.mdc: allow services/ and core/ to import from src.api.internal.schemas only; keep prohibition on other api imports"
    status: completed
  - id: emit-signature
    content: Change log_client.emit to accept LogEmitRequest; POST json via model_dump (by_alias/mode as needed)
    status: completed
  - id: align-provider-translator
    content: "Optional: refactor log_provider_request and log_translator_request to use LogProviderRequest / LogTranslatorRequest"
    status: completed
  - id: pylint-readme
    content: Run Pylint on touched modules; update backend README log_client note if the helper API changes
    status: completed
isProject: false
---

# Logging service: shared emit contract

## Context from your thread (accurate)

- **`LogEmitRequest` purpose**: Validates and documents the JSON body for [`POST /internal/logging/emit`](src/api/internal/logging_routes.py) (see [`emit_log`](src/api/internal/logging_routes.py) + [`LogEmitRequest`](src/api/internal/schemas/logging.py)).
- **Taking `LogEmitRequest` on `emit()`**: Single contract, type hints, and serialization via `model_dump()` / `model_dump(mode="json")` when needed.
- **Where layering is documented**: [`.cursor/rules/backend-general.mdc`](/home/plamen/_Projects/MarXman/mx-admin/.cursor/rules/backend-general.mdc) (today says **`services/` MUST NOT depend on `api/`**) and [`.cursor/rules/backend-http-boundary.mdc`](/home/plamen/_Projects/MarXman/mx-admin/.cursor/rules/backend-http-boundary.mdc). [CLAUDE.md](CLAUDE.md) states **`core/` must not import `src.services`**.

## Important codebase finding

- [`src/core/log_client.py`](src/core/log_client.py) defines `emit()`, but **nothing currently calls `log_client.emit`** (only `log_provider_request` / `log_translator_request` are used from services). Low churn for a signature change.

## Architectural choice (updated per your direction)

**Goal**: Use the same Pydantic types for the HTTP client and routes **without** moving models out of [`src/api/internal/schemas/logging.py`](src/api/internal/schemas/logging.py).

**Rule change (required before / with implementation)**  
Edit [`.cursor/rules/backend-general.mdc`](/home/plamen/_Projects/MarXman/mx-admin/.cursor/rules/backend-general.mdc) under **Layered Structure → Rules**:

- Remove or replace the blanket line **`services/` MUST NOT depend on `api/`** with a **narrow exception**.
- **Recommended wording** (tight scope avoids routes/deps leaking into services):

  - `services/` and `core/` **MAY** import **only** from the package `src.api.internal.schemas` (and its submodules): Pydantic wire contracts for internal HTTP endpoints.
  - They **MUST NOT** import from `src.api.v1`, `src.api.dependencies`, route modules, or other `src.api` areas.

**Why include `core/`**  
[`src/core/log_client.py`](src/core/log_client.py) lives under `core/`, not `services/`. Allowing only `services/` → schemas would still block `emit(request: LogEmitRequest)` in the client unless models were duplicated or moved. The same scoped exception for `core/` fixes that in one rule.

**Optional doc sync**  
If [CLAUDE.md](CLAUDE.md) or [backend-http-boundary.mdc](/home/plamen/_Projects/MarXman/mx-admin/.cursor/rules/backend-http-boundary.mdc) should mention “internal schemas are shared contracts,” add one short cross-reference when implementing (keep it minimal).

## Implementation steps

1. **Apply the rule change** in `backend-general.mdc` as above (and optionally one line in CLAUDE / http-boundary for discoverability).
2. **Update `emit`** in [`src/core/log_client.py`](src/core/log_client.py):

   - `from src.api.internal.schemas.logging import LogEmitRequest`
   - `async def emit(request: LogEmitRequest) -> None:` → POST `json=request.model_dump(mode="json")` or `model_dump()` depending on whether `fields` can hold non-JSON-native values.

3. **(Optional, same PR or follow-up)** Build `LogProviderRequest` / `LogTranslatorRequest` in `log_provider_request` / `log_translator_request` and use `model_dump(by_alias=True, mode="json")` so aliases (`model`, `input`) stay aligned with the server.

4. **Routes**: No structural change to [`logging_routes.py`](src/api/internal/logging_routes.py); it already uses `LogEmitRequest`.

5. **Quality gates**: Pylint on touched Python files.

6. **README**: If [`README.md`](README.md) documents `log_client` helpers, note that `emit` takes `LogEmitRequest` ([readme-sync](.cursor/rules/readme-sync.mdc)).

## Optional ergonomics

A small helper `def log_emit_request(...)-> LogEmitRequest` (in `schemas/logging.py` or next to `emit`) keeps call sites readable while `emit()` stays strictly typed.

## Abandoned approach

~~Moving logging models into `core/`~~ — not needed once the scoped schema-import exception is in place.
