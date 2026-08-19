/** Shared wire entries — Intel headlines them; the Wire page prints the full
 *  feed. One component so the package cannot disagree about a departure. */
import { wireHeadline, wireRead } from '../lib/story'
import type { WireItem } from '../types'
import ImpactLine from './ImpactLine'

export function baselineFigure(item: WireItem): string {
  const points = item.points_from_baseline
  if (points === null) return '—'
  if (item.pair_baseline === null) return `${points.toFixed(1)} pts`
  const bar = item.pair_baseline >= 0
    ? `+${item.pair_baseline.toFixed(1)}`
    : item.pair_baseline.toFixed(1)
  return `${points.toFixed(1)} pts from ${bar}`
}

/** A departure: when, how far, what happened.
 *
 *  Colour carries the DIRECTION of the departure, not the act's absolute
 *  sign — a calmer week in a war is accent, not alert. */
export function WireDeparture({
  item,
  onOpen,
  onEffects,
  onStudy,
  effectsOpen,
  compact,
}: {
  item: WireItem
  onOpen?: () => void
  onEffects?: () => void
  onStudy?: () => void
  effectsOpen?: boolean
  compact?: boolean
}) {
  const points = item.points_from_baseline
  const sign =
    item.escalation_direction === 'escalating'
      ? 'var(--alert)'
      : item.escalation_direction === 'deescalating'
        ? 'var(--accent)'
        : 'var(--text)'
  return (
    <article className="wire-entry">
      <div className="ledger-row">
        <time className="mono text-xs" style={{ color: 'var(--muted)' }} dateTime={item.event_time ?? undefined}>
          {item.event_time}
        </time>
        <span className="ledger-leader" aria-hidden="true" />
        <span className="ledger-figure" style={{ color: sign }}>
          {points === null ? '—' : baselineFigure(item)}
        </span>
      </div>
      <h3 className="wire-headline">{wireHeadline(item)}</h3>
      <p className="wire-read">{wireRead(item)}</p>
      {!compact && (
        <div className="toolbar mt-2" style={{ borderTop: 'none' }}>
          {item.dyad_id && onOpen && (
            <button type="button" className="article-link" onClick={onOpen}>
              open the relationship →
            </button>
          )}
          {onEffects && (
            <button type="button" className="article-link" onClick={onEffects}>
              {effectsOpen ? 'hide measured vs typical' : 'measured vs typical →'}
            </button>
          )}
          {onStudy && (
            <button type="button" className="article-link" onClick={onStudy}>
              the study →
            </button>
          )}
        </div>
      )}
      {compact && item.dyad_id && onOpen && (
        <button type="button" className="article-link" onClick={onOpen}>
          open the relationship →
        </button>
      )}
      {effectsOpen && onEffects && (
        <ImpactLine
          eventId={item.node_id}
          onOpenPair={item.dyad_id ? onOpen : undefined}
        />
      )}
    </article>
  )
}
