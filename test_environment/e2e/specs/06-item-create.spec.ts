/**
 * Flagship spec (implementation plan §6, spec 6): the full create-item hard-gate chain
 * — translate (stub) + TTS (stub), no image — asserted both through the UI and by
 * reading the stub's own call log, which is the one place "cherry-fr" (the stub's
 * `{word}-{lang}` signature) is unambiguous proof of the stub path rather than the
 * offline static_translate lookup.
 */

import { test, expect } from '@playwright/test'
import { addItem, itemDetail, modal } from '../fixtures/selectors'
import { FIXTURE } from '../fixtures/seed'
import { countByKind, resetStub, stubCalls } from '../fixtures/stub'

test('creating "cherry" in C_Fruits translates and generates voices via the stub', async ({ page }) => {
  await resetStub()

  await page.goto(`/categories/${FIXTURE.categories.fruits.id}`)
  await addItem.openButton(page).click()
  await expect(modal.box(page)).toBeVisible()

  await modal.nameInput(page).fill('cherry')
  await expect(modal.previewIdCode(page)).toHaveText('I_Cherry')

  // create_item's translate+TTS calls happen server-side before the response returns —
  // this can take a few seconds even against the local stub.
  // The success toast is owned by CategoriesView, which unmounts as soon as the route
  // changes to ItemDetailsView below — it is not a reliable post-navigation assertion,
  // so the URL change and the new page's own heading are the proof of success instead.
  await modal.primaryButton(page).click()
  await expect(page).toHaveURL(/\/categories\/C_Fruits\/items\/I_Cherry$/, { timeout: 20_000 })
  await expect(itemDetail.idHeading(page)).toHaveText('I_Cherry')

  const calls = await stubCalls()
  const translateCalls = calls.filter((c) => c.kind === 'translate')
  expect(countByKind(calls, 'tts')).toBe(4)
  expect(translateCalls).toHaveLength(3)

  const byLang = Object.fromEntries(translateCalls.map((c) => [c.target_lang_code, c]))
  expect(byLang.fr).toMatchObject({ word: 'cherry' })
  expect(byLang.es).toMatchObject({ word: 'cherry' })
  expect(byLang.bg).toMatchObject({ word: 'cherry' })
})

test('duplicate item name is caught before the round trip', async ({ page }) => {
  await page.goto(`/categories/${FIXTURE.categories.fruits.id}`)
  await addItem.openButton(page).click()
  await modal.nameInput(page).fill('avocado') // already exists in the fixture

  await expect(modal.primaryButton(page)).toBeDisabled()
  await expect(page.locator('.cd-modal-body .text-danger')).toBeVisible()
})
