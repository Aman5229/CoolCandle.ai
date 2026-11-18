import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { api, isActive, type Trade } from '../../lib/api'
import { inr, pnlClass } from '../../lib/format'

const SHOW_MS = 5000
const REJECTION_MS = 12000 // longer: it says why an order did not go through

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


/** Trades rejected since the last look, leaving out the ones already rejected then. */
export function rejectedSince(known: ReadonlySet<string>, trades: Trade[]): Trade[] {
  return trades.filter((trade) => trade.state === 'REJECTED' && !known.has(trade.id))
}

/** A popup when the broker turns an order down, with its reason: refused at once, or (like
 * Kotak's margin check) accepted and rejected a moment later. Rejections from before the page
 * opened stay quiet. */
export function RejectionToast({ trades }: { trades: Trade[] | undefined }) {
  const known = useRef<Set<string> | null>(null)
  const [rejected, setRejected] = useState<Trade | null>(null)

  useEffect(() => {
    if (!trades) return
    const fresh = known.current ? rejectedSince(known.current, trades).at(-1) : undefined
    if (fresh) setRejected(fresh)
    known.current = new Set(trades.filter((trade) => trade.state === 'REJECTED').map((t) => t.id))
  }, [trades])

  const reason = useQuery({
    queryKey: ['timeline', rejected?.id],
    queryFn: () => api.timeline(rejected?.id as string),
    enabled: rejected !== null,
    select: (events) => events.find((event) => event.type === 'ENTRY_REJECTED')?.data.reason,
  })

  useEffect(() => {
    if (!rejected) return
    const timer = setTimeout(() => setRejected(null), REJECTION_MS)
    return () => clearTimeout(timer)
  }, [rejected])

  if (!rejected) return null
  return (
    <div className="toast error" role="alert" key={rejected.id}>
      <b>Order rejected</b> {rejected.trading_symbol}
      {typeof reason.data === 'string' && <span className="reason">{reason.data}</span>}
    </div>
  )
}
