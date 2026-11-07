import { mkdirSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { expect, test, type Page } from '@playwright/test'
import { EMAIL, PASSWORD } from './constants'
import { secret, totp } from './helpers'

const SHOTS = join(tmpdir(), 'coolcandle-e2e', 'shots')

const shot = (page: Page, name: string) => page.screenshot({ path: join(SHOTS, `${name}.png`) })

test('sign in, lock a plan, paper trade, hit the lock, see the greyed-out day', async ({ page }) => {
  mkdirSync(SHOTS, { recursive: true })
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('console', (message) => {
    if (message.type() === 'error') errors.push(message.text())
  })

  await page.goto('/')
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeVisible()
  await shot(page, '1-sign-in')
  await page.getByLabel('Email').fill(EMAIL)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByLabel('Authenticator code').fill(totp(secret()))
  await page.getByRole('button', { name: 'Sign in' }).click()

  await expect(page.getByTestId('day-state')).toHaveText('No plan')
  await expect(page.getByRole('button', { name: 'PAPER' })).toBeVisible()
  await expect(page.getByText('ordering is off')).toBeVisible()

  await page.getByRole('button', { name: "Today's plan", exact: true }).click()
  await page.getByLabel('Profit target (₹)').fill('50000')
  await page.getByLabel('Loss limit (₹)').fill('20000')
  await page.getByLabel('Max trades').fill('1')
  await page.getByLabel('Max loss a trade (₹)').fill('10000')
  await page.getByLabel('Cool-off after a loss (min)').fill('0')
  await shot(page, '2-plan')
  await page.getByRole('button', { name: 'Review and lock' }).click()

  const dialog = page.getByRole('dialog')
  await expect(dialog).toBeVisible()
  await expect(dialog.getByRole('button', { name: 'Lock the plan' })).toBeDisabled()
  await dialog.getByLabel('Type LOCK').fill('LOCK')
  await shot(page, '3-confirm')
  await dialog.getByRole('button', { name: 'Lock the plan' }).click()
  await expect(page.getByText('You can only make it stricter')).toBeVisible()

  await page.getByRole('button', { name: 'Trading', exact: true }).click()
  await expect(page.getByTestId('day-state')).toHaveText(/Active|Warning/)
  const atm = page.locator('tr.atm .cell').first()
  await expect(atm).toBeVisible({ timeout: 20_000 })
  await expect(atm).not.toHaveText('–', { timeout: 20_000 })
  await atm.click()

  const price = Number((await page.getByLabel('Limit price').inputValue()) || '0')
  expect(price).toBeGreaterThan(0)
  await expect(page.getByRole('button', { name: /Buy/ })).toBeDisabled()
  await page.getByLabel('Stop price').fill((price * 0.9).toFixed(2))
  await expect(page.getByText('Worst-case loss')).toBeVisible()
  await expect(page.getByRole('button', { name: /Buy/ })).toBeEnabled({ timeout: 10_000 })
  await shot(page, '4-ticket')
  await page.getByRole('button', { name: /Buy/ }).click()

  const open = page.getByTestId('open-trade')
  await expect(open).toBeVisible({ timeout: 15_000 })
  await expect(open.getByText('OPEN')).toBeVisible({ timeout: 15_000 })
  await shot(page, '5-open-trade')
  await open.getByRole('button', { name: 'Exit now' }).click()

  const locked = page.getByTestId('locked-screen')
  await expect(locked).toBeVisible({ timeout: 20_000 })
  await expect(locked).toContainText("all of today's trades")
  await expect(page.getByTestId('day-state')).toHaveText('Locked')
  await expect(locked).toContainText(/Locked at\s*\d\d:\d\d/, { timeout: 2000 })
  await expect(page.getByRole('button', { name: /Buy/ })).toHaveCount(0)
  await shot(page, '6-locked')

  await page.reload()
  await expect(page.getByTestId('locked-screen')).toBeVisible()
  await expect(page.getByTestId('locked-screen')).toContainText(/Locked at\s*\d\d:\d\d/)

  await page.getByRole('button', { name: 'Journal' }).click()
  await expect(page.getByText('MANUAL')).toBeVisible()
  await shot(page, '7-journal')

  await page.getByRole('button', { name: 'Light mode' }).click()
  await page.waitForTimeout(1200)
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await page.getByRole('button', { name: 'Trading', exact: true }).click()
  await shot(page, '8-light')
  await page.getByRole('button', { name: 'Buddy' }).click()
  await shot(page, '9-buddy')

  expect(errors.filter((e) => !e.includes('401'))).toEqual([])
})
