import type { Language } from './viewModels'

export type AudioVersion = 'original' | 'optimized'

/** Optional additions to the existing voices response. See docs/item-voices-api.md. */
export interface ItemVoiceRecord {
  id: string
  language: Language
  type: string
  file_uri?: string | null
  optimized_file_uri?: string | null
  optimized_voice_id?: string | null
  display_name?: string
  creation_method?: 'generated' | 'uploaded' | 'unknown'
  provider?: string
  parameters?: Record<string, unknown>
  is_selected?: boolean
  selected_version?: AudioVersion
}

export interface VoiceCapabilities {
  multiple_voices: boolean
  generate: boolean
  optimize: boolean
  select: boolean
  presets: boolean
  upload_formats: string[]
}

export interface ItemVoiceList {
  item_id: string
  name: Partial<Record<Language, string>>
  voices: ItemVoiceRecord[]
  capabilities?: Partial<VoiceCapabilities>
}

/** Wire response: main recordings and selectable variants are separate lists. */
export interface ItemVoiceListResponse extends Omit<ItemVoiceList, 'voices'> {
  voices?: ItemVoiceRecord[]
  variants?: ItemVoiceRecord[]
  main_voice_ids?: Record<string, string | null>
}

export interface VoicePreset {
  id: string
  provider: string
  name: string
  model: string
  language: Language
  speed: number
  stability?: number
  similarity?: number
}

export interface GenerateItemVoice {
  language: Language
  text: string
  preset_id: string
  speed: number
  stability?: number
  similarity?: number
  optimize: boolean
}

// The current server supports one MP3 per language. Never assume an unavailable
// mutation is supported, or substitute a destructive regenerate for a new take.
export const legacyVoiceCapabilities: VoiceCapabilities = {
  multiple_voices: false, generate: false, optimize: false, select: false,
  presets: false, upload_formats: ['mp3'],
}
