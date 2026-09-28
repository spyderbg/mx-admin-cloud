import client from './client'
import type { AudioVersion, GenerateItemVoice, ItemVoiceList, ItemVoiceRecord, VoicePreset } from '../types/itemVoices'
import type { Language } from '../types/viewModels'

const path = (categoryId: string, itemId: string): string =>
  `/content/categories/${encodeURIComponent(categoryId)}/items/${encodeURIComponent(itemId)}/voices`

export async function listItemVoices(categoryId: string, itemId: string, signal?: AbortSignal): Promise<ItemVoiceList> {
  return (await client.get<ItemVoiceList>(path(categoryId, itemId), { signal })).data
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

// Proposed endpoints are called ONLY when the list response advertises their
// capability. They are implemented by frontend MSW fixtures, not by the backend.
export async function listVoicePresets(categoryId: string, itemId: string, language: Language, signal?: AbortSignal): Promise<VoicePreset[]> {
  return (await client.get<VoicePreset[]>(`${path(categoryId, itemId)}/presets`, { params: { language }, signal })).data
}

export async function generateItemVoice(categoryId: string, itemId: string, body: GenerateItemVoice): Promise<ItemVoiceRecord> {
  return (await client.post<ItemVoiceRecord>(`${path(categoryId, itemId)}/generate`, body)).data
}

export async function optimizeItemVoice(categoryId: string, itemId: string, voiceId: string): Promise<ItemVoiceRecord> {
  return (await client.post<ItemVoiceRecord>(`${path(categoryId, itemId)}/${encodeURIComponent(voiceId)}/optimize`)).data
}

export async function selectItemVoice(categoryId: string, itemId: string, voiceId: string, version: AudioVersion): Promise<ItemVoiceRecord> {
  return (await client.put<ItemVoiceRecord>(`${path(categoryId, itemId)}/${encodeURIComponent(voiceId)}/selection`, { version })).data
}
