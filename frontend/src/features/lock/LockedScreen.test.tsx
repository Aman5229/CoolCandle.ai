import { render, screen } from '@testing-library/react'
import type { Day } from '../../lib/api'
import { LockedScreen } from './LockedScreen'

/** 17 Nov 2026: locked on the target at 10:23, then a trade in the broker's app lost ₹1,456. */
function locked(reason: string, net: string): Day {
  return {
    trading_date: '2026-11-17',
    mode: 'live',
    status: { state: 'LOCKED', reasons: [], lock_reason: reason },
    snapshot: {
      realised_pnl: net,
      unrealised_pnl: '0',
      est_charges: '67.85',
      net_pnl: net,
      peak_net_pnl: '875.13',
      trades_taken: 2,
      open_trade: false,
      last_loss_at: null,
      feed_age: 0.1,
      now: '2026-11-17T05:20:00Z',
    },
    plan: {
      trading_date: '2026-11-17',
      profit_target: '700',
      loss_limit: '500',
      max_trades: 1,
      max_loss_per_trade: '500',
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
    locked_at: '2026-11-17T04:53:59Z',
    lock_ends_at: null,
    market_open: true,
    broker_kill_switch_url: 'https://example.com',
  }
}

describe('the locked screen', () => {
  it('keeps the reason it locked for, and says when the day has since passed the loss limit', () => {
    render(<LockedScreen day={locked('TARGET', '-639.85')} />)
    expect(screen.getByRole('heading')).toHaveTextContent("You reached today's profit target.")
    expect(screen.getByTestId('past-loss-limit')).toHaveTextContent(
      /Since the lock at 10:23, the day has gone past your .*500.* loss limit/,
    )
  })

  it('says nothing more while the day stays inside the loss limit', () => {
    render(<LockedScreen day={locked('TARGET', '851.53')} />)
    expect(screen.queryByTestId('past-loss-limit')).not.toBeInTheDocument()
  })

  it('needs no extra line when the loss limit is what locked it', () => {
    render(<LockedScreen day={locked('LOSS_LIMIT', '-639.85')} />)
    expect(screen.queryByTestId('past-loss-limit')).not.toBeInTheDocument()
  })
})
