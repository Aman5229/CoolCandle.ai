import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import { Loader } from '../../components/Brand'
import { ErrorText } from '../../components/Field'
import { inr, pnlClass } from '../../lib/format'
import { skillApi } from './api'
import { ScoreRow } from './Lobby'

function RunSessions({ runId, onReport }: { runId: string; onReport: (id: string) => void }) {
  const sessions = useQuery({ queryKey: ['skill', 'run', runId], queryFn: () => skillApi.runSessions(runId) })
  if (sessions.error) return <ErrorText error={sessions.error} />
  if (!sessions.data) return <Loader size={120} />
  const over = sessions.data.filter((s) => s.state === 'LOCKED' || s.state === 'ENDED')
  if (over.length === 0) return <p className="muted">No finished sessions in this run.</p>
  return (
    <div className="score-list">
      {over.map((s) => <ScoreRow key={s.id} summary={s} onOpen={() => onReport(s.id)} />)}
    </div>
  )
}

/** Runs, then a run's sessions, then a session's report card. */
export function HistoryView({ onReport, onLobby }: { onReport: (id: string) => void; onLobby: () => void }) {
  const runs = useQuery({ queryKey: ['skill', 'runs'], queryFn: skillApi.runs })
  const [open, setOpen] = useState<string | null>(null)
  if (runs.error) return <ErrorText error={runs.error} />
  if (!runs.data) return <div className="center stage"><Loader text="Loading your runs…" /></div>
  return (
    <div className="card">
      <div className="card-head">
        <h2>History</h2>
        <button type="button" className="link" onClick={onLobby}>Back to lobby</button>
      </div>
      {runs.data.length === 0 && <p className="muted">No runs yet.</p>}
      {runs.data.map((run) => (
        <div key={run.id} className="flow">
          <button type="button" className="score-row" onClick={() => setOpen(open === run.id ? null : run.id)}>
            <span className="score-pill">#{run.number}</span>
            <span>
              {inr(run.start_fund)} → <b>{inr(run.cash)}</b>
              <span className="tag">{run.status}</span>
            </span>
            <span className={pnlClass(Number(run.cash) - Number(run.start_fund))}>
              {inr(Number(run.cash) - Number(run.start_fund))}
            </span>
          </button>
          {open === run.id && <RunSessions runId={run.id} onReport={onReport} />}
        </div>
      ))}
    </div>
  )
}
