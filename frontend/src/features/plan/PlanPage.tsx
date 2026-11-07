import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { ErrorText, Field, Notice } from '../../components/Field'
import { Padlock } from '../../components/Padlock'
import { api, type Plan, type PlanInput } from '../../lib/api'
import { inrWhole, shortTime } from '../../lib/format'

const DEFAULTS: PlanInput = {
  profit_target: '5000',
  loss_limit: '3000',
  max_trades: 3,
  max_loss_per_trade: '1500',
  entry_start: '09:20',
  entry_cutoff: '14:30',
  square_off: '15:15',
  cool_off_minutes: 10,
  min_reward_risk: '1',
  allowed_underlyings: ['NIFTY', 'SENSEX'],
  trail_at_target: false,
  target_giveback_pct: '20',
}

function toInput(plan: Plan): PlanInput {
  const { trading_date: _d, status: _s, confirmations: _c, ...rest } = plan
  return {
    ...rest,
    entry_start: shortTime(plan.entry_start),
    entry_cutoff: shortTime(plan.entry_cutoff),
    square_off: shortTime(plan.square_off),
  }
}

function Summary({ form }: { form: PlanInput }) {
  return (
    <dl className="facts">
      <dt>Profit target</dt><dd>{inrWhole(form.profit_target)}</dd>
      <dt>Loss limit</dt><dd>{inrWhole(form.loss_limit)}</dd>
      <dt>Max trades</dt><dd>{form.max_trades}</dd>
      <dt>Max loss a trade</dt><dd>{inrWhole(form.max_loss_per_trade)}</dd>
      <dt>Entries</dt><dd>{shortTime(form.entry_start)} to {shortTime(form.entry_cutoff)}</dd>
      <dt>Square-off</dt><dd>{shortTime(form.square_off)}</dd>
      <dt>Cool-off after a loss</dt><dd>{form.cool_off_minutes} min</dd>
    </dl>
  )
}

export function PlanPage({ onTrade }: { onTrade: () => void }) {
  const client = useQueryClient()
  const plan = useQuery({ queryKey: ['plan'], queryFn: api.plan })
  const [form, setForm] = useState<PlanInput>(DEFAULTS)
  const [typed, setTyped] = useState('')
  const [justLocked, setJustLocked] = useState(false)
  const locked = plan.data?.status === 'LOCKED'
  const confirming = plan.data?.status === 'CONFIRMING'

  useEffect(() => {
    if (plan.data) setForm(toInput(plan.data))
  }, [plan.data])

  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['plan'] })
    void client.invalidateQueries({ queryKey: ['day'] })
  }
  const save = useMutation({ mutationFn: () => api.savePlan(form), onSuccess: refresh })
  const firstConfirm = useMutation({
    mutationFn: async () => {
      await api.savePlan(form)
      return api.confirmPlan(1)
    },
    onSuccess: refresh,
  })
  const secondConfirm = useMutation({
    mutationFn: () => api.confirmPlan(2, typed),
    onSuccess: () => {
      setTyped('')
      setJustLocked(true)
      refresh()
    },
  })
  const cancel = useMutation({ mutationFn: api.cancelPlan, onSuccess: refresh })

  const set = <K extends keyof PlanInput>(key: K, value: PlanInput[K]) =>
    setForm((current) => ({ ...current, [key]: value }))

  return (
    <section className="card">
      <h2>Today's plan</h2>
      {locked ? (
        <Notice>
          <Padlock size={16} closing={justLocked} />{' '}
          The plan is locked until the next trading day. You can only make it stricter.{' '}
          <button type="button" className="link" onClick={onTrade}>Go to trading</button>
        </Notice>
      ) : (
        <p className="muted">Set your limits before you trade. Locking takes two confirmations.</p>
      )}

      <div className="form-grid">
        <Field label="Profit target (₹)">
          <input inputMode="decimal" value={form.profit_target}
            onChange={(e) => set('profit_target', e.target.value)} />
        </Field>
        <Field label="Loss limit (₹)">
          <input inputMode="decimal" value={form.loss_limit}
            onChange={(e) => set('loss_limit', e.target.value)} />
        </Field>
        <Field label="Max trades">
          <input type="number" min={1} max={20} value={form.max_trades}
            onChange={(e) => set('max_trades', Number(e.target.value))} />
        </Field>
        <Field label="Max loss a trade (₹)">
          <input inputMode="decimal" value={form.max_loss_per_trade}
            onChange={(e) => set('max_loss_per_trade', e.target.value)} />
        </Field>
        <Field label="No entries before">
          <input type="time" value={shortTime(form.entry_start)}
            onChange={(e) => set('entry_start', e.target.value)} />
        </Field>
        <Field label="No entries after">
          <input type="time" value={shortTime(form.entry_cutoff)}
            onChange={(e) => set('entry_cutoff', e.target.value)} />
        </Field>
        <Field label="Square off at">
          <input type="time" value={shortTime(form.square_off)}
            onChange={(e) => set('square_off', e.target.value)} />
        </Field>
        <Field label="Cool-off after a loss (min)">
          <input type="number" min={0} value={form.cool_off_minutes}
            onChange={(e) => set('cool_off_minutes', Number(e.target.value))} />
        </Field>
        <Field label="Minimum reward to risk">
          <input inputMode="decimal" value={form.min_reward_risk}
            onChange={(e) => set('min_reward_risk', e.target.value)} />
        </Field>
        <Field label="At the day target" hint="Trailing keeps a trade open, protecting most of the target.">
          <select value={form.trail_at_target ? 'trail' : 'exit'}
            onChange={(e) => set('trail_at_target', e.target.value === 'trail')}>
            <option value="exit">Exit and lock</option>
            <option value="trail">Trail the open trade</option>
          </select>
        </Field>
      </div>

      <ErrorText error={save.error ?? firstConfirm.error} />
      <div className="row">
        {locked ? (
          <button type="button" className="btn" onClick={() => save.mutate()}>Make stricter</button>
        ) : (
          <>
            <button type="button" className="btn" onClick={() => save.mutate()}>Save draft</button>
            <button type="button" className="btn primary" onClick={() => firstConfirm.mutate()}>
              Review and lock
            </button>
          </>
        )}
      </div>

      {confirming && (
        <div className="dialog-backdrop">
          <div className="dialog" role="dialog" aria-label="Lock today's plan">
            <h2>Lock this plan?</h2>
            <Summary form={form} />
            <p>Once locked it can only get stricter until the next trading day. Type <b>LOCK</b>.</p>
            <input value={typed} onChange={(e) => setTyped(e.target.value)} aria-label="Type LOCK" />
            <ErrorText error={secondConfirm.error} />
            <div className="row">
              <button type="button" className="btn" onClick={() => cancel.mutate()}>Back</button>
              <button type="button" className="btn primary" disabled={typed !== 'LOCK'}
                onClick={() => secondConfirm.mutate()}>
                Lock the plan
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
