import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import type { ReactNode } from 'react'
import { LockedScreen } from '../features/lock/LockedScreen'
import { OrderTicket } from '../features/trading/OrderTicket'
import type { Day, DayState } from '../lib/api'
import { Header, PnlMeter } from './Header'

function day(state: DayState, changes: Partial<Day> = {}): Day {
  return {
    trading_date: '2026-11-11',
    mode: 'paper',
    status: { state, reasons: [], lock_reason: state === 'LOCKED' ? 'LOSS_LIMIT' : null },
    snapshot: {
      realised_pnl: '0',
      unrealised_pnl: '0',
      est_charges: '40',
      net_pnl: '-3000',
      peak_net_pnl: '0',
      trades_taken: 2,
      open_trade: false,
      last_loss_at: null,
      feed_age: 0.4,
      now: '2026-11-11T04:30:00Z',
    },
    plan: {
      trading_date: '2026-11-11',
      profit_target: '5000',
      loss_limit: '3000',
      max_trades: 3,
      max_loss_per_trade: '1500',
      entry_start: '09:20:00',
      entry_cutoff: '14:30:00',
      square_off: '15:15:00',
      cool_off_minutes: 0,
      min_reward_risk: '1',
      allowed_underlyings: ['NIFTY'],
      trail_at_target: false,
      target_giveback_pct: '20',
      status: 'LOCKED',
      confirmations: 2,
    },
    locked_at: null,
    lock_ends_at: null,
    market_open: true,
    broker_kill_switch_url: 'https://example.com',
    ...changes,
  }
}

function withClient(node: ReactNode) {
  return <QueryClientProvider client={new QueryClient()}>{node}</QueryClientProvider>
}

describe('the header', () => {
  it.each([
    ['NO_PLAN', 'No plan'],
    ['ACTIVE', 'Active'],
    ['WARNING', 'Warning'],
    ['COOL_OFF', 'Cooling off'],
    ['ENTRIES_CLOSED', 'Entries closed'],
    ['LOCKED', 'Locked'],
  ] as [DayState, string][])('shows %s as %s', (state, text) => {
    render(withClient(<Header day={day(state)} me={undefined} />))
    expect(screen.getByTestId('day-state')).toHaveTextContent(text)
  })

  it('counts the trades left', () => {
    render(withClient(<Header day={day('ACTIVE')} me={undefined} />))
    expect(screen.getByText('Trades left')).toBeInTheDocument()
  })

  it('warns when prices are simulated', () => {
    const me = { email: 'a', mode: 'paper', account_id: 'x', is_simulated: true } as const
    render(withClient(<Header day={day('ACTIVE')} me={me} />))
    expect(screen.getByText('Simulated prices')).toBeInTheDocument()
  })

  it('shows the mode badge', () => {
    render(withClient(<Header day={day('ACTIVE', { mode: 'live' })} me={undefined} />))
    expect(screen.getByText('LIVE')).toBeInTheDocument()
  })
})

describe('the meter', () => {
  it('runs from the loss limit to the target', () => {
    render(<PnlMeter day={day('ACTIVE')} />)
    const meter = screen.getByRole('meter')
    expect(meter).toHaveAttribute('aria-valuemin', '-3000')
    expect(meter).toHaveAttribute('aria-valuemax', '5000')
    expect(meter).toHaveAttribute('aria-valuenow', '-3000')
  })
})

describe('the locked screen', () => {
  it('names the reason and offers no way out', () => {
    render(<LockedScreen day={day('LOCKED')} />)
    expect(screen.getByRole('heading')).toHaveTextContent("today's loss limit")
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
    expect(screen.getByRole('link')).toHaveTextContent("Kotak's kill switch")
  })

  it('snaps a padlock shut for a loss, trade-count or square-off lock', () => {
    const { container } = render(<LockedScreen day={day('LOCKED')} />)
    expect(container.querySelector('.padlock.closing')).toBeInTheDocument()
    expect(container.querySelector('[data-cc-logo]')).not.toBeInTheDocument()
  })

  it('closes the brand ring when the target is hit', () => {
    const target = day('LOCKED', { status: { state: 'LOCKED', reasons: [], lock_reason: 'TARGET' } })
    const { container } = render(<LockedScreen day={target} />)
    expect(container.querySelector('[data-cc-logo]')).toHaveAttribute('data-play', 'once')
    expect(container.querySelector('.padlock')).not.toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })
})

describe('the ticket', () => {
  const option = {
    key: 'nse_fo|1',
    trading_symbol: 'NIFTY24500CE',
    ltp: '100',
    bid: '99.95',
    ask: '100',
    oi: 1,
  }

  it('will not place an order with no stop', () => {
    render(withClient(<OrderTicket option={option} canTrade />))
    expect(screen.getByRole('button', { name: /Buy/ })).toBeDisabled()
  })

  it('will not place an order when the day cannot trade', () => {
    render(withClient(<OrderTicket option={option} canTrade={false} />))
    expect(screen.getByRole('button', { name: /Buy/ })).toBeDisabled()
  })
})
