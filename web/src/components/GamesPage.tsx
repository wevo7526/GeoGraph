/** The game-theory page: a region's future-event map from solved games, and
 *  the drill-in for one dyad's solved game — both concepts (the LP correlated
 *  equilibrium with its nash_gap, and the fitted quantal response), the ML
 *  tilt, the priced courses of play, and an explanation written from the
 *  numbers (core/games/scenarios.py). Persisted-first: the payload says when
 *  it was solved and whether it was persisted or solved on request. */
import { useEffect, useMemo, useState } from 'react'
import { getDyadSolution, getRegionMap, lastFailureFor } from '../api'
import { useRegionLabel } from '../regions'
import type { ConceptSolution, DyadSolution, RegionMap, Scenario } from '../types'
import { Beat, Chip, Disclosure, Empty, StoryHead } from '../ui'
import { familyRead, postureNote, standingChip, standingLabel } from '../lib/language'
import { BandHeat, Bars, MultiLine, PayoffMatrix, Tiles, pct } from './charts/Kit'

const KIND_LABEL: Record<string, string> = {
  mutual_escalation: 'mutual escalation',
  one_sided_pressure: 'one-sided pressure',
  brinkmanship: 'brinkmanship',
  probe_and_retreat: 'probe and retreat',
  step_down: 'step-down',
  drift_up: 'drift up',
  drift_down: 'drift down',
  holding_pattern: 'holding pattern',
}
const kind = (k: string) => KIND_LABEL[k] ?? k.replace(/_/g, ' ')

function dyadFromRoute(): string | null {
  const q = window.location.hash.split('?')[1]
  return q ? new URLSearchParams(q).get('dyad') : null
}

export default function GamesPage({
  region,
  onNavigate,
}: {
  region: string
  onNavigate: (route: string) => void
}) {
  const [dyad, setDyad] = useState<string | null>(dyadFromRoute)
  useEffect(() => {
    const sync = () => setDyad(dyadFromRoute())
    window.addEventListener('hashchange', sync)
    return () => window.removeEventListener('hashchange', sync)
  }, [])
  const open = (id: string | null) =>
    onNavigate(id ? `/games?dyad=${encodeURIComponent(id)}&region=${encodeURIComponent(region)}` : '/games')
  return dyad ? (
    <DyadGame region={region} dyad={dyad} onBack={() => open(null)} onPick={open} onNavigate={onNavigate} />
  ) : (
    <RegionGames region={region} onPick={open} />
  )
}

// ── the region map ────────────────────────────────────────────────────────

function RegionGames({ region, onPick }: { region: string; onPick: (dyad: string) => void }) {
  const label = useRegionLabel(region)
  const [map, setMap] = useState<RegionMap | null | undefined>(undefined)
  useEffect(() => {
    let live = true
    setMap(undefined)
    getRegionMap(region).then((m) => live && setMap(m))
    return () => { live = false }
  }, [region])

  if (map === undefined) return <div className="reading-column py-10"><Empty>Solving the region…</Empty></div>
  // The map is being re-solved (a payload-shape change): say so and let the
  // reader come back, rather than holding the request open for ~130s.
  if (map && (map as RegionMap & { resolving?: boolean }).resolving) {
    return (
      <div className="reading-column py-10">
        <StoryHead kicker={`Game theory · ${label.toUpperCase()}`}
                   title="Re-solving this region"
                   standfirst={map.note ?? 'The scenario map is being rebuilt for the current shape; it lands within a few minutes.'} />
      </div>
    )
  }
  if (map === null) {
    const f = lastFailureFor('/api/games/region')
    return (
      <div className="reading-column py-10">
        <StoryHead kicker={`Game theory · ${label.toUpperCase()}`} title="No solved games for this region"
                   standfirst={f?.detail ?? 'The API did not answer.'} />
      </div>
    )
  }
  const bands = map.band_labels
  const ranking = map.ranking
  const lead = ranking[0]
  // The bar's scale: the region's own leader, so the lengths are comparable
  // within a region and never pretend to be comparable across them.
  const topCoercive = Math.max(1, ...ranking.map((r) => r.coercive_events ?? 0))

  return (
    <div className="reading-column py-8">
      <StoryHead
        kicker={`Game theory · ${label.toUpperCase()} · ${(map.solvers ?? [map.primary_solver]).map((s) => s.toUpperCase()).join(' + ')}`}
        title="The next four quarters, solved"
        standfirst={map.explanation[0]}
        action={
          <span className="mono text-[11px] text-right" style={{ color: 'var(--muted)' }}>
            as of {map.as_of}<br />
            {map.persisted ? `solved ${map.computed_at?.slice(0, 16).replace('T', ' ')} UTC` : 'solved on request · not persisted'}
          </span>
        }
      />

      <div className="mt-8">
        <Tiles items={[
          { label: 'pairs solved', value: String(map.dyads_solved), sub: `${map.dyads_cinc} with CINC capability` },
          { label: 'tilted by the model', value: String(map.dyads_tilted), sub: map.model ? `${map.model.name}@${map.model.hash.slice(0, 8)}` : 'no frozen model' },
          { label: 'LP nash gap (mean)', value: map.nash_gap.mean !== null ? map.nash_gap.mean.toFixed(3) : '—', sub: map.nash_gap.max !== null ? `worst dyad ${map.nash_gap.max.toFixed(3)}` : undefined },
          { label: 'kernel measured', value: pct(map.kernel.share_measured, 0), sub: `${map.kernel.observations.toLocaleString('en-US')} dyad-quarters` },
        ]} />
      </div>

      <Beat n={1} title="Where coercion is being measured" major aside="coercive events in the last four quarters, and the game's own departure odds beside them">
        {lead && (
          <p className="text-sm mb-4" style={{ maxWidth: '64ch' }}>
            <b>{lead.dyad_name}</b>{standingLabel(lead.standing) ? ` — ${standingLabel(lead.standing)}` : ''}{familyRead(lead.family) ? `, read as ${familyRead(lead.family)!.label}` : ''} — carries the most: {(lead.coercive_events ?? 0).toLocaleString('en-US')} coercive events
            {lead.coercive_share != null ? `, ${pct(lead.coercive_share, 0)} of its record` : ''}, opening at a {lead.opening_label}
            {lead.top_scenario ? `; the likeliest kind of course is ${kind(lead.top_scenario.kind)} at ${pct(lead.top_scenario.likelihood, 0)} of its walk` : ''}.
            {' '}The <b>bar</b> is that measured count. The <b>percentage</b> beside it is a different
            question — the odds this pair departs from its <em>own</em> usual band — which is why a quiet
            ally can score high on it and a settled rivalry low. Click a pair to open its solved game.
          </p>
        )}
        <div className="space-y-1">
          {ranking.map((r) => (
            <div key={r.dyad_id} className="flex items-center gap-3 text-xs cursor-pointer" onClick={() => onPick(r.dyad_id)}>
              <span className="w-44 shrink-0 truncate" title={r.dyad_name}>{r.dyad_name}</span>
              {/* Fixed width AND overflow-hidden: the long form ("formal allies
                  since 1949") spilled out of the chip and ran under the bar. */}
              <span className="w-24 shrink-0 overflow-hidden whitespace-nowrap"
                    title={[standingLabel(r.standing), postureNote(r.posture)].filter(Boolean).join(' · ')}>
                <Chip label={standingChip(r.standing) ?? r.posture?.label ?? 'unaligned'}
                      tone={r.standing?.relations?.length ? 'ink' : 'muted'} />
              </span>
              {/* WHICH GAME: an ally's number is friction, not odds of war. */}
              <span className="w-20 shrink-0 overflow-hidden whitespace-nowrap"
                    title={familyRead(r.family)?.why ?? 'unclassified'}>
                {familyRead(r.family)
                  ? <Chip label={familyRead(r.family)!.label.replace(' pair', '')} tone={familyRead(r.family)!.tone} />
                  : <Chip label="—" tone="muted" />}
              </span>
              {/* THE BAR IS THE MEASURED COUNT, scaled against the region's
                  own leader — the ordering and the length now agree. It used
                  to draw the departure probability, which is relative to each
                  pair's baseline and so put three alliances above US-China. */}
              <span className="relative flex-1 h-3 min-w-[3rem]" style={{ background: 'var(--panel)' }}>
                <span className="absolute top-0 bottom-0 left-0" style={{ width: `${Math.max(0, Math.min(1, (r.coercive_events ?? 0) / Math.max(1, topCoercive))) * 100}%`, background: (r.coercive_events ?? 0) >= topCoercive / 2 ? 'var(--alert)' : 'var(--accent)' }} />
              </span>
              <span className="mono w-14 text-right shrink-0" title="coercive events measured in the last four quarters">{(r.coercive_events ?? 0).toLocaleString('en-US')}</span>
              <span className="mono w-12 text-right shrink-0" title="P(this pair departs from its OWN usual band after 4 quarters)" style={{ color: 'var(--muted)' }}>{pct(r.sharp_departure_probability, 0)}</span>
              <span className="mono w-36 shrink-0 text-right truncate"
                    title={`${r.opening_label}${r.tilted ? ' · tilted by the model' : ''}`}
                    style={{ color: 'var(--muted)' }}>{r.opening_label}{r.tilted ? ' ·⌁' : ''}</span>
            </div>
          ))}
        </div>
        <div className="mt-6">
          <div className="kicker mb-2">The region fan — dyad-average mass by band, period by period</div>
          <BandHeat rows={map.region_fan.map((r) => r.distribution)} bandLabels={bands} />
        </div>
        <div className="mt-6">
          <div className="kicker mb-2">Expected departure band by pair and quarter ({map.primary_solver.toUpperCase()})</div>
          <ExpectedHeat heat={map.heat} bands={bands.length} onPick={onPick} />
        </div>
      </Beat>

      <Beat n={2} title="The scenarios" aside={`${map.scenarios_all.length} kinds of course named across ${map.dyads_solved} pairs · each pair's kinds sum to one`}>
        <div className="grid md:grid-cols-2 gap-8">
          <div>
            <div className="kicker mb-2" style={{ color: 'var(--alert)' }}>Courses that press — most mass first</div>
            <ScenarioList rows={map.scenarios_escalatory} onPick={onPick} />
          </div>
          <div>
            <div className="kicker mb-2" style={{ color: 'var(--accent)' }}>Courses that step down</div>
            <ScenarioList rows={map.scenarios_calming.slice(0, 6)} onPick={onPick} />
          </div>
        </div>
      </Beat>

      <Beat n={3} title="How it was solved">
        {map.explanation.slice(1).map((p, i) => (
          <p key={i} className="text-sm leading-relaxed mb-3" style={{ maxWidth: '68ch' }}>{p}</p>
        ))}
        <Disclosure label="the concepts, the payoffs, the kernel">
          <dl className="statline mt-2">
            {Object.entries(map.payoffs ?? {}).map(([k, v]) => (
              <div key={k}><dt>{k.replace('_', ' ')}</dt><dd>{v.toFixed(3)}</dd></div>
            ))}
          </dl>
          <ul className="mt-3 text-xs space-y-1" style={{ color: 'var(--muted)' }}>
            {Object.entries(map.concepts).map(([k, v]) => <li key={k}><b>{k.toUpperCase()}</b>: {v}</li>)}
            <li>kernel: {map.kernel.measured} of {map.kernel.cells} cells measured, {map.kernel.fallback} fallback</li>
          </ul>
        </Disclosure>
        <p className="mt-4 text-xs italic" style={{ color: 'var(--muted)', maxWidth: '68ch' }}>{map.boundary_statement}</p>
      </Beat>
    </div>
  )
}

function ExpectedHeat({
  heat, bands, onPick,
}: { heat: RegionMap['heat']; bands: number; onPick: (dyad: string) => void }) {
  return (
    <div className="scroll-x">
      <table className="text-[11px] mono" style={{ borderCollapse: 'separate', borderSpacing: 2 }}>
        <thead>
          <tr>
            <th className="font-normal text-left pr-2" style={{ color: 'var(--muted)' }}>pair</th>
            <th className="font-normal px-2" style={{ color: 'var(--muted)' }}>opening</th>
            {heat[0]?.expected_band.map((_, i) => (
              <th key={i} className="font-normal px-2" style={{ color: 'var(--muted)' }}>+{i + 1}q</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {heat.map((h) => (
            <tr key={h.dyad_id} className="cursor-pointer" onClick={() => onPick(h.dyad_id)}>
              <td className="pr-2 whitespace-nowrap">{h.dyad_name}</td>
              <td className="text-center">{h.opening_band}</td>
              {h.expected_band.map((v, i) => {
                const share = v / Math.max(bands - 1, 1)
                const up = v > h.opening_band + 0.15
                const down = v < h.opening_band - 0.15
                return (
                  <td key={i} className="text-center" style={{
                    minWidth: 52, height: 24,
                    background: `color-mix(in srgb, ${up ? 'var(--alert)' : down ? 'var(--accent)' : 'var(--muted)'} ${Math.round(20 + share * 60)}%, var(--ground))`,
                    color: share > 0.5 ? 'var(--ground)' : 'var(--text)',
                  }} title={`expected band ${v.toFixed(2)}`}>
                    {v.toFixed(2)}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mono text-[11px] mt-1" style={{ color: 'var(--muted)' }}>
        oxblood: above the opening band · blue: below · grey: holding. Ink weight = level.
      </p>
    </div>
  )
}

function ScenarioList({ rows, onPick }: { rows: Scenario[]; onPick?: (dyad: string) => void }) {
  if (!rows.length) return <Empty>none named</Empty>
  const top = Math.max(...rows.map((r) => r.likelihood), 1e-9)
  return (
    <ol className="space-y-3">
      {rows.map((sc) => (
        <li key={sc.scenario_name + sc.course} className={`text-sm ${onPick ? 'cursor-pointer' : ''}`}
            onClick={onPick ? () => onPick(sc.dyad_id) : undefined}>
          <div className="flex items-baseline gap-2">
            <span className="figure w-12 shrink-0" style={{ color: sc.delta_band > 0 ? 'var(--alert)' : sc.delta_band < 0 ? 'var(--accent)' : 'var(--text)' }}>
              {pct(sc.likelihood, 0)}
            </span>
            <span className="truncate"><b>{sc.dyad_name}</b> — {kind(sc.kind)}</span>
            {standingLabel(sc.standing) && <Chip label={standingLabel(sc.standing) as string} tone="ink" />}
          </div>
          <div className="relative h-1 mt-1 ml-14" style={{ background: 'var(--panel)' }}>
            <div className="absolute inset-y-0 left-0" style={{
              width: `${(sc.likelihood / top) * 100}%`,
              background: sc.delta_band > 0 ? 'var(--alert)' : sc.delta_band < 0 ? 'var(--accent)' : 'var(--muted)',
            }} />
          </div>
          <div className="ml-14 mono text-[11px] mt-1" style={{ color: 'var(--muted)' }}>
            {sc.courses && sc.courses > 1
              ? `${sc.courses} courses, modal ${sc.course}`
              : sc.course} → {sc.end_label}{sc.presser ? ` · ${sc.presser} presses` : ''}
            {sc.market_implications.length ? ` · ${sc.market_implications.slice(0, 2).map((m) => `${m.market_name} ${(m.median * 100).toFixed(2)}%`).join(', ')}` : ' · unpriced'}
          </div>
        </li>
      ))}
    </ol>
  )
}

// ── one dyad's solved game ────────────────────────────────────────────────

function DyadGame({
  region, dyad, onBack, onPick, onNavigate,
}: {
  region: string
  dyad: string
  onBack: () => void
  onPick: (dyad: string) => void
  onNavigate: (route: string) => void
}) {
  const label = useRegionLabel(region)
  const [sol, setSol] = useState<DyadSolution | null | undefined>(undefined)
  const [map, setMap] = useState<RegionMap | null>(null)
  const [solver, setSolver] = useState<'lp' | 'qre'>('lp')
  const [type, setType] = useState<'resolute' | 'irresolute'>('resolute')
  useEffect(() => {
    let live = true
    setSol(undefined)
    getDyadSolution(region, dyad).then((s) => live && setSol(s))
    getRegionMap(region).then((m) => live && setMap(m))
    return () => { live = false }
  }, [region, dyad])

  const concept: ConceptSolution | null = useMemo(
    () => (sol ? sol.concepts[solver] ?? sol.concepts[sol.primary_solver] : null),
    [sol, solver],
  )

  if (sol === undefined) return <div className="reading-column py-10"><Empty>Solving the game…</Empty></div>
  if (sol === null || !concept) {
    const f = lastFailureFor('/api/games/dyad')
    return (
      <div className="reading-column py-10">
        <StoryHead kicker={`Solved game · ${label.toUpperCase()}`} title="This pair could not be solved"
                   standfirst={f?.detail ?? 'The API did not answer.'}
                   action={<button className="article-link" onClick={onBack}>← the region</button>} />
      </div>
    )
  }
  const bands = sol.band_labels
  const lp = sol.concepts.lp
  const qre = sol.concepts.qre
  const top = concept.scenarios[0]
  const fam = familyRead(sol.opening.family)
  const beliefSteps = top?.steps ?? []
  const propensity = concept.escalation_propensity

  return (
    <div className="reading-column py-8">
      <StoryHead
        kicker={`Solved game · ${label.toUpperCase()} · ${solver.toUpperCase()}`}
        title={sol.dyad_name}
        standfirst={sol.explanation[0]}
        action={
          <div className="flex flex-col items-end gap-2">
            <select className="region-select text-sm" value={dyad} onChange={(e) => onPick(e.target.value)}>
              {(map?.ranking ?? [{ dyad_id: dyad, dyad_name: sol.dyad_name }]).map((r) => (
                <option key={r.dyad_id} value={r.dyad_id}>{r.dyad_name}</option>
              ))}
            </select>
            <span className="flex gap-2">
              <button className="btn btn--quiet" onClick={onBack}>← region map</button>
              <button className="btn btn--quiet" onClick={() => onNavigate(`/relationships?dyad=${encodeURIComponent(dyad)}&region=${encodeURIComponent(region)}`)}>relationship →</button>
            </span>
          </div>
        }
      />

      <div className="toolbar mt-6">
        <span className="kicker">concept</span>
        {(['qre', 'lp'] as const).map((s) => (
          <button key={s} className="btn" aria-pressed={s === solver} onClick={() => setSolver(s)}>
            {s === 'qre' ? 'fitted QRE' : 'LP correlated equilibrium'}
          </button>
        ))}
        <span className="kicker" style={{ marginLeft: '0.75rem' }}>standing</span>
        <Chip label={standingLabel(sol.opening.standing) ?? 'no declared standing'}
              tone={sol.opening.standing?.relations?.length ? 'ink' : 'muted'} />
        {fam && (<>
          <span className="kicker" style={{ marginLeft: '0.75rem' }}>read as</span>
          <span title={fam.why}><Chip label={fam.label} tone={fam.tone} /></span>
        </>)}
        {postureNote(sol.opening.posture) && (
          <span className="mono text-[11px]" style={{ color: 'var(--muted)' }}>
            record: {postureNote(sol.opening.posture)}
          </span>
        )}
        <span className="mono text-[11px]" style={{ color: 'var(--muted)', marginLeft: 'auto' }}>
          {sol.persisted ? `solved ${sol.computed_at?.slice(0, 16).replace('T', ' ')} UTC` : 'solved on request'} · as of {sol.as_of}
        </span>
      </div>

      <div className={`call mt-6 ${concept.sharp_departure_probability >= 0.5 && (fam?.tone !== 'good') ? 'call--rising' : ''}`}>
        <div className="kicker">The call{fam ? ` · ${fam.label}` : ''}</div>
        <p className="call-lede">
          {pct(concept.sharp_departure_probability, 0)} that {sol.sides[0]} and {sol.sides[1]}
          {standingLabel(sol.opening.standing) ? ` — ${standingLabel(sol.opening.standing)} — ` : ' '}
          see a sharper-than-usual departure from their own usual level of {fam?.headline ?? 'friction'} within {sol.horizon} quarters
          {lp && qre ? ` (QRE ${pct(qre.sharp_departure_probability, 0)}, LP ${pct(lp.sharp_departure_probability, 0)})` : ''}.
        </p>
        {/* THE FAMILY'S OWN QUESTION, and the caveat a non-native game owes the
            reader — said here, in the call, not buried under "how it was
            solved": a treaty ally at 77% was being read as a war. */}
        {fam && (
          <p className="text-sm mt-2" style={{ maxWidth: '62ch' }}>
            {fam.why.charAt(0).toUpperCase() + fam.why.slice(1)}. The question worth asking of this pair is {sol.opening.family?.question}.
            {fam.caveat ? <> <em>{fam.caveat}</em></> : null}
          </p>
        )}
        <p className="mono text-[11px] mt-1" style={{ color: 'var(--muted)' }}>
          opening at a {sol.opening.intensity_label} · P(above the opening band) {pct(concept.escalation_probability, 0)} · bands are relative {fam?.headline ?? 'friction'}, not absolute hostility
        </p>
        {top && (
          <p className="text-sm mt-2" style={{ maxWidth: '62ch' }}>
            Most likely kind of course: <b>{kind(top.kind)}</b> at {pct(top.likelihood, 0)} of the walk's mass{top.courses && top.courses > 1 ? ` (${top.courses} courses, modal ${top.course})` : ` (${top.course})`}, ending {top.end_label}
            {top.presser ? `, ${top.presser} pressing` : ''}.
            {top.market_implications.length
              ? ` Historically such courses moved ${top.market_implications.slice(0, 3).map((m) => `${m.market_name} ${(m.median * 100).toFixed(2)}% (n=${m.n})`).join(', ')}.`
              : ' No measured market implication clears the evidence bar for this course.'}
          </p>
        )}
        <div className="mt-4">
          <Tiles items={[
            { label: 'opening departure', value: sol.opening.intensity_label, sub: `latest ${sol.opening.latest_intensity.toFixed(2)} vs the pair's scale ${sol.opening.scale.toFixed(2)}` },
            { label: 'capability', value: `band ${sol.opening.capability.band}`, sub: sol.opening.capability.source === 'cinc' ? `CINC ratio ${(sol.opening.capability.ratio ?? 0.5).toFixed(2)}` : 'default (no CINC)' },
            { label: 'beliefs (resolute)', value: `${pct(sol.opening.beliefs.a, 0)} / ${pct(sol.opening.beliefs.b, 0)}`, sub: sol.opening.beliefs.source === 'bayes_filter' ? `filtered from ${sol.opening.beliefs.quarters_observed} quarters` : 'flat prior' },
            { label: 'kernel', value: sol.opening.tilt ? (sol.opening.tilt.features ? 'this pair’s own' : `η ${(sol.opening.tilt.eta ?? 0) >= 0 ? '+' : ''}${(sol.opening.tilt.eta ?? 0).toFixed(3)}`) : 'region counted', tone: 'plain', sub: sol.opening.tilt ? sol.opening.tilt.model : 'no model ships for this region' },
          ]} />
        </div>
      </div>

      <Beat n={1} title="The fan" major aside={`${concept.paths_enumerated} courses enumerated · top ${concept.paths.length} carry ${pct(concept.retained_probability, 0)}`}>
        <BandHeat rows={concept.marginal.map((m) => m.distribution)} bandLabels={bands}
                  markers={concept.marginal.map((m) => m.modal_band)} />
        <p className="mono text-[11px] mt-2" style={{ color: 'var(--muted)' }}>
          expected band by quarter: {concept.marginal.map((m) => m.expected_band.toFixed(2)).join(' → ')} · outlined cell = modal band
        </p>
      </Beat>

      <Beat n={2} title="The courses of play" aside="each course is a scenario; the band at each step is a distribution">
        <ol className="space-y-4">
          {concept.scenarios.map((sc, i) => (
            <li key={sc.course + i} className="text-sm">
              <div className="flex items-baseline gap-3">
                <span className="figure w-12 shrink-0" style={{ color: sc.delta_band > 0 ? 'var(--alert)' : sc.delta_band < 0 ? 'var(--accent)' : 'var(--text)' }}>{pct(sc.likelihood, 0)}</span>
                <span><b>{kind(sc.kind)}</b> — {sc.rationale}</span>
              </div>
              <div className="ml-15 mt-2 scroll-x" style={{ marginLeft: '3.75rem' }}>
                <table className="mono text-[11px]" style={{ borderCollapse: 'separate', borderSpacing: '8px 2px' }}>
                  <thead>
                    <tr style={{ color: 'var(--muted)' }}>
                      <th className="font-normal text-left">q</th><th className="font-normal text-left">{sol.sides[0]}</th>
                      <th className="font-normal text-left">{sol.sides[1]}</th><th className="font-normal text-left">band</th>
                      <th className="font-normal text-left">P(band)</th><th className="font-normal text-left">beliefs a/b</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(sc.steps ?? []).map((st) => (
                      <tr key={st.period}>
                        <td>+{st.period}</td>
                        <td style={{ color: st.action_a === 'escalate' ? 'var(--alert)' : st.action_a === 'de-escalate' ? 'var(--accent)' : 'var(--text)' }}>{st.action_a}</td>
                        <td style={{ color: st.action_b === 'escalate' ? 'var(--alert)' : st.action_b === 'de-escalate' ? 'var(--accent)' : 'var(--text)' }}>{st.action_b}</td>
                        <td>{bands[st.intensity_band]}</td>
                        <td>{pct(st.band_probability ?? 0, 0)}</td>
                        <td>{st.belief_a !== undefined ? `${pct(st.belief_a, 0)} / ${pct(st.belief_b ?? 0, 0)}` : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {sc.market_implications.length > 0 && (
                <div className="mt-2" style={{ marginLeft: '3.75rem' }}>
                  <Bars rows={sc.market_implications.map((m) => ({ key: m.market_id, label: m.market_name, value: m.median, sub: `n=${m.n}` }))}
                        signed format={(v) => `${(v * 100).toFixed(2)}%`} />
                </div>
              )}
            </li>
          ))}
        </ol>
      </Beat>

      <Beat n={3} title="The stage game at the opening" aside={lp?.nash_gap ? `nash gap: mean ${lp.nash_gap.mean.toFixed(3)}, ${pct(lp.nash_gap.share_product_form, 0)} of stage games at a Nash point` : undefined}>
        <div className="toolbar mb-3" style={{ borderTop: 'none' }}>
          <span className="kicker">own type</span>
          {(['resolute', 'irresolute'] as const).map((t) => (
            <button key={t} className="btn btn--quiet" aria-pressed={t === type} onClick={() => setType(t)}>{t}</button>
          ))}
        </div>
        <PayoffMatrix matrix={concept.opening_matrix[type]} sides={sol.sides} />
      </Beat>

      <Beat n={4} title="Propensity to escalate, by departure band and type" aside={`${solver.toUpperCase()}, period 1, at the opening capability`}>
        <MultiLine
          xLabels={bands}
          series={[
            { name: 'resolute', values: propensity.resolute ?? [], color: 'var(--alert)' },
            { name: 'irresolute', values: propensity.irresolute ?? [], color: 'var(--accent)', dash: '4 3' },
          ]}
        />
      </Beat>

      {beliefSteps.length > 0 && (
        <Beat n={5} title="Beliefs along the most likely course" aside="P(resolute) after each step, by the game's own Bayes rule">
          <MultiLine
            xLabels={['open', ...beliefSteps.map((s) => `+${s.period}q`)]}
            series={[
              { name: `${sol.sides[0]} resolute`, values: [sol.opening.beliefs.a, ...beliefSteps.map((s) => s.belief_a ?? 0)], color: 'var(--alert)' },
              { name: `${sol.sides[1]} resolute`, values: [sol.opening.beliefs.b, ...beliefSteps.map((s) => s.belief_b ?? 0)], color: 'var(--accent)', dash: '4 3' },
            ]}
          />
        </Beat>
      )}

      <Beat n={6} title="Explanation" aside="written from the numbers above — every figure is a field">
        {sol.explanation.slice(1).map((p, i) => (
          <p key={i} className="text-sm leading-relaxed mb-3" style={{ maxWidth: '68ch' }}>{p}</p>
        ))}
        <Disclosure label="payoffs, kernel and pricing evidence">
          <dl className="statline mt-2">
            {Object.entries(sol.payoffs).map(([k, v]) => (<div key={k}><dt>{k.replace('_', ' ')}</dt><dd>{v.toFixed(3)}</dd></div>))}
          </dl>
          <p className="mono text-[11px] mt-2" style={{ color: 'var(--muted)' }}>
            kernel {sol.kernel.measured}/{sol.kernel.cells} measured ({pct(sol.kernel.share_measured, 0)}) over {sol.kernel.observations.toLocaleString('en-US')} dyad-quarters
            {concept.pricing ? ` · pricing over ${concept.pricing.measurements} measured effects in ${concept.pricing.cells} cells` : ' · no pricing evidence'}
          </p>
          <p className="mono text-[11px] mt-1" style={{ color: 'var(--muted)' }}>{concept.concept}</p>
        </Disclosure>
        <p className="mt-4 text-xs italic" style={{ color: 'var(--muted)', maxWidth: '68ch' }}>{sol.boundary_statement}</p>
      </Beat>
    </div>
  )
}
