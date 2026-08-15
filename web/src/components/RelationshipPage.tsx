// The Relationship page — the hero surface. One relationship (US <> Russia),
// one spine: where it's been (events + what markets did), now, where it's
// going (what has followed before + the regional outlook). Plain language
// throughout; the machine's names never reach the surface.

import { useEffect, useMemo, useState } from 'react'

import {
  exploreGame,
  getDyadSeries,
  getDyadTimeline,
  getForecast,
  getForecasts,
  getPanelDyads,
  getPrecedent,
  lastFailureFor,
} from '../api'
import {
  marketMove,
  relationshipName,
  tensionLevel,
  tensionSentence,
  tensionTrend,
  yearOf,
} from '../lib/language'
import { toggle as toggleWatch, useIsWatched } from '../lib/watchlist'
import { useRegionLabel } from '../regions'
import type {
  DyadSeries,
  DyadTimeline,
  ForecastDetail,
  GameExplore,
  PanelDyad,
  Precedent,
} from '../types'
import { BoxRow, Empty, Fan, LineBand } from './charts/Charts'
import type { Point } from './charts/Charts'
import { BandFan, Step } from './GameViz'

function dyadFromHash(): string {
  const q = window.location.hash.split('?')[1]
  if (!q) return ''
  return new URLSearchParams(q).get('dyad') ?? ''
}

function prettyScenario(name: string): string {
  return name
    .split(':')[0]
    .replace(/_/g, ' ')
    .replace(/^\w/, (c) => c.toUpperCase())
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="mt-10 pt-6 border-t" style={{ borderColor: 'var(--rule-strong)' }}>
      <div className="kicker mb-3">{title}</div>
      {children}
    </section>
  )
}

export default function RelationshipPage({
  region,
}: {
  region: string
  onNavigate: (r: string) => void
}) {
  const regionLabel = useRegionLabel(region)
  const [dyads, setDyads] = useState<PanelDyad[] | null>(null)
  const [selected, setSelected] = useState('')
  const [series, setSeries] = useState<DyadSeries | null | undefined>(undefined)
  const [precedent, setPrecedent] = useState<Precedent | null | undefined>(undefined)
  const [timeline, setTimeline] = useState<DyadTimeline | null | undefined>(undefined)
  const [game, setGame] = useState<GameExplore | null | undefined>(undefined)
  const [outlook, setOutlook] = useState<ForecastDetail | null | undefined>(undefined)

  useEffect(() => {
    setDyads(null)
    setSelected('')
    getPanelDyads(region).then((r) => {
      const rows = r?.rows ?? []
      setDyads(rows)
      const linked = dyadFromHash()
      if (linked && rows.some((d) => d.dyad_id === linked)) setSelected(linked)
      else if (rows.length) setSelected(rows[0].dyad_id)
    })
  }, [region])

  useEffect(() => {
    if (!selected) return
    let live = true
    setSeries(undefined)
    setPrecedent(undefined)
    setTimeline(undefined)
    setGame(undefined)
    getDyadSeries(selected, region).then((r) => live && setSeries(r))
    getPrecedent(selected, region).then((r) => live && setPrecedent(r))
    getDyadTimeline(selected).then((r) => live && setTimeline(r))
    exploreGame(region, selected).then((r) => live && setGame(r))
    return () => {
      live = false
    }
  }, [selected, region])

  useEffect(() => {
    let live = true
    setOutlook(undefined)
    getForecasts(region).then((r) => {
      if (!live) return
      const rows = r?.rows ?? []
      const near = rows.find((f) => f.mode === 'near_term') ?? rows[0]
      if (!near) {
        setOutlook(null)
        return
      }
      getForecast(near.node_id).then((d) => live && setOutlook(d))
    })
    return () => {
      live = false
    }
  }, [region])

  const selectedDyad = useMemo(
    () => dyads?.find((d) => d.dyad_id === selected) ?? null,
    [dyads, selected],
  )
  const watched = useIsWatched(selected)

  const name = relationshipName(
    selectedDyad?.dyad_name ?? (series || undefined)?.dyad_name,
    selected || 'a relationship',
  )

  // Header read from the trajectory.
  const rows = series ? series.rows : []
  const level = rows.length ? tensionLevel(rows[rows.length - 1].intensity, series?.peak ?? 0) : null
  const trend = tensionTrend(rows)
  const linePoints: Point[] = rows.map((r) => ({ x: r.q, y: r.intensity }))

  // Measured market moves, as percent, from this relationship's flare-ups.
  const marketRows = (precedent && precedent.markets) || []
  const pctRow = (m: Precedent['markets'][number]) => ({
    market_name: m.market_name,
    n: m.n,
    min: m.min * 100,
    p25: m.p25 * 100,
    median: m.median * 100,
    p75: m.p75 * 100,
    max: m.max * 100,
  })
  const domain: [number, number] = marketRows.length
    ? [
        Math.min(...marketRows.map((m) => m.min * 100)),
        Math.max(...marketRows.map((m) => m.max * 100)),
      ]
    : [-1, 1]

  const scenarios = (outlook?.scenarios ?? [])
    .filter((s) => s.likelihood != null)
    .sort((a, b) => (b.likelihood ?? 0) - (a.likelihood ?? 0))
    .slice(0, 3)

  // Story pieces — the one-paragraph synthesis at the top: the strongest
  // measured market move (most-evidenced first), and where the game sees the
  // balance of play heading (expected band rising over the horizon).
  const topMarket = marketRows.length ? marketRows[0] : null
  const forwardRising =
    game && game.marginal.length >= 2
      ? game.marginal[game.marginal.length - 1].expected_band -
          game.marginal[0].expected_band >
        0.05
      : null

  const seriesFailed = lastFailureFor('/api/panel/dyads')

  return (
    <div className="max-w-4xl mx-auto px-6 py-10">
      {/* Header */}
      <div className="flex items-baseline justify-between gap-4 flex-wrap">
        <div className="kicker">Relationship · {regionLabel.toUpperCase()}</div>
        {dyads && dyads.length > 0 && (
          <select
            className="region-select mono text-xs"
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
          >
            {dyads.map((d) => (
              <option key={d.dyad_id} value={d.dyad_id}>
                {d.dyad_name}
              </option>
            ))}
          </select>
        )}
      </div>

      {dyads === null ? (
        <Empty note="reading the archive…" />
      ) : seriesFailed && seriesFailed.status !== 404 ? (
        <Empty note="couldn't reach the archive — it may still be starting up" />
      ) : !dyads.length ? (
        <Empty note="no relationships in this region yet" />
      ) : (
        <>
          <div className="mt-3 flex items-start justify-between gap-4">
            <div>
              <h1 className="text-2xl" style={{ letterSpacing: '-0.01em' }}>
                {name}
              </h1>
              {level && (
                <p className="mt-1" style={{ color: trend === 'rising' ? 'var(--alert)' : 'var(--muted)' }}>
                  {tensionSentence(level, trend)}
                  {series ? ` · ${yearOf(series.span[0])}–${yearOf(series.span[1])}` : ''}
                </p>
              )}
            </div>
            {selected && (
              <button
                className="article-link whitespace-nowrap"
                onClick={() =>
                  toggleWatch({ dyadId: selected, name, region, addedAt: Date.now() })
                }
                aria-pressed={watched}
              >
                {watched ? '★ Following' : '☆ Follow'}
              </button>
            )}
          </div>

          {/* The story, in one paragraph */}
          {level && (topMarket || (game && forwardRising !== null)) && (
            <p className="mt-5 text-base leading-relaxed" style={{ maxWidth: '44rem' }}>
              {topMarket && (
                <>
                  When {name} has flared before,{' '}
                  <span className="whitespace-nowrap">
                    {topMarket.market_name}{' '}
                    <span
                      style={{ color: topMarket.median >= 0 ? 'var(--accent)' : 'var(--alert)' }}
                    >
                      {marketMove(topMarket.median)}
                    </span>
                  </span>{' '}
                  was the typical move across {topMarket.n} comparable episodes.{' '}
                </>
              )}
              {game &&
                forwardRising !== null &&
                (forwardRising
                  ? 'Looking forward, the solved game sees the balance of play building toward escalation.'
                  : 'Looking forward, the solved game sees pressure easing.')}
            </p>
          )}

          {/* Where it's been */}
          <Section title="Where it's been">
            {series === undefined ? (
              <Empty note="reading the archive…" />
            ) : !series || !series.rows.length ? (
              <Empty note="too little history to chart" />
            ) : (
              <LineBand points={linePoints} label="Tension over time" color="var(--alert)" />
            )}

            <div className="kicker mt-6 mb-2">What markets did after its flare-ups</div>
            {precedent === undefined ? (
              <Empty note="reading the archive…" />
            ) : !precedent ? (
              <Empty note="not enough comparable history to measure" />
            ) : !marketRows.length ? (
              <Empty note={precedent.markets_note ?? 'no market moves measured yet in comparable periods'} />
            ) : (
              <div className="space-y-3">
                {marketRows.map((m) => (
                  <div key={m.market_id}>
                    <div className="text-sm">
                      {m.market_name} —{' '}
                      <span style={{ color: m.median >= 0 ? 'var(--accent)' : 'var(--alert)' }}>
                        typically {marketMove(m.median)}
                      </span>{' '}
                      <span style={{ color: 'var(--muted)' }}>across {m.n} comparable moves</span>
                    </div>
                    <BoxRow row={pctRow(m)} domain={domain} />
                  </div>
                ))}
                <p className="mono text-[10px]" style={{ color: 'var(--muted)' }}>
                  measured market moves after comparable flare-ups — the range, not a single number
                </p>
              </div>
            )}

            {timeline && timeline.events.length > 0 && (
              <div className="mt-6">
                <div className="kicker mb-2">Recent events, and what markets did</div>
                <div className="space-y-1">
                  {timeline.events.slice(0, 8).map((ev) => (
                    <div key={ev.event_id} className="flex items-baseline gap-3 text-sm">
                      <span className="mono text-xs shrink-0" style={{ color: 'var(--muted)' }}>
                        {ev.date}
                      </span>
                      <span className="flex flex-wrap gap-x-4 gap-y-0.5">
                        {ev.markets.slice(0, 3).map((m) => (
                          <span key={m.market_id}>
                            {m.market_name}{' '}
                            <span style={{ color: m.car >= 0 ? 'var(--accent)' : 'var(--alert)' }}>
                              {marketMove(m.car)}
                            </span>
                          </span>
                        ))}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </Section>

          {/* Now */}
          {level && (
            <Section title="Now">
              <p>
                {name} sits at <strong>{level}</strong> tension, {trend}.{' '}
                {series && series.rows.length
                  ? `Last read ${yearOf(series.span[1])}.`
                  : ''}
              </p>
            </Section>
          )}

          {/* Where it's going */}
          <Section title="Where it's going">
            <div className="kicker mb-2" style={{ color: 'var(--muted)' }}>
              What has followed, historically
            </div>
            {precedent === undefined ? (
              <Empty note="reading the archive…" />
            ) : !precedent || !precedent.fan.length ? (
              <Empty note="no comparable episodes to project from yet" />
            ) : (
              <Fan rows={precedent.fan} label="Tension over the quarters that followed" />
            )}

            <div className="kicker mt-6 mb-2" style={{ color: 'var(--muted)' }}>
              Regional outlook · {regionLabel}
            </div>
            {outlook === undefined ? (
              <Empty note="reading the outlook…" />
            ) : !outlook || !scenarios.length ? (
              <Empty note="no frozen outlook for this region yet" />
            ) : (
              <div className="space-y-3">
                {scenarios.map((s) => (
                  <div key={s.scenario_name} className="boxed">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="text-sm">{prettyScenario(s.scenario_name)}</span>
                      <span className="mono figure text-sm" style={{ color: 'var(--muted)' }}>
                        {Math.round((s.likelihood ?? 0) * 100)}%
                      </span>
                    </div>
                    {s.market_implication && (
                      <p className="mt-1 text-sm" style={{ color: 'var(--muted)' }}>
                        {s.market_implication}
                      </p>
                    )}
                  </div>
                ))}
                {outlook.boundary_statement && (
                  <p className="mono text-[10px]" style={{ color: 'var(--muted)' }}>
                    {outlook.boundary_statement}
                  </p>
                )}
              </div>
            )}
          </Section>

          {/* How it plays out — the game toward equilibrium */}
          <Section title="How it plays out">
            <p className="text-sm mb-4" style={{ color: 'var(--muted)' }}>
              We solve the escalation game each side is playing — their incentives, and
              what they currently believe about each other — and let it run forward. This
              is where the balance of play settles, and how the odds of escalation spread
              over the coming quarters.
            </p>
            {game === undefined ? (
              <Empty note="solving the game…" />
            ) : !game ? (
              <Empty note="couldn't solve the game for this relationship yet" />
            ) : (
              <>
                <BandFan
                  marginal={game.marginal}
                  bands={game.marginal[0]?.distribution.length ?? 5}
                />
                {game.paths[0] && game.paths[0].steps.length > 0 && (
                  <div className="mt-5">
                    <div className="kicker mb-2" style={{ color: 'var(--muted)' }}>
                      The most likely sequence, priced to markets
                    </div>
                    <div className="space-y-3">
                      {game.paths[0].steps.map((s) => (
                        <Step key={s.period} step={s} />
                      ))}
                    </div>
                  </div>
                )}
                <p className="mono text-[10px] mt-4" style={{ color: 'var(--muted)' }}>
                  {game.boundary_statement}
                </p>
              </>
            )}
          </Section>

          {/* Track record */}
          {outlook && (outlook.brier_score != null || outlook.retrodiction) && (
            <Section title="Track record">
              <p className="text-sm" style={{ color: 'var(--muted)' }}>
                {outlook.brier_score != null
                  ? `Near-term accuracy score: ${outlook.brier_score.toFixed(3)} (lower is better).`
                  : ''}
                {outlook.retrodiction
                  ? ` Of flagged periods, ${Math.round(
                      (outlook.retrodiction.hit_rate ?? 0) * 100,
                    )}% ran hot against a ${Math.round(
                      (outlook.retrodiction.base_rate ?? 0) * 100,
                    )}% base rate.`
                  : ''}
              </p>
            </Section>
          )}
        </>
      )}
    </div>
  )
}
