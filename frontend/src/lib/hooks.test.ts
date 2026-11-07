import { renderHook } from '@testing-library/react'
import { useTickDirection } from './hooks'

describe('which way a price moved', () => {
  it('stays quiet until the price changes', () => {
    const { result } = renderHook(({ value }) => useTickDirection(value), { initialProps: { value: '100' } })
    expect(result.current).toBeNull()
  })

  it('follows each move up and down', () => {
    const { result, rerender } = renderHook(({ value }) => useTickDirection(value), {
      initialProps: { value: '100' as string | null },
    })
    rerender({ value: '100.05' })
    expect(result.current).toBe('up')
    rerender({ value: '99.90' })
    expect(result.current).toBe('down')
  })

  it('ignores a price appearing or disappearing', () => {
    const { result, rerender } = renderHook(({ value }) => useTickDirection(value), {
      initialProps: { value: null as string | null },
    })
    rerender({ value: '100' })
    expect(result.current).toBeNull()
    rerender({ value: null })
    expect(result.current).toBeNull()
  })

  it('treats the same number written differently as no move', () => {
    const { result, rerender } = renderHook(({ value }) => useTickDirection(value), {
      initialProps: { value: '100' },
    })
    rerender({ value: '100.00' })
    expect(result.current).toBeNull()
  })
})
