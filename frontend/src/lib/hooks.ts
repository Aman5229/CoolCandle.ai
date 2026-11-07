import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { api, type Day, type DayStatus, type Snapshot, type Signal, type Trade } from './api'
import { live, type Tick } from './ws'

interface DayPush {
  status: DayStatus
  snapshot: Snapshot
  locked_at: string | null
  lock_ends_at: string | null
}

/** The day's state comes from the server, pushed live, with a poll as the safety net. */
export function useDay() {
  const client = useQueryClient()
  useEffect(
    () =>
      live.on<DayPush>('day_status', (data) => {
        client.setQueryData<Day>(['day'], (day) => (day ? { ...day, ...data } : day))
      }),
    [client],
  )
  return useQuery({ queryKey: ['day'], queryFn: api.day, refetchInterval: 5000 })
}

export function useTrades() {
  const client = useQueryClient()
  useEffect(
    () =>
      live.on<Trade>('trade_update', () => {
        void client.invalidateQueries({ queryKey: ['trades'] })
        void client.invalidateQueries({ queryKey: ['day'] })
      }),
    [client],
  )
  return useQuery({ queryKey: ['trades'], queryFn: () => api.trades(), refetchInterval: 5000 })
}

export function useSignals() {
  const client = useQueryClient()
  useEffect(
    () =>
      live.on<Signal>('signal', () => {
        void client.invalidateQueries({ queryKey: ['signals'] })
      }),
    [client],
  )
  return useQuery({ queryKey: ['signals'], queryFn: api.signals, refetchInterval: 15000 })
}

/** The latest tick for one instrument. */
export function useTick(key: string | undefined): Tick | null {
  const [tick, setTick] = useState<Tick | null>(null)
  useEffect(() => {
    setTick(null)
    if (!key) return
    live.watch([key])
    return live.on<Tick>('tick', (data) => {
      if (data.instrument_key === key) setTick(data)
    })
  }, [key])
  return tick
}

/** Which way a price last moved, so it can flash. Null until it first changes. */
export function useTickDirection(value: string | null | undefined): 'up' | 'down' | null {
  const [previous, setPrevious] = useState(value)
  const [direction, setDirection] = useState<'up' | 'down' | null>(null)
  if (value !== previous) {
    setPrevious(value)
    const before = Number(previous)
    const now = Number(value)
    if (previous != null && value != null && now !== before) setDirection(now > before ? 'up' : 'down')
  }
  return direction
}

/** True when the user has asked their device for less motion. */
export function prefersCalm(): boolean {
  return matchMedia('(prefers-reduced-motion: reduce)').matches
}

export function useNow(ms: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(timer)
  }, [ms])
  return now
}

export type Theme = 'dark' | 'light'

function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme
  localStorage.setItem('theme', theme)
}

/** Dark and light, cross-faded over a second, unless the user prefers less motion. */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(
    () => (localStorage.getItem('theme') as Theme | null) ?? 'dark',
  )
  useEffect(() => applyTheme(theme), [theme])
  const toggle = () => {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    if (!prefersCalm() && document.startViewTransition) {
      document.startViewTransition(() => {
        applyTheme(next)
        setTheme(next)
      })
    } else {
      setTheme(next)
    }
  }
  return [theme, toggle]
}
