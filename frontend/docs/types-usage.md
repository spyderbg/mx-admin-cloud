# Type Usage Reference

Interfaces and types defined in `src/types/index.ts` and their usage across the frontend project.

---

## `Language` type

```ts
type Language = 'en' | 'es' | 'fr' | 'bg'
```

| File | How |
|---|---|
| `src/types/index.ts` | Exported as `SUPPORTED_LANGUAGES: Language[]` |
| `src/composables/useContentLanguage.ts` | `Ref<Language>` -- core type of the content-language composable; read from and persisted to `localStorage` |
| `src/api/content.ts` | `DEFAULT_LANGUAGES: Language[]` used in `fallbackVoice()` and `normalizeVoices()` to filter valid language values |
| `src/utils/localizedLabels.ts` | `labelFromVoices()` picks a `Voice` by `Language` code; `breadcrumbCategoriesLabel()` maps `Language` to translated breadcrumb strings |
| `src/mocks/handlers.ts` | `(['en', 'es', 'fr', 'bg'] as const)` for building `images_by_lang` in category detail API responses |

---

## `Voice` interface

```ts
interface Voice {
  id: string
  name: string
  language: Language
  type: string
  iconUrl: string
  audioUrl?: string  // absent/empty = missing voice, play disabled
}
```

| File | How |
|---|---|
| `src/data/mockData.ts` | Each `Category` and `Item` has a `voices: Voice[]` array (one per supported language) |
| `src/api/content.ts` | `normalizeVoices()` parses raw API responses into `Voice[]` with fallback generation |
| `src/mocks/handlers.ts` | `toVoiceResponse()` serializes `Voice` to API shape (`audio_url` snake_case); handlers for voice CRUD clear `.audioUrl` to disable play |
| `src/utils/localizedLabels.ts` | `labelFromVoices()` searches `Voice[]` by `.language` to get a localized display name |

---

## `Item` interface

```ts
interface Item {
  id: string
  name: string
  files: string[]
  voices: Voice[]
  thumbnailUrl: string
}
```

| File | How |
|---|---|
| `src/data/mockData.ts` | `mockCategories` contains `Item` entries (e.g. "Coffee", "Apple Pie") with files and voices |
| `src/api/categories.ts` | `getCategoryDetail()` maps `images_ids` to `imagesIds: string[]` (item names) |
| `src/api/content.ts` | `loadCategoriesTree()` constructs `Item` objects from API + mock fallbacks; `normalizeFiles()` populates `.files`; `.thumbnailUrl` defaults to `files[0]` |
| `src/mocks/handlers.ts` | `findItem()` by id/name; CRUD on `item.files`, `item.thumbnailUrl`, `item.name`, `item.id` |
| `src/utils/localizedLabels.ts` | `localizedImageLabel()` takes an `Item` to get a localized display name |

---

## `Category` interface

```ts
interface Category {
  id: string          // UI-only
  name: string        // backend identifier
  description: string
  iconUrl: string     // UI-only
  archived?: boolean
  voices: Voice[]
  items: Item[]
}
```

| File | How |
|---|---|
| `src/data/mockData.ts` | `mockCategories: Category[]` -- the primary mock data source (Drinks, Foods, Vegetables, Toys) |
| `src/api/content.ts` | `loadCategoriesTree()` returns `Promise<Category[]>`; builds `Category` from `CategoryDetail` + `listItems` + mock fallbacks |
| `src/mocks/handlers.ts` | `findCategory()` locates `Category` by name; CRUD creates, renames, archives, restores entries |
| `src/utils/localizedLabels.ts` | `localizedCategoryLabel()` takes a `Category` to get a localized name |

---

## `User` interface

```ts
interface User {
  name: string
  role: string
  avatarUrl: string
}
```

| File | How |
|---|---|
| `src/data/mockData.ts` | `mockUser: User` -- "Allen Moreno, Administrator" with dynamically loaded face image |
| `src/layouts/AppNavbar.vue` | Imports `mockUser` to populate navbar user display |
| `src/mocks/handlers.ts` | Login handler returns `{ access_token: 'debug-token' }` (no `User` payload) |

---

## `ArchivedEntry` interface

```ts
interface ArchivedEntry {
  baseName: string
  archiveFolder: string
  archivedAt: number
}
```

| File | How |
|---|---|
| `src/types/index.ts` | Referenced as `archived?: ArchivedEntry[] \| null` inside `CategorySummary` |
| `src/mocks/handlers.ts` | Local `ArchivedEntry` interface (different field naming: `base_name`, `archive_folder`, `archived_at`) used for in-memory archive store; pushed on category deletion, removed on restore |
| `src/views/CategoriesView.vue` | Uses `archived` from `CategorySummary` to display archived categories |

---

## `CategorySummary` interface

```ts
interface CategorySummary {
  categories: string[]
  archived?: ArchivedEntry[] | null
  error?: string | null
}
```

| File | How |
|---|---|
| `src/api/categories.ts` | `listCategories()` returns `Promise<CategorySummary>` from `GET /content/categories` |
| `src/api/content.ts` | `loadCategoriesTree()` calls `listCategories()`; checks `list.categories.length` to early-return |
| `src/mocks/handlers.ts` | `GET /content/categories` handler returns `{ categories, archived, error }` shape matching this interface |

---

## `CategoryDetail` interface

```ts
interface CategoryDetail {
  name: string
  items: string[]
  imagesIds?: string[] | null
  imagesByLanguage?: Partial<Record<Language, string[]>>
}
```

| File | How |
|---|---|
| `src/api/categories.ts` | `getCategoryDetail()` returns `Promise<CategoryDetail>`; maps `images_by_lang` to `imagesByLanguage` keyed by `Language` |
| `src/api/content.ts` | `getCategoryDetail(categoryName)` called per category to get `detail.name` and `detail.items` (item names) |
| `src/mocks/handlers.ts` | `toCategoryDetailResponse()` builds this shape from `Category`/`Item` mock data |

---

## Data flow overview

1. `listCategories()` returns `CategorySummary` -- list of category names + archived entries
2. `getCategoryDetail(name)` returns `CategoryDetail` -- items and language-mapped labels per category
3. `loadCategoriesTree()` in `api/content.ts` combines both APIs with mock fallbacks to produce `Category[]` (each with `Item[]` and `Voice[]`)
4. Mock data (`mockData.ts`) provides `mockCategories: Category[]` and `mockUser: User` as static fallbacks
5. MSW handlers (`mocks/handlers.ts`) intercept API calls and serve mock data typed via `Category` and `Voice`
6. Composables (`useContentLanguage.ts`) use `Language` as the core type, stored in `localStorage`
7. Utilities (`localizedLabels.ts`) use `Category` and `Item` with `Language` to compute localized display names
