import { useEffect, useRef, useState } from 'react'
import { isActive, type Trade } from '../../lib/api'
import { inr, pnlClass } from '../../lib/format'

const SHOW_MS = 5000

/** Trades that were active last time and have closed since. */
export function closedSince(active: ReadonlySet<string>, trades: Trade[]): Trade[] {
  return trades.filter((trade) => active.has(trade.id) && trade.state === 'CLOSED')
}

/** A short note when a trade closes. The same calm look for a win or a loss. */
export function TradeToast({ trades }: { trades: Trade[] | undefined }) {
  const active = useRef<Set<string> | null>(null)
  const [closed, setClosed] = useState<Trade | null>(null)

  useEffect(() => {
    if (!trades) return
    const done = active.current ? closedSince(active.current, trades).at(-1) : undefined
    if (done) setClosed(done)
    active.current = new Set(trades.filter(isActive).map((trade) => trade.id))
  }, [trades])

  useEffect(() => {
    if (!closed) return
    const timer = setTimeout(() => setClosed(null), SHOW_MS)
    return () => clearTimeout(timer)
  }, [closed])

  if (!closed) return null
  return (
    <div className="toast" role="status" key={closed.id}>
      <span className="muted">Trade closed</span> {closed.trading_symbol}{' '}
      <b className={pnlClass(closed.realised_pnl)}>{inr(closed.realised_pnl)}</b>
      {closed.exit_reason && <span className="tag">{closed.exit_reason.replaceAll('_', ' ')}</span>}
    </div>
  )
}
