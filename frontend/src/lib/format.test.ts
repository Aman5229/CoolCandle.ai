import { parseConfirmLink } from '../features/signals/ConfirmPage'
import { inr, inrWhole, istTime, lockMessage, pnlClass, price } from './format'

describe('money', () => {
  it('groups rupees the Indian way', () => {
    expect(inr('100000')).toBe('₹1,00,000.00')
    expect(inrWhole('1500000')).toBe('₹15,00,000')
  })

  it('shows a loss with a real minus sign', () => {
    expect(inr('-2500.5')).toBe('−₹2,500.50')
  })

  it('shows a dash for nothing', () => {
    expect(inr(null)).toBe('–')
    expect(price(undefined)).toBe('–')
  })
})

describe('times', () => {
  it('shows UTC instants in IST', () => {
    expect(istTime('2026-11-11T04:30:00Z')).toBe('10:00')
  })
})

describe('colours', () => {
  it('follows the sign', () => {
    expect(pnlClass('10')).toBe('up')
    expect(pnlClass('-10')).toBe('down')
    expect(pnlClass('0')).toBe('')
  })
})

describe('lock messages', () => {
  it('names the reason', () => {
    expect(lockMessage('LOSS_LIMIT')).toContain('loss limit')
    expect(lockMessage(null)).toBe('Trading is locked.')
  })
})

describe('alert links', () => {
  it('reads the signed link', () => {
    expect(parseConfirmLink('#/confirm/abc-1?e=1790000000&s=ab12')).toEqual({
      id: 'abc-1',
      expires: '1790000000',
      signature: 'ab12',
    })
  })

  it('ignores anything else', () => {
    expect(parseConfirmLink('')).toBeNull()
    expect(parseConfirmLink('#/confirm/abc')).toBeNull()
  })
})
