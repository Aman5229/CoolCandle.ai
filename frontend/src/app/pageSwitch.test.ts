import { act, renderHook } from '@testing-library/react'
import { MAX_LOAD_MS, MAX_SWITCH_MS, usePageSwitch } from './pageSwitch'

type Page = 'trading' | 'plan' | 'journal'

function setup(instant: (next: Page) => boolean = () => false) {
  return renderHook(({ busy }) => usePageSwitch<Page>('trading', instant, busy), {
    initialProps: { busy: false },
  })
}

beforeEach(() => vi.useFakeTimers())
afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

describe('switching pages', () => {
  it('starts on the first page with no loader', () => {
    const { result } = setup()
    expect(result.current).toMatchObject({ view: 'trading', switching: false })
  })

  it('shows the loader for the random time, then the page', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.4)
    const { result } = setup()
    act(() => result.current.go('plan'))
    expect(result.current).toMatchObject({ view: 'plan', switching: true })
    act(() => vi.advanceTimersByTime(799)) // 0.4 of the 2-second range
    expect(result.current.switching).toBe(true)
    act(() => vi.advanceTimersByTime(1))
    expect(result.current.switching).toBe(false)
  })

  it('never waits two seconds or more when the data is there', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.9999)
    const { result } = setup()
    act(() => result.current.go('journal'))
    act(() => vi.advanceTimersByTime(MAX_SWITCH_MS))
    expect(result.current.switching).toBe(false)
  })

  it('stays until the data comes when the page is slow', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.1)
    const { result, rerender } = setup()
    act(() => result.current.go('journal'))
    rerender({ busy: true })
    act(() => vi.advanceTimersByTime(3000)) // well past the random 200 ms
    expect(result.current.switching).toBe(true)
    rerender({ busy: false })
    expect(result.current.switching).toBe(false)
  })

  it('fast data does not cut the random time short', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.5)
    const { result, rerender } = setup()
    act(() => result.current.go('journal'))
    rerender({ busy: true })
    act(() => vi.advanceTimersByTime(100))
    rerender({ busy: false })
    act(() => vi.advanceTimersByTime(800))
    expect(result.current.switching).toBe(true)
    act(() => vi.advanceTimersByTime(100))
    expect(result.current.switching).toBe(false)
  })

  it('gives up waiting after eight seconds', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.1)
    const { result, rerender } = setup()
    act(() => result.current.go('journal'))
    rerender({ busy: true })
    act(() => vi.advanceTimersByTime(MAX_LOAD_MS - 1))
    expect(result.current.switching).toBe(true)
    act(() => vi.advanceTimersByTime(1))
    expect(result.current.switching).toBe(false)
  })

  it('never comes back once the page is showing', () => {
    vi.spyOn(Math, 'random').mockReturnValue(0.1)
    const { result, rerender } = setup()
    act(() => result.current.go('plan'))
    act(() => vi.advanceTimersByTime(200))
    expect(result.current.switching).toBe(false)
    rerender({ busy: true }) // say, an order ticket loading its first preview
    expect(result.current.switching).toBe(false)
  })

  it('draws a new time on every switch', () => {
    const random = vi.spyOn(Math, 'random').mockReturnValue(0.5)
    const { result } = setup()
    act(() => result.current.go('plan'))
    act(() => vi.advanceTimersByTime(MAX_SWITCH_MS))
    act(() => result.current.go('journal'))
    expect(random).toHaveBeenCalledTimes(2)
  })

  it('does nothing when the page is already open', () => {
    const { result } = setup()
    act(() => result.current.go('trading'))
    expect(result.current.switching).toBe(false)
  })

  it('only the latest switch decides when the loader ends', () => {
    vi.spyOn(Math, 'random').mockReturnValueOnce(0.2).mockReturnValueOnce(0.8)
    const { result } = setup()
    act(() => result.current.go('plan')) // would end at 400 ms
    act(() => result.current.go('journal')) // ends at 1600 ms
    act(() => vi.advanceTimersByTime(500))
    expect(result.current).toMatchObject({ view: 'journal', switching: true })
    act(() => vi.advanceTimersByTime(1100))
    expect(result.current.switching).toBe(false)
  })

  it('opens a page at once when it must, even with data on its way', () => {
    const { result, rerender } = setup((next) => next === 'plan')
    act(() => result.current.go('journal'))
    rerender({ busy: true })
    act(() => result.current.go('plan'))
    expect(result.current).toMatchObject({ view: 'plan', switching: false })
  })
})
