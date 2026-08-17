/** The map: the archive's roster on a turning globe, and nothing else.
 *
 *  CUT BACK ON 2026-08-17, the same day it shipped. The first version put a
 *  rail of live departures beside the globe and a margin lane under it; the
 *  owner asked for the globe alone. That is the better instinct — the front
 *  door now makes ONE claim and the pages behind it make the rest, and the
 *  departures still have a home on the Wire.
 *
 *  What survives the cut is the honesty, because it costs one line: the
 *  strapline still states the roster it drew AND the nineteen actors it cannot
 *  place. A globe showing 56 of 75 and calling it the archive would assert
 *  coverage the data does not have, whether or not there is room for a list.
 *
 *  WHITE, as the rest of the surface is. The dark plate this briefly wore was
 *  an override of a dated decision; it is withdrawn.
 */
import { useEffect, useState } from 'react'

import { getGlobe, lastFailureFor } from '../api'
import { plateStrapline } from '../lib/story'
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
      <p className="plate-strapline">{plateStrapline(board)}</p>
    </section>
  )
}
