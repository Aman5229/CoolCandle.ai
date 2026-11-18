import { useMutation, useQuery, useQueryClient, type UseQueryResult } from '@tanstack/react-query'
import type { SeriesMarker, Time } from 'lightweight-charts'
import { useMemo, useState } from 'react'
import { Loader } from '../../components/Brand'
import { ErrorText, Notice } from '../../components/Field'
import { Icon } from '../../components/Icon'
import { inr, inrWhole, pnlClass, price } from '../../lib/format'
import { useTickDirection } from '../../lib/hooks'
import { PriceChart, type ChartSource } from '../trading/PriceChart'
import {
  SPEEDS,
  TIMEFRAMES,
  skillApi,
  type ChainView,
  type OptionQuote,
  type SessionView,
  type SkillTrade,
  type Timeframe,
} from './api'
import { useCountUp, useSkillSession } from './hooks'
import { clockText, dayText, meterSpan, secondsUntil, tradeMarkers, tradesLeft } from './hud'
import { LockOverlay } from './LockOverlay'
import { SkillTicket } from './SkillTicket'

const STATE_TEXT: Record<SessionView['state'], { label: string; tone: string }> = {
  DRAFT: { label: 'Draft', tone: 'warn' },
  ACTIVE: { label: 'Active', tone: 'ok' },
  WARNING: { label: 'Warning', tone: 'warn' },
  COOL_OFF: { label: 'Cooling off', tone: 'warn' },
  ENTRIES_CLOSED: { label: 'Entries closed', tone: 'warn' },
  LOCKED: { label: 'Locked', tone: 'bad' },
  ENDED: { label: 'Ended', tone: 'bad' },
}

const TIMEFRAME_LABEL: Record<Timeframe, string> = {
  '1m': '1m',
  '5m': '5m',
  '15m': '15m',
  '1h': '1h',
  '1d': '1D',
}

const istClock = new Intl.DateTimeFormat('en-IN', {
  timeZone: 'Asia/Kolkata',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
})

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function SkillHud({ view }: { view: SessionView }) {
  const net = Number(view.pnl.net)
  const shownNet = useCountUp(net)
  const loss = Number(view.plan.loss_limit)
  const target = Number(view.plan.profit_target)
  const span = meterSpan(net, loss, target)
  const direction = useTickDirection(String(view.score.score))
  const clock = view.replay?.cursor ?? view.now
  const state = STATE_TEXT[view.state]

  return (
    <div className="hud" aria-label="Session HUD">
      <div className="hud-cell">
        <span>Fund</span>
        <span className="hud-value">{inrWhole(view.fund)}</span>
      </div>
      <div className="hud-cell meter-cell">
        <span>Day P&amp;L <span className={`chip ${state.tone}`}>{state.label}</span></span>
        <span className={`hud-value ${pnlClass(net)}`} data-testid="skill-pnl">{inr(shownNet)}</span>
        <div className="meter" role="meter" aria-valuenow={net} aria-valuemin={-loss}
          aria-valuemax={target} aria-label="Day P&L between the loss limit and the target">
          <span className="muted small">{inrWhole(-loss)}</span>
          <div className="meter-track">
            <div className={`meter-fill ${net >= 0 ? 'up-bg' : 'down-bg'}`}
              style={{ left: `${span.left}%`, width: `${span.width}%` }} />
            <div className="meter-zero" style={{ left: `${span.zero}%` }} />
          </div>
          <span className="muted small">{inrWhole(target)}</span>
        </div>
      </div>
      <div className="hud-cell">
        <span>Trades left</span>
        <span className="hud-value">{tradesLeft(view.plan, view.trades_taken)} / {view.plan.max_trades}</span>
      </div>
      <div className="hud-cell">
        <span>Score</span>
        <span key={view.score.score} className={`hud-value score-badge${direction ? ' pulse' : ''}`}
          data-testid="skill-score">
          {view.score.score}
          {direction && <span className={`delta ${direction}`}>{direction === 'up' ? '▲' : '▼'}</span>}
          <span className="muted small">{view.grade}</span>
        </span>
      </div>
      <div className="hud-cell">
        <span>{istClock.format(new Date(clock))} · square-off in</span>
        <span className="hud-value" data-testid="skill-timer">
          {clockText(secondsUntil(view.plan.square_off, clock))}
        </span>
      </div>
    </div>
  )
}

function ReplayControls({ view }: { view: SessionView }) {
  const client = useQueryClient()
  const control = useMutation({
    mutationFn: ({ action, speed }: { action: 'play' | 'pause' | 'speed'; speed?: number }) =>
      skillApi.replay(view.id, action, speed),
    onSuccess: (next) => client.setQueryData(['skill', 'session', view.id], next),
  })
  const replay = view.replay
  if (!replay) return null
  const over = view.state === 'LOCKED' || view.state === 'ENDED'
  return (
    <div className="replay-bar" aria-label="Replay controls">
      <button type="button" className="btn" disabled={over || control.isPending}
        aria-label={replay.paused ? 'Play' : 'Pause'}
        onClick={() => control.mutate({ action: replay.paused ? 'play' : 'pause' })}>
        <Icon name={replay.paused ? 'play' : 'pause'} size={16} />
      </button>
      {SPEEDS.map((speed) => (
        <button key={speed} type="button" disabled={over}
          className={`seg${replay.speed === speed ? ' active' : ''}`}
          onClick={() => control.mutate({ action: 'speed', speed })}>
          {speed}x
        </button>
      ))}
      <span className="hud-value">{istClock.format(new Date(replay.cursor))} IST</span>
      <ErrorText error={control.error} />
    </div>
  )
}

/** Strikes around the money, CE on the left and PE on the right, at the model's prices. */
function SkillChain({
  chain,
  selected,
  onPick,
}: {
  chain: UseQueryResult<ChainView>
  selected: string | undefined
  onPick: (quote: OptionQuote) => void
}) {
  const cell = (quote: OptionQuote) => (
    <td>
      <button type="button" className={`cell${selected === quote.key ? ' active' : ''}`}
        title={`Open a ticket for ${quote.trading_symbol}`} onClick={() => onPick(quote)}>
        <span>{price(quote.ltp)}</span>
      </button>
    </td>
  )
  return (
    <div className="card chain" aria-label="Option chain">
      <div className="card-head">
        <h3>Options <span className="muted small">model prices</span></h3>
        {chain.data && (
          <span className="muted small">
            Expiry {dayText(chain.data.expiry)} · lot {chain.data.lot_size} · volatility{' '}
            {(Number(chain.data.vol) * 100).toFixed(1)}%
            {chain.data.vol_source === 'REALISED' ? ' (realised)' : ' (India VIX)'}
          </span>
        )}
      </div>
      {chain.isLoading && <div className="center"><Loader size={160} text="Pricing options…" /></div>}
      <ErrorText error={chain.error} />
      {chain.data && (
        <div className="table-scroll">
          <table>
            <thead><tr><th>CE</th><th>Strike</th><th>PE</th></tr></thead>
            <tbody>
              {chain.data.rows.map((row) => (
                <tr key={row.strike} className={row.strike === chain.data.atm ? 'atm' : undefined}>
                  {cell(row.ce)}
                  <td className="strike">{Number(row.strike)}</td>
                  {cell(row.pe)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

function OpenTrade({ view, trade }: { view: SessionView; trade: SkillTrade }) {
  const client = useQueryClient()
  const [stop, setStop] = useState(trade.stop)
  const update = (next: SessionView) => client.setQueryData(['skill', 'session', view.id], next)
  const move = useMutation({ mutationFn: () => skillApi.moveStop(trade.id, stop), onSuccess: update })
  const exit = useMutation({ mutationFn: () => skillApi.exit(trade.id), onSuccess: update })
  const pending = trade.status === 'PENDING'
  const quote = view.open_quote
  return (
    <div className="card" aria-label="Open trade">
      <div className="card-head">
        <h3>{trade.trading_symbol}</h3>
        <span className={`chip ${pending ? 'warn' : 'info'}`}>{pending ? 'Waiting to fill' : 'Open'}</span>
      </div>
      <dl className="facts compact">
        <dt>Quantity</dt><dd>{trade.qty / view.lot_size} lot · {trade.qty}</dd>
        <dt>Bought at</dt><dd>{price(trade.entry)}</dd>
        <dt>Now</dt><dd>{quote ? `${price(quote.bid)} bid` : '–'}</dd>
        <dt>Stop</dt><dd>{price(trade.stop)}{trade.stop_widened && <span className="tag">widened</span>}</dd>
        <dt>Target</dt><dd>{price(trade.target)}</dd>
        <dt>1R at risk</dt><dd>{inr(trade.r_risk)}</dd>
        <dt>Open P&amp;L</dt><dd className={pnlClass(view.pnl.unrealised)}>{inr(view.pnl.unrealised)}</dd>
      </dl>
      <div className="row">
        <input inputMode="decimal" value={stop} onChange={(e) => setStop(e.target.value)}
          aria-label="New stop" />
        <button type="button" className="btn" disabled={move.isPending} onClick={() => move.mutate()}>
          Move stop
        </button>
      </div>
      <p className="muted small">Moving the stop further away is allowed, and costs points.</p>
      <ErrorText error={move.error ?? exit.error} />
      <button type="button" className="btn danger wide" disabled={exit.isPending} onClick={() => exit.mutate()}>
        {pending ? 'Cancel order' : 'Sell at market'}
      </button>
    </div>
  )
}

function TradeList({ trades }: { trades: SkillTrade[] }) {
  const done = trades.filter((t) => t.status === 'CLOSED')
  if (done.length === 0) return null
  return (
    <div className="card" aria-label="This session's trades">
      <h3>This session</h3>
      <ul className="plain">
        {done.map((t, i) => (
          <li key={t.id} className="row">
            <span className="muted">#{i + 1}</span>
            <span>{t.trading_symbol} {price(t.entry)} → {price(t.exit)}</span>
            <span className="tag">{t.exit_kind}</span>
            <span className={pnlClass(t.pnl)} style={{ marginLeft: 'auto' }}>{inr(t.pnl)}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export function ArenaView({
  id,
  onReport,
  onLobby,
}: {
  id: string
  onReport: () => void
  onLobby: () => void
}) {
  const client = useQueryClient()
  const session = useSkillSession(id)
  const [ending, setEnding] = useState(false)
  const [timeframe, setTimeframe] = useState<Timeframe>('1m')
  const [picked, setPicked] = useState<OptionQuote | null>(null)
  const end = useMutation({
    mutationFn: () => skillApi.end(id),
    onSuccess: (next) => {
      client.setQueryData(['skill', 'session', id], next)
      void client.invalidateQueries({ queryKey: ['skill', 'account'] })
      onReport()
    },
  })

  const view = session.data
  const over = view?.state === 'LOCKED' || view?.state === 'ENDED'
  const chain = useQuery({
    queryKey: ['skill', 'chain', id],
    queryFn: () => skillApi.chain(id),
    refetchInterval: 1000,
    enabled: !!view && !over,
  })
  // The ticket follows the picked strike's price as the day plays.
  const fresh = chain.data?.rows
    .flatMap((row) => [row.ce, row.pe])
    .find((quote) => quote.key === picked?.key)
  const key = `skill|${view?.index ?? ''}`
  const source = useMemo<ChartSource>(
    () => ({
      queryKey: ['skill', 'candles', id, timeframe],
      load: () => skillApi.candles(id, timeframe),
      topic: 'skill.tick',
      candleTopic: timeframe === '1m' ? 'skill.candle' : undefined,
      accepts: (item) => item.session_id === id,
    }),
    [id, timeframe],
  )
  const trades = view?.trades
  const markers = useMemo(
    () =>
      tradeMarkers(
        trades ?? [],
        { up: cssVar('--up'), down: cssVar('--down'), open: cssVar('--accent') },
        timeframe,
      ) as SeriesMarker<Time>[],
    [trades, timeframe],
  )

  if (!view) {
    return session.error ? <ErrorText error={session.error} /> : <div className="center stage"><Loader text="Opening the day…" /></div>
  }

  const open = view.trades.find((t) => t.status === 'OPEN' || t.status === 'PENDING') ?? null

  return (
    <div className="stack">
      <div className="row">
        <button type="button" className="link" onClick={onLobby}>← Back to lobby</button>
        <span className="muted small">
          {view.index} · {dayText(view.day)} · the day keeps playing while you are away
        </span>
      </div>
      <SkillHud view={view} />
      {view.live_locked && (
        <Notice tone="warn">Live is locked for today. This is practice only.</Notice>
      )}
      <div className="arena-grid">
        <div className="stack">
          <div className="card">
            <div className="card-head">
              <h3>{view.index} <span className="muted small">spot {price(view.spot)}</span></h3>
              {!over && (
                ending ? (
                  <span className="row">
                    <span className="muted small">End now? No points lost.</span>
                    <button type="button" className="btn danger" disabled={end.isPending} onClick={() => end.mutate()}>End</button>
                    <button type="button" className="btn" onClick={() => setEnding(false)}>Keep going</button>
                  </span>
                ) : (
                  <button type="button" className="btn" onClick={() => setEnding(true)}>End session</button>
                )
              )}
            </div>
            <div className="row" role="group" aria-label="Timeframe">
              {TIMEFRAMES.map((frame) => (
                <button key={frame} type="button" className={`seg${timeframe === frame ? ' active' : ''}`}
                  aria-pressed={timeframe === frame} onClick={() => setTimeframe(frame)}>
                  {TIMEFRAME_LABEL[frame]}
                </button>
              ))}
            </div>
            <PriceChart instrumentKey={key} interval={timeframe} source={source} markers={markers} />
            <ReplayControls view={view} />
            <ErrorText error={end.error} />
          </div>
          {!over && <SkillChain chain={chain} selected={picked?.key} onPick={setPicked} />}
        </div>
        <div className="stack">
          {open ? <OpenTrade key={open.id + open.stop} view={view} trade={open} /> : (
            <SkillTicket key={picked?.key ?? 'none'} view={view} open={!over}
              option={picked && !over ? (fresh ?? picked) : null} />
          )}
          <TradeList trades={view.trades} />
          {over && (
            <div className="row">
              <button type="button" className="btn primary" onClick={onReport}>See report card</button>
              <button type="button" className="btn" onClick={onLobby}>Back to lobby</button>
            </div>
          )}
        </div>
      </div>
      <LockOverlay view={view} onReport={onReport} onLobby={onLobby} />
    </div>
  )
}
