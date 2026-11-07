import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, type FormEvent } from 'react'
import { ErrorText, Field } from '../../components/Field'
import { api } from '../../lib/api'
import { shortTime } from '../../lib/format'
import type { Theme } from '../../lib/hooks'

export function SettingsPage({ theme, onTheme }: { theme: Theme; onTheme: () => void }) {
  const client = useQueryClient()
  const events = useQuery({ queryKey: ['events'], queryFn: api.events })
  const [day, setDay] = useState('')
  const [name, setName] = useState('')
  const [at, setAt] = useState('')
  const add = useMutation({
    mutationFn: () => api.addEvent({ day, name, at: at || null }),
    onSuccess: () => {
      setDay('')
      setName('')
      setAt('')
      void client.invalidateQueries({ queryKey: ['events'] })
    },
  })
  const signOut = useMutation({
    mutationFn: api.signOutEverywhere,
    onSuccess: () => client.invalidateQueries(),
  })
  const submit = (event: FormEvent) => {
    event.preventDefault()
    add.mutate()
  }
  return (
    <div className="stack">
      <section className="card">
        <h2>Theme</h2>
        <button type="button" className="btn" onClick={onTheme}>
          Switch to {theme === 'dark' ? 'light' : 'dark'}
        </button>
      </section>
      <section className="card">
        <h2>Event calendar</h2>
        <p className="muted">
          New entries stop 15 minutes either side of a high-impact event, and the slippage buffer doubles
          that day. Exits are never affected.
        </p>
        <ul className="plain">
          {events.data?.map((event) => (
            <li key={`${event.day}${event.name}`}>
              <b>{event.day}</b> {event.at ? shortTime(event.at) : 'all day'} · {event.name}
            </li>
          ))}
        </ul>
        <form className="flow" onSubmit={submit}>
          <div className="form-grid">
            <Field label="Date"><input type="date" required value={day} onChange={(e) => setDay(e.target.value)} /></Field>
            <Field label="What"><input required value={name} onChange={(e) => setName(e.target.value)} /></Field>
            <Field label="Time (IST, optional)"><input type="time" value={at} onChange={(e) => setAt(e.target.value)} /></Field>
          </div>
          <ErrorText error={add.error} />
          <button type="submit" className="btn">Add event</button>
        </form>
      </section>
      <section className="card">
        <h2>Sessions</h2>
        <button type="button" className="btn danger" onClick={() => signOut.mutate()}>Sign out everywhere</button>
      </section>
    </div>
  )
}
