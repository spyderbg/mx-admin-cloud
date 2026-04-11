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

The dev server proxies **`/api`** to **`http://127.0.0.1:8000`** and serves the monorepo **`content/`** tree at **`/content`** (see `vite.config.ts`). Run the FastAPI backend on port 8000 for a full API-backed app.

**Optional offline UI:** If `VITE_DEBUG_MODE=true` (e.g. in `.env.development.local`), the app starts [MSW](https://mswjs.io/) (`src/mocks/`), seeds a placeholder token when none is set, and unhandled requests bypass the worker so real APIs still work when reachable.

## Project Structure

```
src/
├── api/              # REST API client modules
│   └── auth.ts       # Login, status, file-list endpoints
├── assets/           # Static assets (copied from vue3-demo)
│   ├── css/          # Shared + demo_1 Star Admin stylesheets
│   ├── images/       # Faces, logos, samples, auth backgrounds
│   └── vendors/      # Bootstrap, MDI, Font Awesome, flag-icons, etc.
├── components/       # Reusable UI components
│   ├── CategoryDetail.vue
│   ├── ImageGrid.vue
│   └── LoginForm.vue
├── composables/      # Shared composition helpers (e.g. content UI language)
├── utils/            # Pure helpers (labels from voices, title-case formatting, etc.)
├── data/
│   └── mockData.ts   # Sidebar / MSW sample categories aligned with repo ../content (drinks, foods, vegetables, toys)
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
│   ├── ImageDetailView.vue
│   ├── LoginView.vue
│   └── DashboardView.vue   # present but not registered in the router
├── App.vue           # Root component (bare router-view)
├── env.d.ts          # Vite/Vue/SVG type declarations
└── main.ts           # App entry point and CSS imports
```

## Routes

| Path | View | Layout | Description |
|------|------|--------|-------------|
| `/login` | `LoginView` | None (full-page) | Authentication screen |
| `/` | — | — | Redirects to `/categories` |
| `/categories` | `CategoriesView` | `AppLayout` | Shows first category by default |
| `/categories/:categoryId` | `CategoriesView` | `AppLayout` | Selected category details + image grid |
| `/categories/:categoryId/images/:imageId` | `ImageDetailView` | `AppLayout` | Image detail: files, voices, name editing |

All `/categories` routes require authentication (token in localStorage). Unauthenticated users are redirected to `/login`.

## Layouts

### AppLayout

The main authenticated shell, wrapping all `/categories` routes:

```
┌──────────────────────────────────────────────┐
│  AppNavbar (fixed top)                       │
├──────────┬───────────────────────────────────┤
│          │                                   │
│ AppSidebar│  <router-view />                 │
│ (270px)  │  (CategoriesView or              │
│          │   ImageDetailView)                │
│          │                                   │
│          ├───────────────────────────────────┤
│          │  AppFooter                        │
└──────────┴───────────────────────────────────┘
```

- **AppNavbar** -- Brand logo; **content language** selector (en/es/fr/bg with flags) and user avatar / sign-out are grouped on the **right** of the bar (`navbar-menu-wrapper` uses remaining width with end alignment; both controls stay visible at all breakpoints). Scoped styles in `AppNavbar.vue` fix vendor + Bootstrap rules that otherwise stretch language and user dropdowns to the full `navbar-menu-wrapper` width under narrow layouts, and anchor menus under their toggles (`top: 100%`, `right: 0`), overriding the theme language rule `left: -15px !important`. Opening one header dropdown closes the other. On small viewports, opening either dropdown closes the off-canvas category sidebar, and opening the sidebar closes both dropdowns. At **max-width 991px**, the logo / mini-logo area toggles the off-canvas categories drawer (home links apply at wider widths; there is no separate header menu button). The drawer slides in from the **left** with a short eased transition (see `AppSidebar.vue` scoped overrides). The selected language is shared with the sidebar (via `AppLayout` provide/inject), persisted in `localStorage` under `mx-admin-content-language`, and controls which `Voice.name` is shown for each category and image in the tree. There is no global header search bar; use the sidebar filter on category routes.
- **AppSidebar** -- User profile (avatar, name, role), fuzzy-filter input, expandable category tree with child image labels in the **navbar-selected language** (`Voice.name` for that language, else **English** `Voice.name`, else the stored category/image `name`). Choosing a category or image emits `close-sidebar` so the off-canvas drawer closes on narrow viewports. Next to the **Categories** heading, **Expand all** / **Collapse all** toggle every top-level category open or closed. Clicking a category navigates to its detail; clicking an image navigates to its detail page.
- **AppFooter** -- Simple branded footer.

## Views

### LoginView

Full-page authentication screen (Star Admin auth theme). Uses `LoginForm` component with username/password fields, "Keep me signed in", and Google login placeholder.

### CategoriesView

Displayed when a category is selected (or defaults to first category):

- **Page header** with category name
- **CategoryDetail card** -- category icon, name, description, Edit/Delete buttons, voice list per language with Edit/Delete per voice; rename / delete / upload / voice-upload modals close each other so only one overlay is open at a time
- **ImageGrid** -- responsive 4-column grid of image thumbnails, each with a pencil edit-icon badge. Clicking navigates to ImageDetailView.

### ImageDetailView

Displayed for a specific image within a category:

- **Breadcrumb** navigation: root + category + image labels follow the **navbar content language** (same as the sidebar). Category and image segments use the matching `Voice.name`, then the **English** voice if that language is missing, then the stored `name`. The root crumb is translated for en/es/fr/bg (unknown codes fall back to English).
- **Page title** (`h3.page-title`) uses the same localized image label as the last breadcrumb segment, formatted with **per-word Pascal-style casing** (first letter of each word uppercased, the rest lowercased via `toLocaleUpperCase` / `toLocaleLowerCase`).
- **Image name** editable input
- **Files section** -- grid of the image's file variants with replace/delete actions. At least one file must remain (delete disabled when only one). **Add File** opens the system file picker (`image/*`); selection is logged until upload is wired to the API.
- **Voices table** -- one row per supported language (en, es, fr, bg) showing flag, voice name (text only), type badge, and replace/upload button.

## Components

| Component | Props | Emits | Purpose |
|-----------|-------|-------|---------|
| `CategoryDetail` | `category: Category` | `edit`, `delete`, `edit-voice`, `delete-voice` | Category info card with voice list |
| `ImageGrid` | `images: Image[]`, `categoryId: string` | — | Responsive thumbnail grid with edit badges |
| `LoginForm` | — | — | Login form with auth API integration |

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

interface Image {
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
  images: Image[]
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
