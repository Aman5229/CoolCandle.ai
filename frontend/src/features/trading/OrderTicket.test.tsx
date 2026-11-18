import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { api, ApiError, type ChainSide, type Preview, type StopRange } from '../../lib/api'
import { live, type Tick } from '../../lib/ws'
import { OrderTicket } from './OrderTicket'

const CALL: ChainSide = {
  key: 'nse_fo|52123',
  trading_symbol: 'NIFTY26N1725250CE',
  ltp: '83.45',
  bid: '83.40',
  ask: '83.45',
  oi: null,
}

const ALLOW: Preview = {
  decision: { action: 'ALLOW', reasons: [] },
  size: { lots: 1, limited_by: 'MAX_LOSS', limits: {} },
  qty: 65,
  stop: '80.00',
  stop_limit: '77.40',
  worst_case: '290.00',
  to_stop_limit: '120.00',
  slippage: '130.00',
  charges: '40.00',
  reward_risk: null,
}

const ROOM: StopRange = { lowest: '80.00', highest: '83.00', budget: '300.00' }

let onTick: ((tick: Tick) => void) | null = null

/** A tick for the ticket's option, pushed through the live feed the ticket listens to. */
function price(ask: string) {
  act(() => onTick?.({ instrument_key: CALL.key, ts: '2026-11-16T08:20:00Z', ltp: ask, bid: ask, ask }))
}

function ticket() {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <OrderTicket option={CALL} canTrade />
    </QueryClientProvider>,
  )
}

const limitInput = () => screen.getByLabelText('Limit price')

beforeEach(() => {
  onTick = null
  vi.spyOn(live, 'on').mockImplementation(((type: string, handler: (tick: Tick) => void) => {
    if (type === 'tick') onTick = handler
    return () => {}
  }) as typeof live.on)
  vi.spyOn(live, 'watch').mockImplementation(() => {})
  vi.spyOn(api, 'preview').mockResolvedValue(ALLOW)
  vi.spyOn(api, 'stopRange').mockResolvedValue(ROOM)
})
afterEach(() => vi.restoreAllMocks())

describe('the live order ticket', () => {
  it('opens at the ask and follows it as the price moves', () => {
    ticket()
    expect(limitInput()).toHaveValue('83.45')
    expect(screen.getByText('Follows the live ask')).toBeInTheDocument()
    price('84.10')
    expect(limitInput()).toHaveValue('84.10')
    price('83.90')
    expect(limitInput()).toHaveValue('83.90')
  })

  it('keeps a price the trader types, until asked to follow again', () => {
    ticket()
    fireEvent.change(limitInput(), { target: { value: '82' } })
    price('85.00')
    expect(limitInput()).toHaveValue('82')
    expect(screen.queryByText('Follows the live ask')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Follow the ask again' }))
    expect(limitInput()).toHaveValue('85.00')
    price('85.20')
    expect(limitInput()).toHaveValue('85.20')
  })

  it('sends the price showing when Buy is pressed', async () => {
    const submit = vi.spyOn(api, 'submit').mockResolvedValue({} as never)
    ticket()
    fireEvent.change(screen.getByLabelText('Stop price'), { target: { value: '79' } })
    price('84.30')
    const buy = await screen.findByRole('button', { name: 'Buy 1 lot' })
    await vi.waitFor(() => expect(buy).toBeEnabled())
    fireEvent.click(buy)
    await vi.waitFor(() =>
      expect(submit).toHaveBeenCalledWith(expect.objectContaining({ limit_price: '84.30' })),
    )
  })

  it("takes the plan's stop without one being typed, and lets the server set it", async () => {
    const submit = vi.spyOn(api, 'submit').mockResolvedValue({} as never)
    ticket()
    await vi.waitFor(() => expect(screen.getByLabelText('Stop price')).toHaveValue('80.00'))
    expect(api.stopRange).toHaveBeenCalledWith(CALL.key, '83.45', 1)
    expect(screen.getByText(/From your plan: .*300.* a trade, 4\.1% below the entry/)).toBeInTheDocument()
    const buy = screen.getByRole('button', { name: 'Buy 1 lot' })
    await vi.waitFor(() => expect(buy).toBeEnabled())
    fireEvent.click(buy)
    await vi.waitFor(() =>
      expect(submit).toHaveBeenCalledWith(expect.objectContaining({ stop_trigger: null })),
    )
  })

  it("takes a typed stop, shows the range it must sit in, and can go back to the plan's", async () => {
    ticket()
    await vi.waitFor(() => expect(screen.getByLabelText('Stop price')).toHaveValue('80.00'))
    fireEvent.change(screen.getByLabelText('Stop price'), { target: { value: '81.5' } })
    expect(screen.getByLabelText('Stop price')).toHaveValue('81.5')
    expect(screen.getByText('Your plan allows 80.00 to 83.00')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: "Use the plan's stop again" }))
    expect(screen.getByLabelText('Stop price')).toHaveValue('80.00')
  })

  it('says when no stop fits and a cheaper strike is needed', async () => {
    vi.mocked(api.stopRange).mockResolvedValue({ lowest: null, highest: null, budget: '300.00' })
    ticket()
    expect(await screen.findByText(/No stop fits .*300.* for 1 lot: pick a cheaper strike/)).toBeInTheDocument()
  })

  it('breaks the worst case into its parts', async () => {
    ticket()
    fireEvent.change(screen.getByLabelText('Stop price'), { target: { value: '79' } })
    expect(await screen.findByText("Down to the stop order's limit, 77.40")).toBeInTheDocument()
    expect(screen.getByText('Slippage buffer')).toBeInTheDocument()
    expect(screen.getByText('Worst-case loss')).toBeInTheDocument()
  })

  it('shows why the broker refused an order', async () => {
    const refusal = 'The broker refused the order: unauthorized'
    vi.spyOn(api, 'submit').mockRejectedValue(new ApiError(409, 'BROKER_REJECTED', refusal))
    ticket()
    fireEvent.change(screen.getByLabelText('Stop price'), { target: { value: '79' } })
    const buy = await screen.findByRole('button', { name: 'Buy 1 lot' })
    await vi.waitFor(() => expect(buy).toBeEnabled())
    fireEvent.click(buy)
    expect(await screen.findByText(refusal)).toBeInTheDocument()
  })

  it('says why the broker rejected an order it had first accepted', async () => {
    const margin = 'RMS:Margin Exceeds,Cash Available:5978.55,Additional margin required:823.32'
    vi.spyOn(api, 'submit').mockResolvedValue({ id: 't1', state: 'ENTRY_PENDING' } as never)
    const trades = vi.spyOn(api, 'trades').mockResolvedValue([])
    vi.spyOn(api, 'timeline').mockResolvedValue([
      { ts: '2026-11-16T09:17:37Z', type: 'ENTRY_SUBMITTED', data: {} },
      { ts: '2026-11-16T09:17:38Z', type: 'ENTRY_REJECTED', data: { reason: margin } },
    ])
    ticket()
    fireEvent.change(screen.getByLabelText('Stop price'), { target: { value: '79' } })
    const buy = await screen.findByRole('button', { name: 'Buy 1 lot' })
    await vi.waitFor(() => expect(buy).toBeEnabled())
    trades.mockResolvedValue([{ id: 't1', state: 'REJECTED' }] as never)
    fireEvent.click(buy)
    expect(
      await screen.findByText(`The broker rejected the order: ${margin}`, {}, { timeout: 3000 }),
    ).toBeInTheDocument()
  })
})
