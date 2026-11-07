import { useMutation } from '@tanstack/react-query'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Loader } from '../../components/Brand'
import { ErrorText } from '../../components/Field'
import { api, type Signal } from '../../lib/api'
import { SignalCard } from '../signals/SignalCard'

interface Line {
  who: 'you' | 'buddy'
  text: string
  proposal?: Signal | null
}

/** Text chat with the buddy. It explains and proposes; only a tap on Confirm enters a trade. */
export function BuddyPage({ canTrade }: { canTrade: boolean }) {
  const [lines, setLines] = useState<Line[]>([])
  const [text, setText] = useState('')
  const end = useRef<HTMLDivElement>(null)

  const ask = useMutation({
    mutationFn: (message: string) => api.chat(message),
    onSuccess: (reply) => {
      setLines((all) => [...all, { who: 'buddy', text: reply.text, proposal: reply.proposal }])
    },
  })

  useEffect(() => {
    end.current?.scrollIntoView?.({ behavior: 'smooth' })
  }, [lines, ask.isPending])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    const message = text.trim()
    if (!message || ask.isPending) return
    setLines((all) => [...all, { who: 'you', text: message }])
    setText('')
    ask.mutate(message)
  }

  return (
    <section className="card buddy">
      <div className="card-head">
        <h2>Trading buddy</h2>
        <span className="muted small">English or हिन्दी</span>
      </div>
      <div className="chat">
        {lines.length === 0 && (
          <p className="muted">Ask about today's signal, your limits or what's scheduled.</p>
        )}
        {lines.map((line, index) => (
          <div key={index} className={`bubble ${line.who}`}>
            <p>{line.text}</p>
            {line.proposal && <SignalCard signal={line.proposal} canTrade={canTrade} />}
          </div>
        ))}
        {ask.isPending && <div className="bubble buddy"><Loader size={96} /></div>}
        <div ref={end} />
      </div>
      <ErrorText error={ask.error} />
      <form className="row" onSubmit={submit}>
        <input aria-label="Message" placeholder="Ask your buddy" value={text}
          onChange={(e) => setText(e.target.value)} />
        <button type="submit" className="btn primary" disabled={ask.isPending || !text.trim()}>
          Send
        </button>
      </form>
    </section>
  )
}
