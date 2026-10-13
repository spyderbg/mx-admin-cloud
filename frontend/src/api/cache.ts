import { ref, shallowRef } from 'vue'
import type { AxiosError } from 'axios'
import type { ArchivedCategory, CategoryFull, ImageMetadata, Item, Voice } from '../types/viewModels'
import { LocalizedString } from '../types/viewModels'
import {
  categoriesList,
  categoriesDetails,
  categoriesImagesList,
  categoriesVoicesList,
  itemsImagesList,
  itemsVoicesList,
} from './contentClient'
import type { ImageResponse, ItemImagesResponse, ItemSummary } from '../types/contentSchemas'

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

/**
 * Per-category cache-busting counter for the icon file, keyed by category id.
 * Icon uploads/deletes keep the same filename, so views rendering the icon (dashboard
 * cards, category detail) need this to force a fresh request instead of a stale browser
 * cache — bump it via bumpCategoryIconVersion() whenever the icon file actually changes.
 */
export const categoryIconVersions = ref(new Map<string, number>())

export const bumpCategoryIconVersion = (categoryId: string): void => {
  const next = new Map(categoryIconVersions.value)
  next.set(categoryId, (next.get(categoryId) ?? 0) + 1)
  categoryIconVersions.value = next
}

/**
 * Per-item cache key for the main image shown in both item details and item grids.
 * Selecting a different variant rewrites the main image under the same filename.
 */
const ITEM_THUMBNAIL_VERSIONS_KEY = 'mx-admin:item-thumbnail-versions'

const readItemThumbnailVersions = (): Map<string, number> => {
  try {
    const stored = localStorage.getItem(ITEM_THUMBNAIL_VERSIONS_KEY)
    if (!stored) return new Map()
    const entries: unknown = JSON.parse(stored)
    if (!Array.isArray(entries)) return new Map()
    return new Map(entries.filter((entry): entry is [string, number] =>
      Array.isArray(entry)
      && typeof entry[0] === 'string'
      && typeof entry[1] === 'number'
      && Number.isFinite(entry[1]),
    ))
  } catch {
    return new Map()
  }
}

export const itemThumbnailVersions = ref(readItemThumbnailVersions())

const itemThumbnailVersionKey = (categoryId: string, itemId: string): string =>
  `${categoryId}:${itemId}`

export const bumpItemThumbnailVersion = (categoryId: string, itemId: string): void => {
  const key = itemThumbnailVersionKey(categoryId, itemId)
  const next = new Map(itemThumbnailVersions.value)
  // A fresh timestamp also avoids reusing a cached ?v=1 URL after a page reload.
  next.set(key, Math.max(Date.now(), (next.get(key) ?? 0) + 1))
  itemThumbnailVersions.value = next
  try {
    localStorage.setItem(ITEM_THUMBNAIL_VERSIONS_KEY, JSON.stringify([...next]))
  } catch {
    // The in-memory version still refreshes both views when storage is unavailable.
  }
}

/** Whether the basic category list is currently loading. */
export const sharedLoading = ref(false)

/** Error message from the last failed fetch, or empty string. */
export const sharedError = ref('')

/** Whether the category list last answered 503: no content provider is active, so nothing is cached. */
export const sharedContentUnavailable = ref(false)

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

/** How each item image was made, keyed by image id. */
const normalizeImageMetadata = (images: ImageResponse[] | undefined): Record<string, ImageMetadata> =>
  Object.fromEntries((images ?? []).map((image) => [image.id, {
    provider: image.provider || 'unknown',
    parameters: image.parameters && typeof image.parameters === 'object' ? image.parameters : {},
  }]))

/** Preserve an explicitly returned null thumbnail; only use a fallback when the
 * response predates the thumbnail field entirely. */
const itemThumbnailUri = (
  thumbnailUri: string | null | undefined,
  images: string[],
  fallback: string,
): string => {
  if (thumbnailUri !== undefined) return thumbnailUri ?? ''
  return images[0] ?? fallback
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
      isSelected: typeof voice.is_selected === 'boolean' ? voice.is_selected : undefined,
      selectedVersion: voice.selected_version === 'optimized' ? 'optimized' : 'original',
      optimizedFileUri: typeof voice.optimized_file_uri === 'string' ? voice.optimized_file_uri : undefined,
      provider: typeof voice.provider === 'string' && voice.provider ? voice.provider : 'unknown',
      parameters:
        voice.parameters && typeof voice.parameters === 'object'
          ? (voice.parameters as Record<string, unknown>)
          : {},
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
    sharedArchivedCategories.value = (list.archived ?? []).map((entry) => {
      const archiveEntry = entry as typeof entry & {
        items_count?: number | null
        thumbnail_uri?: string | null
        created_at?: number | null
      }
      return {
        baseName: entry.base_name,
        archiveFolder: entry.archive_folder,
        archivedAt: entry.archived_at,
        itemsCount: archiveEntry.items_count ?? undefined,
        thumbnailUri: archiveEntry.thumbnail_uri ?? undefined,
        createdAt: archiveEntry.created_at ?? undefined,
      }
    })

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
          localizedName: new LocalizedString(itemNameDict),
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
    if (isContentUnavailable(err)) markContentUnavailable()
    else sharedError.value = 'Failed to load categories.'
    console.error('Failed to load categories:', err)
    return []
  } finally {
    sharedLoading.value = false
  }
}

/** Whether *err* is the category list answering 503: no content provider is active. */
function isContentUnavailable(err: unknown): boolean {
  return (err as AxiosError).response?.status === 503
}

/** Drop every cached category, so views show that no content provider is active instead of stale content. */
function markContentUnavailable(): void {
  invalidateCategoriesCache()
  sharedContentUnavailable.value = true
  sharedError.value = 'No active content provider is configured.'
}

/**
 * Bring the category cache in line with whether content is available right now, which a
 * content configuration change (in this app, or a config file edited on disk) can flip.
 *
 * With categories cached, one list request checks they may still be shown; a 503 drops them.
 * After content was unavailable the categories are loaded again, so they return once it is not.
 */
export const revalidateContent = async (): Promise<void> => {
  if (sharedLoading.value) return // a load in progress reports availability itself
  if (sharedContentUnavailable.value) {
    invalidateCategoriesCache() // an empty cache makes ensureCategoriesLoaded load, not revalidate
    await ensureCategoriesLoaded()
    return
  }
  if (sharedCategories.value.length === 0) return // nothing cached that could be stale
  try {
    await withInflight('revalidate', () => categoriesList())
  } catch (err) {
    if (isContentUnavailable(err)) markContentUnavailable()
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
 *
 * Cached categories are returned at once, while revalidateContent checks in the background
 * that content is still available, so views stop showing them once it is not.
 */
export const ensureCategoriesLoaded = async (): Promise<CategoryFull[]> => {
  if (sharedCategories.value.length > 0 && failedCategoryIds.size === 0) {
    void revalidateContent()
    return sharedCategories.value
  }

  sharedLoading.value = true
  sharedError.value = ''
  sharedContentUnavailable.value = false

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
// Item detail loader — for ItemDetailsView
// 2 requests: GET …/items/{id}/images + GET …/items/{id}/voices
// ---------------------------------------------------------------------------

/**
 * Load images and voices for a single item.
 * Skips if already loaded. Used by ItemDetailsView.
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
        const thumbnailUri = itemThumbnailUri(
          imagesResponse.thumbnail_uri,
          images,
          items[itemIdx].thumbnailUri,
        )
        items[itemIdx] = {
          ...items[itemIdx],
          localizedName: Object.keys(itemNamesDict).length > 0
            ? new LocalizedString(itemNamesDict)
            : items[itemIdx].localizedName,
          images,
          imageMetadata: normalizeImageMetadata(imagesResponse.images),
          voices,
          thumbnailUri,
          mainImageId: imagesResponse.main_image_id ?? undefined,
          // Item tags and the image gallery both represent variants. The dedicated
          // main-image copy is exposed only by `thumbnailUri` and is not counted.
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

/** Apply an item-images response to just the matching item in the shared cache. */
export const updateCachedItemImages = (
  categoryId: string,
  itemId: string,
  response: ItemImagesResponse,
): void => {
  const categoryIndex = sharedCategories.value.findIndex((category) => category.id === categoryId)
  if (categoryIndex === -1) return

  const category = sharedCategories.value[categoryIndex]
  const itemIndex = category.items.findIndex((item) => item.id === itemId)
  if (itemIndex === -1) return

  const images = normalizeImages(
    (response.images ?? []).map((image) => image.file_uri ?? ''),
    [],
  )
  const nameDict = response.name as unknown as Record<string, string>
  const items = [...category.items]
  const cachedItem = items[itemIndex]
  const thumbnailUri = itemThumbnailUri(response.thumbnail_uri, images, cachedItem.thumbnailUri)
  items[itemIndex] = {
    ...cachedItem,
    localizedName: Object.keys(nameDict).length > 0
      ? new LocalizedString(nameDict)
      : cachedItem.localizedName,
    images,
    imageMetadata: normalizeImageMetadata(response.images),
    thumbnailUri,
    mainImageId: response.main_image_id ?? undefined,
    // `itemsImagesSetMain` returns variants plus a separate main thumbnail; retain
    // the same count shown by the category summary after navigating back.
    imagesCount: images.length,
  }

  const categories = [...sharedCategories.value]
  categories[categoryIndex] = { ...category, items }
  sharedCategories.value = categories
}
