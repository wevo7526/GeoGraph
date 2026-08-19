/** The region's case desk: worked pack episodes, then a builder.
 *
 *  A GDELT row is not a finished case. Selecting one opens the measured
 *  record (`/case/dynamic`); the desk writes a reading only when asked. */
import { useEffect, useMemo, useState } from 'react'
import { getCaseStudies, getDyads, getWire } from '../api'
import { relationshipName } from '../lib/language'
import { wireHeadline } from '../lib/story'
import { useRegionLabel } from '../regions'
import type { CaseStudyIndexEntry, Dyad, WireFeed, WireItem } from '../types'
import { Beat, Empty, StoryHead } from '../ui'

/** Card footers show the pack's CAPTION, not its key — 'china' reads ASIA. */
function PackTag({ pack }: { pack: string }) {
  const label = useRegionLabel(pack)
  return <>{label.toUpperCase()}</>
}

function pairLine(item: WireItem): string | null {
  const left = item.initiator_name?.trim()
  const right = item.target_name?.trim()
  if (left && right) return `${left} · ${right}`
  return null
}

export default function CasesPage({
  region,
  onNavigate,
}: {
  region: string
  onNavigate: (route: string) => void
}) {
  const [studies, setStudies] = useState<CaseStudyIndexEntry[] | null>(null)
  const [wire, setWire] = useState<WireFeed | null | undefined>(undefined)
  const [dyads, setDyads] = useState<Dyad[] | null | undefined>(undefined)
  const regionLabel = useRegionLabel(region)

  useEffect(() => {
    let live = true
    setStudies(null)
    setWire(undefined)
    setDyads(undefined)
    getCaseStudies().then((r) => live && setStudies(r?.rows ?? []))
    getWire(region, 200).then((r) => live && setWire(r))
    getDyads(region).then((r) => live && setDyads(r?.rows ?? []))
    return () => {
      live = false
    }
  }, [region])

  const visible = (studies ?? []).filter((s) => !s.pack || s.pack === region)
  const curatedIds = useMemo(() => {
    const ids = new Set<string>()
    for (const study of visible) {
      for (const id of study.events) ids.add(id)
    }
    return ids
  }, [visible])

  const picker = useMemo(() => {
    const rows = wire?.rows ?? []
    const major = rows.filter((row) => row.departure || curatedIds.has(row.node_id))
    return major.length ? major : rows
  }, [wire, curatedIds])

  return (
    <div className="desk-page">
      <StoryHead
        kicker={`Case studies · ${regionLabel.toUpperCase()}`}
        title="The case desk"
        standfirst={`Worked episodes the pack already narrated, and a builder for any major event in ${regionLabel}. Selecting an event opens its measured record — not a finished case.`}
      />

      {studies === null ? (
        <Empty>reaching the archive…</Empty>
      ) : visible.length === 0 ? (
        <p className="figure-note mt-6">
          This region&rsquo;s pack has not declared a worked episode. The builder
          below still stands: pick a major event to open the measured record.
        </p>
      ) : (
        <Beat
          title="Worked episodes"
          aside="Pack-declared studies: events coded, effects measured, prose beside the numbers."
        >
          <ul className="space-y-4">
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
        </Beat>
      )}

      <div className="desk-grid">
        <Beat
          title="Build a study"
          major
          aside="Major events on this region's wire — departures from a pair's own baseline, plus any spine event in a worked episode that landed in this batch. Click one to open the measured record."
        >
          {wire === undefined ? (
            <Empty>reading the wire…</Empty>
          ) : wire === null ? (
            <Empty>The wire did not answer.</Empty>
          ) : picker.length === 0 ? (
            <Empty>
              No coded events in this region&rsquo;s current batch. When they land,
              they appear here.
            </Empty>
          ) : (
            <div>
              {picker.map((item) => {
                const pair = pairLine(item)
                return (
                  <button
                    key={item.node_id}
                    type="button"
                    className="desk-pick"
                    onClick={() =>
                      onNavigate(
                        `/case/dynamic?event=${encodeURIComponent(item.node_id)}&region=${encodeURIComponent(region)}`,
                      )
                    }
                  >
                    <time className="mono text-xs" style={{ color: 'var(--muted)' }} dateTime={item.event_time}>
                      {item.event_time}
                    </time>
                    <span className="desk-pick-head">{wireHeadline(item)}</span>
                    {pair && (
                      <span className="figure-note" style={{ display: 'block', margin: '0.25rem 0 0' }}>
                        {pair}
                      </span>
                    )}
                  </button>
                )
              })}
            </div>
          )}
        </Beat>

        <Beat
          title="Or a pair's record"
          aside="Every roster pair in this lens. Opens the measured record of the events that moved them most."
        >
          {dyads === undefined ? (
            <Empty>reading the pairs…</Empty>
          ) : !dyads || dyads.length === 0 ? (
            <Empty>No roster pairs in this region yet.</Empty>
          ) : (
            <div>
              {dyads.map((d) => (
                <button
                  key={d.node_id}
                  type="button"
                  className="desk-pick"
                  onClick={() =>
                    onNavigate(
                      `/case/dynamic?dyad=${encodeURIComponent(d.node_id)}&region=${encodeURIComponent(region)}`,
                    )
                  }
                >
                  {relationshipName(d.name, d.node_id)}
                </button>
              ))}
            </div>
          )}
        </Beat>
      </div>
    </div>
  )
}
