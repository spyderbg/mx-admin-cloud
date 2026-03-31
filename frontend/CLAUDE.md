# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
npm install
npm run dev        # Dev server at http://localhost:5173 (proxies /api → :8000)
npm run build      # Type-check + production build
npm run type-check # vue-tsc --noEmit only
```

No test framework is configured.

## Architecture

Vue 3 + TypeScript + Vite SPA. No state management library (no Pinia/Vuex) — data flows via props.

### Routing & Auth

`src/router/index.ts` defines two top-level trees:
- `/login` — unauthenticated, renders `LoginView`
- `/categories` — requires auth (`meta: { requiresAuth: true }`), uses `AppLayout` as shell; children are `CategoriesView` and `ImageDetailView`

Auth guard checks `localStorage.getItem('token')`. Token is written by `src/api/auth.ts` on successful login (POST `/api/v1/login` with `application/x-www-form-urlencoded`).

### Data Layer

**All views currently use mock data from `src/data/mockData.ts`** — `CategoriesView` and `ImageDetailView` import `mockCategories` directly; action handlers (`onEditCategory`, `onDeleteCategory`, etc.) only `console.log`. The real API integration is not yet wired up. `src/api/auth.ts` is the only real API module.

When replacing mock data with real API calls, add new modules to `src/api/` and inject the `Authorization: Bearer <token>` header (token from `localStorage`).

### Component Structure

- `src/layouts/` — `AppLayout` (shell), `AppNavbar`, `AppSidebar`, `AppFooter`
- `src/views/` — page-level components (`CategoriesView`, `ImageDetailView`, `LoginView`, `DashboardView`)
- `src/components/` — reusable components (`CategoryDetail`, `ImageGrid`, `LoginForm`)
- `src/types/index.ts` — shared types: `Category`, `Image`, `Voice`, `User`, `Language`

`AppSidebar` renders a filterable category tree; expanding a category navigates to `/categories/:id`; clicking an image navigates to `/categories/:id/images/:imageId`.

### UI Framework

Star Admin Bootstrap 4 theme (vendor CSS/JS in `src/assets/vendors/`). Use Bootstrap 4 utility classes and MDI icons (`mdi mdi-*`). Flag icons use `flag-icon flag-icon-<code>` with the mapping `en → us`.

**Modal pattern**: All modals are implemented with `<Teleport to="body">` and `cd-` prefixed CSS classes (e.g. `cd-modal-overlay`, `cd-modal-box`) to avoid conflicts with Bootstrap's `.close` and `.modal` styles. Follow this pattern for any new modals.

### Types

`Language = 'en' | 'es' | 'fr' | 'bg'` — supported languages. A `Voice` with an absent or empty `audioUrl` is considered missing; play controls must be disabled in that case.