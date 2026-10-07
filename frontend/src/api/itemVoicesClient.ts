import client from './client'
import { itemsVoicesGenerate, itemsVoicesSetMain } from './contentClient'
import { normalizeItemVoices } from '../utils/itemVoices'
import type { GenerateItemVoice, ItemVoiceList, ItemVoiceListResponse, ItemVoiceRecord, VoicePreset } from '../types/itemVoices'
import type { GenerateItemVoiceResponse } from '../types/contentSchemas'
import type { Language } from '../types/viewModels'
import { getServiceConfig, listServiceConfigs } from './configurationClient'
import { orderConfigurationFiles } from '../utils/configurationNavigation'
import { clampSpeed, defaultTtsConfiguration, speedRange, ttsConfigurationOption } from '../utils/ttsConfigurations'

const path = (categoryId: string, itemId: string): string =>
  `/content/categories/${encodeURIComponent(categoryId)}/items/${encodeURIComponent(itemId)}/voices`

export async function listItemVoices(categoryId: string, itemId: string, signal?: AbortSignal): Promise<ItemVoiceList> {
  return normalizeItemVoices((await client.get<ItemVoiceListResponse>(path(categoryId, itemId), { signal })).data)
}

export async function uploadItemVoice(categoryId: string, itemId: string, language: Language, file: File, voiceId?: string): Promise<ItemVoiceRecord> {
  const body = new FormData()
  body.append('audio', file)
  if (!voiceId) body.append('language', language)
  const url = path(categoryId, itemId)
  return voiceId
    ? (await client.put<ItemVoiceRecord>(`${url}/${encodeURIComponent(voiceId)}`, body)).data
    : (await client.post<ItemVoiceRecord>(url, body)).data
}

export async function deleteItemVoice(categoryId: string, itemId: string, voiceId: string): Promise<ItemVoiceRecord> {
  return (await client.delete<ItemVoiceRecord>(`${path(categoryId, itemId)}/${encodeURIComponent(voiceId)}`)).data
}

// The presets endpoint is proposed: it is called ONLY when the list response advertises
// its capability, and is implemented by frontend MSW fixtures, not by the backend.
export async function listVoicePresets(categoryId: string, itemId: string, language: Language, signal?: AbortSignal): Promise<VoicePreset[]> {
  return (await client.get<VoicePreset[]>(`${path(categoryId, itemId)}/presets`, { params: { language }, signal })).data
}

/** Adds a new take of the language's voice; the voice currently in use stays selected. */
export async function generateItemVoice(categoryId: string, itemId: string, body: GenerateItemVoice): Promise<GenerateItemVoiceResponse> {
  return itemsVoicesGenerate(encodeURIComponent(categoryId), encodeURIComponent(itemId), body)
}

export async function optimizeItemVoice(categoryId: string, itemId: string, voiceId: string): Promise<ItemVoiceRecord> {
  return (await client.post<ItemVoiceRecord>(`${path(categoryId, itemId)}/${encodeURIComponent(voiceId)}/optimize`)).data
}

export async function selectItemVoice(categoryId: string, itemId: string, voiceId: string) {
  return itemsVoicesSetMain(encodeURIComponent(categoryId), encodeURIComponent(itemId), encodeURIComponent(voiceId))
}

/**
 * Generates a new take of the language's voice with the default text-to-speech configuration
 * and its own voice settings, then makes it the language's main voice. This is the generate
 * dialog's flow without the dialog; the new take stays in the item's voices either way.
 */
export async function generateMainItemVoice(categoryId: string, itemId: string, language: Language, text: string): Promise<GenerateItemVoiceResponse> {
  const summaries = orderConfigurationFiles(await listServiceConfigs('text_to_speech')).filter((summary) => summary.valid_json)
  const files = await Promise.all(summaries.map((summary) => getServiceConfig('text_to_speech', summary.config_name)))
  const configuration = defaultTtsConfiguration(files
    .map((file) => ttsConfigurationOption(file, language))
    .filter((option) => option !== null))
  if (!configuration) throw new Error(`No text to speech configuration supports ${language.toUpperCase()}.`)

  const generated = await generateItemVoice(categoryId, itemId, {
    language,
    text: text.trim(),
    configuration_name: configuration.config_name,
    speed: clampSpeed(configuration.speed, speedRange(configuration.provider)),
    stability: configuration.stability,
    similarity: configuration.similarity,
    optimize: false,
  })
  await selectItemVoice(categoryId, itemId, generated.voice_id)
  return generated
}
