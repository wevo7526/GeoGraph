import { useEffect, useState } from 'react'
import { getActors, getDyads, getHealth, getPacks, getWire } from '../api'
import { wireHeadline } from '../lib/story'

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
        <FindBox region={region} onNavigate={onNavigate} />
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

type Hit = { kind: 'pair' | 'event' | 'actor'; label: string; route: string }

function FindBox({
  region,
  onNavigate,
}: {
  region: string
  onNavigate: (route: string) => void
}) {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<Hit[]>([])

  useEffect(() => {
    const needle = q.trim().toLowerCase()
    if (needle.length < 2) {
      setHits([])
      return
    }
    let live = true
    const timer = window.setTimeout(() => {
      Promise.all([getActors(), getDyads(), getWire(region, 60)]).then(([actors, dyads, wire]) => {
        if (!live) return
        const out: Hit[] = []
        for (const d of dyads?.rows ?? []) {
          if (d.name.toLowerCase().includes(needle)) {
            out.push({
              kind: 'pair',
              label: d.name,
              route:
                `/relationships?dyad=${encodeURIComponent(d.node_id)}` +
                `&region=${encodeURIComponent(region)}`,
            })
          }
        }
        for (const item of wire?.rows ?? []) {
          const head = wireHeadline(item)
          const blob = `${head} ${item.initiator_name ?? ''} ${item.target_name ?? ''}`.toLowerCase()
          if (!blob.includes(needle)) continue
          out.push({
            kind: 'event',
            label: head,
            route: item.dyad_id
              ? `/relationships?dyad=${encodeURIComponent(item.dyad_id)}&region=${encodeURIComponent(region)}`
              : '/wire',
          })
        }
        for (const a of actors?.rows ?? []) {
          if (a.region_pack && a.region_pack !== region) continue
          if (!a.name.toLowerCase().includes(needle)) continue
          const pair = (dyads?.rows ?? []).find((d) => d.name.toLowerCase().includes(a.name.toLowerCase()))
          out.push({
            kind: 'actor',
            label: a.name,
            route: pair
              ? `/relationships?dyad=${encodeURIComponent(pair.node_id)}&region=${encodeURIComponent(region)}`
              : '/wire',
          })
        }
        const seen = new Set<string>()
        const unique = out.filter((h) => {
          if (seen.has(h.route + h.label)) return false
          seen.add(h.route + h.label)
          return true
        })
        setHits(unique.slice(0, 20))
      })
    }, 200)
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [q, region])

  return (
    <div className="sidebar-find">
      <label className="kicker sidebar-find-kicker" htmlFor="sidebar-find">
        find
      </label>
      <input
        id="sidebar-find"
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="a pair or actor"
        autoComplete="off"
      />
      {hits.length > 0 && (
        <ul className="sidebar-hits">
          {hits.map((hit) => (
            <li key={`${hit.kind}-${hit.route}-${hit.label}`}>
              <button
                type="button"
                className="sidebar-hit"
                onClick={() => {
                  onNavigate(hit.route)
                  setQ('')
                  setHits([])
                }}
              >
                <span className="sidebar-hit-kind">{hit.kind}</span>
                <span className="sidebar-hit-label">{hit.label}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
