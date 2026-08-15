// The Relationship page — the product's hero, rebuilt (2026-08-15).
//
// One relationship, answer-first, in the broadsheet language:
//   THE CALL      where tension is heading, the forecasted next move and its
//                 TYPE, and the market movement associated with it — the whole
//                 point of the machine, on one plate.
//   WHERE IT'S BEEN   the relationship's market-moving events, what markets did.
//   TRACK RECORD  how the region's calls have scored.
//
// Plain language throughout (lib/language); the solver's vocabulary — bands,
// quads, joint actions — never reaches the reader. Measured, never asserted:
// every market figure is a median of real abnormal returns, never a model
// price, and an absent number is an honest silence, never a zero.

import { useEffect, useMemo, useState } from 'react'

import {
  exploreGame,
  getBacktest,
  getDyadSeries,
  getDyadTimeline,
  getForecast,
  getForecasts,
  getPanelDyads,
  lastFailureFor,
} from '../api'
import {
  bandLabel,
  jointAction,
  relationshipName,
  tensionLevel,
  tensionSentence,
  tensionTrend,
  yearOf,
} from '../lib/language'
import { toggle as toggleWatch, useIsWatched } from '../lib/watchlist'
import { useRegionLabel } from '../regions'
import type {
  BacktestLedger,
  DyadSeries,
  DyadTimeline,
  ForecastDetail,
  GameExplore,
  PanelDyad,
  SequenceStep,
} from '../types'
import { LineBand } from './charts/Charts'
import type { Point } from './charts/Charts'
import { Beat, Disclosure, Empty, MoveRow, StatLine, StoryHead, TensionBadge } from '../ui'

function dyadFromHash(): string {
  const q = window.location.hash.split('?')[1]
  if (!q) return ''
  return new URLSearchParams(q).get('dyad') ?? ''
}

/** The predicted step's markets, as signed percent moves worth showing — the
 *  measured median abnormal return for comparable events, thin cells dropped. */
function stepMoves(step: SequenceStep | undefined) {
  if (!step) return []
  return step.market
    .filter((m) => !m.thin && m.n > 0)
    .map((m) => ({ name: m.market_name, pct: m.median * 100, n: m.n }))
    .sort((a, b) => Math.abs(b.pct) - Math.abs(a.pct))
    .slice(0, 6)
}

export default function RelationshipPage({ region }: { region: string; onNavigate: (r: string) => void }) {
  const regionLabel = useRegionLabel(region)
  const [dyads, setDyads] = useState<PanelDyad[] | null>(null)
  const [selected, setSelected] = useState('')
  const [linkNote, setLinkNote] = useState<string | null>(null)
  const [series, setSeries] = useState<DyadSeries | null | undefined>(undefined)
  const [timeline, setTimeline] = useState<DyadTimeline | null | undefined>(undefined)
  const [game, setGame] = useState<GameExplore | null | undefined>(undefined)
  const [outlook, setOutlook] = useState<ForecastDetail | null | undefined>(undefined)
  const [backtest, setBacktest] = useState<BacktestLedger | null | undefined>(undefined)

  useEffect(() => {
    let live = true
    setDyads(null)
    setSelected('')
    setSeries(undefined)
    setTimeline(undefined)
    setGame(undefined)
    getPanelDyads(region).then((r) => {
      if (!live) return
      const rows = r?.rows ?? []
      setDyads(rows)
      const linked = dyadFromHash()
      if (linked && rows.some((d) => d.dyad_id === linked)) {
        setSelected(linked)
        setLinkNote(null)
      } else {
        if (rows.length) setSelected(rows[0].dyad_id)
        setLinkNote(
          linked && rows.length
            ? 'The relationship this link named is not tracked in this region — showing the most active pair instead.'
            : null,
        )
      }
    })
    return () => {
      live = false
    }
  }, [region])

  // A hash naming a different dyad while mounted must change what is shown.
  useEffect(() => {
    const onHash = () => {
      const linked = dyadFromHash()
      if (linked) setSelected((cur) => (linked !== cur ? linked : cur))
    }
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    if (!selected) return
    let live = true
    setSeries(undefined)
    setTimeline(undefined)
    setGame(undefined)
    getDyadSeries(selected, region).then((r) => live && setSeries(r))
    getDyadTimeline(selected).then((r) => live && setTimeline(r))
    exploreGame(region, selected).then((r) => live && setGame(r))
    return () => {
      live = false
    }
  }, [selected, region])

  useEffect(() => {
    let live = true
    setOutlook(undefined)
    setBacktest(undefined)
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
    getBacktest(region).then((r) => live && setBacktest(r))
    return () => {
      live = false
    }
  }, [region])

  const selectedDyad = useMemo(() => dyads?.find((d) => d.dyad_id === selected) ?? null, [dyads, selected])
  const watched = useIsWatched(selected)
  const name = relationshipName(selectedDyad?.dyad_name ?? (series || undefined)?.dyad_name, selected || 'a relationship')

  const rows = series ? series.rows : []
  const level = rows.length ? tensionLevel(rows[rows.length - 1].intensity, series?.peak ?? 0) : null
  const trend = tensionTrend(rows)
  const linePoints: Point[] = rows.map((r) => ({ x: r.q, y: r.intensity }))

  // ── THE CALL: where tension heads, the forecasted move + type, its markets ──
  const bands = game && game.marginal[0] ? game.marginal[0].distribution.length : 5
  const marginal = game?.marginal ?? []
  const expectedStart = marginal[0]?.expected_band ?? null
  const expectedEnd = marginal.length ? marginal[marginal.length - 1].expected_band : null
  const drift = expectedStart != null && expectedEnd != null ? expectedEnd - expectedStart : null
  const forwardTrend: 'rising' | 'falling' | 'steady' =
    drift == null ? 'steady' : drift > 0.1 ? 'rising' : drift < -0.1 ? 'falling' : 'steady'
  const topPath = game?.paths?.[0]
  const nextStep = topPath?.steps?.[0]
  const nextType = nextStep ? bandLabel(nextStep.intensity_band, bands) : null
  const nextMove = nextStep ? jointAction(nextStep.action_a, nextStep.action_b) : null
  // The badge tracks where the relationship is HEADING (the trajectory
  // endpoint), so it agrees with the lede rather than the immediate step —
  // a near-term escalation inside a medium-term easing read as a contradiction.
  const headingType = expectedEnd != null ? bandLabel(Math.round(expectedEnd), bands) : nextType
  const moves = stepMoves(nextStep)
  const horizonQuarters = marginal.length

  const callLede = (() => {
    if (game === undefined) return null
    if (!game || !marginal.length) return null
    const dir =
      forwardTrend === 'rising'
        ? 'building toward escalation'
        : forwardTrend === 'falling'
          ? 'easing back toward calm'
          : 'holding roughly where it is'
    const span = horizonQuarters ? `Over the next ${horizonQuarters} quarters, ` : ''
    return `${span}the balance of play is ${dir}.`
  })()

  // ── Track record (region-wide) ──
  const retro = outlook?.retrodiction
  const hasRetro = retro && retro.hit_rate != null && retro.base_rate != null

  const dyadsFailed = lastFailureFor('/api/panel/dyads', { exact: true })

  return (
    <div className="reading-column">
      {linkNote && (
        <p className="mono text-[11px] mb-3" style={{ color: 'var(--alert)' }}>
          {linkNote}
        </p>
      )}

      <StoryHead
        kicker={`Relationship · ${regionLabel.toUpperCase()}`}
        title={name}
        standfirst={
          level ? (
            <span>
              {tensionSentence(level, trend)}
              {series ? ` · ${yearOf(series.span[0])}–${yearOf(series.span[1])}` : ''}
            </span>
          ) : dyads === null ? (
            'Reading the archive…'
          ) : undefined
        }
        action={
          <div className="flex flex-col items-end gap-2">
            {dyads && dyads.length > 0 && (
              <select
                className="region-select mono text-xs"
                value={selected}
                onChange={(e) => {
                  setSelected(e.target.value)
                  setLinkNote(null)
                }}
                aria-label="Choose a relationship"
              >
                {dyads.map((d) => (
                  <option key={d.dyad_id} value={d.dyad_id}>
                    {relationshipName(d.dyad_name, d.dyad_id)}
                  </option>
                ))}
              </select>
            )}
            {selected && (
              <button
                className="article-link whitespace-nowrap"
                onClick={() => toggleWatch({ dyadId: selected, name, region, addedAt: Date.now() })}
                aria-pressed={watched}
              >
                {watched ? '★ Following' : '☆ Follow'}
              </button>
            )}
          </div>
        }
      />

      {dyads === null ? (
        <Empty>reading the archive…</Empty>
      ) : dyadsFailed && dyadsFailed.status !== 404 ? (
        <Empty>couldn’t reach the archive — it may still be starting up</Empty>
      ) : !dyads.length ? (
        <Empty>no relationships in this region yet</Empty>
      ) : (
        <>
          {/* ── THE CALL — the forecast hero ─────────────────────────────── */}
          <section className={`call mt-8 ${forwardTrend === 'rising' ? 'call--rising' : ''}`}>
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <span className="kicker">The call · where it’s going</span>
              {headingType && <TensionBadge label={headingType} trend={forwardTrend} />}
            </div>

            {callLede ? (
              <p className="call-lede">{callLede}</p>
            ) : game === undefined ? (
              <p className="call-lede" style={{ color: 'var(--muted)' }}>
                solving the game for this relationship…
              </p>
            ) : (
              <p className="call-lede" style={{ color: 'var(--muted)' }}>
                Not enough comparable play to solve this relationship’s game yet.
              </p>
            )}

            {nextMove && (
              <p className="mt-3 text-base">
                The most likely next move: <strong>{nextMove}</strong>
                {nextType ? <> — a <strong>{nextType}</strong> turn.</> : '.'}
              </p>
            )}

            {/* THE FEATURE: the market movement ASSOCIATED with that move. */}
            {moves.length > 0 ? (
              <div className="mt-4">
                <div className="kicker mb-1">If it plays out, markets have moved</div>
                {moves.map((m) => (
                  <MoveRow key={m.name} name={m.name} pct={m.pct} sub={`${m.n} comparable`} />
                ))}
                <p className="mt-2 text-xs" style={{ color: 'var(--muted)' }}>
                  Median abnormal return across comparable past moves in this regime — measured, not modelled.
                </p>
              </div>
            ) : game && marginal.length ? (
              <p className="mt-4 text-sm" style={{ color: 'var(--muted)' }}>
                No comparable market moves are measured for the predicted turn yet.
              </p>
            ) : null}

            {/* Show me why — the trajectory fan behind the one-line call. */}
            {marginal.length > 1 && (
              <Disclosure label="show me the trajectory">
                <TrajectoryStrip marginal={marginal} bands={bands} />
                {game?.boundary_statement && (
                  <p className="mono text-[10px] mt-3" style={{ color: 'var(--muted)' }}>
                    {game.boundary_statement}
                  </p>
                )}
              </Disclosure>
            )}
          </section>

          {/* ── NOW — the tension trajectory ─────────────────────────────── */}
          <Beat n={1} title="Where it stands" aria-label="now">
            {series === undefined ? (
              <Empty>reading the trajectory…</Empty>
            ) : series && rows.length ? (
              <>
                <StatLine
                  items={[
                    { label: 'Tension now', value: level ?? '—' },
                    { label: 'Trend', value: <span style={{ textTransform: 'capitalize' }}>{trend}</span> },
                    { label: 'Peak', value: (series.peak ?? 0).toFixed(1) },
                    { label: 'Active quarters', value: series.active_quarters },
                  ]}
                />
                <div className="mt-4 scroll-x">
                  <LineBand points={linePoints} width={720} height={140} />
                </div>
              </>
            ) : (
              <Empty>no quarterly trajectory for this relationship yet</Empty>
            )}
          </Beat>

          {/* ── WHERE IT'S BEEN — the event timeline ─────────────────────── */}
          <Beat
            n={2}
            title="Where it’s been"
            aside={timeline && timeline.total ? `${timeline.total} market-moving events` : undefined}
          >
            {timeline === undefined ? (
              <Empty>reading the record…</Empty>
            ) : timeline && timeline.events.length ? (
              <div>
                {timeline.events.slice(0, 12).map((ev) => (
                  <div key={ev.event_id} className="event-row">
                    <div className="flex items-baseline justify-between gap-3">
                      <span className="mono text-xs" style={{ color: 'var(--muted)' }}>
                        {ev.date}
                      </span>
                    </div>
                    {ev.markets.length ? (
                      <div className="mt-1">
                        {ev.markets.slice(0, 4).map((m) => (
                          <MoveRow key={m.market_id} name={m.market_name} pct={m.car * 100} />
                        ))}
                      </div>
                    ) : (
                      <p className="text-xs mt-1" style={{ color: 'var(--muted)' }}>
                        no measured market move
                      </p>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <Empty>no market-moving events measured for this relationship yet</Empty>
            )}
          </Beat>

          {/* ── TRACK RECORD (region-wide) ───────────────────────────────── */}
          {(hasRetro || outlook?.brier_score != null || backtest?.summary) && (
            <Beat n={3} title="Track record" aside={`${regionLabel} calls`}>
              {hasRetro && retro && (
                <p className="text-sm">
                  When the system flagged a period as likely to run hot, it did{' '}
                  <strong>{Math.round(retro.hit_rate! * 100)}%</strong> of the time — versus{' '}
                  {Math.round(retro.base_rate! * 100)}% for an average period.
                </p>
              )}

              {/* The paper backtest: the region's frozen calls marked to market
                  on $1M notional, walked forward quarter by quarter. Measured
                  outcomes only — the boundary the whole platform holds to. */}
              {backtest?.summary && (
                <div className="mt-4">
                  <div className="kicker mb-1">Paper backtest · ${(backtest.summary.notional_usd / 1e6).toFixed(0)}M notional</div>
                  <StatLine
                    items={[
                      {
                        label: 'Total return',
                        value: (
                          <span style={{ color: backtest.summary.total_return >= 0 ? 'var(--accent)' : 'var(--alert)' }}>
                            {backtest.summary.total_return >= 0 ? '+' : ''}
                            {(backtest.summary.total_return * 100).toFixed(1)}%
                          </span>
                        ),
                      },
                      { label: 'Hit rate', value: `${Math.round(backtest.summary.hit_rate * 100)}%` },
                      { label: 'Quarters', value: backtest.summary.quarters_traded },
                      { label: 'Max drawdown', value: `${(backtest.summary.max_drawdown * 100).toFixed(1)}%` },
                    ]}
                  />
                  {backtest.computed_at && (
                    <p className="mono text-[10px] mt-1" style={{ color: 'var(--muted)' }}>
                      walk-forward, computed {backtest.computed_at.slice(0, 10)}
                    </p>
                  )}
                </div>
              )}

              {outlook?.brier_score != null && (
                <p className="mono text-[10px] mt-3" style={{ color: 'var(--muted)' }}>
                  near-term accuracy score {outlook.brier_score.toFixed(3)} (lower is better)
                </p>
              )}
            </Beat>
          )}
        </>
      )}
    </div>
  )
}

/** A compact per-period band-mass strip — the forecast fan, small, behind the
 *  one-line call. Rows are periods; each cell's ink weight is its probability
 *  mass; the alert hue climbs with the band (escalation is the alert
 *  direction). */
function TrajectoryStrip({
  marginal,
  bands,
}: {
  marginal: GameExplore['marginal']
  bands: number
}) {
  return (
    <div className="scroll-x">
      <table className="text-xs" style={{ borderCollapse: 'collapse' }}>
        <tbody>
          {marginal.map((m) => (
            <tr key={m.period}>
              <td className="mono pr-2" style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                +{m.period + 1}Q
              </td>
              {Array.from({ length: bands }, (_, b) => {
                const p = m.distribution[b] ?? 0
                const share = bands > 1 ? b / (bands - 1) : 0
                return (
                  <td key={b} style={{ padding: '2px' }} title={`${bandLabel(b, bands)} · ${Math.round(p * 100)}%`}>
                    <span
                      style={{
                        display: 'block',
                        width: 26,
                        height: 12,
                        background: share > 0.5 ? 'var(--alert)' : 'var(--accent)',
                        opacity: Math.max(0.06, Math.min(1, p * 2.2)),
                      }}
                    />
                  </td>
                )
              })}
              <td className="mono pl-2" style={{ color: 'var(--muted)', whiteSpace: 'nowrap' }}>
                {bandLabel(Math.round(m.expected_band), bands)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
