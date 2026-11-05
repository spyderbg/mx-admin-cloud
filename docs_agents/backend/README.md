# Backend — Agent Documentation Index

> Owner role: **backend** (`backend/**` except `backend/data/**`, `docs_agents/backend/**`; v4 §3) · Default runner: Claude Code · Verified at commit `e2ddd9c` · evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified — see [discovery](../discovery/README.md)
> Pre-existing backend docs (preserved, not replaced): [`backend/README.md`](../../backend/README.md), [`backend/CLAUDE.md`](../../backend/CLAUDE.md), [`backend/code_improvements.md`](../../backend/code_improvements.md), [`backend/docs/openapi.yaml`](../../backend/docs/openapi.yaml) (stale snapshot)

## Layout

| Page | Covers |
| --- | --- |
| [`api/`](api/README.md) + [`api/openapi.yaml`](api/openapi.yaml) | App composition, auth/users/common endpoints, internal jobs/services endpoints, DI dependencies (17 operations) |
| [`core/`](core/README.md) | Settings, core models, logging client, pipeline (disabled), job store |
| [`security/`](security/README.md) | JWT, roles, users DB, insecure defaults |
| [`services/<name>/`](#service-catalog) | One README + generated `openapi.yaml` per service package (74 operations) |

## The service/provider model in one paragraph

Each service package under `backend/src/services/` may export:

- `SERVICE_CLASS` — a `Service` subclass;
- `SERVICE_NAME`;
- `SERVICE_ACCEPT_CRITERIA` — the config subdirectory plus validate/build hooks.

`ServiceRegistry` discovers these packages with `pkgutil`. `ServiceRegistryBuilder` turns each `backend/config/<subdir>/*.json` file into a `ServiceProvider` envelope, which a per-service `ServiceProviderFactory` materializes into a concrete provider class registered at import time. Providers are then sorted by priority and dispatched with `ALL_PROVIDERS` or `FIRST_SUCCESS` according to `backend/config/_service_strategies.json`. Config changes are hot-reloaded. Details: [architecture §4](../architecture/README.md#4-service-and-provider-model-sv).

Each package's `__init__.py` is also its public facade (`__all__`): other services import only from it, and every concrete provider is its own package under `providers/`. The rules and their automated check are in [`backend/CLAUDE.md`](../../backend/CLAUDE.md) ("Service and provider encapsulation").

## Service catalog

| Service | Registered | Provider contract | Implemented providers | Instances (active/total) | Strategy | Ops | Tests |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [`configuration`](services/configuration/README.md) | yes | `ConfigurationProvider` | 1 (runtime-wired) | 1/1 | — | 16 | 105 |
| [`content`](services/content/README.md) | yes | `ContentProvider` | 1 | 1/1 | FIRST_SUCCESS | 37 | 474 |
| [`evaluation`](services/evaluation/README.md) | **no** | `EvaluationServiceProvider` (unused, not ABC) | 0 | — | — | 2 | **0** |
| [`logging`](services/logging/README.md) | yes | `LoggingServiceProvider` | 1 | 2/3 | ALL_PROVIDERS | 8 | 14 |
| [`text_to_image`](services/text_to_image/README.md) | yes | `TextToImageProvider` | 5 | 1/5 | FIRST_SUCCESS | 3 | 121 |
| [`text_to_speech`](services/text_to_speech/README.md) | yes | `TTSProvider` | 5 | 2/6 | FIRST_SUCCESS | 3 | 48 |
| [`text_translation`](services/text_translation/README.md) | yes | `TextTranslationServiceProvider` | 3 | 4/7 | FIRST_SUCCESS | 3 | 35 |
| [`voice_optimization`](services/voice_optimization/README.md) | yes | `AudioOptimizerBase` (not ABC) | 2 | 3/3 | ALL_PROVIDERS | 2 | **0** |

Totals **[V]**:

- 8 service packages, 7 registered;
- 18 provider classes, 26 configured instances;
- 74 service operations + 17 API-layer operations = 91 runtime operations;
- 797 service tests (tables above), plus `api` 88, `core` 13 and shared `services/__tests__/test_provider_strategy.py` 21 = 919 tests.

## API ownership

The generated documents assign every runtime operation by the **module that defines its endpoint**: `src.services.<name>.*` → that service; `src.api.*` and `src.main` → API layer. There are no unowned or doubly owned operations **[V]**. Contract authority and pipeline: [`../architecture/api-contracts.md`](../architecture/api-contracts.md).

```bash
python scripts/export_openapi.py --docs-agents          # regenerate docs_agents/backend/**/openapi.yaml
python scripts/export_openapi.py --docs-agents --check  # fail on drift
python scripts/check_docs_agents.py                     # links, inventories, OpenAPI structure, drift
```

## Running backend checks

| Check | Command (from `backend/`) | Baseline |
| --- | --- | --- |
| Tests | `../.venv/bin/python -m pytest -q -p no:cacheprovider` | 919 passed, 1 warning **[V]** |
| Lint | `PYTHONPATH=src ../.venv/bin/python -m pylint src/` | 10.00/10, 1 R0915 message **[V]** |
| Compile | `../.venv/bin/python -m compileall -q src` | OK **[V]** |

Never run tests against the real `content/`, `data/` or `backend/data/`. Phase 0 ran them in an isolated `git archive` export. Known leak: `test_content_filesystem_item_images.py` writes lock files into the real content tree (discovery F-5).

## Verified findings relevant to Backend work (not fixed in Phase 1)

| ID | Finding | Page |
| --- | --- | --- |
| F-1 | `POST /internal/generate` raises `NameError`; `core/pipeline.py` is commented out | [api](api/README.md), [core](core/README.md) |
| F-4 | `evaluation` unregistered; copy-pasted provider contract | [evaluation](services/evaluation/README.md) |
| F-5 | Test writes lock files into real `content/` | [content](services/content/README.md#testing) |
| F-11 | `TTSProvider` docstring claims an `is_active` override that does not exist | [text_to_speech](services/text_to_speech/README.md#service-interface--serviceprovider-contract) |
| F-12 | `AudioOptimizerBase` and `EvaluationServiceProvider` use `@abstractmethod` without `ABC` | [voice_optimization](services/voice_optimization/README.md), [evaluation](services/evaluation/README.md) |
| F-13 | `VoiceOptimizationService.optimize()` has no callers | [voice_optimization](services/voice_optimization/README.md) |
| F-14 | `core/log_client.py` imports `src.services.logging.routes` (beyond the documented exception) | [core](core/README.md#verified-boundary-deviation) |
| F-15 | Credential variable for TTS instances derives from the config file name (`elevenlabs_slow.json` → `ELEVENLABS_SLOW_API_KEY`) | [text_to_speech](services/text_to_speech/README.md#implemented-providers) |
| SEC-1..5, SEC-8 | Insecure defaults, tracked DBs, path parameters in request bodies | [security](security/README.md) |
