// The Watchlist — the relationships this user follows, each with a fresh
// current read and a way back in. The list is local; each row fetches live
// state on mount, so returning here shows where things stand now, not a
// snapshot from when it was saved.

import { useEffect, useState } from 'react'

import { getDyadSeries } from '../api'
import { relationshipName, tensionLevel, tensionSentence, tensionTrend } from '../lib/language'
import { remove as unwatch, useWatchlist } from '../lib/watchlist'
import type { WatchedRelationship } from '../lib/watchlist'
import { useRegionLabel } from '../regions'
import type { DyadSeries } from '../types'
import { Empty, StoryHead, TensionBadge } from '../ui'

function badgeTrend(trend: 'rising' | 'easing' | 'steady'): 'rising' | 'falling' | 'steady' {
  return trend === 'easing' ? 'falling' : trend
}

function WatchRow({ item, onOpen }: { item: WatchedRelationship; onOpen: () => void }) {
  const regionLabel = useRegionLabel(item.region)
  const [series, setSeries] = useState<DyadSeries | null | undefined>(undefined)

  useEffect(() => {
    let live = true
    setSeries(undefined)
    getDyadSeries(item.dyadId, item.region).then((r) => live && setSeries(r))
    return () => {
      live = false
    }
  }, [item.dyadId, item.region])

  const rows = series ? series.rows : []
  const level = rows.length ? tensionLevel(rows[rows.length - 1].intensity, series?.peak ?? 0) : null
  const trend = tensionTrend(rows)
  const name = relationshipName((series || undefined)?.dyad_name, item.name)

  return (
    <div className="boxed" style={{ padding: '0.85rem 1rem' }}>
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <button className="text-left" onClick={onOpen} style={{ minWidth: 0 }}>
          <div className="text-lg" style={{ letterSpacing: '-0.01em' }}>
            {name}
          </div>
          <div className="mono text-[11px]" style={{ color: 'var(--muted)', letterSpacing: '0.1em' }}>
            {regionLabel.toUpperCase()}
          </div>
        </button>
        <div className="flex items-center gap-3">
          {level && <TensionBadge label={level} trend={badgeTrend(trend)} />}
          <button
            className="article-link whitespace-nowrap"
            onClick={() => unwatch(item.dyadId)}
            aria-label={`Stop following ${name}`}
          >
            Remove
          </button>
        </div>
      </div>
      {series !== undefined && level && (
        <p className="text-sm mt-1" style={{ color: trend === 'rising' ? 'var(--alert)' : 'var(--muted)' }}>
          {tensionSentence(level, trend)}
        </p>
      )}
      {series === undefined && (
        <p className="text-sm mt-1" style={{ color: 'var(--muted)' }}>
          reading…
        </p>
      )}
    </div>
  )
}

export default function WatchlistPage({ onNavigate }: { region: string; onNavigate: (r: string) => void }) {
  const items = useWatchlist()

  return (
    <div className="reading-column">
      <StoryHead kicker="Watchlist" title="Relationships you follow" />
      <div className="mt-8">
        {!items.length ? (
          <Empty>Nothing saved yet — open a relationship and press ☆ Follow to add it here.</Empty>
        ) : (
          <div className="space-y-3">
            {items.map((item) => (
              <WatchRow
                key={item.dyadId}
                item={item}
                onOpen={() =>
                  onNavigate(
                    `/relationship?dyad=${encodeURIComponent(item.dyadId)}` +
                      `&region=${encodeURIComponent(item.region)}`,
                  )
                }
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
