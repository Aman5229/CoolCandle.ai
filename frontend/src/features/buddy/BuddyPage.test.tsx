import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { api } from '../../lib/api'
import { BuddyPage } from './BuddyPage'

function page() {
  return render(
    <QueryClientProvider client={new QueryClient()}><BuddyPage canTrade /></QueryClientProvider>,
  )
}

afterEach(() => vi.restoreAllMocks())

describe('the buddy', () => {
  it('is a text chat for now', () => {
    page()
    expect(screen.getByLabelText('Message')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /speak|listen/i })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/read aloud/i)).not.toBeInTheDocument()
  })

  it('sends nothing blank', () => {
    page()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })

  it('shows the question and the answer', async () => {
    const reply = { text: 'Your loss limit is 3000.', proposal: null }
    const chat = vi.spyOn(api, 'chat').mockResolvedValue(reply)
    page()
    fireEvent.change(screen.getByLabelText('Message'), { target: { value: '  My limit?  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(screen.getByText('My limit?')).toBeInTheDocument()
    expect(await screen.findByText('Your loss limit is 3000.')).toBeInTheDocument()
    expect(chat).toHaveBeenCalledWith('My limit?')
  })
})
