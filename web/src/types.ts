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
  /**
   * Which instrument produced this pair's kernel, or null for the region's
   * plain counted table. `features` present = the dynamics model (the counted
   * table conditioned on this pair's own record); `eta` present = the older
   * ML→game bridge, which is the fallback where no dynamics artifact ships.
   */
  tilt: {
    model: string
    method: string
    eta?: number
    scale?: number
    features?: Record<string, number>
    max_tilt?: number
    gate?: string
  } | null
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

/** A relationship's market-moving events, most recent first — the Relationship
 *  page's past→now timeline. Each event carries the measured moves of its
 *  markets (AFFECTED abnormal returns), never a modelled number. */
export interface TimelineEvent {
  event_id: string
  date: string
  name?: string | null
  goldstein?: number | null
  escalation_direction?: string | null
  escalation_magnitude?: number | null
  fidelity_tier?: string | null
  initiator_id?: string | null
  target_id?: string | null
  first_mover?: string | null
  markets: Array<{
    market_id: string
    market_name: string
    car: number
    window: string
    p_value?: number | null
    first_mover?: boolean
  }>
}

export interface DyadTimeline {
  dyad: string
  total: number
  events: TimelineEvent[]
}

/** GET /api/impact/{event_id} — measured beside expected, with the surprise. */
export interface EventImpact {
  mode: 'historical' | 'hypothetical'
  event: {
    id: string
    date: string
    dyad: string
    actors: { initiator: string; target: string }
    region?: string
    escalation?: { direction: string | null; magnitude: number | null }
    goldstein?: number | null
  }
  markets: Array<{
    market_id: string
    market_name: string
    measured: {
      car: number
      window: string
      first_mover: boolean
      resolution: string
    } | null
    expected: {
      mean_car: number
      median_car: number
      lo: number
      hi: number
      n_precedents: number
    } | null
    surprise: number | null
  }>
  precedents: { n: number; as_of: string; regime_gated: boolean }
  boundary_statement: string
}

/** GET /api/impact/coverage — the market-movement trace registered per dyad. */
export interface ImpactCoverage {
  region: string
  dyads: Array<{
    dyad_id: string
    events: number
    measured: number
    share_measured: number
    status: 'measured' | 'unmeasured' | 'no_events'
  }>
  summary: {
    dyads: number
    dyads_measured: number
    events: number
    events_measured: number
    share_measured: number
  }
  note: string
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
    quarters_skipped?: number | null
    final_equity_usd: number
    total_return: number
    hit_rate: number
    max_drawdown: number
    best_quarter?: string
    worst_quarter?: string
    first_quarter?: string
    last_quarter?: string
  } | null
  drawdown?: Array<{ quarter_end: string; drawdown: number }>
  attribution?: Array<{
    ticker: string
    pnl_usd: number
    quarters: number
    hit_rate: number | null
    mean_abs_weight: number | null
  }>
  skipped?: Array<{ quarter_end: string; reason: string }>
  skip_reasons?: Array<{
    reason: string
    quarters: number
    example: string
    first?: string
    last?: string
  }>
  quarters_skipped?: number
  books?: { escalation: Record<string, number>; reversion: Record<string, number> }
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


// ── the solved-game surface (core/games/scenarios.py, 2026-08-15) ─────────

export interface ScenarioStep extends SequenceStep {
  belief_a?: number
  belief_b?: number
}

export interface Scenario {
  scenario_name: string
  kind: string
  /** Pooled over every enumerated course the classifier reads as this kind —
   *  so it answers "how likely is mutual escalation at all", and the
   *  likelihoods across a dyad's scenarios still sum to the retained mass. */
  likelihood: number
  courses?: number
  lead_likelihood?: number
  dyad_id: string
  dyad_name: string
  presser: string | null
  /** The modal course of this kind; `courses` says how many were pooled. */
  course: string
  steps?: ScenarioStep[]
  opening_band: number
  end_band: number
  end_band_range?: [number, number]
  end_label: string
  delta_band: number
  beliefs_end?: { a: number | null; b: number | null } | null
  market_implications: Array<{
    market_id: string
    market_name: string
    median: number
    n: number
    steps_priced: number
  }>
  rationale: string
  /** The family's own label for the kind — "free-riding" where an
   *  adversary's course would read "one-sided pressure". */
  kind_label?: string | null
  /** And what that kind MEANS, in the family's own words ("both sides
   *  escalate, then at least one steps back"). Carried since 2026-08-17 so a
   *  reader surface never has to print the raw course string to say it. */
  kind_sentence?: string | null
  tone_label?: string
  standing?: Standing | null
  posture?: Posture | null
  family?: Family | null
}

/** WHICH GAME THIS PAIR PLAYS — ally / rival / adversary — from what it IS
 *  (standing) and how its record READS (posture). The solver has one game, a
 *  crisis-bargaining model, and it is the right one only for adversaries:
 *  `native` says whether the solved game is this family's own, and `headline`
 *  is the word the surface may use for the solved probability (friction,
 *  hardening, escalation) — calling an alliance's friction "escalation" is
 *  the specific thing that made US–Japan read as a war. */
export interface Family {
  family: 'ally' | 'rival' | 'adversary'
  why: string
  native: boolean
  question: string
  headline: string
  bad_end: string
  note: string
}

/** What a pair IS: the graph's declared, dated, sourced relations in force at
 *  the solve's as_of. The only field entitled to characterise a relationship —
 *  mean tone ranks pairs by how much they talk. */
export interface Standing {
  relations: Array<{
    relation_type: string
    since: string
    until: string | null
    source_id: string
    directed_from: string
  }>
  source: string
  as_of?: string
}

/** How the coded record READS lately: the coercive share of the pair's events
 *  over its last few quarters, with the sample that stands behind it. */
export interface Posture {
  label: string
  share: number | null
  events: number
  coercive: number
  tone: number | null
  quarters: number
  thin: boolean
}

export interface OpeningMatrix {
  a: number[][]
  b: number[][]
  actions: string[]
  type: string
  mix_a: number[]
  mix_b: number[]
  value: number
}

export interface NashGap {
  mean: number
  max: number
  share_product_form: number
  stage_games: number
  all_optimal: boolean
  /** The degeneracy audit: a vertex selection reports entropy 0 at every
   *  stage, which is what used to produce "most likely course … at 100%". */
  entropy_mean?: number
  entropy_min?: number
  ce_violation_max?: number
}

export interface ConceptSolution {
  concept: string
  nash_gap: NashGap | null
  marginal: Array<{
    period: number
    distribution: number[]
    modal_band: number
    expected_band: number
  }>
  escalation_probability: number
  sharp_departure_probability: number
  escalation_propensity: Record<string, number[]>
  paths: Array<{ probability: number; steps: ScenarioStep[] }>
  paths_enumerated: number
  retained_probability: number
  pricing: { measurements: number; cells: number; note?: string } | null
  opening_matrix: Record<string, OpeningMatrix>
  scenarios: Scenario[]
}

export interface DyadSolution {
  resolving?: boolean
  note?: string
  payload_version?: string
  region: string
  dyad_id: string
  dyad_name: string
  sides: [string, string]
  as_of: string
  horizon: number
  bands: number
  band_labels: string[]
  band_semantics?: string
  /** The band `sharp_departure_probability` counts ABOVE — the pair's own
   *  typical band. Without it a page cannot choose between "breaks above its
   *  norm" and "is still above it", which are different claims about the same
   *  number: US–Iran opens above the line with a fan drifting down. */
  typical_band?: number
  opening: {
    intensity_band: number
    intensity_label: string
    tone?: number | null
    tone_label?: string
    standing?: Standing | null
    posture?: Posture | null
    family?: Family | null
    latest_intensity: number
    scale: number
    active_quarters: number
    capability: { band: number; ratio?: number; source: string }
    beliefs: { a: number; b: number; quarters_observed?: number; source: string }
    tilt: {
      model: string
      method: string
      eta?: number
      scale?: number
      features?: Record<string, number>
      max_tilt?: number
      gate?: string
    } | null
  }
  payoffs: Record<string, number>
  /** THE GAME PLAYED: its family, its actions in order (concede / hold /
   *  press) and its private types — commit/affirm/withhold and
   *  reluctant/committed for an ally pair; de-escalate/hold/escalate and
   *  irresolute/resolute for the rest. */
  space?: { family: string; actions: string[]; types: string[]; quads: Record<string, string> }
  primary_solver: 'lp' | 'qre'
  concepts: Record<'lp' | 'qre', ConceptSolution>
  kernel: {
    cells: number
    measured: number
    fallback: number
    share_measured: number
    observations: number
  }
  explanation: string[]
  boundary_statement: string
  computed_at?: string
  persisted?: boolean
}

export interface RegionRanking {
  dyad_id: string
  dyad_name: string
  opening_band: number
  opening_label: string
  tone?: number | null
  tone_label?: string
  standing?: Standing | null
  posture?: Posture | null
  family?: Family | null
  escalation_probability: number
  /** P(this pair leaves its OWN usual band) — relative, not absolute. */
  sharp_departure_probability: number
  sharp_departure_probability_lp?: number | null
  escalation_probability_qre: number | null
  /** The measured, cross-pair comparable quantity the ranking sorts by. */
  coercive_events?: number | null
  coercive_share?: number | null
  expected_end_band: number | null
  top_scenario: {
    scenario_name: string
    kind: string
    kind_label?: string | null
    kind_sentence?: string | null
    likelihood: number
    course: string
    end_label: string
    presser: string | null
  } | null
  nash_gap_mean: number | null
  tilted: boolean
  capability_source: string
  beliefs_source: string
}

export interface RegionMap {
  /** The shape of a persisted solve; a stored row of another version is a
   *  cache miss, not a payload (core/games/scenarios.py PAYLOAD_VERSION). */
  payload_version?: string
  region: string
  as_of: string
  horizon: number
  bands: number
  band_labels: string[]
  /** See DyadSolution.typical_band. */
  typical_band?: number
  primary_solver: 'lp' | 'qre'
  solvers: string[]
  concepts: Record<string, string>
  payoffs: Record<string, number> | null
  kernel: {
    cells: number
    measured: number
    fallback: number
    share_measured: number
    observations: number
  }
  model: { name: string; hash: string } | null
  dyads_solved: number
  dyads_tilted: number
  dyads_cinc: number
  nash_gap: { mean: number | null; max: number | null }
  ranking: RegionRanking[]
  heat: Array<{
    dyad_id: string
    dyad_name: string
    opening_band: number
    expected_band: number[]
    modal_band: number[]
  }>
  region_fan: Array<{ period: number; distribution: number[]; expected_band: number | null }>
  scenarios_escalatory: Scenario[]
  scenarios_calming: Scenario[]
  scenarios_all: Scenario[]
  explanation: string[]
  boundary_statement: string
  computed_at?: string
  persisted?: boolean
  note?: string
  /** The map exists but is being re-solved for the current payload shape. A
   *  fast honest answer beats a request that waits out a ~130s solve. */
  resolving?: boolean
}


/** The calibration walk: the SAME near-term estimator re-run at every
 *  historical cutoff whose three-year horizon has closed, scored against what
 *  the archive then recorded. The scoreboard exists today because a call
 *  frozen this week cannot be scored until 2029. */
export interface CalibrationBand {
  band: [number, number]
  calls: number
  mean_forecast: number
  observed_rate: number
}

export interface CalibrationBlock {
  cutoffs?: number
  calls?: number
  span?: [string, string]
  brier?: number
  base_rate_brier?: number
  /** Against predicting the sample's own frequency: 0 is no better than the
   *  base rate, negative is worse. */
  skill?: number | null
  observed_rate?: number
  reliability?: CalibrationBand[]
}

export interface CalibrationWalk extends CalibrationBlock {
  /** The walk is warmed by a background job; a pending payload carries no
   *  numbers and the surface simply shows no scoreboard yet. */
  pending?: boolean
  region_pack: string
  horizon_years?: number
  recent?: CalibrationBlock & { years: number }
  by_cutoff?: Array<{ cutoff: string; brier: number; calls: number }>
  method?: string
  note?: string
}

// ── the markets story (core/reasoning/markets.py) ─────────────────────────────

/** One (kind, window) cell of a market's measured response: quantiles of the
 *  event study's abnormal returns, with the sample stated. `thin` means the
 *  cell sits under the bar and is shown as an anecdote, not a number. */
export interface ResponseCell {
  n: number
  median: number
  p25: number
  p75: number
  share_positive: number
  thin?: boolean
}

export interface MarketStoryMarket {
  ticker: string
  name: string
  market_type?: string | null
  inception_date?: string | null
  trading_calendar?: string | null
  measured: number
  windows: string[]
  /** kind → window → cell. Kinds: sharp_escalation · escalation ·
   *  de-escalation · stable — the event's own Head B coding. */
  response: Record<string, Record<string, ResponseCell>>
  headline: (ResponseCell & { kind: string }) | null
  first_mover_share: Record<string, number>
  biggest_moves: Array<{
    event_id: string
    name: string
    date: string
    kind: string
    pair: string | null
    abnormal_return: number
    first_mover: boolean
  }>
}

export interface MarketsStory {
  /** The pack KEY — what every `region=` parameter takes. */
  region: string
  /** The pack's CAPTION, which is what a surface shows. `packs/china` is keyed
   *  china and captioned ASIA; mena and eurasia gained captions on 2026-08-17
   *  because the markets h1 was rendering the key ("…when mena escalates"). */
  region_label?: string
  pending?: boolean
  note?: string
  as_of?: string | null
  computed_at?: string
  persisted?: boolean
  markets: MarketStoryMarket[]
  forward: {
    as_of?: string
    computed_at?: string
    courses: Array<{
      dyad_name: string
      kind: string
      kind_label?: string | null
      likelihood: number
      end_label: string
      market_implications: Array<{ market_id: string; market_name: string; median: number; n: number }>
    }>
    direction: Array<{
      market_id: string
      market_name: string
      expected_abnormal_return: number
      measurements: number
      courses: number
    }>
    note: string
  } | null
  duration: {
    events_with_a_curve_response?: number
    tenors_measured?: string[]
    usable_dyads?: number
    dyads: Array<{ dyad_id: string; dyad_name?: string; n: number; implied_persistence: number; p25: number; p75: number }>
    calibration?: string
    note?: string | null
    method?: string
  } | null
  sovereign_capital: {
    funds: Array<{
      actor_id: string
      name: string
      as_of: string
      value_usd: number
      previous_as_of?: string | null
      change_usd?: number | null
      quarters_reported: number
    }>
    note: string
  } | null
  coverage: {
    summary?: { dyads: number; dyads_measured: number; events: number; events_measured: number; share_measured: number }
    dyads?: Array<{ dyad_id: string; dyad_name?: string; events: number; measured: number }>
  } | null
  method: string
  explanation: string[]
}
