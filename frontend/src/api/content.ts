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

const normalizeFiles = (raw: unknown, fallback: string[]): string[] => {
  if (Array.isArray(raw)) {
    const files = raw.filter((entry): entry is string => typeof entry === 'string' && entry.trim().length > 0)
    if (files.length > 0) return files
  }
  return fallback
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

export const loadCategoriesTree = async (): Promise<Category[]> => {
  const list = await listCategories()
  if (!list.categories.length) return []
  const categories = await Promise.all(
    list.categories.map(async (categoryName) => {
      const categoryFallback = mockCategories.find((c) => c.name.toLowerCase() === categoryName.toLowerCase())
      const [detail, itemNames] = await Promise.all([
        getCategoryDetail(categoryName),
        listItems(categoryName).then((r) => r.items).catch(() => []),
      ])
      const items = await Promise.all(
        itemNames.map(async (itemName) => {
          const itemId = slugify(itemName)
          const itemFallback = categoryFallback?.items.find((i) => i.name.toLowerCase() === itemName.toLowerCase())
          const [imagesResponse, voicesResponse] = await Promise.all([
            listItemImages(categoryName, itemName).catch(() => ({})),
            listItemVoices(categoryName, itemName).catch(() => ({})),
          ])
          const imagePayload = imagesResponse as { files?: unknown; images?: unknown }
          const files = normalizeFiles(
            imagePayload.files ?? imagePayload.images,
            itemFallback?.files ?? [],
          )
          const voices = normalizeVoices(
            'voices' in voicesResponse ? voicesResponse.voices : undefined,
            itemId,
            itemFallback?.voices,
          )
          const item: Item = {
            id: itemFallback?.id ?? `${slugify(categoryName)}-${itemId}`,
            name: itemName,
            files,
            thumbnailUri: files[0] ?? itemFallback?.thumbnailUri ?? '',
            voices,
          }
          return item
        }),
      )

      const category: Category = {
        id: categoryFallback?.id ?? slugify(detail.name),
        name: detail.name,
        description: categoryFallback?.description ?? '',
        icon: categoryFallback?.icon ?? { id: '', name: detail.name, language: 'en' as const, iconUri: items[0]?.thumbnailUri ?? '' },
        archived: false,
        voices: categoryFallback?.voices ?? DEFAULT_LANGUAGES.map((lang) => fallbackVoice(detail.name, lang)),
        items,
      }
      return category
    }),
  )
  return categories
}
