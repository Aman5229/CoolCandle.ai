import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { api, type Day, type Me, type Mode } from '../lib/api'
import { inr, inrWhole, pnlClass } from '../lib/format'
import { useTickDirection } from '../lib/hooks'
import { ErrorText } from './Field'
import { Icon } from './Icon'

const STATE_TEXT: Record<Day['status']['state'], { label: string; tone: string }> = {
  NO_PLAN: { label: 'No plan', tone: 'warn' },
  ACTIVE: { label: 'Active', tone: 'ok' },
  WARNING: { label: 'Warning', tone: 'warn' },
  COOL_OFF: { label: 'Cooling off', tone: 'warn' },
  ENTRIES_CLOSED: { label: 'Entries closed', tone: 'warn' },
  LOCKED: { label: 'Locked', tone: 'bad' },
}

/** Net P&L on a scale from −loss limit to +target, with zero where it really falls. */
export function PnlMeter({ day }: { day: Day }) {
  if (!day.plan) return null
  const loss = Number(day.plan.loss_limit)
  const target = Number(day.plan.profit_target)
  const net = Number(day.snapshot.net_pnl)
  const span = loss + target
  const zero = (loss / span) * 100
  const at = Math.min(Math.max(((net + loss) / span) * 100, 0), 100)
  const left = Math.min(at, zero)
  return (
    <div className="meter" role="meter" aria-valuenow={net} aria-valuemin={-loss} aria-valuemax={target}
      aria-label="Day P&L between the loss limit and the target">
      <span className="muted small">{inrWhole(-loss)}</span>
      <div className="meter-track">
        <div className={`meter-fill ${net >= 0 ? 'up-bg' : 'down-bg'}`}
          style={{ left: `${left}%`, width: `${Math.abs(at - zero)}%` }} />
        <div className="meter-zero" style={{ left: `${zero}%` }} />
      </div>
      <span className="muted small">{inrWhole(target)}</span>
    </div>
  )
}

function ModeSwitch({ mode }: { mode: Mode }) {
  const client = useQueryClient()
  const [asking, setAsking] = useState(false)
  const [typed, setTyped] = useState('')
  const [error, setError] = useState<unknown>(null)

  const change = async (next: Mode, word?: string) => {
    try {
      await api.setMode(next, word)
      setAsking(false)
      setTyped('')
      setError(null)
      await client.invalidateQueries()
    } catch (e) {
      setError(e)
    }
  }

  if (mode === 'live') {
    return (
      <button type="button" className="chip bad" onClick={() => void change('paper')}>
        LIVE
      </button>
    )
  }
  return (
    <>
      <button type="button" className="chip info" onClick={() => setAsking(true)}>
        PAPER
      </button>
      {asking && (
        <div className="dialog-backdrop">
          <div className="dialog" role="dialog" aria-label="Switch to live">
            <h2>Switch to live orders?</h2>
            <p>Orders will reach your real broker account. Type <b>LIVE</b> to confirm.</p>
            <input value={typed} onChange={(e) => setTyped(e.target.value)} aria-label="Type LIVE" />
            <ErrorText error={error} />
            <div className="row">
              <button type="button" className="btn" onClick={() => setAsking(false)}>Stay on paper</button>
              <button type="button" className="btn danger" disabled={typed !== 'LIVE'}
                onClick={() => void change('live', typed)}>
                Go live
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  )
}

/** On a phone the navigation is a slide-in menu, opened from here. */
function MenuButton({ onMenu }: { onMenu?: () => void }) {
  if (!onMenu) return null
  return (
    <button type="button" className="icon-btn menu-btn" aria-label="Open menu" onClick={onMenu}>
      <Icon name="menu" />
    </button>
  )
}

export function Header({ day, me, onMenu }: {
  day: Day | undefined
  me: Me | undefined
  onMenu?: () => void
}) {
  const moved = useTickDirection(day?.snapshot.net_pnl)
  if (!day) {
    return (
      <header className="topbar">
        <MenuButton onMenu={onMenu} />
        <div className="chips status"><span className="chip">Loading…</span></div>
      </header>
    )
  }
  const state = STATE_TEXT[day.status.state]
  const age = day.snapshot.feed_age
  const fresh = age !== null && age <= 3
  const left = day.plan ? Math.max(day.plan.max_trades - day.snapshot.trades_taken, 0) : null
  return (
    <header className="topbar">
      <MenuButton onMenu={onMenu} />
      <div className="chips status">
        <ModeSwitch mode={day.mode} />
        <span key={state.label} className={`chip pop ${state.tone}`} data-testid="day-state">{state.label}</span>
        {me?.is_simulated && (
          <span className="chip warn" title="Simulated prices">
            <span className="long">Simulated prices</span><span className="short">SIM</span>
          </span>
        )}
        <span className={`chip ${fresh ? 'ok' : 'warn'}`} title="Time since the last price">
          <span className={`dot${fresh ? ' pulse' : ''}`} />
          <span className="age">{age === null ? 'No feed' : `${age.toFixed(1)} s`}</span>
        </span>
      </div>
      <PnlMeter day={day} />
      <div className="chips money">
        <span className="chip">
          Net <b key={day.snapshot.net_pnl} className={`${pnlClass(day.snapshot.net_pnl)} ${moved ? `flash-${moved}` : ''}`}>
            {inr(day.snapshot.net_pnl)}
          </b>
        </span>
        {left !== null && <span className="chip">Trades left <b key={left} className="pop">{left}</b></span>}
      </div>
    </header>
  )
}
