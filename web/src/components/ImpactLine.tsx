import { useEffect, useState } from 'react'
import { getEventImpact, lastFailureFor } from '../api'
import { impactLine } from '../lib/story'
import type { EventImpact } from '../types'

/** Measured vs expected vs surprise, one line. Empty is "not measured". */
export default function ImpactLine({
  eventId,
  onOpenPair,
}: {
  eventId: string
  onOpenPair?: () => void
}) {
  const [impact, setImpact] = useState<EventImpact | null | undefined>(undefined)

  useEffect(() => {
    let live = true
    setImpact(undefined)
    getEventImpact(eventId).then((r) => live && setImpact(r))
    return () => {
      live = false
    }
  }, [eventId])

  if (impact === undefined) {
    return <p className="figure-note">Reading measured against typical…</p>
  }
  if (impact === null) {
    const failure = lastFailureFor(`/api/impact/${encodeURIComponent(eventId)}`)
    return (
      <p className="figure-note">
        {failure?.status === 404 ? 'Not measured.' : (failure?.detail ?? 'The impact read is unavailable.')}
      </p>
    )
  }

  return (
    <p className="figure-note">
      {impactLine(impact)}
      {onOpenPair && (
        <>
          {' '}
          <button type="button" className="article-link" onClick={onOpenPair}>
            open the pair →
          </button>
        </>
      )}
    </p>
  )
}
