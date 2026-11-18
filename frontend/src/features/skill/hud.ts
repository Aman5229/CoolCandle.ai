/** Skill Test's HUD and ticket maths. Pure, so it is tested without a browser. */

import type { UTCTimestamp } from 'lightweight-charts'
import { bucketOf, type Interval } from '../trading/bars'
import type { SessionPlan, SkillLock, SkillTrade } from './api'

/** Where the session's P&L sits on a track from −loss limit to +target, in percent. */
export function meterSpan(net: number, loss: number, target: number) {
  const span = loss + target
  const zero = (loss / span) * 100
  const at = Math.min(Math.max(((net + loss) / span) * 100, 0), 100)
  return { zero, left: Math.min(at, zero), width: Math.abs(at - zero) }
}

export function tradesLeft(plan: SessionPlan, taken: number): number {
  return Math.max(plan.max_trades - taken, 0)
}

const IST_MS = 5.5 * 3600 * 1000

/** Whole seconds from `now` to an IST time of day ("15:15:00") on the same IST day. */
export function secondsUntil(istTime: string, now: string): number {
  const moment = Date.parse(now)
  const day = new Date(moment + IST_MS).toISOString().slice(0, 10)
  const target = Date.parse(`${day}T${istTime.slice(0, 8).padEnd(8, ':00')}+05:30`)
  return Math.max(Math.floor((target - moment) / 1000), 0)
}

export function clockText(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  const two = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${two(m)}:${two(s)}` : `${two(m)}:${two(s)}`
}

export const LOCK_TITLES: Record<SkillLock, string> = {
  TARGET: 'Target hit 🎯',
  LOSS_LIMIT: 'Loss limit reached',
  MAX_TRADES: 'Max trades used',
  SQUARE_OFF: 'Day over',
  BLOWN: 'Account blown',
}

/** A starting plan scaled to the fund (2% target, 1.5% loss limit, 0.75% a trade), with the
 * live day's times. */
export function defaultPlan(fund: number): SessionPlan {
  const share = (pct: number) => String(Math.max(Math.round((fund * pct) / 100 / 100) * 100, 100))
  return {
    profit_target: share(2),
    loss_limit: share(1.5),
    max_trades: 4,
    max_loss_per_trade: share(0.75),
    cool_off_minutes: 5,
    min_reward_risk: '1.5',
    entry_start: '09:20',
    entry_cutoff: '14:30',
    square_off: '15:15',
  }
}

export interface MarkerSpec {
  time: UTCTimestamp
  position: 'aboveBar' | 'belowBar' | 'inBar'
  shape: 'arrowUp' | 'arrowDown' | 'circle'
  color: string
  text: string
  size: number
}

function taken(trades: SkillTrade[]): SkillTrade[] {
  return trades
    .filter((t) => t.entry !== null)
    .sort((a, b) => Date.parse(a.opened_at) - Date.parse(b.opened_at))
}

/** On the index chart: an arrow under the bar each option was bought in and a dot where it was
 * sold, coloured by the result and numbered in order. The focused trade is drawn larger. */
export function tradeMarkers(
  trades: SkillTrade[],
  colours: { up: string; down: string; open: string },
  interval: Interval = '1m',
  focus: string | null = null,
): MarkerSpec[] {
  const marks: MarkerSpec[] = []
  taken(trades).forEach((trade, index) => {
    const color =
      trade.closed_at === null ? colours.open : Number(trade.pnl) >= 0 ? colours.up : colours.down
    const size = trade.id === focus ? 2 : 1
    marks.push({
      time: bucketOf(trade.opened_at, interval) as UTCTimestamp,
      position: 'belowBar',
      shape: 'arrowUp',
      color,
      text: `#${index + 1}`,
      size,
    })
    if (trade.closed_at) {
      marks.push({
        time: bucketOf(trade.closed_at, interval) as UTCTimestamp,
        position: 'aboveBar',
        shape: 'circle',
        color,
        text: '',
        size,
      })
    }
  })
  return marks.sort((a, b) => a.time - b.time)
}

/** Trade numbers in the order they were taken, for the report's breakdown. */
export function tradeNumbers(trades: SkillTrade[]): Map<string, number> {
  return new Map(taken(trades).map((t, i) => [t.id, i + 1]))
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

/** "Mon 10 Nov 2025", for a replayed day, the same in every browser. */
export function dayText(day: string): string {
  const date = new Date(`${day}T00:00:00Z`)
  return `${WEEKDAYS[date.getUTCDay()]} ${date.getUTCDate()} ${MONTHS[date.getUTCMonth()]} ${date.getUTCFullYear()}`
}
