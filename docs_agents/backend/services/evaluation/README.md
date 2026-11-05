# `evaluation` service

> **Unregistered, partially implemented** service package · owner role: **backend** · HTTP spec: [`openapi.yaml`](openapi.yaml) (generated) · evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified — see [discovery](../../../discovery/README.md)
>
> The package exports **no** `SERVICE_CLASS` / `SERVICE_NAME` / `SERVICE_ACCEPT_CRITERIA`, so `ServiceRegistry` never discovers it **[V]**. Its routes are still mounted by `backend/src/main.py`. This page describes what exists. Per assignment §4.5, discovery added no endpoint or provider to make it look complete; the speech-to-text (STT) backends were later extracted from the engine into provider packages.

## Purpose and Usage

Scores generated speech audio:

1. Transcribe the audio with a speech-to-text (STT) backend.
2. Compare the transcription with the expected text using Levenshtein-based metrics: character accuracy, word accuracy and pronunciation accuracy.
3. Persist each result and return a rolling average per TTS provider.

It was designed as the last step of the TTS pipeline (`Translate → Generate → Optimize → Evaluate`). That pipeline is currently disabled (see [core](../../core/README.md)), so today only direct calls to `POST /internal/evaluators/evaluate` use it.

## Location and Dependencies

| Path | Role |
| --- | --- |
| [`backend/src/services/evaluation/engine.py`](../../../../backend/src/services/evaluation/engine.py) | `EvaluationEngine` (`evaluate`, `compare`; resolves the STT backend through `SpeechToTextProviderFactory`), `levenshtein_distance`, `normalized_accuracy` |
| [`providers/`](../../../../backend/src/services/evaluation/providers/) | STT backends, one package each: [`openai_stt/`](../../../../backend/src/services/evaluation/providers/openai_stt/), [`elevenlabs_stt/`](../../../../backend/src/services/evaluation/providers/elevenlabs_stt/), [`speaktor_stt/`](../../../../backend/src/services/evaluation/providers/speaktor_stt/); `providers/__init__.py` imports them all |
| [`score_store.py`](../../../../backend/src/services/evaluation/score_store.py) | `ScoreStore` (ABC: `save`, `get_best_score`, …) and `MultiScoreStore` (fan-out) |
| [`score_store_json.py`](../../../../backend/src/services/evaluation/score_store_json.py), [`score_store_sqlite.py`](../../../../backend/src/services/evaluation/score_store_sqlite.py) | `JsonScoreStore`, `SQLiteScoreStore` (tables `evaluations`, `provider_aggregates`) |
| [`service.py`](../../../../backend/src/services/evaluation/service.py) | `SpeechToTextProviderFactory` (the STT registry); `EvaluationService`, `EvaluationProviderFactory` — **defined but unused** |
| [`models.py`](../../../../backend/src/services/evaluation/models.py) | `EvaluationResult`, `ProviderAggregate`, `ScoreRecord` value types; `STTProvider` (STT backend contract); `EvaluationServiceProvider` — **no implementations** |
| [`routes.py`](../../../../backend/src/services/evaluation/routes.py), [`schemas.py`](../../../../backend/src/services/evaluation/schemas.py) | HTTP API, `EvaluateRequest` |

Depends on: `src.services.text_to_speech` (its configurations hold the `evaluate` blocks, and its facade exports `GeneratedAudio`), `core.log_client.log_provider_request`, `core.languages.to_bcp47` (Speaktor), `httpx`, external STT APIs.

## Service Interface / ServiceProvider Contract

What exists is **not** a working `ServiceProvider` model **[V][S]**:

- `EvaluationService(Service)` — `service.py:59`. It implements only the four `Service` provider accessors; no evaluation method; `SUPPORTED_STRATEGIES = ()`.
- `EvaluationServiceProvider(ServiceProvider)` — `models.py:61`. It declares `@abstractmethod async evaluate_text(text, input_lang_code, target_lang_code, category=None) -> str | None` and a static `language_name(lang_code)`.
  - Its docstrings are copied from the translator base ("Base class for translator service providers", "Translate a single word/phrase").
  - It does **not** inherit `ABC`, so the abstract method is not enforced.
- `EvaluationProviderFactory` has **no registered builders**.

The **verified equivalent** of a provider contract is `EvaluationEngine`:

- `EvaluationEngine(timeout_seconds=120.0, base_dir=None)`
- `async evaluate(generated_audio, evaluate_config, client, provider_name)` → `EvaluationResult`
- `async compare(...)` for A/B evaluation

**STT provider contract:** `STTProvider(ABC)` — `models.py:84`. One method, `@abstractmethod async transcribe(audio_path, language, eval_cfg, api_key, client) -> str`:

- `language` is `evaluate.language` when set, else the generated audio's language code (the engine resolves it);
- `eval_cfg` is the whole `evaluate` block, and each backend reads its own keys from it;
- `client` is the request's `httpx.AsyncClient`, and the backend uses its own `timeout_seconds` (the engine's, default 120 s) for each HTTP call;
- HTTP and polling errors propagate; the backend logs each call through the base class's `_log_request`.

An STT provider is **not** a `ServiceProvider`: it has no configuration file of its own, priority or enabled flag. `SpeechToTextProviderFactory` (`service.py:18`) keeps the `ServiceProviderFactory` conventions (`register`, `supports`, `available_names`, `create`), with two differences: `create(id, timeout_seconds)` needs no config envelope, and registering a second class under an existing id raises `ValueError`. Neither the factory nor the providers are exported from the facade **[V]**.

## Implemented Providers

Three STT backends, selected by `evaluate.stt_provider` **[V]**. `EvaluationProviderFactory` stays empty.

| Class | Registered id | Path | Mechanism | `evaluate` keys it reads |
| --- | --- | --- | --- | --- |
| `OpenAISTTProvider` | `openai` | `providers/openai_stt/provider.py:16` | `POST https://api.openai.com/v1/audio/transcriptions` (multipart WAV) | `model` (default `whisper-1`), `prompt` |
| `ElevenLabsSTTProvider` | `elevenlabs` | `providers/elevenlabs_stt/provider.py:16` | `POST https://api.elevenlabs.io/v1/speech-to-text` (multipart WAV) | `model` (default `scribe_v1`) |
| `SpeaktorSTTProvider` | `speaktor` | `providers/speaktor_stt/provider.py:19` | Transkriptor local-file workflow: upload URL → `PUT` → initiate → poll `GET /files/{order_id}/content`; language sent as BCP-47 | `poll_interval_seconds` (3), `poll_max_attempts` (40) |

| Component kind | Classes | Notes |
| --- | --- | --- |
| Persistence adapters (not providers) | `JsonScoreStore`, `SQLiteScoreStore`, `MultiScoreStore` | Built per request in `routes.py`, under `settings.scores_output_dir()` = `<OUTPUT_DIR>/results/scores/{json,sqlite/scores.db}` |

## Configured Provider Instances

The package has no configuration directory. Evaluator settings live in the `evaluate` block of each **text-to-speech** configuration (`enabled`, `stt_provider`, `model`, `language`, and Speaktor polling settings). They appear in `backend/config/text_to_speech/` for the ElevenLabs, OpenAI and Speaktor configurations — see the [text_to_speech README](../text_to_speech/README.md#configured-provider-instances). `GET /internal/evaluators/` lists them as `{name, enabled, config_path}` sorted by priority **[S]**.

## Selection and Lifecycle

`EvaluationEngine.evaluate()` and `compare()` select the STT backend in this order **[V]**:

1. The id is `evaluate.stt_provider`, else the `provider_name` argument, else the generated audio's TTS provider, lower-cased.
2. The API key is read from `<ID>_API_KEY` (for example `OPENAI_API_KEY`). When it is unset, the result fails with "Evaluation skipped: <ID>_API_KEY is not set.".
3. `SpeechToTextProviderFactory` looks the id up (`engine.py:162`). An unregistered id fails with "Unsupported stt_provider: <id>". The key check runs first, so an unknown id without a key reports the missing key.
4. A new provider instance is created for each `evaluate()`/`compare()` call; `compare()` transcribes both clips with it.

Importing `src.services.evaluation` loads `providers/__init__.py`, which imports every provider package; each one registers its class at import time. Nothing is instantiated, and no client is created, until an evaluation runs.

**Adding an STT backend:**

1. Create `providers/<name>_stt/` with `provider.py` (a subclass of `STTProvider` that implements `transcribe` and calls `SpeechToTextProviderFactory.register("<id>", cls)` at import time) and an `__init__.py` that re-exports the class.
2. Set `"stt_provider": "<id>"` in the `evaluate` block of a text-to-speech configuration, and set `<ID>_API_KEY`.
3. Add its tests in `__tests__/test_stt_providers.py`, including the isolation case.

No engine edit is needed. The rules the package must follow (no sibling imports, registration only inside the package) are in `backend/CLAUDE.md` ("Service and provider encapsulation") and are checked by `src/__tests__/test_architecture.py`.

There is no service-registry lifecycle. Each `POST /internal/evaluators/evaluate`:

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
- STT calls are logged as provider requests via `log_client.log_provider_request` (`STTProvider._log_request`) **[S]**.
- STT HTTP errors and the Speaktor polling timeout are **not** caught: they surface as HTTP 500 from `POST /internal/evaluators/evaluate` **[S]**.
- **Security:**
  - The request body carries **filesystem paths** (`raw_path`, `normalized_path`, `output_dir`) that the server reads and writes (discovery SEC-8). The endpoint is internal-only and must stay so.
  - STT calls use paid external APIs.

## Testing

`backend/src/services/evaluation/__tests__/` **[V]**:

- `test_evaluation_engine.py` registers a test backend under a new id. It checks that the engine selects it with no engine change, plus language and id fallbacks, the error order, error propagation and `compare()`.
- `test_stt_providers.py` checks that every id resolves to its package, and covers unknown and duplicate ids and the abstract contract. It also checks the exact request each backend sends (with a client double; no real API calls) and Speaktor polling and timeout.
- `test_stt_providers.py::test_stt_provider_works_with_its_sibling_packages_removed` copies `src/`, deletes the other STT packages, then imports the facade in a fresh interpreter. Only that backend may register, and it must transcribe on its own.

The score stores are untested, and scoring is covered only through the engine tests.

Suggested Backend follow-ups:

- unit tests for scoring and the score stores using temp directories;
- a decision whether to register the service in the provider model or retire the unused `EvaluationService` / `EvaluationServiceProvider` (discovery F-4).
