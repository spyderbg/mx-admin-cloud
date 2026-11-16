/**
 * Category operations, scenario 1 (API contract): every route under
 * /api/v1/content/categories, success and error paths, including the operations the UI
 * doesn't expose (category images, description edits, the archive list filters).
 * 11-category-operations-ui.spec.ts drives the same operations through the UI.
 *
 * Deterministic by construction:
 * - every test works on its own scratch category ("ops api ..."), created through the
 *   stub (translations are "{word}-{lang}", voices and icons are fixed bytes);
 * - the stub's call log is reset before each test, so call counts are exact;
 * - nothing time-dependent is asserted: archive folders are "<folder>_<unix seconds>",
 *   so they're read from responses, never predicted;
 * - every scratch category, live or archived, is purged after each test, and the last
 *   test checks nothing was left behind, so the curated fixture the other specs assert
 *   against (4 categories, empty archive) is unchanged.
 *
 * One backend quirk shapes the archive tests: archiving the same folder twice within one
 * second would reuse the destination name (see archive_category in
 * backend/src/services/content/providers/file_system/categories.py). The tests never do
 * that — a restore consumes the snapshot before the category is archived again.
 */

import { fileURLToPath } from 'node:url'
import { readFileSync } from 'node:fs'
import { test, expect } from '@playwright/test'
import {
  ContentApi, FAKE_MP3, LANGS, errorDetail, folderFromName, idFromName, ok,
  type CategoryFile, type CategoryVoice,
} from '../fixtures/contentApi'
import { FIXTURE } from '../fixtures/seed'
import { countByKind, resetStub, stubCalls } from '../fixtures/stub'

const UPLOAD_PNG = readFileSync(fileURLToPath(new URL('../fixtures/upload.png', import.meta.url)))
const PNG_MAGIC = Buffer.from([0x89, 0x50, 0x4e, 0x47])
const png = (name = 'upload.png') => ({ name, mimeType: 'image/png', buffer: UPLOAD_PNG })
const mp3 = (buffer: Buffer = FAKE_MP3) => ({ name: 'voice.mp3', mimeType: 'audio/mpeg', buffer })

// The stub's translation signature and the backend's description template, both fixed.
const translated = (text: string, lang: string) => (lang === 'en' ? text : `${text}-${lang}`)
const defaultDescription = (name: string) => `Explore fun items about ${name}.`

// Every name a test creates or renames to; purged after each test.
const SCRATCH = [
  'ops api create', 'ops api update', 'ops api move', 'ops api moved', 'ops api icon',
  'ops api images', 'ops api voices', 'ops api archive', 'ops api conflict',
]

let api: ContentApi

// Also before the first test: an aborted earlier run may have left scratch state behind.
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

test('create: translates, generates four voices and an icon through the stub', async () => {
  const name = 'ops api create'
  const folder = folderFromName(name)
  const id = await api.create(name)

  const calls = await stubCalls()
  // Name and description, each into the 3 non-English languages.
  expect(countByKind(calls, 'translate')).toBe(6)
  expect(countByKind(calls, 'tts')).toBe(4)
  expect(calls.filter((c) => c.kind === 'image')).toEqual([expect.objectContaining({ provider: 'openai_http' })])

  const details = await api.details(id)
  expect(details.id).toBe(id)
  expect(details.items).toEqual([])
  for (const lang of LANGS) {
    expect(details.name[lang]).toBe(translated(name, lang))
    expect(details.description[lang]).toBe(translated(defaultDescription(name), lang))
  }

  const summary = (await api.list()).find((c) => c.id === id)
  expect(summary).toEqual({
    id, name: details.name, items_count: 0, thumbnail_uri: `/content/${folder}/${folder}.png`,
  })

  const voices = await api.voices(id)
  expect(voices.map((v) => v.language).sort()).toEqual([...LANGS].sort())
  for (const voice of voices) {
    expect(voice).toMatchObject({
      id: `${folder}-v-${voice.language}`,
      file_uri: `/content/${folder}/${folder}_${voice.language}.mp3`,
      provider: 'speaktor',
    })
    // Each voice speaks the category name in its own language.
    expect(voice.parameters._text).toBe(details.name[voice.language])
    expect(await api.contentFile(voice.file_uri!)).not.toBeNull()
  }

  // The icon is the category's only image.
  expect((await api.images(id)).map((i) => [i.id, i.file_uri])).toEqual([[folder, `/content/${folder}/${folder}.png`]])
  expect((await api.contentFile(`/content/${folder}/${folder}.png`))!.subarray(0, 4)).toEqual(PNG_MAGIC)
})

test('create: rejects an existing or invalid name without creating anything', async () => {
  const before = await api.list()

  expect(await errorDetail(await api.post('/categories', { name: FIXTURE.categories.drinks.nameEn }), 409))
    .toBe(`Category already exists: ${FIXTURE.categories.drinks.folder}`)
  await errorDetail(await api.post('/categories', { name: '   ' }), 400)
  await errorDetail(await api.post('/categories', { name: '../escape' }), 400)

  expect(await api.list()).toEqual(before)
  expect(countByKind(await stubCalls(), 'tts')).toBe(0)
})

test('every per-category route answers 404 for an unknown category', async () => {
  const unknown = 'C_OpsApiNoSuchCategory'
  await errorDetail(await api.get(`/categories/${unknown}`), 404)
  await errorDetail(await api.patch(`/categories/${unknown}`, { category_name: { en: 'x' }, description: {} }), 404)
  await errorDetail(await api.delete(`/categories/${unknown}`), 404)
  await errorDetail(await api.get(`/categories/${unknown}/voices`), 404)
  await errorDetail(await api.get(`/categories/${unknown}/images`), 404)
  await errorDetail(await api.postMultipart(`/categories/${unknown}/icon`, { image: png() }), 404)
  await errorDetail(await api.delete(`/categories/${unknown}/icon`), 404)
})

test('update: names and descriptions are set per language, and omitted languages are kept', async () => {
  const name = 'ops api update'
  const id = await api.create(name)

  const full = {
    category_name: { en: name, fr: 'mise à jour', es: 'actualización', bg: 'обновяване' },
    description: { en: 'Desc en', fr: 'Desc fr', es: 'Desc es', bg: 'Desc bg' },
  }
  expect(await ok(await api.patch(`/categories/${id}`, full))).toMatchObject({ category_id: id, old_category_id: id })
  let details = await api.details(id)
  expect(details.name).toEqual(full.category_name)
  expect(details.description).toEqual(full.description)

  // Only the French name and the Bulgarian description change; everything else stays.
  await ok(await api.patch(`/categories/${id}`, { category_name: { fr: 'nouveau' }, description: { bg: 'Ново' } }))
  details = await api.details(id)
  expect(details.name).toEqual({ ...full.category_name, fr: 'nouveau' })
  expect(details.description).toEqual({ ...full.description, bg: 'Ново' })
  // The list shows the same names as the details.
  expect((await api.list()).find((c) => c.id === id)!.name).toEqual(details.name)
})

test('update: changing the id moves the folder and renames every category file', async () => {
  const id = await api.create('ops api move')
  const newId = idFromName('ops api moved')
  const oldFolder = folderFromName('ops api move')
  const newFolder = folderFromName('ops api moved')
  const namesBefore = (await api.details(id)).name

  // Same id: accepted, nothing moves.
  expect(await ok(await api.patch(`/categories/${id}`, { new_category_id: id, category_name: {}, description: {} })))
    .toMatchObject({ category_id: id })
  // Taken or unsafe ids are refused and change nothing.
  expect(await errorDetail(
    await api.patch(`/categories/${id}`, { new_category_id: FIXTURE.categories.drinks.id, category_name: {}, description: {} }), 409,
  )).toBe(`Category id already exists: ${FIXTURE.categories.drinks.id}`)
  await errorDetail(await api.patch(`/categories/${id}`, { new_category_id: '../C_Escape', category_name: {}, description: {} }), 400)
  expect((await api.details(id)).id).toBe(id)

  expect(await ok(await api.patch(`/categories/${id}`, { new_category_id: newId, category_name: {}, description: {} })))
    .toMatchObject({ category: newFolder, category_id: newId, old_category_id: id })

  await errorDetail(await api.get(`/categories/${id}`), 404)
  expect((await api.details(newId)).name).toEqual(namesBefore)

  const voices = await api.voices(newId)
  for (const voice of voices) {
    expect(voice.id).toBe(`${newFolder}-v-${voice.language}`)
    expect(voice.file_uri).toBe(`/content/${newFolder}/${newFolder}_${voice.language}.mp3`)
    expect(await api.contentFile(voice.file_uri!)).not.toBeNull()
    expect(await api.contentFile(`/content/${oldFolder}/${oldFolder}_${voice.language}.mp3`)).toBeNull()
  }
  expect((await api.images(newId)).map((i) => i.file_uri)).toEqual([`/content/${newFolder}/${newFolder}.png`])
  expect((await api.list()).find((c) => c.id === newId)!.thumbnail_uri).toBe(`/content/${newFolder}/${newFolder}.png`)
})

test('icon: replace, delete, and create again', async () => {
  const name = 'ops api icon'
  const folder = folderFromName(name)
  const id = await api.create(name)
  const iconUri = `/content/${folder}/${folder}.png`
  const generated = await api.contentFile(iconUri)
  expect(generated).not.toEqual(UPLOAD_PNG)

  // The route reports "update" whether or not an icon existed before.
  expect(await ok<CategoryFile>(await api.postMultipart(`/categories/${id}/icon`, { image: png() })))
    .toMatchObject({ id: folder, file_uri: iconUri, action: 'update' })
  expect(await api.contentFile(iconUri)).toEqual(UPLOAD_PNG)

  expect(await ok<CategoryFile>(await api.delete(`/categories/${id}/icon`))).toMatchObject({ file_uri: iconUri, action: 'delete' })
  expect(await api.contentFile(iconUri)).toBeNull()
  expect((await api.list()).find((c) => c.id === id)!.thumbnail_uri).toBeNull()
  expect(await api.images(id)).toEqual([])
  expect(await errorDetail(await api.delete(`/categories/${id}/icon`), 404)).toBe(`Category '${id}' has no icon image.`)

  expect(await ok<CategoryFile>(await api.postMultipart(`/categories/${id}/icon`, { image: png() })))
    .toMatchObject({ file_uri: iconUri, action: 'update' })
  expect((await api.list()).find((c) => c.id === id)!.thumbnail_uri).toBe(iconUri)
})

test('images: add numbered images, replace one, delete one', async () => {
  const name = 'ops api images'
  const folder = folderFromName(name)
  const id = await api.create(name)

  const first = await ok<CategoryFile>(await api.postMultipart(`/categories/${id}/images`, { image: png() }))
  expect(first).toMatchObject({ id: `${folder}_01`, file_uri: `/content/${folder}/${folder}_01.png`, action: 'add' })
  const second = await ok<CategoryFile>(await api.postMultipart(`/categories/${id}/images`, { image: png() }))
  expect(second).toMatchObject({ id: `${folder}_02`, action: 'add' })
  expect((await api.images(id)).map((i) => i.id)).toEqual([folder, `${folder}_01`, `${folder}_02`])

  // Replacing with a JPEG name keeps the id and swaps the file's extension.
  expect(await ok<CategoryFile>(await api.putMultipart(`/categories/${id}/images/${folder}_01`, { image: png('photo.jpg') })))
    .toMatchObject({ id: `${folder}_01`, file_uri: `/content/${folder}/${folder}_01.jpg`, action: 'update' })
  expect(await api.contentFile(`/content/${folder}/${folder}_01.png`)).toBeNull()
  expect(await api.contentFile(`/content/${folder}/${folder}_01.jpg`)).toEqual(UPLOAD_PNG)

  expect(await ok<CategoryFile>(await api.delete(`/categories/${id}/images/${folder}_01`))).toMatchObject({ action: 'delete' })
  expect((await api.images(id)).map((i) => i.id)).toEqual([folder, `${folder}_02`])
  // The next added image takes the lowest free number again.
  expect(await ok<CategoryFile>(await api.postMultipart(`/categories/${id}/images`, { image: png() })))
    .toMatchObject({ id: `${folder}_01` })

  await errorDetail(await api.delete(`/categories/${id}/images/${folder}_09`), 404)
  await errorDetail(await api.putMultipart(`/categories/${id}/images/${folder}_09`, { image: png() }), 404)
  expect(await errorDetail(
    await api.postMultipart(`/categories/${id}/images`, { image: { name: 'empty.png', mimeType: 'image/png', buffer: Buffer.alloc(0) } }), 400,
  )).toBe('Image file is empty')
})

test('voices: delete, upload a missing one, replace one, regenerate one', async () => {
  const name = 'ops api voices'
  const folder = folderFromName(name)
  const id = await api.create(name)
  const frUri = `/content/${folder}/${folder}_fr.mp3`

  // Delete: the language keeps its entry, with no file.
  expect(await ok<CategoryVoice>(await api.delete(`/categories/${id}/voices/${folder}-v-fr`)))
    .toMatchObject({ language: 'fr', file_uri: frUri, action: 'delete' })
  expect(await api.voice(id, 'fr')).toMatchObject({ id: `${folder}-v-fr`, file_uri: null, provider: 'unknown' })
  expect(await api.contentFile(frUri)).toBeNull()
  await errorDetail(await api.delete(`/categories/${id}/voices/${folder}-v-fr`), 404)

  // Upload into the empty slot.
  expect(await ok<CategoryVoice>(await api.postMultipart(`/categories/${id}/voices`, { language: 'fr', audio: mp3() })))
    .toMatchObject({ id: `${folder}-v-fr`, file_uri: frUri, action: 'add', provider: 'custom' })
  expect(await api.contentFile(frUri)).toEqual(FAKE_MP3)
  expect(await errorDetail(await api.postMultipart(`/categories/${id}/voices`, { language: 'fr', audio: mp3() }), 409))
    .toBe(`Voice for language 'fr' already exists in category '${folder}'.`)
  expect(await errorDetail(await api.postMultipart(`/categories/${id}/voices`, { language: 'it', audio: mp3() }), 400))
    .toBe('Unsupported language: it')
  expect(await errorDetail(await api.postMultipart(`/categories/${id}/voices`, { language: 'fr', audio: mp3(Buffer.alloc(0)) }), 400))
    .toBe('Audio file is empty')

  // Replace an existing voice.
  const esUri = `/content/${folder}/${folder}_es.mp3`
  const generatedEs = await api.contentFile(esUri)
  expect(await ok<CategoryVoice>(await api.putMultipart(`/categories/${id}/voices/${folder}-v-es`, { audio: mp3() })))
    .toMatchObject({ file_uri: esUri, action: 'update', provider: 'custom' })
  expect(await api.contentFile(esUri)).toEqual(FAKE_MP3)
  await errorDetail(await api.putMultipart(`/categories/${id}/voices/${folder}-v-it`, { audio: mp3() }), 404)

  // Regenerate it: one TTS call with the current Spanish name, and the stub's audio back.
  await resetStub()
  const regenerated = await ok<CategoryVoice>(await api.post(`/categories/${id}/voices/${folder}-v-es/regenerate`))
  expect(regenerated).toMatchObject({ file_uri: esUri, action: 'regenerate', provider: 'speaktor' })
  expect(regenerated.parameters._text).toBe(translated(name, 'es'))
  expect((await stubCalls()).filter((c) => c.kind === 'tts')).toEqual([
    expect.objectContaining({ text: translated(name, 'es'), language: 'es-ES' }),
  ])
  expect(await api.contentFile(esUri)).toEqual(generatedEs)
})

test('voices: regenerating speaks the name as it is now', async () => {
  const name = 'ops api voices'
  const folder = folderFromName(name)
  const id = await api.create(name)

  await ok(await api.patch(`/categories/${id}`, { category_name: { bg: 'гласове' }, description: {} }))
  await resetStub()
  const regenerated = await ok<CategoryVoice>(await api.post(`/categories/${id}/voices/${folder}-v-bg/regenerate`))
  expect(regenerated.parameters._text).toBe('гласове')
  expect((await stubCalls()).filter((c) => c.kind === 'tts')).toEqual([expect.objectContaining({ text: 'гласове' })])

  // A language with a blank name falls back to the English name, still in its own voice.
  await ok(await api.patch(`/categories/${id}`, { category_name: { fr: '' }, description: {} }))
  await resetStub()
  const fallback = await ok<CategoryVoice>(await api.post(`/categories/${id}/voices/${folder}-v-fr/regenerate`))
  expect(fallback).toMatchObject({ language: 'fr', file_uri: `/content/${folder}/${folder}_fr.mp3` })
  expect(fallback.parameters._text).toBe(name)
  expect((await stubCalls()).filter((c) => c.kind === 'tts')).toEqual([
    expect.objectContaining({ text: name, language: 'fr-FR' }),
  ])
})

test('archive, restore, and permanently delete an archived category', async () => {
  const name = 'ops api archive'
  const folder = folderFromName(name)
  const id = await api.create(name)
  // Non-default names/descriptions, to prove the archive keeps them.
  const edits = { category_name: { fr: 'archives' }, description: { en: 'Kept through the archive' } }
  await ok(await api.patch(`/categories/${id}`, edits))
  const before = await api.details(id)

  const archivedResponse = await ok<{ archived: boolean; name: string; archive_folder: string }>(
    await api.delete(`/categories/${id}`),
  )
  expect(archivedResponse).toMatchObject({ archived: true, name: folder })
  const snapshot = archivedResponse.archive_folder
  expect(snapshot).toMatch(new RegExp(`^${folder}_\\d+$`))

  await errorDetail(await api.get(`/categories/${id}`), 404)
  expect((await api.list()).map((c) => c.id)).not.toContain(id)
  expect(await api.archived()).toContainEqual(expect.objectContaining({ base_name: folder, archive_folder: snapshot }))
  // include_archived appends the archive to the live list; archived_only lists no live ones.
  const withArchived = await ok<{ categories: { id: string }[]; archived: { archive_folder: string }[] }>(
    await api.get('/categories?include_archived=true'),
  )
  expect(withArchived.categories.map((c) => c.id)).toEqual((await api.list()).map((c) => c.id))
  expect(withArchived.archived.map((a) => a.archive_folder)).toContain(snapshot)
  expect((await ok<{ categories: unknown[] }>(await api.get('/categories?archived_only=true'))).categories).toEqual([])
  // Without either flag, no archive entries at all (null, not an empty list).
  expect((await ok<{ archived: unknown }>(await api.get('/categories'))).archived).toBeNull()

  // Restore by base name: the same category comes back, names and descriptions intact.
  expect(await ok(await api.post('/categories/restore', { name: folder })))
    .toMatchObject({ restored: true, name: folder, from_archive: snapshot })
  expect(await api.details(id)).toEqual(before)
  expect((await api.voices(id)).every((v) => v.file_uri !== null)).toBe(true)
  // The snapshot was consumed.
  expect(await api.archived()).not.toContainEqual(expect.objectContaining({ archive_folder: snapshot }))
  await errorDetail(await api.post('/categories/restore', { name: folder }), 404)

  // Archive again, then delete the snapshot for good (addressed by its folder name).
  const second = (await ok<{ archive_folder: string }>(await api.delete(`/categories/${id}`))).archive_folder
  expect(await ok(await api.delete(`/categories/archive/${second}`)))
    .toMatchObject({ deleted: true, name: folder, archive_folder: second })
  expect(await api.archived()).not.toContainEqual(expect.objectContaining({ base_name: folder }))
  await errorDetail(await api.delete(`/categories/archive/${second}`), 404)
  await errorDetail(await api.post('/categories/restore', { name: folder }), 404)
  await errorDetail(await api.post('/categories/restore', { name: '  ' }), 400)
})

test('restore refuses to overwrite a category that exists again', async () => {
  const name = 'ops api conflict'
  const folder = folderFromName(name)
  const id = await api.create(name)
  const snapshot = (await ok<{ archive_folder: string }>(await api.delete(`/categories/${id}`))).archive_folder
  await api.create(name)

  expect(await errorDetail(await api.post('/categories/restore', { name: snapshot }), 409))
    .toBe(`Category already exists in content directory: ${folder}`)
  // Both survive: the new live category and the untouched snapshot.
  expect((await api.details(id)).id).toBe(id)
  expect(await api.archived()).toContainEqual(expect.objectContaining({ archive_folder: snapshot }))

  // Drop the snapshot before afterEach archives the live category, so the two never share
  // an archive folder name.
  await ok(await api.delete(`/categories/archive/${snapshot}`))
})

test('leaves no scratch category or archive behind', async () => {
  const scratch = (folder: string) => folder.startsWith('ops_api_')
  expect((await api.list()).filter((c) => scratch(folderFromName(c.name.en ?? '')) || c.id.startsWith('C_OpsApi'))).toEqual([])
  expect((await api.archived()).filter((a) => scratch(a.base_name))).toEqual([])
})
