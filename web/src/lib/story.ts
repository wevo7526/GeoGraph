// THE LEDE IS WRITTEN HERE, NOT IN PYTHON (2026-08-17).
//
// Every working page used to render `explanation[0]` — the backend's audit
// paragraph — as its standfirst. That paragraph exists to satisfy build-spec
// §17 (every number in the prose is a field in the payload) and it does its job
// well; the mistake was reading the rule as "every field must be in the prose".
// The region map's first sentence ran to a hundred words with nested
// parentheses and named the solver twice, the pair page's opened on CINC and a
// Bayes filter, and prose defects shipped to production without a frontend
// change ever being made.
//
// So the split is now: the backend's `explanation` is the AUDIT — kept whole,
// moved under "How this was solved" — and the page's first sentence is composed
// here from named fields. §17 still holds, and holds more tightly: every clause
// below is reachable from one field, and where a field is missing the clause is
// dropped rather than filled.
//
// Nothing in this file fetches, formats markup, or knows about React.

import type {
  ConceptSolution,
  DyadSolution,
  EventImpact,
  MarketsStory,
  RegionMap,
  Scenario,
  Standing,
  Posture,
  Family,
  RegionRanking,
  StrategySignal,
  TransmissionSkill,
  PricingTrust,
  WireFeed,
  WireItem,
  WireLiveFeed,
  NetworkSnapshot,
} from '../types'

// ── words ───────────────────────────────────────────────────────────────────

/** The band the game's headline probability counts from: `sharp_departure_
 *  probability` is P(the pair ends above ITS OWN typical band), and the typical
 *  band is index 2 of six (core/games/scenarios.py TYPICAL_BAND, derived from
 *  the intensity edges). Read off the payload when it carries the constant, so
 *  the two cannot drift; the literal is the fallback for solutions persisted
 *  before the field existed. */
const TYPICAL_BAND = 2

export function typicalBand(payload: { typical_band?: number } | null | undefined): number {
  const declared = payload?.typical_band
  return typeof declared === 'number' ? declared : TYPICAL_BAND
}

/** The two payload types that carry the constant, so callers stop casting. */
export type BandCarrier = { typical_band?: number }

/** A whole number as a word, for the small counts prose reads badly as digits
 *  ("Three in ten" beats "3 in 10" in a sentence; 30% beats both in a table). */
const WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten']

/** A probability as plain odds — the register a reader thinks in. Only used
 *  where a sentence carries ONE probability; a comparison keeps its percents. */
export function odds(p: number): string {
  const tenths = Math.round(p * 10)
  if (tenths <= 0) return 'almost no chance'
  if (tenths >= 10) return 'near-certain'
  return `${WORDS[tenths]} in ten`
}

export function pctWord(p: number | null | undefined, digits = 0): string {
  return Number.isFinite(p as number) ? `${((p as number) * 100).toFixed(digits)}%` : '—'
}

export function signedPct(v: number, digits = 1): string {
  return `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(digits)}%`
}

/** First letter up. Every composed sentence goes through it, because the
 *  fields it is built from are lower-case fragments by design. */
export function capitalise(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text
}

/** A count with a thousands separator, or an em dash. */
export function count(n: number | null | undefined): string {
  return typeof n === 'number' ? n.toLocaleString('en-US') : '—'
}

// ── what a pair IS ──────────────────────────────────────────────────────────

/** The declared, dated, sourced relation, as a phrase a sentence can carry.
 *  This is the ONLY field entitled to characterise a relationship — the wire's
 *  mean tone ranks pairs by how much they talk, and called two thirds of every
 *  region "friendly". */
const STANDING_WORDS: Record<string, string> = {
  rivalry: 'rivals',
  alliance: 'formal allies',
  proxy: 'patron and client',
  membership: 'partners in the same bloc',
  trade: 'trading partners',
}

export function standingPhrase(standing?: Standing | null): string | null {
  const first = standing?.relations?.[0]
  if (!first) return null
  const word = STANDING_WORDS[first.relation_type] ?? first.relation_type.replace(/_/g, ' ')
  const since = (first.since ?? '').slice(0, 4)
  return since ? `${word} since ${since}` : word
}

/** How the record READS lately: the coercive share, in a clause. The sample
 *  travels with it, because a share over thirty events is a different claim
 *  from a share over seven thousand. */
export function postureClause(posture?: Posture | null): string | null {
  if (!posture?.label) return null
  if (posture.thin || posture.share == null) return 'too little recent coverage to read'
  return `${Math.round(posture.share * 100)}% of their ${count(posture.events)} recent exchanges were coercive`
}

/** Standing, posture, intensity — three questions, one standfirst.
 *
 *  WHAT THE PAIR IS is the curated RELATES_TO web. HOW ITS RECORD READS LATELY
 *  is the coercive share, with the sample. WHERE IT SITS is a departure from
 *  its own baseline. A fourth ladder (mean Goldstein, `tensionSentence`) used
 *  to characterise the pair and contradicted the first. */
export function relationshipStandfirst(opts: {
  standing?: string | null
  posture?: string | null
  intensity?: string | null
  span?: [string, string] | null
}): string | null {
  const bits = [opts.standing, opts.posture, opts.intensity].filter(Boolean) as string[]
  if (!bits.length) return null
  const years = opts.span ? ` Over ${opts.span[0].slice(0, 4)}–${opts.span[1].slice(0, 4)}.` : ''
  const body = bits.map((b, i) => (i === 0 ? capitalise(b) : b)).join('. ')
  return `${body.replace(/\.$/, '')}.${years}`
}

/** The word this family's game may use for its headline number — an alliance's
 *  "escalation probability" is a statement about friction between partners. */
export function headlineWord(family?: Family | null): string {
  return family?.headline ?? 'escalation'
}

// ── the shape of a course ───────────────────────────────────────────────────

/** A course kind as a sentence. MIRRORS core/games/family.py KIND_WORDS, whose
 *  second element now travels as `kind_sentence` and is always preferred. This
 *  table is the fallback for a payload persisted before 2026-08-17, when only
 *  the label left the backend. Keyed by family because an
 *  adversary's "brinkmanship" is an ally's "withhold, then recommit". */
const KIND_SENTENCE: Record<string, Record<string, string>> = {
  adversary: {
    mutual_escalation: 'both sides escalate',
    brinkmanship: 'both sides escalate, then at least one steps back',
    one_sided_pressure: 'one side presses while the other holds',
    probe_and_retreat: 'one side presses, then steps back',
    step_down: 'at least one side de-escalates and neither presses',
    drift_up: 'both hold, and the pressure rises anyway',
    drift_down: 'both hold and the pressure subsides',
    holding_pattern: 'both sides hold where they are',
  },
  rival: {
    mutual_escalation: 'both sides press',
    brinkmanship: 'both sides press, then at least one eases',
    one_sided_pressure: 'one side presses while the other holds',
    probe_and_retreat: 'one side presses, then eases',
    step_down: 'at least one side eases and neither presses',
    drift_up: 'both hold, and the friction rises anyway',
    drift_down: 'both hold and the friction subsides',
    holding_pattern: 'both sides hold where they are',
  },
  ally: {
    mutual_escalation: 'both partners withhold — the rift course',
    brinkmanship: 'both partners withhold, then at least one recommits',
    one_sided_pressure: 'one partner withholds while the other carries the alliance',
    probe_and_retreat: 'one partner withholds, then recommits',
    step_down: 'at least one partner commits and neither withholds',
    drift_up: 'both affirm, and friction rises anyway',
    drift_down: 'both affirm and friction subsides',
    holding_pattern: 'both partners affirm; friction stays where it is',
  },
}

type KindCarrier = {
  kind: string
  kind_label?: string | null
  kind_sentence?: string | null
}

export function kindName(sc: KindCarrier | null | undefined): string {
  if (!sc) return ''
  return sc.kind_label ?? sc.kind.replace(/_/g, ' ')
}

export function kindSentence(
  sc: KindCarrier | null | undefined,
  family?: Family | null,
): string | null {
  if (!sc) return null
  if (sc.kind_sentence) return sc.kind_sentence
  const table = KIND_SENTENCE[family?.family ?? 'adversary'] ?? KIND_SENTENCE.adversary
  return table[sc.kind] ?? null
}

/** Just the sentence, for a surface that already shows the name. Callers used
 *  to split `courseInWords` on ": ", which yields undefined for a kind with no
 *  sentence and fell through to the raw course string — the exact machine
 *  vocabulary the rewrite removed. */
export function courseSentence(
  sc: KindCarrier | null | undefined,
  family?: Family | null,
): string | null {
  return kindSentence(sc, family)
}

/** "brinkmanship: both sides escalate, then at least one steps back" */
export function courseInWords(
  sc: KindCarrier | null | undefined,
  family?: Family | null,
): string | null {
  if (!sc) return null
  const name = kindName(sc)
  const sentence = kindSentence(sc, family)
  return sentence ? `${name}: ${sentence}` : name
}

// ── the region map's lede ───────────────────────────────────────────────────

export type Lede = {
  /** The claim, as one sentence a reader could repeat. */
  headline: string
  /** What stands behind it — sample, standing, the second fact. */
  support: string | null
  /** Where the archive stops. Never inside the claim. */
  asOf: string | null
}

/** "United States–Iran carries the most coercion in the region — 1,213 coercive
 *  acts in the last year, rivals since 1980. The likeliest course for the year
 *  ahead is brinkmanship: both sides escalate, then at least one steps back." */
export function regionLede(map: RegionMap, label: string): Lede | null {
  const lead: RegionRanking | undefined = map.ranking?.[0]
  if (!lead) return null
  const acts = lead.coercive_events ?? 0
  const standing = standingPhrase(lead.standing)
  const course = courseInWords(lead.top_scenario, lead.family)
  const quarters = map.horizon ?? 4
  const span = quarters === 4 ? 'the year ahead' : `the next ${quarters} quarters`

  const headline = acts
    ? `${lead.dyad_name} carries more coercion than any other pair in ${label}`
    : `${lead.dyad_name} leads ${label}'s solved games`
  const facts: string[] = []
  if (acts) facts.push(`${count(acts)} coercive acts over the last four quarters`)
  if (standing) facts.push(standing)
  const support = [
    facts.length ? facts.join(', ') : null,
    course ? `The likeliest course for ${span} is ${course}.` : null,
  ]
    .filter(Boolean)
    .join('. ')
    .replace(/\.\./g, '.')

  return { headline, support: support || null, asOf: map.as_of ?? null }
}

// ── one pair's call ─────────────────────────────────────────────────────────

export type Call = {
  /** The finding. */
  headline: string
  /** The odds, said once, in the family's own words. */
  odds: string
  /** The likeliest course, in words. */
  course: string | null
  /** What the archive measured after courses like it — never a model price. */
  markets: string | null
  /** True when the pair is already above its own typical band, which changes
   *  the verb: it is not "sees a departure", it is "is still above". */
  opensAbove: boolean
}

/** The pair page's call, composed from the concept the payload leads with.
 *
 *  THE VERB HAS TO MATCH WHAT THE NUMBER COUNTS. `sharp_departure_probability`
 *  is P(the pair ends the horizon ABOVE its own typical band). US–Iran opens at
 *  a sharp departure and the fan drifts DOWN (2.41 → 2.23), so "25% that they
 *  see a sharper-than-usual departure" described a break that the game is in
 *  fact expecting to ease. A pair already above the line is asked a different
 *  question — whether it is still there — and gets a different sentence. */
export function dyadCall(sol: DyadSolution, concept: ConceptSolution): Call {
  const family = sol.opening.family
  const word = headlineWord(family)
  const p = concept.sharp_departure_probability
  const typical = typicalBand(sol)
  const opensAbove = sol.opening.intensity_band > typical
  const marginal = concept.marginal ?? []
  const drift =
    marginal.length > 1
      ? marginal[marginal.length - 1].expected_band - marginal[0].expected_band
      : 0
  const quarters = sol.horizon ?? 4
  const span = quarters === 4 ? 'the year' : `${quarters} quarters`
  const [a, b] = sol.sides

  const direction = drift > 0.1 ? 'and the game expects it to climb'
    : drift < -0.1 ? 'and the game expects it to ease'
    : 'and the game expects it to hold there'

  const headline = opensAbove
    ? `${a} and ${b} open ${span} above their own usual level of ${word}, ${direction}.`
    : drift > 0.1
      ? `${a} and ${b} open at their usual level of ${word}, and the game expects it to build.`
      : `${a} and ${b} open at their usual level of ${word}, and the game expects it to stay there.`

  const horizonWords = quarters === 4 ? 'a year' : `${quarters} quarters`
  const oddsSentence = capitalise(
    opensAbove
      ? `${odds(p)} that they are still above it ${horizonWords} out.`
      : `${odds(p)} that ${word} breaks above that usual level within ${horizonWords}.`,
  )

  const top: Scenario | undefined = concept.scenarios?.[0]
  const course = top
    ? `The likeliest course, at ${pctWord(top.likelihood)} of the game's outcomes, is ${courseInWords(top, family)}.`
    : null

  const priced = (top?.market_implications ?? []).slice(0, 3)
  const markets = priced.length
    ? `When courses like it have played out, ${priced
        .map((m) => `${m.market_name} moved ${signedPct(m.median)} (${count(m.n)} events)`)
        .join(', ')}.`
    : null

  return { headline, odds: oddsSentence, course, markets, opensAbove }
}

// ── the markets page's lede ─────────────────────────────────────────────────

/** THE SPREAD IS THE STORY, and only the payload can say whether it is.
 *
 *  MENA's medians sit within half a percent of zero while the middle half of
 *  outcomes runs from −1.9% to +3.9%, so a headline built from the median alone
 *  ("US 2-Year Treasury yield moves +0.47% when mena escalates sharply") states
 *  a finding the sample does not support — 52% of those events were positive.
 *  The composer therefore MEASURES the ratio before choosing the sentence: a
 *  region whose medians are large relative to their spread gets the direct
 *  claim, and one whose medians are noise gets the honest one. */
export function marketsLede(
  story: MarketsStory,
  label: string,
): Lede | null {
  const headlined = story.markets.filter((m) => m.headline)
  if (!headlined.length) return null
  const lead = headlined[0]
  const cell = lead.headline!
  const windowSpread = (m: (typeof headlined)[number]) => {
    const h = m.headline!
    return Math.abs(h.p75 - h.p25)
  }
  // "Barely moves" is a claim about the median against its own dispersion, not
  // against zero: a median a tenth the width of the interquartile range is
  // noise whatever its sign.
  const noisy = Math.abs(cell.median) < windowSpread(lead) / 6
  const sinceYear = (lead.inception_date ?? '').slice(0, 4)

  // THE CAPTION IS A NOUN PHRASE AND MAY CARRY ITS OWN ARTICLE ("the Middle
  // East"), so it goes AFTER the noun. Slotted in front, it produced "A sharp
  // the Middle East escalation barely moves the median price" (2026-08-17).
  const headline = noisy
    ? `A sharp escalation in ${label} barely moves the median price. The story is in the spread.`
    : `${lead.name} moves ${signedPct(cell.median, 1)} when ${label} escalates sharply.`

  const parts = headlined.slice(0, 2).map((m) => {
    const h = m.headline!
    return `${m.name}, ${signedPct(h.median, 1)} typically, ${signedPct(h.p25, 1)} to ${signedPct(h.p75, 1)} across the middle half`
  })
  const sample = `Across ${count(cell.n)} sharp escalations${sinceYear ? ` since ${sinceYear}` : ''}: ${parts.join('; ')}.`

  const cov = story.coverage?.summary
  const coverage =
    cov && cov.events
      ? ` The engine has measured ${pctWord(cov.share_measured, 0)} of this region's coded events.`
      : ''

  return { headline, support: sample + coverage, asOf: story.as_of ?? null }
}

/** What happened, in a reader's words — never the CAMEO string GDELT ships.
 *
 *  `item.name` is machine vocabulary ("Engage in negotiation: Israel → Egypt")
 *  and the globe already refuses to render it. The act comes from the CAMEO
 *  ROOT when the code is present, so quad 4 is not always "used force toward"
 *  (19 fight vs 15 exhibit force vs 17 coerce). Quad class is the fallback
 *  for a row that never carried a code. */
const QUAD_ACT: Record<string, string> = {
  verbal_cooperation: 'spoke with',
  material_cooperation: 'cooperated with',
  verbal_conflict: 'spoke against',
  material_conflict: 'used force toward',
}

/** Mirrors `core/wire/headline.py` ROOT_ACT. Keep the two tables in step. */
const CAMEO_ACT: Record<string, string> = {
  '01': 'issued a statement about',
  '02': 'appealed to',
  '03': 'expressed intent to cooperate with',
  '04': 'consulted',
  '05': 'cooperated diplomatically with',
  '06': 'cooperated materially with',
  '07': 'provided aid to',
  '08': 'yielded to',
  '09': 'investigated',
  '10': 'demanded of',
  '11': 'disapproved of',
  '12': 'rejected',
  '13': 'threatened',
  '14': 'protested against',
  '15': 'exhibited force toward',
  '16': 'reduced relations with',
  '17': 'coerced',
  '18': 'assaulted',
  '19': 'fought',
  '20': 'used mass violence against',
}

const FORCE_ROOTS = new Set(['15', '18', '19', '20'])

export type WireHeadlineFields = {
  initiator_name?: string | null
  target_name?: string | null
  name?: string | null
  headline?: string | null
  quad_class?: string | null
  cameo_code?: string | null
  action_geo?: string | null
  action_geo_name?: string | null
  initiator_iso3?: string | null
  target_iso3?: string | null
  third_country_force?: boolean
  allied_presence?: boolean
  unconfirmed_force?: boolean
  pair_fight?: boolean
  coercion?: boolean | null
  combat?: boolean | null
  dyad_id?: string | null
}

export function cameoRoot(code?: string | null): string | null {
  const digits = (code ?? '').replace(/\D/g, '')
  return digits.length >= 2 ? digits.slice(0, 2) : null
}

/** Fight/use-of-force coded on a country that is neither side.
 *  Display only — does not retarget the stored pair. A geo off the pack is
 *  still a third country; we just cannot name it. */
export function thirdCountryForce(item: WireHeadlineFields): boolean {
  if (item.third_country_force === true) return true
  if (item.third_country_force === false) return false
  const geo = item.action_geo?.trim().toUpperCase() || ''
  const left = item.initiator_iso3?.trim().toUpperCase() || ''
  const right = item.target_iso3?.trim().toUpperCase() || ''
  if (!geo || !left || !right || geo === left || geo === right) return false
  return FORCE_ROOTS.has(cameoRoot(item.cameo_code) ?? '')
}

/** Defence-pact partners coded in a force event — presence, not a war. */
export function alliedPresence(item: WireHeadlineFields): boolean {
  if (item.allied_presence === true) return true
  if (item.allied_presence === false) return false
  if (!FORCE_ROOTS.has(cameoRoot(item.cameo_code) ?? '')) return false
  const geo = item.action_geo?.trim().toUpperCase() || ''
  const left = item.initiator_iso3?.trim().toUpperCase() || ''
  const right = item.target_iso3?.trim().toUpperCase() || ''
  const onHome = Boolean(geo && (geo === left || geo === right))
  return item.coercion === false && onHome
}

/** Home-soil or unlocated force between a pair not marked combat. */
export function unconfirmedForce(item: WireHeadlineFields): boolean {
  if (item.unconfirmed_force === true) return true
  if (item.unconfirmed_force === false) return false
  // Absent combat means the API did not resolve a pack — do not soften.
  if (item.combat !== false) return false
  if (!FORCE_ROOTS.has(cameoRoot(item.cameo_code) ?? '')) return false
  if (thirdCountryForce(item) || alliedPresence(item)) return false
  const geo = item.action_geo?.trim().toUpperCase() || ''
  const left = item.initiator_iso3?.trim().toUpperCase() || ''
  const right = item.target_iso3?.trim().toUpperCase() || ''
  if (!left || !right) return false
  return !geo || geo === left || geo === right
}

/** Whether the surface may offer this row as an A–B fight relationship. */
export function offersPairNav(item: WireHeadlineFields): boolean {
  if (!item.dyad_id) return false
  if (item.pair_fight === false) return false
  return !thirdCountryForce(item) && !alliedPresence(item) && !unconfirmedForce(item)
}

export function wireHeadline(item: WireHeadlineFields): string {
  const left = item.initiator_name?.trim() || null
  const right = item.target_name?.trim() || null
  if (thirdCountryForce(item) && left) {
    const place = item.action_geo_name?.trim() || item.action_geo?.trim() || 'a third country'
    return `${left} used force in ${place}`
  }
  const notPair =
    item.pair_fight === false
    || alliedPresence(item)
    || unconfirmedForce(item)
  if (notPair && left && right) {
    const root = cameoRoot(item.cameo_code)
    if (root && FORCE_ROOTS.has(root)) {
      const place = item.action_geo_name?.trim() || null
      if (place && thirdCountryForce(item)) return `${left} used force in ${place}`
      if (place) {
        return `${left} and ${right} — a force coding in ${place}, not a fight between them`
      }
      return `${left} and ${right} — a force coding, not a fight between them`
    }
    if (item.coercion === false) {
      const act = (root && CAMEO_ACT[root]) || QUAD_ACT[item.quad_class ?? ''] || 'interacted with'
      return `${left} ${act} ${right} — not counted as interstate coercion`
    }
  }
  const root = cameoRoot(item.cameo_code)
  const act = (root && CAMEO_ACT[root]) || QUAD_ACT[item.quad_class ?? ''] || 'interacted with'
  if (left && right) return `${left} ${act} ${right}`
  if (left) return `${left} ${act} an unnamed counterpart`
  return 'A coded event between unnamed actors'
}

function namesFromCodedTitle(name?: string | null): { left: string | null; right: string | null } {
  const text = name ?? ''
  if (!text.includes(':') || !text.includes('→')) return { left: null, right: null }
  const rest = text.slice(text.indexOf(':') + 1)
  const parts = rest.split('→').map((s) => s.trim())
  if (parts.length < 2 || !parts[0] || !parts[1]) return { left: null, right: null }
  return { left: parts[0], right: parts[1] }
}

/** Reader sentence for any event payload — never the CAMEO `name` title. */
export function eventHeadline(
  item: WireHeadlineFields & { name?: string | null; headline?: string | null },
): string {
  if (item.headline?.trim()) return item.headline.trim()
  const parsed = namesFromCodedTitle(item.name)
  return wireHeadline({
    ...item,
    initiator_name: item.initiator_name || parsed.left,
    target_name: item.target_name || parsed.right,
  })
}

/** The live wire's implied kind, as a noun phrase — not the raw token. */
export function wireKindWord(kind: string | null | undefined): string {
  switch (kind) {
    case 'sharp_escalation':
      return 'a sharp escalation'
    case 'escalation':
      return 'an escalation'
    case 'de-escalation':
      return 'a step down'
    case 'stable':
      return 'routine traffic'
    default:
      return 'coded traffic'
  }
}

/** The wire's one-line read of a single event.
 *
 *  THE READ IS RELATIVE, and that is the whole point. `points_from_baseline`
 *  is the distance from THAT PAIR's own running baseline, so the same
 *  Goldstein score reads as a quiet week for a rivalry and a rupture for an
 *  alliance. A sentence that said "hostile" off the raw score would call the
 *  first a crisis and miss the second — which is exactly the mistake the
 *  relationship page made before the bands became comparatives.
 *
 *  Composed HERE from named fields, never rendered from a backend string
 *  (test_surface_language.py refuses the latter).
 */
export function wireRead(item: WireItem): string {
  const place = item.action_geo_name?.trim() || item.action_geo?.trim() || null
  if (thirdCountryForce(item) && place) {
    const off = item.points_from_baseline
    const who = item.initiator_name?.trim() || 'A named actor'
    if (off === null) {
      return `${who} acted in ${place}; GDELT attached another roster flag, which is not a fight between those two states.`
    }
    return `${who} acted in ${place} — ${off.toFixed(1)} points from the coded pair's usual level, which is who GDELT attached, not a fight between them.`
  }
  if (
    alliedPresence(item)
    || unconfirmedForce(item)
    || (item.pair_fight === false && FORCE_ROOTS.has(cameoRoot(item.cameo_code) ?? ''))
  ) {
    const pair =
      item.initiator_name && item.target_name
        ? `${item.initiator_name} and ${item.target_name}`
        : 'the coded pair'
    const where = place ? ` in ${place}` : ''
    return `A force coding involving ${pair}${where} — not a fight between those two states.`
  }
  if (item.coercion === false && item.quad_class === 'material_conflict') {
    const pair =
      item.initiator_name && item.target_name
        ? `${item.initiator_name} and ${item.target_name}`
        : 'the coded pair'
    return `Coded between ${pair}, but not counted as interstate coercion (thin sources, personal enforcement, or a non-state actor type).`
  }
  const pair =
    item.initiator_name && item.target_name
      ? `${item.initiator_name} and ${item.target_name}`
      : null
  const off = item.points_from_baseline

  if (off === null) {
    return pair
      ? `Coded between ${pair}; too little history on this pair to say how far it sits from their usual.`
      : 'Coded, with no reading yet of how far it sits from the pair\u2019s usual.'
  }

  const theirs = pair ? 'their' : 'the pair\u2019s'

  if (!item.departure) {
    return pair
      ? `Routine for ${pair}: ${off.toFixed(1)} points from ${theirs} usual level.`
      : `Routine traffic: ${off.toFixed(1)} points from ${theirs} usual level.`
  }

  // WHICH WAY IT DEPARTED COMES FROM `escalation_direction`, NEVER FROM THE
  // GOLDSTEIN SIGN. `points_from_baseline` is |score \u2212 baseline|, an absolute
  // distance that says nothing about direction, and the raw sign is the act's
  // absolute tone rather than its tone RELATIVE to this pair. The archive
  // supplies the canonical trap on one day: "Disapprove" scores \u22122.0 both
  // times, but against the United Kingdom and France (baseline +1.7) it is an
  // escalation, and against Russia and Ukraine (baseline \u22129.1) it is 7.1
  // points CALMER than their war. Reading the sign would have called both
  // coercive and got the second exactly backwards.
  const way =
    item.escalation_direction === 'escalating'
      ? `${off.toFixed(1)} points more hostile than ${theirs} usual`
      : item.escalation_direction === 'deescalating'
        ? `${off.toFixed(1)} points calmer than ${theirs} usual`
        : `${off.toFixed(1)} points from ${theirs} usual level`
  return pair ? `A real departure for ${pair} \u2014 ${way}.` : `A real departure \u2014 ${way}.`
}

/** The wire's standfirst: what this batch of events amounts to. */
export function wireLede(feed: WireFeed, label: string): Lede | null {
  if (!feed.rows.length) return null
  const departures = feed.rows.filter((r) => r.departure).length
  const newest = feed.as_of ?? feed.rows[0].event_time
  const headline =
    departures === 0
      ? `Nothing in ${label} has left its usual band.`
      : departures === 1
        ? `One pair in ${label} has stepped outside its usual band.`
        : `${count(departures)} pairs in ${label} have stepped outside their usual band.`
  const cap = feed.truncated
    ? ' The newest sixty are shown; the archive holds more.'
    : ''
  const support =
    `The newest ${count(feed.rows.length)} coded events, to ${newest}. ` +
    `A departure means at least ${feed.departure_points} Goldstein points from that ` +
    `pair\u2019s own running baseline \u2014 not from an absolute scale, because a score ` +
    `that is routine for a rivalry is a rupture for an alliance.` +
    cap
  return { headline, support, asOf: newest }
}

/** The intel plate's small figure: departures by pair, else live kinds.
 *
 *  Counts of named fields already on the page — never a new measurement. */
export function intelTrafficFigure(args: {
  wire: WireFeed | null
  live: WireLiveFeed | null
}): {
  title: string
  support: string
  bars: Array<{ key: string; label: string; value: number; sub?: string }>
} | null {
  const byPair = new Map<string, number>()
  for (const row of args.wire?.rows ?? []) {
    if (!row.departure || thirdCountryForce(row)) continue
    const label =
      row.initiator_name && row.target_name
        ? `${row.initiator_name}–${row.target_name}`
        : 'unnamed pair'
    byPair.set(label, (byPair.get(label) ?? 0) + 1)
  }
  if (byPair.size) {
    const bars = [...byPair.entries()]
      .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
      .slice(0, 8)
      .map(([label, value]) => ({ key: label, label, value }))
    const n = [...byPair.values()].reduce((sum, v) => sum + v, 0)
    return {
      title: `${count(n)} departure${n === 1 ? '' : 's'} by pair`,
      support:
        'Coded events at least the pair’s own departure bar from their running baseline. A third-country force coding is omitted here — it is not offered as a fight between the named flags.',
      bars,
    }
  }
  const byKind = new Map<string, number>()
  for (const row of args.live?.rows ?? []) {
    const label = wireKindWord(row.implied_kind)
    byKind.set(label, (byKind.get(label) ?? 0) + 1)
  }
  if (!byKind.size) return null
  const bars = [...byKind.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .map(([label, value]) => ({ key: label, label, value }))
  const n = [...byKind.values()].reduce((sum, v) => sum + v, 0)
  return {
    title: `${count(n)} live coding${n === 1 ? '' : 's'} in this export`,
    support: 'Implied kind against each pair’s usual level in the frozen archive, counted — not a forecast.',
    bars,
  }
}

// ── the situation briefing ──────────────────────────────────────────────────

const MARKET_TYPE_WORD: Record<string, string> = {
  equity_index: 'regional equities',
  commodity: 'commodities',
  sovereign_yield: 'sovereign yields',
}

/** The working home's first sentence: what broke, then what the games and the
 *  packed markets add — three named ledes, not a fourth invented claim. */
export function situationLede(args: {
  label: string
  wire: WireFeed | null
  map: RegionMap | null
  story: MarketsStory | null
}): Lede | null {
  const wire = args.wire ? wireLede(args.wire, args.label) : null
  const games =
    args.map && !args.map.resolving ? regionLede(args.map, args.label) : null
  const markets =
    args.story && !args.story.pending ? marketsLede(args.story, args.label) : null
  if (!wire && !games && !markets) return null

  const headline = wire?.headline ?? games?.headline ?? markets?.headline ?? `The situation in ${args.label}`
  const next = [games?.headline, markets?.headline].filter((part) => part && part !== headline)
  const support = next.length ? next.join(' ') : (wire?.support ?? games?.support ?? markets?.support ?? null)
  const asOf = wire?.asOf ?? games?.asOf ?? markets?.asOf ?? null
  return { headline, support, asOf }
}

/** How the transmission map has scored, in a reader's words.
 *
 *  Leave-one-out of stored cells — never a new measurement. MAE stays off the
 *  hero line; the sentence is sign-hit, whether it beat a naive last-cell
 *  guess, and coverage. */
export function skillSentence(skill: TransmissionSkill | null | undefined, label: string): string | null {
  const kind = skill?.kind
  if (!kind || kind.n == null || kind.n < 1) return null
  const hit = kind.sign_hit
  const covered = kind.coverage
  const guess = kind.beats_naive
    ? 'and beat a naive last-cell guess'
    : 'and did not beat a naive last-cell guess'
  const sign =
    hit == null
      ? 'the map has a sample but not a sign-hit rate yet'
      : `the map got the sign of the next move right ${pctWord(hit, 0)} of the time`
  const cover =
    covered == null ? '' : `, on ${pctWord(covered, 0)} of ${label}'s stored reactions`
  const types = Object.entries(skill?.by_market_type ?? {})
    .filter(([, row]) => row.n && row.n > 0 && row.sign_hit != null)
    .slice(0, 4)
    .map(([key, row]) => `${MARKET_TYPE_WORD[key] ?? key.replaceAll('_', ' ')} ${pctWord(row.sign_hit, 0)}`)
  const strata = types.length ? ` By kind of market: ${types.join('; ')}.` : ''
  const control =
    skill?.gspc_control?.n && skill.gspc_control.sign_hit != null
      ? ` The S&P 500 control sat at ${pctWord(skill.gspc_control.sign_hit, 0)}.`
      : ''
  return `On a leave-one-out walk of stored reactions (matching by kind of event, excluding overlapping windows), ${sign} ${guess}${cover}.${strata}${control}`
}

/** Whether the games' priced courses rest on a matcher with skill. */
export function pricingTrustSentence(trust: PricingTrust | null | undefined): string | null {
  if (!trust) return null
  if (trust.bottleneck === 'sequencing') {
    return 'Past events of the same class predicted the next move; the game’s predicted class did not. The bottleneck is sequencing, not measurement — the prices on a known class stand, the path that picked the class does not.'
  }
  if (trust.trusted) {
    return 'Priced courses rest on a matcher that beat a naive last-cell guess.'
  }
  return 'Priced courses are shown and marked untrusted: oracle-class cells did not beat a naive last-cell guess.'
}

/** Strategy gate as vocabulary — never as a blotter. */
export function strategyWord(sig: StrategySignal | null | undefined): string | null {
  if (!sig) return null
  if (sig.thin || sig.n < 1) {
    return 'Too few measurements to take a view — stand aside.'
  }
  if (sig.action === 'stand_aside') {
    return 'Stand aside: the typical move does not clear a cost hurdle.'
  }
  if (sig.action === 'watch') {
    return 'Watch: the typical move sits near the cost hurdle, not past it.'
  }
  const way =
    sig.direction === 'long' ? 'higher' : sig.direction === 'short' ? 'lower' : 'unchanged'
  return `The typical move is ${way}, and it clears a cost hurdle. That is a reading of the archive, not advice.`
}

/** One line of measured vs expected vs surprise. Empty is "not measured", never a zero. */
export function impactLine(impact: EventImpact): string {
  const rows = impact.markets.filter((m) => m.measured || m.expected).slice(0, 3)
  if (!rows.length) return 'Not measured.'
  return rows
    .map((m) => {
      if (!m.measured) {
        return `${m.market_name} not measured`
      }
      if (!m.expected) {
        return `${m.market_name} moved ${signedPct(m.measured.car)} — no comparable base rate`
      }
      const surprise =
        m.surprise == null ? '' : `; surprise ${signedPct(m.surprise)}`
      return `${m.market_name} moved ${signedPct(m.measured.car)}; typically ${signedPct(m.expected.median_car)}${surprise}`
    })
    .join(' · ')
}

/** Who sits between others in this lens's latest computed window. */
export function networkLede(snap: NetworkSnapshot, label: string): Lede | null {
  const lead = snap.brokers?.[0]
  if (!lead || !snap.window_end) return null
  const through = snap.window_end.slice(0, 4)
  return {
    headline: `${lead.name} sits between the others in ${label}'s web as of ${through}.`,
    support: snap.holes?.[0]
      ? `${snap.holes[0].name} has the most room to broker — the least constrained position in this window.`
      : `Ranked from persisted structural measures, not a live recompute.`,
    asOf: snap.window_end,
  }
}
