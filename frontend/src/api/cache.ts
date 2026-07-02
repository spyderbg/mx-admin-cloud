import { ref, shallowRef } from 'vue'
import type { CategoryFull, Item, Voice } from '../types/viewModels'
import { LocalizedString, SUPPORTED_LANGUAGES } from '../types/viewModels'
import {
  categoriesList,
  categoriesDetails,
  categoriesImagesList,
  categoriesVoicesList,
  itemsImagesList,
  itemsVoicesList,
} from './contentClient'
import type { CategoryImagesResponse, ItemImagesResponse, ItemSummary, ItemVoicesResponse } from '../types/contentSchemas'

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

/** All categories loaded at the "basic" level (names + items). */
export const sharedCategories = shallowRef<CategoryFull[]>([])

/** id → name mapping for the shared categories. */
export const sharedCategoryNames = ref(new Map<string, string>())

/** Whether the basic category list is currently loading. */
export const sharedLoading = ref(false)

/** Error message from the last failed fetch, or empty string. */
export const sharedError = ref('')

// ---------------------------------------------------------------------------
// Basic category loader
// ---------------------------------------------------------------------------


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
    const nameDict = summary.name as unknown as Record<string, string>
    const enName = nameDict['en'] ?? Object.values(nameDict)[0] ?? summary.id
    const detail = await withInflight(`detail:${enName}`, () => categoriesDetails(enName))
    const detailNameDict = detail.name as unknown as Record<string, string>

    const category: CategoryFull = {
      id: summary.id,
      name: new LocalizedString(detailNameDict),
      description: detail.description ?? '',
      thumbnail_uri: summary.thumbnail_uri,
      images: [],
      voices: [],
      items: (detail.items ?? []).map((itemSummary: ItemSummary) => {
        const itemNameDict = itemSummary.name as unknown as Record<string, string>
        const itemName = itemNameDict['en'] ?? Object.values(itemNameDict)[0] ?? itemSummary.id
        return {
          id: itemSummary.id,
          name: itemName,
          images: [],
          voices: [],
          thumbnailUri: itemSummary.thumbnail_uri ?? '',
        }
      }),
    }
    categories.push(category)
  }

  const nameMap = new Map<string, string>()
  for (const c of categories) nameMap.set(c.id, c.name.en)

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

/**
 * Load full category data (images + voices) for a category by name.
 * Updates shared state and tracks which categories have been fully
 * loaded. Uses in-flight deduplication.
 */
export const ensureCategoryFull = async (
  categoryName: string,
): Promise<CategoryFull | null> => {
  const [detail, voicesResponse, categoryImagesResponse] = await Promise.all([
    categoriesDetails(categoryName),
    withInflight(
      `cat-voices:${categoryName}`,
      () => categoriesVoicesList(categoryName).catch(() => ({ category_id: '', name: {} as LocalizedString, voices: [] })),
    ),
    withInflight(
      `cat-images:${categoryName}`,
      () => categoriesImagesList(categoryName).catch(() => ({}) as CategoryImagesResponse),
    ),
  ])

  const nameDict = detail.name as unknown as Record<string, string>

  const items = await Promise.all(
    (detail.items ?? []).map(async (itemSummary: ItemSummary) => {
      const itemId = itemSummary.id
      const itemNameDict = itemSummary.name as unknown as Record<string, string>
      const itemName = itemNameDict['en'] ?? Object.values(itemNameDict)[0] ?? itemId
      const [imagesResponse, itemVoicesResponse] = await Promise.all([
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
      const itemNamesDict = (itemVoicesResponse.name as unknown as Record<string, string>) ?? {}
      const voices = normalizeVoices(itemVoicesResponse.voices, itemNamesDict)
      return {
        id: itemId,
        name: itemName,
        images,
        thumbnailUri: images[0] ?? itemSummary.thumbnail_uri ?? '',
        voices,
      } satisfies Item
    }),
  )

  const categoryThumbnail =
    categoryImagesResponse.images?.[0]?.file_uri ?? items[0]?.thumbnailUri ?? null

  const fullCategory: CategoryFull = {
    id: detail.id,
    name: new LocalizedString(nameDict),
    description: detail.description ?? '',
    images: [],
    thumbnail_uri: categoryThumbnail,
    voices: normalizeVoices(voicesResponse.voices, (voicesResponse.name as unknown as Record<string, string>) ?? {}),
    items,
  }

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

  return fullCategory
}
