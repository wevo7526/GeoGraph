/** The map: the archive's roster on a turning globe, and nothing else at all.
 *
 *  CUT BACK ON 2026-08-17, the same day it shipped. The first version put a
 *  rail of live departures beside the globe and a margin lane under it; the
 *  owner asked for the globe alone. That is the better instinct — the front
 *  door now makes ONE claim and the pages behind it make the rest, and the
 *  departures still have a home on Intel.
 *
 *  Pulses and roster dots click through: a recent departure opens the pair,
 *  otherwise Intel. They used to be ornamental.
 *
 *  WHITE, as the rest of the surface is. The dark plate this briefly wore was
 *  an override of a dated decision; it is withdrawn.
 */
import { useEffect, useState } from 'react'

import { getGlobe, lastFailureFor } from '../api'
import { dyadId } from '../lib/ids'
import type { GlobeBoard, GlobeNode } from '../types'
import { Empty } from '../ui'
import OrthoGlobe from './OrthoGlobe'

export default function SituationPlate({
  region,
  board: given,
  onOpen,
}: {
  region?: string
  board?: GlobeBoard | null
  onOpen?: (route: string) => void
}) {
  const [fetched, setFetched] = useState<GlobeBoard | null | undefined>(undefined)
  const controlled = given !== undefined
  const board = controlled ? given : fetched

  useEffect(() => {
    if (controlled) return
    let live = true
    setFetched(undefined)
    getGlobe(region, 12).then((b) => live && setFetched(b))
    return () => {
      live = false
    }
  }, [region, controlled])

  const openNode = (node: GlobeNode) => {
    if (!onOpen || !board) return
    const pulse = board.pulses.find((p) => p.source === node.id || p.target === node.id)
    if (pulse) {
      onOpen(
        `/relationships?dyad=${encodeURIComponent(dyadId(pulse.source, pulse.target))}` +
          `&region=${encodeURIComponent(pulse.pack || region || '')}`,
      )
      return
    }
    const pack = node.packs[0] || region
    onOpen(pack ? `/intel?region=${encodeURIComponent(pack)}` : '/intel')
  }

  if (board === undefined) {
    return <div className="plate plate--waiting" aria-busy="true" />
  }
  if (board === null) {
    const failure = lastFailureFor('/api/globe')
    return (
      <div className="plate plate--waiting">
        <Empty>{failure?.detail ?? 'The board is not answering.'}</Empty>
      </div>
    )
  }

  return (
    <section className="plate" aria-label="the archive's roster, mapped">
      <OrthoGlobe board={board} onNodeClick={onOpen ? openNode : undefined} />
    </section>
  )
}
