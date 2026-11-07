import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { Logo } from '../../components/Brand'
import { ErrorText, Field } from '../../components/Field'
import { api } from '../../lib/api'

export function SignIn() {
  const client = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [code, setCode] = useState('')
  const login = useMutation({
    mutationFn: () => {
      const request = api.login(email, password, code)
      // A full-page loader is fine here: nothing else is on screen yet.
      return window.CoolCandleLoader?.track(request, { text: 'Signing you in…' }) ?? request
    },
    onSuccess: () => client.invalidateQueries(),
    onSettled: () => setCode(''),
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    login.mutate()
  }
  return (
    <div className="auth-page">
      <form className="card auth-card" onSubmit={submit}>
        <div className="auth-logo"><Logo play="loop" size={72} /></div>
        <p className="muted">Your plan, your limits, one calm day at a time.</p>
        <Field label="Email">
          <input type="email" autoComplete="username" required value={email}
            onChange={(e) => setEmail(e.target.value)} />
        </Field>
        <Field label="Password">
          <input type="password" autoComplete="current-password" required value={password}
            onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field label="Authenticator code">
          <input inputMode="numeric" autoComplete="one-time-code" pattern="[0-9]{6}" required
            value={code} onChange={(e) => setCode(e.target.value)} />
        </Field>
        <ErrorText error={login.error} />
        <button type="submit" className="btn primary" disabled={login.isPending}>Sign in</button>
      </form>
    </div>
  )
}
