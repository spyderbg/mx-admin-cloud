/**
 * Full hard-gate chain — translate + TTS + icon generation, all through the stub — the
 * best integration assertion in the suite and impossible without it (implementation
 * plan §6, spec 10). create_category translates both the label and the description for
 * each non-English language (2 x 3 = 6 calls), then generates 4 voices and 1 icon.
 */

import { test, expect } from '@playwright/test'
import { categoryDetail, dashboard, modal } from '../fixtures/selectors'
import { countByKind, resetStub, stubCalls } from '../fixtures/stub'

test('creating "gadgets" runs translate, TTS and icon generation via the stub', async ({ page }) => {
  await resetStub()

  await page.goto('/')
  await dashboard.addCategoryButton(page).click()
  await expect(modal.box(page)).toBeVisible()

  await modal.nameInput(page).fill('gadgets')
  await expect(modal.previewIdCode(page)).toHaveText('C_Gadgets')

  await modal.primaryButton(page).click()
  await expect(page).toHaveURL(/\/categories\/C_Gadgets$/, { timeout: 20_000 })
  await expect(categoryDetail.idHeading(page)).toHaveText('C_Gadgets')
  await expect.poll(() => categoryDetail.icon(page).evaluate((el) => (el as HTMLImageElement).naturalWidth))
    .toBeGreaterThan(0)

  const calls = await stubCalls()
  expect(countByKind(calls, 'translate')).toBe(6)
  expect(countByKind(calls, 'tts')).toBe(4)

  const imageCalls = calls.filter((c) => c.kind === 'image')
  expect(imageCalls).toHaveLength(1)
  expect(imageCalls[0]).toMatchObject({ provider: 'openai_http' })
})
