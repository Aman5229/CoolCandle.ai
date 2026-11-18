import { mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { signIn } from './helpers'

/* Skill Test end to end on the NIFTY year the seed stored (e2e/seed.py): no Kotak call. A run
   starts with Mon 10 Nov 2025 as its today. That day is quiet at 25,000 until one minute at 09:20
   reaches both 24,900 and 25,200. A call bought before it, stop 10 below and target 20 above, must
   be stopped first. The plan squares off at 09:30, which locks the day. */

const SHOTS = join(tmpdir(), 'coolcandle-e2e', 'shots')

async function openSkill(page: Page, phone: boolean): Promise<void> {
  if (phone) await page.getByRole('button', { name: 'Open menu' }).click()
  await page.getByRole('button', { name: 'Skill Test', exact: true }).click()
  await expect(page.locator('.page-loader')).toHaveCount(0, { timeout: 10_000 })
  expect(await page.evaluate(() => document.documentElement.dataset.arena)).toBe('skill')
}

async function playADay(page: Page, phone: boolean): Promise<void> {
  mkdirSync(SHOTS, { recursive: true })
  const tag = phone ? 'phone' : 'laptop'
  const shot = (name: string, fullPage = false) =>
    page.screenshot({ path: join(SHOTS, `skill-${tag}-${name}.png`), fullPage })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })
  await signIn(page)
  await openSkill(page, phone)

  // A fresh run each time, its today the first day with a year of history behind it.
  const retire = page.getByRole('button', { name: 'Retire this run' })
  const startRun = page.getByRole('button', { name: /Start a run with/ })
  await expect(retire.or(startRun)).toBeVisible()
  if (await retire.isVisible()) {
    await retire.click()
    await page.getByLabel('Type RETIRE').fill('RETIRE')
    await expect(page.getByLabel('Start day')).toHaveValue('2025-11-10')
    await page.getByRole('button', { name: 'Retire and start a new run' }).click()
  } else {
    await expect(page.getByLabel('Start day')).toHaveValue('2025-11-10')
    await startRun.click()
  }
  await expect(page.getByTestId('skill-today')).toHaveText('Mon 10 Nov 2025')
  await expect(page.getByText(/fake/i)).toHaveCount(0)
  await shot('1-lobby')

  await page.getByRole('button', { name: /Play Mon 10 Nov 2025/ }).click()
  await page.getByLabel('Entries from').fill('09:15')
  await page.getByLabel('Entries until').fill('09:25')
  await page.getByLabel('Square-off').fill('09:30')
  await page.getByRole('button', { name: 'Review plan' }).click()
  await page.getByLabel('Type START').fill('START')
  await shot('2-confirm')
  await page.getByRole('button', { name: 'Start', exact: true }).click()

  await expect(page.getByLabel('Session HUD')).toBeVisible()
  await expect(page.getByTestId('skill-score')).toContainText('100')
  const chain = page.getByLabel('Option chain')
  await chain.locator('tr.atm button').first().click() // the call at the money
  const quote = page.getByTestId('ticket-ask')
  await expect(quote).toContainText('Ask')
  const ask = Number((/Ask ([\d,.]+)/.exec((await quote.textContent()) ?? '')?.[1] ?? '0').replaceAll(',', ''))
  expect(ask).toBeGreaterThan(20)
  await page.getByLabel('Stop price').fill((ask - 10).toFixed(2))
  await page.getByLabel('Target price').fill((ask + 20).toFixed(2))
  const buy = page.getByRole('button', { name: 'Buy 1 lot' })
  await expect(buy).toBeEnabled({ timeout: 10_000 })
  await buy.click()
  await expect(page.getByLabel('Open trade')).toBeVisible()
  await shot('3-open-trade')
  if (!phone) {
    await page.reload() // a refresh stays on the running day, trade and all
    await expect(page).toHaveURL(/#\/skill\/day\//)
    await expect(page.getByLabel('Open trade')).toBeVisible({ timeout: 15_000 })
  }

  await page.getByRole('button', { name: '60x' }).click()
  const trades = page.getByLabel("This session's trades")
  await expect(trades.getByText('STOP', { exact: true })).toBeVisible({ timeout: 20_000 })
  await expect(trades.getByText('TARGET', { exact: true })).toHaveCount(0)
  await expect(page.getByTestId('skill-score')).toContainText('90') // lost more than 1R
  await shot('4-stopped')
  const locked = page.getByRole('dialog', { name: 'Day over' })
  await expect(locked).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('lock-score')).toHaveText('90')
  await shot('5-locked')
  await locked.getByRole('button', { name: 'See report card' }).click()

  await expect(page.getByTestId('report-score')).toHaveText('90')
  await page.reload() // and on the report card
  await expect(page).toHaveURL(/#\/skill\/report\//)
  await expect(page.getByTestId('report-score')).toHaveText('90', { timeout: 15_000 })
  await expect(page.locator('p', { hasText: 'Biggest mistake:' })).toContainText(
    'more than the risk you planned',
  )
  await shot('6-report', true)
  const width = await page.evaluate(() => document.documentElement.scrollWidth)
  expect(width).toBeLessThanOrEqual(page.viewportSize()?.width ?? width)

  if (phone) {
    await page.getByRole('button', { name: '← Back to lobby' }).click() // at the top of the report
  } else {
    await page.getByRole('button', { name: 'Skill Test', exact: true }).click() // the menu, again
  }
  await expect(page.getByTestId('skill-today')).toHaveText('Tue 11 Nov 2025') // the day is spent
  await expect(page.getByRole('button', { name: /^90 NIFTY/ }).first()).toBeVisible()
  expect(errors.filter((e) => !e.includes('401'))).toEqual([]) // the signed-out check before sign-in
}

test('a replayed NIFTY day, dark, on a laptop: stop first, square-off, report', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('theme', 'dark'))
  await page.setViewportSize({ width: 1440, height: 900 })
  await playADay(page, false)
})

test('the same day, light, on a 390px phone', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('theme', 'light'))
  await page.setViewportSize({ width: 390, height: 844 })
  await playADay(page, true)
})

test('timeframes reach back a year, and leaving brings back the live header', async ({ page }) => {
  await signIn(page)
  await openSkill(page, false)
  await expect(page.getByTestId('day-state')).toHaveCount(0) // no live header inside Skill Test
  const hud = page.getByLabel('Session HUD') // a session still running opens straight into its day
  const resume = page.getByRole('button', { name: 'Resume it' })
  const play = page.getByRole('button', { name: /Play / })
  await expect(hud.or(resume).or(play)).toBeVisible()
  if (await resume.isVisible()) {
    await resume.click()
  } else if (await play.isVisible()) {
    await play.click()
    await page.getByRole('button', { name: 'Review plan' }).click()
    await page.getByLabel('Type START').fill('START')
    await page.getByRole('button', { name: 'Start', exact: true }).click()
  }
  const daily = page.waitForResponse((r) => r.url().includes('/candles?interval=1d'))
  await page.getByRole('button', { name: '1D', exact: true }).click()
  const bars = (await (await daily).json()) as { start: string }[]
  expect(bars.length).toBeGreaterThan(200) // a year of days behind today
  await expect(page.getByRole('button', { name: '1D', exact: true })).toHaveAttribute('aria-pressed', 'true')
  await page.getByRole('button', { name: 'Trading', exact: true }).click()
  await expect(page.getByTestId('day-state')).toBeVisible()
  await expect.poll(() => page.evaluate(() => document.documentElement.dataset.arena ?? '')).toBe('')
})
