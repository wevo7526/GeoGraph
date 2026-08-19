/** The desk itself — argument over numbers already on the archive.
 *
 *  Used full-page on Intel and as the body of the corner panel. Citations
 *  in square brackets become routes when they name a pair, event or market.
 *  The prose is the agent's; the figures it cites live on the board beside
 *  it, never in a token the surface-language test would ban. */
import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { citeRoute, DEFAULT_QUESTION } from '../lib/desk'
import { Empty } from '../ui'
import { useAgent } from './AgentSession'

const CITE = /\[([^\]]{3,80})\]/g

function Cited({
  text,
  region,
  onNavigate,
}: {
  text: string
  region: string
  onNavigate?: (route: string) => void
}) {
  const parts: ReactNode[] = []
  let cursor = 0
  CITE.lastIndex = 0
  let match: RegExpExecArray | null
  let key = 0
  while ((match = CITE.exec(text)) !== null) {
    if (match.index > cursor) {
      parts.push(text.slice(cursor, match.index))
    }
    const id = match[1]
    const route = onNavigate ? citeRoute(id, region) : null
    if (route && onNavigate) {
      parts.push(
        <button
          key={`c-${key++}`}
          type="button"
          className="article-link"
          onClick={() => onNavigate(route)}
        >
          {id}
        </button>,
      )
    } else {
      parts.push(match[0])
    }
    cursor = match.index + match[0].length
  }
  if (cursor < text.length) parts.push(text.slice(cursor))
  return <p className="desk-assessment">{parts}</p>
}

export default function AgentDesk({
  region,
  onNavigate,
  variant = 'page',
  autofocus = false,
}: {
  region: string
  onNavigate?: (route: string) => void
  variant?: 'page' | 'panel'
  autofocus?: boolean
}) {
  const { messages, asking, error, darkReason, method, ask } = useAgent()
  const [draft, setDraft] = useState('')
  const end = useRef<HTMLDivElement>(null)
  const field = useRef<HTMLTextAreaElement>(null)

  useEffect(() => {
    end.current?.scrollIntoView({ block: 'end' })
  }, [messages, asking])

  useEffect(() => {
    if (autofocus) field.current?.focus()
  }, [autofocus])

  const send = (event: FormEvent) => {
    event.preventDefault()
    const asked = draft.trim()
    if (!asked || asking || darkReason) return
    setDraft('')
    ask(asked)
  }

  return (
    <div className={variant === 'panel' ? 'desk desk--panel' : 'desk'}>
      {darkReason ? (
        <Empty>{darkReason}</Empty>
      ) : (
        <>
          <div className="desk-thread" aria-live="polite">
            {messages.length === 0 && !asking && (
              <p className="figure-note">
                The desk reads the same briefing as this page — wire, markets,
                games, the globe. It argues; it does not measure.
              </p>
            )}
            {messages.map((turn, index) =>
              turn.role === 'user' ? (
                <p key={`u-${index}`} className="desk-ask-line">
                  {turn.content}
                </p>
              ) : (
                <Cited
                  key={`a-${index}`}
                  text={turn.content}
                  region={region}
                  onNavigate={onNavigate}
                />
              ),
            )}
            {asking && <p className="figure-note">Reading…</p>}
            {error && <Empty>{error}</Empty>}
            {method && messages.some((t) => t.role === 'assistant') && (
              <p className="figure-note">{method}</p>
            )}
            <div ref={end} />
          </div>
          <form className="desk-ask" onSubmit={send}>
            <textarea
              ref={field}
              rows={variant === 'panel' ? 2 : 3}
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              disabled={asking}
              placeholder={
                messages.length ? 'A follow-up' : DEFAULT_QUESTION
              }
              aria-label="Question for the desk"
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  event.currentTarget.form?.requestSubmit()
                }
              }}
            />
            <button type="submit" className="ink-button" disabled={asking || !draft.trim()}>
              {asking ? 'Reading…' : 'Ask'}
            </button>
          </form>
        </>
      )}
    </div>
  )
}
