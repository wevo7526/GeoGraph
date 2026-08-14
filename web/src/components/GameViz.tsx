/** The game's shared visual grammar — used by the Reasoning page's equilibrium
 *  panel and the dedicated Game page, extracted so the two cannot drift.
 *
 *  The encodings are the paper system's validated diverging pair doing its
 *  one job: `--accent` is de-escalation (the gain direction), `--alert` is
 *  escalation (the loss direction), hold is neutral ink. Band mass is the
 *  sequential case — one hue (`--alert`), light→dark by share. Nothing here
 *  invents a color. */

import type React from 'react'
import type { SequenceDyad, SequenceStep } from '../types'

/** Per-period probability mass over intensity bands — the fan a forecast is. */
export function BandFan({
  marginal, bands,
}: { marginal: SequenceDyad['marginal']; bands: number }) {
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

/** The counterfactual controls — the one thing a fitted policy buys that a
 *  black box cannot.
 *
 *  Each slider is a PARAMETER WITH A MEANING, not a weight with a position,
 *  which is why "what if war were costly for the resolute side" is a question
 *  this can answer at all. Bounds match the estimator's own clips, so a reader
 *  cannot explore a region of the space the fit was never allowed to reach.
 *
 *  What is NOT adjustable is the transition kernel: what escalation has
 *  historically led to is counted from the archive and is evidence, not a
 *  setting. */
export const CONTROLS: Array<{
  key: string; label: string; min: number; max: number; step: number
}> = [
  { key: 'discount', label: 'patience (δ)', min: 0.5, max: 0.99, step: 0.01 },
  { key: 'cost_resolute', label: 'cost of war · resolute', min: 0.05, max: 3.0, step: 0.05 },
  { key: 'cost_irresolute', label: 'cost of war · irresolute', min: 0.05, max: 6.0, step: 0.05 },
  { key: 'stake', label: 'stake', min: 0.1, max: 3.0, step: 0.05 },
  { key: 'audience', label: 'audience cost', min: 0.0, max: 2.0, step: 0.05 },
]

export function Controls({
  values, fitted, onChange, onReset, busy,
}: {
  values: Record<string, number>
  fitted: Record<string, number>
  onChange: (key: string, value: number) => void
  onReset: () => void
  busy: boolean
}) {
  const dirty = CONTROLS.some((c) => Math.abs((values[c.key] ?? 0) - (fitted[c.key] ?? 0)) > 1e-9)
  return (
    <div className="mt-3 pt-3 border-t" style={{ borderColor: 'var(--line)' }}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="kicker">counterfactual</span>
        {dirty && (
          <button
            type="button"
            onClick={onReset}
            className="mono text-[10px]"
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--accent)' }}
          >
            reset to fitted
          </button>
        )}
      </div>
      <div className="mt-2 space-y-1.5">
        {CONTROLS.map((c) => {
          const value = values[c.key] ?? fitted[c.key] ?? c.min
          const moved = Math.abs(value - (fitted[c.key] ?? 0)) > 1e-9
          return (
            <label key={c.key} className="flex items-center gap-2 text-[11px]">
              <span className="w-40 shrink-0" style={{ color: 'var(--muted)' }}>{c.label}</span>
              <input
                type="range"
                min={c.min} max={c.max} step={c.step} value={value}
                onChange={(e) => onChange(c.key, Number(e.target.value))}
                className="flex-1"
                disabled={busy}
              />
              <span
                className="mono w-12 text-right"
                style={{ color: moved ? 'var(--alert)' : 'var(--muted)' }}
              >
                {value.toFixed(2)}
              </span>
            </label>
          )
        })}
      </div>
    </div>
  )
}

/** One predicted step, with whatever the archive measured after events like
 *  it. A step with no measured market says so rather than showing nothing. */
export function Step({ step }: { step: SequenceStep }) {
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

/** A numbered working-paper section — same skeleton the Reasoning page set. */
export function Panel({
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
