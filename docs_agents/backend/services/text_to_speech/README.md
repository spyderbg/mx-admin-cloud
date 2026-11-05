# `text_to_speech` service

> Registered service · `SERVICE_NAME = "text_to_speech"` · owner role: **backend** · HTTP spec: [`openapi.yaml`](openapi.yaml) (generated) · evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified — see [discovery](../../../discovery/README.md)

## Purpose and Usage

Synthesizes speech for words and phrases in the supported languages (`en`, `fr`, `es`, `bg` by default) through configurable providers: ElevenLabs, OpenAI, Speaktor, offline pyttsx3, and a static placeholder provider.

Its configurations also carry the `evaluate` block the [evaluation](../evaluation/README.md) endpoints read. The content service calls `POST /internal/generators/generate-word` to create, regenerate and generate item and category voices, and records the `X-TTS-*` headers in voice sidecars.

## Location and Dependencies

| Path | Role |
| --- | --- |
| [`backend/src/services/text_to_speech/__init__.py`](../../../../backend/src/services/text_to_speech/__init__.py) | Registration (`config_subdir="text_to_speech"`) |
| [`service.py`](../../../../backend/src/services/text_to_speech/service.py) | `TextToSpeechService`, `TextToSpeechProviderFactory`, `NoActiveTTSProvidersError`, `TTSProviderNotFoundError` |
| [`models.py`](../../../../backend/src/services/text_to_speech/models.py) | `TTSRequest`, `GeneratedAudio` (the generation result; also used by evaluation and voice_optimization), `TTSProvider` contract (`:61`) |
| [`providers/`](../../../../backend/src/services/text_to_speech/providers/) | Concrete providers |
| [`routes.py`](../../../../backend/src/services/text_to_speech/routes.py), [`schemas.py`](../../../../backend/src/services/text_to_speech/schemas.py) | HTTP API (`GenerateRequest`, `GenerateWordRequest`, `VoiceSettingsOverride`, `TtsGenerateResult`, `GeneratorProviderInfo`) |

Depends on: `httpx` (the routes pass a shared client into providers), `pyttsx3`, `core.config.settings.raw_output_dir()` (`<OUTPUT_DIR>/tts_audio_<lang>/<provider>/`), `core.log_client`.

## Service Interface / ServiceProvider Contract

**Service:** `TextToSpeechService(Service)` — `service.py:29`; `SUPPORTED_STRATEGIES = (FIRST_SUCCESS,)`, default `FIRST_SUCCESS` **[V]**.

| Method | Meaning |
| --- | --- |
| `async generate(request: TTSRequest, client, *, provider_name=None)` → `GeneratedAudio` | With `provider_name`, only that **active** provider runs and its result is returned as is. Without it, active providers are tried in priority order; a provider that raises or returns an unsuccessful result hands over to the next one |
| `async generate_with_configuration(request, client, configuration_name)` → `GeneratedAudio` | Generate with a named configuration **even if it is disabled** |
| `configured_provider(configuration_name)` → `TTSProvider` | Lookup by configuration name, enabled or not |
| `generation_parameters(provider_name, request)` → `dict` | Effective parameters (recorded in sidecars and headers) |

**Provider contract:** `TTSProvider(ServiceProvider, ABC)` — `models.py:61` **[V]**.

- **Abstract:** `async generate(request: TTSRequest, client) -> GeneratedAudio`.
- **Shared, concrete:**
  - `generate_config` / `evaluate_config` (copies of `config.generate` / `config.evaluate`);
  - `resolve_language_generate(language_code)` — `languages` map lookup: exact code, then `en`, then the first entry;
  - `resolve_request_generate(request)`;
  - `apply_generate_overrides(generate, overrides)`;
  - `build_prompt_text(request)`;
  - `build_output_path(request, extension)`;
  - `async evaluate(...)` — an optional hook that is a no-op by default.

> **Docstring drift (verified):** the `TTSProvider` docstring says activation is driven by `config.generate.enabled` and that `is_active` is overridden. It is **not** overridden: `TTSProvider.is_active is ServiceProvider.is_active` **[V]**. Activation uses the envelope's `service_provider.enabled`, like every other service. `config.generate.enabled` in `static.json` does not affect activation.

## Implemented Providers

| Class | Registered name | Path | Mechanism | Credential env var |
| --- | --- | --- | --- | --- |
| `ElevenLabsTTSProvider` | `elevenlabs` | `providers/elevenlabs_tts.py:50` | Raw HTTP to `https://api.elevenlabs.io/v1` with the shared `httpx` client | `<CONFIG_NAME>_API_KEY` (default convention) |
| `OpenAITTSProvider` | `openai` | `providers/openai_tts.py:18` | Raw HTTP to `https://api.openai.com/v1/audio/speech` with the shared `httpx` client (not the SDK) | `<CONFIG_NAME>_API_KEY` |
| `SpeaktorTTSProvider` | `speaktor` | `providers/speaktor_tts.py:49` | Speaktor/Transkriptor REST (`api.tor.app`), config-driven payload template and response mode; overrides `apply_generate_overrides` | `<CONFIG_NAME>_API_KEY` |
| `Pyttsx3TTSProvider` | `pyttsx3` | `providers/pyttsx3_tts.py:27` | Offline, local `pyttsx3` engine | none |
| `StaticTTSProvider` | `static` | `providers/static_tts.py:21` | Returns an item's existing English voice, or a silent placeholder | none |

**Credential caveat [S]:** the first three use `ServiceProvider.api_key_env_name()` = `f"{config_name.upper()}_API_KEY"`:

- `elevenlabs.json` resolves `ELEVENLABS_API_KEY`;
- `elevenlabs_slow.json` resolves `ELEVENLABS_SLOW_API_KEY`, a variable absent from `.env.example`;
- `openai.json` resolves `OPENAI_API_KEY`;
- `speaktor.json` resolves `SPEAKTOR_API_KEY`.

A second configuration of the same provider therefore needs its own variable (see `backend/code_improvements.md` §3.4).

## Configured Provider Instances

| File (`backend/config/text_to_speech/`) | Provider | Enabled | Priority | Config sections |
| --- | --- | --- | --- | --- |
| [`static.json`](../../../../backend/config/text_to_speech/static.json) | `static` | no | 1 | `generate.enabled` (ignored for activation, see above) |
| [`elevenlabs.json`](../../../../backend/config/text_to_speech/elevenlabs.json) | `elevenlabs` | **yes** | 3 | `generate.languages`, `evaluate` (`enabled`, `stt_provider`, `model`, `language`) |
| [`elevenlabs_slow.json`](../../../../backend/config/text_to_speech/elevenlabs_slow.json) | `elevenlabs` | no | 3 | same shape as `elevenlabs.json` |
| [`openai.json`](../../../../backend/config/text_to_speech/openai.json) | `openai` | no | 4 | `generate` (`model`, `languages`), `evaluate` |
| [`speaktor.json`](../../../../backend/config/text_to_speech/speaktor.json) | `speaktor` | no | 4 | `generate` (endpoint, method, response mode, audio field, headers, payload template, languages), `evaluate` (+ polling) |
| [`pyttsx3.json`](../../../../backend/config/text_to_speech/pyttsx3.json) | `pyttsx3` | **yes** | 5 | `generate` (`rate`, `volume`, `languages`) |

Active at baseline: `elevenlabs` (priority 3), then `pyttsx3` (5) **[V]**.

## Selection and Lifecycle

- `FIRST_SUCCESS` over active providers in ascending priority (the registry sorts by priority on load).
- Named-provider calls (`/internal/generators/{provider}/generate`) bypass the strategy.
- `generate-word` with `configuration_name` can use a **disabled** configuration (`generate_with_configuration`); the UI uses this to try a specific voice.
- Providers are replaced on registry reload.

## HTTP API

Three internal operations under `/internal/generators`, all `require_internal` **[V]**. Full contract: [`openapi.yaml`](openapi.yaml).

| Operation | Returns |
| --- | --- |
| `GET /internal/generators/` | `GeneratorProviderInfo` list (name, enabled, config path, generate settings, evaluate flag) |
| `POST /internal/generators/{provider}/generate` | `TtsGenerateResult` JSON for one `GenerateRequest`; 503 none active, 404 unknown/inactive |
| `POST /internal/generators/generate-word` | Raw `audio/mpeg` + `X-TTS-Provider` + `X-TTS-Parameters` (base64 JSON) for `GenerateWordRequest` (`word`, `lang`, optional `configuration_name`, optional `settings` speed/stability/similarity); 400 empty word, 404, 500, 503 |

CLI: `kiddo-cli generate word` calls `generate-word`. `generate run` targets a removed endpoint (discovery F-2).

## Errors, Observability and Security

- Errors: `NoActiveTTSProvidersError` → 503, `TTSProviderNotFoundError` → 404, other failures → 500 (`generate-word`). A provider failure inside `FIRST_SUCCESS` is collected (`AllProvidersFailedError`), and the next provider is tried **[S]**.
- Observability: provider calls are logged over HTTP (`core/log_client`).
- Security and cost:
  - Providers call paid APIs.
  - Endpoints are internal-only.
  - Output files are written under `OUTPUT_DIR`.

## Testing

| Command | Scope | Baseline |
| --- | --- | --- |
| `cd backend && ../.venv/bin/python -m pytest -q src/services/text_to_speech` | `test_text_to_speech_service.py` (14), `test_text_to_speech_service_integration.py` (25), `test_speaktor_provider.py` (6), `test_pyttsx3_provider.py` (3) — 48 tests | Passed in the 919-test baseline **[V]** |

There are no dedicated tests for `ElevenLabsTTSProvider`, `OpenAITTSProvider` or `StaticTTSProvider` beyond the service-level tests **[V]**.
