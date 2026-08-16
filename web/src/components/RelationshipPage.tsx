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
  getCalibration,
  getDyadSeries,
  getDyadTimeline,
  getForecast,
  getForecasts,
  getPanelDyads,
  lastFailureFor,
  getPrecedent,
  getEventImpact,
  getImpactCoverage,
  getDyadSolution,
} from '../api'
import {
  bandLabel,
  postureNote,
  standingLabel,
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
  CalibrationWalk,
  BacktestLedger,
  DyadSeries,
  DyadTimeline,
  ForecastDetail,
  GameExplore,
  PanelDyad,
  SequenceStep,
  Precedent,
  EventImpact,
  ImpactCoverage,
  TimelineEvent,
  DyadSolution,
} from '../types'
import { BoxRow, Fan, LineBand } from './charts/Charts'
import type { Point } from './charts/Charts'
import { Bars, MultiLine, pct } from './charts/Kit'
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

export default function RelationshipPage({ region, onNavigate }: { region: string; onNavigate: (r: string) => void }) {
  const regionLabel = useRegionLabel(region)
  const [dyads, setDyads] = useState<PanelDyad[] | null>(null)
  const [selected, setSelected] = useState('')
  const [linkNote, setLinkNote] = useState<string | null>(null)
  const [series, setSeries] = useState<DyadSeries | null | undefined>(undefined)
  const [timeline, setTimeline] = useState<DyadTimeline | null | undefined>(undefined)
  const [game, setGame] = useState<GameExplore | null | undefined>(undefined)
  const [outlook, setOutlook] = useState<ForecastDetail | null | undefined>(undefined)
  const [backtest, setBacktest] = useState<BacktestLedger | null | undefined>(undefined)
  const [calibration, setCalibration] = useState<CalibrationWalk | null | undefined>(undefined)
  const [precedent, setPrecedent] = useState<Precedent | null | undefined>(undefined)
  const [coverage, setCoverage] = useState<ImpactCoverage | null | undefined>(undefined)
  const [solution, setSolution] = useState<DyadSolution | null | undefined>(undefined)
  const [modelTrajectory, setModelTrajectory] = useState<Array<{ q: string; deviation: number; lo?: number; hi?: number }> | null>(null)

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
    setPrecedent(undefined)
    setSolution(undefined)
    getDyadSeries(selected, region).then((r) => live && setSeries(r))
    getDyadTimeline(selected).then((r) => live && setTimeline(r))
    exploreGame(region, selected).then((r) => live && setGame(r))
    getPrecedent(selected, region).then((r) => live && setPrecedent(r))
    getDyadSolution(region, selected).then((r) => live && setSolution(r))
    return () => {
      live = false
    }
  }, [selected, region])

  useEffect(() => {
    let live = true
    setCoverage(undefined)
    getImpactCoverage(region).then((r) => live && setCoverage(r))
    return () => {
      live = false
    }
  }, [region])

  useEffect(() => {
    let live = true
    setOutlook(undefined)
    setBacktest(undefined)
    getForecasts(region).then((r) => {
      if (!live) return
      const rows = r?.rows ?? []
      const near = rows.find((f) => f.mode === 'near_term') ?? rows[0]
      const model = rows.find((f) => f.mode === 'model')
      if (model) {
        getForecast(model.node_id).then((d) => {
          if (!live) return
          const t = d?.frozen_inputs?.trajectories?.find((x) => x.dyad_id === selected)
          setModelTrajectory(t ? t.path.map((p) => ({ q: p.date, deviation: p.deviation, lo: p.lo, hi: p.hi })) : null)
        })
      }
      if (!near) {
        setOutlook(null)
        return
      }
      getForecast(near.node_id).then((d) => live && setOutlook(d))
    })
    getBacktest(region).then((r) => live && setBacktest(r))
    getCalibration(region).then((r) => live && setCalibration(r))
    return () => {
      live = false
    }
  }, [region, selected])

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
  const bandNames = solution?.band_labels
  // ONE HIERARCHY FOR THE PAGE (2026-08-15): what the pair IS comes from the
  // graph's declared relations, how its record READS lately from the coercive
  // share of its coded events, and where it is HEADING from the solved game's
  // departure bands. Three questions, three sources, one vocabulary each —
  // the page used to run four ladders at once and call a declared rivalry
  // "friendly" beside a "severe" tension reading.
  const standing = standingLabel(solution?.opening.standing)
  const posture = postureNote(solution?.opening.posture)
  const nextType = nextStep ? bandLabel(nextStep.intensity_band, bands, bandNames) : null
  const nextMove = nextStep ? jointAction(nextStep.action_a, nextStep.action_b) : null
  // The badge tracks where the relationship is HEADING (the trajectory
  // endpoint), so it agrees with the lede rather than the immediate step —
  // a near-term escalation inside a medium-term easing read as a contradiction.
  const headingType = expectedEnd != null ? bandLabel(Math.round(expectedEnd), bands, bandNames) : nextType
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
              {standing ? <><strong>{standing}</strong> · </> : null}
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

            {solution && (
              <div className="mt-3 text-sm">
                <p>
                  {standing
                    ? <>The archive declares this pair a <strong>{standing}</strong>.</>
                    : <>The archive declares no standing relation for this pair.</>}
                  {posture ? <> Its coded record lately: <strong>{posture}</strong>.</> : null}
                  {' '}The solved game puts <strong>{pct(solution.concepts[solution.primary_solver]?.sharp_departure_probability ?? 0, 0)}</strong> on a
                  sharper-than-usual departure from its own baseline within {solution.horizon} quarters
                  {solution.concepts.lp && solution.primary_solver !== 'lp' ? ` (LP benchmark ${pct(solution.concepts.lp.sharp_departure_probability, 0)})` : ''}
                  {solution.opening.tilt ? `, with the learned layer tilting the kernel by η ${solution.opening.tilt.eta >= 0 ? '+' : ''}${solution.opening.tilt.eta.toFixed(3)}` : ', untilted by the learned layer'}.
                </p>
                <p className="mt-2">
                  <button className="btn" onClick={() => onNavigate(`/games?dyad=${encodeURIComponent(selected)}&region=${encodeURIComponent(region)}`)}>
                    Open the solved game →
                  </button>
                </p>
              </div>
            )}

            {/* Show me why — the trajectory fan behind the one-line call. */}
            {marginal.length > 1 && (
              <Disclosure label="show me the trajectory">
                <TrajectoryStrip marginal={marginal} bands={bands} bandNames={bandNames} />
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
                {modelTrajectory && modelTrajectory.length > 0 && (
                  <div className="mt-4">
                    <div className="kicker mb-1">The learned layer's read — deviation from this pair's own baseline, next {modelTrajectory.length} quarters</div>
                    <MultiLine
                      xLabels={modelTrajectory.map((m) => m.q)}
                      yMax={Math.max(...modelTrajectory.map((m) => Math.abs(m.hi ?? m.deviation)), 1)}
                      format={(v) => v.toFixed(1)}
                      series={[
                        { name: 'predicted deviation', values: modelTrajectory.map((m) => Math.max(0, m.deviation)), color: 'var(--alert)' },
                        { name: 'upper band', values: modelTrajectory.map((m) => Math.max(0, m.hi ?? m.deviation)), color: 'var(--muted)', dash: '3 3' },
                      ]}
                    />
                    <p className="mono text-[10px] mt-1" style={{ color: 'var(--muted)' }}>
                      gated within-dyad ridge (models/intensity.json); the model's claim is magnitude, persistence keeps the ordering.
                    </p>
                  </div>
                )}
                {solution?.concepts.lp && (
                  <div className="mt-4">
                    <div className="kicker mb-1">Escalation propensity by intensity band and type (LP, at the opening capability)</div>
                    <MultiLine
                      xLabels={solution.band_labels}
                      series={[
                        { name: 'resolute', values: solution.concepts.lp.escalation_propensity.resolute ?? [], color: 'var(--alert)' },
                        { name: 'irresolute', values: solution.concepts.lp.escalation_propensity.irresolute ?? [], color: 'var(--accent)', dash: '4 3' },
                      ]}
                    />
                  </div>
                )}
              </>
            ) : (
              <Empty>no quarterly trajectory for this relationship yet</Empty>
            )}
          </Beat>

          {/* ── PRECEDENT — what such episodes did next, and to which markets ─ */}
          <Beat n={2} title="What comparable episodes did next" aside={precedent ? `${precedent.episodes.length} episodes, regime-gated` : undefined}>
            {precedent === undefined ? (
              <Empty>reading the precedent…</Empty>
            ) : precedent && (precedent.fan.length || precedent.markets.length) ? (
              <div className="grid md:grid-cols-2 gap-6">
                <div>
                  <div className="kicker mb-1">Intensity after an episode opens — median, p25–p75, min–max</div>
                  <Fan rows={precedent.fan} width={480} height={150} />
                </div>
                <div>
                  <div className="kicker mb-1">Measured abnormal returns across this pair's episodes</div>
                  {precedent.markets.length ? (
                    <div className="space-y-1">
                      {(() => {
                        const lo = Math.min(...precedent.markets.map((m) => m.min), 0)
                        const hi = Math.max(...precedent.markets.map((m) => m.max), 0)
                        return precedent.markets.map((m) => <BoxRow key={m.market_id} row={m} domain={[lo, hi]} />)
                      })()}
                    </div>
                  ) : (
                    <p className="text-xs" style={{ color: 'var(--muted)' }}>{precedent.markets_note ?? 'no measured market effects for this pair'}</p>
                  )}
                </div>
              </div>
            ) : (
              <Empty>{precedent?.markets_note ?? 'no comparable episodes on record for this pair'}</Empty>
            )}
          </Beat>

          {/* ── WHERE IT'S BEEN — the event timeline ─────────────────────── */}
          <Beat
            n={3}
            title="Where it’s been"
            aside={timeline && timeline.total ? `${timeline.total} market-moving events · click one for measured vs expected` : undefined}
          >
            {coverage && (() => {
              const c = coverage.dyads.find((d) => d.dyad_id === selected)
              return c ? (
                <p className="mono text-[11px] mb-3" style={{ color: 'var(--muted)' }}>
                  market-movement trace: {c.measured.toLocaleString('en-US')} of {c.events.toLocaleString('en-US')} graph events carry a measured effect ({pct(c.share_measured, 0)}) —
                  {c.status === 'unmeasured' ? ' none measured yet; the transmission engine reaches them on a measuring boot.' : c.status === 'no_events' ? ' no graph events between these two.' : ' the rest await a measuring boot.'}
                </p>
              ) : (
                <p className="mono text-[11px] mb-3" style={{ color: 'var(--muted)' }}>market-movement trace: this pair holds no graph events between roster actors.</p>
              )
            })()}
            {timeline === undefined ? (
              <Empty>reading the record…</Empty>
            ) : timeline && timeline.events.length ? (
              <div>
                {timeline.events.slice(0, 14).map((ev) => (
                  <TimelineEntry key={ev.event_id} ev={ev} />
                ))}
              </div>
            ) : (
              <Empty>no market-moving events measured for this relationship yet</Empty>
            )}
            {selected && (
              <p className="mt-3">
                <button className="btn" onClick={() => onNavigate(`/case/dynamic?dyad=${encodeURIComponent(selected)}&region=${encodeURIComponent(region)}`)}>
                  Compose a case study from this record →
                </button>
              </p>
            )}
          </Beat>

          {/* ── TRACK RECORD (region-wide) ───────────────────────────────── */}
          {(hasRetro || outlook?.brier_score != null || backtest?.summary || calibration?.brier != null) && (
            <Beat n={4} title="Track record" aside={`${regionLabel} calls`}>
              {outlook?.scenarios?.some((sc) => sc.scenario_name.endsWith(selected)) && (
                <div className="mb-3">
                  <div className="kicker mb-1">The frozen near-term call names this pair</div>
                  <Bars
                    rows={outlook.scenarios.filter((sc) => sc.scenario_name.endsWith(selected)).map((sc) => ({
                      key: sc.scenario_name, label: sc.scenario_name.split(':')[0].replace(/_/g, ' '), value: sc.likelihood ?? 0,
                    }))}
                    format={(v) => pct(v, 0)} max={1}
                  />
                </div>
              )}
              {hasRetro && retro && (
                <p className="text-sm">
                  When the system flagged a period as likely to run hot, it did{' '}
                  <strong>{Math.round(retro.hit_rate! * 100)}%</strong> of the time — versus{' '}
                  {Math.round(retro.base_rate! * 100)}% for an average period.
                </p>
              )}

              {/* THE SCOREBOARD. A near-term call asks a three-year question,
                  so nothing frozen this week can be scored this week — every
                  frozen forecast read `brier_score: null`. The same estimator
                  re-run at each closed-horizon cutoff can be scored today, and
                  the RECENT era leads because the whole-walk number is
                  dominated by a sparse deep past where near-zero calls were
                  easy to get right. */}
              {calibration?.brier != null && (
                <div className="mt-4">
                  <div className="kicker mb-1">
                    Scored against history — the same estimator, {calibration.cutoffs} closed-horizon cutoffs
                  </div>
                  {calibration.recent?.brier != null ? (
                    <p className="text-sm">
                      Over the last {calibration.recent.years} years it scores a Brier of{' '}
                      <strong>{calibration.recent.brier.toFixed(3)}</strong> across {calibration.recent.calls} calls —{' '}
                      {calibration.recent.skill != null && calibration.recent.skill > 0
                        ? <>better than predicting the era's own base rate ({calibration.recent.base_rate_brier?.toFixed(3)}).</>
                        : <><strong>no better than predicting the era's own base rate</strong> ({calibration.recent.base_rate_brier?.toFixed(3)}), which in this era is close to certain.</>}
                      {' '}Over the whole archive it scores {calibration.brier.toFixed(3)}.
                    </p>
                  ) : (
                    <p className="text-sm">
                      Brier <strong>{calibration.brier.toFixed(3)}</strong> across {calibration.calls} calls
                      {calibration.base_rate_brier != null ? ` against ${calibration.base_rate_brier.toFixed(3)} for the base rate` : ''}.
                    </p>
                  )}
                  {(calibration.recent?.reliability ?? calibration.reliability ?? []).length > 0 && (
                    <div className="mt-2 scroll-x">
                      <table className="text-xs mono">
                        <thead>
                          <tr style={{ color: 'var(--muted)' }}>
                            <th className="text-left pr-3 font-normal">when it said</th>
                            <th className="text-left pr-3 font-normal">it happened</th>
                            <th className="text-left font-normal">calls</th>
                          </tr>
                        </thead>
                        <tbody>
                          {(calibration.recent?.reliability ?? calibration.reliability ?? []).map((b) => (
                            <tr key={String(b.band)}>
                              <td className="pr-3">{pct(b.mean_forecast, 0)}</td>
                              <td className="pr-3" style={{ color: b.observed_rate > b.mean_forecast + 0.1 ? 'var(--alert)' : 'var(--text)' }}>
                                {pct(b.observed_rate, 0)}
                              </td>
                              <td style={{ color: 'var(--muted)' }}>{b.calls}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <p className="mono text-[10px] mt-1" style={{ color: 'var(--muted)' }}>
                        oxblood marks a band the estimator is under-confident in — it happened more often than it said.
                      </p>
                    </div>
                  )}
                </div>
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
                      walk-forward, computed {backtest.computed_at.slice(0, 10)} ·{' '}
                      <button className="article-link" onClick={() => onNavigate('/markets')}>the markets page →</button>
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
  bandNames,
}: {
  marginal: GameExplore['marginal']
  bands: number
  bandNames?: string[]
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
                  <td key={b} style={{ padding: '2px' }} title={`${bandLabel(b, bands, bandNames)} · ${Math.round(p * 100)}%`}>
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
                {bandLabel(Math.round(m.expected_band), bands, bandNames)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}


/** One event on the timeline: what happened, who did it, how it was coded,
 *  which market reacted first, and — on click — the impact read: measured
 *  beside expected (the regime-gated base rate over this pair's other
 *  events) with the surprise. */
function TimelineEntry({ ev }: { ev: TimelineEvent }) {
  const [open, setOpen] = useState(false)
  const [impact, setImpact] = useState<EventImpact | null | undefined>(undefined)
  useEffect(() => {
    if (!open || impact !== undefined) return
    let live = true
    getEventImpact(ev.event_id).then((r) => live && setImpact(r))
    return () => {
      live = false
    }
  }, [open, impact, ev.event_id])
  const dir = ev.escalation_direction
  const dirColor = dir === 'escalating' ? 'var(--alert)' : dir === 'deescalating' ? 'var(--accent)' : 'var(--muted)'
  return (
    <div className="event-row">
      <button className="w-full text-left" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <div className="flex items-baseline gap-3 flex-wrap">
          <span className="mono text-xs" style={{ color: 'var(--muted)' }}>{ev.date}</span>
          <span className="text-sm" style={{ flex: '1 1 auto', minWidth: 0 }}>{ev.name ?? ev.event_id}</span>
          {ev.goldstein != null && (
            <span className="mono text-[11px]" style={{ color: dirColor }}>
              {dir ?? 'stable'} · goldstein {ev.goldstein.toFixed(1)}
              {ev.escalation_magnitude != null ? ` · departure ${ev.escalation_magnitude.toFixed(1)}` : ''}
            </span>
          )}
          {ev.first_mover && (
            <span className="mono text-[11px]" style={{ color: 'var(--muted)' }}>first to react: {ev.first_mover}</span>
          )}
        </div>
      </button>
      {ev.markets.length ? (
        <div className="mt-1">
          {ev.markets.slice(0, open ? ev.markets.length : 4).map((m) => (
            <MoveRow key={m.market_id + m.window} name={`${m.market_name} · ${m.window}`} pct={m.car * 100}
                     sub={m.p_value != null && m.p_value < 0.05 ? 'p<0.05' : undefined} />
          ))}
        </div>
      ) : (
        <p className="text-xs mt-1" style={{ color: 'var(--muted)' }}>no measured market move</p>
      )}
      {open && (
        <div className="mt-2 boxed p-3">
          {impact === undefined && <Empty>reading measured vs expected…</Empty>}
          {impact === null && <Empty>the impact read is unavailable</Empty>}
          {impact && (
            <div>
              <div className="kicker mb-1">Measured · expected (base rate over {impact.precedents.n} precedents) · surprise</div>
              <table className="mono text-[11px] w-full" style={{ borderCollapse: 'collapse' }}>
                <thead>
                  <tr style={{ color: 'var(--muted)' }}>
                    <th className="text-left font-normal">market</th><th className="text-right font-normal">measured</th>
                    <th className="text-right font-normal">expected</th><th className="text-right font-normal">p10–p90</th>
                    <th className="text-right font-normal">surprise</th><th className="text-right font-normal">n</th>
                  </tr>
                </thead>
                <tbody>
                  {impact.markets.map((m) => (
                    <tr key={m.market_id} style={{ borderTop: '1px dotted var(--line)' }}>
                      <td className="py-0.5">{m.market_name}{m.measured?.first_mover ? ' ★' : ''}</td>
                      <td className="text-right" style={{ color: m.measured ? (m.measured.car >= 0 ? 'var(--accent)' : 'var(--alert)') : 'var(--muted)' }}>{m.measured ? `${(m.measured.car * 100).toFixed(2)}%` : '—'}</td>
                      <td className="text-right">{m.expected ? `${(m.expected.mean_car * 100).toFixed(2)}%` : '—'}</td>
                      <td className="text-right" style={{ color: 'var(--muted)' }}>{m.expected ? `${(m.expected.lo * 100).toFixed(1)}…${(m.expected.hi * 100).toFixed(1)}%` : '—'}</td>
                      <td className="text-right" style={{ color: m.surprise != null ? (m.surprise >= 0 ? 'var(--accent)' : 'var(--alert)') : 'var(--muted)' }}>{m.surprise != null ? `${(m.surprise * 100).toFixed(2)}%` : '—'}</td>
                      <td className="text-right" style={{ color: 'var(--muted)' }}>{m.expected?.n_precedents ?? 0}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="text-[10px] italic mt-2" style={{ color: 'var(--muted)' }}>{impact.boundary_statement}</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
