import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { skillApi, type OptionQuote, type SessionView, type SkillPreview } from './api'
import { SkillTicket } from './SkillTicket'

const VIEW = { id: 's1', index: 'NIFTY', lot_size: 65 } as SessionView

const CALL: OptionQuote = {
  key: 'skill|NIFTY|2025-11-11|25000|CE',
  trading_symbol: 'NIFTY 11 NOV 25000 CE',
  ltp: '85.25',
  bid: '85.00',
  ask: '85.50',
}

const ALLOW: SkillPreview = {
  entry: '85.55',
  lowest_stop: '78.60',
  highest_stop: '85.10',
  worst_case_loss: '1345.12',
  charges: '45.10',
  cash_needed: '5560.75',
  reward_risk: '2.00',
  decision: { action: 'ALLOW', reasons: [] },
}

function ticket(option: OptionQuote | null = CALL, open = true) {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <SkillTicket view={VIEW} option={option} open={open} />
    </QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('the Skill Test ticket', () => {
  it('asks for an option first', () => {
    const preview = vi.spyOn(skillApi, 'preview').mockResolvedValue(ALLOW)
    ticket(null)
    expect(screen.getByText('Pick an option from the chain')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Buy 1 lot' })).toBeDisabled()
    expect(preview).not.toHaveBeenCalled()
  })

  it('shows the picked option and cannot be sent without a stop', async () => {
    vi.spyOn(skillApi, 'preview').mockResolvedValue(ALLOW)
    ticket()
    expect(screen.getByText('NIFTY 11 NOV 25000 CE')).toBeInTheDocument()
    expect(screen.getByTestId('ticket-ask')).toHaveTextContent('Ask 85.50')
    expect(await screen.findByText('₹1,345.12')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Buy 1 lot' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Stop price'), { target: { value: '65' } })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Buy 1 lot' })).toBeEnabled())
  })

  it('shows why an order is refused and keeps the button off', async () => {
    vi.spyOn(skillApi, 'preview').mockResolvedValue({
      ...ALLOW,
      decision: {
        action: 'BLOCK',
        reasons: [{ code: 'TRADE_TOO_BIG', message: 'Worst-case loss is above the per-trade limit' }],
      },
    })
    ticket()
    fireEvent.change(screen.getByLabelText('Stop price'), { target: { value: '65' } })
    expect(await screen.findByText('Worst-case loss is above the per-trade limit')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Buy 1 lot' })).toBeDisabled()
  })

  it('says which stops the plan allows', async () => {
    vi.spyOn(skillApi, 'preview').mockResolvedValue(ALLOW)
    ticket()
    expect(await screen.findByText('Your plan allows 78.60 to 85.10')).toBeInTheDocument()
  })

  it('says when no stop on the option fits', async () => {
    vi.spyOn(skillApi, 'preview').mockResolvedValue({ ...ALLOW, lowest_stop: null, highest_stop: null })
    ticket()
    expect(await screen.findByText('No stop on this option fits your per-trade limit')).toBeInTheDocument()
  })

  it('sends the order it shows', async () => {
    vi.spyOn(skillApi, 'preview').mockResolvedValue(ALLOW)
    const enter = vi.spyOn(skillApi, 'enter').mockResolvedValue(VIEW)
    ticket()
    fireEvent.change(screen.getByLabelText('Lots'), { target: { value: '2' } })
    fireEvent.change(screen.getByLabelText('Stop price'), { target: { value: '65' } })
    fireEvent.change(screen.getByLabelText('Target price'), { target: { value: '125' } })
    const buy = screen.getByRole('button', { name: 'Buy 2 lots' })
    await waitFor(() => expect(buy).toBeEnabled())
    fireEvent.click(buy)
    await waitFor(() =>
      expect(enter).toHaveBeenCalledWith('s1', {
        instrument_key: CALL.key,
        lots: 2,
        order_type: 'MARKET',
        stop: '65',
        target: '125',
      }),
    )
  })

  it('stays greyed once the session is over', () => {
    const preview = vi.spyOn(skillApi, 'preview').mockResolvedValue(ALLOW)
    ticket(CALL, false)
    fireEvent.change(screen.getByLabelText('Stop price'), { target: { value: '65' } })
    expect(screen.getByRole('button', { name: 'Buy 1 lot' })).toBeDisabled()
    expect(screen.getByLabelText('Order ticket')).toHaveAttribute('aria-disabled', 'true')
    expect(screen.getByText(/The session is over/)).toBeInTheDocument()
    expect(preview).not.toHaveBeenCalled()
  })
})
