/**
 * Reads the dummy admin password out of .testenv/test.env at run time, rather than
 * hardcoding it a second time in TypeScript — test_environment/testenv_seed.py
 * (TEST_DEFAULT_ADMIN_PASSWORD in test_environment/testenvlib/paths.py) is the one source of truth.
 */

import { readFileSync } from 'node:fs'
import { resolve, dirname } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
export const TEST_ENV_PATH = resolve(HERE, '../../../.testenv/test.env')
export const ARTIFACTS_DIR = resolve(HERE, '../../../.testenv/artifacts')
export const AUTH_STATE_PATH = resolve(ARTIFACTS_DIR, '.auth/admin.json')

function parseEnvFile(text: string): Record<string, string> {
  const values: Record<string, string> = {}
  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim()
    if (!line || line.startsWith('#') || !line.includes('=')) continue
    const idx = line.indexOf('=')
    values[line.slice(0, idx).trim()] = line.slice(idx + 1).trim()
  }
  return values
}

export function loadTestEnv(): Record<string, string> {
  let text: string
  try {
    text = readFileSync(TEST_ENV_PATH, 'utf-8')
  } catch {
    throw new Error(
      `${TEST_ENV_PATH} not found. Seed the test environment first: python test_environment/testenv.py seed`,
    )
  }
  return parseEnvFile(text)
}

export function adminPassword(): string {
  const value = loadTestEnv().DEFAULT_ADMIN_PASSWORD
  if (!value) throw new Error(`DEFAULT_ADMIN_PASSWORD missing from ${TEST_ENV_PATH}`)
  return value
}
