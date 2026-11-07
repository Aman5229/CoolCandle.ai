/** Typed REST client. Money stays a string end to end; only display code formats it. */

export type Mode = 'paper' | 'live'
export type DayState = 'NO_PLAN' | 'ACTIVE' | 'WARNING' | 'COOL_OFF' | 'ENTRIES_CLOSED' | 'LOCKED'

export interface Reason {
  code: string
  message: string
}

export interface DayStatus {
  state: DayState
  reasons: Reason[]
  lock_reason: string | null
}

export interface Snapshot {
  realised_pnl: string
  unrealised_pnl: string
  est_charges: string
  net_pnl: string
  peak_net_pnl: string
  trades_taken: number
  open_trade: boolean
  last_loss_at: string | null
  feed_age: number | null
  now: string
}

export interface Plan {
  trading_date: string
  profit_target: string
  loss_limit: string
  max_trades: number
  max_loss_per_trade: string
  entry_start: string
  entry_cutoff: string
  square_off: string
  cool_off_minutes: number
  min_reward_risk: string
  allowed_underlyings: string[]
  trail_at_target: boolean
  target_giveback_pct: string
  status: 'DRAFT' | 'CONFIRMING' | 'LOCKED'
  confirmations: number
}

export type PlanInput = Omit<Plan, 'trading_date' | 'status' | 'confirmations'>

export interface Day {
  trading_date: string
  mode: Mode
  status: DayStatus
  snapshot: Snapshot
  plan: Plan | null
  locked_at: string | null
  lock_ends_at: string | null
  market_open: boolean
  broker_kill_switch_url: string
}

export interface Me {
  email: string
  mode: Mode
  account_id: string
  is_simulated: boolean
}

export interface Candle {
  start: string
  open: string
  high: string
  low: string
  close: string
}

export interface ChainSide {
  key: string
  trading_symbol: string
  ltp: string | null
  bid: string | null
  ask: string | null
  oi: number | null
}

export interface ChainRow {
  strike: string
  ce: ChainSide | null
  pe: ChainSide | null
}

export interface Chain {
  underlying: string
  spot: string | null
  expiry: string | null
  atm: string | null
  lot_size: number | null
  rows: ChainRow[]
}

export type TrailKind = 'BREAKEVEN' | 'TARGET_TRAIL' | 'STEP' | 'SWING_LOW' | 'ATR'

export interface Ticket {
  instrument_key: string
  lots: number
  limit_price: string
  stop_trigger: string
  target?: string | null
  trailing?: { kind: TrailKind } | null
}

export interface Preview {
  decision: { action: string; reasons: Reason[] }
  size: { lots: number; limited_by: string; limits: Record<string, number> } | null
  qty: number
  stop_limit: string
  worst_case: string
  charges: string
  reward_risk: string | null
}

export interface Trade {
  id: string
  state: string
  instrument_key: string
  trading_symbol: string
  qty: number
  filled_qty: number
  entry_limit: string
  entry_avg: string | null
  exit_avg: string | null
  stop_trigger: string
  target: string | null
  realised_pnl: string
  source: string
  opened_at: string | null
  closed_at: string | null
  exit_reason: string | null
  trailing: { kind: TrailKind } | null
}

const ACTIVE_STATES = ['ENTRY_PENDING', 'OPEN', 'EXITING', 'NEEDS_ATTENTION']

/** Waiting to fill, holding, or on its way out. */
export function isActive(trade: Trade): boolean {
  return ACTIVE_STATES.includes(trade.state)
}

export interface TimelineEvent {
  ts: string
  type: string
  data: Record<string, unknown>
}

export interface DaySummary {
  day: string
  trades: number
  wins: number
  gross_pnl: string
  lock_reason: string | null
}

export interface Signal {
  id: string
  setup: string
  underlying: string
  direction: string
  instrument_key: string
  trading_symbol: string
  lot_size: number
  entry: string
  stop: string
  target: string
  expires_at: string
  reason: string
  status: string
}

export interface FieldSpec {
  name: string
  label: string
  type: string
  secret: boolean
}

export interface BrokerSpec {
  id: string
  name: string
  status: 'live' | 'coming_soon'
  fields: FieldSpec[]
  daily_fields: FieldSpec[]
}

export interface Account {
  id: string
  broker: string
  label: string
  status: string
  has_credentials: boolean
}

export interface Funds {
  available: string
  used_margin: string
}

/** A day the exchange is closed, from its published holiday list. */
export interface MarketHoliday {
  day: string
  name: string
}

export interface MarketEvent {
  day: string
  name: string
  at: string | null
  impact: 'HIGH' | 'MEDIUM'
}

export interface Headline {
  headline: { title: string; link: string; source: string; published_at: string | null }
  tags: string[]
}

export interface AgentReply {
  text: string
  proposal: Signal | null
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly reasons: Reason[] = [],
  ) {
    super(message)
  }
}

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|; )cc_csrf=([^;]+)/)
  return match?.[1] ? decodeURIComponent(match[1]) : ''
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {}
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (method !== 'GET') headers['X-CSRF-Token'] = csrfToken()
  const response = await fetch(`/api${path}`, {
    method,
    headers,
    credentials: 'same-origin',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!response.ok) {
    const error = (await response.json().catch(() => ({}))) as {
      code?: string
      message?: string
      reasons?: Reason[]
    }
    throw new ApiError(
      response.status,
      error.code ?? 'ERROR',
      error.message ?? 'Something went wrong',
      error.reasons,
    )
  }
  return (await response.json()) as T
}

const get = <T>(path: string) => request<T>('GET', path)
const post = <T>(path: string, body: unknown = {}) => request<T>('POST', path, body)
const put = <T>(path: string, body: unknown) => request<T>('PUT', path, body)

export const api = {
  login: (email: string, password: string, code: string) =>
    post<{ ok: boolean }>('/auth/login', { email, password, code }),
  logout: () => post<{ ok: boolean }>('/auth/logout'),
  me: () => get<Me>('/auth/me'),

  brokers: () => get<BrokerSpec[]>('/brokers'),
  accounts: () => get<Account[]>('/broker-accounts'),
  saveAccount: (broker: string, label: string, credentials: Record<string, string>) =>
    post<Account>('/broker-accounts', { broker, label, credentials }),
  removeAccount: (id: string) => request<Account>('DELETE', `/broker-accounts/${id}`),
  connect: (id: string, totp: string) => post<Funds>(`/broker-accounts/${id}/connect`, { totp }),
  testConnection: (id: string) => post<Funds>(`/broker-accounts/${id}/test`),

  plan: () => get<Plan | null>('/plan/today'),
  savePlan: (plan: PlanInput) => put<Plan>('/plan/today', plan),
  confirmPlan: (step: 1 | 2, typed?: string) => post<Plan>('/plan/today/confirm', { step, typed }),
  cancelPlan: () => post<Plan>('/plan/today/cancel'),

  day: () => get<Day>('/day/status'),
  setMode: (mode: Mode, typed?: string) => post<{ mode: Mode }>('/mode', { mode, typed }),

  candles: (key: string, interval: string) =>
    get<Candle[]>(`/market/candles?key=${encodeURIComponent(key)}&interval=${interval}`),
  chain: (underlying: string) => get<Chain>(`/market/option-chain?underlying=${underlying}`),
  events: () => get<MarketEvent[]>('/market/events'),
  holidays: (start: string, end: string) =>
    get<MarketHoliday[]>(`/market/holidays?start=${start}&end=${end}`),
  addEvent: (event: Omit<MarketEvent, 'impact'> & { impact?: string }) =>
    post<MarketEvent>('/market/events', event),
  news: () => get<Headline[]>('/market/news'),

  preview: (ticket: Ticket) => post<Preview>('/orders/preview', ticket),
  submit: (ticket: Ticket) => post<Trade>('/trades', ticket),
  exitTrade: (id: string) => post<Trade>(`/trades/${id}/exit`),
  cancelTrade: (id: string) => post<Trade>(`/trades/${id}/cancel`),
  moveStop: (id: string, trigger: string) => post<Trade>(`/trades/${id}/stop`, { trigger }),
  trades: (day?: string) => get<Trade[]>(day ? `/trades?day=${day}` : '/trades'),
  timeline: (id: string) => get<TimelineEvent[]>(`/trades/${id}`),
  journal: (start: string, end: string) =>
    get<DaySummary[]>(`/journal?start=${start}&end=${end}`),

  signals: () => get<Signal[]>('/signals'),
  signal: (id: string, expires: string, signature: string) =>
    get<Signal>(`/signals/${id}?e=${expires}&s=${signature}`),
  takeSignal: (id: string, lots?: number) => post<Trade>(`/signals/${id}/take`, { lots }),
  declineSignal: (id: string) => post<{ ok: boolean }>(`/signals/${id}/decline`),
  chat: (message: string) => post<AgentReply>('/agent/chat', { message }),

  settings: () => get<Record<string, unknown>>('/settings'),
  saveSettings: (values: Record<string, unknown>) =>
    put<Record<string, unknown>>('/settings', { values }),
  signOutEverywhere: () => post<{ ok: boolean }>('/settings/sign-out-everywhere'),
}
