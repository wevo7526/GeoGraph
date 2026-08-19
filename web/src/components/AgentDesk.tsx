/** The desk itself — argument over numbers already on the archive.
 *
 *  Used full-page on Intel and as the body of the corner panel. The opening
 *  reading is an article (grafs, a lede, named citations), not a chat log.
 *  The default first question is not printed. Method sits under a disclosure. */
import { useEffect, useRef, useState, type FormEvent } from 'react'
import {
  citeLabel,
  citeRoute,
  DEFAULT_QUESTION,
  parseDeskProse,
  type DeskBlock,
  type DeskInline,
} from '../lib/desk'
import type { SituationBriefing } from '../types'
import { Disclosure, Empty } from '../ui'
import { useAgent } from './AgentSession'

function Inline({
  parts,
  region,
  briefing,
  onNavigate,
}: {
  parts: DeskInline[]
  region: string
  briefing: SituationBriefing | null
  onNavigate?: (route: string) => void
}) {
  return (
    <>
      {parts.map((part, index) => {
        if (part.kind === 'text') return <span key={index}>{part.value}</span>
        if (part.kind === 'strong') return <strong key={index}>{part.value}</strong>
        const route = onNavigate ? citeRoute(part.id, region) : null
        const label = citeLabel(part.id, briefing)
        if (route && onNavigate) {
          return (
            <button
              key={index}
              type="button"
              className="article-link"
              onClick={() => onNavigate(route)}
            >
              {label}
            </button>
          )
        }
        return <span key={index}>{label}</span>
      })}
    </>
  )
}

function Blocks({
  blocks,
  lede,
  region,
  briefing,
  onNavigate,
}: {
  blocks: DeskBlock[]
  lede: boolean
  region: string
  briefing: SituationBriefing | null
  onNavigate?: (route: string) => void
}) {
  return (
    <>
      {blocks.map((block, index) => {
        if (block.kind === 'ul') {
          return (
            <ul key={index} className="desk-list">
              {block.items.map((item, itemIndex) => (
                <li key={itemIndex}>
                  <Inline parts={item} region={region} briefing={briefing} onNavigate={onNavigate} />
                </li>
              ))}
            </ul>
          )
        }
        const cls = lede && index === 0 ? 'desk-lede' : 'desk-graf'
        return (
          <p key={index} className={cls}>
            <Inline parts={block.children} region={region} briefing={briefing} onNavigate={onNavigate} />
          </p>
        )
      })}
    </>
  )
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
  const { messages, asking, error, darkReason, method, briefing, ask } = useAgent()
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

  const visible = messages.filter((turn, index) => {
    if (index === 0 && turn.role === 'user' && turn.content === DEFAULT_QUESTION) return false
    return true
  })
  const firstAssistant = visible.findIndex((turn) => turn.role === 'assistant')

  return (
    <div className={variant === 'panel' ? 'desk desk--panel' : 'desk'}>
      {darkReason ? (
        <Empty>{darkReason}</Empty>
      ) : (
        <>
          <div className="desk-thread" aria-live="polite">
            {visible.length === 0 && !asking && (
              <p className="figure-note">
                The desk reads the briefing on this page. It argues; it does not measure.
              </p>
            )}
            {visible.map((turn, index) =>
              turn.role === 'user' ? (
                <p key={`u-${index}`} className="desk-ask-line">
                  {turn.content}
                </p>
              ) : (
                <article key={`a-${index}`} className="desk-reading">
                  <Blocks
                    blocks={parseDeskProse(turn.content)}
                    lede={index === firstAssistant}
                    region={region}
                    briefing={briefing}
                    onNavigate={onNavigate}
                  />
                </article>
              ),
            )}
            {asking && <p className="figure-note">Reading…</p>}
            {error && <Empty>{error}</Empty>}
            {method && visible.some((turn) => turn.role === 'assistant') && (
              <Disclosure label="how this was read">
                <p className="figure-note">{method}</p>
              </Disclosure>
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
                visible.some((turn) => turn.role === 'assistant') ? 'A follow-up' : DEFAULT_QUESTION
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
