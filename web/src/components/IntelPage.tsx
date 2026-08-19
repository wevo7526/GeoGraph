/** Intel: the desk's office, and the Wire's front page.
 *
 *  THE AGENT RUNS THE ROOM. The briefing numbers — globe, what broke, packed
 *  markets, the region's lead pair — sit as working papers beside the
 *  argument. They are composed from named fields (`lib/story.ts`); the desk
 *  narrates them, it does not originate them.
 *
 *  Intel and the Wire are one package. This page headlines the departures;
 *  `/wire` is the full feed of the same scan, plus the live overlay. Old
 *  `/situation` hashes still land here.
 */
import { useEffect, useState } from 'react'
import {
  getGlobe,
  getJobs,
  getMarketsStory,
  getRegionMap,
  getWire,
  lastFailureFor,
} from '../api'
import { count, signedPct, situationLede, skillSentence } from '../lib/story'
import { useRegionLabel } from '../regions'
import type {
  GlobeBoard,
  JobsStatus,
  MarketsStory,
  RegionMap,
  WireFeed,
  WireItem,
} from '../types'
import { Empty, StoryHead } from '../ui'
import AgentDesk from './AgentDesk'
import { useAgent } from './AgentSession'
import SituationPlate from './SituationPlate'
import { WireDeparture } from './WireList'

export default function IntelPage({
  region,
  onNavigate,
}: {
  region: string
  onNavigate: (route: string) => void
}) {
  const label = useRegionLabel(region)
  const { brief, darkReason, reread } = useAgent()
  const [wire, setWire] = useState<WireFeed | null | undefined>(undefined)
  const [map, setMap] = useState<RegionMap | null | undefined>(undefined)
  const [story, setStory] = useState<MarketsStory | null | undefined>(undefined)
  const [globe, setGlobe] = useState<GlobeBoard | null | undefined>(undefined)
  const [jobs, setJobs] = useState<JobsStatus | null>(null)

  useEffect(() => {
    let live = true
    setWire(undefined)
    setMap(undefined)
    setStory(undefined)
    setGlobe(undefined)
    getWire(region).then((r) => live && setWire(r))
    getRegionMap(region).then((r) => live && setMap(r))
    getMarketsStory(region).then((r) => live && setStory(r))
    getGlobe(region, 12).then((r) => live && setGlobe(r))
    getJobs().then((r) => live && setJobs(r))
    return () => {
      live = false
    }
  }, [region])

  useEffect(() => {
    brief()
  }, [region, darkReason, brief])

  if (wire === null) {
    const failure = lastFailureFor('/api/wire')
    return (
      <div className="reading-column py-10">
        <StoryHead
          kicker={`Intel · ${label.toUpperCase()}`}
          title="The intel desk did not answer"
          standfirst={failure?.detail ?? 'The archive is not answering.'}
        />
      </div>
    )
  }

  const lede = situationLede({
    label,
    wire: wire ?? null,
    map: map ?? null,
    story: story ?? null,
  })
  const departures = (wire?.rows ?? []).filter((r) => r.departure).slice(0, 5)
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
    <div className="intel">
      <div className="intel-desk">
        <StoryHead
          kicker={`Intel · ${label.toUpperCase()}`}
          title={lede?.headline ?? `The desk is reading ${label}`}
          standfirst={lede?.support ?? 'The argument opens as the briefing lands.'}
          action={
            <button type="button" className="article-link" onClick={() => reread()}>
              new reading
            </button>
          }
        />
        {(unplaced > 0 || studyStopped) && (
          <p className="figure-note mt-4">
            {unplaced > 0 &&
              `${count(unplaced)} roster actor${unplaced === 1 ? '' : 's'} sit off the globe — blocs, funds, movements with no coordinate.`}
            {unplaced > 0 && studyStopped ? ' ' : ''}
            {studyStopped && `The event study is paused (${studyStopped}). Coverage will not grow until the volume has headroom.`}
          </p>
        )}
        <AgentDesk region={region} onNavigate={onNavigate} />
      </div>

      <aside className="intel-board" aria-label="working papers">
        <div className="situation-plate">
          <SituationPlate region={region} board={globe === undefined ? undefined : globe} onOpen={onNavigate} />
        </div>

        <section>
          <h2 className="intel-board-title">What broke</h2>
          {wire === undefined ? (
            <Empty>Reading the wire…</Empty>
          ) : departures.length ? (
            <ul className="intel-board-list">
              {departures.map((item) => (
                <li key={item.node_id}>
                  <WireDeparture item={item} compact onOpen={() => openPair(item)} />
                </li>
              ))}
            </ul>
          ) : (
            <Empty>Nothing in this batch left its pair&rsquo;s usual band.</Empty>
          )}
          <p className="mt-3">
            <button type="button" className="article-link" onClick={() => onNavigate('/wire')}>
              the full wire →
            </button>
          </p>
        </section>

        <section>
          <h2 className="intel-board-title">Prices</h2>
          {story === undefined ? (
            <Empty>Reading packed markets…</Empty>
          ) : story === null || story.pending ? (
            <Empty>{story?.note ?? 'The markets story is still being written for this region.'}</Empty>
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
          <p className="mt-3">
            <button type="button" className="article-link" onClick={() => onNavigate('/markets')}>
              the transmission map →
            </button>
          </p>
        </section>

        <section>
          <h2 className="intel-board-title">What happens next</h2>
          {map === undefined ? (
            <Empty>Solving the region…</Empty>
          ) : map === null || map.resolving ? (
            <Empty>{map?.note ?? 'No solved games for this region yet.'}</Empty>
          ) : lead ? (
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
          ) : (
            <Empty>The region map has no ranking yet.</Empty>
          )}
          <p className="mt-3">
            <button type="button" className="article-link" onClick={() => onNavigate('/games')}>
              the region map →
            </button>
          </p>
        </section>
      </aside>
    </div>
  )
}

/** @deprecated old hash; App still accepts /situation. */
export { IntelPage as SituationPage }
