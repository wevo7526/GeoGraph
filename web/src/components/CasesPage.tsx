import { useEffect, useState } from 'react'
import { getCaseStudies } from '../api'
import type { CaseStudyIndexEntry } from '../types'

/** Every narrated episode the archive carries, one card each — the pack's
 *  curated studies today, generated analyses beside them once the reasoning
 *  agent writes some. */
export default function CasesPage({
  region,
  onNavigate,
}: {
  region: string
  onNavigate: (route: string) => void
}) {
  const [studies, setStudies] = useState<CaseStudyIndexEntry[] | null>(null)

  useEffect(() => {
    getCaseStudies().then((r) => setStudies(r?.rows ?? []))
  }, [])

  const visible = (studies ?? []).filter((s) => !s.pack || s.pack === region)

  return (
    <div className="max-w-3xl mx-auto px-6 py-10">
      <p className="mono text-xs uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
        Case studies · {region.toUpperCase()}
      </p>
      {studies === null ? (
        <p className="mt-6 text-sm" style={{ color: 'var(--muted)' }}>
          Reaching the archive…
        </p>
      ) : visible.length === 0 ? (
        <p className="mt-6 text-sm leading-relaxed" style={{ color: 'var(--muted)' }}>
          No narrated episode in this region's pack yet. A case study is the
          pack's worked story — events coded, effects measured, prose beside
          the numbers — and this page will also carry analyses the reasoning
          agent generates once it has an API key to think with.
        </p>
      ) : (
        <ul className="mt-8 space-y-6">
          {visible.map((study) => (
            <li key={study.slug}>
              <button
                type="button"
                onClick={() => onNavigate(`/case/${study.slug}`)}
                className="w-full text-left border p-5"
                style={{
                  borderColor: 'var(--line)',
                  background: 'var(--panel)',
                  cursor: 'pointer',
                }}
              >
                <h2 className="text-xl" style={{ color: 'var(--text)' }}>
                  {study.title}
                </h2>
                <p className="mt-2 text-sm leading-relaxed" style={{ color: 'var(--muted)' }}>
                  {study.dek}
                </p>
                <p className="mono text-xs mt-3" style={{ color: 'var(--muted)' }}>
                  {study.events.length} episode event{study.events.length === 1 ? '' : 's'} ·{' '}
                  {study.pack.toUpperCase()}
                </p>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
