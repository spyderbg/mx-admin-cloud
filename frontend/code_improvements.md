# Frontend Code Improvements

Full code review of `frontend/` (Vue 3 + TypeScript + Vite SPA, MSW mock layer, generated API
client/schemas). Line references are against the current `dev` branch. The review pays particular
attention to the two stated requirements: the app must work with **both mock data and the real
backend**, and the **schemas/client are generated from the backend**. A prioritized action list is
at the end.


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


---

## 3. Data layer (`src/api/cache.ts`)

The module-level shared cache with in-flight dedup is a sound lightweight choice (documented "no
Pinia"). Specific issues:


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

- **ME endpoints** - add a `/me` endpoint; don't import mock fixtures in production
  components.

- **Icon upload bypasses the icon endpoint** — `CategoryDetail.onFileSelected` (line 545) calls
  `categoriesImagesAdd`/`categoriesImagesUpdate`, so uploading an icon for a category *without* one
  creates a numbered gallery image (`{category}_01.png`), not the icon file (`{category}.png`) the
  backend derives `thumbnail_uri` from. The generated `categoriesIconUpdate` exists precisely for
  this — use it for both create and replace, and `categoriesIconDelete` in `confirmImageDelete`
  instead of reverse-engineering the image id from the thumbnail URI (`imageIdFromUri`).
- 
- **Audio playback**: `audio.play()` returns a promise that rejects (autoplay policies, 404 file)
  — unhandled in both `CategoryDetail.togglePlay` and `ItemDetailView.togglePlay`; also neither
  view stops audio on unmount (`onUnmounted(stopAudio)`), so audio keeps playing after navigating
  away. The two `togglePlay`/`stopAudio`/`hasAudio` implementations are copy-pasted — extract a
  `useAudioPlayer()` composable.
- 
- **`ItemDetailView` delete modal** renders `{{ category?.name }}` (line 175) — a `LocalizedString`
  object; it only works because `toString()` returns the English value. Use the localized label
  helper like everywhere else.
- 
- **Voice "rename"** (`ServerWord` pencil on voice cards) emits `rename-voice`, which cannot be
  persisted — the backend has no voice-name concept (names come from the per-language item/category
  labels). Either drop the affordance or route it to the (future) item/category name update
  endpoint; today it's a modal that ends in a console.log.
- 
- **Duplicated per-view lang maps** — `langFlagMap`/`langLabelMap` are declared in both
  `CategoryDetail.vue` and `ItemDetailView.vue`; move next to `SUPPORTED_LANGUAGES` in
  `types/viewModels.ts` (single source for language metadata: code → flag, label, display name).
- 
- **`DashboardView` double-render state** — `v-if="loading"` / `v-else-if="error"` then a separate
  `v-if="cards.length === 0"` block: while loading, both the spinner *and* "No categories found"
  render. Make the empty state part of the same `v-else` chain.

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

### 6.3 **Vendored theme weight
`src/assets/vendors/` ships** Bootstrap 4-era bundles (jQuery-based `vendor.bundle.*`, Font Awesome
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
