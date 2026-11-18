import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, render, screen } from '@testing-library/react'
import { api, type Trade } from '../../lib/api'
import { closedSince, RejectionToast, rejectedSince, TradeToast } from './TradeToast'

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

describe('the rejection popup', () => {
  const margin = 'Not enough funds in Kotak: ₹5,978.55 available, ₹823.32 more needed'

  function popup(trades: Trade[]) {
    const client = new QueryClient()
    const view = (list: Trade[]) => (
      <QueryClientProvider client={client}>
        <RejectionToast trades={list} />
      </QueryClientProvider>
    )
    const result = render(view(trades))
    return (next: Trade[]) => result.rerender(view(next))
  }

  beforeEach(() => {
    vi.spyOn(api, 'timeline').mockResolvedValue([
      { ts: '2026-11-17T04:00:00Z', type: 'ENTRY_SUBMITTED', data: {} },
      { ts: '2026-11-17T04:00:01Z', type: 'ENTRY_REJECTED', data: { reason: margin } },
    ])
  })
  afterEach(() => vi.restoreAllMocks())

  it('counts only rejections that are new since the last look', () => {
    const now = [trade('a', 'REJECTED'), trade('b', 'REJECTED'), trade('c', 'OPEN')]
    expect(rejectedSince(new Set(['a']), now).map((t) => t.id)).toEqual(['b'])
  })

  it('stays quiet about rejections from before the page opened', () => {
    popup([trade('old', 'REJECTED')])
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })

  it('pops up with the reason when the broker rejects an order a moment after taking it', async () => {
    const update = popup([])
    update([trade('t1', 'ENTRY_PENDING')])
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    update([trade('t1', 'REJECTED')])
    expect(await screen.findByText(margin)).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('Order rejected')
  })

  it('pops up for an order refused at once, which is already rejected when first seen', async () => {
    const update = popup([trade('done', 'CLOSED')])
    update([trade('done', 'CLOSED'), trade('t2', 'REJECTED')])
    expect(await screen.findByText(margin)).toBeInTheDocument()
  })

  it('goes away by itself, so it never sits over a button', async () => {
    vi.useFakeTimers()
    const update = popup([])
    update([trade('t1', 'REJECTED')])
    expect(screen.getByRole('alert')).toBeInTheDocument()
    act(() => vi.advanceTimersByTime(12000))
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    vi.useRealTimers()
  })
})
