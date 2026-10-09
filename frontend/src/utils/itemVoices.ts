import type { ItemVoiceRecord, ItemVoiceList, ItemVoiceListResponse, OptimizedVoiceVersion, VoiceCapabilities, AudioVersion } from '../types/itemVoices'

export function normalizeItemVoices(response: ItemVoiceListResponse): ItemVoiceList {
  if (!response.variants || !response.main_voice_ids) {
    return { ...response, voices: response.voices || [] }
  }
  const mainIds = response.main_voice_ids
  // Optimizer output is stored as its own variant. Fold it into the recording it was
  // made from; one whose source is gone stays a card of its own.
  const optimizedBySource = new Map<string, ItemVoiceRecord[]>()
  const standalone = response.variants.filter((voice) => {
    const source = optimizedSourceId(voice)
    if (!source || !voice.file_uri || !response.variants!.some((other) => other.id === source && other.language === voice.language)) return true
    optimizedBySource.set(source, [...(optimizedBySource.get(source) || []), voice])
    return false
  })
  const voices = standalone.map((voice) => {
    const folded = (optimizedBySource.get(voice.id) || []).map(toOptimizedVersion)
    const versions = folded.length ? folded : optimizedVersions(voice)
    const chosen = versions.find((entry) => entry.id && entry.id === mainIds[voice.language])
    return {
      ...voice,
      ...(folded.length ? { optimized_versions: folded } : {}),
      ...(chosen ? { selected_optimized_id: chosen.id } : {}),
      is_selected: mainIds[voice.language] === voice.id || !!chosen,
      selected_version: chosen ? 'optimized' as const : 'original' as const,
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

function optimizedSourceId(voice: ItemVoiceRecord): string {
  const { _optimizer, _source } = voice.parameters || {}
  return _optimizer && typeof _source === 'string' ? _source : ''
}

function toOptimizedVersion(voice: ItemVoiceRecord): OptimizedVoiceVersion {
  const optimizer = voice.parameters?._optimizer
  return { id: voice.id, file_uri: voice.file_uri || '', ...(typeof optimizer === 'string' ? { optimizer } : {}) }
}

/** The optimized renditions of a recording, oldest first. */
export function optimizedVersions(voice: ItemVoiceRecord): OptimizedVoiceVersion[] {
  if (voice.optimized_versions?.length) return voice.optimized_versions
  return voice.optimized_file_uri && voice.optimized_voice_id
    ? [{ id: voice.optimized_voice_id, file_uri: voice.optimized_file_uri }]
    : voice.optimized_file_uri ? [{ id: '', file_uri: voice.optimized_file_uri }] : []
}

/** The id to send when making this recording (or one of its optimized renditions) the item's voice. */
export function voiceSelectionId(voice: ItemVoiceRecord, version: AudioVersion, optimizedId?: string): string | undefined {
  if (version === 'original') return voice.file_uri ? voice.id : undefined
  const versions = optimizedVersions(voice)
  return (optimizedId ? versions.find((entry) => entry.id === optimizedId) : versions[0])?.id || undefined
}

export function voiceTitle(voice: ItemVoiceRecord): string {
  return audioFileName(voice.file_uri).replace(/\.[^.]+$/, '') || voice.id
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
  return (version === 'optimized' ? optimizedVersions(voice)[0]?.file_uri : voice.file_uri) || ''
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

export function formatFileSize(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`
}

/** Average bitrate of an audio file, from its size and playing time. */
export function audioBitrateKbps(bytes: number, seconds: number): number | null {
  return bytes > 0 && seconds > 0 ? Math.round((bytes * 8) / seconds / 1000) : null
}

const fileSizes = new Map<string, Promise<number | null>>()

/** The size of a served audio file, or null when it can't be told (blob URLs, no Content-Length, errors). */
export function fetchAudioSize(uri: string): Promise<number | null> {
  if (!uri || uri.startsWith('blob:') || uri.startsWith('data:')) return Promise.resolve(null)
  let size = fileSizes.get(uri)
  if (!size) {
    size = fetch(uri, { method: 'HEAD' })
      .then((response) => {
        const length = Number(response.headers.get('content-length'))
        return response.ok && Number.isFinite(length) && length > 0 ? length : null
      })
      .catch(() => null)
    fileSizes.set(uri, size)
  }
  return size
}
