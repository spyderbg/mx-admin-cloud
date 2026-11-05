# `voice_optimization` service

> Registered service · `SERVICE_NAME = "voice_optimization"` · owner role: **backend** · HTTP spec: [`openapi.yaml`](openapi.yaml) (generated) · evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified — see [discovery](../../../discovery/README.md)

## Purpose and Usage

Post-processes generated voice files: resampling, mono down-mix and VBR re-encoding, using ffmpeg or pydub. Its main caller is the content service. `POST /api/v1/content/…/voices/{voice_id}/optimize` creates **one optimized variant per active optimizer configuration**, named after the source voice and the configuration, e.g. `<item>_en_01_<configuration>.mp3` (see `backend/CLAUDE.md`). Content reads this service's provider strategy over HTTP to decide whether to run all optimizers or stop after the first success.

## Location and Dependencies

| Path | Role |
| --- | --- |
| [`backend/src/services/voice_optimization/__init__.py`](../../../../backend/src/services/voice_optimization/__init__.py) | Registration (`config_subdir="voice_optimizers"` — note: **not** the package name) |
| [`service.py`](../../../../backend/src/services/voice_optimization/service.py) | `VoiceOptimizationService`, `AudioOptimizerFactory` |
| [`models.py`](../../../../backend/src/services/voice_optimization/models.py) | `AudioOptimizerBase` contract (`:18`) |
| [`providers/ffmpeg_optimizer/`](../../../../backend/src/services/voice_optimization/providers/ffmpeg_optimizer/), [`providers/pydub_optimizer/`](../../../../backend/src/services/voice_optimization/providers/pydub_optimizer/) | Concrete optimizers |
| [`normalizer.py`](../../../../backend/src/services/voice_optimization/normalizer.py) | `normalize_audio()` — converts raw provider output to WAV for evaluation; a **function, not a provider**; re-exported from `__init__.py`, with no other live caller **[S]** |
| [`routes.py`](../../../../backend/src/services/voice_optimization/routes.py), [`schemas.py`](../../../../backend/src/services/voice_optimization/schemas.py) | HTTP API (`OptimizeRequest`: `raw_path`, `output_path`) |

Depends on: the `ffmpeg` executable on `PATH` (ffmpeg optimizer) and the `pydub` package (pydub optimizer).

## Service Interface / ServiceProvider Contract

**Service:** `VoiceOptimizationService(Service)` — `service.py:20`; `SUPPORTED_STRATEGIES = (ALL_PROVIDERS, FIRST_SUCCESS)`, default `ALL_PROVIDERS` **[V]**.

| Member | Meaning |
| --- | --- |
| `get_active_providers()` / `active_optimizers` (alias property) | Enabled optimizers |
| `async optimize(input_path, output_path)` → `Path` | Runs **all** active optimizers **in sequence**, feeding each output to the next. **No caller exists today** **[S]**: the route runs a single named optimizer, and content runs one optimizer per configuration over HTTP. Treat it as unused. |

**Provider contract:** `AudioOptimizerBase(ServiceProvider)` — `models.py:18`, "Common interface for all audio optimizer implementations" **[V]**. It declares `@abstractmethod async optimize(input_path: Path, output_path: Path) -> Path`. **It does not inherit `ABC`, so the abstract method is not enforced at instantiation** **[V]**.

## Implemented Providers

| Class | Registered name | Path | Mechanism |
| --- | --- | --- | --- |
| `FfmpegAudioOptimizer` | `ffmpeg` | `providers/ffmpeg_optimizer/provider.py:13` | Runs `ffmpeg -y -i <in> …` via `asyncio.create_subprocess_exec` with the configured sample rate, channels and VBR quality; raises if `ffmpeg` is not on `PATH` |
| `PydubAudioOptimizer` | `pydub` | `providers/pydub_optimizer/provider.py:12` | Re-encodes through `pydub` |

## Configured Provider Instances

| File (`backend/config/voice_optimizers/`) | Provider | Enabled | Priority | Config keys |
| --- | --- | --- | --- | --- |
| [`ffmpeg_low.json`](../../../../backend/config/voice_optimizers/ffmpeg_low.json) | `ffmpeg` | yes | 1 | `sample_rate`, `mono`, `vbr_quality` |
| [`ffmpeg_high.json`](../../../../backend/config/voice_optimizers/ffmpeg_high.json) | `ffmpeg` | yes | 2 | `sample_rate`, `mono`, `vbr_quality` |
| [`pydub_optimized.json`](../../../../backend/config/voice_optimizers/pydub_optimized.json) | `pydub` | yes | 3 | `sample_rate`, `mono`, `vbr_quality` |

All three are active at baseline **[V]**. `ffmpeg_low.json` and `ffmpeg_high.json` are two **instances** of one class.

## Selection and Lifecycle

- `/internal/optimizers/{optimizer}/optimize` runs exactly the named **active** configuration and bypasses the strategy.
- Content `optimize_item_voice` reads `GET /internal/configurations/voice_optimization/strategy`:
  - `ALL_PROVIDERS` creates a variant per active configuration;
  - `FIRST_SUCCESS` stops after the first success.

  (Content provider; see `backend/CLAUDE.md` "Provider selection strategy".) **[S]**
- No strategy is stored for this service in `_service_strategies.json`, so the default `ALL_PROVIDERS` applies **[V]**.

## HTTP API

Two internal operations under `/internal/optimizers`, both `require_internal` **[V]**. Full contract: [`openapi.yaml`](openapi.yaml).

| Operation | Behavior |
| --- | --- |
| `GET /internal/optimizers/` | Optimizer configurations with state |
| `POST /internal/optimizers/{optimizer}/optimize` | `OptimizeRequest {raw_path, output_path}` → `{success, output_path}`; 404 optimizer not found or inactive, 400 raw file missing, 500 on optimizer failure |

The CLI has `list optimizers`, but no optimize command.

## Errors, Observability and Security

- Errors are listed above. Optimizer exceptions become 500 with the exception text **[S]**.
- **Security:**
  - The request body supplies **arbitrary server-side paths**: `raw_path` is read and `output_path` is written. This is the same class of issue as evaluation (discovery SEC-8; `backend/code_improvements.md` §4.3).
  - The endpoint must remain internal-only. Path confinement would be a Backend task.
  - It spawns `ffmpeg` processes with argument lists (no shell).

## Testing

`backend/src/services/voice_optimization/__tests__/test_voice_optimization_service.py` covers only the normalizer's output file naming **[V]**. The content tests cover the *caller* side: `test_content_filesystem_item_voices.py` patches `FileSystemContentProvider._active_voice_optimizers` and the optimize call with fakes **[S]**. Neither optimizer class is tested.
