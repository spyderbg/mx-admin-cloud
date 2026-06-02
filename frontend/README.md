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

The dev server proxies `**/api**` to `**http://127.0.0.1:8000**` and serves the monorepo `**content/**` tree at `**/content**` (see `vite.config.ts`). Run the FastAPI backend on port 8000 for a full API-backed app.

**Optional offline UI:** If `VITE_DEBUG_MODE=true` (e.g. in `.env.development.local`), the app starts [MSW](https://mswjs.io/) (`src/mocks/`), seeds a placeholder token when none is set, and unhandled requests bypass the worker so real APIs still work when reachable.

**Verbose request logging:** If `VITE_VERBOSE_REQUESTS=true`, every axios request/response is logged to the browser console as `[VERBOSE] METHOD /api/endpoint → status` with the JSON body. Useful during development to inspect API payloads.

## Project Structure

```
src/
├── api/              # REST API client modules
│   ├── auth.ts       # Login, status, file-list endpoints
│   ├── categories.ts # Category CRUD + restore/list
│   ├── categoryVoices.ts
│   ├── items.ts
│   ├── itemImages.ts
│   ├── itemVoices.ts
│   └── content.ts    # Aggregates OpenAPI endpoints into UI tree data
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
├── data/
│   └── mockData.ts   # Sidebar / MSW sample categories; toys includes a no-image placeholder item for empty-state testing
├── mocks/            # MSW handlers (used when VITE_DEBUG_MODE=true)
├── layouts/          # Application shell components
│   ├── AppLayout.vue
│   ├── AppNavbar.vue
│   ├── AppSidebar.vue
│   └── AppFooter.vue
├── router/
│   └── index.ts      # Route definitions and auth guard
├── types/
│   └── index.ts      # TypeScript interfaces (Category, Image, Voice, etc.)
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

- **Page header** -- `h3.page-title` with localized category label + `toPascalCaseWords` (navbar content language; no edit control on the title)
- **CategoryDetail card** -- **Category Name** and each **voice-card** word use `ServerWord` (category row: localized + rename modal; voice row: `voice.name` as-is, pencil → **Edit word** modal, `rename-voice` emit). Two-row layout: **Category Icon** (`aspect-ratio: 1 / 1`, `object-fit: contain`) and **Category Voices** share a baseline; voice grid `repeat(auto-fit, minmax(7.5rem, 1fr))`; play + **Upload** (audio); corner **delete** icon opens **Delete or reset voice** confirmation, then `delete-voice` emit; modals mutually exclusive
- **ItemGrid** -- responsive 4-column grid of item thumbnails, each with a pencil edit-icon badge. If an item has no `thumbnailUrl` or file entries, a built-in camera placeholder is shown. Clicking navigates to ItemDetailView.

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
| `CategoryDetail` | `category: Category`                    | `edit`, `delete`, `rename`, `edit-voice`, `rename-voice`, `delete-voice` | Category info card with voice list              |
| `ItemGrid`       | `items: Item[]`, `categoryId: string` | —                                                                        | Thumbnail grid with camera fallback for missing items |
| `LoginForm`      | —                                       | —                                                                        | Login form with auth API integration            |


**ServerWord** — Source: non-empty `text` is shown as-is; otherwise `localizedCategoryLabel(category, content language)`. `pascalCase` runs `toPascalCaseWords` on that raw string. Styling: `rootClass`, `textClass`, `buttonClass`, `iconClass`, `iconStyle`; `textTag` (default `h5`, `mb-0` only for `h5`); `inheritAttrs: false` with class/style on the root merged from `$attrs` + `rootClass`. DOM hooks: `server-word-root`, `server-word-text`, `server-word-edit`, `server-word-icon`.

## API Specification

The full REST API contract is defined in [`backend/src/services/content/openapi.yaml`](../backend/src/services/content/openapi.yaml) (OpenAPI 3.1). It covers every endpoint the
frontend relies on — derived from the MSW handlers (`src/mocks/handlers.ts`), API client modules (`src/api/`), mock
data shapes (`src/data/mockData.ts`), and the UI actions exposed by components.

**Tag groups:** Auth, Categories, Category Voices, Images, Image Files, Image Voices.

## Domain Types

Defined in `src/types/index.ts` (excerpt):

```typescript
type Language = 'en' | 'es' | 'fr' | 'bg'

const SUPPORTED_LANGUAGES: Language[] = ['en', 'es', 'fr', 'bg']

interface Voice {
  id: string
  name: string
  language: Language
  type: string        // e.g. "male", "female", "ambient"
  iconUrl: string
  audioUrl?: string   // if absent/empty, play is disabled for that voice
}

interface Item {
  id: string
  name: string
  files: string[]     // at least 1 file URL
  voices: Voice[]     // exactly 1 per language (4 total)
  thumbnailUrl: string
}

interface Category {
  id: string
  name: string
  description: string
  iconUrl: string
  voices: Voice[]     // category-level voices per language
  items: Item[]
}

interface User {
  name: string
  role: string
  avatarUrl: string
}
```

## Styles

Imported from `src/main.ts` (Star Admin / vue3-demo lineage):

- `assets/vendors/css/vendor.bundle.base.css` -- Bootstrap 4 + Perfect Scrollbar + jVectorMap
- `assets/vendors/css/vendor.bundle.addons.css` -- Additional vendor styles
- `assets/css/shared/style.css` -- Star Admin shared theme
- `assets/css/demo_1/style.css` -- Demo skin overrides (sidebar, navbar, cards)
- `assets/css/auth.css` -- Login page styles
- Icon fonts under `assets/vendors/`: MDI, Font Awesome 4.7, flag-icon-css

