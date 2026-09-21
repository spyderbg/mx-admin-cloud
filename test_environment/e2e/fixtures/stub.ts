/**
 * Talks directly to the stub's introspection routes (/__calls, /__reset) — the stub is
 * not behind the frontend's /api /internal /content proxy, so specs need its own URL.
 * Set by `python scripts/testenv.py e2e` (E2E_STUB_URL); defaults to native mode's
 * standard port for ad-hoc `npx playwright test` runs.
 */

const STUB_URL = process.env.E2E_STUB_URL ?? 'http://127.0.0.1:48002'

export interface StubCall {
  ts: number
  kind: 'translate' | 'image' | 'image-view' | 'tts' | 'stt' | 'log'
  path: string
  [key: string]: unknown
}

export async function resetStub(): Promise<void> {
  const response = await fetch(`${STUB_URL}/__reset`, { method: 'POST' })
  if (!response.ok) throw new Error(`POST ${STUB_URL}/__reset -> HTTP ${response.status}`)
}

export async function stubCalls(): Promise<StubCall[]> {
  const response = await fetch(`${STUB_URL}/__calls`)
  if (!response.ok) throw new Error(`GET ${STUB_URL}/__calls -> HTTP ${response.status}`)
  const body = (await response.json()) as { calls: StubCall[] }
  return body.calls
}

export function countByKind(calls: StubCall[], kind: StubCall['kind']): number {
  return calls.filter((call) => call.kind === kind).length
}
