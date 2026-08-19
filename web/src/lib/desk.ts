/** Where the desk is sitting, and how a citation becomes a route.
 *
 *  Intel and the corner control share one agent. The surface name is a desk
 *  the reader is on, not a measurement. Pair ids are routed; they are never
 *  written into a sentence here. */
import { isPairId } from './ids'

export const DEFAULT_QUESTION = 'What is the situation?'

export type DeskTurn = { role: 'user' | 'assistant'; content: string }

export function surfaceFromRoute(route: string): string {
  const path = route.split('?')[0]
  if (path.startsWith('/intel') || path.startsWith('/situation')) return 'intel'
  if (path.startsWith('/wire')) return 'wire'
  if (path.startsWith('/markets') || path.startsWith('/trading')) return 'markets'
  if (path.startsWith('/games')) return 'games'
  if (path.startsWith('/relationship') || path.startsWith('/reasoning')) {
    return 'relationships'
  }
  if (path.startsWith('/case')) return 'cases'
  if (path.startsWith('/explore')) return 'explorer'
  return 'intel'
}

export function focusFromRoute(route: string): Record<string, string> {
  const [path, query] = route.split('?')
  const params = new URLSearchParams(query ?? '')
  const focus: Record<string, string> = {}
  const pair = params.get('dyad')
  if (pair) focus.dyad_id = pair
  const event = params.get('event')
  if (event) focus.event_id = event
  if (path.startsWith('/case/') && path.length > '/case/'.length) {
    const slug = path.slice('/case/'.length)
    if (slug && slug !== 'dynamic') focus.slug = slug
  }
  return focus
}

export function citeRoute(id: string, region: string): string | null {
  const lens = `region=${encodeURIComponent(region)}`
  if (isPairId(id)) {
    return `/relationships?dyad=${encodeURIComponent(id)}&${lens}`
  }
  if (id.startsWith('event:')) {
    return `/case/dynamic?event=${encodeURIComponent(id)}&${lens}`
  }
  if (id.startsWith('market:')) return '/markets'
  return null
}
