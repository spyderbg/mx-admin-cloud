/**
 * Pins the real-world mess in the fixture as deliberate coverage (see implementation
 * plan §6, spec 5, and risk #10 — each assertion here is "this is correct", reviewed,
 * not merely "this is what happens").
 */

import { test, expect } from '@playwright/test'
import { categoryDetail, itemDetail, navbar, voiceCards } from '../fixtures/selectors'
import { FIXTURE } from '../fixtures/seed'

test('fruits/apricot (no on-disk directory) opens without error and shows fully empty state', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (err) => errors.push(err.message))

  await page.goto(`/categories/${FIXTURE.categories.fruits.id}/items/${FIXTURE.items.apricot.id}`)
  await expect(itemDetail.idHeading(page)).toHaveText(FIXTURE.items.apricot.id)

  await expect(itemDetail.emptyImagesState(page)).toBeVisible()
  await expect(itemDetail.imageCards(page)).toHaveCount(0)
  // "No image available" placeholder, not a broken <img> pointing at a missing file.
  await expect(itemDetail.currentImage(page)).toHaveAttribute('alt', 'No image available')

  // item_voices() always returns one row per SUPPORTED_LANGUAGES regardless of file
  // existence — "empty" here means no *playable* audio, not zero rows.
  const playButtons = itemDetail.voicePlayButtons(page)
  await expect(playButtons).toHaveCount(8) // 4 languages x 2 columns (normal + optimized)
  for (const button of await playButtons.all()) {
    await expect(button).toBeDisabled()
  }

  expect(errors).toEqual([])
})

test('drinks/water lists both variants; the lowest-indexed one is the fallback main image', async ({ page }) => {
  await page.goto(`/categories/${FIXTURE.categories.drinks.id}/items/${FIXTURE.items.water.id}`)
  await expect(itemDetail.idHeading(page)).toHaveText(FIXTURE.items.water.id)

  const cards = itemDetail.imageCards(page)
  await expect(cards).toHaveCount(2)

  // item_images() (file_system.py) falls back to the first sorted variant as the main
  // image whenever no dedicated "water.{ext}" file exists on disk — water_01.png sorts
  // before water_02.jpg, so it is the (fallback) selected one, not "no main image".
  await expect(itemDetail.mainImageCard(page)).toHaveCount(1)
  await expect(itemDetail.selectImageButton(cards.nth(0))).toBeDisabled() // water_01: already selected
  await expect(itemDetail.selectImageButton(cards.nth(1))).toBeEnabled() // water_02: selectable

  // The main preview is populated (the fallback), not the placeholder.
  await expect(itemDetail.currentImage(page)).not.toHaveAttribute('alt', 'No image available')
})

test('drinks category icon is drinks.png, not one of the stray same-folder files', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (err) => errors.push(err.message))

  await page.goto(`/categories/${FIXTURE.categories.drinks.id}`)
  await expect(categoryDetail.idHeading(page)).toHaveText(FIXTURE.categories.drinks.id)

  // list_categories() only ever picks a file named exactly "{category}.{ext}" as the
  // icon — asd_drinks.png / asd_drinks_01.png / drinks_old.png cannot match that, and
  // names_export.py / images.csv / images_names.csv aren't images at all.
  await expect(categoryDetail.icon(page)).toHaveAttribute('src', /\/content\/drinks\/drinks\.png(\?.*)?$/)

  expect(errors).toEqual([])
})

test('no "it" language appears anywhere despite descriptions_it.csv existing', async ({ page }) => {
  await page.goto('/')
  await navbar.languageToggle(page).click()
  const options = page.locator('.language-dropdown .dropdown-item')
  await expect(options).toHaveCount(4)
  await expect(options).not.toContainText(['Italiano', 'Italian'])
})
