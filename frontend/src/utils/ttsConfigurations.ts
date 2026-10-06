import type { ConfigurationFileResponse } from '../types/configurationSchemas'
import type { Language } from '../types/viewModels'

export interface TtsConfigurationOption {
  config_name: string
  provider: string
  actor: string
  model: string
  enabled: boolean
  speed: number
  stability?: number
  similarity?: number
}

function object(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown> : {}
}

/** Read the selected language's defaults without changing the saved configuration. */
export function ttsConfigurationOption(file: ConfigurationFileResponse, language: Language): TtsConfigurationOption | null {
  const envelope = object(file.payload.service_provider)
  const config = object(envelope.config)
  const generate = object(config.generate)
  if (typeof envelope.provider !== 'string' || !Object.keys(generate).length) return null
  const languages = object(generate.languages)
  if (generate.languages !== undefined && !Object.hasOwn(languages, language)) return null
  const settings = { ...generate, ...object(languages[language]) }
  const voiceSettings = object(settings.voice_settings)
  const speed = settings.speed ?? settings.speed_rate
  return {
    config_name: file.config_name,
    provider: envelope.provider,
    actor: String(settings.actor ?? settings.voice ?? settings.voice_name ?? envelope.provider),
    model: typeof settings.model === 'string' ? settings.model : '',
    enabled: envelope.enabled === true,
    speed: typeof speed === 'number' && Number.isFinite(speed) ? speed : 1,
    stability: typeof voiceSettings.stability === 'number' ? voiceSettings.stability : undefined,
    similarity: typeof voiceSettings.similarity_boost === 'number' ? voiceSettings.similarity_boost : undefined,
  }
}

export interface SpeedRange { min: number; max: number }

// Speeds each provider's API accepts; values outside them fail the request. The static
// provider synthesizes through ElevenLabs.
const providerSpeedRanges: Record<string, SpeedRange> = {
  elevenlabs: { min: .7, max: 1.2 },
  static: { min: .7, max: 1.2 },
  openai: { min: .25, max: 4 },
}

export function speedRange(provider: string | undefined): SpeedRange {
  return providerSpeedRanges[provider ?? ''] ?? { min: .5, max: 2 }
}

export function clampSpeed(speed: number, range: SpeedRange): number {
  return Math.min(range.max, Math.max(range.min, speed))
}
