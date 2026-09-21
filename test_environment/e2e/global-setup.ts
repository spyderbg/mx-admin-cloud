/**
 * Readiness probe, run once before the whole suite (see playwright.config.ts's
 * `globalSetup`). The stack is externally managed (`python scripts/testenv.py up`) —
 * this only confirms it is actually reachable before any spec (or auth.setup.ts) burns
 * time against a stack that never came up.
 *
 * GET /content/categories_ids.csv, not /openapi.json: nginx (docker mode) / the native
 * proxy only forward /api/, /internal/, /content/ — an /openapi.json request falls
 * through to the SPA's index.html with a 200, which would be a silent false positive.
 * A 200 containing "C_AtSchool" proves nginx/the proxy, the /content/ route, a finished
 * backend lifespan, the content volume, and the seed, all in one request.
 */

const BASE_URL = process.env.E2E_BASE_URL ?? 'http://127.0.0.1:48001'
const TIMEOUT_MS = 60_000
const POLL_INTERVAL_MS = 1_000

async function globalSetup(): Promise<void> {
  const url = `${BASE_URL.replace(/\/$/, '')}/content/categories_ids.csv`
  const deadline = Date.now() + TIMEOUT_MS
  let lastError = 'never attempted'

  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(5_000) })
      if (response.ok) {
        const body = await response.text()
        if (body.includes('C_AtSchool')) {
          return
        }
        lastError = `200 OK but body did not contain "C_AtSchool" (got: ${body.slice(0, 200)})`
      } else {
        lastError = `HTTP ${response.status}`
      }
    } catch (err) {
      lastError = err instanceof Error ? err.message : String(err)
    }
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS))
  }

  throw new Error(
    `Test stack at ${BASE_URL} never became ready (${url} — last error: ${lastError}).\n` +
    'Is it running? Try: python scripts/testenv.py up',
  )
}

export default globalSetup
