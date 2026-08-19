/** Canonical pair id — same rule as `core.classifier.escalation.dyad_id`.
 *  A rivalry is one relationship whichever side acted last. */
export function dyadId(actorA: string, actorB: string): string {
  const bare = (id: string) => {
    const cut = id.indexOf(':')
    return cut === -1 ? id : id.slice(cut + 1)
  }
  const [a, b] = [bare(actorA), bare(actorB)].sort()
  return `dyad:${a}--${b}`
}

export function isPairId(id: string): boolean {
  return id.startsWith('dyad:')
}

export function actorsFromPairId(id: string): [string, string] | null {
  if (!isPairId(id)) return null
  const rest = id.slice('dyad:'.length)
  const cut = rest.indexOf('--')
  if (cut < 1 || cut + 2 >= rest.length) return null
  return [`actor:${rest.slice(0, cut)}`, `actor:${rest.slice(cut + 2)}`]
}
