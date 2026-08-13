import { useEffect, useState } from 'react'
import { getForecasts, getWhatIf, getWhatIfOptions, postAssess } from '../api'
import { useRegionLabel } from '../regions'
import type { Assessment, ForecastSummary, WhatIfOptions, WhatIfResult } from '../types'

/** The reasoning layer, presented as ONE FLOW RATHER THAN THREE WIDGETS.
 *
 *  The three sections are the same machinery pointed at different questions,
 *  and the page used to leave the reader to infer that:
 *
 *    1. THE STANDING CALLS — what this region's archive has already frozen,
 *       and how each is being judged. The archive commits first.
 *    2. WHAT IF — the same structural engine, pointed at a question the
 *       reader poses instead of one the archive posed. Deterministic end to
 *       end, and it opens on a worked example drawn from the region's own
 *       spine, because an empty composer explains nothing.
 *    3. THE AGENT — narration over 1 and 2, and NOTHING ELSE. It returns the
 *       context it was given so the reader can check the numbers came from
 *       upstream (§17), rather than taking the claim on trust.
 *
 *  Only 3 needs a key. 1 and 2 run on the deterministic core forever. */

const selectStyle = {
  background: 'var(--ground)',
  color: 'var(--text)',
  border: '1px solid var(--rule-strong)',
  padding: '0.35rem 0.5rem',
} as const

/** A numbered stage heading — what makes the page read as a sequence. */
function Stage({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <header className="mt-14 first:mt-0">
      <div className="flex items-baseline gap-3">
        <span className="kicker" style={{ color: 'var(--accent)' }}>{n}</span>
        <h2 className="text-xl" style={{ color: 'var(--text)' }}>{title}</h2>
      </div>
      <p className="mt-2 text-sm leading-relaxed max-w-3xl" style={{ color: 'var(--muted)' }}>
        {children}
      </p>
    </header>
  )
}

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

  const [drawnFrom, setDrawnFrom] = useState<WhatIfOptions['example'] | null>(null)

  const [question, setQuestion] = useState('')
  const [asking, setAsking] = useState(false)
  const [answer, setAnswer] = useState<
    { dark: boolean; text: string; context?: Assessment['context'] } | null
  >(null)
  const [trail, setTrail] = useState<ForecastSummary[] | null>(null)
  const regionLabel = useRegionLabel(region)

  useEffect(() => {
    let live = true
    setOptions(undefined)
    setResult(null)
    setAnswer(null)
    setTrail(null)
    setDrawnFrom(null)
    getForecasts(region).then((r) => live && setTrail(r?.rows ?? []))
    getWhatIfOptions(region).then((o) => {
      if (!live) return
      setOptions(o)
      if (!o) return
      // Open on the region's OWN worked example when it has one — a composer
      // seeded with the first two actors alphabetically produces a question
      // nobody asked and an answer nobody can judge.
      const seed = o.example
      const today = new Date().toISOString().slice(0, 10)
      const nextInitiator = seed?.initiator ?? o.actors[0]?.id ?? ''
      const nextTarget = seed?.target ?? o.actors[1]?.id ?? o.actors[0]?.id ?? ''
      const nextCameo = seed?.cameo ?? o.codes[0]?.code ?? ''
      setInitiator(nextInitiator)
      setTarget(nextTarget)
      setCameo(nextCameo)
      setDate(today)
      setDrawnFrom(seed)
      // …and RUN it, so the engine demonstrates itself on arrival rather than
      // waiting behind a button for a reader who does not yet know what it does.
      if (seed) void execute(nextInitiator, nextTarget, nextCameo, today, () => live)
    })
    return () => {
      live = false
    }
    // `region` is the only input that should re-seed the composer; `execute`
    // is stable for a given region and re-running on every keystroke would
    // fight the reader's own edits.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [region])

  const execute = async (
    who: string, whom: string, code: string, when: string, stillLive: () => boolean = () => true,
  ) => {
    if (!who || !whom || !code) return
    setRunning(true)
    // Clear first: a stale result sitting under a spinner reads as the answer
    // to the question being asked right now.
    setResult(null)
    const r = await getWhatIf({ region, initiator: who, target: whom, cameo: code, date: when })
    if (!stillLive()) return
    setResult(r)
    setRunning(false)
  }

  const run = () => execute(initiator, target, cameo, date)

  const ask = async () => {
    if (!question.trim()) return
    setAsking(true)
    setAnswer(null)
    const r = await postAssess(question.trim(), region)
    setAnswer(
      r.ok
        ? { dark: false, text: r.result?.assessment ?? '', context: r.result?.context }
        : { dark: true, text: r.detail ?? 'the agent is unavailable' },
    )
    setAsking(false)
  }

  const composed = Boolean(initiator && target && cameo)

  return (
    <div className="max-w-5xl mx-auto px-6 py-10">
      <p className="kicker">Reasoning · {regionLabel.toUpperCase()}</p>

      <p className="mt-4 text-base leading-relaxed max-w-3xl" style={{ color: 'var(--text)' }}>
        Three things happen on this page, and they are the same machinery
        pointed at different questions: the archive's own frozen calls and how
        they are scoring, the engine that will read a question you pose
        instead, and an agent that argues over both without computing
        anything. The first two need no API key and never will.
      </p>

      {/* ── 1 · the what-if engine ──────────────────────────────────────── */}
      <Stage n={1} title="What if —">
        Pose a hypothetical event and read it through the archive: its coded
        weight, how it lands against the dyad's own standing baseline, the
        regime-admissible analogues, and what those analogues measurably did
        to markets. Deterministic end to end — an analogy, never a prediction.
        {drawnFrom && (
          <>
            {' '}
            <span style={{ color: 'var(--text)' }}>
              Loaded with a worked example — {drawnFrom.drawn_from.name} (
              {drawnFrom.drawn_from.date}), posed again at today's date. The
              event it was drawn from will usually rank among its own
              analogues; that is the engine agreeing with itself, and a useful
              thing to see it do once. Change any field and read again.
            </span>
          </>
        )}
      </Stage>

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
              disabled={running || !composed}
              className="mono text-xs uppercase tracking-widest px-4 py-2 border"
              style={{
                borderColor: 'var(--accent)', color: 'var(--accent)',
                cursor: running ? 'wait' : composed ? 'pointer' : 'not-allowed',
                background: 'transparent',
                opacity: composed ? 1 : 0.5,
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
                  <p className="mt-2 text-sm leading-relaxed max-w-3xl" style={{ color: 'var(--muted)' }}>
                    No event in a comparable regime matches this shape. That is
                    the admissibility gate doing its job, not an empty archive:
                    analogues must sit inside the same monetary order as the
                    date you asked about, so moving the date across a regime
                    boundary changes which history is eligible before any
                    similarity is computed.
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

      {/* ── 2 · the trail ───────────────────────────────────────────────── */}
      <Stage n={2} title="The trail">
        Every frozen call this region has made, with how it is being judged:
        near-term calls are Brier-scored the day their horizon closes, and
        long-horizon calls carry the structural method's retrodiction — its
        record when run against the past. An open horizon is an open
        question, not a zero. These were frozen at boot by the same
        deterministic engine stage 1 just ran for you.
      </Stage>
      {trail === null ? (
        <p className="mt-4 text-sm mono" style={{ color: 'var(--muted)' }}>Reaching the archive…</p>
      ) : trail.length === 0 ? (
        <p className="mt-4 text-sm" style={{ color: 'var(--muted)' }}>
          Nothing frozen for this region yet.
        </p>
      ) : (
        <ul className="mt-5 space-y-4 max-w-4xl">
          {trail.map((f) => (
            <li
              key={f.node_id}
              className="border p-4"
              style={{ borderColor: 'var(--line)', background: 'var(--panel)' }}
            >
              <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                <span
                  className="mono text-[10px] uppercase tracking-wider border px-2 py-0.5"
                  style={{ borderColor: 'var(--line)', color: 'var(--accent)' }}
                >
                  {f.mode === 'near_term' ? 'near term' : 'long horizon'}
                </span>
                <span className="mono text-xs" style={{ color: 'var(--text)' }}>{f.node_id}</span>
                <span className="mono text-[10px]" style={{ color: 'var(--muted)' }}>
                  frozen {f.generated_at?.slice(0, 10)} · horizon {f.horizon_end ?? 'open'}
                </span>
              </div>
              <p className="mt-2 text-sm" style={{ color: 'var(--muted)' }}>{f.question}</p>
              {f.mode === 'near_term' ? (
                <p className="mono text-xs mt-2" style={{ color: 'var(--text)' }}>
                  {f.brier_score !== null
                    ? `Brier ${f.brier_score.toFixed(4)} — 0 is perfect, 0.25 is coin-flip calling`
                    : `unresolved — the horizon runs through ${f.horizon_end ?? '?'}`}
                </p>
              ) : (
                <div className="mt-2">
                  {f.retrodiction ? (
                    f.retrodiction.hit_rate !== null ? (
                      <p className="mono text-xs" style={{ color: 'var(--text)' }}>
                        retrodiction as of {f.retrodiction.as_of}: hit rate{' '}
                        {(f.retrodiction.hit_rate * 100).toFixed(0)}% vs base rate{' '}
                        {f.retrodiction.base_rate !== null
                          ? `${(f.retrodiction.base_rate * 100).toFixed(0)}%`
                          : '?'}{' '}
                        — reported beside each other, never adjudicated
                      </p>
                    ) : (
                      <p className="mono text-xs" style={{ color: 'var(--muted)' }}>
                        retrodiction as of {f.retrodiction.as_of}:{' '}
                        {f.retrodiction.verdict ?? 'no verdict'}
                      </p>
                    )
                  ) : (
                    <p className="mono text-xs" style={{ color: 'var(--muted)' }}>
                      retrodiction pending — attached by the calibration pass on boot
                    </p>
                  )}
                  {f.boundary_statement && (
                    <p className="text-xs italic mt-2" style={{ color: 'var(--muted)' }}>
                      {f.boundary_statement}
                    </p>
                  )}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}

      {/* ── 3 · the agent ───────────────────────────────────────────────── */}
      <Stage n={3} title="Ask the agent">
        A narrated assessment over the frozen numbers above — the agent
        argues, it never computes. It is handed this region's frozen calls and
        its most conflictual dyad baselines, and it gets nothing else; the
        answer comes back with that exact context attached so you can check
        every figure it cites against what it was given. It runs only when the
        archive has an API key to think with, and says so plainly when it does
        not.
      </Stage>
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
          <div className="boxed p-5 mt-4">
            {answer.dark ? (
              <p className="text-sm italic leading-relaxed" style={{ color: 'var(--muted)' }}>
                {answer.text}
              </p>
            ) : (
              <>
                <p className="text-sm leading-relaxed whitespace-pre-wrap" style={{ color: 'var(--text)' }}>
                  {answer.text}
                </p>
                {answer.context && (
                  <details className="mt-5">
                    <summary className="kicker" style={{ cursor: 'pointer' }}>
                      What the agent was given
                    </summary>
                    <p className="mt-3 text-xs leading-relaxed" style={{ color: 'var(--muted)' }}>
                      Every number in the prose above should appear here. One
                      that does not is the agent originating a figure, which
                      section 17 forbids — and which you can now catch.
                    </p>
                    <ul className="mt-3 space-y-1">
                      {(answer.context.frozen_forecasts ?? []).map((f) => (
                        <li key={f.node_id} className="mono text-[11px]" style={{ color: 'var(--text)' }}>
                          {f.node_id}{' '}
                          <span style={{ color: 'var(--muted)' }}>{f.question}</span>
                        </li>
                      ))}
                      {(answer.context.most_conflictual_dyads ?? []).map((d) => (
                        <li key={d.node_id} className="mono text-[11px]" style={{ color: 'var(--text)' }}>
                          {d.name ?? d.node_id}{' '}
                          <span style={{ color: 'var(--muted)' }}>
                            baseline {d.baseline === null ? 'none' : d.baseline.toFixed(2)}
                          </span>
                        </li>
                      ))}
                    </ul>
                    {answer.context.note && (
                      <p className="mt-3 text-xs italic" style={{ color: 'var(--muted)' }}>
                        {answer.context.note}
                      </p>
                    )}
                  </details>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
