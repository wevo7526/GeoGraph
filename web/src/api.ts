import type {
  CaseStudy,
  CaseStudyIndexEntry,
  Dyad,
  Effect,
  EventDetail,
  EventList,
  Flow,
  ForecastDetail,
  ForecastSummary,
  GraphActor,
  Health,
  PaperBook,
  Pack,
  Relation,
  Segmentation,
  Stats,
  Trajectory,
} from './types'

// Null on any failure, deliberately: the explorer renders its built-in sample
// when the API is absent (vite dev with no backend), and every caller shows
// what is live versus placeholder rather than crashing.
async function get<T>(path: string): Promise<T | null> {
  try {
    const res = await fetch(path)
    if (!res.ok) return null
    return (await res.json()) as T
  } catch {
    return null
  }
}

export const getHealth = () => get<Health>('/api/health')
export const getPacks = () => get<{ packs: string[] }>('/api/packs')
export const getStats = () => get<Stats>('/api/stats')
export const getRegimes = () => get<Segmentation>('/api/regimes')
export const getPack = (name: string) => get<Pack>(`/api/packs/${name}`)

export const getEvents = (params: { start?: string; end?: string; limit?: number } = {}) => {
  const query = new URLSearchParams()
  if (params.start) query.set('start', params.start)
  if (params.end) query.set('end', params.end)
  if (params.limit) query.set('limit', String(params.limit))
  const suffix = query.toString() ? `?${query}` : ''
  return get<EventList>(`/api/events${suffix}`)
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

export const getForecasts = () => get<{ rows: ForecastSummary[] }>('/api/forecasts')

export const getForecast = (nodeId: string) =>
  get<ForecastDetail>(`/api/forecasts/${encodeURIComponent(nodeId)}`)

export const getPaperBook = (nodeId: string) =>
  get<PaperBook>(`/api/forecasts/${encodeURIComponent(nodeId)}/paper`)

export const getCaseStudies = () =>
  get<{ rows: CaseStudyIndexEntry[] }>('/api/case-studies')

export const getCaseStudy = (slug: string) =>
  get<CaseStudy>(`/api/case-studies/${encodeURIComponent(slug)}`)
