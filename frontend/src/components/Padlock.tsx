/** A padlock. With `closing`, the shackle swings down and snaps shut once. */
export function Padlock({ size = 28, closing = false }: { size?: number; closing?: boolean }) {
  return (
    <svg className={`padlock${closing ? ' closing' : ''}`} width={size} height={size} viewBox="0 0 24 24"
      fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden>
      <path className="shackle" d="M8 11V7a4 4 0 018 0v4" />
      <rect className="body" x="6" y="11" width="12" height="10" rx="2" />
    </svg>
  )
}
