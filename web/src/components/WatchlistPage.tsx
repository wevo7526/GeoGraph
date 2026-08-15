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
import { Empty } from './charts/Charts'

function WatchRow({
  item,
  onOpen,
}: {
  item: WatchedRelationship
  onOpen: () => void
}) {
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
    <div className="boxed flex items-center justify-between gap-4">
      <button className="text-left" onClick={onOpen}>
        <div className="text-lg">{name}</div>
        <div className="text-sm" style={{ color: level && trend === 'rising' ? 'var(--alert)' : 'var(--muted)' }}>
          {series === undefined
            ? 'reading…'
            : level
              ? `${tensionSentence(level, trend)} · ${regionLabel}`
              : `${regionLabel}`}
        </div>
      </button>
      <button
        className="article-link whitespace-nowrap"
        onClick={() => unwatch(item.dyadId)}
        aria-label={`Stop following ${name}`}
      >
        Remove
      </button>
    </div>
  )
}

export default function WatchlistPage({
  onNavigate,
}: {
  region: string
  onNavigate: (r: string) => void
}) {
  const items = useWatchlist()

  return (
    <div className="max-w-3xl mx-auto px-6 py-10">
      <div className="kicker mb-1">Watchlist</div>
      <h1 className="text-2xl mb-6">Relationships you follow</h1>

      {!items.length ? (
        <Empty note="Nothing saved yet — open a relationship and press ☆ Follow to add it here." />
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
  )
}
