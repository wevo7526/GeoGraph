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
 *
 *  Headlines are composed the same way. The event's own `name` is CAMEO
 *  vocabulary ("Engage in negotiation: Israel → Egypt") and is never rendered
 *  here — the globe already refused that string; this page caught up.
 */
import { useEffect, useState } from 'react'

import { getWire, getWireLive, lastFailureFor } from '../api'
import { count, wireHeadline, wireKindWord, wireLede } from '../lib/story'
import { useRegionLabel } from '../regions'
import type { WireFeed, WireItem, WireLiveFeed, WireLiveItem } from '../types'
import { Beat, Disclosure, Empty, StoryHead } from '../ui'
import { Tiles } from './charts/Kit'
import { baselineFigure, WireDeparture } from './WireList'

const LIVE_POLL_MS = 60_000

function LiveCard({ item }: { item: WireLiveItem }) {
  const outlook = item.market_outlook
  const kind = wireKindWord(item.implied_kind)
  return (
    <article className="wire-entry">
      <div className="ledger-row">
        <time className="mono text-xs" style={{ color: 'var(--muted)' }}>{item.available_at ?? item.event_time}</time>
        <span className="ledger-leader" aria-hidden="true" />
        <span className="ledger-figure">{item.mentions ?? '—'} mentions</span>
      </div>
      <h3 className="wire-headline">{wireHeadline(item)}</h3>
      <p className="wire-read">
        {item.escalation_direction
          ? `${item.implied_kind === 'stable' ? 'Routine against this pair’s usual level' : `This reads as ${kind} against this pair’s usual level`}${
              item.escalation_magnitude != null
                ? ` (${item.escalation_magnitude.toFixed(1)} Goldstein points from their usual).`
                : '.'
            } Historical market cells below are analogy — what similarly coded events did — not a live trade.`
          : `Coded as ${kind}. Historical cells are analogy from the frozen transmission map, not a live trade.`}
      </p>
      {outlook.length ? (
        <div className="scroll-x mt-2">
          <table className="rule-table" style={{ minWidth: 480 }}>
            <thead>
              <tr>
                <th className="text-left">historically, after this kind</th>
                <th className="text-right">median</th>
                <th className="text-right">middle half</th>
                <th className="text-right">sample</th>
              </tr>
            </thead>
            <tbody>{outlook.map((market) => (
              <tr key={market.ticker}>
                <td>
                  {market.market}{' '}
                  <span className="mono text-xs" style={{ color: 'var(--muted)' }}>
                    {market.thin ? 'thin' : ''}
                  </span>
                </td>
                <td className="text-right mono">
                  {market.median == null ? '—' : `${market.median >= 0 ? '+' : ''}${(market.median * 100).toFixed(2)}%`}
                </td>
                <td className="text-right mono" style={{ color: 'var(--muted)' }}>
                  {market.p25 == null || market.p75 == null
                    ? '—'
                    : `${(market.p25 * 100).toFixed(1)} to ${(market.p75 * 100).toFixed(1)}`}
                </td>
                <td className="text-right mono">{market.n}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      ) : item.implied_kind === 'stable' ? (
        <p className="figure-note">
          No priced cell: this is routine against the pair&rsquo;s own baseline, and the
          historical &ldquo;stable&rdquo; median is what the region does on an ordinary
          day, not what this event is worth.
        </p>
      ) : (
        <p className="figure-note">No measured market cell for this kind of event — analogy has nothing to attach.</p>
      )}
      {item.source_url && (
        <a className="article-link" href={item.source_url} target="_blank" rel="noreferrer">
          source →
        </a>
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
  const [liveFeed, setLiveFeed] = useState<WireLiveFeed | null | undefined>(undefined)
  const [openEffects, setOpenEffects] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    setFeed(undefined)
    setLiveFeed(undefined)
    setOpenEffects(null)
    getWire(region).then((r) => live && setFeed(r))
    const pullLive = () => getWireLive(region).then((r) => live && setLiveFeed(r))
    pullLive()
    const id = window.setInterval(pullLive, LIVE_POLL_MS)
    return () => {
      live = false
      window.clearInterval(id)
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
          kicker={`Intel · Wire · ${label.toUpperCase()}`}
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
          kicker={`Intel · Wire · ${label.toUpperCase()}`}
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
  const study = (item: WireItem) =>
    onNavigate(
      `/case/dynamic?event=${encodeURIComponent(item.node_id)}` +
        `&region=${encodeURIComponent(region)}`,
    )

  return (
    <div className="reading-column py-8">
      <StoryHead
        kicker={`Intel · Wire · ${label.toUpperCase()}`}
        title={lede?.headline ?? `The newest events in ${label}`}
        standfirst={lede?.support}
        action={
          <button type="button" className="article-link" onClick={() => onNavigate('/intel')}>
            the desk →
          </button>
        }
      />

      {liveFeed?.rows.length ? (
        <Beat
          title="What just arrived"
          major
          aside="Newest 15-minute GDELT 2.0 export, scored against each pair's usual level in the frozen archive. A historical cell is analogy — what similarly coded events did — attached only for escalations and step-downs. Routine traffic is not a trade. Not advice."
        >
          {liveFeed.rows.slice(0, 8).map((item) => (
            <LiveCard key={item.node_id} item={item} />
          ))}
          <p className="figure-note">{liveFeed.method}</p>
        </Beat>
      ) : null}

      <div className="mt-8">
        <Tiles
          items={[
            {
              label: 'departures',
              value: count(departures.length),
              tone: departures.length ? 'loss' : 'plain',
              sub: `of ${count(feed.rows.length)} coded events${feed.truncated ? ' (newest sixty)' : ''}`,
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
            <WireDeparture
              key={item.node_id}
              item={item}
              onOpen={() => open(item)}
              onEffects={() => setOpenEffects((id) => (id === item.node_id ? null : item.node_id))}
              onStudy={() => study(item)}
              effectsOpen={openEffects === item.node_id}
            />
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
                    <td>{wireHeadline(item)}</td>
                    <td className="text-right figure">{baselineFigure(item)}</td>
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
          pair and a departure for another. The live section uses the event&rsquo;s
          raw Goldstein band, not that baseline — a fifteen-minute-old event has
          no history of its own yet. Nothing here is a forecast: the wire
          reports what was coded and how far it sat from usual.
        </p>
      </Disclosure>
    </div>
  )
}
