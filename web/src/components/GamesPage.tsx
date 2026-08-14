import { useEffect, useMemo, useState } from 'react'
import { exploreGame, getGameDefaults, getPanelDyads, lastFailureFor } from '../api'
import { BandFan, CONTROLS, Controls, Panel, Step } from './GameViz'
import { useRegionLabel } from '../regions'
import type { GameDefaults, GameExplore, PanelDyad } from '../types'

/** THE CALL LEADS. The page used to open on a method rail and end at raw
 *  policy grids — numbers on a screen with no verdict anywhere. It now opens
 *  with what the equilibrium actually expects for the chosen dyad, in
 *  sentences derived DETERMINISTICALLY from the solved numbers (templates
 *  over the payload — nothing originates here, per section 17), and the
 *  panels below are that call's evidence.
 *
 *  BASELINE IS PINNED. A solve with no levers moved is the model's own call
 *  (fitted payoffs at the data-driven opening state — the same construction
 *  as the frozen sequence forecast) and stays on screen. Moving a lever
 *  solves a COUNTERFACTUAL that renders BESIDE the baseline, labelled, and
 *  clears when the levers come home. The old page had one slot for both, so
 *  the moment you touched a slider the model's call ceased to exist. */

const ACTION_VERB: Record<string, string> = {
  'de-escalate': 'de-escalation',
  hold: 'holding',
  escalate: 'escalation',
}

function describePath(steps: GameExplore['paths'][number]['steps']): string {
  const words = steps.map((s) =>
    s.action_a === s.action_b
      ? `${ACTION_VERB[s.action_a] ?? s.action_a} by both`
      : `${s.action_a} / ${s.action_b}`,
  )
  const compact: string[] = []
  for (const word of words) {
    if (compact.length && compact[compact.length - 1].startsWith(word)) {
      const last = compact.pop() as string
      const count = last.includes(' ×') ? Number(last.split(' ×')[1]) + 1 : 2
      compact.push(`${word} ×${count}`)
    } else {
      compact.push(word)
    }
  }
  return compact.join(', then ')
}

/** The verdict: sentences a reader can act on, every number the solver's. */
function verdictLines(solve: GameExplore): string[] {
  const lines: string[] = []
  const last = solve.marginal[solve.marginal.length - 1]
  if (!last) return lines
  const horizon = solve.marginal.length
  const drift = last.expected_band - solve.opening_band
  const direction =
    drift <= -0.5
      ? 'wind down'
      : drift >= 0.5
        ? 'escalate further'
        : 'hold near its current intensity'
  lines.push(
    `The equilibrium expects this dyad to ${direction} over the next ` +
      `${horizon} quarters: expected intensity band ${last.expected_band.toFixed(1)} ` +
      `by +${horizon}q, from band ${solve.opening_band} now (bands are rungs of ` +
      `the dyad's own ladder — its own typical departure is band 2).`,
  )
  const rupture = last.distribution.slice(4).reduce((a, b) => a + b, 0)
  lines.push(
    `Odds of a rupture — intensity at least twice this dyad's own typical ` +
      `departure (band 4+) — by +${horizon}q: ${(rupture * 100).toFixed(0)}%.`,
  )
  const top = solve.paths[0]
  if (top) {
    lines.push(
      `The single most likely course (${(top.probability * 100).toFixed(0)}% of ` +
        `the retained mass): ${describePath(top.steps)}.`,
    )
  }
  const firstMarkets = top?.steps[0]?.market ?? []
  if (firstMarkets.length) {
    const named = firstMarkets
      .slice(0, 3)
      .map(
        (m) =>
          `${m.market_name} ${m.median >= 0 ? '+' : ''}${(m.median * 100).toFixed(2)}% (n=${m.n})`,
      )
      .join(', ')
    lines.push(
      `After comparable events, markets moved a median of: ${named} — measured ` +
        `abnormal returns, never modelled.`,
    )
  }
  return lines
}

function openingProvenance(solve: GameExplore): string {
  const { capability, beliefs, tilt } = solve.opening
  const capText =
    capability.source === 'cinc'
      ? `capability ${capability.band} (CINC ratio ${capability.ratio})`
      : 'capability 1 (default — no CINC estimate for this pair)'
  const beliefText =
    beliefs.source === 'bayes_filter'
      ? `beliefs ${beliefs.a.toFixed(2)}/${beliefs.b.toFixed(2)} (filtered from ` +
        `${beliefs.quarters_observed}q of observed actions)`
      : 'beliefs 0.50/0.50 (default — no observed actions)'
  const tiltText = tilt
    ? `kernel tilted η=${tilt.eta} by ${tilt.model}`
    : 'no learned tilt (no gated trajectory for this dyad)'
  return `opens at band ${solve.opening_band} · ${capText} · ${beliefText} · ${tiltText}`
}

function Rail({ boxes }: { boxes: Array<{ name: string; n: string; what: string }> }) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2">
      {boxes.map((b) => (
        <div key={b.name} className="border p-2" style={{ borderColor: 'var(--line)' }}>
          <p className="kicker">{b.name}</p>
          <p className="mono text-sm mt-1">{b.n}</p>
          <p className="text-[10px] mt-1 leading-snug" style={{ color: 'var(--muted)' }}>
            {b.what}
          </p>
        </div>
      ))}
    </div>
  )
}

function Policy({ solved, bands }: { solved: GameExplore; bands: number[] }) {
  const types = Object.keys(solved.escalation_propensity)
  return (
    <table className="mono text-[11px]">
      <thead>
        <tr style={{ color: 'var(--muted)' }}>
          <th className="text-left pr-3 font-normal">band</th>
          {types.map((t) => (
            <th key={t} className="text-left px-3 font-normal">{t}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {bands.map((edge, b) => (
          <tr key={b}>
            <td className="pr-3" style={{ color: 'var(--muted)' }}>
              {b}{b === solved.opening_band ? ' ◀ now' : ''} ≥{edge}
            </td>
            {types.map((t) => {
              const p = solved.escalation_propensity[t][b]
              return (
                <td
                  key={t}
                  className="px-3 py-0.5"
                  title={`P(escalate | band ${b}, ${t}) = ${p.toFixed(3)}`}
                  style={{ background: `color-mix(in srgb, var(--alert) ${Math.round(15 + 85 * p)}%, transparent)` }}
                >
                  {p.toFixed(2)}
                </td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** The dyad a cross-page link asked for (e.g. #/games?dyad=…), read once. */
function dyadFromHash(): string | null {
  const query = window.location.hash.split('?')[1]
  return query ? new URLSearchParams(query).get('dyad') : null
}

export default function GamesPage({
  region,
  onNavigate,
}: {
  region: string
  onNavigate: (route: string) => void
}) {
  const regionLabel = useRegionLabel(region)
  const [defaults, setDefaults] = useState<GameDefaults | null | undefined>(undefined)
  const [panelTotal, setPanelTotal] = useState<number | null>(null)
  const [dyads, setDyads] = useState<PanelDyad[]>([])
  const [selected, setSelected] = useState('')
  const [baseline, setBaseline] = useState<GameExplore | null | undefined>(undefined)
  const [counterfactual, setCounterfactual] = useState<GameExplore | null>(null)
  const [solving, setSolving] = useState(false)
  // Payoff knobs mirror the fitted values until moved; belief/capability
  // knobs are null until moved — null means "the data decides".
  const [knobs, setKnobs] = useState<Record<string, number>>({})
  const [beliefA, setBeliefA] = useState<number | null>(null)
  const [beliefB, setBeliefB] = useState<number | null>(null)
  const [capability, setCapability] = useState<number | null>(null)

  useEffect(() => {
    setDefaults(undefined)
    setSelected('')
    setBaseline(undefined)
    setCounterfactual(null)
    getGameDefaults(region).then((d) => {
      setDefaults(d)
      if (d) {
        setKnobs({ ...d.payoffs })
        const linked = dyadFromHash()
        if (linked) setSelected(linked)
        else if (d.dyads.length) setSelected(d.dyads[0].dyad_id)
      }
    })
    getPanelDyads(region).then((r) => {
      setPanelTotal(r?.total ?? null)
      setDyads(r?.rows ?? [])
    })
  }, [region])

  // THE BASELINE SOLVE: no overrides at all — the server answers with the
  // fitted payoffs at the data-driven opening state and says baseline: true.
  useEffect(() => {
    if (!selected) return
    let live = true
    setBaseline(undefined)
    setCounterfactual(null)
    setBeliefA(null)
    setBeliefB(null)
    setCapability(null)
    if (defaults) setKnobs({ ...defaults.payoffs })
    exploreGame(region, selected, {}).then((r) => live && setBaseline(r ?? null))
    return () => {
      live = false
    }
  }, [selected, region, defaults])

  const dirty = useMemo(() => {
    if (!defaults) return false
    return (
      CONTROLS.some(
        (c) => Math.abs((knobs[c.key] ?? 0) - (defaults.payoffs[c.key] ?? 0)) > 1e-9,
      ) ||
      beliefA !== null ||
      beliefB !== null ||
      capability !== null
    )
  }, [knobs, beliefA, beliefB, capability, defaults])

  // THE COUNTERFACTUAL SOLVE: only when a lever moved, debounced,
  // latest-wins; clears itself when the levers come home.
  useEffect(() => {
    if (!selected || !defaults) return
    if (!dirty) {
      setCounterfactual(null)
      setSolving(false)
      return
    }
    let live = true
    setSolving(true)
    const overrides: Record<string, number> = { ...knobs }
    if (beliefA !== null) overrides.belief_a = beliefA
    if (beliefB !== null) overrides.belief_b = beliefB
    if (capability !== null) overrides.capability = capability
    const timer = setTimeout(() => {
      exploreGame(region, selected, overrides).then((r) => {
        if (!live) return
        setCounterfactual(r)
        setSolving(false)
      })
    }, 200)
    return () => {
      live = false
      clearTimeout(timer)
    }
  }, [dirty, knobs, beliefA, beliefB, capability, selected, region, defaults])

  const activeQuarters = dyads.reduce((sum, d) => sum + d.active_quarters, 0)
  const failure = lastFailureFor('/api/games/explore')

  if (defaults === undefined) {
    return (
      <div className="max-w-4xl mx-auto px-6 py-10">
        <p className="mono text-xs" style={{ color: 'var(--muted)' }}>Reading the archive…</p>
      </div>
    )
  }
  if (defaults === null) {
    return (
      <div className="max-w-4xl mx-auto px-6 py-10">
        <h1 className="text-2xl">The game — {regionLabel}</h1>
        <p className="mt-4 text-sm max-w-xl" style={{ color: 'var(--muted)' }}>
          No solvable game for this region yet: the panel is empty or the
          transition kernel is too sparsely measured to carry an equilibrium.
          The archive pages above do not depend on it.
        </p>
      </div>
    )
  }

  const shown = counterfactual ?? baseline

  return (
    <div className="max-w-4xl mx-auto px-6 py-10">
      <p className="kicker">The game · {regionLabel.toUpperCase()}</p>

      <div className="mt-2 flex flex-wrap items-baseline gap-3">
        <h1 className="text-2xl">What the equilibrium expects</h1>
        <select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          className="region-select mono text-xs"
          aria-label="dyad"
          style={{ maxWidth: '22rem' }}
        >
          {defaults.dyads.map((d) => (
            <option key={d.dyad_id} value={d.dyad_id}>
              {d.dyad_name} · {d.active_quarters}q
            </option>
          ))}
          {selected && !defaults.dyads.some((d) => d.dyad_id === selected) && (
            <option value={selected}>{baseline?.dyad_name ?? selected}</option>
          )}
        </select>
        <button
          type="button"
          onClick={() => onNavigate(`/reasoning?dyad=${encodeURIComponent(selected)}`)}
          className="mono text-[10px]"
          style={{
            background: 'none', border: 'none', padding: 0,
            cursor: 'pointer', color: 'var(--accent)',
            textDecoration: 'underline dotted',
          }}
        >
          precedent &amp; evidence →
        </button>
      </div>

      {/* ── THE CALL — the baseline verdict, pinned ─────────────────────── */}
      <div className="mt-5 pb-4 border-b" style={{ borderColor: 'var(--rule-strong)' }}>
        {baseline === undefined ? (
          <p className="mono text-xs" style={{ color: 'var(--muted)' }}>solving the baseline…</p>
        ) : baseline === null ? (
          <p className="text-sm" style={{ color: 'var(--alert)' }}>
            The solve did not answer
            {failure ? ` — ${failure.detail}` : ' — the API may still be booting; reselect the dyad to retry'}.
          </p>
        ) : (
          <>
            <div className="space-y-1.5">
              {verdictLines(baseline).map((line, i) => (
                <p key={i} className={i === 0 ? 'text-base leading-relaxed' : 'text-sm leading-relaxed'}
                   style={i === 0 ? {} : { color: 'var(--muted)' }}>
                  {line}
                </p>
              ))}
            </div>
            <p className="mono text-[10px] mt-2" style={{ color: 'var(--muted)' }}>
              {baseline.dyad_name} · {openingProvenance(baseline)} · kernel{' '}
              {(baseline.kernel.share_measured * 100).toFixed(0)}% measured ·
              the fitted baseline — the frozen sequence forecast&apos;s construction
            </p>
          </>
        )}
      </div>

      {/* ── the counterfactual, BESIDE the baseline, only when dirty ────── */}
      {dirty && (
        <div className="mt-4 border-l-2 pl-4" style={{ borderColor: 'var(--alert)' }}>
          <p className="kicker" style={{ color: 'var(--alert)' }}>
            counterfactual — under your levers{solving ? ' · solving…' : ''}
          </p>
          {counterfactual ? (
            <>
              <div className="mt-1 space-y-1">
                {verdictLines(counterfactual).slice(0, 2).map((line, i) => (
                  <p key={i} className="text-sm leading-relaxed">{line}</p>
                ))}
              </div>
              {baseline && counterfactual.marginal.length > 0 && baseline.marginal.length > 0 && (
                <p className="mono text-[11px] mt-1" style={{ color: 'var(--muted)' }}>
                  expected band at the horizon:{' '}
                  {baseline.marginal[baseline.marginal.length - 1].expected_band.toFixed(2)} →{' '}
                  <span style={{ color: 'var(--alert)' }}>
                    {counterfactual.marginal[counterfactual.marginal.length - 1].expected_band.toFixed(2)}
                  </span>
                </p>
              )}
              <p className="text-[11px] mt-1" style={{ color: 'var(--muted)' }}>
                not frozen, not scored, not comparable to the frozen sequence forecast
              </p>
            </>
          ) : (
            !solving && (
              <p className="text-sm mt-1" style={{ color: 'var(--muted)' }}>
                the counterfactual solve did not answer
                {failure ? ` — ${failure.detail}` : ''}
              </p>
            )
          )}
        </div>
      )}

      <Panel
        n={1}
        title="How this forecast is made"
        method="every stage shows its own evidence count — the platform rule is that a reader sees the process, not just its conclusion"
      >
        <Rail
          boxes={[
            {
              name: 'the wire',
              n: panelTotal != null ? `${panelTotal} dyads` : '…',
              what: 'GDELT event stream, CAMEO-coded, one deterministic parse',
            },
            {
              name: 'escalation',
              n: `${activeQuarters} dyad-quarters`,
              what: `across the ${dyads.length} most-watched dyads — Head B scores each event against its OWN dyad's EWMA baseline`,
            },
            {
              name: 'the kernel',
              n: `${defaults.kernel.measured}/${defaults.kernel.cells} cells`,
              what: `where escalation historically led — ${defaults.kernel.observations.toLocaleString()} counted transitions, not a setting`,
            },
            {
              name: 'the model',
              n: shown?.opening.tilt ? `η ${shown.opening.tilt.eta}` : 'no tilt',
              what: shown?.opening.tilt
                ? `the gated learned trajectory leans this dyad's kernel (${shown.opening.tilt.model})`
                : "a gated learned trajectory tilts a covered dyad's kernel; this dyad has none",
            },
            {
              name: 'equilibrium',
              n: `${CONTROLS.length} fitted payoffs`,
              what: 'solved over (band × capability × resolve); payoffs fitted to observed action frequencies',
            },
            {
              name: 'the price',
              n: shown ? `${shown.pricing.measurements.toLocaleString()} measurements` : '…',
              what: 'each step marked against what markets did after comparable events — measured, never asserted',
            },
          ]}
        />
      </Panel>

      <Panel
        n={2}
        title="Where the mass goes"
        method="probability over intensity bands per quarter ahead, across ALL branches of the walk — the kernel's own spread, not a summary of survivors"
      >
        {baseline ? (
          <>
            <BandFan marginal={baseline.marginal} bands={defaults.bands.length} />
            {counterfactual && (
              <div className="mt-3 pt-2 border-t" style={{ borderColor: 'var(--line)' }}>
                <p className="kicker" style={{ color: 'var(--alert)' }}>under your levers</p>
                <div className="mt-1">
                  <BandFan marginal={counterfactual.marginal} bands={defaults.bands.length} />
                </div>
              </div>
            )}
          </>
        ) : (
          <p className="mono text-xs" style={{ color: 'var(--muted)' }}>
            {baseline === undefined ? 'solving…' : 'the solve did not answer'}
          </p>
        )}
      </Panel>

      <Panel
        n={3}
        title="The sequences the equilibrium keeps"
        method={`top paths by probability; each step priced from the archive's measured effects (${shown?.pricing.note ?? 'regime-gated'})`}
      >
        {shown ? (
          <>
            <p className="mono text-[10px]" style={{ color: 'var(--muted)' }}>
              {shown.paths.length} of {shown.paths_enumerated} paths · holding{' '}
              {(shown.retained_probability * 100).toFixed(1)}% of the mass
              {counterfactual && (
                <span style={{ color: 'var(--alert)' }}> · counterfactual</span>
              )}
            </p>
            {shown.paths.slice(0, 6).map((path, i) => (
              <div key={i} className="mt-2">
                <span
                  className="mono text-[10px]"
                  title={`${(path.probability * 100).toFixed(1)}% of retained mass`}
                  style={{ color: 'var(--accent)' }}
                >
                  {(path.probability * 100).toFixed(1)}%
                </span>
                <ol className="mt-0.5 space-y-0.5">
                  {path.steps.map((step) => (
                    <Step key={step.period} step={step} />
                  ))}
                </ol>
              </div>
            ))}
          </>
        ) : (
          <p className="mono text-xs" style={{ color: 'var(--muted)' }}>
            {baseline === undefined ? 'solving…' : 'the solve did not answer'}
          </p>
        )}
      </Panel>

      <Panel
        n={4}
        title="What the equilibrium plays"
        method="P(escalate) per intensity band and private type; darker is more likely — the fitted policy, not a prediction of any single quarter"
      >
        {shown ? (
          <div className="flex flex-wrap gap-8 items-start">
            <Policy solved={shown} bands={defaults.bands} />
            <div className="max-w-xs space-y-2 text-xs leading-relaxed" style={{ color: 'var(--muted)' }}>
              <p>
                Bands are rungs of a ladder — each row is escalation intensity
                relative to this dyad&apos;s own history. The dyad stands at band{' '}
                <span style={{ color: 'var(--text)' }}>{shown.opening_band}</span> now.
              </p>
              <p>
                A resolute side genuinely bears the cost of war; an irresolute
                one is bluffing. Neither side knows which the other is —
                beliefs update along every path, and the opening beliefs are
                filtered from what each side actually did.
              </p>
            </div>
          </div>
        ) : (
          <p className="mono text-xs" style={{ color: 'var(--muted)' }}>
            {baseline === undefined ? 'solving…' : 'the solve did not answer'}
          </p>
        )}
      </Panel>

      <Panel
        n={5}
        title="What the yield curve says"
        method={defaults.duration?.method ?? 'bond tenors (front/belly/long) measured per event by the transmission engine'}
      >
        {defaults.duration && defaults.duration.events_with_a_curve_response > 0 ? (
          <div className="text-sm space-y-1">
            <p>
              <span className="mono">{defaults.duration.events_with_a_curve_response.toLocaleString()}</span>{' '}
              events carry a measured curve response across{' '}
              <span className="mono">{defaults.duration.tenors_measured.join(', ')}</span>
              {' '}· <span className="mono">{defaults.duration.usable_dyads}</span> of{' '}
              <span className="mono">{defaults.duration.dyads}</span> dyads usable
            </p>
            {defaults.duration.calibration && (
              <p className="text-xs" style={{ color: 'var(--muted)' }}>
                {defaults.duration.calibration}
              </p>
            )}
          </div>
        ) : (
          <p className="text-sm" style={{ color: 'var(--muted)' }}>
            {defaults.duration?.note ??
              'No measured curve responses yet — the panel holds no bond tenors for these events, and an absent measurement is reported, never invented.'}
          </p>
        )}
      </Panel>

      <Panel
        n={6}
        title="Move the levers"
        method="the kernel is evidence and stays fixed; payoffs, beliefs and capability are the model's assumptions — beliefs and capability OPEN at their measured values, and each is yours to move"
      >
        <Controls
          values={knobs}
          fitted={defaults.payoffs}
          onChange={(key, value) => setKnobs((k) => ({ ...k, [key]: value }))}
          onReset={() => {
            setKnobs({ ...defaults.payoffs })
            setBeliefA(null)
            setBeliefB(null)
            setCapability(null)
          }}
          busy={solving}
          dirty={dirty}
        />
        <div className="mt-2 space-y-1.5">
          {(
            [
              ['belief A holds: B is resolute', beliefA, setBeliefA, baseline?.opening.beliefs.a],
              ['belief B holds: A is resolute', beliefB, setBeliefB, baseline?.opening.beliefs.b],
            ] as const
          ).map(([label, value, set, measured]) => (
            <label key={label} className="flex items-center gap-2 text-[11px]">
              <span className="w-40 shrink-0" style={{ color: 'var(--muted)' }}>{label}</span>
              <input
                type="range"
                min={0} max={1} step={0.05}
                value={value ?? measured ?? 0.5}
                onChange={(e) => set(Number(e.target.value))}
                className="flex-1 lever"
              />
              <span
                className="mono w-12 text-right"
                style={{ color: value !== null ? 'var(--alert)' : 'var(--muted)', opacity: solving ? 0.5 : 1 }}
              >
                {(value ?? measured ?? 0.5).toFixed(2)}
              </span>
            </label>
          ))}
          <label className="flex items-center gap-2 text-[11px]">
            <span className="w-40 shrink-0" style={{ color: 'var(--muted)' }}>relative capability</span>
            <select
              value={capability ?? baseline?.opening.capability.band ?? 1}
              onChange={(e) => setCapability(Number(e.target.value))}
              className="region-select mono text-xs"
            >
              <option value={0}>0 — challenger much weaker</option>
              <option value={1}>1 — approaching parity</option>
              <option value={2}>2 — near parity</option>
            </select>
            <span className="mono text-[10px]" style={{ color: capability !== null ? 'var(--alert)' : 'var(--muted)' }}>
              {capability !== null
                ? 'moved'
                : baseline?.opening.capability.source === 'cinc'
                  ? `CINC ${baseline.opening.capability.ratio}`
                  : 'default'}
            </span>
          </label>
        </div>
        {shown && (
          <p className="text-xs mt-3 leading-relaxed" style={{ color: 'var(--muted)' }}>
            {shown.boundary_statement}
          </p>
        )}
      </Panel>
    </div>
  )
}
