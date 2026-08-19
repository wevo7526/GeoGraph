/** Corner control: the same desk, from any working page but Intel.
 *
 *  Intel is the office; this is the summons. Paper language — a ruled
 *  panel, not a chat bubble. Hidden on the landing and on Intel itself. */
import { useEffect, useState } from 'react'
import AgentDesk from './AgentDesk'
import { useAgent } from './AgentSession'

export default function AgentModal({
  region,
  route,
  onNavigate,
}: {
  region: string
  route: string
  onNavigate: (next: string) => void
}) {
  const path = route.split('?')[0]
  const onIntel =
    path.startsWith('/intel') || path.startsWith('/situation') || path.startsWith('/wire')
  const [open, setOpen] = useState(false)
  const { asking, messages } = useAgent()

  useEffect(() => {
    if (!open) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open])

  if (onIntel) return null

  return (
    <>
      {!open && (
        <button
          type="button"
          className="desk-fab"
          onClick={() => setOpen(true)}
          aria-haspopup="dialog"
          aria-label="Open the desk"
        >
          Desk{asking ? '…' : messages.some((t) => t.role === 'assistant') ? ' ·' : ''}
        </button>
      )}
      {open && (
        <>
          <button
            type="button"
            className="desk-scrim"
            aria-label="Close the desk"
            onClick={() => setOpen(false)}
          />
          <div className="desk-modal" role="dialog" aria-label="the desk">
            <header className="desk-modal-head">
              <span className="kicker">The desk</span>
              <button
                type="button"
                className="article-link"
                onClick={() => setOpen(false)}
              >
                close
              </button>
            </header>
            <AgentDesk
              region={region}
              onNavigate={(next) => {
                setOpen(false)
                onNavigate(next)
              }}
              variant="panel"
              autofocus
            />
          </div>
        </>
      )}
    </>
  )
}
