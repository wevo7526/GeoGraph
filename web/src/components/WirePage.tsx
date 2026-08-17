/** The Wire: what has just come in, and what the system makes of it.
 *
 *  REWRITTEN 2026-08-17, an hour after it first shipped. The first version was
 *  a flat reverse-chronological list in bespoke markup, and it was wrong twice
 *  over. It reached for classes this stylesheet does not define (`page-stack`,
 *  `muted`), so half of it rendered unstyled; and a flat feed is a LOG, not a
 *  wire — every item weighted the same, the two that mattered buried among
 *  fifty that did not.
 *
 *  Now it reads like the rest of the surface: a lede that counts its own claim,
 *  tiles for the shape of the batch, then the editorial split that is the whole
 *  point — what BROKE from routine, at full width with its reasoning, and the
 *  routine traffic under it as a table you can scan and ignore.
 *
 *  THE READ IS RELATIVE. Every item is placed against THAT PAIR's own running
 *  baseline, so the same Goldstein score is a quiet week for a rivalry and a
 *  rupture for an alliance. An absolute scale would call the first a crisis and
 *  miss the second — the mistake the relationship page made before its bands
 *  became comparatives. The sentences are composed in lib/story.ts from named
 *  fields; the backend ships numbers, never prose.
 */
import { useEffect, useState } from 'react'

import { getWire, lastFailureFor } from '../api'
import { count, wireLede, wireRead } from '../lib/story'
import { useRegionLabel } from '../regions'
import type { WireFeed, WireItem } from '../types'
import { Beat, Disclosure, Empty, StoryHead } from '../ui'
import { Tiles } from './charts/Kit'

/** A departure, at full width: when, how far, what happened, what it means.
 *
 *  The figure is a dot-leader ledger row — the same device the markets page
 *  uses for a column of returns, because it is what makes figures scannable
 *  without a table's rules. Colour carries SIGN, as it does everywhere on this
 *  surface: alert for coercive, accent for cooperative. */
function Departure({ item, onOpen }: { item: WireItem; onOpen: () => void }) {
  const points = item.points_from_baseline
  // COLOUR CARRIES THE DIRECTION OF THE DEPARTURE, not the act's absolute
  // sign. `--accent` and `--alert` are a diverging pair meaning
  // de-escalation/escalation, so keying them to the raw Goldstein sign would
  // paint a Russia–Ukraine "disapprove" — 7.1 points CALMER than their war —
  // in the escalation colour.
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
          {points === null ? '—' : `${points.toFixed(1)} pts`}
        </span>
      </div>
      <h3 className="wire-headline">{item.name}</h3>
      <p className="wire-read">{wireRead(item)}</p>
      {item.dyad_id && (
        <button type="button" className="article-link" onClick={onOpen}>
          open the relationship →
        </button>
      )}
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
    return (
      <div className="reading-column py-10">
        <Empty>Reading the wire…</Empty>
      </div>
    )
  }
  if (feed === null) {
    const failure = lastFailureFor('/api/wire')
    return (
      <div className="reading-column py-10">
        <StoryHead
          kicker={`The wire · ${label.toUpperCase()}`}
          title="The wire did not answer"
          standfirst={failure?.detail ?? 'The archive is not answering.'}
        />
      </div>
    )
  }
  if (!feed.rows.length) {
    return (
      <div className="reading-column py-10">
        <StoryHead
          kicker={`The wire · ${label.toUpperCase()}`}
          title={`Nothing coded for ${label} yet`}
          standfirst="The wire fills as the harvest fetches each day and the archive codes it."
        />
      </div>
    )
  }

  const lede = wireLede(feed, label)
  const departures = feed.rows.filter((r) => r.departure)
  const routine = feed.rows.filter((r) => !r.departure)
  const open = (item: WireItem) =>
    onNavigate(
      `/relationships?dyad=${encodeURIComponent(item.dyad_id ?? '')}` +
        `&region=${encodeURIComponent(region)}`,
    )

  return (
    <div className="reading-column py-8">
      <StoryHead
        kicker={`The wire · ${label.toUpperCase()}`}
        title={lede?.headline ?? `The newest events in ${label}`}
        standfirst={lede?.support}
      />

      <div className="mt-8">
        <Tiles
          items={[
            {
              label: 'departures',
              value: count(departures.length),
              tone: departures.length ? 'loss' : 'plain',
              sub: `of ${count(feed.rows.length)} coded events`,
            },
            {
              label: 'newest event',
              value: feed.as_of ? feed.as_of.slice(5) : '—',
              sub: feed.as_of ? feed.as_of.slice(0, 4) : undefined,
            },
            {
              label: 'a departure is',
              value: `${feed.departure_points} pts`,
              sub: "from that pair's own baseline",
            },
          ]}
        />
      </div>

      {/* 1 — WHAT BROKE FROM ROUTINE */}
      <Beat
        title="What broke from routine"
        major
        aside={`Events at least ${feed.departure_points} Goldstein points from the pair's own running baseline. The bar moves per pair: a score that is an ordinary week for a rivalry is a rupture for an alliance.`}
      >
        {departures.length ? (
          departures.map((item) => (
            <Departure key={item.node_id} item={item} onOpen={() => open(item)} />
          ))
        ) : (
          <Empty>
            Nothing in this batch left its pair&rsquo;s usual band. That is a
            reading, not a gap — {count(feed.rows.length)} events were coded and
            every one of them sat where that pair normally sits.
          </Empty>
        )}
      </Beat>

      {/* 2 — THE REST */}
      {routine.length > 0 && (
        <Beat
          title="The rest of the traffic"
          aside={`${count(routine.length)} events that sat inside their pair's usual band. Listed so the wire is complete, not because any of them is news.`}
        >
          <div className="scroll-x">
            <table className="rule-table" style={{ minWidth: 460 }}>
              <thead>
                <tr>
                  <th className="text-left">when</th>
                  <th className="text-left">event</th>
                  <th className="text-right">from baseline</th>
                </tr>
              </thead>
              <tbody>
                {routine.map((item) => (
                  <tr key={item.node_id}>
                    <td className="mono text-xs whitespace-nowrap">{item.event_time}</td>
                    <td>{item.name}</td>
                    <td className="text-right figure">
                      {item.points_from_baseline === null
                        ? '—'
                        : `${item.points_from_baseline.toFixed(1)}`}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Beat>
      )}

      <Disclosure label="How the wire reads an event">
        <p className="figure-note">{feed.method}</p>
        <p className="figure-note">
          The distance is measured in Goldstein points against the pair&rsquo;s own
          running baseline, which is why the same event can be routine for one
          pair and a departure for another. Nothing here is a forecast: the wire
          reports what was coded and how far it sat from usual.
        </p>
      </Disclosure>
    </div>
  )
}
