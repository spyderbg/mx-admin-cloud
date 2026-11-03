import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import { readFile } from 'node:fs/promises'
import { createServer } from 'vite'
let server, api, client, download, labels
const storage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
before(async () => {
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: { getItem: () => null } })
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, watch: null }, appType: 'custom' })
  api = await server.ssrLoadModule('/src/api/contentClient.ts')
  client = (await server.ssrLoadModule('/src/api/client.ts')).default
  download = await server.ssrLoadModule('/src/composables/useCategoryItemsExport.ts')
  labels = await server.ssrLoadModule('/src/utils/localizedLabels.ts')
})
after(async () => {
  await server?.close()
  if (storage) Object.defineProperty(globalThis, 'localStorage', storage)
  else delete globalThis.localStorage
})
test('GET encodes category ID and returns unchanged CSV blob', async () => {
  const blob = new Blob(['\uFEFFid,name_en\r\n'], { type: 'text/csv' })
  client.defaults.adapter = async config => {
    assert.equal(config.method, 'get')
    assert.equal(config.baseURL + config.url, '/api/v1/content/categories/fruit%20%26%20veg%2F1/items/export')
    assert.equal(config.responseType, 'blob')
    return { data: blob, status: 200, statusText: 'OK', headers: {}, config }
  }
  assert.equal(await api.itemsExport('fruit & veg/1'), blob)
})
test('busy download ignores duplicates, saves blob and revokes URL', async () => {
  const blob = new Blob(['id\r\n'])
  let resolve, requests = 0, errors = 0, clicked = 0, removed = 0
  const revoked = []
  client.defaults.adapter = config => {
    requests++
    return new Promise(done => { resolve = () => done({ data: blob, status: 200, statusText: 'OK', headers: {}, config }) })
  }
  const anchor = { click() { clicked++ }, remove() { removed++ } }
  const doc = Object.getOwnPropertyDescriptor(globalThis, 'document')
  globalThis.document = { createElement: tag => { assert.equal(tag, 'a'); return anchor }, body: { appendChild: node => assert.equal(node, anchor) } }
  const create = URL.createObjectURL, revoke = URL.revokeObjectURL
  URL.createObjectURL = value => { assert.equal(value, blob); return 'blob:csv' }
  URL.revokeObjectURL = value => revoked.push(value)
  try {
    const state = download.useCategoryItemsExport(() => errors++)
    const pending = state.exportItems('../fruit')
    assert.equal(state.exporting.value, true)
    await state.exportItems('../fruit')
    await new Promise(done => setTimeout(done, 0))
    assert.equal(requests, 1)
    resolve()
    await pending
    assert.equal(state.exporting.value, false)
    assert.equal(anchor.download, '___fruit-items.csv')
    assert.equal(anchor.href, 'blob:csv')
    assert.equal(clicked, 1)
    assert.equal(removed, 1)
    await new Promise(done => setTimeout(done, 0))
    assert.deepEqual(revoked, ['blob:csv'])
    assert.equal(errors, 0)
  } finally {
    URL.createObjectURL = create; URL.revokeObjectURL = revoke
    if (doc) Object.defineProperty(globalThis, 'document', doc)
    else delete globalThis.document
  }
})
test('failure reports localized feedback, clears loading and permits retry', async () => {
  let errors = 0, requests = 0
  client.defaults.adapter = async () => { requests++; throw new Error('failed') }
  const state = download.useCategoryItemsExport(() => errors++)
  await state.exportItems('C_Fruits')
  assert.equal(state.exporting.value, false)
  await state.exportItems('C_Fruits')
  assert.equal(errors, 2)
  assert.equal(requests, 2)
  const messages = ['en', 'es', 'fr', 'bg'].map(lang => labels.exportCsvErrorLabel(lang))
  assert.equal(new Set(messages).size, 4)
})
test('safe bounded filename', () => {
  assert.equal(download.categoryItemsCsvFilename(''), 'category-items.csv')
  assert.match(download.categoryItemsCsvFilename('../a\\b\r\n'), /^[a-zA-Z0-9_-]+-items\.csv$/)
  assert.equal(download.categoryItemsCsvFilename('a'.repeat(200)).length, 110)
})
test('header binds export independently of edit permission and uses localized toast', async () => {
  const view = await readFile(new URL('../src/views/CategoriesView.vue', import.meta.url), 'utf8')
  const button = view.match(/<button\s+type="button"[\s\S]*?<\/button>/)[0]
  assert.ok(!button.includes('canEdit'))
  assert.ok(button.includes(':disabled="exporting"'))
  assert.ok(button.includes('@click="exportItems(category.id)"'))
  assert.ok(button.includes('exportCsvLabel(contentLanguage)'))
  assert.ok(view.includes("showToast(exportCsvErrorLabel(contentLanguage.value), 'error')"))
})
