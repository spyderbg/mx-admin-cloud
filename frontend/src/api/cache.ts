import { ref, shallowRef } from 'vue'
import type { ArchivedCategory, CategoryFull, Item, Voice } from '../types/viewModels'
import { LocalizedString } from '../types/viewModels'
import {
  categoriesList,
  categoriesDetails,
  categoriesImagesList,
  categoriesVoicesList,
  itemsImagesList,
  itemsVoicesList,
} from './contentClient'
import type { ItemSummary } from '../types/contentSchemas'

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

/** All categories with items loaded (needed for sidebar search). */
export const sharedCategories = shallowRef<CategoryFull[]>([])

/** id → english name mapping for the shared categories. */
export const sharedCategoryNames = ref(new Map<string, string>())

/** Categories currently sitting in the archive, most recently archived first. */
export const sharedArchivedCategories = shallowRef<ArchivedCategory[]>([])

/** Whether the basic category list is currently loading. */
export const sharedLoading = ref(false)

/** Error message from the last failed fetch, or empty string. */
export const sharedError = ref('')

// ---------------------------------------------------------------------------
// Loaded-state tracking (prevent duplicate fetches across navigations)
// ---------------------------------------------------------------------------

/** Categories whose voices have been loaded (items always loaded in basic). */
const loadedCategoryDetailIds = new Set<string>()
/** Items whose images and voices have been loaded. */
const loadedItemIds = new Map<string, Set<string>>()
/** Categories whose detail (items) fetch failed during the last basic load — retried on next call. */
const failedCategoryIds = new Set<string>()

// ---------------------------------------------------------------------------
// Shared normalizers
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
): Voice[] => {
  const baseId = localizedNames['en'] ?? Object.values(localizedNames)[0] ?? 'voice'
  const nameFor = (lang: string): string =>
    localizedNames[lang] ?? localizedNames['en'] ?? baseId.replaceAll('_', ' ')

  if (!Array.isArray(raw)) return []

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
  return parsed
}

// ---------------------------------------------------------------------------
// Basic category loader
// 1 list request + N parallel detail requests (items needed for sidebar search)
// ---------------------------------------------------------------------------

async function _loadCategoriesBasic(): Promise<CategoryFull[]> {
  try {
    const list = await categoriesList({ includeArchived: true })
    // Assigned before the empty-list guard below, so an installation whose every
    // category is archived still shows its archive.
    sharedArchivedCategories.value = (list.archived ?? []).map((entry) => ({
      baseName: entry.base_name,
      archiveFolder: entry.archive_folder,
      archivedAt: entry.archived_at,
    }))

    if (!list.categories.length) {
      sharedCategories.value = []
      sharedCategoryNames.value = new Map()
      failedCategoryIds.clear()
      return []
    }

    // Build shells from the list response for immediate reference
    const shells: CategoryFull[] = list.categories.map((summary) => {
      const nameDict = summary.name as unknown as Record<string, string>
      return {
        id: summary.id,
        name: new LocalizedString(nameDict),
        description: new LocalizedString({}),
        thumbnail_uri: summary.thumbnail_uri,
        items_count: summary.items_count,
        images: [],
        voices: [],
        items: [],
      }
    })

    // Fetch all category details in parallel to populate items for sidebar search
    const detailResults = await Promise.allSettled(
      shells.map((cat) =>
        withInflight(`detail:${cat.id}`, () => categoriesDetails(cat.id))
      ),
    )

    failedCategoryIds.clear()
    const categories: CategoryFull[] = shells.map((cat, i) => {
      const result = detailResults[i]
      if (result.status !== 'fulfilled') {
        failedCategoryIds.add(cat.id)
        return cat
      }
      const detail = result.value
      const items: Item[] = (detail.items ?? []).map((itemSummary: ItemSummary) => {
        const itemNameDict = itemSummary.name as unknown as Record<string, string>
        return {
          id: itemSummary.id,
          name: itemNameDict['en'] ?? Object.values(itemNameDict)[0] ?? itemSummary.id,
          images: [],
          voices: [],
          thumbnailUri: itemSummary.thumbnail_uri ?? '',
          imagesCount: itemSummary.images_count ?? 0,
        }
      })
      const descriptionDict = detail.description as unknown as Record<string, string> | undefined
      return { ...cat, items, description: new LocalizedString(descriptionDict ?? {}) }
    })

    const nameMap = new Map<string, string>()
    for (const c of categories) nameMap.set(c.id, c.name.en)

    sharedCategories.value = categories
    sharedCategoryNames.value = nameMap

    if (failedCategoryIds.size > 0) {
      sharedError.value = `${failedCategoryIds.size} categor${failedCategoryIds.size === 1 ? 'y' : 'ies'} failed to load completely. Try again.`
    }

    return categories
  } catch (err) {
    sharedError.value = 'Failed to load categories.'
    console.error('Failed to load categories:', err)
    return []
  } finally {
    sharedLoading.value = false
  }
}

/** Clear the full category cache (list + items + tracking state). */
export const invalidateCategoriesCache = (): void => {
  sharedCategories.value = []
  sharedCategoryNames.value = new Map()
  sharedArchivedCategories.value = []
  loadedCategoryDetailIds.clear()
  loadedItemIds.clear()
  failedCategoryIds.clear()
}

/**
 * Load the basic category list with items for all categories.
 * Used by DashboardView. 1 list request + N parallel detail requests.
 */
export const ensureCategoriesLoaded = async (): Promise<CategoryFull[]> => {
  if (sharedCategories.value.length > 0 && failedCategoryIds.size === 0) {
    return sharedCategories.value
  }

  sharedLoading.value = true
  sharedError.value = ''

  return withInflight('basic:all', _loadCategoriesBasic)
}

// ---------------------------------------------------------------------------
// Category voice loader — for CategoriesView
// 1 request: GET /content/categories/{category_id}/voices
// (items already loaded during ensureCategoriesLoaded)
// ---------------------------------------------------------------------------

/**
 * Load voices for a category. Items are guaranteed to exist from the basic load.
 * Skips if already loaded. Used by CategoriesView.
 */
export const ensureCategoryDetail = async (
  categoryId: string,
  categoryName: string,
): Promise<CategoryFull | null> => {
  if (loadedCategoryDetailIds.has(categoryName)) {
    return sharedCategories.value.find((c) => c.name.en === categoryName) ?? null
  }

  try {
    const [voicesResponse, imagesResponse] = await Promise.all([
      withInflight(`cat-voices:${categoryId}`, () => categoriesVoicesList(categoryId)),
      withInflight(`cat-images:${categoryId}`, () => categoriesImagesList(categoryId)),
    ])

    const voices = normalizeVoices(
      voicesResponse.voices,
      (voicesResponse.name as unknown as Record<string, string>) ?? {},
    )
    const thumbnail_uri = imagesResponse.images?.[0]?.file_uri
      ?? sharedCategories.value.find((c) => c.name.en === categoryName)?.thumbnail_uri

    const catIdx = sharedCategories.value.findIndex((c) => c.name.en === categoryName)
    if (catIdx !== -1) {
      const arr = [...sharedCategories.value]
      arr[catIdx] = { ...arr[catIdx], voices, thumbnail_uri }
      sharedCategories.value = arr
    }

    loadedCategoryDetailIds.add(categoryName)
    return sharedCategories.value.find((c) => c.name.en === categoryName) ?? null
  } catch (err) {
    sharedError.value = 'Failed to load category details.'
    console.error('Failed to load category details:', err)
    // Not marked as loaded — the next call will retry. Return whatever shell/partial
    // data already exists rather than null, so the view doesn't think it's missing.
    return sharedCategories.value.find((c) => c.name.en === categoryName) ?? null
  }
}

/** Clear the voice cache for a single category so the next ensureCategoryDetail re-fetches. */
export const invalidateCategoryDetail = (categoryName: string): void => {
  loadedCategoryDetailIds.delete(categoryName)
}

// ---------------------------------------------------------------------------
// Item detail loader — for ItemDetailView
// 2 requests: GET …/items/{id}/images + GET …/items/{id}/voices
// ---------------------------------------------------------------------------

/**
 * Load images and voices for a single item.
 * Skips if already loaded. Used by ItemDetailView.
 */
export const ensureItemDetail = async (
  categoryId: string,
  itemId: string,
): Promise<Item | null> => {
  if (loadedItemIds.get(categoryId)?.has(itemId)) {
    return (
      sharedCategories.value.find((c) => c.id === categoryId)?.items.find((i) => i.id === itemId) ?? null
    )
  }

  try {
    const [imagesResponse, voicesResponse] = await Promise.all([
      withInflight(`images:${categoryId}:${itemId}`, () => itemsImagesList(categoryId, itemId)),
      withInflight(`voices:${categoryId}:${itemId}`, () => itemsVoicesList(categoryId, itemId)),
    ])

    const images = normalizeImages(
      (imagesResponse.images ?? []).map((img) => img.file_uri ?? ''),
      [],
    )
    const itemNamesDict = (voicesResponse.name as unknown as Record<string, string>) ?? {}
    const voices = normalizeVoices(voicesResponse.voices, itemNamesDict)

    const catIdx = sharedCategories.value.findIndex((c) => c.id === categoryId)
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
          imagesCount: images.length,
        }
        cat.items = items
        arr[catIdx] = cat
        sharedCategories.value = arr
      }
    }

    if (!loadedItemIds.has(categoryId)) loadedItemIds.set(categoryId, new Set())
    loadedItemIds.get(categoryId)!.add(itemId)

    return (
      sharedCategories.value.find((c) => c.id === categoryId)?.items.find((i) => i.id === itemId) ?? null
    )
  } catch (err) {
    sharedError.value = 'Failed to load item details.'
    console.error('Failed to load item details:', err)
    // Not marked as loaded — the next call will retry.
    return (
      sharedCategories.value.find((c) => c.id === categoryId)?.items.find((i) => i.id === itemId) ?? null
    )
  }
}

/** Clear the detail cache for a single item so the next ensureItemDetail re-fetches. */
export const invalidateItemDetail = (categoryId: string, itemId: string): void => {
  loadedItemIds.get(categoryId)?.delete(itemId)
}
