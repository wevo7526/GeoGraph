import { useEffect, useRef, useState } from 'react'
import {
  getGlobe,
  getHealth,
  getJobs,
  getMarketsStory,
  getRegionMap,
  getWire,
  getWireLive,
  lastFailureFor,
  postAssess,
} from '../api'
import { count, signedPct, situationLede, skillSentence, wireHeadline, wireRead } from '../lib/story'
import { useRegionLabel } from '../regions'
import type {
  Assessment,
  GlobeBoard,
  Health,
  JobsStatus,
  MarketsStory,
  RegionMap,
  WireFeed,
  WireItem,
  WireLiveFeed,
} from '../types'
import { Beat, Disclosure, Empty, StoryHead } from '../ui'
import SituationPlate from './SituationPlate'

const DEFAULT_QUESTION = 'What is the situation?'

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
  const [health, setHealth] = useState<Health | null>(null)
  const [question, setQuestion] = useState(DEFAULT_QUESTION)
  const [asking, setAsking] = useState(false)
  const [assessment, setAssessment] = useState<Assessment | null>(null)
  const [askError, setAskError] = useState<string | null>(null)
  const askGen = useRef(0)

  useEffect(() => {
    let live = true
    askGen.current += 1
    setAsking(false)
    setWire(undefined)
    setLiveFeed(undefined)
    setMap(undefined)
    setStory(undefined)
    setGlobe(undefined)
    setAssessment(null)
    setAskError(null)
    setQuestion(DEFAULT_QUESTION)
    getWire(region).then((r) => live && setWire(r))
    getWireLive(region).then((r) => live && setLiveFeed(r))
    getRegionMap(region).then((r) => live && setMap(r))
    getMarketsStory(region).then((r) => live && setStory(r))
    getGlobe(region, 12).then((r) => live && setGlobe(r))
    getJobs().then((r) => live && setJobs(r))
    getHealth().then((h) => live && setHealth(h))
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
  const darkReason = health?.disabled?.reasoning
  const frozenQuestions = assessment?.context?.forecasts?.rows ?? []
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

      <Beat
        title="Ask the desk"
        aside="An argument over the figures already on this page — not a source of them. Called when you ask, not on arrival."
      >
        {darkReason ? (
          <Empty>{darkReason}</Empty>
        ) : (
          <form
            className="desk-ask"
            onSubmit={(event) => {
              event.preventDefault()
              const asked = question.trim() || DEFAULT_QUESTION
              const gen = ++askGen.current
              setAsking(true)
              setAskError(null)
              postAssess(asked, region).then((response) => {
                if (gen !== askGen.current) return
                setAsking(false)
                if (!response.ok || !response.result) {
                  setAssessment(null)
                  setAskError(response.detail ?? 'the desk did not answer')
                  return
                }
                setAssessment(response.result)
              })
            }}
          >
            <textarea
              id="desk-question"
              name="question"
              rows={3}
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              disabled={asking}
              aria-label="Question for the desk"
            />
            <button type="submit" className="ink-button" disabled={asking}>
              {asking ? 'Reading…' : 'Ask'}
            </button>
          </form>
        )}
        {askError && <Empty>{askError}</Empty>}
        {assessment && (
          <>
            <p className="desk-assessment">{assessment.assessment}</p>
            <p className="figure-note">
              An argument, not a measurement. The figures on this page are the
              source; a number that is not here was invented.
            </p>
            <Disclosure label="what the desk was handed">
              <p className="figure-note">
                The same briefing already printed above: wire departures, the
                region map, packed market cells, globe coverage, and the frozen
                forecasts named below. The method string is the rule, not a
                second set of figures.
              </p>
              {frozenQuestions.length > 0 && (
                <ul className="mt-3 space-y-1">
                  {frozenQuestions.map((row) => (
                    <li key={row.node_id ?? row.question} className="figure-note" style={{ marginTop: 0 }}>
                      {row.question}
                    </li>
                  ))}
                </ul>
              )}
              {assessment.method && (
                <p className="figure-note mt-3">{assessment.method}</p>
              )}
            </Disclosure>
          </>
        )}
      </Beat>
    </article>
  )
}
