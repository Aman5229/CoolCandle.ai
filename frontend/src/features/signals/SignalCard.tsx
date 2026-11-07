import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ErrorText } from '../../components/Field'
import { api, type Signal } from '../../lib/api'
import { istTime, price } from '../../lib/format'

/** The Confirm button is the only way a signal becomes a trade. */
export function SignalCard({ signal, canTrade }: { signal: Signal; canTrade: boolean }) {
  const client = useQueryClient()
  const refresh = () => {
    void client.invalidateQueries({ queryKey: ['signals'] })
    void client.invalidateQueries({ queryKey: ['trades'] })
  }
  const take = useMutation({ mutationFn: () => api.takeSignal(signal.id), onSuccess: refresh })
  const decline = useMutation({ mutationFn: () => api.declineSignal(signal.id), onSuccess: refresh })
  const live = signal.status === 'NEW' && Date.parse(signal.expires_at) > Date.now()
  return (
    <div className="signal flow" data-testid="signal">
      <div className="card-head">
        <h3>{signal.trading_symbol}</h3>
        <span className="chip info">{signal.setup.replaceAll('_', ' ')}</span>
      </div>
      <p className="muted small">{signal.reason}</p>
      <dl className="facts compact">
        <dt>Entry</dt><dd>{price(signal.entry)}</dd>
        <dt>Stop</dt><dd>{price(signal.stop)}</dd>
        <dt>Target</dt><dd>{price(signal.target)}</dd>
        <dt>Valid till</dt><dd>{istTime(signal.expires_at)}</dd>
      </dl>
      <ErrorText error={take.error} />
      {live ? (
        <div className="row">
          <button type="button" className="btn" onClick={() => decline.mutate()}>Skip</button>
          <button type="button" className="btn primary" disabled={!canTrade || take.isPending}
            onClick={() => take.mutate()}>
            Confirm
          </button>
        </div>
      ) : (
        <p className="muted small">{signal.status === 'NEW' ? 'Expired' : signal.status}</p>
      )}
    </div>
  )
}
