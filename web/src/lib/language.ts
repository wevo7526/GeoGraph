// Plain language for the surface. The machine keeps its exact names (dyad,
// escalation band, AFFECTED, Goldstein, retrodiction); the reader — a senior
// investor, not a quant — gets English. Everything user-facing routes through
// here so terms never drift page to page.

/** A relationship's current tension AGAINST ITS OWN HISTORY, as a phrase.
 *
 *  THE WORDS ARE RELATIVE BECAUSE THE MEASUREMENT IS. This reads intensity
 *  against the pair's own historical peak, which is the right comparison — what
 *  is routine for a rivalry is a rupture for a quiet pair — but it used to
 *  spend that relative number on absolute words: "severe", "elevated". The
 *  United States and Israel then carried a standfirst reading "formal allies
 *  since 1987 · Tension severe and easing", which is the same contradiction the
 *  2026-08-15 band work removed from the game's own vocabulary and left alive
 *  here. A phrase that says "near its own high" cannot contradict "allies",
 *  because it is no longer making a claim about hostility. */
export function tensionLevel(intensity: number, peak: number): string {
  if (peak <= 0 || intensity <= 0) return 'quiet'
  const share = intensity / peak
  if (share >= 0.75) return 'near its own high'
  if (share >= 0.5) return 'well above its usual level'
  if (share >= 0.25) return 'a little above its usual level'
  return 'around its usual level'
}

export type Trend = 'rising' | 'easing' | 'steady'

// Hostility per quarter from the SIGNED tone (mean Goldstein): positive means
// hostile (tone more negative). Direction reads THIS, never the intensity
// magnitude — intensity is a departure from a self-catching-up baseline, so a
// relationship pinned at peak hostility shows near-zero intensity and would
// read "easing" at its worst. Escalation is hostility RISING (tone falling).
function hostility(rows: Array<{ tone: number }>): number[] {
  return rows.map((r) => -(r.tone ?? 0))
}

function slope(values: number[]): number {
  const n = values.length
  if (n < 2) return 0
  const xbar = (n - 1) / 2
  const ybar = values.reduce((s, v) => s + v, 0) / n
  let num = 0
  let den = 0
  for (let i = 0; i < n; i++) {
    num += (i - xbar) * (values[i] - ybar)
    den += (i - xbar) ** 2
  }
  return den === 0 ? 0 : num / den
}

function stdev(values: number[]): number {
  const n = values.length
  if (n < 2) return 0
  const m = values.reduce((s, v) => s + v, 0) / n
  return Math.sqrt(values.reduce((s, v) => s + (v - m) ** 2, 0) / n)
}

/** Direction — the least-squares slope of HOSTILITY over the recent window,
 *  self-scaled by the dyad's own volatility. Hostility rising is escalation.
 *  Robust to a single spike's placement, unlike a two-quarter mean of the
 *  spiky departure-magnitude that made "escalating" read as "easing". */
export function tensionTrend(rows: Array<{ tone: number }>): Trend {
  const host = hostility(rows)
  if (host.length < 4) return 'steady'
  const window = host.slice(-Math.min(6, host.length))
  const sd = stdev(host) || 1
  const norm = slope(window) / sd
  if (norm > 0.08) return 'rising'
  if (norm < -0.08) return 'easing'
  return 'steady'
}

/** The one-sentence read at the top of a relationship. The level is already a
 *  phrase about the pair's own history, so this only adds the direction. */
export function tensionSentence(level: string, trend: Trend): string {
  const move =
    trend === 'rising' ? 'and rising' : trend === 'easing' ? 'and easing' : 'and steady'
  return `Friction is ${level}, ${move}`
}

/** A quarter code / ISO-ish date shown as a plain year. */
export function yearOf(date: string): string {
  return String(date).slice(0, 4)
}

/** A relationship's display name. The surface never shows a dyad id. */
export function relationshipName(name: string | undefined, fallback: string): string {
  if (name && name.trim()) return name
  // The fallback is usually a raw dyad id ('dyad:cow-2--cow-666') — machine
  // vocabulary. Read it as the pair it names rather than printing the id.
  const bare = fallback.startsWith('dyad:') ? fallback.slice('dyad:'.length) : fallback
  const [a, b] = bare.split('--')
  if (a && b) return `${a} – ${b}`
  return fallback
}

// ── the game, in plain words ────────────────────────────────────────────────
// The solver speaks in intensity bands (0…N), joint actions and quad classes.
// None of that reaches the reader: a band becomes a temperature word, a joint
// action becomes a sentence, and the match/thin flags become a confidence note.

/** A band as the word the BACKEND gives it. Bands are departures from the
 *  pair's OWN baseline (core/games/scenarios.py BAND_LABELS: "at baseline" …
 *  "extreme rupture"), and this file used to re-derive its own hostility
 *  ladder over them — "calm", "tense", "open conflict". That is the 2026-08-15
 *  contradiction the reader saw: the same pair could be a declared rivalry, a
 *  "mild departure" from its own baseline, and "calm" on the forecast badge,
 *  three vocabularies answering three different questions while looking like
 *  one. Pass the payload's `band_labels`; the fallback is the backend's own
 *  list, never a second ladder. */
/** MIRRORS core/games/scenarios.py BAND_LABELS — the payload's own list is
 *  preferred everywhere and this is only the fallback for a solution persisted
 *  before the words changed. The words are comparatives ("well above") rather
 *  than nouns ("notable departure") because they are read INSIDE sentences:
 *  "a notable departure turn" is not English, and "a turn well above the pair's
 *  norm" is the same fact in a form a reader can hold. */
const DEPARTURE_LABELS = [
  'at its norm',
  'a little above',
  'well above',
  'far above',
  'rupture',
  'extreme rupture',
]

export function bandLabel(band: number, bands: number, labels?: string[]): string {
  if (labels && labels.length) {
    return labels[Math.max(0, Math.min(Math.round(band), labels.length - 1))]
  }
  const share = bands > 1 ? band / (bands - 1) : 0
  const index = Math.round(share * (DEPARTURE_LABELS.length - 1))
  return DEPARTURE_LABELS[Math.max(0, Math.min(index, DEPARTURE_LABELS.length - 1))]
}


/** WHAT THE PAIR IS — the graph's declared, dated, sourced relation. This is
 *  the only thing on the surface entitled to characterise a relationship; the
 *  wire's mean tone is a statistic about how much a pair talks (it called two
 *  thirds of every region "friendly", the US and China included). */
export function standingLabel(
  standing?: { relations?: Array<{ relation_type: string; since?: string }> } | null,
): string | null {
  const rows = standing?.relations ?? []
  if (!rows.length) return null
  const words: Record<string, string> = {
    rivalry: 'declared rivalry',
    alliance: 'formal allies',
    proxy: 'patron and client',
    membership: 'shared bloc',
    trade: 'trade dependence',
  }
  // The backend orders these so the standing that CHARACTERISES the pair
  // leads (a non-aggression pact between rivals does not make them allies —
  // North and South Korea hold both). A second live relation is noted rather
  // than dropped.
  const first = rows[0]
  const word = words[first.relation_type] ?? first.relation_type.replace(/_/g, ' ')
  const since = (first.since ?? '').slice(0, 4)
  const also = rows.length > 1 ? ` +${rows.length - 1}` : ''
  return (since ? `${word} since ${since}` : word) + also
}

/** The same standing, compact enough for a table cell. The long form
 *  ("formal allies since 1949") overflowed a fixed-width chip and ran under
 *  the bar beside it; a row needs the fact, not the sentence. */
export function standingChip(
  standing?: { relations?: Array<{ relation_type: string; since?: string }> } | null,
): string | null {
  const rows = standing?.relations ?? []
  if (!rows.length) return null
  const short: Record<string, string> = {
    rivalry: 'rivalry',
    alliance: 'allies',
    proxy: 'patron',
    membership: 'bloc',
    trade: 'trade',
  }
  const first = rows[0]
  const word = short[first.relation_type] ?? first.relation_type.replace(/_/g, ' ')
  const year = (first.since ?? '').slice(2, 4)
  const also = rows.length > 1 ? `+${rows.length - 1}` : ''
  return [word, year ? `’${year}` : '', also].filter(Boolean).join(' ')
}

/** HOW THE RECORD READS LATELY — the coercive share of the pair's coded
 *  events, with its sample. A measurement, worded as one. */
export function postureNote(
  posture?: { label?: string; share?: number | null; events?: number; thin?: boolean } | null,
): string | null {
  if (!posture || !posture.label) return null
  if (posture.thin || posture.share == null) return posture.label
  return `${posture.label} · ${Math.round(posture.share * 100)}% of ${posture.events} coded interactions coercive`
}

/** A joint move (both sides' actions) as one plain sentence. The solver's
 *  actions are already words — de-escalate / hold / escalate — but "escalate /
 *  hold" reads as a machine tuple; this reads as English. */
export function jointAction(a: string, b: string): string {
  if (a === b) {
    if (a === 'escalate') return 'both sides escalate'
    if (a === 'de-escalate') return 'both sides step back'
    return 'both sides hold'
  }
  const set = new Set([a, b])
  if (set.has('escalate') && set.has('hold')) return 'one side escalates, the other holds'
  if (set.has('escalate') && set.has('de-escalate')) return 'one escalates as the other steps back'
  if (set.has('de-escalate') && set.has('hold')) return 'one steps back, the other holds'
  return `${a} / ${b}`
}


/** WHICH GAME THE PAIR PLAYS — ally / rival / adversary — and the words the
 *  solved number may wear. The solver has one game (crisis bargaining) and it
 *  is the right one only for adversaries; for an ally the "escalation
 *  probability" describes departures from the pair's own usual FRICTION and
 *  must not be read as odds of conflict. `label` is the chip; `headline` is
 *  the noun for the call ("friction", "hardening", "escalation"); `caveat` is
 *  the sentence a non-native family owes the reader, or null. */
export function familyRead(
  family?: { family: string; native?: boolean; headline?: string; bad_end?: string; note?: string; why?: string } | null,
): { label: string; headline: string; badEnd: string; caveat: string | null; why: string; tone: 'good' | 'bad' | 'ink' } | null {
  if (!family || !family.family) return null
  const label = family.family === 'ally' ? 'ally pair'
    : family.family === 'adversary' ? 'adversary pair' : 'rival pair'
  const tone: 'good' | 'bad' | 'ink' = family.family === 'ally' ? 'good'
    : family.family === 'adversary' ? 'bad' : 'ink'
  const headline = family.headline ?? (family.family === 'ally' ? 'friction'
    : family.family === 'adversary' ? 'escalation' : 'hardening')
  const badEnd = family.bad_end ?? (family.family === 'ally' ? 'a rift'
    : family.family === 'adversary' ? 'open conflict' : 'a coercive turn')
  const caveat = family.native === false
    ? `The solver runs the adversarial crisis-bargaining game for every pair, so these numbers describe departures from this pair's own usual level of ${headline} — not odds of ${badEnd}.`
    : null
  return { label, headline, badEnd, caveat, why: family.why ?? '', tone }
}
