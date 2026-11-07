import { useLayoutEffect, useRef, type CSSProperties } from 'react'

// The brand script draws into these placeholders. It only looks below the node it is given,
// so it is handed the placeholder's parent, and it skips anything it has drawn already.
// A layout effect draws before the first paint, so a loader never pops in a frame late.
function useBrandRender<T extends HTMLElement>() {
  const box = useRef<T>(null)
  useLayoutEffect(() => {
    const parent = box.current?.parentElement
    if (parent) window.CoolCandleLoader?.render(parent)
  }, [])
  return box
}

/** The CoolCandle mark. `once` plays on arrival then rests; `loop` keeps going. */
export function Logo({ play, size = 48, wordmark = true }: {
  play?: 'once' | 'loop'
  size?: number
  wordmark?: boolean
}) {
  const box = useBrandRender<HTMLSpanElement>()
  // With the wordmark the logo is about 4.4 times as wide as its size, so a phone shrinks it.
  const fitted = wordmark ? `min(${size}px, calc((100vw - 80px) / 4.4))` : `${size}px`
  return (
    <span ref={box} data-cc-logo="" data-play={play} data-wordmark={wordmark ? undefined : 'false'}
      style={{ '--cc-logo-size': fitted } as CSSProperties} />
  )
}

/** Trader DNA: green and red candles twisting like a helix, for anything that takes a moment.
 * `size` is the width; the height follows its 3:2 shape. */
export function Loader({ text, size = 260 }: { text?: string; size?: number }) {
  const box = useBrandRender<HTMLDivElement>()
  return (
    <div ref={box} data-cc-loader="" data-text={text}
      style={{ '--cc-loader-size': `min(${size}px, 70vw)` } as CSSProperties} />
  )
}
