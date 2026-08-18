import { useEffect, useState } from 'react'
import { getHealth, getPacks } from '../api'

/** The one rail every working page shares, set as the paper's LEFT RULE:
 *  wordmark and region at the top (the lens every layer looks through), the
 *  desks in order of the reader's question, and a single status dot at the
 *  foot instead of a stats readout — coverage lives in the pages, not the
 *  chrome. The double rule on the rail's right edge is the same motif the
 *  front page wears under its wordmark, turned vertical so the desks read as
 *  later pages of one paper rather than as a toolbar above a product. */
export default function Sidebar({
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
  // machine's parts: the situation first (what broke, what it means, what
  // happens next), then the desks that go deeper, with the 3D explorer as the
  // archive instrument rather than the door.
  const pages: Array<[string, string, string]> = [
    ['/situation', 'Situation', 'S'],
    ['/wire', 'Wire', 'W'],
    ['/markets', 'Markets', 'M'],
    ['/games', 'Game theory', 'G'],
    ['/relationships', 'Relationships', 'R'],
    ['/explore', 'Explorer', 'E'],
    ['/cases', 'Case studies', 'C'],
  ]

  return (
    <aside className="sidebar">
      <div className="sidebar-head">
        <button
          type="button"
          onClick={() => onNavigate('/')}
          className="sidebar-wordmark"
          title="GeoGraph — front page"
          aria-label="GeoGraph, front page"
        >
          <span className="sidebar-wordmark-full">GeoGraph</span>
          <span className="sidebar-wordmark-mark" aria-hidden>
            G
          </span>
        </button>
        <label className="sidebar-region">
          <span className="kicker sidebar-region-kicker">region</span>
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

      <nav className="sidebar-nav" aria-label="desks">
        {pages.map(([path, label, mark]) => {
          // The game, reasoning and trading pages folded INTO Relationship, so
          // their old deep links must light the Relationship item, not nothing.
          // Reading one case (/case/<slug>) is being IN Case studies.
          const active =
            route.startsWith(path) ||
            (path === '/cases' && route.startsWith('/case/')) ||
            (path === '/relationships' &&
              ['/relationship', '/reasoning'].some((r) => route.startsWith(r))) ||
            (path === '/markets' && route.startsWith('/trading'))
          return (
            <button
              key={path}
              type="button"
              onClick={() => onNavigate(path)}
              className={active ? 'sidebar-nav-item is-active' : 'sidebar-nav-item'}
              title={label}
              aria-label={label}
              aria-current={active ? 'page' : undefined}
            >
              <span className="sidebar-nav-full">{label}</span>
              <span className="sidebar-nav-mark" aria-hidden>
                {mark}
              </span>
            </button>
          )
        })}
      </nav>

      <div className="sidebar-foot">
        <span
          aria-label={healthy === null ? 'connecting' : healthy ? 'archive live' : 'archive offline'}
          title={healthy === null ? 'connecting…' : healthy ? 'archive live' : 'archive offline'}
          className="sidebar-health"
          style={{
            background:
              healthy === null ? 'var(--line)' : healthy ? 'var(--accent)' : 'var(--alert)',
          }}
        />
        <span className="kicker sidebar-health-label">
          {healthy === null ? 'connecting' : healthy ? 'live' : 'offline'}
        </span>
      </div>
    </aside>
  )
}
