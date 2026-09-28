import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { createServer } from 'vite'

let server
let api
let client
const storage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
before(async () => {
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: () => null } })
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, watch: null }, appType: 'custom' })
  api = await server.ssrLoadModule('/src/api/itemVoicesClient.ts')
  client = (await server.ssrLoadModule('/src/api/client.ts')).default
})
after(async () => {
  await server?.close()
  if (storage) Object.defineProperty(globalThis, 'localStorage', storage)
  else delete globalThis.localStorage
})

test('selection sends PUT to the generated main endpoint with the voice language', async () => {
  const response = { item_id: 'I_Bananas', name: { en: 'bananas' }, voices: [], variants: [], main_voice_ids: { en: 'bananas_en_02' } }
  let requests = 0
  client.defaults.adapter = async (config) => {
    requests++
    assert.equal(config.method, 'put')
    assert.equal(config.baseURL + config.url, '/api/v1/content/categories/C_Fruits/items/I_Bananas/voices/bananas_en_02/main')
    assert.deepEqual(JSON.parse(config.data), { language: 'en' })
    return { data: response, status: 200, statusText: 'OK', headers: {}, config }
  }
  assert.deepEqual(await api.selectItemVoice('C_Fruits', 'I_Bananas', 'bananas_en_02', 'en'), response)
  assert.equal(requests, 1)
})

test('selection encodes IDs and propagates backend rejection', async () => {
  const failure = new Error('Voice not found for language')
  client.defaults.adapter = async (config) => {
    assert.equal(config.url, '/content/categories/fruit%20%26%20veg/items/item%2F1/voices/take%231/main')
    assert.deepEqual(JSON.parse(config.data), { language: 'bg' })
    throw failure
  }
  await assert.rejects(api.selectItemVoice('fruit & veg', 'item/1', 'take#1', 'bg'), failure)
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
