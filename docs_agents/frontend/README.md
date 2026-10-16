# Frontend — Agent Documentation Index

> Owner role: **frontend** (`frontend/**`, `docs_agents/frontend/**`) · Default runner: Codex · Verified at commit `e2ddd9c` · evidence tags: **[V]** executed, **[S]** source-read, **[U]** unverified — see [discovery](../discovery/README.md)
> Pre-existing frontend docs (preserved): [`frontend/README.md`](../../frontend/README.md), [`frontend/CLAUDE.md`](../../frontend/CLAUDE.md), [`frontend/code_improvements.md`](../../frontend/code_improvements.md), [`frontend/docs/types-usage.md`](../../frontend/docs/types-usage.md), [`frontend/docs/item-voices-api.md`](../../frontend/docs/item-voices-api.md), [`frontend/docs/backend-regeneration-agent-tasks.md`](../../frontend/docs/backend-regeneration-agent-tasks.md), [`frontend/docs/openapi.yaml`](../../frontend/docs/openapi.yaml) (legacy hand-written design spec: **0 of its 24 operations exist at runtime**, so do not use it as a contract)

## What it is

A Vue 3 + TypeScript + Vite 8 single-page admin app for Kiddo content. It uses axios, vue-router, the Star Admin Bootstrap 4 theme (vendored under `src/assets/vendors/`) and MDI icons, with no state-management library **[S]**.

| Route | View | Guard |
| --- | --- | --- |
| `/login` | `LoginView` | — |
| `/` | `DashboardView` (inside `AppLayout`) | `requiresAuth` (token present in `localStorage`) |
| `/categories`, `/categories/:categoryId` | `CategoriesView` | `requiresAuth` |
| `/categories/:categoryId/items/:itemId` | `ItemDetailsView` | `requiresAuth` |
| `/configuration`, `/configuration/:serviceName` | `ConfigurationsView`, `ServiceConfigurationView` | `requiresAuth` + `requiresWriteAccess` (viewers are sent to the dashboard) |

Editing controls are hidden behind `hasWriteAccess()` (`src/utils/permissions.ts`), and the backend refuses viewers regardless.

## Commands (all exist in `frontend/package.json`)

| Command | Purpose | Baseline |
| --- | --- | --- |
| `npm run dev` | Dev server on `:5173`; proxies `/api` and `/internal` to `http://127.0.0.1:8000`; **serves `/content` directly from the local content directory** (dev only) | not run |
| `npm run type-check` | `vue-tsc --noEmit` | pass **[V]** |
| `npm run build` | type-check + `vite build` | pass, no warnings **[V]** |
| `npm run test:voices` | `node --experimental-strip-types --test tests/itemVoices.test.ts tests/itemVoicesApi.test.mjs` | 29/29 **[V]** |
| `npm run test:content` | `node --test tests/contentAvailability.test.mjs` | 5/5 **[V]** |
| `npm run test:auth` | `node --test tests/auth.test.mjs` | 8/8 **[V]** |

There is no component/DOM test framework and no lint script. Do not invent `npm run` targets; adding one is a Frontend change with its own review. Note that `frontend/CLAUDE.md` says "No test framework is configured", which is stale (discovery F-9).

Build-time flags read by the source: `VITE_MOCK_DATA` (starts MSW with `src/mocks/*Handlers.ts`), `VITE_ARCHIVE_CONFIRMATION_MODE`, `VITE_DELETE_ITEM_CONFIRMATION_MODE`, `VITE_LOG_REQUESTS`, `VITE_VERBOSE_REQUESTS` **[V]**. `VITE_DEBUG_MODE` is passed by Docker but not read anywhere in `src/` **[V]**.

## Data layer

| File | Kind | Role |
| --- | --- | --- |
| `src/api/client.ts` | hand-written | axios instance factory (`baseURL /api/v1`), Bearer-token and logging interceptors |
| `src/api/internalClient.ts` | hand-written | axios instance for `/internal/*` (served at the server root) |
| `src/api/contentClient.ts` | **generated** (`scripts/generate_ts_client.py content`) | Typed calls for `/api/v1/content/*` |
| `src/api/configurationClient.ts` | **generated** (`scripts/generate_ts_client.py configuration`) | Typed calls for `/internal/configurations/*`; **drifted from its generator** (+6/−6 lines) **[V]** |
| `src/types/*Schemas.ts` (9 files) | **generated** (`scripts/generate_ts_schemas.py`) | DTO interfaces parsed from backend `schemas.py` files; reproducible **[V]** |
| `src/mocks/contentHandlers.ts`, `configurationHandlers.ts` | generated stubs + hand edits | MSW handlers for mock mode |
| `src/api/cache.ts`, `configurationCache.ts`, `uiConfigurationDb.ts`, `itemVoicesClient.ts`, `auth.ts` | hand-written | Caching boundary, auth (see below) |
| `src/types/viewModels.ts`, `index.ts`, `itemVoices.ts`, `localization.ts` | hand-written | UI view-models; `Language` union is hand-maintained |

**Rule:** never hand-edit generated files; regenerate them. The generators parse **Python source** (AST), not the OpenAPI document, so their output can disagree with `docs_agents/backend/**/openapi.yaml` without any check noticing ([api-contracts](../architecture/api-contracts.md)).

## Cache layers (summary; a full `caching.md` is a pending Frontend task)

| Layer | Location | Semantics **[S]** |
| --- | --- | --- |
| Shared reactive content cache | `src/api/cache.ts` | Module-level `sharedCategories`, `sharedCategoryNames`, `sharedArchivedCategories`, loading/error/unavailable flags; `ensure*` (load once) / `invalidate*` (drop) pairs per category and item; in-flight request dedup (`withInflight`); serving cached categories triggers `revalidateContent()`; a 503 drops the cache and sets `sharedContentUnavailable` |
| Media cache-busting | `cache.ts` (`categoryIconVersions`, `itemThumbnailVersions`, the latter persisted in `localStorage`) + `src/utils/cacheBustedUri.ts` | Appends `?v=<n>` after a mutation because file URIs stay the same across re-uploads |
| Configuration cache | `src/api/configurationCache.ts` | Service list and per-service config lists with ensure/refresh; refreshing content configs revalidates content |
| UI preferences | `src/api/uiConfigurationDb.ts` | IndexedDB `mx-admin` → store `ui_configuration` |
| Token | `localStorage.token` | Read by the router guard, the client interceptor and `useCurrentUser()` |

Mutation pattern: components call the generated client, then `invalidate…` + `ensure…` (see `CategoryDetail.vue`, `AddItemModal.vue`, `ItemDetailsView.vue`).

Preliminary risks to verify with tests **[S]**:

- Logout (`AppNavbar.vue`) only removes the token, so module-level caches survive a user switch within the same tab.
- There is no 401 response interceptor, so expired tokens are not handled.

Both are also in `frontend/code_improvements.md` §3.4.

## Deployment notes **[S]**

The Docker image is served by nginx (`docker/nginx.conf`), which proxies `/api/`, `/internal/` **and** `/content/` to the backend. Internal endpoints are therefore reachable from the frontend's public port, protected only by the backend's role checks. Those checks are disabled by default (`AUTH_DISABLED`, discovery SEC-1).

## User help

There is no in-app user help today. The target (v3, unchanged in v4 §6.5) is app-renderable Markdown under `docs_agents/frontend/help/`, exposed through a safe reader/build integration. That is future Frontend work; no placeholder was created.
