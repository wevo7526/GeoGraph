import type {
  GlobeBoard,
  WireFeed,
  WireLiveFeed,
  CalibrationWalk,
  Assessment,
  BacktestLedger,
  CaseStudy,
  CaseStudyIndexEntry,
  Dyad,
  DyadSeries,
  DyadTimeline,
  Effect,
  EventDetail,
  EventList,
  Flow,
  ForecastDetail,
  ForecastSummary,
  GameExplore,
  GraphActor,
  Health,
  PaperBook,
  Pack,
  PanelDyad,
  Precedent,
  Relation,
  Segmentation,
  Stats,
  Trajectory,
  RegionMap,
  DyadSolution,
  ForwardView,
  EventImpact,
  ImpactCoverage,
  JobsStatus,
  MarketsStory,
  TradeableEdge,
} from './types'

/** A recorded API failure — kept so the surface can tell BROKEN from EMPTY.
 *  Every helper still returns null on failure (callers render their empty
 *  states), but the failure itself is no longer swallowed: pages subscribe to
 *  render "the API did not answer" instead of "the archive holds nothing",
 *  which were indistinguishable on 2026-08-14. */
export interface ApiFailure {
  path: string
  status: number | null
  detail: string
  at: number
}

const failures = new Map<string, ApiFailure>()
const failureListeners = new Set<() => void>()

function notifyFailureListeners() {
  for (const listener of failureListeners) listener()
}

/** Subscribe to the failure map; returns an unsubscribe. */
export function onApiFailures(listener: () => void): () => void {
  failureListeners.add(listener)
  return () => failureListeners.delete(listener)
}

/** Failures worth a page-level banner: server errors and unreachability.
 *  4xx answers are the API talking (a 404 series, a 409 sparse kernel) and
 *  belong to the caller that asked, via lastFailureFor. */
export function bannerFailures(): ApiFailure[] {
  return [...failures.values()]
    .filter((f) => f.status === null || f.status >= 500)
    .sort((a, b) => b.at - a.at)
}

/** The most recent recorded failure whose path starts with the prefix.
 *  Pass `exact` to match one endpoint precisely — a prefix like
 *  '/api/panel/dyads' otherwise also matches every '/api/panel/dyads/<id>/series'
 *  failure, so one dyad's transient series error would wedge a whole page
 *  behind its collection-level error state. */
export function lastFailureFor(
  prefix: string,
  opts: { exact?: boolean } = {},
): ApiFailure | null {
  let hit: ApiFailure | null = null
  for (const failure of failures.values()) {
    const bare = failure.path.split('?')[0]
    const matches = opts.exact ? bare === prefix : failure.path.startsWith(prefix)
    if (matches && (!hit || failure.at > hit.at)) hit = failure
  }
  return hit
}

// Null on any failure, deliberately: the explorer renders its built-in sample
// when the API is absent (vite dev with no backend), and every caller shows
// what is live versus placeholder rather than crashing. The failure is
// RECORDED before the null is returned — see ApiFailure above.
async function get<T>(path: string): Promise<T | null> {
  const bare = path.split('?')[0]
  try {
    const res = await fetch(path)
    if (!res.ok) {
      const body = await res.json().catch(() => null)
      failures.set(bare, {
        path,
        status: res.status,
        detail:
          (body && typeof body.detail === 'string' && body.detail) ||
          `the API answered ${res.status}`,
        at: Date.now(),
      })
      notifyFailureListeners()
      return null
    }
    if (failures.delete(bare)) notifyFailureListeners()
    return (await res.json()) as T
  } catch {
    failures.set(bare, {
      path,
      status: null,
      detail: 'the API is unreachable',
      at: Date.now(),
    })
    notifyFailureListeners()
    return null
  }
}

export const getHealth = () => get<Health>('/api/health')
// `packs` are the KEYS every region= parameter takes; `labels` is what a
// reader is shown. Never send a label back to the API.
//
// Memoised: the pack list is immutable for the session, and Sidebar, regions.ts
// and Landing each asked for it independently (2-3 fetches per page). One
// shared promise dedups them; a failed fetch is NOT cached, so it still
// retries on the next call.
type PacksPayload = { packs: string[]; labels?: Record<string, string> }
let _packsPromise: Promise<PacksPayload | null> | null = null
export const getPacks = (): Promise<PacksPayload | null> => {
  if (!_packsPromise) {
    _packsPromise = get<PacksPayload>('/api/packs').then((r) => {
      if (r === null) _packsPromise = null
      return r
    })
  }
  return _packsPromise
}
export const getStats = () => get<Stats>('/api/stats')
export const getRegimes = () => get<Segmentation>('/api/regimes')
export const getPack = (name: string) => get<Pack>(`/api/packs/${name}`)

export const getEvents = (
  params: {
    start?: string
    end?: string
    limit?: number
    pack?: string
    order?: 'asc' | 'desc'
  } = {},
) => {
  const query = new URLSearchParams()
  if (params.start) query.set('start', params.start)
  if (params.end) query.set('end', params.end)
  if (params.limit) query.set('limit', String(params.limit))
  if (params.pack) query.set('pack', params.pack)
  if (params.order) query.set('order', params.order)
  const suffix = query.toString() ? `?${query}` : ''
  return get<EventList>(`/api/events${suffix}`)
}

// The wire: newest coded events with the fields for a one-line read. The
// SENTENCE is composed in lib/story.ts — the backend names fields, never prose.
export const getWire = (region?: string, limit = 60) => {
  const query = new URLSearchParams()
  if (region) query.set('region', region)
  query.set('limit', String(limit))
  return get<WireFeed>(`/api/wire?${query}`)
}

export const getWireLive = (region: string, limit = 30) => {
  const query = new URLSearchParams({ region, limit: String(limit) })
  return get<WireLiveFeed>(`/api/wire/live?${query}`)
}

// The globe board. NOT memoised: the pulses are the live layer, and a shared
// promise would freeze the front door on whatever the first visitor saw.
export const getGlobe = (region?: string, pulses = 12) => {
  const query = new URLSearchParams()
  if (region) query.set('region', region)
  query.set('pulses', String(pulses))
  return get<GlobeBoard>(`/api/globe?${query}`)
}

export const getCoverage = (pack?: string) =>
  get<{ years: Record<string, number>; total: number }>(
    pack ? `/api/events/coverage?pack=${encodeURIComponent(pack)}` : '/api/events/coverage',
  )

export const getEvent = (nodeId: string) =>
  get<EventDetail>(`/api/events/${encodeURIComponent(nodeId)}`)

export const getEventEffects = (nodeId: string) =>
  get<{ event: string; measured: number; rows: Effect[] }>(
    `/api/events/${encodeURIComponent(nodeId)}/effects`,
  )

export const getTrajectory = (dyadId: string) =>
  get<Trajectory>(`/api/escalation/${encodeURIComponent(dyadId)}`)

export const getDyads = () => get<{ rows: Dyad[] }>('/api/dyads')

export const getFlows = () => get<{ rows: Flow[] }>('/api/flows')

export const getRelations = (params: { start?: string; end?: string } = {}) => {
  const query = new URLSearchParams()
  if (params.start) query.set('start', params.start)
  if (params.end) query.set('end', params.end)
  const suffix = query.toString() ? `?${query}` : ''
  return get<{ rows: Relation[]; truncated: boolean }>(`/api/relations${suffix}`)
}

export const getActors = (params: { start?: string; end?: string } = {}) => {
  const query = new URLSearchParams()
  if (params.start) query.set('start', params.start)
  if (params.end) query.set('end', params.end)
  const suffix = query.toString() ? `?${query}` : ''
  return get<{ rows: GraphActor[] }>(`/api/actors${suffix}`)
}

// The quarterly panel the forecaster is fitted on — a different view of the
// same dyads than /api/dyads, which serves standing baselines.
export const getPanelDyads = (region?: string) =>
  get<{ rows: PanelDyad[]; total: number }>(
    `/api/panel/dyads${region ? `?region=${encodeURIComponent(region)}` : ''}`,
  )

export const getDyadSeries = (dyadId: string, region?: string) =>
  get<DyadSeries>(
    `/api/panel/dyads/${encodeURIComponent(dyadId)}/series` +
      (region ? `?region=${encodeURIComponent(region)}` : ''),
  )

export const getPrecedent = (dyadId: string, region?: string) => {
  const query = new URLSearchParams({ dyad: dyadId })
  if (region) query.set('region', region)
  return get<Precedent>(`/api/precedent?${query}`)
}

// A relationship's market-moving events, most recent first — the timeline feed.
export const getDyadTimeline = (dyadId: string) =>
  get<DyadTimeline>(`/api/impact/dyad/${encodeURIComponent(dyadId)}`)

// Counterfactuals: re-solved on request, never frozen and never scored. The
// payload says so itself; the UI must not present one as a forecast.
export const exploreGame = (
  region: string,
  dyad: string,
  overrides: Record<string, number> = {},
) => {
  const query = new URLSearchParams({ region, dyad })
  for (const [key, value] of Object.entries(overrides)) {
    if (Number.isFinite(value)) query.set(key, String(value))
  }
  return get<GameExplore>(`/api/games/explore?${query}`)
}

// The walk-forward paper backtest ledger — the region's frozen calls marked to
// market on $1M notional, quarter by quarter. Region-level, not per-dyad.
export const getBacktest = (region: string) =>
  get<BacktestLedger>(`/api/trading/backtest?region=${encodeURIComponent(region)}`)

// The scoreboard: the near-term estimator re-run at every closed-horizon
// cutoff and Brier-scored. Read `recent` before the headline — the whole-walk
// number is dominated by a sparse deep past.
export const getCalibration = (region: string) =>
  get<CalibrationWalk>(`/api/forecasts/calibration?region=${encodeURIComponent(region)}`)

export const getForecasts = (region?: string) =>
  get<{ rows: ForecastSummary[] }>(
    region ? `/api/forecasts?region=${encodeURIComponent(region)}` : '/api/forecasts',
  )

export const getForecast = (nodeId: string) =>
  get<ForecastDetail>(`/api/forecasts/${encodeURIComponent(nodeId)}`)

export const getPaperBook = (nodeId: string) =>
  get<PaperBook>(`/api/forecasts/${encodeURIComponent(nodeId)}/paper`)

/** The assess call keeps the error DETAIL: a dark agent answers with a 503
 *  whose message is the page's honest content, not a null to swallow. */
export async function postAssess(
  question: string,
  region: string,
): Promise<{ ok: boolean; detail?: string; result?: Assessment }> {
  try {
    const res = await fetch('/api/reasoning/assess', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question, region }),
    })
    const body = await res.json().catch(() => null)
    if (!res.ok) {
      return { ok: false, detail: body?.detail ?? `the API answered ${res.status}` }
    }
    return { ok: true, result: body as Assessment }
  } catch {
    return { ok: false, detail: 'the API is unreachable' }
  }
}

// The solved-game surface: persisted-first region map and dyad solution.
export const getRegionMap = (region: string) =>
  get<RegionMap>(`/api/games/region?region=${encodeURIComponent(region)}`)

export const getDyadSolution = (region: string, dyad: string) =>
  get<DyadSolution>(
    `/api/games/dyad?region=${encodeURIComponent(region)}&dyad=${encodeURIComponent(dyad)}`,
  )

// The markets story: the transmission map, the biggest moves, where the games
// point, the curve's read on duration, sovereign capital and coverage — built by
// the markets job and served from Postgres (pending until its first pass).
export const getMarketsStory = (region: string) =>
  get<MarketsStory>(`/api/markets/story?region=${encodeURIComponent(region)}`)

export const getTradeableEdge = (leader = '^TASI.SR', follower = '^GSPC') => {
  const query = new URLSearchParams({ leader, follower })
  return get<TradeableEdge>(`/api/trading/edge?${query}`)
}

export const getJobs = () => get<JobsStatus>('/api/jobs')

// The standing book: the latest frozen near-term call marked at the latest close.
export const getForward = (region: string) =>
  get<ForwardView>(`/api/trading/forward?region=${encodeURIComponent(region)}`)

// One event's measured vs expected vs surprise — the north-star object.
export const getEventImpact = (eventId: string) =>
  get<EventImpact>(`/api/impact/${encodeURIComponent(eventId)}`)

// The market-movement trace registered per dyad for a pack.
export const getImpactCoverage = (region: string) =>
  get<ImpactCoverage>(`/api/impact/coverage?region=${encodeURIComponent(region)}`)

// A case study composed on request for any dyad or event.
export const getDynamicCaseStudy = (params: {
  dyad?: string
  event?: string
  region?: string
}) => {
  const query = new URLSearchParams()
  if (params.dyad) query.set('dyad', params.dyad)
  if (params.event) query.set('event', params.event)
  if (params.region) query.set('region', params.region)
  return get<CaseStudy>(`/api/case-studies/dynamic?${query}`)
}

export const getCaseStudies = () =>
  get<{ rows: CaseStudyIndexEntry[] }>('/api/case-studies')

export const getCaseStudy = (slug: string) =>
  get<CaseStudy>(`/api/case-studies/${encodeURIComponent(slug)}`)
