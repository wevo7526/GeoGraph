import { useEffect, useState } from 'react'
import {
  getBacktest,
  getEvents,
  getForecast,
  getForecasts,
  getHealth,
  getPacks,
  getRegionMap,
  getStats,
} from '../api'
import type { BacktestLedger, GraphEvent, Health, RegionMap, Stats } from '../types'
import { pct } from './charts/Kit'

/** The front door, set as a broadsheet front page — and LIVE: beside the
 *  masthead and the archive's ledger it carries, per regional lens, the frozen
 *  near-term call, the solved game's lead pair and its most likely course, and
 *  the paper model's record; below them, the newest events on the wire. Every
 *  figure is a served number with its own page behind it. */

type Region = {
  key: string
  label: string
  escalation: number | null
  asOf: string | null
  map: RegionMap | null
  ledger: BacktestLedger | null
}

export default function Landing({ onEnter }: { onEnter: (route: string) => void }) {
  const [health, setHealth] = useState<Health | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [regions, setRegions] = useState<Region[]>([])
  const [wire, setWire] = useState<GraphEvent[]>([])

  useEffect(() => {
    let live = true
    getHealth().then((h) => live && setHealth(h))
    getStats().then((s) => live && setStats(s))
    const now = new Date()
    const start = new Date(now.getFullYear() - 1, now.getMonth(), now.getDate()).toISOString().slice(0, 10)
    getEvents({ start, end: now.toISOString().slice(0, 10), limit: 8, order: 'desc' }).then(
      (r) => live && setWire(r?.rows ?? []),
    )
    getPacks().then(async (r) => {
      const keys = r?.packs ?? []
      const labels = r?.labels ?? {}
      const built = await Promise.all(
        keys.map(async (key): Promise<Region> => {
          const [forecasts, map, ledger] = await Promise.all([
            getForecasts(key),
            getRegionMap(key),
            getBacktest(key),
          ])
          const near = forecasts?.rows.find((f) => f.mode === 'near_term')
          let escalation: number | null = null
          let asOf: string | null = null
          if (near) {
            const detail = await getForecast(near.node_id)
            const likelihoods = (detail?.scenarios ?? [])
              .filter((s) => s.scenario_name.startsWith('further_escalation') && s.likelihood != null)
              .map((s) => s.likelihood as number)
            escalation = likelihoods.length ? likelihoods.reduce((a, b) => a + b, 0) / likelihoods.length : null
            asOf = detail?.frozen_inputs?.as_of ?? null
          }
          return { key, label: labels[key] ?? key, escalation, asOf, map, ledger }
        }),
      )
      if (live) setRegions(built)
    })
    return () => {
      live = false
    }
  }, [])

  const graphLive = health?.graph === 'open'
  const figure = (v: number | undefined) => (graphLive && v !== undefined ? v.toLocaleString('en-US') : '—')
  const ledger: Array<[string, string]> = [
    ['Coded events', figure(stats?.nodes.Event)],
    ['Actors', figure(stats?.nodes.Actor)],
    ['Measured market effects', figure(stats?.edges.AFFECTED)],
    ['Frozen forecasts', figure(stats?.nodes.Forecast)],
    ['Region lenses', regions.length ? String(regions.length) : '—'],
  ]

  const enterRegion = (route: string, key: string) => {
    window.localStorage.setItem('geograph.region', key)
    onEnter(route)
  }

  return (
    <div className="min-h-full flex flex-col">
      <main className="flex-1 px-6 py-10 sm:py-14">
        <div className="w-full max-w-5xl mx-auto">
          {/* Masthead */}
          <div className="masthead pb-3 flex flex-wrap items-baseline justify-between gap-3">
            <span className="text-2xl tracking-[0.18em]" style={{ fontVariantCaps: 'small-caps' }}>
              GeoGraph
            </span>
            <span className="mono text-[11px] tracking-[0.2em]" style={{ color: 'var(--muted)' }}>
              AN APPLIED-HISTORY ENGINE · 1905 — PRESENT
            </span>
          </div>

          {/* Headline and standfirst */}
          <h1 className="mt-10 text-5xl sm:text-6xl leading-[1.05] tracking-tight">
            A hundred and twenty years of geopolitics, priced.
          </h1>
          <p className="mt-6 text-lg leading-relaxed" style={{ color: 'var(--muted)', maxWidth: '58ch' }}>
            Actors, relationships and events held as a network; a deterministic
            transmission layer that measures what each event actually did to
            markets; solved games that map where each region's pairs go next.
          </p>

          {/* The regional board — the live front page */}
          <section className="mt-12">
            <p className="mono text-[11px] tracking-[0.25em] mb-4" style={{ color: 'var(--muted)' }}>
              THE BOARD · THREE LENSES, TODAY
            </p>
            {regions.length === 0 ? (
              <p className="text-sm italic" style={{ color: 'var(--muted)' }}>reading the lenses…</p>
            ) : (
              <div className="grid md:grid-cols-3 gap-6">
                {regions.map((r) => {
                  const lead = r.map?.ranking?.[0] ?? null
                  const esc = r.map?.scenarios_escalatory?.[0] ?? null
                  const summary = r.ledger?.summary ?? null
                  return (
                    <article key={r.key} className="boxed p-4 flex flex-col gap-3">
                      <header className="flex items-baseline justify-between">
                        <h2 className="text-xl" style={{ fontVariantCaps: 'small-caps', letterSpacing: '0.08em' }}>{r.label}</h2>
                        <span className="mono text-[10px]" style={{ color: 'var(--muted)' }}>{r.asOf ? `as of ${r.asOf}` : ''}</span>
                      </header>
                      <div>
                        <div className="kicker">near-term escalation call</div>
                        <div className="figure text-3xl leading-tight" style={{ color: r.escalation != null && r.escalation >= 0.5 ? 'var(--alert)' : 'var(--text)' }}>
                          {r.escalation != null ? pct(r.escalation, 0) : '—'}
                        </div>
                      </div>
                      <div>
                        <div className="kicker">the solved game's lead pair</div>
                        {lead ? (
                          <p className="text-sm leading-snug">
                            <b>{lead.dyad_name}</b> — {pct(lead.escalation_probability, 0)} to sit above {lead.opening_label}
                            {lead.top_scenario ? `; most likely ${lead.top_scenario.kind.replace(/_/g, ' ')} (${pct(lead.top_scenario.likelihood, 0)})` : ''}.
                          </p>
                        ) : (
                          <p className="text-sm italic" style={{ color: 'var(--muted)' }}>not solved yet</p>
                        )}
                        {esc && esc.market_implications.length > 0 && (
                          <p className="mono text-[11px] mt-1" style={{ color: 'var(--muted)' }}>
                            {esc.dyad_name}: {esc.market_implications.slice(0, 2).map((m) => `${m.market_name} ${(m.median * 100).toFixed(2)}%`).join(' · ')}
                          </p>
                        )}
                      </div>
                      <div>
                        <div className="kicker">paper model, $1M notional</div>
                        {summary ? (
                          <p className="text-sm">
                            <span className="figure" style={{ color: summary.total_return >= 0 ? 'var(--accent)' : 'var(--alert)' }}>
                              {summary.total_return >= 0 ? '+' : ''}{pct(summary.total_return, 1)}
                            </span>{' '}
                            over {summary.quarters_traded} quarters · hit {pct(summary.hit_rate, 0)} · drawdown −{pct(summary.max_drawdown, 0)}
                          </p>
                        ) : (
                          <p className="text-sm italic" style={{ color: 'var(--muted)' }}>
                            {r.ledger?.quarters_skipped ? `${r.ledger.quarters_skipped} quarters, every one a recorded skip` : 'no ledger yet'}
                          </p>
                        )}
                      </div>
                      <nav className="mt-auto flex flex-wrap gap-x-4 gap-y-1 mono text-[11px] pt-2" style={{ borderTop: '1px dotted var(--line)' }}>
                        <button className="article-link" onClick={() => enterRegion('/games', r.key)}>game map</button>
                        <button className="article-link" onClick={() => enterRegion('/markets', r.key)}>markets</button>
                        <button className="article-link" onClick={() => enterRegion('/relationships', r.key)}>relationships</button>
                        <button className="article-link" onClick={() => enterRegion('/explore', r.key)}>explorer</button>
                      </nav>
                    </article>
                  )
                })}
              </div>
            )}
          </section>

          <div className="mt-12 grid md:grid-cols-5 gap-10">
            {/* The ledger */}
            <section className="md:col-span-2 border-t border-b py-5" style={{ borderColor: 'var(--rule-strong)' }}>
              <p className="mono text-[11px] tracking-[0.25em] mb-4" style={{ color: 'var(--muted)' }}>
                THE ARCHIVE TODAY
              </p>
              <dl className="space-y-2">
                {ledger.map(([label, value]) => (
                  <div key={label} className="ledger-row text-base">
                    <dt>{label}</dt>
                    <span className="ledger-leader" aria-hidden="true" />
                    <dd className="ledger-figure text-lg">{value}</dd>
                  </div>
                ))}
              </dl>
              <p className="mt-5 text-sm leading-relaxed" style={{ color: 'var(--muted)', maxWidth: '62ch' }}>
                {graphLive ? (
                  <>
                    The record runs from the Correlates-of-War deep tier (1905) through the present-day
                    GDELT wire, read through {regions.length || 'its'} regional lens{regions.length === 1 ? '' : 'es'}
                    {regions.length ? ` (${regions.map((p) => p.label.toUpperCase()).join(', ')})` : ''}.
                  </>
                ) : (
                  <>The graph is opening; the corpus surfaces serve meanwhile.</>
                )}
              </p>
            </section>

            {/* The wire */}
            <section className="md:col-span-3 border-t border-b py-5" style={{ borderColor: 'var(--rule-strong)' }}>
              <p className="mono text-[11px] tracking-[0.25em] mb-4" style={{ color: 'var(--muted)' }}>
                LATEST ON THE WIRE
              </p>
              {wire.length === 0 ? (
                <p className="text-sm italic" style={{ color: 'var(--muted)' }}>reading the wire…</p>
              ) : (
                <ol className="space-y-2">
                  {wire.map((ev) => (
                    <li key={ev.node_id} className="flex items-baseline gap-3 text-sm">
                      <span className="mono text-[11px] shrink-0" style={{ color: 'var(--muted)' }}>{ev.event_time.slice(0, 10)}</span>
                      <span className="truncate" style={{ minWidth: 0 }}>{ev.name}</span>
                      <span className="mono text-[11px] shrink-0 ml-auto" style={{
                        color: ev.escalation_direction === 'escalating' ? 'var(--alert)' : ev.escalation_direction === 'deescalating' ? 'var(--accent)' : 'var(--muted)',
                      }}>
                        {ev.escalation_direction ?? 'stable'}{ev.goldstein != null ? ` · ${ev.goldstein.toFixed(1)}` : ''}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
              <p className="mt-4">
                <button type="button" className="ink-button text-base" onClick={() => onEnter('/explore')}>
                  Enter the archive
                </button>
              </p>
            </section>
          </div>
        </div>
      </main>

      <footer
        className="px-6 py-4 border-t flex flex-wrap items-baseline justify-between gap-3"
        style={{ borderColor: 'var(--rule-strong)', color: 'var(--muted)' }}
      >
        <span className="mono text-[11px] tracking-[0.15em]">
          GRAPH: {(health ? health.graph : 'offline').toUpperCase()}
        </span>
        <span className="mono text-[11px] tracking-[0.15em]">MEASURED, NEVER ASSERTED</span>
      </footer>
    </div>
  )
}
