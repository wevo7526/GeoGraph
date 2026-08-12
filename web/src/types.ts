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
  actors: { actors: PackActor[]; igo_spotlight?: string[] }
  marquee_events: { events: PackEvent[] }
}

export interface Flow {
  actor_id: string
  actor_name: string
  market_id: string
  market_name: string
  ticker: string
  as_of: string
  value_usd: number
  source_id: string
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

export interface ForecastSummary {
  node_id: string
  mode: 'near_term' | 'long_horizon'
  region_pack: string | null
  question: string
  generated_at: string
  horizon_end: string | null
  boundary_statement: string | null
  brier_score: number | null
}

export interface ForecastScenario {
  scenario_name: string
  likelihood: number | null
  market_implication: string
  rationale: string
  analogue_ids?: string[]
}

export interface ForecastDetail extends ForecastSummary {
  scenarios: ForecastScenario[]
  frozen_inputs: {
    pressure?: Record<string, number>
    windows?: Array<{ start: number; end: number; level: string }>
    episodes?: number
    continuations?: number
    as_of?: string
    method?: string
  }
}

export interface PaperPosition {
  ticker: string
  weight: number
  status: 'marked' | 'skipped'
  reason?: string
  entry_date?: string
  entry?: number
  mark_date?: string
  mark?: number
  pnl_usd?: number
}

export interface PaperBook {
  forecast: string
  escalation_likelihood: number
  entry_after: string
  notional_usd: number
  deployed_usd: number
  pnl_usd: number
  return_on_notional: number
  positions: PaperPosition[]
  method: string
}

export interface MarkedBook {
  notional_usd: number
  deployed_usd: number
  pnl_usd: number
  return_on_notional: number
  positions: PaperPosition[]
  method: string
}

export interface BacktestRow {
  quarter_end: string
  marked_through: string
  escalation_likelihood: number
  episodes: number
  pnl_usd: number
  quarter_return: number
  equity_usd: number
  positions: PaperPosition[]
  method: string
  computed_at: string
}

export interface BacktestLedger {
  region: string
  rows: BacktestRow[]
  summary: {
    notional_usd: number
    quarters_traded: number
    final_equity_usd: number
    total_return: number
    hit_rate: number
    max_drawdown: number
  } | null
  note?: string
  method?: string
}

export interface ForwardView {
  region: string
  forecast: {
    node_id: string
    generated_at: string
    horizon_end: string | null
    as_of: string
    escalation_likelihood: number
    scenarios: ForecastScenario[]
  }
  net_weights: Record<string, number>
  book: MarkedBook | null
  book_unavailable: string | null
  pressure: {
    node_id: string
    generated_at: string
    horizon_end: string | null
    boundary_statement: string
    trajectory: Record<string, number>
    windows: Array<{ start: number; end: number; level: string }>
  } | null
}

export interface WhatIfOptions {
  region: string
  actors: Array<{ id: string; name: string; actor_type: string }>
  codes: Array<{ code: string; label: string; goldstein: number; quad_class: string }>
}

export interface WhatIfResult {
  region: string
  hypothetical: {
    initiator: string
    target: string
    date: string
    cameo: string
    label: string
    goldstein: number
    quad_class: string
  }
  dyad: {
    node_id: string
    name: string | null
    baseline: number | null
    baseline_as_of: string | null
    escalation_baseline: number
    escalation_direction: 'escalating' | 'deescalating' | 'stable'
    escalation_magnitude: number
    note?: string
  }
  analogues: Array<{
    event_id: string
    name: string
    event_time: string
    similarity: number
    goldstein: number | null
    quad_class: string | null
    escalation_direction: string | null
    measured_effects: number
  }>
  transmission: {
    rows: Array<{
      ticker: string
      market: string
      window: string
      mean_abnormal_return: number
      n: number
    }>
    label: string
  }
  method: string
}

export interface Assessment {
  question: string
  region_pack: string
  assessment: string
  model: string
  method: string
}

export interface CaseStudyIndexEntry {
  slug: string
  pack: string
  title: string
  dek: string
  events: string[]
}
