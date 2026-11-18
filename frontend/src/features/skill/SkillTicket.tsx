import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ErrorText, Field } from '../../components/Field'
import { inr, price } from '../../lib/format'
import { skillApi, type OptionQuote, type SessionView, type SkillOrder } from './api'

/** Buy the option picked from the chain: lots, a stop that is required, an optional target, and
 * a preview of what it could cost before anything is sent. */
export function SkillTicket({
  view,
  option,
  open,
}: {
  view: SessionView
  option: OptionQuote | null
  open: boolean
}) {
  const client = useQueryClient()
  const [lots, setLots] = useState(1)
  const [stop, setStop] = useState('')
  const [target, setTarget] = useState('')

  const order: SkillOrder | null = option
    ? {
        instrument_key: option.key,
        lots,
        order_type: 'MARKET',
        stop: stop.trim() || null,
        target: target.trim() || null,
      }
    : null
  const preview = useQuery({
    queryKey: ['skill', 'preview', view.id, order],
    queryFn: () => skillApi.preview(view.id, order as SkillOrder),
    enabled: open && order !== null && lots > 0,
    refetchInterval: 2000,
    retry: false,
  })

  const place = useMutation({
    mutationFn: () => skillApi.enter(view.id, order as SkillOrder),
    onSuccess: (next) => {
      client.setQueryData(['skill', 'session', view.id], next)
      setStop('')
      setTarget('')
    },
  })

  const range = preview.data
  const stopHint =
    !open || !range?.entry
      ? undefined
      : range.lowest_stop && range.highest_stop
        ? `Your plan allows ${price(range.lowest_stop)} to ${price(range.highest_stop)}`
        : 'No stop on this option fits your per-trade limit'
  const decision = preview.data?.decision
  const blocked = decision?.action === 'BLOCK'
  const allowed = open && order !== null && Number(stop) > 0 && !!decision && !blocked

  return (
    <div className={`card${open ? '' : ' greyed'}`} aria-label="Order ticket" aria-disabled={!open}>
      <div className="card-head">
        <h3>{option ? option.trading_symbol : 'Pick an option from the chain'}</h3>
        {option && (
          <span className="muted small" data-testid="ticket-ask">
            Bid {price(option.bid)} · Ask {price(option.ask)}
          </span>
        )}
      </div>
      <div className="form-grid tight">
        <Field label={`Lots (${view.lot_size} each)`}>
          <input type="number" min={1} value={lots} onChange={(e) => setLots(Number(e.target.value))}
            aria-label="Lots" />
        </Field>
        <Field label="Stop (required)" hint={stopHint}>
          <input inputMode="decimal" value={stop} onChange={(e) => setStop(e.target.value)}
            aria-label="Stop price" placeholder="below the premium" />
        </Field>
        <Field label="Target (optional)">
          <input inputMode="decimal" value={target} onChange={(e) => setTarget(e.target.value)}
            aria-label="Target price" />
        </Field>
      </div>

      {!open && <p className="muted">The session is over. The ticket opens again in a new one.</p>}
      {open && preview.data && (
        <dl className="facts compact">
          <dt>Fills near</dt><dd>{price(preview.data.entry)}</dd>
          <dt>Worst-case loss</dt><dd>{inr(preview.data.worst_case_loss)}</dd>
          <dt>Charges</dt><dd>{inr(preview.data.charges)}</dd>
          <dt>Pays</dt><dd>{inr(preview.data.cash_needed)}</dd>
          {preview.data.reward_risk && (<><dt>Reward to risk</dt><dd>{preview.data.reward_risk}</dd></>)}
        </dl>
      )}
      {open && decision?.reasons.map((reason) => (
        <p key={reason.code} className="form-error">{reason.message}</p>
      ))}
      <ErrorText error={place.error ?? preview.error} />
      <p className="muted small">Premiums are model prices from the replayed index, not real quotes.</p>
      <button type="button" className="btn wide buy" disabled={!allowed || place.isPending}
        onClick={() => place.mutate()}>
        {place.isPending ? 'Sending order…' : `Buy ${lots} lot${lots === 1 ? '' : 's'}`}
      </button>
    </div>
  )
}
