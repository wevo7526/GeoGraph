import { useEffect, useMemo, useState } from 'react'
import { getBacktest, getForward } from '../api'
import { useRegionLabel } from '../regions'
import type { BacktestLedger, BacktestRow, ForwardView } from '../types'

/** The paper model, whole: the walk-forward ledger (the rule run through
 *  history with no hindsight) and the standing forward book beside the
 *  long-horizon pressure — which always carries its boundary statement.
 *  Every number is the backend's; this page only draws them. Not advice. */

const fmtUsd = (v: number) =>
  v.toLocaleString('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 })
const fmtPct = (v: number, digits = 1) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(digits)}%`

function StatTile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="border p-4" style={{ borderColor: 'var(--line)', background: 'var(--panel)' }}>
      <p className="mono text-[10px] uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
        {label}
      </p>
      <p className="mono text-lg mt-1" style={{ color: 'var(--text)' }}>{value}</p>
      {sub && (
        <p className="mono text-[10px] mt-1" style={{ color: 'var(--muted)' }}>{sub}</p>
      )}
    </div>
  )
}

/** Single-series equity line. One axis, recessive grid, hover crosshair with
 *  a tooltip; the ledger table below is the accessible twin. */
function EquityCurve({ rows }: { rows: BacktestRow[] }) {
  const [hover, setHover] = useState<number | null>(null)
  const W = 720
  const H = 220
  const PAD = { left: 56, right: 16, top: 12, bottom: 24 }

  const geometry = useMemo(() => {
    const values = rows.map((r) => r.equity_usd)
    const lo = Math.min(...values, 1_000_000)
    const hi = Math.max(...values, 1_000_000)
    const span = hi - lo || 1
    const x = (i: number) =>
      PAD.left + ((W - PAD.left - PAD.right) * i) / Math.max(rows.length - 1, 1)
    const y = (v: number) =>
      PAD.top + (H - PAD.top - PAD.bottom) * (1 - (v - lo) / span)
    const path = rows.map((r, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(r.equity_usd).toFixed(1)}`).join(' ')
    return { lo, hi, x, y, path }
  }, [rows])

  if (rows.length < 2) return null
  const gridLevels = [0, 0.5, 1].map((t) => geometry.lo + t * (geometry.hi - geometry.lo))
  const hovered = hover === null ? null : rows[hover]

  return (
    <div className="relative">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full"
        style={{ display: 'block' }}
        onMouseLeave={() => setHover(null)}
        onMouseMove={(e) => {
          const rect = e.currentTarget.getBoundingClientRect()
          const px = ((e.clientX - rect.left) / rect.width) * W
          const i = Math.round(
            ((px - PAD.left) / (W - PAD.left - PAD.right)) * (rows.length - 1),
          )
          setHover(Math.max(0, Math.min(rows.length - 1, i)))
        }}
      >
        {gridLevels.map((level) => (
          <g key={level}>
            <line
              x1={PAD.left} x2={W - PAD.right}
              y1={geometry.y(level)} y2={geometry.y(level)}
              stroke="var(--line)" strokeWidth={1}
            />
            <text
              x={PAD.left - 8} y={geometry.y(level) + 3} textAnchor="end"
              className="mono" fontSize={9} fill="var(--muted)"
            >
              {fmtUsd(level)}
            </text>
          </g>
        ))}
        <path d={geometry.path} fill="none" stroke="var(--accent)" strokeWidth={2} />
        {hovered && (
          <g>
            <line
              x1={geometry.x(hover!)} x2={geometry.x(hover!)}
              y1={PAD.top} y2={H - PAD.bottom}
              stroke="var(--muted)" strokeWidth={1} strokeDasharray="2,3"
            />
            <circle
              cx={geometry.x(hover!)} cy={geometry.y(hovered.equity_usd)} r={4}
              fill="var(--accent)" stroke="var(--ground)" strokeWidth={2}
            />
          </g>
        )}
        <text x={PAD.left} y={H - 8} className="mono" fontSize={9} fill="var(--muted)">
          {rows[0].quarter_end}
        </text>
        <text x={W - PAD.right} y={H - 8} textAnchor="end" className="mono" fontSize={9} fill="var(--muted)">
          {rows[rows.length - 1].quarter_end}
        </text>
      </svg>
      {hovered && (
        <div
          className="absolute mono text-[10px] border px-2 py-1 pointer-events-none"
          style={{
            left: `${(geometry.x(hover!) / W) * 100}%`,
            top: 0,
            transform: hover! > rows.length / 2 ? 'translateX(-105%)' : 'translateX(8px)',
            borderColor: 'var(--line)',
            background: 'var(--panel)',
            color: 'var(--text)',
          }}
        >
          <div>{hovered.quarter_end}</div>
          <div>{fmtUsd(hovered.equity_usd)}</div>
          <div style={{ color: 'var(--muted)' }}>
            q {fmtPct(hovered.quarter_return)} · p {hovered.escalation_likelihood.toFixed(2)}
          </div>
        </div>
      )}
    </div>
  )
}

export default function TradingPage({
  region,
}: {
  region: string
  onNavigate: (route: string) => void
}) {
  const [ledger, setLedger] = useState<BacktestLedger | null | undefined>(undefined)
  const [forward, setForward] = useState<ForwardView | null | undefined>(undefined)
  const regionLabel = useRegionLabel(region)

  useEffect(() => {
    setLedger(undefined)
    setForward(undefined)
    getBacktest(region).then(setLedger)
    getForward(region).then(setForward)
  }, [region])

  const rows = ledger?.rows ?? []
  const summary = ledger?.summary ?? null

  return (
    <div className="max-w-5xl mx-auto px-6 py-10">
      <p className="mono text-xs uppercase tracking-widest" style={{ color: 'var(--muted)' }}>
        Trading · {regionLabel.toUpperCase()} · paper model
      </p>

      {/* ── The backtest: the rule run through history ──────────────────── */}
      <h2 className="text-xl mt-6" style={{ color: 'var(--text)' }}>
        Walk-forward backtest
      </h2>
      <p className="mono text-[10px] mt-1" style={{ color: 'var(--muted)' }}>
        forecast recomputed each quarter end from the events that existed then ·
        entered at the first close after cutoff, marked at the next quarter end ·
        thin, pooled-prior and partially-enterable quarters are recorded skips —
        only fully-entered books compound
        {ledger?.computed_at && (
          <>
            {' · computed '}
            <span style={{ color: 'var(--text)' }}>
              {ledger.computed_at.slice(0, 10)}
            </span>
          </>
        )}
      </p>

      {ledger === undefined ? (
        <p className="mt-6 text-sm mono" style={{ color: 'var(--muted)' }}>Reaching the archive…</p>
      ) : ledger === null ? (
        <p className="mt-6 text-sm" style={{ color: 'var(--muted)' }}>
          The trading surface is unreachable — the API may be down, or this
          region has no paper model.
        </p>
      ) : rows.length === 0 ? (
        <p className="mt-6 text-sm" style={{ color: 'var(--muted)' }}>
          {ledger.note ?? 'No persisted backtest for this region yet.'}
        </p>
      ) : (
        <>
          {summary && (
            <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mt-6">
              <StatTile label="Final equity" value={fmtUsd(summary.final_equity_usd)}
                sub={`on ${fmtUsd(summary.notional_usd)} notional`} />
              <StatTile label="Total return" value={fmtPct(summary.total_return)} />
              <StatTile label="Hit rate" value={`${(summary.hit_rate * 100).toFixed(0)}%`}
                sub="quarters positive" />
              <StatTile label="Max drawdown" value={fmtPct(-summary.max_drawdown)} />
              <StatTile label="Quarters" value={String(summary.quarters_traded)} sub="traded" />
            </div>
          )}
          <div className="border mt-4 p-4" style={{ borderColor: 'var(--line)', background: 'var(--panel)' }}>
            <EquityCurve rows={rows} />
          </div>
          <details className="mt-3">
            <summary className="mono text-xs cursor-pointer" style={{ color: 'var(--muted)' }}>
              Quarterly ledger ({rows.length} rows)
            </summary>
            <div className="overflow-x-auto mt-2 border" style={{ borderColor: 'var(--line)' }}>
              <table className="w-full mono text-xs">
                <thead>
                  <tr style={{ color: 'var(--muted)' }}>
                    {['Quarter', 'p(escalation)', 'Episodes', 'P&L', 'Return', 'Equity'].map((h) => (
                      <th key={h} className="text-left px-3 py-2 border-b" style={{ borderColor: 'var(--line)' }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody style={{ color: 'var(--text)' }}>
                  {rows.map((r) => (
                    <tr key={r.quarter_end}>
                      <td className="px-3 py-1.5">{r.quarter_end}</td>
                      <td className="px-3 py-1.5">{r.escalation_likelihood.toFixed(3)}</td>
                      <td className="px-3 py-1.5">{r.episodes}</td>
                      <td className="px-3 py-1.5">{fmtUsd(r.pnl_usd)}</td>
                      <td className="px-3 py-1.5">{fmtPct(r.quarter_return, 2)}</td>
                      <td className="px-3 py-1.5">{fmtUsd(r.equity_usd)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}

      {/* ── The forward view: standing book + long-horizon pressure ─────── */}
      <h2 className="text-xl mt-12" style={{ color: 'var(--text)' }}>
        The standing book
      </h2>
      {forward === undefined ? (
        <p className="mt-4 text-sm mono" style={{ color: 'var(--muted)' }}>Reaching the archive…</p>
      ) : forward === null ? (
        <p className="mt-4 text-sm" style={{ color: 'var(--muted)' }}>
          No frozen near-term forecast for this region yet — the forward book
          is built from a frozen call, never computed on request.
        </p>
      ) : (
        <>
          <p className="mono text-[11px] mt-1" style={{ color: 'var(--muted)' }}>
            data through <span style={{ color: 'var(--text)' }}>{forward.forecast.as_of}</span>
            {' · escalation likelihood '}
            <span style={{ color: 'var(--text)' }}>
              {(forward.forecast.escalation_likelihood * 100).toFixed(1)}%
            </span>
            {' · marked at the latest close the panel holds'}
          </p>
          {forward.book === null ? (
            <p className="mt-4 text-sm mono" style={{ color: 'var(--muted)' }}>
              Book unmarked: {forward.book_unavailable}
            </p>
          ) : (
            <>
              {/* A book with NOTHING entered is not a book at $0 — tiles
                  reading "$0 P&L" over all-skipped positions describe a flat
                  book, which is false. Say what actually happened (usually:
                  the panel holds no closes after the entry date yet) and let
                  the table carry the per-ticker reasons. */}
              {forward.book.positions.every((p) => p.status !== 'marked') ? (
                <p className="mt-5 text-sm max-w-xl" style={{ color: 'var(--alert)' }}>
                  Nothing entered yet — {forward.book.positions[0]?.reason ?? 'no position could be opened'}.
                  {' '}The book opens at the first close after the frozen call; it marks
                  itself as soon as the panel holds one.
                </p>
              ) : (
                <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 mt-5 max-w-xl">
                  <StatTile label="Paper P&L" value={fmtUsd(forward.book.pnl_usd)}
                    sub={`on ${fmtUsd(forward.book.notional_usd)} notional`} />
                  <StatTile label="Return" value={fmtPct(forward.book.return_on_notional, 2)} />
                  <StatTile label="Deployed" value={fmtUsd(forward.book.deployed_usd)} />
                </div>
              )}
              <div className="overflow-x-auto mt-4 border max-w-3xl" style={{ borderColor: 'var(--line)' }}>
                <table className="w-full mono text-xs">
                  <thead>
                    <tr style={{ color: 'var(--muted)' }}>
                      {['Ticker', 'Weight', 'Entry', 'Mark', 'P&L'].map((h) => (
                        <th key={h} className="text-left px-3 py-2 border-b" style={{ borderColor: 'var(--line)' }}>
                          {h}
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody style={{ color: 'var(--text)' }}>
                    {forward.book.positions.map((p) => (
                      <tr key={p.ticker}>
                        <td className="px-3 py-1.5">{p.ticker}</td>
                        <td className="px-3 py-1.5">{p.weight >= 0 ? '+' : ''}{p.weight.toFixed(3)}</td>
                        {p.status === 'marked' ? (
                          <>
                            <td className="px-3 py-1.5">{p.entry?.toFixed(2)} <span style={{ color: 'var(--muted)' }}>{p.entry_date}</span></td>
                            <td className="px-3 py-1.5">{p.mark?.toFixed(2)} <span style={{ color: 'var(--muted)' }}>{p.mark_date}</span></td>
                            <td className="px-3 py-1.5">{fmtUsd(p.pnl_usd ?? 0)}</td>
                          </>
                        ) : (
                          <td className="px-3 py-1.5" colSpan={3} style={{ color: 'var(--muted)' }}>
                            skipped — {p.reason}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {forward.pressure && (
            <div className="mt-10 max-w-3xl">
              <h3 className="text-lg" style={{ color: 'var(--text)' }}>Long-horizon pressure</h3>
              {forward.pressure.windows.length > 0 && (
                <div className="flex flex-wrap gap-2 mt-3">
                  {forward.pressure.windows.map((w) => (
                    <span
                      key={`${w.start}-${w.end}`}
                      className="mono text-[10px] uppercase tracking-wider border px-2 py-1"
                      style={{
                        borderColor: w.level === 'high' ? 'var(--alert)' : 'var(--line)',
                        color: w.level === 'high' ? 'var(--alert)' : 'var(--muted)',
                      }}
                    >
                      {w.start}–{w.end} · {w.level}
                    </span>
                  ))}
                </div>
              )}
              <p className="mt-4 text-sm leading-relaxed italic" style={{ color: 'var(--muted)' }}>
                {forward.pressure.boundary_statement}
              </p>
            </div>
          )}
        </>
      )}

      {(ledger?.method || forward?.book?.method) && (
        <p className="mono text-[10px] leading-relaxed mt-12 max-w-4xl" style={{ color: 'var(--muted)' }}>
          {ledger?.method ?? forward?.book?.method}
        </p>
      )}
    </div>
  )
}
