import { useEffect, useState } from 'react'
import { getEventImpact, lastFailureFor } from '../api'
import { impactLine } from '../lib/story'
import type { EventImpact } from '../types'
import { Caption, Read } from '../ui'

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
    return <Caption>Reading measured against typical…</Caption>
  }
  if (impact === null) {
    const failure = lastFailureFor(`/api/impact/${encodeURIComponent(eventId)}`)
    return (
      <Caption>
        {failure?.status === 404 ? 'Not measured.' : (failure?.detail ?? 'The impact read is unavailable.')}
      </Caption>
    )
  }

  return (
    <Read>
      {impactLine(impact)}
      {onOpenPair && (
        <>
          {' '}
          <button type="button" className="article-link" onClick={onOpenPair}>
            open the pair →
          </button>
        </>
      )}
    </Read>
  )
}
