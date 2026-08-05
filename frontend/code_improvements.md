# Frontend Code Improvements

Full code review of `frontend/` (Vue 3 + TypeScript + Vite SPA, MSW mock layer, generated API
client/schemas). Line references are against the current `dev` branch. The review pays particular
attention to the two stated requirements: the app must work with **both mock data and the real
backend**, and the **schemas/client are generated from the backend**. A prioritized action list is
at the end.

---

## 1. Critical — mock mode and backend mode are both compromised

### 1.1 The production Docker image ships with mocks enabled
- `docker/docker-compose.yml` builds the frontend with `VITE_MOCK_DATA: "true"`, and
  `docker/frontend.Dockerfile` bakes that env into the production bundle.
- `src/main.ts:13–25` starts the MSW service worker and **seeds a fake `debug-token`** whenever
  `VITE_MOCK_DATA === 'true'`. Since MSW intercepts every `/api/v1/content/*` route it has a
  handler for, the deployed container on port 9001 serves **mock data**, not the real backend that
  nginx dutifully proxies to (`docker/nginx.conf`).
- Anyone "testing the deployment" sees mock categories and a bypassed login and may not notice.

**Improvement:** default the compose arg to `"false"` (keep `true` only in a dev/demo override
file, e.g. `docker-compose.demo.yml`), and make the app render a visible "MOCK MODE" badge whenever
the worker is active so the mode is never ambiguous.

### 1.2 The MSW handlers were not migrated to the id-based API
The backend (and the generated client) moved to category **IDs** (`C_Drinks`) in commits
`22c25cb`/`eee6286`, but most hand-written handlers in `src/mocks/contentHandlers.ts` still look
categories up **by English name**:

- `findCategory` (line 20) matches `c.name.en` — but the app calls `/content/categories/C_Drinks`,
  so `GET /categories/:category_name` (line 137), the voices/items/item-images/item-voices handlers
  (lines 192, 224, 237, 284, 322, …) all return **404** for every real request from `cache.ts`.
- Because `cache.ts` swallows those failures (see §3.2), mock mode silently degrades: category
  shells render, but items, voices, and images are empty or fabricated placeholders.

**Improvement:** make every handler resolve by `id` first with a name fallback
(`findCategoryById(param) ?? findCategory(param)` — `findCategoryById` already exists at line 23),
and cover the flows with a couple of Vitest tests that run the real `cache.ts` against the MSW
server (`msw/node`) so mock-mode regressions fail CI instead of failing silently in the browser.

### 1.3 Duplicate and dead handlers from stub appending
`scripts/generate_ts_client.py` appends "missing stubs only" to `contentHandlers.ts`. The result
(lines 402–481) is a second block of handlers that:
- **duplicate** earlier routes with different param names (`:category_name` vs `:category_id` are
  the *same* path pattern to MSW — first registration wins, so most appended stubs are dead);
- return `HttpResponse.json({})` for typed responses (`ItemListResponse`, `CategoryVoicesResponse`)
  — an empty object that satisfies neither the TypeScript types nor the consuming code;
- return hardcoded 404s for routes the backend now implements (item images/voices mutations,
  lines 448–466), so wiring those UI actions can never be exercised in mock mode;
- contain path shapes that don't exist in the backend at all
  (`…/items/:item_id/files/:file_index`, `…/voices/:voice_id/audio` — the old pre-refactor API).

**Improvement:** treat `contentHandlers.ts` the way `contentClient.ts` is treated — fully
generated, never appended. Generate one handler per route from the same route table the client
generator already builds, backed by the generated `mockData.ts` fixtures, and keep hand-written
behavior (stateful create/rename/archive) in a separate `customHandlers.ts` that is spread first.
Delete the legacy-path stubs.

### 1.4 Most mutations in the UI are `console.log` stubs
The backend implements — and the generated client exposes — create/rename/remove/restore category,
the dedicated icon endpoint, and all item image/voice mutations. None of them are wired:

- `CategoriesView.vue:115–135`: `onEditCategory`, `onDeleteCategory`, `onRenameCategory`,
  `onEditVoice`, `onRenameVoice`, `onDeleteVoice`, `onAddItem` → all `console.log`.
- `ItemDetailView.vue:277–331`: item name change, add/replace/delete file, upload voice → all
  `console.log` (the delete-confirmation modal confirms… a log line).
- Unused generated functions confirm it: `categoriesCreate`, `categoriesRename`,
  `categoriesRemove`, `categoriesRestore`, `categoriesIconUpdate`, `itemsImagesAdd/Update/Delete`,
  `itemsVoicesAdd/Update/Delete`, `createItemEndpoint`, `itemsList`, `itemsGet` are imported
  nowhere outside `contentClient.ts`.
- Meanwhile `CategoryDetail.vue` *does* fully wire category voice and image mutations (toasts,
  cache invalidation, cache-busting). So the same component tree mixes two patterns: child calls
  API directly in one flow, child emits to a parent that logs in another.

**Improvement:** pick one pattern — given there is no store, the pragmatic one is what
`CategoryDetail.vue` already does: call the generated client from the owning component, then
`invalidate… + ensure…` refresh. Wire the stubbed actions to the existing client functions
(`categoriesRename` for rename, `itemsImagesAdd/Update/Delete` for the Files section,
`itemsVoicesAdd/Update` for the voice Replace buttons, `createItemEndpoint` for "Add New Item"),
and remove the emit-to-parent indirection that currently ends in logs. The CLAUDE.md claim that
"backend mutation routes are currently 501 stubs, so the generator skips them" is stale on both
counts — the routes exist and the client functions exist.

### 1.5 `listFiles` calls a non-existent endpoint
`src/api/auth.ts:35–41` does `GET /list`; the backend route is `POST /api/v1/list-files`. Dead
(and wrong-method) code — fix or delete. Both `getStatus` and `listFiles` also manually attach the
`Authorization` header (producing `Bearer null` when no token) even though the axios interceptor in
`client.ts` already does this — drop the manual headers.

---

## 2. Codegen pipeline (schemas + client generated from backend)

The discipline is right (generated `contentClient.ts` / `*Schemas.ts`, "do not edit" markers,
`mockData.ts` generated from the real `content/` tree). The mechanics can be strengthened:

### 2.1 Two competing contract sources
- `scripts/generate_ts_client.py` and `generate_ts_schemas.py` parse backend **Python source via
  AST** (route decorators, Pydantic classes). Yet `scripts/export_openapi.py` already exports the
  authoritative OpenAPI document, and `backend/src/services/content/openapi.yaml` is a third,
  hand-maintained spec whose README even says it was "derived from the MSW handlers" — the contract
  flowing *from mocks to spec* is the wrong direction.
- AST parsing silently misses anything it doesn't model (f-string prefixes, `response_model`
  aliases, multipart params) and must be re-taught every FastAPI idiom. Generating from the
  exported `openapi.json` (either with the existing custom scripts or an off-the-shelf generator
  like `openapi-typescript`/`orval`) removes a whole class of drift and lets the same document
  drive client, schema types, and MSW handler stubs.

**Improvement:** make `export_openapi.py` the single source: backend code → OpenAPI → TS types +
client + handler skeletons. Delete or auto-regenerate `content/openapi.yaml` from the export so it
can't rot.

### 2.2 No drift check
Nothing fails when someone edits backend routes and forgets `/api-sync`. Add a CI/`/verify` step
that regenerates into a temp dir and diffs against `src/types/*Schemas.ts` + `src/api/*Client.ts`
(fail on mismatch). This is cheap and catches exactly the bug class that broke the backend's own
content↔translator route contract.

### 2.3 Generated types lie about `LocalizedString`
`contentSchemas.ts` types `name` fields as the `LocalizedString` **class**, but the wire format is
a plain `{ [lang]: string }` object and axios never instantiates the class. Every consumer then
casts: `summary.name as unknown as Record<string, string>` (`cache.ts:136, 161, 171, 242, 297`).
Either:
- generate DTO fields as `Record<string, string>` (honest wire types) and construct
  `LocalizedString` once at the `cache.ts` boundary (which it already does), or
- add a response interceptor that revives `LocalizedString` instances.
The current hybrid gives class-typed fields holding plain objects — the casts are the type system
telling you the model is wrong.

---

## 3. Data layer (`src/api/cache.ts`)

The module-level shared cache with in-flight dedup is a sound lightweight choice (documented "no
Pinia"). Specific issues:

### 3.1 Mixed identity keys
`ensureCategoryDetail(categoryId, categoryName)` takes **both**, tracks loaded state by *name*
(`loadedCategoryDetailIds.add(categoryName)`, line 254), and finds categories by `c.name.en ===
categoryName` — while `ensureItemDetail` keys everything by *id*. Name-keying breaks the moment a
category is renamed (the very operation the UI offers) and forces callers to haul the English name
around (`ensureCategoryDetail(cat.id, cat.name.en)` in both views). Key everything by `id` and drop
the second parameter.

### 3.2 Errors are swallowed into fake-success
- `categoriesVoicesList(...).catch(() => ({ …empty }))` and
  `categoriesImagesList(...).catch(() => ({}) as CategoryImagesResponse)` (lines 228–237, 283–290)
  turn *any* failure — 401, 500, network down — into "this category has no voices/images". Combined
  with `normalizeVoices`'s fabricated fallback entries (line 111: placeholder voices with invented
  `type: 'male'/'female'` per language), the UI can render confident-looking wrong data with no
  error surfaced anywhere.
- `ensureCategoriesLoaded` sets `sharedLoading = true` but `_loadCategoriesBasic` only resets it on
  its success paths; a rejected `categoriesList()` leaves `sharedLoading` stuck `true` and
  `sharedError` never set (only `DashboardView` catches locally; `CategoriesView.onMounted` awaits
  with no catch → unhandled rejection).
- Partial failures are cached forever: a category whose detail fetch rejected stays an empty shell
  because `sharedCategories.length > 0` short-circuits all future loads (line 197).

**Improvement:** let errors propagate to one place — set `sharedError`, reset `sharedLoading` in a
`finally`, render the error state in both views, and don't mark ids "loaded" when the fetch failed.
Remove the fabricated voice placeholders; the backend already returns one entry per supported
language with `file_uri: null` for missing audio, which is the honest signal the UI needs
(`voice.fileUri` empty → upload slot).

### 3.3 Startup fetch storm
`_loadCategoriesBasic` fetches the **details of every category in parallel** (line 150) on first
load, purely so the sidebar filter can search item names. With N categories that is N+1 requests,
and each backend `category_details` call re-reads every CSV in the content root. Options: a
lightweight `?include=items` on the list endpoint, lazy-loading details on category expand, or
searching only after details arrive per category. At minimum, cap the concurrency.

### 3.4 No 401 handling / token lifecycle
- The router guard checks only that `localStorage.token` exists (`router/index.ts:44`); tokens
  expire after 30 min, after which every request 401s with no redirect, no message, and (per §3.2)
  often no visible error at all. Add a response interceptor: on 401 clear the token and
  `router.push('/login')` (keep the current route for post-login redirect).
- "Keep me signed in" (`LoginForm.vue:47`) is a no-op checkbox — wire it or remove it.
- localStorage token is XSS-readable; acceptable for an internal admin tool, but worth a comment,
  and the vendored jQuery-era theme JS enlarges the XSS surface (§6.3).

---

## 4. Views and components

- **`ItemDetailView` / `CategoriesView` case-insensitive id matching** —
  `c.id.toLowerCase() === id.toLowerCase()` (CategoriesView.vue:80, ItemDetailView.vue:214, 222,
  244): IDs are opaque (`C_Drinks`); compare them exactly, or normalize once at the boundary.
  Case-folding ids invites two different ids being treated as one.
- **Icon upload bypasses the icon endpoint** — `CategoryDetail.onFileSelected` (line 545) calls
  `categoriesImagesAdd`/`categoriesImagesUpdate`, so uploading an icon for a category *without* one
  creates a numbered gallery image (`{category}_01.png`), not the icon file (`{category}.png`) the
  backend derives `thumbnail_uri` from. The generated `categoriesIconUpdate` exists precisely for
  this — use it for both create and replace, and `categoriesIconDelete` in `confirmImageDelete`
  instead of reverse-engineering the image id from the thumbnail URI (`imageIdFromUri`).
- **`AppSidebar` shows a hardcoded mock user** (`mockUser` import, line 120–139) in real-backend
  mode: "Allen Moreno, Administrator" regardless of who logged in. Decode the JWT payload
  (username/role are in it) or add a `/me` endpoint; don't import mock fixtures in production
  components.
- **Audio playback**: `audio.play()` returns a promise that rejects (autoplay policies, 404 file)
  — unhandled in both `CategoryDetail.togglePlay` and `ItemDetailView.togglePlay`; also neither
  view stops audio on unmount (`onUnmounted(stopAudio)`), so audio keeps playing after navigating
  away. The two `togglePlay`/`stopAudio`/`hasAudio` implementations are copy-pasted — extract a
  `useAudioPlayer()` composable.
- **`ItemDetailView` delete modal** renders `{{ category?.name }}` (line 175) — a `LocalizedString`
  object; it only works because `toString()` returns the English value. Use the localized label
  helper like everywhere else.
- **Voice "rename"** (`ServerWord` pencil on voice cards) emits `rename-voice`, which cannot be
  persisted — the backend has no voice-name concept (names come from the per-language item/category
  labels). Either drop the affordance or route it to the (future) item/category name update
  endpoint; today it's a modal that ends in a console.log.
- **Duplicated per-view lang maps** — `langFlagMap`/`langLabelMap` are declared in both
  `CategoryDetail.vue` and `ItemDetailView.vue`; move next to `SUPPORTED_LANGUAGES` in
  `types/viewModels.ts` (single source for language metadata: code → flag, label, display name).
- **`DashboardView` double-render state** — `v-if="loading"` / `v-else-if="error"` then a separate
  `v-if="cards.length === 0"` block: while loading, both the spinner *and* "No categories found"
  render. Make the empty state part of the same `v-else` chain.

---

## 5. Types

- **Header comments are swapped**: `types/viewModels.ts` opens with "Mock-data-only types live-in
  types/viewModels.ts" (it *is* viewModels.ts — the mock types live in `types/index.ts`), while
  `types/index.ts` claims business types live elsewhere. Both README.md and CLAUDE.md still
  document `types/index.ts` as holding `Category`, `Voice`, `Image`, etc. Fix comments + docs, or
  better: rename `index.ts` → `mockTypes.ts` so the import paths self-describe.
- **`Image` view-model is barely used** and doesn't match what the UI actually renders (item images
  are plain URI strings in `Item.images`). Either model item images as `{ id, fileUri }` (the ids
  are needed for update/delete wiring — today the Files section has no ids and mutations can't be
  wired without refetching) or drop the interface.
- **`Category` (mock) vs `CategoryFull` (business)** overlap almost entirely; after the mock
  handler regeneration (§1.3) consider one shared shape with optional UI-only fields, so the mock
  fixtures can't drift from the view-model.

---

## 6. Tooling and configuration

### 6.1 No lint, no tests
`package.json` has no ESLint/Prettier and no test runner ("No test framework is configured" per
CLAUDE.md). For a codebase with a generated/mocked seam this is where regressions hide. Minimum
worthwhile setup: `eslint` + `@vue/eslint-config-typescript` + `prettier`, and `vitest` +
`@vue/test-utils` + `msw/node` with three seed tests: cache loads against mock handlers, login
flow, and the codegen-diff check (§2.2). Wire them into `scripts/validate_frontend_build.py` /
`/verify`.

### 6.2 `vite.config.ts` ignores its own env loading
`loadEnv(mode, …)` is called and discarded; `backendTarget` is hardcoded to `127.0.0.1:8000`
(line 10) and a stray `console.log` runs on every command. Honor an env var
(`VITE_BACKEND_URL ?? 'http://127.0.0.1:8000'`) and drop the log. Also `VITE_LOG_REQUESTS`
(client.ts:14) is undocumented — README mentions only `VITE_VERBOSE_REQUESTS`.

### 6.3 Vendored theme weight
`src/assets/vendors/` ships Bootstrap 4-era bundles (jQuery-based `vendor.bundle.*`, Font Awesome
4.7, jVectorMap, carousel banners, face photos) of which the app uses a small fraction — and it
inflates both install size and the XSS surface (§3.4). Not urgent, but plan to either prune unused
vendor files (the `faces/` and `carousel/` imagery, unused icon sets) or migrate the ~6 components
actually styled by the theme to a maintained library.

### 6.4 Dead view
`DashboardView.vue` *is* registered (route `/`, name `dashboard`) — the README claims it isn't;
conversely the README's route table still documents `/categories/:id/images/:imageId` (now
`items/:itemId`) and an `src/api/` layout (`categories.ts`, `categoryVoices.ts`, `items.ts`,
`itemImages.ts`, `itemVoices.ts`, `content.ts`) that does not exist — the real modules are
`client.ts`, `auth.ts`, `cache.ts`, `contentClient.ts`. CLAUDE.md likewise references
`ImageDetailView`/`ImageGrid` (now `ItemDetailView`/`ItemGrid`). Sync both docs with the tree —
for this project the READMEs are load-bearing (they are the stated source of truth).

---

## 7. Prioritized action list

**P0 — the two operating modes**
1. Stop shipping mocks in the production image; add a visible mock-mode indicator (§1.1).
2. Migrate MSW handlers to id-based lookups so mock mode matches the current API (§1.2).
3. Regenerate handlers as a whole instead of appending stubs; delete dead/legacy-path handlers
   (§1.3).
4. Add a 401 interceptor + auth-expiry redirect (§3.4) and surface fetch errors instead of
   `catch(() => empty)` (§3.2).

**P1 — finish the wiring**
5. Wire the stubbed mutations (rename/create/remove category, item files, item voices, item name,
   Add New Item) to the already-generated client functions; unify on the CategoryDetail pattern
   (§1.4).
6. Use `categoriesIconUpdate`/`categoriesIconDelete` for the icon flow (§4).
7. Key the cache by category id, not English name (§3.1).
8. Fix/remove `listFiles` and the manual auth headers (§1.5).

**P2 — contract robustness**
9. Single OpenAPI-driven codegen path + regeneration drift check in `/verify` (§2.1–2.2).
10. Honest wire types for `LocalizedString` fields; kill the `as unknown as` casts (§2.3).
11. Vitest + MSW node tests for the cache layer; ESLint/Prettier (§6.1).

**P3 — cleanup**
12. Real user identity in the sidebar; remove mock imports from production components (§4).
13. Shared language-metadata map, `useAudioPlayer` composable, stop-audio-on-unmount (§4).
14. Doc sync (README/CLAUDE route table, api module list, type-file comments) (§6.4, §5).
15. Prune unused vendor assets or plan the theme migration (§6.3).
