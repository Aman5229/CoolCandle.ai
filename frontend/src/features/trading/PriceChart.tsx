import {
  CandlestickSeries,
  ColorType,
  createChart,
  CrosshairMode,
  type IChartApi,
  type ISeriesApi,
} from 'lightweight-charts'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { Loader } from '../../components/Brand'
import { api } from '../../lib/api'
import { prefersCalm } from '../../lib/hooks'
import { live, type Tick } from '../../lib/ws'
import { bucketOf, glide, nextBar, toBar, type Bar, type Interval } from './bars'

const GLIDE_MS = 250

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim()
}

function themed() {
  const border = cssVar('--border')
  return {
    chart: {
      layout: { textColor: cssVar('--muted') },
      grid: { vertLines: { color: border }, horzLines: { color: border } },
      rightPriceScale: { borderColor: border },
      timeScale: { borderColor: border },
    },
    series: {
      upColor: cssVar('--up'),
      downColor: cssVar('--down'),
      wickUpColor: cssVar('--up'),
      wickDownColor: cssVar('--down'),
    },
  }
}

export function PriceChart({ instrumentKey, interval }: { instrumentKey: string; interval: Interval }) {
  const box = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)
  const series = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const last = useRef<Bar | null>(null) // the true last bar
  const shown = useRef<Bar | null>(null) // the last bar as drawn, part way through a glide
  const frame = useRef(0)
  const history = useQuery({
    queryKey: ['candles', instrumentKey, interval],
    queryFn: () => api.candles(instrumentKey, interval),
    gcTime: 0, // fetched fresh on every visit: ticks are not drawn while the chart is away
  })

  useEffect(() => {
    if (!box.current) return
    const colours = themed()
    const instance = createChart(box.current, {
      autoSize: true,
      layout: { background: { type: ColorType.Solid, color: 'transparent' }, fontFamily: 'inherit' },
      crosshair: { mode: CrosshairMode.Normal },
      kineticScroll: { mouse: true, touch: true },
      timeScale: { timeVisible: true, secondsVisible: false, rightOffset: 4 },
    })
    instance.applyOptions(colours.chart)
    const candles = instance.addSeries(CandlestickSeries, { ...colours.series, borderVisible: false })
    const recolour = new MutationObserver(() => {
      const next = themed()
      instance.applyOptions(next.chart)
      candles.applyOptions(next.series)
    })
    recolour.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    chart.current = instance
    series.current = candles
    return () => {
      recolour.disconnect()
      instance.remove()
      chart.current = null
      series.current = null
    }
  }, [])

  // A new instrument or interval starts empty; its history is drawn when it arrives.
  useEffect(() => {
    series.current?.setData([])
    last.current = null
    shown.current = null

    const draw = (bar: Bar) => {
      series.current?.update(bar)
      shown.current = bar
    }

    live.watch([instrumentKey])
    const stop = live.on<Tick>('tick', (tick) => {
      if (tick.instrument_key !== instrumentKey || !series.current) return
      const previous = last.current
      const next = nextBar(previous, Number(tick.ltp), bucketOf(tick.ts, interval))
      if (!next) return
      cancelAnimationFrame(frame.current)
      // A new bar: settle the old one on its true close before starting the next.
      if (previous && previous.time !== next.time) draw(previous)
      last.current = next
      const from = shown.current
      if (!from || from.time !== next.time || prefersCalm()) {
        draw(next)
        return
      }
      const started = performance.now()
      const step = (now: number) => {
        const progress = Math.min((now - started) / GLIDE_MS, 1)
        draw(glide(from, next, progress))
        if (progress < 1) frame.current = requestAnimationFrame(step)
      }
      frame.current = requestAnimationFrame(step)
    })
    return () => {
      cancelAnimationFrame(frame.current)
      stop()
    }
  }, [instrumentKey, interval])

  useEffect(() => {
    if (!history.data || !series.current) return
    const bars = history.data.map(toBar)
    series.current.setData(bars)
    last.current = bars.at(-1) ?? null
    shown.current = last.current
    chart.current?.timeScale().fitContent()
  }, [history.data])

  return (
    <>
      {history.error && <p className="form-error">Could not load the chart: {String(history.error)}</p>}
      <div className="chart-wrap">
        <div className={`chart-box${history.isLoading ? ' loading' : ''}`} ref={box} />
        {history.isLoading && (
          <div className="chart-loader"><Loader size={220} text="Loading candles…" /></div>
        )}
      </div>
    </>
  )
}
