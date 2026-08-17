/** The map: the archive's roster on a turning globe, and nothing else at all.
 *
 *  CUT BACK ON 2026-08-17, the same day it shipped. The first version put a
 *  rail of live departures beside the globe and a margin lane under it; the
 *  owner asked for the globe alone. That is the better instinct — the front
 *  door now makes ONE claim and the pages behind it make the rest, and the
 *  departures still have a home on the Wire.
 *
 *  THE STRAPLINE WENT TOO (second cut, same day). It named the nineteen
 *  actors the globe cannot place — blocs, funds and armed movements with no
 *  coordinate — and losing it means the front door no longer discloses that
 *  gap. That is a deliberate trade the owner made for a page with no subtext
 *  on it, not an oversight: the globe now shows the states it can place and
 *  claims nothing about the rest, and `/api/globe` still serves `unplaced` for
 *  any surface that wants to say so.
 *
 *  WHITE, as the rest of the surface is. The dark plate this briefly wore was
 *  an override of a dated decision; it is withdrawn.
 */
import { useEffect, useState } from 'react'

import { getGlobe, lastFailureFor } from '../api'
import type { GlobeBoard } from '../types'
import { Empty } from '../ui'
import OrthoGlobe from './OrthoGlobe'

export default function SituationPlate({ region }: { region?: string }) {
  const [board, setBoard] = useState<GlobeBoard | null | undefined>(undefined)

  useEffect(() => {
    let live = true
    setBoard(undefined)
    getGlobe(region, 12).then((b) => live && setBoard(b))
    return () => {
      live = false
    }
  }, [region])

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
      <OrthoGlobe board={board} />
    </section>
  )
}
