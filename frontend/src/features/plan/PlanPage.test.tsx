import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { api, type Mode, type Plan } from '../../lib/api'
import { PlanPage } from './PlanPage'

const LOCKED: Plan = {
  trading_date: '2026-11-09',
  profit_target: '5000',
  loss_limit: '3000',
  max_trades: 3,
  max_loss_per_trade: '1500',
  entry_start: '09:20:00',
  entry_cutoff: '14:30:00',
  square_off: '15:15:00',
  cool_off_minutes: 10,
  min_reward_risk: '1',
  allowed_underlyings: ['NIFTY', 'SENSEX'],
  trail_at_target: false,
  target_giveback_pct: '20',
  status: 'LOCKED',
  confirmations: 2,
}

function page(mode: Mode) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <PlanPage mode={mode} onTrade={() => {}} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('a locked plan', () => {
  it('can still be changed in paper', async () => {
    vi.spyOn(api, 'plan').mockResolvedValue(LOCKED)
    page('paper')
    expect(await screen.findByText(/you can still change any limit/)).toBeInTheDocument()
    expect(screen.getByText('PAPER plan')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Make stricter' })).not.toBeInTheDocument()
  })

  it('only gets stricter in live', async () => {
    vi.spyOn(api, 'plan').mockResolvedValue(LOCKED)
    page('live')
    expect(await screen.findByText(/You can only make it stricter/)).toBeInTheDocument()
    expect(screen.getByText('LIVE plan')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Make stricter' })).toBeInTheDocument()
  })

  it('sends a looser paper edit and says it was saved', async () => {
    vi.spyOn(api, 'plan').mockResolvedValue(LOCKED)
    const save = vi.spyOn(api, 'savePlan').mockResolvedValue({ ...LOCKED, max_trades: 5 })
    page('paper')
    await screen.findByRole('button', { name: 'Save changes' }) // the saved plan has loaded
    fireEvent.change(screen.getByLabelText('Max trades'), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    expect(await screen.findByText('Saved.')).toBeInTheDocument()
    expect(save).toHaveBeenCalledWith(expect.objectContaining({ max_trades: 5 }))
  })
})
