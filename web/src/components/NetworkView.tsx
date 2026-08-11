import {
  forceCenter,
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  type SimulationLinkDatum,
  type SimulationNodeDatum,
} from 'd3-force'
import { useMemo } from 'react'
import type { PackActor } from '../types'

// A force-directed placeholder over the pack roster: enough to prove the
// wiring and set the visual language. Phase 4 replaces the sample edges with
// real RELATES_TO structure filtered by the slider year, and the layout keeps
// this module's shape.
//
// The palette is duplicated from styles.css because SVG attribute fills are
// set here, not in CSS — keep the two in step (the MarketGraph WebGL lesson).
const COLOR: Record<PackActor['actor_type'], string> = {
  state: '#b08d57',
  org: '#7d8598',
  person: '#d9d4c5',
  swf: '#5e8f6e',
}

const SAMPLE: PackActor[] = [
  { id: 'actor:cow-2', name: 'United States', actor_type: 'state' },
  { id: 'actor:cow-670', name: 'Saudi Arabia', actor_type: 'state' },
  { id: 'actor:cow-630', name: 'Iran', actor_type: 'state' },
  { id: 'actor:cow-666', name: 'Israel', actor_type: 'state' },
  { id: 'actor:cow-696', name: 'UAE', actor_type: 'state' },
  { id: 'actor:opec', name: 'OPEC', actor_type: 'org' },
  { id: 'actor:swf-pif', name: 'PIF', actor_type: 'swf' },
]

// Illustrative structure only, for layout: the real network is RELATES_TO.
const SAMPLE_LINKS: Array<[string, string]> = [
  ['actor:cow-2', 'actor:cow-666'],
  ['actor:cow-2', 'actor:cow-670'],
  ['actor:cow-670', 'actor:opec'],
  ['actor:cow-630', 'actor:opec'],
  ['actor:cow-670', 'actor:swf-pif'],
  ['actor:cow-696', 'actor:cow-666'],
]

interface LayoutNode extends SimulationNodeDatum {
  id: string
  name: string
  actor_type: PackActor['actor_type']
}

const W = 720
const H = 460

export default function NetworkView({ actors }: { actors: PackActor[] | null }) {
  const roster = actors && actors.length > 0 ? actors : SAMPLE
  const live = Boolean(actors && actors.length > 0)

  const { nodes, links } = useMemo(() => {
    const ids = new Set(roster.map((a) => a.id))
    // d3-force MUTATES what it is given — always feed it fresh copies and
    // keep component state clean (the MarketGraph Graph3D rule).
    const nodes: LayoutNode[] = roster.map((a) => ({
      id: a.id,
      name: a.name,
      actor_type: a.actor_type,
    }))
    const links: SimulationLinkDatum<LayoutNode>[] = SAMPLE_LINKS.filter(
      ([s, t]) => ids.has(s) && ids.has(t),
    ).map(([source, target]) => ({ source, target }))

    const sim = forceSimulation(nodes)
      .force('charge', forceManyBody().strength(-160))
      .force('link', forceLink<LayoutNode, SimulationLinkDatum<LayoutNode>>(links).id((n) => n.id).distance(90))
      .force('center', forceCenter(W / 2, H / 2))
      .force('collide', forceCollide(26))
      .stop()
    for (let i = 0; i < 200; i++) sim.tick()
    return { nodes, links }
  }, [roster])

  return (
    <figure className="relative">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full" role="img" aria-label="actor network">
        {links.map((l, i) => {
          const s = l.source as LayoutNode
          const t = l.target as LayoutNode
          return (
            <line key={i} x1={s.x} y1={s.y} x2={t.x} y2={t.y} stroke="#232a3a" strokeWidth={1} />
          )
        })}
        {nodes.map((n) => (
          <g key={n.id} transform={`translate(${n.x},${n.y})`}>
            <circle r={7} fill={COLOR[n.actor_type]} />
            <text dy={-12} textAnchor="middle" fontSize={11} fill="#7d8598">
              {n.name}
            </text>
          </g>
        ))}
      </svg>
      <figcaption
        className="absolute bottom-2 right-3 text-xs mono"
        style={{ color: 'var(--muted)' }}
      >
        {live ? 'roster: MENA pack · edges: placeholder until Phase 4' : 'sample data — API offline'}
      </figcaption>
    </figure>
  )
}
