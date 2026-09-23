import { test, expect } from '@playwright/test'
import { categoryDetail, navbar, sidebar, voiceCards } from '../fixtures/selectors'
import { FIXTURE } from '../fixtures/seed'

test('C_Drinks shows 4 voice cards and play issues a request for the real MP3', async ({ page }) => {
  await page.goto(`/categories/${FIXTURE.categories.drinks.id}`)
  await expect(categoryDetail.idHeading(page)).toHaveText(FIXTURE.categories.drinks.id)

  const cards = voiceCards.all(page)
  await expect(cards).toHaveCount(4)

  const enCard = voiceCards.byLanguageFlag(page, 'us')
  await expect(enCard).toHaveCount(1)

  const responsePromise = page.waitForResponse((response) =>
    response.url().includes('/content/drinks/drinks_en.mp3') && response.request().method() === 'GET',
  )
  await voiceCards.playButton(enCard).click()
  const response = await responsePromise
  // Chromium's <audio> element issues a Range request for media by default, and
  // FastAPI's StaticFiles answers it with 206 Partial Content — both prove the file
  // exists and was served through the proxy correctly.
  expect([200, 206]).toContain(response.status())

  // Stop playback so it doesn't bleed into the next assertion/test.
  await voiceCards.playButton(enCard).click()
})

test('C_AtHome has no items but still shows all 4 voice placeholders', async ({ page }) => {
  await page.goto(`/categories/${FIXTURE.categories.atHome.id}`)
  await expect(categoryDetail.idHeading(page)).toHaveText(FIXTURE.categories.atHome.id)
  await expect(voiceCards.all(page)).toHaveCount(4)
})

test('Bulgarian content language renders the category name as напитки', async ({ page }) => {
  await page.goto(`/categories/${FIXTURE.categories.drinks.id}`)

  await navbar.languageToggle(page).click()
  await navbar.languageOption(page, 'Български').click()

  // The sidebar renders category.name.bg directly, with no PascalCase transform (unlike
  // the breadcrumb, which title-cases it via toPascalCaseWords) — this is also the one
  // assertion in the suite that proves Cyrillic survives the whole read path: real CSV
  // bytes -> FastAPI JSON -> Vue render.
  await expect(sidebar.categoryLinkByText(page, FIXTURE.categories.drinks.nameBg)).toBeVisible()
})
