import { render } from '@testing-library/react'
import { Loader, Logo } from './Brand'

afterEach(() => {
  delete window.CoolCandleLoader
})

describe('the brand placeholders', () => {
  it('asks the brand script to draw the logo it was given', () => {
    const drawn = vi.fn()
    window.CoolCandleLoader = { render: drawn, track: (p) => p }
    const { container } = render(<Logo play="once" size={30} />)
    const logo = container.querySelector('[data-cc-logo]')
    expect(logo).toHaveAttribute('data-play', 'once')
    expect(logo).not.toHaveAttribute('data-wordmark')
    expect((logo as HTMLElement).style.getPropertyValue('--cc-logo-size')).toBe(
      'min(30px, calc((100vw - 80px) / 4.4))',
    )
    expect(drawn).toHaveBeenCalledWith(container)
  })

  it('can leave out the wordmark, and then keeps its size', () => {
    const { container } = render(<Logo wordmark={false} size={72} />)
    const logo = container.querySelector('[data-cc-logo]') as HTMLElement
    expect(logo).toHaveAttribute('data-wordmark', 'false')
    expect(logo.style.getPropertyValue('--cc-logo-size')).toBe('72px')
  })

  it('passes the loader its text and size', () => {
    const { container } = render(<Loader text="Loading candles…" size={56} />)
    const loader = container.querySelector('[data-cc-loader]')
    expect(loader).toHaveAttribute('data-text', 'Loading candles…')
    expect((loader as HTMLElement).style.getPropertyValue('--cc-loader-size')).toBe('min(56px, 70vw)')
  })

  it('still renders when the brand script is missing', () => {
    const { container } = render(<Loader />)
    expect(container.querySelector('[data-cc-loader]')).toBeInTheDocument()
  })
})
