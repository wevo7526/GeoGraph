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
  const [healthy, setHealthy] = useState<boolean | null>(null)

  useEffect(() => {
    getPacks().then((r) => {
      if (r?.packs?.length) setPacks(r.packs)
    })
    getHealth().then((h) => setHealthy(h?.graph === 'open'))
  }, [])

  const pages: Array<[string, string]> = [
    ['/explore', 'Explorer'],
    ['/reasoning', 'Reasoning'],
    ['/trading', 'Trading'],
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
                {name.toUpperCase()}
              </option>
            ))}
          </select>
        </label>
      </div>

      <nav className="flex items-baseline gap-5 text-sm">
        {pages.map(([path, label]) => {
          const active = route.startsWith(path)
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
