import { mockCategories } from '../data/mockData'
import type { Category, Item, Language, Voice } from '../types'
import { getCategoryDetail, listCategories } from './categories'
import { listItems } from './items'
import { listItemImages } from './itemImages'
import { listItemVoices } from './itemVoices'

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_LANGUAGES: Language[] = ['en', 'es', 'fr', 'bg']

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

const slugify = (value: string): string =>
  value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')

const fallbackVoice = (itemId: string, language: Language): Voice => ({
  id: `${itemId}-${language}`,
  name: itemId.replaceAll('_', ' '),
  language,
  type: language === 'en' || language === 'fr' ? 'male' : 'female',
  iconUri: '',
  audioUri: '',
})

const normalizeVoices = (raw: unknown, itemId: string, fallback?: Voice[]): Voice[] => {
  if (Array.isArray(raw) && raw.length > 0) {
    const parsed: Voice[] = []
    raw.forEach((entry, index) => {
      if (!entry || typeof entry !== 'object') return
      const voice = entry as Record<string, unknown>
      const language = voice.language
      if (language !== 'en' && language !== 'es' && language !== 'fr' && language !== 'bg') return
      parsed.push({
        id: typeof voice.id === 'string' ? voice.id : `${itemId}-${language}-${index}`,
        name: typeof voice.name === 'string' ? voice.name : itemId.replaceAll('_', ' '),
        language,
        type: typeof voice.type === 'string' ? voice.type : 'male',
        iconUri: '',
        audioUri: typeof voice.audio_url === 'string' ? voice.audio_url : '',
      })
    })
    if (parsed.length > 0) return parsed
  }
  if (fallback && fallback.length > 0) return fallback
  return DEFAULT_LANGUAGES.map((lang) => fallbackVoice(itemId, lang))
}

const normalizeImages = (raw: unknown, fallback: string[]): string[] => {
  if (Array.isArray(raw)) {
    const images = raw.filter((entry): entry is string => typeof entry === 'string' && entry.trim().length > 0)
    if (images.length > 0) return images
  }
  return fallback
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Load basic category info (names, descriptions, item names) without fetching
 * images or voices. This is the initial load for page mount -- it reduces the
 * API call count from O(N*M) to O(N) where N = categories and M = items per
 * category.
 */
export const loadCategoriesBasic = async (): Promise<Category[]> => {
  const list = await listCategories()
  if (!list.categories.length) return []

  return await Promise.all(
    list.categories.map(async (summary) => {
      const enName = summary.name['en'] ?? ''
      const categoryFallback = mockCategories.find((c) => c.name.toLowerCase() === enName.toLowerCase())
      const detail = await getCategoryDetail(enName)
      const itemNames = await listItems(enName).then((r) => r.items).catch(() => [])

      const category: Category = {
        id: summary.category_id,
        name: detail.name,
        description: categoryFallback?.description ?? '',
        images: categoryFallback?.images ?? [{ id: '', name: detail.name, language: 'en' as const, iconUri: '' }],
        archived: false,
        voices: categoryFallback?.voices ?? DEFAULT_LANGUAGES.map((lang) => fallbackVoice(detail.name, lang)),
        items: itemNames.map((name) => ({
          id: `${summary.category_id}-${slugify(name)}`,
          name,
          images: [],
          voices: [],
          thumbnailUri: categoryFallback?.items.find(
            (i) => i.name.toLowerCase() === name.toLowerCase(),
          )?.thumbnailUri ?? '',
        })),
      }
      return category
    }),
  )
}

/**
 * Load a single category with all its items populated with images and voices.
 */
export const loadCategoryFull = async (categoryName: string): Promise<Category | null> => {
  const detail = await getCategoryDetail(categoryName)
  const categoryFallback = mockCategories.find((c) => c.name.toLowerCase() === categoryName.toLowerCase())
  const itemNames = await listItems(categoryName).then((r) => r.items).catch(() => [])

  const items = await Promise.all(
    itemNames.map(async (itemName) => {
      const itemId = slugify(itemName)
      const itemFallback = categoryFallback?.items.find((i) => i.name.toLowerCase() === itemName.toLowerCase())
      const [imagesResponse, voicesResponse] = await Promise.all([
        listItemImages(categoryName, itemName).catch(() => ({})),
        listItemVoices(categoryName, itemName).catch(() => ({})),
      ])
      const imagePayload = imagesResponse as { files?: unknown; images?: unknown }
      const images = normalizeImages(
        imagePayload.images ?? imagePayload.files,
        itemFallback?.images ?? [],
      )
      const voices = normalizeVoices(
        'voices' in voicesResponse ? voicesResponse.voices : undefined,
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

  return {
    id: categoryFallback?.id ?? slugify(categoryName),
    name: detail.name,
    description: categoryFallback?.description ?? '',
    images: categoryFallback?.images ?? [{ id: '', name: detail.name, language: 'en' as const, iconUri: items[0]?.thumbnailUri ?? '' }],
    archived: false,
    voices: categoryFallback?.voices ?? DEFAULT_LANGUAGES.map((lang) => fallbackVoice(detail.name, lang)),
    items,
  } satisfies Category
}

/**
 * @deprecated Use {@link loadCategoriesBasic} + {@link loadCategoryFull} instead.
 * Loads the entire category tree (all categories, all items with images/voices).
 * Expensive O(N*M) operation -- prefer the two-step approach.
 */
export const loadCategoriesTree = async (): Promise<Category[]> => {
  const list = await listCategories()
  if (!list.categories.length) return []
  const categories = await Promise.all(
    list.categories.map(async (summary) => {
      const enName = summary.name['en'] ?? ''
      const categoryFallback = mockCategories.find((c) => c.name.toLowerCase() === enName.toLowerCase())
      const [detail, itemNames] = await Promise.all([
        getCategoryDetail(enName),
        listItems(enName).then((r) => r.items).catch(() => []),
      ])
      const items = await Promise.all(
        itemNames.map(async (itemName) => {
          const itemId = slugify(itemName)
          const itemFallback = categoryFallback?.items.find((i) => i.name.toLowerCase() === itemName.toLowerCase())
          const [imagesResponse, voicesResponse] = await Promise.all([
            listItemImages(enName, itemName).catch(() => ({})),
            listItemVoices(enName, itemName).catch(() => ({})),
          ])
          const imagePayload = imagesResponse as { files?: unknown; images?: unknown }
          const images = normalizeImages(
            imagePayload.images ?? imagePayload.files,
            itemFallback?.images ?? [],
          )
          const voices = normalizeVoices(
            'voices' in voicesResponse ? voicesResponse.voices : undefined,
            itemId,
            itemFallback?.voices,
          )
          const item: Item = {
            id: itemFallback?.id ?? `${summary.category_id}-${itemId}`,
            name: itemName,
            images,
            thumbnailUri: images[0] ?? itemFallback?.thumbnailUri ?? '',
            voices,
          }
          return item
        }),
      )

      const category: Category = {
        id: summary.category_id,
        name: detail.name,
        description: categoryFallback?.description ?? '',
        images: categoryFallback?.images ?? [{ id: '', name: detail.name, language: 'en' as const, iconUri: items[0]?.thumbnailUri ?? '' }],
        archived: false,
        voices: categoryFallback?.voices ?? DEFAULT_LANGUAGES.map((lang) => fallbackVoice(detail.name, lang)),
        items,
      }
      return category
    }),
  )
  return categories
}
