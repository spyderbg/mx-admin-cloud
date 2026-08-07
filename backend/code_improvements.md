# Backend Code Improvements

Full code review of `backend/` (FastAPI service, service registry, providers, content management,
TTS/translation/optimization/evaluation/logging services). Line references are against the current
`dev` branch. Findings are grouped by severity and theme; a prioritized action list is at the end.

---

## 1. Critical — broken or dead code paths

### 1.1 `POST /internal/generate` crashes: the pipeline is commented out
- `src/core/pipeline.py` is **~380 lines of fully commented-out code**. `run_pipeline` does not exist.
- `src/api/internal/jobs_routes.py:95` still calls `_run_job(...)`, which is also commented out
  (lines 45–69). The code even carries `# pylint: disable=undefined-variable` to silence the error.
- Result: uploading a file to `/internal/generate` raises `NameError` and returns HTTP 500; the
  README and CLI (`kiddo-cli generate run`) still document this as a working feature.

**Improvement:** either restore the pipeline (rewrite it against the current internal endpoints:
`/internal/translators/translate`, `/internal/generators/{provider}/generate`,
`/internal/optimizers/{opt}/optimize`, `/internal/evaluators/evaluate`) or remove the job routes,
the job store wiring, and the CLI/README references until it exists. Dead code of this size hides
the real API surface and misleads every consumer.

### 1.2 Content provider calls a non-existent internal endpoint
- `src/services/content/providers/file_system.py:98` posts to `{API_BASE_URL}/internal/translate`.
  No such route exists — the translation route is `/internal/translators/translate`
  (`src/services/text_translation/routes.py`). Every call returns 404 and
  `raise_for_status()` throws.
- Even if the URL were fixed, line 109 reads `response.json()["translated_text"]`, but the
  translate endpoint returns `{"success": ..., "lang_text": {"lang_code", "text"}}`.
- Impact: `create_category`, `create_item`, and `rename_category` fail for every non-English
  language (translation step) — the core content-creation flow is broken end to end.

**Improvement:** fix the URL and response parsing, and add one integration test that exercises
`create_category` against the real app (ASGI transport) rather than mocked HTTP, so a route rename
can never silently break the content service again. Long term, generate a typed internal client
from the OpenAPI spec (you already generate a TS client for the frontend — the same discipline
would have caught this).

### 1.3 `POST /api/v1/content/items` passes a coroutine as image bytes
- `src/services/content/routes.py:1187`: `image_data = image.read()` inside a **sync** handler.
  `UploadFile.read()` is async — this returns a coroutine object, not bytes. The emptiness check
  passes (a coroutine is truthy) and the provider later crashes on `fh.write(image_data)`.
- All other upload handlers correctly use `image.file.read()`.

**Improvement:** change to `image.file.read()` (or make the handler `async` and `await` it). Add a
test that posts a real multipart body to this endpoint.

### 1.4 `apply_runtime_payload` wipes all other providers of a service
- `src/services/service_registry.py:414–426` (`_replace_provider_by_identity`) matches providers by
  `getattr(provider, "provider_name", "")` / `getattr(provider, "service_name", "")`. Those
  attributes **do not exist** — `ServiceProvider.__init__` stores them as `self.provider` and
  `self.service` (`src/services/models.py:114–122`). Both getattrs therefore return `""` for every
  provider, the "same identity" filter matches everything, and the merged list keeps **only the new
  provider**. One `POST /internal/configurations/apply` silently drops all other runtime providers
  of the service until the next disk reload.
- The sibling helper `_ensure_runtime_configuration_provider` (line 443) had to work around exactly
  this with `getattr(provider, "provider_name", getattr(provider, "provider", ""))` — a hint the
  attribute naming is inconsistent.

**Improvement:** match on `provider.name` / `provider.service` / `provider.provider` explicitly
(no `getattr` with defaults — let a missing attribute fail loudly), and add a unit test that applies
a runtime payload with two providers loaded. Also consider identity = config-file name (`name`),
since two configs may share the same `provider` implementation (e.g. `openai_en_fr`, `openai_en_es`).

### 1.5 `apply_runtime_payload` crashes on invalid envelopes
- `src/services/service_registry.py:301–314`: when `ServiceProvider.from_dict` returns `None`, the
  error string is set but execution **continues** into `criteria.can_accept(common_provider)` and
  `common_provider.service` — an `AttributeError` on `None` → HTTP 500 instead of a clean
  validation error. Return early after each failed check.


---

## 2. Architecture — service separation ("each microservice")

The registry/plugin design (`SERVICE_CLASS` / `SERVICE_NAME` / `SERVICE_ACCEPT_CRITERIA` exported by
each package, discovered by `pkgutil`) is genuinely good: adding a service requires no registry
changes, config ingestion lives outside services, and routes co-located with each service keep the
domains separable. The issues are where the boundaries are not actually respected:

### 2.1 The documented `core/ → services/` boundary is violated
- README/CLAUDE.md state "`core/` has **zero** imports from `services/`". In reality
  `src/core/log_client.py:17–31` imports route constants and schemas from
  `src.services.logging.routes` / `.schemas`, and `src/core/config.py:9` imports
  `GeneratedAudio` from `src.core.models` (fine) but also gets imported *by* nearly every service —
  making `core` both below and above services in practice.
- Either enforce the boundary (move the logging wire-contract into `core/` or into a shared
  `contracts/` package, and have the services import it) or drop the claim from the docs. An
  import-linter contract (`lint-imports`) in CI would make the rule verifiable instead of aspirational.

### 2.2 In-process HTTP as a service boundary is expensive and fragile
- The content provider (`file_system.py`) makes **synchronous** `httpx.post` calls back into its own
  server for every translation and every TTS word (4 languages × 2 calls per created item), each
  with a freshly signed 30-second internal JWT. Every log line also becomes an HTTP POST
  (`core/log_client.py`), awaited inside TTS provider hot paths.
- This buys the "microservice" shape but pays for it on every request: serialization, TCP,
  auth, and a hard dependency on `API_BASE_URL` being reachable from inside the process. It also
  makes unit testing awkward (the content tests must stand up the whole app or mock httpx).
- **Improvement options** (pick one and apply consistently):
  1. Keep HTTP boundaries, but route all internal calls through **one shared async client**
     (created in lifespan, injected) with the startup internal token, connection pooling, timeouts
     and retry policy in one place — today every call site constructs its own client and policy.
  2. Accept that these services live in one process and call the sibling **service objects through
     the registry** (`registry.get_service("text_translation")`), keeping HTTP only for the
     genuinely external consumers (CLI, frontend). The registry already gives you the seam; HTTP
     can be reintroduced per-service when a service is actually split out.
- For logging specifically, an in-process fallback is the pragmatic choice: `LoggingService` is
  already in the same process; posting JSON to yourself per log line adds latency (up to the 5 s
  client timeout) and silently drops logs when the HTTP loop is saturated.

### 2.3 Evaluation service is registered but bypassed
- `services/evaluation/` exports the full service plugin (service class, factory, acceptance), yet
  `routes.py` never touches `EvaluationService`: it lists evaluator configs from the
  **text_to_speech** service and instantiates `EvaluationEngine` ad hoc per request. There is also
  no `config/evaluation/` directory, so the registered service always holds zero providers.
- The engine itself hardcodes STT backends in an `if/elif` chain
  (`engine.py:96–104`) — the one place in the codebase where "provider" logic is not
  provider-classes-plus-factory.
- **Improvement:** make STT backends real `EvaluationServiceProvider`s (openai/elevenlabs/speaktor)
  configured under `config/evaluation/`, let `EvaluationService` pick by priority, and delete the
  engine's dispatch chain. That aligns evaluation with every other service and makes the
  "evaluators" listed by `/internal/evaluators` the ones actually used.

### 2.4 Duplicate service-resolution idioms
- Some routes use `get_typed_service(registry, NAME, Type)` inline; content routes define a local
  `get_content_service` dependency + `ContentServiceDep` annotation. Standardize on the annotated
  dependency pattern (`XServiceDep = Annotated[XService, Depends(...)]`) for every service — it
  removes ~4 lines per handler and gives you one place to add readiness checks.

---

## 3. Provider model — "work together or pick one by priority"

Each service currently implements a *different* cooperation model, only some of them intentional:

| Service | Behavior today | Assessment |
|---|---|---|
| text_to_speech | First active by priority, or explicit by name (`service.generate`) | Correct "pick one", but **no fallback**: if the chosen provider fails, the request fails even when a lower-priority provider could serve it |
| text_translation | **Chains all** active providers; each provider's output becomes the next provider's input (`service.translate_text`) | Almost certainly wrong (see 3.1) |
| voice_optimization | Pipeline: all active optimizers in sequence | Reasonable, but the only real consumer (`/internal/optimizers/{name}/optimize`) picks one by name; `optimize()` chaining is unused |
| content | First active; **falls back to a disabled provider** if none active (`_select_provider`, `service.py:243–250`) | Fallback to a disabled provider contradicts `enabled` semantics — drop it or log loudly |
| configuration | Hardcoded `get_provider("file_system")` then first provider | Fine (single impl), but the name lookup defeats the factory abstraction |
| logging | Priority groups with fallback, plus name-routed `log()` | Most elaborate model; OK, but see 3.3 |
| evaluation | if/elif on `stt_provider` string | Not a provider model at all (see 2.3) |

### 3.1 Translation chaining is a correctness and cost bug
`TextTranslationService.translate_text` (`service.py:64–107`) feeds each provider's *output* into
the next provider while still labeling it with the **original** input language. With the shipped
configs (static CSV at priority 1, three OpenAI configs at priority 2), a word found in the static
CSV is then re-sent to OpenAI — with the Bulgarian text labeled as English — and re-translated. You
pay for OpenAI even when the static lookup succeeded, and the second hop can mangle the result.

**Improvement:** decide the semantics explicitly and encode them:
- *Fallback (recommended, and what the README describes):* iterate active providers by priority and
  **return on the first non-None result**.
- If multi-hop chains are genuinely wanted later, model them as an explicit chain config
  (`[{provider, from, to}, ...]`), not as an emergent property of "every enabled config runs".
Also fix the route inconsistency: on success `translate_text` returns the raw
`body.target_lang_code` unnormalized, on failure a normalized code (`routes.py:121–130`).

### 3.2 Add failure fallback to TTS selection
`_resolve_active_provider` picks providers[0] and `generate` propagates any exception. Since
priority already expresses preference, iterate active providers in priority order and fall back on
provider errors (with a logged warning), returning the first success. That single change delivers
the "providers that can work together" requirement for TTS with the config model you already have.

### 3.3 Provider activation is inconsistent for TTS
`TTSProvider`'s docstring claims `is_active()` is overridden to follow `config.generate.enabled` —
it is **not** (no override exists in `text_to_speech/models.py`); the top-level `enabled` decides.
Meanwhile `GET /internal/generators/` reports `enabled` from `config.generate.enabled`
(`routes.py:62`). So the list endpoint can show a provider as disabled while `generate-word`
happily uses it (or vice versa). Pick one source of truth (top-level `enabled`, with
`generate.enabled` as an optional additional gate implemented in an actual `is_active` override)
and make the listing use `is_active()`.

### 3.4 API-key resolution by config-file name is a trap
`ServiceProvider.api_key_env_name()` returns `f"{self.name.upper()}_API_KEY"` where `name` is the
**config file stem**. `openai.json` → `OPENAI_API_KEY` works, but a second config like
`openai_en_fr.json` (translation) resolves `OPENAI_EN_FR_API_KEY`, which is never set; the provider
then raises "OPENAI_API_KEY is not set" — a misleading error caused by file naming. `static.json`
already had to override the method to reuse `ELEVENLABS_API_KEY`.

**Improvement:** key the env var on `self.provider` (the implementation) with an optional
`config.api_key_env` override, not on the file name. Verify: `OpenAITextTranslate.resolve_api_key`
currently only works because the shipped file is the only one whose stem starts with `openai` —
`openai_en_bg.json` etc. are broken today unless those exact env names are exported.

### 3.5 Duplication between ElevenLabs and Static TTS providers
`static_tts.py` re-implements ~100 lines of `elevenlabs_tts.py` (voice-settings normalization,
payload build, request, logging, `GeneratedAudio` assembly) and imports the private helpers
`_to_float` / `_to_bool` across modules. Extract a shared `ElevenLabsBackendMixin` (or make Static a
thin subclass overriding language resolution) so a fix to ElevenLabs handling can't miss its twin.
While there, both files deliberately honor the config typo `speder_boost` — migrate the configs and
delete the tolerance.

### 3.6 Provider failure results are inconsistent
"No actor configured" returns a `GeneratedAudio(success=False, ...)`, but "no API key" and
"missing voice_id" raise `RuntimeError`, and Speaktor raises on HTTP errors while OpenAI/ElevenLabs
use `raise_for_status`. Callers therefore need both an exception path and a `success=False` path.
Standardize: providers return `GeneratedAudio` (success or failure) for *expected* conditions and
raise only for programming errors; or always raise typed provider exceptions the service converts.

---

## 4. Security

### 4.1 Insecure-by-default settings (highest priority)
`src/core/config.py` ships with:
- `AUTH_DISABLED: bool = True` — every request is treated as an **admin** (`verify_token`,
  `auth.py:26–27`) unless the deployment explicitly opts into auth;
- `DEBUG_MODE: bool = True` — startup **removes the UDS and admin dependencies** on the user routes
  (`main.py:96–103`);
- `SECRET_KEY: str = "your-secret-key"` — forgeable JWTs if unset;

`docker/docker-compose.yml` sets **none** of `AUTH_DISABLED`, `DEBUG_MODE`, or `SECRET_KEY`, so the
published containers (backend on host port 9000) run with auth disabled, debug bypasses on, and the
default secret. Flip the defaults (`AUTH_DISABLED=False`, `DEBUG_MODE=False`), generate/require
`SECRET_KEY` (fail startup in non-debug when it is the placeholder), and set explicit values in
compose. Insecure modes should be an explicit opt-in, never the default.

Related compose issue: the content volume is hardcoded to one machine's path
(`/home/spyder/Nextcloud/_Projects/KiddoWords/Content`) in all three services. Since the content
folder is explicitly allowed to live anywhere on the host, parameterize it
(`${CONTENT_HOST_DIR:?set in .env}:/app/content`) so the compose file is portable. The unused
`INTERNAL_SERVICE_TOKEN` env var should be removed (the README itself notes `Settings` ignores it).

### 4.2 `/api/v1/status` leaks the admin password
`core/tasks.py:_env_from_settings` masks `*_API_KEY` and `SECRET_KEY` but returns everything else
from `settings.model_dump()` — including **`DEFAULT_ADMIN_PASSWORD`** — over an endpoint that is
effectively unauthenticated with the default `AUTH_DISABLED=True`. Switch to an explicit allowlist
of safe keys instead of a denylist.

### 4.3 Path traversal via internal optimizer/evaluator endpoints
`POST /internal/optimizers/{name}/optimize` accepts arbitrary `raw_path`/`output_path` and
reads/writes them as-is (`voice_optimization/routes.py:85–96`); `/internal/evaluators/evaluate`
similarly reads any `raw_path`/`normalized_path`. With an admin JWT (or `AUTH_DISABLED`), this is
an arbitrary-file-write primitive (ffmpeg output to any path). Constrain both to resolved paths
under `settings.output_path` (e.g. `path.resolve().is_relative_to(output_root)`).

### 4.4 Upload hardening
- No size limits on any upload (images, audio, translate-file, generate) — everything is read fully
  into memory (`image.file.read()`); a large body can exhaust RAM. Enforce max sizes.
- File type is inferred from the client-supplied extension only, and unknown extensions are
  silently coerced to `.png` — a WEBP or an HTML file gets stored as `.png` and then served back
  from the same origin via `app.mount("/content", StaticFiles(...))`. Validate magic bytes
  (or at least content-type) for images and mp3/audio.
- `translate_file` writes temp uploads under `OUTPUT_DIR` instead of `TEMP_DIR`
  (`text_translation/routes.py:176`) — use `settings.temp_path` (it exists precisely for this).

### 4.5 Static content mount
`app.mount("/content", StaticFiles(directory=...))` (`main.py:112`) serves the whole content tree
**without authentication**, including any file an upload endpoint writes there. If content is not
meant to be public, wrap it in an authenticated route; if it is, document that explicitly.

### 4.6 Smaller items
- CORS: no middleware configured; fine while the frontend is same-origin/proxied — revisit before
  serving the SPA from another origin.

---

## 5. Content data model — CSV alignment is fragile

The content domain keys everything on **row position across parallel CSV files**
(`categories_ids.csv` ↔ `categories_{lang}.csv`; `items_ids.csv` ↔ `items_{lang}.csv`). Multiple
code paths can silently break the alignment:

- `create_item` appends `items_en.csv` and `items_ids.csv` **before** translating
  (`file_system.py:660–661`), then appends other languages one by one. A translation failure for
  `fr` aborts after `en`/ids/possibly `es` were appended — every language file now has a different
  length and **all subsequent items display shifted translations**. Same pattern in
  `create_category` (voices generated per language mid-loop).
- Appends are not atomic and there is no cross-process/file locking — two concurrent creates
  interleave rows across four files.
- Rename/archive/restore do not maintain the registry at all (§1.6).

**Improvements, in increasing order of ambition:**
1. Make every multi-file mutation *all-or-nothing*: compute all translations/audio first, then
   append all rows inside one lock (a simple `filelock` around the category dir), and roll back
   the folder on failure (`create_item` currently leaves a half-created folder).
2. Replace positional CSVs with one `manifest.json` per category (id, names per language,
   description per language, item list with per-item names/voices). One file per category makes
   mutations atomic-by-rename, self-describing, and kills the row-alignment invariant entirely.
   The provider abstraction (`ContentProvider`) is the right seam for this migration.
3. Icon generation (`_generate_icon`) calls OpenAI **directly** from the content provider with a
   hardcoded `dall-e-2` — inconsistent with everything else being a configurable provider service.
   Consider an `image_generation` service package like the others (config-driven model/size), which
   also removes the hard OpenAI dependency from content.

Also worth fixing in this area:
- `item_voices` with an unknown item builds ids like `"None-v-en"` (`item_folder_name` is `None`,
  `file_system.py:1077–1090`) — raise `CategoryNotFoundError` like `_resolve_item_path` does.
- `create_item` writes the first image as `01.png` while `add_item_image` writes
  `{item}_{01}.png` — two naming schemes for the same concept; unify (and migrate old files).
- `restore_category` uses `copytree` (archive copy stays around) while `archive_category` uses
  `move` — decide whether archive entries are consumed on restore, and clean up on success.

---

## 6. API design

- **Same URI, different identifier semantics:** `GET /content/categories/{category_id}` expects an
  ID while `PATCH`/`PUT`/`DELETE /content/categories/{category_name}` expect a name — on the *same
  path template* (`routes.py:60–61`). Clients cannot tell which to send. Finish the id migration
  (commit `22c25cb` started it): all item/category addressing by ID, names only as payload.
- **501 stubs are documented as features:** README tables list voices/images routes marked
  "stub — 501" that are now implemented, while `PUT /categories/{name}` and item update/delete are
  real 501s. Keep the OpenAPI spec authoritative and prune the stale README tables (§8).
- **Repeated upload boilerplate:** the 10-line filename/emptiness/extension block appears 6×
  in content routes; the try/except → HTTPException ladder appears ~15×. Extract a
  `read_image_upload(upload) -> tuple[bytes, str]` helper and one
  `@content_error_handler` decorator (or FastAPI exception handlers registered for
  `ContentError` subclasses — `app.add_exception_handler` removes the ladders entirely).
- **`response_model` + manual dict building:** several internal routes declare
  `response_model=list[GeneratorProviderInfo]` but return hand-built dicts; return the Pydantic
  models so schema drift is caught at build time.
- **Per-request `httpx.AsyncClient`:** `generate_single`, `generate_word`, `evaluate` each build a
  fresh client per request; share the lifespan client (§2.2, option 1).

---

## 7. Performance and blocking work

- All content route handlers are **sync `def`** — FastAPI runs them in the threadpool, but each one
  performs sequential internal HTTP calls (create_item: up to 8 calls) with per-call token signing.
  Batch per-language work concurrently (`asyncio.gather` with the shared client) once the handlers
  are async; a 4-language item creation drops from ~8 RTT to ~2.
- `_load_category_localized_names` re-reads every root CSV on **every** call — `list_categories`,
  `category_id_by_name`, `category_details`, and every image/voice operation each parse the full
  registry. Cache with an mtime check, or fold into the manifest redesign (§5).
- `StaticTextTranslate._load` reads all category CSVs at construction (every registry reload) —
  fine at current scale; add an mtime-based reload if content grows.
- `LoggingService.emit`'s per-call HTTP hop (§2.2) is the largest hidden latency in TTS providers:
  `await log_provider_request(...)` sits between the provider API call and writing the audio bytes.
  Fire-and-forget (`asyncio.create_task`) or in-process emit would take it off the critical path.
- `JobStore` TTL eviction and `asyncio.create_task(_watch_configs())` in lifespan hold no reference
  to the tasks; keep the task handles (and cancel them on shutdown) so they aren't GC'd mid-flight
  and shutdown is clean.

---

## 8. Documentation drift (README as source of truth)

The user-facing docs describe several behaviors the code no longer has. Since the project treats
README files as the primary reference, these are worth fixing promptly:

| Doc claim | Reality |
|---|---|
| `core/` has zero `services/` imports | `core/log_client.py` imports from `services.logging` |
| `POST /internal/translate` exists | Route is `/internal/translators/translate`; the old path is referenced only by the (broken) content provider |
| Logging emit uses `provider_category == "normal"` | Code selects providers with **empty** category (`service.py:198–201`); shipped configs use `""`/`"requests"`, and README's example configs (`priority: 2`, `"normal"`) don't match `config/logging/*.json` (`priority: 20`, no category) |
| `src/core/content_naming.py` exists | File does not exist (helpers live on `ContentProvider` / `Settings`) |
| Voices/images/files routes "stub — 501" | Most are implemented; different ones are stubs |
| TTS `is_active` driven by `generate.enabled` | Not implemented (docstring claim only, §3.3) |
| `kiddo-cli generate run` runs the pipeline | Pipeline endpoint is dead (§1.1) |

Suggestion: add a doc-check step to `/verify` that at minimum greps documented route paths against
`app.routes` (a 20-line pytest), and move endpoint tables out of README into generated OpenAPI docs.

---

## 9. Code quality / maintainability

- **`ServiceProvider` attribute naming:** constructor parameters are `service_name`/`provider_name`
  but attributes are `service`/`provider` — this mismatch already caused bug §1.4. Rename
  attributes to match the constructor (or vice versa) across the codebase.
- **`ConfigApplyResult`** is declared `@dataclass` but defines `__init__` manually
  (`acceptance.py:30–37`) — drop the decorator or the manual init.
- **`Service.get_provider`** scans active then all providers with duplicated loops — one loop over
  `get_all_providers()` with an `is_active` preference, or a dict keyed by name.
- **Registry `dump()`** prints with Rich directly to stdout on startup and on every hot reload —
  route it through logging (respecting `LOG_PROFILE`) so prod logs stay structured; same for
  `_log_apply_result`.
- **`build_output_stem_from_result`** creates a dynamic `type("RequestStub", ...)` — replace with a
  small dataclass or accept keyword args.
- **Naked pass-through orchestrators:** `ContentService` is ~200 lines of one-line delegations to
  `_select_provider()`. Fine as a seam, but every new provider method needs three edits (ABC,
  service, route). Consider exposing the provider directly to routes via the service
  (`service.provider()`), keeping the service for cross-provider logic only.
- **Speaktor `_render_template`** applies `str.format(**context)` to arbitrary config strings —
  a stray `{` in a prompt template raises `KeyError`/`ValueError` at request time, and `{api_key}`
  can be interpolated into any field (it's in the context). Use `string.Template` with explicit
  placeholders, and don't include the API key in the general template context.
- **Broad `except Exception: continue`** in `translate_text` hides provider misconfiguration
  entirely (an OpenAI auth failure is indistinguishable from "no translation"). Log at warning
  level with provider name; count failures in the result.
- **Custom TRACE level** registered at import of `struct_log.py` — do it in `configure()` so
  importing the module has no global side effects (pattern repeats: every provider registers itself
  at import time, which is why `ServiceRegistry._discover` has import side-effect ordering
  concerns; acceptable, but document it).
- Commented-out experiment code left in `main.py:128–130` (`test_app`) — delete.

---

## 10. Testing gaps

Existing tests are substantial (content, configuration, TTS, translation, logging) but the critical
bugs above live exactly in the untested seams:

1. No test posts to `/internal/generate` (dead pipeline would fail instantly).
2. No test exercises `POST /api/v1/content/items` with real multipart (coroutine bug §1.3).
3. Content service tests mock the internal HTTP calls — add one ASGI-transport integration test
   that runs create_category end-to-end against the real translator route (static provider only,
   no external APIs needed).
4. No test covers `apply_runtime_payload` with multiple loaded providers (§1.4) or invalid
   envelopes (§1.5).
5. No test covers rename → details-by-id (§1.6).
6. Add a route-inventory test asserting documented paths exist (§8).

---

## 11. Prioritized action list

**P0 — broken functionality / security**
1. Fix content-provider translate URL + response key (§1.2); add integration test.
2. Fix `image.read()` coroutine bug in `/content/items` (§1.3).
3. Restore or remove the TTS pipeline and job routes (§1.1).
4. Flip security defaults (`AUTH_DISABLED=False`, `DEBUG_MODE=False`), require real `SECRET_KEY`,
   set explicit values in docker-compose (§4.1).
5. Stop leaking `DEFAULT_ADMIN_PASSWORD` via `/status` (§4.2).

**P1 — correctness of the provider model**
6. Translation: first-success-wins fallback instead of chaining (§3.1).
7. Registry: fix `_replace_provider_by_identity` attributes + early returns (§1.4, §1.5).
8. Rename/archive/restore: maintain the CSV registry (§1.6) and make item creation atomic (§5.1).
9. API-key resolution by provider, not config-file name (§3.4).
10. TTS priority fallback on provider failure (§3.2) and one `is_active` source of truth (§3.3).

**P2 — hardening and structure**
11. Path containment for optimizer/evaluator file paths (§4.3); upload size/type validation (§4.4).
12. Shared internal HTTP client + fire-and-forget logging (§2.2, §7).
13. Turn evaluation into a real provider-based service (§2.3).
14. De-duplicate ElevenLabs/Static providers (§3.5) and content route boilerplate (§6).

**P3 — long term**
15. Category `manifest.json` data model to replace positional CSVs (§5.2).
16. README/doc sync + route-inventory test (§8).
17. Category id/name API unification (§6).
