import { useEffect, useState } from 'react'
import { Loader } from '../../components/Brand'
import { ErrorText } from '../../components/Field'
import { Icon } from '../../components/Icon'
import { hashForSkill, navigate, skillScreenFromHash, type SkillScreen } from '../../app/route'
import { ArenaView } from './Arena'
import { HistoryView } from './History'
import { useArenaTheme, useSkillAccount } from './hooks'
import { Lobby } from './Lobby'
import { ReportView } from './Report'
import { Setup } from './Setup'
import './skill.css'

/** Replaces the live header while Skill Test is open: no live funds, P&L or mode up here. Only a
 * phone shows it, for the menu button. */
export function SkillTopbar({ onMenu }: { onMenu: () => void }) {
  return (
    <header className="skill-topbar">
      <button type="button" className="icon-btn menu-btn" aria-label="Open menu" onClick={onMenu}>
        <Icon name="menu" />
      </button>
      <h2>Skill Test</h2>
    </header>
  )
}

export function SkillPage() {
  useArenaTheme()
  const account = useSkillAccount()
  // The screen lives in the address (#/skill/day/<id>), so a refresh stays on it.
  const [screen, setScreen] = useState<SkillScreen>(() => skillScreenFromHash(location.hash))
  useEffect(() => {
    const follow = () => setScreen(skillScreenFromHash(location.hash))
    window.addEventListener('hashchange', follow)
    return () => window.removeEventListener('hashchange', follow)
  }, [])
  const show = (next: SkillScreen) => {
    setScreen(next)
    navigate(hashForSkill(next))
  }
  const lobby = () => {
    void account.refetch()
    show({ name: 'lobby' })
  }

  let body
  if (account.error) body = <ErrorText error={account.error} />
  else if (!account.data) body = <div className="center stage"><Loader text="Opening Skill Test…" /></div>
  else if (screen.name === 'setup') {
    body = <Setup account={account.data} onCancel={lobby}
      onStarted={(id) => show({ name: 'arena', id })} />
  } else if (screen.name === 'arena') {
    body = <ArenaView id={screen.id} onLobby={lobby}
      onReport={() => show({ name: 'report', id: screen.id })} />
  } else if (screen.name === 'report') {
    body = <ReportView id={screen.id} onLobby={lobby} />
  } else if (screen.name === 'history') {
    body = <HistoryView onLobby={lobby} onReport={(id) => show({ name: 'report', id })} />
  } else {
    body = (
      <Lobby account={account.data}
        onStart={() => show({ name: 'setup' })}
        onResume={(id) => show({ name: 'arena', id })}
        onReport={(id) => show({ name: 'report', id })}
        onHistory={() => show({ name: 'history' })} />
    )
  }
  return <div className="skill-page">{body}</div>
}
