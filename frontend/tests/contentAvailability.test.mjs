import assert from 'node:assert/strict'
import { after, before, beforeEach, test } from 'node:test'
import { createServer } from 'vite'

const UNAVAILABLE = 'No active content provider is configured.'
const LIST = { categories: [{ id: 'C_Foods', name: { en: 'Foods' }, items_count: 0, thumbnail_uri: null }], archived: [] }
const DETAILS = { name: { en: 'Foods' }, description: {}, items: [] }

let server
let cache
let configurationCache
let client
let internalClient
let contentAvailable
let contentRequests
const storage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')

function httpError(status, detail) {
  return Object.assign(new Error(`Request failed with status code ${status}`), { response: { status, data: { detail } } })
}

/** Serve the content API as an installation whose content provider is enabled while *contentAvailable*. */
function serveContent(config) {
  contentRequests.push(config.url)
  if (!contentAvailable) throw httpError(503, UNAVAILABLE)
  const data = config.url === '/content/categories' ? LIST : DETAILS
  return { data, status: 200, statusText: 'OK', headers: {}, config }
}

async function waitFor(condition) {
  for (let attempt = 0; attempt < 50 && !condition(); attempt++) await new Promise(resolve => setTimeout(resolve, 0))
  assert.ok(condition(), 'condition never became true')
}

before(async () => {
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: () => null, setItem: () => undefined } })
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, watch: null }, appType: 'custom' })
  cache = await server.ssrLoadModule('/src/api/cache.ts')
  configurationCache = await server.ssrLoadModule('/src/api/configurationCache.ts')
  client = (await server.ssrLoadModule('/src/api/client.ts')).default
  internalClient = (await server.ssrLoadModule('/src/api/internalClient.ts')).default
})

after(async () => {
  await server?.close()
  if (storage) Object.defineProperty(globalThis, 'localStorage', storage)
  else delete globalThis.localStorage
})

beforeEach(async () => {
  contentAvailable = true
  contentRequests = []
  client.defaults.adapter = async config => serveContent(config)
  internalClient.defaults.adapter = async config => ({ data: [], status: 200, statusText: 'OK', headers: {}, config })
  cache.invalidateCategoriesCache()
  cache.sharedContentUnavailable.value = false
  cache.sharedError.value = ''
  await cache.ensureCategoriesLoaded()
  assert.deepEqual(cache.sharedCategories.value.map(category => category.id), ['C_Foods'])
})

test('cached categories are dropped once a revisit finds content disabled', async () => {
  contentAvailable = false

  const returned = await cache.ensureCategoriesLoaded()

  assert.deepEqual(returned.map(category => category.id), ['C_Foods']) // served from the cache at once
  await waitFor(() => cache.sharedContentUnavailable.value)
  assert.deepEqual(cache.sharedCategories.value, [])
  assert.equal(cache.sharedError.value, UNAVAILABLE)
})

test('refreshing the content configurations drops the categories when content was disabled', async () => {
  contentAvailable = false

  await configurationCache.refreshServiceConfigurations('content')

  await waitFor(() => cache.sharedContentUnavailable.value)
  assert.deepEqual(cache.sharedCategories.value, [])
})

test('refreshing the content configurations brings the categories back once content is enabled again', async () => {
  contentAvailable = false
  await configurationCache.refreshServiceConfigurations('content')
  await waitFor(() => cache.sharedContentUnavailable.value)
  contentAvailable = true

  await configurationCache.refreshServiceConfigurations('content')

  await waitFor(() => cache.sharedCategories.value.length > 0)
  assert.equal(cache.sharedContentUnavailable.value, false)
  assert.equal(cache.sharedError.value, '')
})

test('refreshing another service configuration leaves the content cache alone', async () => {
  contentAvailable = false
  contentRequests = []

  await configurationCache.refreshServiceConfigurations('text_to_speech')

  await new Promise(resolve => setTimeout(resolve, 10))
  assert.deepEqual(contentRequests, [])
  assert.deepEqual(cache.sharedCategories.value.map(category => category.id), ['C_Foods'])
})

test('a failure other than 503 keeps the cached categories', async () => {
  client.defaults.adapter = async () => { throw httpError(500, 'boom') }

  await cache.revalidateContent()

  assert.equal(cache.sharedContentUnavailable.value, false)
  assert.deepEqual(cache.sharedCategories.value.map(category => category.id), ['C_Foods'])
})
