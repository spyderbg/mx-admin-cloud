import type { ItemVoiceRecord, ItemVoiceList, ItemVoiceListResponse, VoiceCapabilities, AudioVersion } from '../types/itemVoices'

export function normalizeItemVoices(response: ItemVoiceListResponse): ItemVoiceList {
  if (!response.variants || !response.main_voice_ids) {
    return { ...response, voices: response.voices || [] }
  }
  const mainIds = response.main_voice_ids
  const voices = response.variants.map((voice) => {
    const optimized = !!voice.optimized_voice_id && mainIds[voice.language] === voice.optimized_voice_id
    return {
      ...voice,
      is_selected: mainIds[voice.language] === voice.id || optimized,
      selected_version: optimized ? 'optimized' as const : 'original' as const,
    }
  })
  // A standalone main file may have no matching variant. Keep that recording
  // visible and selected, but avoid duplicating a main file copied from a variant.
  for (const main of response.voices || []) {
    if (main.file_uri && !voices.some((voice) => voice.language === main.language && voice.is_selected)) {
      const existing = voices.find((voice) => voice.id === main.id && voice.language === main.language)
      if (existing) existing.is_selected = true
      else voices.push({ ...main, is_selected: true, selected_version: 'original' })
    }
  }
  return { ...response, voices, capabilities: { select: true, ...response.capabilities } }
}

export function voiceSelectionId(voice: ItemVoiceRecord, version: AudioVersion): string | undefined {
  if (!voiceAudioUri(voice, version)) return undefined
  return version === 'optimized' ? voice.optimized_voice_id || undefined : voice.id
}

export function voiceTitle(voice: ItemVoiceRecord): string {
  const actor = voice.parameters?.actor ?? voice.parameters?.voice
  return voice.display_name || (typeof actor === 'string' ? actor : '') || voice.id
}

export function voiceGenerationText(voice: ItemVoiceRecord): string {
  const text = voice.parameters?._text
  return typeof text === 'string' ? text.trim() : ''
}

export function voiceSource(voice: ItemVoiceRecord): 'generated' | 'uploaded' | 'unknown' {
  if (voice.creation_method) return voice.creation_method
  if (voice.provider === 'custom') return 'uploaded'
  return voice.provider && voice.provider !== 'unknown' ? 'generated' : 'unknown'
}

export function selectedVoice(voices: ItemVoiceRecord[], capabilities: VoiceCapabilities): ItemVoiceRecord | undefined {
  const recorded = voices.find((voice) => voice.is_selected && voiceAudioUri(voice, voice.selected_version || 'original'))
  if (recorded) return recorded
  // On legacy servers the sole file IS the item's published recording. A future
  // multi-candidate response must explicitly identify its selection.
  return !capabilities.multiple_voices && voices.length === 1 && voices[0].is_selected === undefined && voices[0].file_uri
    ? voices[0] : undefined
}

export function voiceAudioUri(voice: ItemVoiceRecord, version: AudioVersion): string {
  return (version === 'optimized' ? voice.optimized_file_uri : voice.file_uri) || ''
}

export function audioFileName(uri?: string | null): string {
  const name = uri?.split(/[?#]/)[0].split('/').pop() ?? ''
  try { return decodeURIComponent(name) } catch { return name }
}

export function validateAudioFile(file: File, formats: string[]): 'empty' | 'format' | null {
  if (!file.size) return 'empty'
  const extension = file.name.split('.').pop()?.toLowerCase()
  return extension && formats.includes(extension) ? null : 'format'
}

export function voiceErrorMessage(error: unknown): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  return typeof detail === 'string' ? detail : ''
}
