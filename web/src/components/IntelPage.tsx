/** Intel: the briefing — lede, globe, a small figure, then the wire.
 *
 *  THE AGENT RUNS THE ROOM from the corner desk, not from this page. The
 *  lede is composed from named fields (`lib/story.ts` `situationLede`);
 *  questions go through the global modal. The feed that used to live on its
 *  own Wire page is folded under the argument: live overlay, what broke,
 *  the rest of the traffic. Old `/situation` and `/wire` hashes still land
 *  here.
 */
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
import { count, intelTrafficFigure, situationLede } from '../lib/story'
import { useRegionLabel } from '../regions'
import type {
  GlobeBoard,
  JobsStatus,
  MarketsStory,
  RegionMap,
  WireFeed,
  WireLiveFeed,
} from '../types'
import { Empty, StoryHead } from '../ui'
import SituationPlate from './SituationPlate'
import { WireFeedBeats } from './WireList'
import { Bars } from './charts/Kit'

const LIVE_POLL_MS = 60_000

export default function IntelPage({
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
    getRegionMap(region).then((r) => live && setMap(r))
    getMarketsStory(region).then((r) => live && setStory(r))
    getGlobe(region, 12).then((r) => live && setGlobe(r))
    getJobs().then((r) => live && setJobs(r))
    const pullLive = () => getWireLive(region).then((r) => live && setLiveFeed(r))
    pullLive()
    const id = window.setInterval(pullLive, LIVE_POLL_MS)
    return () => {
      live = false
      window.clearInterval(id)
    }
  }, [region])

  if (wire === null) {
    const failure = lastFailureFor('/api/wire')
    return (
      <div className="intel-page py-10">
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
  const unplaced = globe?.counts?.unplaced ?? globe?.unplaced?.length ?? 0
  const studyStopped = jobs?.jobs?.find((j) => j.name === 'study')?.last_result?.stopped
  const figure = intelTrafficFigure({
    wire: wire ?? null,
    live: liveFeed ?? null,
  })

  return (
    <div className="intel-page">
      <StoryHead
        kicker={`Intel · ${label.toUpperCase()}`}
        title={lede?.headline ?? `The desk is reading ${label}`}
        standfirst={lede?.support ?? 'The argument opens as the briefing lands.'}
      />
      {(unplaced > 0 || studyStopped) && (
        <p className="figure-note mt-4">
          {unplaced > 0 &&
            `${count(unplaced)} roster actor${unplaced === 1 ? '' : 's'} sit off the globe — blocs, funds, movements with no coordinate.`}
          {unplaced > 0 && studyStopped ? ' ' : ''}
          {studyStopped && `The event study is paused (${studyStopped}). Coverage will not grow until the volume has headroom.`}
        </p>
      )}
      <div className="intel-grid">
        <div className="situation-plate intel-globe">
          <SituationPlate region={region} board={globe === undefined ? undefined : globe} onOpen={onNavigate} />
        </div>
        {figure && (
          <figure className="intel-figure">
            <figcaption>
              <h2>{figure.title}</h2>
              <p className="figure-note">{figure.support}</p>
            </figcaption>
            <Bars rows={figure.bars} format={(v) => String(v)} />
          </figure>
        )}
      </div>
      {wire === undefined ? (
        <Empty>Reading the wire…</Empty>
      ) : (
        <WireFeedBeats
          className="intel-wire"
          feed={wire}
          liveFeed={liveFeed}
          region={region}
          onNavigate={onNavigate}
        />
      )}
    </div>
  )
}

/** @deprecated old hash; App still accepts /situation. */
export { IntelPage as SituationPage }
