import { ref } from 'vue'
import type { Category, Item, Language, Voice } from '../types'
import { listCategories, getCategoryDetail } from './categories'
import { listItems } from './items'
import { listItemImages } from './itemImages'
import { listItemVoices } from './itemVoices'
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
export const sharedCategories = ref<Category[]>([])

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
async function _loadCategoriesBasic(): Promise<Category[]> {
  const list = await listCategories()
  if (!list.categories.length) {
    sharedCategories.value = []
    sharedCategoryNames.value = new Map()
    sharedLoading.value = false
    return []
  }

  const categories: Category[] = []

  for (const summary of list.categories) {
    const enName = summary.name.find((n) => n.language === 'en')?.name ?? ''
    const categoryFallback = mockCategories.find(
      (c) => c.name.toLowerCase() === enName.toLowerCase(),
    )
    const [detail, itemNames] = await Promise.all([
      withInflight(`detail:${enName}`, () => getCategoryDetail(enName)),
      withInflight(`items:${enName}`, () => listItems(enName)).then(
        (r) => r.items,
      ),
    ])

    const category: Category = {
      id: summary.category_id,
      name: detail.name,
      description: categoryFallback?.description ?? '',
      images: categoryFallback?.images ?? [
        { id: '', name: detail.name, language: 'en' as const, iconUri: '' },
      ],
      archived: false,
      voices: categoryFallback?.voices ?? [
        ...SUPPORTED_LANGUAGES.map((lang) => fallbackVoice(detail.name, lang)),
      ],
      items: itemNames.map((name) => ({
        id: `${summary.category_id}-${slugify(name)}`,
        name,
        images: [],
        voices: [],
        thumbnailUri:
          categoryFallback?.items.find(
            (i) => i.name.toLowerCase() === name.toLowerCase(),
          )?.thumbnailUri ?? '',
      })),
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
export const ensureCategoriesLoaded = async (): Promise<Category[]> => {
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
): Promise<Category | null> => {
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

  const detail = await getCategoryDetail(categoryName)
  const categoryFallback = mockCategories.find(
    (c) => c.name.toLowerCase() === categoryName.toLowerCase(),
  )
  const itemNames = await listItems(categoryName).then((r) => r.items).catch(
    () => [],
  )

  const items = await Promise.all(
    itemNames.map(async (itemName) => {
      const itemId = slugify(itemName)
      const itemFallback = categoryFallback?.items.find(
        (i) => i.name.toLowerCase() === itemName.toLowerCase(),
      )
      const [imagesResponse, voicesResponse] = await Promise.all([
        withInflight(
          `images:${categoryName}:${itemName}`,
          () => listItemImages(categoryName, itemName).catch(() => ({})),
        ),
        withInflight(
          `voices:${categoryName}:${itemName}`,
          () => listItemVoices(categoryName, itemName).catch(() => ({})),
        ),
      ])
      const imagePayload = imagesResponse as {
        files?: unknown
        images?: unknown
      }
      const images = normalizeImages(
        imagePayload.images ?? imagePayload.files,
        itemFallback?.images ?? [],
      )
      const voices = normalizeVoices(
        'voices' in voicesResponse
          ? (voicesResponse as { voices: unknown }).voices
          : undefined,
        itemId,
        itemFallback?.voices,
      )
      return {
        id: itemFallback?.id ?? `${slugify(categoryName)}-${itemId}`,
        name: itemName,
        images,
        thumbnailUri: images[0] ?? itemFallback?.thumbnailUri ?? '',
        voices,
      } satisfies Item
    }),
  )

  const fullCategory: Category = {
    id: categoryFallback?.id ?? slugify(categoryName),
    name: detail.name,
    description: categoryFallback?.description ?? '',
    images:
      categoryFallback?.images ?? [
        {
          id: '',
          name: detail.name,
          language: 'en' as const,
          iconUri: items[0]?.thumbnailUri ?? '',
        },
      ],
    archived: false,
    voices:
      categoryFallback?.voices ??
      SUPPORTED_LANGUAGES.map((lang) => fallbackVoice(detail.name, lang)),
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
