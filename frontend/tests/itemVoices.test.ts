import assert from 'node:assert/strict'
import { test } from 'node:test'
import { selectedVoice, voiceAudioUri, validateAudioFile, voiceSource, audioFileName, normalizeItemVoices, optimizedVersions, formatFileSize, audioBitrateKbps, fetchAudioSize, voiceSelectionId, voiceGenerationText } from '../src/utils/itemVoices.ts'
import { legacyVoiceCapabilities, type ItemVoiceRecord } from '../src/types/itemVoices.ts'
import { cacheBustedUri } from '../src/utils/cacheBustedUri.ts'
import { langLabel } from '../src/utils/voiceLabels.ts'
import { clampSpeed, speedRange, ttsConfigurationOption } from '../src/utils/ttsConfigurations.ts'
import { readVoiceGenerationPreferences, writeVoiceGenerationPreferences, voiceGenerationSettingsFor, type VoiceGenerationPreferences } from '../src/utils/voiceGenerationPreferences.ts'
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

test('the speed slider stays within what the configuration\'s provider accepts', () => {
  assert.deepEqual(speedRange('elevenlabs'), { min: .7, max: 1.2 })
  assert.deepEqual(speedRange('static'), { min: .7, max: 1.2 })
  assert.deepEqual(speedRange('openai'), { min: .25, max: 4 })
  assert.deepEqual(speedRange('speaktor'), { min: .5, max: 2 })
  assert.deepEqual(speedRange(undefined), { min: .5, max: 2 })
  assert.equal(clampSpeed(.5, speedRange('elevenlabs')), .7)
  assert.equal(clampSpeed(1.5, speedRange('elevenlabs')), 1.2)
  assert.equal(clampSpeed(.9, speedRange('elevenlabs')), .9)
})

test('generation preferences survive reloads and retain independent settings for each language', () => {
  let stored: string | null = null
  const storage = { getItem: () => stored, setItem: (_key: string, value: string) => { stored = value } }
  assert.deepEqual(readVoiceGenerationPreferences(storage), { settings: {} })
  const preferences: VoiceGenerationPreferences = { language: 'bg', settings: {
    en: { configurationName: 'openai', speed: 1.5, stability: .5, similarity: .75, autoOptimize: false },
    bg: { configurationName: 'elevenlabs_slow', speed: .8, stability: .4, similarity: .9, autoOptimize: true },
  } }
  writeVoiceGenerationPreferences(preferences, storage)
  assert.deepEqual(readVoiceGenerationPreferences(storage), preferences)
})

test('invalid or unavailable preference storage does not prevent generation', () => {
  const storage = { getItem: () => '{broken', setItem: () => { throw new Error('storage blocked') } }
  assert.deepEqual(readVoiceGenerationPreferences(storage), { settings: {} })
  storage.getItem = () => JSON.stringify({ language: 'invalid', settings: {
    en: { configurationName: 'openai', speed: 'fast', stability: .5, similarity: .75, autoOptimize: true },
    bg: { configurationName: 'elevenlabs', speed: 1, stability: 2, similarity: .75, autoOptimize: true },
  } })
  assert.deepEqual(readVoiceGenerationPreferences(storage), { settings: {} })
  storage.getItem = () => { throw new Error('storage blocked') }
  assert.deepEqual(readVoiceGenerationPreferences(storage), { settings: {} })
  assert.doesNotThrow(() => writeVoiceGenerationPreferences({ settings: {} }, storage))
})

test('restored settings follow provider speed limits and fall back when the configuration changes', () => {
  const configuration = { config_name: 'elevenlabs', provider: 'elevenlabs', actor: 'Milena', model: 'model', enabled: true, speed: 1, stability: .6, similarity: .7 }
  const saved = { configurationName: 'elevenlabs', speed: 1.8, stability: .3, similarity: .8, autoOptimize: true }
  assert.deepEqual(voiceGenerationSettingsFor(configuration, saved), { ...saved, speed: 1.2 })
  assert.deepEqual(voiceGenerationSettingsFor({ ...configuration, config_name: 'replacement' }, saved), {
    configurationName: 'replacement', speed: 1, stability: .6, similarity: .7, autoOptimize: true,
  })
  assert.equal(voiceGenerationSettingsFor(configuration).autoOptimize, false)
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

test('cache busting inserts the version before the fragment and preserves query separators', () => {
  for (const [uri, expected] of [
    ['/img.png', '/img.png?v=2'],
    ['/img.png?lang=bg', '/img.png?lang=bg&v=2'],
    ['/img.png#preview', '/img.png?v=2#preview'],
    ['/img.png?lang=bg#preview', '/img.png?lang=bg&v=2#preview'],
    ['/img.png#preview?mode=full', '/img.png?v=2#preview?mode=full'],
    ['/img.png#', '/img.png?v=2#'],
    ['/img.png#preview#detail', '/img.png?v=2#preview#detail'],
  ]) {
    assert.equal(cacheBustedUri(uri, 2), expected)
  }
})

test('cache busting preserves empty URIs, preview URLs and falsy versions', () => {
  for (const uri of ['', null, undefined]) assert.equal(cacheBustedUri(uri, 2), '')
  for (const uri of ['blob:http://localhost/123#preview', 'data:audio/wav;base64,AAAA#preview', '/img.png?lang=bg#preview']) {
    for (const version of [0, NaN]) assert.equal(cacheBustedUri(uri, version), uri)
  }
  assert.equal(cacheBustedUri('blob:http://localhost/123#preview', 2), 'blob:http://localhost/123#preview')
  assert.equal(cacheBustedUri('data:audio/wav;base64,AAAA#preview', 2), 'data:audio/wav;base64,AAAA#preview')
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

test('optimized variants fold into the recording they were made from and stay addressable by their own ID', () => {
  const optimized = (id: string, optimizer: string) => voice({ id, file_uri: `/content/${id}.mp3`, parameters: { _source: 'en-1', _optimizer: optimizer } })
  const normalized = normalizeItemVoices({
    item_id: 'item', name: {}, voices: [],
    variants: [voice(), optimized('en-ffmpeg', 'ffmpeg'), optimized('en-auphonic', 'auphonic'), voice({ id: 'es-1', language: 'es' })],
    main_voice_ids: { en: 'en-auphonic', es: 'es-1' },
  })
  assert.deepEqual(normalized.voices.map((entry) => entry.id), ['en-1', 'es-1'])
  const [english] = normalized.voices
  assert.deepEqual(optimizedVersions(english).map(({ id, optimizer }) => [id, optimizer]), [['en-ffmpeg', 'ffmpeg'], ['en-auphonic', 'auphonic']])
  assert.deepEqual([english.is_selected, english.selected_version, english.selected_optimized_id], [true, 'optimized', 'en-auphonic'])
  assert.equal(voiceSelectionId(english, 'original'), 'en-1')
  assert.equal(voiceSelectionId(english, 'optimized'), 'en-ffmpeg')
  assert.equal(voiceSelectionId(english, 'optimized', 'en-auphonic'), 'en-auphonic')
  assert.equal(voiceSelectionId(english, 'optimized', 'unknown'), undefined)
  assert.equal(voiceAudioUri(english, 'optimized'), '/content/en-ffmpeg.mp3')
})

test('an optimized variant whose source recording is gone remains a card of its own', () => {
  const orphan = voice({ id: 'en-orphan', parameters: { _source: 'deleted', _optimizer: 'ffmpeg' } })
  const normalized = normalizeItemVoices({ item_id: 'item', name: {}, voices: [], variants: [orphan], main_voice_ids: { en: 'en-orphan' } })
  assert.deepEqual(normalized.voices.map(({ id, is_selected }) => [id, is_selected]), [['en-orphan', true]])
  assert.deepEqual(optimizedVersions(normalized.voices[0]), [])
})

test('a single optimized file can come from the legacy optimized_file_uri fields', () => {
  assert.equal(voiceSelectionId(voice({ optimized_file_uri: '/optimized.mp3' }), 'optimized'), undefined)
  assert.equal(voiceSelectionId(voice({ optimized_file_uri: '/optimized.mp3', optimized_voice_id: 'optimized-id' }), 'optimized'), 'optimized-id')
  assert.equal(voiceSelectionId(voice({ file_uri: null }), 'original'), undefined)
  assert.deepEqual(optimizedVersions(voice()), [])
})

test('file info chips derive size and average bitrate from the file', () => {
  assert.equal(formatFileSize(250_880), '245 KB')
  assert.equal(formatFileSize(10), '1 KB')
  assert.equal(formatFileSize(3 * 1024 * 1024), '3.0 MB')
  assert.equal(audioBitrateKbps(24_000, 1), 192)
  assert.equal(audioBitrateKbps(0, 1), null)
  assert.equal(audioBitrateKbps(24_000, 0), null)
})

test('sizes of blob and data URLs are not requested', async () => {
  assert.equal(await fetchAudioSize('blob:http://localhost/1'), null)
  assert.equal(await fetchAudioSize('data:audio/wav;base64,AAAA'), null)
  assert.equal(await fetchAudioSize(''), null)
})
