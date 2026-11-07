import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, within } from '@testing-library/react'
import { api } from '../../lib/api'
import { closedReason, JournalPage } from './JournalPage'

const HOLIDAYS = new Map([['2026-11-10', 'Diwali-Balipratipada']])

describe('closed days', () => {
  it('names a holiday', () => {
    expect(closedReason('2026-11-10', HOLIDAYS)).toBe('Diwali-Balipratipada')
  })

  it('calls Saturday and Sunday the weekend', () => {
    expect(closedReason('2026-11-07', HOLIDAYS)).toBe('Weekend')
    expect(closedReason('2026-11-08', HOLIDAYS)).toBe('Weekend')
  })

  it('prefers the holiday name when one falls on a weekend', () => {
    const sunday = new Map([['2026-11-08', 'Diwali Laxmi Pujan (Muhurat trading)']])
    expect(closedReason('2026-11-08', sunday)).toBe('Diwali Laxmi Pujan (Muhurat trading)')
  })

  it('leaves a trading day open', () => {
    expect(closedReason('2026-11-09', HOLIDAYS)).toBeNull()
  })
})

describe('the journal calendar', () => {
  beforeEach(() => {
    vi.spyOn(api, 'journal').mockResolvedValue([])
    vi.spyOn(api, 'trades').mockResolvedValue([])
    vi.spyOn(api, 'holidays').mockResolvedValue([
      { day: '2026-11-08', name: 'Diwali Laxmi Pujan (Muhurat trading)' },
      { day: '2026-11-10', name: 'Diwali-Balipratipada' },
    ])
  })
  afterEach(() => vi.restoreAllMocks())

  function page() {
    return render(
      <QueryClientProvider client={new QueryClient()}><JournalPage today="2026-11-09" /></QueryClientProvider>,
    )
  }

  it('asks for the holidays of the month on screen', async () => {
    page()
    await screen.findByText('Diwali-Balipratipada')
    expect(api.holidays).toHaveBeenCalledWith('2026-11-01', '2026-11-30')
  })

  it('marks each closed day with its reason', async () => {
    page()
    const diwali = (await screen.findByText('Diwali-Balipratipada')).closest('button') as HTMLElement
    expect(diwali).toHaveClass('closed', 'holiday')
    expect(diwali).toHaveAttribute('title', 'Market closed: Diwali-Balipratipada')
    const saturday = screen.getByRole('button', { name: /^7\s*Weekend/ })
    expect(saturday).toHaveClass('closed')
    expect(saturday).not.toHaveClass('holiday')
    const monday = screen.getByRole('button', { name: /^9$/ })
    expect(monday).not.toHaveClass('closed')
  })

  it('says so when the holidays cannot be loaded, rather than showing none', async () => {
    vi.spyOn(api, 'holidays').mockRejectedValue(new Error('Not Found'))
    page()
    expect(await screen.findByText(/holidays could not be loaded/, {}, { timeout: 4000 })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^7\s*Weekend/ })).toHaveClass('closed')
  })

  it('says why the market was shut on the chosen day', async () => {
    page()
    fireEvent.click((await screen.findByText('Diwali-Balipratipada')).closest('button') as HTMLElement)
    const details = screen.getByRole('heading', { name: '2026-11-10' }).closest('section') as HTMLElement
    expect(within(details).getByText('Market closed: Diwali-Balipratipada')).toBeInTheDocument()
  })
})
