import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ErrorText } from '../../components/Field'
import { api, type Trade } from '../../lib/api'
import { inr, pnlClass, price } from '../../lib/format'
import { useTick, useTickDirection } from '../../lib/hooks'

export function OpenTrade({ trade }: { trade: Trade }) {
  const client = useQueryClient()
  const tick = useTick(trade.instrument_key)
  const [stop, setStop] = useState('')
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['trades'] })
    void client.invalidateQueries({ queryKey: ['day'] })
  }
  const exit = useMutation({ mutationFn: () => api.exitTrade(trade.id), onSuccess: refresh })
  const cancel = useMutation({ mutationFn: () => api.cancelTrade(trade.id), onSuccess: refresh })
  const move = useMutation({
    mutationFn: () => api.moveStop(trade.id, stop),
    onSuccess: () => {
      setStop('')
      refresh()
    },
  })

  const pending = trade.state === 'ENTRY_PENDING'
  const entry = Number(trade.entry_avg ?? trade.entry_limit)
  const pnl = tick && !pending ? (Number(tick.ltp) - entry) * trade.filled_qty : null
  const ticked = useTickDirection(tick?.ltp)
  const stopMoved = useTickDirection(trade.stop_trigger) !== null

  return (
    <div className="open-trade flow" data-testid="open-trade">
      <div className="card-head">
        <h3>{trade.trading_symbol}</h3>
        <span key={trade.state} className={`chip pop ${trade.state === 'NEEDS_ATTENTION' ? 'bad' : 'info'}`}>
          {pending && <span className="dot pulse" />}
          {trade.state}
        </span>
      </div>
      <dl className="facts compact">
        <dt>Qty</dt><dd>{trade.filled_qty || trade.qty}</dd>
        <dt>Entry</dt><dd>{price(trade.entry_avg ?? trade.entry_limit)}</dd>
        <dt>Last</dt>
        <dd><span key={tick?.ltp} className={ticked ? `flash-${ticked}` : undefined}>{price(tick?.ltp)}</span></dd>
        <dt>Stop</dt>
        <dd><span key={trade.stop_trigger} className={stopMoved ? 'flash-stop' : undefined}>
          {price(trade.stop_trigger)}
        </span></dd>
        <dt>Target</dt><dd>{price(trade.target)}</dd>
        <dt>Trailing</dt><dd>{trade.trailing?.kind ?? 'Off'}</dd>
        <dt>Live P&L</dt><dd className={pnl === null ? '' : pnlClass(pnl)}>{inr(pnl)}</dd>
      </dl>
      <ErrorText error={exit.error ?? cancel.error ?? move.error} />
      {pending ? (
        <button type="button" className="btn wide" onClick={() => cancel.mutate()}>Cancel entry</button>
      ) : (
        <>
          <div className="row">
            <input aria-label="New stop" placeholder="Tighter stop" inputMode="decimal" value={stop}
              onChange={(e) => setStop(e.target.value)} />
            <button type="button" className="btn" disabled={!stop} onClick={() => move.mutate()}>Move</button>
          </div>
          <button type="button" className="btn danger wide" disabled={exit.isPending}
            onClick={() => exit.mutate()}>
            {exit.isPending ? 'Exiting…' : 'Exit now'}
          </button>
        </>
      )}
    </div>
  )
}
