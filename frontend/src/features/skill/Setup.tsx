import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ErrorText, Field } from '../../components/Field'
import { inr, shortTime } from '../../lib/format'
import { SPEEDS, skillApi, type AccountView, type SessionPlan, type SessionView } from './api'
import { dayText, defaultPlan } from './hud'

const WORD = 'START'

function Summary({ plan }: { plan: SessionPlan }) {
  return (
    <dl className="facts compact">
      <dt>Profit target</dt><dd>{inr(plan.profit_target)}</dd>
      <dt>Loss limit</dt><dd>{inr(plan.loss_limit)}</dd>
      <dt>Max trades</dt><dd>{plan.max_trades}</dd>
      <dt>Max loss a trade</dt><dd>{inr(plan.max_loss_per_trade)}</dd>
      <dt>Cool-off after a loss</dt><dd>{plan.cool_off_minutes} min</dd>
      <dt>Min reward to risk</dt><dd>{plan.min_reward_risk}</dd>
      <dt>Entries</dt><dd>{shortTime(plan.entry_start)} to {shortTime(plan.entry_cutoff)}</dd>
      <dt>Square-off</dt><dd>{shortTime(plan.square_off)}</dd>
    </dl>
  )
}

export function Setup({
  account,
  onStarted,
  onCancel,
}: {
  account: AccountView
  onStarted: (id: string) => void
  onCancel: () => void
}) {
  const client = useQueryClient()
  const [index, setIndex] = useState(account.indices[0] ?? 'NIFTY')
  const [speed, setSpeed] = useState<number>(1)
  const [plan, setPlan] = useState<SessionPlan>(() => defaultPlan(Number(account.run?.cash ?? 100000)))
  const [draft, setDraft] = useState<SessionView | null>(null)
  const [typed, setTyped] = useState('')

  const set = <K extends keyof SessionPlan>(key: K, value: SessionPlan[K]) =>
    setPlan((current) => ({ ...current, [key]: value }))

  const review = useMutation({
    mutationFn: async () => {
      const created = await skillApi.create({ index, speed, plan })
      return skillApi.confirm(created.id, 1)
    },
    onSuccess: (view) => setDraft(view),
  })

  const begin = useMutation({
    mutationFn: () => skillApi.confirm(draft?.id ?? '', 2, typed),
    onSuccess: (view) => {
      client.setQueryData(['skill', 'session', view.id], view)
      void client.invalidateQueries({ queryKey: ['skill', 'account'] })
      onStarted(view.id)
    },
  })

  return (
    <div className="card">
      <div className="card-head">
        <h2>{account.run ? `Play ${dayText(account.run.today)}` : 'Play the next day'}</h2>
        <button type="button" className="link" onClick={onCancel}>Back</button>
      </div>
      <div className="form-grid">
        <Field label="Index">
          <select value={index} onChange={(e) => setIndex(e.target.value)}>
            {account.indices.map((name) => <option key={name} value={name}>{name}</option>)}
          </select>
        </Field>
        <Field label="Speed" hint="Change it any time while the day plays">
          <select value={speed} onChange={(e) => setSpeed(Number(e.target.value))}>
            {SPEEDS.map((s) => <option key={s} value={s}>{s}x</option>)}
          </select>
        </Field>
      </div>

      <h3>The day's plan</h3>
      <div className="form-grid">
        <Field label="Profit target (₹)">
          <input inputMode="decimal" value={plan.profit_target}
            onChange={(e) => set('profit_target', e.target.value)} />
        </Field>
        <Field label="Loss limit (₹)">
          <input inputMode="decimal" value={plan.loss_limit}
            onChange={(e) => set('loss_limit', e.target.value)} />
        </Field>
        <Field label="Max trades">
          <input type="number" min={1} max={20} value={plan.max_trades}
            onChange={(e) => set('max_trades', Number(e.target.value))} />
        </Field>
        <Field label="Max loss a trade (₹)">
          <input inputMode="decimal" value={plan.max_loss_per_trade}
            onChange={(e) => set('max_loss_per_trade', e.target.value)} />
        </Field>
        <Field label="Cool-off after a loss (min)">
          <input type="number" min={0} value={plan.cool_off_minutes}
            onChange={(e) => set('cool_off_minutes', Number(e.target.value))} />
        </Field>
        <Field label="Min reward to risk">
          <input inputMode="decimal" value={plan.min_reward_risk}
            onChange={(e) => set('min_reward_risk', e.target.value)} />
        </Field>
        <Field label="Entries from (IST)">
          <input type="time" value={shortTime(plan.entry_start)}
            onChange={(e) => set('entry_start', e.target.value)} aria-label="Entries from" />
        </Field>
        <Field label="Entries until (IST)">
          <input type="time" value={shortTime(plan.entry_cutoff)}
            onChange={(e) => set('entry_cutoff', e.target.value)} aria-label="Entries until" />
        </Field>
        <Field label="Square-off (IST)">
          <input type="time" value={shortTime(plan.square_off)}
            onChange={(e) => set('square_off', e.target.value)} aria-label="Square-off" />
        </Field>
      </div>
      <p className="muted small">
        Once it starts, the plan only gets stricter. A limit or the square-off locks the day, as
        live does, and the run moves on to the next day.
      </p>
      <ErrorText error={review.error} />
      <button type="button" className="btn primary" disabled={review.isPending}
        onClick={() => review.mutate()}>
        Review plan
      </button>

      {draft && (
        <div className="dialog-backdrop">
          <div className="dialog" role="dialog" aria-label="Start the session">
            <h2>Start {draft.index} on {dayText(draft.day)}?</h2>
            <Summary plan={draft.plan} />
            <p>Type <b>{WORD}</b> to begin. From then on the plan only gets stricter.</p>
            <input value={typed} onChange={(e) => setTyped(e.target.value)} aria-label="Type START" />
            <ErrorText error={begin.error} />
            <div className="row">
              <button type="button" className="btn" onClick={() => setDraft(null)}>Change the plan</button>
              <button type="button" className="btn primary" disabled={typed !== WORD || begin.isPending}
                onClick={() => begin.mutate()}>
                {begin.isPending ? 'Starting…' : 'Start'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
