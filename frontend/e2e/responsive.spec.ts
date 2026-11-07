import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

const SCREENS = [
  { name: 'small phone', width: 320, height: 640 },
  { name: 'phone', width: 390, height: 844 },
  { name: 'tablet', width: 768, height: 1024 },
  { name: 'tablet landscape', width: 1024, height: 768 },
  { name: 'laptop', width: 1440, height: 900 },
]
const PAGES = ['Trading', "Today's plan", 'Buddy', 'Journal', 'Brokers', 'Settings']

/** Everything visible that pokes out past either edge of the screen. */
async function spills(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const width = document.documentElement.clientWidth
    const out: string[] = []
    for (const element of document.querySelectorAll<HTMLElement>('body *')) {
      // Wide tables scroll inside their card, and the closed phone menu waits off screen.
      if (element.closest('.table-scroll, .sidebar')) continue
      if (getComputedStyle(element).visibility === 'hidden') continue
      const box = element.getBoundingClientRect()
      if (box.width === 0) continue
      if (box.right > width + 1 || box.left < -1) {
        out.push(`${element.tagName.toLowerCase()}.${element.className} ${Math.round(box.left)}–${Math.round(box.right)}`)
      }
    }
    if (document.documentElement.scrollWidth > width) out.push(`page scrolls sideways to ${document.documentElement.scrollWidth}`)
    return out.slice(0, 8)
  })
}

async function open(page: Page, name: string, phone: boolean): Promise<void> {
  if (phone) {
    await page.getByRole('button', { name: 'Open menu' }).click()
    await expect(page.getByRole('navigation')).toBeVisible()
  }
  await page.getByRole('button', { name, exact: true }).click()
  await expect(page.locator('.page-loader')).toHaveCount(0, { timeout: 10_000 })
  if (phone) await expect(page.locator('.nav-backdrop')).toHaveCount(0)
}

for (const screen of SCREENS) {
  test(`every page fits a ${screen.name} screen (${screen.width}px)`, async ({ page }) => {
    await page.setViewportSize({ width: screen.width, height: screen.height })
    await signIn(page)
    const phone = screen.width < 768
    await expect(page.getByRole('button', { name: 'Open menu' })).toBeVisible({ visible: phone })
    expect(await spills(page), 'sign-in landing').toEqual([])
    for (const name of PAGES) {
      await open(page, name, phone)
      expect(await spills(page), name).toEqual([])
    }
  })
}
