// The Wire — what has just come in, newest first, with the system's first
// read on each item.
//
// THE READ IS RELATIVE, and that is what makes this a wire rather than a log.
// Every item is placed against THAT PAIR's own running baseline, so the same
// Goldstein score reads as a quiet week for a rivalry and a rupture for an
// alliance. An absolute scale would call the first a crisis and miss the
// second — the mistake the relationship page made before its bands became
// comparatives.
//
// The sentences are composed in lib/story.ts from named fields. The backend
// ships numbers, never prose (test_surface_language.py refuses the latter).

import { useEffect, useState } from 'react'

import { getWire } from '../api'
import { wireLede, wireRead } from '../lib/story'
import { useRegionLabel } from '../regions'
import type { WireFeed, WireItem } from '../types'
import { Chip, Empty, StoryHead } from '../ui'

function WireRow({ item, onOpen }: { item: WireItem; onOpen: () => void }) {
  return (
    <article className="boxed wire-item" style={{ padding: '0.8rem 1rem' }}>
      <div className="flex items-baseline justify-between gap-3 flex-wrap">
        <time className="mono text-xs muted" dateTime={item.event_time ?? undefined}>
          {item.event_time}
        </time>
        <div className="flex items-center gap-2">
          {item.departure ? <Chip label="departure" tone="bad" /> : null}
          {item.tone ? (
            <Chip label={item.tone} tone={item.tone === 'coercive' ? 'bad' : 'good'} />
          ) : null}
        </div>
      </div>
      <h3 className="wire-headline" style={{ margin: '0.3rem 0 0.25rem' }}>
        {item.name}
      </h3>
      <p className="muted" style={{ margin: 0 }}>
        {wireRead(item)}
      </p>
      {item.dyad_id ? (
        <button type="button" className="linklike mono text-xs" onClick={onOpen}>
          open the relationship
        </button>
      ) : null}
    </article>
  )
}

export default function WirePage({
  region,
  onNavigate,
}: {
  region: string
  onNavigate: (route: string) => void
}) {
  const label = useRegionLabel(region)
  const [feed, setFeed] = useState<WireFeed | null | undefined>(undefined)

  useEffect(() => {
    let live = true
    setFeed(undefined)
    getWire(region).then((r) => live && setFeed(r))
    return () => {
      live = false
    }
  }, [region])

  if (feed === undefined) {
    return <Empty>Reading the wire…</Empty>
  }
  if (feed === null) {
    return <Empty>The wire is unavailable — the archive is not answering.</Empty>
  }
  if (!feed.rows.length) {
    return (
      <Empty>
        Nothing coded for {label} yet. The wire fills as the harvest fetches each
        day and the archive codes it.
      </Empty>
    )
  }

  const lede = wireLede(feed, label)

  return (
    <div className="page-stack">
      {lede ? (
        <StoryHead
          kicker="the wire"
          title={lede.headline}
          standfirst={lede.support}
        />
      ) : null}
      <div className="wire-feed">
        {feed.rows.map((item) => (
          <WireRow
            key={item.node_id}
            item={item}
            onOpen={() =>
              onNavigate(
                `/relationships?dyad=${encodeURIComponent(item.dyad_id ?? '')}` +
                  `&region=${encodeURIComponent(region)}`,
              )
            }
          />
        ))}
      </div>
      <p className="muted text-xs">{feed.method}</p>
    </div>
  )
}
