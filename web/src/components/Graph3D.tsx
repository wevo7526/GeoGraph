import type { Ref } from 'react'
import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import ForceGraph3D, { type ForceGraphMethods } from 'react-force-graph-3d'
import { forceCenter, forceCollide, forceX, forceY, forceZ } from 'd3-force-3d'
import * as THREE from 'three'
import SpriteText from 'three-spritetext'
import type { Dyad, Flow, GraphEvent, PackActor, Relation } from '../types'

/** Actor-type encoding, shared with the legend overlay in Explorer.tsx.
 * Duplicated deliberately — WebGL cannot read CSS custom properties, and a
 * mismatch between legend and canvas would silently mislead (the MarketGraph
 * lesson). These are the validated categorical steps from styles.css, NOT the
 * UI inks: --text/--muted fail as series colors by design. */
export const ACTOR_COLOR: Record<PackActor['actor_type'], string> = {
  state: '#c48a12',
  person: '#b04a5c',
  org: '#4a82d4',
  swf: '#2f9960',
}

export const TYPE_LABEL: Record<PackActor['actor_type'], string> = {
  state: 'state',
  org: 'organisation',
  person: 'person',
  swf: 'sovereign fund',
}

/** One hue per DURABLE relation type — the standing structure drawn under the
 * event flow. Proxy is the brand's gold: patronage is the structure this
 * region's story runs on, and the directed particles show which way the hand
 * points. Swatches for the legend are these hues at full opacity. */
const RELATION_COLOR: Record<string, string> = {
  proxy: 'rgba(176, 141, 87, 0.55)',
  alliance: 'rgba(74, 130, 212, 0.45)',
  membership: 'rgba(125, 133, 152, 0.35)',
  trade: 'rgba(47, 153, 96, 0.40)',
  rivalry: 'rgba(164, 74, 63, 0.45)',
}
export const RELATION_SWATCH: Record<string, string> = {
  proxy: '#b08d57',
  alliance: '#4a82d4',
  membership: '#7d8598',
  trade: '#2f9960',
  rivalry: '#a44a3f',
}

/** The capital layer: SWF → market deployment from 13F. Fund-green edges,
 * cube nodes — a market is not an actor and does not pretend to be one. */
const FLOW_EDGE = 'rgba(47, 153, 96, 0.65)'
export const FLOW_SWATCH = '#2f9960'
export const MARKET_NODE = '#8a93a6'

/** Event-flow edges (dyads active in the slider window). */
const EDGE_ESCALATING = 'rgba(164, 74, 63, 0.85)'
const EDGE_ACTIVE = 'rgba(90, 98, 115, 0.70)'
const EDGE_DORMANT = 'rgba(35, 42, 58, 0.18)'
export const EDGE_SWATCH_ESCALATING = '#a44a3f'
export const EDGE_SWATCH_ACTIVE = '#5a6273'

/** What everything unrelated to the selection fades to: just below the white
 * ground, so the shape of the region stays visible as context. The plate is
 * WHITE (styles.css, 2026-08-15) — these constants were inverted with it. */
const DIM_NODE = '#dcdcdc'
const DIM_LINK = 'rgba(35, 42, 58, 0.10)'
const INK = '#ffffff'
const TEXT = '#111111'

interface Sim extends PackActor {
  kind: 'actor' | 'market'
  x?: number
  y?: number
  z?: number
  fx?: number
  fy?: number
  fz?: number
  val: number
  color: string
  /** Events touching this actor in the window — drives size and label weight. */
  activity: number
}

export type LinkKind = 'relation' | 'dyad' | 'flow'

interface SimLink {
  source: string | Sim
  target: string | Sim
  kind: LinkKind
  key: string
  color: string
  width: number
  particles: number
  relation?: Relation
  dyad?: Dyad
  flow?: Flow
  /** Events on this dyad inside the window. */
  count: number
  escalating: boolean
}

export interface Graph3DHandle {
  /** Frame everything — how you recover when the camera ends up inside a
   *  cluster or a mile outside it (the single most useful 3D control). */
  fit: (ms?: number) => void
}

export interface LinkSelection {
  kind: LinkKind
  relation?: Relation
  dyadId?: string
}

interface Props {
  actors: PackActor[]
  relations: Relation[]
  flows: Flow[]
  dyads: Dyad[]
  events: GraphEvent[]
  windowFrom: string
  windowTo: string
  selectedActor: string | null
  onSelectActor: (id: string | null) => void
  onSelectLink: (selection: LinkSelection) => void
  onHover: (text: string | null) => void
  handleRef?: Ref<Graph3DHandle>
}

export default function Graph3D({
  actors,
  relations,
  flows,
  dyads,
  events,
  windowFrom,
  windowTo,
  selectedActor,
  onSelectActor,
  onSelectLink,
  onHover,
  handleRef,
}: Props) {
  const fg = useRef<ForceGraphMethods<Sim, SimLink> | undefined>(undefined)
  const wrap = useRef<HTMLDivElement>(null)

  // MEASURED, NOT INHERITED. ForceGraph3D with no width/height draws a
  // window-sized canvas that overflows its grid column — the exact bug
  // MarketGraph documents. Sizing from the element means the drawer opening
  // reflows the canvas correctly.
  const [size, setSize] = useState({ width: 0, height: 0 })
  useEffect(() => {
    const element = wrap.current
    if (!element) return
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (!rect) return
      setSize({ width: Math.round(rect.width), height: Math.round(rect.height) })
    })
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  // Per-actor and per-dyad activity for the slider window. Restyling only —
  // the LAYOUT is a function of structure and must not reheat as you scrub.
  const activity = useMemo(() => {
    const byActor = new Map<string, number>()
    const byDyad = new Map<string, { count: number; escalating: boolean }>()
    for (const e of events) {
      if (e.event_time < windowFrom || e.event_time >= windowTo) continue
      for (const id of [e.initiator_id, e.target_id]) {
        if (id) byActor.set(id, (byActor.get(id) ?? 0) + 1)
      }
      if (e.dyad_id) {
        const entry = byDyad.get(e.dyad_id) ?? { count: 0, escalating: false }
        entry.count += 1
        entry.escalating = entry.escalating || e.escalation_direction === 'escalating'
        byDyad.set(e.dyad_id, entry)
      }
    }
    return { byActor, byDyad }
  }, [events, windowFrom, windowTo])

  // react-force-graph MUTATES what it is given: it writes x/y/z onto nodes
  // and replaces link endpoints with node references. The simulation gets a
  // fresh copy on every change (the MarketGraph Graph3D rule).
  const data = useMemo(() => {
    const ids = new Set(actors.map((a) => a.id))
    const nodes = actors.map<Sim>((a) => {
      const n = activity.byActor.get(a.id) ?? 0
      return {
        ...a,
        kind: 'actor' as const,
        activity: n,
        // Cube-root-ish growth: an actor in twenty events must read bigger,
        // not twenty times bigger.
        val: 3 + Math.min(9, 2.6 * Math.sqrt(n)),
        color: ACTOR_COLOR[a.actor_type],
      }
    })
    // The capital layer's markets: cube nodes, sized by total deployed value
    // across the window's filings — a market is a SENSOR here, not an actor.
    const marketTotals = new Map<string, { name: string; total: number }>()
    for (const f of flows) {
      if (!ids.has(f.actor_id)) continue
      const entry = marketTotals.get(f.market_id) ?? { name: f.market_name, total: 0 }
      entry.total += f.value_usd
      marketTotals.set(f.market_id, entry)
    }
    for (const [marketId, entry] of [...marketTotals.entries()].sort()) {
      ids.add(marketId)
      nodes.push({
        id: marketId,
        name: entry.name,
        actor_type: 'org',
        kind: 'market',
        activity: 0,
        val: 4 + Math.min(8, Math.max(0, Math.log10(entry.total) - 8)),
        color: MARKET_NODE,
      })
    }

    const links: SimLink[] = []
    for (const r of relations) {
      if (!ids.has(r.a_id) || !ids.has(r.b_id)) continue
      links.push({
        source: r.a_id,
        target: r.b_id,
        kind: 'relation',
        key: `rel:${r.a_id}--${r.b_id}--${r.relation_type}`,
        color: RELATION_COLOR[r.relation_type] ?? 'rgba(125, 133, 152, 0.35)',
        width: 1.4,
        // Direction IS the information on a patron→client edge.
        particles: r.relation_type === 'proxy' ? 2 : 0,
        relation: r,
        count: 0,
        escalating: false,
      })
    }
    for (const d of dyads) {
      if (!d.actor_a_id || !d.actor_b_id || d.actor_a_id === d.actor_b_id) continue
      if (!ids.has(d.actor_a_id) || !ids.has(d.actor_b_id)) continue
      const state = activity.byDyad.get(d.node_id)
      links.push({
        source: d.actor_a_id,
        target: d.actor_b_id,
        kind: 'dyad',
        key: `dyad:${d.node_id}`,
        color: state ? (state.escalating ? EDGE_ESCALATING : EDGE_ACTIVE) : EDGE_DORMANT,
        width: state ? 1.2 + Math.min(2.4, state.count * 0.6) : 0.5,
        particles: 0,
        dyad: d,
        count: state?.count ?? 0,
        escalating: state?.escalating ?? false,
      })
    }
    for (const f of flows) {
      if (!ids.has(f.actor_id) || !ids.has(f.market_id)) continue
      links.push({
        source: f.actor_id,
        target: f.market_id,
        kind: 'flow',
        key: `flow:${f.actor_id}--${f.market_id}`,
        color: FLOW_EDGE,
        // Width scales with the ORDER OF MAGNITUDE of deployed capital:
        // $100M and $25B must read as different classes of relationship.
        width: 0.8 + Math.min(3, Math.max(0, Math.log10(f.value_usd) - 8)),
        particles: 2,
        flow: f,
        count: 0,
        escalating: false,
      })
    }
    return { nodes, links }
  }, [actors, relations, flows, dyads, activity])

  // A durable relation outside its validity window is NOT drawn — an
  // alliance signed in 1949 is a false claim on the 1914 screen. The check
  // styles rather than restructures: the LAYOUT stays a function of the
  // century's whole structure, so scrubbing never reheats the simulation.
  const inWindow = useCallback(
    (r: Relation | undefined) => {
      if (!r) return true
      if (r.valid_from && r.valid_from > windowTo) return false
      return !(r.valid_to && r.valid_to < windowFrom)
    },
    [windowFrom, windowTo],
  )

  // FOCUS: selecting an actor dims everything not attached to it, and what IS
  // attached keeps its own layer color — which kind of tie it is matters as
  // much as that there is one.
  const focus = useMemo(() => {
    if (!selectedActor) return null
    const endpoint = (side: string | Sim) => (typeof side === 'string' ? side : side.id)
    const neighbours = new Set<string>([selectedActor])
    const edges = new Set<string>()
    for (const link of data.links) {
      const source = endpoint(link.source)
      const target = endpoint(link.target)
      if (source !== selectedActor && target !== selectedActor) continue
      neighbours.add(source)
      neighbours.add(target)
      edges.add(link.key)
    }
    return { neighbours, edges }
  }, [data, selectedActor])

  useImperativeHandle(
    handleRef,
    () => ({ fit: (ms = 700) => fg.current?.zoomToFit(ms, 40) }),
    [],
  )

  // ORBIT, NOT TRACKBALL: trackball has no polar clamp, so rotating past the
  // pole flips the world's up vector and every later drag reads backwards —
  // the inverted-controls bug MarketGraph shipped and then documented.
  useEffect(() => {
    const controls = fg.current?.controls() as
      | {
          zoomSpeed?: number
          rotateSpeed?: number
          enableDamping?: boolean
          dampingFactor?: number
          minDistance?: number
          maxDistance?: number
        }
      | undefined
    if (!controls) return
    controls.zoomSpeed = 1.4
    controls.rotateSpeed = 0.7
    controls.enableDamping = true
    controls.dampingFactor = 0.12
    controls.minDistance = 20
    controls.maxDistance = 2400
  }, [])

  // LAYOUT. Charge alone lets a hub pull its neighbours into itself; the
  // collision force is the hard minimum separation, measured in the same
  // units as the rendered radius. This is a ~20-node roster, so everything
  // takes the sparse tuning.
  useEffect(() => {
    const graph = fg.current
    if (!graph) return
    const charge = graph.d3Force('charge') as unknown as
      | { strength?: (fn: (n: Sim) => number) => void; distanceMax?: (d: number) => void }
      | undefined
    charge?.strength?.((n: Sim) => -(90 + 40 * n.val))
    // Bounded, or the UNLINKED actors (a person, a quiet fund) get pushed to
    // wherever repulsion peters out and zoomToFit frames a huge empty volume —
    // every node then renders as dust. Same failure the 2D view had.
    charge?.distanceMax?.(420)

    const link = graph.d3Force('link') as unknown as
      | { distance?: (fn: (l: SimLink) => number) => void; strength?: (s: number) => void }
      | undefined
    link?.distance?.((l: SimLink) => (l.kind === 'relation' ? 70 : 95))
    link?.strength?.(0.16)

    graph.d3Force('collide', forceCollide<Sim>((n) => Math.cbrt(n.val) * 7).strength(0.9))
    graph.d3Force('center', forceCenter().strength(0.08))
    // Positional containment: forceCenter only translates the centroid, so a
    // node with no links has NOTHING pulling it in. Weak per-axis springs give
    // every node a reason to stay near the middle without collapsing the
    // linked structure. Y slightly stronger: a flattened cloud reads better
    // on a landscape screen.
    graph.d3Force('x', forceX(0).strength(0.045))
    graph.d3Force('y', forceY(0).strength(0.07))
    graph.d3Force('z', forceZ(0).strength(0.045))
  }, [data])

  // Frame the graph ONCE per structure, when the simulation actually
  // settles — a timer guesses and guesses wrong as the cloud keeps
  // expanding. The guard matters: onEngineStop also fires after every drag,
  // and auto-refit there would yank the camera out of the viewer's hands.
  const fitted = useRef(false)
  useEffect(() => {
    fitted.current = false
  }, [data.nodes.length])
  const handleEngineStop = useCallback(() => {
    if (!fitted.current) {
      fitted.current = true
      fg.current?.zoomToFit(600, 40)
    }
  }, [])

  const describeLink = useCallback((l: SimLink): string => {
    const name = (side: string | Sim) => (typeof side === 'string' ? side : side.name)
    if (l.kind === 'relation' && l.relation) {
      const r = l.relation
      const window = r.valid_to ? `${r.valid_from}–${r.valid_to}` : `since ${r.valid_from}`
      return `${r.a_name} → ${r.b_name} · ${r.relation_type} ${window}`
    }
    if (l.kind === 'flow' && l.flow) {
      const f = l.flow
      const billions = (f.value_usd / 1e9).toFixed(1)
      return (
        `${f.actor_name} → ${f.market_name}: $${billions}B as of ${f.as_of} · ` +
        'US-listed long equity only (13F, 45-day lag)'
      )
    }
    const tail = l.count
      ? `${l.count} event${l.count === 1 ? '' : 's'} in window${l.escalating ? ' · escalating' : ''}`
      : 'quiet in window'
    return `${name(l.source)} – ${name(l.target)} · ${tail}`
  }, [])

  // Labels with LEVEL OF DETAIL: a pack-sized cast labels everyone (an
  // unlabeled sphere in a serious tool is decoration), but a 1940s cast is a
  // hundred states — there, labels go to the actors DOING something in the
  // window, the selection, and its neighbours. The MarketGraph lesson: a
  // text sprite per node is unreadable exactly when it is most expensive.
  const sparse = data.nodes.length <= 40
  const nodeLabelObject = useCallback(
    (n: Sim) => {
      const dimmed = focus !== null && !focus.neighbours.has(n.id)
      const label = (height: number): THREE.Object3D => {
        const sprite = new SpriteText(n.name)
        sprite.color = n.id === selectedActor ? '#000000' : dimmed ? '#b8b8b8' : TEXT
        sprite.fontFace = 'Georgia'
        sprite.textHeight = height
        ;(sprite as unknown as { position: { set: (x: number, y: number, z: number) => void } })
          .position.set(0, -(n.val + 5), 0)
        return sprite as unknown as THREE.Object3D
      }
      if (n.kind === 'market') {
        // A market is a CUBE — a different kind of thing, said with shape,
        // not another hue for the categorical set to absorb.
        const group = new THREE.Group()
        const side = n.val * 1.6
        const cube = new THREE.Mesh(
          new THREE.BoxGeometry(side, side, side),
          new THREE.MeshLambertMaterial({
            color: dimmed ? DIM_NODE : n.color,
            transparent: true,
            opacity: 0.92,
          }),
        )
        group.add(cube)
        group.add(label(sparse ? 4.6 : 3.8))
        return group as unknown as THREE.Object3D
      }
      const wanted =
        sparse ||
        n.activity > 0 ||
        n.id === selectedActor ||
        (focus !== null && focus.neighbours.has(n.id))
      if (!wanted) return undefined as unknown as THREE.Object3D
      return label(n.id === selectedActor ? 6 : sparse ? 4.6 : 3.8)
    },
    [focus, selectedActor, sparse],
  )

  return (
    <div ref={wrap} className="canvas3d-fill">
      <ForceGraph3D<Sim, SimLink>
        ref={fg}
        width={size.width || undefined}
        height={size.height || undefined}
        graphData={data}
        backgroundColor={INK}
        showNavInfo={false}
        controlType="orbit"
        nodeVal="val"
        nodeColor={(n) => {
          if (n.id === selectedActor) return '#000000'
          if (focus && !focus.neighbours.has(n.id)) return DIM_NODE
          return n.color
        }}
        nodeOpacity={0.92}
        nodeResolution={16}
        nodeLabel={() => ''}
        nodeThreeObjectExtend={(n) => n.kind !== 'market'}
        nodeThreeObject={nodeLabelObject}
        linkColor={(l) => {
          if (l.kind === 'relation' && !inWindow(l.relation)) return 'rgba(0,0,0,0)'
          if (focus && !focus.edges.has(l.key)) return DIM_LINK
          return l.color
        }}
        linkWidth={(l) => {
          if (l.kind === 'relation' && !inWindow(l.relation)) return 0
          if (focus) return focus.edges.has(l.key) ? Math.max(l.width, 1.6) : 0.3
          return l.width
        }}
        linkOpacity={focus ? 1 : 0.75}
        linkCurvature={0}
        linkDirectionalParticles={(l) =>
          l.kind === 'relation' && !inWindow(l.relation) ? 0 : l.particles
        }
        linkDirectionalParticleWidth={1.6}
        linkDirectionalParticleSpeed={0.004}
        onNodeClick={(n) => onSelectActor(n.id === selectedActor ? null : n.id)}
        onNodeHover={(n) =>
          onHover(
            n
              ? n.kind === 'market'
                ? `${n.name} — market · SWF capital deployed here (13F)`
                : `${n.name} — ${TYPE_LABEL[n.actor_type]} · ${n.activity} event${
                    n.activity === 1 ? '' : 's'
                  } in window`
              : null,
          )
        }
        onLinkClick={(l) => {
          if (l.kind === 'flow') return // the hover line carries the number
          if (l.kind === 'relation' && !inWindow(l.relation)) return
          onSelectLink(
            l.kind === 'relation'
              ? { kind: 'relation', relation: l.relation }
              : { kind: 'dyad', dyadId: l.dyad?.node_id },
          )
        }}
        onLinkHover={(l) => {
          if (l && l.kind === 'relation' && !inWindow(l.relation)) return onHover(null)
          onHover(l ? describeLink(l) : null)
        }}
        onBackgroundClick={() => onSelectActor(null)}
        onNodeDragEnd={(n) => {
          // Pin a dragged node so the viewer's arrangement survives the tick.
          n.fx = n.x
          n.fy = n.y
          n.fz = n.z
        }}
        onEngineStop={handleEngineStop}
        cooldownTicks={280}
        warmupTicks={0}
      />
    </div>
  )
}
