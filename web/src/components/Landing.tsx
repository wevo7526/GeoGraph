import { useEffect, useState } from 'react'
import { getEvents, getHealth, getPacks, getRegionMap, getStats } from '../api'
import type { GraphEvent, Health, RegionMap, Stats } from '../types'
import { courseInWords, standingPhrase } from '../lib/story'

/** The front door, cut to the bone (2026-08-15): masthead, headline, three
 *  live region tiles — one figure each, the frozen near-term call, and the
 *  solved game's lead pair as a bar — the wire running underneath as a
 *  ticker, one line of archive figures, one door. No paragraphs. Every
 *  number is served and has a page behind it; a tile is the way in. */

/** THE TILE LEADS WITH THE PAIR AND ITS COURSE, not with a probability.
 *
 *  It used to lead with the near-term escalation call — 99% for Asia — and the
 *  platform's own calibration walk says that question's base rate since 2005 is
 *  0.97: "does a focal pair escalate again within three years" is very nearly
 *  "yes, always". A front door whose one number is a near-certainty advertises
 *  a machine predicting what is already known. Beneath it sat `tone_label`,
 *  which scored the United States and China — a declared rivalry — "friendly",
 *  and which core/games/scenarios.py says in as many words must never
 *  characterise a pair. Both are gone: the tile now carries the region's most
 *  coercive pair, the course the game puts most mass on in that pair's own
 *  words, and the count behind the ranking. */
type Tile = {
  key: string
  label: string
  lead: {
    name: string
    course: string | null
    coercive: number | null
    standing: string | null
  } | null
}

export default function Landing({ onEnter }: { onEnter: (route: string) => void }) {
  const [health, setHealth] = useState<Health | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [tiles, setTiles] = useState<Tile[]>([])
  const [wire, setWire] = useState<GraphEvent[]>([])

  useEffect(() => {
    let live = true
    getHealth().then((h) => live && setHealth(h))
    getStats().then((s) => live && setStats(s))
    const now = new Date()
    const start = new Date(now.getFullYear() - 1, now.getMonth(), now.getDate()).toISOString().slice(0, 10)
    getEvents({ start, end: now.toISOString().slice(0, 10), limit: 24, order: 'desc' }).then(
      (r) => live && setWire(r?.rows ?? []),
    )
    getPacks().then(async (r) => {
      const keys = r?.packs ?? []
      const labels = r?.labels ?? {}
      const built = await Promise.all(
        keys.map(async (key): Promise<Tile> => {
          const map = await getRegionMap(key)
          const top: RegionMap['ranking'][number] | undefined = map?.ranking?.[0]
          return {
            key,
            label: labels[key] ?? key,
            lead: top
              ? {
                  name: top.dyad_name,
                  course: courseInWords(top.top_scenario, top.family),
                  coercive: top.coercive_events ?? null,
                  standing: standingPhrase(top.standing),
                }
              : null,
          }
        }),
      )
      if (live) setTiles(built)
    })
    return () => {
      live = false
    }
  }, [])

  const graphLive = health?.graph === 'open'
  const n = (v: number | undefined) => (graphLive && v !== undefined ? v.toLocaleString('en-US') : '—')
  const enter = (route: string, key: string) => {
    window.localStorage.setItem('geograph.region', key)
    onEnter(route)
  }
  // The ticker doubles its list so the loop is seamless.
  const ribbon = wire.length ? [...wire, ...wire] : []

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

          {/* Three tiles — the whole front page's content */}
          <section className="mt-14 grid sm:grid-cols-3 gap-px" style={{ background: 'var(--rule-strong)', border: '1px solid var(--rule-strong)' }}>
            {(tiles.length ? tiles : [{ key: 'a' }, { key: 'b' }, { key: 'c' }].map((t) => ({ ...t, label: '', escalation: null, lead: null }))).map((t) => (
              <button
                key={t.key}
                type="button"
                className="tile text-left p-6 flex flex-col gap-4"
                onClick={() => t.label && enter('/games', t.key)}
                aria-label={t.label ? `Enter ${t.label}` : 'loading'}
              >
                <span className="text-lg" style={{ fontVariantCaps: 'small-caps', letterSpacing: '0.12em' }}>{t.label || ' '}</span>
                {t.lead ? (
                  <>
                    <span className="block text-3xl leading-tight" style={{ letterSpacing: '-0.015em' }}>
                      {t.lead.name}
                    </span>
                    {t.lead.standing && (
                      <span className="block text-sm" style={{ color: 'var(--muted)' }}>{t.lead.standing}</span>
                    )}
                    <span className="block text-base">{t.lead.course ?? 'no course named yet'}</span>
                    <span className="mono text-[11px] block mt-auto pt-3" style={{ color: 'var(--muted)' }}>
                      {t.lead.coercive != null
                        ? `${t.lead.coercive.toLocaleString('en-US')} COERCIVE ACTS IN THE LAST YEAR`
                        : 'THE REGION’S MOST ACTIVE PAIR'}
                    </span>
                  </>
                ) : (
                  <span className="block text-3xl leading-tight" style={{ color: 'var(--muted)' }}>
                    {t.label ? 'solving…' : ' '}
                  </span>
                )}
              </button>
            ))}
          </section>

          {/* The wire, running */}
          <div className="ticker mt-10" aria-label="latest on the wire">
            {ribbon.length ? (
              <div className="ticker-track">
                {ribbon.map((ev, i) => (
                  <span key={ev.node_id + i} className="ticker-item mono text-[11px]">
                    <span style={{ color: 'var(--muted)' }}>{ev.event_time.slice(0, 10)}</span>
                    <span style={{ color: ev.escalation_direction === 'escalating' ? 'var(--alert)' : ev.escalation_direction === 'deescalating' ? 'var(--accent)' : 'var(--text)' }}>
                      {' '}{ev.name}
                    </span>
                  </span>
                ))}
              </div>
            ) : (
              <span className="mono text-[11px]" style={{ color: 'var(--muted)' }}>the wire is loading…</span>
            )}
          </div>

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
