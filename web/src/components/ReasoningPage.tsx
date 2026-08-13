import { useEffect, useMemo, useState } from 'react'
import { getDyadSeries, getForecasts, getForecast, getPanelDyads, getPrecedent } from '../api'
import { BoxRow, Empty, Fan, LineBand, Strip } from './charts/Charts'
import { useRegionLabel } from '../regions'
import type {
  DyadSeries, ForecastDetail, PanelDyad, Precedent, SequenceDyad, SequenceStep,
} from '../types'

/** The solved mode's per-period fan: where the equilibrium puts its mass.
 *
 *  This LEADS the sequence panel rather than the paths, because the path tail
 *  is long and flat — eight of 271 paths can retain under a tenth of the
 *  probability — so a list of sequences reads as more certainty than exists.
 *  The fan shows the whole distribution at a glance and cannot. */
function BandFan({ marginal, bands }: { marginal: SequenceDyad['marginal']; bands: number }) {
  return (
    <div className="space-y-1">
      {marginal.map((row) => (
        <div key={row.period} className="flex items-center gap-2">
          <span className="mono text-[10px] w-8" style={{ color: 'var(--muted)' }}>
            +{row.period}q
          </span>
          <span className="flex-1 flex h-4">
            {Array.from({ length: bands }, (_, band) => {
              const share = row.distribution[band] ?? 0
              return (
                <span
                  key={band}
                  title={`band ${band}: ${(share * 100).toFixed(1)}%`}
                  style={{
                    width: `${100 / bands}%`,
                    background: share > 0 ? 'var(--alert)' : 'var(--line)',
                    opacity: share > 0 ? 0.25 + 0.75 * share : 0.25,
                  }}
                />
              )
            })}
          </span>
          <span className="mono text-[10px] w-16 text-right" style={{ color: 'var(--muted)' }}>
            E {row.expected_band.toFixed(2)}
          </span>
        </div>
      ))}
    </div>
  )
}

/** One predicted step, with whatever the archive measured after events like
 *  it. A step with no measured market says so rather than showing nothing. */
function Step({ step }: { step: SequenceStep }) {
  return (
    <li className="text-xs">
      <div className="flex items-baseline gap-2">
        <span className="mono w-8" style={{ color: 'var(--muted)' }}>+{step.period}q</span>
        <span style={{ color: step.quad.includes('conflict') ? 'var(--alert)' : 'var(--accent)' }}>
          {step.action_a} / {step.action_b}
        </span>
        <span className="mono" style={{ color: 'var(--muted)' }}>
          band {step.intensity_band}
        </span>
      </div>
      {step.market.length > 0 && (
        <div className="ml-10 mt-0.5 flex flex-wrap gap-x-4 gap-y-0.5">
          {step.market.slice(0, 4).map((m) => (
            <span key={m.market_id} className="mono text-[10px]">
              <span style={{ color: 'var(--muted)' }}>{m.market_name.slice(0, 14)}</span>{' '}
              <span style={{ color: m.median >= 0 ? 'var(--accent)' : 'var(--alert)' }}>
                {(m.median * 100).toFixed(2)}%
              </span>
              <span style={{ color: 'var(--muted)' }}>
                {' '}n={m.n}{m.thin ? ' thin' : ''}{m.match === 'quad only' ? ' loose' : ''}
              </span>
            </span>
          ))}
        </div>
      )}
    </li>
  )
}

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

function Panel({
  n, title, method, children,
}: {
  n: number
  title: string
  method: string
  children: React.ReactNode
}) {
  return (
    <section className="mt-8 pt-6 border-t" style={{ borderColor: 'var(--rule-strong)' }}>
      <div className="flex items-baseline gap-3">
        <span className="kicker" style={{ color: 'var(--accent)' }}>{n}</span>
        <h2 className="text-lg">{title}</h2>
      </div>
      <div className="mt-3">{children}</div>
      <p className="mono text-[10px] mt-2 leading-relaxed" style={{ color: 'var(--muted)' }}>
        {method}
      </p>
    </section>
  )
}

export default function ReasoningPage({ region }: { region: string; onNavigate: (r: string) => void }) {
  const regionLabel = useRegionLabel(region)
  const [dyads, setDyads] = useState<PanelDyad[] | null>(null)
  const [selected, setSelected] = useState<string>('')
  const [series, setSeries] = useState<DyadSeries | null | undefined>(undefined)
  const [precedent, setPrecedent] = useState<Precedent | null | undefined>(undefined)
  const [model, setModel] = useState<ForecastDetail | null | undefined>(undefined)
  const [sequence, setSequence] = useState<ForecastDetail | null | undefined>(undefined)

  useEffect(() => {
    setDyads(null)
    setSelected('')
    getPanelDyads(region).then((r) => {
      const rows = r?.rows ?? []
      setDyads(rows)
      // Open on the dyad the archive has watched most — an empty selector
      // explains nothing, and the best-evidenced dyad is the one whose charts
      // a reader can actually judge.
      if (rows.length) setSelected(rows[0].dyad_id)
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
    getForecasts(region).then((r) => {
      const rows = r?.rows ?? []
      for (const [mode, set] of [
        ['model', setModel],
        ['sequence', setSequence],
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
        </select>
        {summary && (
          <span className="mono text-[11px]" style={{ color: 'var(--muted)' }}>
            {summary.first.slice(0, 7)}–{summary.last.slice(0, 7)} ·{' '}
            {summary.active_quarters} of {summary.quarters} quarters active · peak{' '}
            {summary.peak_intensity.toFixed(1)}
          </span>
        )}
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
              marginal={solved.marginal}
              bands={sequence.frozen_inputs.bands?.length ?? 6}
            />
            <p className="mono text-[10px] mt-1" style={{ color: 'var(--muted)' }}>
              probability mass over intensity bands, per quarter ahead · opening band{' '}
              {solved.opening_band}
            </p>

            <div className="mt-4">
              <p className="mono text-[10px]" style={{ color: 'var(--muted)' }}>
                most-weighted sequences — {solved.paths.length} of{' '}
                {solved.paths_enumerated} paths, holding{' '}
                {(solved.retained_probability * 100).toFixed(1)}% of the mass
              </p>
              {solved.paths.slice(0, 3).map((path, i) => (
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
    </div>
  )
}
