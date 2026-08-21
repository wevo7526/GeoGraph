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
  return <p className="note-empty">{note}</p>
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
      <p className="figure-note" style={{ marginTop: '0.5rem' }}>
        cells: {sides[0]}'s payoff , {sides[1]}'s payoff (stage + discounted continuation) for a {matrix.type} type ·
        value to {sides[0]} <span className="num">{matrix.value.toFixed(3)}</span> · margins: equilibrium mixtures
      </p>
    </div>
  )
}

// ── the four figures the game and the market pages were missing ─────────────
//
// What the game had until 2026-08-17: a table of percentages in cells, a 12×4
// table of expected bands to two decimals, a payoff matrix, and two line charts
// that on the default view drew flat lines at 100% and 0%. What the market had:
// bars of the median, all six within half a percent of each other, while the
// payload's interquartile ranges ran −1.9% to +3.9%. Neither page had a picture
// of the thing it is about. These four are that picture, and each replaces
// something the pages were printing as a table.

/** The band a distribution's `p`-th quantile falls in, treating band i as
 *  spanning [i−0.5, i+0.5] so the ribbon is smooth rather than a staircase. */
function bandQuantile(dist: number[], p: number): number {
  let cumulative = 0
  for (let i = 0; i < dist.length; i++) {
    const mass = dist[i]
    if (mass <= 0) continue
    if (cumulative + mass >= p) return i - 0.5 + (p - cumulative) / mass
    cumulative += mass
  }
  return dist.length - 1
}

/** THE FAN, AS A FAN — the forecast's own shape, replacing `BandHeat`'s grid of
 *  percentages and the "expected band by quarter: 2.41 → 2.28 → 2.26 → 2.23"
 *  line under it. A reader cannot see a trajectory in a table of cells; the
 *  whole claim of the game is where a pair sits now, where the mass moves, and
 *  how wide the uncertainty is around it.
 *
 *  The two shades are the middle 50% and 80% of the game's mass, the line is
 *  the median course, the dot is where the pair opens, and the dashed rule is
 *  the band the headline probability counts above — so the number in the call
 *  and the picture are visibly the same claim. */
export function FanRibbon({
  marginal,
  bandLabels,
  openingBand,
  typicalBand,
  height = 250,
  width = 820,
}: {
  marginal: number[][]
  bandLabels: string[]
  openingBand: number
  typicalBand?: number
  height?: number
  width?: number
}) {
  if (!marginal.length || !bandLabels.length) return <EmptyNote note="no fan to draw" />
  const gutter = 150
  const pad = { top: 16, right: 18, bottom: 26 }
  const plotW = width - gutter - pad.right
  const plotH = height - pad.top - pad.bottom
  const bands = bandLabels.length
  // Band 0 at the bottom: up the page is more departure, which is the direction
  // the alert hue means everywhere else on the surface.
  const py = (band: number) =>
    pad.top + plotH - ((band + 0.5) / bands) * plotH
  const px = (i: number) => gutter + (i / marginal.length) * plotW

  const quantiles = marginal.map((dist) => ({
    p10: bandQuantile(dist, 0.1),
    p25: bandQuantile(dist, 0.25),
    p50: bandQuantile(dist, 0.5),
    p75: bandQuantile(dist, 0.75),
    p90: bandQuantile(dist, 0.9),
  }))
  // The ribbon starts AT the opening band — a fan that begins spread at period
  // one implies uncertainty about where the pair is now, which is measured.
  const ribbon = (lo: 'p10' | 'p25', hi: 'p90' | 'p75') =>
    [
      `M${px(0)},${py(openingBand)}`,
      ...quantiles.map((q, i) => `L${px(i + 1)},${py(q[hi])}`),
      ...[...quantiles].reverse().map((q, i) =>
        `L${px(quantiles.length - i)},${py(q[lo])}`),
      'Z',
    ].join(' ')

  return (
    <svg viewBox={`0 0 ${width} ${height}`} width="100%" role="img"
         aria-label="the fan: where this pair's departure from its own baseline is heading">
      {bandLabels.map((label, i) => (
        <g key={label + i}>
          <line x1={gutter} x2={width - pad.right} y1={py(i)} y2={py(i)}
                stroke="var(--line)" strokeWidth={1} strokeDasharray="2 4" />
          <text x={gutter - 10} y={py(i) + 3.5} textAnchor="end" fontSize={11}
                fill="var(--muted)" fontFamily="Georgia, serif">
            {label}
          </text>
        </g>
      ))}
      {typicalBand !== undefined && typicalBand + 1 < bands && (
        <line x1={gutter} x2={width - pad.right}
              y1={py(typicalBand + 0.5)} y2={py(typicalBand + 0.5)}
              stroke="var(--alert)" strokeWidth={1} strokeDasharray="5 4" />
      )}
      <path d={ribbon('p10', 'p90')} fill="var(--accent)" opacity={0.12} />
      <path d={ribbon('p25', 'p75')} fill="var(--accent)" opacity={0.26} />
      <path
        d={[
          `M${px(0)},${py(openingBand)}`,
          ...quantiles.map((q, i) => `L${px(i + 1)},${py(q.p50)}`),
        ].join(' ')}
        fill="none" stroke="var(--accent)" strokeWidth={2.25} strokeLinejoin="round"
      />
      <circle cx={px(0)} cy={py(openingBand)} r={5} fill="var(--alert)"
              stroke="var(--ground)" strokeWidth={2} />
      <text x={px(0)} y={height - 8} textAnchor="middle" className="mono" fontSize={10}
            fill="var(--muted)">now</text>
      {quantiles.map((_, i) => (
        <text key={i} x={px(i + 1)} y={height - 8} textAnchor="middle" className="mono"
              fontSize={10} fill="var(--muted)">
          +{i + 1}q
        </text>
      ))}
    </svg>
  )
}

/** ONE COURSE OF PLAY, AS TWO LANES — replacing the machine string the pages
 *  printed twenty times ("130 courses, modal withhold/withhold →
 *  withhold/withhold → withhold/withhold → withhold/withhold") and the mono
 *  step table under each scenario.
 *
 *  Colour is the action's PLACE in the family's own action space, never a
 *  hardcoded word: index 2 presses (escalate, or withhold) and takes the alert,
 *  index 0 concedes (de-escalate, or commit) and takes the accent. The beliefs
 *  row is the strip's second line rather than a separate chart, because what a
 *  side believes is a property of the step it is taking. */
const SHORT_ACTION: Record<string, string> = {
  'de-escalate': 'step back',
  escalate: 'escalate',
  hold: 'hold',
  commit: 'commit',
  affirm: 'affirm',
  withhold: 'withhold',
  press: 'press',
  ease: 'ease',
}

export function CourseStrip({
  steps,
  sides,
  actions,
  typeName,
}: {
  steps: Array<{
    period: number
    action_a: string
    action_b: string
    belief_a?: number
    belief_b?: number
  }>
  sides: [string, string]
  actions?: string[]
  typeName?: string
}) {
  if (!steps.length) return <EmptyNote note="no course to draw" />
  const order = actions ?? ['de-escalate', 'hold', 'escalate']
  const tone = (action: string) => {
    const i = order.indexOf(action)
    return i === 2 ? 'var(--alert)' : i === 0 ? 'var(--accent)' : 'var(--muted)'
  }
  const hasBeliefs = steps.some((s) => s.belief_a !== undefined)
  return (
    <div className="scroll-x">
      <table className="course-strip">
        <thead>
          <tr>
            <th />
            {steps.map((s) => (
              <th key={s.period} className="kicker">+{s.period}q</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {([0, 1] as const).map((side) => (
            <tr key={side}>
              <th scope="row">{sides[side]}</th>
              {steps.map((s) => {
                const action = side === 0 ? s.action_a : s.action_b
                return (
                  <td key={s.period}>
                    <span className="course-cell" style={{ background: tone(action) }}>
                      {SHORT_ACTION[action] ?? action}
                    </span>
                  </td>
                )
              })}
            </tr>
          ))}
          {hasBeliefs && (
            <tr className="course-beliefs">
              <th scope="row">
                each reads the other as {typeName ?? 'resolute'}
              </th>
              {steps.map((s) => (
                <td key={s.period} className="mono">
                  {s.belief_a !== undefined ? pct(s.belief_a, 0) : '—'}
                </td>
              ))}
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}

/** THE MATCHUP — replacing four tiles that answered nothing ("capability band
 *  0 · CINC ratio 0.13", "beliefs (resolute) 90% / 90%", "opening departure ·
 *  latest 16.94 vs the pair's scale 10.54", "kernel · dynamics-mena@bd86fa…").
 *
 *  Every figure here is the SAME FIELD as the tile it replaces. The difference
 *  is that a label is the reader's question and the value is its answer: a
 *  capability ratio of 0.13 is "about an eighth", and a hash belongs in a title
 *  attribute rather than on the page. */
export function Matchup({
  sides,
  standing,
  posture,
  capability,
  beliefs,
  typeName,
  opening,
  kernel,
}: {
  sides: [string, string]
  standing: string | null
  posture: string | null
  capability: { ratio?: number | null; source: string }
  beliefs: { a: number; b: number; source: string; quarters_observed?: number }
  typeName: string
  opening: { label: string; latest: number; scale: number; band: number; typical: number }
  kernel: { own: boolean; model?: string | null }
}) {
  const ratio = capability.ratio ?? null
  // "About eight times" reads; "0.1324" is the same fact in the estimator's
  // units. min/max, so the stronger side is always the multiple.
  const multiple = ratio && ratio > 0 ? 1 / ratio : null
  const strongerIsA = true
  const share = ratio != null ? 1 / (1 + ratio) : 0.5
  const swing = opening.scale > 0 ? opening.latest / opening.scale : null

  return (
    <div className="matchup">
      <div className="matchup-side">{sides[0]}</div>
      <div className="matchup-vs kicker">vs</div>
      <div className="matchup-side matchup-side--right">{sides[1]}</div>

      {(standing || posture) && (
        <p className="matchup-span">
          {[standing, posture].filter(Boolean).join(' · ')}
        </p>
      )}

      {multiple !== null ? (
        <>
          <div className="matchup-figure">about {multiple.toFixed(0)}×</div>
          <div className="matchup-label kicker">material capability</div>
          <div className="matchup-figure matchup-figure--right">1×</div>
          <div className="matchup-bar" aria-hidden="true">
            <span style={{ width: `${(strongerIsA ? share : 1 - share) * 100}%` }} />
            <span style={{ width: `${(strongerIsA ? 1 - share : share) * 100}%` }} />
          </div>
        </>
      ) : (
        <p className="matchup-span">
          No capability estimate for either side, so the game opens them even.
        </p>
      )}

      <div className="matchup-figure">{pct(beliefs.a, 0)}</div>
      <div className="matchup-label kicker">reads the other as {typeName}</div>
      <div className="matchup-figure matchup-figure--right">{pct(beliefs.b, 0)}</div>

      <p className="matchup-span">
        <strong>
          {opening.band > opening.typical
            ? 'Opens above its own usual level'
            : 'Opens at its own usual level'}
        </strong>
        {swing ? ` — this quarter's departure is ${swing.toFixed(1)}× the pair's usual swing` : ''}
        {beliefs.source === 'bayes_filter' && beliefs.quarters_observed
          ? `. Beliefs filtered from ${beliefs.quarters_observed} observed quarters`
          : ''}
        {kernel.own ? '. Solved on this pair’s own transition table' : '. Solved on the region’s counted transition table'}
        <span className="matchup-provenance" title={kernel.model ?? undefined}>.</span>
      </p>
    </div>
  )
}

/** THE TRANSMISSION MAP, WITH ITS SPREAD — replacing bars of the median.
 *
 *  MENA's medians all sit within half a percent of zero; the middle half of
 *  outcomes for the same cells runs −1.9% to +3.9% (2-year) and −1.4% to +7.0%
 *  (Dubai). Bars of the median drew six near-identical stubs and hid both the
 *  dispersion and the fact that the median is barely distinguishable from
 *  nothing. Dot-and-whisker says all of it at once, and thin cells are drawn in
 *  grey rather than dropped — an absent measurement is a fact too. */
export function DotWhisker({
  rows,
  format = (v: number) => `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(2)}%`,
  onPick,
  height,
}: {
  rows: Array<{
    key: string
    label: string
    median: number
    p25: number
    p75: number
    n: number
    thin?: boolean
  }>
  format?: (v: number) => string
  onPick?: (key: string) => void
  height?: number
}) {
  const [hover, setHover] = useState<string | null>(null)
  if (!rows.length) return <EmptyNote note="nothing measured yet" />
  // WIDE ENOUGH FOR THE LONGEST NAME. At 170 the market names were clipped by
  // the viewBox — "Tadawul All Share Index (Saudi Arabia)" rendered as "l Share
  // Index (Saudi Arabia)", which reads as a broken chart rather than a long
  // label (2026-08-17). Measured off the rows rather than fixed, so a pack with
  // shorter names does not pay for one with longer.
  const gutter = Math.min(
    340,
    Math.max(170, ...rows.map((r) => r.label.length * 6.6 + (r.thin ? 46 : 14))),
  )
  const width = 620 + gutter
  const rowH = 27
  const pad = { top: 22, bottom: 24, right: 62 }
  const h = height ?? pad.top + rows.length * rowH + pad.bottom
  const span = Math.max(
    ...rows.map((r) => Math.max(Math.abs(r.p25), Math.abs(r.p75), Math.abs(r.median))),
    0.005,
  )
  const plotW = width - gutter - pad.right
  const x = (v: number) => gutter + ((v + span) / (2 * span)) * plotW
  const y = (i: number) => pad.top + i * rowH + rowH / 2
  const ticks = [-span, -span / 2, 0, span / 2, span]

  return (
    <div className="scroll-x">
      <svg viewBox={`0 0 ${width} ${h}`} width="100%" role="img"
           aria-label="measured market response by market: median and the middle half of outcomes">
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={pad.top - 6} y2={h - pad.bottom}
                  stroke={t === 0 ? 'var(--rule-strong)' : 'var(--line)'}
                  strokeWidth={1} strokeDasharray={t === 0 ? undefined : '2 4'} />
            <text x={x(t)} y={pad.top - 11} textAnchor="middle" className="mono"
                  fontSize={10} fill="var(--muted)">
              {t === 0 ? '0' : `${t > 0 ? '+' : '−'}${Math.abs(t * 100).toFixed(1)}%`}
            </text>
          </g>
        ))}
        {rows.map((r, i) => {
          const ink = r.thin ? 'var(--muted)' : r.median >= 0 ? 'var(--accent)' : 'var(--alert)'
          const band = r.thin ? 'var(--line)' : 'var(--accent)'
          return (
            <g key={r.key} onMouseEnter={() => setHover(r.key)} onMouseLeave={() => setHover(null)}
               onClick={onPick ? () => onPick(r.key) : undefined}
               style={{ cursor: onPick ? 'pointer' : 'default' }}>
              <rect x={0} y={y(i) - rowH / 2} width={width} height={rowH}
                    fill={hover === r.key ? 'var(--panel)' : 'transparent'} />
              <text x={gutter - 12} y={y(i) + 4} textAnchor="end" fontSize={12.5}
                    fill={r.thin ? 'var(--muted)' : 'var(--text)'} fontFamily="Georgia, serif">
                {r.label}{r.thin ? ' (thin)' : ''}
              </text>
              <line x1={x(r.p25)} x2={x(r.p75)} y1={y(i)} y2={y(i)}
                    stroke={band} strokeWidth={6} opacity={r.thin ? 0.5 : 0.32}
                    strokeLinecap="butt" />
              <circle cx={x(r.median)} cy={y(i)} r={4.5} fill={ink}
                      stroke="var(--ground)" strokeWidth={1.5} />
              <text x={width - pad.right + 8} y={y(i) + 4} className="mono" fontSize={10}
                    fill="var(--muted)">
                n {r.n}
              </text>
              <title>
                {r.label}: median {format(r.median)}, middle half {format(r.p25)} to {format(r.p75)}, {r.n} events
              </title>
            </g>
          )
        })}
      </svg>
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
