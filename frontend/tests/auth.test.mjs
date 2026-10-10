import assert from 'node:assert/strict'
import { after, before, beforeEach, test } from 'node:test'
import { createServer } from 'vite'

let server
let auth
let client
let permissions
let mockToken
let stored
const storage = Object.getOwnPropertyDescriptor(globalThis, 'localStorage')
before(async () => {
  Object.defineProperty(globalThis, 'localStorage', {
    configurable: true,
    value: { getItem: (key) => stored.get(key) ?? null, setItem: (key, value) => stored.set(key, value) },
  })
  server = await createServer({ configFile: false, optimizeDeps: { noDiscovery: true, include: [] }, server: { middlewareMode: true, watch: null }, appType: 'custom' })
  auth = await server.ssrLoadModule('/src/api/auth.ts')
  client = (await server.ssrLoadModule('/src/api/client.ts')).default
  permissions = await server.ssrLoadModule('/src/utils/permissions.ts')
  mockToken = await server.ssrLoadModule('/src/mocks/mockToken.ts')
})
beforeEach(() => { stored = new Map() })
after(async () => {
  await server?.close()
  if (storage) Object.defineProperty(globalThis, 'localStorage', storage)
  else delete globalThis.localStorage
})

test('the sign-in options are read from the public auth config endpoint', async () => {
  const config = { google_enabled: true, google_client_id: 'client-123.apps.googleusercontent.com' }
  client.defaults.adapter = async (request) => {
    assert.equal(request.method, 'get')
    assert.equal(request.baseURL + request.url, '/api/v1/auth/config')
    assert.equal(request.headers.Authorization, undefined)
    return { data: config, status: 200, statusText: 'OK', headers: {}, config: request }
  }
  assert.deepEqual(await auth.getAuthConfig(), config)
})

test('Google login exchanges the credential for an access token and keeps it', async () => {
  client.defaults.adapter = async (request) => {
    assert.equal(request.method, 'post')
    assert.equal(request.baseURL + request.url, '/api/v1/login/google')
    assert.deepEqual(JSON.parse(request.data), { credential: 'google-id-token' })
    return { data: { access_token: 'app-token', token_type: 'bearer' }, status: 200, statusText: 'OK', headers: {}, config: request }
  }
  assert.equal((await auth.loginWithGoogle('google-id-token')).access_token, 'app-token')
  assert.equal(stored.get('token'), 'app-token')
})

test('a refused Google login stores no token', async () => {
  const refused = Object.assign(new Error('Forbidden'), { response: { status: 403, data: { detail: 'Google sign-in is not enabled.' } } })
  client.defaults.adapter = async () => { throw refused }
  await assert.rejects(auth.loginWithGoogle('google-id-token'), refused)
  assert.equal(stored.has('token'), false)
})

const tokenFor = (payload) =>
  ['{"alg":"HS256","typ":"JWT"}', JSON.stringify(payload)].map((part) => Buffer.from(part).toString('base64url')).join('.') + '.signature'

test('only admins (and internal services) get the editing controls', () => {
  assert.equal(permissions.hasWriteAccess(tokenFor({ sub: 'boss', role: 'admin' })), true)
  assert.equal(permissions.hasWriteAccess(tokenFor({ sub: 'service', role: 'internal' })), true)
  assert.equal(permissions.hasWriteAccess(tokenFor({ sub: 'ana@example.com', role: 'viewer' })), false)
})

test('a missing, role-less or unreadable token is read-only', () => {
  assert.equal(permissions.hasWriteAccess(null), false)
  assert.equal(permissions.hasWriteAccess(tokenFor({ sub: 'boss' })), false)
  assert.equal(permissions.hasWriteAccess('debug-token'), false)
})

test('write access is read from the stored token by default', () => {
  assert.equal(permissions.hasWriteAccess(), false)
  stored.set('token', tokenFor({ sub: 'ana@example.com', role: 'viewer' }))
  assert.equal(permissions.hasWriteAccess(), false)
  stored.set('token', tokenFor({ sub: 'boss', role: 'admin' }))
  assert.equal(permissions.hasWriteAccess(), true)
})

test('the mock-mode token keeps the editing controls', () => {
  assert.equal(permissions.hasWriteAccess(mockToken.MOCK_TOKEN), true)
})
