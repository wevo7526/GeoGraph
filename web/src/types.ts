// Shapes mirror the API responses. The API is the contract; keep these in
// step with core/api rather than inventing richer local shapes.

export interface BootPackStatus {
  pack?: string
  step?: string
  ok: boolean
  seconds?: number
  error?: string
}

export interface BootStatus {
  seeded: boolean
  reason?: string
  packs?: BootPackStatus[]
  panel?: { ok: boolean; error?: string; skipped?: string } | null
  prices?: { ok: boolean; error?: string; skipped?: string } | null
  study?: { ok: boolean; error?: string; skipped?: string } | null
}

export interface Health {
  status: string
  graph: 'open' | 'unavailable'
  graphError: string | null
  disabled: Record<string, string>
  boot?: BootStatus | null
}

export interface Stats {
  nodes: Record<string, number>
  edges: Record<string, number>
}

export interface RegimeEntry {
  id: string
  name: string
  start: string
  end: string | null
  note?: string
}

export type Segmentation = Record<string, RegimeEntry[]>

export interface PackActor {
  id: string
  name: string
  actor_type: 'state' | 'org' | 'person' | 'swf'
  cow_ccode?: number
}

export interface PackEvent {
  id: string
  date: string
  name: string
  initiator?: string
  target?: string
  quad_class?: string
  phase0_candidate?: boolean
  note?: string
}

export interface Pack {
  name: string
  actors: { actors: PackActor[] }
  marquee_events: { events: PackEvent[] }
}

// ── the graph, as the API serves it ─────────────────────────────────────────

export type EscalationDirection = 'escalating' | 'stable' | 'deescalating'

export interface GraphEvent {
  node_id: string
  name: string
  event_time: string
  cameo_code: string
  quad_class: string | null
  goldstein: number | null
  escalation_direction: EscalationDirection | null
  escalation_magnitude: number | null
  escalation_baseline: number | null
  fidelity_tier: string | null
  temporal_resolution: string | null
  source_scale: string | null
  region_pack: string | null
  // Ride-along ids so one /api/events request can draw the network for a
  // window. Null when the event's actors failed to code — listed, not hidden.
  initiator_id: string | null
  target_id: string | null
  dyad_id: string | null
}

export interface EventList {
  rows: GraphEvent[]
  truncated: boolean
}

export interface Effect {
  ticker: string
  market: string
  market_type: string
  window: string
  resolution: string
  raw_return: number | null
  abnormal_return: number | null
  t_stat: number | null
  p_value: number | null
  first_mover: boolean
  overlapping: boolean
  method: string
}

export interface Dyad {
  node_id: string
  name: string
  actor_a_id?: string
  actor_b_id?: string
  ewma_baseline: number | null
  ewma_as_of: string | null
}

export interface EventDetail extends GraphEvent {
  initiator: { node_id: string; name: string } | null
  target: { node_id: string; name: string } | null
  dyad: Dyad | null
  regimes: { node_id: string; name: string; kind: string }[]
  sources: { node_id: string; name: string; url: string; citation: string }[]
}

export interface GraphActor {
  node_id: string
  name: string
  actor_type: 'state' | 'org' | 'person' | 'swf'
  cow_ccode?: number | null
  state_from?: string | null
  state_to?: string | null
  region_pack?: string | null
}

export interface Relation {
  a_id: string
  a_name: string
  b_id: string
  b_name: string
  relation_type: string
  valid_from: string
  valid_to: string
  source_id: string
}

export interface TrajectoryPoint {
  node_id: string
  name: string
  event_time: string
  goldstein: number | null
  escalation_baseline: number | null
  escalation_direction: EscalationDirection | null
  escalation_magnitude: number | null
}

export interface Trajectory extends Dyad {
  events: TrajectoryPoint[]
}

export interface CaseStudyEpisode {
  node_id: string
  missing?: string
  name?: string
  event_time?: string
  cameo_code?: string
  quad_class?: string
  goldstein?: number | null
  escalation_direction?: EscalationDirection | null
  escalation_magnitude?: number | null
  escalation_baseline?: number | null
  note?: string
  dyad?: Dyad | null
  effects?: Effect[]
  first_movers?: string[]
}

export interface CaseStudy {
  slug: string
  pack: string
  title: string
  dek: string
  summary: string
  reading: string
  caveat: string
  episodes: CaseStudyEpisode[]
  measured: number
  status: 'measured' | 'not_yet_measured'
}

export interface CaseStudyIndexEntry {
  slug: string
  pack: string
  title: string
  dek: string
  events: string[]
}
