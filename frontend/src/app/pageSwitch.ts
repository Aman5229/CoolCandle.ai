import { useEffect, useRef, useState } from 'react'

export const MAX_SWITCH_MS = 2000
export const MAX_LOAD_MS = 8000

// waiting: the random time is running. loading: it ran out, but the page's data has not arrived.
type Phase = 'shown' | 'waiting' | 'loading'

/**
 * The page on screen. Each switch shows the loader for a random 0 to 2 seconds, and after that
 * for as long as the new page's data is still on its way (`busy`). After 8 seconds the page shows
 * anyway, with its own loaders, so a hung request never hides it. `instant(next)` opens a page at
 * once. Once a page shows, later loading never brings the loader back.
 */
export function usePageSwitch<T extends string>(
  first: T,
  instant: (next: T) => boolean,
  busy: boolean,
) {
  const [view, setView] = useState(first)
  const [phase, setPhase] = useState<Phase>('shown')
  const wait = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const cap = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  const stopTimers = () => {
    clearTimeout(wait.current)
    clearTimeout(cap.current)
  }
  useEffect(() => stopTimers, [])

  useEffect(() => {
    if (phase === 'loading' && !busy) setPhase('shown')
  }, [phase, busy])

  const go = (next: T) => {
    if (next === view) return
    stopTimers()
    setView(next)
    if (instant(next)) {
      setPhase('shown')
      return
    }
    setPhase('waiting')
    wait.current = setTimeout(
      () => setPhase((now) => (now === 'waiting' ? 'loading' : now)),
      Math.random() * MAX_SWITCH_MS,
    )
    cap.current = setTimeout(() => setPhase('shown'), MAX_LOAD_MS)
  }

  return { view, switching: phase !== 'shown', go }
}
