import { ref } from 'vue'
import type { CategoryFull, Item, Language, Voice } from '../types'
import {
  categoriesList,
  categoriesDetails,
  itemsImagesList,
  itemsVoicesList,
} from './contentClient'
import type { ItemImagesResponse, ItemSummary, ItemVoicesResponse } from '../types/contentSchemas'
import { mockCategories } from '../data/mockData'
import { SUPPORTED_LANGUAGES } from '../types'

// ---------------------------------------------------------------------------
// In-flight promise deduplication
// ---------------------------------------------------------------------------

const inflightPromises = new Map<string, Promise<unknown>>()

/**
 * Execute a fetcher with in-flight deduplication. If the same key is
 * requested while a promise is already in flight, return the existing
 * promise instead of creating a new one.
 */
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

/** All categories loaded at the "basic" level (names + items). */
export const sharedCategories = ref<CategoryFull[]>([])

/** id → name mapping for the shared categories. */
export const sharedCategoryNames = ref(new Map<string, string>())

/** Set of category IDs whose full data (images + voices) has been loaded. */
export const sharedLoadedCategoryIds = ref(new Set<string>())

/** Whether the basic category list is currently loading. */
export const sharedLoading = ref(false)

/** Error message from the last failed fetch, or empty string. */
export const sharedError = ref('')

// ---------------------------------------------------------------------------
// Basic category loader
// ---------------------------------------------------------------------------

const slugify = (value: string): string =>
  value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')

const fallbackVoice = (itemId: string, language: Language) => ({
  id: `${itemId}-${language}`,
  name: itemId.replaceAll('_', ' '),
  language,
  type: language === 'en' || language === 'fr' ? 'male' : 'female',
  iconUri: '',
  audioUri: '',
})

/**
 * Load basic category info (names, descriptions, item names) without
 * images or voices. Returns the array of categories and updates shared
 * reactive state. Uses in-flight deduplication to prevent duplicate
 * requests.
 */
async function _loadCategoriesBasic(): Promise<CategoryFull[]> {
  const list = await categoriesList()
  if (!list.categories.length) {
    sharedCategories.value = []
    sharedCategoryNames.value = new Map()
    sharedLoading.value = false
    return []
  }

  const categories: CategoryFull[] = []

  for (const summary of list.categories) {
    const enName = summary.name['en'] ?? ''
    const categoryFallback = mockCategories.find(
      (c) => c.name.toLowerCase() === enName.toLowerCase(),
    )
    const detail = await withInflight(`detail:${enName}`, () => categoriesDetails(enName))

    const detailEnName = detail.name['en'] ?? Object.values(detail.name)[0] ?? enName

    const category: CategoryFull = {
      id: summary.id,
      name: detailEnName,
      description: categoryFallback?.description ?? '',
      thumbnail_uri: summary.thumbnail_uri,
      images: categoryFallback?.images ?? [
        { id: '', name: detailEnName, language: 'en' as const, iconUri: '' },
      ],
      voices: categoryFallback?.voices ?? [
        ...SUPPORTED_LANGUAGES.map((lang) => fallbackVoice(detailEnName, lang)),
      ],
      items: (detail.items ?? []).map((itemSummary: ItemSummary) => {
        const itemName = itemSummary.name['en'] ?? Object.values(itemSummary.name)[0] ?? itemSummary.id
        return {
          id: itemSummary.id,
          name: itemName,
          images: [],
          voices: [],
          thumbnailUri:
            itemSummary.thumbnail_uri ??
            categoryFallback?.items.find(
              (i) => i.name.toLowerCase() === itemName.toLowerCase(),
            )?.thumbnailUri ?? '',
        }
      }),
    }
    categories.push(category)
  }

  const nameMap = new Map<string, string>()
  for (const c of categories) nameMap.set(c.id, c.name)

  sharedCategories.value = categories
  sharedCategoryNames.value = nameMap
  sharedLoading.value = false
  return categories
}

/**
 * Public entry point to load the basic category list. If data is already
 * cached, returns it immediately without fetching.
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
// Full category loader
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
  itemId: string,
  fallback?: Voice[],
): Voice[] => {
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
            : `${itemId}-${language}-${index}`,
        name:
          typeof voice.name === 'string' ? voice.name : itemId.replaceAll('_', ' '),
        language,
        type: typeof voice.type === 'string' ? voice.type : 'male',
        iconUri: '',
        audioUri: typeof voice.audio_url === 'string' ? voice.audio_url : '',
      })
    })
    if (parsed.length > 0) return parsed
  }
  if (fallback && fallback.length > 0) return fallback
  return SUPPORTED_LANGUAGES.map((lang) => fallbackVoice(itemId, lang))
}

/**
 * Load full category data (images + voices) for a category by name.
 * Updates shared state and tracks which categories have been fully
 * loaded. Uses in-flight deduplication.
 */
export const ensureCategoryFull = async (
  categoryName: string,
): Promise<CategoryFull | null> => {
  // Check if we already have the name→id mapping
  let targetId = ''
  for (const [id, name] of sharedCategoryNames.value) {
    if (name.toLowerCase() === categoryName.toLowerCase()) {
      targetId = id
      break
    }
  }

  // Check shared loaded set
  if (targetId && sharedLoadedCategoryIds.value.has(targetId)) {
    const existing = sharedCategories.value.find(
      (c) => c.id.toLowerCase() === targetId.toLowerCase(),
    )
    return existing ?? null
  }

  const detail = await categoriesDetails(categoryName)
  const detailEnName = detail.name['en'] ?? Object.values(detail.name)[0] ?? categoryName
  const categoryFallback = mockCategories.find(
    (c) => c.name.toLowerCase() === categoryName.toLowerCase(),
  )

  const items = await Promise.all(
    (detail.items ?? []).map(async (itemSummary: ItemSummary) => {
      const itemId = itemSummary.id
      const itemName = itemSummary.name['en'] ?? Object.values(itemSummary.name)[0] ?? itemId
      const itemFallback = categoryFallback?.items.find(
        (i) => i.name.toLowerCase() === itemName.toLowerCase(),
      )
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
        imagesResponse.files,
        itemFallback?.images ?? [],
      )
      const voices = normalizeVoices(
        voicesResponse.voices,
        itemId,
        itemFallback?.voices,
      )
      return {
        id: itemId,
        name: itemName,
        images,
        thumbnailUri: images[0] ?? itemSummary.thumbnail_uri ?? itemFallback?.thumbnailUri ?? '',
        voices,
      } satisfies Item
    }),
  )

  const fullCategory: CategoryFull = {
    id: categoryFallback?.id ?? slugify(categoryName),
    name: detailEnName,
    description: categoryFallback?.description ?? '',
    images: categoryFallback?.images ?? [
      { id: '', name: detailEnName, language: 'en' as const, iconUri: items[0]?.thumbnailUri ?? '' },
    ],
    thumbnail_uri: items[0]?.thumbnailUri ?? categoryFallback?.thumbnailUri ?? null,
    voices:
      categoryFallback?.voices ??
      SUPPORTED_LANGUAGES.map((lang) => fallbackVoice(detailEnName, lang)),
    items,
  }

  // Update shared state
  const idx = sharedCategories.value.findIndex(
    (c) => c.id.toLowerCase() === fullCategory.id.toLowerCase(),
  )
  if (idx !== -1) {
    const arr = [...sharedCategories.value]
    arr[idx] = fullCategory
    sharedCategories.value = arr
  } else {
    sharedCategories.value = [...sharedCategories.value, fullCategory]
  }

  // Track loaded
  if (targetId) {
    const loaded = new Set(sharedLoadedCategoryIds.value)
    loaded.add(targetId)
    sharedLoadedCategoryIds.value = loaded
  }

  return fullCategory
}
