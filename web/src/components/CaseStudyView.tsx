import { useEffect, useState } from 'react'
import { getCaseStudy } from '../api'
import type { CaseStudy, CaseStudyEpisode, Effect } from '../types'

const pct = (value: number | null | undefined) =>
  value == null ? '—' : `${value >= 0 ? '+' : ''}${(value * 100).toFixed(2)}%`

const num = (value: number | null | undefined, digits = 2) =>
  value == null ? '—' : value.toFixed(digits)

/** A p-value gets a weight, not a verdict: significance is shown by emphasis
 *  and stated numerically, never converted into a claim the study did not make. */
function significance(p: number | null | undefined) {
  if (p == null) return { weight: 400, note: 'no test' }
  if (p < 0.01) return { weight: 700, note: 'p < 0.01' }
  if (p < 0.05) return { weight: 600, note: 'p < 0.05' }
  return { weight: 400, note: `p = ${p.toFixed(3)}` }
}

function EffectsTable({ effects }: { effects: Effect[] }) {
  if (effects.length === 0) {
    return (
      <p className="text-sm mt-4" style={{ color: 'var(--muted)' }}>
        Nothing measured for this event yet. That is not the same as no effect —
        the transmission engine has not run, or every market was skipped.
      </p>
    )
  }
  const windows = [...new Set(effects.map((e) => e.window))].sort()
  const tickers = [...new Set(effects.map((e) => e.ticker))]
  const byKey = new Map(effects.map((e) => [`${e.ticker}|${e.window}`, e]))

  return (
    <div className="mt-4 overflow-x-auto">
      <table className="w-full text-sm border-collapse">
        <thead>
          <tr style={{ color: 'var(--muted)' }}>
            <th className="text-left font-normal py-2 pr-4">Market</th>
            {windows.map((w) => (
              <th key={w} className="text-right font-normal py-2 px-3 mono text-xs">
                {w}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tickers.map((ticker) => {
            const row = effects.find((e) => e.ticker === ticker)
            return (
              <tr key={ticker} style={{ borderTop: '1px solid var(--line)' }}>
                <td className="py-3 pr-4">
                  <div>{row?.market ?? ticker}</div>
                  <div className="mono text-xs" style={{ color: 'var(--muted)' }}>
                    {ticker}
                    {row?.first_mover && (
                      <span style={{ color: 'var(--accent)' }}> · first mover</span>
                    )}
                  </div>
                </td>
                {windows.map((w) => {
                  const cell = byKey.get(`${ticker}|${w}`)
                  const sig = significance(cell?.p_value)
                  return (
                    <td key={w} className="py-3 px-3 text-right align-top">
                      <div
                        className="mono"
                        style={{
                          fontWeight: sig.weight,
                          color:
                            cell?.abnormal_return == null
                              ? 'var(--muted)'
                              : cell.abnormal_return >= 0
                                ? 'var(--text)'
                                : 'var(--alert)',
                        }}
                      >
                        {pct(cell?.abnormal_return)}
                      </div>
                      <div className="mono text-xs" style={{ color: 'var(--muted)' }}>
                        {cell ? `t ${num(cell.t_stat)} · ${sig.note}` : ''}
                        {cell?.overlapping && (
                          <span style={{ color: 'var(--alert)' }}> · overlap</span>
                        )}
                      </div>
                    </td>
                  )
                })}
              </tr>
            )
          })}
        </tbody>
      </table>
      <p className="mt-3 text-xs leading-relaxed" style={{ color: 'var(--muted)' }}>
        Abnormal return against a constant-mean baseline estimated over the 120
        sessions before the event, with a five-session gap. Windows count
        sessions from each market's own first tradable session after the event —
        which is why the Gulf and New York do not share a column meaning.
        <span className="mono"> overlap</span> marks a window another event falls
        inside; those figures are flagged rather than averaged away.
      </p>
    </div>
  )
}

function Episode({ episode }: { episode: CaseStudyEpisode }) {
  if (episode.missing) {
    return (
      <section className="mt-12">
        <h3 className="text-xl">{episode.node_id}</h3>
        <p className="text-sm mt-2" style={{ color: 'var(--alert)' }}>
          {episode.missing}
        </p>
      </section>
    )
  }
  const magnitude = episode.escalation_magnitude ?? 0
  const firstObservation = magnitude === 0 && episode.escalation_direction === 'stable'

  return (
    <section className="mt-14">
      <div className="mono text-xs uppercase tracking-widest" style={{ color: 'var(--accent)' }}>
        {episode.event_time}
      </div>
      <h3 className="text-2xl mt-1">{episode.name}</h3>
      {episode.note && (
        <p className="mt-3 text-base leading-relaxed" style={{ color: 'var(--muted)', maxWidth: '68ch' }}>
          {episode.note}
        </p>
      )}

      <dl
        className="mt-6 grid grid-cols-2 sm:grid-cols-4 gap-5 py-4 border-y text-sm"
        style={{ borderColor: 'var(--line)' }}
      >
        <div>
          <dt className="mono text-xs uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
            CAMEO
          </dt>
          <dd className="mono mt-1">{episode.cameo_code}</dd>
        </div>
        <div>
          <dt className="mono text-xs uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
            Goldstein
          </dt>
          <dd className="mono mt-1">{num(episode.goldstein, 1)}</dd>
        </div>
        <div>
          <dt className="mono text-xs uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
            Dyad
          </dt>
          <dd className="mt-1">{episode.dyad?.name ?? '—'}</dd>
        </div>
        <div>
          <dt className="mono text-xs uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
            Escalation
          </dt>
          <dd className="mt-1">
            {/* A first observation has no history to depart from. Rendering that
                as "stable" without saying so would read as calm. */}
            {firstObservation ? (
              <span style={{ color: 'var(--muted)' }}>no prior history in this dyad</span>
            ) : (
              <>
                {episode.escalation_direction}{' '}
                <span className="mono" style={{ color: 'var(--muted)' }}>
                  {num(magnitude, 2)} vs baseline {num(episode.escalation_baseline, 2)}
                </span>
              </>
            )}
          </dd>
        </div>
      </dl>

      <EffectsTable effects={episode.effects ?? []} />
    </section>
  )
}

export default function CaseStudyView({
  slug,
  onNavigate,
}: {
  slug: string
  onNavigate: (route: string) => void
}) {
  const [study, setStudy] = useState<CaseStudy | null>(null)
  const [missing, setMissing] = useState(false)

  useEffect(() => {
    let active = true
    getCaseStudy(slug).then((result) => {
      if (!active) return
      setStudy(result)
      setMissing(result === null)
    })
    return () => {
      active = false
    }
  }, [slug])

  if (missing) {
    return (
      <div className="px-6 py-16 max-w-3xl mx-auto">
        <p style={{ color: 'var(--alert)' }}>
          No case study named <span className="mono">{slug}</span> is available.
        </p>
        <button
          type="button"
          className="mt-6 underline"
          style={{ color: 'var(--accent)', background: 'none', border: 'none' }}
          onClick={() => onNavigate('/')}
        >
          Back to the front
        </button>
      </div>
    )
  }

  if (!study) {
    return (
      <p className="px-6 py-16 text-center" style={{ color: 'var(--muted)' }}>
        Loading the case study…
      </p>
    )
  }

  return (
    <article className="px-6 py-14 max-w-4xl mx-auto">
      <nav className="mb-10 flex gap-6 text-sm">
        <button
          type="button"
          onClick={() => onNavigate('/')}
          className="underline underline-offset-4"
          style={{ color: 'var(--muted)', background: 'none', border: 'none' }}
        >
          GeoGraph
        </button>
        <button
          type="button"
          onClick={() => onNavigate('/explore')}
          className="underline underline-offset-4"
          style={{ color: 'var(--muted)', background: 'none', border: 'none' }}
        >
          Explore the network
        </button>
      </nav>

      <p className="mono text-xs uppercase tracking-[0.3em]" style={{ color: 'var(--muted)' }}>
        Case study · {study.pack}
      </p>
      <h1 className="text-5xl mt-4 leading-tight">{study.title}</h1>
      <p className="mt-5 text-xl leading-snug" style={{ color: 'var(--text)', maxWidth: '54ch' }}>
        {study.dek}
      </p>

      {study.status !== 'measured' && (
        <p
          className="mt-8 p-4 text-sm"
          style={{ border: '1px solid var(--alert)', color: 'var(--alert)' }}
        >
          The transmission engine has not run for this episode, so the figures
          below are absent. The narrative is stated as an expectation, not as a
          finding.
        </p>
      )}

      <p className="mt-10 text-lg leading-relaxed" style={{ maxWidth: '68ch' }}>
        {study.summary}
      </p>

      {study.episodes.map((episode) => (
        <Episode key={episode.node_id} episode={episode} />
      ))}

      <section className="mt-16 pt-8 border-t" style={{ borderColor: 'var(--line)' }}>
        <h2 className="mono text-xs uppercase tracking-[0.3em]" style={{ color: 'var(--accent)' }}>
          Reading the measurements
        </h2>
        <p className="mt-4 text-lg leading-relaxed" style={{ maxWidth: '68ch' }}>
          {study.reading}
        </p>
        {study.caveat && (
          <p
            className="mt-6 pl-5 text-base leading-relaxed"
            style={{ color: 'var(--muted)', borderLeft: '2px solid var(--line)', maxWidth: '68ch' }}
          >
            {study.caveat}
          </p>
        )}
      </section>
    </article>
  )
}
