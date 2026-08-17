/** The game-theory page: a region's future-event map from solved games, and
 *  the drill-in for one dyad's solved game.
 *
 *  REWRITTEN 2026-08-17, and the rewrite is about what the page SAYS. It used
 *  to open on `explanation[0]` — a hundred-word paragraph that named the solver
 *  twice, listed CINC and a Bayes filter before it said anything a reader
 *  asked, and closed by explaining why a number that was zero could mislead.
 *  Beneath it were four tiles (nash gap, kernel cells, a model hash), a table
 *  of percentages, a 12×4 table of expected bands to two decimals, and twenty
 *  copies of "escalate/escalate → de-escalate/de-escalate → …".
 *
 *  Now: a composed lede (lib/story.ts), the ranking as one measured bar, the
 *  courses as strips, the fan as a fan — and the solver's whole vocabulary,
 *  with the audit prose it belongs to, under "How this was solved". The
 *  payload is unchanged; §17 still holds, and holds more tightly, because
 *  every clause on the page is reachable from one field.
 */
import { useEffect, useMemo, useState } from 'react'
import { getDyadSolution, getRegionMap, lastFailureFor } from '../api'
import { useRegionLabel } from '../regions'
import type { ConceptSolution, DyadSolution, RegionMap, Scenario } from '../types'
import { Beat, Disclosure, Empty, StoryHead } from '../ui'
import {
  courseInWords,
  count,
  dyadCall,
  headlineWord,
  kindName,
  postureClause,
  regionLede,
  signedPct,
  standingPhrase,
  typicalBand,
} from '../lib/story'
import {
  BandHeat, Bars, CourseStrip, FanRibbon, Matchup, MultiLine, PayoffMatrix, Tiles, pct,
} from './charts/Kit'

/** The two concepts, in a reader's words. The names QRE and LP are the
 *  estimator's; they stay on the audit line under "How this was solved". */
const CONCEPT_WORD: Record<string, string> = {
  qre: 'the fitted game',
  lp: 'the exact benchmark',
}

function dyadFromRoute(): string | null {
  const q = window.location.hash.split('?')[1]
  return q ? new URLSearchParams(q).get('dyad') : null
}

/** Does a series carry any information at all? A propensity chart that draws
 *  two flat lines at 100% and 0% is six hundred pixels saying nothing, and the
 *  LP's opening play is pure often enough that this was the default view. */
function hasVariance(values: number[] | undefined): boolean {
  if (!values || values.length < 2) return false
  return Math.max(...values) - Math.min(...values) > 0.02
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
  const lede = regionLede(map, label)
  // The bar's scale: the region's own leader, so the lengths are comparable
  // within a region and never pretend to be comparable across them.
  const topCoercive = Math.max(1, ...ranking.map((r) => r.coercive_events ?? 0))

  // COURSES SPLIT BY FAMILY, because an alliance's rift-course and a rivalry's
  // escalation are not the same claim and cannot be ranked against each other.
  // Until 2026-08-17 this page's "courses that press" list was led by
  // Syria–Lebanon (formal allies since 1945) at 95% mutual WITHHOLDING, with
  // Egypt–Israel and US–Turkey behind it — three alliances heading a list a
  // reader takes as the region's escalation risk.
  const pressing = map.scenarios_escalatory.filter((s) => s.family?.family !== 'ally')
  const alliances = map.scenarios_escalatory.filter((s) => s.family?.family === 'ally')
  const calming = map.scenarios_calming.filter((s) => s.family?.family !== 'ally')

  return (
    <div className="reading-column py-8">
      <StoryHead
        kicker={`Game theory · ${label.toUpperCase()}`}
        title={lede?.headline ?? 'The next four quarters, solved'}
        standfirst={lede?.support}
        action={
          <span className="mono text-[11px] text-right" style={{ color: 'var(--muted)' }}>
            {map.dyads_solved} pairs solved<br />
            archive to {map.as_of}
          </span>
        }
      />

      <Beat
        title="Who is pressing whom"
        major
        aside="Ranked by coercive acts measured between each pair over the last four quarters — the one quantity here that is comparable across pairs. Click a pair for its solved game."
      >
        <div className="space-y-2">
          {ranking.map((r) => {
            const standing = standingPhrase(r.standing)
            const acts = r.coercive_events ?? 0
            const course = kindName(r.top_scenario)
            return (
              <button
                key={r.dyad_id}
                type="button"
                className="ranking-row"
                onClick={() => onPick(r.dyad_id)}
                title={
                  `${pct(r.sharp_departure_probability, 0)} that this pair ends the horizon above its own usual band` +
                  (r.tilted ? ' · solved on this pair’s own transition table' : '')
                }
              >
                <span className="ranking-name">
                  <span className="ranking-pair">{r.dyad_name}</span>
                  {standing && <span className="ranking-standing">{standing}</span>}
                </span>
                <span className="ranking-bar" aria-hidden="true">
                  <span
                    style={{
                      width: `${Math.max(0, Math.min(1, acts / topCoercive)) * 100}%`,
                      background: acts >= topCoercive / 2 ? 'var(--alert)' : 'var(--accent)',
                    }}
                  />
                </span>
                <span className="ranking-figure mono">{count(acts)}</span>
                <span className="ranking-course" title={courseInWords(r.top_scenario, r.family) ?? undefined}>
                  {course || '—'}
                </span>
              </button>
            )
          })}
        </div>
        <p className="figure-note">
          The bar is a measured count of coercive acts, not a forecast. A pair's own
          odds of breaking above its usual band are a different question — one a quiet
          ally can score high on — and they are on each pair's page.
        </p>
      </Beat>

      <Beat
        title="Where the games point"
        aside={`The courses the solved games put the most mass on, pooled by kind across ${map.dyads_solved} pairs. Each pair's kinds sum to one.`}
      >
        <div className="grid md:grid-cols-2 gap-8">
          <div>
            <div className="kicker mb-2" style={{ color: 'var(--alert)' }}>Toward pressure</div>
            <ScenarioList rows={pressing} onPick={onPick} />
          </div>
          <div>
            <div className="kicker mb-2" style={{ color: 'var(--accent)' }}>Toward a step down</div>
            <ScenarioList rows={calming.slice(0, 6)} onPick={onPick} />
          </div>
        </div>
      </Beat>

      {alliances.length > 0 && (
        <Beat
          title="Alliances under strain"
          aside="Allied pairs play a burden-sharing game, not a crisis: withholding here is a partner declining to carry the alliance, and its bad end is a rift — never conflict between them."
        >
          <ScenarioList rows={alliances.slice(0, 6)} onPick={onPick} />
        </Beat>
      )}

      <Beat title="How this was solved" aside="The concepts, the kernel and the audit paragraphs the numbers above come from.">
        <Disclosure label="the method, in the estimator's own words">
          <div className="mt-3">
            <Tiles items={[
              { label: 'pairs solved', value: String(map.dyads_solved), sub: `${map.dyads_cinc} with a capability estimate` },
              { label: 'on their own kernel', value: String(map.dyads_tilted), sub: map.model ? `${map.model.name}` : 'no frozen model' },
              { label: 'distance from Nash', value: map.nash_gap.mean !== null ? map.nash_gap.mean.toFixed(3) : '—', sub: map.nash_gap.max !== null ? `worst pair ${map.nash_gap.max.toFixed(3)}` : undefined },
              { label: 'kernel measured', value: pct(map.kernel.share_measured, 0), sub: `${count(map.kernel.observations)} dyad-quarters` },
            ]} />
          </div>
          {map.explanation.map((p, i) => (
            <p key={i} className="text-sm leading-relaxed mt-3" style={{ maxWidth: '68ch' }}>{p}</p>
          ))}
          <dl className="statline mt-4">
            {Object.entries(map.payoffs ?? {}).map(([k, v]) => (
              <div key={k}><dt>{k.replace('_', ' ')}</dt><dd>{v.toFixed(3)}</dd></div>
            ))}
          </dl>
          <ul className="mt-3 text-xs space-y-1" style={{ color: 'var(--muted)' }}>
            {Object.entries(map.concepts).map(([k, v]) => <li key={k}><b>{k.toUpperCase()}</b>: {v}</li>)}
            <li>kernel: {map.kernel.measured} of {map.kernel.cells} cells measured, {map.kernel.fallback} fallback</li>
          </ul>
          <div className="mt-6">
            <div className="kicker mb-2">The region's fan — average mass by band, quarter by quarter</div>
            <BandHeat rows={map.region_fan.map((r) => r.distribution)} bandLabels={bands} />
          </div>
          <div className="mt-6">
            <div className="kicker mb-2">Expected band by pair and quarter</div>
            <ExpectedHeat heat={map.heat} bands={bands.length} onPick={onPick} />
          </div>
        </Disclosure>
      </Beat>

      <p className="page-boundary">{map.boundary_statement}</p>
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

/** A named course, its share of the game's outcomes, and the one market the
 *  archive priced it to. The machine string it used to print underneath —
 *  "130 courses, modal withhold/withhold → withhold/withhold → …" — is on the
 *  pair's own page now, drawn as a strip. */
function ScenarioList({ rows, onPick }: { rows: Scenario[]; onPick?: (dyad: string) => void }) {
  if (!rows.length) return <Empty>none named</Empty>
  const top = Math.max(...rows.map((r) => r.likelihood), 1e-9)
  return (
    <ol className="space-y-3">
      {rows.map((sc) => {
        const priced = sc.market_implications[0]
        return (
          <li key={sc.scenario_name + sc.course} className={`text-sm ${onPick ? 'cursor-pointer' : ''}`}
              onClick={onPick ? () => onPick(sc.dyad_id) : undefined}>
            <div className="flex items-baseline gap-2">
              <span className="figure w-12 shrink-0" style={{ color: sc.delta_band > 0 ? 'var(--alert)' : sc.delta_band < 0 ? 'var(--accent)' : 'var(--text)' }}>
                {pct(sc.likelihood, 0)}
              </span>
              <span className="truncate"><b>{sc.dyad_name}</b> — {kindName(sc)}</span>
            </div>
            <div className="relative h-1 mt-1 ml-14" style={{ background: 'var(--panel)' }}>
              <div className="absolute inset-y-0 left-0" style={{
                width: `${(sc.likelihood / top) * 100}%`,
                background: sc.delta_band > 0 ? 'var(--alert)' : sc.delta_band < 0 ? 'var(--accent)' : 'var(--muted)',
              }} />
            </div>
            <div className="ml-14 mt-1 text-xs" style={{ color: 'var(--muted)' }}>
              {courseInWords(sc, sc.family)?.split(': ')[1] ?? sc.course}
              {priced
                ? ` · historically moved ${priced.market_name} ${signedPct(priced.median)} over ${count(priced.n)} events`
                : ' · no market has been priced to this course'}
            </div>
          </li>
        )
      })}
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
  // DEFAULTS TO THE PAYLOAD'S OWN PRIMARY. It used to default to 'lp' while the
  // region page ranks and names courses by `primary_solver` (qre), so clicking
  // "brinkmanship at 52%" landed on a page whose call read "mutual escalation
  // at 54%" — the same pair, a different concept, and nothing saying so.
  const [solver, setSolver] = useState<'lp' | 'qre' | null>(null)
  const [typeIndex, setTypeIndex] = useState<1 | 0>(1)
  useEffect(() => {
    let live = true
    setSol(undefined)
    setSolver(null)
    getDyadSolution(region, dyad).then((s) => live && setSol(s))
    getRegionMap(region).then((m) => live && setMap(m))
    return () => { live = false }
  }, [region, dyad])

  const chosen = solver ?? sol?.primary_solver ?? 'qre'
  const concept: ConceptSolution | null = useMemo(
    () => (sol ? sol.concepts[chosen] ?? sol.concepts[sol.primary_solver] : null),
    [sol, chosen],
  )

  if (sol === undefined) return <div className="reading-column py-10"><Empty>Solving the game…</Empty></div>
  if (sol === null || !concept) {
    const f = lastFailureFor('/api/games/dyad')
    return (
      <div className="reading-column py-10">
        <StoryHead kicker={`Solved game · ${label.toUpperCase()}`} title="This pair could not be solved"
                   standfirst={f?.detail ?? 'The API did not answer.'}
                   action={<button className="btn btn--quiet" onClick={onBack}>← the region</button>} />
      </div>
    )
  }
  const bands = sol.band_labels
  const lp = sol.concepts.lp
  const qre = sol.concepts.qre
  const top = concept.scenarios[0]
  const family = sol.opening.family
  const word = headlineWord(family)
  const call = dyadCall(sol, concept)
  // THE GAME'S OWN NAMES: types and actions come from the payload's space, so
  // an ally pair reads reluctant/committed and commit/affirm/withhold where
  // an adversary pair reads irresolute/resolute and de-escalate/hold/escalate.
  const types = sol.space?.types ?? ['irresolute', 'resolute']
  const typeName = types[typeIndex] ?? 'resolute'
  const pressWord = (sol.space?.actions ?? ['de-escalate', 'hold', 'escalate'])[2]
  const propensity = concept.escalation_propensity
  const propensityWorth =
    hasVariance(propensity[types[1]]) || hasVariance(propensity[types[0]])
  const standing = standingPhrase(sol.opening.standing)
  const posture = postureClause(sol.opening.posture)

  return (
    <div className="reading-column py-8">
      <StoryHead
        kicker={`Solved game · ${label.toUpperCase()}`}
        title={sol.dyad_name}
        standfirst={
          // STANDING, THEN RECORD, THEN THE QUESTION — three facts from three
          // sources, said once each. `family.why` is deliberately not here: it
          // restates the standing and the record in the classifier's terms,
          // and the reader has just read both.
          <>
            {[standing, posture].filter(Boolean).join(' · ')}
            {family ? `. The question worth asking of this pair is ${family.question}.` : ''}
          </>
        }
        action={
          <div className="flex flex-col items-end gap-2">
            <select className="region-select text-sm" value={dyad} onChange={(e) => onPick(e.target.value)}>
              {(map?.ranking ?? [{ dyad_id: dyad, dyad_name: sol.dyad_name }]).map((r) => (
                <option key={r.dyad_id} value={r.dyad_id}>{r.dyad_name}</option>
              ))}
            </select>
            <span className="flex gap-2">
              <button className="btn btn--quiet" onClick={onBack}>← the region</button>
              <button className="btn btn--quiet" onClick={() => onNavigate(`/relationships?dyad=${encodeURIComponent(dyad)}&region=${encodeURIComponent(region)}`)}>relationship →</button>
            </span>
          </div>
        }
      />

      {/* THE CALL — one lede, one odds line, one course, one market line. It
          used to hold seven registers in one plate: kicker, three-line lede, a
          caveat paragraph, an 11px mono line, a course paragraph and four
          tiles, with nothing leading. */}
      <div className={`call mt-8 ${call.opensAbove && family?.family !== 'ally' ? 'call--rising' : ''}`}>
        <div className="kicker">The call</div>
        <p className="call-lede">{call.headline}</p>
        <p className="call-odds">{call.odds}</p>
        {call.course && <p className="call-note">{call.course}</p>}
        {call.markets ? (
          <p className="call-note">{call.markets}</p>
        ) : (
          <p className="call-note" style={{ color: 'var(--muted)' }}>
            No market has enough comparable measurements to price this course.
          </p>
        )}
        {family && !family.native && (
          <p className="call-note" style={{ color: 'var(--muted)' }}>
            <em>
              The solver runs one game — an adversary's crisis bargaining — for every pair, so
              these numbers describe departures from this pair's own usual level of {word}, not
              odds of {family.bad_end}.
            </em>
          </p>
        )}
      </div>

      <Beat title="The matchup" major aside="What the two sides bring, what each believes about the other, and where the pair opens against its own history.">
        <Matchup
          sides={sol.sides}
          standing={standing}
          posture={posture}
          capability={sol.opening.capability}
          beliefs={sol.opening.beliefs}
          typeName={types[1]}
          opening={{
            label: sol.opening.intensity_label,
            latest: sol.opening.latest_intensity,
            scale: sol.opening.scale,
            band: sol.opening.intensity_band,
            typical: typicalBand(sol),
          }}
          kernel={{ own: Boolean(sol.opening.tilt), model: sol.opening.tilt?.model ?? null }}
        />
      </Beat>

      <Beat
        title="Where it's heading"
        aside={`Every course the game can take, gathered into a fan. The line is the middle of the game's mass; the shaded bands hold the middle half and the middle four fifths of it.`}
      >
        <div className="toolbar mb-4" style={{ borderTop: 'none' }}>
          <span className="kicker">solved under</span>
          {(['qre', 'lp'] as const).map((s) => (
            <button key={s} className="btn" aria-pressed={s === chosen} onClick={() => setSolver(s)}>
              {CONCEPT_WORD[s]}
            </button>
          ))}
          {lp && qre && (
            <span className="text-xs" style={{ color: 'var(--muted)', marginLeft: 'auto' }}>
              {word} above the usual band: {pct(qre.sharp_departure_probability, 0)} fitted,{' '}
              {pct(lp.sharp_departure_probability, 0)} benchmark
            </span>
          )}
        </div>
        <FanRibbon
          marginal={concept.marginal.map((m) => m.distribution)}
          bandLabels={bands}
          openingBand={sol.opening.intensity_band}
          typicalBand={typicalBand(sol)}
        />
        <p className="figure-note">
          Bands are departures from <em>this pair's own</em> baseline, not absolute hostility. The
          dashed rule is the level the call's odds count above.
        </p>

        {top?.steps && top.steps.length > 0 && (
          <div className="mt-8">
            <div className="kicker mb-2">The likeliest course, quarter by quarter</div>
            <CourseStrip
              steps={top.steps}
              sides={sol.sides}
              actions={sol.space?.actions}
              typeName={types[1]}
            />
            <p className="figure-note">
              {kindName(top)} — {pct(top.likelihood, 0)} of the game's outcomes, pooled over{' '}
              {count(top.courses ?? 1)} courses the classifier reads the same way.
              {top.steps.some((s) => s.belief_a !== undefined) &&
                ' The bottom row is what each side believes about the other as the course plays out.'}
            </p>
          </div>
        )}

        {concept.scenarios.length > 1 && (
          <div className="mt-8">
            <div className="kicker mb-2">The other courses</div>
            <ol className="space-y-2">
              {concept.scenarios.slice(1).map((sc, i) => (
                <li key={sc.course + i} className="text-sm flex items-baseline gap-3">
                  <span className="figure w-12 shrink-0" style={{ color: sc.delta_band > 0 ? 'var(--alert)' : sc.delta_band < 0 ? 'var(--accent)' : 'var(--text)' }}>
                    {pct(sc.likelihood, 0)}
                  </span>
                  <span>
                    <b>{kindName(sc)}</b> — {courseInWords(sc, family)?.split(': ')[1] ?? sc.rationale}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        )}
      </Beat>

      {top && top.market_implications.length > 0 && (
        <Beat
          title="What the archive measured after courses like this"
          aside="Median abnormal return across every measured event of a comparable course — the direction and size markets typically moved beyond what their own estimation window expected."
        >
          <Bars
            rows={top.market_implications.map((m) => ({
              key: m.market_id, label: m.market_name, value: m.median, sub: `${count(m.n)} events`,
            }))}
            signed format={(v) => signedPct(v, 2)}
          />
        </Beat>
      )}

      <Beat title="How this was solved" aside="The stage game, the payoffs, the kernel, and the estimator's own account of the numbers above.">
        <Disclosure label="the method, in the estimator's own words">
          {sol.explanation.map((p, i) => (
            <p key={i} className="text-sm leading-relaxed mt-3" style={{ maxWidth: '68ch' }}>{p}</p>
          ))}

          <div className="mt-6">
            <div className="kicker mb-2">The stage game at the opening</div>
            <div className="toolbar mb-3" style={{ borderTop: 'none' }}>
              <span className="kicker">own type</span>
              {([1, 0] as const).map((t) => (
                <button key={t} className="btn btn--quiet" aria-pressed={t === typeIndex} onClick={() => setTypeIndex(t)}>{types[t]}</button>
              ))}
            </div>
            {concept.opening_matrix[typeName] && <PayoffMatrix matrix={concept.opening_matrix[typeName]} sides={sol.sides} />}
          </div>

          {propensityWorth && (
            <div className="mt-6">
              <div className="kicker mb-2">Propensity to {pressWord}, by departure band and type</div>
              <MultiLine
                xLabels={bands}
                series={[
                  { name: types[1], values: propensity[types[1]] ?? [], color: 'var(--alert)' },
                  { name: types[0], values: propensity[types[0]] ?? [], color: 'var(--accent)', dash: '4 3' },
                ]}
              />
            </div>
          )}

          <dl className="statline mt-6">
            {Object.entries(sol.payoffs).map(([k, v]) => (<div key={k}><dt>{k.replace('_', ' ')}</dt><dd>{v.toFixed(3)}</dd></div>))}
          </dl>
          <p className="mono text-[11px] mt-2" style={{ color: 'var(--muted)' }}>
            kernel {sol.kernel.measured}/{sol.kernel.cells} measured ({pct(sol.kernel.share_measured, 0)}) over {count(sol.kernel.observations)} dyad-quarters
            {concept.pricing ? ` · pricing over ${count(concept.pricing.measurements)} measured effects in ${concept.pricing.cells} cells` : ' · no pricing evidence'}
            {sol.opening.tilt ? ` · ${sol.opening.tilt.model}` : ''}
          </p>
          <p className="mono text-[11px] mt-1" style={{ color: 'var(--muted)' }}>{concept.concept}</p>
          <p className="mono text-[11px] mt-1" style={{ color: 'var(--muted)' }}>
            {sol.persisted ? `solved ${sol.computed_at?.slice(0, 16).replace('T', ' ')} UTC` : 'solved on request'} · archive to {sol.as_of}
          </p>
        </Disclosure>
      </Beat>

      <p className="page-boundary">{sol.boundary_statement}</p>
    </div>
  )
}
