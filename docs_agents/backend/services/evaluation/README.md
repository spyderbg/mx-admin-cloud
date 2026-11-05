# `evaluation` service

> **Unregistered, partially implemented** service package · owner role: **backend** · HTTP spec: [`openapi.yaml`](openapi.yaml) (generated) · evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified — see [discovery](../../../discovery/README.md)
>
> The package exports **no** `SERVICE_CLASS` / `SERVICE_NAME` / `SERVICE_ACCEPT_CRITERIA`, so `ServiceRegistry` never discovers it **[V]**. Its routes are still mounted by `backend/src/main.py`. This page describes what exists. Per assignment §4.5, no endpoint or provider was added to make it look complete.

## Purpose and Usage

Scores generated speech audio:

1. Transcribe the audio with a speech-to-text (STT) backend.
2. Compare the transcription with the expected text using Levenshtein-based metrics: character accuracy, word accuracy and pronunciation accuracy.
3. Persist each result and return a rolling average per TTS provider.

It was designed as the last step of the TTS pipeline (`Translate → Generate → Optimize → Evaluate`). That pipeline is currently disabled (see [core](../../core/README.md)), so today only direct calls to `POST /internal/evaluators/evaluate` use it.

## Location and Dependencies

| Path | Role |
| --- | --- |
| [`backend/src/services/evaluation/engine.py`](../../../../backend/src/services/evaluation/engine.py) | `EvaluationEngine` (`evaluate`, `compare`, `_transcribe_{openai,elevenlabs,speaktor}`), `levenshtein_distance`, `normalized_accuracy` |
| [`score_store.py`](../../../../backend/src/services/evaluation/score_store.py) | `ScoreStore` (ABC: `save`, `get_best_score`, …) and `MultiScoreStore` (fan-out) |
| [`score_store_json.py`](../../../../backend/src/services/evaluation/score_store_json.py), [`score_store_sqlite.py`](../../../../backend/src/services/evaluation/score_store_sqlite.py) | `JsonScoreStore`, `SQLiteScoreStore` (tables `evaluations`, `provider_aggregates`) |
| [`service.py`](../../../../backend/src/services/evaluation/service.py) | `EvaluationService`, `EvaluationProviderFactory` — **defined but unused** |
| [`models.py`](../../../../backend/src/services/evaluation/models.py) | `EvaluationResult`, `ProviderAggregate`, `ScoreRecord` value types; `EvaluationServiceProvider` — **no implementations** |
| [`routes.py`](../../../../backend/src/services/evaluation/routes.py), [`schemas.py`](../../../../backend/src/services/evaluation/schemas.py) | HTTP API, `EvaluateRequest` |

Depends on: `src.services.text_to_speech` (its configurations hold the `evaluate` blocks), `core.models.GeneratedAudio`, `core.log_client.log_provider_request`, `httpx`, external STT APIs.

## Service Interface / ServiceProvider Contract

What exists is **not** a working `ServiceProvider` model **[V][S]**:

- `EvaluationService(Service)` — `service.py:18`. It implements only the four `Service` provider accessors; no evaluation method; `SUPPORTED_STRATEGIES = ()`.
- `EvaluationServiceProvider(ServiceProvider)` — `models.py:57`. It declares `@abstractmethod async evaluate_text(text, input_lang_code, target_lang_code, category=None) -> str | None` and a static `language_name(lang_code)`.
  - Its docstrings are copied from the translator base ("Base class for translator service providers", "Translate a single word/phrase").
  - It does **not** inherit `ABC`, so the abstract method is not enforced.
- `EvaluationProviderFactory` has **no registered builders**.

The **verified equivalent** of a provider contract is `EvaluationEngine`:

- `EvaluationEngine(timeout_seconds=120.0, base_dir=None)`
- `async evaluate(generated_audio, evaluate_config, client, provider_name)` → `EvaluationResult`
- `async compare(...)` for A/B evaluation

The STT backend is chosen by `evaluate_config["stt_provider"]`: `openai`, `elevenlabs` or `speaktor`. Any other value fails with "Unsupported stt_provider".

## Implemented Providers

None registered (`EvaluationProviderFactory` is empty) **[V]**.

| Component kind | Classes | Notes |
| --- | --- | --- |
| STT backends (inside `EvaluationEngine`, not providers) | `_transcribe_openai`, `_transcribe_elevenlabs`, `_transcribe_speaktor` | API key from `<STT_PROVIDER>_API_KEY`; evaluation is skipped when it is not set |
| Persistence adapters (not providers) | `JsonScoreStore`, `SQLiteScoreStore`, `MultiScoreStore` | Built per request in `routes.py`, under `settings.scores_output_dir()` = `<OUTPUT_DIR>/results/scores/{json,sqlite/scores.db}` |

## Configured Provider Instances

The package has no configuration directory. Evaluator settings live in the `evaluate` block of each **text-to-speech** configuration (`enabled`, `stt_provider`, `model`, `language`, and Speaktor polling settings). They appear in `backend/config/text_to_speech/` for the ElevenLabs, OpenAI and Speaktor configurations — see the [text_to_speech README](../text_to_speech/README.md#configured-provider-instances). `GET /internal/evaluators/` lists them as `{name, enabled, config_path}` sorted by priority **[S]**.

## Selection and Lifecycle

There is no registry lifecycle. Each `POST /internal/evaluators/evaluate`:

1. creates a new `EvaluationEngine` and a `MultiScoreStore` of both stores;
2. opens an `httpx.AsyncClient` (180 s timeout);
3. evaluates and saves;
4. attaches the rolling average.

A store failure is appended to the result's `error`; it is not raised **[S]**.

## HTTP API

Two internal operations, both `require_internal` **[V]**. Full contract: [`openapi.yaml`](openapi.yaml).

| Operation | Purpose |
| --- | --- |
| `GET /internal/evaluators/` | List STT evaluator settings derived from text-to-speech configurations |
| `POST /internal/evaluators/evaluate` | Evaluate one generated audio file (`EvaluateRequest`: provider, request ids, texts, language, `raw_path`, `normalized_path`, `output_dir`, `evaluate_config`, …) |

No CLI command covers these endpoints. `kiddo-cli list` mentions "evaluators" in its help, but the command does not exist (discovery F-10).

## Errors, Observability and Security

- Failures are returned **inside** the result (`success`, `error`) rather than as HTTP errors in most paths **[S]**.
- STT calls are logged as provider requests via `log_client.log_provider_request` **[S]**.
- **Security:**
  - The request body carries **filesystem paths** (`raw_path`, `normalized_path`, `output_dir`) that the server reads and writes (discovery SEC-8). The endpoint is internal-only and must stay so.
  - STT calls use paid external APIs.

## Testing

`backend/src/services/evaluation/__tests__/` contains only `__init__.py`. **There are no evaluation tests** **[V]**. The scoring functions (`levenshtein_distance`, `normalized_accuracy`) and the score stores are untested.

Suggested Backend follow-ups, not part of Phase 1:

- unit tests for scoring and the score stores using temp directories;
- a decision whether to register the service in the provider model or retire the unused `EvaluationService` / `EvaluationServiceProvider` (discovery F-4).
