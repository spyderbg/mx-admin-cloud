# Frontend Code Improvements

Full code review of `frontend/` (Vue 3 + TypeScript + Vite SPA, MSW mock layer, generated API
client/schemas). Line references are against the current `dev` branch. The review pays particular
attention to the two stated requirements: the app must work with **both mock data and the real
backend**, and the **schemas/client are generated from the backend**. A prioritized action list is
at the end.


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

- **Voice "rename"** (`ServerWord` pencil on voice cards) emits `rename-voice`, which cannot be
  persisted — the backend has no voice-name concept (names come from the per-language item/category
  labels). Either drop the affordance or route it to the (future) item/category name update
  endpoint; today it's a modal that ends in a console.log.
- 
- **Duplicated per-view lang maps** — `langFlagMap`/`langLabelMap` are declared in both
  `CategoryDetail.vue` and `ItemDetailsView.vue`; move next to `SUPPORTED_LANGUAGES` in
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
`ImageDetailView`/`ImageGrid` (now `ItemDetailsView`/`ItemGrid`). Sync both docs with the tree —
for this project the READMEs are load-bearing (they are the stated source of truth).

---

## 7. Prioritized action list

**P0 — the two operating modes**
3. Regenerate handlers as a whole instead of appending stubs; delete dead/legacy-path handlers
   (§1.3).

**P1 — finish the wiring**
6. Use `categoriesIconUpdate`/`categoriesIconDelete` for the icon flow (§4).

**P2 — contract robustness**
9. Single OpenAPI-driven codegen path + regeneration drift check in `/verify` (§2.1–2.2).
10. Honest wire types for `LocalizedString` fields; kill the `as unknown as` casts (§2.3).
11. Vitest + MSW node tests for the cache layer; ESLint/Prettier (§6.1).

**P3 — cleanup**
15. Prune unused vendor assets or plan the theme migration (§6.3).
