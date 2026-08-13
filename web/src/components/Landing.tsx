import { useEffect, useState } from 'react'
import { getCaseStudies, getHealth, getPacks, getStats } from '../api'
import type { CaseStudyIndexEntry, Health, Stats } from '../types'

/** The front door, set as a broadsheet front page: masthead, headline,
 *  standfirst, and the archive's figures as a ledger. The print components it
 *  introduced are shared vocabulary now (styles.css) rather than scoped here —
 *  the whole archive is set on the same paper, so the front door is the first
 *  page of the paper rather than a cover on a different product. The coverage
 *  statement stays honest and current: it names what the archive holds and
 *  what is still a later phase. */
export default function Landing({ onEnter }: { onEnter: (route: string) => void }) {
  const [health, setHealth] = useState<Health | null>(null)
  const [stats, setStats] = useState<Stats | null>(null)
  const [studies, setStudies] = useState<CaseStudyIndexEntry[]>([])
  const [packs, setPacks] = useState<string[]>([])

  useEffect(() => {
    getHealth().then(setHealth)
    getStats().then(setStats)
    getCaseStudies().then((r) => setStudies(r?.rows ?? []))
    // The front page only counts and names the lenses, so it holds their
    // LABELS; the pack keys matter to the working pages, not here.
    getPacks().then((r) =>
      setPacks((r?.packs ?? []).map((name) => r?.labels?.[name] ?? name)),
    )
  }, [])

  const live = health?.graph === 'open'
  const figure = (v: number | undefined) =>
    live && v !== undefined ? v.toLocaleString('en-US') : '—'

  const ledger: Array<[string, string]> = [
    ['Coded events', figure(stats?.nodes.Event)],
    ['Actors', figure(stats?.nodes.Actor)],
    ['Measured market effects', figure(stats?.edges.AFFECTED)],
    ['Worked case studies', live ? String(studies.length) : '—'],
    ['Region lenses', live ? String(packs.length) : '—'],
  ]

  return (
    <div className="min-h-full flex flex-col">
      <main className="flex-1 px-6 py-10 sm:py-14">
        <div className="w-full max-w-3xl mx-auto">
          {/* Masthead */}
          <div className="masthead pb-3 flex flex-wrap items-baseline justify-between gap-3">
            <span
              className="text-2xl tracking-[0.18em]"
              style={{ fontVariantCaps: 'small-caps' }}
            >
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
          <p
            className="mt-6 text-lg leading-relaxed"
            style={{ color: 'var(--muted)', maxWidth: '58ch' }}
          >
            Actors, relationships and events held as a network; a deterministic
            transmission layer that measures what each event actually did to
            markets — never asserts it; and a reasoning layer that argues from
            the record without ever originating a number.
          </p>

          {/* The ledger */}
          <section className="mt-12 border-t border-b py-5" style={{ borderColor: 'var(--rule-strong)' }}>
            <p className="mono text-[11px] tracking-[0.25em] mb-4" style={{ color: 'var(--muted)' }}>
              THE ARCHIVE TODAY
            </p>
            <dl className="space-y-2 max-w-xl">
              {ledger.map(([label, value]) => (
                <div key={label} className="ledger-row text-base">
                  <dt>{label}</dt>
                  <span className="ledger-leader" aria-hidden="true" />
                  <dd className="ledger-figure text-lg">{value}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-5 text-sm leading-relaxed" style={{ color: 'var(--muted)', maxWidth: '62ch' }}>
              {live ? (
                <>
                  The record runs from the Correlates-of-War deep tier through
                  the GDELT wire (1979–2005), read through {packs.length || 'its'}{' '}
                  regional lens{packs.length === 1 ? '' : 'es'}
                  {packs.length ? ` (${packs.map((p) => p.toUpperCase()).join(', ')})` : ''}.
                  The daily wire era and further lenses are later phases —
                  absence here is not evidence that nothing happened.
                </>
              ) : (
                <>
                  The API is not answering, so the figures are unavailable. The
                  archive still opens, and will say what it cannot show.
                </>
              )}
            </p>
          </section>

          {/* The way in — one door. The case studies are reached from inside
              the archive, not listed on the front page. */}
          <div className="mt-10">
            <button type="button" className="ink-button text-lg" onClick={() => onEnter('/explore')}>
              Enter
            </button>
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
        <span className="mono text-[11px] tracking-[0.15em]">
          MEASURED, NEVER ASSERTED
        </span>
      </footer>
    </div>
  )
}
