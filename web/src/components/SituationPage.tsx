import { useEffect, useState } from 'react'
import {
  getGlobe,
  getJobs,
  getMarketsStory,
  getRegionMap,
  getWire,
  getWireLive,
  lastFailureFor,
} from '../api'
import { count, signedPct, situationLede, skillSentence, wireHeadline, wireRead } from '../lib/story'
import { useRegionLabel } from '../regions'
import type {
  GlobeBoard,
  JobsStatus,
  MarketsStory,
  RegionMap,
  WireFeed,
  WireItem,
  WireLiveFeed,
} from '../types'
import { Beat, Empty, StoryHead } from '../ui'
import SituationPlate from './SituationPlate'

/** Situation is the working home — what just happened, what it means for
 *  markets, what happens next — composed from endpoints that already exist.
 *  No new job, no invented number. */
export default function SituationPage({
  region,
  onNavigate,
}: {
  region: string
  onNavigate: (route: string) => void
}) {
  const label = useRegionLabel(region)
  const [wire, setWire] = useState<WireFeed | null | undefined>(undefined)
  const [liveFeed, setLiveFeed] = useState<WireLiveFeed | null | undefined>(undefined)
  const [map, setMap] = useState<RegionMap | null | undefined>(undefined)
  const [story, setStory] = useState<MarketsStory | null | undefined>(undefined)
  const [globe, setGlobe] = useState<GlobeBoard | null | undefined>(undefined)
  const [jobs, setJobs] = useState<JobsStatus | null>(null)

  useEffect(() => {
    let live = true
    setWire(undefined)
    setLiveFeed(undefined)
    setMap(undefined)
    setStory(undefined)
    setGlobe(undefined)
    getWire(region).then((r) => live && setWire(r))
    getWireLive(region).then((r) => live && setLiveFeed(r))
    getRegionMap(region).then((r) => live && setMap(r))
    getMarketsStory(region).then((r) => live && setStory(r))
    getGlobe(region, 12).then((r) => live && setGlobe(r))
    getJobs().then((r) => live && setJobs(r))
    return () => {
      live = false
    }
  }, [region])

  if (wire === undefined) {
    return (
      <div className="reading-column py-10">
        <Empty>Reading the situation…</Empty>
      </div>
    )
  }
  if (wire === null) {
    const failure = lastFailureFor('/api/wire')
    return (
      <div className="reading-column py-10">
        <StoryHead
          kicker={`Situation · ${label.toUpperCase()}`}
          title="The situation did not answer"
          standfirst={failure?.detail ?? 'The archive is not answering.'}
        />
      </div>
    )
  }

  const lede = situationLede({
    label,
    wire,
    map: map ?? null,
    story: story ?? null,
  })
  const departures = wire.rows.filter((r) => r.departure).slice(0, 5)
  const liveRows = liveFeed?.rows?.slice(0, 3) ?? []
  const lead = map && !map.resolving ? map.ranking?.[0] : undefined
  const headlined = (story?.markets ?? []).filter((m) => m.headline).slice(0, 4)
  const skill = skillSentence(story?.transmission_skill, label)
  const unplaced = globe?.counts?.unplaced ?? globe?.unplaced?.length ?? 0
  const studyStopped = jobs?.jobs?.find((j) => j.name === 'study')?.last_result?.stopped
  const openPair = (item: WireItem) => {
    if (!item.dyad_id) return
    onNavigate(
      `/relationships?dyad=${encodeURIComponent(item.dyad_id)}` +
        `&region=${encodeURIComponent(region)}`,
    )
  }

  return (
    <article className="reading-column py-8">
      <StoryHead
        kicker={`Situation · ${label.toUpperCase()}`}
        title={lede?.headline ?? `The situation in ${label}`}
        standfirst={lede?.support}
      />

      {(unplaced > 0 || studyStopped) && (
        <p className="figure-note mt-4">
          {unplaced > 0 &&
            `${count(unplaced)} roster actor${unplaced === 1 ? '' : 's'} sit off the globe — blocs, funds, movements with no coordinate.`}
          {unplaced > 0 && studyStopped ? ' ' : ''}
          {studyStopped && `The event study is paused (${studyStopped}). Coverage will not grow until the volume has headroom.`}
        </p>
      )}

      <div className="situation-plate mt-8">
        <SituationPlate region={region} onOpen={onNavigate} />
      </div>

      <Beat
        title="What broke from routine"
        major
        aside="Departures from each pair's own running baseline — the same read as the Wire, cut to the ones that matter on arrival."
      >
        {liveRows.length > 0 && (
          <p className="figure-note mb-4">
            Live overlay: {liveRows.map((item) => wireHeadline(item)).join('; ')}.
          </p>
        )}
        {departures.length ? (
          <ul className="space-y-4">
            {departures.map((item) => (
              <li key={item.node_id}>
                <time className="mono text-xs" style={{ color: 'var(--muted)' }}>
                  {item.event_time}
                </time>
                <h3 className="wire-headline">{wireHeadline(item)}</h3>
                <p className="wire-read">{wireRead(item)}</p>
                {item.dyad_id && (
                  <button type="button" className="article-link" onClick={() => openPair(item)}>
                    open the relationship →
                  </button>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <Empty>
            Nothing in this batch left its pair&rsquo;s usual band.
          </Empty>
        )}
        <p className="mt-4">
          <button type="button" className="article-link" onClick={() => onNavigate('/wire')}>
            the full wire →
          </button>
        </p>
      </Beat>

      <Beat
        title="What it means for prices"
        aside="Headline cells from the packed transmission map — historical mix, so published medians do not silently jump."
      >
        {story === undefined ? (
          <Empty>Reading packed markets…</Empty>
        ) : story === null || story.pending ? (
          <Empty>
            {story?.note ?? 'The markets story is still being written for this region.'}
          </Empty>
        ) : headlined.length ? (
          <>
            <ul className="space-y-2">
              {headlined.map((m) => {
                const cell = m.headline!
                return (
                  <li key={m.ticker} className="ledger-row">
                    <span>{m.name}</span>
                    <span className="ledger-leader" aria-hidden="true" />
                    <span className="ledger-figure" style={{ color: cell.median >= 0 ? 'var(--accent)' : 'var(--alert)' }}>
                      {signedPct(cell.median, 1)} typical
                    </span>
                  </li>
                )
              })}
            </ul>
            {skill && <p className="figure-note mt-3">{skill}</p>}
          </>
        ) : (
          <Empty>No packed market holds a headline cell yet.</Empty>
        )}
        <p className="mt-4">
          <button type="button" className="article-link" onClick={() => onNavigate('/markets')}>
            the transmission map →
          </button>
        </p>
      </Beat>

      <Beat
        title="What happens next"
        aside="The region's solved games — the pair carrying the most coercion, and its likeliest course."
      >
        {map === undefined ? (
          <Empty>Solving the region…</Empty>
        ) : map === null || map.resolving ? (
          <Empty>{map?.note ?? 'No solved games for this region yet.'}</Empty>
        ) : lead ? (
          <>
            <p>
              <button
                type="button"
                className="article-link"
                onClick={() =>
                  onNavigate(
                    `/relationships?dyad=${encodeURIComponent(lead.dyad_id)}&region=${encodeURIComponent(region)}`,
                  )
                }
              >
                {lead.dyad_name}
              </button>
              {lead.coercive_events
                ? ` — ${count(lead.coercive_events)} coercive acts in the last year.`
                : '.'}
            </p>
            <p className="mt-3">
              <button type="button" className="article-link" onClick={() => onNavigate('/games')}>
                the region map →
              </button>
            </p>
          </>
        ) : (
          <Empty>The region map has no ranking yet.</Empty>
        )}
      </Beat>
    </article>
  )
}
