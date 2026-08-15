import { useEffect, useState } from 'react'
import { getEvents, getForecast, getForecasts, getHealth, getPacks, getRegionMap, getStats } from '../api'
import type { GraphEvent, Health, RegionMap, Stats } from '../types'
import { pct } from './charts/Kit'

/** The front door, cut to the bone (2026-08-15): masthead, headline, three
 *  live region tiles — one figure each, the frozen near-term call, and the
 *  solved game's lead pair as a bar — the wire running underneath as a
 *  ticker, one line of archive figures, one door. No paragraphs. Every
 *  number is served and has a page behind it; a tile is the way in. */

type Tile = {
  key: string
  label: string
  escalation: number | null
  lead: { name: string; p: number; label: string; tone: string } | null
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
          const [forecasts, map] = await Promise.all([getForecasts(key), getRegionMap(key)])
          const near = forecasts?.rows.find((f) => f.mode === 'near_term')
          let escalation: number | null = null
          if (near) {
            const detail = await getForecast(near.node_id)
            const ls = (detail?.scenarios ?? [])
              .filter((s) => s.scenario_name.startsWith('further_escalation') && s.likelihood != null)
              .map((s) => s.likelihood as number)
            escalation = ls.length ? ls.reduce((a, b) => a + b, 0) / ls.length : null
          }
          const top: RegionMap['ranking'][number] | undefined = map?.ranking?.[0]
          return {
            key,
            label: labels[key] ?? key,
            escalation,
            lead: top
              ? { name: top.dyad_name, p: top.sharp_departure_probability, label: top.opening_label, tone: top.tone_label ?? 'unread' }
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
                <span className="text-lg" style={{ fontVariantCaps: 'small-caps', letterSpacing: '0.12em' }}>{t.label || ' '}</span>
                <span className="figure text-6xl leading-none" style={{ color: t.escalation != null && t.escalation >= 0.5 ? 'var(--alert)' : 'var(--text)' }}>
                  {t.escalation != null ? pct(t.escalation, 0) : t.label ? '—' : ' '}
                </span>
                <span className="kicker">near-term escalation call</span>
                {t.lead ? (
                  <span className="mt-2 block">
                    <span className="text-sm block truncate">{t.lead.name}</span>
                    <span className="relative block h-1.5 mt-1" style={{ background: 'var(--panel)' }}>
                      <span className="absolute inset-y-0 left-0" style={{ width: `${t.lead.p * 100}%`, background: t.lead.p >= 0.5 ? 'var(--alert)' : 'var(--accent)' }} />
                    </span>
                    <span className="mono text-[11px] block mt-1" style={{ color: 'var(--muted)' }}>
                      solved game · {t.lead.tone} · {pct(t.lead.p, 0)} for sharper-than-usual friction
                    </span>
                  </span>
                ) : (
                  <span className="mt-2 block h-12" />
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
