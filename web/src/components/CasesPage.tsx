import { useEffect, useState } from 'react'
import { getCaseStudies } from '../api'
import { useRegionLabel } from '../regions'
import { Empty, StoryHead } from '../ui'
import type { CaseStudyIndexEntry } from '../types'

/** Card footers show the pack's CAPTION, not its key — 'china' reads ASIA. */
function PackTag({ pack }: { pack: string }) {
  const label = useRegionLabel(pack)
  return <>{label.toUpperCase()}</>
}

/** Every narrated episode the archive carries, one card each. Open a study
 *  and the desk writes a reading of the measured tables. */
export default function CasesPage({
  region,
  onNavigate,
}: {
  region: string
  onNavigate: (route: string) => void
}) {
  const [studies, setStudies] = useState<CaseStudyIndexEntry[] | null>(null)
  const regionLabel = useRegionLabel(region)

  useEffect(() => {
    getCaseStudies().then((r) => setStudies(r?.rows ?? []))
  }, [])

  const visible = (studies ?? []).filter((s) => !s.pack || s.pack === region)

  return (
    <div className="reading-column">
      <StoryHead kicker={`Case studies · ${regionLabel.toUpperCase()}`} title="Worked episodes" />
      {studies === null ? (
        <Empty>reaching the archive…</Empty>
      ) : visible.length === 0 ? (
        <p className="mt-8 text-sm leading-relaxed" style={{ color: 'var(--muted)', maxWidth: '58ch' }}>
          No narrated episode in this region's pack yet. A case study is the
          pack's worked story — events coded, effects measured, prose beside
          the numbers. Open one and the desk writes a reading of those
          tables when the key is present.
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
                  <PackTag pack={study.pack} />
                </p>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
