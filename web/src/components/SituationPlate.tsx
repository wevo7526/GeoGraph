/** The plate: a plotting board set into the broadsheet.
 *
 *  BOUNDED, NOT FULL-BLEED, and that is the deliberate half of the override.
 *  The surface is white by a dated decision the stylesheet cites twice and
 *  tells the next reader not to "fix" back. A dark field running edge to edge
 *  reads as a theme reversal and invites exactly that. A dark plate set INSIDE
 *  the same 2px black frame the explorer's instrument already wears reads as
 *  an instrument on paper — which is what it is, and which is why the white
 *  citation survives intact around it.
 *
 *  THE RAIL IS NOT A CAPTION. Everything the globe draws, it states in words:
 *  the pairs, the distance from their own baseline, which way. The canvas is
 *  aria-hidden because the rail is the accessible version of the same data,
 *  not a description of a picture.
 *
 *  THE MARGIN LANE IS THE HONEST PART. Nineteen of the roster's seventy-five
 *  actors have no coordinate — every proxy client, every sovereign fund, OPEC,
 *  the GCC, and three historical states deliberately given no iso3. A globe
 *  that quietly showed 56 and called it the roster would assert coverage it
 *  does not have. They are drawn beside it, with their patron where one is
 *  declared.
 */
import { useEffect, useState } from 'react'

import { getGlobe, lastFailureFor } from '../api'
import { count, plateStrapline, pulseRead } from '../lib/story'
import type { GlobeBoard } from '../types'
import { Empty } from '../ui'
import OrthoGlobe from './OrthoGlobe'

export default function SituationPlate({
  region,
  onNavigate,
}: {
  region?: string
  onNavigate?: (route: string) => void
}) {
  const [board, setBoard] = useState<GlobeBoard | null | undefined>(undefined)
  const [marked, setMarked] = useState<string | null>(null)

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
    <section className="plate" aria-label="the board">
      <div className="plate-grid">
        <OrthoGlobe board={board} highlightId={marked} />

        <div className="plate-rail">
          <div className="plate-rail-head">
            <span className="kicker">what just moved</span>
            {board.as_of && <span className="mono text-xs">to {board.as_of}</span>}
          </div>

          {board.pulses.length ? (
            <ol className="plate-pulses">
              {board.pulses.map((pulse) => (
                <li key={pulse.event_id}>
                  <button
                    type="button"
                    className="plate-pulse"
                    onMouseEnter={() => setMarked(pulse.source)}
                    onMouseLeave={() => setMarked(null)}
                    onFocus={() => setMarked(pulse.source)}
                    onBlur={() => setMarked(null)}
                    onClick={() => onNavigate?.('/wire')}
                  >
                    <span
                      className="plate-tick"
                      data-direction={pulse.direction ?? 'stable'}
                      aria-hidden="true"
                    />
                    <span className="plate-pulse-text">{pulseRead(pulse)}</span>
                    <span className="plate-leader" aria-hidden="true" />
                    <span className="plate-figure">
                      {pulse.points_from_baseline.toFixed(1)}
                    </span>
                  </button>
                </li>
              ))}
            </ol>
          ) : (
            <p className="plate-empty">
              Nothing in the archive&rsquo;s latest window left its pair&rsquo;s usual
              band. That is a reading, not a gap.
            </p>
          )}

          {board.unplaced.length > 0 && (
            <div className="plate-margin">
              <span className="kicker">not placeable</span>
              <ul>
                {board.unplaced.slice(0, 8).map((actor) => (
                  <li key={actor.id}>
                    <span>{actor.name}</span>
                    <span className="plate-leader" aria-hidden="true" />
                    <span className="plate-margin-note">
                      {actor.patron_name ? `client of ${actor.patron_name}` : actor.actor_type}
                    </span>
                  </li>
                ))}
              </ul>
              {board.unplaced.length > 8 && (
                <p className="plate-margin-more">
                  and {count(board.unplaced.length - 8)} more
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      <p className="plate-strapline">
        {plateStrapline(board)} A departure is at least {board.departure_points}{' '}
        Goldstein points from that pair&rsquo;s own running baseline — never an
        absolute scale, because a score that is an ordinary week for a rivalry is
        a rupture for an alliance.
      </p>
    </section>
  )
}
