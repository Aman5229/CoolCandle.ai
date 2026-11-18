import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { ErrorText, Field, Notice } from '../../components/Field'
import { inr, inrWhole, pnlClass } from '../../lib/format'
import { START_FUNDS, skillApi, type AccountView, type SessionSummary } from './api'
import { dayText } from './hud'

function StartRun({ account }: { account: AccountView }) {
  const client = useQueryClient()
  const [fund, setFund] = useState<string>(START_FUNDS[1])
  const [day, setDay] = useState(account.earliest_start ?? '')
  const [typed, setTyped] = useState('')
  const retiring = account.run !== null
  const start = useMutation({
    mutationFn: () => skillApi.startRun(fund, day, retiring ? typed : undefined),
    onSuccess: () => {
      setTyped('')
      void client.invalidateQueries({ queryKey: ['skill'] })
    },
  })
  return (
    <div className="flow">
      <div className="row">
        {START_FUNDS.map((amount) => (
          <button key={amount} type="button" className={`seg${fund === amount ? ' active' : ''}`}
            onClick={() => setFund(amount)}>
            {inrWhole(amount)}
          </button>
        ))}
      </div>
      <Field label="The run's today" hint="The chart shows the year before it; the days after it play forward.">
        <input type="date" value={day} min={account.earliest_start ?? undefined}
          max={account.last_day ?? undefined} onChange={(e) => setDay(e.target.value)}
          aria-label="Start day" />
      </Field>
      {retiring && (
        <Field label="Type RETIRE to end this run">
          <input value={typed} onChange={(e) => setTyped(e.target.value)} aria-label="Type RETIRE" />
        </Field>
      )}
      <ErrorText error={start.error} />
      <button type="button" className="btn primary" disabled={start.isPending || !day}
        onClick={() => start.mutate()}>
        {retiring ? 'Retire and start a new run' : `Start a run with ${inrWhole(fund)}`}
      </button>
    </div>
  )
}

export function ScoreRow({ summary, onOpen }: { summary: SessionSummary; onOpen: () => void }) {
  return (
    <button type="button" className="score-row" onClick={onOpen}>
      <span className="score-pill">{summary.score ?? '–'}</span>
      <span>
        <b>{summary.index}</b> <span className="muted small">{dayText(summary.day)}</span>
        {summary.lock_reason && <span className="tag">{summary.lock_reason.replace('_', ' ')}</span>}
      </span>
      <span className={pnlClass(summary.net_pnl ?? 0)}>{inr(summary.net_pnl)}</span>
    </button>
  )
}

export function Lobby({
  account,
  onStart,
  onResume,
  onReport,
  onHistory,
}: {
  account: AccountView
  onStart: () => void
  onResume: (id: string) => void
  onReport: (id: string) => void
  onHistory: () => void
}) {
  const [retire, setRetire] = useState(false)
  const run = account.run
  const closed = account.live_locked && !account.settings.when_live_locked
  const running = account.running?.state === 'ACTIVE' ? account.running : null
  const played = run !== null && account.last_day !== null && run.today > account.last_day
  const canStart = run !== null && !closed && running === null && !played

  if (account.indices.length === 0) {
    return (
      <div className="card">
        <h2>Skill Test</h2>
        <p>
          Skill Test replays past NIFTY and SENSEX days with options priced by a model, so you can
          practise your plan without real money. It needs the index history first.
        </p>
        <p>Fetch it once, from the <code>backend</code> folder, then reload this page:</p>
        <pre className="code">uv run coolcandle fetch-index-history</pre>
        <p className="muted small">
          It uses only your Kotak consumer key, never logs in, and takes about five minutes.
        </p>
      </div>
    )
  }

  return (
    <div className="stack">
      {account.live_locked && (
        <Notice tone="warn">
          {closed
            ? 'Live is locked for today, so Skill Test is closed until it unlocks.'
            : 'Live is locked for today. This is practice only.'}
        </Notice>
      )}

      <div className="card">
        <div className="card-head">
          <h2>{run ? `Run #${run.number}` : 'Your practice fund'}</h2>
        </div>
        {run ? (
          <>
            <div className="big-number" data-testid="skill-fund">{inr(run.cash)}</div>
            <dl className="facts compact">
              <dt>Today in this run</dt><dd data-testid="skill-today">{dayText(run.today)}</dd>
              <dt>Started with</dt><dd>{inr(run.start_fund)}</dd>
              <dt>Blown below</dt><dd>{inr(run.blown_line)}</dd>
              <dt>Room above it</dt><dd>{inr(Number(run.cash) - Number(run.blown_line))}</dd>
              {account.best_score !== null && (<><dt>Best score</dt><dd>{account.best_score}</dd></>)}
            </dl>
            {retire ? <StartRun account={account} /> : (
              <button type="button" className="link small" onClick={() => setRetire(true)}>
                Retire this run
              </button>
            )}
          </>
        ) : (
          <>
            <p className="muted">
              {account.runs > 0
                ? 'Your last run has ended. Start a new one: the old one stays in your history.'
                : 'Pick a fund and a day to call today. Each session then plays the next trading day. If the fund falls 30% below its start, the run ends.'}
            </p>
            <StartRun account={account} />
          </>
        )}
      </div>

      {running && (
        <Notice tone="info">
          A {running.index} session is running.{' '}
          <button type="button" className="link" onClick={() => onResume(running.id)}>Resume it</button>
        </Notice>
      )}
      {played && <Notice tone="info">This run has played every stored day. Fetch newer history, or start a new run.</Notice>}

      <button type="button" className="tile" disabled={!canStart} onClick={onStart}>
        <strong>{run ? `Play ${dayText(run.today)}` : 'Play the next day'}</strong>
        <span className="muted">
          {account.indices.join(' or ')} from 09:15, replayed at 1x to 60x. Options are priced by a
          model, and the day locks at your limits, as live does.
        </span>
      </button>

      <div className="card">
        <div className="card-head">
          <h3>Last sessions</h3>
          <button type="button" className="link" onClick={onHistory}>All runs</button>
        </div>
        {account.recent.length === 0 ? (
          <p className="muted">No sessions yet. Your discipline score shows here after each one.</p>
        ) : (
          <div className="score-list">
            {account.recent.map((s) => <ScoreRow key={s.id} summary={s} onOpen={() => onReport(s.id)} />)}
          </div>
        )}
      </div>
    </div>
  )
}
