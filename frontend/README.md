# KiddoWords Content Management - Frontend

Vue 3 + TypeScript + Vite application for managing image categories, their voices, and associated image assets.

## Quick Start

```bash
npm install
npm run dev         # Vite dev server: port 5173, host 0.0.0.0 (see vite.config.ts)
npm run build       # vue-tsc --noEmit && vite build
npm run type-check  # vue-tsc --noEmit only
npm run preview     # serve production build locally
```

**Optional offline UI:** If `VITE_MOCK_DATA=true` (e.g. in `.env.development.local`), the app starts [MSW](https://mswjs.io/) (`src/mocks/`) and seeds a placeholder token when none is set.

**Verbose request logging:** If `VITE_VERBOSE_REQUESTS=true`, every axios request/response is logged to the browser console with method, URL, status, and JSON body. Useful during development to inspect payloads.

## Mock Data Mode

Use this mode when developing the UI with local fixtures.

```bash
# in frontend/.env.development.local
VITE_MOCK_DATA=true
VITE_LOG_REQUESTS=true
VITE_VERBOSE_REQUESTS=true

# in frontend/
npm run dev
```

In this mode:

- `src/main.ts` starts the MSW browser worker before mounting Vue.
- `src/mocks/browser.ts` registers handlers from `src/mocks/contentHandlers.ts` and `src/mocks/configurationHandlers.ts`.
- `src/mocks/contentHandlers.ts` serves content fixtures from `src/data/mockData.ts`.
- `src/mocks/configurationHandlers.ts` serves in-memory configuration fixtures for the navbar configuration popup.
- If `localStorage.token` is empty, the app seeds `debug-token` so authenticated routes work.

Quick verification:

1. Open DevTools Console and confirm MSW starts without unhandled request errors.
2. Visit `/categories`; the category sidebar should populate from `mockCategories`.
3. Select a category and an item; the page should receive MSW responses.
4. Rename, delete, upload, or configuration actions should either update the in-memory mock state or return the intentional mock response from the handler.

## Project Structure

```
src/
├── api/              # REST API client modules
│   ├── auth.ts       # Login, status, file-list endpoints
│   ├── cache.ts      # Shared content cache + DTO-to-view-model mapping
│   ├── client.ts     # Axios client factory for app requests
│   ├── internalClient.ts
│   ├── configurationClient.ts # [generated] Configuration API client
│   └── contentClient.ts       # [generated] Content API client
├── assets/           # Static assets (copied from vue3-demo)
│   ├── css/          # Shared + demo_1 Star Admin stylesheets
│   ├── images/       # Faces, logos, samples, auth backgrounds
│   └── vendors/      # Bootstrap, MDI, Font Awesome, flag-icons, etc.
├── components/       # Reusable UI components
│   ├── CategoryDetail.vue
│   ├── ServerWord.vue
│   ├── ItemGrid.vue
│   └── LoginForm.vue
├── composables/      # Shared composition helpers (e.g. content UI language)
├── utils/            # Pure helpers (labels from voices, title-case formatting, etc.)
│   └── localizedLabels.ts  # UI chrome strings keyed by content language (en, es, fr, bg)
├── data/
│   └── mockData.ts   # [generated] Sidebar / MSW sample categories
├── mocks/            # MSW handlers (used when VITE_MOCK_DATA=true)
├── layouts/          # Application shell components
│   ├── AppLayout.vue
│   ├── AppNavbar.vue
│   ├── AppSidebar.vue
│   └── AppFooter.vue
├── router/
│   └── index.ts      # Route definitions and auth guard
├── types/
│   ├── index.ts      # Hand-written mock-only helper types (currently just `User`)
│   ├── localization.ts  # LocalizedString class with typed getters + JSON serialization
│   ├── viewModels.ts # Hand-written UI view-model types (CategoryFull, Item, Voice, Image, etc.)
│   ├── configurationSchemas.ts     # [generated] Configuration DTOs
│   ├── contentSchemas.ts           # [generated] Content DTOs
│   ├── evaluationSchemas.ts        # [generated] Evaluation DTOs
│   ├── loggingSchemas.ts           # [generated] Logging DTOs
│   ├── sharedSchemas.ts            # [generated] Shared DTOs
│   ├── textToSpeechSchemas.ts      # [generated] Text-to-speech DTOs
│   ├── textTranslationSchemas.ts   # [generated] Text-translation DTOs
│   └── voiceOptimizationSchemas.ts # [generated] Voice-optimization DTOs
├── views/            # Route-level page components
│   ├── CategoriesView.vue
│   ├── ItemDetailView.vue
│   ├── LoginView.vue
│   └── DashboardView.vue   # present but not registered in the router
├── App.vue           # Root component (bare router-view)
├── env.d.ts          # Vite/Vue/SVG type declarations
├── main.ts           # App entry point and CSS imports
└── .cursorignore     # Cursor ignore file NOTE: the file is different in each *-sync branch!!!
```

## Routes


| Path                                      | View                                  | Layout                    | Description                               |
| ----------------------------------------- | ------------------------------------- | ------------------------- | ----------------------------------------- |
| `/login`                                  | `LoginView`                           | None (full-page)          | Authentication screen                     |
| `/`                                       | —                                     | —                         | Redirects to `/categories`                |
| `/categories`                             | `[CategoriesView](#categoriesview)`   | `[AppLayout](#applayout)` | Shows first category by default           |
| `/categories/:categoryId`                 | `[CategoriesView](#categoriesview)`   | `[AppLayout](#applayout)` | Selected category details + image grid    |
| `/categories/:categoryId/items/:itemId` | `[ItemDetailView](#itemdetailview)` | `[AppLayout](#applayout)` | Item detail: files, voices, name editing |


All `/categories` routes require authentication (token in localStorage). Unauthenticated users are redirected to `/login`.

## Layouts

### AppLayout

The main authenticated shell, wrapping all `/categories` routes:

```
┌──────────────────────────────────────────────┐
│  AppNavbar (fixed top)                       │
├──────────┬───────────────────────────────────┤
│          │                                   │
│AppSidebar│  <router-view />                  │
│ (270px)  │  (CategoriesView or               │
│          │   ItemDetailView)                 │
│          │                                   │
│          ├───────────────────────────────────┤
│          │  AppFooter                        │
└──────────┴───────────────────────────────────┘
```

- **AppNavbar** -- Brand logo; **content language** selector (en/es/fr/bg with flags) and user avatar / sign-out are grouped on the **right** of the bar (`navbar-menu-wrapper` uses remaining width with end alignment; both controls stay visible at all breakpoints). Scoped styles in `AppNavbar.vue` fix vendor + Bootstrap rules that otherwise stretch language and user dropdowns to the full `navbar-menu-wrapper` width under narrow layouts, and anchor menus under their toggles (`top: 100%`); the language menu is nudged slightly right (`right: -8px`) while the user menu stays flush (`right: 0`), overriding the theme language rule `left: -15px !important`. Opening one header dropdown closes the other. On small viewports, opening either dropdown closes the off-canvas category sidebar, and opening the sidebar closes both dropdowns. At **max-width 991px**, the logo / mini-logo area toggles the off-canvas categories drawer (home links apply at wider widths; there is no separate header menu button). The drawer slides in from the **left** with a short eased transition (see `AppSidebar.vue` scoped overrides). The selected language is shared with the sidebar (via `AppLayout` provide/inject), persisted in `localStorage` under `mx-admin-content-language`, and controls which `Voice.name` is shown for each category and image in the tree. There is no global header search bar; use the sidebar filter on category routes.
- **AppSidebar** -- User profile (avatar, name, role), fuzzy-filter input (bright `::placeholder` for contrast on the blue bar), expandable category tree with child image labels in the **navbar-selected language** (`Voice.name` for that language, else **English** `Voice.name`, else the stored category/image `name`). Choosing a category or image emits `close-sidebar` so the off-canvas drawer closes on narrow viewports. Next to the **Categories** heading, **Expand all** / **Collapse all** toggle every top-level category open or closed. Clicking a category navigates to its detail; clicking an image navigates to its detail page.
- **AppFooter** -- Simple branded footer.

## Views

### LoginView

Full-page authentication screen (Star Admin auth theme). Uses `LoginForm` component with username/password fields, "Keep me signed in", and Google login placeholder.

### CategoriesView

Displayed when a category is selected (or defaults to first category):

- **Breadcrumb** navigation: root + category segments follow the **navbar content language** (same as the sidebar and ItemDetailView). Root crumb is translated for en/es/fr/bg; category segment uses the matching `Voice.name`, then the **English** voice if that language is missing, then the stored `name`.
- **Page header** -- `h3.page-title` with localized category label + `toPascalCaseWords` (navbar content language; no edit control on the title)
- **CategoryDetail card** -- **Category Name** and each **voice-card** word use `ServerWord` (category row: localized + rename modal; voice row: `voice.name` as-is, pencil → **Edit word** modal, `rename-voice` emit). Two-row layout: **Category Icon** (`aspect-ratio: 1 / 1`, `object-fit: contain`) and **Category Voices** share a baseline; voice grid `repeat(auto-fit, minmax(7.5rem, 1fr))`; play + **Upload** (audio); corner **delete** icon opens **Delete or reset voice** confirmation, then `delete-voice` emit; modals mutually exclusive
- **ItemGrid** -- responsive 4-column grid of item thumbnails, each with a pencil edit-icon badge. If an item has no `thumbnailUri` or file entries, a built-in camera placeholder is shown. Clicking navigates to ItemDetailView.

### ItemDetailView

Displayed for a specific image within a category:

- **Breadcrumb** navigation: root + category + image labels follow the **navbar content language** (same as the sidebar). Category and image segments use the matching `Voice.name`, then the **English** voice if that language is missing, then the stored `name`. The root crumb is translated for en/es/fr/bg (unknown codes fall back to English).
- **Page title** (`h3.page-title`) uses the same localized image label as the last breadcrumb segment, formatted with **per-word Pascal-style casing** (first letter of each word uppercased, the rest lowercased via `toLocaleUpperCase` / `toLocaleLowerCase`).
- **Image name** editable input
- **Files section** -- grid of the image's file variants with replace/delete actions. Delete opens a confirmation modal and is available even when only one file is shown; selection/confirmation actions are currently logged until API wiring is completed. **Add File** opens the system file picker (`image/`*). If an image has no files, this section shows a camera placeholder card and only a single pencil **Edit** action.
- **Voices table** -- one row per supported language (en, es, fr, bg) showing flag, voice name (text only), type badge, and replace/upload button.

## Components


| Component        | Props                                   | Emits                                                                    | Purpose                                         |
| ---------------- | --------------------------------------- | ------------------------------------------------------------------------ | ----------------------------------------------- |
| `ServerWord`     | `category` and/or `text` + style props  | `edit`                                                                   | Label + pencil; `text` overrides display source |
| `CategoryDetail` | `category: CategoryFull`                | `edit`, `delete`, `rename`, `edit-voice`, `rename-voice`, `delete-voice` | Category info card with voice list              |
| `ItemGrid`       | `items: Item[]`, `categoryId: string` | —                                                                        | Thumbnail grid with camera fallback for missing items |
| `LoginForm`      | —                                       | —                                                                        | Login form with auth API integration            |


**ServerWord** — Source: non-empty `text` is shown as-is; otherwise `localizedCategoryLabel(category, content language)`. `pascalCase` runs `toPascalCaseWords` on that raw string. Styling: `rootClass`, `textClass`, `buttonClass`, `iconClass`, `iconStyle`; `textTag` (default `h5`, `mb-0` only for `h5`); `inheritAttrs: false` with class/style on the root merged from `$attrs` + `rootClass`. DOM hooks: `server-word-root`, `server-word-text`, `server-word-edit`, `server-word-icon`.

## API Specification

The full REST API contract is defined in [`backend/src/services/content/openapi.yaml`](../backend/src/services/content/openapi.yaml) (OpenAPI 3.1). It covers every endpoint the
frontend relies on — derived from the MSW handlers (`src/mocks/handlers.ts`), API client modules (`src/api/`), mock
data shapes (`src/data/mockData.ts`), and the UI actions exposed by components.

**Tag groups:** Auth, Categories, Category Voices, Images, Image Files, Image Voices.

## Domain Types

Three type layers exist:

- **`src/types/localization.ts`** — `LocalizedString` class with typed property getters for every supported language code (`en`, `es`, `fr`, `bg`, `de`, `it`, `pl`, `uk`, `ro`, `nl`); falls back to English when a translation is absent. Serializes to/from the JSON wire format `{"en": "dog", "fr": "chien"}` via `toJSON()`. Use `LocalizedString.fromJSON(data)` to deserialize responses.
- **`src/utils/localizedLabels.ts`** — Static UI strings (buttons, headings, loading/error/empty states) for the four content UI languages (`en`, `es`, `fr`, `bg`). Views call helpers such as `breadcrumbCategoriesLabel()`, `loadingCategoriesLabel()`, and `failedToLoadCategoriesLabel()` with the active `contentLanguage` from `useContentLanguage()`. Category and item display names come from backend voice/name data via `localizedCategoryLabel()` / `localizedImageLabel()`, not from this file.
- **`src/types/contentSchemas.ts`** — Auto-generated content DTOs (do not edit). Imports `LocalizedString`. Regenerate by running `scripts/generate_ts_schemas.py`.
- **`src/types/viewModels.ts`** — Hand-written UI view-model types (camelCase; UI-only fields). `src/api/cache.ts` maps content DTOs → these types.
- **`src/types/index.ts`** — Hand-written helper types outside the DTO/view-model split. Currently just `User` — used by `src/composables/useCurrentUser.ts`, which decodes it from the logged-in user's JWT payload (`src/utils/jwt.ts`) rather than from mock data.

```typescript
// src/types/viewModels.ts (hand-written view-models)
type Language = 'en' | 'es' | 'fr' | 'bg'

const SUPPORTED_LANGUAGES: Language[] = ['en', 'es', 'fr', 'bg']

interface Image {
  id: string
  name: string
  language: Language
  iconUri: string
}

interface Voice {
  id: string
  name: string
  language: Language
  type: string        // e.g. "male", "female", "ambient"
  fileUri?: string   // if absent/empty, play is disabled for that voice
}

interface Item {
  id: string
  name: string
  images: string[]
  voices: Voice[]
  thumbnailUri: string
  imagesCount?: number
}

interface CategoryFull {
  id: string
  name: LocalizedString
  description: LocalizedString
  thumbnail_uri?: string | null
  items_count?: number
  images: Image[]
  voices: Voice[]
  items: Item[]
}
```

```typescript
// src/types/index.ts (mock-only helper types)
interface User {
  name: string
  role: string
  avatarUri: string
}
```

`name`/`description` fields on categories are `LocalizedString` end to end — both in `contentSchemas.ts` DTOs and in `CategoryFull` — resolved to a plain string only at render time via `localizedCategoryLabel()` / `LocalizedString.get(lang)`, using the content language selected in the navbar. `Item.name` and `Voice.name` are already plain, per-item/per-voice strings (not localized).

## Styles

Imported from `src/main.ts` (Star Admin / vue3-demo lineage):

- `assets/vendors/css/vendor.bundle.base.css` -- Bootstrap 4 + Perfect Scrollbar + jVectorMap
- `assets/vendors/css/vendor.bundle.addons.css` -- Additional vendor styles
- `assets/css/shared/style.css` -- Star Admin shared theme
- `assets/css/demo_1/style.css` -- Demo skin overrides (sidebar, navbar, cards)
- `assets/css/auth.css` -- Login page styles
- Icon fonts under `assets/vendors/`: MDI, Font Awesome 4.7, flag-icon-css
