/** The second sheet of chart primitives (2026-08-15): the equity curve, the
 *  drawdown, ranked bars, the band heat, propensity and belief lines, and the
 *  payoff matrix. Same rules as Charts.tsx — inline SVG on the theme tokens,
 *  a viewBox that scales to its container, the diverging pair carrying SIGN
 *  only, one hue for magnitude, a legend whenever two series share a plot,
 *  and a hover layer on anything with a plot (a crosshair on lines, a per-mark
 *  tooltip on bars and cells). Nothing here picks a colour by eye: the pair
 *  and the series steps were re-validated against the white ground. */

import { useState } from 'react'

const PAD = { top: 10, right: 12, bottom: 22, left: 44 }

function lin(value: number, lo: number, hi: number, size: number): number {
  if (hi === lo) return size / 2
  return ((value - lo) / (hi - lo)) * size
}

function money(v: number): string {
  const abs = Math.abs(v)
  const s = abs >= 1e6 ? `${(abs / 1e6).toFixed(2)}M` : abs >= 1e3 ? `${(abs / 1e3).toFixed(0)}k` : abs.toFixed(0)
  return `${v < 0 ? '−' : ''}$${s}`
}

/** A share as a percentage — and an em dash for anything that is not a number.
 *  A missing field is a missing measurement, not a quantity: `NaN%` printed
 *  across the whole game-theory page on 2026-08-15 (a persisted payload from
 *  before a field rename), and it read as a broken product rather than as
 *  absent data. Undefined in, "—" out, everywhere pct is used. */
export function pct(v: number | null | undefined, digits = 1): string {
  return Number.isFinite(v as number) ? `${((v as number) * 100).toFixed(digits)}%` : '—'
}

function EmptyNote({ note }: { note: string }) {
  return (
    <p className="mono text-[11px] py-6" style={{ color: 'var(--muted)' }}>
      {note}
    </p>
  )
}

/** A time series with its OWN domain (an equity curve around $1M is a flat
 *  line on a zero-anchored axis), a baseline rule at `baseline`, and a
 *  crosshair tooltip. `format` renders the hovered value. */
export function SeriesLine({
  points,
  height = 200,
  width = 720,
  baseline,
  color = 'var(--accent)',
  format = (v: number) => v.toFixed(2),
  label,
  fillBelowBaseline = false,
}: {
  points: Array<{ x: string; y: number }>
  height?: number
  width?: number
  baseline?: number
  color?: string
  format?: (v: number) => string
  label?: string
  fillBelowBaseline?: boolean
}) {
  const [hover, setHover] = useState<number | null>(null)
  if (points.length < 2) return <EmptyNote note={label ?? 'not enough points to draw'} />
  const w = width - PAD.left - PAD.right
  const h = height - PAD.top - PAD.bottom
  const ys = points.map((p) => p.y)
  const lo = Math.min(...ys, baseline ?? Infinity)
  const hi = Math.max(...ys, baseline ?? -Infinity)
  const padY = (hi - lo || 1) * 0.06
  const y0 = lo - padY
  const y1 = hi + padY
  const px = (i: number) => PAD.left + lin(i, 0, points.length - 1, w)
  const py = (v: number) => PAD.top + h - lin(v, y0, y1, h)
  const path = points.map((p, i) => `${i ? 'L' : 'M'}${px(i)},${py(p.y)}`).join(' ')
  const area =
    baseline !== undefined && fillBelowBaseline
      ? `${path} L${px(points.length - 1)},${py(baseline)} L${px(0)},${py(baseline)} Z`
      : null
  const ticks = [y1, (y0 + y1) / 2, y0]
  const yearMarks = points
    .map((p, i) => ({ i, year: p.x.slice(0, 4) }))
    .filter((m, idx, arr) => idx === 0 || arr[idx - 1].year !== m.year)
    .filter((_, idx, arr) => arr.length <= 12 || idx % Math.ceil(arr.length / 12) === 0)

  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - rect.left) / rect.width) * width
    const i = Math.round(((x - PAD.left) / w) * (points.length - 1))
    setHover(Math.max(0, Math.min(points.length - 1, i)))
  }
  const hp = hover !== null ? points[hover] : null

  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width="100%"
      role="img"
      aria-label={label}
      onMouseMove={onMove}
      onMouseLeave={() => setHover(null)}
      style={{ cursor: 'crosshair' }}
    >
      {ticks.map((t) => (
        <g key={t}>
          <line x1={PAD.left} x2={width - PAD.right} y1={py(t)} y2={py(t)}
                stroke="var(--line)" strokeWidth={1} strokeDasharray="2 3" />
          <text x={PAD.left - 6} y={py(t) + 3} textAnchor="end" className="mono" fontSize={9}
                fill="var(--muted)">
            {format(t)}
          </text>
        </g>
      ))}
      {baseline !== undefined && (
        <line x1={PAD.left} x2={width - PAD.right} y1={py(baseline)} y2={py(baseline)}
              stroke="var(--rule-strong)" strokeWidth={1} />
      )}
      {yearMarks.map((m) => (
        <text key={m.i} x={px(m.i)} y={height - 6} textAnchor="middle" className="mono"
              fontSize={9} fill="var(--muted)">
          {m.year}
        </text>
      ))}
      {area && <path d={area} fill={color} opacity={0.12} />}
      <path d={path} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
      {hp && hover !== null && (
        <g>
          <line x1={px(hover)} x2={px(hover)} y1={PAD.top} y2={PAD.top + h}
                stroke="var(--rule-strong)" strokeWidth={1} strokeDasharray="3 3" />
          <circle cx={px(hover)} cy={py(hp.y)} r={4} fill={color} stroke="var(--ground)"
                  strokeWidth={2} />
          <g transform={`translate(${Math.min(px(hover) + 8, width - 150)}, ${PAD.top + 4})`}>
            <rect width={142} height={34} fill="var(--ground)" stroke="var(--rule-strong)" />
            <text x={6} y={13} className="mono" fontSize={10} fill="var(--text)">{hp.x}</text>
            <text x={6} y={27} className="mono" fontSize={10} fill="var(--text)">
              {format(hp.y)}
            </text>
          </g>
        </g>
      )}
    </svg>
  )
}

/** The equity curve: $ notional as the baseline, gains fill above it in
 *  the accent, the loss stretch is a drawdown the reader sees separately. */
export function EquityCurve({
  rows,
  notional,
  height = 220,
  width = 720,
}: {
  rows: Array<{ quarter_end: string; equity_usd: number }>
  notional: number
  height?: number
  width?: number
}) {
  return (
    <SeriesLine
      points={[{ x: rows[0]?.quarter_end ?? '', y: notional }, ...rows.map((r) => ({ x: r.quarter_end, y: r.equity_usd }))]}
      baseline={notional}
      height={height}
      width={width}
      format={money}
      label="paper equity, $ on the notional"
      fillBelowBaseline
    />
  )
}

/** Drawdown from the running peak, drawn as a filled area below zero in the
 *  alert hue — the loss side of the diverging pair, because that is what it is. */
export function Drawdown({
  rows,
  height = 120,
  width = 720,
}: {
  rows: Array<{ quarter_end: string; drawdown: number }>
  height?: number
  width?: number
}) {
  const [hover, setHover] = useState<number | null>(null)
  if (rows.length < 2) return <EmptyNote note="not enough quarters to draw a drawdown" />
  const w = width - PAD.left - PAD.right
  const h = height - PAD.top - PAD.bottom
  const worst = Math.max(...rows.map((r) => r.drawdown), 0.0001)
  const px = (i: number) => PAD.left + lin(i, 0, rows.length - 1, w)
  const py = (v: number) => PAD.top + lin(v, 0, worst, h)
  const line = rows.map((r, i) => `${i ? 'L' : 'M'}${px(i)},${py(r.drawdown)}`).join(' ')
  const area = `${line} L${px(rows.length - 1)},${py(0)} L${px(0)},${py(0)} Z`
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - rect.left) / rect.width) * width
    setHover(Math.max(0, Math.min(rows.length - 1, Math.round(((x - PAD.left) / w) * (rows.length - 1)))))
  }
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" aria-label="drawdown from peak"
         onMouseMove={onMove} onMouseLeave={() => setHover(null)} style={{ cursor: 'crosshair' }}>
      <line x1={PAD.left} x2={width - PAD.right} y1={py(0)} y2={py(0)} stroke="var(--rule-strong)" />
      <text x={PAD.left - 6} y={py(0) + 3} textAnchor="end" className="mono" fontSize={9} fill="var(--muted)">0</text>
      <text x={PAD.left - 6} y={py(worst) + 3} textAnchor="end" className="mono" fontSize={9} fill="var(--muted)">
        −{pct(worst, 0)}
      </text>
      <path d={area} fill="var(--alert)" opacity={0.18} />
      <path d={line} fill="none" stroke="var(--alert)" strokeWidth={1.5} />
      {hover !== null && (
        <g>
          <line x1={px(hover)} x2={px(hover)} y1={PAD.top} y2={PAD.top + h} stroke="var(--rule-strong)" strokeDasharray="3 3" />
          <g transform={`translate(${Math.min(px(hover) + 8, width - 150)}, ${PAD.top + 4})`}>
            <rect width={142} height={34} fill="var(--ground)" stroke="var(--rule-strong)" />
            <text x={6} y={13} className="mono" fontSize={10} fill="var(--text)">{rows[hover].quarter_end}</text>
            <text x={6} y={27} className="mono" fontSize={10} fill="var(--alert)">−{pct(rows[hover].drawdown, 2)}</text>
          </g>
        </g>
      )}
    </svg>
  )
}

/** Ranked horizontal bars: a label, a bar whose length is the magnitude, the
 *  figure. Signed values take the diverging pair; unsigned magnitudes take
 *  the accent alone. Thin marks, a 2px gap, direct labels — no legend needed
 *  for one measure. */
export function Bars({
  rows,
  signed = false,
  format = (v: number) => v.toFixed(2),
  max,
  onPick,
}: {
  rows: Array<{ key: string; label: string; value: number; sub?: string }>
  signed?: boolean
  format?: (v: number) => string
  max?: number
  onPick?: (key: string) => void
}) {
  if (!rows.length) return <EmptyNote note="nothing to rank" />
  const top = max ?? Math.max(...rows.map((r) => Math.abs(r.value)), 1e-9)
  return (
    <div className="space-y-1">
      {rows.map((r) => {
        const share = Math.min(1, Math.abs(r.value) / top)
        const color = signed ? (r.value >= 0 ? 'var(--accent)' : 'var(--alert)') : 'var(--accent)'
        return (
          <div key={r.key} className={`flex items-center gap-3 text-xs ${onPick ? 'cursor-pointer' : ''}`}
               onClick={onPick ? () => onPick(r.key) : undefined} title={r.sub ?? r.label}>
            <span className="w-44 shrink-0 truncate">{r.label}</span>
            <span className="relative flex-1 h-3" style={{ background: 'var(--panel)' }}>
              <span className="absolute top-0 bottom-0" style={{
                left: 0, width: `${share * 100}%`, background: color,
              }} />
            </span>
            <span className="mono w-16 text-right" style={{ color: signed ? color : 'var(--text)' }}>
              {format(r.value)}
            </span>
            {r.sub && <span className="mono w-24 text-right truncate" style={{ color: 'var(--muted)' }}>{r.sub}</span>}
          </div>
        )
      })}
    </div>
  )
}

/** Rows × periods of probability mass over intensity bands: one row per
 *  period (or per dyad), one cell per band, ink weight = share. Sequential:
 *  ONE hue, light→dark, with the value in the cell on hover. */
export function BandHeat({
  rows,
  bandLabels,
  rowLabel = (i: number) => `+${i + 1}q`,
  markers,
}: {
  rows: number[][]
  bandLabels: string[]
  rowLabel?: (index: number) => string
  markers?: number[]
}) {
  const [hover, setHover] = useState<[number, number] | null>(null)
  if (!rows.length) return <EmptyNote note="no fan" />
  const bands = bandLabels.length
  return (
    <div className="scroll-x">
      <table className="text-[11px] mono" style={{ borderCollapse: 'separate', borderSpacing: 2, minWidth: 420 }}>
        <thead>
          <tr>
            <th className="text-left font-normal pr-2" style={{ color: 'var(--muted)' }}></th>
            {bandLabels.map((b) => (
              <th key={b} className="font-normal px-1 text-center" style={{ color: 'var(--muted)', width: `${88 / bands}%` }}>{b}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              <td className="pr-2 whitespace-nowrap" style={{ color: 'var(--muted)' }}>{rowLabel(i)}</td>
              {row.map((share, j) => {
                const active = hover && hover[0] === i && hover[1] === j
                const marked = markers && markers[i] === j
                return (
                  <td key={j} className="text-center"
                      onMouseEnter={() => setHover([i, j])} onMouseLeave={() => setHover(null)}
                      style={{
                        background: `color-mix(in srgb, var(--accent) ${Math.round(share * 100)}%, var(--ground))`,
                        color: share > 0.5 ? 'var(--ground)' : 'var(--text)',
                        outline: marked ? '2px solid var(--rule-strong)' : active ? '1px solid var(--rule-strong)' : 'none',
                        height: 26, minWidth: 44,
                      }}
                      title={`${bandLabels[j]}: ${pct(share)}`}>
                    {active || share >= 0.2 ? pct(share, 0) : ''}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Two or more series over the band axis (escalation propensity by type),
 *  with a legend and direct end labels — identity never by colour alone. */
export function MultiLine({
  series,
  xLabels,
  height = 160,
  width = 480,
  yMax = 1,
  format = (v: number) => pct(v, 0),
}: {
  series: Array<{ name: string; values: number[]; color: string; dash?: string }>
  xLabels: string[]
  height?: number
  width?: number
  yMax?: number
  format?: (v: number) => string
}) {
  const [hover, setHover] = useState<number | null>(null)
  if (!series.length || !series[0].values.length) return <EmptyNote note="no series" />
  const w = width - PAD.left - PAD.right
  const h = height - PAD.top - PAD.bottom
  const n = xLabels.length
  const px = (i: number) => PAD.left + lin(i, 0, Math.max(n - 1, 1), w)
  const py = (v: number) => PAD.top + h - lin(v, 0, yMax, h)
  const onMove = (e: React.MouseEvent<SVGSVGElement>) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const x = ((e.clientX - rect.left) / rect.width) * width
    setHover(Math.max(0, Math.min(n - 1, Math.round(((x - PAD.left) / w) * (n - 1)))))
  }
  return (
    <div>
      <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img" onMouseMove={onMove}
           onMouseLeave={() => setHover(null)} style={{ cursor: 'crosshair' }}>
        {[0, 0.5, 1].map((t) => (
          <g key={t}>
            <line x1={PAD.left} x2={width - PAD.right} y1={py(t * yMax)} y2={py(t * yMax)} stroke="var(--line)" strokeDasharray="2 3" />
            <text x={PAD.left - 6} y={py(t * yMax) + 3} textAnchor="end" className="mono" fontSize={9} fill="var(--muted)">{format(t * yMax)}</text>
          </g>
        ))}
        {xLabels.map((l, i) => (
          <text key={l + i} x={px(i)} y={height - 6} textAnchor="middle" className="mono" fontSize={9} fill="var(--muted)">{l}</text>
        ))}
        {series.map((s) => (
          <g key={s.name}>
            <path d={s.values.map((v, i) => `${i ? 'L' : 'M'}${px(i)},${py(v)}`).join(' ')} fill="none"
                  stroke={s.color} strokeWidth={2} strokeDasharray={s.dash} />
            {s.values.map((v, i) => (
              <circle key={i} cx={px(i)} cy={py(v)} r={hover === i ? 4 : 2.5} fill={s.color} stroke="var(--ground)" strokeWidth={1.5} />
            ))}
          </g>
        ))}
        {hover !== null && (
          <g transform={`translate(${Math.min(px(hover) + 8, width - 170)}, ${PAD.top + 4})`}>
            <rect width={160} height={14 + 13 * series.length} fill="var(--ground)" stroke="var(--rule-strong)" />
            <text x={6} y={11} className="mono" fontSize={10} fill="var(--text)">{xLabels[hover]}</text>
            {series.map((s, k) => (
              <text key={s.name} x={6} y={24 + 13 * k} className="mono" fontSize={10} fill="var(--text)">
                {s.name}: {format(s.values[hover] ?? 0)}
              </text>
            ))}
          </g>
        )}
      </svg>
      <div className="flex flex-wrap gap-4 mono text-[11px] mt-1" style={{ color: 'var(--muted)' }}>
        {series.map((s) => (
          <span key={s.name} className="inline-flex items-center gap-2">
            <span style={{ width: 18, height: 0, borderTop: `2px ${s.dash ? 'dashed' : 'solid'} ${s.color}` }} />
            {s.name}
          </span>
        ))}
      </div>
    </div>
  )
}

/** The stage game at the opening state: A's and B's payoffs per joint action
 *  (payoff + discounted continuation), with each side's equilibrium mixture
 *  in the margins. Ink weight marks the mixture; the numbers are the game. */
export function PayoffMatrix({
  matrix,
  sides,
}: {
  matrix: { a: number[][]; b: number[][]; actions: string[]; mix_a: number[]; mix_b: number[]; value: number; type: string }
  sides: [string, string]
}) {
  const acts = matrix.actions
  const weight = (p: number) => `color-mix(in srgb, var(--accent) ${Math.round(p * 70)}%, var(--ground))`
  return (
    <div className="scroll-x">
      <table className="text-[11px] mono" style={{ borderCollapse: 'separate', borderSpacing: 2 }}>
        <thead>
          <tr>
            <th className="font-normal text-left pr-2" style={{ color: 'var(--muted)' }}>{sides[0]} ↓ / {sides[1]} →</th>
            {acts.map((b, j) => (
              <th key={b} className="font-normal px-2 py-1" style={{ background: weight(matrix.mix_b[j]), minWidth: 96 }}>
                {b} <span style={{ color: 'var(--muted)' }}>{pct(matrix.mix_b[j], 0)}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {acts.map((a, i) => (
            <tr key={a}>
              <th className="font-normal text-left pr-2 py-1" style={{ background: weight(matrix.mix_a[i]) }}>
                {a} <span style={{ color: 'var(--muted)' }}>{pct(matrix.mix_a[i], 0)}</span>
              </th>
              {acts.map((_, j) => (
                <td key={j} className="px-2 py-1 text-center" style={{ border: '1px solid var(--line)' }}
                    title={`${sides[0]} ${a} / ${sides[1]} ${acts[j]}`}>
                  <span style={{ color: matrix.a[i][j] >= 0 ? 'var(--text)' : 'var(--alert)' }}>{matrix.a[i][j].toFixed(2)}</span>
                  <span style={{ color: 'var(--muted)' }}> , </span>
                  <span style={{ color: matrix.b[i][j] >= 0 ? 'var(--text)' : 'var(--alert)' }}>{matrix.b[i][j].toFixed(2)}</span>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mono text-[11px] mt-1" style={{ color: 'var(--muted)' }}>
        cells: {sides[0]}'s payoff , {sides[1]}'s payoff (stage + discounted continuation) for a {matrix.type} type ·
        value to {sides[0]} {matrix.value.toFixed(3)} · margins: equilibrium mixtures
      </p>
    </div>
  )
}

/** A stat tile row: label over figure, tabular, no chart — the hero-number
 *  form for a handful of headline figures. */
export function Tiles({ items }: { items: Array<{ label: string; value: string; tone?: 'gain' | 'loss' | 'plain'; sub?: string }> }) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-x-6 gap-y-4">
      {items.map((it) => (
        <div key={it.label} className="flex flex-col">
          <span className="kicker">{it.label}</span>
          <span className="figure text-2xl leading-tight" style={{
            color: it.tone === 'gain' ? 'var(--accent)' : it.tone === 'loss' ? 'var(--alert)' : 'var(--text)',
          }}>
            {it.value}
          </span>
          {it.sub && <span className="mono text-[11px]" style={{ color: 'var(--muted)' }}>{it.sub}</span>}
        </div>
      ))}
    </div>
  )
}
