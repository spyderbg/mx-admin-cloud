/**
 * Category operations, scenario 1 (UI): every category operation the frontend offers,
 * driven through the real UI and checked against the backend API and the stub's call log.
 * 12-category-operations-api.spec.ts covers the same routes at the API level, plus what the
 * UI doesn't expose (category images, description edits, the archive list filters).
 *
 *   change the category id ......... id pencil -> "Rename category" dialog
 *   rename one language's name ..... pencil beside a voice card's name
 *   upload / delete the icon ....... icon actions (regenerate is a placeholder toast, not tested;
 *                                    the page not refreshing after a delete is a known bug, test.fail)
 *   delete / upload / replace voice  voice card actions
 *   regenerate a voice ............. voice card action
 *   archive ........................ category page
 *   restore / delete permanently ... dashboard's archived section
 *
 * Creating a category through the UI is already covered by 10-category-create.spec.ts.
 *
 * Deterministic the same way as the API spec: each test creates its own scratch category
 * ("ops ui ...") through the API, the stub's log is reset before each test, archive
 * folder names are never predicted, and every scratch category (live or archived) is
 * purged afterwards, so the fixture the other specs assert against stays as seeded.
 */

import { fileURLToPath } from 'node:url'
import { test, expect, type Page } from '@playwright/test'
import { ContentApi, FAKE_MP3, folderFromName, idFromName } from '../fixtures/contentApi'
import { categoryDetail, dashboard, fileDialog, modal, toast, voiceCards } from '../fixtures/selectors'
import { resetStub, stubCalls } from '../fixtures/stub'

const UPLOAD_PNG = fileURLToPath(new URL('../fixtures/upload.png', import.meta.url))
const voiceFile = (lang: string) => ({ name: `${lang}.mp3`, mimeType: 'audio/mpeg', buffer: FAKE_MP3 })

// Voice cards carry a flag icon, not a language code; English is the US flag.
const FLAG = { en: 'us', fr: 'fr', es: 'es', bg: 'bg' } as const

const SCRATCH = ['ops ui rename', 'ops ui renamed', 'ops ui word', 'ops ui icon', 'ops ui voices', 'ops ui archive']

let api: ContentApi

test.beforeAll(async ({ request }) => {
  const setup = new ContentApi(request)
  for (const name of SCRATCH) await setup.purge(name)
})

test.beforeEach(async ({ request }) => {
  api = new ContentApi(request)
  await resetStub()
})

test.afterEach(async () => {
  for (const name of SCRATCH) await api.purge(name)
})

async function openCategory(page: Page, id: string): Promise<void> {
  await page.goto(`/categories/${id}`)
  await expect(categoryDetail.idHeading(page)).toHaveText(id)
}

test('changing the category id from the rename dialog', async ({ page }) => {
  const id = await api.create('ops ui rename')
  const newId = idFromName('ops ui renamed')
  const newFolder = folderFromName('ops ui renamed')
  const namesBefore = (await api.details(id)).name
  await openCategory(page, id)

  // The dialog starts from the current id; cancelling changes nothing.
  await categoryDetail.renameIdButton(page).click()
  await expect(modal.nameInput(page)).toHaveValue(id)
  await modal.nameInput(page).fill(newId)
  await modal.cancelButton(page).click()
  await expect(modal.box(page)).toHaveCount(0)
  await expect(categoryDetail.idHeading(page)).toHaveText(id)
  expect((await api.details(id)).id).toBe(id)

  await categoryDetail.renameIdButton(page).click()
  await modal.nameInput(page).fill(newId)
  await modal.primaryButton(page).click()

  await expect(page).toHaveURL(new RegExp(`/categories/${newId}$`))
  await expect(categoryDetail.idHeading(page)).toHaveText(newId)
  expect((await api.get(`/categories/${id}`)).status()).toBe(404)
  expect((await api.details(newId)).name).toEqual(namesBefore)
  for (const voice of await api.voices(newId)) {
    expect(voice.file_uri).toBe(`/content/${newFolder}/${newFolder}_${voice.language}.mp3`)
  }
  await expect(categoryDetail.icon(page)).toHaveAttribute('src', new RegExp(`^/content/${newFolder}/${newFolder}\\.png`))
})

test("renaming one language's name from its voice card", async ({ page }) => {
  const id = await api.create('ops ui word')
  const before = await api.details(id)
  await openCategory(page, id)

  const frCard = voiceCards.byLanguageFlag(page, FLAG.fr)
  await expect(voiceCards.name(frCard)).toHaveText(before.name.fr)

  await voiceCards.renameButton(frCard).click()
  await expect(modal.nameInput(page)).toHaveValue(before.name.fr)
  await modal.nameInput(page).fill('mot du jour')
  await modal.primaryButton(page).click()

  await expect(voiceCards.name(frCard)).toHaveText('mot du jour')
  await expect(page).toHaveURL(new RegExp(`/categories/${id}$`))
  const after = await api.details(id)
  expect(after.name).toEqual({ ...before.name, fr: 'mot du jour' })
  expect(after.description).toEqual(before.description)
})

test('uploading a new icon, then deleting it', async ({ page }) => {
  const id = await api.create('ops ui icon')
  const folder = folderFromName('ops ui icon')
  const iconUri = `/content/${folder}/${folder}.png`
  const generated = await api.contentFile(iconUri)
  await openCategory(page, id)

  await categoryDetail.iconUploadButton(page).click()
  await modal.fileInput(page).setInputFiles(UPLOAD_PNG)
  await expect(toast.success(page)).toHaveText('Category icon updated.')
  await expect(modal.box(page)).toHaveCount(0)

  // Same file name, new bytes; the page cache-busts the URL and shows the new image.
  const uploaded = await api.contentFile(iconUri)
  expect(uploaded).not.toBeNull()
  expect(uploaded).not.toEqual(generated)
  const icon = categoryDetail.icon(page)
  await expect(icon).toHaveAttribute('src', new RegExp(`^${iconUri}\\?v=\\d+$`))
  await expect.poll(() => icon.evaluate((el) => (el as HTMLImageElement).naturalWidth)).toBeGreaterThan(0)

  await categoryDetail.iconDeleteButton(page).click()
  await expect(modal.box(page)).toBeVisible()
  await modal.dangerButton(page).click()
  await expect(toast.success(page)).toHaveText('Category icon deleted.')
  expect(await api.contentFile(iconUri)).toBeNull()
  expect((await api.list()).find((c) => c.id === id)!.thumbnail_uri).toBeNull()

  // Checked after a reload: without one the page keeps the stale icon (see the next test).
  await page.reload()
  await expect(categoryDetail.idHeading(page)).toHaveText(id)
  await expect(icon).not.toHaveAttribute('src', /\/content\//)
  await expect(categoryDetail.iconDeleteButton(page)).toBeDisabled()
})

test('deleting the icon updates the page without a reload', async ({ page }) => {
  // KNOWN BUG: after the delete, CategoryDetail.vue's refreshCategory() only invalidates
  // the category-detail cache, while thumbnail_uri comes from the shared category list,
  // which is never re-fetched — so the page keeps pointing at the deleted file (a broken
  // image) and the delete button stays enabled until a reload. Remove test.fail() once
  // that's fixed; Playwright reports this test as an unexpected pass when it is.
  test.fail(true, 'stale icon after delete: category list cache not invalidated (CategoryDetail.vue)')

  const id = await api.create('ops ui icon')
  await openCategory(page, id)
  await categoryDetail.iconDeleteButton(page).click()
  await modal.dangerButton(page).click()
  await expect(toast.success(page)).toHaveText('Category icon deleted.')

  await expect(categoryDetail.icon(page)).not.toHaveAttribute('src', /\/content\//)
  await expect(categoryDetail.iconDeleteButton(page)).toBeDisabled()
})

test('deleting a voice, uploading it again, and replacing another', async ({ page }) => {
  const id = await api.create('ops ui voices')
  const folder = folderFromName('ops ui voices')
  await openCategory(page, id)

  const frCard = voiceCards.byLanguageFlag(page, FLAG.fr)
  await expect(voiceCards.playButton(frCard)).toBeEnabled()

  // Delete (a native <dialog> confirmation, not a cd-modal).
  await voiceCards.deleteButton(frCard).click()
  await expect(fileDialog.box(page)).toBeVisible()
  await fileDialog.confirmButton(page).click()
  await expect(fileDialog.box(page)).toHaveCount(0)
  await expect(voiceCards.playButton(frCard)).toBeDisabled()
  expect(await api.voice(id, 'fr')).toMatchObject({ file_uri: null })

  // Upload into the now-empty slot: the UI adds it (POST), since there's no file to replace.
  await voiceCards.uploadButton(frCard).click()
  await voiceCards.uploadInput(page).setInputFiles(voiceFile('fr'))
  await expect(toast.success(page)).toContainText('added')
  await expect(voiceCards.playButton(frCard)).toBeEnabled()
  expect(await api.voice(id, 'fr')).toMatchObject({ file_uri: `/content/${folder}/${folder}_fr.mp3`, provider: 'custom' })
  expect(await api.contentFile(`/content/${folder}/${folder}_fr.mp3`)).toEqual(FAKE_MP3)

  // Upload over an existing voice: the UI replaces it (PUT).
  const esCard = voiceCards.byLanguageFlag(page, FLAG.es)
  await voiceCards.uploadButton(esCard).click()
  await voiceCards.uploadInput(page).setInputFiles(voiceFile('es'))
  await expect(toast.success(page)).toContainText('updated')
  expect(await api.voice(id, 'es')).toMatchObject({ provider: 'custom' })
  expect(await api.contentFile(`/content/${folder}/${folder}_es.mp3`)).toEqual(FAKE_MP3)

  // The other two languages are untouched.
  for (const lang of ['en', 'bg'] as const) {
    expect(await api.voice(id, lang)).toMatchObject({ provider: 'speaktor' })
  }
})

test('regenerating a voice from its card', async ({ page }) => {
  const id = await api.create('ops ui voices')
  const bgName = (await api.details(id)).name.bg
  await api.putMultipart(`/categories/${id}/voices/${folderFromName('ops ui voices')}-v-bg`, { audio: voiceFile('bg') })
  expect(await api.voice(id, 'bg')).toMatchObject({ provider: 'custom' })
  await openCategory(page, id)
  await resetStub()

  await voiceCards.regenerateButton(voiceCards.byLanguageFlag(page, FLAG.bg)).click()
  await expect(toast.success(page)).toContainText('regenerated')

  expect((await stubCalls()).filter((c) => c.kind === 'tts')).toEqual([expect.objectContaining({ text: bgName })])
  const voice = await api.voice(id, 'bg')
  expect(voice).toMatchObject({ provider: 'speaktor' })
  expect(voice.parameters._text).toBe(bgName)
})

test('archiving, restoring, then permanently deleting from the dashboard', async ({ page }) => {
  const name = 'ops ui archive'
  const id = await api.create(name)
  const folder = folderFromName(name)

  async function archiveFromCategoryPage(): Promise<void> {
    await openCategory(page, id)
    await categoryDetail.archiveButton(page).click()
    await modal.nameInput(page).fill(id)
    await modal.dangerButton(page).click()
    await expect(page).toHaveURL(/\/$/)
    await expect(dashboard.cardByName(page, name)).toHaveCount(0)
    // The archived list starts collapsed every time the dashboard mounts.
    await dashboard.archivedToggle(page).click()
  }

  await archiveFromCategoryPage()
  const row = dashboard.archivedRowByName(page, name)
  await expect(row).toBeVisible()
  expect(await api.archived()).toContainEqual(expect.objectContaining({ base_name: folder }))

  await dashboard.archivedRestore(row).click()
  await expect(page).toHaveURL(new RegExp(`/categories/${id}$`), { timeout: 10_000 })
  await expect(categoryDetail.idHeading(page)).toHaveText(id)
  expect(await api.archived()).not.toContainEqual(expect.objectContaining({ base_name: folder }))

  // The restore consumed the first snapshot, so archiving again can't reuse its folder.
  await archiveFromCategoryPage()
  await dashboard.archivedDelete(row).click()
  await expect(modal.box(page)).toBeVisible()
  await modal.nameInput(page).fill('not the id')
  await expect(modal.dangerButton(page)).toBeDisabled()
  await modal.nameInput(page).fill(id)
  await modal.dangerButton(page).click()

  await expect(row).toHaveCount(0)
  expect(await api.archived()).not.toContainEqual(expect.objectContaining({ base_name: folder }))
  expect((await api.get(`/categories/${id}`)).status()).toBe(404)
})

test('leaves no scratch category or archive behind', async () => {
  const scratch = (folder: string) => folder.startsWith('ops_ui_')
  expect((await api.list()).filter((c) => scratch(folderFromName(c.name.en ?? '')) || c.id.startsWith('C_OpsUi'))).toEqual([])
  expect((await api.archived()).filter((a) => scratch(a.base_name))).toEqual([])
})
