# `text_translation` service

> Registered service · `SERVICE_NAME = "text_translation"` · owner role: **backend** · HTTP spec: [`openapi.yaml`](openapi.yaml) (generated) · evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified — see [discovery](../../../discovery/README.md)

## Purpose and Usage

Translates single words or short phrases between content languages, with an optional category hint, through prioritized providers:

1. a **static CSV** lookup over the existing content registries;
2. OpenAI or a self-hosted Qwen model (OpenAI-compatible API), configured per language pair.

It also translates the words of an uploaded TXT/CSV/XLSX file. The content service calls `POST /internal/translators/translate` when an item is created.

## Location and Dependencies

| Path | Role |
| --- | --- |
| [`backend/src/services/text_translation/__init__.py`](../../../../backend/src/services/text_translation/__init__.py) | Registration (`config_subdir="text_translation"`) |
| [`service.py`](../../../../backend/src/services/text_translation/service.py) | `TextTranslationService`, `TextTranslationProviderFactory` |
| [`models.py`](../../../../backend/src/services/text_translation/models.py) | `TranslationSourceWord`, `TranslateResult`, `TranslateTextRequest`, `TextTranslationServiceProvider` contract (`:52`) |
| [`input_loader.py`](../../../../backend/src/services/text_translation/input_loader.py) | `load_words()`: parses TXT/CSV/XLSX into `TranslationSourceWord`s for `translate-file`, skipping segments already in the target language |
| [`providers/`](../../../../backend/src/services/text_translation/providers/) | Concrete providers |
| [`routes.py`](../../../../backend/src/services/text_translation/routes.py), [`schemas.py`](../../../../backend/src/services/text_translation/schemas.py) | HTTP API (`TranslateRequest`, `TranslateResponse`, `TranslateFileResponse`, `TranslationFileSourceWord`) |

Depends on: `core.languages`, `openpyxl` (XLSX parsing in `input_loader.py`), the OpenAI-compatible Chat Completions API (`openai`/`qwen`), and the content directory (`static`).

## Service Interface / ServiceProvider Contract

**Service:** `TextTranslationService(Service)` — `service.py:23`; `SUPPORTED_STRATEGIES = (FIRST_SUCCESS,)`, default `FIRST_SUCCESS` **[V]**.

| Method | Meaning |
| --- | --- |
| `async translate_text(text, input_lang_code=None, target_lang_code=None, category=None)` → `TranslateResult` | Tries active providers that `supports()` the language pair, in priority order. The first translation wins. A provider that returns `None` ("cannot handle") or raises hands over to the next. `errors` lists one reason per failed attempt. **No chaining** of providers |

`TranslateResult`: `success`, `lang_text` (`lang_code`, `text`), `errors`.

**Provider contract:** `TextTranslationServiceProvider(ServiceProvider, ABC)` — `models.py:52` **[V]**.

- **Abstract:** `supports(input_lang_code, target_lang_code) -> bool` and `async translate_text(request: TranslateTextRequest) -> str | None`.
- **Concrete helpers:** `default_input_language()`, `default_output_language()` (from the `input_language` / `output_language` config), and a static `language_name(lang_code)`.

## Implemented Providers

| Class | Registered name | Path | Mechanism | Credential env var |
| --- | --- | --- | --- | --- |
| `StaticTextTranslate` | `static_translate` | `providers/static_translate/provider.py:18` | Row-aligned lookup in `items_<lang>.csv` files under the configured `content_dir` (same line = same concept) | none |
| `OpenAITextTranslate` | `openai_translate` | `providers/openai_translate/provider.py:18` | OpenAI Chat Completions with the configured model, prompt and temperature | `OPENAI_API_KEY` (fixed override, because there is one config file per language pair) |
| `QwenTextTranslate` | `qwen_translate` | `providers/qwen_translate/provider.py:19` | Self-hosted Qwen through an OpenAI-compatible Chat Completions API at the configured `base_url` | `QWEN_API_KEY` |

## Configured Provider Instances

| File (`backend/config/text_translation/`) | Provider | Enabled | Priority | Language pair |
| --- | --- | --- | --- | --- |
| [`static.json`](../../../../backend/config/text_translation/static.json) | `static_translate` | **yes** | 1 | any pair whose `items_<lang>.csv` files exist under `content_dir` = `../content` (`supports()`); `en`/`bg` are only its configured defaults |
| [`openai_en_bg.json`](../../../../backend/config/text_translation/openai_en_bg.json) | `openai_translate` | no | 2 | en → bg |
| [`openai_en_es.json`](../../../../backend/config/text_translation/openai_en_es.json) | `openai_translate` | no | 2 | en → es |
| [`openai_en_fr.json`](../../../../backend/config/text_translation/openai_en_fr.json) | `openai_translate` | no | 2 | en → fr |
| [`qwen_en_bg.json`](../../../../backend/config/text_translation/qwen_en_bg.json) | `qwen_translate` | **yes** | 3 | en → bg |
| [`qwen_en_es.json`](../../../../backend/config/text_translation/qwen_en_es.json) | `qwen_translate` | **yes** | 3 | en → es |
| [`qwen_en_fr.json`](../../../../backend/config/text_translation/qwen_en_fr.json) | `qwen_translate` | **yes** | 3 | en → fr |

Pairs are verified from the `input_language` / `output_language` values **[V]**. The OpenAI and Qwen `supports()` accept only their configured pair; the static provider accepts any pair it loaded **[S]**. `backend/config/_service_strategies.json` explicitly stores `"text_translation": "FIRST_SUCCESS"`, the only stored strategy at baseline **[V]**.

## Selection and Lifecycle

- `FIRST_SUCCESS` over active providers in ascending priority, filtered by `supports()`. `static` (priority 1) answers known content words. Otherwise the Qwen instance for the pair is tried (priority 3).
- No provider is chained after another: the first non-`None` result wins (see `backend/code_improvements.md` §3.1 for the earlier chaining problem).
- Providers are replaced on registry reload.

## HTTP API

Three internal operations under `/internal/translators`, all `require_internal` **[V]**. Full contract: [`openapi.yaml`](openapi.yaml).

| Operation | Body → result |
| --- | --- |
| `GET /internal/translators/` | Configurations with state |
| `POST /internal/translators/translate` | `TranslateRequest` (`text`, `input_lang_code?`, `target_lang_code`, `category?`) → `TranslateResponse` (`success`, `lang_text`, `errors`) |
| `POST /internal/translators/translate-file` | multipart file (TXT/CSV/XLSX, parsed by `load_words`) → `TranslateFileResponse` (`results`) |

**CLI mismatch [V]:** `kiddo-cli translate word|file` post to `/internal/translate` and `/internal/translate-file`, which do not exist (discovery F-3). The fix belongs to the CLI role; this API is unchanged.

## Errors, Observability and Security

- Translation failures come back as `success: false` with per-provider `errors` (e.g. "openai_en_fr: OPENAI_API_KEY is not set."), not as HTTP errors **[S]**.
- Observability: provider calls are logged over HTTP (`core/log_client`).
- Security and cost:
  - OpenAI and Qwen calls may be paid or reach a remote host (`base_url`).
  - Uploaded files are parsed server-side.
  - Endpoints are internal-only.

## Testing

| Command | Scope | Baseline |
| --- | --- | --- |
| `cd backend && ../.venv/bin/python -m pytest -q src/services/text_translation` | `test_text_translation_service.py` (12), `test_text_translation_service_integration.py` (9), `test_qwen_translate.py` (10), `test_static_translate.py` (4) — 35 tests | Passed in the 919-test baseline **[V]** |

`OpenAITextTranslate` has no dedicated test file **[V]**.
