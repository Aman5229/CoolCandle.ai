import {
  CandlestickSeries,
  ColorType,
  createChart,
  createSeriesMarkers,
  CrosshairMode,
  LineStyle,
  type AutoscaleInfo,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesMarkersPluginApi,
  type SeriesMarker,
  type Time,
} from 'lightweight-charts'
import { useQuery } from '@tanstack/react-query'
import { useEffect, useRef } from 'react'
import { Loader } from '../../components/Brand'
import { api, type Candle } from '../../lib/api'
import { prefersCalm } from '../../lib/hooks'
import { live, type Tick } from '../../lib/ws'
import { bucketOf, glide, nextBar, toBar, type Bar, type Interval } from './bars'

const GLIDE_MS = 250
const GRAB_PX = 7 // how close the pointer must be to a line to pick it up
const VISIBLE_BARS = 100 // a chart opens on this many of the latest bars

/** Where the chart's candles and ticks come from. The live market by default; Skill Test passes
 * its own session's candles and its `skill.tick` topic. */
export interface ChartSource {
  queryKey: unknown[]
  load: () => Promise<Candle[]>
  topic: string
  accepts: (item: { instrument_key: string; session_id?: string | null }) => boolean
  /** Closed bars, to settle each one on its true high and low: ticks are thinned on the way. */
  candleTopic?: string
}

export interface ChartLine {
  id: string
  price: number
  title: string
  tone: 'entry' | 'stop' | 'target'
  draggable?: boolean
}

const TONES = { entry: '--accent', stop: '--down', target: '--up' } as const

function marketSource(key: string, interval: Interval): ChartSource {
  return {
    queryKey: ['candles', key, interval],
    load: () => api.candles(key, interval),
    topic: 'tick',
    accepts: (tick) => tick.instrument_key === key,
  }
}

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

export function PriceChart({
  instrumentKey,
  interval,
  source,
  lines = [],
  markers = [],
  onDrag,
}: {
  instrumentKey: string
  interval: Interval
  source?: ChartSource
  lines?: ChartLine[]
  markers?: SeriesMarker<Time>[]
  onDrag?: (id: string, price: number) => void
}) {
  const box = useRef<HTMLDivElement>(null)
  const chart = useRef<IChartApi | null>(null)
  const series = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const marks = useRef<ISeriesMarkersPluginApi<Time> | null>(null)
  const drawn = useRef(new Map<string, IPriceLine>())
  const last = useRef<Bar | null>(null) // the true last bar
  const shown = useRef<Bar | null>(null) // the last bar as drawn, part way through a glide
  const frame = useRef(0)
  const feed = source ?? marketSource(instrumentKey, interval)
  const history = useQuery({
    queryKey: feed.queryKey,
    queryFn: feed.load,
    gcTime: 0, // fetched fresh on every visit: ticks are not drawn while the chart is away
  })
  const accepts = useRef(feed.accepts)
  accepts.current = feed.accepts
  const dragged = useRef(onDrag)
  dragged.current = onDrag
  const current = useRef(lines)
  current.current = lines
  const held = useRef<{ id: string; price: number } | null>(null) // a line being dragged

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
    const candles = instance.addSeries(CandlestickSeries, {
      ...colours.series,
      borderVisible: false,
      // Keep the price lines (stop, target) in view, not only the candles.
      autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
        const info = original()
        const prices = current.current.map((line) => line.price)
        if (!info?.priceRange || prices.length === 0) return info
        return {
          ...info,
          priceRange: {
            minValue: Math.min(info.priceRange.minValue, ...prices),
            maxValue: Math.max(info.priceRange.maxValue, ...prices),
          },
        }
      },
    })
    const recolour = new MutationObserver(() => {
      const next = themed()
      instance.applyOptions(next.chart)
      candles.applyOptions(next.series)
    })
    recolour.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    chart.current = instance
    series.current = candles
    marks.current = createSeriesMarkers(candles, [])
    const lineMap = drawn.current
    return () => {
      recolour.disconnect()
      instance.remove()
      chart.current = null
      series.current = null
      marks.current = null
      lineMap.clear()
    }
  }, [])

  // Price lines: entry, stop and target. Drawn once, then moved, so a drag never flickers.
  useEffect(() => {
    const candles = series.current
    if (!candles) return
    const wanted = new Set(lines.map((line) => line.id))
    for (const [id, line] of drawn.current) {
      if (!wanted.has(id)) {
        candles.removePriceLine(line)
        drawn.current.delete(id)
      }
    }
    for (const line of lines) {
      const options = {
        price: line.price,
        title: line.title,
        color: cssVar(TONES[line.tone]),
        lineWidth: 2 as const,
        lineStyle: line.tone === 'entry' ? LineStyle.Solid : LineStyle.Dashed,
        axisLabelVisible: true,
      }
      const existing = drawn.current.get(line.id)
      if (existing && held.current?.id === line.id) continue // the pointer owns it until release
      if (existing) existing.applyOptions(options)
      else drawn.current.set(line.id, candles.createPriceLine(options))
    }
  }, [lines])

  useEffect(() => {
    marks.current?.setMarkers(markers)
  }, [markers])

  useEffect(() => {
    if (!feed.candleTopic) return
    return live.on<Candle & { instrument_key: string; session_id?: string | null }>(
      feed.candleTopic,
      (candle) => {
        if (!accepts.current(candle) || !series.current) return
        const bar = toBar(candle)
        const newest = last.current
        if (newest && bar.time > newest.time) return // not drawn yet: its ticks will be
        if (newest && bar.time === newest.time) cancelAnimationFrame(frame.current) // no stale glide
        series.current.update(bar, newest !== null && bar.time < newest.time)
        if (newest && bar.time === newest.time) {
          last.current = bar
          shown.current = bar
        }
      },
    )
  }, [feed.candleTopic])

  // Dragging a line: picked up within a few pixels, the chart holds still meanwhile, and the
  // new price is reported once, on release.
  useEffect(() => {
    const element = box.current
    if (!element || !onDrag) return
    const near = (y: number) =>
      current.current.find((line) => {
        if (!line.draggable) return false
        const at = series.current?.priceToCoordinate(line.price)
        return at !== null && at !== undefined && Math.abs(at - y) <= GRAB_PX
      })
    const offset = (event: PointerEvent) => event.clientY - element.getBoundingClientRect().top
    const down = (event: PointerEvent) => {
      const line = near(offset(event))
      if (!line) return
      held.current = { id: line.id, price: line.price }
      chart.current?.applyOptions({ handleScroll: false, handleScale: false })
      element.setPointerCapture?.(event.pointerId)
      event.preventDefault()
      event.stopPropagation()
    }
    const move = (event: PointerEvent) => {
      const grabbed = held.current
      if (!grabbed) {
        element.style.cursor = near(offset(event)) ? 'ns-resize' : ''
        return
      }
      const price = series.current?.coordinateToPrice(offset(event))
      if (price === null || price === undefined) return
      grabbed.price = price
      drawn.current.get(grabbed.id)?.applyOptions({ price })
      event.stopPropagation()
    }
    const up = () => {
      if (!held.current) return
      const { id, price } = held.current
      held.current = null
      chart.current?.applyOptions({ handleScroll: true, handleScale: true })
      dragged.current?.(id, price)
    }
    element.addEventListener('pointerdown', down, true)
    element.addEventListener('pointermove', move, true)
    element.addEventListener('pointerup', up, true)
    element.addEventListener('pointercancel', up, true)
    return () => {
      element.removeEventListener('pointerdown', down, true)
      element.removeEventListener('pointermove', move, true)
      element.removeEventListener('pointerup', up, true)
      element.removeEventListener('pointercancel', up, true)
    }
  }, [onDrag])

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
    const stop = live.on<Tick>(feed.topic, (tick) => {
      if (!accepts.current(tick) || !series.current) return
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
    // The topic and query key identify the source; `accepts` is read through a ref.
  }, [instrumentKey, interval, feed.topic, JSON.stringify(feed.queryKey)])

  useEffect(() => {
    if (!history.data || !series.current) return
    const bars = history.data.map(toBar)
    series.current.setData(bars)
    last.current = bars.at(-1) ?? null
    shown.current = last.current
    // Open on the latest bars at a readable width, not all of history squeezed into view.
    const scale = chart.current?.timeScale()
    if (bars.length > VISIBLE_BARS) {
      scale?.setVisibleLogicalRange({ from: bars.length - VISIBLE_BARS, to: bars.length + 3 })
    } else {
      scale?.fitContent()
    }
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
