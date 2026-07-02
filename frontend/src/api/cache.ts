import { ref, shallowRef } from 'vue'
import type { CategoryFull, Item, Voice } from '../types/viewModels'
import { LocalizedString, SUPPORTED_LANGUAGES } from '../types/viewModels'
import {
  categoriesList,
  categoriesDetails,
  categoriesVoicesList,
  itemsImagesList,
  itemsVoicesList,
} from './contentClient'
import type { ItemImagesResponse, ItemSummary, ItemVoicesResponse } from '../types/contentSchemas'

// ---------------------------------------------------------------------------
// In-flight promise deduplication
// ---------------------------------------------------------------------------

const inflightPromises = new Map<string, Promise<unknown>>()

function withInflight<T>(
  key: string,
  fetcher: () => Promise<T>,
): Promise<T> {
  if (inflightPromises.has(key)) {
    return inflightPromises.get(key) as Promise<T>
  }

  const promise = fetcher().finally(() => {
    inflightPromises.delete(key)
  })

  inflightPromises.set(key, promise)
  return promise
}

// ---------------------------------------------------------------------------
// Shared reactive state (visible to all views)
// ---------------------------------------------------------------------------

/** All categories loaded at the "basic" level (names + thumbnail + count). */
export const sharedCategories = shallowRef<CategoryFull[]>([])

/** id → english name mapping for the shared categories. */
export const sharedCategoryNames = ref(new Map<string, string>())

/** Whether the basic category list is currently loading. */
export const sharedLoading = ref(false)

/** Error message from the last failed fetch, or empty string. */
export const sharedError = ref('')

// ---------------------------------------------------------------------------
// Loaded-state tracking (prevent duplicate fetches across navigations)
// ---------------------------------------------------------------------------

const loadedCategoryDetailIds = new Set<string>()
const loadedItemIds = new Map<string, Set<string>>()

// ---------------------------------------------------------------------------
// Shared normalizers (used by ensureCategoryDetail + ensureItemDetail)
// ---------------------------------------------------------------------------

const normalizeImages = (raw: unknown, fallback: string[]): string[] => {
  if (Array.isArray(raw)) {
    const images = raw.filter(
      (entry): entry is string => typeof entry === 'string' && entry.trim().length > 0,
    )
    if (images.length > 0) return images
  }
  return fallback
}

const normalizeVoices = (
  raw: unknown,
  localizedNames: Record<string, string>,
  fallback?: Voice[],
): Voice[] => {
  const baseId = localizedNames['en'] ?? Object.values(localizedNames)[0] ?? 'voice'
  const nameFor = (lang: string): string =>
    localizedNames[lang] ?? localizedNames['en'] ?? baseId.replaceAll('_', ' ')

  if (Array.isArray(raw) && raw.length > 0) {
    const parsed: Voice[] = []
    raw.forEach((entry, index) => {
      if (!entry || typeof entry !== 'object') return
      const voice = entry as Record<string, unknown>
      const language = voice.language
      if (
        language !== 'en' &&
        language !== 'es' &&
        language !== 'fr' &&
        language !== 'bg'
      )
        return
      parsed.push({
        id:
          typeof voice.id === 'string'
            ? voice.id
            : `${baseId}-${language}-${index}`,
        name: nameFor(language as string),
        language,
        type: typeof voice.type === 'string' ? voice.type : 'male',
        fileUri: typeof voice.file_uri === 'string' ? voice.file_uri : '',
      })
    })
    if (parsed.length > 0) return parsed
  }
  if (fallback && fallback.length > 0) return fallback
  return SUPPORTED_LANGUAGES.map((lang) => ({
    id: `${baseId}-${lang}`,
    name: nameFor(lang),
    language: lang,
    type: lang === 'en' || lang === 'fr' ? 'male' : 'female',
    fileUri: '',
  }))
}

// ---------------------------------------------------------------------------
// Basic category loader — one request: GET /content/categories
// ---------------------------------------------------------------------------

async function _loadCategoriesBasic(): Promise<CategoryFull[]> {
  const list = await categoriesList()
  if (!list.categories.length) {
    sharedCategories.value = []
    sharedCategoryNames.value = new Map()
    sharedLoading.value = false
    return []
  }

  const categories: CategoryFull[] = list.categories.map((summary) => {
    const nameDict = summary.name as unknown as Record<string, string>
    return {
      id: summary.id,
      name: new LocalizedString(nameDict),
      description: '',
      thumbnail_uri: summary.thumbnail_uri,
      items_count: summary.items_count,
      images: [],
      voices: [],
      items: [],
    }
  })

  const nameMap = new Map<string, string>()
  for (const c of categories) nameMap.set(c.id, c.name.en)

  sharedCategories.value = categories
  sharedCategoryNames.value = nameMap
  sharedLoading.value = false
  return categories
}

/** Clear the category cache so the next ensureCategoriesLoaded call re-fetches. */
export const invalidateCategoriesCache = (): void => {
  sharedCategories.value = []
  sharedCategoryNames.value = new Map()
  loadedCategoryDetailIds.clear()
  loadedItemIds.clear()
}

/**
 * Load the basic category list (names, thumbnails, item counts).
 * Used by DashboardView. One request: GET /content/categories.
 */
export const ensureCategoriesLoaded = async (): Promise<CategoryFull[]> => {
  if (sharedCategories.value.length > 0) {
    return sharedCategories.value
  }

  sharedLoading.value = true
  sharedError.value = ''

  return withInflight('basic:all', _loadCategoriesBasic)
}

// ---------------------------------------------------------------------------
// Category detail loader — for CategoriesView
// Two requests: GET /content/categories/{name} + GET /content/categories/{name}/voices
// ---------------------------------------------------------------------------

/**
 * Load full category data (items list + voices) for a category by English name.
 * Skips if already loaded. Used by CategoriesView.
 */
export const ensureCategoryDetail = async (categoryName: string): Promise<CategoryFull | null> => {
  if (loadedCategoryDetailIds.has(categoryName)) {
    return sharedCategories.value.find((c) => c.name.en === categoryName) ?? null
  }

  const [detail, voicesResponse] = await Promise.all([
    withInflight(`detail:${categoryName}`, () => categoriesDetails(categoryName)),
    withInflight(
      `cat-voices:${categoryName}`,
      () => categoriesVoicesList(categoryName).catch(() => ({ category_id: '', name: {} as LocalizedString, voices: [] })),
    ),
  ])

  const nameDict = detail.name as unknown as Record<string, string>
  const items: Item[] = (detail.items ?? []).map((itemSummary: ItemSummary) => {
    const itemNameDict = itemSummary.name as unknown as Record<string, string>
    return {
      id: itemSummary.id,
      name: itemNameDict['en'] ?? Object.values(itemNameDict)[0] ?? itemSummary.id,
      images: [],
      voices: [],
      thumbnailUri: itemSummary.thumbnail_uri ?? '',
    }
  })

  // CategoryDetailsResponse has no thumbnail_uri — preserve from basic load
  const existingCat = sharedCategories.value.find((c) => c.id.toLowerCase() === detail.id.toLowerCase())

  const fullCategory: CategoryFull = {
    id: detail.id,
    name: new LocalizedString(nameDict),
    description: detail.description ?? '',
    thumbnail_uri: existingCat?.thumbnail_uri,
    items_count: existingCat?.items_count ?? items.length,
    images: [],
    voices: normalizeVoices(voicesResponse.voices, (voicesResponse.name as unknown as Record<string, string>) ?? {}),
    items,
  }

  const idx = sharedCategories.value.findIndex((c) => c.id.toLowerCase() === fullCategory.id.toLowerCase())
  if (idx !== -1) {
    const arr = [...sharedCategories.value]
    arr[idx] = fullCategory
    sharedCategories.value = arr
  } else {
    sharedCategories.value = [...sharedCategories.value, fullCategory]
  }

  loadedCategoryDetailIds.add(categoryName)
  return fullCategory
}

/** Clear the detail cache for a single category so the next ensureCategoryDetail re-fetches. */
export const invalidateCategoryDetail = (categoryName: string): void => {
  loadedCategoryDetailIds.delete(categoryName)
}

// ---------------------------------------------------------------------------
// Item detail loader — for ItemDetailView
// Two requests: GET …/items/{id}/images + GET …/items/{id}/voices
// ---------------------------------------------------------------------------

/**
 * Load images and voices for a single item by ID.
 * Skips if already loaded. Used by ItemDetailView.
 */
export const ensureItemDetail = async (
  categoryName: string,
  itemId: string,
): Promise<Item | null> => {
  if (loadedItemIds.get(categoryName)?.has(itemId)) {
    return (
      sharedCategories.value.find((c) => c.name.en === categoryName)?.items.find((i) => i.id === itemId) ?? null
    )
  }

  const [imagesResponse, voicesResponse] = await Promise.all([
    withInflight(
      `images:${categoryName}:${itemId}`,
      () => itemsImagesList(categoryName, itemId).catch(() => ({}) as ItemImagesResponse),
    ),
    withInflight(
      `voices:${categoryName}:${itemId}`,
      () => itemsVoicesList(categoryName, itemId).catch(() => ({}) as ItemVoicesResponse),
    ),
  ])

  const images = normalizeImages(
    (imagesResponse.images ?? []).map((img) => img.file_uri ?? ''),
    [],
  )
  const itemNamesDict = (voicesResponse.name as unknown as Record<string, string>) ?? {}
  const voices = normalizeVoices(voicesResponse.voices, itemNamesDict)

  const catIdx = sharedCategories.value.findIndex((c) => c.name.en === categoryName)
  if (catIdx !== -1) {
    const arr = [...sharedCategories.value]
    const cat = { ...arr[catIdx] }
    const itemIdx = cat.items.findIndex((i) => i.id === itemId)
    if (itemIdx !== -1) {
      const items = [...cat.items]
      items[itemIdx] = {
        ...items[itemIdx],
        images,
        voices,
        thumbnailUri: images[0] ?? items[itemIdx].thumbnailUri,
      }
      cat.items = items
      arr[catIdx] = cat
      sharedCategories.value = arr
    }
  }

  if (!loadedItemIds.has(categoryName)) loadedItemIds.set(categoryName, new Set())
  loadedItemIds.get(categoryName)!.add(itemId)

  return (
    sharedCategories.value.find((c) => c.name.en === categoryName)?.items.find((i) => i.id === itemId) ?? null
  )
}

/** Clear the detail cache for a single item so the next ensureItemDetail re-fetches. */
export const invalidateItemDetail = (categoryName: string, itemId: string): void => {
  loadedItemIds.get(categoryName)?.delete(itemId)
}
