import { useIsFetching, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Loader, Logo } from '../components/Brand'
import { Header } from '../components/Header'
import { Icon, type IconName } from '../components/Icon'
import { SignIn } from '../features/auth/SignIn'
import { BrokersPage } from '../features/brokers/BrokersPage'
import { BuddyPage } from '../features/buddy/BuddyPage'
import { JournalPage } from '../features/journal/JournalPage'
import { ConfirmPage, parseConfirmLink } from '../features/signals/ConfirmPage'
import { PlanPage } from '../features/plan/PlanPage'
import { SettingsPage } from '../features/settings/SettingsPage'
import { TradingPage } from '../features/trading/TradingPage'
import { api, ApiError } from '../lib/api'
import { useDay, useTheme } from '../lib/hooks'
import { live } from '../lib/ws'
import { usePageSwitch } from './pageSwitch'

type View = 'trading' | 'plan' | 'buddy' | 'journal' | 'brokers' | 'settings'

const PAGES: { view: View; label: string; icon: IconName }[] = [
  { view: 'trading', label: 'Trading', icon: 'trade' },
  { view: 'plan', label: "Today's plan", icon: 'plan' },
  { view: 'buddy', label: 'Buddy', icon: 'chat' },
  { view: 'journal', label: 'Journal', icon: 'book' },
  { view: 'brokers', label: 'Brokers', icon: 'link' },
  { view: 'settings', label: 'Settings', icon: 'gear' },
]

export default function App() {
  const me = useQuery({
    queryKey: ['me'],
    queryFn: api.me,
    retry: (count, error) => !(error instanceof ApiError && error.status === 401) && count < 2,
  })
  if (me.isLoading) return <div className="auth-page"><Loader text="Discipline is in your DNA…" /></div>
  if (me.error instanceof ApiError && me.error.status === 401) return <SignIn />
  if (me.isError) {
    return <div className="auth-page"><p className="form-error">The server is not reachable.</p></div>
  }
  return <Workspace />
}

function Workspace() {
  const client = useQueryClient()
  const [collapsed, setCollapsed] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false) // the slide-in menu on a phone
  const [theme, toggleTheme] = useTheme()
  const me = useQuery({ queryKey: ['me'], queryFn: api.me })
  const day = useDay()
  // Requests for data a page has never had. Background refreshes of data on screen don't count.
  const firstLoads = useIsFetching({ predicate: (query) => query.state.data === undefined })
  // With a trade open, Trading opens at once: nothing may stand between the trader and Exit.
  const { view, switching, go } = usePageSwitch<View>(
    'trading',
    (next) => next === 'trading' && !!day.data?.snapshot.open_trade,
    firstLoads > 0,
  )

  useEffect(() => {
    live.start()
    return () => live.stop()
  }, [])

  useEffect(() => {
    if (!menuOpen) return
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false)
    }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [menuOpen])

  const open = (next: View) => {
    setMenuOpen(false)
    go(next)
  }

  const logout = useMutation({
    mutationFn: api.logout,
    onSuccess: () => {
      client.clear()
      void client.invalidateQueries()
    },
  })

  const link = parseConfirmLink(location.hash)
  const state = day.data?.status.state
  const canTrade = state !== undefined && state !== 'LOCKED' && state !== 'NO_PLAN'

  return (
    <div className={`shell${collapsed ? ' collapsed' : ''}${menuOpen ? ' menu-open' : ''}`}>
      {menuOpen && <div className="nav-backdrop" onClick={() => setMenuOpen(false)} />}
      <aside className="sidebar" id="nav" aria-label="Navigation">
        <div className="side-head">
          <div className="brand"><Logo play="once" size={30} /></div>
          <button type="button" className="icon-btn collapse" aria-label="Toggle sidebar"
            onClick={() => setCollapsed((c) => !c)}>
            <Icon name="chevron" />
          </button>
        </div>
        <nav>
          {PAGES.map((page) => (
            <button key={page.view} type="button" title={page.label}
              className={`nav-item${page.view === view ? ' active' : ''}`}
              aria-current={page.view === view ? 'page' : undefined}
              onClick={() => open(page.view)}>
              <Icon name={page.icon} />
              <span className="nav-text">{page.label}</span>
            </button>
          ))}
        </nav>
        <div className="side-foot">
          <button type="button" className="nav-item" title="Switch theme" onClick={toggleTheme}>
            <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
            <span className="nav-text">{theme === 'dark' ? 'Light mode' : 'Dark mode'}</span>
          </button>
          <button type="button" className="nav-item" title="Sign out" onClick={() => logout.mutate()}>
            <Icon name="logout" />
            <span className="nav-text">Sign out</span>
          </button>
        </div>
      </aside>
      <div className="main">
        <Header day={day.data} me={me.data} onMenu={() => setMenuOpen(true)} />
        <main className="content">
          {link && <ConfirmPage link={link} canTrade={canTrade} />}
          {switching && (
            <div className="center stage page-loader">
              <Loader text={`Opening ${PAGES.find((page) => page.view === view)?.label ?? ''}…`} />
            </div>
          )}
          {/* The new page loads underneath the loader, so its data arrives during the wait. */}
          <div className={`page${switching ? ' behind' : ''}`}>
            {!day.data && (view === 'trading' || view === 'journal') && (
              <div className="center stage"><Loader text="Loading your day…" /></div>
            )}
            {day.data && view === 'trading' && (
              <TradingPage day={day.data} onOpenPlan={() => go('plan')} />
            )}
            {view === 'plan' && <PlanPage onTrade={() => go('trading')} />}
            {view === 'buddy' && <BuddyPage canTrade={canTrade} />}
            {day.data && view === 'journal' && <JournalPage today={day.data.trading_date} />}
            {view === 'brokers' && <BrokersPage />}
            {view === 'settings' && <SettingsPage theme={theme} onTheme={toggleTheme} />}
          </div>
        </main>
      </div>
    </div>
  )
}
