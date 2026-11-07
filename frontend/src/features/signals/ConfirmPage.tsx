import { useQuery } from '@tanstack/react-query'
import { ErrorText } from '../../components/Field'
import { api } from '../../lib/api'
import { SignalCard } from './SignalCard'

export interface ConfirmLink {
  id: string
  expires: string
  signature: string
}

/** The hash an alert links to: #/confirm/<id>?e=<expiry>&s=<signature>. */
export function parseConfirmLink(hash: string): ConfirmLink | null {
  const match = /^#\/confirm\/([^?]+)\?e=(\d+)&s=([0-9a-f]+)$/.exec(hash)
  if (!match) return null
  const [, id, expires, signature] = match
  return id && expires && signature ? { id, expires, signature } : null
}

export function ConfirmPage({ link, canTrade }: { link: ConfirmLink; canTrade: boolean }) {
  const signal = useQuery({
    queryKey: ['signal', link.id],
    queryFn: () => api.signal(link.id, link.expires, link.signature),
    retry: false,
  })
  return (
    <section className="card">
      <h2>Confirm the alert</h2>
      <ErrorText error={signal.error} />
      {signal.data && <SignalCard signal={signal.data} canTrade={canTrade} />}
    </section>
  )
}
