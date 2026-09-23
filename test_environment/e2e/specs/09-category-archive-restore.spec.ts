/**
 * The only spec exercising the .testenv/archive mount (implementation plan §6, spec 9).
 */

import { test, expect } from '@playwright/test'
import { categoryDetail, dashboard, modal } from '../fixtures/selectors'
import { FIXTURE } from '../fixtures/seed'

test('archiving C_AtSchool moves it out of the grid, restoring brings it back', async ({ page }) => {
  await page.goto('/')
  await expect(dashboard.cards(page)).toHaveCount(4)

  await page.goto(`/categories/${FIXTURE.categories.atSchool.id}`)
  await categoryDetail.archiveButton(page).click()
  await expect(modal.box(page)).toBeVisible()

  await modal.nameInput(page).fill('not the id')
  await expect(modal.dangerButton(page)).toBeDisabled()

  await modal.nameInput(page).fill(FIXTURE.categories.atSchool.id)
  await expect(modal.dangerButton(page)).toBeEnabled()
  await modal.dangerButton(page).click()

  await expect(page).toHaveURL(/\/$/)
  await expect(dashboard.cards(page)).toHaveCount(3)
  await expect(dashboard.archivedSection(page)).toBeVisible()

  // The archived list itself only renders once expanded (v-if="archivedExpanded").
  await dashboard.archivedToggle(page).click()
  await expect(dashboard.archivedRows(page)).toHaveCount(1)
  const row = dashboard.archivedRowByName(page, 'at school')
  await expect(row).toBeVisible()
  await dashboard.archivedRestore(row).click()

  await expect(page).toHaveURL(new RegExp(`/categories/${FIXTURE.categories.atSchool.id}$`), { timeout: 10_000 })
  await page.goto('/')
  await expect(dashboard.cards(page)).toHaveCount(4)
  await expect(dashboard.archivedSection(page)).toHaveCount(0)
})
