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

export interface RetrodictionAnchor {
  as_of: string
  flagged_years: number[]
  hot_years: number[]
  hits: number[]
  hit_rate: number | null
  base_rate: number | null
  horizon_years_observed: number
}

export interface Retrodiction {
  as_of: string
  region_pack: string
  verdict?: string
  /** Per-anchor record; hit_rate/base_rate below aggregate over all of them. */
  anchors?: RetrodictionAnchor[]
  anchors_evaluated?: number
  flagged_years: number[]
  hot_years?: number[]
  hits: number[]
  hit_rate: number | null
  base_rate: number | null
  flagged_total?: number
  hits_total?: number
  boundary_statement?: string
  method?: string
}

export interface ForecastSummary {
  node_id: string
  // Four modes, believed for four different reasons: near_term and
  // long_horizon are COUNTED, model is FITTED, sequence is SOLVED.
  mode: 'near_term' | 'long_horizon' | 'model' | 'sequence'
  region_pack: string | null
  question: string
  generated_at: string
  horizon_end: string | null
  boundary_statement: string | null
  brier_score: number | null
  retrodiction: Retrodiction | null
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
    coverage?: Record<string, string[]>
    pressure_span?: [number, number] | null
    episodes?: number
    continuations?: number
    evidence_span?: [string, string] | null
    as_of?: string
    method?: string
    /** mode='near_term' — names for the focal dyad ids, which may be outside
     *  the roster's top-40 slice (focal ranks by conflictuality, not
     *  popularity). */
    focal_dyads?: string[]
    dyad_names?: Record<string, string>
    dyad_counts?: Record<string, [number, number]>
    // mode='model' only — the learned trajectories and the artifact that
    // produced them.
    trajectories?: ModelTrajectory[]
    model?: {
      name: string
      hash: string
      target: string
      features: string[]
      train_span: [string, string]
      gate_reason: string
    }
    walk_forward?: Array<{
      horizon: number
      cut_year: number
      within_dyad: number | null
      within_dyad_persistence: number | null
      rmse: number
      rmse_persistence: number
    }>
    // mode='sequence' only — the solved distribution over event paths.
    dyads?: SequenceDyad[]
    equilibrium?: {
      concept: string
      payoffs: Record<string, number>
      distance: number
      converged: boolean
      seed: number
      identification: string
      method: string
    }
    kernel?: {
      cells: number
      measured: number
      fallback: number
      share_measured: number
      observations: number
    }
    bands?: number[]
  }
}

/** One market's measured effect distribution for a predicted step. Never a
 *  modelled price — these are quantiles of measured AFFECTED abnormal
 *  returns for comparable past events. */
export interface StepMarket {
  market_id: string
  market_name: string
  n: number
  match: 'quad+band' | 'quad only'
  thin: boolean
  min: number
  p25: number
  median: number
  p75: number
  max: number
}

export interface SequenceStep {
  period: number
  action_a: string
  action_b: string
  quad: string
  intensity_band: number
  /** The kernel's probability of this band given the joint action — the walk
   *  branches over bands now instead of collapsing rows to their mode. */
  band_probability?: number
  band_spread: [number, number]
  market: StepMarket[]
}

/** Where a solve's opening state came from — measured or defaulted, the
 *  reader sees which. */
export interface GameOpening {
  intensity_band: number
  capability: { band: number; ratio: number | null; source: 'cinc' | 'default' }
  beliefs: {
    a: number
    b: number
    quarters_observed: number
    source: 'bayes_filter' | 'default'
  }
  /** The ML→game bridge's audit block; null when untilted. */
  tilt: { eta: number; scale: number; model: string; method: string } | null
}

export interface SequenceDyad {
  dyad_id: string
  dyad_name: string
  active_quarters: number
  opening_band: number
  paths: Array<{ probability: number; steps: SequenceStep[] }>
  paths_enumerated: number
  retained_probability: number
  // The per-period distribution over intensity bands. This LEADS the display:
  // the path tail is long and flat (8 of 271 paths can retain under a tenth of
  // the mass), so the fan is the honest summary and the paths are the detail.
  marginal: Array<{
    period: number
    distribution: number[]
    modal_band: number
    expected_band: number
  }>
  pricing: {
    measurements: number
    cells: number
    regime_gated_to: string
    min_measurements: number
    method: string
    note: string | null
  }
}

export interface ModelTrajectory {
  dyad_id: string
  dyad_name: string
  active_quarters: number
  last_observed: string
  path: Array<{
    horizon: number
    quarter: number
    date: string
    intensity: number
    deviation: number
    lo: number
    hi: number
  }>
}

/** A dyad as the forecaster's panel sees it — quarters, not events. */
export interface PanelDyad {
  dyad_id: string
  dyad_name: string
  quarters: number
  active_quarters: number
  peak_intensity: number
  mean_intensity: number
  first: string
  last: string
}

export interface DyadSeries {
  dyad_id: string
  dyad_name: string
  rows: Array<{ q: number; date: string; intensity: number; events: number; tone: number }>
  active_quarters: number
  peak: number
  span: [string, string]
}

export interface Precedent {
  dyad_id: string
  dyad_name: string
  as_of: string
  episode_threshold: number
  episodes: Array<{
    date: string
    intensity: number
    aftermath: Array<{ offset: number; date: string; intensity: number }>
  }>
  fan: Array<{
    offset: number
    n: number
    min: number
    p25: number
    median: number
    p75: number
    max: number
  }>
  markets: Array<{
    market_id: string
    market_name: string
    n: number
    windows: string[]
    min: number
    p25: number
    median: number
    p75: number
    max: number
  }>
  markets_note: string | null
  method: string
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
  /** When this history was computed — the reader's staleness check. */
  computed_at?: string
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
  /** A composition drawn from this region's own spine, so the composer opens
   *  on a question the reader recognises rather than on an empty form. */
  example: {
    initiator: string
    target: string
    cameo: string
    drawn_from: { event_id: string; name: string; date: string }
  } | null
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
  /** Exactly what the agent was handed. Returned so section 17 — the agent
   *  never originates a number — is checkable by the reader, not just stated. */
  context?: {
    frozen_forecasts?: Array<{ node_id: string; mode: string; question: string }>
    most_conflictual_dyads?: Array<{ node_id: string; name: string; baseline: number | null }>
    note?: string
  }
}

export interface CaseStudyIndexEntry {
  slug: string
  pack: string
  title: string
  dek: string
  events: string[]
}


/** What a counterfactual control panel needs to open: the region's FITTED
 *  payoffs (so "no change" reproduces the frozen forecast), the dyads worth
 *  asking about, and how much of the kernel is real. */
export interface GameDefaults {
  region: string
  payoffs: Record<string, number>
  kernel: { cells: number; measured: number; share_measured: number; observations: number }
  bands: number[]
  actions: string[]
  dyads: Array<{ dyad_id: string; dyad_name: string; active_quarters: number }>
  /** The yield curve's answer to how long these crises last — bonds. */
  duration?: {
    events_with_a_curve_response: number
    tenors_measured: string[]
    dyads: number
    usable_dyads: number
    calibration: string | null
    note: string | null
    method: string
  }
}

/** A re-solved equilibrium. `baseline: true` means no lever was moved — the
 *  fitted payoffs at the data-driven opening state, the same construction as
 *  the frozen sequence forecast. Anything else is a counterfactual and the
 *  boundary statement says so. `frozen` is always false either way. */
export interface GameExplore {
  region: string
  dyad_id: string
  dyad_name: string
  opening_band: number
  payoffs: Record<string, number>
  changed: Record<string, number>
  capability: number
  beliefs: { a: number; b: number }
  opening: GameOpening
  baseline: boolean
  marginal: Array<{
    period: number
    distribution: number[]
    modal_band: number
    expected_band: number
  }>
  escalation_propensity: Record<string, number[]>
  paths: Array<{ probability: number; steps: SequenceStep[] }>
  paths_enumerated: number
  retained_probability: number
  pricing: { measurements: number; note: string | null }
  kernel: { share_measured: number }
  frozen: false
  boundary_statement: string
}
