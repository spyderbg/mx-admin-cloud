import { delay, http, HttpResponse } from 'msw'
import { mockCategories } from '../data/mockData'
import type { GenerateItemVoice, ItemVoiceList, ItemVoiceListResponse, ItemVoiceRecord, VoicePreset } from '../types/itemVoices'
import { SUPPORTED_LANGUAGES, type Language } from '../types/viewModels'

// Frontend-only contract preview. No synthesis/optimization or disk writes occur.
const base = '/api/v1/content/categories/:category_id/items/:item_id/voices'
const records = new Map<string, ItemVoiceList>()
const uploadedUrls = new Set<string>()
const capabilities = { multiple_voices: true, generate: true, optimize: true, select: true, presets: true, upload_formats: ['mp3', 'wav'] }

function presets(language: Language): VoicePreset[] {
  return ['Milena', 'Sofia'].map((name) => ({
    id: `${language}-${name.toLowerCase()}`, provider: 'elevenlabs', name, language,
    model: 'eleven_multilingual_v2', speed: 1, stability: .5, similarity: .75,
  }))
}

function getList(categoryId: string, itemId: string): ItemVoiceList | undefined {
  const category = mockCategories.find((entry) => entry.id === categoryId)
  const item = category?.items.find((entry) => entry.id === itemId)
  if (!item) return undefined
  const key = `${categoryId}/${itemId}`
  const name = Object.fromEntries(SUPPORTED_LANGUAGES.map((language) => [language, item.voices.find((voice) => voice.language === language)?.name || item.name]))
  const existing = records.get(key)
  if (existing) { existing.name = name; return existing }
  const voices: ItemVoiceRecord[] = item.voices.map((voice) => ({
    id: voice.id, language: voice.language, type: 'tts', file_uri: voice.fileUri,
    display_name: 'Milena', provider: 'elevenlabs', creation_method: 'generated',
    is_selected: !!voice.fileUri, selected_version: 'original',
    parameters: { actor: 'Milena', model: 'eleven_multilingual_v2', speed: 1, _text: name[voice.language], voice_settings: { stability: .5, similarity_boost: .75 }, date: '2026-09-28T08:00:00Z' },
  }))
  const english = voices.find((voice) => voice.language === 'en' && voice.file_uri)
  if (english) {
    // Mock audio is intentionally reused; the UI explicitly labels this simulation.
    english.optimized_file_uri = `${english.file_uri}?preview=optimized`
    english.optimized_voice_id = `${english.id}-optimized`
    english.selected_version = 'optimized'
    voices.push({ ...english, id: `${english.id}-take-2`, display_name: 'Milena · Take 2', optimized_file_uri: null, optimized_voice_id: null, is_selected: false, selected_version: 'original', parameters: { ...english.parameters, speed: .9 } })
    voices.push({ ...english, id: `${english.id}-manual`, display_name: 'Manual recording', provider: 'custom', creation_method: 'uploaded', optimized_file_uri: null, optimized_voice_id: null, is_selected: false, selected_version: 'original', parameters: { date: '2026-09-28T09:00:00Z' } })
  }
  const list = { item_id: itemId, name, voices, capabilities }
  records.set(key, list)
  return list
}

// Match the real list/main endpoint contract; inline optimized previews also
// carry an explicit target ID so selection never silently selects the original.
function listResponse(list: ItemVoiceList): ItemVoiceListResponse {
  const selected = list.voices.filter((voice) => voice.is_selected && voice.file_uri)
  return {
    ...list,
    voices: selected.map((voice) => ({ ...voice, file_uri: voice.selected_version === 'optimized' ? voice.optimized_file_uri : voice.file_uri })),
    variants: list.voices,
    main_voice_ids: Object.fromEntries(SUPPORTED_LANGUAGES.map((language) => {
      const voice = selected.find((entry) => entry.language === language)
      return [language, voice ? (voice.selected_version === 'optimized' ? voice.optimized_voice_id : voice.id) || null : null]
    })),
  }
}

const error = (detail: string, status = 400) => HttpResponse.json({ detail }, { status })
const findList = (params: Record<string, string | readonly string[] | undefined>) => getList(String(params.category_id), String(params.item_id))

async function uploadedFile(request: Request): Promise<{ file: File; language: string } | null> {
  const body = await request.formData()
  const file = body.get('audio')
  return file instanceof File && file.size ? { file, language: String(body.get('language') || '') } : null
}

function replaceFile(voice: ItemVoiceRecord, file: File): void {
  if (voice.file_uri && uploadedUrls.delete(voice.file_uri)) URL.revokeObjectURL(voice.file_uri)
  const url = URL.createObjectURL(file)
  uploadedUrls.add(url)
  voice.file_uri = url
  voice.optimized_file_uri = null
  voice.optimized_voice_id = null
  voice.selected_version = 'original'
  voice.creation_method = 'uploaded'
  voice.provider = 'custom'
  voice.display_name = file.name.replace(/\.[^.]+$/, '')
  voice.parameters = { filename: file.name, date: new Date().toISOString() }
}

export const itemVoiceHandlers = [
  http.get(base, ({ params }) => {
    const list = findList(params)
    return list ? HttpResponse.json(listResponse(list)) : error('Item not found.', 404)
  }),
  http.get(`${base}/presets`, ({ params, request }) => {
    if (!findList(params)) return error('Item not found.', 404)
    const language = new URL(request.url).searchParams.get('language') as Language
    return SUPPORTED_LANGUAGES.includes(language) ? HttpResponse.json(presets(language)) : error('Unsupported language.')
  }),
  http.post(`${base}/generate`, async ({ params, request }) => {
    const list = findList(params)
    if (!list) return error('Item not found.', 404)
    const body = await request.json() as GenerateItemVoice
    if (!SUPPORTED_LANGUAGES.includes(body.language) || !body.text.trim()) return error('Language and text are required.')
    const preset = presets(body.language).find((entry) => entry.id === body.preset_id)
    if (!preset) return error('Base voice not found.', 404)
    const source = list.voices.find((voice) => voice.language === body.language && voice.file_uri) || list.voices.find((voice) => voice.file_uri)
    if (!source?.file_uri) return error('No sample audio is available for this preview.')
    await delay(450)
    const voice: ItemVoiceRecord = {
      id: crypto.randomUUID(), language: body.language, type: 'tts', display_name: `${preset.name} · ${list.voices.filter((voice) => voice.language === body.language && voice.file_uri).length + 1}`,
      provider: preset.provider, creation_method: 'generated', is_selected: false, selected_version: 'original', file_uri: source.file_uri,
      optimized_file_uri: body.optimize ? source.file_uri : null,
      parameters: { actor: preset.name, model: preset.model, speed: body.speed, _text: body.text, voice_settings: { stability: body.stability, similarity_boost: body.similarity }, date: new Date().toISOString() },
    }
    if (voice.optimized_file_uri) voice.optimized_voice_id = `${voice.id}-optimized`
    list.voices.push(voice)
    return HttpResponse.json(voice, { status: 201 })
  }),
  http.post(base, async ({ params, request }) => {
    const list = findList(params)
    if (!list) return error('Item not found.', 404)
    const upload = await uploadedFile(request)
    if (!upload || !SUPPORTED_LANGUAGES.includes(upload.language as Language)) return error('Language and audio are required.')
    const voice: ItemVoiceRecord = { id: crypto.randomUUID(), language: upload.language as Language, type: 'tts', is_selected: false }
    replaceFile(voice, upload.file)
    list.voices.push(voice)
    return HttpResponse.json({ ...voice, action: 'add' }, { status: 201 })
  }),
  http.put(`${base}/:voice_id`, async ({ params, request }) => {
    const voice = findList(params)?.voices.find((entry) => entry.id === params.voice_id)
    if (!voice?.file_uri) return error('Voice not found.', 404)
    const upload = await uploadedFile(request)
    if (!upload) return error('Audio is required.')
    replaceFile(voice, upload.file)
    return HttpResponse.json({ ...voice, action: 'update' })
  }),
  http.post(`${base}/:voice_id/optimize`, async ({ params }) => {
    const voice = findList(params)?.voices.find((entry) => entry.id === params.voice_id)
    if (!voice?.file_uri) return error('Voice not found.', 404)
    await delay(350)
    voice.optimized_voice_id = `${voice.id}-optimized`
    voice.optimized_file_uri = voice.file_uri.startsWith('blob:') ? voice.file_uri : `${voice.file_uri.split('?')[0]}?preview=optimized`
    return HttpResponse.json(voice)
  }),
  http.put(`${base}/:voice_id/main`, async ({ params, request }) => {
    const list = findList(params)
    const voice = list?.voices.find((entry) => entry.id === params.voice_id || entry.optimized_voice_id === params.voice_id)
    if (!list || !voice?.file_uri) return error('Voice not found.', 404)
    const { language } = await request.json() as { language: string }
    if (language !== voice.language) return error('Voice not found for this language.', 404)
    const version = params.voice_id === voice.optimized_voice_id ? 'optimized' : 'original'
    if (version === 'optimized' && !voice.optimized_file_uri) return error('No optimized audio available.')
    list.voices.filter((entry) => entry.language === language).forEach((entry) => { entry.is_selected = entry.id === voice.id })
    voice.selected_version = version
    return HttpResponse.json(listResponse(list))
  }),
  http.delete(`${base}/:voice_id`, ({ params }) => {
    const list = findList(params)
    const voice = list?.voices.find((entry) => entry.id === params.voice_id)
    if (!list || !voice) return error('Voice not found.', 404)
    if (voice.file_uri && uploadedUrls.delete(voice.file_uri)) URL.revokeObjectURL(voice.file_uri)
    list.voices = list.voices.filter((entry) => entry.id !== voice.id)
    return HttpResponse.json({ ...voice, action: 'delete' })
  }),
  http.post(`${base}/:voice_id/regenerate`, ({ params }) => {
    const voice = findList(params)?.voices.find((entry) => entry.id === params.voice_id)
    if (!voice) return error('Voice not found.', 404)
    voice.parameters = { ...voice.parameters, date: new Date().toISOString() }
    voice.optimized_file_uri = null
    voice.selected_version = 'original'
    return HttpResponse.json({ ...voice, action: 'regenerate' })
  }),
]
