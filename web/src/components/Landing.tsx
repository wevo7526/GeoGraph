import { useEffect, useState } from 'react'
import { getHealth, getStats } from '../api'
import SituationPlate from './SituationPlate'
import type { Health, Stats } from '../types'

/** The front door, cut to the bone (2026-08-15): masthead, headline, three
 *  live region tiles — one figure each, the frozen near-term call, and the
 *  solved game's lead pair as a bar — the wire running underneath as a
 *  ticker, one line of archive figures, one door. No paragraphs. Every
 *  number is served and has a page behind it; a tile is the way in. */

/** THE FRONT DOOR IS THE MAP (2026-08-17).
 *
 *  It was a headline, three region tiles and a scrolling ticker. The tiles are
 *  gone with the ticker: they each led with a region's most coercive pair and
 *  the course its game put mass on, which is a page's worth of claim made
 *  three times over a reader who has not yet been told what the archive IS.
 *  The globe says that in one object, and the tabs are three clicks from here.
 *
 *  What is left is a masthead, one headline, the map, one line of archive
 *  figures and one door — which is what the 2026-08-15 cut was aiming at
 *  before it had a map to aim with.
 */

export default function Landing({ onEnter }: { onEnter: (route: string) => void }) {
  const [health, setHealth] = useState<Health | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)

  useEffect(() => {
    let live = true
    getHealth().then((h) => live && setHealth(h))
    getStats().then((s) => live && setStats(s))
    return () => {
      live = false
    }
  }, [])

  const graphLive = health?.graph === 'open'
  const n = (v: number | undefined) => (graphLive && v !== undefined ? v.toLocaleString('en-US') : '—')

  return (
    <div className="min-h-full flex flex-col">
      <main className="flex-1 px-6 py-10 sm:py-14 flex flex-col">
        <div className="w-full max-w-5xl mx-auto flex-1 flex flex-col">
          <div className="masthead pb-3 flex flex-wrap items-baseline justify-between gap-3">
            <span className="text-2xl tracking-[0.18em]" style={{ fontVariantCaps: 'small-caps' }}>GeoGraph</span>
            <span className="mono text-[11px] tracking-[0.2em]" style={{ color: 'var(--muted)' }}>
              AN APPLIED-HISTORY ENGINE · 1905 — PRESENT
            </span>
          </div>

          <h1 className="mt-12 text-5xl sm:text-7xl leading-[1.02] tracking-tight" style={{ maxWidth: '18ch' }}>
            A hundred and twenty years of geopolitics, priced.
          </h1>

          {/* THE BOARD. It replaced the ticker on 2026-08-17, and the swap is
              the point rather than a side effect: the ticker scrolled coded
              event names past the reader with no figure and no way to stop it,
              and it was the page's only motion. The plate's rail carries the
              same events PLUS how far each sat from that pair's own baseline,
              states them in words a screen reader can read, and stops on hover,
              on focus, on a hidden tab and off-screen. */}
          <SituationPlate />



          <div className="mt-10 flex flex-wrap items-center justify-between gap-4">
            <button type="button" className="ink-button text-lg" onClick={() => onEnter('/explore')}>
              Enter
            </button>
            <span className="mono text-[11px] tracking-[0.15em]" style={{ color: 'var(--muted)' }}>
              {n(stats?.nodes.Event)} EVENTS · {n(stats?.edges.AFFECTED)} MEASURED EFFECTS · {n(stats?.nodes.Forecast)} FROZEN FORECASTS
            </span>
          </div>
        </div>
      </main>

      <footer className="px-6 py-4 border-t flex flex-wrap items-baseline justify-between gap-3" style={{ borderColor: 'var(--rule-strong)', color: 'var(--muted)' }}>
        <span className="mono text-[11px] tracking-[0.15em]">GRAPH: {(health ? health.graph : 'offline').toUpperCase()}</span>
        <span className="mono text-[11px] tracking-[0.15em]">MEASURED, NEVER ASSERTED</span>
      </footer>
    </div>
  )
}
