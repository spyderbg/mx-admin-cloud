import { http, HttpResponse } from 'msw'
import { mockCategories } from '../data/mockData'
import type { Voice } from '../types/viewModels'

interface ArchivedEntry {
  base_name: string
  archive_folder: string
  archived_at: number
}

const archivedCategories: ArchivedEntry[] = []

const slugify = (value: string): string =>
  value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')

const findCategory = (categoryName: string) =>
  mockCategories.find((c) => c.name['en']?.toLowerCase() === categoryName.toLowerCase())

const findCategoryById = (categoryId: string) =>
  mockCategories.find((c) => c.id.toLowerCase() === categoryId.toLowerCase())

const findItem = (category: ReturnType<typeof findCategory>, itemId: string) =>
  category?.items.find((item) => item.id === itemId || item.name.toLowerCase() === itemId.toLowerCase())

const toCategoryDetailResponse = (category: ReturnType<typeof findCategory>): Record<string, unknown> => {
  if (!category) return {}
  return {
    id: category.id,
    name: category.name,
    description: category.description,
    items: category.items.map((item) => ({
      id: item.id,
      name: Object.fromEntries(
        (['en', 'es', 'fr', 'bg'] as const).map((lang) => {
          const localized = item.voices.find((v) => v.language === lang)?.name
          return [lang, localized ?? item.name]
        }),
      ),
      thumbnail_uri: item.thumbnailUri ?? null,
      images_count: item.images.length,
    })),
  }
}

const toVoiceResponse = (voice: Voice): Record<string, unknown> => ({
  id: voice.id,
  language: voice.language,
  type: voice.type,
  file_uri: voice.fileUri ?? '',
})

const itemLocalizedName = (item: NonNullable<ReturnType<typeof findItem>>): Record<string, string> =>
  Object.fromEntries(
    (['en', 'es', 'fr', 'bg'] as const).map((lang) => {
      const localized = item.voices.find((v) => v.language === lang)?.name
      return [lang, localized ?? item.name]
    }),
  )

export const contentHandlers = [
  http.post('/api/v1/login', () => HttpResponse.json({ access_token: 'debug-token' })),

  http.get('/api/v1/content/categories', ({ request }) => {
    const url = new URL(request.url)
    const includeArchived = url.searchParams.get('include_archived') === 'true'
    const archivedOnly = url.searchParams.get('archived_only') === 'true'
    const active = mockCategories.filter((category) => !category.archived)
    if (archivedOnly) {
      return HttpResponse.json({ categories: [], archived: archivedCategories })
    }
    return HttpResponse.json({
      categories: active.map((category) => ({
        id: category.id,
        name: category.name,
        items_count: category.items.length,
        thumbnail_uri: category.thumbnailUri ?? null,
      })),
      archived: includeArchived ? archivedCategories : null,
      error: null,
    })
  }),

  http.post('/api/v1/content/categories', async ({ request }) => {
    const body = (await request.json()) as { name?: string }
    const name = body.name?.trim()
    if (!name) return HttpResponse.json({ detail: 'Name is required' }, { status: 422 })
    if (findCategory(name)) {
      return HttpResponse.json({ detail: 'Category already exists' }, { status: 422 })
    }
    const id = slugify(name)
    mockCategories.push({
      id,
      name: { en: name },
      description: {},
      images: [],
      archived: false,
      thumbnailUri: '',
      voices: [],
      items: [],
    })
    return HttpResponse.json({ category: name, message: 'Category created' }, { status: 201 })
  }),

  http.post('/api/v1/content/categories/restore', async ({ request }) => {
    const body = (await request.json()) as { name?: string }
    const name = body.name?.trim()
    if (!name) return HttpResponse.json({ restored: false, message: 'Name is required' }, { status: 422 })
    const index = archivedCategories.findIndex((entry) => entry.base_name.toLowerCase() === name.toLowerCase())
    if (index < 0) {
      return HttpResponse.json({ restored: false, message: 'Category not found in archive' }, { status: 404 })
    }
    const archived = archivedCategories.splice(index, 1)[0]
    if (!findCategory(name)) {
      mockCategories.push({
        id: slugify(name),
        name: { en: name },
        description: {},
        images: [],
        archived: false,
        thumbnailUri: '',
        voices: [],
        items: [],
      })
    }
    return HttpResponse.json({
      restored: true,
      name,
      from_archive: archived.archive_folder,
      message: 'Category restored',
    })
  }),

  http.get('/api/v1/content/categories/:category_name', ({ params }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json(toCategoryDetailResponse(category))
  }),

  http.put('/api/v1/content/categories/:category_name', ({ params }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json({})
  }),

  http.patch('/api/v1/content/categories/:category_name', async ({ params, request }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    const body = (await request.json()) as { name?: string }
    const nextName = body.name?.trim()
    if (!nextName) return HttpResponse.json({ detail: 'Name is required' }, { status: 422 })
    category.name = { ...category.name, en: nextName }
    category.id = slugify(nextName)
    return HttpResponse.json({ category: nextName, message: 'Category renamed' })
  }),

  http.delete('/api/v1/content/categories/:category_name', ({ params }) => {
    const name = String(params.category_name)
    const idx = mockCategories.findIndex((category) => category.name['en']?.toLowerCase() === name.toLowerCase())
    if (idx < 0) return new HttpResponse(null, { status: 404 })
    const [removed] = mockCategories.splice(idx, 1)
    const removedName = removed.name['en'] ?? ''
    const archiveFolder = `${slugify(removedName)}-${Date.now()}`
    archivedCategories.push({
      base_name: removedName,
      archive_folder: archiveFolder,
      archived_at: Date.now(),
    })
    return HttpResponse.json({
      archived: true,
      removed: null,
      name: removedName,
      archive_folder: archiveFolder,
      destination: `/archive/${archiveFolder}`,
      message: 'Category archived',
    })
  }),

  http.get('/api/v1/content/categories/:category_id/images', ({ params }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json({
      category_id: category.id,
      name: category.name,
      images: category.images.map((img) => ({ id: img.id, file_uri: img.iconUri })),
    })
  }),

  http.get('/api/v1/content/categories/:category_name/voices', ({ params }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json({
      category_id: category.id,
      name: category.name,
      voices: category.voices.map(toVoiceResponse),
    })
  }),

  http.post('/api/v1/content/categories/:category_name/voices', ({ params }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json({}, { status: 201 })
  }),

  http.put('/api/v1/content/categories/:category_name/voices/:voice_id', ({ params }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    const voice = category.voices.find((entry) => entry.id === String(params.voice_id))
    if (!voice) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json({})
  }),

  http.delete('/api/v1/content/categories/:category_name/voices/:voice_id', ({ params }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    const voice = category.voices.find((entry) => entry.id === String(params.voice_id))
    if (voice) voice.fileUri = ''
    return new HttpResponse(null, { status: 204 })
  }),

  http.get('/api/v1/content/categories/:category_name/items', ({ params }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json({
      items: category.items.map((item) => ({
        id: item.id,
        name: itemLocalizedName(item),
        thumbnail_uri: item.thumbnailUri ?? null,
        images_count: item.images.length,
      })),
    })
  }),

  http.get('/api/v1/content/categories/:category_name/items/:item_id', ({ params }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    const item = findItem(category, String(params.item_id))
    if (!item) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json({
      id: item.id,
      name: itemLocalizedName(item),
      thumbnail_uri: item.thumbnailUri ?? null,
      images_count: item.images.length,
    })
  }),

  http.put('/api/v1/content/categories/:category_name/items/:item_id', async ({ params, request }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    const item = findItem(category, String(params.item_id))
    if (!item) return new HttpResponse(null, { status: 404 })
    const body = (await request.json()) as { name?: string }
    if (body.name?.trim()) {
      item.name = body.name.trim()
      item.id = `${slugify(category.name['en'] ?? '')}-${slugify(item.name)}`
    }
    return HttpResponse.json({})
  }),

  http.delete('/api/v1/content/categories/:category_name/items/:item_id', ({ params }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    const idx = category.items.findIndex((item) => item.id === String(params.item_id) || item.name === String(params.item_id))
    if (idx < 0) return new HttpResponse(null, { status: 404 })
    category.items.splice(idx, 1)
    return new HttpResponse(null, { status: 204 })
  }),

  http.delete('/api/v1/content/categories/:category_name/items/:item_id/files/:file_index', ({ params }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    const item = findItem(category, String(params.item_id))
    if (!item) return new HttpResponse(null, { status: 404 })
    const index = Number(params.file_index)
    if (!Number.isInteger(index) || index < 0 || index >= item.images.length) return new HttpResponse(null, { status: 404 })
    if (item.images.length > 1) item.images.splice(index, 1)
    item.thumbnailUri = item.images[0] ?? ''
    return new HttpResponse(null, { status: 204 })
  }),

  http.get('/api/v1/content/categories/:category_name/items/:item_id/images', ({ params }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    const item = findItem(category, String(params.item_id))
    if (!item) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json({
      item_id: item.id,
      name: itemLocalizedName(item),
      images: item.images.map((uri, index) => ({ id: `${item.id}_${String(index + 1).padStart(2, '0')}`, file_uri: uri })),
      thumbnail_uri: item.thumbnailUri ?? null,
    })
  }),

  http.post('/api/v1/content/categories/:category_name/items/:item_id/images', async ({ params, request }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    const item = findItem(category, String(params.item_id))
    if (!item) return new HttpResponse(null, { status: 404 })
    const body = (await request.json().catch(() => ({}))) as { file?: string }
    if (body.file) item.images.push(body.file)
    item.thumbnailUri = item.images[0] ?? ''
    return HttpResponse.json({}, { status: 201 })
  }),

  http.put('/api/v1/content/categories/:category_name/items/:item_id/images/:image_index', async ({ params, request }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    const item = findItem(category, String(params.item_id))
    if (!item) return new HttpResponse(null, { status: 404 })
    const index = Number(params.image_index)
    const body = (await request.json().catch(() => ({}))) as { file?: string }
    if (body.file && Number.isInteger(index) && index >= 0 && index < item.images.length) {
      item.images[index] = body.file
      item.thumbnailUri = item.images[0] ?? ''
    }
    return HttpResponse.json({})
  }),

  http.get('/api/v1/content/categories/:category_name/items/:item_id/voices', ({ params }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    const item = findItem(category, String(params.item_id))
    if (!item) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json({
      item_id: item.id,
      name: itemLocalizedName(item),
      voices: item.voices.map(toVoiceResponse),
    })
  }),

  http.put('/api/v1/content/categories/:category_name/items/:item_id/voices/:voice_id', async ({ params, request }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    const item = findItem(category, String(params.item_id))
    if (!item) return new HttpResponse(null, { status: 404 })
    const voice = item.voices.find((entry) => entry.id === String(params.voice_id) || entry.language === String(params.voice_id))
    if (!voice) return new HttpResponse(null, { status: 404 })
    const body = (await request.json().catch(() => ({}))) as { name?: string; type?: string }
    if (body.name?.trim()) voice.name = body.name.trim()
    if (body.type?.trim()) voice.type = body.type.trim()
    return HttpResponse.json({})
  }),

  http.delete('/api/v1/content/categories/:category_name/items/:item_id/voices/:voice_id', ({ params }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    const item = findItem(category, String(params.item_id))
    if (!item) return new HttpResponse(null, { status: 404 })
    const voice = item.voices.find((entry) => entry.id === String(params.voice_id) || entry.language === String(params.voice_id))
    if (voice) voice.fileUri = ''
    return new HttpResponse(null, { status: 204 })
  }),

  http.put('/api/v1/content/categories/:category_name/items/:item_id/voices/:voice_id/audio', async ({ params, request }) => {
    const category = findCategory(String(params.category_name))
    if (!category) return new HttpResponse(null, { status: 404 })
    const item = findItem(category, String(params.item_id))
    if (!item) return new HttpResponse(null, { status: 404 })
    const voice = item.voices.find((entry) => entry.id === String(params.voice_id) || entry.language === String(params.voice_id))
    if (!voice) return new HttpResponse(null, { status: 404 })
    const body = (await request.json().catch(() => ({}))) as { fileUri?: string }
    if (body.fileUri) voice.fileUri = body.fileUri
    return HttpResponse.json({})
  }),

  http.post('/api/v1/content/items', async ({ request }) => {
    const form = await request.formData()
    const categoryName = String(form.get('category') ?? '')
    const itemName = String(form.get('item') ?? '')
    const category = findCategory(categoryName)
    if (!category || !itemName.trim()) return HttpResponse.json({ detail: 'Invalid category or item' }, { status: 422 })

    const catSlug = slugify(category.name['en'] ?? '')
    const itemId = `${catSlug}-${slugify(itemName)}`
    category.items.push({
      id: itemId,
      name: itemName,
      images: [],
      thumbnailUri: '',
      voices: (['en', 'es', 'fr', 'bg'] as const).map((lang) => ({
        id: `${itemId}-${lang}`,
        name: itemName,
        language: lang,
        type: lang === 'en' || lang === 'fr' ? 'male' : 'female',
        fileUri: '',
      })),
    })
    return HttpResponse.json(
      {
        category: category.name['en'] ?? '',
        item: itemName,
        folder: `/content/${catSlug}/${slugify(itemName)}`,
        image_id: itemId,
      },
      { status: 201 },
    )
  }),

  // Add an image to a category
  http.post('/api/v1/content/categories/:category_id/images', () => HttpResponse.json({}, { status: 201 })),

  // Replace a category image file
  http.put('/api/v1/content/categories/:category_id/images/:image_id', () => HttpResponse.json({})),

  // Delete category image
  http.delete('/api/v1/content/categories/:category_id/images/:image_id', () => new HttpResponse(null, { status: 404 })),

  // Get details of a specific content category
  http.get('/api/v1/content/categories/:category_id', () => HttpResponse.json({})),

  // List voices for a category
  http.get('/api/v1/content/categories/:category_id/voices', () => HttpResponse.json({})),

  // Add a voice to a category
  http.post('/api/v1/content/categories/:category_id/voices', () => HttpResponse.json({}, { status: 201 })),

  // Replace audio for a category voice
  http.put('/api/v1/content/categories/:category_id/voices/:voice_id', () => HttpResponse.json({})),

  // Delete a voice from a category
  http.delete('/api/v1/content/categories/:category_id/voices/:voice_id', () => new HttpResponse(null, { status: 204 })),

  // Create or replace a category's icon/thumbnail image
  http.post('/api/v1/content/categories/:category_id/icon', () => HttpResponse.json({})),

  // Delete a category's icon/thumbnail image
  http.delete('/api/v1/content/categories/:category_id/icon', () => HttpResponse.json({})),
]
