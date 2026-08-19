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
