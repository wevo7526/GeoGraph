/** The game's shared visual grammar — used by the Reasoning page's equilibrium
 *  panel and the dedicated Game page, extracted so the two cannot drift.
 *
 *  The encodings are the paper system's validated diverging pair doing its
 *  one job: `--accent` is de-escalation (the gain direction), `--alert` is
 *  escalation (the loss direction), hold is neutral ink. Band mass is the
 *  sequential case — one hue (`--alert`), light→dark by share. Nothing here
 *  invents a color. */

import { bandLabel, evidenceNote, expectedTension, jointAction, marketMove } from '../lib/language'
import type { SequenceDyad, SequenceStep } from '../types'

/** Per-period probability mass over intensity bands — the fan a forecast is. */
export function BandFan({
  marginal, bands,
}: { marginal: SequenceDyad['marginal']; bands: number }) {
  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2 mb-1">
        <span className="w-8" />
        <span className="flex-1 flex justify-between text-[10px]" style={{ color: 'var(--muted)' }}>
          <span>cooperative</span>
          <span>open conflict</span>
        </span>
        <span className="w-20 text-right text-[10px]" style={{ color: 'var(--muted)' }}>
          expected
        </span>
      </div>
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
                  title={`${bandLabel(band, bands)}: ${(share * 100).toFixed(0)}%`}
                  style={{
                    width: `${100 / bands}%`,
                    background: share > 0 ? 'var(--alert)' : 'var(--line)',
                    opacity: share > 0 ? 0.25 + 0.75 * share : 0.25,
                  }}
                />
              )
            })}
          </span>
          <span className="text-[10px] w-20 text-right" style={{ color: 'var(--muted)' }}>
            {expectedTension(row.expected_band, bands)}
          </span>
        </div>
      ))}
    </div>
  )
}

/** One predicted step, with whatever the archive measured after events like
 *  it. A step with no measured market says so rather than showing nothing. */
export function Step({ step, bands = 5 }: { step: SequenceStep; bands?: number }) {
  const escalating = step.quad.includes('conflict')
  return (
    <li className="text-sm">
      <div className="flex items-baseline gap-2 flex-wrap">
        <span className="mono text-xs w-8 shrink-0" style={{ color: 'var(--muted)' }}>
          +{step.period}q
        </span>
        <span style={{ color: escalating ? 'var(--alert)' : 'var(--accent)' }}>
          {jointAction(step.action_a, step.action_b)}
        </span>
        <span style={{ color: 'var(--muted)' }}>
          — {bandLabel(step.intensity_band, bands)}
          {step.band_probability != null && ` (${Math.round(step.band_probability * 100)}% likely)`}
        </span>
      </div>
      {step.market.length > 0 && (
        <div className="ml-10 mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-xs">
          {step.market.slice(0, 4).map((m) => (
            <span key={m.market_id}>
              <span style={{ color: 'var(--muted)' }}>{m.market_name}</span>{' '}
              <span style={{ color: m.median >= 0 ? 'var(--accent)' : 'var(--alert)' }}>
                {marketMove(m.median)}
              </span>{' '}
              <span className="text-[10px]" style={{ color: 'var(--muted)' }}>
                {evidenceNote(m.n, m.thin, m.match === 'quad only')}
              </span>
            </span>
          ))}
        </div>
      )}
    </li>
  )
}
