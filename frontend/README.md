# KiddoWords Content Management - Frontend

Vue 3 + TypeScript + Vite application for managing image categories, their voices, and associated image assets.

## Quick Start

```bash
npm install
npm run dev         # dev server at http://localhost:5173
npm run build       # production build with type-checking
npm run type-check  # type-check only (no build)
```

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
├── data/             # Mock data (replaced by API later)
│   └── mockData.ts
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
│   └── DashboardView.vue
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

- **AppNavbar** -- Brand logo, language selector (en/es/fr/bg with flags), user avatar dropdown with sign-out.
- **AppSidebar** -- User profile (avatar, name, role), fuzzy-filter input, expandable category tree with child images. Clicking a category navigates to its detail; clicking an image navigates to its detail page.
- **AppFooter** -- Simple branded footer.

## Views

### LoginView

Full-page authentication screen (Star Admin auth theme). Uses `LoginForm` component with username/password fields, "Keep me signed in", and Google login placeholder.

### CategoriesView

Displayed when a category is selected (or defaults to first category):

- **Page header** with category name
- **CategoryDetail card** -- category icon, name, description, Edit/Delete buttons, voice list per language with Edit/Delete per voice
- **ImageGrid** -- responsive 4-column grid of image thumbnails, each with a pencil edit-icon badge. Clicking navigates to ImageDetailView.

### ImageDetailView

Displayed for a specific image within a category:

- **Breadcrumb** navigation: Categories > Category Name > Image Name
- **Image name** editable input
- **Files section** -- grid of the image's file variants with replace/delete actions. At least one file must remain (delete disabled when only one).
- **Voices table** -- one row per supported language (en, es, fr, bg) showing flag, voice name, type badge, and replace/upload button.

## Components

| Component | Props | Emits | Purpose |
|-----------|-------|-------|---------|
| `CategoryDetail` | `category: Category` | `edit`, `delete`, `edit-voice`, `delete-voice` | Category info card with voice list |
| `ImageGrid` | `images: Image[]`, `categoryId: string` | — | Responsive thumbnail grid with edit badges |
| `LoginForm` | — | — | Login form with auth API integration |

## Domain Types

```typescript
type Language = 'en' | 'es' | 'fr' | 'bg'

interface Voice {
  id: string
  name: string
  language: Language
  type: string        // "male", "female", "ambient"
  iconUrl: string
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
```

## Styles

Uses the Star Admin Bootstrap template (from vue3-demo):

- `vendor.bundle.base.css` -- Bootstrap 4 + Perfect Scrollbar + jVectorMap
- `vendor.bundle.addons.css` -- Additional vendor styles
- `shared/style.css` -- Star Admin shared theme (~10K lines)
- `demo_1/style.css` -- Demo skin overrides (sidebar gradient, navbar, cards)
- `auth.css` -- Custom login page styles
- Icon fonts: Material Design Icons, Font Awesome 4.7, flag-icon-css
