import { useEffect, useMemo, useRef, useState } from 'react'
import {
  getCoverage,
  getDyads,
  getEvent,
  getEventEffects,
  getEvents,
  getPack,
  getRegimes,
  getRelations,
  getTrajectory,
} from '../api'
import Graph3D, {
  ACTOR_COLOR,
  EDGE_SWATCH_ACTIVE,
  EDGE_SWATCH_ESCALATING,
  RELATION_SWATCH,
  TYPE_LABEL,
  type Graph3DHandle,
  type LinkSelection,
} from './Graph3D'
import TimeSlider, { YEAR_NOW } from './TimeSlider'
import type {
  Dyad,
  Effect,
  EventDetail,
  GraphEvent,
  Pack,
  PackActor,
  Relation,
  Segmentation,
  Trajectory,
} from '../types'

const num = (value: number | null | undefined, digits = 2) =>
  value == null ? '—' : value.toFixed(digits)

const DIRECTION_COLOR: Record<string, string> = {
  escalating: 'var(--alert)',
  deescalating: 'var(--accent)',
  stable: 'var(--muted)',
}

/** What the drawer is showing. One thing at a time — an event's coding, a
 *  dyad's trajectory, a durable relation, or the focused actor — never several
 *  fighting over the same 21rem. */
type Selection =
  | { kind: 'event'; id: string }
  | { kind: 'dyad'; id: string }
  | { kind: 'relation'; relation: Relation }
  | null

function Microcaps({ children }: { children: React.ReactNode }) {
  return (
    <h2 className="mono text-xs uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
      {children}
    </h2>
  )
}

/** A sparkline of one dyad's escalation against its own moving baseline. The
 *  baseline is drawn, not just labelled: without it a reader cannot see why the
 *  same score means different things in different relationships. */
function Trajectoryline({ trajectory }: { trajectory: Trajectory }) {
  const points = trajectory.events.filter((e) => e.goldstein != null)
  if (points.length < 2) {
    return (
      <p className="text-xs mt-2" style={{ color: 'var(--muted)' }}>
        One observation in this dyad — nothing to compare it against yet.
      </p>
    )
  }
  const W = 280
  const H = 64
  const x = (i: number) => (i / (points.length - 1)) * (W - 8) + 4
  const y = (v: number) => H - 6 - ((v + 10) / 20) * (H - 12)

  const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(i)},${y(p.goldstein!)}`).join(' ')
  const baseline = points
    .map((p, i) => `${i ? 'L' : 'M'}${x(i)},${y(p.escalation_baseline ?? p.goldstein!)}`)
    .join(' ')

  return (
    <figure className="mt-3">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="escalation trajectory">
        <line x1={0} y1={y(0)} x2={W} y2={y(0)} stroke="#232a3a" strokeWidth={1} />
        <path d={baseline} fill="none" stroke="#7d8598" strokeWidth={1} strokeDasharray="3 3" />
        <path d={path} fill="none" stroke="#b08d57" strokeWidth={1.5} />
        {points.map((p, i) => (
          <circle key={p.node_id} cx={x(i)} cy={y(p.goldstein!)} r={2.5} fill="#b08d57">
            <title>
              {p.event_time} · {p.name} · {num(p.goldstein, 1)}
            </title>
          </circle>
        ))}
      </svg>
      <figcaption className="text-xs mono mt-1" style={{ color: 'var(--muted)' }}>
        solid: event score · dashed: the dyad's baseline · scale −10…+10
      </figcaption>
    </figure>
  )
}

/** Measured market effects for one event — THE MONEY EDGE, in the drawer.
 *  A p-value gets a weight, not a verdict: significance is shown by emphasis
 *  and stated numerically, never converted into a claim the study did not
 *  make. An empty measurement is said out loud — silence would read as "no
 *  effect", which is a different claim entirely. */
function EffectsSection({ effects }: { effects: Effect[] | null }) {
  const pct = (v: number | null | undefined) =>
    v == null ? '—' : `${v >= 0 ? '+' : ''}${(v * 100).toFixed(2)}%`
  if (effects === null) return null
  return (
    <div className="mt-5 pt-4 border-t" style={{ borderColor: 'var(--line)' }}>
      <Microcaps>Measured market effects</Microcaps>
      {effects.length === 0 ? (
        <p className="mt-2 text-sm" style={{ color: 'var(--muted)' }}>
          Nothing measured for this event yet — the transmission engine has
          not run for it, or every market was skipped. That is not the same
          claim as "no effect".
        </p>
      ) : (
        <ul className="mt-2 space-y-2">
          {effects.map((fx) => {
            const strong = fx.p_value != null && fx.p_value < 0.05
            return (
              <li
                key={`${fx.ticker}-${fx.window}`}
                className="flex items-baseline justify-between gap-3 text-sm"
              >
                <span>
                  {fx.market}
                  <span className="mono text-xs" style={{ color: 'var(--muted)' }}>
                    {' '}
                    {fx.window} · {fx.resolution}
                    {fx.first_mover ? ' · first mover' : ''}
                    {fx.overlapping ? ' · overlapping' : ''}
                  </span>
                </span>
                <span
                  className="mono text-right"
                  style={{
                    fontWeight: strong ? 700 : 400,
                    color:
                      fx.abnormal_return == null
                        ? 'var(--muted)'
                        : fx.abnormal_return >= 0
                          ? 'var(--text)'
                          : 'var(--alert)',
                  }}
                >
                  {pct(fx.abnormal_return)}
                  <span className="text-xs block" style={{ color: 'var(--muted)' }}>
                    {fx.p_value == null ? 'no test' : `p ${fx.p_value.toFixed(3)}`}
                  </span>
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}

function EventDetailPanel({ nodeId }: { nodeId: string }) {
  const [detail, setDetail] = useState<EventDetail | null>(null)
  const [trajectory, setTrajectory] = useState<Trajectory | null>(null)
  const [effects, setEffects] = useState<Effect[] | null>(null)

  useEffect(() => {
    let active = true
    setDetail(null)
    setTrajectory(null)
    setEffects(null)
    getEvent(nodeId).then((result) => {
      if (!active) return
      setDetail(result)
      if (result?.dyad) getTrajectory(result.dyad.node_id).then((t) => active && setTrajectory(t))
    })
    getEventEffects(nodeId).then((r) => active && setEffects(r?.rows ?? []))
    return () => {
      active = false
    }
  }, [nodeId])

  if (!detail) {
    return (
      <p className="text-sm" style={{ color: 'var(--muted)' }}>
        Loading…
      </p>
    )
  }

  const firstObservation =
    detail.escalation_magnitude === 0 && detail.escalation_direction === 'stable'

  return (
    <div>
      <div className="mono text-xs" style={{ color: 'var(--accent)' }}>
        {detail.event_time}
      </div>
      <h3 className="text-lg mt-1 leading-snug">{detail.name}</h3>

      <dl className="mt-4 space-y-2 text-sm">
        <div className="flex justify-between gap-4">
          <dt style={{ color: 'var(--muted)' }}>Actors</dt>
          <dd className="text-right">
            {detail.initiator?.name ?? '—'}
            {detail.target && detail.target.node_id !== detail.initiator?.node_id
              ? ` → ${detail.target.name}`
              : ' (internal)'}
          </dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt style={{ color: 'var(--muted)' }}>CAMEO · Goldstein</dt>
          <dd className="mono text-right">
            {detail.cameo_code} · {num(detail.goldstein, 1)}
          </dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt style={{ color: 'var(--muted)' }}>Escalation</dt>
          <dd
            className="text-right"
            style={{ color: DIRECTION_COLOR[detail.escalation_direction ?? 'stable'] }}
          >
            {firstObservation ? (
              <span style={{ color: 'var(--muted)' }}>no prior history</span>
            ) : (
              <>
                {detail.escalation_direction} {num(detail.escalation_magnitude)}
              </>
            )}
          </dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt style={{ color: 'var(--muted)' }}>Resolution</dt>
          <dd className="mono text-right text-xs">
            {detail.fidelity_tier} · {detail.temporal_resolution}
          </dd>
        </div>
      </dl>

      <EffectsSection effects={effects} />

      {detail.dyad && (
        <div className="mt-5 pt-4 border-t" style={{ borderColor: 'var(--line)' }}>
          <div className="text-sm">{detail.dyad.name}</div>
          {trajectory && <Trajectoryline trajectory={trajectory} />}
        </div>
      )}

      {detail.regimes.length > 0 && (
        <div className="mt-5 pt-4 border-t" style={{ borderColor: 'var(--line)' }}>
          <Microcaps>Regime at the time</Microcaps>
          <ul className="mt-2 space-y-1 text-sm">
            {detail.regimes.map((r) => (
              <li key={r.node_id}>{r.name}</li>
            ))}
          </ul>
        </div>
      )}

      {detail.sources.length > 0 && (
        <div className="mt-5 pt-4 border-t" style={{ borderColor: 'var(--line)' }}>
          <Microcaps>Source</Microcaps>
          <ul className="mt-2 space-y-1 text-xs" style={{ color: 'var(--muted)' }}>
            {detail.sources.map((s) => (
              <li key={s.node_id}>{s.name}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

/** A dyad selected from the network: its baseline, its trajectory, and every
 *  event that moved it — each clickable through to its coding. */
function DyadPanel({
  dyadId,
  onSelectEvent,
}: {
  dyadId: string
  onSelectEvent: (nodeId: string) => void
}) {
  const [trajectory, setTrajectory] = useState<Trajectory | null>(null)

  useEffect(() => {
    let active = true
    setTrajectory(null)
    getTrajectory(dyadId).then((t) => active && setTrajectory(t))
    return () => {
      active = false
    }
  }, [dyadId])

  if (!trajectory) {
    return (
      <p className="text-sm" style={{ color: 'var(--muted)' }}>
        Loading…
      </p>
    )
  }

  return (
    <div>
      <Microcaps>Dyad</Microcaps>
      <h3 className="text-lg mt-1 leading-snug">{trajectory.name}</h3>
      <p className="mono text-xs mt-2" style={{ color: 'var(--muted)' }}>
        baseline {num(trajectory.ewma_baseline, 1)}
        {trajectory.ewma_as_of ? ` · as of ${trajectory.ewma_as_of}` : ''}
      </p>

      <Trajectoryline trajectory={trajectory} />

      <div className="mt-5 pt-4 border-t" style={{ borderColor: 'var(--line)' }}>
        <Microcaps>
          {trajectory.events.length} event{trajectory.events.length === 1 ? '' : 's'} in this dyad
        </Microcaps>
        <ul className="mt-2 space-y-2">
          {trajectory.events.map((e) => (
            <li key={e.node_id}>
              <button
                type="button"
                onClick={() => onSelectEvent(e.node_id)}
                className="text-left"
                style={{ background: 'none', border: 'none', color: 'var(--text)', cursor: 'pointer' }}
              >
                <span className="mono text-xs" style={{ color: 'var(--accent)' }}>
                  {e.event_time}
                </span>
                <span className="text-sm block">{e.name}</span>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

/** A durable relation clicked on the graph: what the edge asserts and where
 *  the assertion comes from. Local data — the row is already in hand. */
function RelationPanel({
  relation,
  dyads,
  onSelectDyad,
}: {
  relation: Relation
  dyads: Dyad[]
  onSelectDyad: (id: string) => void
}) {
  const pairDyad = dyads.find(
    (d) =>
      (d.actor_a_id === relation.a_id && d.actor_b_id === relation.b_id) ||
      (d.actor_a_id === relation.b_id && d.actor_b_id === relation.a_id),
  )
  return (
    <div>
      <Microcaps>Durable relation</Microcaps>
      <h3 className="text-lg mt-1 leading-snug">
        {relation.a_name} → {relation.b_name}
      </h3>
      <dl className="mt-4 space-y-2 text-sm">
        <div className="flex justify-between gap-4">
          <dt style={{ color: 'var(--muted)' }}>Type</dt>
          <dd style={{ color: RELATION_SWATCH[relation.relation_type] ?? 'var(--text)' }}>
            {relation.relation_type}
            {relation.relation_type === 'proxy' && (
              <span style={{ color: 'var(--muted)' }}> · patron → client</span>
            )}
          </dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt style={{ color: 'var(--muted)' }}>Valid</dt>
          <dd className="mono">
            {relation.valid_from}
            {relation.valid_to ? `–${relation.valid_to}` : ' – present'}
          </dd>
        </div>
        <div className="flex justify-between gap-4">
          <dt style={{ color: 'var(--muted)' }}>Source</dt>
          <dd className="mono text-xs text-right">{relation.source_id}</dd>
        </div>
      </dl>
      {pairDyad && (
        <button
          type="button"
          onClick={() => onSelectDyad(pairDyad.node_id)}
          className="mt-5 px-4 py-2 text-sm"
          style={{
            border: '1px solid var(--accent)',
            color: 'var(--accent)',
            background: 'transparent',
            cursor: 'pointer',
          }}
        >
          Escalation history of this pair
        </button>
      )}
    </div>
  )
}

/** The focused actor: their standing relations and what they did in the
 *  window. Assembled locally — everything is already fetched. */
function ActorPanel({
  actor,
  relations,
  events,
  onSelectEvent,
  onSelectRelation,
}: {
  actor: PackActor
  relations: Relation[]
  events: GraphEvent[]
  onSelectEvent: (id: string) => void
  onSelectRelation: (r: Relation) => void
}) {
  const ties = relations.filter((r) => r.a_id === actor.id || r.b_id === actor.id)
  return (
    <div>
      <Microcaps>{TYPE_LABEL[actor.actor_type]}</Microcaps>
      <h3 className="text-lg mt-1 leading-snug" style={{ color: ACTOR_COLOR[actor.actor_type] }}>
        {actor.name}
      </h3>

      {ties.length > 0 && (
        <div className="mt-4 pt-4 border-t" style={{ borderColor: 'var(--line)' }}>
          <Microcaps>Durable relations</Microcaps>
          <ul className="mt-2 space-y-2">
            {ties.map((r) => (
              <li key={`${r.a_id}-${r.b_id}-${r.relation_type}`}>
                <button
                  type="button"
                  onClick={() => onSelectRelation(r)}
                  className="text-left text-sm"
                  style={{ background: 'none', border: 'none', color: 'var(--text)', cursor: 'pointer' }}
                >
                  <span style={{ color: RELATION_SWATCH[r.relation_type] ?? 'var(--muted)' }}>
                    {r.relation_type}
                  </span>{' '}
                  {r.a_id === actor.id ? `→ ${r.b_name}` : `← ${r.a_name}`}
                  <span className="mono text-xs" style={{ color: 'var(--muted)' }}>
                    {' '}
                    since {r.valid_from}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-4 pt-4 border-t" style={{ borderColor: 'var(--line)' }}>
        <Microcaps>
          {events.length} event{events.length === 1 ? '' : 's'} in window
        </Microcaps>
        {events.length === 0 ? (
          <p className="mt-2 text-sm" style={{ color: 'var(--muted)' }}>
            Quiet in this window. Scrub the slider to find their decades.
          </p>
        ) : (
          <ul className="mt-2 space-y-2">
            {events.map((e) => (
              <li key={e.node_id}>
                <button
                  type="button"
                  onClick={() => onSelectEvent(e.node_id)}
                  className="text-left"
                  style={{ background: 'none', border: 'none', color: 'var(--text)', cursor: 'pointer' }}
                >
                  <span className="mono text-xs" style={{ color: 'var(--accent)' }}>
                    {e.event_time}
                  </span>
                  <span className="text-sm block">{e.name}</span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export default function Explorer({ onNavigate }: { onNavigate: (route: string) => void }) {
  const [regimes, setRegimes] = useState<Segmentation | null>(null)
  const [pack, setPack] = useState<Pack | null>(null)
  const [dyads, setDyads] = useState<Dyad[]>([])
  const [relations, setRelations] = useState<Relation[]>([])
  const [coverage, setCoverage] = useState<Record<string, number> | null>(null)
  const [total, setTotal] = useState<number | null>(null)
  const [events, setEvents] = useState<GraphEvent[] | null>(null)
  const [year, setYear] = useState(YEAR_NOW)
  const [selection, setSelection] = useState<Selection>(null)
  const [focusActor, setFocusActor] = useState<string | null>(null)
  const [hover, setHover] = useState<string | null>(null)
  const graphHandle = useRef<Graph3DHandle>(null)
  const windowCache = useRef<Map<string, GraphEvent[]>>(new Map())

  useEffect(() => {
    getRegimes().then(setRegimes)
    getPack('mena').then(setPack)
    getDyads().then((r) => setDyads(r?.rows ?? []))
    getRelations().then((r) => setRelations(r?.rows ?? []))
    getCoverage().then((r) => {
      setCoverage(r?.years ?? {})
      setTotal(r?.total ?? 0)
    })
  }, [])

  // A five-year trailing window: long enough that the network shows structure,
  // short enough that the slider still tells a story. ISO strings compare
  // lexically, so bare years are valid bounds at every resolution.
  const windowFrom = `${year - 4}`
  const windowTo = `${year + 1}`

  // The archive is thousands of events now (the deep tier landed), so the
  // explorer fetches THE WINDOW, not the world — cached per window so
  // scrubbing back and forth is instant after the first visit.
  useEffect(() => {
    const key = `${windowFrom}..${windowTo}`
    const cached = windowCache.current.get(key)
    if (cached) {
      setEvents(cached)
      return
    }
    let active = true
    getEvents({ start: windowFrom, end: `${year}-12-31`, limit: 500 }).then((result) => {
      if (!active) return
      const rows = result?.rows ?? []
      windowCache.current.set(key, rows)
      setEvents(rows)
    })
    return () => {
      active = false
    }
  }, [windowFrom, windowTo, year])

  const inWindow = useMemo(
    () =>
      (events ?? []).filter((e) => e.event_time >= windowFrom && e.event_time < windowTo),
    [events, windowFrom, windowTo],
  )
  const listed = useMemo(
    () =>
      focusActor
        ? inWindow.filter((e) => e.initiator_id === focusActor || e.target_id === focusActor)
        : inWindow,
    [inWindow, focusActor],
  )

  const focusedActor = useMemo(
    () => pack?.actors.actors.find((a) => a.id === focusActor) ?? null,
    [pack, focusActor],
  )

  const selectEvent = (id: string) => setSelection({ kind: 'event', id })
  const selectLink = (link: LinkSelection) => {
    if (link.kind === 'relation' && link.relation) {
      setSelection({ kind: 'relation', relation: link.relation })
    } else if (link.kind === 'dyad' && link.dyadId) {
      setSelection({ kind: 'dyad', id: link.dyadId })
    }
  }

  const offline = total === 0

  return (
    <div className="explorer-shell">
      <header
        className="px-5 flex items-center justify-between gap-6 border-b"
        style={{ borderColor: 'var(--line)', minHeight: '3.25rem' }}
      >
        <button
          type="button"
          onClick={() => onNavigate('/')}
          className="text-left"
          style={{ background: 'none', border: 'none', cursor: 'pointer' }}
        >
          <h1 className="text-xl tracking-wide" style={{ color: 'var(--accent)' }}>
            Geo<span style={{ color: 'var(--text)' }}>Graph</span>
          </h1>
        </button>
        <nav className="flex items-baseline gap-5 text-sm">
          <button
            type="button"
            onClick={() => onNavigate('/case/twelve-day-war')}
            className="underline underline-offset-4"
            style={{ color: 'var(--muted)', background: 'none', border: 'none', cursor: 'pointer' }}
          >
            Case study
          </button>
          <span className="mono text-xs" style={{ color: 'var(--muted)' }}>
            {total === null
              ? '…'
              : `${total} events · ${dyads.length} dyads · ${relations.length} relations`}
          </span>
        </nav>
      </header>

      <main className="explorer-main">
        <section className="pane-scroll border-r px-4 py-4" style={{ borderColor: 'var(--line)' }}>
          <div className="flex items-baseline justify-between gap-3">
            <Microcaps>
              {windowFrom}–{year}
              {focusedActor ? ` · ${focusedActor.name}` : ''}
            </Microcaps>
            {focusActor && (
              <button
                type="button"
                onClick={() => setFocusActor(null)}
                className="mono text-xs underline underline-offset-2"
                style={{ color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer' }}
              >
                clear
              </button>
            )}
          </div>
          {listed.length === 0 ? (
            <p className="mt-3 text-sm" style={{ color: 'var(--muted)' }}>
              {focusedActor
                ? `Nothing in this window involves ${focusedActor.name}.`
                : 'Nothing in the archive for this window. The spine is curated, so this is a statement about coverage — not about history.'}
            </p>
          ) : (
            <ul className="mt-3 space-y-1">
              {listed.map((e) => {
                const active = selection?.kind === 'event' && e.node_id === selection.id
                return (
                  <li key={e.node_id}>
                    <button
                      type="button"
                      onClick={() => selectEvent(e.node_id)}
                      className="w-full text-left py-2 px-2 text-sm"
                      style={{
                        background: active ? 'var(--panel)' : 'transparent',
                        border: 'none',
                        borderLeft: `2px solid ${
                          active
                            ? 'var(--accent)'
                            : DIRECTION_COLOR[e.escalation_direction ?? 'stable']
                        }`,
                        color: 'var(--text)',
                        cursor: 'pointer',
                      }}
                    >
                      <span className="mono text-xs block" style={{ color: 'var(--muted)' }}>
                        {e.event_time} · {num(e.goldstein, 1)}
                      </span>
                      {e.name}
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
          {offline && (
            <p className="mt-6 text-sm" style={{ color: 'var(--alert)' }}>
              The graph holds no events at all. If this is a fresh deployment,
              the seed may not have run — check
              <span className="mono"> /api/health</span>.
            </p>
          )}
        </section>

        <section className="canvas3d">
          {pack && (
            <Graph3D
              actors={pack.actors.actors}
              relations={relations}
              dyads={dyads}
              events={events ?? []}
              windowFrom={windowFrom}
              windowTo={windowTo}
              selectedActor={focusActor}
              onSelectActor={setFocusActor}
              onSelectLink={selectLink}
              onHover={setHover}
              handleRef={graphHandle}
            />
          )}
          {!pack && (
            <div className="absolute inset-0 grid place-items-center">
              <p className="text-sm" style={{ color: 'var(--muted)' }}>
                {events === null ? 'Reaching the archive…' : 'The API is not answering — the network needs it.'}
              </p>
            </div>
          )}

          <div className="canvas3d-caption mono text-xs" style={{ color: 'var(--muted)' }}>
            {hover ??
              'drag to orbit · scroll to zoom · click an actor to focus, an edge for its story'}
          </div>

          <button
            type="button"
            onClick={() => graphHandle.current?.fit()}
            className="canvas3d-fit mono text-xs px-3 py-1.5"
            style={{
              border: '1px solid var(--line)',
              color: 'var(--muted)',
              background: 'var(--panel)',
              cursor: 'pointer',
            }}
          >
            fit
          </button>

          <div className="canvas3d-legend mono text-xs" style={{ color: 'var(--muted)' }}>
            {(Object.keys(ACTOR_COLOR) as PackActor['actor_type'][]).map((t) => (
              <span key={t} className="inline-flex items-center gap-1.5">
                <span
                  aria-hidden
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ background: ACTOR_COLOR[t] }}
                />
                {TYPE_LABEL[t]}
              </span>
            ))}
            <span className="inline-flex items-center gap-1.5">
              <span
                aria-hidden
                className="inline-block h-0.5 w-4"
                style={{ background: RELATION_SWATCH.proxy }}
              />
              proxy (flow: patron → client)
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span
                aria-hidden
                className="inline-block h-0.5 w-4"
                style={{ background: EDGE_SWATCH_ESCALATING }}
              />
              escalating
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span
                aria-hidden
                className="inline-block h-0.5 w-4"
                style={{ background: EDGE_SWATCH_ACTIVE }}
              />
              active in window
            </span>
          </div>
        </section>

        <aside
          className="pane-scroll border-l px-4 py-4"
          style={{ borderColor: 'var(--line)', background: 'var(--panel)' }}
        >
          {selection?.kind === 'event' ? (
            <EventDetailPanel nodeId={selection.id} />
          ) : selection?.kind === 'dyad' ? (
            <DyadPanel dyadId={selection.id} onSelectEvent={selectEvent} />
          ) : selection?.kind === 'relation' ? (
            <RelationPanel
              relation={selection.relation}
              dyads={dyads}
              onSelectDyad={(id) => setSelection({ kind: 'dyad', id })}
            />
          ) : focusedActor ? (
            <ActorPanel
              actor={focusedActor}
              relations={relations}
              events={listed}
              onSelectEvent={selectEvent}
              onSelectRelation={(r) => setSelection({ kind: 'relation', relation: r })}
            />
          ) : (
            <>
              <div className="mb-3">
                <Microcaps>{year} in the archive</Microcaps>
              </div>
              <p className="text-sm" style={{ color: 'var(--muted)' }}>
                Scrub the slider and watch the network breathe. Click an actor
                to focus their world, a gold edge to read the patronage behind
                it, or an event on the left to see how it was coded and what it
                moved.
              </p>
            </>
          )}
          {selection && (
            <button
              type="button"
              onClick={() => setSelection(null)}
              className="mt-6 mono text-xs underline underline-offset-2"
              style={{ color: 'var(--muted)', background: 'none', border: 'none', cursor: 'pointer' }}
            >
              back
            </button>
          )}
        </aside>
      </main>

      <TimeSlider year={year} onChange={setYear} regimes={regimes} coverage={coverage} />
    </div>
  )
}
