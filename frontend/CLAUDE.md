# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in the frontend directory.

## Project Overview

**mx-admin** is a content management and voice synthesis platform for the Kiddo language learning app. It manages
multilingual categories/images and orchestrates a TTS pipeline across multiple providers.

Three components: **FastAPI backend**, **Vue 3 frontend SPA**, **Typer CLI client**.

## Commands

```bash
npm install
npm run dev        # Dev server at http://localhost:5173 (proxies /api → :8000)
npm run build      # Type-check + production build
npm run type-check # vue-tsc --noEmit only
```

### Docker (all services, from project root)

```bash
./scripts/app.sh start       # Backend :9000, Frontend :9001
./scripts/app.sh stop
# Or directly:
docker compose -f docker/docker-compose.yml up -d --build
```

No test framework is configured.

## Architecture

Vue 3 + TypeScript + Vite SPA. API proxied to `:8000` in dev via `vite.config.ts`.
No state management library (no Pinia/Vuex) — data flows via props.

### Routing & Auth

`src/router/index.ts` defines two top-level trees:

- `/login` — unauthenticated, renders `LoginView`
- `/` — requires auth (`meta: { requiresAuth: true }`), uses `AppLayout` as shell; children are `DashboardView`
  (`''`), `CategoriesView` (`categories`, `categories/:categoryId`), and `ItemDetailsView`
  (`categories/:categoryId/items/:itemId`)

Auth guard checks `localStorage.getItem('token')`. Token is written by `src/api/auth.ts` on successful login (POST
`/api/v1/login` with `application/x-www-form-urlencoded`).

### Data Layer

The API contract is **generated from the backend** by `scripts/generate_ts_schemas.py` (Pydantic models →
`src/types/*Schemas.ts` DTO interfaces) and `scripts/generate_ts_client.py` (FastAPI routes → `src/api/*Client.ts`
typed axios calls). **Never hand-edit the generated files** (`src/types/*Schemas.ts`, `src/api/contentClient.ts`) —
re-run the scripts to refresh them.

Layering for content/categories:

- `src/api/client.ts` — base axios instance (`baseURL: /api/v1`) + the Bearer-token interceptor.
- `src/api/contentClient.ts` — **generated** thin wrappers over `/content/*` routes (snake_case DTOs).
- `src/api/cache.ts` — **hand-written** boundary that views consume. It calls `contentClient.ts`, maps backend DTOs
  (snake_case, e.g. `file_uri`) to the camelCase UI view-models in `src/types/viewModels.ts`, and provides in-flight
  request **dedup** (`withInflight`) plus shared reactive state (`sharedCategories`, `sharedCategoryNames`,
  `sharedLoading`, `sharedError`) so multiple views don't re-fetch the same data. Views import
  `ensureCategoriesLoaded()` / `ensureCategoryDetail()` / `ensureItemDetail()` from here.
- `src/api/auth.ts` — login/status/list (no generated equivalent; handles the token).

Mutation routes (category rename/icon/voices, item create, item images/voices) are implemented on the backend and
exposed in `contentClient.ts`. Components call them directly and then refresh via `cache.ts`'s
`invalidate…` + `ensure…` pair — see `CategoryDetail.vue` (icon/voice mutations), `CategoriesView.vue` ("Add New
Item" → `createItemEndpoint`), and `ItemDetailsView.vue` (item file/voice mutations) for the pattern to follow for
any new mutation. `src/data/mockData.ts` is only consumed by `src/mocks/contentHandlers.ts` (MSW fixtures) — `cache.ts`
has no mock fallback at runtime.

### Component Structure

- `src/layouts/` — `AppLayout` (shell), `AppNavbar`, `AppSidebar`, `AppFooter`
- `src/views/` — page-level components (`CategoriesView`, `ItemDetailsView`, `LoginView`, `DashboardView`)
- `src/components/` — reusable components (`CategoryDetail`, `ItemGrid`, `LoginForm`)
- `src/types/viewModels.ts` — hand-written UI view-model types: `CategoryFull`, `Image`, `Voice`, `Item`, `Language`,
  `SUPPORTED_LANGUAGES`. `src/types/index.ts` holds only `User` (built from the JWT payload by
  `useCurrentUser()`, not mock data). Backend DTOs live in the generated `src/types/*Schemas.ts`.

`AppSidebar` renders a filterable category tree; expanding a category navigates to `/categories/:id`; clicking an item
navigates to `/categories/:id/items/:itemId`.

### UI Framework

Star Admin Bootstrap 4 theme (vendor CSS/JS in `src/assets/vendors/`). Use Bootstrap 4 utility classes and MDI icons (
`mdi mdi-*`). Flag icons use `flag-icon flag-icon-<code>` with the mapping `en → us`.

**Modal pattern**: All modals are implemented with `<Teleport to="body">` and `cd-` prefixed CSS classes (e.g.
`cd-modal-overlay`, `cd-modal-box`) to avoid conflicts with Bootstrap's `.close` and `.modal` styles. Follow this
pattern for any new modals.

### Types

Two layers: **generated DTOs** in `src/types/*Schemas.ts` (backend-shaped, snake_case — do not edit) and the
**hand-written UI view-models** in `src/types/viewModels.ts` (camelCase, with UI-only fields the backend doesn't
provide). `src/api/cache.ts` is the boundary that maps DTOs → UI types. `src/types/index.ts` is separate — a small
hand-written helper file holding only `User`, not part of this DTO/view-model split.

`Language = 'en' | 'es' | 'fr' | 'bg'` (+ runtime `SUPPORTED_LANGUAGES`) are hand-maintained in `viewModels.ts` — the
backend exposes languages as plain strings, so codegen can't emit them. A UI `Voice` with an absent or empty `fileUri`
(mapped from the backend's `file_uri`) is considered missing; play controls must be disabled in that case.
`CategoryFull.name`/`description` stay `LocalizedString` end to end (DTO and view-model both); resolve to a display
string only at render time via `localizedCategoryLabel()`.
