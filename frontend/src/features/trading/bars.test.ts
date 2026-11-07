import type { UTCTimestamp } from 'lightweight-charts'
import { bucketOf, glide, nextBar, type Bar } from './bars'

const bar = (time: number, open: number, high: number, low: number, close: number): Bar => ({
  time: time as UTCTimestamp, open, high, low, close,
})

describe('the live bar', () => {
  it('grows the current bar', () => {
    expect(nextBar(bar(60, 100, 102, 99, 101), 103, 60)).toEqual(bar(60, 100, 103, 99, 103))
    expect(nextBar(bar(60, 100, 102, 99, 101), 98, 60)).toEqual(bar(60, 100, 102, 98, 98))
  })

  it('opens a new bar at the first price of the next minute', () => {
    expect(nextBar(bar(60, 100, 102, 99, 101), 104, 120)).toEqual(bar(120, 104, 104, 104, 104))
    expect(nextBar(null, 104, 120)).toEqual(bar(120, 104, 104, 104, 104))
  })

  it('drops a price that arrives after its bar has closed', () => {
    expect(nextBar(bar(120, 100, 102, 99, 101), 104, 60)).toBeNull()
  })

  it('glides the close and keeps the rest of the bar true', () => {
    const from = bar(60, 100, 102, 99, 100)
    const to = bar(60, 100, 110, 99, 110)
    expect(glide(from, to, 0)).toEqual({ ...to, close: 100 })
    expect(glide(from, to, 1)).toEqual(to)
    const halfway = glide(from, to, 0.5).close
    expect(halfway).toBeGreaterThan(105) // eases out: most of the way there by half time
    expect(halfway).toBeLessThan(110)
  })
})

describe('the bar a tick belongs to', () => {
  it('counts 5-minute bars from the 09:15 open, in IST', () => {
    // 09:23:10 IST is 03:53:10 UTC, inside the 09:20 bar.
    const start = bucketOf('2026-11-11T03:53:10Z', '5m')
    expect(new Date(start * 1000).toISOString()).toBe('2026-11-11T09:20:00.000Z')
  })
})
