import { http, HttpResponse } from 'msw'
import { mockCategories } from '../data/mockData'
import type { ImageMetadata, Voice } from '../types/viewModels'
import { itemIdFromEnglishWord } from '../utils/itemIds'
import { MOCK_TOKEN } from './mockToken'

interface ArchivedEntry {
  base_name: string
  archive_folder: string
  archived_at: number
  items_count?: number
  thumbnail_uri?: string | null
}

const archivedCategories: ArchivedEntry[] = []

/**
 * Removed category records, keyed by archive folder. The real backend moves the whole
 * category folder into the archive and moves it back on restore, so the mock has to
 * keep the record around to restore the items with it.
 */
const archivedRecords = new Map<string, CategoryRecord>()

const slugify = (value: string): string =>
  value
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')

const findCategory = (categoryRef: string) => {
  const normalizedRef = categoryRef.toLowerCase()
  const slugRef = slugify(categoryRef)
  return mockCategories.find((category) => {
    const englishName = category.name['en'] ?? ''
    return (
      category.id.toLowerCase() === normalizedRef ||
      category.id.toLowerCase() === slugRef ||
      englishName.toLowerCase() === normalizedRef ||
      slugify(englishName) === slugRef
    )
  })
}

const findCategoryById = (categoryId: string) =>
  mockCategories.find((c) => c.id.toLowerCase() === categoryId.toLowerCase())

type CategoryRecord = NonNullable<ReturnType<typeof findCategoryById>>

const findItem = (category: ReturnType<typeof findCategory>, itemId: string) =>
  category?.items.find((item) => item.id === itemId || item.name.toLowerCase() === itemId.toLowerCase())

type ItemRecord = NonNullable<ReturnType<typeof findItem>>

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
      images_count: itemImagesCount(item),
    })),
  }
}

const toVoiceResponse = (voice: Voice): Record<string, unknown> => ({
  id: voice.id,
  language: voice.language,
  type: voice.type,
  file_uri: voice.fileUri ?? '',
  provider: voice.provider,
  parameters: voice.parameters,
})

const itemLocalizedName = (item: NonNullable<ReturnType<typeof findItem>>): Record<string, string> =>
  Object.fromEntries(
    (['en', 'es', 'fr', 'bg'] as const).map((lang) => {
      const localized = item.voices.find((v) => v.language === lang)?.name
      return [lang, localized ?? item.name]
    }),
  )

// Category-level images/voices carry their own id; item-level images don't, so their
// id is synthesized positionally (matches the backend's "{item_id}_NN" file naming).
const categoryImageId = (category: CategoryRecord, index: number): string =>
  `${category.id}-img-${String(index + 1).padStart(2, '0')}`

// The item's main image lives only in `thumbnailUri` (matches the backend's unindexed
// "{item_id}.png" main file) — `item.images` holds just the indexed variants. The main
// image is a copy of one of them, tracked here by `mainImageSource`.
const itemImageId = (item: ItemRecord, index: number): string =>
  `${item.id}_${String(index + 1).padStart(2, '0')}`

const itemImageIndex = (item: ItemRecord, imageId: string): number =>
  item.images.findIndex((_, index) => itemImageId(item, index) === imageId)

const itemHasDedicatedMain = (item: ItemRecord): boolean =>
  Boolean(item.thumbnailUri) && !item.images.includes(item.thumbnailUri)

// Which variant the mock's main image was copied from. The backend recovers this by
// comparing file contents; the mock just remembers it, keyed by item id.
const mainImageSource = new Map<string, string>()

/** Id of the variant the main image was copied from; null when it matches none. */
const itemMainImageId = (item: ItemRecord): string | null => {
  const selected = mainImageSource.get(item.id)
  if (selected && itemImageIndex(item, selected) >= 0) return selected
  if (!item.thumbnailUri || item.images.length === 0) return null
  // Not selected yet: as on the backend, a fresh item's main image is a copy of "_01".
  return itemImageId(item, 0)
}

// How each item image was made, keyed by file URI — the backend's per-image metadata sidecar.
const imageMetadata = new Map<string, ImageMetadata>()

const imageMetadataFor = (uri: string): ImageMetadata =>
  imageMetadata.get(uri) ?? { provider: 'unknown', parameters: {} }

const uploadedImageMetadata = (): ImageMetadata =>
  ({ provider: 'custom', parameters: { date: new Date().toISOString() } })

/** Store a new item image like the backend: an item's first image becomes its main image, copied into "_01". */
const addMockItemImage = (category: CategoryRecord, item: ItemRecord, metadata: ImageMetadata) => {
  if (!item.thumbnailUri && item.images.length === 0) {
    item.thumbnailUri = `/content/${category.id}/${item.id}/${item.id}.png`
    const firstVariant = `/content/${category.id}/${item.id}/${itemImageId(item, 0)}.png`
    item.images = [firstVariant]
    imageMetadata.set(item.thumbnailUri, metadata)
    imageMetadata.set(firstVariant, metadata)
    return { id: item.id, file_uri: item.thumbnailUri, action: 'add', ...metadata }
  }
  const id = itemImageId(item, item.images.length)
  const uri = `/content/${category.id}/${item.id}/${id}.png`
  item.images.push(uri)
  imageMetadata.set(uri, metadata)
  return { id, file_uri: uri, action: 'add', ...metadata }
}

/** Image-variant count, matching the backend's category-summary images_count. */
const itemImagesCount = (item: ItemRecord): number => item.images.length

const findCategoryAndItem = (categoryId: string, itemId: string): [CategoryRecord, ItemRecord] | null => {
  const category = findCategoryById(categoryId)
  if (!category) return null
  const item = findItem(category, itemId)
  if (!item) return null
  return [category, item]
}

export const contentHandlers = [
  http.post('/api/v1/login', () => HttpResponse.json({ access_token: MOCK_TOKEN })),
  // No real Google client id exists in the preview, so only password login is offered.
  http.get('/api/v1/auth/config', () => HttpResponse.json({ google_enabled: false, google_client_id: null })),

  // List all content categories
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

  // Create a new content category
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

  // Restore a category from archive
  http.post('/api/v1/content/categories/restore', async ({ request }) => {
    const body = (await request.json()) as { name?: string }
    const name = body.name?.trim()
    if (!name) return HttpResponse.json({ restored: false, message: 'Name is required' }, { status: 422 })
    // Accepts a base folder name or a full archive folder name, like the backend.
    const lowered = name.toLowerCase()
    const index = archivedCategories.findIndex(
      (entry) => entry.archive_folder.toLowerCase() === lowered || entry.base_name.toLowerCase() === lowered,
    )
    if (index < 0) {
      return HttpResponse.json({ restored: false, message: 'Category not found in archive' }, { status: 404 })
    }
    const archived = archivedCategories.splice(index, 1)[0]
    const record = archivedRecords.get(archived.archive_folder)
    archivedRecords.delete(archived.archive_folder)
    if (!findCategory(archived.base_name)) {
      mockCategories.push(
        record ?? {
          id: slugify(archived.base_name),
          name: { en: archived.base_name },
          description: {},
          images: [],
          archived: false,
          thumbnailUri: '',
          voices: [],
          items: [],
        },
      )
    }
    return HttpResponse.json({
      restored: true,
      name: archived.base_name,
      from_archive: archived.archive_folder,
      message: 'Category restored',
    })
  }),

  // Permanently delete a category snapshot from archive
  http.delete('/api/v1/content/categories/archive/:archive_name', ({ params }) => {
    const name = String(params.archive_name).trim()
    const lowered = name.toLowerCase()
    const index = archivedCategories.findIndex(
      (entry) => entry.archive_folder.toLowerCase() === lowered || entry.base_name.toLowerCase() === lowered,
    )
    if (index < 0) {
      return HttpResponse.json({ deleted: false, message: 'Category not found in archive' }, { status: 404 })
    }
    const [archived] = archivedCategories.splice(index, 1)
    archivedRecords.delete(archived.archive_folder)
    return HttpResponse.json({
      deleted: true,
      name: archived.base_name,
      archive_folder: archived.archive_folder,
      message: 'Archived category deleted',
    })
  }),

  // Get details of a specific content category
  http.get('/api/v1/content/categories/:category_id', ({ params }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json(toCategoryDetailResponse(category))
  }),

  // Update a content category's name and description
  http.patch('/api/v1/content/categories/:category_id', async ({ params, request }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return new HttpResponse(null, { status: 404 })
    const body = (await request.json()) as {
      new_category_id?: string | null
      category_name?: Record<string, string>
      description?: Record<string, string>
    }
    const oldCategoryId = category.id
    if (body.category_name) category.name = { ...category.name, ...body.category_name }
    if (body.description) category.description = { ...category.description, ...body.description }
    if (body.new_category_id?.trim()) category.id = body.new_category_id.trim()
    const nextName = category.name['en'] ?? category.id
    return HttpResponse.json({
      category: nextName,
      category_id: category.id,
      old_category_id: oldCategoryId,
      message: 'Category renamed',
    })
  }),

  // Remove a content category
  http.delete('/api/v1/content/categories/:category_id', ({ params }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return new HttpResponse(null, { status: 404 })
    const idx = mockCategories.findIndex((entry) => entry.id === category.id)
    const [removed] = mockCategories.splice(idx, 1)
    const removedName = category.name['en'] ?? category.id
    // Mirrors the backend's '{folder}_{unix_seconds}' convention, which the UI parses
    // for the relative "archived N days ago" label and sends back on restore.
    const baseName = slugify(removedName)
    const archivedAt = Math.floor(Date.now() / 1000)
    const archiveFolder = `${baseName}_${archivedAt}`
    archivedCategories.push({
      base_name: baseName,
      archive_folder: archiveFolder,
      archived_at: archivedAt,
      items_count: removed.items.length,
      thumbnail_uri: removed.thumbnailUri ?? null,
    })
    archivedRecords.set(archiveFolder, removed)
    return HttpResponse.json({
      archived: true,
      removed: null,
      name: removedName,
      archive_folder: archiveFolder,
      destination: `/archive/${archiveFolder}`,
      message: 'Category archived',
    })
  }),

  // List all images in a category
  http.get('/api/v1/content/categories/:category_id/images', ({ params }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json({
      category_id: category.id,
      name: category.name,
      images: category.images.map((img) => ({ id: img.id, file_uri: img.iconUri })),
    })
  }),

  // Add an image to a category
  http.post('/api/v1/content/categories/:category_id/images', async ({ params, request }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return new HttpResponse(null, { status: 404 })
    await request.formData()
    const id = categoryImageId(category, category.images.length)
    const uri = `/content/${category.id}/${id}.png`
    category.images.push({ id, name: category.name['en'] ?? category.id, language: 'en', iconUri: uri })
    return HttpResponse.json({ id, file_uri: uri, action: 'add' }, { status: 201 })
  }),

  // Replace a category image file
  http.put('/api/v1/content/categories/:category_id/images/:image_id', async ({ params, request }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return new HttpResponse(null, { status: 404 })
    const image = category.images.find((entry) => entry.id === String(params.image_id))
    if (!image) return new HttpResponse(null, { status: 404 })
    await request.formData()
    image.iconUri = `/content/${category.id}/${image.id}.png`
    return HttpResponse.json({ id: image.id, file_uri: image.iconUri, action: 'update' })
  }),

  // Delete category image
  http.delete('/api/v1/content/categories/:category_id/images/:image_id', ({ params }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return new HttpResponse(null, { status: 404 })
    const index = category.images.findIndex((entry) => entry.id === String(params.image_id))
    if (index < 0) return new HttpResponse(null, { status: 404 })
    const [removed] = category.images.splice(index, 1)
    return HttpResponse.json({ id: removed.id, file_uri: removed.iconUri, action: 'delete' })
  }),

  // Create or replace a category's icon/thumbnail image
  http.post('/api/v1/content/categories/:category_id/icon', async ({ params, request }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return new HttpResponse(null, { status: 404 })
    await request.formData()
    category.thumbnailUri = `/content/${category.id}/${category.id}.png`
    return HttpResponse.json({ id: `${category.id}-icon`, file_uri: category.thumbnailUri, action: 'update' })
  }),

  // Delete a category's icon/thumbnail image
  http.delete('/api/v1/content/categories/:category_id/icon', ({ params }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return new HttpResponse(null, { status: 404 })
    category.thumbnailUri = ''
    return HttpResponse.json({ id: `${category.id}-icon`, file_uri: '', action: 'delete' })
  }),

  // List voices for a category
  http.get('/api/v1/content/categories/:category_id/voices', ({ params }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json({
      category_id: category.id,
      name: category.name,
      voices: category.voices.map(toVoiceResponse),
    })
  }),

  // Add a voice to a category
  http.post('/api/v1/content/categories/:category_id/voices', async ({ params, request }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return new HttpResponse(null, { status: 404 })
    const form = await request.formData()
    const language = String(form.get('language') ?? '') as Voice['language']
    const voice: Voice = {
      id: `${category.id}-v-${language}`,
      name: category.name['en'] ?? category.id,
      language,
      type: language === 'en' || language === 'fr' ? 'male' : 'female',
      fileUri: `/content/${category.id}/${category.id}_${language}.mp3`,
      provider: 'unknown',
      parameters: {},
    }
    const existingIndex = category.voices.findIndex((entry) => entry.language === language)
    if (existingIndex >= 0) category.voices[existingIndex] = voice
    else category.voices.push(voice)
    return HttpResponse.json({ ...toVoiceResponse(voice), action: 'add' }, { status: 201 })
  }),

  // Replace audio for a category voice
  http.put('/api/v1/content/categories/:category_id/voices/:voice_id', async ({ params, request }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return new HttpResponse(null, { status: 404 })
    const voice = category.voices.find((entry) => entry.id === String(params.voice_id))
    if (!voice) return new HttpResponse(null, { status: 404 })
    await request.formData()
    voice.fileUri = `/content/${category.id}/${voice.id}.mp3`
    return HttpResponse.json({ ...toVoiceResponse(voice), action: 'update' })
  }),

  // Delete a voice from a category
  http.delete('/api/v1/content/categories/:category_id/voices/:voice_id', ({ params }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return new HttpResponse(null, { status: 404 })
    const voice = category.voices.find((entry) => entry.id === String(params.voice_id))
    if (!voice) return new HttpResponse(null, { status: 404 })
    voice.fileUri = ''
    return HttpResponse.json({ ...toVoiceResponse(voice), action: 'delete' })
  }),

  // List all items in a category
  http.get('/api/v1/content/categories/:category_id/items', ({ params }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return new HttpResponse(null, { status: 404 })
    return HttpResponse.json({
      items: category.items.map((item) => ({
        id: item.id,
        name: itemLocalizedName(item),
        thumbnail_uri: item.thumbnailUri ?? null,
        images_count: itemImagesCount(item),
      })),
    })
  }),

  // Get a single item by ID
  http.get('/api/v1/content/categories/:category_id/items/:item_id', ({ params }) => {
    const found = findCategoryAndItem(String(params.category_id), String(params.item_id))
    if (!found) return new HttpResponse(null, { status: 404 })
    const [, item] = found
    return HttpResponse.json({
      id: item.id,
      name: itemLocalizedName(item),
      thumbnail_uri: item.thumbnailUri ?? null,
      images_count: itemImagesCount(item),
    })
  }),

  // Update an item in a category
  http.put('/api/v1/content/categories/:category_id/items/:item_id', async ({ params, request }) => {
    const found = findCategoryAndItem(String(params.category_id), String(params.item_id))
    if (!found) return new HttpResponse(null, { status: 404 })
    const [, item] = found
    const body = (await request.json()) as { item_name?: Record<string, string> }
    if (body.item_name) {
      for (const voice of item.voices) {
        const localized = body.item_name[voice.language]
        if (localized) voice.name = localized
      }
      item.name = body.item_name['en'] ?? item.name
    }
    return HttpResponse.json({
      id: item.id,
      name: itemLocalizedName(item),
      thumbnail_uri: item.thumbnailUri ?? null,
      images_count: itemImagesCount(item),
    })
  }),

  // Delete an item from a category
  http.delete('/api/v1/content/categories/:category_id/items/:item_id', ({ params }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return new HttpResponse(null, { status: 404 })
    const idx = category.items.findIndex((item) => item.id === String(params.item_id))
    if (idx < 0) return new HttpResponse(null, { status: 404 })
    category.items.splice(idx, 1)
    return new HttpResponse(null, { status: 204 })
  }),

  // List image variants for an item
  http.get('/api/v1/content/categories/:category_id/items/:item_id/images', ({ params }) => {
    const found = findCategoryAndItem(String(params.category_id), String(params.item_id))
    if (!found) return new HttpResponse(null, { status: 404 })
    const [, item] = found
    return HttpResponse.json({
      item_id: item.id,
      name: itemLocalizedName(item),
      images: item.images.map((uri, index) => ({ id: itemImageId(item, index), file_uri: uri, ...imageMetadataFor(uri) })),
      thumbnail_uri: item.thumbnailUri ?? null,
      main_image_id: itemMainImageId(item),
    })
  }),

  // Add an image file variant to an item
  http.post('/api/v1/content/categories/:category_id/items/:item_id/images', async ({ params, request }) => {
    const found = findCategoryAndItem(String(params.category_id), String(params.item_id))
    if (!found) return new HttpResponse(null, { status: 404 })
    const [category, item] = found
    await request.formData()
    return HttpResponse.json(addMockItemImage(category, item, uploadedImageMetadata()), { status: 201 })
  }),

  // Replace a specific image file variant
  http.put('/api/v1/content/categories/:category_id/items/:item_id/images/:image_id', async ({ params, request }) => {
    const found = findCategoryAndItem(String(params.category_id), String(params.item_id))
    if (!found) return new HttpResponse(null, { status: 404 })
    const [category, item] = found
    const index = itemImageIndex(item, String(params.image_id))
    if (index < 0) return new HttpResponse(null, { status: 404 })
    await request.formData()
    const uri = `/content/${category.id}/${item.id}/${String(params.image_id)}.png`
    item.images[index] = uri
    const metadata = uploadedImageMetadata()
    imageMetadata.set(uri, metadata)
    return HttpResponse.json({ id: String(params.image_id), file_uri: uri, action: 'update', ...metadata })
  }),

  // Delete a variant from an item (the main image isn't part of this list, so it's
  // never a delete target here — mirrors the backend excluding it from GET .../images)
  http.delete('/api/v1/content/categories/:category_id/items/:item_id/images/:image_id', ({ params }) => {
    const found = findCategoryAndItem(String(params.category_id), String(params.item_id))
    if (!found) return new HttpResponse(null, { status: 404 })
    const [, item] = found
    const imageId = String(params.image_id)
    const index = itemImageIndex(item, imageId)
    if (index < 0) return new HttpResponse(null, { status: 404 })
    const hadDedicatedMain = itemHasDedicatedMain(item)
    const removedUri = item.images[index]
    item.images.splice(index, 1)
    if (mainImageSource.get(item.id) === imageId) mainImageSource.delete(item.id)
    if (!hadDedicatedMain) item.thumbnailUri = item.images[0] ?? ''
    return HttpResponse.json({ id: imageId, file_uri: removedUri, action: 'delete' })
  }),

  // List voices for an item
  http.get('/api/v1/content/categories/:category_id/items/:item_id/voices', ({ params }) => {
    const found = findCategoryAndItem(String(params.category_id), String(params.item_id))
    if (!found) return new HttpResponse(null, { status: 404 })
    const [, item] = found
    return HttpResponse.json({
      item_id: item.id,
      name: itemLocalizedName(item),
      voices: item.voices.map(toVoiceResponse),
    })
  }),

  // Add a voice to an item
  http.post('/api/v1/content/categories/:category_id/items/:item_id/voices', async ({ params, request }) => {
    const found = findCategoryAndItem(String(params.category_id), String(params.item_id))
    if (!found) return new HttpResponse(null, { status: 404 })
    const [category, item] = found
    const form = await request.formData()
    const language = String(form.get('language') ?? '')
    const voice = item.voices.find((entry) => entry.language === language)
    if (!voice) return new HttpResponse(null, { status: 404 })
    voice.fileUri = `/content/${category.id}/${item.id}/${item.id}_${language}.mp3`
    return HttpResponse.json({ ...toVoiceResponse(voice), action: 'add' }, { status: 201 })
  }),

  // Update voice for an item
  http.put('/api/v1/content/categories/:category_id/items/:item_id/voices/:voice_id', async ({ params, request }) => {
    const found = findCategoryAndItem(String(params.category_id), String(params.item_id))
    if (!found) return new HttpResponse(null, { status: 404 })
    const [category, item] = found
    const voice = item.voices.find((entry) => entry.id === String(params.voice_id))
    if (!voice) return new HttpResponse(null, { status: 404 })
    await request.formData()
    voice.fileUri = `/content/${category.id}/${item.id}/${voice.id}.mp3`
    return HttpResponse.json({ ...toVoiceResponse(voice), action: 'update' })
  }),

  // Delete a voice from an item
  http.delete('/api/v1/content/categories/:category_id/items/:item_id/voices/:voice_id', ({ params }) => {
    const found = findCategoryAndItem(String(params.category_id), String(params.item_id))
    if (!found) return new HttpResponse(null, { status: 404 })
    const [, item] = found
    const voice = item.voices.find((entry) => entry.id === String(params.voice_id))
    if (!voice) return new HttpResponse(null, { status: 404 })
    voice.fileUri = ''
    return HttpResponse.json({ ...toVoiceResponse(voice), action: 'delete' })
  }),

  // Create a new item in a category (translations, an optional image, and opt-in voice/image generation)
  http.post('/api/v1/content/categories/:category_id/items', async ({ params, request }) => {
    const category = findCategoryById(String(params.category_id))
    if (!category) return HttpResponse.json({ detail: 'Category not found' }, { status: 404 })
    const form = await request.formData()
    const word = String(form.get('item') ?? '').trim()
    if (!word) return HttpResponse.json({ detail: 'Item name is required' }, { status: 400 })
    const itemId = itemIdFromEnglishWord(word)
    if (category.items.some((item) => item.id === itemId)) {
      return HttpResponse.json({ detail: `Item id already exists: ${itemId}` }, { status: 409 })
    }
    const image = form.get('image')
    const uploaded = image instanceof File && image.size > 0
    const generateVoices = form.get('generate_voices') === 'true'
    const generateImage = form.get('generate_image') === 'true'
    if (uploaded && generateImage) {
      return HttpResponse.json({ detail: 'Upload an image or generate one, not both.' }, { status: 400 })
    }
    const imageUrl = uploaded ? URL.createObjectURL(image) : ''
    const folder = slugify(word)
    category.items.push({
      id: itemId,
      name: word,
      images: imageUrl ? [imageUrl] : [],
      thumbnailUri: imageUrl,
      voices: (['en', 'es', 'fr', 'bg'] as const).map((lang) => ({
        id: `${folder}-v-${lang}`,
        name: word,
        language: lang,
        type: lang === 'en' || lang === 'fr' ? 'male' : 'female',
        fileUri: '',
        provider: 'unknown',
        parameters: {},
      })),
    })
    // Mock mode has no voice or image generators, so a requested step gets the backend's warning.
    return HttpResponse.json(
      {
        category: slugify(category.name['en'] ?? ''),
        item: word,
        folder,
        image_id: itemId,
        item_id: itemId,
        voices_generated: [],
        voice_warnings: generateVoices ? ['No active voice generators configured; voices were not generated.'] : [],
        image_generated: false,
        image_warnings: generateImage ? ['No active image generators configured; the image was not generated.'] : [],
        message: `Item '${word}' created.`,
      },
      { status: 201 },
    )
  }),

  // Regenerate a voice for an item
  http.post('/api/v1/content/categories/:category_id/items/:item_id/voices/:voice_id/regenerate', () => HttpResponse.json({})),

  // Regenerate a voice for a category
  http.post('/api/v1/content/categories/:category_id/voices/:voice_id/regenerate', () => HttpResponse.json({})),

  // Set an image file variant as the item's main image
  http.put('/api/v1/content/categories/:category_id/items/:item_id/images/:image_id/main', ({ params }) => {
    const found = findCategoryAndItem(String(params.category_id), String(params.item_id))
    if (!found) return new HttpResponse(null, { status: 404 })
    const [category, item] = found
    const imageIndex = itemImageIndex(item, String(params.image_id))
    if (imageIndex < 0) return new HttpResponse(null, { status: 404 })

    // The chosen variant is COPIED over the main image: it stays in the variants list
    // and the item keeps every image it had (matches the backend). Legacy items may
    // have their thumbnail pointing at a variant, so selecting one must create the
    // dedicated unindexed main URI before updating the selection.
    mainImageSource.set(item.id, String(params.image_id))
    if (!itemHasDedicatedMain(item)) {
      item.thumbnailUri = `/content/${category.id}/${item.id}/${item.id}.png`
    }

    return HttpResponse.json({
      item_id: item.id,
      name: itemLocalizedName(item),
      images: item.images.map((uri, index) => ({ id: itemImageId(item, index), file_uri: uri })),
      thumbnail_uri: item.thumbnailUri,
      main_image_id: itemMainImageId(item),
    })
  }),

  // Set a voice variant as the item's main voice for a language
  http.put('/api/v1/content/categories/:category_id/items/:item_id/voices/:voice_id/main', () => HttpResponse.json({})),

  // Create optimized versions of an item voice
  http.post('/api/v1/content/categories/:category_id/items/:item_id/voices/:voice_id/optimize', () => HttpResponse.json({})),

  // Generate a new voice for an item
  http.post('/api/v1/content/categories/:category_id/items/:item_id/voices/generate', () => HttpResponse.json({}, { status: 201 })),

  // Generate an image variant for an item from a text prompt
  http.post('/api/v1/content/categories/:category_id/items/:item_id/images/generate', async ({ params, request }) => {
    const found = findCategoryAndItem(String(params.category_id), String(params.item_id))
    if (!found) return new HttpResponse(null, { status: 404 })
    const [category, item] = found
    const body = await request.json() as { prompt?: string; configuration_name?: string | null }
    const prompt = body.prompt?.trim()
    if (!prompt) return HttpResponse.json({ detail: 'Prompt is required' }, { status: 400 })
    const configurationName = body.configuration_name?.trim() || null
    // Recorded like the backend's sidecar: the configuration's settings plus the prompt.
    const metadata: ImageMetadata = {
      provider: configurationName ?? 'openai',
      parameters: {
        model: 'gpt-image-1-mini', size: '1024x1024', quality: 'low', output_format: 'png',
        _prompt: prompt, date: new Date().toISOString(),
        ...(configurationName ? { configuration_name: configurationName } : {}),
      },
    }
    return HttpResponse.json(addMockItemImage(category, item, metadata), { status: 201 })
  }),
]
