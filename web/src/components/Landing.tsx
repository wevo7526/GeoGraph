import { useEffect, useState } from 'react'
import { getCaseStudies, getHealth, getStats } from '../api'
import type { CaseStudyIndexEntry, Health, Stats } from '../types'

/** The front door. One claim, one way in, and an honest account of how much of
 *  the archive is actually built — the coverage statement belongs where the
 *  reader arrives, not buried in an endpoint. */
export default function Landing({ onEnter }: { onEnter: (route: string) => void }) {
  const [health, setHealth] = useState<Health | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [studies, setStudies] = useState<CaseStudyIndexEntry[]>([])

  useEffect(() => {
    getHealth().then(setHealth)
    getStats().then(setStats)
    getCaseStudies().then((r) => setStudies(r?.rows ?? []))
  }, [])

  const events = stats?.nodes.Event ?? 0
  const actors = stats?.nodes.Actor ?? 0
  const effects = stats?.edges.AFFECTED ?? 0
  const live = health?.graph === 'open'

  return (
    <div className="min-h-full flex flex-col">
      <main className="flex-1 flex items-center justify-center px-6 py-16">
        <div className="w-full max-w-3xl">
          <p
            className="mono text-xs uppercase tracking-[0.35em] mb-8"
            style={{ color: 'var(--muted)' }}
          >
            An applied-history engine
          </p>

          <h1
            className="text-6xl sm:text-8xl leading-none tracking-tight"
            style={{ color: 'var(--text)' }}
          >
            Geo<span style={{ color: 'var(--accent)' }}>Graph</span>
          </h1>

          <p className="mt-8 text-xl sm:text-2xl leading-snug" style={{ maxWidth: '46ch' }}>
            A hundred and twenty years of geopolitics, measured against the
            markets that priced it.
          </p>

          <p
            className="mt-6 text-base leading-relaxed"
            style={{ color: 'var(--muted)', maxWidth: '62ch' }}
          >
            Actors, relationships and events from 1905 to the present, held as a
            network. A deterministic transmission layer that measures what each
            event actually did to prices — never asserts it. And a reasoning
            layer that argues from the record without ever originating a number.
          </p>

          <div className="mt-12 flex flex-wrap items-center gap-4">
            <button
              type="button"
              onClick={() => onEnter('/explore')}
              className="px-8 py-3 text-lg transition-colors"
              style={{
                border: '1px solid var(--accent)',
                color: 'var(--accent)',
                background: 'transparent',
              }}
              onMouseEnter={(e) => {
                e.currentTarget.style.background = 'var(--accent)'
                e.currentTarget.style.color = 'var(--ink)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.background = 'transparent'
                e.currentTarget.style.color = 'var(--accent)'
              }}
            >
              Enter the archive
            </button>

            {studies.map((study) => (
              <button
                key={study.slug}
                type="button"
                onClick={() => onEnter(`/case/${study.slug}`)}
                className="px-5 py-3 text-base underline decoration-1 underline-offset-4"
                style={{ color: 'var(--text)', background: 'transparent', border: 'none' }}
              >
                Read the case study: {study.title}
              </button>
            ))}
          </div>

          {/* What is actually here. A reader who is not told the archive holds
              one region pack will read absence as evidence. */}
          <dl
            className="mt-16 grid grid-cols-2 sm:grid-cols-4 gap-6 pt-8 border-t"
            style={{ borderColor: 'var(--line)' }}
          >
            {[
              ['Actors', actors],
              ['Coded events', events],
              ['Measured effects', effects],
              ['Region packs', stats ? new Set(['mena']).size : 0],
            ].map(([label, value]) => (
              <div key={String(label)}>
                <dt className="mono text-xs uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
                  {label}
                </dt>
                <dd className="text-3xl mt-1" style={{ color: 'var(--text)' }}>
                  {live ? value : '—'}
                </dd>
              </div>
            ))}
          </dl>

          <p className="mt-6 text-sm leading-relaxed" style={{ color: 'var(--muted)' }}>
            {live ? (
              <>
                The archive currently holds the MENA pack's curated marquee spine
                and one worked episode. Deep history (1905–1979) and the GDELT
                backfill are later phases — absence here is not evidence that
                nothing happened.
              </>
            ) : (
              <>
                The API is not answering, so these figures are unavailable. The
                explorer still opens, and will say what it cannot show.
              </>
            )}
          </p>
        </div>
      </main>

      <footer
        className="px-6 py-5 border-t flex flex-wrap items-baseline justify-between gap-3 text-sm"
        style={{ borderColor: 'var(--line)', color: 'var(--muted)' }}
      >
        <span className="mono text-xs">
          graph: {health ? health.graph : 'offline'}
        </span>
      </footer>
    </div>
  )
}
