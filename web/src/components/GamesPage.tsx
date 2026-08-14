/** THE GAME, VISIBLE. One page that shows the stochastic machinery end to
 *  end instead of asking the reader to trust its outputs: how a forecast is
 *  made (the pipeline, with the real numbers at every stage), the game on the
 *  board (states, actions, fitted payoffs), what the equilibrium plays (the
 *  policy), where the probability mass goes (the fan), and the sequences it
 *  keeps — each step priced against what the archive measured after moments
 *  like it.
 *
 *  The page exists because the platform rule is VISIBILITY INTO PROCESS AND
 *  DATA: every stage of the rail carries its own count, so a reader can see
 *  how much evidence stands behind each transformation rather than being
 *  shown a conclusion. */

import { useEffect, useState } from 'react'
import { exploreGame, getGameDefaults, getPanelDyads } from '../api'
import { useRegionLabel } from '../regions'
import type { GameDefaults, GameExplore, PanelDyad } from '../types'
import { BandFan, CONTROLS, Controls, Panel, Step } from './GameViz'

/** The pipeline, each stage named for what it DOES. Numbers arrive live. */
function Rail({ stages }: { stages: Array<{ name: string; what: string; n: string }> }) {
  return (
    <div className="flex flex-wrap items-stretch gap-y-3">
      {stages.map((s, i) => (
        <div key={s.name} className="flex items-stretch">
          {i > 0 && (
            <span className="self-center mx-2 mono text-xs" style={{ color: 'var(--muted)' }}>
              →
            </span>
          )}
          <div
            className="px-3 py-2 border"
            style={{ borderColor: 'var(--line)', minWidth: '9.5rem' }}
          >
            <p className="kicker" style={{ color: 'var(--accent)' }}>{s.name}</p>
            <p className="mono text-sm mt-0.5">{s.n}</p>
            <p className="text-[10px] leading-snug mt-1" style={{ color: 'var(--muted)' }}>
              {s.what}
            </p>
          </div>
        </div>
      ))}
    </div>
  )
}

/** P(escalate) by intensity band and private type — the equilibrium made
 *  legible. Sequential: one hue, share of one. */
function Policy({
  propensity, bands, opening,
}: {
  propensity: Record<string, number[]>
  bands: number[]
  opening: number
}) {
  const types = Object.keys(propensity)
  return (
    <table className="mono text-[11px]" style={{ borderCollapse: 'separate', borderSpacing: 2 }}>
      <thead>
        <tr>
          <th className="text-left font-normal pr-2" style={{ color: 'var(--muted)' }}>band</th>
          {types.map((t) => (
            <th key={t} className="font-normal px-1" style={{ color: 'var(--muted)' }}>{t}</th>
          ))}
        </tr>
      </thead>
      <tbody>
        {bands.map((edge, b) => (
          <tr key={b}>
            <td className="pr-2" style={{ color: b === opening ? 'var(--ink)' : 'var(--muted)' }}>
              {b}{b === opening ? ' ◀ now' : ''} <span style={{ color: 'var(--muted)' }}>≥{edge}</span>
            </td>
            {types.map((t) => {
              const p = propensity[t][b] ?? 0
              return (
                <td
                  key={t}
                  title={`P(escalate | band ${b}, ${t}) = ${p.toFixed(3)}`}
                  className="px-2 py-1 text-center"
                  style={{
                    background: 'var(--alert)',
                    opacity: 0.15 + 0.85 * p,
                    color: p > 0.45 ? 'var(--paper)' : 'var(--ink)',
                  }}
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

export default function GamesPage({ region }: { region: string; onNavigate: (r: string) => void }) {
  const regionLabel = useRegionLabel(region)
  const [defaults, setDefaults] = useState<GameDefaults | null | undefined>(undefined)
  const [panel, setPanel] = useState<{ rows: PanelDyad[]; total: number } | null>(null)
  const [selected, setSelected] = useState('')
  const [knobs, setKnobs] = useState<Record<string, number>>({})
  const [beliefA, setBeliefA] = useState(0.5)
  const [capability, setCapability] = useState(1)
  const [solved, setSolved] = useState<GameExplore | null | undefined>(undefined)
  const [solving, setSolving] = useState(false)

  useEffect(() => {
    let live = true
    setDefaults(undefined)
    setSelected('')
    setKnobs({})
    getGameDefaults(region).then((d) => {
      if (!live) return
      setDefaults(d)
      if (d) {
        setKnobs({ ...d.payoffs })
        if (d.dyads.length) setSelected(d.dyads[0].dyad_id)
      }
    })
    getPanelDyads(region).then((r) => live && setPanel(r))
    return () => { live = false }
  }, [region])

  // Solve whenever the dyad or any lever moves. Debounced: a slider fires per
  // pixel and solves queue behind each other for no benefit.
  useEffect(() => {
    if (!selected || !Object.keys(knobs).length) return
    let live = true
    setSolving(true)
    const timer = setTimeout(() => {
      exploreGame(region, selected, {
        ...knobs, belief_a: beliefA, belief_b: beliefA, capability,
      }).then((r) => {
        if (!live) return
        setSolved(r)
        setSolving(false)
      })
    }, 200)
    return () => { live = false; clearTimeout(timer) }
  }, [region, selected, knobs, beliefA, capability])

  const dirty = defaults
    ? CONTROLS.some((c) => Math.abs((knobs[c.key] ?? 0) - (defaults.payoffs[c.key] ?? 0)) > 1e-9)
      || Math.abs(beliefA - 0.5) > 1e-9 || capability !== 1
    : false

  if (defaults === undefined) {
    return (
      <div className="max-w-4xl mx-auto px-6 py-10">
        <p className="mono text-xs uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
          Reading the archive…
        </p>
      </div>
    )
  }
  if (defaults === null) {
    return (
      <div className="max-w-4xl mx-auto px-6 py-10">
        <h1 className="text-2xl">The game — {regionLabel}</h1>
        <p className="text-sm mt-4" style={{ color: 'var(--muted)' }}>
          No solvable game for this region yet: the panel is empty or the
          transition kernel is too sparsely measured to carry an equilibrium.
          The archive pages above do not depend on it.
        </p>
      </div>
    )
  }

  const activeQuarters = panel?.rows.reduce((s, d) => s + d.active_quarters, 0)

  return (
    <div className="max-w-4xl mx-auto px-6 py-8">
      <div className="flex items-baseline justify-between gap-4 flex-wrap">
        <h1 className="text-2xl">The game — {regionLabel}</h1>
        <label className="flex items-center gap-2 text-xs" style={{ color: 'var(--muted)' }}>
          dyad
          <select
            value={selected}
            onChange={(e) => setSelected(e.target.value)}
            className="mono text-xs px-1 py-0.5"
            style={{ background: 'var(--paper)', color: 'var(--ink)', border: '1px solid var(--line)' }}
          >
            {defaults.dyads.map((d) => (
              <option key={d.dyad_id} value={d.dyad_id}>
                {d.dyad_name} · {d.active_quarters}q
              </option>
            ))}
          </select>
        </label>
      </div>

      <Panel
        n={1}
        title="How a forecast is made"
        method={
          'every stage shows its own evidence count — the platform rule is that a reader ' +
          'sees the process, not just its conclusion'
        }
      >
        <Rail
          stages={[
            {
              name: 'the wire',
              n: panel ? `${panel.total} dyads` : '…',
              what: 'GDELT event stream, CAMEO-coded, one deterministic parse',
            },
            {
              name: 'escalation',
              n: activeQuarters ? `${activeQuarters.toLocaleString()} dyad-quarters` : '…',
              what: 'Head B scores each event against its OWN dyad’s EWMA baseline',
            },
            {
              name: 'the kernel',
              n: `${defaults.kernel.measured}/${defaults.kernel.cells} cells`,
              what: `where escalation historically led — ${defaults.kernel.observations.toLocaleString()} counted transitions, not a setting`,
            },
            {
              name: 'equilibrium',
              n: `${CONTROLS.length} fitted payoffs`,
              what: 'solved over (band × capability × resolve); payoffs fitted to observed action frequencies',
            },
            {
              name: 'the paths',
              n: solved ? `${solved.paths.length} of ${solved.paths_enumerated}` : '…',
              what: solved
                ? `sequences retaining ${(solved.retained_probability * 100).toFixed(0)}% of the mass`
                : 'sequences the equilibrium makes likely',
            },
            {
              name: 'the price',
              n: solved ? `${solved.pricing.measurements.toLocaleString()} measurements` : '…',
              what: 'each step marked against what markets did after comparable events — measured, never asserted',
            },
          ]}
        />
      </Panel>

      <Panel
        n={2}
        title="What the equilibrium plays"
        method={
          'P(escalate) per intensity band and private type; darker is more likely — ' +
          'the fitted policy, not a prediction of any single quarter'
        }
      >
        {solved ? (
          <div className="flex flex-wrap gap-8 items-start">
            <Policy
              propensity={solved.escalation_propensity}
              bands={defaults.bands}
              opening={solved.opening_band}
            />
            <div className="text-xs max-w-xs space-y-2" style={{ color: 'var(--muted)' }}>
              <p>
                Bands are rungs of a ladder — each row is escalation intensity
                relative to this dyad&apos;s own history. The dyad stands at band{' '}
                <span className="mono" style={{ color: 'var(--ink)' }}>{solved.opening_band}</span> now.
              </p>
              <p>
                A <em>resolute</em> side genuinely bears the cost of war; an{' '}
                <em>irresolute</em> one is bluffing. Neither side knows which the
                other is — beliefs update along every path.
              </p>
            </div>
          </div>
        ) : solved === null ? (
          <p className="mono text-xs" style={{ color: 'var(--alert)' }}>
            the solve did not answer — the API may still be booting; move a lever to retry
          </p>
        ) : (
          <p className="mono text-xs" style={{ color: 'var(--muted)' }}>solving…</p>
        )}
      </Panel>

      <Panel
        n={3}
        title="Where the mass goes"
        method="probability over intensity bands per quarter ahead, across the kept sequences"
      >
        {solved ? (
          <BandFan marginal={solved.marginal} bands={defaults.bands.length} />
        ) : solved === null ? (
          <p className="mono text-xs" style={{ color: 'var(--alert)' }}>
            the solve did not answer — the API may still be booting; move a lever to retry
          </p>
        ) : (
          <p className="mono text-xs" style={{ color: 'var(--muted)' }}>solving…</p>
        )}
      </Panel>

      <Panel
        n={4}
        title="The sequences the equilibrium keeps"
        method={
          solved
            ? `top paths by probability; each step priced from the archive’s measured effects (${solved.pricing.note ?? 'regime-gated'})`
            : 'top paths by probability'
        }
      >
        {solved ? (
          <div className="space-y-4">
            {solved.paths.slice(0, 6).map((path, i) => (
              <div key={i} className="flex gap-4">
                <span
                  className="mono text-xs w-14 shrink-0 text-right"
                  title={`${(path.probability * 100).toFixed(1)}% of retained mass`}
                >
                  {(path.probability * 100).toFixed(1)}%
                </span>
                <ol className="space-y-1 flex-1">
                  {path.steps.map((step) => <Step key={step.period} step={step} />)}
                </ol>
              </div>
            ))}
          </div>
        ) : solved === null ? (
          <p className="mono text-xs" style={{ color: 'var(--alert)' }}>
            the solve did not answer — the API may still be booting; move a lever to retry
          </p>
        ) : (
          <p className="mono text-xs" style={{ color: 'var(--muted)' }}>solving…</p>
        )}
      </Panel>

      <Panel
        n={5}
        title="Move the levers"
        method={
          'the kernel is evidence and stays fixed; payoffs, beliefs and capability are the ' +
          'model’s assumptions, and each is yours to move'
        }
      >
        <Controls
          values={knobs}
          fitted={defaults.payoffs}
          onChange={(key, value) => setKnobs((k) => ({ ...k, [key]: value }))}
          onReset={() => { setKnobs({ ...defaults.payoffs }); setBeliefA(0.5); setCapability(1) }}
          busy={solving}
        />
        <div className="mt-2 space-y-1.5">
          <label className="flex items-center gap-2 text-[11px]">
            <span className="w-40 shrink-0" style={{ color: 'var(--muted)' }}>
              belief the other is resolute
            </span>
            <input
              type="range" min={0} max={1} step={0.05} value={beliefA}
              onChange={(e) => setBeliefA(Number(e.target.value))}
              className="flex-1 lever"
            />
            <span className="mono w-12 text-right" style={{ color: Math.abs(beliefA - 0.5) > 1e-9 ? 'var(--alert)' : 'var(--muted)' }}>
              {beliefA.toFixed(2)}
            </span>
          </label>
          <label className="flex items-center gap-2 text-[11px]">
            <span className="w-40 shrink-0" style={{ color: 'var(--muted)' }}>relative capability</span>
            <select
              value={capability}
              onChange={(e) => setCapability(Number(e.target.value))}
              className="mono text-xs px-1 py-0.5"
              style={{ background: 'var(--paper)', color: 'var(--ink)', border: '1px solid var(--line)' }}
            >
              <option value={0}>0 — challenger weaker</option>
              <option value={1}>1 — balanced</option>
              <option value={2}>2 — challenger stronger</option>
            </select>
          </label>
        </div>
        {dirty && solved && (
          <p className="text-xs mt-3 leading-relaxed" style={{ color: 'var(--alert)' }}>
            COUNTERFACTUAL — re-solved with the levers above; not frozen, not
            scored, not comparable to the frozen sequence forecast.
          </p>
        )}
        {solved?.boundary_statement && (
          <p className="text-xs mt-2 leading-relaxed" style={{ color: 'var(--muted)' }}>
            {solved.boundary_statement}
          </p>
        )}
      </Panel>
    </div>
  )
}
