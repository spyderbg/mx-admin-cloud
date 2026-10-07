# Item voices: multiple takes per item, regenerate and optimize without losing anything

Status: implemented. The outcome is summarised in `initial_result.md` in this folder.
This document is the specification the work was done from. It is self-contained: an engineer or an
AI coding agent with access to the repository, and nothing else, should be able to execute it.

## 1. Goal

An item has one voice per supported language. The content team wants to keep **several recordings
("takes") per item and language**, create more of them by **regenerating** (text-to-speech) and
**optimizing** (audio post-processing), **listen to them, and choose the most accurate one** as the
voice the app plays.

Selection is manual (by listening). Automatic accuracy scoring is explicitly out of scope (section 9).

## 2. Repository context

`mx-admin` is a content-management and voice-synthesis platform for a language-learning app.

- `backend/` FastAPI application (Python 3.10, venv at `backend/.venv`).
- `frontend/` Vue 3 + TypeScript SPA.
- `cli/` Typer CLI client (does not use item-voice regenerate; not affected).
- `scripts/` helper scripts, including the OpenAPI export and TypeScript client generators.
- `content/` the file-system content store the backend serves and edits.

The content service lives in `backend/src/services/content/`:

| Path | Role |
| --- | --- |
| `providers/file_system/items.py` | item, image and voice operations (**main file of this change**) |
| `providers/file_system/provider.py` | shared helpers: CSV registry, voice sidecar read/write, folder lookup |
| `providers/file_system/internal_api.py` | HTTP calls to the internal TTS / optimizer endpoints |
| `routes/items.py`, `routes/common.py` | public REST routes and FastAPI dependencies |
| `schemas.py` | Pydantic request/response models |
| `__tests__/` | pytest tests (unit tests against a temp directory, plus HTTP tests with a stub provider) |

Rule to respect: services call each other through internal HTTP endpoints (`/internal/...`), never
in-process. Voice synthesis and optimization already work that way; do not change it.

**Assumed prerequisite (already true when this spec was written).** An item's folder is derived from
its item id alone (`I_WaterBottle` -> `water_bottle`, `ContentProvider._folder_name_from_item_id`), not
from its English word. All voice file names below embed that folder name (`{folder}`).

## 3. Storage model (unchanged by this work)

Per item and language, in `content/<category>/<folder>/`:

| File | Meaning |
| --- | --- |
| `{folder}_{lang}.mp3` | **main voice**: what the app plays. It is a *byte-for-byte copy* of the chosen take. |
| `{folder}_{lang}_{NN}.mp3` | a **take (variant)**. `NN` is a 2-digit, 1-based index; a new take uses the first free index. |
| `<same stem>.csv` | metadata sidecar for that MP3: columns `creation_method` (`generated` / `uploaded` / `optimized` / `unknown`), `provider`, `parameters` (JSON: `_text`, `_optimizer`, `_source`, `date`, provider settings), `hash` (SHA-256 of the MP3). A variant whose `hash` equals the main voice's is its source; both files are then re-hashed, and a sidecar whose `hash` no longer matches its file is reset to `unknown`, `custom`, `{}` with the new hash. |

Identifiers used by the API:

- per-language voice id: `{folder}-v-{lang}` (refers to "the voice for this language");
- variant id: the file stem, e.g. `apple_en_02`;
- the main voice's stem (`apple_en`) is also accepted where a voice reference is expected.

Which take is currently main is **derived, not stored**: compare the main file with each variant (size
first, then SHA-256). The matching variant's id is reported in `main_voice_ids[lang]`, or `null` when
the main matches no variant. Do not introduce a separate "selected" marker.

## 4. Baseline behaviour and the gaps

Endpoints, all under `/api/v1/content/categories/{category_id}/items/{item_id}/voices`:

| Method and path | Baseline behaviour |
| --- | --- |
| `GET /` | lists `voices` (one per language: the main), `variants`, `main_voice_ids` |
| `POST /` | multipart `language` + `audio`; 409 if the language already has a voice |
| `PUT /{voice_id}` | replace a recording's bytes |
| `DELETE /{voice_id}` | delete a recording and its sidecar |
| `PUT /{voice_id}/main` | copy a variant (and its sidecar) over the main file |
| `POST /{voice_id}/optimize` | for each active optimizer, write an optimized variant of the *original* recording; keeps the original; leaves main untouched; re-running replaces that optimizer's previous output |
| `POST /{voice_id}/regenerate` | synthesize the item's current text again |

Gaps this work closes:

1. **Regenerate overwrites.** It rewrote the existing file, so the previous take was destroyed and
   there was nothing new to compare.
2. **Set-main can destroy the first take.** A newly created item has only the main file (no `_01`
   copy). Choosing a variant copies over it and the original recording is gone. `optimize` already
   protects against this internally; `set-main` did not.
3. **The UI keeps Optimize disabled.** The list response carries no `capabilities`, so the frontend
   falls back to "legacy" defaults (`optimize: false`) and shows "not available yet".
4. **UI copy says audio is replaced** (regenerate-all confirmation dialog and success toasts).

## 5. Requirements

### R1. Regenerate adds a take and never overwrites

`POST /{voice_id}/regenerate`:

- `voice_id` may be a per-language id, the main's stem, or a variant id (resolve the language from
  it; unknown reference -> 404).
- Text to synthesize: the item's current word for that language, falling back to English. No text ->
  400 (`... has no {lang} text to synthesize ...`).
- Synthesis goes through the existing internal TTS call. If it fails -> 400
  (`Voice generation failed for {lang}: ...`) and **nothing on disk changes**.
- If the language has **no recording at all** (no main, no variants): write the take as the main voice
  (`{folder}_{lang}.mp3` + sidecar). The response id is `{folder}-v-{lang}`.
- Otherwise:
  1. keep the current main voice as a variant if it is not already a byte-identical copy of one
     (R2 helper);
  2. write the new take to the next free variant slot with a `generated` sidecar (`_text` = the text
     used, plus the provider and its parameters);
  3. **do not touch the main file**.
  The response is the new variant: `id` = its stem, its `file_uri`, `provider`, `parameters`,
  `action: "regenerate"`.
- Legacy content with variants but no main: add the variant; do not create a main.
- Repeated calls keep adding takes (`_02`, `_03`, ...); the original is preserved exactly once.

### R2. Shared helper: keep the main as a variant

Extract one helper, `_keep_main_voice_as_variant(item_path, folder, language) -> Path | None`:

- no main file -> return `None`;
- main is a byte-identical copy of an existing variant -> return that variant, change nothing;
- otherwise copy the main **with its sidecar** to the next free variant slot and return the new path;
  a copy failure is reported as a content-operation error ("Failed to keep the original voice: ...").

Use it in three places: optimize (replaces the equivalent inline block; behaviour must not change),
regenerate (R1) and set-main (R3).

### R3. Set-main never loses a recording

Before copying the chosen variant over the main file, call the R2 helper so an unpreserved main becomes
a variant. Everything else about set-main (copy variant + sidecar, drop a stale sidecar when the
variant has none, no-op when already selected, 404 for unknown ids) stays as it is.

### R4. The list response advertises capabilities

`GET /` gains an optional `capabilities` object (only this endpoint fills it; other responses
that share the model return `null`):

```json
{
  "multiple_voices": false,
  "generate": false,
  "optimize": true,
  "select": true,
  "presets": false,
  "upload_formats": ["mp3"]
}
```

- `optimize` is true only while at least one voice optimizer is active. Resolve it with a FastAPI
  dependency modelled on the existing `get_voice_generation_available` (look the voice-optimization
  service up in the service registry; missing service -> false).
- `multiple_voices` stays false because `POST /` still answers 409 when a voice exists, and the UI uses
  this flag to keep "add" disabled.
- `generate` and `presets` stay false because those endpoints do not exist (section 9).
- The field names and shape must match `frontend/src/types/itemVoices.ts` (`VoiceCapabilities`).

### R5. Frontend wording and docs

- Regenerate-all confirmation dialog (`regenerateAllVoicesConfirmParts` in
  `frontend/src/utils/localizedLabels.ts`, languages en, es, fr, bg): say a new take is added per
  language and the current voice stays in use until another is chosen in **Voices**. Remove "existing
  audio will be replaced".
- Toasts in `frontend/src/views/ItemDetailsView.vue` (`regenerateVoice`, `regenerateAllVoices`): say
  a new take was added and where to choose it. Do not touch the *category* voice regenerate in
  `CategoryDetail.vue`; that is a different endpoint whose behaviour did not change.
- Update `frontend/docs/item-voices-api.md`: the `/regenerate` row and the `capabilities` row.
- Update the route summary/description of the regenerate endpoint and the list endpoint in
  `routes/items.py` (they feed the OpenAPI document).

### R6. Generated API artefacts stay in sync

The response schema and route text changed, so regenerate the OpenAPI document and the TypeScript
client/schemas (commands in section 8). Do not edit the generated files by hand.

## 6. Design notes and constraints

- **Order inside regenerate:** synthesize first, then preserve the main, then write. A TTS failure must
  leave the directory untouched; a write failure after preserving leaves at most one harmless extra copy.
- **No storage format change, no new metadata file, no database.** Existing content must keep working
  unchanged.
- Reuse existing helpers instead of adding new ones: `_split_item_voice_files`, `_main_copy_source_id`,
  `_next_item_voice_variant_path`, `_copy_voice_with_metadata`, `_language_for_item_voice_ref`,
  `_write_generated_voice_metadata`, `_item_voice_path`, `_item_word_for_language`.
- Match the surrounding code style and comment density. The backend is linted with pylint and is
  currently at 10/10; keep it there.
- Do not edit unrelated files. The working tree may contain uncommitted changes that belong to someone
  else (for example a regenerated `frontend/src/data/mockData.ts`); leave them alone.
- Do not commit unless asked.

## 7. Tests to write or update

All in `backend/src/services/content/__tests__/`. Unit tests run against a temp directory with the TTS
call patched; HTTP tests use the stub provider in `content_http_helpers.py`.

Update (behaviour intentionally changed):

- the regenerate test that asserted overwriting -> now asserts the new variant, the untouched main and
  the kept copy;
- the "creates missing file" regenerate test -> still writes the main, response id `{folder}-v-{lang}`,
  no `_01`;
- the sidecar-lifecycle test that regenerated over an uploaded voice -> the uploaded main keeps its
  sidecar, the copy carries `uploaded`, the new take carries `generated`;
- the set-main test whose main matched no variant -> the old main now exists as an extra variant.

Add:

- first regenerate on a main-only language: `_01` = copy of the original, `_02` = new take, main
  unchanged, `main_voice_ids` = `{"en": "apple_en_01"}`;
- repeated regenerate: `_02`, then `_03`, the original kept once;
- main already a copy of a variant: no duplicate is created;
- `voice_id` accepted as `apple-v-en`, `apple_en` and `apple_en_01`;
- the new take's sidecar: `generated`, provider, `_text`, `date`;
- variants but no main: variant added, no main created;
- TTS failure: directory unchanged, error `Voice generation failed for en: ...`;
- workflow: regenerate -> set-main to the new take -> set-main back to `apple_en_01` restores the
  original bytes and no recording is lost;
- HTTP: `GET /voices` returns `capabilities` with `optimize` true and false (parametrised via the app
  factory), other responses return `capabilities: null`, and regenerate returns the new variant id with
  `action: "regenerate"`.

The HTTP test app factory needs an override for the new optimizer-availability dependency (the real
one reads the service registry, which the bare test app does not have).

## 8. Verification

Run from the repository root. The backend suite needs `AUTH_DISABLED=false`; the setting defaults to
`true` and the `*_requires_auth` tests fail without it.

```bash
# backend tests and lint (baseline before this work: 625 passed)
cd backend
AUTH_DISABLED=false PYTHONPATH="$PWD/src" .venv/bin/python -m pytest -q
.venv/bin/python -m pylint src/services/content
cd ..

# regenerate API artefacts (use the backend venv's interpreter)
backend/.venv/bin/python scripts/export_openapi.py
backend/.venv/bin/python scripts/generate_ts_client.py
backend/.venv/bin/python scripts/generate_ts_schemas.py

# frontend
cd frontend && npm run test:voices && npm run build
```

Expected: all tests green (the new tests raise the count), pylint 10/10, and the generated diffs limited
to the new `VoiceCapabilities` schema, the optional `capabilities` field on the voices response and the
new regenerate summary text.

Manual scenario (app running, an item that has a voice):

1. Regenerate one language. Expect `{folder}_{lang}_02.mp3` and its `.csv` next to `{folder}_{lang}.mp3`
   and a new `{folder}_{lang}_01.mp3` (copy of the previous main). The played voice is unchanged and the
   new take is listed in the Voices section.
2. Click Optimize (now enabled). Expect additional optimized variants.
3. Choose the best take. The main file changes; the previous main is still available as a variant.

## 9. Out of scope (deliberately deferred)

- **`POST /generate` and `GET /presets`**, the richer contract described in
  `frontend/docs/item-voices-api.md` (per-request text, voice preset, speed, stability, similarity,
  optional auto-optimize). It needs the TTS providers to accept per-request settings.
- **Appending uploads** (`POST /` accepting a second recording for a language) and non-MP3 uploads.
- **Automatic accuracy scoring** of takes with speech-to-text. The existing
  `/internal/evaluators/evaluate` does not fit: it is shaped for the batch pipeline (needs a normalized
  file path and STT config, and saves into the pipeline's score stores). A per-file scoring endpoint
  that does not touch those stores would be needed.
- Adapting the frontend's Original/Optimized tab preview to the real API: optimized takes appear as
  separate cards.

## 10. Definition of done

- R1 to R6 hold, and the tests in section 7 exist and pass.
- The full backend suite is green with `AUTH_DISABLED=false`, pylint is 10/10 on the content package,
  `npm run test:voices` and `npm run build` pass.
- No existing content needs migrating: files written by the previous behaviour are read correctly.
- No recording can be lost by regenerate, optimize or set-main.
