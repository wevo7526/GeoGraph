import { useEffect, useMemo, useState } from 'react'
import { getHealth, getPack, getRegimes } from './api'
import NetworkView from './components/NetworkView'
import TimeSlider, { YEAR_NOW } from './components/TimeSlider'
import type { Health, Pack, Segmentation } from './types'

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [regimes, setRegimes] = useState<Segmentation | null>(null)
  const [pack, setPack] = useState<Pack | null>(null)
  const [year, setYear] = useState(YEAR_NOW)

  useEffect(() => {
    getHealth().then(setHealth)
    getRegimes().then(setRegimes)
    getPack('mena').then(setPack)
  }, [])

  const eventsOfYear = useMemo(
    () =>
      (pack?.marquee_events.events ?? []).filter(
        (e) => Number(e.date.slice(0, 4)) === year,
      ),
    [pack, year],
  )

  return (
    <div className="h-full flex flex-col">
      <header
        className="px-6 py-4 flex items-baseline justify-between border-b"
        style={{ borderColor: 'var(--line)' }}
      >
        <div>
          <h1 className="text-2xl tracking-wide" style={{ color: 'var(--accent)' }}>
            GeoGraph
          </h1>
          <p className="text-sm" style={{ color: 'var(--muted)' }}>
            An applied-history engine — 120 years of geopolitics and markets, as a network.
          </p>
        </div>
        <span className="mono text-xs" style={{ color: health?.graph === 'open' ? 'var(--accent)' : 'var(--alert)' }}>
          {health ? `graph: ${health.graph}` : 'api: offline'}
        </span>
      </header>

      <main className="flex-1 grid grid-cols-1 lg:grid-cols-[1fr_320px] overflow-hidden">
        <section className="p-6 overflow-auto">
          <NetworkView actors={pack?.actors.actors ?? null} />
        </section>

        <aside
          className="border-l p-6 overflow-auto"
          style={{ borderColor: 'var(--line)', background: 'var(--panel)' }}
        >
          <h2 className="text-sm uppercase tracking-widest mb-3" style={{ color: 'var(--muted)' }}>
            {year} in the archive
          </h2>
          {eventsOfYear.length === 0 && (
            <p className="text-sm" style={{ color: 'var(--muted)' }}>
              No marquee events this year{pack ? '' : ' (pack not loaded)'} — the
              full event layer arrives with the GDELT backfill and the deep-tier
              ingestion.
            </p>
          )}
          <ul className="space-y-3">
            {eventsOfYear.map((e) => (
              <li key={e.id}>
                <div className="mono text-xs" style={{ color: 'var(--accent)' }}>
                  {e.date}
                </div>
                <div className="text-sm">{e.name}</div>
                {e.note && (
                  <div className="text-xs mt-1" style={{ color: 'var(--muted)' }}>
                    {e.note}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </aside>
      </main>

      <TimeSlider year={year} onChange={setYear} regimes={regimes} />
    </div>
  )
}
