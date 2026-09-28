import assert from 'node:assert/strict'
import { test } from 'node:test'
import { selectedVoice, voiceAudioUri, validateAudioFile, voiceSource, audioFileName } from '../src/utils/itemVoices.ts'
import { legacyVoiceCapabilities, type ItemVoiceRecord } from '../src/types/itemVoices.ts'
import { cacheBustedUri } from '../src/utils/cacheBustedUri.ts'

const voice = (overrides: Partial<ItemVoiceRecord> = {}): ItemVoiceRecord => ({
  id: 'en-1', language: 'en', type: 'tts', file_uri: '/content/en.mp3', ...overrides,
})
const multi = { ...legacyVoiceCapabilities, multiple_voices: true, select: true }

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

test('cache busting refreshes real files and leaves uploaded preview URLs playable', () => {
  assert.equal(cacheBustedUri('/audio.mp3?format=mp3', 2), '/audio.mp3?format=mp3&v=2')
  assert.equal(cacheBustedUri('blob:http://localhost/123', 2), 'blob:http://localhost/123')
  assert.equal(cacheBustedUri('data:audio/wav;base64,AAAA', 2), 'data:audio/wav;base64,AAAA')
  assert.equal(audioFileName('/content/my%20voice.mp3?v=1'), 'my voice.mp3')
  assert.equal(audioFileName('/content/bad%encoding.mp3'), 'bad%encoding.mp3')
})
