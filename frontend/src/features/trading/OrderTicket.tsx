import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { ErrorText, Field } from '../../components/Field'
import { api, type ChainSide, type StopRange, type Ticket, type TrailKind } from '../../lib/api'
import { inr, price } from '../../lib/format'
import { useThrottled, useTick, useTrades } from '../../lib/hooks'

const TRAILS: { value: TrailKind | ''; label: string }[] = [
  { value: '', label: 'None' },
  { value: 'BREAKEVEN', label: 'Move to breakeven' },
  { value: 'TARGET_TRAIL', label: 'Trail past the target' },
  { value: 'STEP', label: 'Step trail' },
  { value: 'SWING_LOW', label: 'Swing-low trail' },
  { value: 'ATR', label: 'ATR trail' },
]

const valid = (value: string) => Number(value) > 0

function stopHint(
  range: StopRange | null | undefined,
  lots: number,
  fromPlan: boolean,
  entry: string,
): string | undefined {
  if (!range) return undefined
  if (!range.lowest || !range.highest) {
    const size = `${lots} lot${lots === 1 ? '' : 's'}`
    return `No stop fits ${inr(range.budget)} at this price for ${size}: pick a cheaper strike`
  }
  if (!fromPlan) return `Your plan allows ${price(range.lowest)} to ${price(range.highest)}`
  const below = ((Number(entry) - Number(range.lowest)) / Number(entry)) * 100
  return `From your plan: ${inr(range.budget)} a trade, ${below.toFixed(1)}% below the entry`
}

export function OrderTicket({ option, canTrade }: { option: ChainSide; canTrade: boolean }) {
  const client = useQueryClient()
  const tick = useTick(option.key)
  const ask = tick?.ask ?? option.ask ?? option.ltp ?? ''
  // The limit follows the live ask until the trader types a price of their own.
  const [ownLimit, setOwnLimit] = useState<string | null>(null)
  const limit = ownLimit ?? ask
  // The stop comes from the plan until the trader types a tighter one of their own.
  const [ownStop, setOwnStop] = useState<string | null>(null)
  const [target, setTarget] = useState('')
  const [lots, setLots] = useState(1)
  const [trail, setTrail] = useState<TrailKind | ''>('')
  const [sized, setSized] = useState(false)
  const [sentId, setSentId] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setOwnLimit(null)
    setSentId(null)
    setOwnStop(null)
    setTarget('')
    setSized(false)
  }, [option.key])

  // On a narrow screen the ticket sits above the chain, so bring it into view when a strike is picked.
  useEffect(() => {
    box.current?.scrollIntoView?.({ block: 'nearest' })
  }, [option.key])

  // With no stop sent, the server sets the plan's stop from the price sent at that moment.
  const ticket: Ticket = {
    instrument_key: option.key,
    lots,
    limit_price: limit,
    stop_trigger: ownStop,
    target: target || null,
    trailing: trail ? { kind: trail } : null,
  }
  // A following limit changes with every tick: ask the guard about it once a second, not each time.
  const checkedLimit = useThrottled(limit, 1000)
  const checked: Ticket = { ...ticket, limit_price: checkedLimit }

  // The stops the plan allows: the lowest is the plan's stop, shown before anything is sent.
  const range = useQuery({
    queryKey: ['stop-range', option.key, checkedLimit, lots],
    queryFn: () => api.stopRange(option.key, checkedLimit, lots),
    enabled: valid(checkedLimit) && lots > 0,
    refetchInterval: 2000,
    retry: false,
  })
  const planStop = range.data?.lowest ?? null
  const ready = valid(limit) && lots > 0 && (ownStop === null ? planStop !== null : valid(ownStop))

  const preview = useQuery({
    queryKey: ['preview', checked],
    queryFn: () => api.preview(checked),
    enabled: ready,
    refetchInterval: 2000,
    retry: false,
  })

  // With a stop of the trader's own, the ticket opens at the largest size the guard allows.
  // The plan's stop already spends the whole per-trade limit on the lots chosen.
  useEffect(() => {
    if (!sized && ownStop !== null && preview.data?.size && preview.data.size.lots > 0) {
      setLots(preview.data.size.lots)
      setSized(true)
    }
  }, [preview.data, sized, ownStop])

  const place = useMutation({
    mutationFn: () => api.submit(ticket),
    onMutate: () => setSentId(null),
    onSuccess: (trade) => setSentId(trade.id),
    onSettled: () => {
      void client.invalidateQueries({ queryKey: ['trades'] })
      void client.invalidateQueries({ queryKey: ['day'] })
    },
  })

  // The broker can accept an order and reject it a moment later (Kotak's margin check does),
  // so watch the order just sent and say why if it is rejected.
  const sent = useTrades().data?.find((trade) => trade.id === sentId)
  const rejection = useQuery({
    queryKey: ['timeline', sentId],
    queryFn: () => api.timeline(sentId as string),
    enabled: sent?.state === 'REJECTED',
    select: (events) => events.find((event) => event.type === 'ENTRY_REJECTED')?.data.reason,
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
        <Field label="Limit price" hint={ownLimit === null ? 'Follows the live ask' : undefined}>
          <input inputMode="decimal" value={limit} onChange={(e) => setOwnLimit(e.target.value)}
            aria-label="Limit price" />
          {ownLimit !== null && (
            <button type="button" className="link small" onClick={() => setOwnLimit(null)}>
              Follow the ask again
            </button>
          )}
        </Field>
        <Field label="Stop" hint={stopHint(range.data, lots, ownStop === null, checkedLimit)}>
          <input inputMode="decimal" value={ownStop ?? planStop ?? ''}
            onChange={(e) => setOwnStop(e.target.value)} aria-label="Stop price" />
          {ownStop !== null && (
            <button type="button" className="link small" onClick={() => setOwnStop(null)}>
              Use the plan's stop again
            </button>
          )}
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
          <dt>Down to the stop order's limit, {price(preview.data.stop_limit)}</dt>
          <dd>{inr(preview.data.to_stop_limit)}</dd>
          <dt>Slippage buffer</dt><dd>{inr(preview.data.slippage)}</dd>
          <dt>Charges</dt><dd>{inr(preview.data.charges)}</dd>
          <dt>Worst-case loss</dt><dd><b>{inr(preview.data.worst_case)}</b></dd>
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
      {sent?.state === 'REJECTED' && (
        <p className="form-error">
          The broker rejected the order{typeof rejection.data === 'string' ? `: ${rejection.data}` : ''}
        </p>
      )}
      <ErrorText error={place.error ?? preview.error} />
      <p className="muted small">A stop is a plan, not a promise: in a fast market it can fill worse.</p>
      <button type="button" className={`btn primary wide${place.isPending ? ' busy' : ''}`}
        disabled={!allowed || place.isPending} onClick={() => place.mutate()}>
        {place.isPending ? 'Sending order…' : `Buy ${lots} lot${lots === 1 ? '' : 's'}`}
      </button>
    </div>
  )
}
