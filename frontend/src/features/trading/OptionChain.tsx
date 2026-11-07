import { useQuery } from '@tanstack/react-query'
import { useEffect } from 'react'
import { Loader } from '../../components/Brand'
import { api, type ChainRow, type ChainSide } from '../../lib/api'
import { price } from '../../lib/format'
import { useTickDirection } from '../../lib/hooks'
import { live } from '../../lib/ws'

function Side({ side, selected, onPick }: {
  side: ChainSide | null
  selected: string | undefined
  onPick: (side: ChainSide) => void
}) {
  const direction = useTickDirection(side?.ltp)
  if (!side) return <td className="muted">–</td>
  return (
    <td>
      <button type="button" className={`cell${selected === side.key ? ' active' : ''}`}
        title={`Open a ticket for ${side.trading_symbol}`} onClick={() => onPick(side)}>
        {/* A new key on each price restarts the flash. */}
        <span key={side.ltp} className={direction ? `flash-${direction}` : undefined}>{price(side.ltp)}</span>
      </button>
    </td>
  )
}

export function OptionChain({ underlying, selected, onPick }: {
  underlying: string
  selected: string | undefined
  onPick: (side: ChainSide) => void
}) {
  const chain = useQuery({
    queryKey: ['chain', underlying],
    queryFn: () => api.chain(underlying),
    refetchInterval: 1000,
  })
  const rows: ChainRow[] = chain.data?.rows ?? []

  useEffect(() => {
    live.watch(rows.flatMap((row) => [row.ce?.key, row.pe?.key].filter((k): k is string => !!k)))
  }, [rows])

  return (
    <div className="chain flow">
      <div className="card-head">
        <h3>Option chain</h3>
        <span className="muted small">
          Spot {price(chain.data?.spot)} · expiry {chain.data?.expiry ?? '–'} · lot {chain.data?.lot_size ?? '–'}
        </span>
      </div>
      {chain.isLoading && <div className="center"><Loader size={200} text="Loading the chain…" /></div>}
      <table>
        <thead>
          <tr><th>CE</th><th>Strike</th><th>PE</th></tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.strike} className={row.strike === chain.data?.atm ? 'atm' : undefined}>
              <Side side={row.ce} selected={selected} onPick={onPick} />
              <td className="strike">{Number(row.strike)}</td>
              <Side side={row.pe} selected={selected} onPick={onPick} />
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
