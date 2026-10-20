# `text_to_image` service

> Registered service · `SERVICE_NAME = "text_to_image"` · owner role: **backend** · HTTP spec: [`openapi.yaml`](openapi.yaml) (generated) · evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified — see [discovery](../../../discovery/README.md)

## Purpose and Usage

Generates images from text prompts through configurable providers: OpenAI Images, Google GenAI, or a self-hosted ComfyUI. Every outbound call goes through a per-provider traffic governor that enforces concurrency, spacing and retries, and every call is priced from a configurable table.

The content service is the main caller (`POST /internal/image-generators/generate-image`), for category icons and generated item images. It records the returned `X-Image-Provider` / `X-Image-Parameters` headers in the image's metadata sidecar. The service may run on a different host (e.g. a GPU box), which is why it logs only over HTTP.

## Location and Dependencies

| Path | Role |
| --- | --- |
| [`backend/src/services/text_to_image/__init__.py`](../../../../backend/src/services/text_to_image/__init__.py) | Registration (`config_subdir="text_to_image"`) |
| [`service.py`](../../../../backend/src/services/text_to_image/service.py) | `TextToImageService`, `TextToImageProviderFactory`, `NoActiveImageProvidersError`, `ImageProviderNotFoundError` |
| [`models.py`](../../../../backend/src/services/text_to_image/models.py) | `ImageRequest`, `ImagePayload`, `TextToImageProvider` contract (`:142`) |
| [`governor.py`](../../../../backend/src/services/text_to_image/governor.py) | `TrafficPolicy`, `RequestGovernor`, `RetryableError` / `NonRetryableError`; retryable HTTP statuses 408, 409, 425, 429, 500, 502, 503, 504; honors `Retry-After` |
| [`pricing.py`](../../../../backend/src/services/text_to_image/pricing.py) | `PriceTable`; an unknown model yields `None`, not `0.0` |
| [`templating.py`](../../../../backend/src/services/text_to_image/templating.py) | Typed placeholder rendering for ComfyUI workflow graphs |
| [`providers/`](../../../../backend/src/services/text_to_image/providers/) | Concrete providers (+ `_openai_payload.py`, `_genai_common.py` helpers) |
| [`routes.py`](../../../../backend/src/services/text_to_image/routes.py), [`schemas.py`](../../../../backend/src/services/text_to_image/schemas.py) | HTTP API |

Depends on: `openai` and `google-genai` SDKs, `httpx`, `core.log_client` (`log_provider_request` ledger), `core.config.settings.images_output_dir()` (`<OUTPUT_DIR>/images/<provider>`).

## Service Interface / ServiceProvider Contract

**Service:** `TextToImageService(Service)` — `service.py:29`; `SUPPORTED_STRATEGIES = (FIRST_SUCCESS,)`, default `FIRST_SUCCESS` **[V]**.

| Method | Meaning |
| --- | --- |
| `async generate(request: ImageRequest, client, *, provider_name=None)` → `GeneratedImage` | With `provider_name`, only that **active** provider runs. Without it, active providers are tried in priority order until one succeeds (`first_success`) |
| `generation_parameters(provider_name, request)` → `dict` | Settings the named provider would use (recorded in sidecars and headers) |

**Provider contract:** `TextToImageProvider(ServiceProvider, ABC)` — `models.py:142`, "Common interface and shared machinery for image providers" **[V]**.

- **Abstract:** `async fetch_image(request, client) -> ImagePayload`, which performs exactly one attempt against the external service.
- **Shared, concrete:** `async generate(request, client)` (runs `fetch_image` under the governor and logs the attempt), `resolve_model`, `resolve_size`, `resolve_quality`, `resolve_negative_prompt`, `build_prompt` (prefix/suffix), `build_output_path`, `generation_parameters`, `estimate_cost`.

`ImageRequest` fields: `request_id`, `prompt`, `filename_text`, `negative_prompt`, `language_code`, `size`, `quality`, `output_format` (default `png`), `seed`, `metadata`.

## Implemented Providers

| Class | Registered name | Path | Mechanism | Credential env var |
| --- | --- | --- | --- | --- |
| `OpenAIImageProvider` | `openai` | `providers/openai_image.py:20` | Official OpenAI SDK | `OPENAI_API_KEY` |
| `OpenAIHttpImageProvider` | `openai_http` | `providers/openai_http_image.py:22` | Raw HTTP to the OpenAI Images API | `OPENAI_API_KEY` |
| `GenAIImageProvider` | `genai` | `providers/genai_image.py:23` | `google-genai` SDK | `GEMINI_API_KEY` |
| `GenAIHttpImageProvider` | `genai_http` | `providers/genai_http_image.py:30` | Raw HTTP to Google GenAI | `GEMINI_API_KEY` |
| `ComfyUIImageProvider` | `comfyui` | `providers/comfyui_image.py:37` | Self-hosted ComfyUI server: submits a workflow graph, then polls for the result | `COMFYUI_API_KEY` (only behind an authenticating proxy) |

Each provider overrides `api_key_env_name()` with a fixed variable name. These names do not follow the base class's config-file-name convention **[S]**.

## Configured Provider Instances

| File (`backend/config/text_to_image/`) | Provider | Enabled | Priority | Config sections |
| --- | --- | --- | --- | --- |
| [`openai.json`](../../../../backend/config/text_to_image/openai.json) | `openai` | **yes** | 1 | `generate` (model, size, quality, prompt prefix/suffix), `traffic`, `pricing` |
| [`openai_http.json`](../../../../backend/config/text_to_image/openai_http.json) | `openai_http` | no | 2 | `generate` (+ `endpoint`), `traffic`, `pricing` |
| [`genai.json`](../../../../backend/config/text_to_image/genai.json) | `genai` | no | 3 | `generate` (api, model, aspect ratio, image size, MIME type, negative prompt), `traffic`, … |
| [`genai_http.json`](../../../../backend/config/text_to_image/genai_http.json) | `genai_http` | no | 4 | same as `genai.json` |
| [`comfyui.json`](../../../../backend/config/text_to_image/comfyui.json) | `comfyui` | no | 5 | `generate` (base URL, `workflow_file`, checkpoint, size, steps, cfg, seed, polling, headers, …) |

ComfyUI's workflow template is [`workflows/default_txt2img.json`](../../../../backend/config/text_to_image/workflows/default_txt2img.json). It is not a provider instance: the registry loads only `*.json` directly in `text_to_image/` **[V]**. The `traffic` section holds `max_concurrency`, `min_interval_seconds`, `max_retries`, `backoff_base_seconds`, `backoff_max_seconds` and `timeout_seconds`.

## Selection and Lifecycle

- `FIRST_SUCCESS` over active providers in ascending priority. A named provider bypasses the strategy, but it must be active.
- One `RequestGovernor` per provider applies the traffic policy. The governor is the **only** retry layer, so SDK retries are disabled.
- Providers are replaced on registry reload.

## HTTP API

Three internal operations under `/internal/image-generators`, all `require_internal` **[V]**. Full contract: [`openapi.yaml`](openapi.yaml).

| Operation | Returns |
| --- | --- |
| `GET /internal/image-generators/` | Configurations with state |
| `POST /internal/image-generators/{provider}/generate` | JSON result for one request with the named provider |
| `POST /internal/image-generators/generate-image` | Raw image bytes (`media_type` from the extension) + `X-Image-Provider` + `X-Image-Parameters` (base64 JSON); 400 empty prompt, 404 unknown provider, 500 all failed, 503 none active |

No CLI command covers this service (discovery §7).

## Errors, Observability and Security

- Errors: `NoActiveImageProvidersError` → 503, `ImageProviderNotFoundError` → 404, `AllProvidersFailedError` and unexpected errors → 500. Governor retry exhaustion surfaces as a failed `GeneratedImage` or a provider error **[S]**.
- Observability:
  - The router uses `LoggingAPIRoute.for_source("text_to_image", event_prefix="image_generator")`.
  - Every attempt is written to the provider ledger (model, latency, cost estimate, attempts) via `log_client.log_provider_request`.
- Security and cost:
  - Each call can spend money on a paid API, so the governor limits and the internal-only access are cost controls.
  - Never enable paid providers from tests. The tests use stubs and mocked HTTP.

## Testing

| Command | Scope | Baseline |
| --- | --- | --- |
| `cd backend && ../.venv/bin/python -m pytest -q src/services/text_to_image` | 8 files, 121 tests: governor (30), ComfyUI (22), HTTP providers (14), provider base (12), pricing (8), templating (6), service (9), integration (20) | Passed in the 919-test baseline **[V]** |
