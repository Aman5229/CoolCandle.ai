/** Skill Test's REST calls. Money is rupees and premiums are model prices, both as strings. */

import { request, type Candle, type Reason } from '../../lib/api'

export type SessionState =
  | 'DRAFT'
  | 'ACTIVE'
  | 'WARNING'
  | 'COOL_OFF'
  | 'ENTRIES_CLOSED'
  | 'LOCKED'
  | 'ENDED'
export type SkillLock = 'TARGET' | 'LOSS_LIMIT' | 'MAX_TRADES' | 'SQUARE_OFF' | 'BLOWN'
export type Timeframe = '1m' | '5m' | '15m' | '1h' | '1d'
export type ReplayAction = 'play' | 'pause' | 'speed'

export const SPEEDS = [1, 5, 20, 60] as const
export const TIMEFRAMES: Timeframe[] = ['1m', '5m', '15m', '1h', '1d']
export const START_FUNDS = ['50000', '100000', '500000'] as const

export interface SessionPlan {
  profit_target: string
  loss_limit: string
  max_trades: number
  max_loss_per_trade: string
  cool_off_minutes: number
  min_reward_risk: string
  entry_start: string // IST, "09:20:00"
  entry_cutoff: string
  square_off: string
}

export interface RunView {
  id: string
  number: number
  start_fund: string
  cash: string
  status: 'ACTIVE' | 'BLOWN' | 'RETIRED'
  today: string // the replayed day the next session plays
  started_at: string
  ended_at: string | null
  peak_equity: string
  blown_line: string
}

export interface SessionSummary {
  id: string
  run_id: string
  index: string
  day: string
  state: SessionState
  lock_reason: SkillLock | null
  score: number | null
  grade: string | null
  net_pnl: string | null
  started_at: string | null
  ended_at: string | null
}

export interface SkillTrade {
  id: string
  instrument_key: string
  trading_symbol: string
  qty: number
  entry: string | null
  stop_at_fill: string
  stop: string
  target: string | null
  exit: string | null
  r_risk: string
  pnl: string
  fees: string
  opened_at: string
  closed_at: string | null
  exit_kind: 'STOP' | 'TARGET' | 'MANUAL' | 'LOCK' | null
  stop_widened: boolean
  status: 'PENDING' | 'OPEN' | 'CLOSED' | 'CANCELLED'
}

export interface ScoreLine {
  line: string
  points: number
  label: string
  trade_id: string | null
  event_id: string | null
}

export interface ScoreCard {
  score: number
  lines: ScoreLine[]
}

export interface ReplayInfo {
  cursor: string
  speed: number
  paused: boolean
  finished: boolean
}

/** A model price: Black-Scholes on the replayed index, not a quote that ever traded. */
export interface OptionQuote {
  key: string
  trading_symbol: string
  ltp: string
  bid: string
  ask: string
}

export interface ChainView {
  index: string
  spot: string
  expiry: string
  atm: string
  lot_size: number
  vol: string
  vol_source: 'INDIA_VIX' | 'REALISED'
  rows: { strike: string; ce: OptionQuote; pe: OptionQuote }[]
}

export interface SessionView {
  id: string
  run_id: string
  index: string
  day: string
  expiry: string
  lot_size: number
  plan: SessionPlan
  plan_status: 'DRAFT' | 'CONFIRMING' | 'STARTED'
  state: SessionState
  lock_reason: SkillLock | null
  started_at: string | null
  ended_at: string | null
  now: string
  fund: string
  start_equity: string | null
  end_equity: string | null
  pnl: { realised: string; unrealised: string; net: string }
  trades_taken: number
  trades: SkillTrade[]
  open_quote: OptionQuote | null
  score: ScoreCard
  grade: string
  spot: string | null
  prices_fresh: boolean
  replay: ReplayInfo | null
  live_locked: boolean
}

export interface SkillSettings {
  blown_pct: string
  min_stop_pct: string
  revenge_minutes: number
  when_live_locked: boolean
}

export interface AccountView {
  run: RunView | null
  running: SessionSummary | null
  recent: SessionSummary[]
  best_score: number | null
  sessions_played: number
  runs: number
  indices: string[] // those with history on disk
  first_day: string | null
  last_day: string | null
  earliest_start: string | null
  lot_sizes: Record<string, number>
  settings: SkillSettings
  live_locked: boolean
}

export interface SkillOrder {
  instrument_key: string
  lots: number
  order_type: 'MARKET' | 'LIMIT'
  price?: string | null
  stop: string | null
  target?: string | null
}

export interface SkillPreview {
  entry: string | null
  lowest_stop: string | null // the stops the plan allows, for these lots
  highest_stop: string | null
  worst_case_loss: string | null
  charges: string | null
  cash_needed: string | null
  reward_risk: string | null
  decision: { action: string; reasons: Reason[] }
}

export interface ReportCard {
  score: number
  grade: string
  net_pnl: string
  trades: number
  win_rate: string | null
  avg_win_r: string | null
  avg_loss_r: string | null
  expectancy_r: string | null
  profit_factor: string | null
  max_drawdown: string
  fees: string
  minutes_in_market: number
  equity_curve: { ts: string; equity: string }[]
  lines: ScoreLine[]
  biggest_mistake: string | null
  fund_before: string
  fund_after: string
  above_blown_line: string
}

export interface NewSession {
  index: string
  speed: number
  plan: SessionPlan
}

const get = <T>(path: string) => request<T>('GET', `/skill${path}`)
const post = <T>(path: string, body: unknown = {}) => request<T>('POST', `/skill${path}`, body)
const patch = <T>(path: string, body: unknown) => request<T>('PATCH', `/skill${path}`, body)

export const skillApi = {
  account: () => get<AccountView>('/account'),
  runs: () => get<RunView[]>('/runs'),
  startRun: (start_fund: string, start_day: string, typed?: string) =>
    post<RunView>('/runs', { start_fund, start_day, typed }),
  runSessions: (runId: string) => get<SessionSummary[]>(`/runs/${runId}/sessions`),
  create: (body: NewSession) => post<SessionView>('/sessions', body),
  session: (id: string) => get<SessionView>(`/sessions/${id}`),
  confirm: (id: string, step: 1 | 2, typed?: string) =>
    post<SessionView>(`/sessions/${id}/confirm`, { step, typed }),
  editPlan: (id: string, plan: SessionPlan) => patch<SessionView>(`/sessions/${id}/plan`, plan),
  replay: (id: string, action: ReplayAction, speed?: number) =>
    post<SessionView>(`/sessions/${id}/replay`, { action, speed }),
  preview: (id: string, order: SkillOrder) => post<SkillPreview>(`/sessions/${id}/preview`, order),
  enter: (id: string, order: SkillOrder) => post<SessionView>(`/sessions/${id}/orders`, order),
  moveStop: (tradeId: string, stop: string) =>
    patch<SessionView>(`/trades/${tradeId}/stop`, { stop }),
  exit: (tradeId: string) => post<SessionView>(`/trades/${tradeId}/exit`),
  end: (id: string) => post<SessionView>(`/sessions/${id}/end`),
  report: (id: string) => get<ReportCard>(`/sessions/${id}/report`),
  candles: (id: string, interval: Timeframe) =>
    get<Candle[]>(`/sessions/${id}/candles?interval=${interval}`),
  chain: (id: string) => get<ChainView>(`/sessions/${id}/chain`),
}
