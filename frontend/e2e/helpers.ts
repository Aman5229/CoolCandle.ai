import { createHmac } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { expect, type Page } from '@playwright/test'
import { EMAIL, PASSWORD } from './constants'

function base32(text: string): Buffer {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
  let bits = ''
  for (const char of text) bits += alphabet.indexOf(char).toString(2).padStart(5, '0')
  const bytes = bits.match(/.{8}/g) ?? []
  return Buffer.from(bytes.map((byte) => parseInt(byte, 2)))
}

export function totp(secret: string): string {
  const counter = Buffer.alloc(8)
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)))
  const hash = createHmac('sha1', base32(secret)).update(counter).digest()
  const offset = (hash.at(-1) as number) & 0xf
  const code = (hash.readUInt32BE(offset) & 0x7fffffff) % 1_000_000
  return String(code).padStart(6, '0')
}

/** The seed script printed the authenticator secret here (see playwright.config.ts). */
export function secret(): string {
  const output = readFileSync(join(tmpdir(), 'coolcandle-e2e', 'secret'), 'utf8').trim()
  return output.split('\n').at(-1) ?? ''
}

export async function signIn(page: Page): Promise<void> {
  await page.goto('/')
  await page.getByLabel('Email').fill(EMAIL)
  await page.getByLabel('Password').fill(PASSWORD)
  await page.getByLabel('Authenticator code').fill(totp(secret()))
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByTestId('day-state')).toBeVisible()
}
