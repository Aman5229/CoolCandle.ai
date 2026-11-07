import type { CandlestickData, UTCTimestamp } from 'lightweight-charts'
import type { Candle } from '../../lib/api'

// The chart draws times as UTC, so every time is shifted +5:30 to make the axis read IST.
const IST_OFFSET_S = 5.5 * 3600
const SESSION_OPEN_S = (9 * 60 + 15) * 60
const DAY_S = 86400
const MINUTES = { '1m': 1, '5m': 5 } as const

export type Interval = keyof typeof MINUTES
export type Bar = CandlestickData<UTCTimestamp>

export function toBar(candle: Candle): Bar {
  return {
    time: (Date.parse(candle.start) / 1000 + IST_OFFSET_S) as UTCTimestamp,
    open: Number(candle.open),
    high: Number(candle.high),
    low: Number(candle.low),
    close: Number(candle.close),
  }
}

/** The start of the bar a tick belongs to, counted from the 09:15 open. */
export function bucketOf(iso: string, interval: Interval): number {
  const shifted = Math.floor(Date.parse(iso) / 1000) + IST_OFFSET_S
  const day = shifted - (shifted % DAY_S)
  const size = MINUTES[interval] * 60
  const sinceOpen = shifted - day - SESSION_OPEN_S
  return day + SESSION_OPEN_S + Math.floor(sinceOpen / size) * size
}

/** The last bar after one more price: the same bar grown, a new bar, or null for a late tick. */
export function nextBar(previous: Bar | null, price: number, start: number): Bar | null {
  if (previous && start === previous.time) {
    return {
      ...previous,
      high: Math.max(previous.high, price),
      low: Math.min(previous.low, price),
      close: price,
    }
  }
  if (!previous || start > previous.time) {
    return { time: start as UTCTimestamp, open: price, high: price, low: price, close: price }
  }
  return null
}

/** Part of the way from one close to the next, easing out, so a live bar glides. */
export function glide(from: Bar, to: Bar, progress: number): Bar {
  const eased = 1 - (1 - progress) ** 3
  return { ...to, close: from.close + (to.close - from.close) * eased }
}
