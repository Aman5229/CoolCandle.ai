import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { skillApi, type AccountView, type RunView } from './api'
import { Lobby } from './Lobby'

const RUN: RunView = {
  id: 'r1',
  number: 2,
  start_fund: '100000',
  cash: '100763.68',
  status: 'ACTIVE',
  today: '2025-11-10',
  started_at: '2026-11-08T10:00:00Z',
  ended_at: null,
  peak_equity: '100763.68',
  blown_line: '70000',
}

const ACCOUNT: AccountView = {
  run: null,
  running: null,
  recent: [],
  best_score: null,
  sessions_played: 0,
  runs: 0,
  indices: ['NIFTY', 'SENSEX'],
  first_day: '2023-11-08',
  last_day: '2026-11-06',
  earliest_start: '2024-11-08',
  lot_sizes: { NIFTY: 65, SENSEX: 20 },
  settings: {
    blown_pct: '30',
    min_stop_pct: '0.5',
    revenge_minutes: 3,
    when_live_locked: false,
  },
  live_locked: false,
}

function lobby(account: AccountView, onStart = vi.fn()) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <Lobby account={account} onStart={onStart} onResume={vi.fn()} onReport={vi.fn()} onHistory={vi.fn()} />
    </QueryClientProvider>,
  )
  return onStart
}

afterEach(() => vi.restoreAllMocks())

describe('the Skill Test lobby', () => {
  it('says how to fetch the history when there is none', () => {
    lobby({ ...ACCOUNT, indices: [], first_day: null, last_day: null, earliest_start: null })
    expect(screen.getByText('uv run coolcandle fetch-index-history')).toBeInTheDocument()
  })

  it('starts a run at the fund and the day picked', async () => {
    const start = vi.spyOn(skillApi, 'startRun').mockResolvedValue(RUN)
    lobby(ACCOUNT)
    const day = screen.getByLabelText('Start day')
    expect(day).toHaveValue('2024-11-08') // a year after the first stored day
    expect(day).toHaveAttribute('max', '2026-11-06')
    fireEvent.change(day, { target: { value: '2025-11-08' } })
    fireEvent.click(screen.getByRole('button', { name: '₹5,00,000' }))
    fireEvent.click(screen.getByRole('button', { name: 'Start a run with ₹5,00,000' }))
    await vi.waitFor(() => expect(start).toHaveBeenCalledWith('500000', '2025-11-08', undefined))
  })

  it("shows the run's today and plays it", () => {
    const onStart = lobby({ ...ACCOUNT, run: RUN, runs: 2 })
    expect(screen.getByTestId('skill-fund')).toHaveTextContent('₹1,00,763.68')
    expect(screen.getByTestId('skill-today')).toHaveTextContent('Mon 10 Nov 2025')
    fireEvent.click(screen.getByRole('button', { name: /Play Mon 10 Nov 2025/ }))
    expect(onStart).toHaveBeenCalled()
  })

  it('is closed while the live day is locked, by default', () => {
    lobby({ ...ACCOUNT, run: RUN, live_locked: true })
    expect(screen.getByText(/Skill Test is closed until it unlocks/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Play / })).toBeDisabled()
  })

  it('only shows a banner when the setting keeps it open', () => {
    lobby({
      ...ACCOUNT,
      run: RUN,
      live_locked: true,
      settings: { ...ACCOUNT.settings, when_live_locked: true },
    })
    expect(screen.getByText('Live is locked for today. This is practice only.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Play / })).toBeEnabled()
  })
})
