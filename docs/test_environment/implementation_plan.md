# Test Environment + GitLab Issue Tracking for mx-admin-cloud

> **Migration note (2026-10-30):** this plan is kept as written. Its `scripts/testenv*.py`,
> `scripts/testenvlib/`, `scripts/mxissues/`, `scripts/mxfix/`, `scripts/issues.py`,
> `scripts/app.py`, `scripts/requirements.txt` and root `testenv.toml`/`testenv.local.toml`
> now live under [`test_environment/`](../../test_environment/README.md), which is the
> current reference.

> **Status:** plan, not yet implemented. Nothing in the repo has been changed except the
> addition of this file.
> **Origin document:** [initial_spec.md](initial_spec.md) — the architecture proposal
> this plan responds to.
> **Untracked:** `docs/test_environment/` is not yet in git; `git add` it when you're ready.

## Context

[docs/test_environment/initial_spec.md](docs/test_environment/initial_spec.md)
proposes an autonomous self-healing loop: a local LLM detects
problems, Codex fixes them, a second local LLM reviews, and `pytest`/`lint`/`build` act as
the deterministic judge with final authority. Its core claim — *"AI никога не трябва да има
последната дума върху нещо, което може да бъде проверено детерминистично"* (line 386) — is
sound. But the judge it delegates to barely exists here:

- ~510 backend tests, **zero** covering `src/api/**`, `src/db`, `src/security`, or `providers/`.
- No test runner in the frontend at all. [scripts/validate_tests.py:160](scripts/validate_tests.py#L160)
  silently skips the frontend leg — an autonomous loop would read that as PASS.
- The three `validate_*.py` scripts print human tables, not a machine-readable verdict.

This plan builds two independent pieces the document's loop would need, without building the
loop itself:

**Workstream 1 — the sandbox stack (the judge).** An isolated, runnable environment: the real
Docker images, real FastAPI, real nginx, real browser, pointed at throwaway directories with
external providers replaced by a local stub, driven by Playwright E2E. It tests the wiring
unit tests structurally cannot — nginx proxying, lifespan startup, the content volume, and the
synchronous loopback self-calls in `file_system.py`.

**Workstream 2 — GitLab issues as the task store.** Replaces the document's `tasks.sqlite`
(§2) with the project's real GitLab issue tracker, driving each finding through
`open → in progress → done`. Sources: AI detector findings, E2E/test failures, and manual entry.

**Workstream 3 — selectable execution strategies.** One runner, three modes: `report` (file
issues and stop), `heal` (fix one issue at a time, resetting the environment between each),
and `batch` (fix many in parallel, then verify once). This is where the document's executor
finally lands, behind a pluggable `Fixer` interface.

Workstreams 1 and 2 are independent; 3 depends on both.

**Constraints (user-set):** no application source changes; `docker/docker-compose.yml` stays
unedited and is the base of the stack; test volumes point at separate temp dirs; Playwright
for E2E; local only, no CI; GitLab Free tier assumed; `python-gitlab` + PAT for API access.

---

# Workstream 1 — Sandbox test environment

## Key findings that shape the design

**1. `create_category` and `create_item` hard-fail without upstream providers.**
[file_system.py:996](backend/src/services/content/providers/file_system.py#L996) raises
`ContentOperationError(f"TTS failed for {lang}")`, [:1009](backend/src/services/content/providers/file_system.py#L1009)
raises on icon generation, then `shutil.rmtree`s the folder.
`_translate_item_word` ([:1030](backend/src/services/content/providers/file_system.py#L1030))
calls `_translate_word(..., require_success=True)` and raises on first failure.

So simply disabling every network provider makes the three highest-value write flows
(create category, create item, regenerate voice) **100% untestable**. A stub upstream is not
a nicety — it is what makes the sandbox worth building.

**2. Two providers are cleanly stubbable with zero code changes.**
- `speaktor_tts.py` is fully config-driven: `endpoint`, `headers`, `payload_template`,
  `response_mode` all come from JSON. `_requires_api_key` ([:28](backend/src/services/text_to_speech/providers/speaktor_tts.py#L28))
  only demands a key when `{api_key}` appears in a template — drop the `Authorization`
  header and no key is needed. Setting `response_mode` to anything but `json_url`/`json_base64`
  hits [:166](backend/src/services/text_to_speech/providers/speaktor_tts.py#L166)
  `output_path.write_bytes(response.content)` — one request, raw MP3 back.
- `openai_http_image.py` reads `endpoint` from config and parses `data[0].b64_json`.
- `openai_translate.py:106` does `AsyncOpenAI(api_key=...)` with no explicit `base_url`, so
  the SDK honours `OPENAI_BASE_URL` from the environment.

**3. `text_to_image/openai.json` is `enabled: true, priority: 1`** (verified across all 20
configs). It's the SDK provider and must be flipped **off** in the test config so the
config-driven `openai_http` provider wins.

**4. ElevenLabs cannot be stubbed.** `elevenlabs_tts.py:17` `API_BASE` is a module constant,
and `static_tts.py:13` imports it. Both stay disabled.

**5. Compose merge traps.**
- `container_name` is hardcoded (`kiddo-backend`/`kiddo-frontend`); `-p <project>` does **not**
  isolate it. The override must rename, or the test stack collides with a running dev stack.
- `ports` **concatenate** on merge, they don't replace. Needs the `!override` tag (Compose ≥ 2.24).
- `volumes` replace by container path — so `${TEST_ARCHIVE_DIR}:/app/archive` cleanly
  supersedes `../archive:/app/archive`. Good.
- Named volumes `backend-data`/`tts-output` isolate for free via project name. Leave them alone.

**6. Host env beats `--env-file` in Compose v2.** Both `scripts/app.sh` and `scripts/deploy.sh`
export the real provider keys into the shell. If a developer has sourced them, the test stack
would make **real, billed API calls**. The env-sanitising filter below is load-bearing, not
hygiene.

**7. Only one `data-testid` exists in the whole frontend** — specs must lean on semantic CSS
classes, and no test ids can be added under the no-source-changes rule.

---

## 1. Run modes — Docker and native

**Docker is not installed on this machine** (no `docker`, `colima`, `podman`,
`/Applications/Docker.app`). So the environment supports two interchangeable modes, and
`testenv.py` picks native automatically when Docker is absent (`--mode docker|native` forces
either):

| | Docker mode | Native mode |
|---|---|---|
| Backend | `mxtest-backend` container | `uvicorn src.main:app` on :8000 |
| Frontend | nginx container | `frontend/dist` served by `scripts/app.py` |
| Stub | `mxtest-stub` container | plain `python stub_server.py` process |
| Isolation | bind mounts + project-scoped volumes | env vars on each child process |

### `scripts/app.py` — the native runner (new file)

Today `scripts/app.sh` is a pure Docker Compose wrapper, so it cannot serve this purpose;
`scripts/app.py` is new. It starts the stub, then the backend, then the frontend, waits for
readiness, and tears all three down on exit (PID file, same shape as
[backend/uvicorn_start.sh](backend/uvicorn_start.sh)).

```
app.py start [--backend-port 8000] [--frontend-port 5173] [--env-file .testenv/test.env]
app.py stop | status | logs [backend|frontend|stub]
```

**One trap:** [frontend/vite.config.ts](frontend/vite.config.ts) proxies `/api` and
`/internal` to the backend but serves `/content` itself, from a **hardcoded `../content`** via
`sirv`. Under `npm run dev` the test frontend would therefore read the *real* content
directory. So native mode does **not** use the dev server. `app.py` runs `npm run build` once
(with the `VITE_*` values as env) and serves `frontend/dist` from a small static+proxy handler
that forwards `/api/`, `/internal/` and `/content/` to the backend — mirroring
[docker/nginx.conf](docker/nginx.conf) exactly. That keeps the two modes behaviourally
identical and routes `/content` through the backend's `StaticFiles` mount, which *is* pointed
at `.testenv/content`.

Native mode also has an advantage: `VITE_ARCHIVE_CONFIRMATION_MODE` and friends are build-time
values, so switching confirmation modes costs one `npm run build` instead of a Docker image
rebuild.

Everything below describes Docker mode; native mode reuses the same `.testenv/` tree, the same
seeded fixture, the same neutralised config dir and the same stub.

## 1b. Docker stack isolation

New file `docker/docker-compose.test.yml`, layered on the untouched base:

```
docker compose \
  -f <repo>/docker/docker-compose.yml \
  -f <repo>/docker/docker-compose.test.yml \
  --project-directory <repo>/docker \
  --env-file <repo>/.testenv/test.env \
  -p mxadmin-test up -d
```

`--project-directory` pins relative-path resolution to the base file's directory regardless
of cwd. The override itself uses **only absolute paths** from `test.env`, so path
resolution cannot bite either way.

The override redefines, per service:

- **backend** — `container_name: mxtest-backend`, `restart: "no"`; volumes
  `${TEST_CONTENT_DIR}:/app/content`, `${TEST_ARCHIVE_DIR}:/app/archive`,
  `${TEST_CONFIG_DIR}:/app/config`; `ports: !override ["${TEST_BACKEND_PORT}:8000"]`;
  env `OPENAI_BASE_URL=http://stub:8080/v1`; a healthcheck hitting
  `http://127.0.0.1:8000/openapi.json` via stdlib `urllib` (no `curl` in `python:3.11-slim`,
  and there is no `/health` endpoint). Uvicorn doesn't accept connections until `lifespan`
  finishes, so a 200 there proves DB seeded + registry loaded + content mounted.
- **frontend** — `container_name: mxtest-frontend`, `ports: !override`, the content volume
  repointed, confirmation-mode build args wired to env vars.
- **stub** (new) — stock `python:3.11-slim` with `${TEST_STUB_DIR}:/srv:ro` and
  `command: ["python","-u","/srv/stub_server.py"]`. **No Dockerfile, no image build.**

Not overridden: `CONTENT_DIR`/`ARCHIVE_DIR`/`DB_PATH`/`CONFIG_DIR`/`OUTPUT_DIR` (container-side
paths are already right), the named volumes, and the profile-gated `backend-cli`/`cli` services.

---

## 2. Throwaway directories

```
<repo>/.testenv/            # gitignored (task A1)
  test.env                  # generated: absolute paths + DUMMY keys only
  content/                  # curated subset COPIED from the real content/  (~5 MB)
  archive/                  # pre-created empty (Docker would create it root-owned)
  config/                   # COPIED from backend/config/, then rewritten in place
  artifacts/                # state.json, test-results/, html-report/
```

### Hard rule: never symlink into a project directory — always copy

Nothing under `.testenv/` is ever a symlink to `content/`, `backend/config/`, or any other
tracked path. Every file is a real copy (`shutil.copy2` / `copytree`). Two reasons this is
absolute, not stylistic:

- A destructive spec — archive, delete item, delete image — would follow the symlink and
  **destroy real tracked content**. The suite is *designed* to delete things.
- `awatch(settings.config_path)` in [main.py:80](backend/src/main.py#L80) hot-reloads the
  registry on any change under the config dir. Through a symlink, editing the real
  `backend/config/` while the stack runs would silently reconfigure the test backend.

The seed must assert this: after building the tree, walk `.testenv/` and fail if any entry is
a symlink. Cheap, and it catches a regression in the seed script itself.

`down` = `down -v --remove-orphans` (named volumes carry `users.db` and TTS output, so they're
destroyed with the stack). `reset` = down + `rm -rf` the dirs + seed + up.

---

## 3. Seed fixture — a curated subset copied from the real `content/`

`scripts/testenv_seed.py` **copies real categories verbatim** out of `content/` and rewrites
only the index CSVs. Nothing is synthesised, so the fixture carries the real naming, the real
CSV shapes, and the real messiness — and it cannot drift from what the app actually produces.

Copying the whole 57 MB tree was rejected for one concrete reason: specs could not assert
fixed counts, because every assertion would break the next time you edited `content/`. The
subset is chosen once, is deterministic, and is ~5 MB.

### The subset

Your pick — `drinks`, `fruits`, `at_school` — plus `at_home` as a fourth:

| Category | Items kept | ~Size | Why this one |
|---|---|---|---|
| `drinks` (`C_Drinks`) | `water`, `coffee` | 1.7 MB | Carries the quirks: `asd_drinks.png`, `asd_drinks_01.png`, `drinks_old.png`, the stray `names_export.py`, legacy `images.csv`/`images_names.csv`. `water/` has **two variants of different types (`water_01.png`, `water_02.jpg`) and no main image**, and **no voice sidecars**. |
| `fruits` (`C_Fruits`) | `apricot`, `avocado` | 1.6 MB | `apricot/` is a **completely empty directory (0 B)** yet listed in `items_ids.csv` — an item with no images and no voices. |
| `at_school` (`C_AtSchool`) | `pencil`, `water_bottle` | 2.8 MB | **Has category-level voice sidecars** (`at_school_en.csv`) — the deliberate contrast against `drinks`, which has none. `I_WaterBottle` → `water_bottle` exercises the multi-word `_to_pascal`/`_to_snake` round-trip. Destructive specs target this category. |
| `at_home` (`C_AtHome`) | — none — | 184 KB | **Added beyond your three**, because none of them is empty and the empty-category branch would otherwise go untested. It's the smallest category in the repo: thumbnail + 4 voices, no `items_*.csv` at all. Drop it if you'd rather, at the cost of that branch. |

`at_school`'s `calculator/` item (1.5 MB) is dropped to keep the subset near 6 MB; the two kept
items are enough for grid and detail assertions.

Real ids and folder names throughout, so the `_folder_name_from_category_id` round-trip
([models.py:405](backend/src/services/content/models.py#L405)) is exercised against real data.

### What the seed rewrites

Only the index CSVs, by **slicing the kept rows out of the originals** — never regenerating
their contents:

- Root: `categories_ids.csv`, `categories_{en,fr,es,bg}.csv`, `descriptions_{en,fr,es,bg}.csv`.
  All eight must be sliced by the **same row indices** (1, 2, 5, 6 of the original 9) or the
  row-alignment invariant breaks. Resulting order: `C_AtSchool`, `C_Drinks`, `C_AtHome`,
  `C_Fruits`.
- `descriptions_it.csv` — sliced and **kept**, preserving the orphan quirk (there is no
  `categories_it.csv`, and `it` is not in `SUPPORTED_LANGUAGES`).
- Per-category `items_ids.csv` + `items_{en,fr,es,bg}.csv` — sliced to the kept items, same
  index rule.

Everything else is copied byte-for-byte: images, MP3s, voice sidecars, stray files, legacy
CSVs. Constants for the filenames are at
[file_system.py:66-77](backend/src/services/content/providers/file_system.py#L66-L77).

Note [backend/CLAUDE.md](backend/CLAUDE.md) documents `content/<category>/images/<name>/`,
which is **stale** — reality is flat item dirs. Copying from disk sidesteps the stale doc
entirely; worth fixing the doc separately.

### Determinism

The seed takes no clock, no randomness, no network: given the same `content/`, two runs
produce byte-identical trees, so `reset` is trustworthy. Guard the inputs — the seed should
record a manifest (`.testenv/content/.mx-seed.json`: source paths + sizes + a digest of the
sliced CSVs) so a spec failing after someone edits the real `drinks/` reports *"fixture source
changed"* rather than an inscrutable assertion diff.

### Still generated (not copied)

Two small stdlib generators survive, for the **stub server and upload fixture only** — never
for the seed tree:
- **PNG**: `zlib` + `struct`, 8×8 solid RGB (~90 bytes), colour from `sha1(prompt)`. Used by
  the stub's `/v1/images/generations` response and as `tests/e2e/fixtures/upload.png`.
- **MP3**: a real MPEG-1 Layer III frame so `<audio>` decodes it. 32 kbps/44.1 kHz mono →
  `144 * 32000 // 44100 = 104` bytes/frame: `b"\xff\xfb\x10\xc4" + b"\x00"*100`, ×20 ≈ 0.5 s
  of silence. Used by the stub's `/tts` response.

Seeded words translate offline through `static_translate` against the **real** CSV rows
(`drinks` → `напитки`). Only genuinely new words reach the stub, which returns
`f"{word}-{code}"` — so a create-item spec asserting `cherry-bg` is unambiguously exercising
the stub path, not the static lookup.

---

## 4. Provider neutralisation

`scripts/testenv_config.py` copies **every** config file (the builder reads the whole
directory — a missing file is a missing provider, not a disabled one) and rewrites:

| Config | Test state | Change |
|---|---|---|
| `content/file_system.json` | on | none |
| `logging/{default,requests}.json` | on | none (`seq` handler already `"active": false`) |
| `text_translation/static.json` | on, prio 1 | `content_dir: ""` → falls back to `settings.content_path` |
| `text_translation/openai_en_{bg,es,fr}.json` | on, prio 2 | none — repointed via `OPENAI_BASE_URL` |
| `text_translation/qwen_en_{bg,es,fr}.json` | on, prio 3 | `base_url` → `http://stub:8080/v1/`, set on **every** `qwen_translate` config (matched by provider type, so new language pairs are covered); a config without one would fall back to the real self-hosted server |
| `text_to_speech/speaktor.json` | on, prio 2 | `endpoint` → `http://stub:8080/tts`; `response_mode` → `"raw"`; **drop the `Authorization` header** |
| `text_to_speech/{openai,elevenlabs,static}.json` | off | already off — cannot be stubbed |
| **`text_to_image/openai.json`** | **→ off** | **`true` → `false`** (SDK provider, prio 1, would shadow the stubbable one) |
| `text_to_image/openai_http.json` | **→ on**, prio 2 | `false` → `true`; `endpoint` → `http://stub:8080/v1/images/generations` |
| `text_to_image/genai_http.json` | **→ on**, prio 4 | `false` → `true`; add `base_url` → `http://stub:8080/v1beta/models` |
| `text_to_image/comfyui.json` | **→ on**, prio 5 | `false` → `true`; `base_url` → `http://stub:8080` |
| `text_to_image/genai.json` | off | SDK, no config base URL — stays off |
| `logging/default.json` | on | set the `seq` handler `active: true`, `server_url` → `http://stub:8080` — exercises the log sink instead of silently skipping it |
| `voice_optimizers/*` | off | already off. **`ffmpeg` is absent from the host too** (checked), so native mode is no better here; nothing regresses either way |

Keeping **both** `static_translate` (prio 1) and the OpenAI-stub configs (prio 2) means seeded
words resolve offline and instantly while new words fall through to the stub — and gives a
spec that asserts the priority chain works. The Qwen configs (prio 3) are only tried when both
fail, which the stubbed OpenAI never does, so no spec reaches Qwen today: its redirect is a
safety net that keeps the test environment off the real Qwen server.

### `tests/stub-provider/stub_server.py`

~120 lines, stdlib `ThreadingHTTPServer` on `:8080`. Three routes, all deterministic:

1. **`POST /v1/chat/completions`** → must satisfy the SDK's `ChatCompletion` model:
   `{"id","object":"chat.completion","created","model","choices":[{"index","finish_reason","message":{"role","content"}}],"usage":{...}}`.
   `_render_prompt` ([openai_translate.py:78-91](backend/src/services/text_translation/providers/openai_translate.py#L78-L91))
   substitutes `{{ output_language }}` with the *name* from `LANG_MAP`, so map
   `English/French/Spanish/Bulgarian` back to codes and return `f"{word}-{code}"`.
2. **`POST /v1/images/generations`** → `{"created":0,"data":[{"b64_json":"<base64 PNG>"}]}`,
   colour from `sha1(prompt)`.
3. **`POST /tts`** → raw `tiny_mp3()` bytes, `Content-Type: audio/mpeg`.

Plus `GET /__health`, `GET /__calls` (a recorded call log) and `POST /__reset`. `/__calls`
enables assertions no unit test can make: *"creating one item issued exactly 3 translate calls
and 4 TTS calls."*

### Covering *every* external service

Extending the stub to the rest turns out to be mostly free — three more providers already read
their base URL from config:

| Service | Provider | Redirect seam | Stub route |
|---|---|---|---|
| Translation | `openai_translate` | `OPENAI_BASE_URL` env | `POST /v1/chat/completions` |
| Translation (Qwen) | `qwen_translate` | **`base_url` in config** — OpenAI-compatible SDK client, same request shape | `POST /v1/chat/completions` (shared with `openai_translate`) |
| TTS | `speaktor` | `endpoint` in config | `POST /tts` |
| Image (OpenAI) | `openai_http` | `endpoint` in config | `POST /v1/images/generations` |
| Image (Gemini) | `genai_http` | **`base_url` in config** — [genai_http_image.py:43](backend/src/services/text_to_image/providers/genai_http_image.py#L43) reads `generate_config.get("base_url") or BASE_URL` | `POST /v1beta/models/{model}:predict` → `{"predictions":[{"bytesBase64Encoded":"…"}]}` |
| Image (ComfyUI) | `comfyui` | **`base_url` in config** (`http://127.0.0.1:8188`) | `POST /prompt` → `{"prompt_id":"stub-1"}`; `GET /history/{id}` → completed record; `GET /view` → PNG bytes |
| Logging sink | `struct_log` → Seq | `server_url` in config | `POST /api/events/raw` → 201 |
| STT / evaluation | `speaktor` transcription | same config-driven endpoint | `POST /stt` |

Enabling `genai_http` and `comfyui` is worth it even though `openai_http` already covers image
generation: they exercise two *different* response-parsing paths, and ComfyUI's
submit-then-poll flow is the only asynchronous provider protocol in the codebase.

### Four providers that cannot be redirected without a source change

| Provider | Blocker |
|---|---|
| `elevenlabs_tts` | `API_BASE = "https://api.elevenlabs.io/v1"` — module constant ([elevenlabs_tts.py:17](backend/src/services/text_to_speech/providers/elevenlabs_tts.py#L17)) |
| `static_tts` | imports that same constant ([static_tts.py:13](backend/src/services/text_to_speech/providers/static_tts.py#L13)) |
| `openai_tts` | `ENDPOINT = "https://api.openai.com/v1/audio/speech"` — class constant, raw httpx, so `OPENAI_BASE_URL` does not apply ([openai_tts.py:21](backend/src/services/text_to_speech/providers/openai_tts.py#L21)) |
| `genai` (SDK) | `genai.Client(http_options=...)` never passes a base URL ([genai_image.py:54](backend/src/services/text_to_image/providers/genai_image.py#L54)) |

Each is a **redundant implementation of a capability the stub already covers** — TTS via
`speaktor`, Gemini imagery via `genai_http` — so no *capability* goes untested. They stay
disabled, exactly as they ship today.

Closing the gap completely would take four one-line edits, each following a pattern the
codebase already uses: `self.generate_config.get("endpoint") or ENDPOINT`. That is a source
change, which the constraint rules out — flagging it as a follow-up you can approve separately.
Until then, those four providers can only be exercised against the real vendors.

`.testenv/test.env` carries dummy credentials. `OPENAI_API_KEY` must be **non-empty** —
`openai_http_image.py:38` and `openai_translate.py:100` both raise on a falsy key before any
HTTP call. `SPEAKTOR_API_KEY` can be empty because the test config drops `{api_key}`.

---

## 5. Playwright

**Lives in a new top-level `tests/e2e/` with its own `package.json`**, not in `frontend/`:

1. `npm run build` is `vue-tsc --noEmit && vite build` and is a validation gate — specs under
   `frontend/` get type-checked by the app tsconfig, so a spec type error would break the
   *build* gate.
2. `docker/frontend.Dockerfile` runs `npm ci` then `COPY frontend /app`, and the root
   `.dockerignore` is **empty (0 bytes)** — Playwright devDeps would slow every image build
   for everyone.
3. E2E depends on the deployed stack's URL, not on the frontend package.

```
tests/e2e/
  package.json  tsconfig.json  playwright.config.ts
  global-setup.ts  auth.setup.ts
  fixtures/seed.ts  fixtures/selectors.ts  fixtures/upload.png
  specs/*.spec.ts
```

Config: `baseURL` from `E2E_BASE_URL`, chromium only, `trace: 'retain-on-failure'`,
artifacts under `.testenv/artifacts`, launch arg `--autoplay-policy=no-user-gesture-required`
(the app drives `<audio>` playback).

**`fullyParallel: false`, `workers: 1`, `retries: 0`** — and these are not negotiable defaults.
Every root CSV is row-index aligned (`_set_csv_row_aligned`,
[file_system.py:674](backend/src/services/content/providers/file_system.py#L674)); concurrent
writes misalign every category below and surface as unrelated failures elsewhere. A retry
would run against state the first attempt already mutated, masking corruption rather than
absorbing flake.

**No `webServer`** — the stack is externally managed by `testenv.py`, so you can leave it up
all day and iterate on specs in milliseconds.

**Readiness probe:** `GET ${BASE_URL}/content/categories_ids.csv` → 200 containing
`C_Fixtures`. One request proves nginx, the `/content/` proxy, completed lifespan, the volume
mount, and the seed. Do **not** probe `${BASE_URL}/openapi.json` — nginx only proxies `/api/`,
`/internal/`, `/content/`, so that path hits the SPA fallback and returns `index.html` with a
200: a silent false positive.

**Auth:** `AUTH_DISABLED`/`DEBUG_MODE` default True so the backend accepts anything, but
`router.beforeEach` ([router/index.ts:43](frontend/src/router/index.ts#L43)) still requires
`localStorage.token`. `auth.setup.ts` does one real UI login and saves `storageState`.

**Confirmation modes: keep `complex`** (matching production), with a `--simple-confirm`
escape hatch. `usesComplexArchiveConfirmation` and `usesComplexDeleteItemConfirmation` gate
the two most destructive operations and require typing an exact id — precisely what deserves
E2E coverage, and fully deterministic. Note these are **build args**, so flipping them costs a
frontend rebuild and can't be done per-test.

Keep `VITE_MOCK_DATA=false`: if it turned on, `main.ts:14` would install the MSW worker and
auto-set `localStorage.token`, and every spec would silently test the mock instead of the backend.

---

## 6. First specs

Routes from [frontend/src/router/index.ts](frontend/src/router/index.ts): `/login`, `/`,
`/categories`, `/categories/:categoryId`, `/categories/:categoryId/items/:itemId`.
All selectors centralised in `fixtures/selectors.ts` — with one `data-testid` in the codebase,
specs ride on CSS classes a refactor could rename without any type error, so a rename must be
a one-file fix.

| # | File | Asserts |
|---|---|---|
| 1 | `auth.setup.ts` | Real UI login as `admin`; writes `storageState`. |
| 2 | `login.spec.ts` | Wrong password → `.error-msg`, stays on `/login`; unauthenticated deep-link redirects. |
| 3 | `dashboard.spec.ts` | Exactly 4 cards — `at school`, `drinks`, `at home`, `fruits`, **in that row order**. Icons render (`naturalWidth > 0` — proves proxy + mount + real PNG in one assertion). Item counts 2 / 2 / 0 / 2. |
| 4 | `category-detail.spec.ts` | `C_Drinks`: 4 voice cards; play issues `GET /content/drinks/drinks_en.mp3` → 200. `C_AtHome`: empty state, still 4 voices. Bulgarian label renders as `напитки` (real data, so this also proves UTF-8 survives the whole stack). |
| 5 | `content-quirks.spec.ts` | **New — pins the real-world mess as deliberate coverage.** `fruits/apricot` (empty dir) opens without error and shows empty image *and* voice states. `drinks/water` lists 2 variants across `.png` and `.jpg` with **no main image** selected. `drinks` does not surface `names_export.py`, `images.csv` or `images_names.csv` as content. `asd_drinks*.png`/`drinks_old.png` appear as ordinary category images, not as the thumbnail. No `it` language appears anywhere despite `descriptions_it.csv` existing. |
| 6 | `item-create.spec.ts` | **Flagship.** In `C_Fruits`, create `cherry` → id preview `I_Cherry` → 4 voices, translations `cherry-fr/-es/-bg` (stub path, unambiguous vs. static lookup); then `/__calls` shows exactly 3 translate + 4 TTS calls. |
| 7 | `item-images.spec.ts` | On `drinks/water` — the no-main-image item. Set variant 2 as main and assert the thumbnail appears (covers commit 86fcf35's main-image copy). `setInputFiles` on the hidden input: variants 2 → 3; delete → back to 2. Covers the main/variant split at [file_system.py:1678](backend/src/services/content/providers/file_system.py#L1678). |
| 8 | `item-delete.spec.ts` | `C_AtSchool` / `I_Pencil`: complex confirm, cancel path then confirm path. Afterwards re-read the category and assert remaining rows still resolve — the row-alignment guard. |
| 9 | `category-archive-restore.spec.ts` | Archive `C_AtSchool` (type the exact id) → 3 cards + 1 archived; restore → 4. Only spec exercising the `.testenv/archive` mount. |
| 10 | `category-create.spec.ts` | Full hard-gate chain (translate + TTS + image). Best integration assertion in the suite; impossible without the stub. |

Specs 3–5 are read-only. 6–7 write into `drinks`/`fruits`; 8–9 are confined to `at_school`;
10 adds a new category. Run in file order at `workers: 1`.

---

## 7. Entry point

`scripts/testenv.py` — argparse + the ANSI constants and `_resolve_paths()` style of
[scripts/validate_tests.py:17-23](scripts/validate_tests.py#L17-L23). Python not bash: the
seed generator is Python anyway, and the env-sanitising subprocess call is safety-critical.

A global `--mode docker|native` flag on every subcommand, defaulting to autodetect: Docker if
the daemon answers, native otherwise. `doctor` always prints which mode it chose and why.

```
doctor    mode? compose >= 2.24? ports free? dev stack up? REAL KEYS IN CHILD ENV?
seed      (re)build .testenv/{content,config,archive} + test.env
up        [--build] [--simple-confirm]  seed if absent, start stack, wait, print URLs
down      [--purge]                     docker: down -v --remove-orphans / native: app.py stop
reset     down + rm dirs + seed + up    (the workhorse)
e2e       [-- <pw args>]                ensure up, npm ci + playwright install, run
run       [--strategy …]                Workstream 3 — see §18
logs | status | shell <service>
```

In native mode `up` delegates to `scripts/app.py start` and `down` to `app.py stop`; everything
above that line — seeding, config neutralisation, the stub, the E2E suite — is shared verbatim.

### Secrets: a dedicated local env file, and an allowlist

The repo `.env` holds live `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `SPEAKTOR_API_KEY` and
`GITHUB_PAT`. Two paths would leak them into the test environment:

1. **Compose precedence** — host environment variables beat `--env-file`, so already-exported
   real keys silently win over the test file.
2. **[scripts/app.sh](scripts/app.sh) exports them.** Its `load_env_file` reads the repo `.env`
   and `export`s every key, so merely having run `app.sh` in that shell is enough.

So the test environment reads **its own file, `.testenv/test.env`**, and never the repo `.env`.
Child processes are built from an **allowlist, not a blocklist** — a blocklist silently leaks
whatever gets added to `.env` next month:

```python
ALLOWED = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TERM", "SSL_CERT_FILE"}

def child_env(test_env: dict[str, str]) -> dict[str, str]:
    base = {k: v for k, v in os.environ.items() if k in ALLOWED}
    return base | test_env      # only .testenv/test.env supplies application config
```

Both modes use it: Compose is invoked with this env, and `app.py` starts every child process
with it. Dummy values only — `OPENAI_API_KEY=test-openai-key-not-real`.

The allowlist alone is not enough for the backend: `backend/src/core/config.py` calls
`load_dotenv()`, which searches upward from `backend/src/core/` for a `.env` and fills in every
variable not already set. So `test.env` lists **every** provider key the backend reads — a
dummy (`QWEN_API_KEY=test-qwen-key-not-real`) or an empty string (`ELEVENLABS_API_KEY=`) —
because a key that is merely absent would be filled from a real `backend/.env` or repo-root
`.env` in native mode. A new provider key therefore needs an entry in
`testenv_seed._write_test_env` (dummy values live in `scripts/testenvlib/paths.py`).

`doctor` refuses to start on a hit: scan the composed child env (and, in Docker mode,
`docker inspect` of the running container) for `sk-`, `sk_`, `glpat-`, `ghp_`, **or any value
that also appears in the repo `.env`**. That last check is the one that catches key formats
nobody anticipated.
Mirror the `--compose-file`/`--backend-port`/`--frontend-port` flags and the
`MAX_RETRIES`/`RETRY_INTERVAL` retry shape already in `scripts/deploy.sh`.

---

# Workstream 2 — GitLab issues as the task store

## Key findings

**1. The GitLab instance is self-hosted at `http://82.103.77.45/marxman/mx-admin`, and it has
no TLS.** Verified: HTTP answers `302` in ~74 ms, `/api/v4/version` answers
`401 Unauthorized` (API live, auth enforced), the project answers `404` unauthenticated
(private, as expected) — and **port 443 refuses connections**.

`82.103.77.45` is a public address, not RFC1918. So a PAT would cross the open internet in
cleartext on every API call, together with every issue title and body. Interception yields a
working `api`-scoped token for the project.

Mitigations, in order of preference:

1. **Terminate TLS on the server** — the real fix; infra work outside this plan.
2. **SSH tunnel** — `ssh -L 8080:localhost:80 <user>@82.103.77.45`, then
   `GITLAB_HOST=http://127.0.0.1:8080`. [scripts/deploy.sh](scripts/deploy.sh) documents a
   GitLab shell runner on that Ubuntu 24.04 host, so shell access likely already exists.
   **This is what the plan assumes**, and `doctor` warns when `GITLAB_HOST` is a plain-HTTP
   non-loopback address.
3. **Short-lived project access token** — scoped to this one project with a near expiry, so
   interception has a small blast radius. Reduces damage; does not prevent capture.

Note the tunnel is a per-session thing: `doctor` must give a clear "tunnel not up" message
rather than a connection-refused traceback, or every command fails confusingly.

Local tooling is otherwise absent: `origin` is GitHub only
(`git@github.com:spyderbg/mx-admin-cloud.git`), and there is no `glab`, no `gh`, no GitLab
token in the environment, and no GitLab MCP server in this session.

**Self-hosted also means:** the GitLab *version* and CE-vs-EE edition are unknown, and they
decide whether scoped labels are enforced. `/api/v4/version` returns an `enterprise` flag —
`doctor` should print version + edition and say plainly which label mode is in effect. The
plan works either way (§10). Rate limits are configurable per instance and commonly off on
self-managed, so the bulk-`sync` throttling concern is much weaker here than on GitLab.com.

**2. The mirror is one-way and push-only** — `main` + tags, via an SSH deploy key that grants
git-ref push, not API access. So issues live on GitLab while code flows GitHub → GitLab.

*Free win, and a hazard:* when the mirror pushes to GitLab's default branch, GitLab parses
those commit messages for closing patterns, so a `Closes #12` that reaches `main` will close
the issue on its own. That's useful, but it races with `issues.py done`. The transition code
must treat "already closed" as success, not as an error.

**3. GitLab issues have only `opened`/`closed`.** "In progress" must be a label. Scoped labels
(`status::in-progress`) are mutually exclusive — but that enforcement is **Premium**. On Free
the `::` is just part of the name, so exclusivity must be enforced client-side. Name them with
the `status::` prefix anyway: on a later Premium upgrade they become enforced-exclusive with
zero renaming.

**4. Zero new dependencies needed for the report formats.** `validate_build.py` parses pylint's
*text* output and `validate_tests.py` runs bare `pytest`. Machine-readable results come from
flags that already exist: `pylint --output-format=json`, `pytest --junit-xml=<path>` (verified
present), and Playwright's built-in `json` reporter.

**5. Dependency placement matters.** `docker/backend.Dockerfile` does
`COPY backend/requirements.txt` then `pip install` — adding `python-gitlab` there would ship
it in the production image. It goes in a **new `scripts/requirements.txt`** instead, alongside
the existing `backend/requirements.txt` and `cli/requirements.txt` that
[scripts/setup_initial.py:19-20](scripts/setup_initial.py#L19-L20) already installs; add a
third install step there.

---

## 9. Configuration

Three new keys in the repo-root `.env` (already gitignored, already holds `GITHUB_PAT`):

```
# Via SSH tunnel (recommended — see finding 1):
#   ssh -L 8080:localhost:80 <user>@82.103.77.45
GITLAB_HOST=http://127.0.0.1:8080
# Direct, cleartext over the public internet:
# GITLAB_HOST=http://82.103.77.45

GITLAB_PROJECT=marxman/mx-admin   # namespace/path, or the numeric project id
GITLAB_TOKEN=glpat-...            # scope: api (read_api is not enough — we write)
```

`python-gitlab` takes the scheme in the URL, so `http://` works without extra flags. Do **not**
set `ssl_verify=False` anywhere — there's no TLS to verify, and leaving that flag behind would
silently weaken things later if TLS is ever added.

Safe to add: `backend/src/core/config.py` sets `extra="ignore"` on its `SettingsConfigDict`,
so unknown `.env` keys can't break `Settings()`.

Prefer a **project access token** over a personal one — scoped to this one project, separately
revocable, and it shows as a distinct author on the issues it files. Given the cleartext
transport, set a short expiry and plan to rotate.

---

## 10. Label schema and the state machine

`issues.py init` creates these idempotently:

| Label | Purpose |
|---|---|
| `status::open` / `status::in-progress` / `status::done` | the workflow you asked for |
| `status::needs-human` | the doc's `MAX_FIX_ATTEMPTS = 3` escape (§4). **Addition beyond the three states** — without it a stuck task has nowhere to go but silently back to `open`. |
| `source::detector` / `source::e2e` / `source::pytest` / `source::pylint` / `source::manual` | provenance |
| `severity::high` / `::medium` / `::low` | from the detector's schema |
| `mx-auto` | ownership marker — see below |

### Atomic transition (the Free-tier crux)

Never `add_labels`/`remove_labels` — they're separate operations and can leave two `status::`
labels set. Instead compute the full set client-side and send it as one replacement:

```python
labels = [l for l in issue.labels if not l.startswith("status::")] + [target]
issue.labels = labels
if target == "status::done":
    issue.state_event = "close"
issue.save()
issue = project.issues.get(issue.iid)          # read back
assert [l for l in issue.labels if l.startswith("status::")] == [target]
```

The **read-back assertion is the point** — it is the verification Free tier doesn't give you,
and it costs one cheap GET. Note `issues.get()` takes the **iid** (per-project number shown in
the UI), not the global `id`; mixing them up is the classic python-gitlab footgun and will
silently act on the wrong issue.

### Ownership rule — so manual issues are never clobbered

Since manual entry is in scope, the boundary must be explicit:

- The tool **creates** issues only with the `mx-auto` label.
- It **rewrites descriptions, auto-reopens, or auto-closes** only issues carrying `mx-auto`.
- It **transitions state** on *any* issue when you name it explicitly (`issues.py start 42`),
  manual ones included.
- A `sync` run never touches the body of an issue it doesn't own.

That lets hand-written and generated issues share one board without the automation editing
human prose.

---

## 11. Deduplication — the make-or-break part

A detector run nightly, or a suite that fails the same way twice, must not open duplicates.

Each finding gets a **fingerprint**: `sha1` of a source-specific tuple, truncated to 12 hex
chars. The tuple is chosen for stability across edits:

| Source | Fingerprint input | Why |
|---|---|---|
| `e2e` | spec path + test title | stable across reruns and refactors |
| `pytest` | node id (`src/.../test_x.py::test_y`) | canonical and stable |
| `pylint` | file path + `symbol` | **not** the line number — lines shift on every edit, so including them would duplicate the issue on the next commit |
| `detector` | normalised title + sorted affected files | LLM ids aren't stable; this is the weakest key and the reason the doc's critic/confidence gate matters |

Stored in the issue description in a fenced block, so both humans and the future worker can
read it:

````
```mx-meta
{"fp": "a1b2c3d4e5f6", "source": "e2e", "key": "specs/item-create.spec.ts::creates an item",
 "first_seen": "2026-09-21", "occurrences": 3}
```
````

Lookup: `project.issues.list(search=f"mx-fp:{fp}", in_="description", state="all")`, with a
local cache at `.mxissues/index.json` as a fast path. **The API is the source of truth** — the
cache may be absent or stale on another machine, so a cache miss always falls through to a
search, never to "create".

On a match, don't create. Instead comment the recurrence and bump `occurrences`. And if the
matched issue was `status::done`/closed, **reopen it as a regression** with `status::open` and
a `regression` comment — which is exactly the *"search for regressions"* step the document's
reviewer (§4) is supposed to perform.

---

## 12. Feeding results in

Add machine-readable output as **optional flags** so the default `/verify` human tables are
unchanged:

- `scripts/validate_tests.py --junit-xml <path>` → appends `--junit-xml` to the pytest
  invocation at [validate_tests.py:146](scripts/validate_tests.py#L146); parse with stdlib
  `xml.etree`.
- `scripts/validate_build.py --json <path>` → runs pylint a second time with
  `--output-format=json`, or switches format and renders the table from the parsed JSON.
- `playwright.config.ts` gains `['json', { outputFile: '../../.testenv/artifacts/results.json' }]`
  in its reporter list.

Then:

```
python scripts/issues.py sync --pytest .testenv/artifacts/pytest.xml
python scripts/issues.py sync --e2e .testenv/artifacts/results.json --apply
```

**`sync` defaults to a dry run.** Creating issues in a shared tracker is outward-facing and
awkward to undo, so it prints the create/update/reopen plan and does nothing without
`--apply`.

---

## 13. Layout and CLI

```
scripts/requirements.txt         # python-gitlab (NOT backend/requirements.txt)
scripts/issues.py                # argparse entry point, styled like validate_tests.py
scripts/mxissues/
  __init__.py
  config.py                      # .env loading, host/project/token resolution
  client.py                      # python-gitlab wrapper + the mx-auto ownership guard
  state.py                       # label state machine, atomic transition, read-back assert
  fingerprint.py
  models.py                      # Finding dataclass — the one shape every source maps to
  sources/{e2e,pytest,pylint,detector}.py
```

```
issues.py doctor            token valid? project reachable? labels present? print resolved host/project
issues.py init              create missing labels (idempotent)
issues.py list [--status S] [--source S] [--all]
issues.py show <iid>
issues.py open "<title>" [--source manual] [--severity S] [--file F]
issues.py start <iid>       → status::in-progress
issues.py done <iid>        → status::done + close
issues.py reopen <iid>  |  issues.py needs-human <iid>
issues.py sync --e2e <json> | --pytest <xml> | --pylint <json>  [--apply]
```

`doctor` runs first, always: it's the only thing standing between a typo'd project path and a
pile of issues filed into the wrong repository.

---

# Workstream 3 — selectable execution strategies

## Key findings

**`codex` and `claude` are both installed** (`/Users/spyder/.local/bin/`). **`gh` is not** —
so opening a PR goes through the GitHub REST API with the `GITHUB_PAT` already in `.env`, via
`httpx` (already in the venv). No new dependency, and no MCP: scripts can't call MCP servers.

**`tomllib` is stdlib** on the repo venv (3.12.14), so the config file needs nothing new.

**Worktrees are already in use here** — `.kilo/worktrees/shrouded-tourmaline` is registered.
Ours go in a separate `.mxworktrees/` (gitignored, `git worktree prune` on cleanup) so the two
tools can't collide.

---

## 14. Configuration: `testenv.toml` (repo root, tracked)

**Not** in `.testenv/` — that directory is throwaway and `reset --purge` deletes it, which
would silently wipe your settings. Tracked at the repo root, with an optional gitignored
`testenv.local.toml` for per-developer overrides; `--strategy` overrides both.

```toml
strategy = "report"              # report | heal | batch

[limits]
max_fix_attempts   = 3           # the origin doc's MAX_FIX_ATTEMPTS
concurrency        = 3           # batch only
severity_threshold = "medium"    # ignore severity::low

[fixer]
adapter         = "codex"        # codex | claude
timeout_seconds = 1800

[git]
base_branch   = "dev"
branch_prefix = "ai/"
push          = true
open_pr       = true
```

Resolution order: `testenv.toml` → `testenv.local.toml` → CLI flags. `testenv.py run
--print-config` shows the merged result, because a strategy that modifies code should never
run on settings you can't see.

---

## 15. The `Fixer` interface

Neither tool gets hardcoded. `scripts/mxfix/fixer.py`:

```python
@dataclass(frozen=True)
class FixTask:
    issue_iid: int
    title: str
    body: str                  # evidence, affected files, acceptance criteria
    worktree: Path
    attempt: int
    feedback: str | None       # reviewer output from the previous attempt

@dataclass(frozen=True)
class FixResult:
    status: Literal["fixed", "failed", "no_change"]
    files_changed: list[str]
    summary: str
    raw_log: Path

class Fixer(Protocol):
    name: str
    def preflight(self) -> None: ...     # binary present? authenticated? raises with a fix hint
    def run(self, task: FixTask) -> FixResult: ...
```

- **`CodexFixer`** — `codex exec --json --output-schema <schema.json>`, cwd pinned to the
  worktree. JSONL events stream to `raw_log`; the structured final result maps to `FixResult`.
- **`ClaudeFixer`** — `claude -p` with the same task text, scoped to the worktree.

`preflight()` runs before any issue is touched, so a missing or unauthenticated binary fails
in the first second rather than after filing and transitioning a dozen issues.

The prompt follows the origin document's §3 constraints verbatim — *do not modify unrelated
code, do not change public API unless necessary, do not disable tests, do not weaken lint
rules* — plus the issue's acceptance criteria. Those constraints are what keep "make the test
pass" from becoming "delete the test."

---

## 16. The three strategies

All three share one pipeline — **check → sync → select** — and diverge only after selection.
Checks are `validate_build.py`, `validate_tests.py`, `validate_frontend_build.py` and
`testenv.py e2e`, consumed through the machine-readable flags from §12.

### `report` — file and stop

Run checks → `sync --apply` → every new finding lands as `status::open`. Nothing is modified.
This is the default, and the only mode safe to run unattended.

### `heal` — one at a time, reset between each

```
select highest-severity status::open issue
  → status::in-progress
  → worktree .mxworktrees/<iid> on branch ai/<iid> off dev
  → fixer.run(task)
  → testenv.py reset            # byte-identical fixture, per your requirement
  → run all checks
      green → commit "Closes #<iid>", push, open PR, comment the PR on the issue
      red   → attempt += 1; feed failures back as FixTask.feedback
              attempt > max_fix_attempts → status::needs-human, worktree kept for inspection
  → next issue
```

The `reset` between attempts is load-bearing: earlier specs delete items and archive
categories, so without it a "fix" would be verified against a mutated tree.

### `batch` — parallel fix, single verify

```
select up to `concurrency` issues above severity_threshold
  → all → status::in-progress, one worktree + branch each
  → fixers run in parallel
  → merge every candidate branch onto integration branch ai/batch-<timestamp>
  → testenv.py reset, then ONE full check run
      green → all issues done, push each branch, open a PR per issue
      red   → attribution fallback, below
```

**The attribution fallback.** A red batch verify can't say which fix broke it — that's the
cost of verifying once. Rather than send the whole batch to `needs-human`, bisect: verify the
first half of the candidate set merged, then the second, recursing on whichever is red. That's
`log2(N)` extra verify runs — at `concurrency = 3`, at most two. Candidates landing in a green
subset are marked `done`; the culprit goes back to `in-progress` with the failure as feedback,
or to `needs-human` once attempts are exhausted. This keeps per-issue attribution without
paying for per-issue verification on the happy path.

Conflicting merges are detected before verification: a candidate that won't merge cleanly onto
the integration branch is dropped back to `status::open` with a `merge-conflict` comment and
retried alone in a later run.

---

## 17. Landing the fix

- Commit message ends with `Closes #<iid>` and the GitLab issue URL
  (`http://82.103.77.45/marxman/mx-admin/-/issues/<iid>`).
- Branch pushed to the **GitHub** origin; PR opened via the REST API with `GITHUB_PAT`.
  PR body cross-links the GitLab issue; a GitLab comment links back to the PR.
- **The issue is not closed when the PR opens.** It stays `status::in-progress` until the PR
  merges — closing on "PR opened" would mark unreviewed work as done.
- On merge to `main`, the mirror pushes those commits to GitLab, GitLab parses `Closes #<iid>`
  and closes the issue itself. `issues.py reconcile` then sweeps issues whose GitLab state is
  `closed` but whose label isn't `status::done` and corrects the label — which also resolves
  the mirror/`issues.py done` race noted in Workstream 2's finding 2.

Note the branch never reaches GitLab (the mirror carries only `main` and tags), so the GitLab
issue links to a GitHub PR across the one-way boundary. That's inherent to the current
topology, not something this plan can fix.

---

## 18. Runner CLI

```
testenv.py run [--strategy report|heal|batch] [--max-issues N] [--dry-run] [--print-config]
```

`--dry-run` prints the selected issues and the plan of action — worktrees, branches,
fixer — without invoking a fixer or touching GitLab. Given `heal` and `batch` write code, push
branches and open PRs, the first run of either should always be a dry run.

`run` refuses to start on a dirty working tree, and refuses `heal`/`batch` unless
`fixer.preflight()` passes and the GitLab token is valid.

---

# Task order

Host and project are known (`82.103.77.45`, `marxman/mx-admin`). **The one remaining input is a
token with `api` scope** — needed from B4 onward. Everything up to B3, and all of Workstream 1,
can start now.

### Workstream 1 — sandbox

| # | Task | Depends on |
|---|---|---|
| A1 | Add `.testenv/`, `.mxworktrees/`, `testenv.local.toml` to `.gitignore` | — |
| A2 | `scripts/testenv_seed.py` — copy the 4-category subset from `content/`, slice the index CSVs by row index, write `.mx-seed.json`, **assert no symlinks**, emit `.testenv/test.env`, `--check-ts` | — |
| A3 | `scripts/testenv_config.py` — copy `backend/config/` then apply the §4 rewrite table in place | — |
| A4 | `tests/stub-provider/stub_server.py` — all 7 service routes + `__health`/`__calls`/`__reset`, with the PNG/MP3 generators | — |
| A5 | `scripts/app.py` — **native runner** (stub + uvicorn + built frontend with the nginx-equivalent proxy), allowlist `child_env` | A2,A3,A4 |
| A6 | `docker/docker-compose.test.yml` | A2–A4 |
| A7 | `scripts/testenv.py` — mode autodetect, `doctor` secret scan, seed/up/down/reset/logs/status | A5,A6 |
| A8 | **Checkpoint A** (below) | A7 |
| A9 | **Checkpoint B** (below) | A8 |
| A10 | Scaffold `tests/e2e/` | A8 |
| A11 | `testenv.py e2e` subcommand | A7,A10 |
| A12 | Specs 2–4 (read-only; fastest feedback on selector strategy) | A11 |
| A13 | Spec 5 (`content-quirks`) — pins real-world mess before anything mutates it | A12 |
| A14 | Specs 6–7 (first write flows, first real stub exercise) | A13 |
| A15 | Specs 8–9 (complex confirmations + archive mount) | A14 |
| A16 | Spec 10 (full hard-gate chain) | A15 |
| A17 | Document both run modes in `docker/README.md`: the invocation, Compose ≥ 2.24, never-edit-the-base, never-symlink | A16 |

### Workstream 2 — GitLab issues

| # | Task | Depends on |
|---|---|---|
| B1 | `scripts/requirements.txt` with `python-gitlab`; add the install step to `scripts/setup_initial.py` | — |
| B2 | `scripts/mxissues/{config,models,fingerprint}.py` — config loading, the `Finding` dataclass, fingerprinting | B1 |
| B3 | `scripts/mxissues/{client,state}.py` — python-gitlab wrapper, ownership guard, atomic transition + read-back | B2 |
| B4 | `scripts/issues.py` — `doctor` (version/edition, tunnel check, plain-HTTP warning), `init`, `list`, `show`, `open` | B3, **token** |
| B5 | **Checkpoint C** (below) | B4 |
| B6 | `start` / `done` / `reopen` / `needs-human` transitions | B5 |
| B7 | `sources/pytest.py` + `validate_tests.py --junit-xml`; `sync --pytest` with `--apply` gating | B6 |
| B8 | `sources/pylint.py` + `validate_build.py --json`; `sync --pylint` | B7 |
| B9 | `sources/e2e.py` + the Playwright `json` reporter; `sync --e2e` | B7, **A16** |
| B10 | `sources/detector.py` — accepts the doc's §1 JSON schema; no LLM call, just the mapping | B7 |
| B11 | `issues.py reconcile` — correct labels on issues the mirror auto-closed | B6 |
| B12 | Document the label schema, the state machine, and the ownership rule in `docs/test_environment/` | B9 |

### Workstream 3 — execution strategies

| # | Task | Depends on |
|---|---|---|
| C1 | `testenv.toml` + loader (`tomllib`, three-level merge, `--print-config`) | — |
| C2 | `scripts/mxfix/fixer.py` — `FixTask`/`FixResult`/`Fixer` protocol | C1 |
| C3 | `CodexFixer` (`codex exec --json --output-schema`) + `preflight()` | C2 |
| C4 | `ClaudeFixer` (`claude -p`) — proves the interface isn't Codex-shaped | C3 |
| C5 | `scripts/mxfix/worktree.py` — create/prune under `.mxworktrees/`, dirty-tree refusal | C2 |
| C6 | `scripts/mxfix/git_target.py` — commit with `Closes #<iid>`, push to GitHub, open PR via REST + `GITHUB_PAT`, cross-link both ways | C5, B6 |
| C7 | `strategy/report.py` — check → sync → stop | B7, B8, B9 |
| C8 | `strategy/heal.py` — sequential loop with `reset` between attempts and feedback re-entry | C3, C5, C6, C7 |
| C9 | `strategy/batch.py` — parallel fix, merge, single verify | C8 |
| C10 | The bisect attribution fallback for `batch` | C9 |
| C11 | `testenv.py run` with `--strategy`, `--dry-run`, `--print-config` | C7–C10 |
| C12 | **Checkpoint D** (below) | C11 |

B9 couples Workstreams 1 and 2; Workstream 3 depends on both being green.

---

## Verification

**Checkpoint A (after A7) — the stack stands up.** Docker is absent here, so this runs in
**native mode**; re-run the same checks under `--mode docker` wherever Docker exists.
```
python scripts/testenv.py doctor          # reports mode, and fails on any real-key match
python scripts/testenv.py up --build
curl -s localhost:5173/content/categories_ids.csv  # → C_AtSchool/C_Drinks/C_AtHome/C_Fruits
find .testenv -type l                              # must print nothing — no symlinks
du -sh .testenv/content                            # ~6 MB, not 57
```
That `curl` is the load-bearing one in native mode: it proves `app.py`'s proxy forwards
`/content/` to the backend rather than letting Vite's hardcoded `../content` serve the **real**
tree. If it returns the full 9-category list, the proxy is wrong and every subsequent
assertion is meaningless.

In Docker mode, add the merge-semantics check — it's where the compose traps surface:
```
docker compose -f docker/docker-compose.yml -f docker/docker-compose.test.yml \
  --project-directory docker --env-file .testenv/test.env -p mxadmin-test config
docker inspect mxtest-backend --format '{{json .Config.Env}}' | grep -i key   # no real keys
```
Confirm exactly one published port per service, the three bind mounts repointed at `.testenv/`,
and `mxtest-*` container names.

Then confirm the copy is genuinely isolated: delete a file inside `.testenv/content/at_school/`
and check `git status` in the repo is still clean.

**Checkpoint B (after A8) — the stub contract holds, before any Playwright exists.**
```
curl -X POST localhost:9100/api/v1/content/categories/C_Fruits/items -d '{"word":"cherry"}'
curl -s localhost:<stub>/__calls | python -m json.tool
```
Expect 3 chat-completion calls and 4 TTS calls, and `.testenv/content/fruits/cherry/`
containing 4 MP3s + sidecars with `cherry-fr`/`-es`/`-bg` appended to `items_*.csv` — appended
in row-aligned position after `apricot` and `avocado`.

Almost every way this plan can be wrong — compose merge semantics, path resolution, stub
response shapes, key leakage — surfaces at A or B.

**Full suite:** `python scripts/testenv.py reset && python scripts/testenv.py e2e`.
Single spec while iterating: `python scripts/testenv.py e2e -- --headed specs/item-create.spec.ts`.
HTML report at `.testenv/artifacts/html-report/index.html`.

**Checkpoint C (after B4) — GitLab wiring is real, before anything writes in bulk.**
```
ssh -L 8080:localhost:80 <user>@82.103.77.45 &      # if using the tunnel
python scripts/issues.py doctor      # host, project, token scopes, GitLab version + edition
python scripts/issues.py init        # creates the label set
python scripts/issues.py open "Checkpoint C smoke test" --source manual
python scripts/issues.py start <iid> && python scripts/issues.py done <iid>
```
Confirm in the GitLab UI that the issue moved `status::open → in-progress → done` and closed,
that exactly **one** `status::` label is set at each step, and that the issue landed in the
project you expected. Then delete it. Verifying the project path here is the whole point —
`doctor` is cheap, filing a hundred issues into a stranger's repo is not.

**Dedupe check (after B7):** run `sync --pytest --apply` against a failing suite twice. The
second run must report `0 created, N updated` and bump `occurrences` — not open duplicates.
Then fix the test, mark the issue `done`, break it again, and confirm the third run **reopens**
the same issue as a regression rather than creating a new one.

**Checkpoint D (after C11) — strategies, in ascending order of blast radius.**
```
python scripts/testenv.py run --print-config             # merged config is what you expect
python scripts/testenv.py run --strategy report          # files issues, changes no code
python scripts/testenv.py run --strategy heal --dry-run  # prints plan, invokes no fixer
```
Then a real `heal` run against **one deliberately broken thing** — revert a known-good line,
confirm the loop files the issue, transitions it, fixes it, resets, re-verifies green, opens
the PR, and leaves the issue `in-progress` with the PR linked. Only after that has worked
end-to-end at least once should `batch` run for real.

For `batch`, verify the fallback explicitly: break two things, have one fix deliberately fail,
and confirm the bisect attributes the failure to the right issue rather than dooming both.

**Regression guard:** `python scripts/validate_build.py && python scripts/validate_tests.py &&
python scripts/validate_frontend_build.py` must stay green, with unchanged human-readable
output when the new `--junit-xml` / `--json` flags are absent. No workstream touches
application source — except, by design, whatever a fixer changes inside its own worktree.

---

## Risks

1. **Real API keys leaking in.** Host env beats `--env-file` in Compose v2, and
   `scripts/app.sh` exports every repo-`.env` key into the shell. Mitigated by the dedicated
   `.testenv/test.env` plus the allowlist `child_env` (§1b) — an allowlist, because a
   blocklist leaks whatever is added to `.env` later. `doctor` refuses to start on a match.
2. **`container_name` is hardcoded** in the base compose file — `-p` does not isolate it.
   Without the `mxtest-*` rename the test stack collides with a running dev stack.
3. **`ports` concatenate rather than replace.** Needs `!override` (Compose ≥ 2.24), or the
   stack silently double-publishes on 9000/9001.
4. **Docker is not installed on this machine** (checked: no `docker`, `colima`, `podman`,
   `/Applications/Docker.app`), so **native mode via `scripts/app.py` is the default path
   here** (§1). Its own risk: native mode must never use `npm run dev`, because
   `vite.config.ts` serves `/content` from a hardcoded `../content` and would silently expose
   the real tree. Checkpoint A tests exactly that.
5. **Aligned-CSV corruption looks like an app bug.** Every root CSV is row-index aligned
   ([file_system.py:674](backend/src/services/content/providers/file_system.py#L674)). A
   half-completed create under parallel workers misaligns every category below it and the
   next spec fails somewhere unrelated. This is why `workers: 1` and `retries: 0` are not
   tunables.
6. **Sync content routes make nested loopback calls.** Every content handler is `def`, so it
   holds a threadpool slot while making 3–7 nested HTTP calls that each need another. Safe at
   `workers: 1`; a real deadlock risk if the suite is ever parallelised.
7. **Only one `data-testid` in the whole frontend.** Specs ride on CSS classes a refactor can
   rename with no type error. Mitigated by centralising every selector in
   `tests/e2e/fixtures/selectors.ts`; unfixable under no-source-changes.
8. **Bind-mount performance on macOS** (VirtioFS) is poor for many small files. The ~5 MB
   subset keeps this tolerable; the full 57 MB tree would make every `reset` painful.
9. **The fixture is a copy of live data, so it can drift.** Editing `content/drinks/` or
   `content/fruits/` changes the next `seed`, and count assertions fail for a reason that
   looks like an app bug. The `.mx-seed.json` manifest (§3) turns that into a clear
   *"fixture source changed"* message.
10. **Quirk specs pin current behaviour, not desired behaviour.** `content-quirks.spec.ts`
    asserts what the app does *today* with stray files and the empty `apricot/` dir. Each
    assertion should be reviewed as *"this is correct"*, not merely *"this is what happens"* —
    any that are actually wrong belong in GitLab as issues, not cemented as expectations.
11. **`awatch` hot-reload fires on any write into `.testenv/config`.** Don't rewrite configs
    while the stack is up; `reset` does `down` first for exactly this reason.
12. **Docker auto-creates missing bind-mount sources as root**, which breaks archive
    permissions on Linux. `.testenv/archive` must be created by `seed` before `up`.
13. **Backend source is COPYed at image build time** — every backend change needs
    `testenv.py up --build`. Easy to forget, and in `heal`/`batch` it would make a correct fix
    look like a failing one. The strategy runner must rebuild after any change under
    `backend/src/`, not just `reset`.
14. **`batch` cannot attribute a red verify on its own.** Accepted tradeoff; §16's bisect
    fallback recovers attribution in `log2(N)` extra runs. Without that fallback, one bad fix
    condemns the whole batch.
15. **A fixer can "fix" by weakening the check.** Deleting an assertion, disabling a test, or
    relaxing a pylint rule all turn the suite green. The §15 constraint prompt forbids it, but
    prompts are not enforcement — so the runner should additionally reject a diff that only
    touches test files when the issue's `source` is `pytest` or `e2e`, and flag any change to
    `.pylintrc`, `pytest.ini`, or `pyproject.toml` for human review.
16. **`heal` and `batch` push branches and open PRs unattended.** `--dry-run` first, always;
    `report` is the default for exactly this reason.
17. **Two tools now manage worktrees** — `.kilo/worktrees/` already exists. Keep ours under
    `.mxworktrees/` and never run a bare `git worktree prune` that could disturb the other.

---

## Out of scope / not testable under these constraints

- **Four provider implementations that can't be redirected** — `elevenlabs_tts`, `static_tts`,
  `openai_tts`, `genai` (SDK). See §4: each hardcodes its base URL, and each is a redundant
  implementation of a capability the stub already covers, so no *capability* is uncovered.
  Four one-line source changes would close it; flagged for separate approval.
- **Voice optimisation** — `ffmpeg` is absent from `python:3.11-slim` **and from this host**
  (checked), so neither mode helps. Already disabled; nothing regresses.
- **Auth/RBAC** — `AUTH_DISABLED`/`DEBUG_MODE` default True and lifespan overrides
  `require_uds`/`require_admin`. Both *can* be flipped via the override's `environment`
  (no source change) for a genuine auth suite — worth a follow-up, but keep the default test
  stack permissive so E2E failures mean content bugs, not token expiry.
- **`cli/` and the `backend-cli` UDS profile** — would need their own harness.
- **Real provider contract drift** — the stub asserts *our* parsing of the contract, not the
  vendor's. Inherent to the approach; a separate opt-in suite against the real APIs is the
  only answer, and it isn't a local-E2E concern.
- **Backend hot-reload** — `backend.Dockerfile` COPYs source at build time, so every backend
  change needs `testenv.py up --build`. A source bind mount in the override would fix this
  (still a config file, so permitted) but changes what's under test: working tree vs. built
  image. Follow-up.

### Workstreams 2 and 3

- **The LLM *detector* and *reviewer*** — Workstream 3 builds the executor
  (origin doc §3) but not §1's detector, §7's critic, or §4's independent review. Findings
  still come from deterministic checks; `sources/detector.py` (B10) defines the hand-off shape
  so an LLM detector can feed the same pipeline later. The document's layered-verification
  design (§5) is partly honoured already: deterministic checks decide green/red, and no model
  overrides them.
- **GitLab merge requests** — fixes land as GitHub PRs, because the mirror is push-only and a
  feature branch never reaches GitLab. Native GitLab MRs would need a second push target or a
  real GitLab remote. Worth deciding before anyone relies on branch↔issue links.
- **Scoped-label enforcement** — Premium/EE only, and the instance's edition is unknown until
  `doctor` reports it. The read-back assertion in §10 is the safe path either way; on an EE
  instance with a license it becomes redundant but harmless.
- **TLS on the GitLab host** — out of scope here, but it is the only real fix for finding 1.
  The SSH tunnel is a workaround that each developer must remember to start.
- **Rate limits** — configurable per instance and often disabled on self-managed, so likely a
  non-issue here. The `--apply` dry-run gate still prevents an accidental bulk flood.
- **Issue templates / boards** — a GitLab board whose lists are the `status::` labels would
  give a free visual workflow, but it's UI configuration, not code. Recommended, not planned.

## Relationship to the origin document

Mapping this plan onto
[docs/test_environment/initial_spec.md](docs/test_environment/initial_spec.md):

| Origin § | Proposed | Here |
|---|---|---|
| §1 Local model as Problem Detector | LLM scans repo, emits structured findings | **Not built.** Findings come from deterministic checks; `sources/detector.py` (B10) defines the hand-off so an LLM detector can plug in unchanged. |
| §2 SQLite task queue | `tasks.sqlite` with a 7-state machine | **Replaced** by GitLab issues + `status::` labels (Workstream 2). Survives restarts, and humans can see and edit it. |
| §3 Codex gets a concrete task | `codex exec` with acceptance criteria and constraints | **Built** as `CodexFixer` behind the `Fixer` protocol (§15), constraints carried verbatim. |
| §4 Independent verification | second LLM reviews the diff | **Not built.** Deterministic checks alone decide green/red for now. The `needs-human` state and `FixTask.feedback` are the seams it would use. |
| §5 Three-layer verification | deterministic > AI > regression search | **Partly honoured.** Deterministic checks are the only authority — the document's own strongest claim (line 386). Regression search exists as reopen-on-recurrence (§11). |
| §6 Worktree per task | `worktrees/BUG-00N/` | **Built** as `.mxworktrees/<iid>/` (§16). |
| §7 Task Critic before Codex | confidence gate, discard speculative tasks | **Not built.** `severity_threshold` in `testenv.toml` is the crude stand-in; a real critic matters most once an LLM detector exists. |
| §8 LM Studio as the local model | OpenAI-compatible localhost:1234 | **Not built** — no local model is used anywhere yet. |

The document's missing piece was the judge it delegates final authority to. That's Workstream 1.
~~One gap still to close before any of this runs unattended: `validate_tests.py` silently skips
the frontend test leg, which an autonomous loop reads as PASS.~~ **Closed 2026-11-07:** the
frontend has an `npm test` script, `validate_tests.py` also runs the CLI tests, and a suite that
cannot run, exits non-zero or runs zero tests now fails the script — a fabricated green is the
one failure mode this whole design cannot survive.
