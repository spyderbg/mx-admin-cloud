/**
 * Real UI login as `admin`, once, before the suite — not a direct localStorage.setItem:
 * AUTH_DISABLED means the backend accepts any bearer token, but router.beforeEach
 * (frontend/src/router/index.ts) still gates on localStorage.token being *present*, and
 * the only supported way to obtain and store a real one is this form.
 *
 * Registered as the "setup" project in playwright.config.ts; every other project depends
 * on it and reuses the storageState this writes.
 */

import { test as setup, expect } from '@playwright/test'
import { login } from './fixtures/selectors'
import { adminPassword, AUTH_STATE_PATH } from './fixtures/testEnv'

setup('authenticate as admin', async ({ page }) => {
  await page.goto('/login')
  await login.username(page).fill('admin')
  await login.password(page).fill(adminPassword())
  await login.submit(page).click()

  await expect(page).toHaveURL(/\/(categories.*)?$/, { timeout: 15_000 })
  await expect(page.locator('.sidebar')).toBeVisible()

  await page.context().storageState({ path: AUTH_STATE_PATH })
})
