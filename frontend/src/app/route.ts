/** Where the app is, kept in the address (#/skill/report/<id>), so a refresh or the browser's
 * back and forward buttons return to the same place. */

export const VIEWS = ['trading', 'plan', 'buddy', 'journal', 'skill', 'brokers', 'settings'] as const
export type View = (typeof VIEWS)[number]

export type SkillScreen =
  | { name: 'lobby' }
  | { name: 'setup' }
  | { name: 'arena'; id: string }
  | { name: 'report'; id: string }
  | { name: 'history' }

function parts(hash: string): string[] {
  return hash.replace(/^#\/?/, '').split('?')[0]?.split('/').filter(Boolean) ?? []
}

/** The page an address names. Anything else, a signal's confirm link included, is Trading. */
export function viewFromHash(hash: string): View {
  const first = parts(hash)[0]
  return VIEWS.find((view) => view === first) ?? 'trading'
}

export function skillScreenFromHash(hash: string): SkillScreen {
  const [, screen, id] = parts(hash)
  if (screen === 'play') return { name: 'setup' }
  if (screen === 'history') return { name: 'history' }
  if (screen === 'day' && id) return { name: 'arena', id }
  if (screen === 'report' && id) return { name: 'report', id }
  return { name: 'lobby' }
}

export function hashForSkill(screen: SkillScreen): string {
  if (screen.name === 'setup') return '#/skill/play'
  if (screen.name === 'history') return '#/skill/history'
  if (screen.name === 'arena') return `#/skill/day/${screen.id}`
  if (screen.name === 'report') return `#/skill/report/${screen.id}`
  return '#/skill'
}

/** Go somewhere: a new entry in the browser's history, unless it is already there. */
export function navigate(hash: string): void {
  if (location.hash !== hash) location.hash = hash
}
