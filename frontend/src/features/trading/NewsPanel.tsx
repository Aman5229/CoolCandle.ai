import { useQuery } from '@tanstack/react-query'
import { api } from '../../lib/api'
import { shortTime } from '../../lib/format'

/** Scheduled events gate entries; headlines only inform, so read the source before acting. */
export function NewsPanel() {
  const events = useQuery({ queryKey: ['events'], queryFn: api.events, staleTime: 60_000 })
  const news = useQuery({ queryKey: ['news'], queryFn: api.news, refetchInterval: 120_000 })
  return (
    <div className="flow">
      <h3>News risk</h3>
      <ul className="plain">
        {events.data?.slice(0, 4).map((event) => (
          <li key={`${event.day}${event.name}`}>
            <b>{event.day}</b> {event.at ? shortTime(event.at) : ''} {event.name}
          </li>
        ))}
        {events.data?.length === 0 && <li className="muted">No events scheduled.</li>}
      </ul>
      {news.data && news.data.length > 0 && (
        <ul className="plain small">
          {news.data.slice(0, 5).map((item) => (
            <li key={item.headline.link || item.headline.title}>
              <a href={item.headline.link} target="_blank" rel="noreferrer">{item.headline.title}</a>
              {item.tags.map((tag) => <span key={tag} className="tag">{tag}</span>)}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
