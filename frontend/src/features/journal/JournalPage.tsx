import { useQuery } from '@tanstack/react-query'
import { Fragment, useMemo, useState } from 'react'
import { Loader } from '../../components/Brand'
import { Notice } from '../../components/Field'
import { api, type DaySummary, type Trade } from '../../lib/api'
import { inr, istTime, pnlClass, price } from '../../lib/format'

function iso(date: Date): string {
  return date.toISOString().slice(0, 10)
}

/** Why the market is shut on a day, or null when it is open. */
export function closedReason(day: string, holidays: ReadonlyMap<string, string>): string | null {
  const holiday = holidays.get(day)
  if (holiday) return holiday
  const weekday = new Date(`${day}T00:00:00Z`).getUTCDay()
  return weekday === 0 || weekday === 6 ? 'Weekend' : null
}

function monthGrid(year: number, month: number): (string | null)[] {
  const first = new Date(Date.UTC(year, month, 1))
  const days = new Date(Date.UTC(year, month + 1, 0)).getUTCDate()
  const lead = (first.getUTCDay() + 6) % 7 // Monday first
  const cells: (string | null)[] = Array.from({ length: lead }, () => null)
  for (let day = 1; day <= days; day += 1) cells.push(iso(new Date(Date.UTC(year, month, day))))
  return cells
}

function Timeline({ trade }: { trade: Trade }) {
  const events = useQuery({ queryKey: ['timeline', trade.id], queryFn: () => api.timeline(trade.id) })
  return (
    <ol className="timeline">
      {events.data?.map((event, index) => (
        <li key={index}><span className="muted">{istTime(event.ts)}</span> {event.type.replaceAll('_', ' ')}</li>
      ))}
    </ol>
  )
}

function DayTrades({ day }: { day: string }) {
  const trades = useQuery({ queryKey: ['trades', day], queryFn: () => api.trades(day) })
  const [open, setOpen] = useState<string | null>(null)
  if (trades.isLoading) return <div className="center"><Loader size={160} /></div>
  if (trades.data?.length === 0) return <p className="muted">No trades on {day}.</p>
  return (
    <div className="table-scroll">
    <table className="trades">
      <thead>
        <tr><th>Time</th><th>Contract</th><th>Entry</th><th>Exit</th><th>Reason</th><th>P&L</th></tr>
      </thead>
      <tbody>
        {trades.data?.map((trade) => (
          <Fragment key={trade.id}>
            <tr onClick={() => setOpen(open === trade.id ? null : trade.id)}>
              <td>{istTime(trade.opened_at)}</td>
              <td>{trade.trading_symbol}</td>
              <td>{price(trade.entry_avg)}</td>
              <td>{price(trade.exit_avg)}</td>
              <td>{trade.exit_reason ?? trade.state}</td>
              <td className={pnlClass(trade.realised_pnl)}>{inr(trade.realised_pnl)}</td>
            </tr>
            {open === trade.id && (
              <tr><td colSpan={6}><Timeline trade={trade} /></td></tr>
            )}
          </Fragment>
        ))}
      </tbody>
    </table>
    </div>
  )
}

export function JournalPage({ today }: { today: string }) {
  const [year, month] = today.split('-').map(Number) as [number, number]
  const [cursor, setCursor] = useState({ year, month: month - 1 })
  const [selected, setSelected] = useState(today)
  const cells = useMemo(() => monthGrid(cursor.year, cursor.month), [cursor])
  const start = iso(new Date(Date.UTC(cursor.year, cursor.month, 1)))
  const end = iso(new Date(Date.UTC(cursor.year, cursor.month + 1, 0)))
  const days = useQuery({ queryKey: ['journal', start], queryFn: () => api.journal(start, end) })
  const holidayList = useQuery({
    queryKey: ['holidays', start],
    queryFn: () => api.holidays(start, end),
    staleTime: Infinity, // the exchange's list changes once a year
    retry: 1,
  })
  const byDay = new Map<string, DaySummary>(days.data?.map((d) => [d.day, d]))
  const holidays = new Map<string, string>(holidayList.data?.map((h) => [h.day, h.name]))
  const selectedClosed = closedReason(selected, holidays)
  const step = (by: number) => {
    const next = new Date(Date.UTC(cursor.year, cursor.month + by, 1))
    setCursor({ year: next.getUTCFullYear(), month: next.getUTCMonth() })
  }

  return (
    <div className="stack">
      <section className="card">
        <div className="card-head">
          <h2>{new Date(Date.UTC(cursor.year, cursor.month, 1)).toLocaleString('en-IN', { month: 'long', year: 'numeric', timeZone: 'UTC' })}</h2>
          <div className="row">
            <button type="button" className="btn" onClick={() => step(-1)}>Previous</button>
            <button type="button" className="btn" onClick={() => step(1)}>Next</button>
          </div>
        </div>
        <div className="calendar">
          {['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((d) => <b key={d} className="muted small">{d}</b>)}
          {cells.map((cell, index) => {
            const summary = cell ? byDay.get(cell) : undefined
            const closed = cell ? closedReason(cell, holidays) : null
            const holiday = cell ? holidays.has(cell) : false
            return cell ? (
              <button key={cell} type="button" title={closed ? `Market closed: ${closed}` : undefined}
                className={`day${closed ? ' closed' : ''}${holiday ? ' holiday' : ''}${selected === cell ? ' active' : ''}`}
                onClick={() => setSelected(cell)}>
                <span className="small muted">{Number(cell.slice(8))}</span>
                {closed && <span className="off">{closed}</span>}
                {summary && <b className={pnlClass(summary.gross_pnl)}>{inr(summary.gross_pnl)}</b>}
                {summary?.lock_reason && <span className="tag">{summary.lock_reason.replaceAll('_', ' ')}</span>}
              </button>
            ) : <span key={`blank-${index}`} />
          })}
        </div>
        {holidayList.isError && (
          <p className="form-warn">Exchange holidays could not be loaded, so only weekends are marked.</p>
        )}
        <p className="muted small calendar-key">
          <span className="key holiday" /> Exchange holiday
          <span className="key closed" /> Weekend
        </p>
      </section>
      <section className="card">
        <h2>{selected}</h2>
        {selectedClosed && <Notice>Market closed: {selectedClosed}</Notice>}
        <DayTrades day={selected} />
      </section>
    </div>
  )
}
