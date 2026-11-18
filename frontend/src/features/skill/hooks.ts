import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { prefersCalm } from '../../lib/hooks'
import { live } from '../../lib/ws'
import { skillApi, type SessionView } from './api'

/** The account and its run. Refreshed when a session locks or a run is blown. */
export function useSkillAccount() {
  const client = useQueryClient()
  useEffect(() => {
    const refresh = () => void client.invalidateQueries({ queryKey: ['skill', 'account'] })
    const offLocked = live.on('skill.locked', refresh)
    const offBlown = live.on('skill.run_blown', refresh)
    return () => {
      offLocked()
      offBlown()
    }
  }, [client])
  return useQuery({ queryKey: ['skill', 'account'], queryFn: skillApi.account })
}

/** One session, pushed live by the server, with a poll as the safety net. */
export function useSkillSession(id: string) {
  const client = useQueryClient()
  useEffect(
    () =>
      live.on<SessionView>('skill.session', (data) => {
        if (data.id === id) client.setQueryData(['skill', 'session', id], data)
      }),
    [client, id],
  )
  return useQuery({
    queryKey: ['skill', 'session', id],
    queryFn: () => skillApi.session(id),
    refetchInterval: 5000,
  })
}

/** A number that runs up or down to its new value instead of jumping. Still under reduced motion. */
export function useCountUp(value: number, ms = 350): number {
  const [shown, setShown] = useState(value)
  const from = useRef(value)
  useEffect(() => {
    if (prefersCalm()) {
      from.current = value
      setShown(value)
      return
    }
    const start = from.current
    const began = performance.now()
    let frame = 0
    const step = (now: number) => {
      const progress = Math.min((now - began) / ms, 1)
      const next = start + (value - start) * (1 - (1 - progress) ** 3)
      from.current = next
      setShown(next)
      if (progress < 1) frame = requestAnimationFrame(step)
    }
    frame = requestAnimationFrame(step)
    return () => cancelAnimationFrame(frame)
  }, [value, ms])
  return shown
}

function crossFade(change: () => void): void {
  if (prefersCalm() || !document.startViewTransition) {
    change()
    return
  }
  const transition = document.startViewTransition(change)
  // A switch that lands mid-way through another is skipped; that is not an error.
  transition.ready.catch(() => undefined)
  transition.finished.catch(() => undefined)
  transition.updateCallbackDone.catch(() => undefined)
}

let arenaOpen = false

/** Applies whatever is wanted now, so overlapping switches can't leave the wrong colours. */
function applyArena(): void {
  if (arenaOpen) document.documentElement.dataset.arena = 'skill'
  else delete document.documentElement.dataset.arena
}

/** Skill Test's colours while its page is open, cross-faded in and out over a second. */
export function useArenaTheme(): void {
  useEffect(() => {
    arenaOpen = true
    crossFade(applyArena)
    return () => {
      arenaOpen = false
      crossFade(applyArena)
    }
  }, [])
}
