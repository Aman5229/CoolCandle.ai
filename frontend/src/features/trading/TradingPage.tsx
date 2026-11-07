import { useState } from 'react'
import { Notice } from '../../components/Field'
import { isActive, type ChainSide, type Day } from '../../lib/api'
import { useSignals, useTrades } from '../../lib/hooks'
import { LockedScreen } from '../lock/LockedScreen'
import { SignalCard } from '../signals/SignalCard'
import { NewsPanel } from './NewsPanel'
import { OpenTrade } from './OpenTrade'
import { OptionChain } from './OptionChain'
import { OrderTicket } from './OrderTicket'
import { type Interval } from './bars'
import { PriceChart } from './PriceChart'
import { TradeToast } from './TradeToast'

const INDICES = { NIFTY: 'nse_cm|Nifty 50', SENSEX: 'bse_cm|SENSEX' } as const
type Underlying = keyof typeof INDICES

export function TradingPage({ day, onOpenPlan }: { day: Day; onOpenPlan: () => void }) {
  const [underlying, setUnderlying] = useState<Underlying>('NIFTY')
  const [interval, setInterval] = useState<Interval>('1m')
  const [option, setOption] = useState<ChainSide | null>(null)
  const trades = useTrades()
  const signals = useSignals()

  const locked = day.status.state === 'LOCKED'
  const noPlan = day.status.state === 'NO_PLAN'
  const open = trades.data?.filter(isActive) ?? []
  const canTrade = !locked && !noPlan && open.length === 0
  const liveSignals = signals.data?.filter((s) => s.status === 'NEW').slice(0, 3) ?? []

  return (
    <div className={`trading stack${locked ? ' is-locked' : ''}`}>
      {locked && <LockedScreen day={day} />}
      {noPlan && (
        <Notice tone="warn">
          Charts are on, ordering is off. <button type="button" className="link" onClick={onOpenPlan}>
            Lock today's plan
          </button> to trade.
        </Notice>
      )}
      {!locked && day.status.reasons.map((reason) => (
        <Notice key={reason.code} tone="warn">{reason.message}</Notice>
      ))}

      <div className="trading-grid">
        <section className="card chart-card">
          <div className="card-head">
            <div className="row">
              {(Object.keys(INDICES) as Underlying[]).map((name) => (
                <button key={name} type="button" className={`seg${underlying === name ? ' active' : ''}`}
                  onClick={() => { setUnderlying(name); setOption(null) }}>
                  {name}
                </button>
              ))}
            </div>
            <div className="row">
              {(['1m', '5m'] as Interval[]).map((name) => (
                <button key={name} type="button" className={`seg${interval === name ? ' active' : ''}`}
                  onClick={() => setInterval(name)}>
                  {name}
                </button>
              ))}
            </div>
          </div>
          <PriceChart instrumentKey={INDICES[underlying]} interval={interval} />
        </section>

        <aside className="side-column">
          {open.map((trade) => (
            <section key={trade.id} className="card enter"><OpenTrade trade={trade} /></section>
          ))}
          {liveSignals.map((signal) => (
            <section key={signal.id} className="card enter"><SignalCard signal={signal} canTrade={canTrade} /></section>
          ))}
          {!locked && option && open.length === 0 && (
            <section key={option.key} className="card enter">
              <OrderTicket option={option} canTrade={canTrade} />
            </section>
          )}
          {!locked && !option && open.length === 0 && (
            <section className="card enter">
              <h3>Take a trade</h3>
              <p className="muted">Tap a CE or PE price in the option chain to open an order ticket.</p>
            </section>
          )}
          <section className="card"><NewsPanel /></section>
        </aside>

        <section className="card chain-card">
          <OptionChain underlying={underlying} selected={option?.key} onPick={setOption} />
        </section>
      </div>
      <TradeToast trades={trades.data} />
    </div>
  )
}
