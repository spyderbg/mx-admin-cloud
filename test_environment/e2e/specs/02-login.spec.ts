import { test, expect } from '@playwright/test'
import { login } from '../fixtures/selectors'

// Runs unauthenticated: no storageState (see playwright.config.ts — only the
// "chromium" project depends on "setup" / reuses its storageState).
test.use({ storageState: { cookies: [], origins: [] } })

test('wrong password shows an inline error and stays on /login', async ({ page }) => {
  await page.goto('/login')
  await login.username(page).fill('admin')
  await login.password(page).fill('definitely-not-the-password')
  await login.submit(page).click()

  await expect(login.error(page)).toBeVisible()
  await expect(page).toHaveURL(/\/login$/)
  await expect(page.locator('.sidebar')).toHaveCount(0)
})

test('an unauthenticated deep link redirects to /login', async ({ page }) => {
  await page.goto('/categories/C_Drinks')
  await expect(page).toHaveURL(/\/login$/)
})
