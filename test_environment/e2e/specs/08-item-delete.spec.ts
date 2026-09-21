/**
 * C_AtSchool / I_Pencil: complex delete confirmation (cancel path then confirm path),
 * then the row-alignment guard — every root/category CSV is row-index aligned
 * (_delete_csv_row_at_index), so deleting one item must not corrupt the row a sibling
 * item depends on. See implementation plan §6, spec 8.
 */

import { test, expect } from '@playwright/test'
import { itemDetail, itemGrid, modal } from '../fixtures/selectors'
import { FIXTURE } from '../fixtures/seed'

test('cancelling the delete-item confirmation leaves the item intact', async ({ page }) => {
  await page.goto(`/categories/${FIXTURE.categories.atSchool.id}/items/${FIXTURE.items.pencil.id}`)
  await itemDetail.deleteItemButton(page).click()
  await expect(modal.box(page)).toBeVisible()

  await modal.cancelButton(page).click()
  await expect(modal.box(page)).toHaveCount(0)
  await expect(itemDetail.idHeading(page)).toHaveText(FIXTURE.items.pencil.id)
})

test('confirming with the exact typed id deletes the item and preserves its sibling', async ({ page }) => {
  await page.goto(`/categories/${FIXTURE.categories.atSchool.id}/items/${FIXTURE.items.pencil.id}`)
  await itemDetail.deleteItemButton(page).click()

  // Wrong text keeps the danger button disabled — complex confirmation mode requires
  // the exact item id, matching the archive-category dialog's pattern.
  await modal.nameInput(page).fill('not the id')
  await expect(modal.dangerButton(page)).toBeDisabled()

  await modal.nameInput(page).fill(FIXTURE.items.pencil.id)
  await expect(modal.dangerButton(page)).toBeEnabled()
  await modal.dangerButton(page).click()

  await expect(page).toHaveURL(new RegExp(`/categories/${FIXTURE.categories.atSchool.id}$`))
  await expect(itemGrid.cards(page)).toHaveCount(1)

  // The row-alignment guard: water_bottle (the row after pencil in every at_school CSV)
  // must still resolve correctly, not silently point at pencil's old data or 404.
  await page.goto(`/categories/${FIXTURE.categories.atSchool.id}/items/${FIXTURE.items.waterBottle.id}`)
  await expect(itemDetail.idHeading(page)).toHaveText(FIXTURE.items.waterBottle.id)
})
