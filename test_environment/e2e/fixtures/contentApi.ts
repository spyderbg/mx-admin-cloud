/**
 * Thin client for the backend's category API, used by the category-operations specs to
 * set up and inspect state without going through the UI.
 *
 * Requests go through the frontend's /api/ proxy (Playwright's baseURL), the same route
 * the SPA uses, so this works unchanged in native and Docker mode. It logs in once per
 * worker with the dummy admin password from .testenv/test.env.
 */

import { expect, type APIRequestContext, type APIResponse } from '@playwright/test'
import { adminPassword } from './testEnv'

const API = '/api/v1/content'

/** Bytes the backend accepts as a voice upload: it stores audio as-is, without decoding it. */
export const FAKE_MP3 = Buffer.from('ID3\u0003\u0000\u0000\u0000\u0000\u0000\u0000mx-admin e2e fake mp3', 'binary')

export type Lang = 'en' | 'fr' | 'es' | 'bg'
export const LANGS: readonly Lang[] = ['en', 'fr', 'es', 'bg']

export interface CategorySummary {
  id: string
  name: Record<string, string>
  items_count: number
  thumbnail_uri: string | null
}

export interface ArchivedEntry {
  base_name: string
  archive_folder: string
  archived_at: number
}

export interface CategoryDetails {
  id: string
  name: Record<string, string>
  description: Record<string, string>
  items: unknown[]
}

export interface CategoryFile {
  id: string
  file_uri: string | null
  action: string | null
  provider: string
  parameters: Record<string, unknown>
}

export interface CategoryVoice extends CategoryFile {
  language: Lang
  type: string
}

/**
 * Mirrors the backend's folder <-> id rules for the plain lowercase names these specs
 * use: "ops alpha" -> folder "ops_alpha" -> id "C_OpsAlpha".
 */
export function folderFromName(name: string): string {
  return name.trim().toLowerCase().replace(/\s+/g, '_')
}

export function idFromName(name: string): string {
  return `C_${folderFromName(name).split('_').map((w) => w[0].toUpperCase() + w.slice(1)).join('')}`
}

let cachedToken: string | null = null

export class ContentApi {
  constructor(private readonly request: APIRequestContext) {}

  private async headers(): Promise<Record<string, string>> {
    if (!cachedToken) {
      const response = await this.request.post('/api/v1/login', {
        form: { username: 'admin', password: adminPassword() },
      })
      expect(response.status(), `login: ${await response.text()}`).toBe(200)
      cachedToken = ((await response.json()) as { access_token: string }).access_token
    }
    return { Authorization: `Bearer ${cachedToken}` }
  }

  // -- raw calls (return the response, for status/error assertions) ------------------

  async get(path: string): Promise<APIResponse> {
    return this.request.get(`${API}${path}`, { headers: await this.headers() })
  }

  async post(path: string, data?: unknown): Promise<APIResponse> {
    return this.request.post(`${API}${path}`, { headers: await this.headers(), data })
  }

  async postMultipart(path: string, multipart: Record<string, string | { name: string; mimeType: string; buffer: Buffer }>): Promise<APIResponse> {
    return this.request.post(`${API}${path}`, { headers: await this.headers(), multipart })
  }

  async putMultipart(path: string, multipart: Record<string, string | { name: string; mimeType: string; buffer: Buffer }>): Promise<APIResponse> {
    return this.request.put(`${API}${path}`, { headers: await this.headers(), multipart })
  }

  async patch(path: string, data: unknown): Promise<APIResponse> {
    return this.request.patch(`${API}${path}`, { headers: await this.headers(), data })
  }

  async delete(path: string): Promise<APIResponse> {
    return this.request.delete(`${API}${path}`, { headers: await this.headers() })
  }

  /** GET a /content/... file through the proxy; returns its bytes, or null on 404. */
  async contentFile(fileUri: string): Promise<Buffer | null> {
    const response = await this.request.get(fileUri, { headers: await this.headers() })
    if (response.status() === 404) return null
    expect(response.status(), `GET ${fileUri}`).toBe(200)
    return Buffer.from(await response.body())
  }

  // -- typed helpers (assert success) -------------------------------------------------

  async list(): Promise<CategorySummary[]> {
    return (await ok<{ categories: CategorySummary[] }>(await this.get('/categories'))).categories
  }

  async archived(): Promise<ArchivedEntry[]> {
    // The route sends `archived: null`, not [], when the archive is empty.
    return (await ok<{ archived: ArchivedEntry[] | null }>(await this.get('/categories?archived_only=true'))).archived ?? []
  }

  async details(id: string): Promise<CategoryDetails> {
    return ok<CategoryDetails>(await this.get(`/categories/${id}`))
  }

  async voices(id: string): Promise<CategoryVoice[]> {
    return (await ok<{ voices: CategoryVoice[] }>(await this.get(`/categories/${id}/voices`))).voices
  }

  async voice(id: string, lang: Lang): Promise<CategoryVoice> {
    const voice = (await this.voices(id)).find((v) => v.language === lang)
    expect(voice, `${id} has a ${lang} voice entry`).toBeTruthy()
    return voice!
  }

  async images(id: string): Promise<CategoryFile[]> {
    return (await ok<{ images: CategoryFile[] }>(await this.get(`/categories/${id}/images`))).images
  }

  /** Create a category (translate + 4 TTS + icon through the stub) and return its id. */
  async create(name: string): Promise<string> {
    const response = await this.post('/categories', { name })
    expect(response.status(), `create "${name}": ${await response.text()}`).toBe(201)
    return idFromName(name)
  }

  /**
   * Remove every trace of a scratch category: archive it if it's live, then permanently
   * delete every archive snapshot of its folder. Safe to call when nothing exists.
   */
  async purge(name: string): Promise<void> {
    const folder = folderFromName(name)
    const live = (await this.list()).find((c) => folderFromName(c.name.en ?? '') === folder || c.id === idFromName(name))
    if (live) {
      const response = await this.delete(`/categories/${live.id}`)
      expect([200, 404], `purge archive ${live.id}`).toContain(response.status())
    }
    for (const entry of await this.archived()) {
      if (entry.base_name === folder) {
        const response = await this.delete(`/categories/archive/${entry.archive_folder}`)
        expect([200, 404], `purge ${entry.archive_folder}`).toContain(response.status())
      }
    }
  }
}

export async function ok<T>(response: APIResponse): Promise<T> {
  expect(response.ok(), `${response.url()} -> ${response.status()} ${await response.text()}`).toBe(true)
  return (await response.json()) as T
}

/** The `detail` of an error response, asserting its status first. */
export async function errorDetail(response: APIResponse, status: number): Promise<string> {
  expect(response.status(), `${response.url()}: ${await response.text()}`).toBe(status)
  return ((await response.json()) as { detail: string }).detail
}
