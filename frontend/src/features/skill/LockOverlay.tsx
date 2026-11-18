import { useMemo, useState } from 'react'
import { inr } from '../../lib/format'
import type { SessionView } from './api'
import { LOCK_TITLES } from './hud'

const PIECES = 48

function Confetti() {
  const pieces = useMemo(
    () =>
      Array.from({ length: PIECES }, (_, i) => ({
        left: `${(i * 37) % 100}%`,
        delay: `${(i % 12) * 0.08}s`,
        colour: ['var(--accent)', 'var(--up)', 'var(--skill-cyan)', 'var(--warn)'][i % 4],
      })),
    [],
  )
  return (
    <div className="confetti" aria-hidden>
      {pieces.map((piece, i) => (
        <i key={i} style={{ left: piece.left, animationDelay: piece.delay, background: piece.colour }} />
      ))}
    </div>
  )
}

/** Full screen when a limit locks the session. Confetti only for the profit target. */
export function LockOverlay({
  view,
  onReport,
  onLobby,
}: {
  view: SessionView
  onReport: () => void
  onLobby: () => void
}) {
  const [hidden, setHidden] = useState(false)
  if (view.state !== 'LOCKED' || !view.lock_reason || hidden) return null
  const blown = view.lock_reason === 'BLOWN'
  return (
    <>
      {view.lock_reason === 'TARGET' && <Confetti />}
      <div className="lock-overlay">
        <div className="lock-card" role="dialog" aria-label={LOCK_TITLES[view.lock_reason]}>
          <h2>{LOCK_TITLES[view.lock_reason]}</h2>
          {blown ? (
            <p>
              The fund fell more than 30% below where this run started. The run is over and stays
              in your history. Start a new one from the lobby.
            </p>
          ) : (
            <p className="muted">The session is locked. No more entries until you start a new one.</p>
          )}
          <div className="report-score" data-testid="lock-score">{view.score.score}</div>
          <p className="muted">Discipline score · grade {view.grade}</p>
          <dl className="facts compact">
            <dt>Session P&amp;L</dt><dd>{inr(view.pnl.net)}</dd>
            <dt>Fund now</dt><dd>{inr(view.fund)}</dd>
          </dl>
          <div className="row">
            <button type="button" className="btn primary" onClick={onReport}>See report card</button>
            <button type="button" className="btn" onClick={blown ? onLobby : () => setHidden(true)}>
              {blown ? 'Start a new run' : 'View the chart'}
            </button>
          </div>
        </div>
      </div>
    </>
  )
}
