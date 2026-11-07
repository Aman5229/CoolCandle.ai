import { useEffect } from 'react'
import { Logo } from '../../components/Brand'
import { Padlock } from '../../components/Padlock'
import type { Day } from '../../lib/api'
import { inr, istTime, lockMessage, pnlClass } from '../../lib/format'

/** Driven only by the server's day status. There is deliberately no way to dismiss it. */
export function LockedScreen({ day }: { day: Day }) {
  useEffect(() => {
    window.scrollTo?.({ top: 0 })
  }, [])
  // The brand mark means "target hit, ring closes", so it marks that lock; the others get a padlock.
  const target = day.status.lock_reason === 'TARGET'
  return (
    <section className="card locked" data-testid="locked-screen">
      {target ? (
        <div className="locked-mark"><Logo play="once" size={72} wordmark={false} /></div>
      ) : (
        <div className="locked-icon"><Padlock size={28} closing /></div>
      )}
      <h2>{lockMessage(day.status.lock_reason)}</h2>
      <p>Trading opens again on the next trading day at 09:15.</p>
      <dl className="facts">
        <dt>Net P&L</dt><dd className={pnlClass(day.snapshot.net_pnl)}>{inr(day.snapshot.net_pnl)}</dd>
        <dt>Trades taken</dt><dd>{day.snapshot.trades_taken}</dd>
        <dt>Charges</dt><dd>{inr(day.snapshot.est_charges)}</dd>
        <dt>Locked at</dt><dd>{istTime(day.locked_at)}</dd>
      </dl>
      <p className="muted small">
        CoolCandle cannot stop orders in your broker's own app. To lock there too, use{' '}
        <a href={day.broker_kill_switch_url} target="_blank" rel="noreferrer">Kotak's kill switch</a>.
      </p>
    </section>
  )
}
