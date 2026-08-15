import { useEffect, useState } from 'react'
import { getHealth, getPacks } from '../api'

/** The one bar every working page shares, set as the paper's MASTHEAD: REGION
 *  on the left (the lens every layer looks through), the pages in the middle,
 *  and a single status dot on the right instead of a stats readout — coverage
 *  lives in the pages, not the chrome. The double rule under it is the same
 *  one the front page carries, which is what makes the working pages read as
 *  later pages of one paper rather than as a different application. */
export default function TopBar({
  route,
  region,
  onRegion,
  onNavigate,
}: {
  route: string
  region: string
  onRegion: (next: string) => void
  onNavigate: (next: string) => void
}) {
  const [packs, setPacks] = useState<string[]>([region])
  // Pack name → display label. The option VALUE stays the name (it is what
  // every region= parameter takes); only the caption is the label.
  const [labels, setLabels] = useState<Record<string, string>>({})
  const [healthy, setHealthy] = useState<boolean | null>(null)

  useEffect(() => {
    getPacks().then((r) => {
      if (r?.packs?.length) setPacks(r.packs)
      if (r?.labels) setLabels(r.labels)
    })
    // The dot is LIVE state, not a snapshot: during an API-first boot the
    // graph opens minutes after the page loads, and a one-shot fetch showed
    // "offline" until a reload. Poll gently; refresh on tab focus too.
    let cancelled = false
    const check = () => getHealth().then((h) => !cancelled && setHealthy(h?.graph === 'open'))
    check()
    const timer = window.setInterval(check, 60_000)
    window.addEventListener('focus', check)
    return () => {
      cancelled = true
      window.clearInterval(timer)
      window.removeEventListener('focus', check)
    }
  }, [])

  // One coherent front-of-house, organised around the user's question, not the
  // machine's parts: browse the web (Explorer), open a relationship (the
  // answer, past→now→forward), follow it (Watchlist), read the worked cases.
  // Reasoning, the game and trading folded INTO the Relationship page as its
  // evidence, its "how it plays out", and its track record — no longer tabs.
  const pages: Array<[string, string]> = [
    ['/explore', 'Explorer'],
    ['/relationship', 'Relationship'],
    ['/watchlist', 'Watchlist'],
    ['/cases', 'Case studies'],
  ]

  return (
    <header className="topbar masthead">
      <div className="flex items-center gap-4 min-w-0">
        <button
          type="button"
          onClick={() => onNavigate('/')}
          className="text-xl shrink-0"
          style={{
            background: 'none',
            border: 'none',
            cursor: 'pointer',
            color: 'var(--text)',
            fontVariantCaps: 'small-caps',
            letterSpacing: '0.14em',
          }}
        >
          GeoGraph
        </button>
        <label className="flex items-center gap-2 kicker">
          region
          <select
            value={region}
            onChange={(e) => onRegion(e.target.value)}
            className="region-select mono text-xs"
            aria-label="region pack"
          >
            {packs.map((name) => (
              <option key={name} value={name}>
                {(labels[name] ?? name).toUpperCase()}
              </option>
            ))}
          </select>
        </label>
      </div>

      <nav className="flex items-baseline gap-5 text-sm">
        {pages.map(([path, label]) => {
          // The game, reasoning and trading pages folded INTO Relationship, so
          // their old deep links must light the Relationship tab, not nothing.
          // Reading one case (/case/<slug>) is being IN Case studies.
          const active =
            route.startsWith(path) ||
            (path === '/cases' && route.startsWith('/case/')) ||
            (path === '/relationship' &&
              ['/games', '/reasoning', '/trading'].some((r) => route.startsWith(r)))
          return (
            <button
              key={path}
              type="button"
              onClick={() => onNavigate(path)}
              className="pb-0.5"
              style={{
                background: 'none',
                border: 'none',
                cursor: 'pointer',
                color: active ? 'var(--text)' : 'var(--muted)',
                borderBottom: active ? '2px solid var(--accent)' : '2px solid transparent',
              }}
            >
              {label}
            </button>
          )
        })}
        <span
          aria-label={healthy === null ? 'connecting' : healthy ? 'archive live' : 'archive offline'}
          title={healthy === null ? 'connecting…' : healthy ? 'archive live' : 'archive offline'}
          className="inline-block h-2 w-2 rounded-full"
          style={{
            background:
              healthy === null ? 'var(--line)' : healthy ? 'var(--accent)' : 'var(--alert)',
          }}
        />
      </nav>
    </header>
  )
}
