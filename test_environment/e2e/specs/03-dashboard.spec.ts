import { test, expect } from '@playwright/test'
import { dashboard } from '../fixtures/selectors'
import { FIXTURE } from '../fixtures/seed'

test.beforeEach(async ({ page }) => {
  await page.goto('/')
})

test('shows exactly 4 cards, sorted alphabetically by folder name', async ({ page }) => {
  const cards = dashboard.cards(page)
  await expect(cards).toHaveCount(4)

  // DashboardView renders the raw localized name (no PascalCase transform), and
  // list_categories() (file_system.py) sorts by on-disk folder name, not CSV row order:
  // at_home < at_school < drinks < fruits.
  const names = await cards.locator('h5').allTextContents()
  expect(names).toEqual([...FIXTURE.dashboardNameOrder])
})

test('category icons render as real images through the content proxy', async ({ page }) => {
  const images = dashboard.cardIcons(page)
  await expect(images).toHaveCount(4)
  for (const image of await images.all()) {
    // naturalWidth > 0 proves the browser actually decoded bytes from the proxy — a
    // broken /content/ route or a missing file would leave this at 0 without erroring.
    await expect.poll(() => image.evaluate((el) => (el as HTMLImageElement).naturalWidth)).toBeGreaterThan(0)
  }
})

test('item counts match the seeded fixture (dashboard counts on-disk item folders, not CSV rows)', async ({ page }) => {
  const expected: Record<string, number> = {
    'at school': FIXTURE.categories.atSchool.itemCount,
    drinks: FIXTURE.categories.drinks.itemCount,
    'at home': FIXTURE.categories.atHome.itemCount,
    // fruits: 1, not the 2 CSV rows — apricot has no on-disk directory at all.
    fruits: FIXTURE.categories.fruits.itemCount,
  }

  for (const [name, count] of Object.entries(expected)) {
    const card = dashboard.cardByName(page, name)
    const label = count === 1 ? '1 item' : `${count} items`
    await expect(dashboard.cardItemCount(card)).toHaveText(label)
  }
})

test('clicking a card navigates to that category', async ({ page }) => {
  await dashboard.cardByName(page, 'drinks').click()
  await expect(page).toHaveURL(new RegExp(`/categories/${FIXTURE.categories.drinks.id}$`))
})
