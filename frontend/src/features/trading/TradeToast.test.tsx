import { act, render, screen } from '@testing-library/react'
import type { Trade } from '../../lib/api'
import { closedSince, TradeToast } from './TradeToast'

function trade(id: string, state: string, changes: Partial<Trade> = {}): Trade {
  return {
    id,
    state,
    instrument_key: 'nse_fo|1',
    trading_symbol: 'NIFTY26NOV24500CE',
    qty: 65,
    filled_qty: 65,
    entry_limit: '150',
    entry_avg: '150',
    exit_avg: null,
    stop_trigger: '135',
    target: null,
    realised_pnl: '0',
    source: 'MANUAL',
    opened_at: null,
    closed_at: null,
    exit_reason: null,
    trailing: null,
    ...changes,
  }
}

describe('closed trades', () => {
  it('are the ones that were active and are now closed', () => {
    const now = [trade('a', 'CLOSED'), trade('b', 'CLOSED'), trade('c', 'CANCELLED')]
    expect(closedSince(new Set(['a', 'c']), now).map((t) => t.id)).toEqual(['a'])
  })
})

describe('the toast', () => {
  it('says nothing about trades that were closed before the page opened', () => {
    render(<TradeToast trades={[trade('a', 'CLOSED', { realised_pnl: '500' })]} />)
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('reports a trade as it closes, with its P&L and reason', () => {
    const { rerender } = render(<TradeToast trades={[trade('a', 'OPEN')]} />)
    rerender(<TradeToast trades={[trade('a', 'CLOSED', { realised_pnl: '-650', exit_reason: 'STOP_HIT' })]} />)
    const toast = screen.getByRole('status')
    expect(toast).toHaveTextContent('Trade closed')
    expect(toast).toHaveTextContent('NIFTY26NOV24500CE')
    expect(toast).toHaveTextContent('STOP HIT')
    expect(toast.querySelector('b')).toHaveClass('down')
  })

  it('goes away by itself', () => {
    vi.useFakeTimers()
    const { rerender } = render(<TradeToast trades={[trade('a', 'OPEN')]} />)
    rerender(<TradeToast trades={[trade('a', 'CLOSED')]} />)
    expect(screen.getByRole('status')).toBeInTheDocument()
    act(() => vi.advanceTimersByTime(5000))
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    vi.useRealTimers()
  })
})
