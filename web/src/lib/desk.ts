/** Where the desk is sitting, and how a citation becomes a route.
 *
 *  Intel and the corner control share one agent. The surface name is a desk
 *  the reader is on, not a measurement. Pair ids are routed; they are never
 *  written into a sentence here. */
import type { SituationBriefing } from '../types'
import { isPairId } from './ids'

export const DEFAULT_QUESTION = 'What is the situation?'

export type DeskTurn = { role: 'user' | 'assistant'; content: string }

export type DeskInline =
  | { kind: 'text'; value: string }
  | { kind: 'strong'; value: string }
  | { kind: 'cite'; id: string }

export type DeskBlock =
  | { kind: 'p'; children: DeskInline[] }
  | { kind: 'ul'; items: DeskInline[][] }

export function surfaceFromRoute(route: string): string {
  const path = route.split('?')[0]
  if (path.startsWith('/intel') || path.startsWith('/situation') || path.startsWith('/wire')) {
    return 'intel'
  }
  if (path.startsWith('/markets') || path.startsWith('/trading')) return 'markets'
  if (path.startsWith('/games')) return 'games'
  if (path.startsWith('/relationship') || path.startsWith('/reasoning')) {
    return 'relationships'
  }
  if (path.startsWith('/case')) return 'cases'
  if (path.startsWith('/network')) return 'network'
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

export function citeRoute(
  id: string,
  region: string,
  briefing?: SituationBriefing | null,
): string | null {
  const lens = `region=${encodeURIComponent(region)}`
  if (isPairId(id)) {
    return `/relationships?dyad=${encodeURIComponent(id)}&${lens}`
  }
  if (id.startsWith('event:')) {
    // Ordinary wire events are not narrated case studies. Send the reader to
    // the pair's record when we have one; otherwise the label stands alone.
    const dep = briefing?.wire?.departures?.find((row) => row.node_id === id)
    if (dep?.dyad_id) {
      return `/relationships?dyad=${encodeURIComponent(dep.dyad_id)}&${lens}`
    }
    return null
  }
  if (id.startsWith('market:')) return '/markets'
  return null
}

export function citeLabel(id: string, briefing?: SituationBriefing | null): string {
  if (isPairId(id)) {
    const ranked = briefing?.region_games?.ranking ?? []
    const named = ranked.find((row) => row.dyad_id === id)?.dyad_name
    if (named) return named
    const lead = briefing?.region_games?.lead
    if (lead?.dyad_id === id && lead.dyad_name) return lead.dyad_name
    const dep = briefing?.wire?.departures?.find((row) => row.dyad_id === id)
    if (dep?.initiator_name && dep.target_name) {
      return `${dep.initiator_name}–${dep.target_name}`
    }
    return 'the pair'
  }
  if (id.startsWith('event:')) {
    const dep = briefing?.wire?.departures?.find((row) => row.node_id === id)
    if (dep?.initiator_name && dep.target_name) {
      return `${dep.initiator_name}–${dep.target_name}`
    }
    return 'this event'
  }
  if (id.startsWith('market:')) {
    const ticker = id.slice('market:'.length)
    const name = briefing?.markets?.headlines?.find((row) => row.ticker === ticker)?.name
    return name || 'this market'
  }
  return 'open →'
}

export function parseDeskProse(raw: string): DeskBlock[] {
  const text = raw.replace(/^#{1,6}\s+/gm, '').trim()
  if (!text) return []
  const out: DeskBlock[] = []
  for (const chunk of text.split(/\n{2,}/)) {
    const lines = chunk.split('\n').map((line) => line.trim()).filter(Boolean)
    if (!lines.length) continue
    if (lines.every((line) => /^[-*]\s+/.test(line))) {
      out.push({
        kind: 'ul',
        items: lines.map((line) => parseInlines(line.replace(/^[-*]\s+/, ''))),
      })
      continue
    }
    out.push({ kind: 'p', children: parseInlines(lines.join(' ')) })
  }
  return out
}

function parseInlines(text: string): DeskInline[] {
  const out: DeskInline[] = []
  const token = /\[([^\]]{3,80})\]|\*\*([^*]+)\*\*/g
  let cursor = 0
  let match: RegExpExecArray | null
  while ((match = token.exec(text)) !== null) {
    if (match.index > cursor) {
      out.push({ kind: 'text', value: text.slice(cursor, match.index) })
    }
    if (match[1]) out.push({ kind: 'cite', id: match[1] })
    else if (match[2]) out.push({ kind: 'strong', value: match[2] })
    cursor = match.index + match[0].length
  }
  if (cursor < text.length) out.push({ kind: 'text', value: text.slice(cursor) })
  return out
}
