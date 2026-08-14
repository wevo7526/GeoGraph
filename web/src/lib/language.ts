// Plain language for the surface. The machine keeps its exact names (dyad,
// escalation band, AFFECTED, Goldstein, retrodiction); the reader — a senior
// investor, not a quant — gets English. Everything user-facing routes through
// here so terms never drift page to page.

/** A relationship's current tension, as a word, from its intensity vs its own
 *  historical peak (relative, because what is routine for a rivalry is a
 *  rupture for a quiet pair). */
export function tensionLevel(intensity: number, peak: number): string {
  if (peak <= 0 || intensity <= 0) return 'quiet'
  const share = intensity / peak
  if (share >= 0.75) return 'severe'
  if (share >= 0.5) return 'elevated'
  if (share >= 0.25) return 'moderate'
  return 'low'
}

export type Trend = 'rising' | 'easing' | 'steady'

/** Direction over the last stretch of quarters, size-normalised so a big quiet
 *  pair and a small tense one are judged on their own scale. */
export function tensionTrend(rows: Array<{ intensity: number }>): Trend {
  if (rows.length < 4) return 'steady'
  const recent = rows.slice(-2).reduce((s, r) => s + r.intensity, 0) / 2
  const prior = rows.slice(-4, -2).reduce((s, r) => s + r.intensity, 0) / 2
  const scale = Math.max(1e-9, Math.abs(prior))
  const change = (recent - prior) / scale
  if (change > 0.15) return 'rising'
  if (change < -0.15) return 'easing'
  return 'steady'
}

/** The one-sentence read at the top of a relationship. */
export function tensionSentence(level: string, trend: Trend): string {
  const move =
    trend === 'rising' ? 'and rising' : trend === 'easing' ? 'and easing' : 'and steady'
  return `Tension ${level} ${move}`
}

/** A market move as a signed percent. AFFECTED abnormal returns are fractions
 *  (0.041 = +4.1%). */
export function marketMove(car: number): string {
  const pct = car * 100
  return `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`
}

/** A quarter code / ISO-ish date shown as a plain year. */
export function yearOf(date: string): string {
  return String(date).slice(0, 4)
}

/** The four forecast modes, named for a human. Kept here so the surface never
 *  prints 'near_term' / 'long_horizon' / 'sequence'. */
export function outlookLabel(mode: string): string {
  switch (mode) {
    case 'near_term':
      return 'Near-term read'
    case 'long_horizon':
      return 'Long-range pressure'
    case 'model':
      return 'Model read'
    case 'sequence':
      return 'Most likely path'
    default:
      return 'Outlook'
  }
}

/** A relationship's display name. The surface never shows a dyad id. */
export function relationshipName(name: string | undefined, fallback: string): string {
  return name && name.trim() ? name : fallback
}
