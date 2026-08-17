import { useEffect, useState } from 'react'
import { getHealth } from '../api'
import SituationPlate from './SituationPlate'
import type { Health } from '../types'

/** The front door, cut to the bone (2026-08-15): masthead, headline, three
 *  live region tiles — one figure each, the frozen near-term call, and the
 *  solved game's lead pair as a bar — the wire running underneath as a
 *  ticker, one line of archive figures, one door. No paragraphs. Every
 *  number is served and has a page behind it; a tile is the way in. */

/** THE FRONT DOOR IS THE MAP (2026-08-17, cut twice the same day).
 *
 *  Four things, centred: the hero line, the globe, one door, and a hairline
 *  saying whether the graph is open. Everything else is gone — the masthead
 *  kicker, the three region tiles, the scrolling ticker, the archive-figures
 *  line and the globe's own strapline.
 *
 *  Each of those was defensible on its own and the pile was not. A reader who
 *  has not been told what this is does not need the region with the most
 *  coercive pair, the count of measured effects and a caption about
 *  unplaceable actors, all before the first click. The globe is the argument;
 *  the pages behind the door carry the evidence, and every number that used to
 *  sit here is still served by the endpoint that fed it.
 *
 *  ONE HONEST LOSS, recorded because it is not free: the strapline named the
 *  nineteen roster actors the globe cannot place. The front door no longer
 *  discloses that. `/api/globe` still returns `unplaced`, so any surface that
 *  wants to say it can.
 */

export default function Landing({ onEnter }: { onEnter: (route: string) => void }) {
  const [health, setHealth] = useState<Health | null>(null)

  useEffect(() => {
    let live = true
    getHealth().then((h) => live && setHealth(h))
    return () => {
      live = false
    }
  }, [])


  return (
    <div className="landing">
      {/* THE MASTHEAD IS BACK (2026-08-17). It went with the rest of the
          subtext in the cut and took the wordmark and its double rule with
          it, which left the page anonymous — a headline and a globe belonging
          to nobody. The rule is the same masthead motif every working page
          wears, so the front door reads as the same publication. */}
      <header className="landing-masthead">
        <span className="landing-wordmark">GeoGraph</span>
      </header>

      <main className="landing-main">
        {/* HERO LEFT, GLOBE RIGHT. The headline is a sentence and sentences
            are read from a left edge; centring it made the eye hunt for the
            start of each line. The globe takes the right half because it is
            the object, not an illustration of the copy. */}
        <div className="landing-split">
          {/* The copy and its door are one column: the door follows the
              sentence that earns it, on the same left edge, rather than
              floating on the page's centre axis away from the thing it
              answers. */}
          <div className="landing-copy">
            <h1 className="landing-hero">
              A hundred and twenty years of geopolitics, priced.
            </h1>
            <button
              type="button"
              className="ink-button text-lg"
              onClick={() => onEnter('/explore')}
            >
              Enter
            </button>
          </div>
          <SituationPlate />
        </div>
      </main>

      <footer className="landing-foot">
        <span className="mono text-[11px] tracking-[0.15em]">
          GRAPH: {(health ? health.graph : 'offline').toUpperCase()}
        </span>
      </footer>
    </div>
  )
}
