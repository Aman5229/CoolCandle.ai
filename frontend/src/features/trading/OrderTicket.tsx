import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { ErrorText, Field } from '../../components/Field'
import { api, type ChainSide, type Ticket, type TrailKind } from '../../lib/api'
import { inr, price } from '../../lib/format'
import { useTick } from '../../lib/hooks'

const TRAILS: { value: TrailKind | ''; label: string }[] = [
  { value: '', label: 'None' },
  { value: 'BREAKEVEN', label: 'Move to breakeven' },
  { value: 'TARGET_TRAIL', label: 'Trail past the target' },
  { value: 'STEP', label: 'Step trail' },
  { value: 'SWING_LOW', label: 'Swing-low trail' },
  { value: 'ATR', label: 'ATR trail' },
]

const valid = (value: string) => Number(value) > 0

export function OrderTicket({ option, canTrade }: { option: ChainSide; canTrade: boolean }) {
  const client = useQueryClient()
  const tick = useTick(option.key)
  const ask = tick?.ask ?? option.ask ?? option.ltp ?? ''
  const [limit, setLimit] = useState(ask)
  const [stop, setStop] = useState('')
  const [target, setTarget] = useState('')
  const [lots, setLots] = useState(1)
  const [trail, setTrail] = useState<TrailKind | ''>('')
  const [sized, setSized] = useState(false)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setLimit(option.ask ?? option.ltp ?? '')
    setStop('')
    setTarget('')
    setSized(false)
  }, [option.key, option.ask, option.ltp])

  // On a narrow screen the ticket sits above the chain, so bring it into view when a strike is picked.
  useEffect(() => {
    box.current?.scrollIntoView?.({ block: 'nearest' })
  }, [option.key])

  const ticket: Ticket = {
    instrument_key: option.key,
    lots,
    limit_price: limit,
    stop_trigger: stop,
    target: target || null,
    trailing: trail ? { kind: trail } : null,
  }
  const ready = valid(limit) && valid(stop) && lots > 0

  const preview = useQuery({
    queryKey: ['preview', ticket],
    queryFn: () => api.preview(ticket),
    enabled: ready,
    refetchInterval: 2000,
    retry: false,
  })

  // The ticket opens at the largest size the guard allows.
  useEffect(() => {
    if (!sized && preview.data?.size && preview.data.size.lots > 0) {
      setLots(preview.data.size.lots)
      setSized(true)
    }
  }, [preview.data, sized])

  const place = useMutation({
    mutationFn: () => api.submit(ticket),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['trades'] })
      void client.invalidateQueries({ queryKey: ['day'] })
    },
  })

  const decision = preview.data?.decision
  const blocked = decision?.action === 'BLOCK'
  const allowed = canTrade && ready && !!decision && !blocked

  return (
    <div className="ticket flow" ref={box}>
      <div className="card-head">
        <h3>{option.trading_symbol}</h3>
        <span className="muted small">Bid {price(tick?.bid ?? option.bid)} · Ask {price(ask)}</span>
      </div>
      <div className="form-grid tight">
        <Field label="Limit price">
          <input inputMode="decimal" value={limit} onChange={(e) => setLimit(e.target.value)} />
        </Field>
        <Field label="Stop (required)">
          <input inputMode="decimal" value={stop} onChange={(e) => setStop(e.target.value)}
            aria-label="Stop price" />
        </Field>
        <Field label="Target (optional)">
          <input inputMode="decimal" value={target} onChange={(e) => setTarget(e.target.value)} />
        </Field>
        <Field label="Lots">
          <input type="number" min={1} value={lots} onChange={(e) => setLots(Number(e.target.value))} />
        </Field>
        <Field label="Trailing stop">
          <select value={trail} onChange={(e) => setTrail(e.target.value as TrailKind | '')}>
            {TRAILS.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
          </select>
        </Field>
      </div>

      {preview.data && (
        <dl className="facts compact">
          <dt>Worst-case loss</dt><dd>{inr(preview.data.worst_case)}</dd>
          <dt>Charges</dt><dd>{inr(preview.data.charges)}</dd>
          <dt>Max lots</dt>
          <dd>{preview.data.size?.lots ?? '–'} ({preview.data.size?.limited_by.replaceAll('_', ' ')})</dd>
          {preview.data.reward_risk && (
            <><dt>Reward to risk</dt><dd>{Number(preview.data.reward_risk).toFixed(2)}</dd></>
          )}
        </dl>
      )}
      {decision?.reasons.map((reason) => (
        <p key={reason.code} className={blocked ? 'form-error' : 'form-warn'}>{reason.message}</p>
      ))}
      <ErrorText error={place.error ?? preview.error} />
      <p className="muted small">A stop is a plan, not a promise: in a fast market it can fill worse.</p>
      <button type="button" className={`btn primary wide${place.isPending ? ' busy' : ''}`}
        disabled={!allowed || place.isPending} onClick={() => place.mutate()}>
        {place.isPending ? 'Sending order…' : `Buy ${lots} lot${lots === 1 ? '' : 's'}`}
      </button>
    </div>
  )
}
