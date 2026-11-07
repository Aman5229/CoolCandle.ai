/** Display only: ₹ with Indian grouping, tabular numbers, and times in IST. */

const rupees = new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const whole = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 })
const clock = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})

export function inr(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '–'
  const number = Number(value)
  const sign = number < 0 ? '−' : ''
  return `${sign}₹${rupees.format(Math.abs(number))}`
}

export function inrWhole(value: string | number): string {
  const number = Number(value)
  return `${number < 0 ? '−' : ''}₹${whole.format(Math.abs(number))}`
}

export function price(value: string | number | null | undefined): string {
  return value === null || value === undefined ? '–' : rupees.format(Number(value))
}

export function istTime(iso: string | null | undefined): string {
  return iso ? clock.format(new Date(iso)) : '–'
}

export const shortTime = (value: string): string => value.slice(0, 5)

export function pnlClass(value: string | number): string {
  const number = Number(value)
  return number > 0 ? 'up' : number < 0 ? 'down' : ''
}

const LOCK_MESSAGES: Record<string, string> = {
  LOSS_LIMIT: "You reached today's loss limit.",
  TARGET: "You reached today's profit target.",
  MAX_TRADES: 'You used all of today\'s trades.',
  SQUARE_OFF: 'The session is over for today.',
}

export function lockMessage(reason: string | null): string {
  return reason ? (LOCK_MESSAGES[reason] ?? 'Trading is locked.') : 'Trading is locked.'
}
