/** The network page: who sits between others in this lens, from persisted
 *  NetworkMetric rows. The API never computes a metric on request. */
import { useEffect, useState } from 'react'
import { getNetworkSnapshot, lastFailureFor } from '../api'
import { count, networkLede } from '../lib/story'
import { useRegionLabel } from '../regions'
import type { NetworkSnapshot } from '../types'
import { Beat, Empty, StoryHead } from '../ui'
import { Bars, SeriesLine, Tiles } from './charts/Kit'

const fig = (value: number | null | undefined, digits = 3) =>
  value == null || !Number.isFinite(value) ? '—' : value.toFixed(digits)

export default function NetworkPage({
  region,
  onNavigate,
}: {
  region: string
  onNavigate?: (route: string) => void
}) {
  const label = useRegionLabel(region)
  const [snap, setSnap] = useState<NetworkSnapshot | null | undefined>(undefined)
  const [focus, setFocus] = useState<string | null>(null)

  useEffect(() => {
    let live = true
    setSnap(undefined)
    setFocus(null)
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
  const roster = snap.roster ?? []
  const decades = snap.decades ?? []
  const eigen = snap.eigenvector ?? []
  const overTime = snap.brokerage_over_time ?? []
  const pick = (key: string) => setFocus((was) => (was === key ? null : key))

  return (
    <div className="desk-page py-10">
      <StoryHead
        kicker={`Network · ${label.toUpperCase()}`}
        title={lede?.headline ?? `The web in ${label}`}
        standfirst={lede?.support ?? snap.note}
        action={
          onNavigate ? (
            <button
              type="button"
              className="mono text-xs underline underline-offset-2"
              style={{ color: 'var(--accent)', background: 'none', border: 'none', cursor: 'pointer' }}
              onClick={() => onNavigate('/explore')}
            >
              watch the web move →
            </button>
          ) : null
        }
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
            Ranked on this region&rsquo;s roster only. Click a name to mark it
            in the table.
          </p>

          <Tiles
            items={[
              {
                label: 'window',
                value: from && through ? `${from}–${through}` : '—',
                sub: 'latest computed slice',
              },
              {
                label: 'roster ranked',
                value: count(snap.n ?? roster.length),
                sub: 'actors with a structural score',
              },
              {
                label: 'coalitions',
                value: count(snap.communities.length),
                sub: 'greedy modularity clusters',
              },
              {
                label: 'decades on file',
                value: count(decades.length),
                sub: 'not open regime spans',
              },
            ]}
          />

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
                onPick={pick}
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
                onPick={pick}
              />
            </Beat>
          </div>

          <div className="desk-grid">
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
                onPick={pick}
              />
            </Beat>

            <Beat
              title="Who is tied to the well-tied"
              aside="Eigenvector centrality: a high score is being next to other well-connected actors, not merely having many ties."
            >
              {eigen.length ? (
                <Bars
                  rows={eigen.map((row) => ({
                    key: row.subject_id,
                    label: row.name,
                    value: row.value,
                  }))}
                  format={(v) => v.toFixed(3)}
                  onPick={pick}
                />
              ) : (
                <p className="figure-note">
                  No eigenvector ranking for this window — the solver skips a
                  graph it cannot rank rather than inventing one.
                </p>
              )}
            </Beat>
          </div>

          <Beat
            title="The roster, scored"
            aside="Every actor on this lens who carried a structural score in the latest window. A blank cell is an isolate on that measure, not a zero."
          >
            <div className="scroll-x">
              <table className="rule-table" style={{ minWidth: 640 }}>
                <thead>
                  <tr>
                    <th className="text-left">actor</th>
                    <th className="text-right">between</th>
                    <th className="text-right">holes</th>
                    <th className="text-right">degree</th>
                    <th className="text-right">eigen</th>
                    <th className="text-right">cluster</th>
                  </tr>
                </thead>
                <tbody>
                  {roster.map((row) => {
                    const active = focus === row.subject_id
                    return (
                      <tr
                        key={row.subject_id}
                        onClick={() => pick(row.subject_id)}
                        style={{
                          cursor: 'pointer',
                          background: active ? 'var(--panel)' : undefined,
                        }}
                      >
                        <td>{row.name}</td>
                        <td className="text-right mono">{fig(row.betweenness)}</td>
                        <td className="text-right mono">{fig(row.constraint)}</td>
                        <td className="text-right mono">{fig(row.degree)}</td>
                        <td className="text-right mono">{fig(row.eigenvector)}</td>
                        <td className="text-right mono">
                          {row.community == null ? '—' : row.community + 1}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </Beat>

          {snap.communities.length > 0 && (
            <div className="desk-grid">
              <Beat
                title="Coalition size"
                aside="Greedy modularity on the same graph. Height is how many roster actors sit in the cluster, not a score."
              >
                <Bars
                  rows={snap.communities.map((cluster) => ({
                    key: String(cluster.id),
                    label: `cluster ${cluster.id + 1}`,
                    value: cluster.size,
                  }))}
                  format={(v) => String(Math.round(v))}
                />
              </Beat>
              <Beat
                title="Who sits with whom"
                aside="A number is a cluster label. Membership is the claim."
              >
                <div className="scroll-x">
                  <table className="rule-table" style={{ minWidth: 360 }}>
                    <thead>
                      <tr>
                        <th className="text-left">cluster</th>
                        <th className="text-right">n</th>
                        <th className="text-left">members</th>
                      </tr>
                    </thead>
                    <tbody>
                      {snap.communities.map((cluster) => (
                        <tr key={cluster.id}>
                          <td className="mono">{cluster.id + 1}</td>
                          <td className="text-right mono">{cluster.size}</td>
                          <td>{cluster.members.join(', ')}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Beat>
            </div>
          )}

          {overTime.length > 1 && (
            <Beat
              title="How concentrated the centre was"
              aside="Each decade's highest betweenness on this roster — how much of the web ran through one actor. Decade windows only; an open regime span would smear the axis."
            >
              <SeriesLine
                points={overTime}
                format={(v) => v.toFixed(3)}
                label="top betweenness by decade"
              />
            </Beat>
          )}

          {decades.length > 0 && (
            <Beat
              title="Who led each decade"
              aside="The roster actor who sat between others, who had the most room to broker, and who was most connected — one row per computed decade."
            >
              <div className="scroll-x">
                <table className="rule-table" style={{ minWidth: 640 }}>
                  <thead>
                    <tr>
                      <th className="text-left">decade</th>
                      <th className="text-right">ranked</th>
                      <th className="text-left">sat between</th>
                      <th className="text-left">room to broker</th>
                      <th className="text-left">most connected</th>
                    </tr>
                  </thead>
                  <tbody>
                    {decades.map((row) => (
                      <tr key={`${row.window_start}..${row.window_end}`}>
                        <td className="mono">{row.label}</td>
                        <td className="text-right mono">{row.n}</td>
                        <td>
                          {row.broker
                            ? `${row.broker.name} · ${fig(row.broker.value)}`
                            : '—'}
                        </td>
                        <td>
                          {row.hole ? `${row.hole.name} · ${fig(row.hole.value)}` : '—'}
                        </td>
                        <td>
                          {row.degree
                            ? `${row.degree.name} · ${fig(row.degree.value)}`
                            : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Beat>
          )}

          {snap.method && <p className="figure-note mt-6">{snap.method}</p>}
        </>
      )}
    </div>
  )
}
