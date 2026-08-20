/** The wire, as Intel prints it — departures at full width, routine as a table.
 *
 *  Headlines are composed in lib/story.ts. The event's own `name` is CAMEO
 *  vocabulary and is never rendered here. */
import { useState } from 'react'
import { citableUrl } from '../lib/cite'
import { count, offersPairNav, thirdCountryForce, wireHeadline, wireKindWord, wireRead } from '../lib/story'
import type { WireFeed, WireItem, WireLiveFeed, WireLiveItem } from '../types'
import { Beat, Disclosure, Empty } from '../ui'
import { Tiles } from './charts/Kit'
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
  effectsOpen,
}: {
  item: WireItem
  onOpen?: () => void
  onEffects?: () => void
  effectsOpen?: boolean
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
      <div className="toolbar mt-2" style={{ borderTop: 'none' }}>
        {offersPairNav(item) && onOpen && (
          <button type="button" className="article-link" onClick={onOpen}>
            open the relationship →
          </button>
        )}
        {onEffects && (
          <button type="button" className="article-link" onClick={onEffects}>
            {effectsOpen ? 'hide measured vs typical' : 'measured vs typical →'}
          </button>
        )}
      </div>
      {effectsOpen && onEffects && (
        <ImpactLine
          eventId={item.node_id}
          onOpenPair={item.dyad_id ? onOpen : undefined}
        />
      )}
    </article>
  )
}

function SourceCredit({
  name,
  id,
  url,
}: {
  name?: string | null
  id?: string | null
  url?: string | null
}) {
  const label = name || id
  if (!label) return null
  const href = citableUrl(url)
  if (href) {
    return (
      <a className="article-link" href={href} target="_blank" rel="noreferrer">
        {label}
      </a>
    )
  }
  return <span className="figure-note">{label}</span>
}

function LiveCard({ item }: { item: WireLiveItem }) {
  const outlook = item.market_outlook
  const kind = wireKindWord(item.implied_kind)
  const place = item.action_geo_name?.trim() || item.action_geo?.trim() || null
  const inThird = thirdCountryForce(item) && place
  return (
    <article className="wire-entry">
      <div className="ledger-row">
        <time className="mono text-xs" style={{ color: 'var(--muted)' }}>{item.available_at ?? item.event_time}</time>
        <span className="ledger-leader" aria-hidden="true" />
        <span className="ledger-figure">{item.mentions ?? '—'} mentions</span>
      </div>
      <h3 className="wire-headline">{wireHeadline(item)}</h3>
      <p className="wire-read">
        {inThird
          ? `Coded in ${place}. The named flags are who GDELT attached, not a fight between them.`
          : item.escalation_direction
          ? `${item.implied_kind === 'stable' ? 'Routine against this pair’s usual level' : `This reads as ${kind} against this pair’s usual level`}${
              item.escalation_magnitude != null
                ? ` (${item.escalation_magnitude.toFixed(1)} points from their usual).`
                : '.'
            } Historical market cells below are analogy — what similarly coded events did — not a live trade.`
          : `Coded as ${kind}. Historical cells are analogy from the frozen transmission map, not a live trade.`}
      </p>
      {item.measured && item.measured.length ? (
        <div className="scroll-x mt-2">
          <table className="rule-table" style={{ minWidth: 420 }}>
            <thead>
              <tr>
                <th className="text-left">this event</th>
                <th className="text-right">window</th>
                <th className="text-right">abnormal</th>
              </tr>
            </thead>
            <tbody>{item.measured.slice(0, 8).map((row) => (
              <tr key={`${row.ticker}-${row.window}`}>
                <td>
                  {row.market || row.ticker}{' '}
                  <span className="mono text-xs" style={{ color: 'var(--muted)' }}>{row.ticker}</span>
                </td>
                <td className="text-right mono">{row.window}</td>
                <td className="text-right mono">
                  {row.abnormal_return == null
                    ? '—'
                    : `${row.abnormal_return >= 0 ? '+' : ''}${(row.abnormal_return * 100).toFixed(2)}%`}
                </td>
              </tr>
            ))}</tbody>
          </table>
          <p className="figure-note">
            Session prints for this event, scored in memory. Not written into the frozen transmission map.
          </p>
        </div>
      ) : null}
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
      <SourceCredit name={item.source_name} id={item.source_id} url={item.source_url} />
    </article>
  )
}

/** The editorial split that used to be its own page: live overlay, tiles,
 *  what broke, the rest of the traffic. */
export function WireFeedBeats({
  feed,
  liveFeed,
  region,
  onNavigate,
  className,
}: {
  feed: WireFeed
  liveFeed: WireLiveFeed | null | undefined
  region: string
  onNavigate: (route: string) => void
  className?: string
}) {
  const [openEffects, setOpenEffects] = useState<string | null>(null)
  const departures = feed.rows.filter((row) => row.departure)
  const routine = feed.rows.filter((row) => !row.departure)
  const open = (item: WireItem) => {
    if (!offersPairNav(item)) return
    onNavigate(
      `/relationships?dyad=${encodeURIComponent(item.dyad_id!)}` +
        `&region=${encodeURIComponent(region)}`,
    )
  }

  return (
    <div className={className}>
      {liveFeed?.rows.length ? (
        <Beat
          title="What just arrived"
          major
          aside="Newest 15-minute export, scored against each pair's usual level in the frozen archive. A historical cell is analogy — what similarly coded events did — attached only for escalations and step-downs. Routine traffic is not a trade."
        >
          {liveFeed.rows.slice(0, 8).map((item) => (
            <LiveCard key={item.node_id} item={item} />
          ))}
          {liveFeed.method && <p className="figure-note">{liveFeed.method}</p>}
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

      <div className="desk-split intel-wire-split">
      <Beat
        title="What broke from routine"
        major
        aside={`Events at least ${feed.departure_points} points from the pair's own running baseline. The bar moves per pair: a score that is an ordinary week for a rivalry is a rupture for an alliance.`}
      >
        {departures.length ? (
          departures.map((item) => (
            <WireDeparture
              key={item.node_id}
              item={item}
              onOpen={offersPairNav(item) ? () => open(item) : undefined}
              onEffects={() => setOpenEffects((id) => (id === item.node_id ? null : item.node_id))}
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

      {routine.length > 0 && (
        <Beat
          title="The rest of the traffic"
          aside={`${count(routine.length)} events that sat inside their pair's usual band. Listed so the feed is complete, not because any of them is news.`}
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
      </div>

      <Disclosure label="How the feed reads an event">
        <p className="figure-note">{feed.method}</p>
        <p className="figure-note">
          The distance is measured against the pair&rsquo;s own running baseline,
          which is why the same event can be routine for one pair and a
          departure for another. The live section uses the event&rsquo;s raw
          band, not that baseline — a fifteen-minute-old event has no history
          of its own yet. Nothing here is a forecast: the feed reports what
          was coded and how far it sat from usual.
        </p>
      </Disclosure>
    </div>
  )
}
