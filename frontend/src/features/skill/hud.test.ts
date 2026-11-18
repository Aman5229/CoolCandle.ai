import { describe, expect, it } from 'vitest'
import type { SkillTrade } from './api'
import {
  clockText,
  dayText,
  defaultPlan,
  meterSpan,
  secondsUntil,
  tradeMarkers,
  tradeNumbers,
  tradesLeft,
} from './hud'

function trade(changes: Partial<SkillTrade>): SkillTrade {
  return {
    id: 't1',
    instrument_key: 'skill|NIFTY|2025-11-11|25000|CE',
    trading_symbol: 'NIFTY 11 NOV 25000 CE',
    qty: 65,
    entry: '85.25',
    stop_at_fill: '65',
    stop: '65',
    target: '125',
    exit: null,
    r_risk: '1350',
    pnl: '0',
    fees: '0',
    opened_at: '2025-11-10T04:00:20Z', // 09:30:20 IST
    closed_at: null,
    exit_kind: null,
    stop_widened: false,
    status: 'OPEN',
    ...changes,
  }
}

const colours = { up: 'green', down: 'red', open: 'grey' }

describe('the P&L meter', () => {
  it('puts zero where it falls between the loss limit and the target', () => {
    expect(meterSpan(0, 1000, 3000)).toEqual({ zero: 25, left: 25, width: 0 })
  })
  it('fills right of zero for a profit and left for a loss', () => {
    expect(meterSpan(1500, 1000, 3000)).toEqual({ zero: 25, left: 25, width: 37.5 })
    expect(meterSpan(-500, 1000, 3000)).toEqual({ zero: 25, left: 12.5, width: 12.5 })
  })
  it('stops at the ends of the track', () => {
    expect(meterSpan(-9000, 1000, 3000).left).toBe(0)
    expect(meterSpan(9000, 1000, 3000)).toEqual({ zero: 25, left: 25, width: 75 })
  })
})

describe('the HUD numbers', () => {
  it('counts trades left and never goes below zero', () => {
    const plan = defaultPlan(100000)
    expect(tradesLeft(plan, 1)).toBe(3)
    expect(tradesLeft(plan, 9)).toBe(0)
  })
  it('counts down to the square-off on the replayed day', () => {
    expect(secondsUntil('15:15:00', '2025-11-10T09:43:30Z')).toBe(90) // 15:13:30 IST
    expect(secondsUntil('15:15', '2025-11-10T09:45:00Z')).toBe(0)
  })
  it('shows minutes and seconds, and hours once there are any', () => {
    expect(clockText(90)).toBe('01:30')
    expect(clockText(3725)).toBe('1:02:05')
  })
  it('scales a starting plan to the fund, with the live day times', () => {
    expect(defaultPlan(100000)).toMatchObject({
      profit_target: '2000',
      loss_limit: '1500',
      max_loss_per_trade: '800',
      entry_start: '09:20',
      square_off: '15:15',
    })
  })
  it('names a replayed day', () => {
    expect(dayText('2025-11-10')).toBe('Mon 10 Nov 2025')
  })
})

describe('chart markers', () => {
  it('marks buys with arrows and sells with dots, coloured by the result', () => {
    const won = trade({ id: 'a', closed_at: '2025-11-10T04:05:10Z', pnl: '763.68' })
    const lost = trade({
      id: 'b',
      opened_at: '2025-11-10T04:20:00Z',
      closed_at: '2025-11-10T04:21:00Z',
      pnl: '-934.66',
    })
    const marks = tradeMarkers([lost, won], colours, '1m', 'b')
    expect(marks.map((m) => [m.shape, m.color, m.text, m.size])).toEqual([
      ['arrowUp', 'green', '#1', 1],
      ['circle', 'green', '', 1],
      ['arrowUp', 'red', '#2', 2],
      ['circle', 'red', '', 2],
    ])
    const times = marks.map((m) => m.time)
    expect(times).toEqual(marks.map((m) => m.time).sort((x, y) => x - y))
  })
  it('places a marker on the bar of the timeframe shown', () => {
    const [oneMinute] = tradeMarkers([trade({})], colours, '1m')
    const [hourly] = tradeMarkers([trade({})], colours, '1h')
    expect((oneMinute?.time ?? 0) - (hourly?.time ?? 0)).toBe(15 * 60) // 09:30 in the 09:15 hour
  })
  it('leaves out an order that never filled, and greys an open trade', () => {
    const marks = tradeMarkers([trade({}), trade({ id: 'p', entry: null })], colours)
    expect(marks).toHaveLength(1)
    expect(marks[0]?.color).toBe('grey')
  })
  it('numbers trades in the order they were taken', () => {
    const later = trade({ id: 'later', opened_at: '2025-11-10T05:00:00Z' })
    expect([...tradeNumbers([later, trade({})]).entries()]).toEqual([['t1', 1], ['later', 2]])
  })
})
