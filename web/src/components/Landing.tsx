import { useEffect, useState } from 'react'
import { getHealth } from '../api'
import SituationPlate from './SituationPlate'
import type { Health } from '../types'

/** THE FRONT DOOR IS THE MAP (2026-08-17, recut 2026-08-18).
 *
 *  Left column: kicker, headline, standfirst, then the door on the same left
 *  edge, dropped so it is not jammed under the sentence that earns it. Right
 *  column: the globe. A hairline at the foot says whether the graph is open.
 *
 *  The 08-17 cut was right about the pile — tiles, ticker, archive figures,
 *  globe strapline — and wrong about the sentence. "A hundred and twenty
 *  years of geopolitics, priced" was the 1905 claim in longhand, written to
 *  dodge the surface-language ban on `120 years`. The floor is 1972. The
 *  product is applied history: the archive measures what events did to
 *  prices; games answer what happens next. A standfirst is the one paragraph
 *  the door is allowed, because a headline alone cannot say both halves.
 *
 *  ONE HONEST LOSS, still recorded: the strapline named the nineteen roster
 *  actors the globe cannot place. `/api/globe` still returns `unplaced`.
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
          to nobody. The rule is the cover's masthead; working pages wear the
          same double rule turned vertical on the left rail. */}
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
            <div className="landing-copy-text">
              <p className="kicker landing-kicker">Applied history</p>
              <h1 className="landing-hero">
                What events did to prices.
              </h1>
              <p className="landing-lede">
                From 1972 to the live wire. The archive measures the record.
                Games solve for what happens next.
              </p>
            </div>
            <button
              type="button"
              className="ink-button landing-enter text-lg"
              onClick={() => onEnter('/intel')}
            >
              Enter
            </button>
          </div>
          <SituationPlate onOpen={onEnter} />
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
