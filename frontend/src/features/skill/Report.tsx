import { useQuery } from '@tanstack/react-query'
import type { SeriesMarker, Time } from 'lightweight-charts'
import { useMemo, useRef, useState } from 'react'
import { Loader } from '../../components/Brand'
import { ErrorText } from '../../components/Field'
import { inr, pnlClass } from '../../lib/format'
import { PriceChart, type ChartSource } from '../trading/PriceChart'
import { skillApi, type ReportCard, type SessionPlan } from './api'
import { dayText, tradeMarkers, tradeNumbers } from './hud'

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

/** The fund through the session, with the target and loss-limit lines it was locked between. */
function EquityCurve({ report, plan }: { report: ReportCard; plan: SessionPlan }) {
  const start = Number(report.fund_before)
  const top = start + Number(plan.profit_target)
  const bottom = start - Number(plan.loss_limit)
  const values = report.equity_curve.map((p) => Number(p.equity))
  const high = Math.max(top, ...values)
  const low = Math.min(bottom, ...values)
  const width = 600
  const height = 160
  const y = (v: number) => (high === low ? height / 2 : ((high - v) / (high - low)) * (height - 10) + 5)
  const step = values.length > 1 ? width / (values.length - 1) : width
  const points = values.map((v, i) => `${(i * step).toFixed(1)},${y(v).toFixed(1)}`).join(' ')
  return (
    <svg className="equity" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none"
      role="img" aria-label="Fund through the session">
      <line className="limit target" x1={0} x2={width} y1={y(top)} y2={y(top)} />
      <line className="limit start" x1={0} x2={width} y1={y(start)} y2={y(start)} />
      <line className="limit loss" x1={0} x2={width} y1={y(bottom)} y2={y(bottom)} />
      <polyline className="curve" points={values.length > 1 ? points : `0,${y(start)} ${width},${y(start)}`} />
    </svg>
  )
}

export function ReportView({ id, onLobby }: { id: string; onLobby: () => void }) {
  const report = useQuery({ queryKey: ['skill', 'report', id], queryFn: () => skillApi.report(id) })
  const session = useQuery({ queryKey: ['skill', 'session', id], queryFn: () => skillApi.session(id) })
  const [focus, setFocus] = useState<string | null>(null)
  const chart = useRef<HTMLDivElement>(null)
  const source = useMemo<ChartSource>(
    () => ({
      queryKey: ['skill', 'candles', id, 'report'],
      load: () => skillApi.candles(id, '5m'),
      topic: 'skill.report', // a finished session gets no more ticks
      accepts: () => false,
    }),
    [id],
  )
  const trades = session.data?.trades
  const markers = useMemo(
    () =>
      tradeMarkers(
        trades ?? [],
        { up: cssVar('--up'), down: cssVar('--down'), open: cssVar('--accent') },
        '5m',
        focus,
      ) as SeriesMarker<Time>[],
    [trades, focus],
  )

  if (report.error) return <ErrorText error={report.error} />
  if (!report.data || !session.data) return <div className="center stage"><Loader text="Marking the report card…" /></div>

  const card = report.data
  const view = session.data
  const numbers = tradeNumbers(view.trades)
  const show = (tradeId: string | null) => {
    if (!tradeId) return
    setFocus(tradeId)
    chart.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' })
  }
  const stat = (value: string | null, suffix = '') => (value === null ? '–' : `${value}${suffix}`)

  return (
    <div className="stack" aria-label="Report card">
      <div className="row">
        <button type="button" className="link" onClick={onLobby}>← Back to lobby</button>
        <span className="muted small">{view.index} · {dayText(view.day)}</span>
      </div>
      <div className="card">
        <div className="report-hero">
          <div className="report-score" data-testid="report-score">{card.score}</div>
          <div className="report-grade">{card.grade}</div>
          <div className="flow">
            <span className="muted">Discipline first. Then the money:</span>
            <span className={`big-number ${pnlClass(card.net_pnl)}`}>{inr(card.net_pnl)}</span>
          </div>
        </div>
        {card.biggest_mistake ? (
          <p><b>Biggest mistake:</b> {card.biggest_mistake}</p>
        ) : (
          <p className="up">No mistakes that cost points. That is the habit.</p>
        )}
      </div>

      <div className="card">
        <h3>Score breakdown</h3>
        {card.lines.length === 0 ? <p className="muted">A clean 100: nothing gained or lost.</p> : (
          <table className="breakdown">
            <tbody>
              {card.lines.map((line, i) => (
                <tr key={`${line.line}-${i}`}
                  className={`${line.trade_id ? 'linked' : ''}${line.trade_id && line.trade_id === focus ? ' focus' : ''}`}
                  onClick={() => show(line.trade_id)}>
                  <td>{line.label}</td>
                  <td className="muted">{line.trade_id ? `trade ${numbers.get(line.trade_id) ?? ''}` : ''}</td>
                  <td className={`points ${line.points > 0 ? 'up' : 'down'}`}>
                    {line.points > 0 ? `+${line.points}` : line.points}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="card" ref={chart}>
        <h3>The day <span className="muted small">{view.index} · {dayText(view.day)} · every trade marked</span></h3>
        <PriceChart instrumentKey={`skill|${view.index}`} interval="5m" source={source} markers={markers} />
      </div>

      <div className="card">
        <h3>Equity</h3>
        <EquityCurve report={card} plan={view.plan} />
        <dl className="facts compact">
          <dt>Trades</dt><dd>{card.trades}</dd>
          <dt>Win rate</dt><dd>{stat(card.win_rate, '%')}</dd>
          <dt>Average win</dt><dd>{stat(card.avg_win_r, 'R')}</dd>
          <dt>Average loss</dt><dd>{stat(card.avg_loss_r, 'R')}</dd>
          <dt>Expectancy</dt><dd>{stat(card.expectancy_r, 'R')}</dd>
          <dt>Profit factor</dt><dd>{stat(card.profit_factor)}</dd>
          <dt>Max drawdown</dt><dd>{inr(card.max_drawdown)}</dd>
          <dt>Fees paid</dt><dd>{inr(card.fees)}</dd>
          <dt>Time in market</dt><dd>{card.minutes_in_market} min</dd>
        </dl>
      </div>

      <div className="card">
        <h3>The run</h3>
        <dl className="facts compact">
          <dt>Fund before</dt><dd>{inr(card.fund_before)}</dd>
          <dt>Fund after</dt><dd>{inr(card.fund_after)}</dd>
          <dt>Above the blown line</dt><dd className={pnlClass(card.above_blown_line)}>{inr(card.above_blown_line)}</dd>
        </dl>
        <button type="button" className="btn primary" onClick={onLobby}>Back to lobby</button>
      </div>
    </div>
  )
}
