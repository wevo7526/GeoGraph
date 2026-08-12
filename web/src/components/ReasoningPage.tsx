import { useEffect, useState } from 'react'
import { getWhatIf, getWhatIfOptions, postAssess } from '../api'
import type { WhatIfOptions, WhatIfResult } from '../types'

/** The reasoning layer's two halves, degrading honestly: the deterministic
 *  what-if engine (always on — codebook, dyad baselines, admissible
 *  analogues, measured transmission BY ANALOGY), and the LLM agent, which
 *  is dark until ANTHROPIC_API_KEY exists and says so in its own words. */

const selectStyle = {
  background: 'var(--panel)',
  color: 'var(--text)',
  border: '1px solid var(--line)',
  padding: '0.35rem 0.5rem',
} as const

function DirectionChip({ direction, magnitude }: { direction: string; magnitude: number }) {
  const hot = direction === 'escalating'
  return (
    <span
      className="mono text-[10px] uppercase tracking-wider border px-2 py-1"
      style={{
        borderColor: hot ? 'var(--alert)' : 'var(--line)',
        color: hot ? 'var(--alert)' : 'var(--muted)',
      }}
    >
      {direction} · Δ{magnitude.toFixed(1)}
    </span>
  )
}

export default function ReasoningPage({
  region,
}: {
  region: string
  onNavigate: (route: string) => void
}) {
  const [options, setOptions] = useState<WhatIfOptions | null | undefined>(undefined)
  const [initiator, setInitiator] = useState('')
  const [target, setTarget] = useState('')
  const [cameo, setCameo] = useState('')
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState<WhatIfResult | null>(null)

  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [answer, setAnswer] = useState<{ dark: boolean; text: string } | null>(null)

  useEffect(() => {
    setOptions(undefined)
    setResult(null)
    setAnswer(null)
    getWhatIfOptions(region).then((o) => {
      setOptions(o)
      if (o) {
        setInitiator(o.actors[0]?.id ?? '')
        setTarget(o.actors[1]?.id ?? o.actors[0]?.id ?? '')
        setCameo(o.codes[0]?.code ?? '')
      }
    })
  }, [region])

  const run = async () => {
    if (!initiator || !target || !cameo) return
    setRunning(true)
    const r = await getWhatIf({ region, initiator, target, cameo, date })
    setResult(r)
    setRunning(false)
  }

  const ask = async () => {
    if (!question.trim()) return
    setAsking(true)
    const r = await postAssess(question.trim(), region)
    setAnswer(
      r.ok
        ? { dark: false, text: r.result?.assessment ?? '' }
        : { dark: true, text: r.detail ?? 'the agent is unavailable' },
    )
    setAsking(false)
  }

  return (
    <div className="max-w-5xl mx-auto px-6 py-10">
      <p className="mono text-xs uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
        Reasoning · {region.toUpperCase()}
      </p>

      {/* ── The what-if engine ──────────────────────────────────────────── */}
      <h2 className="text-xl mt-6" style={{ color: 'var(--text)' }}>
        What if —
      </h2>
      <p className="mt-2 text-sm leading-relaxed max-w-3xl" style={{ color: 'var(--muted)' }}>
        Pose a hypothetical event and read it through the archive: its coded
        weight, how it lands against the dyad's own standing baseline, the
        regime-admissible analogues, and what those analogues measurably did
        to markets. Deterministic end to end — an analogy, never a prediction.
      </p>

      {options === undefined ? (
        <p className="mt-6 text-sm mono" style={{ color: 'var(--muted)' }}>Reaching the archive…</p>
      ) : options === null ? (
        <p className="mt-6 text-sm" style={{ color: 'var(--muted)' }}>
          The reasoning surface is unreachable.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-end gap-3 mt-6">
            <label className="block">
              <span className="mono text-[10px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
                Initiator
              </span>
              <select value={initiator} onChange={(e) => setInitiator(e.target.value)}
                className="block mt-1" style={selectStyle}>
                {options.actors.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mono text-[10px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
                Target
              </span>
              <select value={target} onChange={(e) => setTarget(e.target.value)}
                className="block mt-1" style={selectStyle}>
                {options.actors.map((a) => (
                  <option key={a.id} value={a.id}>{a.name}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mono text-[10px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
                Event type (CAMEO)
              </span>
              <select value={cameo} onChange={(e) => setCameo(e.target.value)}
                className="block mt-1 max-w-[22rem]" style={selectStyle}>
                {options.codes.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.code} · {c.label} ({c.goldstein > 0 ? '+' : ''}{c.goldstein})
                  </option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mono text-[10px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
                Date
              </span>
              <input type="date" value={date} onChange={(e) => setDate(e.target.value)}
                className="block mt-1 mono text-sm" style={selectStyle} />
            </label>
            <button
              type="button"
              onClick={run}
              disabled={running}
              className="mono text-xs uppercase tracking-widest px-4 py-2 border"
              style={{
                borderColor: 'var(--accent)', color: 'var(--accent)',
                cursor: running ? 'wait' : 'pointer', background: 'transparent',
              }}
            >
              {running ? 'Reading…' : 'Read the archive'}
            </button>
          </div>

          {result && (
            <div className="mt-8 space-y-8">
              <div className="border p-5" style={{ borderColor: 'var(--line)', background: 'var(--panel)' }}>
                <p className="text-sm leading-relaxed" style={{ color: 'var(--text)' }}>
                  <span className="mono">{result.hypothetical.cameo}</span>{' '}
                  {result.hypothetical.label} — Goldstein{' '}
                  <span className="mono">
                    {result.hypothetical.goldstein > 0 ? '+' : ''}{result.hypothetical.goldstein}
                  </span>
                  , {result.hypothetical.quad_class.replace('_', ' ')}.
                </p>
                <div className="flex flex-wrap items-center gap-3 mt-3">
                  <DirectionChip
                    direction={result.dyad.escalation_direction}
                    magnitude={result.dyad.escalation_magnitude}
                  />
                  <span className="mono text-xs" style={{ color: 'var(--muted)' }}>
                    {result.dyad.name ?? result.dyad.node_id}
                    {result.dyad.baseline !== null &&
                      ` · baseline ${result.dyad.baseline.toFixed(2)} as of ${result.dyad.baseline_as_of}`}
                  </span>
                </div>
                {result.dyad.note && (
                  <p className="mt-3 text-xs italic" style={{ color: 'var(--muted)' }}>
                    {result.dyad.note}
                  </p>
                )}
              </div>

              <div>
                <h3 className="text-lg" style={{ color: 'var(--text)' }}>Admissible analogues</h3>
                {result.analogues.length === 0 ? (
                  <p className="mt-2 text-sm" style={{ color: 'var(--muted)' }}>
                    No event in a comparable regime matches this shape.
                  </p>
                ) : (
                  <ul className="mt-3 space-y-2">
                    {result.analogues.map((a) => (
                      <li
                        key={a.event_id}
                        className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b pb-2"
                        style={{ borderColor: 'var(--line)' }}
                      >
                        <span className="mono text-xs" style={{ color: 'var(--accent)' }}>
                          {a.similarity.toFixed(2)}
                        </span>
                        <span className="text-sm" style={{ color: 'var(--text)' }}>{a.name}</span>
                        <span className="mono text-xs" style={{ color: 'var(--muted)' }}>
                          {a.event_time} · {a.escalation_direction ?? 'uncoded'} ·{' '}
                          {a.measured_effects} measured effect{a.measured_effects === 1 ? '' : 's'}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              {result.transmission.rows.length > 0 && (
                <div>
                  <h3 className="text-lg" style={{ color: 'var(--text)' }}>Transmission, by analogy</h3>
                  <div className="overflow-x-auto mt-3 border max-w-3xl" style={{ borderColor: 'var(--line)' }}>
                    <table className="w-full mono text-xs">
                      <thead>
                        <tr style={{ color: 'var(--muted)' }}>
                          {['Market', 'Window', 'Mean abnormal return', 'n'].map((h) => (
                            <th key={h} className="text-left px-3 py-2 border-b" style={{ borderColor: 'var(--line)' }}>
                              {h}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody style={{ color: 'var(--text)' }}>
                        {result.transmission.rows.map((r) => (
                          <tr key={`${r.ticker}-${r.window}`}>
                            <td className="px-3 py-1.5">{r.market} <span style={{ color: 'var(--muted)' }}>{r.ticker}</span></td>
                            <td className="px-3 py-1.5">{r.window}</td>
                            <td className="px-3 py-1.5">
                              {(r.mean_abnormal_return * 100).toFixed(2)}%
                            </td>
                            <td className="px-3 py-1.5">{r.n}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <p className="mt-2 text-xs italic max-w-3xl" style={{ color: 'var(--muted)' }}>
                    {result.transmission.label}
                  </p>
                </div>
              )}

              <p className="mono text-[10px] leading-relaxed max-w-4xl" style={{ color: 'var(--muted)' }}>
                {result.method}
              </p>
            </div>
          )}
        </>
      )}

      {/* ── The agent ───────────────────────────────────────────────────── */}
      <h2 className="text-xl mt-14" style={{ color: 'var(--text)' }}>
        Ask the agent
      </h2>
      <p className="mt-2 text-sm leading-relaxed max-w-3xl" style={{ color: 'var(--muted)' }}>
        A narrated assessment over the frozen numbers — the agent argues, it
        never computes. It runs only when the archive has an API key to think
        with, and answers honestly when it does not.
      </p>
      <div className="mt-4 max-w-3xl">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          rows={3}
          placeholder="e.g. Where does the standing escalation pressure in this region actually bind?"
          className="w-full text-sm p-3"
          style={{ ...selectStyle, resize: 'vertical' }}
        />
        <button
          type="button"
          onClick={ask}
          disabled={asking || !question.trim()}
          className="mono text-xs uppercase tracking-widest px-4 py-2 border mt-2"
          style={{
            borderColor: 'var(--accent)', color: 'var(--accent)',
            cursor: asking ? 'wait' : 'pointer', background: 'transparent',
            opacity: question.trim() ? 1 : 0.5,
          }}
        >
          {asking ? 'Thinking…' : 'Ask'}
        </button>
        {answer && (
          <div
            className="border p-5 mt-4"
            style={{ borderColor: 'var(--line)', background: 'var(--panel)' }}
          >
            {answer.dark ? (
              <p className="text-sm italic leading-relaxed" style={{ color: 'var(--muted)' }}>
                {answer.text}
              </p>
            ) : (
              <p className="text-sm leading-relaxed whitespace-pre-wrap" style={{ color: 'var(--text)' }}>
                {answer.text}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
