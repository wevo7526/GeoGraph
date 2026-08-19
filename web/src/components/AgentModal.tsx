/** Corner control: the same desk, from any working page.
 *
 *  Paper language — a ruled panel, not a chat bubble. Hidden on the landing
 *  only (`App` returns the front page before this mounts; the path check is
 *  the same gate). Intel is a briefing; questions come here. */
import { useEffect } from 'react'
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
  const onLanding = path === '/' || path === ''
  const { asking, messages, deskOpen, setDeskOpen } = useAgent()

  useEffect(() => {
    if (!deskOpen) return
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setDeskOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [deskOpen, setDeskOpen])

  if (onLanding) return null

  return (
    <>
      {!deskOpen && (
        <button
          type="button"
          className="desk-fab"
          onClick={() => setDeskOpen(true)}
          aria-haspopup="dialog"
          aria-label="Open the desk"
        >
          Desk{asking ? '…' : messages.some((t) => t.role === 'assistant') ? ' ·' : ''}
        </button>
      )}
      {deskOpen && (
        <>
          <button
            type="button"
            className="desk-scrim"
            aria-label="Close the desk"
            onClick={() => setDeskOpen(false)}
          />
          <div className="desk-modal" role="dialog" aria-label="the desk">
            <header className="desk-modal-head">
              <span className="kicker">The desk</span>
              <button
                type="button"
                className="article-link"
                onClick={() => setDeskOpen(false)}
              >
                close
              </button>
            </header>
            <AgentDesk
              region={region}
              onNavigate={(next) => {
                setDeskOpen(false)
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
