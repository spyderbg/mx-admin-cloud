/**
 * drinks/water: exercises the main/variant split (implementation plan §6, spec 7;
 * commit 86fcf35's main-image-by-copy behaviour) at
 * backend/src/services/content/providers/file_system/items.py (item_images and
 * set_main_item_image). See content-quirks.spec.ts for the fallback-main assertion this
 * spec builds on: water starts with water_01 selected via fallback, not "no main image".
 */

import { fileURLToPath } from 'node:url'
import { test, expect } from '@playwright/test'
import { fileDialog, itemDetail } from '../fixtures/selectors'
import { FIXTURE } from '../fixtures/seed'

const UPLOAD_PNG = fileURLToPath(new URL('../fixtures/upload.png', import.meta.url))

test('selecting the second variant makes it the main image', async ({ page }) => {
  await page.goto(`/categories/${FIXTURE.categories.drinks.id}/items/${FIXTURE.items.water.id}`)
  const cards = itemDetail.imageCards(page)
  await expect(cards).toHaveCount(2)

  const initialMainSrc = await itemDetail.currentImage(page).getAttribute('src')

  await itemDetail.selectImageButton(cards.nth(1)).click()
  await expect(page.locator('.cd-toast-success')).toBeVisible()

  // Selection flips: variant 2 now disabled/selected, variant 1 now selectable.
  await expect(itemDetail.selectImageButton(cards.nth(1))).toBeDisabled()
  await expect(itemDetail.selectImageButton(cards.nth(0))).toBeEnabled()

  const updatedMainSrc = await itemDetail.currentImage(page).getAttribute('src')
  expect(updatedMainSrc).not.toBe(initialMainSrc)
})

test('adding then deleting a variant returns the item to its original count', async ({ page }) => {
  await page.goto(`/categories/${FIXTURE.categories.drinks.id}/items/${FIXTURE.items.water.id}`)
  await expect(itemDetail.imageCards(page)).toHaveCount(2)

  await itemDetail.addImageInput(page).setInputFiles(UPLOAD_PNG)
  await expect(itemDetail.imageCards(page)).toHaveCount(3)

  // Delete the variant just added (the last card) — a plain confirm, unlike the
  // typed-id confirmations for archive/delete-item.
  const cards = itemDetail.imageCards(page)
  await itemDetail.deleteImageButton(cards.nth(2)).click()
  await expect(fileDialog.title(page)).toHaveText('Delete image')
  await fileDialog.confirmButton(page).click()
  await expect(fileDialog.box(page)).toHaveCount(0)

  await expect(itemDetail.imageCards(page)).toHaveCount(2)
})
