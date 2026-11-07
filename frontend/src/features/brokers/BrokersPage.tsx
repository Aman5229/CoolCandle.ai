import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { ErrorText, Field, Notice } from '../../components/Field'
import { api, type Account, type BrokerSpec, type Funds } from '../../lib/api'
import { inr } from '../../lib/format'

function BrokerForm({ spec, onSaved }: { spec: BrokerSpec; onSaved: () => void }) {
  const [values, setValues] = useState<Record<string, string>>({})
  const save = useMutation({
    mutationFn: () => api.saveAccount(spec.id, spec.name, values),
    onSuccess: () => {
      setValues({})
      onSaved()
    },
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    save.mutate()
  }
  return (
    <form className="flow" onSubmit={submit}>
      <div className="form-grid">
        {spec.fields.map((field) => (
          <Field key={field.name} label={field.label}>
            <input type={field.secret ? 'password' : 'text'} autoComplete="off" required
              value={values[field.name] ?? ''}
              onChange={(e) => setValues({ ...values, [field.name]: e.target.value })} />
          </Field>
        ))}
      </div>
      <ErrorText error={save.error} />
      <p className="muted small">Stored encrypted. Your TOTP is never stored.</p>
      <button type="submit" className="btn primary">Save</button>
    </form>
  )
}

function MorningConnect({ account, onDone }: { account: Account; onDone: () => void }) {
  const [totp, setTotp] = useState('')
  const [funds, setFunds] = useState<Funds | null>(null)
  const connect = useMutation({
    mutationFn: () => api.connect(account.id, totp),
    onSuccess: (result) => {
      setFunds(result)
      setTotp('')
      onDone()
    },
    onSettled: () => setTotp(''),
  })
  const remove = useMutation({ mutationFn: () => api.removeAccount(account.id), onSuccess: onDone })
  return (
    <div className="flow">
      <p>Status: <b>{account.status.replaceAll('_', ' ')}</b></p>
      <Notice tone="warn">
        Connecting logs in to your real broker account and ends any other session on it, including the
        Kotak app. Never connect from a second machine while the server is trading.
      </Notice>
      <Field label="Kotak TOTP (daily)">
        <input inputMode="numeric" autoComplete="one-time-code" value={totp}
          onChange={(e) => setTotp(e.target.value)} />
      </Field>
      <ErrorText error={connect.error ?? remove.error} />
      {funds && <p>Available funds: <b>{inr(funds.available)}</b></p>}
      <div className="row">
        <button type="button" className="btn primary" disabled={!totp || connect.isPending}
          onClick={() => connect.mutate()}>
          Connect
        </button>
        <button type="button" className="btn" onClick={() => remove.mutate()}>Remove credentials</button>
      </div>
    </div>
  )
}

export function BrokersPage() {
  const client = useQueryClient()
  const catalog = useQuery({ queryKey: ['brokers'], queryFn: api.brokers })
  const accounts = useQuery({ queryKey: ['accounts'], queryFn: api.accounts })
  const account = accounts.data?.[0]
  const refresh = () => void client.invalidateQueries({ queryKey: ['accounts'] })

  return (
    <div className="stack">
      {catalog.data?.map((spec) => (
        <section key={spec.id} className="card">
          <div className="card-head">
            <h2>{spec.name}</h2>
            <span className={`chip ${spec.status === 'live' ? 'ok' : ''}`}>
              {spec.status === 'live' ? 'Available' : 'Coming soon'}
            </span>
          </div>
          {spec.status === 'live' &&
            (account?.has_credentials && account.broker === spec.id ? (
              <MorningConnect account={account} onDone={refresh} />
            ) : (
              <BrokerForm spec={spec} onSaved={refresh} />
            ))}
        </section>
      ))}
    </div>
  )
}
