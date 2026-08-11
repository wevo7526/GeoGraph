// Shapes mirror the API responses. The API is the contract; keep these in
// step with core/api rather than inventing richer local shapes.

export interface Health {
  status: string
  graph: 'open' | 'unavailable'
  graphError: string | null
  disabled: Record<string, string>
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
