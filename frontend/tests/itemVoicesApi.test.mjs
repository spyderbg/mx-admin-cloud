import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { createServer } from 'vite'

let server
let api
let client
let internalClient
const storage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
before(async () => {
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: () => null } })
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, watch: null }, appType: 'custom' })
  api = await server.ssrLoadModule('/src/api/itemVoicesClient.ts')
  client = (await server.ssrLoadModule('/src/api/client.ts')).default
  internalClient = (await server.ssrLoadModule('/src/api/internalClient.ts')).default
})
after(async () => {
  await server?.close()
  if (storage) Object.defineProperty(globalThis, 'localStorage', storage)
  else delete globalThis.localStorage
})

test('selection sends a bodyless PUT to the generated main endpoint', async () => {
  const response = { item_id: 'I_Bananas', name: { en: 'bananas' }, voices: [], variants: [], main_voice_ids: { en: 'bananas_en_02' } }
  let requests = 0
  client.defaults.adapter = async (config) => {
    requests++
    assert.equal(config.method, 'put')
    assert.equal(config.baseURL + config.url, '/api/v1/content/categories/C_Fruits/items/I_Bananas/voices/bananas_en_02/main')
    assert.equal(config.data, undefined)
    return { data: response, status: 200, statusText: 'OK', headers: {}, config }
  }
  assert.deepEqual(await api.selectItemVoice('C_Fruits', 'I_Bananas', 'bananas_en_02'), response)
  assert.equal(requests, 1)
})

test('generation sends the selected configuration identity with per-item settings', async () => {
  const body = { language: 'bg', text: 'диван', configuration_name: 'elevenlabs_slow', speed: .7, optimize: false }
  const response = {
    item_id: 'I_Sofa', name: { bg: 'диван' }, voices: [], variants: [], main_voice_ids: { bg: 'sofa_bg_01' },
    voice_id: 'sofa_bg_02', optimized_voice_ids: [], warnings: [],
  }
  client.defaults.adapter = async (config) => {
    assert.equal(config.method, 'post')
    assert.equal(config.url, '/content/categories/C_AtHome/items/I_Sofa/voices/generate')
    assert.deepEqual(JSON.parse(config.data), body)
    return { data: response, status: 201, statusText: 'Created', headers: {}, config }
  }
  assert.deepEqual(await api.generateItemVoice('C_AtHome', 'I_Sofa', body), response)
})

test('generation encodes the category and item IDs', async () => {
  client.defaults.adapter = async (config) => {
    assert.equal(config.url, '/content/categories/fruit%20%26%20veg/items/item%2F1/voices/generate')
    return { data: {}, status: 201, statusText: 'Created', headers: {}, config }
  }
  await api.generateItemVoice('fruit & veg', 'item/1', { language: 'en', text: 'x', configuration_name: 'openai', speed: 1, optimize: false })
})

test('selection encodes IDs and propagates backend rejection', async () => {
  const failure = new Error('Voice not found')
  client.defaults.adapter = async (config) => {
    assert.equal(config.url, '/content/categories/fruit%20%26%20veg/items/item%2F1/voices/take%231/main')
    assert.equal(config.data, undefined)
    throw failure
  }
  await assert.rejects(api.selectItemVoice('fruit & veg', 'item/1', 'take#1'), failure)
})

test('deletion sends DELETE to the encoded item voice endpoint', async () => {
  const response = { id: 'take#1', language: 'en', type: 'tts', action: 'delete' }
  client.defaults.adapter = async (config) => {
    assert.equal(config.method, 'delete')
    assert.equal(config.url, '/content/categories/fruit%20%26%20veg/items/item%2F1/voices/take%231')
    return { data: response, status: 200, statusText: 'OK', headers: {}, config }
  }
  assert.deepEqual(await api.deleteItemVoice('fruit & veg', 'item/1', 'take#1'), response)
})

test('list loads real backend variants and enables selection without proposed capabilities', async () => {
  client.defaults.adapter = async (config) => ({
    status: 200, statusText: 'OK', headers: {}, config,
    data: {
      item_id: 'I_Bananas', name: { en: 'bananas' },
      voices: [{ id: 'bananas-v-en', language: 'en', type: 'tts', file_uri: '/main.mp3' }],
      variants: [
        { id: 'bananas_en_01', language: 'en', type: 'tts', file_uri: '/01.mp3' },
        { id: 'bananas_en_02', language: 'en', type: 'tts', file_uri: '/02.mp3' },
      ],
      main_voice_ids: { en: 'bananas_en_02' },
    },
  })
  const list = await api.listItemVoices('C_Fruits', 'I_Bananas')
  assert.equal(list.capabilities.select, true)
  assert.deepEqual(list.voices.map(({ id, is_selected }) => [id, is_selected]), [['bananas_en_01', false], ['bananas_en_02', true]])
})

function ttsFile(name, provider, enabled, priority, generate) {
  return { config_name: name, payload: { service_provider: { service: 'text_to_speech', provider, enabled, priority, config: { generate } } } }
}
const ttsSummary = (name, priority) => ({ config_name: name, filename: `${name}.json`, valid_json: true, priority })

test('main voice generation uses the default configuration and selects the new voice', async () => {
  const files = {
    openai: ttsFile('openai', 'openai', false, 1, { languages: { en: { actor: 'alloy' } } }),
    elevenlabs: ttsFile('elevenlabs', 'elevenlabs', true, 2, {
      languages: { en: { actor: 'Milena', speed: 2, voice_settings: { stability: .4, similarity_boost: .8 } } },
    }),
  }
  const calls = []
  const adapter = async (config) => {
    calls.push(`${config.method} ${config.url}`)
    const respond = (data) => ({ data, status: 200, statusText: 'OK', headers: {}, config })
    if (config.url === '/internal/configurations/text_to_speech') return respond([ttsSummary('openai', 1), ttsSummary('elevenlabs', 2)])
    if (config.url.startsWith('/internal/configurations/text_to_speech/')) return respond(files[config.url.split('/').pop()])
    if (config.url.endsWith('/voices/generate')) {
      assert.deepEqual(JSON.parse(config.data), {
        language: 'en', text: 'sofa', configuration_name: 'elevenlabs', speed: 1.2, stability: .4, similarity: .8, optimize: false,
      })
      return respond({ voice_id: 'sofa_en_03' })
    }
    return respond({})
  }
  client.defaults.adapter = adapter
  internalClient.defaults.adapter = adapter
  assert.deepEqual(await api.generateMainItemVoice('C_AtHome', 'I_Sofa', 'en', ' sofa '), { voice_id: 'sofa_en_03' })
  assert.equal(calls.at(-1), 'put /content/categories/C_AtHome/items/I_Sofa/voices/sofa_en_03/main')
})

test('main voice generation does not select anything when generation fails', async () => {
  const failure = new Error('generation failed')
  const calls = []
  const adapter = async (config) => {
    calls.push(config.url)
    const respond = (data) => ({ data, status: 200, statusText: 'OK', headers: {}, config })
    if (config.url === '/internal/configurations/text_to_speech') return respond([ttsSummary('openai', 1)])
    if (config.url.startsWith('/internal/configurations/text_to_speech/')) {
      return respond(ttsFile('openai', 'openai', true, 1, { languages: { en: { actor: 'alloy' } } }))
    }
    throw failure
  }
  client.defaults.adapter = adapter
  internalClient.defaults.adapter = adapter
  await assert.rejects(api.generateMainItemVoice('C_AtHome', 'I_Sofa', 'en', 'sofa'), failure)
  assert.ok(!calls.some((url) => url.endsWith('/main')))
})

test('main voice generation reports a language no configuration supports', async () => {
  const adapter = async (config) => {
    const respond = (data) => ({ data, status: 200, statusText: 'OK', headers: {}, config })
    if (config.url === '/internal/configurations/text_to_speech') return respond([ttsSummary('openai', 1)])
    return respond(ttsFile('openai', 'openai', true, 1, { languages: { en: { actor: 'alloy' } } }))
  }
  client.defaults.adapter = adapter
  internalClient.defaults.adapter = adapter
  await assert.rejects(api.generateMainItemVoice('C_AtHome', 'I_Sofa', 'bg', 'диван'), /No text to speech configuration supports BG/)
})
