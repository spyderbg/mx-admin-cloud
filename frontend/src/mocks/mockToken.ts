const base64Url = (value: object): string =>
  btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')

/**
 * Unsigned token the mocked backend hands out. It decodes like a real one, so the UI shows a
 * named admin with every editing control; nothing verifies its signature in mock mode.
 */
export const MOCK_TOKEN = `${base64Url({ alg: 'none', typ: 'JWT' })}.${base64Url({ sub: 'debug-user', role: 'admin' })}.mock`
