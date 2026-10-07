import type { Language } from '../types/viewModels'
import { clampSpeed, speedRange, type TtsConfigurationOption } from './ttsConfigurations.ts'

const STORAGE_KEY = 'mx-admin-generate-voice-settings-v1'
const languages: Language[] = ['en', 'es', 'fr', 'bg']
type PreferenceStorage = Pick<Storage, 'getItem' | 'setItem'>

export interface VoiceGenerationSettings {
  configurationName: string
  speed: number
  stability: number
  similarity: number
  autoOptimize: boolean
}

export interface VoiceGenerationPreferences {
  language?: Language
  settings: Partial<Record<Language, VoiceGenerationSettings>>
}

function finiteNumber(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value)
}

export function readVoiceGenerationPreferences(storage?: PreferenceStorage): VoiceGenerationPreferences {
  const preferences: VoiceGenerationPreferences = { settings: {} }
  try {
    const saved = JSON.parse((storage ?? localStorage).getItem(STORAGE_KEY) || 'null')
    if (!saved || typeof saved !== 'object') return preferences
    if (languages.includes(saved.language)) preferences.language = saved.language
    for (const language of languages) {
      const settings = saved.settings?.[language]
      if (!settings || typeof settings.configurationName !== 'string' || !settings.configurationName
        || !finiteNumber(settings.speed) || settings.speed <= 0
        || !finiteNumber(settings.stability) || settings.stability < 0 || settings.stability > 1
        || !finiteNumber(settings.similarity) || settings.similarity < 0 || settings.similarity > 1
        || typeof settings.autoOptimize !== 'boolean') continue
      preferences.settings[language] = {
        configurationName: settings.configurationName, speed: settings.speed,
        stability: settings.stability, similarity: settings.similarity, autoOptimize: settings.autoOptimize,
      }
    }
  } catch {
    // Invalid or unavailable browser storage must not prevent generating a voice.
  }
  return preferences
}

export function writeVoiceGenerationPreferences(preferences: VoiceGenerationPreferences, storage?: PreferenceStorage): void {
  try {
    (storage ?? localStorage).setItem(STORAGE_KEY, JSON.stringify(preferences))
  } catch {
    // The current popup settings still work when browser storage is unavailable.
  }
}

export function voiceGenerationSettingsFor(configuration: TtsConfigurationOption, saved?: VoiceGenerationSettings): VoiceGenerationSettings {
  const previous = saved?.configurationName === configuration.config_name ? saved : undefined
  return {
    configurationName: configuration.config_name,
    speed: clampSpeed(previous?.speed ?? configuration.speed, speedRange(configuration.provider)),
    stability: previous?.stability ?? configuration.stability ?? .5,
    similarity: previous?.similarity ?? configuration.similarity ?? .75,
    autoOptimize: saved?.autoOptimize ?? false,
  }
}
