/** The network page: who sits between others in this lens, from persisted
 *  NetworkMetric rows. The API never computes a metric on request. */
import { useEffect, useState } from 'react'
import { getNetworkSnapshot, lastFailureFor } from '../api'
import { networkLede } from '../lib/story'
import { useRegionLabel } from '../regions'
import type { NetworkSnapshot } from '../types'
import { Beat, Empty, StoryHead } from '../ui'
import { Bars } from './charts/Kit'

export default function NetworkPage({ region }: { region: string }) {
  const label = useRegionLabel(region)
  const [snap, setSnap] = useState<NetworkSnapshot | null | undefined>(undefined)

  useEffect(() => {
    let live = true
    setSnap(undefined)
    getNetworkSnapshot(region).then((row) => live && setSnap(row))
    return () => {
      live = false
    }
  }, [region])

  if (snap === undefined) {
    return (
      <div className="desk-page py-10">
        <Empty>Reading the persisted web…</Empty>
      </div>
    )
  }
  if (snap === null) {
    const failure = lastFailureFor('/api/network/snapshot')
    return (
      <div className="desk-page py-10">
        <StoryHead
          kicker={`Network · ${label.toUpperCase()}`}
          title="The network snapshot did not answer"
          standfirst={failure?.detail ?? 'The API did not answer.'}
        />
      </div>
    )
  }

  const lede = networkLede(snap, label)
  const through = snap.window_end?.slice(0, 4)
  const from = snap.window_start?.slice(0, 4)

  return (
    <div className="desk-page py-10">
      <StoryHead
        kicker={`Network · ${label.toUpperCase()}`}
        title={lede?.headline ?? `The web in ${label}`}
        standfirst={lede?.support ?? snap.note}
      />

      {!snap.brokers.length ? (
        <p className="mt-8 text-sm leading-relaxed" style={{ color: 'var(--muted)', maxWidth: '58ch' }}>
          No structural window has been computed yet. Centrality and brokerage
          are persisted by the metrics job; until it has run, this page has
          names and no ranks.
        </p>
      ) : (
        <>
          <p className="figure-note mt-4">
            Latest computed window{from && through ? ` ${from}–${through}` : ''}.
            Ranked on this region&rsquo;s roster only.
          </p>

          <div className="desk-grid">
          <Beat
            title="Who sits between the others"
            aside="Betweenness: the share of shortest paths that pass through this actor. A high score is a broker, not a talker."
          >
            <Bars
              rows={snap.brokers.map((row) => ({
                key: row.subject_id,
                label: row.name,
                value: row.value,
              }))}
              format={(v) => v.toFixed(3)}
            />
          </Beat>

          <Beat
            title="Who has room to broker"
            aside="Burt's constraint: lower is more structural holes — more room to play sides off each other. The inverse of sitting in a closed clique."
          >
            <Bars
              rows={snap.holes.map((row) => ({
                key: row.subject_id,
                label: row.name,
                value: row.value,
              }))}
              format={(v) => v.toFixed(3)}
            />
          </Beat>
          </div>

          <Beat
            title="Who is most connected"
            aside="Degree centrality on the windowed web — durable ties plus event flow in the same slice."
          >
            <Bars
              rows={snap.degree.map((row) => ({
                key: row.subject_id,
                label: row.name,
                value: row.value,
              }))}
              format={(v) => v.toFixed(3)}
            />
          </Beat>

          {snap.communities.length > 0 && (
            <Beat title="Coalitions in this window" aside="Greedy modularity on the same graph. A number is a cluster label, not a score.">
              <ul className="mt-2 space-y-3">
                {snap.communities.map((cluster) => (
                  <li key={cluster.id} className="text-sm" style={{ maxWidth: '64ch' }}>
                    <span className="kicker">cluster {cluster.id + 1}</span>
                    <p className="mt-1">{cluster.members.join(', ')}</p>
                  </li>
                ))}
              </ul>
            </Beat>
          )}
        </>
      )}
    </div>
  )
}
