import assert from 'node:assert/strict'
import { test } from 'node:test'
import { selectedVoice, voiceAudioUri, validateAudioFile, voiceSource, audioFileName, normalizeItemVoices, voiceSelectionId, voiceGenerationText } from '../src/utils/itemVoices.ts'
import { legacyVoiceCapabilities, type ItemVoiceRecord } from '../src/types/itemVoices.ts'
import { cacheBustedUri } from '../src/utils/cacheBustedUri.ts'
import { langLabel } from '../src/utils/voiceLabels.ts'
import { ttsConfigurationOption } from '../src/utils/ttsConfigurations.ts'
import type { ConfigurationFileResponse } from '../src/types/configurationSchemas'

test('TTS configuration selection uses language defaults and retains the configuration identity', () => {
  const file: ConfigurationFileResponse = {
    config_name: 'elevenlabs_slow', filename: 'elevenlabs_slow.json',
    payload: { service_provider: { provider: 'elevenlabs', enabled: false, config: { generate: {
      model: 'shared-model', speed: 1, languages: {
        en: { actor: 'Milena' },
        bg: { actor: 'Milena', model: 'bg-model', speed: .7, voice_settings: { stability: .6, similarity_boost: .7 } },
      },
    } } } },
  }
  assert.deepEqual(ttsConfigurationOption(file, 'bg'), {
    config_name: 'elevenlabs_slow', provider: 'elevenlabs', actor: 'Milena', model: 'bg-model',
    enabled: false, speed: .7, stability: .6, similarity: .7,
  })
  assert.equal(ttsConfigurationOption(file, 'en')?.model, 'shared-model')
  assert.equal(ttsConfigurationOption(file, 'fr'), null)
  assert.equal(file.payload.service_provider.enabled, false)
})

test('TTS options reject malformed configurations and handle provider speed defaults', () => {
  const file: ConfigurationFileResponse = { config_name: 'speaktor', filename: 'speaktor.json', payload: {
    service_provider: { provider: 'speaktor', enabled: true, config: { generate: { languages: { en: { voice_name: 'Sveta', speed_rate: .8 } } } } },
  } }
  assert.equal(ttsConfigurationOption(file, 'en')?.speed, .8)
  assert.equal(ttsConfigurationOption(file, 'en')?.actor, 'Sveta')
  assert.equal(ttsConfigurationOption({ ...file, payload: {} }, 'en'), null)
})

const voice = (overrides: Partial<ItemVoiceRecord> = {}): ItemVoiceRecord => ({
  id: 'en-1', language: 'en', type: 'tts', file_uri: '/content/en.mp3', ...overrides,
})
const multi = { ...legacyVoiceCapabilities, multiple_voices: true, select: true }

test('language names follow the globally selected display language', () => {
  assert.deepEqual(['en', 'es', 'fr', 'bg'].map((lang) => langLabel(lang as 'en' | 'es' | 'fr' | 'bg', 'en')), ['English', 'Spanish', 'French', 'Bulgarian'])
  assert.deepEqual(['en', 'es', 'fr', 'bg'].map((lang) => langLabel(lang as 'en' | 'es' | 'fr' | 'bg', 'es')), ['Inglés', 'Español', 'Francés', 'Búlgaro'])
  assert.deepEqual(['en', 'es', 'fr', 'bg'].map((lang) => langLabel(lang as 'en' | 'es' | 'fr' | 'bg', 'fr')), ['Anglais', 'Espagnol', 'Français', 'Bulgare'])
  assert.deepEqual(['en', 'es', 'fr', 'bg'].map((lang) => langLabel(lang as 'en' | 'es' | 'fr' | 'bg', 'bg')), ['Английски', 'Испански', 'Френски', 'Български'])
})

test('a legacy recording is already the active item voice, while an empty language is not', () => {
  const current = voice()
  assert.equal(selectedVoice([current], legacyVoiceCapabilities), current)
  assert.equal(selectedVoice([voice({ file_uri: null })], legacyVoiceCapabilities), undefined)
  assert.equal(selectedVoice([], legacyVoiceCapabilities), undefined)
})

test('new candidates never become selected by array order or by having optimized audio', () => {
  const current = voice({ is_selected: true })
  const newTake = voice({ id: 'en-2', is_selected: false, optimized_file_uri: '/content/en-2-optimized.mp3' })
  assert.equal(selectedVoice([newTake, current], multi), current)
  assert.equal(selectedVoice([newTake], multi), undefined)
  assert.equal(selectedVoice([voice({ is_selected: false })], legacyVoiceCapabilities), undefined)
  assert.equal(selectedVoice([voice()], multi), undefined)
})

test('an optimized preview requires the actual optimized URL; original audio is never substituted', () => {
  const current = voice()
  assert.equal(voiceAudioUri(current, 'optimized'), '')
  assert.equal(voiceAudioUri(current, 'original'), '/content/en.mp3')
  const optimized = voice({ optimized_file_uri: '/content/processed/abc.wav' })
  assert.equal(voiceAudioUri(optimized, 'optimized'), '/content/processed/abc.wav')
  assert.equal(selectedVoice([voice({ is_selected: true, selected_version: 'optimized' })], multi), undefined)
})

test('upload validation follows server format capabilities and rejects empty/non-audio files', () => {
  assert.equal(validateAudioFile(new File(['audio'], 'take.MP3', { type: 'audio/mpeg' }), ['mp3']), null)
  assert.equal(validateAudioFile(new File(['audio'], 'take.wav'), ['mp3']), 'format')
  assert.equal(validateAudioFile(new File(['audio'], 'take.wav'), ['mp3', 'wav']), null)
  assert.equal(validateAudioFile(new File([], 'empty.mp3'), ['mp3']), 'empty')
  assert.equal(validateAudioFile(new File(['image'], 'photo.png'), ['mp3', 'wav']), 'format')
})

test('manual/unknown recordings do not acquire fabricated generation metadata', () => {
  assert.equal(voiceSource(voice({ provider: 'custom' })), 'uploaded')
  assert.equal(voiceSource(voice({ provider: 'unknown' })), 'unknown')
  assert.equal(voiceSource(voice({ provider: 'elevenlabs' })), 'generated')
  assert.equal(voiceSource(voice({ provider: 'elevenlabs', creation_method: 'uploaded' })), 'uploaded')
})

test('each generated voice exposes only its own stored synthesis text', () => {
  assert.equal(voiceGenerationText(voice({ parameters: { _text: '  yellow bananas  ' } })), 'yellow bananas')
  assert.equal(voiceGenerationText(voice({ parameters: { _text: 42 } })), '')
  assert.equal(voiceGenerationText(voice()), '')
})

test('cache busting refreshes real files and leaves uploaded preview URLs playable', () => {
  assert.equal(cacheBustedUri('/audio.mp3?format=mp3', 2), '/audio.mp3?format=mp3&v=2')
  assert.equal(cacheBustedUri('blob:http://localhost/123', 2), 'blob:http://localhost/123')
  assert.equal(cacheBustedUri('data:audio/wav;base64,AAAA', 2), 'data:audio/wav;base64,AAAA')
  assert.equal(audioFileName('/content/my%20voice.mp3?v=1'), 'my voice.mp3')
  assert.equal(audioFileName('/content/bad%encoding.mp3'), 'bad%encoding.mp3')
})


test('standalone main recordings remain selected alongside alternatives, without selecting by order', () => {
  const response = {
    item_id: 'item', name: { en: 'bananas' },
    voices: [voice({ id: 'main' }), voice({ id: 'es-main', language: 'es', file_uri: null })],
    variants: [voice({ id: 'candidate' }), voice({ id: 'es-candidate', language: 'es' })],
    main_voice_ids: { en: null, es: null },
  }
  const normalized = normalizeItemVoices(response)
  assert.deepEqual(normalized.voices.map(({ id, is_selected }) => [id, is_selected]), [
    ['candidate', false], ['es-candidate', false], ['main', true],
  ])
  assert.equal(response.variants[0].is_selected, undefined)
  assert.equal(normalized.capabilities?.select, true)
  assert.equal(normalized.capabilities?.multiple_voices, undefined)
})

test('selection respects language mappings and keeps optimized variants addressable by their actual ID', () => {
  const optimized = voice({ id: 'en-optimized', parameters: { _source: 'en-1', _optimizer: 'ffmpeg' } })
  const normalized = normalizeItemVoices({
    item_id: 'item', name: {}, voices: [],
    variants: [voice(), optimized, voice({ id: 'es-1', language: 'es' })],
    main_voice_ids: { en: optimized.id, es: 'es-1' },
  })
  assert.deepEqual(normalized.voices.filter((entry) => entry.is_selected).map((entry) => entry.id), ['en-optimized', 'es-1'])
  assert.equal(voiceSelectionId(normalized.voices[1], 'original'), 'en-optimized')
  assert.equal(voiceSelectionId(voice({ optimized_file_uri: '/optimized.mp3' }), 'optimized'), undefined)
  assert.equal(voiceSelectionId(voice({ optimized_file_uri: '/optimized.mp3', optimized_voice_id: 'optimized-id' }), 'optimized'), 'optimized-id')
  assert.equal(voiceSelectionId(voice({ file_uri: null }), 'original'), undefined)
})
