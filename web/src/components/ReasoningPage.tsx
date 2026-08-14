import { useEffect, useMemo, useState } from 'react'
import {
  exploreGame, getDyadSeries, getForecasts, getForecast, getGameDefaults,
  getPanelDyads, getPrecedent,
} from '../api'
import { BoxRow, Empty, Fan, LineBand, Strip } from './charts/Charts'
// The game's visual grammar is SHARED with the Game page (GameViz.tsx), so
// the equilibrium reads identically wherever it appears.
import { BandFan, CONTROLS, Controls, Panel, Step } from './GameViz'
import { useRegionLabel } from '../regions'
import type {
  DyadSeries, ForecastDetail, GameDefaults, GameExplore, PanelDyad, Precedent,
} from '../types'

/** ONE QUESTION, FOUR CHARTS: pick a dyad, see where it stands, what the
 *  archive measured after comparable moments, what markets did then, and what
 *  the model says now.
 *
 *  The page used to be three stacked widgets under three paragraphs each, and
 *  readers could not tell what it was for. The prose is gone; every panel now
 *  carries one line of method under it and nothing else. What replaced the
 *  explanation is ORDER: precedent (counted) comes before forecast (fitted),
 *  because the counted half is the check on the fitted half and burying it
 *  underneath would invert which one the reader trusts first.
 *
 *  Nothing here computes at request time. Every number is either a projection
 *  of Event rows or a frozen Forecast. */

/** The dyad a cross-page link asked for (e.g. #/reasoning?dyad=…). */
function dyadFromHash(): string | null {
  const query = window.location.hash.split('?')[1]
  return query ? new URLSearchParams(query).get('dyad') : null
}

export default function ReasoningPage({
  region,
  onNavigate,
}: { region: string; onNavigate: (r: string) => void }) {
  const regionLabel = useRegionLabel(region)
  const [dyads, setDyads] = useState<PanelDyad[] | null>(null)
  const [selected, setSelected] = useState<string>('')
  const [series, setSeries] = useState<DyadSeries | null | undefined>(undefined)
  const [precedent, setPrecedent] = useState<Precedent | null | undefined>(undefined)
  const [model, setModel] = useState<ForecastDetail | null | undefined>(undefined)
  const [sequence, setSequence] = useState<ForecastDetail | null | undefined>(undefined)
  const [nearTerm, setNearTerm] = useState<ForecastDetail | null | undefined>(undefined)
  const [longHorizon, setLongHorizon] = useState<ForecastDetail | null | undefined>(undefined)
  const [gameDefaults, setGameDefaults] = useState<GameDefaults | null>(null)
  const [knobs, setKnobs] = useState<Record<string, number>>({})
  const [counterfactual, setCounterfactual] = useState<GameExplore | null>(null)
  const [solving, setSolving] = useState(false)

  useEffect(() => {
    setDyads(null)
    setSelected('')
    getPanelDyads(region).then((r) => {
      const rows = r?.rows ?? []
      setDyads(rows)
      // A cross-page link's dyad wins; otherwise open on the dyad the archive
      // has watched most — an empty selector explains nothing, and the
      // best-evidenced dyad is the one whose charts a reader can judge.
      const linked = dyadFromHash()
      if (linked) setSelected(linked)
      else if (rows.length) setSelected(rows[0].dyad_id)
    })
  }, [region])

  useEffect(() => {
    if (!selected) return
    let live = true
    setSeries(undefined)
    setPrecedent(undefined)
    getDyadSeries(selected, region).then((r) => live && setSeries(r))
    getPrecedent(selected, region).then((r) => live && setPrecedent(r))
    return () => {
      live = false
    }
  }, [selected, region])

  useEffect(() => {
    let live = true
    setModel(undefined)
    setSequence(undefined)
    setNearTerm(undefined)
    setLongHorizon(undefined)
    getForecasts(region).then((r) => {
      const rows = r?.rows ?? []
      for (const [mode, set] of [
        ['model', setModel],
        ['sequence', setSequence],
        ['near_term', setNearTerm],
        ['long_horizon', setLongHorizon],
      ] as const) {
        const row = rows.find((f) => f.mode === mode)
        if (!row) {
          if (live) set(null)
          continue
        }
        getForecast(row.node_id).then((d) => live && set(d ?? null))
      }
    })
    return () => {
      live = false
    }
  }, [region])

  useEffect(() => {
    let live = true
    setGameDefaults(null)
    setKnobs({})
    setCounterfactual(null)
    getGameDefaults(region).then((d) => {
      if (!live || !d) return
      setGameDefaults(d)
      setKnobs({ ...d.payoffs })
    })
    return () => {
      live = false
    }
  }, [region])

  // Re-solve when a knob moves. Debounced because a slider fires on every
  // pixel and a solve is ~30ms of server work — fast, but not free, and a
  // burst of them would queue behind each other for no benefit.
  useEffect(() => {
    if (!gameDefaults || !selected || !Object.keys(knobs).length) return
    const dirty = CONTROLS.some(
      (c) => Math.abs((knobs[c.key] ?? 0) - (gameDefaults.payoffs[c.key] ?? 0)) > 1e-9,
    )
    if (!dirty) {
      setCounterfactual(null)
      return
    }
    let live = true
    setSolving(true)
    const timer = setTimeout(() => {
      exploreGame(region, selected, knobs).then((r) => {
        if (!live) return
        setCounterfactual(r)
        setSolving(false)
      })
    }, 200)
    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [knobs, selected, region, gameDefaults])

  const trajectory = useMemo(
    () => model?.frozen_inputs?.trajectories?.find((t) => t.dyad_id === selected) ?? null,
    [model, selected],
  )

  const solved = useMemo(
    () => sequence?.frozen_inputs?.dyads?.find((d) => d.dyad_id === selected) ?? null,
    [sequence, selected],
  )

  const marks = useMemo(
    () =>
      series && precedent
        ? precedent.episodes
            .map((e) => series.rows.find((r) => r.date === e.date)?.q)
            .filter((q): q is number => q !== undefined)
        : [],
    [series, precedent],
  )

  const marketDomain = useMemo((): [number, number] => {
    const values = (precedent?.markets ?? []).flatMap((m) => [m.min, m.max])
    if (!values.length) return [-0.05, 0.05]
    const bound = Math.max(...values.map(Math.abs))
    return [-bound, bound]
  }, [precedent])

  const summary = dyads?.find((d) => d.dyad_id === selected)

  return (
    <div className="max-w-4xl mx-auto px-6 py-10">
      <p className="kicker">Reasoning · {regionLabel.toUpperCase()}</p>

      {/* THE LEDE: the frozen call, before the evidence. The page used to
          open on a dyad chart with no statement of what the system actually
          concluded — the near-term forecast existed as an API node and never
          appeared here, so the page read as charts about nothing. The call
          leads; the five panels below are its evidence chain. */}
      {nearTerm && (() => {
        // The frozen call, honestly labelled. The headline used to read
        // "Escalation X%" where X was the FIRST focal dyad's continuation
        // rate wearing a regional costume; the call is per dyad, so the
        // headline names its dyad. Names come from the frozen payload —
        // focal dyads rank by conflictuality, not roster popularity, so the
        // top-40 roster may not contain them (two of three were dead links).
        const dyadName = (id: string) =>
          nearTerm.frozen_inputs?.dyad_names?.[id] ??
          (dyads ?? []).find((d) => d.dyad_id === id)?.dyad_name ??
          id
        const focal = nearTerm.scenarios
          .filter(
            (s) => s.scenario_name.startsWith('further_escalation') && s.likelihood != null,
          )
          .map((s) => ({
            dyadId: s.scenario_name.split(':').slice(1).join(':'),
            likelihood: s.likelihood as number,
          }))
          .sort((a, b) => b.likelihood - a.likelihood)
        const top = focal[0]
        return (
          <div className="mt-5 pb-4 border-b" style={{ borderColor: 'var(--rule-strong)' }}>
            <div className="flex flex-wrap items-baseline gap-x-6 gap-y-1">
              <span className="text-2xl">
                {top ? (
                  <>
                    {dyadName(top.dyadId)}{' '}
                    <span className="mono" style={{ color: 'var(--alert)' }}>
                      {(top.likelihood * 100).toFixed(1)}%
                    </span>
                  </>
                ) : (
                  'No focal dyad cleared the evidence bar'
                )}
              </span>
              {top && (
                <span className="text-sm" style={{ color: 'var(--muted)' }}>
                  most likely to escalate again within 3y
                </span>
              )}
            </div>
            <p className="mono text-[11px] mt-1" style={{ color: 'var(--muted)' }}>
              frozen {nearTerm.generated_at?.slice(0, 10)} · as of{' '}
              {nearTerm.frozen_inputs?.as_of ?? '—'} · horizon{' '}
              {nearTerm.horizon_end?.slice(0, 4) ?? '—'}
              {nearTerm.frozen_inputs?.episodes != null &&
                ` · ${nearTerm.frozen_inputs.episodes.toLocaleString()} episodes counted`}
              {nearTerm.frozen_inputs?.evidence_span &&
                ` · evidence ${nearTerm.frozen_inputs.evidence_span[0].slice(0, 4)}–${nearTerm.frozen_inputs.evidence_span[1].slice(0, 4)}`}
            </p>
            <div className="mt-2 space-y-0.5">
              {focal.map(({ dyadId, likelihood }) => (
                <p key={dyadId} className="text-xs flex items-baseline gap-2">
                  <span className="mono w-12" style={{ color: 'var(--alert)' }}>
                    {(likelihood * 100).toFixed(0)}%
                  </span>
                  <button
                    type="button"
                    onClick={() => setSelected(dyadId)}
                    className="mono text-xs"
                    style={{
                      background: 'none', border: 'none', padding: 0,
                      cursor: 'pointer', color: 'var(--ink)',
                      textDecoration: 'underline dotted',
                    }}
                  >
                    {dyadName(dyadId)}
                  </button>
                  <span style={{ color: 'var(--muted)' }}>escalates again within 3y</span>
                </p>
              ))}
            </div>
            <p className="mono text-[10px] mt-2" style={{ color: 'var(--muted)' }}>
              likelihoods ARE base rates counted from the archive — recountable, then
              Brier-scored against what happens · {nearTerm.question}
            </p>
          </div>
        )
      })()}

      <div className="mt-4 flex flex-wrap items-baseline gap-3">
        <label className="kicker">dyad</label>
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          className="region-select mono text-xs"
          aria-label="dyad"
          style={{ maxWidth: '22rem' }}
        >
          {(dyads ?? []).map((d) => (
            <option key={d.dyad_id} value={d.dyad_id}>
              {d.dyad_name} · {d.active_quarters}q
            </option>
          ))}
          {selected && !(dyads ?? []).some((d) => d.dyad_id === selected) && (
            // A focal dyad from the lede can sit outside the roster's top-40
            // slice; selecting it must not silently snap back to the list.
            <option value={selected}>
              {nearTerm?.frozen_inputs?.dyad_names?.[selected] ?? selected}
            </option>
          )}
        </select>
        {summary && (
          <span className="mono text-[11px]" style={{ color: 'var(--muted)' }}>
            {summary.first.slice(0, 7)}–{summary.last.slice(0, 7)} ·{' '}
            {summary.active_quarters} of {summary.quarters} quarters active · peak{' '}
            {summary.peak_intensity.toFixed(1)}
          </span>
        )}
        <button
          type="button"
          onClick={() => onNavigate(`/games?dyad=${encodeURIComponent(selected)}`)}
          className="mono text-[10px]"
          style={{
            background: 'none', border: 'none', padding: 0,
            cursor: 'pointer', color: 'var(--accent)',
            textDecoration: 'underline dotted',
          }}
        >
          open in the game →
        </button>
      </div>

      {dyads !== null && !dyads.length && (
        <Empty note="No dyad in this region has enough quarters to model." />
      )}

      <Panel
        n={1}
        title="The arc"
        method={
          series
            ? `quarterly peak departure from the dyad's own baseline, ${series.span[0].slice(0, 7)}–${series.span[1].slice(0, 7)}; marked bars are the precedent episodes below`
            : 'quarterly peak departure from the dyad’s own baseline'
        }
      >
        {series === undefined ? (
          <Empty note="reading the archive…" />
        ) : series ? (
          <Strip
            values={series.rows.map((r) => ({ x: r.q, y: r.intensity }))}
            marks={marks}
            label={`${series.dyad_name} intensity by quarter`}
          />
        ) : (
          <Empty note="no series for this dyad" />
        )}
      </Panel>

      <Panel
        n={2}
        title="What followed, the times this happened before"
        method={precedent?.method ?? 'comparable episodes, regime-gated, aftermath as measured'}
      >
        {precedent === undefined ? (
          <Empty note="reading the archive…" />
        ) : precedent && precedent.fan.length ? (
          <>
            <Fan rows={precedent.fan} label="intensity after comparable episodes" />
            <p className="mono text-[11px] mt-1" style={{ color: 'var(--muted)' }}>
              {precedent.episodes.length} comparable episode
              {precedent.episodes.length === 1 ? '' : 's'} · band is p25–p75 inside min–max
            </p>
          </>
        ) : (
          <Empty note="no comparable episode inside the current regime — the archive has not watched this dyad do this before" />
        )}
      </Panel>

      <Panel
        n={3}
        title="What markets did"
        method={
          precedent?.markets.length
            ? 'measured AFFECTED effects for this dyad’s events in the current regime — never modelled'
            : 'measured effects only; a market that did not exist at event time is a recorded skip, not a zero'
        }
      >
        {precedent === undefined ? (
          <Empty note="reading the archive…" />
        ) : precedent?.markets.length ? (
          <div className="space-y-1.5">
            {precedent.markets.map((m) => (
              <BoxRow key={m.market_id} row={m} domain={marketDomain} />
            ))}
          </div>
        ) : (
          <Empty note={precedent?.markets_note ?? 'no measured market effects for this dyad'} />
        )}
      </Panel>

      <Panel
        n={4}
        title="What the model says"
        method={
          model?.frozen_inputs?.model
            ? `${model.frozen_inputs.model.name}@${model.frozen_inputs.model.hash} · ${model.frozen_inputs.model.gate_reason}`
            : 'a learned forecast appears only when its walk-forward gate passed'
        }
      >
        {model === undefined ? (
          <Empty note="reading the archive…" />
        ) : !model ? (
          <Empty note="No model forecast is frozen for this region — either no artifact is deployed or its gate failed. The three panels above do not depend on it." />
        ) : !trajectory ? (
          <Empty note="This dyad is outside the model's covered set. The counted panels above still hold." />
        ) : (
          <>
            <LineBand
              points={[
                { x: 0, y: series?.rows.at(-1)?.intensity ?? 0 },
                ...trajectory.path.map((p) => ({
                  x: p.horizon, y: p.intensity, lo: p.lo, hi: p.hi,
                })),
              ]}
              label={`${trajectory.dyad_name} predicted intensity`}
            />
            <div className="mt-2 flex flex-wrap gap-x-6 gap-y-1">
              {trajectory.path.map((p) => (
                <span key={p.horizon} className="mono text-[11px]">
                  +{p.horizon}q{' '}
                  <span style={{ color: p.deviation >= 0 ? 'var(--alert)' : 'var(--accent)' }}>
                    {p.intensity.toFixed(2)}
                  </span>
                  <span style={{ color: 'var(--muted)' }}>
                    {' '}
                    ({p.deviation >= 0 ? '+' : ''}
                    {p.deviation.toFixed(2)})
                  </span>
                </span>
              ))}
            </div>
            {model.boundary_statement && (
              <p className="text-xs mt-3 leading-relaxed" style={{ color: 'var(--muted)' }}>
                {model.boundary_statement}
              </p>
            )}
          </>
        )}
      </Panel>

      <Panel
        n={5}
        title="What the equilibrium plays"
        method={
          sequence?.frozen_inputs?.equilibrium
            ? `${sequence.frozen_inputs.equilibrium.concept} · payoffs fitted to observed action frequencies, distance ${sequence.frozen_inputs.equilibrium.distance} · kernel ${((sequence.frozen_inputs.kernel?.share_measured ?? 0) * 100).toFixed(0)}% measured`
            : 'a solved forecast appears only when the transition kernel is mostly measured'
        }
      >
        {sequence === undefined ? (
          <Empty note="reading the archive…" />
        ) : !sequence ? (
          <Empty note="No sequence forecast is frozen for this region — the kernel is too sparsely measured to carry an equilibrium, or none has been frozen yet. The four panels above do not depend on it." />
        ) : !solved ? (
          <Empty note="This dyad is outside the solved set. The counted panels above still hold." />
        ) : (
          <>
            <BandFan
              marginal={(counterfactual ?? solved).marginal}
              bands={sequence.frozen_inputs.bands?.length ?? 6}
            />
            <p className="mono text-[10px] mt-1" style={{ color: 'var(--muted)' }}>
              probability mass over intensity bands, per quarter ahead · opening band{' '}
              {solved.opening_band}
              {counterfactual && (
                <span style={{ color: 'var(--alert)' }}>
                  {' '}· COUNTERFACTUAL, re-solved — not frozen, not scored
                </span>
              )}
              {solving && <span style={{ color: 'var(--muted)' }}> · solving…</span>}
            </p>

            {gameDefaults && (
              <Controls
                values={knobs}
                fitted={gameDefaults.payoffs}
                onChange={(key, value) => setKnobs((k) => ({ ...k, [key]: value }))}
                onReset={() => setKnobs({ ...gameDefaults.payoffs })}
                busy={solving}
              />
            )}

            {counterfactual && (
              <div className="mt-3">
                <p className="mono text-[10px]" style={{ color: 'var(--muted)' }}>
                  P(escalate) by band, under the moved payoffs
                </p>
                {Object.entries(counterfactual.escalation_propensity).map(([type, byBand]) => (
                  <div key={type} className="flex items-baseline gap-2 text-[11px]">
                    <span className="w-20" style={{ color: 'var(--muted)' }}>{type}</span>
                    <span className="mono">
                      {byBand.map((v) => v.toFixed(3)).join('  ')}
                    </span>
                  </div>
                ))}
                <p className="text-xs mt-2 leading-relaxed" style={{ color: 'var(--muted)' }}>
                  {counterfactual.boundary_statement}
                </p>
              </div>
            )}

            <div className="mt-4">
              <p className="mono text-[10px]" style={{ color: 'var(--muted)' }}>
                most-weighted sequences — {(counterfactual ?? solved).paths.length} of{' '}
                {(counterfactual ?? solved).paths_enumerated} paths, holding{' '}
                {((counterfactual ?? solved).retained_probability * 100).toFixed(1)}% of the mass
              </p>
              {(counterfactual ?? solved).paths.slice(0, 3).map((path, i) => (
                <div key={i} className="mt-2">
                  <span className="mono text-[10px]" style={{ color: 'var(--accent)' }}>
                    p={path.probability.toFixed(3)}
                  </span>
                  <ul className="mt-0.5 space-y-0.5">
                    {path.steps.map((step) => (
                      <Step key={step.period} step={step} />
                    ))}
                  </ul>
                </div>
              ))}
            </div>

            {solved.pricing.note && (
              <p className="mono text-[10px] mt-3" style={{ color: 'var(--muted)' }}>
                {solved.pricing.note}
              </p>
            )}
            {sequence.boundary_statement && (
              <p className="text-xs mt-3 leading-relaxed" style={{ color: 'var(--muted)' }}>
                {sequence.boundary_statement}
              </p>
            )}
          </>
        )}
      </Panel>

      {/* Region-level, deliberately last: the decades close the page the way
          the frozen call opened it. This mode existed as an API node from
          Phase 5 and rendered NOWHERE — the platform's long-horizon half was
          invisible. */}
      <Panel
        n={6}
        title="Where the pressure runs"
        method={
          longHorizon?.retrodiction?.method ??
          'structural pressure over windows for the whole lens — never dated predictions'
        }
      >
        {longHorizon === undefined ? (
          <Empty note="reading the archive…" />
        ) : !longHorizon ? (
          <Empty note="No long-horizon forecast is frozen for this region yet." />
        ) : (
          <>
            {(() => {
              const pressure = Object.entries(longHorizon.frozen_inputs?.pressure ?? {})
                .map(([year, value]) => ({ x: Number(year), y: value }))
                .sort((a, b) => a.x - b.x)
              return pressure.length ? (
                <Strip
                  values={pressure}
                  label={`${regionLabel} structural pressure by year`}
                />
              ) : (
                <Empty note="no year holds every pressure component — see coverage" />
              )
            })()}
            {(longHorizon.frozen_inputs?.windows ?? []).length > 0 && (
              <div className="flex flex-wrap gap-2 mt-2">
                {(longHorizon.frozen_inputs?.windows ?? []).map((w) => (
                  <span
                    key={`${w.start}-${w.end}`}
                    className="mono text-[10px] uppercase tracking-wider border px-2 py-1"
                    style={{
                      borderColor: w.level === 'high' ? 'var(--alert)' : 'var(--line)',
                      color: w.level === 'high' ? 'var(--alert)' : 'var(--muted)',
                    }}
                  >
                    {w.start}–{w.end} · {w.level}
                  </span>
                ))}
              </div>
            )}
            {longHorizon.retrodiction && (
              <p className="mono text-[11px] mt-3" style={{ color: 'var(--muted)' }}>
                {longHorizon.retrodiction.hit_rate != null ? (
                  <>
                    the method's own record:{' '}
                    <span style={{ color: 'var(--text)' }}>
                      {(longHorizon.retrodiction.hit_rate * 100).toFixed(0)}% of{' '}
                      {longHorizon.retrodiction.flagged_total ?? '—'} flagged years ran hot
                    </span>
                    {' vs a '}
                    {longHorizon.retrodiction.base_rate != null
                      ? `${(longHorizon.retrodiction.base_rate * 100).toFixed(0)}%`
                      : '—'}{' '}
                    base rate, across {longHorizon.retrodiction.anchors_evaluated ?? '—'}{' '}
                    as-of anchors — reported, never adjudicated
                  </>
                ) : (
                  <>
                    the method flagged no years at any of{' '}
                    {longHorizon.retrodiction.anchors_evaluated ?? '—'} as-of anchors for
                    this lens — it has no verification record here to claim
                  </>
                )}
              </p>
            )}
            {longHorizon.boundary_statement && (
              <p className="text-xs mt-2 leading-relaxed italic" style={{ color: 'var(--muted)' }}>
                {longHorizon.boundary_statement}
              </p>
            )}
          </>
        )}
      </Panel>
    </div>
  )
}
